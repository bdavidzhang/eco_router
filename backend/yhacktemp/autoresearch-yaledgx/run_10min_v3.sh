#!/usr/bin/env bash
# run_10min_v3.sh — Autonomous research run with 45-channel sensor logging
#
# Self-bootstrapping: creates venv, installs deps with pip, then runs the
# workbench with sensor_logger_v3.sh (45 channels) capturing hardware data.
#
# New in v3:
#   - 45 sensor channels (was 25): GPU throttle reasons, PSI pressure,
#     NVMe IO counters, VM page faults, memory controller util, SM clocks,
#     instantaneous power, pstate, thermal alarm
#   - sensor_logger_v3.sh lives in-repo (no fragile ../../ path)
#   - Runs analyze_runs.py on exit for cross-run Pareto analysis
#
# Usage:
#   ./run_10min_v3.sh                              # defaults
#   ./run_10min_v3.sh --minutes 5 --budget 30      # quick run
#   ./run_10min_v3.sh --region eu_france            # French nuclear grid
#   SENSOR_INTERVAL=2 ./run_10min_v3.sh             # 2s sensor sampling
#
# All results land in: runs/run_YYYYMMDD_HHMMSS/
# Ctrl+C stops cleanly — partial results are preserved.

set -euo pipefail

# ── Configuration ───────────────────────────────────────────────────────────

MINUTES="${MINUTES:-10}"
TOTAL_SECONDS=$((MINUTES * 60))
TIME_BUDGET_PER_EXPERIMENT="${TIME_BUDGET:-30}"
STRATEGY="${STRATEGY:-auto}"
REGION="${REGION:-us_average}"
SENSOR_INTERVAL="${SENSOR_INTERVAL:-1}"
HF_TOKEN="${HF_TOKEN:-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}"
MAX_ITER="${MAX_ITER:-50}"

# Parse CLI overrides
while [[ $# -gt 0 ]]; do
  case "$1" in
    --minutes)   MINUTES="$2"; TOTAL_SECONDS=$((MINUTES * 60)); shift 2 ;;
    --strategy)  STRATEGY="$2"; shift 2 ;;
    --region)    REGION="$2"; shift 2 ;;
    --budget)    TIME_BUDGET_PER_EXPERIMENT="$2"; shift 2 ;;
    --interval)  SENSOR_INTERVAL="$2"; shift 2 ;;
    --max-iter)  MAX_ITER="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --minutes N    Total run time (default: 10)"
      echo "  --strategy S   grid|random|bayesian|auto (default: auto)"
      echo "  --region R     Carbon intensity region (default: us_average)"
      echo "  --budget S     Per-experiment time budget in seconds (default: 30)"
      echo "  --interval S   Sensor sampling interval in seconds (default: 1)"
      echo "  --max-iter N   Max experiment iterations (default: 50)"
      exit 0
      ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# ── HuggingFace Token ─────────────────────────────────────────────────────

export HF_TOKEN="${HF_TOKEN:-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}"

# ── Paths ───────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RUN_DIR="${SCRIPT_DIR}/runs/run_${TIMESTAMP}"
DB_PATH="${RUN_DIR}/results.db"
SENSOR_LOG="${RUN_DIR}/sensor_log.csv"
SENSOR_LOGGER="${SCRIPT_DIR}/sensor_logger_v3.sh"
WORKBENCH_LOG="${RUN_DIR}/workbench.log"
VENV_DIR="${SCRIPT_DIR}/.venv"

mkdir -p "${RUN_DIR}"

# ── Banner ──────────────────────────────────────────────────────────────────

cat << 'BANNER'

  ╔══════════════════════════════════════════════════════════════╗
  ║  🔬 Auto-Improving LLM Research Workbench v3               ║
  ║     45-channel sensor logging · SCI optimization            ║
  ╚══════════════════════════════════════════════════════════════╝

BANNER

echo "  Total time:        ${MINUTES} minutes (${TOTAL_SECONDS}s)"
echo "  Per-experiment:    ${TIME_BUDGET_PER_EXPERIMENT}s"
echo "  Strategy:          ${STRATEGY}"
echo "  Region:            ${REGION}"
echo "  Sensor interval:   ${SENSOR_INTERVAL}s"
echo "  Sensor channels:   45 (v3)"
echo "  Results folder:    runs/run_${TIMESTAMP}/"
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
  "total_time_sec": ${TOTAL_SECONDS},
  "time_budget_per_experiment_sec": ${TIME_BUDGET_PER_EXPERIMENT},
  "strategy": "${STRATEGY}",
  "region": "${REGION}",
  "sensor_interval_sec": ${SENSOR_INTERVAL},
  "sensor_channels": 45,
  "max_iterations": ${MAX_ITER},
  "hostname": "$(hostname)",
  "python_version": "${PYTHON_VERSION}",
  "script_version": "4.0.0"
}
METADATA

echo "📁 Run directory: ${RUN_DIR}"

# ═══════════════════════════════════════════════════════════════════════════
# PHASE 3: Cleanup handler
# ═══════════════════════════════════════════════════════════════════════════

SENSOR_PID=""

cleanup() {
  echo ""
  echo "🛑 Shutting down..."

  # Stop sensor logger
  if [[ -n "${SENSOR_PID}" ]] && kill -0 "${SENSOR_PID}" 2>/dev/null; then
    kill "${SENSOR_PID}" 2>/dev/null || true
    wait "${SENSOR_PID}" 2>/dev/null || true
    echo "   ✓ Sensor logger stopped"
  fi

  cd "${SCRIPT_DIR}"

  # Check if this run's DB actually has completed experiments
  local HAS_RESULTS=false
  if [[ -f "${DB_PATH}" ]]; then
    local COUNT
    COUNT=$(sqlite3 "${DB_PATH}" "SELECT COUNT(*) FROM experiments WHERE status='completed'" 2>/dev/null || echo "0")
    COUNT="${COUNT:-0}"
    if [[ "${COUNT}" -gt 0 ]]; then
      HAS_RESULTS=true
    fi
  fi

  # Export + show status only if this run produced results
  if ${HAS_RESULTS}; then
    echo "   📤 Exporting ${COUNT} results..."
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
    echo "   ⚠ No completed experiments in this run (interrupted before finishing)"
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

  # Cross-run analysis (always — shows aggregate across ALL previous runs)
  if [[ -f "${SCRIPT_DIR}/analyze_all.py" ]]; then
    echo ""
    echo "━━━ Cross-Run Analysis (all runs) ━━━"
    "${PYTHON}" "${SCRIPT_DIR}/analyze_all.py" --region "${REGION}" 2>/dev/null || true
  elif [[ -f "${SCRIPT_DIR}/analyze_runs.py" ]]; then
    echo ""
    echo "━━━ Cross-Run Analysis ━━━"
    "${PYTHON}" "${SCRIPT_DIR}/analyze_runs.py" --region "${REGION}" 2>/dev/null || true
  fi

  # Summary of output files
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "📁 All results in: ${RUN_DIR}/"
  echo ""
  ls -lh "${RUN_DIR}/" 2>/dev/null || true
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

trap cleanup EXIT INT TERM

# ═══════════════════════════════════════════════════════════════════════════
# PHASE 4: Start sensor logger (background)
# ═══════════════════════════════════════════════════════════════════════════

echo ""
echo "━━━ Phase 2: Sensor Logger (v3, 45 channels) ━━━"
echo ""

if [[ -x "${SENSOR_LOGGER}" ]]; then
  echo "🌡️  Starting sensor_logger_v3.sh (${SENSOR_INTERVAL}s interval)..."
  bash "${SENSOR_LOGGER}" -i "${SENSOR_INTERVAL}" -o "${SENSOR_LOG}" &
  SENSOR_PID=$!
  echo "   ✓ Sensor logger PID: ${SENSOR_PID}"
elif command -v nvidia-smi &>/dev/null; then
  echo "⚠️  sensor_logger_v3.sh not found — falling back to basic nvidia-smi logging"
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
# PHASE 5: Run the workbench
# ═══════════════════════════════════════════════════════════════════════════

echo ""
echo "━━━ Phase 3: Research Loop (${MINUTES} minutes) ━━━"
echo ""
echo "🚀 Starting workbench..."
echo "   📁 Results:    ${RUN_DIR}/"
echo "   🗄️  Database:   ${DB_PATH}"
echo "   📝 Log:        ${WORKBENCH_LOG}"
echo "   🌡️  Sensors:    ${SENSOR_LOG}"
echo ""

cd "${SCRIPT_DIR}"

"${PYTHON}" -c "from workbench.cli import main; main()" -- run \
  --strategy "${STRATEGY}" \
  --max-iter "${MAX_ITER}" \
  --db "${DB_PATH}" \
  --region "${REGION}" \
  --total-time "${TOTAL_SECONDS}" \
  --time-budget "${TIME_BUDGET_PER_EXPERIMENT}" \
  2>&1 | tee "${WORKBENCH_LOG}"

echo ""
echo "✅ Workbench run complete!"
