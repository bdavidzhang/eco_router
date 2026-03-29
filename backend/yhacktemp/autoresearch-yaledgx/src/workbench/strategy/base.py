"""Abstract base for search strategies.

Every strategy does exactly two things:
1. Propose an experiment config.
2. Learn from a completed result.

That's it. No god-objects. SOLID, baby.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from workbench.store.models import ExperimentConfig, ExperimentResult


class BaseStrategy(ABC):
    """Interface for experiment search strategies."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable strategy name."""

    @abstractmethod
    def propose(self, history: list[ExperimentResult]) -> ExperimentConfig | None:
        """Propose the next experiment config, or None if search is exhausted.

        Args:
            history: All completed experiment results so far.

        Returns:
            Next config to try, or None if no more configs to explore.
        """

    @abstractmethod
    def update(self, result: ExperimentResult) -> None:
        """Inform the strategy about a completed experiment.

        Args:
            result: The completed experiment result.
        """
