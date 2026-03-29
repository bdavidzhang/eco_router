# Program: Auto-Improving LLM Research Workbench

## Agent Instructions (Karpathy Pattern)

You are an autonomous research agent. Your job is to discover **Pareto-optimal**
LLM configurations — configs where you cannot improve quality without increasing
carbon intensity, and vice versa.

## The Metric: SCI (Software Carbon Intensity)

**SCI = (E × I) + M per R**

| Symbol | Meaning | Source |
|--------|---------|--------|
| **E** | Energy consumed per token (kWh) | GPU power monitoring |
| **I** | Grid carbon intensity (gCO₂/kWh) | Region config (coal vs solar) |
| **M** | Embodied emissions per token (gCO₂) | Hardware lifecycle amortization |
| **R** | Functional unit | Per token (default) |

**Lower SCI = greener code.** This is an ISO standard from the Green Software Foundation.

## Rules

1. **Never modify infrastructure code.** You only propose experiment configs.
2. Each experiment gets a **fixed time budget** (default 5 min).
3. Every config you propose must be a valid YAML diff from the baseline.
4. You optimize across **three axes simultaneously**: quality (BPB), carbon (SCI), throughput.
5. A config is "better" only if it **Pareto-dominates** at least one existing
   frontier member, or expands the frontier into new trade-off territory.

## Search Space

You may vary:
- `model_name`: Qwen 3.5 family (0.8B, 4B, 9B)
- `quantization`: none, gptq-4bit, gptq-8bit, awq-4bit, awq-8bit
- `batch_size`: 1, 2, 4, 8, 16, 32
- `sequence_length`: 128, 256, 512, 1024, 2048
- `max_new_tokens`: 64, 128, 256, 512
- `temperature`: 0.0 - 2.0
- `use_kv_cache`: true/false
- `dtype`: float16, bfloat16, float32

## Metrics (collected automatically)

- `sci_per_token`: **SCI score** in gCO₂/token (THE metric — lower is better)
- `val_bpb`: Validation bits-per-byte (lower is better)
- `tokens_per_sec`: Throughput (higher is better)
- `energy_per_token_j`: Joules per token (lower is better)
- `carbon_operational_g`: E × I component of SCI
- `carbon_embodied_g`: M component of SCI
- `gpu_power_avg_w`: Average GPU power draw (informational)

## Strategy

1. **Explore first**: Start with grid/random search to map the landscape.
2. **Exploit later**: Switch to Bayesian optimization once you have ≥20 data points.
3. **Discard dominated**: Never re-run a config that is strictly dominated.
4. **Track thermal state**: If GPU temp exceeds 80°C, pause and cool down.
5. **Region matters**: Same model on a coal grid (Poland, 650 gCO₂/kWh) has
   26× higher SCI than on hydro (Sweden, 25 gCO₂/kWh). Config matters less
   than grid in some cases — quantify this!

## The Metric

*"The metric is val_bpb — lower is better." — Karpathy*
*Ours is SCI per token — gCO₂ per token. Lower is greener.*
*SCI = (E × I) + M. You can't optimize what you don't measure.*
