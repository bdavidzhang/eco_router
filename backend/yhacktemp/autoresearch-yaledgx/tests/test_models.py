"""Tests for data models — configs, metrics, results (with SCI + system sensors)."""

from workbench.store.models import (
    BenchmarkMetrics,
    ExperimentConfig,
    ExperimentResult,
    ExperimentStatus,
    Quantization,
    SearchStrategy,
)


def test_config_hash_deterministic():
    """Same config -> same hash. Always."""
    c1 = ExperimentConfig(model_name="test/model", batch_size=4)
    c2 = ExperimentConfig(model_name="test/model", batch_size=4)
    assert c1.config_hash == c2.config_hash


def test_config_hash_differs_on_change():
    """Different config -> different hash."""
    c1 = ExperimentConfig(model_name="test/model", batch_size=4)
    c2 = ExperimentConfig(model_name="test/model", batch_size=8)
    assert c1.config_hash != c2.config_hash


def test_config_roundtrip():
    """Config -> dict -> Config should be identity."""
    original = ExperimentConfig(
        model_name="test/model",
        quantization=Quantization.GPTQ_4BIT,
        batch_size=16,
        sequence_length=1024,
    )
    restored = ExperimentConfig.from_dict(original.to_dict())
    assert restored == original
    assert restored.config_hash == original.config_hash


def test_metrics_derived():
    """Derived metrics compute correctly including SCI."""
    m = BenchmarkMetrics(
        tokens_per_sec=100.0,
        gpu_power_avg_w=25.0,
        energy_per_token_j=0.25,
    )
    m.compute_derived(usd_per_kwh=0.12)
    assert m.gpu_efficiency == 4.0  # 100 / 25
    assert m.cost_per_token_usd is not None
    assert m.cost_per_token_usd > 0


def test_metrics_sci_computed():
    """SCI should be computed from energy when compute_derived is called."""
    m = BenchmarkMetrics(
        tokens_per_sec=100.0,
        gpu_power_avg_w=25.0,
        energy_per_token_j=3600.0,  # 0.001 kWh
    )
    m.compute_derived(
        carbon_intensity_gco2_per_kwh=400.0,
        embodied_gco2_per_token=0.05,
    )
    assert m.sci_per_token is not None
    assert m.energy_kwh_per_token is not None
    assert m.carbon_operational_g is not None
    assert m.carbon_embodied_g == 0.05
    # E = 0.001 kWh, I = 400, E*I = 0.4 gCO2, M = 0.05, SCI = 0.45
    assert abs(m.sci_per_token - 0.45) < 1e-6


def test_metrics_no_sci_without_energy():
    """SCI should be None if energy isn't available."""
    m = BenchmarkMetrics(tokens_per_sec=100.0, gpu_power_avg_w=25.0)
    m.compute_derived()
    assert m.sci_per_token is None


def test_metrics_system_fields():
    """New system sensor fields should be present and serializable."""
    m = BenchmarkMetrics(
        val_bpb=1.5,
        gpu_util_avg_pct=75.0,
        gpu_clock_avg_mhz=2100.0,
        gpu_clock_min_mhz=1800.0,
        mem_used_gb=45.2,
        mem_available_gb=82.8,
        mem_pressure_pct=35.3,
        nvme_temp_c=42.0,
        system_load_avg=3.5,
    )
    d = m.to_dict()
    assert d["gpu_util_avg_pct"] == 75.0
    assert d["gpu_clock_avg_mhz"] == 2100.0
    assert d["mem_used_gb"] == 45.2
    assert d["nvme_temp_c"] == 42.0
    assert d["system_load_avg"] == 3.5

    restored = BenchmarkMetrics.from_dict(d)
    assert restored.gpu_util_avg_pct == 75.0
    assert restored.mem_pressure_pct == 35.3


def test_result_roundtrip():
    """Result -> dict -> Result preserves all fields."""
    config = ExperimentConfig(model_name="test/model")
    metrics = BenchmarkMetrics(val_bpb=1.5, energy_per_token_j=0.3)
    result = ExperimentResult(
        config=config,
        metrics=metrics,
        status=ExperimentStatus.COMPLETED,
        strategy_used=SearchStrategy.GRID,
        pareto_rank=0,
    )
    d = result.to_dict()
    restored = ExperimentResult.from_dict(d)
    assert restored.config_hash == result.config_hash
    assert restored.metrics.val_bpb == 1.5
    assert restored.status == ExperimentStatus.COMPLETED
    assert restored.pareto_rank == 0


def test_config_frozen():
    """ExperimentConfig should be immutable (frozen dataclass)."""
    config = ExperimentConfig(model_name="test/model")
    try:
        config.model_name = "other/model"  # type: ignore
        assert False, "Should have raised FrozenInstanceError"
    except AttributeError:
        pass  # Expected
