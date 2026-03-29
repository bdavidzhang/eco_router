"""CLI for the Auto-Improving LLM Research Workbench.

Usage:
    workbench run [--strategy auto] [--region us_average] [--total-time 600]
    workbench results [--pareto] [--sort sci]
    workbench export [--format json|csv] [--output results.json]
    workbench status
    workbench regions

SCI = (E x I) + M per R — the sustainability optimization target.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import click
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from workbench.benchmark.carbon import CARBON_INTENSITY_PRESETS, SciConfig
from workbench.controller import ResearchController
from workbench.display import (
    display_frontier_table,
    display_scatter_ascii,
    display_summary,
    export_results_json,
)
from workbench.store.database import ResultStore
from workbench.store.models import ExperimentStatus

console = Console()

_DB_DEFAULT = "experiments/results.db"


def _setup_logging(
    verbose: bool = False,
    log_file: str | None = None,
) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handlers: list[logging.Handler] = [
        RichHandler(rich_tracebacks=True, show_path=False),
    ]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, mode="a")
        fh.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"),
        )
        handlers.append(fh)
    logging.basicConfig(level=level, format="%(message)s", datefmt="[%X]", handlers=handlers)


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
def main(verbose: bool) -> None:
    """🔬 Auto-Improving LLM Research Workbench

    Discover Pareto-optimal LLM configs optimizing SCI (Software Carbon Intensity).
    SCI = (E x I) + M — gCO2 per token. Lower is greener.
    """
    _setup_logging(verbose)


@main.command()
@click.option(
    "--strategy", "-s",
    type=click.Choice(["grid", "random", "bayesian", "auto"]),
    default="auto",
    help="Search strategy (default: auto)",
)
@click.option("--max-iter", "-n", default=100, help="Maximum experiment iterations")
@click.option("--db", default=_DB_DEFAULT, help="SQLite database path")
@click.option("--cooldown", default=10.0, help="Thermal cooldown seconds")
@click.option("--usd-per-kwh", default=0.12, help="Electricity cost for $/token calc")
@click.option(
    "--region", "-r",
    default="us_average",
    help="Grid carbon intensity region (run 'workbench regions' for list)",
)
@click.option(
    "--carbon-intensity", "-I",
    default=None, type=float,
    help="Override carbon intensity (gCO2/kWh). Takes precedence over --region.",
)
@click.option(
    "--embodied", "-M",
    default=0.00003, type=float,
    help="Embodied emissions per token (gCO2). Default: 0.00003",
)
@click.option(
    "--total-time", "-T",
    default=None, type=float,
    help="Total run time in seconds. Stops the loop when reached.",
)
@click.option(
    "--time-budget", "-t",
    default=None, type=int,
    help="Per-experiment time budget in seconds (default: 300).",
)
@click.option(
    '--live', is_flag=True,
    help="Enable live TUI dashboard (GPU stats + Pareto frontier, updates every 2s).",
)
@click.option(
    '--log-file',
    default=None, type=str,
    help="Also write log messages to this file (avoids piping through tee).",
)
def run(
    strategy: str,
    max_iter: int,
    db: str,
    cooldown: float,
    usd_per_kwh: float,
    region: str,
    carbon_intensity: float | None,
    embodied: float,
    total_time: float | None,
    time_budget: int | None,
    live: bool,
    log_file: str | None,
) -> None:
    """🚀 Start the autonomous research loop optimizing SCI.

    Proposes experiments, runs benchmarks, evaluates SCI (Software Carbon
    Intensity), and builds a Pareto frontier — all automatically.

    SCI = (E x I) + M  where E=energy, I=grid carbon, M=embodied emissions.
    """
    # Add file handler if requested (lets --live TUI work without | tee)
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, mode="a")
        fh.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"),
        )
        logging.getLogger().addHandler(fh)

    # Build SCI config
    if carbon_intensity is not None:
        sci_config = SciConfig(
            carbon_intensity_gco2_per_kwh=carbon_intensity,
            embodied_gco2_per_token=embodied,
        )
        intensity_label = f"{carbon_intensity:.0f} gCO2/kWh (custom)"
    else:
        sci_config = SciConfig.from_region(region, embodied_gco2_per_token=embodied)
        intensity_label = f"{sci_config.carbon_intensity_gco2_per_kwh:.0f} gCO2/kWh ({region})"

    time_label = f"{total_time:.0f}s" if total_time else "unlimited"
    budget_label = f"{time_budget}s" if time_budget else "300s (default)"

    console.print(
        "[bold green]🔬 Auto-Improving LLM Research Workbench[/bold green]\n"
        "[bold]Optimizing: SCI = (E x I) + M — gCO2 per token[/bold]\n"
        f"  Strategy: [cyan]{strategy}[/cyan]\n"
        f"  Max iterations: [cyan]{max_iter}[/cyan]\n"
        f"  Total time: [cyan]{time_label}[/cyan]\n"
        f"  Per-experiment budget: [cyan]{budget_label}[/cyan]\n"
        f"  Grid intensity (I): [green]{intensity_label}[/green]\n"
        f"  Embodied (M): [green]{embodied:.5f} gCO2/tok[/green]\n"
        f"  Database: [dim]{db}[/dim]\n"
    )

    controller = ResearchController(
        db_path=db,
        strategy_name=strategy,
        max_iterations=max_iter,
        cooldown_sec=cooldown,
        usd_per_kwh=usd_per_kwh,
        sci_config=sci_config,
        total_time_sec=total_time,
        time_budget_per_experiment=time_budget,
        live_tui=live,
    )
    controller.run()


@main.command()
@click.option("--pareto", is_flag=True, help="Show only Pareto-optimal configs")
@click.option(
    "--sort",
    type=click.Choice(["sci", "energy_per_token", "val_bpb", "tokens_per_sec", "created_at"]),
    default="sci",
    help="Sort results by metric (default: sci)",
)
@click.option("--db", default=_DB_DEFAULT, help="SQLite database path")
@click.option("--limit", "-n", default=20, help="Max results to show")
def results(pareto: bool, sort: str, db: str, limit: int) -> None:
    """📊 Query and display experiment results."""
    store = ResultStore(db)

    if pareto:
        all_results = store.pareto_frontier()
        console.print(f"[bold]Pareto frontier: {len(all_results)} configs[/bold]\n")
    else:
        all_results = store.all_results(ExperimentStatus.COMPLETED)
        console.print(f"[bold]All results: {len(all_results)} experiments[/bold]\n")

    if not all_results:
        console.print("[yellow]No results found. Run some experiments first![/yellow]")
        store.close()
        return

    sort_keys = {
        "sci": lambda r: r.metrics.sci_per_token or float("inf"),
        "energy_per_token": lambda r: r.metrics.energy_per_token_j or float("inf"),
        "val_bpb": lambda r: r.metrics.val_bpb or float("inf"),
        "tokens_per_sec": lambda r: -(r.metrics.tokens_per_sec or 0),
        "created_at": lambda r: r.created_at,
    }
    all_results.sort(key=sort_keys.get(sort, sort_keys["sci"]))

    display_frontier_table(all_results[:limit], top_n=limit)
    display_scatter_ascii(all_results)
    display_scatter_per_model(all_results)
    store.close()


@main.command()
@click.option("--db", default=_DB_DEFAULT, help="SQLite database path")
def status(db: str) -> None:
    """📈 Show current workbench status and SCI frontier summary."""
    store = ResultStore(db)

    from workbench.evaluator import Evaluator

    evaluator = Evaluator(store)
    summary = evaluator.summary()
    display_summary(summary)

    frontier = store.pareto_frontier()
    if frontier:
        display_frontier_table(frontier, top_n=5)

    store.close()


@main.command()
def regions() -> None:
    """🌍 List available grid carbon intensity regions."""
    table = Table(title="🌍 Grid Carbon Intensity Presets (I)")
    table.add_column("Region", style="bold")
    table.add_column("gCO2/kWh", justify="right", style="green")
    table.add_column("Notes", style="dim")

    notes = {
        "renewable_100": "The dream — 100% renewable",
        "iceland": "Geothermal + hydro",
        "eu_sweden": "Hydro + nuclear",
        "eu_france": "Nuclear-dominant",
        "brazil": "Hydro-dominant",
        "us_oregon": "Hydro-heavy (us-west-2)",
        "canada": "Hydro-heavy",
        "uk": "Wind + gas",
        "eu_average": "EU average",
        "us_california": "High solar/wind",
        "us_virginia": "Major cloud region (us-east-1)",
        "eu_germany": "Still coal-heavy",
        "us_texas": "ERCOT — gas + wind",
        "us_average": "US national average",
        "japan": "Gas + coal",
        "australia": "Coal + gas",
        "china_average": "Coal-heavy",
        "india_average": "Coal-dominant",
        "eu_poland": "Coal-dominant",
    }

    # Sort by intensity (greenest first)
    for region, intensity in sorted(CARBON_INTENSITY_PRESETS.items(), key=lambda x: x[1]):
        table.add_row(region, f"{intensity:.0f}", notes.get(region, ""))

    console.print(table)
    console.print("\n[dim]Usage: workbench run --region eu_france[/dim]")
    console.print("[dim]       workbench run --carbon-intensity 55[/dim]")


@main.command()
@click.option(
    "--format", "fmt",
    type=click.Choice(["json", "csv"]),
    default="json",
    help="Export format",
)
@click.option("--output", "-o", default="experiments/export.json", help="Output file path")
@click.option("--db", default=_DB_DEFAULT, help="SQLite database path")
def export(fmt: str, output: str, db: str) -> None:
    """💾 Export results to JSON or CSV."""
    store = ResultStore(db)
    all_results = store.all_results()

    if not all_results:
        console.print("[yellow]No results to export.[/yellow]")
        store.close()
        return

    if fmt == "json":
        export_results_json(all_results, output)
    elif fmt == "csv":
        _export_csv(all_results, output)

    store.close()


def _export_csv(results: list, output: str) -> None:
    """Export results as CSV with SCI + system sensor columns."""
    import csv

    if not results:
        return

    path = Path(output).with_suffix(".csv")
    fieldnames = [
        "config_hash", "model_name", "quantization", "batch_size",
        "sequence_length", "sci_per_token", "val_bpb", "energy_per_token_j",
        "tokens_per_sec", "gpu_power_avg_w", "gpu_util_avg_pct",
        "gpu_clock_avg_mhz", "mem_used_gb", "mem_pressure_pct",
        "latency_p50_ms", "carbon_operational_g", "carbon_embodied_g",
        "nvme_temp_c", "system_load_avg",
        "pareto_rank", "status", "created_at",
    ]

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow({
                "config_hash": r.config_hash,
                "model_name": r.config.model_name,
                "quantization": r.config.quantization.value,
                "batch_size": r.config.batch_size,
                "sequence_length": r.config.sequence_length,
                "sci_per_token": r.metrics.sci_per_token,
                "val_bpb": r.metrics.val_bpb,
                "energy_per_token_j": r.metrics.energy_per_token_j,
                "tokens_per_sec": r.metrics.tokens_per_sec,
                "gpu_power_avg_w": r.metrics.gpu_power_avg_w,
                "gpu_util_avg_pct": r.metrics.gpu_util_avg_pct,
                "gpu_clock_avg_mhz": r.metrics.gpu_clock_avg_mhz,
                "mem_used_gb": r.metrics.mem_used_gb,
                "mem_pressure_pct": r.metrics.mem_pressure_pct,
                "latency_p50_ms": r.metrics.latency_p50_ms,
                "carbon_operational_g": r.metrics.carbon_operational_g,
                "carbon_embodied_g": r.metrics.carbon_embodied_g,
                "nvme_temp_c": r.metrics.nvme_temp_c,
                "system_load_avg": r.metrics.system_load_avg,
                "pareto_rank": r.pareto_rank,
                "status": r.status.value,
                "created_at": r.created_at,
            })

    console.print(f"[green]Exported {len(results)} results to {path}[/green]")


if __name__ == "__main__":
    main()
