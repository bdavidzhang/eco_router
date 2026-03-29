"""Terminal-based Pareto frontier display using Rich.

Shows scatter plots (ASCII), top-N frontier configs, and
SCI-first sustainability metrics — all in the terminal.

SCI = (E x I) + M — gCO2 per token. Lower is greener.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from workbench.store.models import ExperimentResult

console = Console()

# Quantization -> symbol mapping for scatter plot
_QUANT_SYMBOLS = {
    "none": "●",
    "gptq-4bit": "▲",
    "gptq-8bit": "■",
    "awq-4bit": "◆",
    "awq-8bit": "★",
}

# Model -> color mapping (auto-assigned from a palette)
_MODEL_PALETTE = [
    "cyan", "green", "yellow", "magenta", "blue",
    "bright_red", "bright_green", "bright_cyan",
]


def _sci_value(r: ExperimentResult) -> float | None:
    """Get SCI, falling back to energy-derived estimate."""
    if r.metrics.sci_per_token is not None:
        return r.metrics.sci_per_token
    if r.metrics.energy_per_token_j is not None:
        return (r.metrics.energy_per_token_j / 3_600_000) * 400.0 + 0.00003
    return None


def _fmt(val: float | None, fmt: str = ".4f", suffix: str = "") -> str:
    """Format a value or return em-dash for None."""
    return f"{val:{fmt}}{suffix}" if val is not None else "—"


# ── Frontier Table ──────────────────────────────────────────────────────


def display_frontier_table(results: list[ExperimentResult], top_n: int = 10) -> None:
    """Show top Pareto-optimal configs as a Rich table, SCI-first."""
    frontier = [r for r in results if r.pareto_rank == 0]
    if not frontier:
        console.print("[yellow]No Pareto-optimal configs found yet.[/yellow]")
        return

    frontier.sort(key=lambda r: _sci_value(r) or float("inf"))

    table = Table(
        title=f"🏆 Pareto Frontier — Top {min(top_n, len(frontier))} (by SCI ↓)",
        show_lines=True,
    )
    table.add_column("Hash", style="dim", width=12)
    table.add_column("Model", style="bold")
    table.add_column("Quant", style="green")
    table.add_column("Batch", justify="right")
    table.add_column("SCI ↓", justify="right", style="bold green")
    table.add_column("BPB ↓", justify="right", style="cyan")
    table.add_column("J/tok", justify="right", style="magenta")
    table.add_column("tok/s ↑", justify="right", style="yellow")
    table.add_column("GPU%", justify="right", style="blue")
    table.add_column("Mem GB", justify="right", style="red")
    table.add_column("Watts", justify="right", style="red")

    for r in frontier[:top_n]:
        c, m = r.config, r.metrics
        model_short = c.model_name.split("/")[-1][:20]
        sci = _sci_value(r)
        table.add_row(
            r.config_hash, model_short, c.quantization.value,
            str(c.batch_size), _fmt(sci, ".6f"), _fmt(m.val_bpb, ".4f"),
            _fmt(m.energy_per_token_j, ".4f"), _fmt(m.tokens_per_sec, ".1f"),
            _fmt(m.gpu_util_avg_pct, ".0f", "%"), _fmt(m.mem_used_gb, ".1f"),
            _fmt(m.gpu_power_avg_w, ".1f"),
        )

    console.print(table)


# ── Scatter Plot ────────────────────────────────────────────────────────


def _build_model_colors(results: list[ExperimentResult]) -> dict[str, str]:
    """Assign a unique color to each model family present in the data."""
    models = sorted({r.config.model_name.split("/")[-1] for r in results})
    return {
        model: _MODEL_PALETTE[i % len(_MODEL_PALETTE)]
        for i, model in enumerate(models)
    }


def _compute_jittered_x(
    valid: list[ExperimentResult],
    width: int,
) -> dict[int, int]:
    """Compute jittered X positions for same-BPB clusters.

    Groups experiments by BPB value.  Within each group, spreads points
    into a horizontal band (max ±5 chars) sorted by SCI so you can see
    the vertical structure of each model column.

    Returns: {index_in_valid: jittered_x_column}
    """
    # Group indices by BPB (rounded to avoid float noise)
    bpb_groups: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(valid):
        key = f"{r.metrics.val_bpb:.6f}"
        bpb_groups[key].append(i)

    # Get canonical X for each BPB
    bpb_vals = sorted({r.metrics.val_bpb for r in valid})
    bpb_min, bpb_max = min(bpb_vals), max(bpb_vals)
    bpb_range = max(bpb_max - bpb_min, 1e-9)

    result_x: dict[int, int] = {}
    max_jitter = 5  # ±5 chars from center

    for _bpb_key, indices in bpb_groups.items():
        bpb = valid[indices[0]].metrics.val_bpb
        center_x = int((bpb - bpb_min) / bpb_range * (width - 1))

        if len(indices) == 1:
            result_x[indices[0]] = max(0, min(width - 1, center_x))
            continue

        # Sort by SCI within this BPB group
        indices.sort(key=lambda i: _sci_value(valid[i]) or float("inf"))

        # Spread evenly across [-max_jitter, +max_jitter]
        band = min(max_jitter, len(indices) // 2 + 1)
        for rank, idx in enumerate(indices):
            t = rank / max(len(indices) - 1, 1)  # 0..1
            offset = int((t - 0.5) * 2 * band)
            jx = max(0, min(width - 1, center_x + offset))
            result_x[idx] = jx

    return result_x


def display_scatter_ascii(
    results: list[ExperimentResult],
    width: int = 72,
    height: int = 25,
) -> None:
    """ASCII scatter: BPB (x, jittered) vs log₁₀(SCI) (y), colored by model.

    - Log scale on Y spreads data across orders of magnitude.
    - Same-BPB clusters get horizontal jitter to reveal density.
    - Pareto-optimal points shown as bold ★.
    - Remaining overlaps shown as density digits (2–9) or ▣ (10+).
    - Legend built dynamically from actual data.
    """
    valid = [
        r for r in results
        if r.metrics.val_bpb is not None and _sci_value(r) is not None
    ]
    if not valid:
        console.print("[yellow]No data points for scatter plot.[/yellow]")
        return

    model_colors = _build_model_colors(valid)

    scis = [_sci_value(r) for r in valid]
    s_min, s_max = min(scis), max(scis)

    # Log scale Y
    log_s_min = math.log10(max(s_min, 1e-12))
    log_s_max = math.log10(max(s_max, 1e-12))
    log_s_range = max(log_s_max - log_s_min, 1e-9)

    # Jittered X positions
    jittered_x = _compute_jittered_x(valid, width)

    def _y_pos(sci: float) -> int:
        log_sci = math.log10(max(sci, 1e-12))
        y = int((1 - (log_sci - log_s_min) / log_s_range) * (height - 1))
        return max(0, min(height - 1, y))

    # First pass: count overlaps
    cell_count: Counter[tuple[int, int]] = Counter()
    for i, r in enumerate(valid):
        x = jittered_x[i]
        y = _y_pos(_sci_value(r))
        cell_count[(x, y)] += 1

    grid: list[list[str]] = [[" " for _ in range(width)] for _ in range(height)]
    colors: list[list[str]] = [["white" for _ in range(width)] for _ in range(height)]

    # Second pass: draw (non-Pareto first, Pareto on top)
    draw_order = sorted(
        range(len(valid)),
        key=lambda i: valid[i].pareto_rank != 0,
        reverse=True,
    )
    for i in draw_order:
        r = valid[i]
        x = jittered_x[i]
        y = _y_pos(_sci_value(r))

        quant = r.config.quantization.value
        model_short = r.config.model_name.split("/")[-1]
        is_frontier = r.pareto_rank == 0

        n = cell_count[(x, y)]
        if is_frontier:
            symbol = "★"
        elif n >= 10:
            symbol = "▣"
        elif n > 1:
            symbol = str(min(n, 9))
        else:
            symbol = _QUANT_SYMBOLS.get(quant, "·")

        base_color = model_colors.get(model_short, "white")
        color = f"bold {base_color}" if is_frontier else base_color

        grid[y][x] = symbol
        colors[y][x] = color

    # ── Render ──
    lines = []
    for row_idx, (row, color_row) in enumerate(zip(grid, colors)):
        line = Text()
        if row_idx == 0:
            line.append(f"{s_max:.2e} │", style="dim")
        elif row_idx == height - 1:
            line.append(f"{s_min:.2e} │", style="dim")
        else:
            line.append("         │", style="dim")
        for ch, clr in zip(row, color_row):
            line.append(ch, style=clr)
        lines.append(line)

    x_axis = Text()
    x_axis.append(f"         └{'─' * width}", style="dim")
    lines.append(x_axis)

    # X tick labels -- skip any that would overlap
    bpb_vals = sorted({r.metrics.val_bpb for r in valid})
    bpb_min = min(bpb_vals)
    bpb_max = max(bpb_vals)
    bpb_range = max(bpb_max - bpb_min, 1e-9)
    tick_line = [" "] * (width + 20)
    last_end = -1
    for bpb in bpb_vals:
        cx = int((bpb - bpb_min) / bpb_range * (width - 1))
        label = f"{bpb:.2f}"
        pos = 10 + cx - len(label) // 2
        if pos <= last_end:  # would overlap previous label
            continue
        for j, ch in enumerate(label):
            if 0 <= pos + j < len(tick_line):
                tick_line[pos + j] = ch
        last_end = pos + len(label)
    x_label = Text()
    x_label.append("".join(tick_line).rstrip(), style="dim")
    lines.append(x_label)

    subtitle_line = Text()
    subtitle_line.append(
        "          BPB →  (★=Pareto  2‒9=overlap  ▣=10+  log₁₀ Y-axis)",
        style="dim italic",
    )
    lines.append(subtitle_line)

    # ── Dynamic legend ──
    legend_parts = []
    for model, color in model_colors.items():
        legend_parts.append(f"[{color}]● {model}[/{color}]")

    seen_quants = {r.config.quantization.value for r in valid}
    if seen_quants != {"none"}:
        legend_parts.append("│")
        for quant in sorted(seen_quants):
            sym = _QUANT_SYMBOLS.get(quant, "·")
            legend_parts.append(f"{sym}={quant}")

    legend = "  ".join(legend_parts)

    panel_content = Text("\n")
    for line in lines:
        panel_content.append_text(line)
        panel_content.append("\n")

    n_frontier = sum(1 for r in valid if r.pareto_rank == 0)
    unique_cells = sum(1 for v in cell_count.values() if v > 0)
    console.print(
        Panel(
            panel_content,
            title="🌱 SCI (gCO₂/tok, log₁₀) ↓ greener  vs  BPB → smarter",
            subtitle=legend,
            border_style="green",
        )
    )
    console.print(
        f"  [dim]{len(valid)} experiments → {unique_cells} visible cells "
        f"({n_frontier} on Pareto frontier)[/dim]"
    )



# ── Per-model scatter (tok/s vs SCI) ────────────────────────────────────

# Batch-size symbols for per-model plots
_BATCH_SYMBOLS = {1: "·", 2: "○", 4: "◆", 8: "■", 16: "▲", 32: "●"}


def display_scatter_per_model(
    results: list[ExperimentResult],
    width: int = 68,
    height: int = 16,
) -> None:
    """One scatter per model: log10(tok/s) X vs log10(SCI) Y.

    Separating by model eliminates the fixed-BPB clustering problem
    and reveals how batch_size / seq_len trade off throughput vs carbon.
    """
    valid = [
        r for r in results
        if _sci_value(r) is not None
        and r.metrics.tokens_per_sec is not None
        and r.metrics.tokens_per_sec > 0
    ]
    if not valid:
        console.print("[yellow]No data for per-model scatter.[/yellow]")
        return

    # Group by model
    groups: dict[str, list[ExperimentResult]] = defaultdict(list)
    for r in valid:
        model = r.config.model_name.split("/")[-1]
        groups[model].append(r)

    model_colors = _build_model_colors(valid)

    for model_name in sorted(groups, key=lambda m: len(groups[m]), reverse=True):
        exps = groups[model_name]
        if len(exps) < 2:
            continue  # need at least 2 points for a plot

        _render_model_scatter(
            model_name, exps, model_colors.get(model_name, "white"),
            width, height,
        )


def _render_model_scatter(
    model_name: str,
    exps: list[ExperimentResult],
    color: str,
    width: int,
    height: int,
) -> None:
    """Render a single model's tok/s-vs-SCI scatter."""
    tps_vals = [math.log10(r.metrics.tokens_per_sec) for r in exps]
    sci_vals = [math.log10(max(_sci_value(r), 1e-12)) for r in exps]

    x_min, x_max = min(tps_vals), max(tps_vals)
    y_min, y_max = min(sci_vals), max(sci_vals)
    x_range = max(x_max - x_min, 1e-9)
    y_range = max(y_max - y_min, 1e-9)

    def _xy(r: ExperimentResult) -> tuple[int, int]:
        lx = math.log10(r.metrics.tokens_per_sec)
        ly = math.log10(max(_sci_value(r), 1e-12))
        xi = int((lx - x_min) / x_range * (width - 1))
        yi = int((1 - (ly - y_min) / y_range) * (height - 1))
        return max(0, min(width - 1, xi)), max(0, min(height - 1, yi))

    cell_count: Counter[tuple[int, int]] = Counter()
    for r in exps:
        cell_count[_xy(r)] += 1

    grid = [[" "] * width for _ in range(height)]
    color_grid = [["dim"] * width for _ in range(height)]

    for r in exps:
        x, y = _xy(r)
        is_frontier = r.pareto_rank == 0
        bs = r.config.batch_size
        n = cell_count[(x, y)]

        if is_frontier:
            sym = "★"
        elif n >= 10:
            sym = "▣"
        elif n > 1:
            sym = str(min(n, 9))
        else:
            sym = _BATCH_SYMBOLS.get(bs, "●")

        clr = f"bold {color}" if is_frontier else color
        grid[y][x] = sym
        color_grid[y][x] = clr

    # Y tick labels (SCI)
    lines: list[Text] = []
    for row_idx in range(height):
        line = Text()
        if row_idx == 0:
            label = f"{10**y_max:.2e}"
        elif row_idx == height - 1:
            label = f"{10**y_min:.2e}"
        else:
            label = ""
        line.append(f"{label:>9s} │", style="dim")
        for xi in range(width):
            line.append(grid[row_idx][xi], style=color_grid[row_idx][xi])
        lines.append(line)

    # X axis
    x_axis = Text()
    x_axis.append(f"          └{'─' * width}", style="dim")
    lines.append(x_axis)

    # X tick labels (tok/s) — evenly space ~5 ticks
    n_ticks = 5
    tick_line = [" "] * (width + 20)
    last_end = -1
    for i in range(n_ticks):
        t = i / max(n_ticks - 1, 1)
        val = 10 ** (x_min + t * x_range)
        cx = int(t * (width - 1))
        label = f"{val:.1f}" if val >= 1 else f"{val:.2f}"
        pos = 11 + cx - len(label) // 2
        if pos <= last_end:
            continue
        for j, ch in enumerate(label):
            if 0 <= pos + j < len(tick_line):
                tick_line[pos + j] = ch
        last_end = pos + len(label)
    x_label = Text()
    x_label.append("".join(tick_line).rstrip(), style="dim")
    lines.append(x_label)

    # Batch size legend
    batches_used = sorted({r.config.batch_size for r in exps})
    batch_legend = "  ".join(
        f"{_BATCH_SYMBOLS.get(bs, chr(0x25CF))}=bs{bs}" for bs in batches_used
    )
    sub_line = Text()
    sub_line.append(
        f"          tok/s → (log₁₀)  ★=Pareto  {batch_legend}",
        style="dim italic",
    )
    lines.append(sub_line)

    panel_content = Text("\n")
    for ln in lines:
        panel_content.append_text(ln)
        panel_content.append("\n")

    n_frontier = sum(1 for r in exps if r.pareto_rank == 0)
    unique_cells = sum(1 for v in cell_count.values() if v > 0)
    bpb = exps[0].metrics.val_bpb
    bpb_str = f"BPB={bpb:.2f}" if bpb else ""

    console.print(
        Panel(
            panel_content,
            title=(
                f"[{color}]{model_name}[/{color}]  "
                f"SCI ↓ vs tok/s →  ({len(exps)} experiments, {bpb_str})"
            ),
            subtitle=f"{unique_cells} visible cells, {n_frontier} Pareto",
            border_style=color,
        )
    )


# ── Summary ─────────────────────────────────────────────────────────────


def display_summary(summary: dict) -> None:
    """Display experiment summary with SCI-first sustainability metrics."""
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Key", style="bold")
    table.add_column("Value")

    table.add_row("Total experiments", str(summary.get("total_experiments", 0)))
    table.add_row("Completed", str(summary.get("completed", 0)))
    table.add_row("Failed", str(summary.get("failed", 0)))
    table.add_row("Frontier size", str(summary.get("frontier_size", 0)))
    table.add_row("", "")

    best_sci = summary.get("best_sci")
    table.add_row(
        "🌱 Best SCI",
        f"[bold green]{best_sci:.6f} gCO₂/tok[/bold green]" if best_sci else "—",
    )
    ci = summary.get("carbon_intensity")
    table.add_row(
        "  Grid intensity (I)",
        f"{ci:.0f} gCO₂/kWh" if ci else "—",
    )
    scale = summary.get("best_sci_scale")
    if scale:
        table.add_row(
            "  @ 1M tok/day",
            f"{scale['kg_co2_per_day']:.4f} kgCO₂ "
            f"(≈ {scale['driving_miles_equivalent']:.1f} miles driven)",
        )

    table.add_row("", "")
    best_bpb = summary.get("best_bpb")
    table.add_row("Best BPB", _fmt(best_bpb, ".4f"))
    best_e = summary.get("best_energy")
    table.add_row("Best J/token", _fmt(best_e, ".4f"))
    best_t = summary.get("best_throughput")
    table.add_row("Best throughput", f"{best_t:.1f} tok/s" if best_t else "—")

    console.print(
        Panel(table, title="📊 Workbench Summary — SCI Optimization", border_style="green")
    )


# ── Export ──────────────────────────────────────────────────────────────


def export_results_json(results: list[ExperimentResult], path: str) -> None:
    """Export results to JSON file."""
    data = [r.to_dict() for r in results]
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    console.print(f"[green]Exported {len(data)} results to {path}[/green]")
