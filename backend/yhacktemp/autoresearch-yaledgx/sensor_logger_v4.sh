#!/usr/bin/env bash
# sensor_logger_v4.sh — 67-channel time-series sensor logger for DGX Spark
#
# Discovered on ASUS Ascent GX10 (GB10 Blackwell, ARM aarch64,
# 128GB unified LPDDR5X, kernel 6.17.0-1008-nvidia, driver 580.126.09).
#
# Channels (67):
#   GPU core (14):  temp, tlimit, power (avg+instant), util (gpu+mem), clocks
#                   (graphics+video+SM), pstate, throttle reasons (hex+3 bools)
#   GPU extra (5):  max_clock, gpu_idle, power_brake, pcie gen+width
#   Board (7):      thermal_zone0-6 ACPI temps
#   Fan (2):        state (0-3), power_uw
#   NVMe (3):       composite temp, NAND sensor temp, thermal alarm
#   NICs (4):       4x ConnectX-7 ASIC temps
#   WiFi (1):       mt7925
#   CPU freq (2):   big cluster avg MHz (X925), LITTLE cluster avg MHz (A725)
#   CPU time (4):   user/system/idle/iowait (cumulative jiffies)
#   CPU state (3):  context_switches, procs_running, procs_blocked
#   CPU thermal (1): max throttle state across all 20 processor cooling devices
#   Memory (7):     used, available, cached, dirty, anon, file_hugepages, swap_used
#   Network (2):    rx_bytes, tx_bytes (active interface, cumulative)
#   Load (1):       1-min load average
#   PSI (4):        cpu some, mem some, mem full, io some (avg10)
#   IO (5):         NVMe read/write IOs, read/write sectors, in-progress
#   VM (2):         pgmajfault, pgfault (cumulative)
#
# Usage:
#   ./sensor_logger_v4.sh                        # 1s interval, auto-named CSV
#   ./sensor_logger_v4.sh -i 2 -o run.csv        # 2s interval, custom output
#
# Stop with Ctrl+C. Flushed after every row — partial runs are safe.

set -euo pipefail

# ── Defaults ────────────────────────────────────────────────────────────────

INTERVAL=1
OUTPUT=""

usage() {
  echo "Usage: $0 [-i SECONDS] [-o OUTPUT_FILE]"
  echo "  -i    Sampling interval in seconds (default: 1)"
  echo "  -o    Output file path (default: sensor_log_v4_<timestamp>.csv)"
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -i) INTERVAL="$2"; shift 2 ;;
    -o) OUTPUT="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "Unknown option: $1"; usage ;;
  esac
done

[[ -z "$OUTPUT" ]] && OUTPUT="sensor_log_v4_$(date +%Y%m%d_%H%M%S).csv"

# ── Helpers ─────────────────────────────────────────────────────────────────

sysfs_read() { cat "$1" 2>/dev/null || echo "$2"; }

# millidegrees → degrees with 1 decimal, pure bash (no bc)
mdeg_to_c() {
  local raw="${1:-0}"
  [[ -z "$raw" || "$raw" == "0" ]] && { echo "0.0"; return; }
  echo "$(( raw / 1000 )).$(( (raw % 1000) / 100 ))"
}

# Extract avg10 from PSI: "some avg10=0.00 avg60=..."
psi_avg10() {
  awk -v k="$1" '$1==k {for(i=2;i<=NF;i++){split($i,a,"="); if(a[1]=="avg10") print a[2]}}' "$2" 2>/dev/null || echo ""
}

# ── Detect available sensors (once at startup) ─────────────────────────────

HAS_NVIDIA=false
command -v nvidia-smi &>/dev/null && HAS_NVIDIA=true

THERMAL_ZONES=()
for tz in /sys/class/thermal/thermal_zone*/; do
  [[ -d "$tz" ]] && THERMAL_ZONES+=("$tz")
done

HWMON_FAN="" HWMON_NVME="" HWMON_WIFI=""
HWMON_NICS=()
for hwmon in /sys/class/hwmon/hwmon*/; do
  [[ -d "$hwmon" ]] || continue
  name=$(cat "${hwmon}/name" 2>/dev/null || echo "")
  case "$name" in
    acpi_fan)    HWMON_FAN="$hwmon" ;;
    nvme)        HWMON_NVME="$hwmon" ;;
    mlx5)        HWMON_NICS+=("$hwmon") ;;
    mt7925_phy*) HWMON_WIFI="$hwmon" ;;
  esac
done

CPU_DIRS=()
for cpu in /sys/devices/system/cpu/cpu[0-9]*/cpufreq; do
  [[ -d "$cpu" ]] && CPU_DIRS+=("$cpu")
done

# Processor cooling devices (cooling_device1..20)
COOL_PROCS=()
for cd in /sys/class/thermal/cooling_device*/; do
  [[ -d "$cd" ]] || continue
  [[ "$(cat ${cd}type 2>/dev/null)" == "Processor" ]] && COOL_PROCS+=("$cd")
done

HAS_PSI=false;       [[ -f /proc/pressure/cpu ]] && HAS_PSI=true
HAS_NVME_STAT=false;  [[ -f /sys/block/nvme0n1/stat ]] && HAS_NVME_STAT=true

# Detect active network interface (for bandwidth counters)
NET_IFACE=$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}' || echo "")
NET_STATS=""
[[ -n "$NET_IFACE" && -d "/sys/class/net/${NET_IFACE}/statistics" ]] && NET_STATS="/sys/class/net/${NET_IFACE}/statistics"

# ── CSV header ──────────────────────────────────────────────────────────────

build_header() {
  local cols=("timestamp")

  # GPU — 19 fields from single nvidia-smi call
  if $HAS_NVIDIA; then
    cols+=(
      "gpu_temp_c" "gpu_power_w" "gpu_power_instant_w"
      "gpu_util_pct" "gpu_mem_util_pct"
      "gpu_clock_mhz" "gpu_vid_clock_mhz" "gpu_sm_clock_mhz" "gpu_max_clock_mhz"
      "gpu_tlimit_c" "gpu_pstate"
      "gpu_throttle_reasons"
      "gpu_hw_thermal_throttle" "gpu_hw_slowdown" "gpu_sw_power_cap"
      "gpu_idle" "gpu_power_brake"
      "pcie_gen" "pcie_width"
    )
  fi

  # Thermal zones
  for i in "${!THERMAL_ZONES[@]}"; do cols+=("thermal_zone${i}_c"); done

  # Fan
  [[ -n "$HWMON_FAN" ]] && cols+=("fan_state" "fan_power_uw")

  # NVMe
  if [[ -n "$HWMON_NVME" ]]; then
    cols+=("nvme_temp_c" "nvme_temp2_c" "nvme_temp_alarm")
  fi

  # NICs
  for i in "${!HWMON_NICS[@]}"; do cols+=("nic${i}_temp_c"); done

  # WiFi
  [[ -n "$HWMON_WIFI" ]] && cols+=("wifi_temp_c")

  # CPU frequency clusters
  cols+=("cpu_big_avg_mhz" "cpu_little_avg_mhz")

  # CPU time (cumulative jiffies from /proc/stat line 1)
  cols+=("cpu_user" "cpu_system" "cpu_idle" "cpu_iowait")

  # CPU state
  cols+=("context_switches" "procs_running" "procs_blocked")

  # CPU thermal throttle
  (( ${#COOL_PROCS[@]} > 0 )) && cols+=("cpu_throttle_max")

  # Memory (7 fields)
  cols+=("mem_used_kb" "mem_available_kb" "mem_cached_kb" "mem_dirty_kb"
         "mem_anon_kb" "mem_file_hugepages_kb" "swap_used_kb")

  # Network
  [[ -n "$NET_STATS" ]] && cols+=("net_rx_bytes" "net_tx_bytes")

  # Load
  cols+=("load_avg_1m")

  # PSI
  $HAS_PSI && cols+=("psi_cpu_avg10" "psi_mem_some_avg10" "psi_mem_full_avg10" "psi_io_some_avg10")

  # IO
  $HAS_NVME_STAT && cols+=("nvme_read_ios" "nvme_write_ios" "nvme_read_sectors" "nvme_write_sectors" "nvme_io_in_progress")

  # VM (2 counters)
  cols+=("pgmajfault" "pgfault")

  local IFS=','
  echo "${cols[*]}"
}

# ── Collect one sample ──────────────────────────────────────────────────────

collect_sample() {
  local vals=("$(date -Iseconds)")

  # ── GPU (single nvidia-smi call, 19 parsed fields) ────────────────────
  if $HAS_NVIDIA; then
    local gpu_csv
    gpu_csv=$(nvidia-smi --query-gpu=\
temperature.gpu,\
power.draw,power.draw.instant,\
utilization.gpu,utilization.memory,\
clocks.gr,clocks.video,clocks.sm,clocks.max.graphics,\
temperature.gpu.tlimit,\
pstate,\
clocks_throttle_reasons.active,\
clocks_throttle_reasons.hw_thermal_slowdown,\
clocks_throttle_reasons.hw_slowdown,\
clocks_throttle_reasons.sw_power_cap,\
clocks_event_reasons.gpu_idle,\
clocks_event_reasons.hw_power_brake_slowdown,\
pcie.link.gen.current,\
pcie.link.width.current \
      --format=csv,noheader,nounits 2>/dev/null || echo ",,,,,,,,,,,,,,,,,,")

    # Normalize: strip spaces, convert booleans
    gpu_csv=$(echo "$gpu_csv" | sed 's/ //g;s/\[N\/A\]//g;s/\[NotSupported\]//g;s/NotActive/0/g;s/Active/1/g')

    IFS=',' read -r g_temp g_pow g_pow_inst g_util g_mutil \
                     g_clk g_vclk g_smclk g_maxclk \
                     g_tlimit g_pstate \
                     g_thr_hex g_thr_therm g_thr_hw g_thr_swpow \
                     g_idle g_brake \
                     g_pcie_gen g_pcie_width <<< "$gpu_csv"

    vals+=(
      "${g_temp:-}" "${g_pow:-}" "${g_pow_inst:-}"
      "${g_util:-}" "${g_mutil:-}"
      "${g_clk:-}" "${g_vclk:-}" "${g_smclk:-}" "${g_maxclk:-}"
      "${g_tlimit:-}" "${g_pstate:-}"
      "${g_thr_hex:-}" "${g_thr_therm:-}" "${g_thr_hw:-}" "${g_thr_swpow:-}"
      "${g_idle:-}" "${g_brake:-}"
      "${g_pcie_gen:-}" "${g_pcie_width:-}"
    )
  fi

  # ── Thermal zones ─────────────────────────────────────────────────────
  for tz in "${THERMAL_ZONES[@]}"; do
    vals+=("$(mdeg_to_c "$(sysfs_read "${tz}temp" "0")")")
  done

  # ── Fan ───────────────────────────────────────────────────────────────
  if [[ -n "$HWMON_FAN" ]]; then
    vals+=("$(sysfs_read "${HWMON_FAN}fan1_input" "0")")
    vals+=("$(sysfs_read "${HWMON_FAN}power1_input" "0")")
  fi

  # ── NVMe (Composite + Sensor 1 + alarm) ──────────────────────────────
  if [[ -n "$HWMON_NVME" ]]; then
    vals+=("$(mdeg_to_c "$(sysfs_read "${HWMON_NVME}temp1_input" "0")")")
    vals+=("$(mdeg_to_c "$(sysfs_read "${HWMON_NVME}temp2_input" "0")")")
    vals+=("$(sysfs_read "${HWMON_NVME}temp1_alarm" "")")
  fi

  # ── NICs ──────────────────────────────────────────────────────────────
  for nic in "${HWMON_NICS[@]}"; do
    vals+=("$(mdeg_to_c "$(sysfs_read "${nic}temp1_input" "0")")")
  done

  # ── WiFi ──────────────────────────────────────────────────────────────
  if [[ -n "$HWMON_WIFI" ]]; then
    vals+=("$(mdeg_to_c "$(sysfs_read "${HWMON_WIFI}temp1_input" "0")")")
  fi

  # ── CPU frequency clusters ────────────────────────────────────────────
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
  (( big_n > 0 ))    && big_avg=$((big_sum / big_n / 1000))
  (( little_n > 0 )) && little_avg=$((little_sum / little_n / 1000))
  vals+=("$big_avg" "$little_avg")

  # ── CPU time from /proc/stat (cumulative jiffies) ─────────────────────
  local stat_line
  read -r _ cpu_u cpu_n cpu_s cpu_i cpu_io cpu_irq cpu_si _ < /proc/stat
  vals+=("$((cpu_u + cpu_n))" "$((cpu_s + cpu_irq + cpu_si))" "$cpu_i" "$cpu_io")

  # ── CPU state (context switches, running/blocked) ─────────────────────
  vals+=("$(awk '/^ctxt/{print $2}' /proc/stat)")
  vals+=("$(awk '/^procs_running/{print $2}' /proc/stat)")
  vals+=("$(awk '/^procs_blocked/{print $2}' /proc/stat)")

  # ── CPU thermal throttle (max state across all processor coolers) ─────
  if (( ${#COOL_PROCS[@]} > 0 )); then
    local max_throttle=0
    for cd in "${COOL_PROCS[@]}"; do
      local s
      s=$(cat "${cd}cur_state" 2>/dev/null || echo 0)
      (( s > max_throttle )) && max_throttle=$s
    done
    vals+=("$max_throttle")
  fi

  # ── Memory (7 fields from /proc/meminfo) ──────────────────────────────
  local meminfo
  meminfo=$(</proc/meminfo)
  local mem_total mem_avail mem_cached mem_dirty mem_anon mem_fhp swap_total swap_free
  mem_total=$(echo "$meminfo"  | awk '/^MemTotal:/{print $2}')
  mem_avail=$(echo "$meminfo"  | awk '/^MemAvailable:/{print $2}')
  mem_cached=$(echo "$meminfo" | awk '/^Cached:/{print $2}')
  mem_dirty=$(echo "$meminfo"  | awk '/^Dirty:/{print $2}')
  mem_anon=$(echo "$meminfo"   | awk '/^AnonPages:/{print $2}')
  mem_fhp=$(echo "$meminfo"    | awk '/^FileHugePages:/{print $2}')
  swap_total=$(echo "$meminfo" | awk '/^SwapTotal:/{print $2}')
  swap_free=$(echo "$meminfo"  | awk '/^SwapFree:/{print $2}')
  vals+=("$((mem_total - mem_avail))" "$mem_avail" "$mem_cached" "$mem_dirty"
         "$mem_anon" "${mem_fhp:-0}" "$((swap_total - swap_free))")

  # ── Network (cumulative counters) ─────────────────────────────────────
  if [[ -n "$NET_STATS" ]]; then
    vals+=("$(sysfs_read "${NET_STATS}/rx_bytes" "0")")
    vals+=("$(sysfs_read "${NET_STATS}/tx_bytes" "0")")
  fi

  # ── Load ──────────────────────────────────────────────────────────────
  vals+=("$(cut -d' ' -f1 /proc/loadavg)")

  # ── PSI ───────────────────────────────────────────────────────────────
  if $HAS_PSI; then
    vals+=("$(psi_avg10 some /proc/pressure/cpu)")
    vals+=("$(psi_avg10 some /proc/pressure/memory)")
    vals+=("$(psi_avg10 full /proc/pressure/memory)")
    vals+=("$(psi_avg10 some /proc/pressure/io)")
  fi

  # ── IO (raw counters) ────────────────────────────────────────────────
  if $HAS_NVME_STAT; then
    local iostat
    iostat=$(</sys/block/nvme0n1/stat)
    local r_ios r_sect w_ios w_sect io_prog
    read -r r_ios _ r_sect _ w_ios _ w_sect _ io_prog _ <<< "$iostat"
    vals+=("$r_ios" "$w_ios" "$r_sect" "$w_sect" "$io_prog")
  fi

  # ── VM (2 counters) ──────────────────────────────────────────────────
  local vmstat
  vmstat=$(</proc/vmstat)
  vals+=("$(echo "$vmstat" | awk '/^pgmajfault/{print $2}')")
  vals+=("$(echo "$vmstat" | awk '/^pgfault /{print $2}')")

  local IFS=','
  echo "${vals[*]}"
}

# ── Main ────────────────────────────────────────────────────────────────────

sample_count=0

trap 'echo ""; echo "Stopped after ${sample_count} samples → ${OUTPUT}"; exit 0' INT TERM

cat << EOF
DGX Spark Sensor Logger v4 (67-channel)
  Interval: ${INTERVAL}s
  Output:   ${OUTPUT}
  Sensors:
    GPU:          $($HAS_NVIDIA && echo "19 fields (core+pcie+events)" || echo "none")
    ThermalZones: ${#THERMAL_ZONES[@]}
    Fan:          $([[ -n "$HWMON_FAN" ]] && echo "yes" || echo "no")
    NVMe:         $([[ -n "$HWMON_NVME" ]] && echo "composite+sensor1+alarm" || echo "no")
    NICs:         ${#HWMON_NICS[@]}
    WiFi:         $([[ -n "$HWMON_WIFI" ]] && echo "yes" || echo "no")
    CPUs:         ${#CPU_DIRS[@]} cores, ${#COOL_PROCS[@]} coolers
    Network:      ${NET_IFACE:-none} (rx/tx bytes)
    PSI:          $($HAS_PSI && echo "cpu+mem+io" || echo "no")
    IO:           $($HAS_NVME_STAT && echo "nvme0n1" || echo "no")
    Memory:       7 fields (used/avail/cached/dirty/anon/hugepages/swap)
    VM:           pgmajfault + pgfault

Logging... (Ctrl+C to stop)
EOF

build_header > "$OUTPUT"

while true; do
  collect_sample >> "$OUTPUT"
  sample_count=$((sample_count + 1))
  (( sample_count % 10 == 0 )) && printf "\r  %d samples collected" "$sample_count"
  sleep "$INTERVAL"
done
