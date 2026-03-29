"""Random search strategy — the humble baseline that's surprisingly hard to beat.

Random search escapes local optima and serves as a sanity check
against fancier strategies. If Bayesian can't beat random, something's wrong.
"""

from __future__ import annotations

import random
from typing import Any

from workbench.store.models import ExperimentConfig, ExperimentResult, Quantization
from workbench.strategy.base import BaseStrategy

# Sampling ranges for random search — Qwen 3.5 family
_SEARCH_SPACE: dict[str, list[Any]] = {
    "model_name": [
        "Qwen/Qwen3.5-0.8B",   # 0.8B — fast iteration
        "Qwen/Qwen3.5-4B",     # 4B — quality/speed balance
        "Qwen/Qwen3.5-9B",     # 9B — high quality baseline
    ],
    "quantization": [Quantization.NONE],  # GPTQ/AWQ require extra deps
    "batch_size": [1, 2, 4, 8, 16, 32],
    "sequence_length": [128, 256, 512, 1024, 2048],
    "max_new_tokens": [64, 128, 256, 512],
    "temperature": [0.0, 0.5, 0.7, 1.0, 1.5],
    "use_kv_cache": [True, False],
    "dtype": ["float16", "bfloat16"],
}


class RandomStrategy(BaseStrategy):
    """Uniformly random sampling from the search space."""

    def __init__(
        self,
        search_space: dict[str, list[Any]] | None = None,
        max_proposals: int = 200,
        seed: int | None = None,
    ) -> None:
        self._space = search_space or _SEARCH_SPACE
        self._max_proposals = max_proposals
        self._proposals_made = 0
        self._seen_hashes: set[str] = set()
        self._rng = random.Random(seed)

    @property
    def name(self) -> str:
        return "random"

    def propose(self, history: list[ExperimentResult]) -> ExperimentConfig | None:
        if self._proposals_made >= self._max_proposals:
            return None

        self._seen_hashes = {r.config_hash for r in history}

        # Try up to 100 times to find a novel config (avoid infinite loops)
        for _ in range(100):
            params = {key: self._rng.choice(values) for key, values in self._space.items()}
            config = ExperimentConfig(**params)
            if config.config_hash not in self._seen_hashes:
                self._proposals_made += 1
                return config

        return None  # Search space exhausted (unlikely but safe)

    def update(self, result: ExperimentResult) -> None:
        self._seen_hashes.add(result.config_hash)
