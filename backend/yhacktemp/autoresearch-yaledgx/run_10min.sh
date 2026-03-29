#!/usr/bin/env bash
# run_10min.sh — 10-minute autonomous research run with full sensor logging
#
# Creates a timestamped results folder, runs the workbench for 10 minutes
# with sensor_logger.sh capturing raw hardware data alongside, then
# exports everything (JSON, CSV, SQLite, sensor logs) into the folder.
#
# Usage:
#   ./run_10min.sh                              # defaults: auto strategy, us_average grid
#   ./run_10min.sh --region eu_france           # French nuclear grid (55 gCO₂/kWh)
#   ./run_10min.sh --strategy random            # force random search
#   ./run_10min.sh --minutes 5                  # shorter run
#   SENSOR_INTERVAL=2 ./run_10min.sh            # 2s sensor sampling
#
# All results land in: runs/run_YYYYMMDD_HHMMSS/
#
# Ctrl+C stops cleanly — partial results are preserved.

set -euo pipefail

# ── Configuration ───────────────────────────────────────────────────────────

MINUTES="${MINUTES:-10}"
TOTAL_SECONDS=$((MINUTES * 60))
TIME_BUDGET_PER_EXPERIMENT="${TIME_BUDGET:-90}"     # seconds per experiment
STRATEGY="${STRATEGY:-auto}"
REGION="${REGION:-us_average}"
SENSOR_INTERVAL="${SENSOR_INTERVAL:-1}"             # sensor sampling interval (seconds)
MAX_ITER="${MAX_ITER:-50}"                           # cap iterations (time is the real limit)

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
      echo "Usage: $0 [--minutes N] [--strategy S] [--region R] [--budget S] [--interval S]"
      echo ""
      echo "  --minutes   Total run time (default: 10)"
      echo "  --strategy  Search strategy: grid|random|bayesian|auto (default: auto)"
      echo "  --region    Carbon intensity region (default: us_average)"
      echo "  --budget    Per-experiment time budget in seconds (default: 90)"
      echo "  --interval  Sensor sampling interval in seconds (default: 1)"
      echo "  --max-iter  Max experiment iterations (default: 50)"
      exit 0
      ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# ── Setup ───────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

export HF_TOKEN="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"

# Install workbench package if not already installed
VENV_DIR="${SCRIPT_DIR}/.venv"
if ! command -v workbench &>/dev/null; then
  echo "📦 Installing workbench package..."
  if [[ ! -d "${VENV_DIR}" ]]; then
    python3 -m venv "${VENV_DIR}"
  fi
  "${VENV_DIR}/bin/pip" install -e "${SCRIPT_DIR}" --progress-bar on
fi
export PATH="${VENV_DIR}/bin:$PATH"
RUN_DIR="${SCRIPT_DIR}/runs/run_${TIMESTAMP}"
DB_PATH="${RUN_DIR}/results.db"
SENSOR_LOG="${RUN_DIR}/sensor_log.csv"
SENSOR_LOGGER="${SCRIPT_DIR}/../../sensor_logger.sh"
WORKBENCH_LOG="${RUN_DIR}/workbench.log"

mkdir -p "${RUN_DIR}"

# ── Banner ──────────────────────────────────────────────────────────────────

cat << EOF

  ╔══════════════════════════════════════════════════════════════╗
  ║  🔬 Auto-Improving LLM Research Workbench — ${MINUTES}-Minute Run  ║
  ╠══════════════════════════════════════════════════════════════╣
  ║                                                              ║
  ║  Total time:        ${MINUTES} minutes (${TOTAL_SECONDS}s)$(printf '%*s' $((26 - ${#TOTAL_SECONDS} - ${#MINUTES})) '')║
  ║  Per-experiment:    ${TIME_BUDGET_PER_EXPERIMENT}s$(printf '%*s' $((38 - ${#TIME_BUDGET_PER_EXPERIMENT})) '')║
  ║  Strategy:          ${STRATEGY}$(printf '%*s' $((38 - ${#STRATEGY})) '')║
  ║  Region:            ${REGION}$(printf '%*s' $((38 - ${#REGION})) '')║
  ║  Sensor interval:   ${SENSOR_INTERVAL}s$(printf '%*s' $((38 - ${#SENSOR_INTERVAL})) '')║
  ║                                                              ║
  ║  Results folder:    runs/run_${TIMESTAMP}/          ║
  ║                                                              ║
  ║  SCI = (E × I) + M — gCO₂ per token. Lower is greener.     ║
  ║                                                              ║
  ╚══════════════════════════════════════════════════════════════╝

EOF

# ── Write run metadata ──────────────────────────────────────────────────────

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
  "script_version": "1.0.0"
}
METADATA

echo "📁 Run directory: ${RUN_DIR}"

# ── Cleanup function ───────────────────────────────────────────────────────

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
    # JSON export
    workbench export --format json --output "${RUN_DIR}/results.json" --db "${DB_PATH}" 2>/dev/null || true
    # CSV export
    workbench export --format csv --output "${RUN_DIR}/results.csv" --db "${DB_PATH}" 2>/dev/null || true
    echo "   ✓ Exported to JSON + CSV"

    # Final status
    workbench status --db "${DB_PATH}" 2>/dev/null || true
    workbench results --db "${DB_PATH}" --pareto 2>/dev/null || true
  else
    echo "   ⚠ No database found (run may have been too short)"
  fi

  # Summary of output files
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "📁 All results in: ${RUN_DIR}/"
  echo ""
  ls -lh "${RUN_DIR}/" 2>/dev/null || true
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

trap cleanup EXIT INT TERM

# ── Start sensor logger (background) ───────────────────────────────────────

if [[ -x "${SENSOR_LOGGER}" ]]; then
  echo "🌡️  Starting sensor logger (${SENSOR_INTERVAL}s interval)..."
  bash "${SENSOR_LOGGER}" -i "${SENSOR_INTERVAL}" -o "${SENSOR_LOG}" &
  SENSOR_PID=$!
  echo "   ✓ Sensor logger PID: ${SENSOR_PID}"
elif command -v nvidia-smi &>/dev/null; then
  # Fallback: lightweight nvidia-smi logging if sensor_logger.sh not found
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

echo ""

# ── Run the workbench ───────────────────────────────────────────────────────

echo "🚀 Starting workbench (${MINUTES} minutes, ${STRATEGY} strategy)..."
echo "   Logging to: ${WORKBENCH_LOG}"
echo ""

cd "${SCRIPT_DIR}"

# Run workbench with total time limit, pipe output to both terminal and log
workbench run \
  --strategy "${STRATEGY}" \
  --max-iter "${MAX_ITER}" \
  --db "${DB_PATH}" \
  --region "${REGION}" \
  --total-time "${TOTAL_SECONDS}" \
  --time-budget "${TIME_BUDGET_PER_EXPERIMENT}" \
  2>&1 | tee "${WORKBENCH_LOG}"

echo ""
echo "✅ Workbench run complete!"
