#!/usr/bin/env python3
"""Extract fresh benchmark data from SQLite source-of-truth databases.

Reads all results.db files, deduplicates by config_hash (keeps latest),
filters to completed experiments with valid metrics, recomputes Pareto ranks
on (SCI ↓, BPB ↓), and writes data.js for the website.

Usage:
    python3 extract_data.py
"""

import glob
import json
import sqlite3
from pathlib import Path

RUNS_DIR = Path(__file__).resolve().parent.parent / "yhacktemp" / "yhacktemp" / "autoresearch-yaledgx" / "runs"
OUTPUT_PATH = Path(__file__).resolve().parent / "data.js"


def load_completed_from_all_dbs() -> list[dict]:
    """Load all completed experiments from every results.db."""
    db_paths = sorted(glob.glob(str(RUNS_DIR / "run_*" / "results.db")))
    print(f"📂 Found {len(db_paths)} databases")

    all_rows = []
    for db_path in db_paths:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM experiments WHERE status='completed' ORDER BY created_at"
        ).fetchall()
        all_rows.extend(dict(r) for r in rows)
        conn.close()

    print(f"📊 Total completed rows across all DBs: {len(all_rows)}")
    return all_rows


def deduplicate_by_config_hash(rows: list[dict]) -> list[dict]:
    """Keep only the latest run per config_hash."""
    by_hash: dict[str, dict] = {}
    for row in rows:
        h = row["config_hash"]
        if h not in by_hash or row["created_at"] > by_hash[h]["created_at"]:
            by_hash[h] = row

    deduped = list(by_hash.values())
    print(f"🔑 After dedup: {len(deduped)} unique configs")
    return deduped


def filter_valid_metrics(rows: list[dict]) -> list[dict]:
    """Drop experiments where SCI or BPB is null (can't rank them)."""
    valid = []
    for row in rows:
        metrics = json.loads(row["metrics_json"])
        if metrics.get("sci_per_token") is not None and metrics.get("val_bpb") is not None:
            valid.append(row)

    dropped = len(rows) - len(valid)
    if dropped:
        print(f"⚠️  Dropped {dropped} experiments with null SCI/BPB metrics")
    print(f"✅ Valid experiments: {len(valid)}")
    return valid


def compute_pareto_ranks(rows: list[dict]) -> list[dict]:
    """Recompute Pareto ranks on (SCI ↓, BPB ↓).

    Rank 0 = Pareto frontier (not dominated by anything).
    Uses iterative peeling: remove rank-0, recompute on remainder, etc.
    """
    # Parse metrics once
    indexed = []
    for row in rows:
        metrics = json.loads(row["metrics_json"])
        indexed.append({
            "row": row,
            "sci": metrics["sci_per_token"],
            "bpb": metrics["val_bpb"],
            "rank": None,
        })

    remaining = list(range(len(indexed)))
    current_rank = 0

    while remaining:
        # Find non-dominated points in current set
        non_dominated = []
        for i in remaining:
            dominated = False
            for j in remaining:
                if i == j:
                    continue
                # j dominates i if j is <= on both objectives and < on at least one
                if (indexed[j]["sci"] <= indexed[i]["sci"] and
                    indexed[j]["bpb"] <= indexed[i]["bpb"] and
                    (indexed[j]["sci"] < indexed[i]["sci"] or
                     indexed[j]["bpb"] < indexed[i]["bpb"])):
                    dominated = True
                    break
            if not dominated:
                non_dominated.append(i)

        for i in non_dominated:
            indexed[i]["rank"] = current_rank

        remaining = [i for i in remaining if indexed[i]["rank"] is None]
        current_rank += 1

    # Write ranks back
    for item in indexed:
        item["row"]["_pareto_rank"] = item["rank"]

    frontier_count = sum(1 for item in indexed if item["rank"] == 0)
    print(f"🏆 Pareto frontier: {frontier_count} configs at rank 0")
    return [item["row"] for item in indexed]


def format_experiment(row: dict) -> dict:
    """Format a DB row into the website's expected JSON shape."""
    config = json.loads(row["config_json"])
    metrics = json.loads(row["metrics_json"])

    return {
        "config_hash": row["config_hash"],
        "config": config,
        "metrics": metrics,
        "status": row["status"],
        "strategy_used": row["strategy_used"],
        "pareto_rank": row["_pareto_rank"],
        "created_at": row["created_at"],
        "error_message": row["error_message"],
    }


CARBON_PRESETS = {
    "Connecticut (ISO-NE)": 210,
    "California (CAISO)": 220,
    "Texas (ERCOT)": 380,
    "Virginia (PJM)": 340,
    "France (RTE)": 55,
    "Sweden (SvK)": 12,
    "Poland (PSE)": 620,
    "Iceland (Landsnet)": 8,
    "Germany (DE)": 310,
    "India (National Grid)": 710,
    "Australia (NEM)": 530,
    "Norway (Statnett)": 15,
    "UK (National Grid)": 180,
    "Japan (TEPCO)": 450,
    "China (State Grid)": 580,
    "Brazil (ONS)": 75,
}


def write_data_js(experiments: list[dict]) -> None:
    """Write the data.js file consumed by the website."""
    # Sort by pareto_rank, then by SCI (best first)
    experiments.sort(key=lambda e: (e["pareto_rank"], e["metrics"].get("sci_per_token", float("inf"))))

    # Collect summary stats for the header
    models = sorted(set(e["config"]["model_name"].split("/")[-1] for e in experiments))
    frontier_count = sum(1 for e in experiments if e["pareto_rank"] == 0)

    benchmark_json = json.dumps(experiments, indent=2)
    presets_json = json.dumps(CARBON_PRESETS, indent=2)

    js_content = f"""\
/* ==========================================================================
   Green LLM Bench — Data, Rendering & Interactivity
   Uses Chart.js for visualizations, vanilla JS for everything else.
   Real data extracted from autoresearch-yaledgx DGX Spark experiment databases.
   ========================================================================== */

// ─── Real Benchmark Data from DGX Spark Runs ──────────────────────────────────
// {len(experiments)} unique completed configs across {len(glob.glob(str(RUNS_DIR / 'run_*' / 'results.db')))} experiment runs on NVIDIA DGX Spark (Blackwell GB10)
// Models: {', '.join(models)} | Quantization: FP16/BF16 (no quant)
// Pareto frontier computed on (SCI ↓, BPB ↓) — {frontier_count} configs on frontier
const BENCHMARK_DATA = 
{benchmark_json}
;

const CARBON_PRESETS = {presets_json};
"""

    OUTPUT_PATH.write_text(js_content)
    print(f"\n📝 Wrote {OUTPUT_PATH} ({len(experiments)} experiments, {OUTPUT_PATH.stat().st_size:,} bytes)")


def main():
    print("🐶 PiercePuppy data extractor — fetching fresh data from source of truth!\n")

    rows = load_completed_from_all_dbs()
    rows = deduplicate_by_config_hash(rows)
    rows = filter_valid_metrics(rows)
    rows = compute_pareto_ranks(rows)

    experiments = [format_experiment(r) for r in rows]
    write_data_js(experiments)

    # Quick sanity check
    frontier = [e for e in experiments if e["pareto_rank"] == 0]
    print("\n🏆 Pareto frontier configs:")
    for e in frontier:
        model = e["config"]["model_name"].split("/")[-1]
        sci = e["metrics"]["sci_per_token"]
        bpb = e["metrics"]["val_bpb"]
        print(f"   {model} bs={e['config']['batch_size']} seq={e['config']['sequence_length']} → SCI={sci:.2e} BPB={bpb:.4f}")

    print("\n✅ Done! Website data is fresh. 🎉")


if __name__ == "__main__":
    main()
