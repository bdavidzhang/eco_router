"""Bayesian optimization strategy via Optuna.

This is the big gun — use it after >=20 data points from grid/random
have mapped the landscape. Optuna's TPE sampler naturally handles
multi-objective optimization, which is exactly what we need for
our quality x SCI x throughput Pareto frontier.

SCI = (E x I) + M — the Green Software Foundation's carbon score.
"""

from __future__ import annotations

import logging

import optuna
from optuna.samplers import TPESampler

from workbench.store.models import ExperimentConfig, ExperimentResult, Quantization
from workbench.strategy.base import BaseStrategy

logger = logging.getLogger(__name__)

# Suppress Optuna's chatty logging
optuna.logging.set_verbosity(optuna.logging.WARNING)

# Qwen 3.5 model family — shared across all strategies
_MODEL_CHOICES = [
    "Qwen/Qwen3.5-0.8B",
    "Qwen/Qwen3.5-4B",
    "Qwen/Qwen3.5-9B",
]


def _get_sci_or_fallback(m) -> float:
    """Get SCI score, falling back to energy-derived estimate."""
    if m.sci_per_token is not None:
        return m.sci_per_token
    if m.energy_per_token_j is not None:
        return (m.energy_per_token_j / 3_600_000) * 400.0 + 0.00003
    return float("inf")


class BayesianStrategy(BaseStrategy):
    """Multi-objective Bayesian optimization using Optuna TPE."""

    def __init__(self, max_trials: int = 100, seed: int = 42) -> None:
        self._max_trials = max_trials
        self._study = optuna.create_study(
            directions=["minimize", "minimize", "maximize"],  # bpb, SCI, throughput
            sampler=TPESampler(seed=seed, multivariate=True),
            study_name="workbench-pareto-sci",
        )
        self._pending_trial: optuna.trial.Trial | None = None
        self._seen_hashes: set[str] = set()

    @property
    def name(self) -> str:
        return "bayesian"

    def propose(self, history: list[ExperimentResult]) -> ExperimentConfig | None:
        if len(self._study.trials) >= self._max_trials:
            return None

        self._seen_hashes = {r.config_hash for r in history}

        # Seed Optuna with historical results it hasn't seen
        self._seed_from_history(history)

        # Create a new trial and sample params
        trial = self._study.ask()
        self._pending_trial = trial

        config = self._trial_to_config(trial)

        # If we've already run this config, tell Optuna and try again
        for _ in range(10):
            if config.config_hash not in self._seen_hashes:
                return config
            self._study.tell(trial, values=[float("inf"), float("inf"), 0.0])
            trial = self._study.ask()
            self._pending_trial = trial
            config = self._trial_to_config(trial)

        return config

    def update(self, result: ExperimentResult) -> None:
        self._seen_hashes.add(result.config_hash)
        if self._pending_trial is None:
            return

        m = result.metrics
        val_bpb = m.val_bpb if m.val_bpb is not None else float("inf")
        sci = _get_sci_or_fallback(m)
        throughput = m.tokens_per_sec if m.tokens_per_sec is not None else 0.0

        self._study.tell(self._pending_trial, values=[val_bpb, sci, throughput])
        self._pending_trial = None

    def _trial_to_config(self, trial: optuna.trial.Trial) -> ExperimentConfig:
        """Map Optuna trial suggestions to an ExperimentConfig."""
        model_name = trial.suggest_categorical("model_name", _MODEL_CHOICES)
        batch_size = trial.suggest_categorical("batch_size", [1, 2, 4, 8, 16, 32])
        sequence_length = trial.suggest_categorical(
            "sequence_length", [128, 256, 512, 1024, 2048]
        )
        max_new_tokens = trial.suggest_categorical("max_new_tokens", [64, 128, 256, 512])
        use_kv_cache = trial.suggest_categorical("use_kv_cache", [True, False])
        dtype = trial.suggest_categorical("dtype", ["float16", "bfloat16"])

        return ExperimentConfig(
            model_name=model_name,
            quantization=Quantization.NONE,
            batch_size=batch_size,
            sequence_length=sequence_length,
            max_new_tokens=max_new_tokens,
            use_kv_cache=use_kv_cache,
            dtype=dtype,
        )

    def _seed_from_history(self, history: list[ExperimentResult]) -> None:
        """Feed historical results into Optuna so it learns from prior runs."""
        known_hashes = {t.user_attrs.get("config_hash") for t in self._study.trials}
        for result in history:
            if result.config_hash in known_hashes:
                continue
            if result.metrics.val_bpb is None:
                continue

            m = result.metrics
            distributions = {
                "model_name": optuna.distributions.CategoricalDistribution(
                    _MODEL_CHOICES
                ),
                "batch_size": optuna.distributions.CategoricalDistribution(
                    [1, 2, 4, 8, 16, 32]
                ),
                "sequence_length": optuna.distributions.CategoricalDistribution(
                    [128, 256, 512, 1024, 2048]
                ),
                "max_new_tokens": optuna.distributions.CategoricalDistribution(
                    [64, 128, 256, 512]
                ),
                "use_kv_cache": optuna.distributions.CategoricalDistribution(
                    [True, False]
                ),
                "dtype": optuna.distributions.CategoricalDistribution(
                    ["float16", "bfloat16"]
                ),
            }
            c = result.config
            params = {
                "model_name": c.model_name,
                "batch_size": c.batch_size,
                "sequence_length": c.sequence_length,
                "max_new_tokens": c.max_new_tokens,
                "use_kv_cache": c.use_kv_cache,
                "dtype": c.dtype,
            }
            trial = optuna.trial.create_trial(
                params=params,
                distributions=distributions,
                values=[
                    m.val_bpb or float("inf"),
                    _get_sci_or_fallback(m),
                    m.tokens_per_sec or 0.0,
                ],
            )
            trial.set_user_attr("config_hash", result.config_hash)
            self._study.add_trial(trial)
