# PRD: Auto-Improving LLM Research Workbench

**Version**: 0.1 — MVP  
**Author**: Pierce Brookins  
**Date**: 2026-03-28  
**Hardware Target**: NVIDIA DGX Spark (ARM Cortex-X925/A725, GB10 Blackwell GPU, 128 GB unified memory)  
**Inspiration**: [Karpathy's Autoresearch](https://github.com/karpathy/autoresearch)

---

## 1. Problem

LLM scaling is hitting a wall — not a capability wall, but a sustainability wall. Training and inference costs grow faster than the models improve. A single GPT-4 training run consumes the energy of ~120 US homes for a year. Most research labs optimize for a single metric (quality) while ignoring power draw, cost-per-token, and thermal constraints entirely.

Meanwhile, Karpathy's Autoresearch proved that an autonomous agent loop — modify code, train, evaluate, keep-or-discard, repeat — can explore architecture and hyperparameter space faster than humans. But Autoresearch optimizes one metric (`val_bpb`) on one axis. We need the same loop optimizing across **three axes simultaneously: quality, cost, and energy**.

## 2. Vision

An autonomous research loop running on a DGX Spark that discovers **Pareto-optimal configurations** — the set of model configs where you cannot improve quality without increasing energy, and vice versa. The system runs overnight, and you wake up to a frontier of options: "this 4-bit Llama-3 config gives 92% of FP16 quality at 18% of the energy."

**Target users**: ML researchers, inference-platform engineers, AI sustainability teams.

## 3. Goals & Non-Goals

| Goals | Non-Goals |
|-------|-----------|
| Find energy-efficient inference/training configs automatically | Replace human researchers — humans set the search space |
| Multi-objective optimization (quality × cost × energy) | Chase SOTA at any cost — the point is efficiency |
| Reproducible, versioned experiment artifacts | General-purpose AutoML — we focus on LLM workloads only |
| DGX Spark-native: exploit unified memory, ARM cores, GB10 | Multi-node clusters (future roadmap) |

## 4. Core Design: The Research Loop

Borrowing from Karpathy's key insight: the agent doesn't touch infrastructure code. It modifies a single experiment config surface and the system handles the rest.

```
┌─────────────────────────────────────────────────────────────┐
│                    RESEARCH CONTROLLER                       │
│  (Python orchestrator — runs on ARM efficiency cores)        │
│                                                              │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐               │
│  │ Strategy │───▶│ Executor │───▶│ Evaluator│──┐            │
│  │ Engine   │    │          │    │          │  │            │
│  │ (search) │◀───│ (run exp)│◀───│ (score)  │◀─┘            │
│  └──────────┘    └──────────┘    └──────────┘  keep/discard │
│       │                │               │                     │
│       ▼                ▼               ▼                     │
│  ┌─────────────────────────────────────────┐                │
│  │          Result Store (SQLite)           │                │
│  │  configs │ metrics │ pareto_rank │ diffs │                │
│  └─────────────────────────────────────────┘                │
├──────────────────────────────────────────────────────────────┤
│                    DGX SPARK RUNTIME                         │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ GB10 Blackwell GPU  │  128 GB Unified Memory (C2C)   │   │
│  │ Max 3003 MHz        │  Zero-copy CPU↔GPU transfers   │   │
│  │ ~4W idle / ~50W load│  nvidia-smi power monitoring   │   │
│  ├──────────────────────┴───────────────────────────────┤   │
│  │ 20× ARM Cores (10× X925 @3.9G + 10× A725 @2.8G)    │   │
│  │ Controller on A725 (efficiency) / Workload on X925   │   │
│  └──────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

### Loop Mechanics

Each iteration:

1. **Strategy Engine** proposes an experiment config (model variant, quantization scheme, batch size, sequence length).
2. **Executor** runs the experiment for a **fixed time budget** (5 min default, configurable). This ensures fair comparison across configs regardless of model size — a direct adoption of Karpathy's design.
3. **Evaluator** collects multi-objective metrics (see §6), scores the run, and updates the Pareto frontier.
4. **Strategy Engine** uses the updated frontier + search strategy to propose the next config.

The controller itself is pinned to the A725 efficiency cores via `taskset`, keeping the X925 performance cores and GPU fully available for experiments. Target overhead: **<5% of GPU time**.

## 5. Core Features

### 5.1 Auto-Experiment Runner

Three search strategies for MVP:

| Strategy | When to Use |
|----------|-------------|
| **Grid** | Initial sweep — map the landscape |
| **Random** | Baseline comparator, escape local optima |
| **Bayesian (Optuna)** | Exploitation — converge on Pareto frontier |

Each experiment is a versioned config diff (like Autoresearch's `train.py` modifications, but structured as YAML/JSON rather than raw code edits). This keeps the search space tractable and results reproducible.

### 5.2 Benchmark Engine

Measures every run across four dimensions:

| Metric | Source | Unit |
|--------|--------|------|
| **Inference latency** | Wall clock (P50/P95/P99) | ms/token |
| **Throughput** | Tokens generated / wall time | tokens/sec |
| **GPU power draw** | `nvidia-smi --query-gpu=power.draw` (avg + instantaneous) | Watts |
| **Energy per token** | (avg power × inference time) / tokens | Joules/token |
| **Model quality** | Perplexity on held-out eval set (bits-per-byte for comparability) | val_bpb |
| **Thermal headroom** | GPU T.Limit from `nvidia-smi -q` | °C remaining |

**DGX Spark-specific implementation note**: The GB10 reports power via `nvidia-smi` with both average and instantaneous readings (confirmed working — see sensor report). Memory usage reports as N/A because of unified C2C memory — track via `/proc/meminfo` instead. Monitor 7 ACPI thermal zones via `/sys/class/thermal/thermal_zone*/temp` for board-level thermal awareness.

### 5.3 Optimization Toolkit

Techniques the runner can compose per-experiment:

| Technique | Implementation | Expected Impact |
|-----------|---------------|-----------------|
| **Quantization** (4-bit, 8-bit) | GPTQ, AWQ via `auto-gptq` / `autoawq` | 2–4× memory reduction, ~30% energy savings |
| **Speculative decoding** | Draft model on A725 cores, verify on GPU | Latency reduction with same quality |
| **Dynamic batching** | Continuous batching with `vllm` or custom | Throughput/watt improvement |
| **KV-cache optimization** | PagedAttention, quantized KV cache | Memory efficiency on unified pool |
| **Model routing** | Quality-aware routing between small/large variants | Cost reduction for easy queries |

**Unified memory advantage**: With C2C, model weights loaded into system RAM are directly accessible by the GPU without PCIe copies. This means quantization experiments can load/swap models significantly faster than on discrete-GPU systems.

### 5.4 Experiment Tracking

- **SQLite** result store (MVP) — one table per experiment, indexed by config hash
- Every run records: full config, git-style diff from baseline, all metrics, timestamps, thermal state
- CLI query tool: `workbench results --pareto --sort energy_per_token`
- Export to CSV/JSON for external analysis

### 5.5 Pareto Frontier Dashboard

Terminal-based (MVP) visualizer showing:

- Scatter plot: quality (x) vs. energy-per-token (y), colored by quantization level
- Table: top-10 configs on the current Pareto frontier
- Trend: frontier improvement over experiment iterations

## 6. Sustainability Metrics — Definitions

| Metric | Formula | Target (vs. FP16 baseline) |
|--------|---------|---------------------------|
| **Energy/token** | `avg_power_W × inference_time_s / token_count` | **≥30% reduction** |
| **GPU efficiency** | `throughput_tokens_per_sec / avg_power_W` | Maximize |
| **Thermal efficiency** | `tokens_generated_before_P_state_throttle` | No throttling at <50W sustained |
| **Cost/inference** | `energy_per_token × $/kWh` (configurable rate) | Derived |
| **Pareto rank** | Non-dominated sorting across (quality, energy, latency) | Frontier membership |

## 7. MVP Scope

- [ ] Auto-experiment runner with grid, random, and Bayesian search
- [ ] Benchmark harness: latency, throughput, power draw, quality (perplexity / bits-per-byte)
- [ ] Support 2 model families: **Llama 3** and **Mistral** with 4-bit and 8-bit quantization
- [ ] Power monitoring via `nvidia-smi` CLI integration (confirmed working on GB10)
- [ ] SQLite result store with CLI query tool
- [ ] Single-node DGX Spark deployment only
- [ ] Terminal-based Pareto frontier display
- [ ] `program.md`-style agent instructions (Karpathy pattern) for experiment generation

**Out of scope for MVP**: Web dashboard, multi-node, RL-based search, carbon API integration.

## 8. DGX Spark Implementation Notes

These are hardware-specific details confirmed via direct sensor probing of the target machine:

| Concern | Implementation Detail |
|---------|----------------------|
| **Power monitoring** | `nvidia-smi --query-gpu=power.draw --format=csv,noheader,nounits` returns watts. Both avg and instantaneous available via `-q`. Sample at 1Hz during experiments via `nvidia-smi dmon -d 1`. |
| **Thermal guardrails** | Read `/sys/class/thermal/thermal_zone{0..6}/temp` (millidegrees C). All 7 zones are `acpitz` type. Abort experiment if any zone exceeds 85°C. Current idle: ~41°C. |
| **Unified memory** | GPU reports `FB Memory: N/A` and `BAR1: N/A` because C2C is enabled. Track memory pressure via `free -h` and `/proc/meminfo`. No PCIe bottleneck for model loading. |
| **CPU affinity** | Pin controller to `cpu0-5,cpu11-14` (A725 LITTLE cores @ 2.8 GHz). Leave `cpu6-10,cpu15-19` (X925 big cores @ 3.9 GHz) for data preprocessing and model loading. |
| **Fan management** | ACPI fan reports states 0-3, not RPM. State 2 = medium (current idle). No user-controllable fan curve — firmware-managed. Monitor via `/sys/class/hwmon/hwmon0/fan1_input`. |
| **Container images** | Must be `aarch64`. Use `--platform linux/arm64` for all Docker pulls. Pin PyTorch to NVIDIA's ARM builds. |
| **NVMe thermals** | Storage at 37.8°C idle (crit: 84.8°C). Monitor via `hwmon2` if running data-heavy preprocessing. |
| **Network** | 4× ConnectX-7 NICs available for future multi-node. Not used in MVP. |

## 9. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Goodharting on benchmarks** | Configs that game eval but fail in production | Multi-objective scoring; holdout test sets; cross-model validation |
| **Controller overhead stealing GPU** | Inflated energy measurements, slower experiments | Pin controller to A725 cores; measure and subtract controller power; target <5% GPU utilization overhead |
| **ARM dependency hell** | PyTorch/CUDA packages missing aarch64 wheels | Use NVIDIA's official ARM containers; pin versions in lockfile; CI on aarch64 |
| **Unified memory contention** | GPU and CPU fighting over 128 GB pool | Monitor `/proc/meminfo` MemAvailable; set OOM guardrails; pre-allocate GPU memory budget per experiment |
| **Thermal throttling skews results** | Power readings become meaningless if GPU is throttling | Check `nvidia-smi -q` throttle reasons before/after each run; discard results where `HW Thermal Slowdown: Active` |

## 10. Success Metrics

| Metric | Target | Timeframe |
|--------|--------|-----------|
| Energy reduction vs. FP16 baseline | **≥30%** at ≤5% quality loss | MVP |
| Research iteration speed | **10× faster** than manual tuning | MVP |
| Experiments per overnight run | **~100** (5-min budget × 8 hours) | MVP |
| External adoption | **3+ research groups** using the tool | 6 months post-launch |

## 11. Future Roadmap

| Phase | Features |
|-------|----------|
| **v0.2** | Web dashboard with interactive Pareto plots; RL-based experiment generation |
| **v0.3** | Multi-node DGX Spark clusters via ConnectX-7; distributed experiment runners |
| **v0.4** | Carbon reporting API integration (electricity maps, WattTime); auto-schedule experiments during low-carbon grid periods |
| **v1.0** | Cross-lab experiment sharing; federated Pareto frontiers; community leaderboard |

---

*"The metric is val_bpb — lower is better." — Karpathy.  
Ours is joules-per-token at equivalent quality — lower is better.*
