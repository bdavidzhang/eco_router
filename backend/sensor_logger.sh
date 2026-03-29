#!/usr/bin/env bash
# sensor_logger.sh — Continuous time-series sensor logger for NVIDIA DGX Spark
#
# Logs all available sensor readings to CSV at a configurable interval.
# Designed for the YHACK sustainability track: measure power/thermal impact
# of workloads over time.
#
# Usage:
#   ./sensor_logger.sh                          # 1s interval, auto-named CSV
#   ./sensor_logger.sh -i 5                     # 5s interval
#   ./sensor_logger.sh -o my_experiment.csv      # custom output file
#   ./sensor_logger.sh -i 2 -o run1.csv         # both
#   ./sensor_logger.sh --jsonl -o run1.jsonl     # JSONL format instead of CSV
#
# Stop with Ctrl+C. The file is flushed after every row so partial runs are safe.

set -euo pipefail

# ── Defaults ────────────────────────────────────────────────────────────────

INTERVAL=1
OUTPUT=""
FORMAT="csv"

# ── Parse args ──────────────────────────────────────────────────────────────

usage() {
  echo "Usage: $0 [-i SECONDS] [-o OUTPUT_FILE] [--jsonl]"
  echo "  -i    Sampling interval in seconds (default: 1)"
  echo "  -o    Output file path (default: sensor_log_<timestamp>.csv)"
  echo "  --jsonl  Output in JSON Lines format instead of CSV"
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -i) INTERVAL="$2"; shift 2 ;;
    -o) OUTPUT="$2"; shift 2 ;;
    --jsonl) FORMAT="jsonl"; shift ;;
    -h|--help) usage ;;
    *) echo "Unknown option: $1"; usage ;;
  esac
done

if [[ -z "$OUTPUT" ]]; then
  OUTPUT="sensor_log_$(date +%Y%m%d_%H%M%S).${FORMAT}"
fi

# ── Helpers ─────────────────────────────────────────────────────────────────

# Read a sysfs file, return fallback on failure
sysfs_read() {
  cat "$1" 2>/dev/null || echo "$2"
}

# millidegrees → degrees with 1 decimal
mdeg_to_c() {
  local raw="${1:-0}"
  if [[ "$raw" == "0" || -z "$raw" ]]; then
    echo "0.0"
  else
    echo "scale=1; ${raw} / 1000" | bc 2>/dev/null || echo "0.0"
  fi
}

# ── Detect available sensors once at startup ────────────────────────────────

HAS_NVIDIA_SMI=false
if command -v nvidia-smi &>/dev/null; then
  HAS_NVIDIA_SMI=true
fi

# Count thermal zones
THERMAL_ZONES=()
for tz in /sys/class/thermal/thermal_zone*/; do
  [[ -d "$tz" ]] && THERMAL_ZONES+=("$tz")
done

# Detect hwmon devices by type
HWMON_FAN=""
HWMON_NVME=""
HWMON_NICS=()
HWMON_WIFI=""

for hwmon in /sys/class/hwmon/hwmon*/; do
  [[ -d "$hwmon" ]] || continue
  name=$(cat "${hwmon}/name" 2>/dev/null || echo "")
  case "$name" in
    acpi_fan)   HWMON_FAN="$hwmon" ;;
    nvme)       HWMON_NVME="$hwmon" ;;
    mlx5)       HWMON_NICS+=("$hwmon") ;;
    mt7925_phy*) HWMON_WIFI="$hwmon" ;;
  esac
done

# Count CPU cores
CPU_DIRS=()
for cpu in /sys/devices/system/cpu/cpu[0-9]*/cpufreq; do
  [[ -d "$cpu" ]] && CPU_DIRS+=("$cpu")
done

# ── Build CSV header ───────────────────────────────────────────────────────

build_header() {
  local cols=("timestamp")

  # GPU columns
  if $HAS_NVIDIA_SMI; then
    cols+=("gpu_temp_c" "gpu_power_w" "gpu_util_pct" "gpu_clock_mhz" "gpu_vid_clock_mhz")
  fi

  # Thermal zones
  for i in "${!THERMAL_ZONES[@]}"; do
    cols+=("thermal_zone${i}_c")
  done

  # Fan
  if [[ -n "$HWMON_FAN" ]]; then
    cols+=("fan_rpm" "fan_power_uw")
  fi

  # NVMe
  if [[ -n "$HWMON_NVME" ]]; then
    cols+=("nvme_temp_c")
  fi

  # NICs
  for i in "${!HWMON_NICS[@]}"; do
    cols+=("nic${i}_temp_c")
  done

  # WiFi
  if [[ -n "$HWMON_WIFI" ]]; then
    cols+=("wifi_temp_c")
  fi

  # CPU — average freq per cluster instead of 20 individual columns
  cols+=("cpu_big_avg_mhz" "cpu_little_avg_mhz")

  # Memory
  cols+=("mem_used_kb" "mem_available_kb")

  # System load
  cols+=("load_avg_1m")

  local IFS=','
  echo "${cols[*]}"
}

# ── Collect one sample ─────────────────────────────────────────────────────

collect_sample() {
  local ts
  ts=$(date -Iseconds)
  local vals=("$ts")

  # GPU via nvidia-smi (single call for efficiency)
  if $HAS_NVIDIA_SMI; then
    local gpu_csv
    gpu_csv=$(nvidia-smi --query-gpu=temperature.gpu,power.draw,utilization.gpu,clocks.gr,clocks.video \
      --format=csv,noheader,nounits 2>/dev/null || echo ",,,,")
    # Clean up spaces and [N/A] → empty
    gpu_csv=$(echo "$gpu_csv" | sed 's/\[Not Supported\]//g; s/\[N\/A\]//g; s/ //g')
    IFS=',' read -r g_temp g_power g_util g_clock g_vclock <<< "$gpu_csv"
    vals+=("${g_temp:-}" "${g_power:-}" "${g_util:-}" "${g_clock:-}" "${g_vclock:-}")
  fi

  # Thermal zones
  for tz in "${THERMAL_ZONES[@]}"; do
    local raw
    raw=$(sysfs_read "${tz}temp" "0")
    vals+=("$(mdeg_to_c "$raw")")
  done

  # Fan
  if [[ -n "$HWMON_FAN" ]]; then
    vals+=("$(sysfs_read "${HWMON_FAN}fan1_input" "0")")
    vals+=("$(sysfs_read "${HWMON_FAN}power1_input" "0")")
  fi

  # NVMe
  if [[ -n "$HWMON_NVME" ]]; then
    local raw
    raw=$(sysfs_read "${HWMON_NVME}temp1_input" "0")
    vals+=("$(mdeg_to_c "$raw")")
  fi

  # NICs
  for nic in "${HWMON_NICS[@]}"; do
    local raw
    raw=$(sysfs_read "${nic}temp1_input" "0")
    vals+=("$(mdeg_to_c "$raw")")
  done

  # WiFi
  if [[ -n "$HWMON_WIFI" ]]; then
    local raw
    raw=$(sysfs_read "${HWMON_WIFI}temp1_input" "0")
    vals+=("$(mdeg_to_c "$raw")")
  fi

  # CPU frequencies — split into big (>3GHz max) and LITTLE clusters
  local big_sum=0 big_n=0 little_sum=0 little_n=0
  for cpu in "${CPU_DIRS[@]}"; do
    local freq
    freq=$(sysfs_read "${cpu}/scaling_cur_freq" "0")
    if (( freq > 3000000 )); then
      big_sum=$((big_sum + freq))
      big_n=$((big_n + 1))
    else
      little_sum=$((little_sum + freq))
      little_n=$((little_n + 1))
    fi
  done

  local big_avg=0 little_avg=0
  if (( big_n > 0 )); then
    big_avg=$((big_sum / big_n / 1000))  # kHz → MHz
  fi
  if (( little_n > 0 )); then
    little_avg=$((little_sum / little_n / 1000))
  fi
  vals+=("$big_avg" "$little_avg")

  # Memory from /proc/meminfo (faster than calling free)
  local mem_total mem_avail mem_used
  mem_total=$(grep '^MemTotal:' /proc/meminfo | awk '{print $2}')
  mem_avail=$(grep '^MemAvailable:' /proc/meminfo | awk '{print $2}')
  mem_used=$((mem_total - mem_avail))
  vals+=("$mem_used" "$mem_avail")

  # Load average
  local load1
  load1=$(cut -d' ' -f1 /proc/loadavg)
  vals+=("$load1")

  local IFS=','
  echo "${vals[*]}"
}

# ── Collect one sample as JSONL ────────────────────────────────────────────

collect_sample_jsonl() {
  local ts
  ts=$(date -Iseconds)

  # GPU
  local g_temp="" g_power="" g_util="" g_clock="" g_vclock=""
  if $HAS_NVIDIA_SMI; then
    local gpu_csv
    gpu_csv=$(nvidia-smi --query-gpu=temperature.gpu,power.draw,utilization.gpu,clocks.gr,clocks.video \
      --format=csv,noheader,nounits 2>/dev/null || echo ",,,,")
    gpu_csv=$(echo "$gpu_csv" | sed 's/\[Not Supported\]//g; s/\[N\/A\]//g; s/ //g')
    IFS=',' read -r g_temp g_power g_util g_clock g_vclock <<< "$gpu_csv"
  fi

  # Thermal zones array
  local tz_arr="["
  local first=true
  for tz in "${THERMAL_ZONES[@]}"; do
    local raw
    raw=$(sysfs_read "${tz}temp" "0")
    $first || tz_arr+=","
    tz_arr+="$(mdeg_to_c "$raw")"
    first=false
  done
  tz_arr+="]"

  # Fan
  local fan_rpm=0 fan_power=0
  if [[ -n "$HWMON_FAN" ]]; then
    fan_rpm=$(sysfs_read "${HWMON_FAN}fan1_input" "0")
    fan_power=$(sysfs_read "${HWMON_FAN}power1_input" "0")
  fi

  # NVMe
  local nvme_temp="null"
  if [[ -n "$HWMON_NVME" ]]; then
    nvme_temp=$(mdeg_to_c "$(sysfs_read "${HWMON_NVME}temp1_input" "0")")
  fi

  # NICs
  local nic_arr="["
  first=true
  for nic in "${HWMON_NICS[@]}"; do
    $first || nic_arr+=","
    nic_arr+="$(mdeg_to_c "$(sysfs_read "${nic}temp1_input" "0")")"
    first=false
  done
  nic_arr+="]"

  # WiFi
  local wifi_temp="null"
  if [[ -n "$HWMON_WIFI" ]]; then
    wifi_temp=$(mdeg_to_c "$(sysfs_read "${HWMON_WIFI}temp1_input" "0")")
  fi

  # CPU clusters
  local big_sum=0 big_n=0 little_sum=0 little_n=0
  for cpu in "${CPU_DIRS[@]}"; do
    local freq
    freq=$(sysfs_read "${cpu}/scaling_cur_freq" "0")
    if (( freq > 3000000 )); then
      big_sum=$((big_sum + freq)); big_n=$((big_n + 1))
    else
      little_sum=$((little_sum + freq)); little_n=$((little_n + 1))
    fi
  done
  local big_avg=0 little_avg=0
  (( big_n > 0 )) && big_avg=$((big_sum / big_n / 1000))
  (( little_n > 0 )) && little_avg=$((little_sum / little_n / 1000))

  # Memory
  local mem_total mem_avail
  mem_total=$(grep '^MemTotal:' /proc/meminfo | awk '{print $2}')
  mem_avail=$(grep '^MemAvailable:' /proc/meminfo | awk '{print $2}')

  local load1
  load1=$(cut -d' ' -f1 /proc/loadavg)

  printf '{"ts":"%s","gpu":{"temp":%s,"power_w":%s,"util_pct":%s,"clock_mhz":%s,"vid_clock_mhz":%s},"thermal_zones":%s,"fan":{"rpm":%s,"power_uw":%s},"nvme_temp_c":%s,"nic_temps_c":%s,"wifi_temp_c":%s,"cpu":{"big_avg_mhz":%s,"little_avg_mhz":%s},"mem":{"used_kb":%s,"avail_kb":%s},"load_1m":%s}\n' \
    "$ts" \
    "${g_temp:-null}" "${g_power:-null}" "${g_util:-null}" "${g_clock:-null}" "${g_vclock:-null}" \
    "$tz_arr" \
    "$fan_rpm" "$fan_power" \
    "$nvme_temp" \
    "$nic_arr" \
    "$wifi_temp" \
    "$big_avg" "$little_avg" \
    "$((mem_total - mem_avail))" "$mem_avail" \
    "$load1"
}

# ── Main ───────────────────────────────────────────────────────────────────

sample_count=0

trap 'echo ""; echo "Stopped after ${sample_count} samples → ${OUTPUT}"; exit 0' INT TERM

echo "DGX Spark Sensor Logger"
echo "  Format:   ${FORMAT}"
echo "  Interval: ${INTERVAL}s"
echo "  Output:   ${OUTPUT}"
echo "  Sensors:  GPU=$(${HAS_NVIDIA_SMI} && echo yes || echo no)" \
     "ThermalZones=${#THERMAL_ZONES[@]}" \
     "Fan=$([ -n \"$HWMON_FAN\" ] && echo yes || echo no)" \
     "NVMe=$([ -n \"$HWMON_NVME\" ] && echo yes || echo no)" \
     "NICs=${#HWMON_NICS[@]}" \
     "WiFi=$([ -n \"$HWMON_WIFI\" ] && echo yes || echo no)" \
     "CPUs=${#CPU_DIRS[@]}"
echo ""
echo "Logging... (Ctrl+C to stop)"

# Write header (CSV only)
if [[ "$FORMAT" == "csv" ]]; then
  build_header > "$OUTPUT"
fi

while true; do
  if [[ "$FORMAT" == "csv" ]]; then
    collect_sample >> "$OUTPUT"
  else
    collect_sample_jsonl >> "$OUTPUT"
  fi
  sample_count=$((sample_count + 1))

  # Print a dot every 10 samples so you know it's alive
  if (( sample_count % 10 == 0 )); then
    printf "\r  %d samples collected" "$sample_count"
  fi

  sleep "$INTERVAL"
done
