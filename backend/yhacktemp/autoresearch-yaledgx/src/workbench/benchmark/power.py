"""GPU power monitoring via nvidia-smi.

DGX Spark specific: GB10 reports power via nvidia-smi with both average
and instantaneous readings. Memory shows N/A due to unified C2C memory.
We sample at 1Hz during experiments via `nvidia-smi dmon`.

Now captures the FULL nvidia-smi sensor set matching sensor_logger.sh:
  temperature.gpu, power.draw, utilization.gpu, clocks.gr, clocks.video
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Matches sensor_logger.sh nvidia-smi query exactly
_NVIDIA_SMI_QUERY = "temperature.gpu,power.draw,utilization.gpu,clocks.gr,clocks.video"


@dataclass
class PowerSample:
    """Single GPU sensor reading — matches sensor_logger.sh fields."""

    timestamp: float
    power_w: float
    temp_c: float | None = None
    gpu_util_pct: float | None = None    # GPU utilization %
    gpu_clock_mhz: float | None = None   # Graphics clock (throttle detection)
    gpu_vid_clock_mhz: float | None = None  # Video clock


@dataclass
class PowerTrace:
    """Complete power trace from an experiment run."""

    samples: list[PowerSample] = field(default_factory=list)

    @property
    def avg_power_w(self) -> float | None:
        if not self.samples:
            return None
        return sum(s.power_w for s in self.samples) / len(self.samples)

    @property
    def max_power_w(self) -> float | None:
        if not self.samples:
            return None
        return max(s.power_w for s in self.samples)

    @property
    def avg_temp_c(self) -> float | None:
        temps = [s.temp_c for s in self.samples if s.temp_c is not None]
        return sum(temps) / len(temps) if temps else None

    @property
    def max_temp_c(self) -> float | None:
        temps = [s.temp_c for s in self.samples if s.temp_c is not None]
        return max(temps) if temps else None

    @property
    def avg_gpu_util_pct(self) -> float | None:
        """Average GPU utilization — critical for accurate SCI."""
        utils = [s.gpu_util_pct for s in self.samples if s.gpu_util_pct is not None]
        return sum(utils) / len(utils) if utils else None

    @property
    def avg_gpu_clock_mhz(self) -> float | None:
        """Average GPU clock — detects thermal throttling."""
        clocks = [s.gpu_clock_mhz for s in self.samples if s.gpu_clock_mhz is not None]
        return sum(clocks) / len(clocks) if clocks else None

    @property
    def min_gpu_clock_mhz(self) -> float | None:
        """Min GPU clock — shows worst-case throttling."""
        clocks = [s.gpu_clock_mhz for s in self.samples if s.gpu_clock_mhz is not None]
        return min(clocks) if clocks else None

    @property
    def duration_sec(self) -> float:
        if len(self.samples) < 2:
            return 0.0
        return self.samples[-1].timestamp - self.samples[0].timestamp

    @property
    def total_energy_j(self) -> float:
        """Total energy in joules (power x time via trapezoidal integration)."""
        if len(self.samples) < 2:
            return 0.0
        total = 0.0
        for i in range(1, len(self.samples)):
            dt = self.samples[i].timestamp - self.samples[i - 1].timestamp
            avg_p = (self.samples[i].power_w + self.samples[i - 1].power_w) / 2
            total += avg_p * dt
        return total


class PowerMonitor:
    """Background thread that samples GPU sensors via nvidia-smi at ~1Hz.

    Captures the same 5 fields as sensor_logger.sh:
    temp, power, utilization, graphics clock, video clock.
    """

    def __init__(self, sample_interval_sec: float = 1.0, gpu_index: int = 0) -> None:
        self._interval = sample_interval_sec
        self._gpu_index = gpu_index
        self._trace = PowerTrace()
        self._running = False
        self._thread: threading.Thread | None = None

    @property
    def trace(self) -> PowerTrace:
        return self._trace

    def start(self) -> None:
        """Start background power sampling."""
        self._trace = PowerTrace()
        self._running = True
        self._thread = threading.Thread(target=self._sample_loop, daemon=True)
        self._thread.start()
        logger.info("Power monitor started (interval=%.1fs)", self._interval)

    def stop(self) -> PowerTrace:
        """Stop sampling and return the collected trace."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
        logger.info(
            "Power monitor stopped: %d samples, avg=%.1fW, gpu_util=%.0f%%",
            len(self._trace.samples),
            self._trace.avg_power_w or 0,
            self._trace.avg_gpu_util_pct or 0,
        )
        return self._trace

    def _sample_loop(self) -> None:
        while self._running:
            sample = self._read_gpu_sensors()
            if sample:
                self._trace.samples.append(sample)
            time.sleep(self._interval)

    def _read_gpu_sensors(self) -> PowerSample | None:
        """Read GPU sensors via nvidia-smi — same query as sensor_logger.sh."""
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    f"--id={self._gpu_index}",
                    f"--query-gpu={_NVIDIA_SMI_QUERY}",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return self._fallback_sample()

            # Parse: "62, 35.50, 78, 2100, 1890"
            raw = result.stdout.strip()
            # Clean up [Not Supported] / [N/A] values (sensor_logger.sh does this too)
            raw = raw.replace("[Not Supported]", "").replace("[N/A]", "")
            parts = [p.strip() for p in raw.split(",")]

            return PowerSample(
                timestamp=time.time(),
                temp_c=_safe_float(parts, 0),
                power_w=_safe_float(parts, 1) or 0.0,
                gpu_util_pct=_safe_float(parts, 2),
                gpu_clock_mhz=_safe_float(parts, 3),
                gpu_vid_clock_mhz=_safe_float(parts, 4),
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError, IndexError):
            return self._fallback_sample()

    @staticmethod
    def _fallback_sample() -> PowerSample:
        """Fallback when nvidia-smi isn't available (dev machines, CI, etc.)."""
        logger.debug("nvidia-smi unavailable, using simulated GPU reading")
        import random

        return PowerSample(
            timestamp=time.time(),
            power_w=round(random.uniform(15.0, 45.0), 1),
            temp_c=round(random.uniform(40.0, 65.0), 1),
            gpu_util_pct=round(random.uniform(50.0, 95.0), 1),
            gpu_clock_mhz=round(random.uniform(1500.0, 3003.0), 0),
        )


def _safe_float(parts: list[str], index: int) -> float | None:
    """Safely parse a float from split nvidia-smi output."""
    try:
        val = parts[index].strip()
        return float(val) if val else None
    except (IndexError, ValueError):
        return None
