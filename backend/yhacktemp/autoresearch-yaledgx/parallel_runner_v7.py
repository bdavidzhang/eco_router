#!/usr/bin/env python3
"""parallel_runner_v7.py — Infinite worker-pool experiment runner.

Runs experiments forever with N concurrent workers. Each result is saved
to the DB the instant it completes — no batch boundaries, no data loss.

When a worker finishes, a fresh experiment immediately takes its slot.
Ctrl+C gracefully drains in-flight workers and saves their results too.

New in v7 (vs v6):
  - Infinite loop: no experiment limit, runs until Ctrl+C
  - Incremental saves: each result persisted the moment it completes
  - Worker-pool pattern: maintains N active workers at all times
  - Wave tracking: groups of N for dashboard clarity
  - Cumulative stats: total experiments, uptime, results/hour
  - Overnight-safe: partial results always preserved

Usage (called by run_30s_v7.sh):
    python parallel_runner_v7.py \
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
import random
import signal
import time
from dataclasses import replace
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Thermal protection ──────────────────────────────────────────────────
THERMAL_COOLDOWN_MIN_SEC = 30
THERMAL_COOLDOWN_MAX_SEC = 60
GPU_TEMP_THRESHOLD_C = 85.0


# ── Worker (runs in child process) ──────────────────────────────────────


def _experiment_worker(
    worker_id: int,
    experiment_num: int,
    config_dict: dict,
    time_budget: int,
    region: str,
    status_queue: mp.Queue,
) -> None:
    """Run one experiment in its own process, sending phase updates via queue.

    Each worker process has its own CUDA context and model cache —
    no shared mutable state, no locks, no drama.
    """
    logging.basicConfig(level=logging.WARNING, format=f"[W{worker_id:02d}] %(message)s")
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    try:
        from workbench.benchmark.carbon import SciConfig
        from workbench.executor import Executor
        from workbench.store.models import ExperimentConfig, SearchStrategy

        config = ExperimentConfig.from_dict(config_dict)
        config = replace(config, time_budget_sec=time_budget)

        def on_phase(phase: str) -> None:
            status_queue.put({"id": worker_id, "exp": experiment_num, "phase": phase})

        status_queue.put({"id": worker_id, "exp": experiment_num, "phase": "loading"})

        executor = Executor()
        result = executor.run(config, SearchStrategy.RANDOM, on_phase=on_phase)

        sci = SciConfig.from_region(region)
        result.metrics.compute_derived(
            carbon_intensity_gco2_per_kwh=sci.carbon_intensity_gco2_per_kwh,
            embodied_gco2_per_token=sci.embodied_gco2_per_token,
        )

        status_queue.put({
            "id": worker_id, "exp": experiment_num,
            "phase": "done", "result": result.to_dict(),
        })

    except Exception as e:
        status_queue.put({
            "id": worker_id, "exp": experiment_num,
            "phase": "failed", "error": f"{type(e).__name__}: {e}",
        })


# ── Config generation ───────────────────────────────────────────────────


def _generate_one_config(seen_hashes: set[str], seed: int | None = None) -> object | None:
    """Generate a single unique config not already seen."""
    from workbench.strategy import RandomStrategy

    strategy = RandomStrategy(seed=seed)
    for _ in range(100):
        config = strategy.propose([])
        if config and config.config_hash not in seen_hashes:
            return config
    return None


# ── Dashboard rendering ─────────────────────────────────────────────────

_PHASE_ICONS = {
    "queued":     "[dim]⏳ queued[/dim]",
    "starting":   "[blue]🔧 init[/blue]",
    "loading":    "[blue]📦 loading[/blue]",
    "inference":  "[yellow]⚡ inference[/yellow]",
    "evaluating": "[cyan]📊 eval[/cyan]",
    "done":       "[green]✅ done[/green]",
    "failed":     "[red]❌ failed[/red]",
}


def _build_dashboard(slots, cumulative, t0, gpu, n_workers, cooldown_remaining=0):
    """Build the Rich Panel showing active worker slots + cumulative stats."""
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    elapsed = time.time() - t0
    h, rem = divmod(int(elapsed), 3600)
    m, s = divmod(rem, 60)

    rate = cumulative["done"] / (elapsed / 3600) if elapsed > 60 else 0

    # GPU stats header
    header = Text.from_markup(
        f"  [bold]GPU:[/bold] {gpu.get('power', 0):.0f}W │ "
        f"{gpu.get('temp', 0):.0f}°C │ "
        f"{gpu.get('util', 0):.0f}% util │ "
        f"{gpu.get('clock', 0):.0f} MHz │ "
        f"mem: {gpu.get('mem_used', 0):.0f}/{gpu.get('mem_total', 128):.0f} GB\n"
    )

    # Active workers table
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    for col, w, j, st in [
        ("Slot", 4, "right", "dim"), ("Exp#", 5, "right", "dim"),
        ("Model", 16, "left", None), ("dtype", 9, "left", None),
        ("B", 3, "right", None), ("Seq", 5, "right", None),
        ("Phase", 16, "left", None),
        ("SCI gCO₂/tok", 13, "right", None),
        ("BPB", 7, "right", None), ("tok/s", 7, "right", None),
    ]:
        table.add_column(col, width=w, justify=j, style=st)

    for slot_id in range(n_workers):
        slot = slots.get(slot_id)
        if slot is None:
            table.add_row(
                str(slot_id + 1), "—", "[dim]—[/dim]", "—", "—", "—",
                "[dim]⏳ idle[/dim]", "—", "—", "—",
            )
            continue

        cfg = slot["config"]
        phase = slot["phase"]
        exp_num = slot["exp_num"]
        result = slot.get("result")

        sci_s = bpb_s = tps_s = "—"
        if result:
            me = result.get("metrics", {})
            if me.get("sci_per_token") is not None:
                sci_s = f"[green]{me['sci_per_token']:.6f}[/green]"
            if me.get("val_bpb") is not None:
                bpb_s = f"[cyan]{me['val_bpb']:.4f}[/cyan]"
            if me.get("tokens_per_sec") is not None:
                tps_s = f"[yellow]{me['tokens_per_sec']:.1f}[/yellow]"
        elif phase == "failed":
            sci_s = "[red]err[/red]"

        table.add_row(
            str(slot_id + 1), str(exp_num),
            cfg.model_name.split("/")[-1], cfg.dtype,
            str(cfg.batch_size), str(cfg.sequence_length),
            _PHASE_ICONS.get(phase, f"🔄 {phase}"),
            sci_s, bpb_s, tps_s,
        )

    # Footer: cumulative stats
    cooldown_text = ""
    if cooldown_remaining > 0:
        cooldown_text = (
            f" │ [bold red]🌡️  THERMAL COOLDOWN: "
            f"{cooldown_remaining:.0f}s remaining[/bold red]"
        )
    footer = Text.from_markup(
        f"\n  ✅ {cumulative['done']} done │ "
        f"🗑️ {cumulative.get('discarded', 0)} discarded │ "
        f"❌ {cumulative['failed']} failed │ "
        f"⚡ {cumulative['active']} running │ "
        f"📊 {rate:.1f}/hr │ "
        f"⏱ {h}:{m:02d}:{s:02d} uptime"
        f"{cooldown_text}"
    )

    return Panel(
        Group(header, table, footer),
        title="🔬 v7 Infinite Runner — ∞ experiments · incremental saves",
        border_style="green",
        subtitle="SCI = (E × I) + M — gCO₂/tok · Ctrl+C to stop gracefully 🌱",
    )


# ── Hardware polling ────────────────────────────────────────────────────


def _poll_gpu() -> dict:
    """Quick GPU + memory snapshot."""
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


# ── Incremental save ───────────────────────────────────────────────────


def _save_result(result_dict: dict, evaluator, console) -> bool:
    """Save a single result to the DB immediately. Returns True on success."""
    from workbench.store.models import ExperimentResult

    try:
        result = ExperimentResult.from_dict(result_dict)
        evaluator.evaluate(result)
        return True
    except Exception as e:
        console.print(f"  [red]Save failed: {e}[/red]")
        return False


# ── Main orchestrator ───────────────────────────────────────────────────


def run_infinite(
    db_path: str,
    n_workers: int = 10,
    time_budget: int = 30,
    region: str = "us_average",
    seed: int | None = None,
    live: bool = True,
    log_file: str | None = None,
) -> None:
    """Run experiments forever with N concurrent workers, saving incrementally."""
    from rich.console import Console
    from rich.live import Live

    from workbench.benchmark.carbon import SciConfig
    from workbench.display import display_frontier_table, display_summary
    from workbench.evaluator import Evaluator
    from workbench.store.database import ResultStore

    console = Console(force_terminal=True)

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            datefmt="%H:%M:%S",
            handlers=[logging.FileHandler(log_file, mode="a")],
        )

    # Persistent store + evaluator (kept open for the entire run)
    store = ResultStore(db_path)
    evaluator = Evaluator(store, sci_config=SciConfig.from_region(region))

    # Shared IPC queue
    status_queue: mp.Queue = mp.Queue()

    # Track what we've already tried
    seen_hashes: set[str] = set()

    # Worker slots: slot_id → {process, config, phase, exp_num, result}
    slots: dict[int, dict] = {}
    processes: dict[int, mp.Process] = {}

    # Cumulative counters
    cumulative = {"done": 0, "failed": 0, "discarded": 0, "active": 0}
    next_exp_num = 1

    # Graceful shutdown flag
    stopping = False

    def _handle_sigint(sig, frame):
        nonlocal stopping
        if stopping:
            # Second Ctrl+C = hard exit
            console.print("\n[bold red]⚡ Force quit![/bold red]")
            raise SystemExit(1)
        stopping = True
        console.print(
            "\n[yellow]🛑 Stopping... waiting for in-flight experiments to finish."
            " Press Ctrl+C again to force quit.[/yellow]"
        )

    signal.signal(signal.SIGINT, _handle_sigint)

    gpu = _poll_gpu()
    t0 = time.time()

    def _spawn_worker(slot_id: int) -> None:
        """Generate a config and spawn a worker in the given slot."""
        nonlocal next_exp_num

        cfg = _generate_one_config(seen_hashes, seed=None)  # always random for infinite mode
        if cfg is None:
            console.print(f"  [yellow]Slot {slot_id}: couldn't generate unique config, retrying next cycle[/yellow]")
            return

        seen_hashes.add(cfg.config_hash)
        exp_num = next_exp_num
        next_exp_num += 1

        proc = mp.Process(
            target=_experiment_worker,
            args=(slot_id, exp_num, cfg.to_dict(), time_budget, region, status_queue),
            name=f"exp-{exp_num}",
        )
        proc.start()

        slots[slot_id] = {
            "process": proc,
            "config": cfg,
            "phase": "starting",
            "exp_num": exp_num,
            "result": None,
        }
        processes[slot_id] = proc

    def _drain_and_save() -> tuple[list[int], bool]:
        """Drain the queue, save completed results.

        Returns:
            (freed_slot_ids, thermal_detected) — thermal_detected is True
            if any worker failed with a thermal-related error.
        """
        freed = []
        thermal_detected = False
        while True:
            try:
                msg = status_queue.get_nowait()
            except queue_mod.Empty:
                break

            wid = msg["id"]
            phase = msg["phase"]

            if wid not in slots:
                continue

            slots[wid]["phase"] = phase

            if phase == "done" and "result" in msg:
                slots[wid]["result"] = msg["result"]
                result_status = msg["result"].get("status", "")
                error_msg = msg["result"].get("error_message", "") or ""

                # Detect thermal discards — executor returns status="discarded"
                # for ThermalAbortError, NOT "failed"!
                if result_status == "discarded":
                    if "thermal" in error_msg.lower():
                        thermal_detected = True
                    cumulative["discarded"] += 1
                    logger.warning(
                        "Exp #%d (slot %d) discarded: %s",
                        msg["exp"], wid, error_msg[:80] or "thermal/throttle",
                    )
                elif _save_result(msg["result"], evaluator, console):
                    cumulative["done"] += 1
                    logger.info("Exp #%d (slot %d) saved to DB", msg["exp"], wid)
                else:
                    cumulative["failed"] += 1
                freed.append(wid)

            elif phase == "failed":
                err = msg.get("error", "unknown")
                if "thermal" in err.lower():
                    thermal_detected = True
                slots[wid]["phase"] = "failed"
                cumulative["failed"] += 1
                logger.warning("Exp #%d (slot %d) failed: %s", msg["exp"], wid, err)
                freed.append(wid)

        # Also check for silently dead processes
        for sid, slot in list(slots.items()):
            proc = slot["process"]
            if not proc.is_alive() and slot["phase"] not in ("done", "failed"):
                slot["phase"] = "failed"
                cumulative["failed"] += 1
                logger.warning(
                    "Exp #%d (slot %d) died silently (exit code %s)",
                    slot["exp_num"], sid, proc.exitcode,
                )
                if sid not in freed:
                    freed.append(sid)

        return freed, thermal_detected

    def _cleanup_slot(slot_id: int) -> None:
        """Join a finished process and remove it from tracking."""
        if slot_id in processes:
            processes[slot_id].join(timeout=5)
            del processes[slot_id]
        if slot_id in slots:
            del slots[slot_id]

    console.print(f"\n[bold green]🚀 Starting infinite runner with {n_workers} workers[/bold green]")
    console.print(f"   Budget per experiment: {time_budget}s")
    console.print(f"   Region: {region}")
    console.print(f"   Results saved to: {db_path}")
    console.print(f"   Press Ctrl+C to stop gracefully\n")

    # Initial spawn
    for slot_id in range(n_workers):
        _spawn_worker(slot_id)

    cumulative["active"] = len(slots)

    # ── Main event loop ─────────────────────────────────────────────────
    last_gpu_poll = 0.0
    thermal_cooldown_until = 0.0

    def _tick(live_display=None):
        nonlocal last_gpu_poll, thermal_cooldown_until

        freed, thermal_detected = _drain_and_save()

        # Clean up finished slots
        for sid in freed:
            _cleanup_slot(sid)

        # Poll GPU (before cooldown check so we have fresh temps)
        now = time.time()
        if now - last_gpu_poll > 2.0:
            gpu.update(_poll_gpu())
            last_gpu_poll = now

        # ── Thermal cooldown logic ──────────────────────────────────────
        gpu_too_hot = gpu.get("temp", 0) >= GPU_TEMP_THRESHOLD_C
        if thermal_detected or gpu_too_hot:
            if thermal_cooldown_until <= now:  # don't extend an active cooldown
                pause_secs = random.uniform(
                    THERMAL_COOLDOWN_MIN_SEC, THERMAL_COOLDOWN_MAX_SEC,
                )
                thermal_cooldown_until = now + pause_secs
                logger.warning(
                    "🌡️  Thermal cooldown triggered (gpu_hot=%s, worker_abort=%s)"
                    " — pausing spawns for %.0fs",
                    gpu_too_hot, thermal_detected, pause_secs,
                )

        in_cooldown = now < thermal_cooldown_until
        cooldown_remaining = max(0.0, thermal_cooldown_until - now)

        # Respawn workers into empty slots (unless stopping or cooling)
        if not stopping and not in_cooldown:
            for sid in range(n_workers):
                if sid not in slots:
                    _spawn_worker(sid)

        cumulative["active"] = sum(
            1 for s in slots.values() if s["phase"] not in ("done", "failed")
        )

        if live_display:
            try:
                live_display.update(
                    _build_dashboard(
                        slots, cumulative, t0, gpu, n_workers, cooldown_remaining,
                    )
                )
            except Exception:
                pass

    if live:
        with Live(
            _build_dashboard(slots, cumulative, t0, gpu, n_workers),
            refresh_per_second=2,
            transient=True,
            console=Console(force_terminal=True),
        ) as live_display:
            while True:
                _tick(live_display)

                # If stopping and no workers left, break
                if stopping and not slots:
                    break
                # If stopping, just wait for remaining workers
                if stopping and not any(s["process"].is_alive() for s in slots.values()):
                    # Final drain
                    freed, _ = _drain_and_save()
                    for sid in freed:
                        _cleanup_slot(sid)
                    break

                time.sleep(0.3)
    else:
        while True:
            _tick()

            if stopping and not slots:
                break
            if stopping and not any(s["process"].is_alive() for s in slots.values()):
                freed, _ = _drain_and_save()
                for sid in freed:
                    _cleanup_slot(sid)
                break

            time.sleep(0.3)

    # Join any stragglers
    for proc in processes.values():
        proc.join(timeout=10)

    elapsed = time.time() - t0
    h, rem = divmod(int(elapsed), 3600)
    m, s = divmod(rem, 60)

    console.print(f"\n[bold green]✅ Infinite runner stopped after {h}:{m:02d}:{s:02d}[/bold green]")
    console.print(
        f"   {cumulative['done']} completed · "
        f"{cumulative.get('discarded', 0)} discarded · "
        f"{cumulative['failed']} failed · "
        f"{cumulative['done'] / (elapsed / 3600):.1f}/hr"
    )

    # Final summary + frontier
    summary = evaluator.summary()
    display_summary(summary)
    all_results = store.all_results()
    if all_results:
        display_frontier_table(all_results)
    store.close()


# ── CLI entry point ─────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="v7 infinite experiment runner")
    parser.add_argument("--db", required=True, help="SQLite database path")
    parser.add_argument("--workers", "-n", type=int, default=10, help="Concurrent workers (default: 10)")
    parser.add_argument("--time-budget", "-t", type=int, default=30, help="Per-experiment budget in seconds")
    parser.add_argument("--region", "-r", default="us_average", help="Carbon intensity region")
    parser.add_argument("--seed", "-s", type=int, default=None, help="Random seed (only for first batch)")
    parser.add_argument("--no-live", action="store_true", help="Disable live TUI dashboard")
    parser.add_argument("--log-file", default=None, help="Write logs to file")
    args = parser.parse_args()

    run_infinite(
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
