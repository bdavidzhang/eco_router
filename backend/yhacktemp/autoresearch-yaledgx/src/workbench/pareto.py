"""Pareto frontier computation via non-dominated sorting.

Multi-objective optimization is the heart of this workbench.
A config is Pareto-optimal (rank 0) if no other config is strictly
better on ALL objectives simultaneously.

We optimize: minimize BPB, minimize SCI (gCO₂/token), maximize throughput.

SCI = (E × I) + M — the Green Software Foundation's carbon score.
This replaces raw energy-per-token because it captures the FULL
carbon picture: operational energy AND embodied hardware emissions.
"""

from __future__ import annotations

from workbench.store.models import ExperimentResult


def _get_objectives(r: ExperimentResult) -> tuple[float, float, float] | None:
    """Extract the three optimization objectives from a result.

    Returns (val_bpb, sci_per_token, tokens_per_sec) or None if missing.
    Falls back to energy_per_token_j if SCI hasn't been computed yet.
    """
    m = r.metrics
    if m.val_bpb is None or m.tokens_per_sec is None:
        return None

    # Prefer SCI, fall back to raw energy converted to pseudo-SCI
    carbon = m.sci_per_token
    if carbon is None and m.energy_per_token_j is not None:
        # Fallback: use raw energy with default intensity (400 gCO₂/kWh)
        carbon = (m.energy_per_token_j / 3_600_000) * 400.0 + 0.00003
    if carbon is None:
        return None

    return (m.val_bpb, carbon, m.tokens_per_sec)


def dominates(a: ExperimentResult, b: ExperimentResult) -> bool:
    """Does result `a` Pareto-dominate result `b`?

    `a` dominates `b` iff `a` is at least as good on every objective
    AND strictly better on at least one.

    Objectives:
        - val_bpb: minimize (lower is better)
        - sci_per_token: minimize (lower is better) — gCO₂/token
        - tokens_per_sec: maximize (higher is better)
    """
    obj_a = _get_objectives(a)
    obj_b = _get_objectives(b)
    if obj_a is None or obj_b is None:
        return False

    bpb_a, sci_a, tps_a = obj_a
    bpb_b, sci_b, tps_b = obj_b

    # "At least as good" on all objectives
    at_least_as_good = (
        bpb_a <= bpb_b
        and sci_a <= sci_b
        and tps_a >= tps_b
    )
    # "Strictly better" on at least one
    strictly_better = (
        bpb_a < bpb_b
        or sci_a < sci_b
        or tps_a > tps_b
    )
    return at_least_as_good and strictly_better


def compute_pareto_ranks(results: list[ExperimentResult]) -> dict[str, int]:
    """Non-dominated sorting — assign a Pareto rank to each result.

    Rank 0 = on the Pareto frontier (non-dominated).
    Rank 1 = dominated only by rank-0 members. And so on.

    Returns:
        Mapping of config_hash → pareto_rank.
    """
    valid = [r for r in results if _get_objectives(r) is not None]

    if not valid:
        return {}

    rankings: dict[str, int] = {}
    remaining = list(valid)
    rank = 0

    while remaining:
        non_dominated = []
        for candidate in remaining:
            is_dominated = any(
                dominates(other, candidate)
                for other in remaining
                if other.config_hash != candidate.config_hash
            )
            if not is_dominated:
                non_dominated.append(candidate)

        for r in non_dominated:
            rankings[r.config_hash] = rank

        nd_hashes = {r.config_hash for r in non_dominated}
        remaining = [r for r in remaining if r.config_hash not in nd_hashes]
        rank += 1

    return rankings


def get_pareto_frontier(results: list[ExperimentResult]) -> list[ExperimentResult]:
    """Get only the Pareto-optimal results (rank 0)."""
    rankings = compute_pareto_ranks(results)
    return [r for r in results if rankings.get(r.config_hash) == 0]


def pareto_improvement(
    new_result: ExperimentResult,
    frontier: list[ExperimentResult],
) -> bool:
    """Does the new result expand or improve the Pareto frontier?"""
    if not frontier:
        return True
    return not any(dominates(existing, new_result) for existing in frontier)
