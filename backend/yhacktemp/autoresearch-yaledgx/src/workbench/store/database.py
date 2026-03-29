"""SQLite result store for experiment tracking.

One source of truth. No fancy ORM — just SQL and dataclasses.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Sequence

from workbench.store.models import (
    BenchmarkMetrics,
    ExperimentConfig,
    ExperimentResult,
    ExperimentStatus,
    SearchStrategy,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
    config_hash   TEXT PRIMARY KEY,
    config_json   TEXT NOT NULL,
    metrics_json  TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'completed',
    strategy_used TEXT NOT NULL DEFAULT 'random',
    pareto_rank   INTEGER,
    created_at    TEXT NOT NULL,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_pareto ON experiments(pareto_rank);
CREATE INDEX IF NOT EXISTS idx_status ON experiments(status);
CREATE INDEX IF NOT EXISTS idx_created ON experiments(created_at);
"""


class ResultStore:
    """Thin wrapper around SQLite for experiment persistence."""

    def __init__(self, db_path: str | Path = "experiments/results.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def save(self, result: ExperimentResult) -> None:
        """Insert or replace an experiment result."""
        self._conn.execute(
            """
            INSERT OR REPLACE INTO experiments
                (config_hash, config_json, metrics_json, status,
                 strategy_used, pareto_rank, created_at, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.config_hash,
                json.dumps(result.config.to_dict(), default=str),
                json.dumps(result.metrics.to_dict()),
                result.status.value,
                result.strategy_used.value,
                result.pareto_rank,
                result.created_at,
                result.error_message,
            ),
        )
        self._conn.commit()

    def get(self, config_hash: str) -> ExperimentResult | None:
        """Retrieve a single result by config hash."""
        row = self._conn.execute(
            "SELECT * FROM experiments WHERE config_hash = ?", (config_hash,)
        ).fetchone()
        return self._row_to_result(row) if row else None

    def exists(self, config_hash: str) -> bool:
        """Check if a config has already been run."""
        row = self._conn.execute(
            "SELECT 1 FROM experiments WHERE config_hash = ?", (config_hash,)
        ).fetchone()
        return row is not None

    def all_results(self, status: ExperimentStatus | None = None) -> list[ExperimentResult]:
        """Retrieve all results, optionally filtered by status."""
        if status:
            rows = self._conn.execute(
                "SELECT * FROM experiments WHERE status = ? ORDER BY created_at",
                (status.value,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM experiments ORDER BY created_at"
            ).fetchall()
        return [self._row_to_result(r) for r in rows]

    def pareto_frontier(self) -> list[ExperimentResult]:
        """Get all Pareto-optimal results (rank 0)."""
        rows = self._conn.execute(
            "SELECT * FROM experiments WHERE pareto_rank = 0 ORDER BY created_at"
        ).fetchall()
        return [self._row_to_result(r) for r in rows]

    def update_pareto_ranks(self, rankings: dict[str, int]) -> None:
        """Bulk update pareto ranks. rankings = {config_hash: rank}."""
        with self._conn:
            for config_hash, rank in rankings.items():
                self._conn.execute(
                    "UPDATE experiments SET pareto_rank = ? WHERE config_hash = ?",
                    (rank, config_hash),
                )

    def count(self, status: ExperimentStatus | None = None) -> int:
        if status:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM experiments WHERE status = ?", (status.value,)
            ).fetchone()
        else:
            row = self._conn.execute("SELECT COUNT(*) FROM experiments").fetchone()
        return row[0]

    def export_json(self) -> list[dict]:
        """Export all results as list of dicts (for JSON/CSV export)."""
        return [r.to_dict() for r in self.all_results()]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> ResultStore:
        return self

    def __exit__(self, *args) -> None:
        self.close()

    @staticmethod
    def _row_to_result(row: sqlite3.Row) -> ExperimentResult:
        config = ExperimentConfig.from_dict(json.loads(row["config_json"]))
        metrics = BenchmarkMetrics.from_dict(json.loads(row["metrics_json"]))
        return ExperimentResult(
            config=config,
            metrics=metrics,
            status=ExperimentStatus(row["status"]),
            strategy_used=SearchStrategy(row["strategy_used"]),
            pareto_rank=row["pareto_rank"],
            created_at=row["created_at"],
            error_message=row["error_message"],
        )
