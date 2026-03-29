# NVIDIA DGX Spark — Sensor Exploration Report

**Date**: 2026-03-28  
**Machine**: gx10-f443 (Tailscale: 100.98.82.117, Local: 10.65.68.52)  
**Agent**: PiercePuppy (code-puppy-bc004b) 🐶

---

## System Overview

| Component | Details |
|-----------|---------|
| **Product** | NVIDIA DGX Spark |
| **GPU** | NVIDIA GB10 — **Blackwell architecture** |
| **CPU** | ARM Cortex-X925 (big) + Cortex-A725 (LITTLE) — big.LITTLE |
| **Cores** | 20 total (10 big @ 3.9 GHz max, 10 LITTLE @ 2.8 GHz max) |
| **Memory** | 128 GB unified (CPU+GPU via C2C) — 119 GiB usable |
| **Storage** | 1 TB NVMe (Phison PS5027-E27T, 916 GB formatted) |
| **Network** | 4× Mellanox ConnectX-7 (10/25/100 GbE) + MediaTek MT7925 WiFi |
| **Driver** | NVIDIA 580.126.09, CUDA 13.0 |
| **OS** | Ubuntu 24.04.4 LTS (kernel 6.17.0-1008-nvidia, aarch64) |

### Architecture Note: Unified Memory via C2C

The DGX Spark uses NVIDIA's **Chip-to-Chip (C2C)** interconnect, sharing all 128 GB of memory between CPU and GPU — similar to Apple Silicon's unified memory. This is why `nvidia-smi` reports `FB Memory: N/A` — there's no dedicated VRAM; it's all one shared pool.

---

## Sensor Sources Discovered

### ✅ Working

| # | Source | What It Reports |
|---|--------|-----------------|
| 1 | `nvidia-smi` | GPU temp, power draw (avg + instant), utilization, clocks, perf state |
| 2 | `/sys/class/thermal/thermal_zone*` | 7 ACPI thermal zones (board-level temps) |
| 3 | `/sys/class/hwmon/hwmon0` (acpi_fan) | Fan RPM, fan target, fan controller power |
| 4 | `/sys/class/hwmon/hwmon1` (acpitz) | 7 temperature inputs (board/SoC temps) |
| 5 | `/sys/class/hwmon/hwmon2` (nvme) | NVMe SSD temps (composite + sensor, with critical thresholds) |
| 6 | `/sys/class/hwmon/hwmon3-6` (mlx5) | 4× ConnectX-7 NIC ASIC temperatures |
| 7 | `/sys/class/hwmon/hwmon7` (mt7925_phy0) | WiFi adapter temperature |
| 8 | `/sys/devices/system/cpu/cpu*/cpufreq` | Per-core CPU frequencies (real-time) |
| 9 | `free` / `/proc/meminfo` | Unified memory usage |

### ❌ Not Available

| Source | Status | Why |
|--------|--------|-----|
| `sensors` (lm-sensors) | Not installed | Package not present; could be installed |
| `tegrastats` | Not found | Not a Jetson platform (despite ARM) |
| `ipmitool` | Installed but no IPMI device | `/dev/ipmi0` doesn't exist — no BMC |
| `powertop` | Not installed | Could be installed for per-process power |
| RAPL (Intel energy counters) | N/A | ARM platform, not x86 |
| `/sys/class/power_supply/` | Empty | Desktop, not battery-powered |

---

## Working Commands & Example Output

### 1. GPU Monitoring — `nvidia-smi`

#### Quick CSV query
```bash
nvidia-smi --query-gpu=name,temperature.gpu,power.draw,utilization.gpu,clocks.gr \
  --format=csv,noheader,nounits
```
```
NVIDIA GB10, 38, 4.00, 0, 208
```

#### Detailed power & temperature
```bash
nvidia-smi -q | grep -A20 'Power Readings'
```
```
    GPU Power Readings
        Average Power Draw                : 4.15 W
        Instantaneous Power Draw          : 4.18 W
        Current Power Limit               : N/A
        ...
```

```bash
nvidia-smi -q | grep -A15 'Temperature'
```
```
    Temperature
        GPU Current Temp                  : 38 C
        GPU T.Limit Temp                  : 57 C
        GPU Shutdown T.Limit Temp         : -5 C
        GPU Slowdown T.Limit Temp         : -2 C
```

#### Real-time GPU monitoring (1 sample)
```bash
nvidia-smi dmon -c 1
```
```
# gpu    pwr  gtemp  mtemp     sm    mem    enc    dec    jpg    ofa   mclk   pclk
# Idx      W      C      C      %      %      %      %      %      %    MHz    MHz
    0      3     38      -      0      0      0      0      0      0      -    208
```

### 2. Thermal Zones — `/sys/class/thermal/`

```bash
for tz in /sys/class/thermal/thermal_zone*/; do
  echo "$(basename $tz): $(cat $tz/type) = $(echo "scale=1; $(cat $tz/temp) / 1000" | bc)°C"
done
```
```
thermal_zone0: acpitz = 41.6°C
thermal_zone1: acpitz = 41.0°C
thermal_zone2: acpitz = 41.6°C
thermal_zone3: acpitz = 40.4°C
thermal_zone4: acpitz = 40.7°C
thermal_zone5: acpitz = 39.8°C
thermal_zone6: acpitz = 40.0°C
```

### 3. Fan Speed — hwmon0

```bash
echo "Fan RPM: $(cat /sys/class/hwmon/hwmon0/fan1_input)"
echo "Fan Target: $(cat /sys/class/hwmon/hwmon0/fan1_target)"
echo "Fan Power: $(cat /sys/class/hwmon/hwmon0/power1_input) µW"
```
```
Fan RPM: 2
Fan Target: 3
Fan Power: 5000 µW
```

> **Note**: The fan RPM values (2/3) seem unusual. These may be ACPI fan states
> (0=off, 1=low, 2=medium, 3=high) rather than actual RPM readings. The DGX Spark
> likely uses firmware-controlled fan curves that aren't exposed as standard RPM values.

### 4. NVMe SSD Temperature — hwmon2

```bash
echo "Composite: $(echo "scale=1; $(cat /sys/class/hwmon/hwmon2/temp1_input) / 1000" | bc)°C"
echo "Critical:  $(echo "scale=1; $(cat /sys/class/hwmon/hwmon2/temp1_crit) / 1000" | bc)°C"
echo "Max:       $(echo "scale=1; $(cat /sys/class/hwmon/hwmon2/temp1_max) / 1000" | bc)°C"
```
```
Composite: 37.8°C
Critical:  84.8°C
Max:       82.8°C
```

### 5. Network Adapter Temps — hwmon3-6 (4× Mellanox ConnectX-7)

```bash
for h in 3 4 5 6; do
  echo "hwmon${h} $(cat /sys/class/hwmon/hwmon${h}/temp1_label): \
$(echo "scale=1; $(cat /sys/class/hwmon/hwmon${h}/temp1_input) / 1000" | bc)°C"
done
```
```
hwmon3 asic: 42.0°C
hwmon4 asic: 42.0°C
hwmon5 asic: 42.0°C
hwmon6 asic: 42.0°C
```

### 6. WiFi Adapter Temp — hwmon7

```bash
echo "WiFi: $(echo "scale=1; $(cat /sys/class/hwmon/hwmon7/temp1_input) / 1000" | bc)°C"
```
```
WiFi: 42.0°C
```

### 7. CPU Frequencies — sysfs cpufreq

```bash
for cpu in /sys/devices/system/cpu/cpu[0-9]*/cpufreq; do
  echo "$(basename $(dirname $cpu)): $(cat $cpu/scaling_cur_freq) kHz"
done
```
```
cpu0:  2808000 kHz  (Cortex-A725, LITTLE)
cpu1:  2808000 kHz  (Cortex-A725, LITTLE)
...
cpu6:  3900000 kHz  (Cortex-X925, big)
cpu7:  3900000 kHz  (Cortex-X925, big)
...
```

---

## Monitoring Script

A full monitoring script has been created at:
- **Local**: `dgx_spark_sensors/dgx_spark_sensor_monitor.sh`
- **Remote**: `/tmp/dgx_spark_sensor_monitor.sh`

### Usage

```bash
# One-shot dashboard
bash dgx_spark_sensor_monitor.sh

# Continuous monitoring (2s refresh)
bash dgx_spark_sensor_monitor.sh --watch

# JSON output (for piping to other tools)
bash dgx_spark_sensor_monitor.sh --json
```

### Sample Dashboard Output

```
╔══════════════════════════════════════════════════════════════╗
║         🖥️  NVIDIA DGX Spark — Sensor Dashboard            ║
║         2026-03-28 10:19:47                               ║
╚══════════════════════════════════════════════════════════════╝

🟢 GPU — NVIDIA GB10 (Blackwell)
────────────────────────────────────────────────────────────
  Temperature:                 38°C
  Avg Power Draw:              3.95 W
  Instantaneous Power Draw:    4.02 W
  GPU Utilization:             0%
  Graphics Clock:              208 MHz (max 2418 MHz)
  Video Clock:                 598 MHz
  Performance State:           P8
  Memory:                      Unified (C2C enabled, 128GB shared)

🔵 CPU — ARM Cortex-X925 + Cortex-A725 (big.LITTLE)
────────────────────────────────────────────────────────────
  X925 (big) Cores:            10 cores @ avg 3.90 GHz
  A725 (LITTLE) Cores:         10 cores @ avg 2.80 GHz
  Total Cores:                 20

🌡️  Thermal Zones (ACPI)
────────────────────────────────────────────────────────────
  thermal_zone0:               40.9°C  (acpitz)
  thermal_zone1:               40.9°C  (acpitz)
  ...

🔧 Hardware Monitors
────────────────────────────────────────────────────────────
  [hwmon0] Fan (acpi_fan)       RPM: 2 (target: 3) | Power: 5.0 mW
  [hwmon1] ACPI Thermal         41.0°C 41.0°C 40.3°C 40.1°C ...
  [hwmon2] NVMe SSD             Composite: 37.8°C (critical: 84.8°C)
  [hwmon3] ConnectX-7 NIC       asic: 42.0°C
  [hwmon4] ConnectX-7 NIC       asic: 42.0°C
  [hwmon5] ConnectX-7 NIC       asic: 42.0°C
  [hwmon6] ConnectX-7 NIC       asic: 42.0°C
  [hwmon7] WiFi (MT7925)        Temp: 42.0°C

💾 Memory (Unified CPU+GPU)
────────────────────────────────────────────────────────────
  Total:                       119Gi
  Used:                        6.5Gi
  Available:                   113Gi
  Swap:                        0B / 15Gi
```

---

## Comparison with macOS "silicon" Tool

| Metric | macOS `silicon` | DGX Spark Equivalent |
|--------|----------------|---------------------|
| CPU per-core power | ✅ Per-cluster watts | ❌ Not exposed (ARM, no RAPL) |
| GPU power | ✅ GPU watts | ✅ `nvidia-smi` avg + instantaneous |
| Memory power | ✅ DRAM watts | ❌ Not exposed |
| Total system power | ✅ Total package | ⚠️ GPU only via nvidia-smi |
| CPU temperature | ✅ Per-core | ⚠️ Board-level only (ACPI zones) |
| GPU temperature | ✅ | ✅ `nvidia-smi` |
| CPU frequency | ✅ Per-core | ✅ Per-core via cpufreq sysfs |
| Fan speed | ✅ RPM | ⚠️ ACPI fan state (not true RPM) |
| NVMe temp | ❌ | ✅ hwmon with thresholds |
| Network adapter temp | ❌ | ✅ 4× ConnectX-7 + WiFi |

### Key Limitations vs. macOS silicon

1. **No per-subsystem power breakdown** — The DGX Spark doesn't expose CPU/memory/IO power consumption separately. Only GPU power is available via nvidia-smi.
2. **No per-core CPU temperature** — Only 7 ACPI board-level thermal zones, not individual core temps.
3. **Fan reporting is coarse** — ACPI fan states rather than actual RPM values.

### What the DGX Spark Does Better

1. **Network monitoring** — 4× ConnectX-7 NIC temperatures (enterprise networking hardware)
2. **NVMe details** — Full thermal thresholds (min/max/critical/alarm)
3. **GPU detail** — Extensive nvidia-smi data (power, clocks, utilization, perf states, throttle reasons)

---

## Success Criteria Checklist

- [x] Successfully connect to the DGX Spark
- [x] Identify at least 3 different sensor sources (found **9** distinct sources!)
- [x] Document working commands for extracting sensor data
- [x] Provide sample output showing real sensor readings
- [x] Create a monitoring script (`dgx_spark_sensor_monitor.sh`)
- [x] Provide JSON output mode for programmatic use
