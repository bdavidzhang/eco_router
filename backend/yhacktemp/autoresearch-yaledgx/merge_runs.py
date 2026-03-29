#!/usr/bin/env python3
"""Merge all run SQLite databases into a single JSON file for the benchmark website.

Usage:
    python merge_runs.py                          # writes to runs/combined/all_results.json
    python merge_runs.py -o ../yhack-eco-bench/results.json  # custom output path
"""

import json
import sqlite3
import sys
from pathlib import Path

RUNS_DIR = Path(__file__).parent / "runs"


def load_db(db_path: Path) -> list[dict]:
    """Load all experiments from a single SQLite results.db."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM experiments ORDER BY created_at").fetchall()
    results = []
    for row in rows:
        config = json.loads(row["config_json"])
        metrics = json.loads(row["metrics_json"])
        results.append({
            "config_hash": row["config_hash"],
            "config": config,
            "metrics": metrics,
            "status": row["status"],
            "strategy_used": row["strategy_used"],
            "pareto_rank": row["pareto_rank"],
            "created_at": row["created_at"],
            "error_message": row["error_message"],
        })
    conn.close()
    return results


def merge_all(runs_dir: Path) -> list[dict]:
    """Merge all results.db files, deduplicating by config_hash."""
    seen = {}
    db_files = sorted(runs_dir.glob("*/results.db"))
    for db_path in db_files:
        for result in load_db(db_path):
            key = result["config_hash"]
            # Keep the latest entry if duplicated across runs
            if key not in seen or result["created_at"] > seen[key]["created_at"]:
                seen[key] = result
    # Sort by created_at
    return sorted(seen.values(), key=lambda r: r["created_at"])


def main():
    output = Path(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[1] == "-o" else RUNS_DIR / "combined" / "all_results.json"
    output.parent.mkdir(parents=True, exist_ok=True)

    results = merge_all(RUNS_DIR)

    # Stats
    completed = [r for r in results if r["status"] == "completed"]
    with_metrics = [r for r in completed if r["metrics"].get("tokens_per_sec") is not None]
    models = sorted({r["config"]["model_name"] for r in results})

    print(f"Found {len(results)} unique experiments ({len(completed)} completed, {len(with_metrics)} with metrics)")
    print(f"Models: {', '.join(models)}")
    print(f"Writing to: {output}")

    with open(output, "w") as f:
        json.dump(results, f, indent=2)

    print("Done.")


if __name__ == "__main__":
    main()
