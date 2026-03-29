"""Live TUI dashboard for the research loop using Rich Live.

Shows a continuously-updating terminal panel with:
  - Current experiment info + phase-aware progress
  - Live GPU stats (polled via nvidia-smi every ~2s)
  - System memory (from /proc/meminfo — correct for unified memory)
  - Pareto frontier summary

The harness reports its actual phase ("loading" | "inference" | "evaluating")
via a callback, so the dashboard shows what's really happening — no guessing.

Usage: enabled via ``--live`` flag -> ``ResearchController(live_tui=True)``
"""

from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from rich.live import Live
from rich.panel import Panel

if TYPE_CHECKING:
    from workbench.store.models import ExperimentConfig, ExperimentResult


# ── Dashboard state (mutable bag, updated by controller + poller) ───────


@dataclass
class DashboardState:
    """Shared mutable state between the controller thread and GPU poller."""

    # Experiment loop
    iteration: int = 0
    max_iterations: int = 50
    start_time: float = 0.0
    total_time_sec: float | None = None
    strategy_name: str = "auto"

    # Current experiment — status is set by the harness phase callback:
    #   "idle" | "loading" | "inference" | "evaluating" | "done"
    current_config: ExperimentConfig | None = None
    current_status: str = "idle"
    experiment_start: float = 0.0
    time_budget_sec: int = 30

    # Cumulative results
    completed: int = 0
    failed: int = 0
    frontier: list[ExperimentResult] = field(default_factory=list)
    last_result: ExperimentResult | None = None

    # Live hardware readings (updated by GpuPoller)
    gpu_temp_c: float = 0.0
    gpu_power_w: float = 0.0
    gpu_util_pct: float = 0.0
    gpu_clock_mhz: float = 0.0
    mem_used_gb: float = 0.0
    mem_total_gb: float = 128.0


# ── GPU + memory polling ────────────────────────────────────────────────


def poll_hardware(state: DashboardState) -> None:
    """Quick hardware poll -> mutate *state* in-place.  Fast (<20 ms)."""
    # GPU via nvidia-smi
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=temperature.gpu,power.draw,utilization.gpu,"
                "clocks.current.graphics",
                "--format=csv,noheader,nounits",
            ],
            timeout=2,
            text=True,
        ).strip()
        p = [s.strip() for s in out.split(",")]
        state.gpu_temp_c = float(p[0])
        state.gpu_power_w = float(p[1])
        state.gpu_util_pct = float(p[2])
        state.gpu_clock_mhz = float(p[3])
    except Exception:
        pass  # stale values are fine

    # System memory (more useful than nvidia-smi on unified-memory DGX Spark)
    try:
        total_kb = avail_kb = 0
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total_kb = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    avail_kb = int(line.split()[1])
        state.mem_total_gb = total_kb / 1_048_576
        state.mem_used_gb = (total_kb - avail_kb) / 1_048_576
    except Exception:
        pass


class GpuPoller(threading.Thread):
    """Daemon thread: polls hardware + refreshes the Live display."""

    def __init__(
        self, state: DashboardState, live: Live, interval: float = 2.0,
    ) -> None:
        super().__init__(daemon=True, name="gpu-poller")
        self._state = state
        self._live = live
        self._interval = interval
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.is_set():
            poll_hardware(self._state)
            try:
                self._live.update(build_dashboard(self._state))
            except Exception:
                pass  # Live already stopped — harmless
            self._stop_event.wait(self._interval)

    def stop(self) -> None:
        self._stop_event.set()


# ── Rendering ───────────────────────────────────────────────────────────


def _bar(fraction: float, width: int = 40) -> str:
    filled = int(max(0.0, min(1.0, fraction)) * width)
    return "\u2588" * filled + "\u2591" * (width - filled)


def _f(val: float | None, fmt: str = ".4f") -> str:
    return f"{val:{fmt}}" if val is not None else "\u2014"


def _mmss(seconds: float) -> str:
    m, s = divmod(int(max(0, seconds)), 60)
    return f"{m}:{s:02d}"


_PHASE_LABELS = {
    "loading": "\U0001f4e6 loading model...",
    "inference": "\u26a1 running inference",
    "evaluating": "\U0001f4ca evaluating quality...",
    "done": "\u2705 done",
}


def _experiment_phase_line(state: DashboardState) -> str:
    """Build a phase-aware experiment progress line.

    Uses the real phase reported by the harness callback — no guessing.
    """
    exp_elapsed = time.time() - state.experiment_start
    budget = state.time_budget_sec
    phase = state.current_status
    label = _PHASE_LABELS.get(phase, phase)

    if phase == "loading":
        return f"  Experiment: {label} ({_mmss(exp_elapsed)} elapsed)"

    if phase == "evaluating":
        return f"  Experiment: {label} ({_mmss(exp_elapsed)} elapsed)"

    # "inference" phase — show budget progress bar
    if phase == "inference":
        exp_pct = exp_elapsed / budget
        if exp_pct <= 1.0:
            return (
                f"  Experiment: {_bar(exp_pct, 30)} "
                f"{exp_pct*100:.0f}% of {budget}s budget"
            )
        return (
            f"  Experiment: {_bar(1.0, 30)} "
            f"wrapping up... ({_mmss(exp_elapsed)} elapsed)"
        )

    # Fallback for any other status
    return f"  Experiment: {label} ({_mmss(exp_elapsed)} elapsed)"


def build_dashboard(state: DashboardState) -> Panel:
    """Return a single Rich Panel representing the full dashboard."""

    # ── Title line ──────────────────────────────────────────────────────
    if state.current_config:
        c = state.current_config
        model = c.model_name.split("/")[-1]
        title = (
            f"\U0001f52c Experiment {state.iteration}/{state.max_iterations}"
            f" \u2500\u2500\u2500 {model} \u00b7 {c.dtype} \u00b7 batch={c.batch_size}"
        )
    else:
        title = f"\U0001f52c Idle ({state.iteration}/{state.max_iterations})"

    lines: list[str] = []

    # ── GPU stats ───────────────────────────────────────────────────────
    lines.append(
        f"  [bold]GPU:[/bold] {state.gpu_power_w:.0f}W \u2502 "
        f"{state.gpu_temp_c:.0f}\u00b0C \u2502 "
        f"{state.gpu_util_pct:.0f}% util \u2502 "
        f"{state.gpu_clock_mhz:.0f} MHz \u2502 "
        f"mem: {state.mem_used_gb:.0f}/{state.mem_total_gb:.0f} GB"
    )

    # ── Last-result metrics ─────────────────────────────────────────────
    lr = state.last_result
    if lr and lr.metrics.sci_per_token is not None:
        m = lr.metrics
        lines.append(
            f"  [bold]SCI:[/bold] [green]{_f(m.sci_per_token, '.6f')}[/green] "
            f"gCO\u2082/tok \u2502 "
            f"BPB: [cyan]{_f(m.val_bpb)}[/cyan] \u2502 "
            f"[yellow]{_f(m.tokens_per_sec, '.1f')}[/yellow] tok/s"
        )

    # ── Overall time progress ───────────────────────────────────────────
    elapsed = time.time() - state.start_time if state.start_time else 0
    if state.total_time_sec and state.total_time_sec > 0:
        pct = min(elapsed / state.total_time_sec, 1.0)
        left = max(0, state.total_time_sec - elapsed)
        lines.append(f"  {_bar(pct)} {pct*100:.0f}% ({_mmss(left)} left)")
    else:
        lines.append(f"  Elapsed: {_mmss(elapsed)}")

    # ── Per-experiment progress (phase-aware) ───────────────────────────
    if state.current_status not in ("idle", "done") and state.experiment_start > 0:
        lines.append(_experiment_phase_line(state))

    # ── Counters ────────────────────────────────────────────────────────
    lines.append("")
    lines.append(
        f"  \u2705 {state.completed} completed \u2502 "
        f"\u274c {state.failed} failed \u2502 "
        f"\U0001f3c6 {len(state.frontier)} frontier \u2502 "
        f"\U0001f504 {state.strategy_name}"
    )

    # ── Pareto frontier ─────────────────────────────────────────────────
    if state.frontier:
        lines.append("")
        lines.append(
            f"  [bold]\U0001f3c6 Pareto Frontier ({len(state.frontier)} configs)[/bold]"
        )
        ranked = sorted(
            state.frontier,
            key=lambda r: r.metrics.sci_per_token or float("inf"),
        )
        for i, r in enumerate(ranked[:5], 1):
            c, m = r.config, r.metrics
            model = c.model_name.split("/")[-1][:15]
            lines.append(
                f"  [dim]#{i}[/dim] {model:<15} "
                f"{c.quantization.value:<6} b={c.batch_size:<3} "
                f"SCI=[green]{_f(m.sci_per_token, '.6f')}[/green] "
                f"BPB=[cyan]{_f(m.val_bpb, '.2f')}[/cyan] "
                f"[yellow]{_f(m.tokens_per_sec, '.0f')}[/yellow] tok/s"
            )
    else:
        lines.append("")
        lines.append("  [dim]No experiments completed yet\u2026[/dim]")

    return Panel(
        "\n".join(lines),
        title=title,
        border_style="green",
        subtitle="SCI = (E \u00d7 I) + M \u2014 gCO\u2082/tok  \u00b7  lower is greener \U0001f331",
    )
