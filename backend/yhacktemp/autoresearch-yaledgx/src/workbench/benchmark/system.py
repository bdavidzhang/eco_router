"""System resource monitoring for DGX Spark.

Captures what nvidia-smi can't on unified memory hardware:
- Memory usage via /proc/meminfo (GPU reports N/A due to C2C)
- CPU cluster frequencies (big X925 vs little A725 cores)
- System load average
- NVMe temperature

Matches sensor_logger.sh's system-level data collection.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class SystemSnapshot:
    """Point-in-time system resource reading."""

    # Memory (from /proc/meminfo — the only way on C2C unified memory)
    mem_total_kb: int = 0
    mem_available_kb: int = 0
    mem_used_kb: int = 0

    # CPU cluster frequencies (MHz) — big.LITTLE awareness
    cpu_big_avg_mhz: float = 0.0    # X925 performance cores @ up to 3.9 GHz
    cpu_little_avg_mhz: float = 0.0  # A725 efficiency cores @ up to 2.8 GHz

    # System load
    load_avg_1m: float = 0.0

    # NVMe temperature (°C) — storage bottleneck indicator
    nvme_temp_c: float | None = None

    @property
    def mem_used_gb(self) -> float:
        return self.mem_used_kb / 1_048_576

    @property
    def mem_available_gb(self) -> float:
        return self.mem_available_kb / 1_048_576

    @property
    def mem_total_gb(self) -> float:
        return self.mem_total_kb / 1_048_576

    @property
    def mem_pressure_pct(self) -> float:
        """Memory pressure as percentage used. >90% = danger zone on unified memory."""
        if self.mem_total_kb == 0:
            return 0.0
        return (self.mem_used_kb / self.mem_total_kb) * 100


class SystemMonitor:
    """Reads system-level sensors that nvidia-smi misses on DGX Spark.

    On non-Linux systems (dev machines), falls back gracefully.
    """

    # DGX Spark big.LITTLE threshold — cores above 3 GHz max are X925 (big)
    _BIG_CORE_FREQ_THRESHOLD_KHZ = 3_000_000

    def snapshot(self) -> SystemSnapshot:
        """Take a complete system resource reading."""
        mem = self._read_memory()
        cpu_big, cpu_little = self._read_cpu_freqs()
        load = self._read_load_avg()
        nvme = self._read_nvme_temp()

        return SystemSnapshot(
            mem_total_kb=mem.get("total", 0),
            mem_available_kb=mem.get("available", 0),
            mem_used_kb=mem.get("total", 0) - mem.get("available", 0),
            cpu_big_avg_mhz=cpu_big,
            cpu_little_avg_mhz=cpu_little,
            load_avg_1m=load,
            nvme_temp_c=nvme,
        )

    def _read_memory(self) -> dict[str, int]:
        """Read memory info from /proc/meminfo (faster than `free`)."""
        try:
            meminfo = Path("/proc/meminfo").read_text()
            result = {}
            for line in meminfo.splitlines():
                if line.startswith("MemTotal:"):
                    result["total"] = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    result["available"] = int(line.split()[1])
            return result
        except (FileNotFoundError, PermissionError, ValueError):
            return self._fallback_memory()

    def _read_cpu_freqs(self) -> tuple[float, float]:
        """Read CPU frequencies and split by big/LITTLE cluster.

        DGX Spark: X925 big cores (>3 GHz max) vs A725 LITTLE cores.
        Matches sensor_logger.sh's cluster averaging logic.
        """
        big_sum = 0
        big_count = 0
        little_sum = 0
        little_count = 0

        cpu_base = Path("/sys/devices/system/cpu")
        try:
            for cpu_dir in sorted(cpu_base.glob("cpu[0-9]*/cpufreq")):
                freq_file = cpu_dir / "scaling_cur_freq"
                if not freq_file.exists():
                    continue
                freq_khz = int(freq_file.read_text().strip())
                # Check max freq to determine cluster membership
                max_file = cpu_dir / "scaling_max_freq"
                max_khz = int(max_file.read_text().strip()) if max_file.exists() else freq_khz
                if max_khz > self._BIG_CORE_FREQ_THRESHOLD_KHZ:
                    big_sum += freq_khz
                    big_count += 1
                else:
                    little_sum += freq_khz
                    little_count += 1
        except (PermissionError, ValueError, OSError):
            pass

        big_avg = (big_sum / big_count / 1000) if big_count > 0 else 0.0
        little_avg = (little_sum / little_count / 1000) if little_count > 0 else 0.0
        return big_avg, little_avg

    def _read_load_avg(self) -> float:
        """Read 1-minute load average from /proc/loadavg."""
        try:
            raw = Path("/proc/loadavg").read_text().strip()
            return float(raw.split()[0])
        except (FileNotFoundError, PermissionError, ValueError):
            return 0.0

    def _read_nvme_temp(self) -> float | None:
        """Read NVMe temperature via hwmon (sensor_logger.sh detects as 'nvme')."""
        for hwmon in Path("/sys/class/hwmon").glob("hwmon*"):
            name_file = hwmon / "name"
            if not name_file.exists():
                continue
            try:
                name = name_file.read_text().strip()
                if name == "nvme":
                    temp_file = hwmon / "temp1_input"
                    raw = int(temp_file.read_text().strip())
                    return raw / 1000.0  # millidegrees → degrees
            except (PermissionError, ValueError, FileNotFoundError):
                continue
        return None

    @staticmethod
    def _fallback_memory() -> dict[str, int]:
        """Fallback for non-Linux (dev machines)."""
        try:
            import psutil
            vm = psutil.virtual_memory()
            return {"total": vm.total // 1024, "available": vm.available // 1024}
        except ImportError:
            # Last resort: assume 128 GB DGX Spark with 50% usage
            total = 128 * 1024 * 1024  # 128 GB in KB
            return {"total": total, "available": total // 2}
