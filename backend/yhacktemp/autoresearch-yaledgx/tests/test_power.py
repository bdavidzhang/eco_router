"""Tests for enhanced GPU power monitoring (sensor_logger.sh alignment).

Verifies that PowerSample now captures GPU util%, clock speed,
and that PowerTrace computes aggregate stats for them.
"""

from workbench.benchmark.power import PowerMonitor, PowerSample, PowerTrace


class TestPowerSample:
    def test_full_sensor_fields(self):
        """PowerSample should have all 5 nvidia-smi fields from sensor_logger.sh."""
        sample = PowerSample(
            timestamp=1000.0,
            power_w=35.5,
            temp_c=62.0,
            gpu_util_pct=78.0,
            gpu_clock_mhz=2100.0,
            gpu_vid_clock_mhz=1890.0,
        )
        assert sample.power_w == 35.5
        assert sample.gpu_util_pct == 78.0
        assert sample.gpu_clock_mhz == 2100.0
        assert sample.gpu_vid_clock_mhz == 1890.0

    def test_nullable_fields(self):
        """All optional fields should default to None."""
        sample = PowerSample(timestamp=1000.0, power_w=30.0)
        assert sample.temp_c is None
        assert sample.gpu_util_pct is None
        assert sample.gpu_clock_mhz is None


class TestPowerTrace:
    def _make_trace(self, n: int = 5) -> PowerTrace:
        samples = []
        for i in range(n):
            samples.append(PowerSample(
                timestamp=1000.0 + i,
                power_w=30.0 + i * 2,
                temp_c=55.0 + i,
                gpu_util_pct=60.0 + i * 5,
                gpu_clock_mhz=1800.0 + i * 50,
            ))
        return PowerTrace(samples=samples)

    def test_avg_gpu_util(self):
        trace = self._make_trace()
        assert trace.avg_gpu_util_pct is not None
        # 60, 65, 70, 75, 80 → avg = 70
        assert trace.avg_gpu_util_pct == 70.0

    def test_avg_gpu_clock(self):
        trace = self._make_trace()
        assert trace.avg_gpu_clock_mhz is not None
        # 1800, 1850, 1900, 1950, 2000 → avg = 1900
        assert trace.avg_gpu_clock_mhz == 1900.0

    def test_min_gpu_clock(self):
        trace = self._make_trace()
        assert trace.min_gpu_clock_mhz == 1800.0

    def test_empty_trace_returns_none(self):
        trace = PowerTrace()
        assert trace.avg_gpu_util_pct is None
        assert trace.avg_gpu_clock_mhz is None
        assert trace.min_gpu_clock_mhz is None

    def test_energy_trapezoidal(self):
        """Energy should be computed via trapezoidal integration."""
        trace = PowerTrace(samples=[
            PowerSample(timestamp=0.0, power_w=30.0),
            PowerSample(timestamp=1.0, power_w=30.0),
        ])
        # Constant 30W for 1 second = 30J
        assert trace.total_energy_j == 30.0

    def test_duration(self):
        trace = self._make_trace(5)
        assert trace.duration_sec == 4.0  # timestamps 1000..1004


class TestPowerMonitorFallback:
    def test_fallback_includes_gpu_util(self):
        """Fallback samples (dev machines) should include GPU util."""
        sample = PowerMonitor._fallback_sample()
        assert sample.gpu_util_pct is not None
        assert 0 <= sample.gpu_util_pct <= 100
        assert sample.gpu_clock_mhz is not None
