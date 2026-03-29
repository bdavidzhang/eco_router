"""Experiment executor — the thing that actually runs experiments.

Takes a config, calls the benchmark harness, wraps the result.
Handles errors, timeouts, and thermal aborts gracefully.

"It modifies a single experiment config surface and the system handles the rest."
"""

from __future__ import annotations

import logging
import traceback
from datetime import datetime, timezone
from typing import Callable

from workbench.benchmark.harness import run_benchmark, clear_model_cache, PhaseCallback
from workbench.benchmark.thermal import ThermalAbortError
from workbench.store.models import (
    BenchmarkMetrics,
    ExperimentConfig,
    ExperimentResult,
    ExperimentStatus,
    SearchStrategy,
)

logger = logging.getLogger(__name__)


class Executor:
    """Runs a single experiment and returns a structured result."""

    def run(
        self,
        config: ExperimentConfig,
        strategy: SearchStrategy = SearchStrategy.RANDOM,
        on_phase: PhaseCallback | None = None,
    ) -> ExperimentResult:
        """Execute a single experiment.

        Args:
            config: What to run.
            strategy: Which strategy proposed this config.
            on_phase: Optional callback invoked with phase name strings
                      ("loading", "inference", "evaluating", "done").

        Never raises — all errors are captured in the result.
        """
        logger.info("Executing experiment %s", config.config_hash)

        try:
            metrics = run_benchmark(config, on_phase=on_phase)
            status = ExperimentStatus.COMPLETED

            # Check for thermal throttling (still a valid result, but flagged)
            if metrics.thermal_throttled:
                logger.warning(
                    "Experiment %s completed but thermal throttled — "
                    "results may be unreliable",
                    config.config_hash,
                )
                status = ExperimentStatus.DISCARDED

            return ExperimentResult(
                config=config,
                metrics=metrics,
                status=status,
                strategy_used=strategy,
                created_at=datetime.now(timezone.utc).isoformat(),
            )

        except ThermalAbortError as e:
            logger.error("Thermal abort for %s: %s", config.config_hash, e)
            return ExperimentResult(
                config=config,
                metrics=BenchmarkMetrics(),
                status=ExperimentStatus.DISCARDED,
                strategy_used=strategy,
                error_message=str(e),
                created_at=datetime.now(timezone.utc).isoformat(),
            )

        except (MemoryError, RuntimeError) as e:
            error_msg = str(e)
            # CUDA OOM is a RuntimeError with specific message
            if "out of memory" in error_msg.lower() or "CUDA" in error_msg:
                logger.error("OOM for %s: %s", config.config_hash, error_msg)
                clear_model_cache()  # Free VRAM so next experiment can proceed
            else:
                logger.error("Runtime error for %s: %s", config.config_hash, error_msg)
            return ExperimentResult(
                config=config,
                metrics=BenchmarkMetrics(),
                status=ExperimentStatus.FAILED,
                strategy_used=strategy,
                error_message=error_msg,
                created_at=datetime.now(timezone.utc).isoformat(),
            )

        except Exception as e:
            logger.error(
                "Unexpected error for %s: %s\n%s",
                config.config_hash,
                e,
                traceback.format_exc(),
            )
            return ExperimentResult(
                config=config,
                metrics=BenchmarkMetrics(),
                status=ExperimentStatus.FAILED,
                strategy_used=strategy,
                error_message=f"{type(e).__name__}: {e}",
                created_at=datetime.now(timezone.utc).isoformat(),
            )
