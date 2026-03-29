#!/usr/bin/env bash
# run_30s_v7.sh — Infinite experiment runner with incremental saves
#
# Self-bootstrapping: creates venv, installs deps with pip, then runs
# parallel_runner_v7.py which maintains a pool of N workers forever.
#
# New in v7 (vs v6):
#   - INFINITE: no experiment limit — runs until Ctrl+C
#   - INCREMENTAL SAVES: each result saved to DB the instant it completes
#   - WORKER POOL: maintains N active workers at all times (slot recycling)
#   - OVERNIGHT-SAFE: Ctrl+C drains in-flight workers, saves everything
#   - All v6 features preserved: Rich TUI, GPU stats, 67-channel sensors
#
# Usage:
#   ./run_30s_v7.sh                                # 10 workers, 30s each, forever
#   ./run_30s_v7.sh --workers 5 --budget 60        # 5 workers, 60s each, forever
#   ./run_30s_v7.sh --region eu_france             # French grid
#
# All results land in: runs/run_YYYYMMDD_HHMMSS/
# Ctrl+C stops cleanly — ALL completed results are already saved.

set -euo pipefail

# ── Configuration ───────────────────────────────────────────────────────

NUM_WORKERS="${NUM_WORKERS:-10}"
TIME_BUDGET_PER_EXPERIMENT="${TIME_BUDGET:-30}"
REGION="${REGION:-us_average}"
SENSOR_INTERVAL="${SENSOR_INTERVAL:-1}"
HF_TOKEN="${HF_TOKEN:-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}"
SEED="${SEED:-}"

# Parse CLI overrides
while [[ $# -gt 0 ]]; do
  case "$1" in
    --workers)   NUM_WORKERS="$2"; shift 2 ;;
    --budget)    TIME_BUDGET_PER_EXPERIMENT="$2"; shift 2 ;;
    --region)    REGION="$2"; shift 2 ;;
    --interval)  SENSOR_INTERVAL="$2"; shift 2 ;;
    --seed)      SEED="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --workers N    Number of concurrent experiments (default: 10)"
      echo "  --budget S     Per-experiment time budget in seconds (default: 30)"
      echo "  --region R     Carbon intensity region (default: us_average)"
      echo "  --interval S   Sensor sampling interval in seconds (default: 1)"
      echo "  --seed N       Random seed for first batch (default: random)"
      exit 0
      ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# ── HuggingFace Token ─────────────────────────────────────────────────

export HF_TOKEN="${HF_TOKEN:-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}"

# ── Paths ───────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RUN_DIR="${SCRIPT_DIR}/runs/run_${TIMESTAMP}"
DB_PATH="${RUN_DIR}/results.db"
SENSOR_LOG="${RUN_DIR}/sensor_log.csv"
SENSOR_LOGGER="${SCRIPT_DIR}/sensor_logger_v4.sh"
WORKBENCH_LOG="${RUN_DIR}/workbench.log"
VENV_DIR="${SCRIPT_DIR}/.venv"

mkdir -p "${RUN_DIR}"

# ── Banner ──────────────────────────────────────────────────────────────

cat << 'BANNER'

  ╔══════════════════════════════════════════════════════════════╗
  ║  🔬 Auto-Improving LLM Research Workbench v7               ║
  ║     ∞ experiments · incremental saves · overnight mode      ║
  ║     67-channel sensor logging · worker pool · single term   ║
  ╚══════════════════════════════════════════════════════════════╝

BANNER

echo "  Concurrent workers:     ${NUM_WORKERS}"
echo "  Per-experiment budget:  ${TIME_BUDGET_PER_EXPERIMENT}s"
echo "  Experiment limit:       ∞ (runs until Ctrl+C)"
echo "  Save mode:              incremental (after every experiment)"
echo "  Region:                 ${REGION}"
echo "  Sensor interval:        ${SENSOR_INTERVAL}s"
echo "  Sensor channels:        67 (v4)"
echo "  Live dashboard:         enabled (Rich TUI)"
echo "  Results folder:         runs/run_${TIMESTAMP}/"
echo ""
echo "  SCI = (E × I) + M — gCO₂ per token. Lower is greener."
echo ""

# ═══════════════════════════════════════════════════════════════════════════
# PHASE 1: Bootstrap — venv + deps
# ═══════════════════════════════════════════════════════════════════════════

echo "━━━ Phase 1: Environment Setup ━━━"
echo ""

if [[ ! -d "${VENV_DIR}" ]]; then
  echo "📦 Creating venv..."
  python3 -m venv "${VENV_DIR}"
  echo "   ✓ venv created at ${VENV_DIR}"
else
  echo "✓ venv exists at ${VENV_DIR}"
fi

PIP="${VENV_DIR}/bin/pip"
PYTHON="${VENV_DIR}/bin/python"

echo "📦 Installing dependencies..."
"${PIP}" install --upgrade pip --quiet
"${PIP}" install click pyyaml optuna rich --quiet
"${PIP}" install -e "${SCRIPT_DIR}" --quiet
echo "   ✓ Dependencies installed"

# Quick sanity check
"${PYTHON}" -c "
import click, optuna, rich, yaml
print('   ✓ Core deps verified')
try:
    import torch
    if torch.cuda.is_available():
        print(f'   🎮 GPU: {torch.cuda.get_device_name(0)}')
except ImportError:
    pass
"

echo ""

# ═══════════════════════════════════════════════════════════════════════════
# PHASE 2: Write run metadata
# ═══════════════════════════════════════════════════════════════════════════

PYTHON_VERSION=$("${PYTHON}" --version 2>&1)

cat > "${RUN_DIR}/run_config.json" << METADATA
{
  "timestamp": "${TIMESTAMP}",
  "num_workers": ${NUM_WORKERS},
  "experiment_limit": "infinite",
  "time_budget_per_experiment_sec": ${TIME_BUDGET_PER_EXPERIMENT},
  "execution_mode": "infinite_pool",
  "save_mode": "incremental",
  "region": "${REGION}",
  "sensor_interval_sec": ${SENSOR_INTERVAL},
  "sensor_channels": 67,
  "seed": ${SEED:-null},
  "hostname": "$(hostname)",
  "python_version": "${PYTHON_VERSION}",
  "script_version": "7.0.0"
}
METADATA

echo "📁 Run directory: ${RUN_DIR}"

# ═══════════════════════════════════════════════════════════════════════════
# PHASE 3: Cleanup handler
# ═══════════════════════════════════════════════════════════════════════════

SENSOR_PID=""
GIT_PUSH_PID=""

cleanup() {
  echo ""
  echo "🛑 Shutting down..."

  # Stop git auto-push
  if [[ -n "${GIT_PUSH_PID}" ]] && kill -0 "${GIT_PUSH_PID}" 2>/dev/null; then
    kill "${GIT_PUSH_PID}" 2>/dev/null || true
    wait "${GIT_PUSH_PID}" 2>/dev/null || true
    echo "   ✓ Git auto-push stopped"
  fi

  # Stop sensor logger
  if [[ -n "${SENSOR_PID}" ]] && kill -0 "${SENSOR_PID}" 2>/dev/null; then
    kill "${SENSOR_PID}" 2>/dev/null || true
    wait "${SENSOR_PID}" 2>/dev/null || true
    echo "   ✓ Sensor logger stopped"
  fi

  cd "${SCRIPT_DIR}"

  # v7: Results are already saved incrementally! Just check what we got.
  local HAS_RESULTS=false
  if [[ -f "${DB_PATH}" ]]; then
    local COUNT
    COUNT=$(sqlite3 "${DB_PATH}" "SELECT COUNT(*) FROM experiments WHERE status='completed'" 2>/dev/null || echo "0")
    COUNT="${COUNT:-0}"
    if [[ "${COUNT}" -gt 0 ]]; then
      HAS_RESULTS=true
    fi
  fi

  if ${HAS_RESULTS}; then
    echo "   📊 ${COUNT} experiments already saved (incremental mode)"
    echo "   📤 Exporting to JSON + CSV..."
    "${PYTHON}" -c "from workbench.cli import main; main()" -- export \
      --format json --output "${RUN_DIR}/results.json" --db "${DB_PATH}" 2>/dev/null || true
    "${PYTHON}" -c "from workbench.cli import main; main()" -- export \
      --format csv  --output "${RUN_DIR}/results.csv"  --db "${DB_PATH}" 2>/dev/null || true
    echo "   ✓ Exported to JSON + CSV"
    echo ""
    "${PYTHON}" -c "from workbench.cli import main; main()" -- status \
      --db "${DB_PATH}" 2>/dev/null || true
    echo ""
    "${PYTHON}" -c "from workbench.cli import main; main()" -- results \
      --db "${DB_PATH}" --pareto 2>/dev/null || true
  elif [[ -f "${DB_PATH}" ]]; then
    echo "   ⚠ No completed experiments (interrupted before any finished)"
  else
    echo "   ⚠ No database created (run was too short)"
  fi

  # Sensor log stats
  if [[ -f "${SENSOR_LOG}" ]]; then
    SENSOR_LINES=$(wc -l < "${SENSOR_LOG}")
    SENSOR_COLS=$(head -1 "${SENSOR_LOG}" | tr ',' '\n' | wc -l)
    echo ""
    echo "   🌡️  Sensor log: ${SENSOR_LINES} samples × ${SENSOR_COLS} channels"
  fi

  # Cross-run analysis
  if [[ -f "${SCRIPT_DIR}/analyze_all.py" ]]; then
    echo ""
    echo "━━━ Cross-Run Analysis (all runs) ━━━"
    "${PYTHON}" "${SCRIPT_DIR}/analyze_all.py" --region "${REGION}" 2>/dev/null || true
  elif [[ -f "${SCRIPT_DIR}/analyze_runs.py" ]]; then
    echo ""
    echo "━━━ Cross-Run Analysis ━━━"
    "${PYTHON}" "${SCRIPT_DIR}/analyze_runs.py" --region "${REGION}" 2>/dev/null || true
  fi

  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "📁 All results in: ${RUN_DIR}/"
  echo ""
  ls -lh "${RUN_DIR}/" 2>/dev/null || true
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  # Final git push on exit
  echo ""
  echo "📤 Final git push..."
  cd "${SCRIPT_DIR}"
  git add -A 2>/dev/null || true
  if ! git diff --cached --quiet 2>/dev/null; then
    git commit -m "auto-save (shutdown): $(date +%Y-%m-%d\ %H:%M:%S)" 2>/dev/null || true
    git pull --rebase 2>/dev/null || true
    git push 2>/dev/null && echo "   ✓ Final push complete" || echo "   ⚠ Final push failed"
  else
    echo "   ✓ Nothing new to push"
  fi
}

trap cleanup EXIT INT TERM

# ═══════════════════════════════════════════════════════════════════════════
# PHASE 4: Start sensor logger (background)
# ═══════════════════════════════════════════════════════════════════════════

echo ""
echo "━━━ Phase 2: Sensor Logger (v4, 67 channels) ━━━"
echo ""

if [[ -x "${SENSOR_LOGGER}" ]]; then
  echo "🌡️  Starting sensor_logger_v4.sh (${SENSOR_INTERVAL}s interval)..."
  bash "${SENSOR_LOGGER}" -i "${SENSOR_INTERVAL}" -o "${SENSOR_LOG}" &
  SENSOR_PID=$!
  echo "   ✓ Sensor logger PID: ${SENSOR_PID}"
elif command -v nvidia-smi &>/dev/null; then
  echo "⚠️  sensor_logger_v4.sh not found — falling back to basic nvidia-smi logging"
  (
    echo "timestamp,gpu_temp_c,gpu_power_w,gpu_power_instant_w,gpu_util_pct,gpu_pstate" \
      > "${SENSOR_LOG}"
    while true; do
      ts=$(date -Iseconds)
      reading=$(nvidia-smi \
        --query-gpu=temperature.gpu,power.draw,power.draw.instant,utilization.gpu,pstate \
        --format=csv,noheader,nounits 2>/dev/null | sed 's/ //g' || echo ",,,,")
      echo "${ts},${reading}" >> "${SENSOR_LOG}"
      sleep "${SENSOR_INTERVAL}"
    done
  ) &
  SENSOR_PID=$!
  echo "   ✓ Fallback sensor logger PID: ${SENSOR_PID}"
else
  echo "⚠️  No sensor logging available (no nvidia-smi)"
fi

# ═══════════════════════════════════════════════════════════════════════════
# PHASE 4.5: Git auto-push (every 5 minutes)
# ═══════════════════════════════════════════════════════════════════════════

echo ""
echo "━━━ Phase 2.5: Git Auto-Push (every 5 min) ━━━"
echo ""

(
  while true; do
    sleep 300
    cd "${SCRIPT_DIR}"
    git add -A 2>/dev/null || continue
    git diff --cached --quiet 2>/dev/null && continue  # nothing staged → skip
    git commit -m "auto-save: $(date +%Y-%m-%d\ %H:%M:%S)" 2>/dev/null || continue
    git pull --rebase 2>/dev/null || true
    git push 2>/dev/null || true
  done
) &
GIT_PUSH_PID=$!
echo "   ✓ Git auto-push PID: ${GIT_PUSH_PID} (every 300s)"

# ═══════════════════════════════════════════════════════════════════════════
# PHASE 5: Run infinite experiment pool
# ═══════════════════════════════════════════════════════════════════════════

echo ""
echo "━━━ Phase 3: Infinite Research Loop (${NUM_WORKERS} concurrent workers) ━━━"
echo ""
echo "🚀 Starting infinite runner..."
echo "   📁 Results:    ${RUN_DIR}/"
echo "   🗄️  Database:   ${DB_PATH}"
echo "   💾 Save mode:  incremental (after every experiment)"
echo "   📝 Log:        ${WORKBENCH_LOG}"
echo "   🌡️  Sensors:    ${SENSOR_LOG}"
echo "   ♾️  Mode:       infinite (Ctrl+C to stop)"
echo ""

cd "${SCRIPT_DIR}"

# Build the seed flag (only if SEED is set)
SEED_FLAG=""
if [[ -n "${SEED}" ]]; then
  SEED_FLAG="--seed ${SEED}"
fi

# NOTE: parallel_runner_v7.py uses multiprocessing with 'spawn' start method.
# Each worker gets its own CUDA context + model cache.
# Do NOT pipe through | tee — that kills Rich Live TUI!
"${PYTHON}" "${SCRIPT_DIR}/parallel_runner_v7.py" \
  --db "${DB_PATH}" \
  --workers "${NUM_WORKERS}" \
  --time-budget "${TIME_BUDGET_PER_EXPERIMENT}" \
  --region "${REGION}" \
  --log-file "${WORKBENCH_LOG}" \
  ${SEED_FLAG}

echo ""
echo "✅ Infinite runner stopped."
