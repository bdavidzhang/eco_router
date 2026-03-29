# Prompt: Build the Green LLM Benchmark Website

## Context

We are a team at YHack (Yale's hackathon) working on the ASUS sustainability track. We built an **autoresearch workbench** that autonomously tests different LLM inference configurations on an NVIDIA DGX Spark (Blackwell GB10 GPU, 128GB unified memory, ARM big.LITTLE CPU) and measures their environmental impact using the **SCI (Software Carbon Intensity)** standard (ISO/IEC 21031:2024).

The workbench runs experiments varying model, quantization, batch size, sequence length, etc., and for each experiment collects: quality metrics (BPB), energy/power measurements (via nvidia-smi), thermal data, throughput, and computes an SCI carbon score.

We need a **public-facing benchmark website** that visualizes these results — like an MLPerf leaderboard but for sustainability.

---

## Data Source

The workbench exports results as **JSON** via `workbench export -o results.json`. Each entry in the JSON array has this shape:

```json
{
  "config_hash": "a1b2c3d4e5f6",
  "config": {
    "model_name": "meta-llama/Llama-3.2-1B",
    "quantization": "gptq-4bit",
    "batch_size": 4,
    "sequence_length": 512,
    "max_new_tokens": 128,
    "temperature": 1.0,
    "use_kv_cache": true,
    "dtype": "float16",
    "time_budget_sec": 300
  },
  "metrics": {
    "val_bpb": 1.2345,
    "latency_p50_ms": 12.5,
    "latency_p95_ms": 18.3,
    "latency_p99_ms": 22.1,
    "tokens_per_sec": 85.2,
    "total_tokens": 25000,
    "gpu_power_avg_w": 42.5,
    "gpu_power_max_w": 67.8,
    "energy_per_token_j": 0.498,
    "gpu_util_avg_pct": 78.5,
    "gpu_clock_avg_mhz": 2400.0,
    "gpu_clock_min_mhz": 1800.0,
    "gpu_temp_avg_c": 52.3,
    "gpu_temp_max_c": 68.1,
    "thermal_throttled": false,
    "mem_used_gb": 14.2,
    "mem_available_gb": 104.8,
    "mem_pressure_pct": 11.9,
    "nvme_temp_c": 38.5,
    "system_load_avg": 3.2,
    "sci_per_token": 0.000055,
    "carbon_operational_g": 0.000025,
    "carbon_embodied_g": 0.00003,
    "energy_kwh_per_token": 1.383e-07,
    "gpu_efficiency": 2.005,
    "cost_per_token_usd": 1.66e-08
  },
  "status": "completed",
  "strategy_used": "bayesian",
  "pareto_rank": 0,
  "created_at": "2026-03-28T15:30:00+00:00",
  "error_message": null
}
```

The website should load this JSON (initially from a static file, but structured so it can later accept live updates via an API endpoint).

There is also a secondary CSV data source from `sensor_logger.sh` — time-series sensor data at 1-second intervals during experiment runs. Each row:

```csv
timestamp,gpu_temp_c,gpu_power_w,gpu_util_pct,gpu_clock_mhz,gpu_vid_clock_mhz,thermal_zone0_c,...,thermal_zone6_c,fan_rpm,fan_power_uw,nvme_temp_c,nic0_temp_c,...,nic3_temp_c,wifi_temp_c,cpu_big_avg_mhz,cpu_little_avg_mhz,mem_used_kb,mem_available_kb,load_avg_1m
```

This is for time-series charts showing power/thermal behavior during experiments.

---

## Website Structure

### Page 1: Landing / Overview Dashboard

A hero section introducing the project:
- Project name: **"Green LLM Bench"** (or similar)
- One-liner: "How much CO2 does your LLM inference cost? We measured it."
- Hardware badge: NVIDIA DGX Spark / Blackwell GB10 / 128GB Unified Memory
- Key stats bar showing aggregate numbers:
  - Total experiments run
  - Best SCI score achieved (gCO2/token)
  - Most efficient config (tokens/joule)
  - Grid region: Connecticut / ISO New England

Below that, the **Pareto Frontier Visualization** (the hero chart):
- Interactive scatter plot: **BPB (quality, x-axis) vs SCI (carbon, y-axis)**
- Each point is an experiment config
- Color-coded by quantization type (none, gptq-4bit, gptq-8bit, awq-4bit, awq-8bit)
- Size-coded by throughput (bigger = faster)
- Pareto-optimal points connected by a line and highlighted
- Hover tooltip showing: model, quant, batch size, SCI, BPB, tokens/sec, watts
- The frontier line represents the "efficient frontier" — you can't improve one axis without worsening the other

### Page 2: Leaderboards

Multiple sortable/filterable tables. Each leaderboard ranks all completed experiments by a different primary metric. The user should be able to toggle between them via tabs.

#### Leaderboard 1: "Greenest" — SCI Score (Primary)
**Rank by: `sci_per_token` ascending (lower = greener)**

This is THE sustainability metric. SCI = (E x I) + M per token, from the Green Software Foundation's ISO standard. It captures both operational energy carbon AND embodied hardware carbon.

| Rank | Model | Quant | Batch | SCI (gCO2/tok) | Operational | Embodied | BPB | tok/s |
|------|-------|-------|-------|-----------------|-------------|----------|-----|-------|

#### Leaderboard 2: "Most Energy Efficient" — Joules per Token
**Rank by: `energy_per_token_j` ascending (lower = less energy)**

Raw energy efficiency independent of grid carbon intensity. This is hardware-level efficiency — useful because it's location-agnostic (doesn't depend on where you run it).

| Rank | Model | Quant | Batch | J/token | kWh/token | Watts avg | GPU Util% | tok/s |
|------|-------|-------|-------|---------|-----------|-----------|-----------|-------|

#### Leaderboard 3: "Best Performance per Watt" — Tokens per Joule
**Rank by: `gpu_efficiency` (= tokens_per_sec / gpu_power_avg_w) descending (higher = better)**

A "perf/watt" metric familiar to hardware engineers. How many tokens do you get per unit of energy? This is the inverse of J/token but more intuitive for "higher is better" framing.

| Rank | Model | Quant | Batch | tok/J | tok/s | Watts | GPU Util% | BPB |
|------|-------|-------|-------|-------|-------|-------|-----------|-----|

#### Leaderboard 4: "Best Quality" — Validation BPB
**Rank by: `val_bpb` ascending (lower = better quality)**

Pure model quality regardless of energy. Bits-per-byte measures how well the model predicts held-out text. Important to show that sustainable configs don't sacrifice quality.

| Rank | Model | Quant | Batch | BPB | SCI | J/token | tok/s | Mem GB |
|------|-------|-------|-------|-----|-----|---------|-------|--------|

#### Leaderboard 5: "Fastest" — Throughput
**Rank by: `tokens_per_sec` descending (higher = faster)**

Raw speed. Include energy cost so users see the speed/carbon tradeoff.

| Rank | Model | Quant | Batch | tok/s | Latency p50 | Watts | J/token | BPB |
|------|-------|-------|-------|-------|-------------|-------|---------|-----|

#### Leaderboard 6: "Cheapest" — Cost per Token
**Rank by: `cost_per_token_usd` ascending**

Operational cost at $0.12/kWh. Makes the sustainability case in business terms.

| Rank | Model | Quant | Batch | $/1M tokens | $/token | kWh/token | SCI | BPB |
|------|-------|-------|-------|-------------|---------|-----------|-----|-----|

**Leaderboard features:**
- All tables should be sortable by clicking column headers
- Filter by: model name, quantization type, batch size
- Highlight Pareto-optimal rows (pareto_rank == 0) with a green badge or background
- Show rank change indicators if data is refreshed

### Page 3: Experiment Detail View

Clicking any row in a leaderboard opens a detail page for that experiment config:

**Config section:**
- Model name, quantization, batch size, sequence length, dtype, KV cache, time budget

**Metrics section** (card grid):
- SCI breakdown: pie chart showing operational vs embodied carbon split
- Energy: J/token, kWh/token, total energy
- Power: avg watts, max watts, power trace chart (if time-series data available)
- Thermal: avg/max GPU temp, thermal zones, throttle status
- Quality: BPB, latency percentiles
- Throughput: tokens/sec, total tokens
- System: memory used, memory pressure, NVMe temp, system load
- Cost: $/token, $/1M tokens

**Time-series charts** (from sensor_logger.sh CSV data if available):
- GPU power draw over time during this experiment
- GPU temperature over time
- GPU utilization over time
- GPU clock speed over time (shows thermal throttling)

### Page 4: "What If" Carbon Calculator

An interactive calculator where visitors can explore SCI across different grid regions:

- Dropdown: select a grid region (Connecticut, California, France, Sweden, Poland, Iceland, etc.) from the presets in our code
- Shows: how the SAME experiment results would have different SCI scores if the hardware were located in a different region
- Bar chart comparing SCI across all regions for the best config
- Include real-time grid intensity if possible (our code supports Electricity Maps API)

This demonstrates the "carbon-aware computing" concept — same code, same model, different carbon cost based on location and time.

### Page 5: Methodology

Explain how we measure everything. This is important for credibility:

- **SCI Formula**: SCI = (E x I) + M per R
  - E = Energy per token in kWh (measured via nvidia-smi power sampling at 1Hz, trapezoidal integration)
  - I = Grid carbon intensity in gCO2/kWh (Electricity Maps API for real-time, static presets as fallback, Connecticut/ISO-NE default = ~210 gCO2/kWh)
  - M = Embodied emissions per token (DGX Spark ~200kg manufacturing CO2, amortized over 5-year lifespan at avg utilization)
  - R = Per token (functional unit)
- **Hardware**: NVIDIA DGX Spark specs — Blackwell GB10 GPU, ARM Cortex-X925/A725 big.LITTLE CPU, 128GB unified LPDDR5X via C2C, 1TB NVMe
- **Power measurement**: nvidia-smi 1Hz sampling (instantaneous + average), trapezoidal integration for total energy
- **Quality metric**: Validation bits-per-byte (BPB) on held-out dataset — lower means better language modeling
- **Search strategy**: Grid search -> Random -> Bayesian (Optuna), with auto-switching
- **Thermal safety**: 85C abort threshold, cooldown gating between experiments
- **Standard**: ISO/IEC 21031:2024 (Green Software Foundation SCI)
- **Limitations**: GPU power only (not full system), embodied emissions estimated (NVIDIA hasn't published GB10 LCA), static grid intensity unless API key configured

---

## Design Guidelines

NOTE:YOU HAVE TO USE THE PROVIDED KAIZEN REPORT SYSTEM. 



- **Color scheme**: Green/sustainable theme. Dark mode preferred. Use green (#22c55e) for "good" metrics (low SCI, high efficiency), red/orange for "bad" (high SCI, thermal throttle).
- **Charts**: Use a charting library (Recharts, Chart.js, D3, or similar). The Pareto frontier scatter plot is the hero visual — make it interactive and beautiful.
- **Responsive**: Must work on mobile (judges will be viewing on phones/laptops).
- **Performance**: Static site is fine. Can be a Next.js/React app or even a Vite + React SPA. Data loaded from static JSON.
- **Hosting**: Should be deployable to Vercel/Netlify/GitHub Pages.
- **No backend required initially**: Load data from static JSON files. Structure components so an API can be swapped in later.

---

## Ranking Summary — What to Optimize & Why

| Rank By | Metric | Direction | Why It Matters |
|---------|--------|-----------|----------------|
| **Greenest** | `sci_per_token` | Lower is better | THE sustainability metric — gCO2 per token, ISO standard |
| **Most efficient** | `energy_per_token_j` | Lower is better | Raw energy cost, location-agnostic |
| **Best perf/watt** | `gpu_efficiency` (tok/s/W) | Higher is better | Hardware efficiency, familiar to engineers |
| **Best quality** | `val_bpb` | Lower is better | Model quality — proves green doesn't mean bad |
| **Fastest** | `tokens_per_sec` | Higher is better | Raw speed, shows the speed/carbon tradeoff |
| **Cheapest** | `cost_per_token_usd` | Lower is better | Business case for sustainability |
| **Pareto optimal** | `pareto_rank == 0` | On the frontier | Multi-objective optimal — can't improve one without worsening another |

The Pareto frontier is the most important visualization. It shows configs where you **cannot improve SCI without worsening quality, or vice versa**. These are the only configs that matter — everything else is dominated.

---

## File Locations

- Experiment JSON export: `workbench export -o results.json` (from `yhacktemp/autoresearch-yaledgx/`)
- Sensor time-series CSV: `sensor_log_*.csv` (from `sensor_logger.sh` in repo root)
- Carbon intensity presets: `src/workbench/benchmark/carbon.py` → `CARBON_INTENSITY_PRESETS` dict
- Pareto logic: `src/workbench/pareto.py`
- Data models (all field names/types): `src/workbench/store/models.py`
- Example display logic (formatting, colors): `src/workbench/display.py`

---

## MVP Priorities

If time is limited, build in this order:

1. **Pareto scatter plot** (BPB vs SCI, interactive) — this is the demo hero
2. **SCI leaderboard** (greenest configs table, sortable)
3. **Experiment detail view** (click a row to see full metrics)
4. **Regional carbon calculator** ("What If" page)
5. **Additional leaderboards** (efficiency, quality, speed, cost)
6. **Time-series power charts** (from sensor CSV data)
7. **Methodology page**
