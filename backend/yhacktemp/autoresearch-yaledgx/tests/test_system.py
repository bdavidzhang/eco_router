"""Tests for system resource monitoring (sensor_logger.sh alignment).

These test the /proc/meminfo, CPU freq, load avg, and NVMe
monitoring that matches sensor_logger.sh's system-level data.
"""

from workbench.benchmark.system import SystemMonitor, SystemSnapshot


class TestSystemSnapshot:
    def test_mem_gb_conversion(self):
        snap = SystemSnapshot(
            mem_total_kb=128 * 1024 * 1024,  # 128 GB in KB
            mem_available_kb=64 * 1024 * 1024,
            mem_used_kb=64 * 1024 * 1024,
        )
        assert snap.mem_total_gb == 128.0
        assert snap.mem_used_gb == 64.0
        assert snap.mem_available_gb == 64.0

    def test_mem_pressure_pct(self):
        snap = SystemSnapshot(
            mem_total_kb=100_000,
            mem_available_kb=10_000,
            mem_used_kb=90_000,
        )
        assert snap.mem_pressure_pct == 90.0

    def test_mem_pressure_zero_total(self):
        snap = SystemSnapshot()
        assert snap.mem_pressure_pct == 0.0

    def test_defaults(self):
        snap = SystemSnapshot()
        assert snap.mem_total_kb == 0
        assert snap.load_avg_1m == 0.0
        assert snap.nvme_temp_c is None


class TestSystemMonitor:
    def test_snapshot_returns_snapshot(self):
        """Monitor should always return a snapshot, even on non-Linux."""
        monitor = SystemMonitor()
        snap = monitor.snapshot()
        assert isinstance(snap, SystemSnapshot)
        # On macOS/CI, we get fallback values — that's fine
        # Just verify the structure is right
        assert snap.mem_total_kb >= 0
        assert snap.mem_pressure_pct >= 0

    def test_snapshot_mem_available_plausible(self):
        """Available memory should be <= total."""
        monitor = SystemMonitor()
        snap = monitor.snapshot()
        if snap.mem_total_kb > 0:
            assert snap.mem_available_kb <= snap.mem_total_kb
