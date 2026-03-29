#!/usr/bin/env bash
# run_15iter.sh — Run exactly 15 real workbench iterations in ONE run directory.
#
# Single workbench call with --max-iter 15 and --strategy grid.
# Grid uses Qwen3.5-0.8B + 4B with small configs — no 9B OOM risk.
# All results land in one runs/run_TIMESTAMP/ directory.
# Generates SCI vs BPB graph at the end.
#
# Usage:
#   ./run_15iter.sh
#   ./run_15iter.sh --region eu_france
#   ./run_15iter.sh --budget 90

set -euo pipefail

# ── Config ─────────────────────────────────────────────────────────────────────
TARGET_ITERS=15
REGION="${REGION:-us_average}"
TIME_BUDGET="${TIME_BUDGET:-60}"    # Seconds per experiment (~1.5 min wall time)
STRATEGY="grid"                     # Qwen3.5-0.8B + 4B, batch [1,4,16], no 9B
HF_TOKEN="${HF_TOKEN:-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}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --region)   REGION="$2"; shift 2 ;;
    --budget)   TIME_BUDGET="$2"; shift 2 ;;
    --target)   TARGET_ITERS="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

export HF_TOKEN

# ── Paths ───────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RUN_DIR="${SCRIPT_DIR}/runs/run_${TIMESTAMP}"
DB_PATH="${RUN_DIR}/results.db"
SENSOR_LOG="${RUN_DIR}/sensor_log.csv"
WORKBENCH_LOG="${RUN_DIR}/workbench.log"
VENV_DIR="${SCRIPT_DIR}/.venv"
PYTHON="${VENV_DIR}/bin/python"

mkdir -p "${RUN_DIR}"

# ── Banner ──────────────────────────────────────────────────────────────────────
cat << BANNER

  ╔══════════════════════════════════════════════════════════════╗
  ║  🔬 Qwen 3.5 Research Run — ${TARGET_ITERS} Real Iterations            ║
  ║     Single run dir · grid strategy · SCI vs BPB            ║
  ╚══════════════════════════════════════════════════════════════╝

  Iterations:        ${TARGET_ITERS} (one workbench call)
  Time budget/exp:   ${TIME_BUDGET}s  (~$((TIME_BUDGET * TARGET_ITERS / 60)) min estimated)
  Strategy:          ${STRATEGY} (Qwen3.5-0.8B + 4B)
  Region:            ${REGION}
  Run dir:           runs/run_${TIMESTAMP}/

  SCI = (E × I) + M — gCO₂ per token. Lower is greener.

BANNER

# ── Bootstrap venv ──────────────────────────────────────────────────────────────
echo "━━━ Phase 1: Environment ━━━"
if [[ ! -d "${VENV_DIR}" ]]; then
  python3 -m venv "${VENV_DIR}"
fi
"${VENV_DIR}/bin/pip" install --upgrade pip --quiet
"${VENV_DIR}/bin/pip" install click pyyaml optuna rich matplotlib --quiet
"${VENV_DIR}/bin/pip" install -e "${SCRIPT_DIR}" --quiet
echo "  ✓ Environment ready"
echo ""

# ── Write run metadata ──────────────────────────────────────────────────────────
cat > "${RUN_DIR}/run_config.json" << META
{
  "timestamp": "${TIMESTAMP}",
  "target_iterations": ${TARGET_ITERS},
  "time_budget_per_experiment_sec": ${TIME_BUDGET},
  "strategy": "${STRATEGY}",
  "region": "${REGION}",
  "model_family": "Qwen/Qwen3.5",
  "hostname": "$(hostname)",
  "script": "run_15iter.sh"
}
META

# ── Sensor logger (background) ──────────────────────────────────────────────────
SENSOR_PID=""
SENSOR_LOGGER="${SCRIPT_DIR}/sensor_logger_v4.sh"

cleanup() {
  echo ""
  echo "🛑 Shutting down sensor logger..."
  if [[ -n "${SENSOR_PID}" ]] && kill -0 "${SENSOR_PID}" 2>/dev/null; then
    kill "${SENSOR_PID}" 2>/dev/null || true
    wait "${SENSOR_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

if [[ -x "${SENSOR_LOGGER}" ]]; then
  echo "━━━ Phase 2: Sensor Logger ━━━"
  bash "${SENSOR_LOGGER}" -i 1 -o "${SENSOR_LOG}" &
  SENSOR_PID=$!
  echo "  ✓ Sensor logger PID: ${SENSOR_PID}"
  echo ""
fi

# ── Single workbench run — all 15 iterations in one call ────────────────────────
echo "━━━ Phase 3: Research Loop (${TARGET_ITERS} iterations) ━━━"
echo ""
echo "  Strategy:    ${STRATEGY}  (grid covers 36 configs; stopping at ${TARGET_ITERS})"
echo "  Budget/exp:  ${TIME_BUDGET}s"
echo "  DB:          ${DB_PATH}"
echo ""

cd "${SCRIPT_DIR}"

"${PYTHON}" -c "from workbench.cli import main; main()" -- run \
  --strategy "${STRATEGY}" \
  --max-iter "${TARGET_ITERS}" \
  --db "${DB_PATH}" \
  --region "${REGION}" \
  --time-budget "${TIME_BUDGET}" \
  2>&1 | tee "${WORKBENCH_LOG}"

echo ""
echo "━━━ Phase 4: Export ━━━"
"${PYTHON}" -c "from workbench.cli import main; main()" -- export \
  --format csv  --output "${RUN_DIR}/results.csv"  --db "${DB_PATH}" 2>/dev/null || true
"${PYTHON}" -c "from workbench.cli import main; main()" -- export \
  --format json --output "${RUN_DIR}/results.json" --db "${DB_PATH}" 2>/dev/null || true
echo "  ✓ Exported CSV + JSON"

echo ""
"${PYTHON}" -c "from workbench.cli import main; main()" -- results \
  --db "${DB_PATH}" --pareto 2>/dev/null || true

# ── SCI vs BPB graph ────────────────────────────────────────────────────────────
echo ""
echo "━━━ Phase 5: SCI vs BPB Graph ━━━"
echo ""

"${PYTHON}" << 'PYEOF'
import os, sqlite3, json, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

DB       = os.environ.get("DB_PATH")
RUN_DIR  = os.environ.get("RUN_DIR")
REGION   = os.environ.get("REGION", "us_average")
TIMESTAMP = os.environ.get("TIMESTAMP", "")

conn = sqlite3.connect(DB)
rows = conn.execute(
    "SELECT config_json, metrics_json, status, pareto_rank FROM experiments "
    "WHERE status='completed' ORDER BY created_at"
).fetchall()
conn.close()

if not rows:
    print("  ⚠  No completed results — nothing to graph.")
    sys.exit(0)

data = []
for cfg_j, met_j, status, pr in rows:
    c = json.loads(cfg_j)
    m = json.loads(met_j)
    bpb = m.get("val_bpb")
    sci = m.get("sci_per_token")
    if bpb is not None and sci is not None:
        data.append({
            "model": c.get("model_name", ""),
            "quant": c.get("quantization", "none"),
            "batch": c.get("batch_size", 1),
            "seq":   c.get("sequence_length", 512),
            "dtype": c.get("dtype", "float16"),
            "kv":    c.get("use_kv_cache", True),
            "bpb":   bpb,
            "sci":   sci,
            "tps":   m.get("tokens_per_sec"),
            "pareto_rank": pr,
        })

print(f"  Plotting {len(data)} completed experiments")

fig, ax = plt.subplots(figsize=(12, 7.5))
fig.patch.set_facecolor("#0d1117")
ax.set_facecolor("#0d1117")

MODEL_COLORS = {
    "Qwen/Qwen3.5-0.8B": "#58d6ff",
    "Qwen/Qwen3.5-4B":   "#ff7b54",
    "Qwen/Qwen3.5-9B":   "#78ff78",
}
MODEL_SHORT = {
    "Qwen/Qwen3.5-0.8B": "Qwen3.5-0.8B",
    "Qwen/Qwen3.5-4B":   "Qwen3.5-4B",
    "Qwen/Qwen3.5-9B":   "Qwen3.5-9B",
}
DEFAULT_COLOR = "#bb99ff"

legend_models = {}

for i, d in enumerate(data):
    color     = MODEL_COLORS.get(d["model"], DEFAULT_COLOR)
    is_pareto = (d["pareto_rank"] == 0)

    ax.scatter(
        d["bpb"], d["sci"],
        c=color, s=160 if is_pareto else 90,
        alpha=0.92,
        edgecolors="white" if is_pareto else "none",
        linewidths=2 if is_pareto else 0,
        zorder=5 if is_pareto else 4,
    )
    ax.annotate(
        str(i + 1), (d["bpb"], d["sci"]),
        textcoords="offset points", xytext=(6, 4),
        fontsize=8, color="#cccccc", alpha=0.9,
    )
    if d["model"] not in legend_models:
        label = MODEL_SHORT.get(d["model"], d["model"].split("/")[-1])
        legend_models[d["model"]] = ax.scatter(
            [], [], c=color, s=90, label=label
        )

# Pareto frontier line
pareto_pts = [(d["bpb"], d["sci"]) for d in data if d["pareto_rank"] == 0]
if len(pareto_pts) >= 2:
    pareto_pts.sort()
    px, py = zip(*pareto_pts)
    ax.plot(px, py, color="#ffd700", linewidth=1.8, linestyle="--",
            alpha=0.65, zorder=3, label="Pareto frontier")

all_bpb = [d["bpb"] for d in data]
all_sci = [d["sci"] for d in data]

# Quadrant guides
med_bpb = np.median(all_bpb)
med_sci = np.median(all_sci)
ax.axvline(med_bpb, color="#333355", linewidth=0.9, linestyle=":", alpha=0.7)
ax.axhline(med_sci, color="#333355", linewidth=0.9, linestyle=":", alpha=0.7)
ax.text(min(all_bpb)*0.999, min(all_sci)*0.97,
        "← better quality  ·  ↓ greener",
        fontsize=8.5, color="#44cc88", alpha=0.65, va="top")

ax.set_xlabel("Validation BPB (bits/byte)   ← lower is better quality",
              color="#cccccc", fontsize=12, labelpad=8)
ax.set_ylabel("SCI (gCO₂ / token)   ↓ lower is greener",
              color="#cccccc", fontsize=12, labelpad=8)

ydate = f"{TIMESTAMP[:4]}-{TIMESTAMP[4:6]}-{TIMESTAMP[6:8]}" if len(TIMESTAMP) >= 8 else ""
ax.set_title(
    f"Qwen 3.5 Research Run — {len(data)} Iterations\n"
    f"SCI vs. Validation BPB  ·  region: {REGION}  ·  {ydate}",
    color="white", fontsize=13, pad=14,
)

ax.tick_params(colors="#aaaaaa", labelsize=9)
for sp in ax.spines.values():
    sp.set_edgecolor("#333333")
ax.grid(True, color="#1e2a3a", linewidth=0.7, alpha=0.8)

# Legend
from matplotlib.lines import Line2D
handles = list(legend_models.values())
if len(pareto_pts) >= 2:
    handles.append(Line2D([0],[0], color="#ffd700", linestyle="--",
                          linewidth=1.8, label="Pareto frontier"))
handles.append(ax.scatter([],[],c="none",s=120,edgecolors="white",
                           linewidths=2, label="Pareto-optimal ★"))
leg = ax.legend(handles=handles, fontsize=9.5, loc="upper left",
                framealpha=0.35, facecolor="#1a1a2e", edgecolor="#555",
                labelcolor="white")

# Stats box
n_pareto = sum(1 for d in data if d["pareto_rank"] == 0)
stats = (
    f"Iterations : {len(data)}\n"
    f"Pareto pts : {n_pareto}\n"
    f"Best BPB   : {min(all_bpb):.4f} b/B\n"
    f"Best SCI   : {min(all_sci):.3e} gCO₂/tok\n"
    f"Region     : {REGION}"
)
ax.text(0.99, 0.03, stats, transform=ax.transAxes, fontsize=8.5,
        color="#aaaaaa", va="bottom", ha="right",
        bbox=dict(facecolor="#1a1a2e", edgecolor="#555", alpha=0.8,
                  boxstyle="round,pad=0.5"),
        family="monospace")

plt.tight_layout()
out = os.path.join(RUN_DIR, "sci_vs_bpb.png")
fig.savefig(out, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
plt.close()
print(f"  ✅ Graph → {out}")
PYEOF

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📁 Run complete: ${RUN_DIR}/"
echo ""
ls -lh "${RUN_DIR}/"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
