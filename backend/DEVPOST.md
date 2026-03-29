# 🌱 EcoRoute — Carbon-Aware AI Inference on DGX Spark

> **SCI = (E × I) + M. You can't optimize what you don't measure.**

---

## 💡 Inspiration

AI is getting smarter, but it's also getting hungrier. A single GPT-4 training run consumes the energy of ~120 US homes for a year — and most teams optimize for a single metric (quality) while completely ignoring energy, carbon, and thermal constraints.

Then Karpathy dropped [Autoresearch](https://github.com/karpathy/autoresearch) — an autonomous agent loop that modifies code, trains, evaluates, and repeats. Brilliant, but it optimizes one metric (`val_bpb`) on one axis.

We asked: **what if we ran that same loop, but optimized across quality, carbon intensity, AND throughput simultaneously?** And what if we measured carbon using a real ISO standard — not just raw watts, but the full lifecycle including where the electricity comes from?

That's EcoRoute. Run it overnight on a DGX Spark, wake up to a Pareto frontier of sustainable LLM configurations. Then route every inference request to the greenest model that meets the quality bar.

---

## 🔍 What It Does

EcoRoute is a three-part system:

### 1. 🔬 AutoResearch Workbench
An autonomous research loop that discovers **Pareto-optimal LLM configurations** on NVIDIA DGX Spark. It varies model size, quantization, batch size, sequence length, and dtype — then measures quality (BPB), carbon intensity (SCI), and throughput simultaneously.

A configuration is Pareto-optimal if no other configuration is strictly better on ALL three axes. The workbench maintains this frontier automatically using non-dominated sorting.

**The core metric is SCI (Software Carbon Intensity):**
```
SCI  =  (E  ×  I)  +  M    per token

E = Energy consumed per token (kWh)     ← measured via nvidia-smi at 1Hz
I = Grid carbon intensity (gCO₂/kWh)   ← configurable by region (19 presets)
M = Embodied hardware emissions (gCO₂)  ← amortized over device lifetime
```

**Why SCI instead of raw energy?** Because 1 kWh in Sweden (hydro, 25 gCO₂) ≠ 1 kWh in Poland (coal, 650 gCO₂). The same model config can have 26× different carbon impact depending on *where* it runs.

### 2. 📊 Green LLM Bench (Website)
An interactive benchmark dashboard that visualizes the Pareto frontier — like MLPerf, but for sustainability. Features:
- **SCI vs BPB scatter plot** with Pareto frontier highlighted
- **Per-model throughput vs carbon** breakdowns
- **Real-time sensor timeline** — GPU power, temperature, utilization over time
- **Carbon calculator** — estimate your LLM's footprint by region
- Sortable leaderboard of all tested configurations

### 3. ⚡ EcoRoute Extension (Zed + MCP)
A developer-facing tool that brings carbon-aware model routing into the IDE:
- **Zed slash command** (`/eco`) that recommends the greenest model for your task
- **MCP server** that serves real SCI data from the workbench
- Routes coding tasks to the most carbon-efficient model that meets the quality threshold

---

## 🏗️ How We Built It

### Hardware: NVIDIA DGX Spark
| Component | Spec |
|-----------|------|
| **GPU** | NVIDIA GB10 — Blackwell architecture |
| **Memory** | 128 GB unified LPDDR5X (CPU+GPU shared via C2C) |
| **CPU** | ARM big.LITTLE: 10× Cortex-X925 @ 3.9 GHz + 10× Cortex-A725 @ 2.8 GHz |
| **Storage** | 1 TB NVMe (Phison PS5027-E27T) |
| **Network** | 4× Mellanox ConnectX-7 + MediaTek WiFi |
| **CUDA** | 13.0 / Driver 580.126.09 |

### Architecture
```
┌──────────────────────────────────────────────────────────┐
│                   WORKBENCH CONTROLLER                    │
│  (Python — pinned to ARM A725 efficiency cores)          │
│                                                          │
│  Strategy ──▶ Executor ──▶ Evaluator ──┐                 │
│  (search)     (run exp)    (SCI score) │                 │
│     ▲                                  │                 │
│     └──────────────────────────────────┘                 │
│                    │                                     │
│        Result Store (SQLite — incremental saves)         │
│                                                          │
├──────────────────────────────────────────────────────────┤
│         67-CHANNEL SENSOR LOGGER (bash, 1 Hz)            │
│   GPU power/temp/util · ACPI thermals · memory · PSI     │
│   NVMe · PCIe · CPU frequencies · fan · network I/O      │
├──────────────────────────────────────────────────────────┤
│           PARALLEL RUNNER v7 (infinite mode)             │
│   N concurrent workers · incremental saves · overnight   │
│   Thermal protection (85°C abort) · git auto-push/5min   │
├──────────────────────────────────────────────────────────┤
│       OFFLINE ANALYZER → Website → Zed Extension         │
└──────────────────────────────────────────────────────────┘
```

### Search Strategies
| Strategy | When | How |
|----------|------|-----|
| **Grid** | Initial sweep | Exhaustive coverage of the config space |
| **Random** | Baseline | Escape local optima, fill gaps |
| **Bayesian** | After ≥20 data points | Optuna TPE sampler — converges on the Pareto frontier |
| **Auto** | Default | Grid → Random → Bayesian automatically |

### Sensor Logger Evolution
We systematically probed every sensor accessible without root on the DGX Spark:
```
v1:  26 channels  (GPU basics + thermal zones)
v3:  46 channels  (+20: PSI, throttle events, NVMe I/O, PCIe)
v4:  67 channels  (+21: CPU decomposition, VM counters, network, hugepages)
```

Each version was a deeper dig into the hardware — we found that the DGX Spark uses **41 GB of Transparent Huge Pages** for model weights, that PCIe generation drops from Gen4 to Gen1 when idle, and that CPU zones run hotter than the GPU (85.7°C vs 80°C).

### The Runner (v7)
The final runner is an infinite worker-pool that:
- Maintains N active experiments at all times (slot recycling)
- Saves each result to SQLite the *instant* it completes
- Auto-pushes to GitHub every 5 minutes
- Gracefully drains on Ctrl+C — zero data loss
- Thermal protection: pauses if GPU hits 85°C

---

## 🧪 Results

### Best Configuration Found
```
🏆  Qwen/Qwen3.5-4B (no quantization)
    Batch size:   8
    Seq length:   2048
    SCI:          0.000326 gCO₂/token
    BPB:          1.5657 bits/byte
    Throughput:   5.1 tokens/sec
    Energy:       2.66 J/token
    GPU Power:    13.4W average

    @ 1M tokens/day → 0.33 kgCO₂ (≈ driving 0.8 miles)
```

### Hardware Observations
| Metric | Mean | Max | Notes |
|--------|:----:|:---:|-------|
| GPU Temperature | 49.2°C | **80°C** | Close to limit, zero throttling |
| GPU Power (avg) | 16.4 W | 56.5 W | Burst behavior under load |
| GPU Power (instant) | 41.6 W | **88.7 W** | 1.6× the average reading |
| GPU Utilization | 22% | 96% | Mostly idle; inference bursts at 96% |
| CPU Zone Temp | 53.5°C | **85.7°C** | *Hotter than the GPU!* |
| Memory Used | 16.3 GB | 51.6 GB | 41 GB of THP for model weights |
| PSI (all) | <0.1% | <1% | Zero resource contention |

### Key Findings

1. **Batch size matters more than model size for carbon.** Batch 8 + seq 2048 achieved 3× lower SCI than batch 8 + seq 1024. Longer sequences amortize the fixed overhead of GPU wake-up.

2. **The GPU is mostly idle.** Mean 22% utilization — actual inference hits 96%, but most time is loading models and tokenizing. A better pipeline = dramatically better throughput.

3. **No throttling, but close.** GPU hit 80°C, CPU hit 85.7°C, but the DGX Spark's cooling kept everything in check. Longer sustained runs would need monitoring.

4. **Unified memory is a superpower.** 128 GB shared via C2C means zero-copy model loading. The ARM+GPU architecture eliminates PCIe transfer overhead entirely.

5. **Where you run > how you run.** At US average grid (400 gCO₂/kWh), the same config produces 26× more carbon than running on Iceland's geothermal grid (10 gCO₂/kWh). Grid carbon intensity dominates the SCI equation.

---

## 😤 Challenges We Ran Into

- **`perf_event_paranoid=4`** — The DGX Spark locks down ARM PMU counters (76 events on X925, 65 on A725) behind `perf_event_paranoid=4`. We couldn't access cache miss rates, branch mispredicts, or instructions retired without root. We worked around this with nvidia-smi + sysfs sensors.

- **Quantization dependencies** — GPTQ and AWQ libraries have complex CUDA build requirements on ARM64. The Mistral-7B GPTQ-8bit experiment failed because `optimum` needed a custom build. We focused on unquantized Qwen3.5 models that loaded cleanly.

- **Instantaneous vs average power** — `nvidia-smi` reports both `power.draw` (moving average) and `power.draw.instant` (point-in-time). The instant readings peaked at 88.7W while averages showed 56.5W. We had to capture both to get the real energy picture.

- **Sensor schema evolution** — Going from 26 to 67 sensor channels across logger versions meant the offline analyzer had to handle mixed schemas gracefully. Dynamic column detection saved us.

- **Thermal surprises** — We expected the GPU to be the hottest component. Nope — CPU thermal zones peaked at 85.7°C during model loading. The ARM Grace cores generate serious heat during tokenization and data prep.

---

## 🏆 Accomplishments We're Proud Of

- **67-channel, 1 Hz sensor logging** on DGX Spark — the most comprehensive open-source hardware profiling of this machine we're aware of
- **ISO SCI standard implementation** with 19 grid carbon intensity presets — from Iceland (10 gCO₂/kWh) to Poland (650 gCO₂/kWh)
- **Infinite overnight runner** that auto-saves incrementally and auto-pushes to GitHub — zero data loss even on crash
- **Full Pareto frontier computation** with non-dominated sorting across 3 simultaneous objectives
- **End-to-end pipeline**: autonomous research → offline analysis → interactive website → IDE extension
- **Zero throttling** across 60+ runs despite pushing the hardware to 80°C GPU / 85.7°C CPU

---

## 📖 What We Learned

- **SCI is the right metric.** Raw watts or joules-per-token misses the carbon picture entirely. The grid carbon intensity multiplier can swing your footprint by 65× between regions.
- **Batching is the #1 lever for green inference.** Higher GPU utilization = lower per-token energy overhead. Batch size 8 was the sweet spot on DGX Spark.
- **ARM big.LITTLE matters for orchestration.** Pinning the controller to A725 efficiency cores keeps the X925 performance cores free for actual work. Measured <5% overhead.
- **Unified memory changes the game.** Zero-copy C2C means model loading isn't a bottleneck — 41 GB of Transparent Huge Pages mapped directly. No PCIe transfer tax.
- **Hardware monitoring is harder than ML.** Getting 67 reliable sensor channels on a locked-down system took more engineering than the Bayesian optimizer.

---

## 🔮 What's Next

- **More models**: Extend to Llama-3, Phi-3, Gemma-2 families. The search space is infinite.
- **Quantization support**: Fix GPTQ/AWQ on ARM64 to unlock 4-bit and 8-bit configs.
- **Multi-node**: Scale the runner across multiple DGX Sparks with distributed search.
- **Live carbon API**: Real-time grid carbon intensity from WattTime/Electricity Maps instead of static presets.
- **CI/CD integration**: Carbon budget checks in GitHub Actions — fail the build if SCI regresses.
- **ARM PMU access**: With `perf_event_paranoid=-1`, unlock 76 CPU performance counters for deeper analysis.

---

## 🛠️ Built With

- **Hardware**: NVIDIA DGX Spark (GB10 Blackwell, 128 GB unified memory, ARM Grace)
- **Languages**: Python, Bash, Rust (Zed extension), HTML/CSS/JS (website)
- **ML Frameworks**: PyTorch, HuggingFace Transformers, Accelerate
- **Optimization**: Optuna (Bayesian TPE sampler)
- **Monitoring**: nvidia-smi, Linux sysfs, /proc, ACPI thermal zones
- **Data**: SQLite, Rich (terminal UI), Chart.js (web)
- **Standard**: [Green Software Foundation SCI (ISO/IEC 21031:2024)](https://sci-guide.greensoftware.foundation/)
- **Inspiration**: [Karpathy's Autoresearch](https://github.com/karpathy/autoresearch)

---

## 📁 Repository Structure

```
├── DEVPOST.md                         ← You are here
├── yhacktemp/
│   ├── autoresearch-yaledgx/          ← Core workbench
│   │   ├── src/workbench/             ← Python package (22 modules)
│   │   │   ├── controller.py          ← Autonomous research loop
│   │   │   ├── executor.py            ← Experiment runner
│   │   │   ├── evaluator.py           ← SCI scorer
│   │   │   ├── pareto.py              ← Pareto frontier (non-dominated sorting)
│   │   │   ├── display.py             ← Rich terminal dashboard
│   │   │   ├── benchmark/             ← Power, thermal, carbon, quality, system
│   │   │   ├── store/                 ← SQLite + data models
│   │   │   └── strategy/              ← Grid, Random, Bayesian search
│   │   ├── parallel_runner_v7.py      ← Infinite worker-pool runner
│   │   ├── run_30s_v7.sh             ← Self-bootstrapping experiment script
│   │   ├── sensor_logger_v4.sh        ← 67-channel 1 Hz sensor logger
│   │   ├── analyze_all.py             ← Offline aggregator + report generator
│   │   ├── runs/                      ← 60+ experiment runs with sensor data
│   │   └── tests/                     ← Pytest suite
│   ├── ecoroute-mcp/                  ← MCP server for Zed integration
│   └── ecoroute-zed/                  ← Zed /eco slash command (Rust → WASM)
├── dgx_spark_sensors/                 ← Sensor discovery toolkit
└── DGX_SPARK_COMPLETE_SENSOR_REPORT.md ← Full hardware report
```

---

## 👥 Team

Built at **YHack 2025** — Yale University, ASUS Sustainability Track.

**Pierce Brookins** — Architecture, workbench core, sensor engineering, DGX Spark wrangling

---

*Because sustainability is the new frontier.*
*SCI = (E × I) + M. You can't optimize what you don't measure.* 🌍
