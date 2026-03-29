"""Evaluator — scores results and maintains the Pareto frontier.

The evaluator doesn't run experiments. It takes completed results,
computes derived metrics (including SCI), updates Pareto rankings,
and tells the controller whether progress was made.

SCI = (E × I) + M — the Green Software Foundation's carbon score.
"""

from __future__ import annotations

import logging

from workbench.benchmark.carbon import SciConfig, sci_at_scale
from workbench.pareto import compute_pareto_ranks, get_pareto_frontier, pareto_improvement
from workbench.store.database import ResultStore
from workbench.store.models import ExperimentResult, ExperimentStatus

logger = logging.getLogger(__name__)


class Evaluator:
    """Scores experiment results and maintains the Pareto frontier."""

    def __init__(
        self,
        store: ResultStore,
        usd_per_kwh: float = 0.12,
        sci_config: SciConfig | None = None,
    ) -> None:
        self._store = store
        self._usd_per_kwh = usd_per_kwh
        self._sci = sci_config or SciConfig()

    def evaluate(self, result: ExperimentResult) -> ExperimentResult:
        """Score a completed experiment and update the frontier.

        Computes SCI, derived metrics, and Pareto rankings.
        """
        if result.status != ExperimentStatus.COMPLETED:
            logger.info(
                "Skipping evaluation for %s (status=%s)",
                result.config_hash,
                result.status.value,
            )
            self._store.save(result)
            return result

        # Compute derived metrics including SCI
        result.metrics.compute_derived(
            usd_per_kwh=self._usd_per_kwh,
            carbon_intensity_gco2_per_kwh=self._sci.carbon_intensity_gco2_per_kwh,
            embodied_gco2_per_token=self._sci.embodied_gco2_per_token,
        )

        # Save the result first
        self._store.save(result)

        # Recompute Pareto ranks across ALL completed results
        all_results = self._store.all_results(status=ExperimentStatus.COMPLETED)
        rankings = compute_pareto_ranks(all_results)

        # Update ranks in the store
        self._store.update_pareto_ranks(rankings)

        # Update the current result's rank
        result.pareto_rank = rankings.get(result.config_hash)

        frontier = get_pareto_frontier(all_results)
        improved = pareto_improvement(result, frontier)

        sci = result.metrics.sci_per_token
        if result.is_pareto_optimal:
            logger.info(
                "🌟 Pareto-optimal! %s — SCI=%.6f gCO₂/tok, BPB=%.4f, %.1f tok/s",
                result.config_hash,
                sci or 0,
                result.metrics.val_bpb or 0,
                result.metrics.tokens_per_sec or 0,
            )
        elif improved:
            logger.info(
                "📈 Frontier expanded by %s (rank %d, SCI=%.6f)",
                result.config_hash,
                result.pareto_rank or -1,
                sci or 0,
            )
        else:
            logger.info(
                "📉 Config %s dominated (rank %d, SCI=%.6f)",
                result.config_hash,
                result.pareto_rank or -1,
                sci or 0,
            )

        return result

    @property
    def frontier_size(self) -> int:
        return len(self._store.pareto_frontier())

    @property
    def total_experiments(self) -> int:
        return self._store.count()

    def summary(self) -> dict:
        """Quick summary with SCI-first metrics."""
        completed = self._store.count(ExperimentStatus.COMPLETED)
        failed = self._store.count(ExperimentStatus.FAILED)
        frontier = self._store.pareto_frontier()

        best_sci = min(
            (r.metrics.sci_per_token for r in frontier if r.metrics.sci_per_token),
            default=None,
        )
        # Project best SCI to 1M tokens/day for intuition
        scale = sci_at_scale(best_sci) if best_sci else None

        return {
            "total_experiments": self._store.count(),
            "completed": completed,
            "failed": failed,
            "frontier_size": len(frontier),
            "best_sci": best_sci,
            "best_sci_scale": scale,
            "best_bpb": min(
                (r.metrics.val_bpb for r in frontier if r.metrics.val_bpb),
                default=None,
            ),
            "best_energy": min(
                (r.metrics.energy_per_token_j for r in frontier
                 if r.metrics.energy_per_token_j),
                default=None,
            ),
            "best_throughput": max(
                (r.metrics.tokens_per_sec for r in frontier if r.metrics.tokens_per_sec),
                default=None,
            ),
            "carbon_intensity": self._sci.carbon_intensity_gco2_per_kwh,
        }
