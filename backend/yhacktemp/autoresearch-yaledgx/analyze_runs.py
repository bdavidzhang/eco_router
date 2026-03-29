#!/usr/bin/env python3
"""Offline analysis of all existing workbench runs.

Scans runs/ for completed experiments, aggregates them,
recomputes the Pareto frontier across ALL runs, and displays
the same rich summary you'd see after a live session.

No GPUs harmed. No models loaded. Just vibes and data.

Usage:
    python analyze_runs.py [--runs-dir runs/] [--top-n 10] [--export]
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
import types
from pathlib import Path

# Ensure src/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

# Bypass workbench.benchmark.__init__ -- it eagerly imports torch/numpy
# via harness.py. We only need carbon.py, which is pure Python.
_benchmark_pkg = types.ModuleType("workbench.benchmark")
_benchmark_pkg.__path__ = [
    str(Path(__file__).resolve().parent / "src" / "workbench" / "benchmark")
]
_benchmark_pkg.__package__ = "workbench.benchmark"
sys.modules["workbench.benchmark"] = _benchmark_pkg

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from workbench.benchmark.carbon import SciConfig, sci_at_scale  # noqa: E402
from workbench.display import (  # noqa: E402
    display_frontier_table,
    display_scatter_ascii,
    display_scatter_per_model,
    display_summary,
)
from workbench.pareto import compute_pareto_ranks  # noqa: E402
from workbench.store.models import (  # noqa: E402
    BenchmarkMetrics,
    ExperimentConfig,
    ExperimentResult,
    ExperimentStatus,
    SearchStrategy,
)

console = Console()


# -- Loading -----------------------------------------------------------


def _row_to_result(row: sqlite3.Row) -> ExperimentResult:
    """Deserialize a DB row into an ExperimentResult."""
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


def load_experiments_from_db(db_path: Path) -> list[ExperimentResult]:
    """Load all experiments from a single results.db."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM experiments ORDER BY created_at").fetchall()
    conn.close()
    return [_row_to_result(r) for r in rows]


def discover_runs(runs_dir: Path) -> list[dict]:
    """Find all run directories that contain a results.db with data."""
    discovered = []
    for run_dir in sorted(runs_dir.glob("run_*")):
        db_path = run_dir / "results.db"
        if not db_path.exists():
            continue

        experiments = load_experiments_from_db(db_path)
        if not experiments:
            continue

        sensor_path = run_dir / "sensor_log.csv"
        sensor_samples = 0
        if sensor_path.exists():
            sensor_samples = sum(1 for _ in sensor_path.open()) - 1  # minus header

        config_path = run_dir / "run_config.json"
        run_config = {}
        if config_path.exists():
            run_config = json.loads(config_path.read_text())

        discovered.append({
            "name": run_dir.name,
            "path": run_dir,
            "experiments": experiments,
            "run_config": run_config,
            "sensor_samples": sensor_samples,
        })

    return discovered


# -- Aggregation -------------------------------------------------------


def aggregate_experiments(runs: list[dict]) -> list[ExperimentResult]:
    """Merge experiments across runs, dedup by config_hash (keep latest)."""
    by_hash: dict[str, ExperimentResult] = {}
    for run in runs:
        for exp in run["experiments"]:
            existing = by_hash.get(exp.config_hash)
            if existing is None or exp.created_at > existing.created_at:
                by_hash[exp.config_hash] = exp
    return list(by_hash.values())


def build_summary(results: list[ExperimentResult], sci_config: SciConfig) -> dict:
    """Build the summary dict matching Evaluator.summary() shape."""
    completed = [r for r in results if r.status == ExperimentStatus.COMPLETED]
    failed = [r for r in results if r.status == ExperimentStatus.FAILED]
    frontier = [r for r in results if r.pareto_rank == 0]

    best_sci = min(
        (r.metrics.sci_per_token for r in frontier if r.metrics.sci_per_token),
        default=None,
    )
    scale = sci_at_scale(best_sci) if best_sci else None

    return {
        "total_experiments": len(results),
        "completed": len(completed),
        "failed": len(failed),
        "frontier_size": len(frontier),
        "best_sci": best_sci,
        "best_sci_scale": scale,
        "best_bpb": min(
            (r.metrics.val_bpb for r in frontier if r.metrics.val_bpb is not None),
            default=None,
        ),
        "best_energy": min(
            (r.metrics.energy_per_token_j for r in frontier
             if r.metrics.energy_per_token_j is not None),
            default=None,
        ),
        "best_throughput": max(
            (r.metrics.tokens_per_sec for r in frontier
             if r.metrics.tokens_per_sec is not None),
            default=None,
        ),
        "carbon_intensity": sci_config.carbon_intensity_gco2_per_kwh,
    }


# -- Display -----------------------------------------------------------


def display_run_inventory(runs: list[dict]) -> None:
    """Show a table of discovered runs before the main analysis."""
    table = Table(title="Discovered Runs", show_lines=True)
    table.add_column("Run", style="bold")
    table.add_column("Experiments", justify="right")
    table.add_column("Completed", justify="right", style="green")
    table.add_column("Failed", justify="right", style="red")
    table.add_column("Sensor Samples", justify="right", style="cyan")
    table.add_column("Strategy", style="dim")
    table.add_column("Version", style="dim")

    total_exp = 0
    total_completed = 0
    total_failed = 0
    total_sensors = 0

    for run in runs:
        exps = run["experiments"]
        completed = sum(1 for e in exps if e.status == ExperimentStatus.COMPLETED)
        failed = sum(1 for e in exps if e.status == ExperimentStatus.FAILED)
        cfg = run["run_config"]

        total_exp += len(exps)
        total_completed += completed
        total_failed += failed
        total_sensors += run["sensor_samples"]

        table.add_row(
            run["name"],
            str(len(exps)),
            str(completed),
            str(failed),
            str(run["sensor_samples"]),
            cfg.get("strategy", "-"),
            cfg.get("script_version", "-"),
        )

    table.add_section()
    table.add_row(
        "[bold]TOTAL[/bold]",
        f"[bold]{total_exp}[/bold]",
        f"[bold green]{total_completed}[/bold green]",
        f"[bold red]{total_failed}[/bold red]",
        f"[bold cyan]{total_sensors}[/bold cyan]",
        "",
        "",
    )

    console.print(table)
    console.print()


def _fmt(val, fmt=".4f", suffix=""):
    return f"{val:{fmt}}{suffix}" if val is not None else "-"


def display_all_experiments_table(results: list[ExperimentResult]) -> None:
    """Full table of ALL experiments (not just Pareto frontier)."""
    completed = [r for r in results if r.status == ExperimentStatus.COMPLETED]
    if not completed:
        return

    completed.sort(key=lambda r: r.metrics.sci_per_token or float("inf"))

    table = Table(
        title=f"All Completed Experiments - {len(completed)} configs (by SCI asc)",
        show_lines=True,
    )
    table.add_column("Hash", style="dim", width=12)
    table.add_column("Model", style="bold")
    table.add_column("Quant", style="green")
    table.add_column("Batch", justify="right")
    table.add_column("Seq", justify="right")
    table.add_column("SCI", justify="right", style="bold green")
    table.add_column("BPB", justify="right", style="cyan")
    table.add_column("J/tok", justify="right", style="magenta")
    table.add_column("tok/s", justify="right", style="yellow")
    table.add_column("GPU%", justify="right", style="blue")
    table.add_column("Mem GB", justify="right")
    table.add_column("Watts", justify="right", style="red")
    table.add_column("Rank", justify="right", style="dim")

    for r in completed:
        c, m = r.config, r.metrics
        model_short = c.model_name.split("/")[-1][:20]
        is_frontier = r.pareto_rank == 0
        hash_style = "bold green" if is_frontier else "dim"

        table.add_row(
            f"[{hash_style}]{r.config_hash}[/{hash_style}]",
            model_short,
            c.quantization.value,
            str(c.batch_size),
            str(c.sequence_length),
            _fmt(m.sci_per_token, ".6f"),
            _fmt(m.val_bpb, ".4f"),
            _fmt(m.energy_per_token_j, ".4f"),
            _fmt(m.tokens_per_sec, ".1f"),
            _fmt(m.gpu_util_avg_pct, ".0f", "%"),
            _fmt(m.mem_used_gb, ".1f"),
            _fmt(m.gpu_power_avg_w, ".1f"),
            f"{'*' if is_frontier else ''} {r.pareto_rank if r.pareto_rank is not None else '-'}",
        )

    console.print(table)
    console.print()


# -- Export ------------------------------------------------------------


def export_combined(results: list[ExperimentResult], output_dir: Path) -> None:
    """Export aggregated results to JSON + CSV."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # JSON
    json_path = output_dir / "combined_results.json"
    data = [r.to_dict() for r in results]
    json_path.write_text(json.dumps(data, indent=2, default=str))
    console.print(f"[green]Exported {len(data)} results -> {json_path}[/green]")

    # CSV
    csv_path = output_dir / "combined_results.csv"
    if not results:
        return

    fieldnames = [
        "config_hash", "model_name", "quantization", "batch_size",
        "sequence_length", "sci_per_token", "val_bpb", "energy_per_token_j",
        "tokens_per_sec", "gpu_power_avg_w", "gpu_util_avg_pct",
        "gpu_clock_avg_mhz", "mem_used_gb", "mem_pressure_pct",
        "latency_p50_ms", "carbon_operational_g", "carbon_embodied_g",
        "nvme_temp_c", "system_load_avg", "pareto_rank", "status", "created_at",
    ]

    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            c, m = r.config, r.metrics
            writer.writerow({
                "config_hash": r.config_hash,
                "model_name": c.model_name,
                "quantization": c.quantization.value,
                "batch_size": c.batch_size,
                "sequence_length": c.sequence_length,
                "sci_per_token": m.sci_per_token,
                "val_bpb": m.val_bpb,
                "energy_per_token_j": m.energy_per_token_j,
                "tokens_per_sec": m.tokens_per_sec,
                "gpu_power_avg_w": m.gpu_power_avg_w,
                "gpu_util_avg_pct": m.gpu_util_avg_pct,
                "gpu_clock_avg_mhz": m.gpu_clock_avg_mhz,
                "mem_used_gb": m.mem_used_gb,
                "mem_pressure_pct": m.mem_pressure_pct,
                "latency_p50_ms": m.latency_p50_ms,
                "carbon_operational_g": m.carbon_operational_g,
                "carbon_embodied_g": m.carbon_embodied_g,
                "nvme_temp_c": m.nvme_temp_c,
                "system_load_avg": m.system_load_avg,
                "pareto_rank": r.pareto_rank,
                "status": r.status.value,
                "created_at": r.created_at,
            })

    console.print(f"[green]Exported {len(results)} results -> {csv_path}[/green]")


# -- Main --------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze all existing workbench runs (no GPU required).",
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "runs",
        help="Path to the runs/ directory (default: ./runs)",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="Show top N Pareto-optimal configs (default: 10)",
    )
    parser.add_argument(
        "--export",
        action="store_true",
        help="Export combined results to JSON + CSV in runs/combined/",
    )
    parser.add_argument(
        "--region",
        type=str,
        default="us_average",
        help="Grid carbon intensity region (default: us_average)",
    )
    args = parser.parse_args()

    console.print()
    console.rule("[bold green]Offline Run Analyzer - SCI Optimization[/bold green]")
    console.print()

    # Discover runs
    runs = discover_runs(args.runs_dir)
    if not runs:
        console.print(f"[red]No runs with data found in {args.runs_dir}[/red]")
        sys.exit(1)

    display_run_inventory(runs)

    # Aggregate & dedup (for tables and Pareto ranking)
    all_experiments = aggregate_experiments(runs)
    completed = [e for e in all_experiments if e.status == ExperimentStatus.COMPLETED]

    # Also collect ALL experiments across runs (no dedup) for the scatter
    all_raw = [exp for run in runs for exp in run["experiments"]]
    all_raw_completed = [e for e in all_raw if e.status == ExperimentStatus.COMPLETED]

    console.print(
        f"[bold]Aggregated:[/bold] {len(all_experiments)} unique experiments "
        f"({len(completed)} completed, {len(all_raw_completed)} total incl. reruns) "
        f"from {len(runs)} runs\n"
    )

    if not completed:
        console.print("[red]No completed experiments found. Nothing to analyze.[/red]")
        sys.exit(1)

    # Recompute Pareto ranks across the full combined set
    rankings = compute_pareto_ranks(completed)
    for exp in all_experiments:
        exp.pareto_rank = rankings.get(exp.config_hash)
    # Propagate Pareto ranks to raw experiments too (for scatter)
    for exp in all_raw:
        exp.pareto_rank = rankings.get(exp.config_hash)

    frontier_count = sum(1 for r in rankings.values() if r == 0)
    console.print(f"Pareto frontier: [bold green]{frontier_count}[/bold green] configs\n")

    # Build & display summary
    sci_config = SciConfig.from_region(args.region)
    summary = build_summary(all_experiments, sci_config)
    display_summary(summary)
    console.print()

    # Frontier table (top N)
    display_frontier_table(all_experiments, top_n=args.top_n)
    console.print()

    # Full experiment table
    display_all_experiments_table(all_experiments)

    # Scatter plots — show ALL runs (not deduplicated)
    display_scatter_ascii(all_raw_completed)
    display_scatter_per_model(all_raw_completed)
    console.print()

    # Sensor log summary
    total_sensors = sum(r["sensor_samples"] for r in runs)
    console.print(
        f"Sensor data: [bold cyan]{total_sensors:,}[/bold cyan] "
        f"samples across {len(runs)} runs"
    )
    console.print()

    # Export
    if args.export:
        export_dir = args.runs_dir / "combined"
        export_combined(all_experiments, export_dir)
        console.print()

    # List run dirs
    console.rule("[dim]Source Runs[/dim]")
    for run in runs:
        exp_count = len(run["experiments"])
        console.print(f"  {run['path']}  ({exp_count} experiments)")
    console.print()


if __name__ == "__main__":
    main()
