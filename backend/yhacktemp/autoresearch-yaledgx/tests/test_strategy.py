"""Tests for search strategies."""

from workbench.store.models import (
    BenchmarkMetrics,
    ExperimentConfig,
    ExperimentResult,
    Quantization,
)
from workbench.strategy.grid import GridStrategy
from workbench.strategy.random import RandomStrategy


def _make_result(config: ExperimentConfig, bpb: float = 1.5) -> ExperimentResult:
    return ExperimentResult(
        config=config,
        metrics=BenchmarkMetrics(
            val_bpb=bpb, energy_per_token_j=0.3, tokens_per_sec=50.0
        ),
    )


class TestGridStrategy:
    def test_proposes_configs(self):
        grid = {
            "model_name": ["test/a"],
            "quantization": [Quantization.NONE, Quantization.GPTQ_4BIT],
            "batch_size": [1, 4],
        }
        strategy = GridStrategy(grid=grid)
        assert strategy.total_configs == 4

        configs = []
        for _ in range(10):
            c = strategy.propose(history=[])
            if c is None:
                break
            configs.append(c)
            strategy.update(_make_result(c))

        assert len(configs) == 4

    def test_skips_already_run(self):
        grid = {
            "model_name": ["test/a"],
            "batch_size": [1, 4],
        }
        strategy = GridStrategy(grid=grid)

        c1 = strategy.propose(history=[])
        assert c1 is not None
        r1 = _make_result(c1)
        strategy.update(r1)

        c2 = strategy.propose(history=[r1])
        assert c2 is not None
        assert c2.config_hash != c1.config_hash

    def test_exhaustion_returns_none(self):
        grid = {"model_name": ["test/a"], "batch_size": [1]}
        strategy = GridStrategy(grid=grid)

        c = strategy.propose(history=[])
        assert c is not None
        strategy.update(_make_result(c))

        assert strategy.propose(history=[_make_result(c)]) is None


class TestRandomStrategy:
    def test_proposes_unique_configs(self):
        strategy = RandomStrategy(max_proposals=10, seed=42)
        seen = set()
        for _ in range(10):
            c = strategy.propose(history=[])
            if c is None:
                break
            assert c.config_hash not in seen
            seen.add(c.config_hash)
            strategy.update(_make_result(c))

    def test_respects_max_proposals(self):
        strategy = RandomStrategy(max_proposals=3, seed=42)
        count = 0
        for _ in range(100):
            c = strategy.propose(history=[])
            if c is None:
                break
            count += 1
            strategy.update(_make_result(c))
        assert count == 3

    def test_deterministic_with_seed(self):
        s1 = RandomStrategy(max_proposals=5, seed=123)
        s2 = RandomStrategy(max_proposals=5, seed=123)

        for _ in range(5):
            c1 = s1.propose(history=[])
            c2 = s2.propose(history=[])
            assert c1 is not None and c2 is not None
            assert c1.config_hash == c2.config_hash
            s1.update(_make_result(c1))
            s2.update(_make_result(c2))
