#!/usr/bin/env bash
# run_10min_pip.sh — 10-minute autonomous research run (pip edition)
#
# Self-bootstrapping: creates venv, syncs all deps with pip,
# then runs the workbench with sensor_logger.sh capturing raw hardware data.
#
# Usage:
#   ./run_10min_pip.sh                              # defaults: auto strategy, us_average grid
#   ./run_10min_pip.sh --region eu_france           # French nuclear grid (55 gCO₂/kWh)
#   ./run_10min_pip.sh --strategy random            # force random search
#   ./run_10min_pip.sh --minutes 5                  # shorter run
#   SENSOR_INTERVAL=2 ./run_10min_pip.sh            # 2s sensor sampling
#
# All results land in: runs/run_YYYYMMDD_HHMMSS/
#
# Ctrl+C stops cleanly — partial results are preserved.

set -euo pipefail

# ── Configuration ───────────────────────────────────────────────────────────

MINUTES="${MINUTES:-10}"
TOTAL_SECONDS=$((MINUTES * 60))
TIME_BUDGET_PER_EXPERIMENT="${TIME_BUDGET:-90}"
STRATEGY="${STRATEGY:-auto}"
REGION="${REGION:-us_average}"
SENSOR_INTERVAL="${SENSOR_INTERVAL:-1}"
MAX_ITER="${MAX_ITER:-50}"

export HF_TOKEN="${HF_TOKEN:-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}"

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
      echo "  --strategy S   Search strategy: grid|random|bayesian|auto (default: auto)"
      echo "  --region R     Carbon intensity region (default: us_average)"
      echo "  --budget S     Per-experiment time budget in seconds (default: 90)"
      echo "  --interval S   Sensor sampling interval in seconds (default: 1)"
      echo "  --max-iter N   Max experiment iterations (default: 50)"
      exit 0
      ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# ── Paths ───────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RUN_DIR="${SCRIPT_DIR}/runs/run_${TIMESTAMP}"
DB_PATH="${RUN_DIR}/results.db"
SENSOR_LOG="${RUN_DIR}/sensor_log.csv"
SENSOR_LOGGER="${SCRIPT_DIR}/../../sensor_logger.sh"
WORKBENCH_LOG="${RUN_DIR}/workbench.log"
VENV_DIR="${SCRIPT_DIR}/.venv"

mkdir -p "${RUN_DIR}"

# ── Banner ──────────────────────────────────────────────────────────────────

cat << 'BANNER'

  ╔══════════════════════════════════════════════════════════════╗
  ║  🔬 Auto-Improving LLM Research Workbench (pip edition) ║
  ╚══════════════════════════════════════════════════════════════╝

BANNER

echo "  Total time:        ${MINUTES} minutes (${TOTAL_SECONDS}s)"
echo "  Per-experiment:    ${TIME_BUDGET_PER_EXPERIMENT}s"
echo "  Strategy:          ${STRATEGY}"
echo "  Region:            ${REGION}"
echo "  Sensor interval:   ${SENSOR_INTERVAL}s"
echo "  Results folder:    runs/run_${TIMESTAMP}/"
echo ""
echo "  SCI = (E × I) + M — gCO₂ per token. Lower is greener."
echo ""

# ═══════════════════════════════════════════════════════════════════════════
# PHASE 1: Bootstrap — venv + deps
# ═══════════════════════════════════════════════════════════════════════════

echo "━━━ Phase 1: Environment Setup (pip) ━━━"
echo ""

# 1a. Create venv if missing
if [[ ! -d "${VENV_DIR}" ]]; then
  echo "📦 Creating venv..."
  python3 -m venv "${VENV_DIR}"
  echo "   ✓ venv created at ${VENV_DIR}"
else
  echo "✓ venv exists at ${VENV_DIR}"
fi

PIP="${VENV_DIR}/bin/pip"
PYTHON="${VENV_DIR}/bin/python"

# 1b. Upgrade pip
echo "📦 Upgrading pip..."
"${PIP}" install --upgrade pip --progress-bar=on

# 1c. Install dependencies
echo "📦 Installing dependencies..."
"${PIP}" install click pyyaml optuna rich --progress-bar=on

echo "   ✓ Core deps installed"

# 1d. Install the project
echo "📦 Installing workbench package..."
"${PIP}" install -e "${SCRIPT_DIR}" --progress-bar=on
echo "   ✓ Workbench installed"

# Verify critical imports
echo "📋 Verifying imports..."
VERIFY_SCRIPT='
import sys
checks = {
    "click": "click",
    "optuna": "optuna",
    "rich": "rich",
    "yaml": "yaml",
}
missing = []
for name, module in checks.items():
    try:
        __import__(module)
    except ImportError:
        missing.append(name)

if missing:
    print(f"   ❌ Missing critical deps: {missing}", file=sys.stderr)
    sys.exit(1)

print(f"   ✓ Core deps verified")
'
"${PYTHON}" -c "${VERIFY_SCRIPT}"

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
  "max_iterations": ${MAX_ITER},
  "hostname": "$(hostname)",
  "python_version": "${PYTHON_VERSION}",
  "script_version": "3.0.0"
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

  # Export results
  echo "   📤 Exporting results..."
  cd "${SCRIPT_DIR}"

  if [[ -f "${DB_PATH}" ]]; then
    "${PYTHON}" -c "from workbench.cli import main; main()" -- export --format json --output "${RUN_DIR}/results.json" --db "${DB_PATH}" 2>/dev/null || true
    "${PYTHON}" -c "from workbench.cli import main; main()" -- export --format csv  --output "${RUN_DIR}/results.csv"  --db "${DB_PATH}" 2>/dev/null || true
    echo "   ✓ Exported to JSON + CSV"

    echo ""
    "${PYTHON}" -c "from workbench.cli import main; main()" -- status --db "${DB_PATH}" 2>/dev/null || true
    echo ""
    "${PYTHON}" -c "from workbench.cli import main; main()" -- results --db "${DB_PATH}" --pareto 2>/dev/null || true
  else
    echo "   ⚠ No database found (run may have been too short)"
  fi

  # Sensor log stats
  if [[ -f "${SENSOR_LOG}" ]]; then
    SENSOR_LINES=$(wc -l < "${SENSOR_LOG}")
    echo ""
    echo "   🌡️  Sensor log: ${SENSOR_LINES} samples captured"
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
echo "━━━ Phase 2: Sensor Logger ━━━"
echo ""

if [[ -x "${SENSOR_LOGGER}" ]]; then
  echo "🌡️  Starting sensor logger (${SENSOR_INTERVAL}s interval)..."
  bash "${SENSOR_LOGGER}" -i "${SENSOR_INTERVAL}" -o "${SENSOR_LOG}" &
  SENSOR_PID=$!
  echo "   ✓ Sensor logger PID: ${SENSOR_PID}"
elif command -v nvidia-smi &>/dev/null; then
  echo "🌡️  sensor_logger.sh not found, falling back to nvidia-smi logging..."
  (
    echo "timestamp,gpu_temp_c,gpu_power_w,gpu_util_pct,gpu_clock_mhz" > "${SENSOR_LOG}"
    while true; do
      ts=$(date -Iseconds)
      reading=$(nvidia-smi --query-gpu=temperature.gpu,power.draw,utilization.gpu,clocks.gr \
        --format=csv,noheader,nounits 2>/dev/null || echo ",,,")
      echo "${ts},${reading}" >> "${SENSOR_LOG}"
      sleep "${SENSOR_INTERVAL}"
    done
  ) &
  SENSOR_PID=$!
  echo "   ✓ Fallback sensor logger PID: ${SENSOR_PID}"
else
  echo "⚠️  No sensor logging available (no sensor_logger.sh or nvidia-smi)"
fi

# ═══════════════════════════════════════════════════════════════════════════
# PHASE 5: Run the workbench
# ═══════════════════════════════════════════════════════════════════════════

echo ""
echo "━━━ Phase 3: Research Loop (${MINUTES} minutes) ━━━"
echo ""
echo "🚀 Starting workbench (${MINUTES} minutes, ${STRATEGY} strategy)..."
echo ""
echo "   📁 Results folder:  ${RUN_DIR}/"
echo "   🗄️  Database:        ${DB_PATH}"
echo "   📝 Workbench log:   ${WORKBENCH_LOG}"
echo "   🌡️  Sensor log:      ${SENSOR_LOG}"
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
