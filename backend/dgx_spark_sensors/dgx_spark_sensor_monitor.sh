#!/usr/bin/env bash
# dgx_spark_sensor_monitor.sh — Real-time sensor dashboard for NVIDIA DGX Spark
# Analogous to macOS "silicon" tool but for the DGX Spark (ARM + Blackwell GPU)
#
# Usage:
#   ./dgx_spark_sensor_monitor.sh            # one-shot snapshot
#   ./dgx_spark_sensor_monitor.sh --watch    # continuous monitoring (2s refresh)
#   ./dgx_spark_sensor_monitor.sh --json     # JSON output (for piping)
set -euo pipefail

# ── Helpers ───────────────────────────────────────────────────────────────────

millideg_to_c() {
  local raw="${1:-0}"
  echo "scale=1; ${raw} / 1000" | bc 2>/dev/null || echo "N/A"
}

khz_to_ghz() {
  local raw="${1:-0}"
  echo "scale=2; ${raw} / 1000000" | bc 2>/dev/null || echo "N/A"
}

divider() {
  printf '%.0s─' {1..60}
  echo
}

# ── GPU Sensors (nvidia-smi) ─────────────────────────────────────────────────

read_gpu_sensors() {
  echo "🟢 GPU — NVIDIA GB10 (Blackwell)"
  divider

  local gpu_csv
  gpu_csv=$(nvidia-smi --query-gpu=temperature.gpu,power.draw,utilization.gpu,clocks.gr,clocks.video \
    --format=csv,noheader,nounits 2>/dev/null || echo "N/A,N/A,N/A,N/A,N/A")

  IFS=',' read -r gpu_temp gpu_power gpu_util gpu_clock vid_clock <<< "$gpu_csv"
  gpu_temp=$(echo "$gpu_temp" | xargs)
  gpu_power=$(echo "$gpu_power" | xargs)
  gpu_util=$(echo "$gpu_util" | xargs)
  gpu_clock=$(echo "$gpu_clock" | xargs)
  vid_clock=$(echo "$vid_clock" | xargs)

  # Get power details from full query
  local avg_power inst_power perf_state
  avg_power=$(nvidia-smi -q 2>/dev/null | grep "Average Power Draw" | head -1 | awk -F: '{print $2}' | xargs)
  inst_power=$(nvidia-smi -q 2>/dev/null | grep "Instantaneous Power Draw" | head -1 | awk -F: '{print $2}' | xargs)
  perf_state=$(nvidia-smi -q 2>/dev/null | grep "Performance State" | awk -F: '{print $2}' | xargs)

  printf "  %-28s %s\n" "Temperature:" "${gpu_temp}°C"
  printf "  %-28s %s\n" "Avg Power Draw:" "${avg_power}"
  printf "  %-28s %s\n" "Instantaneous Power Draw:" "${inst_power}"
  printf "  %-28s %s%%\n" "GPU Utilization:" "${gpu_util}"
  printf "  %-28s %s MHz (max 2418 MHz)\n" "Graphics Clock:" "${gpu_clock}"
  printf "  %-28s %s MHz\n" "Video Clock:" "${vid_clock}"
  printf "  %-28s %s\n" "Performance State:" "${perf_state}"
  printf "  %-28s %s\n" "Memory:" "Unified (C2C enabled, 128GB shared)"
  echo
}

# ── CPU Sensors ──────────────────────────────────────────────────────────────

read_cpu_sensors() {
  echo "🔵 CPU — ARM Cortex-X925 + Cortex-A725 (big.LITTLE)"
  divider

  # Read all CPU frequencies
  local freqs=()
  local big_count=0 little_count=0
  local big_sum=0 little_sum=0

  for cpu_dir in /sys/devices/system/cpu/cpu[0-9]*/cpufreq; do
    [ -d "$cpu_dir" ] || continue
    local freq
    freq=$(cat "${cpu_dir}/scaling_cur_freq" 2>/dev/null || echo "0")
    freqs+=("$freq")
    # X925 big cores run at higher freqs (max 3900 MHz = 3900000 kHz)
    if (( freq > 3000000 )); then
      big_count=$((big_count + 1))
      big_sum=$((big_sum + freq))
    else
      little_count=$((little_count + 1))
      little_sum=$((little_sum + freq))
    fi
  done

  local big_avg little_avg
  if (( big_count > 0 )); then
    big_avg=$(echo "scale=2; ${big_sum} / ${big_count} / 1000000" | bc)
  else
    big_avg="N/A"
  fi
  if (( little_count > 0 )); then
    little_avg=$(echo "scale=2; ${little_sum} / ${little_count} / 1000000" | bc)
  else
    little_avg="N/A"
  fi

  printf "  %-28s %s cores @ avg %s GHz\n" "X925 (big) Cores:" "${big_count}" "${big_avg}"
  printf "  %-28s %s cores @ avg %s GHz\n" "A725 (LITTLE) Cores:" "${little_count}" "${little_avg}"
  printf "  %-28s %s\n" "Total Cores:" "${#freqs[@]}"

  # Individual core frequencies
  echo "  Core Frequencies:"
  local i=0
  for freq in "${freqs[@]}"; do
    local ghz
    ghz=$(echo "scale=2; ${freq} / 1000000" | bc)
    printf "    cpu%-2d: %s GHz\n" "$i" "$ghz"
    i=$((i + 1))
  done
  echo
}

# ── Thermal Zones (ACPI) ────────────────────────────────────────────────────

read_thermal_sensors() {
  echo "🌡️  Thermal Zones (ACPI)"
  divider

  for tz_dir in /sys/class/thermal/thermal_zone*/; do
    [ -d "$tz_dir" ] || continue
    local name temp_raw temp_c
    name=$(cat "${tz_dir}/type" 2>/dev/null || echo "unknown")
    temp_raw=$(cat "${tz_dir}/temp" 2>/dev/null || echo "0")
    temp_c=$(millideg_to_c "$temp_raw")
    local zone
    zone=$(basename "$tz_dir")
    printf "  %-28s %s°C  (%s)\n" "${zone}:" "${temp_c}" "${name}"
  done
  echo
}

# ── Hardware Monitors (hwmon) ────────────────────────────────────────────────

read_hwmon_sensors() {
  echo "🔧 Hardware Monitors"
  divider

  for hwmon_dir in /sys/class/hwmon/hwmon*/; do
    [ -d "$hwmon_dir" ] || continue
    local hw_name
    hw_name=$(cat "${hwmon_dir}/name" 2>/dev/null || echo "unknown")
    local hw_id
    hw_id=$(basename "$hwmon_dir")

    case "$hw_name" in
      acpi_fan)
        local fan_rpm fan_target fan_power
        fan_rpm=$(cat "${hwmon_dir}/fan1_input" 2>/dev/null || echo "N/A")
        fan_target=$(cat "${hwmon_dir}/fan1_target" 2>/dev/null || echo "N/A")
        fan_power=$(cat "${hwmon_dir}/power1_input" 2>/dev/null || echo "0")
        local fan_mw
        fan_mw=$(echo "scale=1; ${fan_power} / 1000" | bc 2>/dev/null || echo "N/A")
        printf "  [%s] %-20s RPM: %s (target: %s) | Power: %s mW\n" \
          "$hw_id" "Fan ($hw_name)" "$fan_rpm" "$fan_target" "$fan_mw"
        ;;
      acpitz)
        printf "  [%s] %-20s " "$hw_id" "ACPI Thermal"
        local temps=()
        for tf in "${hwmon_dir}"/temp*_input; do
          [ -f "$tf" ] || continue
          local t
          t=$(millideg_to_c "$(cat "$tf")")
          temps+=("${t}°C")
        done
        echo "${temps[*]}"
        ;;
      nvme)
        local composite crit_temp
        composite=$(millideg_to_c "$(cat "${hwmon_dir}/temp1_input" 2>/dev/null || echo 0)")
        crit_temp=$(millideg_to_c "$(cat "${hwmon_dir}/temp1_crit" 2>/dev/null || echo 0)")
        printf "  [%s] %-20s Composite: %s°C (critical: %s°C)\n" \
          "$hw_id" "NVMe SSD" "$composite" "$crit_temp"
        ;;
      mlx5)
        local asic_temp label
        asic_temp=$(millideg_to_c "$(cat "${hwmon_dir}/temp1_input" 2>/dev/null || echo 0)")
        label=$(cat "${hwmon_dir}/temp1_label" 2>/dev/null || echo "asic")
        printf "  [%s] %-20s %s: %s°C\n" "$hw_id" "ConnectX-7 NIC" "$label" "$asic_temp"
        ;;
      mt7925_phy0)
        local wifi_temp
        wifi_temp=$(millideg_to_c "$(cat "${hwmon_dir}/temp1_input" 2>/dev/null || echo 0)")
        printf "  [%s] %-20s Temp: %s°C\n" "$hw_id" "WiFi (MT7925)" "$wifi_temp"
        ;;
      *)
        printf "  [%s] %-20s (unknown sensor)\n" "$hw_id" "$hw_name"
        ;;
    esac
  done
  echo
}

# ── Memory Info ──────────────────────────────────────────────────────────────

read_memory_info() {
  echo "💾 Memory (Unified CPU+GPU)"
  divider

  local mem_total mem_used mem_avail swap_total swap_used
  mem_total=$(free -h | awk '/^Mem:/ {print $2}')
  mem_used=$(free -h | awk '/^Mem:/ {print $3}')
  mem_avail=$(free -h | awk '/^Mem:/ {print $7}')
  swap_total=$(free -h | awk '/^Swap:/ {print $2}')
  swap_used=$(free -h | awk '/^Swap:/ {print $3}')

  printf "  %-28s %s\n" "Total:" "$mem_total"
  printf "  %-28s %s\n" "Used:" "$mem_used"
  printf "  %-28s %s\n" "Available:" "$mem_avail"
  printf "  %-28s %s / %s\n" "Swap:" "$swap_used" "$swap_total"
  echo
}

# ── JSON Output ──────────────────────────────────────────────────────────────

output_json() {
  local gpu_csv
  gpu_csv=$(nvidia-smi --query-gpu=temperature.gpu,power.draw,utilization.gpu,clocks.gr \
    --format=csv,noheader,nounits 2>/dev/null || echo "0,0,0,0")
  IFS=',' read -r gpu_temp gpu_power gpu_util gpu_clock <<< "$gpu_csv"

  echo "{"
  echo "  \"timestamp\": \"$(date -Iseconds)\","

  # GPU
  echo "  \"gpu\": {"
  echo "    \"name\": \"NVIDIA GB10 (Blackwell)\","
  echo "    \"temp_c\": $(echo "$gpu_temp" | xargs),"
  echo "    \"power_w\": $(echo "$gpu_power" | xargs),"
  echo "    \"utilization_pct\": $(echo "$gpu_util" | xargs),"
  echo "    \"clock_mhz\": $(echo "$gpu_clock" | xargs)"
  echo "  },"

  # Thermal zones
  echo "  \"thermal_zones\": ["
  local first=true
  for tz_dir in /sys/class/thermal/thermal_zone*/; do
    [ -d "$tz_dir" ] || continue
    local temp_raw name
    temp_raw=$(cat "${tz_dir}/temp" 2>/dev/null || echo "0")
    name=$(cat "${tz_dir}/type" 2>/dev/null || echo "unknown")
    local temp_c
    temp_c=$(millideg_to_c "$temp_raw")
    $first || echo ","
    printf '    {"zone": "%s", "type": "%s", "temp_c": %s}' "$(basename "$tz_dir")" "$name" "$temp_c"
    first=false
  done
  echo
  echo "  ],"

  # CPU freqs
  echo "  \"cpu_freqs_khz\": ["
  first=true
  for cpu_dir in /sys/devices/system/cpu/cpu[0-9]*/cpufreq; do
    [ -d "$cpu_dir" ] || continue
    local freq
    freq=$(cat "${cpu_dir}/scaling_cur_freq" 2>/dev/null || echo "0")
    $first || echo ","
    printf "    %s" "$freq"
    first=false
  done
  echo
  echo "  ],"

  # Memory
  local mem_total_kb mem_avail_kb
  mem_total_kb=$(grep MemTotal /proc/meminfo | awk '{print $2}')
  mem_avail_kb=$(grep MemAvailable /proc/meminfo | awk '{print $2}')
  echo "  \"memory\": {"
  echo "    \"total_kb\": ${mem_total_kb},"
  echo "    \"available_kb\": ${mem_avail_kb}"
  echo "  }"

  echo "}"
}

# ── Main ─────────────────────────────────────────────────────────────────────

print_dashboard() {
  clear 2>/dev/null || true
  echo "╔══════════════════════════════════════════════════════════════╗"
  echo "║         🖥️  NVIDIA DGX Spark — Sensor Dashboard            ║"
  echo "║         $(date '+%Y-%m-%d %H:%M:%S')                               ║"
  echo "╚══════════════════════════════════════════════════════════════╝"
  echo

  read_gpu_sensors
  read_cpu_sensors
  read_thermal_sensors
  read_hwmon_sensors
  read_memory_info
}

main() {
  case "${1:-}" in
    --json)
      output_json
      ;;
    --watch)
      while true; do
        print_dashboard
        sleep 2
      done
      ;;
    *)
      print_dashboard
      ;;
  esac
}

main "$@"
