# 🔬 AutoResearch on DGX Spark — Full Report

> **What:** An autonomous LLM research workbench that discovers Pareto-optimal
> model configurations — optimizing quality (BPB), carbon intensity (SCI), and
> throughput simultaneously — running on a single NVIDIA DGX Spark.
>
> **Built at:** YHack 2025, Yale University
>
> **Hardware:** NVIDIA DGX Spark — GB10 Blackwell GPU, 128 GB unified memory,
> ARM Grace CPU (20 cores: 10× Cortex-X925 + 10× Cortex-A725)

---

## 📐 The Metric: SCI (Software Carbon Intensity)

We adopted the **Green Software Foundation's ISO standard** as our primary
optimization target:

```
SCI  =  (E  ×  I)  +  M    per R

E = Energy consumed per token (kWh)     ← measured via nvidia-smi
I = Grid carbon intensity (gCO₂/kWh)   ← configurable by region
M = Embodied hardware emissions (gCO₂)  ← amortized over device lifetime
R = Functional unit = 1 token
```

**Why SCI instead of raw energy?** Because 1 kWh in Sweden (hydro, 25 gCO₂) ≠
1 kWh in Poland (coal, 650 gCO₂). A config that looks "efficient" on pure
watts may be terrible on carbon depending on *where* it runs.

---

## 🏗️ System Architecture

```
┌──────────────────────────────────────────────────────────┐
│                   WORKBENCH CONTROLLER                   │
│                                                          │
│  Strategy ──▶ Executor ──▶ Evaluator ──┐                 │
│  (search)     (run exp)    (SCI score) │                 │
│     ▲                                  │                 │
│     └──────────────────────────────────┘                 │
│                    │                                     │
│        Result Store (SQLite per run)                     │
│                                                          │
├──────────────────────────────────────────────────────────┤
│            SENSOR LOGGER (background bash)               │
│    1 Hz sampling → GPU, thermal, memory, PSI, NVMe...   │
├──────────────────────────────────────────────────────────┤
│            OFFLINE ANALYZER (analyze_all.py)             │
│    Aggregates all runs → Pareto frontier + sensor stats  │
└──────────────────────────────────────────────────────────┘
```

### Source Code Structure

| Path | Purpose |
|------|---------|
| `src/workbench/cli.py` | Click CLI with SCI-aware flags (`--region`, `--carbon-intensity`) |
| `src/workbench/controller.py` | Main research loop — iterates strategy → execute → evaluate |
| `src/workbench/executor.py` | Runs a single experiment config (model load + benchmark) |
| `src/workbench/evaluator.py` | Computes SCI score, maintains Pareto frontier |
| `src/workbench/pareto.py` | Non-dominated sorting on 3 axes (BPB, SCI, throughput) |
| `src/workbench/display.py` | Rich terminal dashboard (tables, scatter plots) |
| `src/workbench/benchmark/carbon.py` | SCI calculator + 7 grid region presets |
| `src/workbench/benchmark/harness.py` | Full benchmark pipeline (tokenize → generate → measure) |
| `src/workbench/benchmark/power.py` | nvidia-smi power monitoring thread |
| `src/workbench/benchmark/thermal.py` | ACPI thermal zone monitoring (85°C safety cutoff) |
| `src/workbench/benchmark/quality.py` | Perplexity / BPB evaluation |
| `src/workbench/benchmark/system.py` | System-level metrics (memory, load, NVMe) |
| `src/workbench/store/models.py` | Data models (`ExperimentConfig`, `ExperimentResult`, etc.) |
| `src/workbench/store/database.py` | SQLite persistence layer |
| `src/workbench/strategy/` | Grid, Random, and Bayesian search strategies |

### Runner Scripts

| Script | What It Does |
|--------|-------------|
| `run_10min.sh` | Original 10-minute timed research run with v1 sensor logger |
| `run_10min_v2.sh` | uv edition — fixed quantization dependencies |
| `run_10min_v3.sh` | Uses v3 sensor logger (46 channels) |
| `run_10min_v4.sh` | Uses v4 sensor logger (68 channels) |
| `sensor_logger_v3.sh` | 45-channel sensor logger |
| `sensor_logger_v4.sh` | 67-channel sensor logger (most comprehensive) |
| `analyze_all.py` | Offline aggregation of all existing runs |

---

## 🔍 What We Explored on the DGX Spark

We systematically probed every sensor source accessible without root on the
DGX Spark. Here's what we found and what made it into the logger:

### GPU Sensors (nvidia-smi)

| Sensor | v1 | v3 | v4 | Notes |
|--------|:--:|:--:|:--:|-------|
| `gpu_temp_c` | ✅ | ✅ | ✅ | GPU die temperature |
| `gpu_power_w` | ✅ | ✅ | ✅ | Average power draw |
| `gpu_power_instant_w` | — | ✅ | ✅ | Instantaneous power (more granular) |
| `gpu_util_pct` | ✅ | ✅ | ✅ | GPU compute utilization % |
| `gpu_mem_util_pct` | — | ✅ | ✅ | Memory controller utilization % |
| `gpu_clock_mhz` | ✅ | ✅ | ✅ | Current graphics clock |
| `gpu_vid_clock_mhz` | ✅ | ✅ | ✅ | Video encoder clock |
| `gpu_sm_clock_mhz` | — | ✅ | ✅ | Streaming multiprocessor clock |
| `gpu_max_clock_mhz` | — | — | ✅ | Max clock (headroom = max − current) |
| `gpu_tlimit_c` | — | ✅ | ✅ | Thermal headroom to throttle point |
| `gpu_pstate` | — | ✅ | ✅ | Performance state (P0=max, P12=idle) |
| `gpu_throttle_reasons` | — | ✅ | ✅ | Hex bitmask of all throttle reasons |
| `gpu_hw_thermal_throttle` | — | ✅ | ✅ | Boolean: HW thermal throttle active |
| `gpu_hw_slowdown` | — | ✅ | ✅ | Boolean: HW slowdown active |
| `gpu_sw_power_cap` | — | ✅ | ✅ | Boolean: SW power cap hit |
| `gpu_idle` | — | — | ✅ | GPU idle event reason |
| `gpu_power_brake` | — | — | ✅ | External power brake signal |
| `pcie_gen` | — | — | ✅ | PCIe link generation (drops to 1 when idle!) |
| `pcie_width` | — | — | ✅ | PCIe link width (1→16 under load) |

### Thermal Zones (7× ACPI + NVMe + NICs + WiFi)

| Sensor | v1 | v3 | v4 | Notes |
|--------|:--:|:--:|:--:|-------|
| `thermal_zone0_c`..`zone6_c` | ✅ | ✅ | ✅ | 7 board thermal zones |
| `nvme_temp_c` | ✅ | ✅ | ✅ | NVMe composite temperature |
| `nvme_temp2_c` | — | — | ✅ | NAND controller (separate sensor) |
| `nvme_temp_alarm` | — | ✅ | ✅ | NVMe thermal alarm bit |
| `nic0..3_temp_c` | ✅ | ✅ | ✅ | 4 NIC temperature sensors |
| `wifi_temp_c` | ✅ | ✅ | ✅ | WiFi adapter temperature |
| `fan_state` | — | ✅ | ✅ | Fan cooling state (0-2) |
| `fan_power_uw` | ✅ | ✅ | ✅ | Fan power consumption (µW) |

### CPU & System

| Sensor | v1 | v3 | v4 | Notes |
|--------|:--:|:--:|:--:|-------|
| `cpu_big_avg_mhz` | ✅ | ✅ | ✅ | Cortex-X925 average frequency |
| `cpu_little_avg_mhz` | ✅ | ✅ | ✅ | Cortex-A725 average frequency |
| `cpu_user/system/idle/iowait` | — | — | ✅ | CPU time decomposition (cumulative jiffies) |
| `context_switches` | — | — | ✅ | Scheduling overhead |
| `procs_running/blocked` | — | — | ✅ | Process contention |
| `cpu_throttle_max` | — | — | ✅ | Max CPU cooling device state (0=fine, 3=throttled) |
| `load_avg_1m` | ✅ | ✅ | ✅ | 1-minute load average |

### Memory

| Sensor | v1 | v3 | v4 | Notes |
|--------|:--:|:--:|:--:|-------|
| `mem_used_kb` | ✅ | ✅ | ✅ | Total memory used |
| `mem_available_kb` | ✅ | ✅ | ✅ | Memory available to processes |
| `mem_cached_kb` | — | — | ✅ | File cache (56 GB at peak!) |
| `mem_dirty_kb` | — | — | ✅ | Dirty pages awaiting writeback |
| `mem_anon_kb` | — | — | ✅ | Anonymous (heap) memory |
| `mem_file_hugepages_kb` | — | — | ✅ | Transparent Huge Pages — **41 GB for model weights!** |
| `swap_used_kb` | — | — | ✅ | Swap pressure indicator |

### Pressure Stall Information (PSI)

| Sensor | v1 | v3 | v4 | Notes |
|--------|:--:|:--:|:--:|-------|
| `psi_cpu_avg10` | — | ✅ | ✅ | CPU pressure (10s window) |
| `psi_mem_some_avg10` | — | ✅ | ✅ | Memory pressure — some tasks stalled |
| `psi_mem_full_avg10` | — | ✅ | ✅ | Memory pressure — all tasks stalled |
| `psi_io_some_avg10` | — | ✅ | ✅ | IO pressure (10s window) |

### Network & I/O

| Sensor | v1 | v3 | v4 | Notes |
|--------|:--:|:--:|:--:|-------|
| `nvme_read/write_ios` | — | ✅ | ✅ | NVMe IOPS counters |
| `nvme_read/write_sectors` | — | ✅ | ✅ | NVMe sector counters |
| `nvme_io_in_progress` | — | ✅ | ✅ | Outstanding I/O depth |
| `net_rx_bytes` | — | — | ✅ | Network receive (auto-detected WiFi interface) |
| `net_tx_bytes` | — | — | ✅ | Network transmit |

### VM Counters

| Sensor | v1 | v3 | v4 | Notes |
|--------|:--:|:--:|:--:|-------|
| `pgmajfault` | — | ✅ | ✅ | Major page faults (disk I/O) |
| `pgfault` | — | — | ✅ | Minor page faults (memory touch pattern) |

### What We Found But *Couldn't* Use (without root/perf)

| Source | What's There | Why We Can't |
|--------|-------------|-------------|
| **ARM PMU (pmuv3)** | 76 events on X925, 65 on A725 (cache misses, branch mispredicts, instructions retired) | `perf_event_paranoid=4` — needs `-1` |
| **ARM SPE** | Statistical Profiling Extension — per-instruction latency sampling | Needs root |
| **SMMUv3 PMCG** | 17 groups: TLB misses, PCIe ATS, transaction counters | Needs perf access |
| **NVMe SMART** | Lifetime writes, wear leveling, error counts | Needs `sudo nvme` |
| **PCIe AER** | Advanced Error Reporting counters (all zeros — clean hardware) | Static — not worth polling |

### Channel Count Evolution

```
v1 (original):  26 columns  (25 data + timestamp)
v3 (expanded):  46 columns  (45 data + timestamp)  — +20 new channels
v4 (full):      68 columns  (67 data + timestamp)  — +22 more channels
```

---

## 🧪 Experiment Results

### Run History

We ran **16 total runs** across a single day on the DGX Spark, producing
**5,136 sensor samples** and **4 unique experiments** (3 completed, 1 failed).

| Run | Experiments | Sensor Samples | Sensor Columns | Duration | Version |
|-----|:-----------:|:--------------:|:--------------:|:--------:|:-------:|
| `run_20260328_112945` | 1 | 207 | 26 | 3m 57s | v1 |
| `run_20260328_114055` | 0 | 745 | 26 | 14m 17s | v1 |
| `run_20260328_120150` | 2 | 1,143 | 26 | 21m 56s | v3 |
| `run_20260328_120810` | 1 | 892 | 26 | 17m 09s | v3 |
| `run_20260328_122508` | 0 | 1,637 | 26 | 32m 00s | v3 |
| `run_20260328_125731` | 0 | 9 | 46 | 0m 09s | v4 |
| `run_20260328_130137` | 0 | 503 | 46 | 10m 26s | v4 |

*(Plus 9 earlier short-lived runs from initial debugging)*

### Completed Experiments (sorted by SCI)

| Hash | Model | Quant | Batch | Seq | SCI (gCO₂/tok) | BPB | J/tok | tok/s | GPU% | Mem GB | Watts | Pareto |
|------|-------|-------|:-----:|:---:|:---------------:|:---:|:-----:|:-----:|:----:|:------:|:-----:|:------:|
| `27ef367ea383` | Qwen3.5-0.8B | none | 8 | 2048 | **0.000326** | 1.5657 | 2.66 | 5.1 | 9% | 29.5 | 13.4 | ★ |
| `f9628aaac9aa` | Qwen3.5-0.8B | none | 1 | 512 | 0.000384 | 4.0656 | 3.36 | 1.8 | 6% | 13.1 | 6.0 | — |
| `7e7983e084d3` | Qwen3.5-0.8B | none | 8 | 1024 | 0.000933 | 1.5657 | 8.13 | 2.0 | 18% | 20.4 | 16.1 | — |

**Failed:** `902d2174eb1b` — Mistral-7B GPTQ-8bit (missing `optimum` dependency)

### Pareto Frontier Winner

```
🏆  Config 27ef367ea383
    Model:       Qwen/Qwen3.5-0.8B (no quantization)
    Batch size:  8
    Seq length:  2048
    SCI:         0.000326 gCO₂/token
    BPB:         1.5657 bits/byte
    Throughput:  5.1 tokens/sec
    Energy:      2.66 J/token
    @ 1M tok/day → 0.33 kgCO₂ (≈ 0.8 miles driven)
```

This config is Pareto-optimal: no other tested config has better SCI *and*
better BPB *and* better throughput simultaneously.

### SCI vs BPB Scatter

```
  SCI (gCO₂/tok)
  9.33e-04 │●                            ← batch=8, seq=1024 (high J/tok)
           │
           │
           │
           │
           │
           │                    ●         ← batch=1, seq=512  (bad BPB)
           │
  3.26e-04 │●                            ← batch=8, seq=2048 (Pareto ★)
           └──────────────────────
            1.566            4.066
            BPB → (lower is better)
```

**Key insight:** Batch size 8 with seq=2048 achieves the best SCI *and* best
BPB because the GPU operates at higher utilization with lower *per-token*
energy overhead. The seq=1024 variant is worse because it draws more power
(16W vs 13W) at lower throughput (2.0 vs 5.1 tok/s).

---

## 🌡️ Sensor Analysis (Aggregated Across All Runs)

### GPU Profile

| Metric | Min | Mean | P95 | Max | Samples |
|--------|:---:|:----:|:---:|:---:|:-------:|
| Temperature (°C) | 40.0 | 49.2 | 74.0 | **80.0** | 5,136 |
| Power avg (W) | 4.0 | 16.4 | 53.2 | 56.5 | 5,136 |
| Power instant (W) | 4.3 | 41.6 | 77.6 | **88.7** | 512 |
| Utilization (%) | 0 | 22 | 96 | 96 | 5,136 |
| Clock (MHz) | 201 | 1,381 | 2,405 | 2,411 | 5,136 |

**Observations:**
- GPU hit **80°C** during the longest runs — close to the thermal limit
- Instantaneous power peaked at **88.7W** (vs 56.5W average), showing burst behavior
- Mean utilization only 22% — most time is idle/loading; actual inference bursts hit 96%
- Clock ranges from 201 MHz (idle) to 2,411 MHz (full boost)

### Throttle Events

```
✅ GPU HW Thermal Throttle: clean
✅ GPU HW Slowdown: clean
✅ GPU SW Power Cap: clean
✅ NVMe Thermal Alarm: clean
```

No throttling observed across any run — the DGX Spark's cooling handled
everything within safe margins despite hitting 80°C.

### Board Thermal Profile

| Sensor | Mean (°C) | Max (°C) |
|--------|:---------:|:--------:|
| Zone 0 (CPU cluster) | 53.5 | **85.7** |
| Zone 1 | 51.0 | 74.6 |
| Zone 2 | 51.9 | **85.7** |
| Zone 4 | 52.2 | **85.1** |
| Zone 5 | 51.8 | 83.2 |
| NVMe Composite | 44.1 | 57.8 |
| NIC 0-3 | 50.9 | 71.0 |
| WiFi | 45.6 | 73.0 |

**Observation:** CPU thermal zones peaked at **85.7°C** — hotter than the GPU!
The ARM Grace cores generate significant heat during model loading and
tokenization.

### Memory Profile

| Metric | Mean | Max |
|--------|:----:|:---:|
| Used (GB) | 16.3 | **51.6** |
| Available (GB) | 103.3 | 112.3 |

The DGX Spark's 128 GB unified memory means models load via zero-copy
(C2C interconnect). Peak usage of 51.6 GB for Qwen3.5-0.8B reflects the
model weights + KV cache + tokenized data. **41 GB** of that was Transparent
Huge Pages (mapped model weights).

### Pressure Stall Information (PSI)

```
🟢 CPU some:    mean=0.05%  p95=0.38%  max=0.70%
🟢 Memory some: mean=0.00%  p95=0.00%  max=0.00%
🟢 Memory full: mean=0.00%  p95=0.00%  max=0.00%
🟢 IO some:     mean=0.04%  p95=0.18%  max=0.32%
```

All green — no resource contention. The system was never bottlenecked on
memory or I/O. CPU pressure stayed below 1%.

### Per-Run Comparison

| Run | Samples | GPU avg W | GPU max °C | GPU max % | Load avg | Mem GB |
|-----|:-------:|:---------:|:----------:|:---------:|:--------:|:------:|
| `112945` | 207 | 4.1 | 41 | 2% | 0.5 | 9.3 |
| `114055` | 745 | 4.1 | 41 | 22% | 0.5 | 10.2 |
| `120150` | 1,143 | 10.6 | 61 | 94% | 0.8 | 41.9 |
| `120810` | 892 | 16.0 | 69 | 95% | 1.1 | 41.5 |
| `122508` | 1,637 | 19.7 | **80** | 96% | 0.9 | **51.6** |
| `125731` | 9 | 9.7 | 61 | 2% | 1.4 | 9.6 |
| `130137` | 503 | **43.2** | **80** | 96% | 1.4 | 45.8 |

**The v4 run (`130137`) drew the most power** — 43.2W average GPU — because
it ran for the full 10 minutes with the model under continuous inference load.

---

## 📊 The Offline Analyzer

`analyze_all.py` was built to produce the full analysis shown above from
existing run data, **without re-running any experiments**. It:

1. **Discovers all runs** in `runs/` by scanning for `results.db` and `sensor_log.csv`
2. **Deduplicates experiments** across runs (same config hash → keep latest)
3. **Recomputes Pareto frontier** using non-dominated sorting on (SCI, BPB, throughput)
4. **Aggregates sensor data** across all runs (handles schema differences gracefully)
5. **Produces rich terminal output** with tables, scatter plots, and sensor analysis

```bash
# Basic analysis
python analyze_all.py

# With export + specific grid region
python analyze_all.py --export --region eu_france

# Custom runs directory
python analyze_all.py --runs-dir /path/to/runs --export
```

**Output sections:**

| Section | What It Shows |
|---------|--------------|
| 📂 Run Inventory | All discovered runs with experiment counts, duration, schema version |
| 📊 SCI Summary | Best SCI, BPB, J/token, throughput; daily carbon footprint at scale |
| 🏆 Pareto Frontier | Table of non-dominated configs |
| 🔬 All Experiments | Every completed experiment sorted by SCI |
| 🌱 SCI vs BPB Plot | ASCII scatter plot with Pareto frontier highlighted |
| 🎮 GPU Profile | Min/mean/p95/max for all GPU metrics |
| ⚡ Throttle Events | Binary check for thermal/power throttling |
| 🌡️ Board Thermal | All 13+ temperature sensors |
| 💾 Memory | Used/available with unit conversion |
| 📊 PSI | Pressure stall traffic lights |
| 🖥️ System | CPU frequencies, load average, fan state |
| 📈 Per-Run Highlights | Side-by-side comparison across runs |

---

## 🔑 Key Findings

### 1. Batch Size Matters More Than Sequence Length for SCI

Batch size 8 with seq=2048 achieved **3× lower SCI** than batch 8 with
seq=1024, despite processing more data per inference call. The longer
sequences amortize the fixed overhead of model loading and GPU wake-up.

### 2. The GPU Is Mostly Idle

Mean utilization across all runs was only **22%**. The actual inference
bursts hit 96%, but most wall-clock time is spent loading models,
tokenizing data, and running the controller logic. A more efficient
pipeline could dramatically improve throughput.

### 3. No Throttling — But Close

The GPU hit 80°C and CPU zones hit 85.7°C, but no throttling events
were triggered. The DGX Spark's cooling system handled the workload,
but longer sustained runs would need monitoring.

### 4. Unified Memory Is Efficient

With 128 GB unified memory and zero-copy C2C, the Qwen3.5-0.8B model
loaded with **41 GB of Transparent Huge Pages** mapped directly to GPU
memory. No PCIe transfer overhead — this is the ARM+GPU advantage.

### 5. Carbon Impact Is Real But Small

At the best config (0.000326 gCO₂/token), running 1 million tokens/day
produces **0.33 kgCO₂** — equivalent to driving 0.8 miles. But at US
average grid (400 gCO₂/kWh), that's 26× worse than running on Iceland's
geothermal grid (10 gCO₂/kWh). **Where you run matters more than how
you run.**

---

## 🗂️ File Inventory

```
autoresearch-yaledgx/
├── README.md                    # Project overview
├── REPORT.md                    # ← This document
├── program.md                   # Agent instructions (Karpathy pattern)
├── pyproject.toml               # Python project config
├── uv.lock                      # Dependency lock file
│
├── src/workbench/               # Core workbench (22 Python files)
│   ├── cli.py                   # Click CLI
│   ├── controller.py            # Research loop
│   ├── executor.py              # Experiment runner
│   ├── evaluator.py             # SCI scorer
│   ├── pareto.py                # Pareto frontier
│   ├── display.py               # Terminal dashboard
│   ├── benchmark/               # Power, thermal, quality, carbon, system
│   ├── store/                   # SQLite + data models
│   └── strategy/                # Grid, Random, Bayesian search
│
├── sensor_logger_v3.sh          # 45-channel sensor logger (308 lines)
├── sensor_logger_v4.sh          # 67-channel sensor logger (384 lines)
├── run_10min.sh                 # Original runner
├── run_10min_v3.sh              # Runner with v3 sensors
├── run_10min_v4.sh              # Runner with v4 sensors
├── analyze_all.py               # Offline analysis (672 lines)
│
├── runs/                        # All collected data
│   ├── run_20260328_*/          # 16 individual runs
│   │   ├── results.db           # SQLite experiment results
│   │   ├── sensor_log.csv       # Sensor time series (1 Hz)
│   │   ├── run_config.json      # Run configuration
│   │   └── workbench.log        # Execution log
│   └── combined/                # Aggregated exports
│       ├── combined_results.json
│       └── combined_results.csv
│
├── config/                      # YAML configs + search spaces
├── experiments/                 # Experiment outputs
└── tests/                       # Pytest suite
```

---

## 🚀 How to Reproduce

```bash
# 1. Clone
git clone https://github.com/piercebrookins/yhacktemp.git
cd yhacktemp/yhacktemp/autoresearch-yaledgx

# 2. Install
pip install -e ".[dev]"

# 3. Run a 10-minute research session with v4 sensors
chmod +x run_10min_v4.sh sensor_logger_v4.sh
./run_10min_v4.sh

# 4. Analyze all existing runs
python analyze_all.py --export --region us_average

# 5. Or run the workbench directly
workbench run --strategy auto --max-iter 50 --region us_average
```

---

*Built at YHack 2025 — because sustainability is the new frontier.*
*SCI = (E × I) + M. You can't optimize what you don't measure.*
