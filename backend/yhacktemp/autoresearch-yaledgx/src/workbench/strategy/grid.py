"""Grid search strategy — systematically sweep every combination.

Use this first to map the landscape before switching to Bayesian.
"""

from __future__ import annotations

import itertools
from typing import Any

from workbench.store.models import ExperimentConfig, ExperimentResult, Quantization
from workbench.strategy.base import BaseStrategy


# Default grid — Qwen 3.5 family, tight for fast iteration
_DEFAULT_GRID: dict[str, list[Any]] = {
    "model_name": [
        "Qwen/Qwen3.5-0.8B",  # Speed demon — loads in ~2s
        "Qwen/Qwen3.5-4B",    # Quality/speed sweet spot
    ],
    "quantization": [Quantization.NONE],  # GPTQ/AWQ require extra deps
    "batch_size": [1, 4, 16],
    "sequence_length": [256, 512, 1024],
    "dtype": ["float16", "bfloat16"],
}


class GridStrategy(BaseStrategy):
    """Exhaustive grid search over a predefined parameter grid."""

    def __init__(self, grid: dict[str, list[Any]] | None = None) -> None:
        self._grid = grid or _DEFAULT_GRID
        self._seen_hashes: set[str] = set()
        self._queue: list[ExperimentConfig] = self._build_queue()
        self._index = 0

    @property
    def name(self) -> str:
        return "grid"

    @property
    def total_configs(self) -> int:
        return len(self._queue)

    @property
    def remaining(self) -> int:
        return max(0, len(self._queue) - self._index)

    def propose(self, history: list[ExperimentResult]) -> ExperimentConfig | None:
        # Skip configs we've already run
        self._seen_hashes = {r.config_hash for r in history}
        while self._index < len(self._queue):
            config = self._queue[self._index]
            self._index += 1
            if config.config_hash not in self._seen_hashes:
                return config
        return None  # Grid exhausted

    def update(self, result: ExperimentResult) -> None:
        self._seen_hashes.add(result.config_hash)

    def _build_queue(self) -> list[ExperimentConfig]:
        """Generate all combinations from the grid."""
        keys = list(self._grid.keys())
        values = list(self._grid.values())
        configs = []
        for combo in itertools.product(*values):
            params = dict(zip(keys, combo))
            configs.append(ExperimentConfig(**params))
        return configs
