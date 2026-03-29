# 🔬 Auto-Improving LLM Research Workbench

> *"The metric is val_bpb — lower is better." — Karpathy*
> *Ours is SCI per token — gCO₂ per token. Lower is greener.*

An autonomous research loop that discovers **Pareto-optimal LLM configurations** — optimizing across quality, carbon intensity, and throughput simultaneously. Run it overnight on a DGX Spark and wake up to a frontier of sustainable options.

Inspired by [Karpathy's Autoresearch](https://github.com/karpathy/autoresearch), extended with the **Green Software Foundation's SCI (Software Carbon Intensity)** standard.

## 🌱 SCI: The Optimization Target

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│            SCI  =  (E  ×  I)  +  M    per R                │
│                                                             │
│    E = Energy (kWh/token)     ← GPU power monitoring        │
│    I = Carbon Intensity       ← Grid region (coal vs solar) │
│    M = Embodied Emissions     ← Hardware lifecycle          │
│    R = Functional Unit        ← Per token                   │
│                                                             │
│    Unit: gCO₂ per token       Lower is greener 🌍           │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Why SCI over raw energy?** Because 1 kWh in Sweden (hydro, 25 gCO₂) ≠ 1 kWh in Poland (coal, 650 gCO₂). SCI captures the full carbon picture: operational energy AND embodied hardware emissions.

## 🏗️ Architecture

```
┌──────────────────────────────────────────────┐
│            RESEARCH CONTROLLER               │
│                                              │
│  Strategy ──▶ Executor ──▶ Evaluator ──┐     │
│  (search)     (run exp)    (SCI score) │     │
│     ▲                                  │     │
│     └──────────────────────────────────┘     │
│                    │                         │
│        Result Store (SQLite)                 │
│        + SCI / Carbon tracking               │
└──────────────────────────────────────────────┘
```

## 🚀 Quick Start

```bash
# Install
pip install -e ".[dev]"

# See available grid regions
workbench regions

# Run with US average grid (400 gCO₂/kWh)
workbench run --strategy auto --max-iter 50

# Run with France's nuclear grid (55 gCO₂/kWh)
workbench run --region eu_france --max-iter 50

# Run with custom carbon intensity
workbench run --carbon-intensity 120 --max-iter 50

# Check status (SCI-first summary)
workbench status

# View results sorted by SCI
workbench results --pareto --sort sci

# Export
workbench export --format json --output results.json
```

## 🌍 Grid Carbon Intensity Regions

| Region | gCO₂/kWh | Notes |
|--------|-----------|-------|
| `renewable_100` | 0 | 100% renewable |
| `iceland` | 10 | Geothermal + hydro |
| `eu_sweden` | 25 | Hydro + nuclear |
| `eu_france` | 55 | Nuclear-dominant |
| `us_oregon` | 80 | Hydro-heavy (us-west-2) |
| `us_average` | 400 | US national average |
| `eu_poland` | 650 | Coal-dominant |

Run `workbench regions` for the full list.

## 🔍 Search Strategies

| Strategy | When to Use |
|----------|-------------|
| **Grid** | Initial sweep — map the landscape |
| **Random** | Baseline, escape local optima |
| **Bayesian** | Exploitation — converge on Pareto frontier |
| **Auto** | Starts with grid → random → bayesian automatically |

## 📊 Metrics

| Metric | Unit | Goal |
|--------|------|------|
| **SCI per token** | gCO₂/token | ↓ **THE metric** |
| Validation BPB | bits/byte | ↓ Lower is better |
| Throughput | tokens/sec | ↑ Higher is better |
| Energy per token | Joules/token | ↓ Lower is better |
| Operational carbon (E×I) | gCO₂ | Informational |
| Embodied carbon (M) | gCO₂ | Informational |
| GPU Power (avg) | Watts | Informational |

## 🎯 Pareto Optimization

A config is **Pareto-optimal** if no other config is strictly better on ALL three objectives (BPB, SCI, throughput). The workbench maintains this frontier automatically using non-dominated sorting.

## 🖥️ DGX Spark Target

Designed for NVIDIA DGX Spark (GB10 Blackwell GPU, 128 GB unified memory):

- Power monitoring via `nvidia-smi`
- Thermal monitoring via ACPI zones (85°C safety threshold)
- Controller pinned to A725 efficiency cores
- Zero-copy GPU memory via C2C
- Embodied emissions estimated from hardware lifecycle

Works on other hardware too — with graceful fallbacks for missing sensors.

## 🧪 Testing

```bash
pytest tests/ -v
```

## 📁 Project Structure

```
├── config/                  # YAML configs + search spaces
├── experiments/             # SQLite DB and experiment outputs
├── src/workbench/
│   ├── cli.py               # Click CLI (SCI-aware flags)
│   ├── controller.py        # Main research loop
│   ├── executor.py          # Experiment runner
│   ├── evaluator.py         # SCI scorer + Pareto maintenance
│   ├── pareto.py            # Pareto frontier (SCI-based)
│   ├── display.py           # Terminal dashboard (SCI-first)
│   ├── strategy/            # Grid, Random, Bayesian search
│   ├── benchmark/
│   │   ├── carbon.py        # SCI calculator + region presets
│   │   ├── harness.py       # Full benchmark pipeline
│   │   ├── power.py         # nvidia-smi power monitoring
│   │   ├── thermal.py       # ACPI thermal zone monitoring
│   │   └── quality.py       # Perplexity / BPB evaluation
│   └── store/               # SQLite + data models
├── tests/                   # Pytest suite (SCI coverage)
├── program.md               # Agent instructions (Karpathy pattern)
└── pyproject.toml
```

## 📄 License

MIT

---

*Built for YHack 2025 — because sustainability is the new frontier.*
*SCI = (E × I) + M. You can't optimize what you don't measure.*
