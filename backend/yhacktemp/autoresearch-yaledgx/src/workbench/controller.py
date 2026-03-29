"""Research Controller — the autonomous experiment loop.

This is the brain. It ties together:
  Strategy -> Executor -> Evaluator -> repeat

Runs on ARM efficiency cores (A725), keeps the GPU free for experiments.
Target overhead: <5% of GPU time.

"Modify code, train, evaluate, keep-or-discard, repeat."
"""

from __future__ import annotations

import logging
import signal
import time
from dataclasses import replace
from pathlib import Path

from workbench.benchmark.carbon import SciConfig
from workbench.benchmark.thermal import ThermalMonitor
from workbench.display import display_frontier_table, display_scatter_ascii, display_summary
from workbench.evaluator import Evaluator
from workbench.executor import Executor
from workbench.store.database import ResultStore
from workbench.store.models import ExperimentConfig, ExperimentStatus, SearchStrategy
from workbench.strategy import BaseStrategy, BayesianStrategy, GridStrategy, RandomStrategy

logger = logging.getLogger(__name__)

# Strategy auto-switching thresholds
_GRID_TO_RANDOM_AFTER = 0  # Switch immediately if grid is exhausted
_RANDOM_TO_BAYESIAN_AFTER = 20  # Need >=20 data points for Bayesian


class ResearchController:
    """Autonomous research loop — the core orchestrator."""

    def __init__(
        self,
        db_path: str | Path = "experiments/results.db",
        strategy_name: str = "auto",
        max_iterations: int = 100,
        cooldown_sec: float = 10.0,
        usd_per_kwh: float = 0.12,
        sci_config: SciConfig | None = None,
        total_time_sec: float | None = None,
        time_budget_per_experiment: int | None = None,
        live_tui: bool = False,
    ) -> None:
        self._store = ResultStore(db_path)
        self._executor = Executor()
        self._sci_config = sci_config or SciConfig()
        self._evaluator = Evaluator(self._store, usd_per_kwh, self._sci_config)
        self._thermal = ThermalMonitor()
        self._max_iterations = max_iterations
        self._cooldown_sec = cooldown_sec
        self._strategy_name = strategy_name
        self._strategy: BaseStrategy | None = None
        self._running = False
        self._iteration = 0
        self._total_time_sec = total_time_sec
        self._time_budget_override = time_budget_per_experiment
        self._start_time: float = 0.0
        self._live_tui = live_tui
        self._dashboard = None  # set when live_tui is active

        # Graceful shutdown on SIGINT/SIGTERM
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def run(self) -> None:
        """Execute the autonomous research loop."""
        self._running = True
        self._start_time = time.time()
        self._strategy = self._select_strategy()
        completed_count = self._store.count(ExperimentStatus.COMPLETED)

        time_label = (
            f", total_time={self._fmt_time(self._total_time_sec)}"
            if self._total_time_sec else ""
        )
        logger.info(
            "\U0001f680 Research loop starting \u2014 strategy=%s, max_iter=%d%s, "
            "existing_results=%d",
            self._strategy.name,
            self._max_iterations,
            time_label,
            completed_count,
        )
        logger.info("\U0001f4c1 Results \u2192 %s", self._store.db_path)

        if self._live_tui:
            self._run_with_dashboard(completed_count)
        else:
            self._run_loop()

        # Final summary (always outside Live context so tables print normally)
        self._show_final_summary()
        logger.info("\U0001f4c1 Results \u2192 %s", self._store.db_path)
        self._store.close()

    # ── Live TUI wrapper ────────────────────────────────────────────────

    def _run_with_dashboard(self, completed_count: int) -> None:
        """Wrap the loop in a Rich Live display with a GPU poller thread."""
        from rich.console import Console
        from rich.live import Live

        from workbench.live_dashboard import (
            DashboardState,
            GpuPoller,
            build_dashboard,
        )

        self._dashboard = DashboardState(
            max_iterations=self._max_iterations,
            start_time=self._start_time,
            total_time_sec=self._total_time_sec,
            strategy_name=self._strategy.name,
            completed=completed_count,
        )
        # Force terminal mode so Live TUI works even if stderr was redirected
        live_console = Console(force_terminal=True)
        with Live(
            build_dashboard(self._dashboard),
            refresh_per_second=2,
            transient=True,
            console=live_console,
        ) as live:
            poller = GpuPoller(self._dashboard, live)
            poller.start()
            try:
                self._run_loop()
            finally:
                poller.stop()
                self._dashboard = None

    # ── Core experiment loop ────────────────────────────────────────────

    def _run_loop(self) -> None:
        """The actual experiment loop — works with or without Live dashboard."""
        while self._running and self._iteration < self._max_iterations:
            # Total time check
            if self._is_time_up():
                logger.info(
                    "\u23f1\ufe0f  Total time limit reached (%.0fs). Stopping.",
                    self._total_time_sec,
                )
                break

            self._iteration += 1
            history = self._store.all_results(ExperimentStatus.COMPLETED)

            # Maybe switch strategy (auto mode)
            if self._strategy_name == "auto":
                self._strategy = self._auto_select_strategy(len(history))

            # Propose next experiment
            config = self._strategy.propose(history)
            if config is None:
                logger.info(
                    "Strategy %s exhausted after %d iterations",
                    self._strategy.name,
                    self._iteration,
                )
                if self._strategy_name == "auto":
                    self._strategy = self._auto_select_strategy(
                        len(history), force_advance=True
                    )
                    config = self._strategy.propose(history)
                if config is None:
                    logger.info("All strategies exhausted. Stopping.")
                    break

            # Override per-experiment time budget if set
            config = self._apply_time_budget(config)

            # Skip if already run
            if self._store.exists(config.config_hash):
                logger.info("Skipping duplicate config %s", config.config_hash)
                continue

            # Thermal gate
            if not self._thermal.is_safe():
                logger.warning(
                    "\u26a0\ufe0f  Thermal threshold exceeded \u2014 cooling down for %.0fs",
                    self._cooldown_sec,
                )
                time.sleep(self._cooldown_sec)
                if not self._thermal.is_safe():
                    logger.error("Still too hot after cooldown. Stopping loop.")
                    break

            # ── Dashboard: mark experiment start ────────────────────────
            if self._dashboard:
                self._dashboard.iteration = self._iteration
                self._dashboard.current_config = config
                self._dashboard.current_status = "loading"
                self._dashboard.experiment_start = time.time()
                self._dashboard.time_budget_sec = config.time_budget_sec
                self._dashboard.strategy_name = self._strategy.name

            # Execute!
            elapsed = time.time() - self._start_time
            time_remaining = ""
            if self._total_time_sec:
                remaining = max(0, self._total_time_sec - elapsed)
                time_remaining = f" \u23f1 {self._fmt_time(remaining)} remaining"

            logger.info(
                "\u2501\u2501\u2501 Iteration %d/%d%s \u2501\u2501\u2501 [%s] %s %s batch=%d",
                self._iteration,
                self._max_iterations,
                time_remaining,
                self._strategy.name,
                config.model_name.split("/")[-1],
                config.quantization.value,
                config.batch_size,
            )

            strategy_enum = SearchStrategy(self._strategy.name)

            # Phase callback: harness reports exactly what it's doing
            # so the dashboard shows real phases, not a guessing game.
            def _on_phase(phase: str) -> None:
                if self._dashboard:
                    self._dashboard.current_status = phase

            result = self._executor.run(
                config, strategy_enum, on_phase=_on_phase,
            )

            # Evaluate and update frontier
            result = self._evaluator.evaluate(result)

            # Inform the strategy
            self._strategy.update(result)

            # ── Dashboard: update with results ─────────────────────────
            if self._dashboard:
                self._dashboard.current_status = "idle"
                self._dashboard.current_config = None
                self._dashboard.last_result = result
                self._dashboard.completed = self._store.count(
                    ExperimentStatus.COMPLETED
                )
                self._dashboard.failed = self._store.count(
                    ExperimentStatus.FAILED
                )
                self._dashboard.frontier = self._store.pareto_frontier()
                self._dashboard.strategy_name = self._strategy.name

            # Progress display every 5 iterations (skip in live mode)
            if self._iteration % 5 == 0 and not self._dashboard:
                self._show_progress()

    def _is_time_up(self) -> bool:
        """Check if we've exceeded the total time limit."""
        if self._total_time_sec is None:
            return False
        return (time.time() - self._start_time) >= self._total_time_sec

    def _apply_time_budget(self, config: ExperimentConfig) -> ExperimentConfig:
        """Override the per-experiment time budget if configured.

        Also clamp to remaining total time so we don't overshoot.
        """
        budget = self._time_budget_override or config.time_budget_sec

        # Don't let a single experiment exceed the remaining total time
        if self._total_time_sec is not None:
            elapsed = time.time() - self._start_time
            remaining = max(30, self._total_time_sec - elapsed)
            budget = min(budget, int(remaining))

        if budget != config.time_budget_sec:
            config = replace(config, time_budget_sec=budget)

        return config

    def _select_strategy(self) -> BaseStrategy:
        """Select initial strategy based on config."""
        strategies = {
            "grid": GridStrategy,
            "random": RandomStrategy,
            "bayesian": BayesianStrategy,
        }
        if self._strategy_name in strategies:
            return strategies[self._strategy_name]()
        # Auto mode — start with grid
        return GridStrategy()

    def _auto_select_strategy(
        self, completed_count: int, force_advance: bool = False
    ) -> BaseStrategy:
        """Automatically switch strategy based on experiment count."""
        current_name = self._strategy.name if self._strategy else "none"

        if completed_count >= _RANDOM_TO_BAYESIAN_AFTER:
            if current_name != "bayesian" or force_advance:
                logger.info(
                    "\U0001f504 Switching to Bayesian strategy (%d completed results)",
                    completed_count,
                )
                return BayesianStrategy()
        elif force_advance or current_name == "grid":
            logger.info(
                "\U0001f504 Switching to Random strategy (%d completed results)",
                completed_count,
            )
            return RandomStrategy()

        return self._strategy or GridStrategy()

    def _show_progress(self) -> None:
        """Show intermediate progress."""
        summary = self._evaluator.summary()
        display_summary(summary)

    def _show_final_summary(self) -> None:
        """Show final results when the loop completes."""
        elapsed = time.time() - self._start_time
        logger.info("\u2501\u2501\u2501 Research loop complete (%.1fs elapsed) \u2501\u2501\u2501", elapsed)
        summary = self._evaluator.summary()
        display_summary(summary)

        all_results = self._store.all_results(ExperimentStatus.COMPLETED)
        if all_results:
            display_frontier_table(all_results)
            display_scatter_ascii(all_results)

    @staticmethod
    def _fmt_time(seconds: float | None) -> str:
        """Format seconds as human-readable m:ss or h:mm:ss."""
        if seconds is None:
            return "\u221e"
        s = int(seconds)
        if s < 3600:
            return f"{s // 60}:{s % 60:02d}"
        return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"

    def _handle_signal(self, signum, frame) -> None:
        logger.info("Received signal %d \u2014 stopping gracefully...", signum)
        self._running = False
