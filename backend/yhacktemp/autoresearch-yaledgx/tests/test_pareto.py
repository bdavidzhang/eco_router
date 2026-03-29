"""Tests for Pareto frontier computation (SCI-based).

Pareto optimization now uses SCI (gCO₂/token) instead of raw energy.
SCI = (E × I) + M — captures full carbon picture.
"""

from workbench.pareto import compute_pareto_ranks, dominates, get_pareto_frontier
from workbench.store.models import (
    BenchmarkMetrics,
    ExperimentConfig,
    ExperimentResult,
)


def _make(
    name: str, bpb: float, energy: float, tps: float,
    sci: float | None = None,
) -> ExperimentResult:
    """Helper to create results with SCI scores."""
    metrics = BenchmarkMetrics(
        val_bpb=bpb, energy_per_token_j=energy, tokens_per_sec=tps,
    )
    # Compute SCI if not explicitly provided
    if sci is not None:
        metrics.sci_per_token = sci
    else:
        # Default: compute from energy with US average grid
        metrics.compute_derived(
            carbon_intensity_gco2_per_kwh=400.0,
            embodied_gco2_per_token=0.00003,
        )
    return ExperimentResult(
        config=ExperimentConfig(model_name=name),
        metrics=metrics,
    )


def test_dominates_strictly_better():
    """A dominates B if better on all objectives (BPB, SCI, throughput)."""
    a = _make("a", bpb=1.0, energy=0.1, tps=100, sci=0.001)
    b = _make("b", bpb=2.0, energy=0.2, tps=50, sci=0.002)
    assert dominates(a, b)
    assert not dominates(b, a)


def test_dominates_equal_not_domination():
    """Equal on all objectives is NOT domination."""
    a = _make("a", bpb=1.0, energy=0.1, tps=100, sci=0.001)
    b = _make("b", bpb=1.0, energy=0.1, tps=100, sci=0.001)
    assert not dominates(a, b)
    assert not dominates(b, a)


def test_dominates_partial_not_domination():
    """Better on some, worse on others = trade-off, not domination."""
    a = _make("a", bpb=1.0, energy=0.2, tps=100, sci=0.002)  # Better BPB, worse SCI
    b = _make("b", bpb=2.0, energy=0.1, tps=100, sci=0.001)  # Worse BPB, better SCI
    assert not dominates(a, b)
    assert not dominates(b, a)


def test_pareto_ranks_simple():
    """Three results with clear ranking structure."""
    r1 = _make("best", bpb=1.0, energy=0.1, tps=100, sci=0.001)
    r2 = _make("trade", bpb=2.0, energy=0.05, tps=80, sci=0.0005)  # Trade-off
    r3 = _make("worst", bpb=3.0, energy=0.3, tps=30, sci=0.003)

    ranks = compute_pareto_ranks([r1, r2, r3])
    assert ranks[r1.config_hash] == 0  # Frontier
    assert ranks[r2.config_hash] == 0  # Frontier (trade-off)
    assert ranks[r3.config_hash] == 1  # Dominated


def test_pareto_frontier():
    r1 = _make("a", bpb=1.0, energy=0.1, tps=100, sci=0.001)
    r2 = _make("b", bpb=2.0, energy=0.05, tps=80, sci=0.0005)
    r3 = _make("c", bpb=3.0, energy=0.3, tps=30, sci=0.003)

    frontier = get_pareto_frontier([r1, r2, r3])
    hashes = {r.config_hash for r in frontier}
    assert r1.config_hash in hashes
    assert r2.config_hash in hashes
    assert r3.config_hash not in hashes


def test_empty_results():
    assert compute_pareto_ranks([]) == {}
    assert get_pareto_frontier([]) == []


def test_single_result():
    r = _make("only", bpb=1.5, energy=0.2, tps=60)
    ranks = compute_pareto_ranks([r])
    assert ranks[r.config_hash] == 0


def test_missing_metrics_excluded():
    """Results with None metrics should be excluded from ranking."""
    good = _make("good", bpb=1.0, energy=0.1, tps=100)
    bad = ExperimentResult(
        config=ExperimentConfig(model_name="bad"),
        metrics=BenchmarkMetrics(),  # All None
    )
    ranks = compute_pareto_ranks([good, bad])
    assert good.config_hash in ranks
    assert bad.config_hash not in ranks


def test_sci_fallback_from_energy():
    """When SCI isn't set, pareto should fallback to energy-derived SCI."""
    # These have energy but no explicit SCI — fallback should kick in
    r1 = ExperimentResult(
        config=ExperimentConfig(model_name="a"),
        metrics=BenchmarkMetrics(
            val_bpb=1.0, energy_per_token_j=0.1, tokens_per_sec=100,
        ),
    )
    r2 = ExperimentResult(
        config=ExperimentConfig(model_name="b"),
        metrics=BenchmarkMetrics(
            val_bpb=2.0, energy_per_token_j=0.5, tokens_per_sec=50,
        ),
    )
    ranks = compute_pareto_ranks([r1, r2])
    # r1 should dominate r2 (better on all axes)
    assert ranks[r1.config_hash] == 0
    assert ranks[r2.config_hash] == 1


def test_region_affects_dominance():
    """Same energy, different grid → different SCI → different dominance."""
    # In France (I=55), low energy config dominates less
    # In Poland (I=650), same energy produces much more carbon
    clean_grid = _make("clean", bpb=1.5, energy=0.1, tps=80, sci=0.0001)
    dirty_grid = _make("dirty", bpb=1.5, energy=0.1, tps=80, sci=0.01)
    # Same BPB and throughput, but clean grid has much lower SCI
    assert dominates(clean_grid, dirty_grid)
