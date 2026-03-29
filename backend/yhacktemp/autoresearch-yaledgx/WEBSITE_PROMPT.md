# Website Build Prompt — AutoResearch on DGX Spark

> Give this prompt (+ the data files) to a web dev agent or LLM to build the site.

---

## Context

We built an autonomous LLM research workbench that discovers Pareto-optimal
model configurations on an NVIDIA DGX Spark. It optimizes three axes
simultaneously: **quality (BPB)**, **carbon intensity (SCI gCO₂/tok)**, and
**throughput (tok/s)**. The core metric is the Green Software Foundation's
**SCI = (E × I) + M** — grams of CO₂ per token.

We have ~60 runs producing experiment results (SQLite DBs + JSON/CSV exports)
and 68-channel, 1 Hz sensor logs (GPU power, thermals, memory, PSI, NVMe,
PCIe, CPU frequencies, etc.).

## Data Sources

| File | Format | What's in it |
|------|--------|-------------|
| `runs/combined/combined_results.json` | JSON array | Every experiment: config (model, quant, batch, seq), metrics (SCI, BPB, J/tok, tok/s, GPU power/util/temp, memory), pareto_rank, status |
| `runs/combined/combined_results.csv` | CSV | Same data, flat columns — easy to load in JS |
| `runs/run_*/sensor_log.csv` | CSV, 1 Hz | Up to 68 columns: gpu_temp_c, gpu_power_w, gpu_power_instant_w, gpu_util_pct, gpu_clock_mhz, thermal_zone0–6_c, mem_used_kb, psi_cpu_avg10, nvme_temp_c, pcie_gen, pcie_width, fan_state, load_avg_1m, etc. |
| `runs/run_*/results.db` | SQLite | Canonical per-run experiment store (experiments table with config_json + metrics_json columns) |

## The 3 Graphs to Recreate as Interactive Web Visualizations

### Graph 1 — SCI vs BPB Scatter (the "main" plot)

**What it shows:** Every completed experiment as a dot on a scatter plot.
- **X-axis:** BPB (bits per byte) — lower = smarter model quality. Range ~1.5–4.1.
- **Y-axis:** SCI (gCO₂ per token) — lower = greener. Use **log₁₀ scale** because values span orders of magnitude (e.g. 5.9e-05 to 9.3e-04).
- **Color:** by model family (e.g. Qwen3.5-0.8B = cyan, Qwen3.5-4B = green, Mistral-7B = magenta).
- **Shape/symbol:** by quantization type (none = circle, gptq-4bit = triangle, awq-4bit = diamond).
- **Pareto-optimal points** get a bold star (★) marker and are connected by a stepped "frontier line".
- **Same-BPB clusters** (same model, different batch/seq configs) stack vertically — use slight horizontal jitter so overlapping dots are visible.
- **Tooltip on hover:** show config hash, model, quant, batch_size, seq_length, SCI, BPB, tok/s, J/tok, GPU watts.

**Why it matters:** This is THE decision plot. The Pareto frontier is the set of configs where you can't improve SCI without sacrificing BPB or vice versa. Anything not on the frontier is strictly dominated.

**Design notes:**
- Use a dark background (the terminal version uses green-bordered panels on black).
- The ideal region is bottom-left (low carbon AND high quality).
- Label axes clearly: "BPB → (lower = smarter)" and "SCI gCO₂/tok ↓ (lower = greener)".
- Show a subtle "greener →" gradient or arrow pointing toward the origin.

### Graph 2 — Per-Model Scatter: tok/s vs SCI

**What it shows:** One sub-plot per model family (e.g. Qwen3.5-0.8B, Qwen3.5-4B). Within each:
- **X-axis:** tok/s (throughput) — **log₁₀ scale**. Higher = faster.
- **Y-axis:** SCI (gCO₂ per token) — **log₁₀ scale**. Lower = greener.
- **Symbol:** by batch_size (bs1 = small dot, bs4 = diamond, bs8 = square, bs16 = triangle, bs32 = large circle).
- **Pareto points** get ★ markers.
- **Tooltip:** same detailed config info as Graph 1.

**Why it matters:** Within a single model, this reveals how batch_size and seq_length trade off throughput vs carbon. Bigger batches = higher throughput AND lower per-token carbon (better GPU utilization). This plot proves batching is the #1 lever for green inference.

**Design notes:**
- Use a small-multiples / grid layout — one card per model.
- Color-code the model card border to match Graph 1's model colors.
- The ideal region is bottom-right (fast AND green).

### Graph 3 — Sensor Timeline (GPU power, temp, utilization over time)

**What it shows:** Time-series line charts from the sensor logs, showing what the hardware was doing during experiments.
- **Primary lines:** gpu_power_w (watts), gpu_temp_c (°C), gpu_util_pct (%).
- **Secondary lines (toggleable):** gpu_clock_mhz, mem_used_kb (converted to GB), load_avg_1m, thermal_zone0_c.
- **X-axis:** time (seconds from start of run, or absolute timestamp).
- **Y-axis:** dual-axis or normalized — power/temp on left, utilization % on right.
- **Annotations:** mark when experiments start/finish (if correlatable with timestamps), and highlight thermal throttle events (gpu_hw_thermal_throttle > 0) or thermal abort zones.

**Why it matters:** This shows the physical reality behind the SCI numbers — you can SEE the GPU ramping up during inference, the thermal soak approaching 85°C, the power bursts hitting 88W instantaneous, and the idle valleys between experiments. It makes the abstract "energy per token" metric visceral.

**Design notes:**
- Let users pick which run to view from a dropdown (runs are named by timestamp, e.g. `run_20260328_180815`).
- Brush/zoom on the timeline to inspect specific intervals.
- Shade the background red when gpu_temp_c ≥ 85°C (thermal danger zone).
- Show peak annotations: "Max 88.7W" / "Max 80°C".

## Overall Website Structure

```
┌──────────────────────────────────────────────────────┐
│  HEADER: "AutoResearch on DGX Spark — SCI = (E×I)+M" │
│  Subtitle: "Autonomous Pareto-optimal LLM discovery"  │
├──────────────────────────────────────────────────────┤
│  HERO STATS (cards):                                  │
│  ┌──────┐ ┌──────┐ ┌──────────┐ ┌─────────────────┐ │
│  │Best  │ │Best  │ │Experiments│ │Daily CO₂ @1M    │ │
│  │SCI   │ │BPB   │ │Completed │ │tok/day (best)   │ │
│  │0.0001│ │1.566 │ │    N     │ │0.06 kg ≈ 0.1 mi│ │
│  └──────┘ └──────┘ └──────────┘ └─────────────────┘ │
├──────────────────────────────────────────────────────┤
│  GRAPH 1: SCI vs BPB scatter (full width)            │
│  [interactive, zoomable, filterable by model/quant]  │
├──────────────────────────────────────────────────────┤
│  GRAPH 2: Per-model tok/s vs SCI (grid of cards)     │
├──────────────────────────────────────────────────────┤
│  GRAPH 3: Sensor timeline (run selector + brush zoom)│
├──────────────────────────────────────────────────────┤
│  TABLE: Full experiment results (sortable, filterable)│
│  Columns: model, quant, batch, seq, SCI, BPB, tok/s, │
│           J/tok, GPU%, mem GB, watts, pareto rank     │
├──────────────────────────────────────────────────────┤
│  FOOTER: SCI formula explanation + link to GSF spec   │
└──────────────────────────────────────────────────────┘
```

## Tech Recommendations

- **Charts:** Plotly.js or Observable Plot (both handle log scales, tooltips, zoom natively). D3 if you want full control.
- **Framework:** Plain HTML + Tailwind + vanilla JS is fine. Or Next.js/Astro if you want SSG.
- **Data loading:** Fetch the JSON/CSV at page load. Sensor CSVs can be large (700KB+) — lazy-load per run on selection.
- **Color scheme:** Dark mode default. Green accent (#22c55e) for SCI/sustainability theme. Model colors: cyan, green, yellow, magenta, blue (matching terminal palette).
- **Responsive:** Graphs should resize. Tables should scroll horizontally on mobile.

## Key Domain Terms

| Term | Meaning |
|------|---------|
| **SCI** | Software Carbon Intensity — gCO₂ emitted per token. THE metric. |
| **BPB** | Bits Per Byte — perplexity-derived quality metric. Lower = model understands text better. |
| **Pareto frontier** | Set of configs where no other config is better on ALL axes simultaneously. |
| **Pareto rank** | 0 = on the frontier (non-dominated). 1+ = dominated by N configs. |
| **tok/s** | Tokens per second throughput. |
| **J/tok** | Joules of energy per token (E in the SCI formula). |
| **Region** | Grid carbon intensity — e.g. us_average=400 gCO₂/kWh, eu_france=55. |
