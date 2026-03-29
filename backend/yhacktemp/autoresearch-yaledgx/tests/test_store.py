"""Tests for SQLite result store."""

import tempfile
from pathlib import Path

import pytest

from workbench.store.database import ResultStore
from workbench.store.models import (
    BenchmarkMetrics,
    ExperimentConfig,
    ExperimentResult,
    ExperimentStatus,
    Quantization,
    SearchStrategy,
)


@pytest.fixture
def store(tmp_path):
    """Fresh in-memory-ish store for each test."""
    db_path = tmp_path / "test.db"
    s = ResultStore(db_path)
    yield s
    s.close()


def _make_result(
    model: str = "test/model",
    batch: int = 1,
    bpb: float = 1.5,
    energy: float = 0.3,
    tps: float = 50.0,
) -> ExperimentResult:
    config = ExperimentConfig(model_name=model, batch_size=batch)
    metrics = BenchmarkMetrics(
        val_bpb=bpb, energy_per_token_j=energy, tokens_per_sec=tps
    )
    return ExperimentResult(config=config, metrics=metrics)


def test_save_and_retrieve(store):
    result = _make_result()
    store.save(result)

    retrieved = store.get(result.config_hash)
    assert retrieved is not None
    assert retrieved.config_hash == result.config_hash
    assert retrieved.metrics.val_bpb == 1.5


def test_exists(store):
    result = _make_result()
    assert not store.exists(result.config_hash)
    store.save(result)
    assert store.exists(result.config_hash)


def test_count(store):
    assert store.count() == 0
    store.save(_make_result(model="a"))
    store.save(_make_result(model="b"))
    assert store.count() == 2


def test_all_results_filtered(store):
    r1 = _make_result(model="a")
    r2 = _make_result(model="b")
    r2.status = ExperimentStatus.FAILED
    store.save(r1)
    store.save(r2)

    completed = store.all_results(ExperimentStatus.COMPLETED)
    assert len(completed) == 1
    assert completed[0].config.model_name == "a"


def test_pareto_update(store):
    r1 = _make_result(model="a")
    r2 = _make_result(model="b")
    store.save(r1)
    store.save(r2)

    store.update_pareto_ranks({r1.config_hash: 0, r2.config_hash: 1})

    frontier = store.pareto_frontier()
    assert len(frontier) == 1
    assert frontier[0].config.model_name == "a"


def test_export_json(store):
    store.save(_make_result(model="a"))
    store.save(_make_result(model="b"))

    exported = store.export_json()
    assert len(exported) == 2
    assert all("config_hash" in e for e in exported)
