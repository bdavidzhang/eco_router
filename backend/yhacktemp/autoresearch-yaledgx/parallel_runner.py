#!/usr/bin/env python3
"""parallel_runner.py — Run N unique experiments concurrently (v6).

Instead of 10 separate terminals, all experiments run as parallel processes
within a single terminal, with a unified Rich TUI dashboard.

Each experiment gets its own process (separate CUDA context + model cache).
The main process collects results, saves to a single DB, and shows the dashboard.

Usage (called by run_30s_v6.sh):
    python parallel_runner.py \
        --db runs/run_XXX/results.db \
        --workers 10 \
        --time-budget 30 \
        --region us_average
"""

from __future__ import annotations

import argparse
import logging
import multiprocessing as mp
import queue as queue_mod
import signal
import time
from dataclasses import replace
from pathlib import Path

logger = logging.getLogger(__name__)


# ── Worker (runs in child process) ──────────────────────────────────────


def _experiment_worker(
    worker_id: int,
    config_dict: dict,
    time_budget: int,
    region: str,
    status_queue: mp.Queue,
) -> None:
    """Run one experiment in its own process, sending phase updates via queue.

    Each worker process has its own CUDA context and model cache —
    no shared mutable state, no locks, no drama.
    """
    # Suppress noisy logs — the dashboard handles all display
    logging.basicConfig(level=logging.WARNING, format=f"[W{worker_id:02d}] %(message)s")

    # Ignore SIGINT in workers — let the main process handle Ctrl+C
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    try:
        from workbench.benchmark.carbon import SciConfig
        from workbench.executor import Executor
        from workbench.store.models import ExperimentConfig, SearchStrategy

        config = ExperimentConfig.from_dict(config_dict)
        config = replace(config, time_budget_sec=time_budget)

        # Wire the harness phase callback → queue
        def on_phase(phase: str) -> None:
            status_queue.put({"id": worker_id, "phase": phase})

        status_queue.put({"id": worker_id, "phase": "loading"})

        executor = Executor()
        result = executor.run(config, SearchStrategy.RANDOM, on_phase=on_phase)

        # Compute SCI + derived metrics in the worker (avoids pickling issues)
        sci = SciConfig.from_region(region)
        result.metrics.compute_derived(
            carbon_intensity_gco2_per_kwh=sci.carbon_intensity_gco2_per_kwh,
            embodied_gco2_per_token=sci.embodied_gco2_per_token,
        )

        status_queue.put({"id": worker_id, "phase": "done", "result": result.to_dict()})

    except Exception as e:
        status_queue.put({"id": worker_id, "phase": "failed", "error": f"{type(e).__name__}: {e}"})


# ── Config generation ───────────────────────────────────────────────────


def generate_unique_configs(n: int, seed: int | None = None) -> list:
    """Generate N unique experiment configs from the random strategy."""
    from workbench.strategy import RandomStrategy

    strategy = RandomStrategy(seed=seed)
    configs, seen = [], set()

    for _ in range(n * 20):  # generous retries for uniqueness
        config = strategy.propose([])  # empty history = full freedom
        if config and config.config_hash not in seen:
            configs.append(config)
            seen.add(config.config_hash)
        if len(configs) >= n:
            break

    return configs[:n]


# ── Dashboard rendering ─────────────────────────────────────────────────

_PHASE_ICONS = {
    "queued": "[dim]⏳ queued[/dim]",
    "starting": "[blue]🔧 init[/blue]",
    "loading": "[blue]📦 loading[/blue]",
    "inference": "[yellow]⚡ inference[/yellow]",
    "evaluating": "[cyan]📊 eval[/cyan]",
    "done": "[green]✅ done[/green]",
    "failed": "[red]❌ failed[/red]",
}


def _build_dashboard(configs, phases, results, errors, t0, gpu):
    """Build the Rich Panel showing all experiments + live GPU stats."""
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    # GPU stats header
    header = Text.from_markup(
        f"  [bold]GPU:[/bold] {gpu.get('power', 0):.0f}W │ "
        f"{gpu.get('temp', 0):.0f}°C │ "
        f"{gpu.get('util', 0):.0f}% util │ "
        f"{gpu.get('clock', 0):.0f} MHz │ "
        f"mem: {gpu.get('mem_used', 0):.0f}/{gpu.get('mem_total', 128):.0f} GB\n"
    )

    # Experiment table
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    for col, w, j, s in [
        ("#", 3, "right", "dim"), ("Model", 16, "left", None),
        ("dtype", 9, "left", None), ("B", 3, "right", None),
        ("Seq", 5, "right", None), ("Phase", 16, "left", None),
        ("SCI gCO₂/tok", 13, "right", None), ("BPB", 7, "right", None),
        ("tok/s", 7, "right", None),
    ]:
        table.add_column(col, width=w, justify=j, style=s)

    done, failed, running = 0, 0, 0
    for i, cfg in enumerate(configs):
        phase = phases.get(i, "queued")
        done += phase == "done"
        failed += phase == "failed"
        running += phase not in ("queued", "done", "failed")

        sci_s = bpb_s = tps_s = "—"
        if i in results:
            m = results[i].get("metrics", {})
            if m.get("sci_per_token") is not None:
                sci_s = f"[green]{m['sci_per_token']:.6f}[/green]"
            if m.get("val_bpb") is not None:
                bpb_s = f"[cyan]{m['val_bpb']:.4f}[/cyan]"
            if m.get("tokens_per_sec") is not None:
                tps_s = f"[yellow]{m['tokens_per_sec']:.1f}[/yellow]"
        elif i in errors:
            sci_s = "[red]err[/red]"

        table.add_row(
            str(i + 1), cfg.model_name.split("/")[-1], cfg.dtype,
            str(cfg.batch_size), str(cfg.sequence_length),
            _PHASE_ICONS.get(phase, f"🔄 {phase}"), sci_s, bpb_s, tps_s,
        )

    # Footer summary
    m, s = divmod(int(time.time() - t0), 60)
    footer = Text.from_markup(
        f"\n  ✅ {done}/{len(configs)} done │ "
        f"⚡ {running} running │ ❌ {failed} failed │ ⏱ {m}:{s:02d} elapsed"
    )

    return Panel(
        Group(header, table, footer),
        title=f"🔬 v6 Parallel Runner — {len(configs)} experiments",
        border_style="green",
        subtitle="SCI = (E × I) + M — gCO₂/tok · lower is greener 🌱",
    )


# ── Hardware polling ────────────────────────────────────────────────────


def _poll_gpu() -> dict:
    """Quick GPU + memory snapshot. Same data sources as live_dashboard.py."""
    import subprocess

    stats = {"power": 0, "temp": 0, "util": 0, "clock": 0, "mem_used": 0, "mem_total": 128}
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=temperature.gpu,power.draw,utilization.gpu,"
             "clocks.current.graphics", "--format=csv,noheader,nounits"],
            timeout=2, text=True,
        ).strip()
        p = [s.strip() for s in out.split(",")]
        stats.update(temp=float(p[0]), power=float(p[1]), util=float(p[2]), clock=float(p[3]))
    except Exception:
        pass
    try:
        with open("/proc/meminfo") as f:
            mi = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and ":" in parts[0]:
                    mi[parts[0].rstrip(":")] = int(parts[1])
        stats["mem_total"] = mi.get("MemTotal", 0) / 1_048_576
        stats["mem_used"] = (mi.get("MemTotal", 0) - mi.get("MemAvailable", 0)) / 1_048_576
    except Exception:
        pass
    return stats


# ── Poll loop (shared by live and non-live modes) ──────────────────────


def _drain_queue(q, phases, results, errors):
    """Non-blocking drain of all pending worker messages."""
    while True:
        try:
            msg = q.get_nowait()
        except queue_mod.Empty:
            break
        wid, phase = msg["id"], msg["phase"]
        phases[wid] = phase
        if phase == "done" and "result" in msg:
            results[wid] = msg["result"]
        elif phase == "failed" and "error" in msg:
            errors[wid] = msg["error"]


def _poll_loop(processes, q, configs, phases, results, errors, gpu, t0, live_display):
    """Main event loop: drain queue, poll GPU, refresh dashboard."""
    last_gpu = 0.0

    while any(p.is_alive() for p in processes):
        _drain_queue(q, phases, results, errors)

        now = time.time()
        if now - last_gpu > 2.0:
            gpu.update(_poll_gpu())
            last_gpu = now

        if live_display:
            try:
                live_display.update(_build_dashboard(configs, phases, results, errors, t0, gpu))
            except Exception:
                pass
        time.sleep(0.3)

    # Final drain (workers may have sent messages right before exiting)
    _drain_queue(q, phases, results, errors)

    # Detect workers that died silently (segfault, killed, etc.)
    for i, p in enumerate(processes):
        if phases.get(i) not in ("done", "failed") and not p.is_alive():
            phases[i] = "failed"
            errors[i] = f"Process died (exit code {p.exitcode})"


# ── Main orchestrator ───────────────────────────────────────────────────


def run_parallel(
    db_path: str,
    n_workers: int = 10,
    time_budget: int = 30,
    region: str = "us_average",
    seed: int | None = None,
    live: bool = True,
    log_file: str | None = None,
) -> None:
    """Generate N unique configs and run them all concurrently."""
    from rich.console import Console
    from rich.live import Live

    from workbench.benchmark.carbon import SciConfig
    from workbench.display import display_frontier_table, display_summary
    from workbench.evaluator import Evaluator
    from workbench.store.database import ResultStore
    from workbench.store.models import ExperimentResult

    console = Console(force_terminal=True)

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            datefmt="%H:%M:%S",
            handlers=[logging.FileHandler(log_file, mode="a")],
        )

    # Generate unique configs
    configs = generate_unique_configs(n_workers, seed=seed)
    console.print(f"\n[bold green]🧪 Generated {len(configs)} unique experiment configs[/bold green]\n")
    for i, c in enumerate(configs):
        model = c.model_name.split("/")[-1]
        console.print(
            f"  [{i+1:2d}] {model:<16} {c.dtype:<9} "
            f"batch={c.batch_size:<3} seq={c.sequence_length:<5} "
            f"tokens={c.max_new_tokens} kv={'Y' if c.use_kv_cache else 'N'} T={c.temperature}"
        )
    console.print()

    # Shared state
    status_queue: mp.Queue = mp.Queue()
    phases: dict[int, str] = {}
    results: dict[int, dict] = {}
    errors: dict[int, str] = {}
    gpu = _poll_gpu()

    # Spawn workers
    processes = [
        mp.Process(
            target=_experiment_worker,
            args=(i, cfg.to_dict(), time_budget, region, status_queue),
            name=f"exp-{i}",
        )
        for i, cfg in enumerate(configs)
    ]

    t0 = time.time()
    console.print(f"[bold]🚀 Launching {len(configs)} workers...[/bold]\n")
    for p in processes:
        p.start()

    # Run the dashboard loop
    if live:
        with Live(
            _build_dashboard(configs, phases, results, errors, t0, gpu),
            refresh_per_second=2,
            transient=True,
            console=Console(force_terminal=True),
        ) as live_display:
            _poll_loop(processes, status_queue, configs, phases, results, errors, gpu, t0, live_display)
    else:
        _poll_loop(processes, status_queue, configs, phases, results, errors, gpu, t0, None)

    for p in processes:
        p.join(timeout=10)

    elapsed = time.time() - t0

    # Save results to DB (main process only — no concurrent SQLite writes)
    console.print(f"\n[bold]💾 Saving results to {db_path}[/bold]")
    store = ResultStore(db_path)
    evaluator = Evaluator(store, sci_config=SciConfig.from_region(region))

    saved = 0
    for wid in sorted(results):
        try:
            result = ExperimentResult.from_dict(results[wid])
            evaluator.evaluate(result)
            saved += 1
        except Exception as e:
            console.print(f"  [red]Worker {wid + 1} result save failed: {e}[/red]")

    console.print(f"\n[bold green]✅ {saved}/{len(configs)} experiments completed in {elapsed:.1f}s[/bold green]")
    if errors:
        for wid, err in sorted(errors.items()):
            console.print(f"  [red]❌ Worker {wid + 1}: {err}[/red]")

    # Final summary + frontier
    summary = evaluator.summary()
    display_summary(summary)
    all_results = store.all_results()
    if all_results:
        display_frontier_table(all_results)
    store.close()


# ── CLI entry point ─────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="v6 parallel experiment runner")
    parser.add_argument("--db", required=True, help="SQLite database path")
    parser.add_argument("--workers", "-n", type=int, default=10, help="Concurrent experiments (default: 10)")
    parser.add_argument("--time-budget", "-t", type=int, default=30, help="Per-experiment budget in seconds")
    parser.add_argument("--region", "-r", default="us_average", help="Carbon intensity region")
    parser.add_argument("--seed", "-s", type=int, default=None, help="Random seed for config generation")
    parser.add_argument("--no-live", action="store_true", help="Disable live TUI dashboard")
    parser.add_argument("--log-file", default=None, help="Write logs to file")
    args = parser.parse_args()

    run_parallel(
        db_path=args.db,
        n_workers=args.workers,
        time_budget=args.time_budget,
        region=args.region,
        seed=args.seed,
        live=not args.no_live,
        log_file=args.log_file,
    )


if __name__ == "__main__":
    # CUDA *requires* 'spawn' for safe multi-process GPU access.
    # 'fork' + CUDA = segfaults. Don't even think about it.
    mp.set_start_method("spawn", force=True)
    main()
