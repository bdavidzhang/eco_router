#!/usr/bin/env python3
"""Comprehensive offline analysis of ALL existing workbench runs.

Scans runs/ for experiment results AND sensor logs, aggregates everything,
and produces a rich terminal report. Works with v1 (26-col), v3 (46-col),
and v4 (68-col) sensor logs — detects available columns dynamically.

No GPUs harmed. No models loaded. Just vibes, data, and pretty tables.

Usage:
    python analyze_all.py                          # default
    python analyze_all.py --runs-dir runs/ --export
    python analyze_all.py --region eu_france --top-n 5
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import statistics
import sys
import types
from datetime import datetime
from pathlib import Path

# ── Lazy imports (avoid torch/numpy import chain) ───────────────────────

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
_benchmark_pkg = types.ModuleType("workbench.benchmark")
_benchmark_pkg.__path__ = [
    str(Path(__file__).resolve().parent / "src" / "workbench" / "benchmark")
]
_benchmark_pkg.__package__ = "workbench.benchmark"
sys.modules["workbench.benchmark"] = _benchmark_pkg

from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.table import Table  # noqa: E402
from rich.text import Text  # noqa: E402

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

# ── Experiment loading (reused from analyze_runs.py) ────────────────────

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

def load_experiments_from_db(db_path: Path) -> list[ExperimentResult]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM experiments ORDER BY created_at").fetchall()
    conn.close()
    return [_row_to_result(r) for r in rows]

# ── Sensor log loading ──────────────────────────────────────────────────

def load_sensor_csv(path: Path) -> tuple[list[str], list[dict]]:
    """Load sensor CSV, return (column_names, list_of_row_dicts).

    Handles v1 (26), v3 (46), and v4 (68) column formats.
    """
    rows = []
    with path.open() as f:
        reader = csv.DictReader(f)
        cols = reader.fieldnames or []
        for row in reader:
            rows.append(row)
    return cols, rows

def safe_float(val: str | None) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None

def sensor_stats(rows: list[dict], col: str) -> dict | None:
    """Compute min/mean/median/p95/max for a numeric sensor column."""
    values = [v for r in rows if (v := safe_float(r.get(col))) is not None]
    if not values:
        return None
    values.sort()
    n = len(values)
    return {
        "min": values[0],
        "mean": statistics.mean(values),
        "median": statistics.median(values),
        "p95": values[int(n * 0.95)] if n >= 2 else values[-1],
        "max": values[-1],
        "count": n,
    }

# ── Run discovery ───────────────────────────────────────────────────────

def discover_runs(runs_dir: Path) -> list[dict]:
    """Find all run directories with any useful data."""
    discovered = []
    for run_dir in sorted(runs_dir.glob("run_*")):
        db_path = run_dir / "results.db"
        sensor_path = run_dir / "sensor_log.csv"
        config_path = run_dir / "run_config.json"

        experiments: list[ExperimentResult] = []
        if db_path.exists():
            experiments = load_experiments_from_db(db_path)

        sensor_cols: list[str] = []
        sensor_rows: list[dict] = []
        if sensor_path.exists():
            sensor_cols, sensor_rows = load_sensor_csv(sensor_path)

        # Skip if nothing useful
        if not experiments and not sensor_rows:
            continue

        run_config = {}
        if config_path.exists():
            run_config = json.loads(config_path.read_text())

        discovered.append({
            "name": run_dir.name,
            "path": run_dir,
            "experiments": experiments,
            "run_config": run_config,
            "sensor_cols": sensor_cols,
            "sensor_rows": sensor_rows,
        })

    return discovered

# ── Aggregation ─────────────────────────────────────────────────────────

def aggregate_experiments(runs: list[dict]) -> list[ExperimentResult]:
    by_hash: dict[str, ExperimentResult] = {}
    for run in runs:
        for exp in run["experiments"]:
            existing = by_hash.get(exp.config_hash)
            if existing is None or exp.created_at > existing.created_at:
                by_hash[exp.config_hash] = exp
    return list(by_hash.values())

def build_summary(results: list[ExperimentResult], sci_config: SciConfig) -> dict:
    completed = [r for r in results if r.status == ExperimentStatus.COMPLETED]
    failed = [r for r in results if r.status == ExperimentStatus.FAILED]
    frontier = [r for r in results if r.pareto_rank == 0]
    best_sci = min(
        (r.metrics.sci_per_token for r in frontier if r.metrics.sci_per_token),
        default=None,
    )
    return {
        "total_experiments": len(results),
        "completed": len(completed),
        "failed": len(failed),
        "frontier_size": len(frontier),
        "best_sci": best_sci,
        "best_sci_scale": sci_at_scale(best_sci) if best_sci else None,
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

# ── Display: Run Inventory ──────────────────────────────────────────────

def display_run_inventory(runs: list[dict]) -> None:
    table = Table(title="📂 Discovered Runs", show_lines=True)
    table.add_column("Run", style="bold")
    table.add_column("Experiments", justify="right")
    table.add_column("Completed", justify="right", style="green")
    table.add_column("Sensor Rows", justify="right", style="cyan")
    table.add_column("Sensor Cols", justify="right", style="dim")
    table.add_column("Duration", justify="right")
    table.add_column("Version", style="dim")

    for run in runs:
        exps = run["experiments"]
        completed = sum(1 for e in exps if e.status == ExperimentStatus.COMPLETED)
        rows = run["sensor_rows"]
        n_cols = len(run["sensor_cols"])
        cfg = run["run_config"]

        # Estimate duration from first/last timestamp
        duration = "-"
        if len(rows) >= 2:
            try:
                t0 = datetime.fromisoformat(rows[0]["timestamp"])
                t1 = datetime.fromisoformat(rows[-1]["timestamp"])
                secs = (t1 - t0).total_seconds()
                duration = f"{int(secs // 60)}m{int(secs % 60):02d}s"
            except (KeyError, ValueError):
                pass

        table.add_row(
            run["name"],
            str(len(exps)),
            str(completed),
            str(len(rows)),
            str(n_cols),
            duration,
            cfg.get("script_version", "-"),
        )

    console.print(table)
    console.print()

# ── Display: Sensor Analysis ───────────────────────────────────────────

def display_sensor_summary(runs: list[dict]) -> None:
    """Aggregate sensor data across all runs and show a rich summary."""
    # Merge all sensor rows (different schemas are fine — dict access)
    all_rows: list[dict] = []
    for run in runs:
        all_rows.extend(run["sensor_rows"])

    if not all_rows:
        console.print("[dim]No sensor data found in any run.[/dim]")
        return

    total_samples = len(all_rows)
    all_cols = set()
    for run in runs:
        all_cols.update(run["sensor_cols"])

    console.print(
        f"[bold]Sensor data:[/bold] {total_samples:,} samples, "
        f"{len(all_cols)} unique channels across {len(runs)} runs\n"
    )

    # ── GPU Power & Thermal ─────────────────────────────────────────
    table = Table(title="🎮 GPU Profile", show_lines=True)
    table.add_column("Metric", style="bold")
    table.add_column("Min", justify="right")
    table.add_column("Mean", justify="right", style="yellow")
    table.add_column("Median", justify="right")
    table.add_column("P95", justify="right", style="red")
    table.add_column("Max", justify="right", style="bold red")
    table.add_column("Samples", justify="right", style="dim")

    gpu_metrics = [
        ("gpu_temp_c", "Temperature (°C)", ".1f"),
        ("gpu_power_w", "Power avg (W)", ".1f"),
        ("gpu_power_instant_w", "Power instant (W)", ".1f"),
        ("gpu_util_pct", "Utilization (%)", ".0f"),
        ("gpu_mem_util_pct", "Mem util (%)", ".0f"),
        ("gpu_clock_mhz", "Clock (MHz)", ".0f"),
        ("gpu_sm_clock_mhz", "SM Clock (MHz)", ".0f"),
        ("gpu_max_clock_mhz", "Max Clock (MHz)", ".0f"),
        ("gpu_tlimit_c", "Thermal limit (°C)", ".1f"),
    ]

    for col, label, fmt in gpu_metrics:
        stats = sensor_stats(all_rows, col)
        if stats is None:
            continue
        table.add_row(
            label,
            f"{stats['min']:{fmt}}", f"{stats['mean']:{fmt}}",
            f"{stats['median']:{fmt}}", f"{stats['p95']:{fmt}}",
            f"{stats['max']:{fmt}}", str(stats["count"]),
        )

    console.print(table)
    console.print()

    # ── Throttle / fault detection ──────────────────────────────────
    throttle_cols = [
        ("gpu_hw_thermal_throttle", "GPU HW Thermal Throttle"),
        ("gpu_hw_slowdown", "GPU HW Slowdown"),
        ("gpu_sw_power_cap", "GPU SW Power Cap"),
        ("gpu_power_brake", "GPU Power Brake"),
        ("gpu_idle", "GPU Idle Event"),
        ("cpu_throttle_max", "CPU Throttle (max state)"),
        ("nvme_temp_alarm", "NVMe Thermal Alarm"),
    ]

    events = []
    for col, label in throttle_cols:
        values = [safe_float(r.get(col)) for r in all_rows]
        nonzero = sum(1 for v in values if v is not None and v > 0)
        if nonzero > 0:
            pct = nonzero / len(values) * 100
            events.append(f"  🚨 {label}: {nonzero}/{len(values)} ({pct:.1f}%)")
        elif any(v is not None for v in values):
            events.append(f"  ✅ {label}: clean")

    if events:
        console.print(Panel(
            "\n".join(events),
            title="⚡ Throttle & Fault Events",
            border_style="yellow",
        ))
        console.print()

    # ── Board Thermal ───────────────────────────────────────────────
    tz_table = Table(title="🌡️ Board Thermal", show_lines=True)
    tz_table.add_column("Sensor", style="bold")
    tz_table.add_column("Mean (°C)", justify="right", style="yellow")
    tz_table.add_column("Max (°C)", justify="right", style="red")

    board_sensors = [
        (f"thermal_zone{i}_c", f"Zone {i}") for i in range(7)
    ] + [
        ("nvme_temp_c", "NVMe Composite"),
        ("nvme_temp2_c", "NVMe Sensor 1"),
    ] + [
        (f"nic{i}_temp_c", f"NIC {i}") for i in range(4)
    ] + [
        ("wifi_temp_c", "WiFi"),
    ]

    for col, label in board_sensors:
        stats = sensor_stats(all_rows, col)
        if stats is None:
            continue
        max_style = "bold red" if stats["max"] > 80 else ""
        tz_table.add_row(
            label,
            f"{stats['mean']:.1f}",
            Text(f"{stats['max']:.1f}", style=max_style),
        )

    console.print(tz_table)
    console.print()

    # ── Memory ──────────────────────────────────────────────────────
    mem_metrics = [
        ("mem_used_kb", "Used", 1024 * 1024, "GB"),
        ("mem_available_kb", "Available", 1024 * 1024, "GB"),
        ("mem_cached_kb", "File Cache", 1024 * 1024, "GB"),
        ("mem_file_hugepages_kb", "HugePages (model)", 1024 * 1024, "GB"),
        ("mem_anon_kb", "Anonymous", 1024 * 1024, "GB"),
        ("mem_dirty_kb", "Dirty", 1024, "MB"),
        ("swap_used_kb", "Swap Used", 1024, "MB"),
    ]

    mem_table = Table(title="💾 Memory Profile", show_lines=True)
    mem_table.add_column("Metric", style="bold")
    mem_table.add_column("Mean", justify="right", style="yellow")
    mem_table.add_column("Max", justify="right", style="red")
    has_mem = False

    for col, label, divisor, unit in mem_metrics:
        stats = sensor_stats(all_rows, col)
        if stats is None:
            continue
        has_mem = True
        mem_table.add_row(
            f"{label} ({unit})",
            f"{stats['mean'] / divisor:.1f}",
            f"{stats['max'] / divisor:.1f}",
        )

    if has_mem:
        console.print(mem_table)
        console.print()

    # ── PSI (Pressure Stall) ────────────────────────────────────────
    psi_cols = [
        ("psi_cpu_avg10", "CPU some"),
        ("psi_mem_some_avg10", "Memory some"),
        ("psi_mem_full_avg10", "Memory full"),
        ("psi_io_some_avg10", "IO some"),
    ]
    psi_events = []
    for col, label in psi_cols:
        stats = sensor_stats(all_rows, col)
        if stats is None:
            continue
        icon = "🔴" if stats["max"] > 5 else "🟡" if stats["max"] > 1 else "🟢"
        psi_events.append(
            f"  {icon} {label}: mean={stats['mean']:.2f}% "
            f"p95={stats['p95']:.2f}% max={stats['max']:.2f}%"
        )

    if psi_events:
        console.print(Panel(
            "\n".join(psi_events),
            title="📊 Pressure Stall Information (PSI)",
            border_style="blue",
        ))
        console.print()

    # ── CPU + System ────────────────────────────────────────────────
    sys_metrics = [
        ("cpu_big_avg_mhz", "CPU big avg (MHz)"),
        ("cpu_little_avg_mhz", "CPU little avg (MHz)"),
        ("load_avg_1m", "Load avg (1m)"),
        ("fan_state", "Fan state"),
    ]

    sys_table = Table(title="🖥️ System", show_lines=True)
    sys_table.add_column("Metric", style="bold")
    sys_table.add_column("Mean", justify="right", style="yellow")
    sys_table.add_column("Max", justify="right", style="red")
    has_sys = False

    for col, label in sys_metrics:
        stats = sensor_stats(all_rows, col)
        if stats is None:
            continue
        has_sys = True
        sys_table.add_row(label, f"{stats['mean']:.1f}", f"{stats['max']:.1f}")

    if has_sys:
        console.print(sys_table)
        console.print()

    # ── PCIe link state (v4 only) ───────────────────────────────────
    pcie_gen = sensor_stats(all_rows, "pcie_gen")
    pcie_width = sensor_stats(all_rows, "pcie_width")
    if pcie_gen and pcie_width:
        console.print(Panel(
            f"  Gen: min={pcie_gen['min']:.0f} mean={pcie_gen['mean']:.1f} max={pcie_gen['max']:.0f}\n"
            f"  Width: min={pcie_width['min']:.0f} mean={pcie_width['mean']:.1f} max={pcie_width['max']:.0f}",
            title="🔌 PCIe Link (changes under load!)",
            border_style="magenta",
        ))
        console.print()

# ── Display: All Experiments ────────────────────────────────────────────

def display_all_experiments(results: list[ExperimentResult]) -> None:
    completed = [r for r in results if r.status == ExperimentStatus.COMPLETED]
    if not completed:
        return

    completed.sort(key=lambda r: r.metrics.sci_per_token or float("inf"))

    table = Table(
        title=f"🔬 All Completed Experiments — {len(completed)} configs (by SCI ↑)",
        show_lines=True,
    )
    table.add_column("Hash", style="dim", width=12)
    table.add_column("Model", style="bold")
    table.add_column("Quant")
    table.add_column("Batch", justify="right")
    table.add_column("Seq", justify="right")
    table.add_column("SCI", justify="right", style="bold green")
    table.add_column("BPB", justify="right", style="cyan")
    table.add_column("J/tok", justify="right", style="magenta")
    table.add_column("tok/s", justify="right", style="yellow")
    table.add_column("GPU%", justify="right")
    table.add_column("Mem GB", justify="right")
    table.add_column("Watts", justify="right", style="red")
    table.add_column("P", justify="right", style="dim")

    def _f(val, fmt=".4f", suffix=""):
        return f"{val:{fmt}}{suffix}" if val is not None else "-"

    for r in completed:
        c, m = r.config, r.metrics
        model_short = c.model_name.split("/")[-1][:20]
        is_frontier = r.pareto_rank == 0
        h = "bold green" if is_frontier else "dim"
        table.add_row(
            f"[{h}]{r.config_hash}[/{h}]", model_short,
            c.quantization.value, str(c.batch_size), str(c.sequence_length),
            _f(m.sci_per_token, ".6f"), _f(m.val_bpb),
            _f(m.energy_per_token_j), _f(m.tokens_per_sec, ".1f"),
            _f(m.gpu_util_avg_pct, ".0f", "%"), _f(m.mem_used_gb, ".1f"),
            _f(m.gpu_power_avg_w, ".1f"),
            f"{'★' if is_frontier else ''}{r.pareto_rank or 0}",
        )

    console.print(table)
    console.print()

# ── Export ──────────────────────────────────────────────────────────────

def export_combined(results: list[ExperimentResult], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "combined_results.json"
    data = [r.to_dict() for r in results]
    json_path.write_text(json.dumps(data, indent=2, default=str))
    console.print(f"[green]Exported {len(data)} results → {json_path}[/green]")

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
                "config_hash": r.config_hash, "model_name": c.model_name,
                "quantization": c.quantization.value, "batch_size": c.batch_size,
                "sequence_length": c.sequence_length,
                "sci_per_token": m.sci_per_token, "val_bpb": m.val_bpb,
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
                "pareto_rank": r.pareto_rank, "status": r.status.value,
                "created_at": r.created_at,
            })

    console.print(f"[green]Exported {len(results)} results → {csv_path}[/green]")

# ── Main ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Full analysis of all workbench runs (experiments + sensors).",
    )
    parser.add_argument("--runs-dir", type=Path,
                        default=Path(__file__).resolve().parent / "runs")
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--export", action="store_true")
    parser.add_argument("--region", type=str, default="us_average")
    args = parser.parse_args()

    console.print()
    console.rule("[bold green]🔬 Full Run Analyzer — Experiments + Sensors[/bold green]")
    console.print()

    runs = discover_runs(args.runs_dir)
    if not runs:
        console.print(f"[red]No runs found in {args.runs_dir}[/red]")
        sys.exit(1)

    # ── Phase 1: Inventory ──────────────────────────────────────────
    display_run_inventory(runs)

    # ── Phase 2: Experiment analysis ────────────────────────────────
    all_experiments = aggregate_experiments(runs)
    completed = [e for e in all_experiments if e.status == ExperimentStatus.COMPLETED]

    # Collect ALL experiments (no dedup) for scatter plot
    all_raw = [exp for run in runs for exp in run["experiments"]]
    all_raw_completed = [e for e in all_raw if e.status == ExperimentStatus.COMPLETED]

    console.print(
        f"[bold]Experiments:[/bold] {len(all_experiments)} unique "
        f"({len(completed)} completed, {len(all_raw_completed)} total incl. reruns) "
        f"from {len(runs)} runs\n"
    )

    if completed:
        rankings = compute_pareto_ranks(completed)
        for exp in all_experiments:
            exp.pareto_rank = rankings.get(exp.config_hash)
        # Propagate Pareto ranks to raw experiments (for scatter)
        for exp in all_raw:
            exp.pareto_rank = rankings.get(exp.config_hash)

        frontier_count = sum(1 for r in rankings.values() if r == 0)
        console.print(
            f"Pareto frontier: [bold green]{frontier_count}[/bold green] configs\n"
        )

        sci_config = SciConfig.from_region(args.region)
        summary = build_summary(all_experiments, sci_config)
        display_summary(summary)
        console.print()

        display_frontier_table(all_experiments, top_n=args.top_n)
        console.print()

        display_all_experiments(all_experiments)
        # Scatter plot -- show ALL runs (not deduplicated)
        display_scatter_ascii(all_raw_completed)
        display_scatter_per_model(all_raw_completed)
        console.print()
    else:
        console.print("[yellow]No completed experiments — showing sensor data only.[/yellow]\n")

    # ── Phase 3: Sensor analysis ────────────────────────────────────
    console.rule("[bold cyan]🌡️ Sensor Analysis (all runs aggregated)[/bold cyan]")
    console.print()
    display_sensor_summary(runs)

    # ── Phase 4: Per-run sensor highlights ──────────────────────────
    per_run_table = Table(title="📈 Per-Run Sensor Highlights", show_lines=True)
    per_run_table.add_column("Run", style="bold")
    per_run_table.add_column("Samples", justify="right")
    per_run_table.add_column("GPU avg W", justify="right", style="yellow")
    per_run_table.add_column("GPU max °C", justify="right", style="red")
    per_run_table.add_column("GPU max %", justify="right")
    per_run_table.add_column("Load avg", justify="right")
    per_run_table.add_column("Mem used GB", justify="right")

    for run in runs:
        rows = run["sensor_rows"]
        if not rows:
            continue
        gpu_pow = sensor_stats(rows, "gpu_power_w")
        gpu_temp = sensor_stats(rows, "gpu_temp_c")
        gpu_util = sensor_stats(rows, "gpu_util_pct")
        load = sensor_stats(rows, "load_avg_1m")
        mem = sensor_stats(rows, "mem_used_kb")

        per_run_table.add_row(
            run["name"],
            str(len(rows)),
            f"{gpu_pow['mean']:.1f}" if gpu_pow else "-",
            f"{gpu_temp['max']:.0f}" if gpu_temp else "-",
            f"{gpu_util['max']:.0f}%" if gpu_util else "-",
            f"{load['mean']:.1f}" if load else "-",
            f"{mem['max'] / 1024 / 1024:.1f}" if mem else "-",
        )

    console.print(per_run_table)
    console.print()

    # ── Phase 5: Export ─────────────────────────────────────────────
    if args.export:
        export_dir = args.runs_dir / "combined"
        export_combined(all_experiments, export_dir)
        console.print()

    # ── Footer ──────────────────────────────────────────────────────
    console.rule("[dim]Source Runs[/dim]")
    for run in runs:
        n_exp = len(run["experiments"])
        n_sens = len(run["sensor_rows"])
        n_cols = len(run["sensor_cols"])
        console.print(
            f"  {run['path']}  "
            f"({n_exp} exp, {n_sens} sensor rows × {n_cols} cols)"
        )
    console.print()

if __name__ == "__main__":
    main()
