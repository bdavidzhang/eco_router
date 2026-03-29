"""Tests for the evaluator — SCI scoring and Pareto frontier updates."""

import pytest

from workbench.benchmark.carbon import SciConfig
from workbench.evaluator import Evaluator
from workbench.store.database import ResultStore
from workbench.store.models import (
    BenchmarkMetrics,
    ExperimentConfig,
    ExperimentResult,
    ExperimentStatus,
)


@pytest.fixture
def evaluator(tmp_path):
    store = ResultStore(tmp_path / "test.db")
    sci_config = SciConfig(
        carbon_intensity_gco2_per_kwh=400.0,
        embodied_gco2_per_token=0.00003,
    )
    ev = Evaluator(store, sci_config=sci_config)
    yield ev
    store.close()


def _make_result(
    name: str,
    bpb: float = 1.5,
    energy: float = 0.3,
    tps: float = 50.0,
) -> ExperimentResult:
    return ExperimentResult(
        config=ExperimentConfig(model_name=name),
        metrics=BenchmarkMetrics(
            val_bpb=bpb,
            energy_per_token_j=energy,
            tokens_per_sec=tps,
            gpu_power_avg_w=25.0,
        ),
    )


def test_evaluate_computes_sci(evaluator):
    """Evaluator should compute SCI during evaluation."""
    r = _make_result("a")
    result = evaluator.evaluate(r)
    assert result.metrics.sci_per_token is not None
    assert result.metrics.sci_per_token > 0
    assert result.metrics.carbon_operational_g is not None
    assert result.metrics.carbon_embodied_g is not None


def test_evaluate_updates_pareto(evaluator):
    r1 = _make_result("a", bpb=1.0, energy=0.1, tps=100)
    result = evaluator.evaluate(r1)
    assert result.pareto_rank == 0


def test_evaluate_computes_derived(evaluator):
    r = _make_result("a")
    result = evaluator.evaluate(r)
    assert result.metrics.gpu_efficiency is not None
    assert result.metrics.cost_per_token_usd is not None
    assert result.metrics.energy_kwh_per_token is not None


def test_frontier_grows(evaluator):
    r1 = _make_result("a", bpb=1.0, energy=0.2, tps=80)
    r2 = _make_result("b", bpb=2.0, energy=0.1, tps=60)
    evaluator.evaluate(r1)
    evaluator.evaluate(r2)
    assert evaluator.frontier_size == 2


def test_summary_includes_sci(evaluator):
    evaluator.evaluate(_make_result("a", bpb=1.0, energy=0.1, tps=100))
    evaluator.evaluate(_make_result("b", bpb=2.0, energy=0.2, tps=50))

    summary = evaluator.summary()
    assert summary["total_experiments"] == 2
    assert summary["completed"] == 2
    assert summary["frontier_size"] >= 1
    assert summary["best_sci"] is not None
    assert summary["best_sci"] > 0
    assert summary["carbon_intensity"] == 400.0
    assert summary["best_sci_scale"] is not None


def test_failed_result_not_ranked(evaluator):
    r = _make_result("fail")
    r.status = ExperimentStatus.FAILED
    result = evaluator.evaluate(r)
    assert result.pareto_rank is None
