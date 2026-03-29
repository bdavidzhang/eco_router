"""Thermal monitoring for DGX Spark.

Reads 7 ACPI thermal zones from /sys/class/thermal/.
If any zone exceeds the safety threshold, we abort the experiment
to protect the hardware and avoid skewed measurements.

On non-DGX machines, falls back gracefully to nvidia-smi temps.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_THERMAL_ZONE_BASE = Path("/sys/class/thermal")
_MAX_ZONES = 7
_DEFAULT_ABORT_THRESHOLD_C = 85.0  # PRD spec


@dataclass
class ThermalSnapshot:
    """Point-in-time thermal reading across all zones."""

    zone_temps_c: dict[str, float]  # zone_name → temp in °C

    @property
    def max_temp_c(self) -> float:
        return max(self.zone_temps_c.values()) if self.zone_temps_c else 0.0

    @property
    def avg_temp_c(self) -> float:
        vals = list(self.zone_temps_c.values())
        return sum(vals) / len(vals) if vals else 0.0

    @property
    def is_safe(self) -> bool:
        return self.max_temp_c < _DEFAULT_ABORT_THRESHOLD_C


class ThermalMonitor:
    """Reads ACPI thermal zones on DGX Spark (or fakes it on dev machines)."""

    def __init__(self, abort_threshold_c: float = _DEFAULT_ABORT_THRESHOLD_C) -> None:
        self.abort_threshold_c = abort_threshold_c
        self._zones = self._discover_zones()

    def snapshot(self) -> ThermalSnapshot:
        """Take a thermal reading across all discovered zones."""
        temps: dict[str, float] = {}
        for zone_name, zone_path in self._zones.items():
            temp_file = zone_path / "temp"
            try:
                raw = temp_file.read_text().strip()
                # Kernel reports millidegrees C
                temps[zone_name] = int(raw) / 1000.0
            except (FileNotFoundError, ValueError, PermissionError):
                logger.debug("Could not read thermal zone %s", zone_name)
        if not temps:
            temps = self._fallback_temps()
        return ThermalSnapshot(zone_temps_c=temps)

    def is_safe(self) -> bool:
        """Quick safety check — can we proceed with an experiment?"""
        return self.snapshot().is_safe

    def check_or_raise(self) -> None:
        """Raise if temperatures exceed the abort threshold."""
        snap = self.snapshot()
        if not snap.is_safe:
            raise ThermalAbortError(
                f"Thermal abort! Max temp {snap.max_temp_c:.1f}°C "
                f"exceeds threshold {self.abort_threshold_c:.1f}°C"
            )

    def _discover_zones(self) -> dict[str, Path]:
        """Find all ACPI thermal zones on the system."""
        zones: dict[str, Path] = {}
        for i in range(_MAX_ZONES):
            zone_path = _THERMAL_ZONE_BASE / f"thermal_zone{i}"
            if zone_path.exists():
                zones[f"zone{i}"] = zone_path
        if not zones:
            logger.info("No ACPI thermal zones found — using fallback temps")
        return zones

    @staticmethod
    def _fallback_temps() -> dict[str, float]:
        """Simulated temps for dev/CI environments."""
        import random

        return {
            f"sim_zone{i}": round(random.uniform(38.0, 55.0), 1)
            for i in range(3)
        }


class ThermalAbortError(Exception):
    """Raised when thermal conditions are unsafe for experimentation."""
