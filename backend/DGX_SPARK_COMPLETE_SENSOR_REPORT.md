# 🖥️ NVIDIA DGX Spark — Complete Sensor & Hardware Report

> **Collected**: 2026-03-28 10:19–10:29 PDT  
> **Machine**: `gx10-f443` (Tailscale `100.98.82.117` / LAN `10.65.68.52`)  
> **Agents**: PiercePuppy 🐶 (live SSH probing) + repo collection scripts  
> **Data sources**: Live `nvidia-smi -q`, sysfs exploration, hwmon enumeration, collected dumps from `sensor_data_20260328_102350/`

---

## Table of Contents

1. [System Identity & Hardware Overview](#1-system-identity--hardware-overview)
2. [CPU — ARM big.LITTLE Architecture](#2-cpu--arm-biglittle-architecture)
3. [GPU — NVIDIA GB10 Blackwell](#3-gpu--nvidia-gb10-blackwell)
4. [Unified Memory — C2C Architecture](#4-unified-memory--c2c-architecture)
5. [Thermal Zones — ACPI Sensor Map](#5-thermal-zones--acpi-sensor-map)
6. [Hardware Monitors (hwmon) — Full Enumeration](#6-hardware-monitors-hwmon--full-enumeration)
7. [Storage — NVMe SSD Sensors](#7-storage--nvme-ssd-sensors)
8. [Networking — ConnectX-7 & WiFi Sensors](#8-networking--connectx-7--wifi-sensors)
9. [Fan & Cooling](#9-fan--cooling)
10. [Sensor Source Audit — What Works, What Doesn't](#10-sensor-source-audit--what-works-what-doesnt)
11. [Command Reference](#11-command-reference)
12. [Comparison with macOS silicon Tool](#12-comparison-with-macos-silicon-tool)
13. [Raw Data Appendices](#13-raw-data-appendices)

---

## 1. System Identity & Hardware Overview

### Hardware Specifications

| Component | Value |
|-----------|-------|
| **Product** | NVIDIA DGX Spark |
| **Hostname** | `gx10-f443` |
| **Architecture** | `aarch64` (ARM 64-bit) |
| **GPU** | NVIDIA GB10 — **Blackwell** architecture |
| **GPU Brand** | NVIDIA RTX |
| **GPU Part Number** | `2E12-275-A1` |
| **GPU UUID** | `GPU-1a775e1b-4f78-f336-8ddb-b08c6d1a1489` |
| **VBIOS** | `9A.0B.0F.00.1D` |
| **CPU** | ARM Cortex-X925 (performance) + Cortex-A725 (efficiency) |
| **CPU Topology** | 1 socket × 10 cores × 1 thread = 20 logical CPUs |
| **CPU Features** | SVE, SVE2, BF16, I8MM, SM3/SM4, AES, SHA-512, BTI, MTE |
| **Memory** | 128 GB unified LPDDR5X (CPU+GPU shared via C2C) |
| **Usable RAM** | 119 GiB (125,442,868 KB) |
| **Swap** | 15 GiB |
| **Storage** | 1 TB NVMe — Phison PS5027-E27T (916 GB formatted, 4% used) |
| **NVMe Model** | `ESL01TBTLCZ-27J2-TYN` |
| **Network** | 4× Mellanox ConnectX-7 (MT2910) + MediaTek MT7925 WiFi |
| **Driver** | NVIDIA `580.126.09` |
| **CUDA** | `13.0` |
| **GSP Firmware** | `580.126.09` |
| **OS** | Ubuntu 24.04.4 LTS "Noble Numbat" |
| **Kernel** | `6.17.0-1008-nvidia` (`PREEMPT_DYNAMIC`) |

### PCI Topology

```
0000:00:00.0 PCI bridge: NVIDIA Corporation Device 22ce (rev 01)
0000:01:00.0 Ethernet controller: Mellanox Technologies MT2910 [ConnectX-7]
0000:01:00.1 Ethernet controller: Mellanox Technologies MT2910 [ConnectX-7]
0002:00:00.0 PCI bridge: NVIDIA Corporation Device 22ce (rev 01)
0002:01:00.0 Ethernet controller: Mellanox Technologies MT2910 [ConnectX-7]
0002:01:00.1 Ethernet controller: Mellanox Technologies MT2910 [ConnectX-7]
0004:00:00.0 PCI bridge: NVIDIA Corporation Device 22ce (rev 01)
0004:01:00.0 Non-Volatile memory controller: Phison PS5027-E27T PCIe4 NVMe (DRAM-less)
0007:00:00.0 PCI bridge: NVIDIA Corporation Device 22d0 (rev 01)
0009:00:00.0 PCI bridge: NVIDIA Corporation Device 22d0 (rev 01)
0009:01:00.0 Network controller: MEDIATEK Corp. Device 7925
000f:00:00.0 PCI bridge: NVIDIA Corporation Device 22d1
000f:01:00.0 VGA compatible controller: NVIDIA Corporation Device 2e12 (rev a1)  ← GPU
```

> **Observation**: 5 NVIDIA PCI bridges, suggesting an NVIDIA-designed SoC/motherboard.
> The GPU sits on domain `000f` — separate from the NIC domain (`0000/0002`) and storage (`0004`).

### Filesystem Layout

```
Filesystem      Size  Used Avail Use% Mounted on
tmpfs            12G  8.3M   12G   1% /run
efivarfs        256K   37K  220K  15% /sys/firmware/efi/efivars
/dev/nvme0n1p2  916G   34G  835G   4% /
tmpfs            60G  4.0K   60G   1% /dev/shm
tmpfs           5.0M  8.0K  5.0M   1% /run/lock
/dev/nvme0n1p1  511M  6.4M  505M   2% /boot/efi
tmpfs            12G  2.6M   12G   1% /run/user/1000
```

### Loaded NVIDIA Kernel Modules

| Module | Size | Used By |
|--------|------|---------|
| `nvidia_uvm` | 1.9 MB | 0 |
| `nvidia_drm` | 135 KB | 16 |
| `nvidia_modeset` | 1.96 MB | 19 (by nvidia_drm) |

### System Load at Collection Time

```
10:23:50 up 59 min,  3 users,  load average: 0.30, 0.25, 0.19
```

---

## 2. CPU — ARM big.LITTLE Architecture

### Core Configuration

The DGX Spark uses ARM's **big.LITTLE** heterogeneous architecture with two distinct core types:

| Cluster | Core Type | Cores | Max Frequency | Observed Frequency | ARM Part ID |
|---------|-----------|-------|---------------|-------------------|-------------|
| **big** | Cortex-X925 | 10 | 3,900 MHz | 3,900 MHz | `0xd85` |
| **LITTLE** | Cortex-A725 | 10 | 2,808 MHz | 2,808 MHz | `0xd87` |
| **Total** | — | **20** | — | — | — |

> **Note**: All cores report `BogoMIPS: 2000.00` and `CPU architecture: 8` (ARMv8-A / ARMv9).

### Per-Core Frequency Snapshot

```
cpu0 :  2.80 GHz  (A725 — LITTLE)
cpu1 :  2.80 GHz  (A725 — LITTLE)
cpu2 :  2.80 GHz  (A725 — LITTLE)
cpu3 :  2.80 GHz  (A725 — LITTLE)
cpu4 :  2.80 GHz  (A725 — LITTLE)
cpu5 :  2.80 GHz  (A725 — LITTLE)
cpu6 :  3.90 GHz  (X925 — big)
cpu7 :  3.90 GHz  (X925 — big)
cpu8 :  3.90 GHz  (X925 — big)
cpu9 :  3.90 GHz  (X925 — big)
cpu10:  3.90 GHz  (X925 — big)
cpu11:  2.80 GHz  (A725 — LITTLE)
cpu12:  2.80 GHz  (A725 — LITTLE)
cpu13:  2.80 GHz  (A725 — LITTLE)
cpu14:  2.80 GHz  (A725 — LITTLE)
cpu15:  3.90 GHz  (X925 — big)
cpu16:  3.90 GHz  (X925 — big)
cpu17:  3.90 GHz  (X925 — big)
cpu18:  3.90 GHz  (X925 — big)
cpu19:  3.90 GHz  (X925 — big)
```

> The big cores (`cpu6-10`, `cpu15-19`) and LITTLE cores (`cpu0-5`, `cpu11-14`) are interleaved
> in the numbering. Frequency boost is reported as **disabled** — these are fixed-clock cores.

### CPU Feature Flags (Partial)

Key ISA extensions present:

| Feature | Description |
|---------|-------------|
| `sve` / `sve2` | Scalable Vector Extension (NVIDIA's AI/ML workloads) |
| `bf16` / `i8mm` | BFloat16 and Int8 matrix multiply (ML inference) |
| `sha512` / `sha3` | Hardware crypto acceleration |
| `sm3` / `sm4` | Chinese national crypto standards |
| `aes` / `pmull` | AES encryption + polynomial multiply |
| `atomics` | LSE atomics (critical for concurrent workloads) |
| `bti` | Branch Target Identification (security) |
| `paca` / `pacg` | Pointer Authentication (ARMv8.3 security) |
| `dit` | Data Independent Timing (side-channel resistance) |
| `flagm2` | Flag manipulation v2 |

### How to Read CPU Frequencies

```bash
# All cores at once
cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq

# Single core
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq

# Frequency range
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_min_freq  # 1378000 (1.378 GHz)
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq  # 3900000 (3.9 GHz)
```

---

## 3. GPU — NVIDIA GB10 Blackwell

### Identity

| Property | Value |
|----------|-------|
| Product Name | NVIDIA GB10 |
| Product Brand | NVIDIA RTX |
| Architecture | **Blackwell** |
| Part Number | 2E12-275-A1 |
| Bus ID | `0000000F:01:00.0` |
| VBIOS | 9A.0B.0F.00.1D |
| UUID | GPU-1a775e1b-4f78-f336-8ddb-b08c6d1a1489 |
| Persistence Mode | Enabled |
| Compute Mode | Default |
| C2C Mode | **Enabled** |
| Addressing Mode | ATS (Address Translation Services) |

### Thermal Readings

| Metric | Value |
|--------|-------|
| **GPU Current Temp** | **39°C** |
| GPU T.Limit Temp (headroom) | 57°C |
| GPU Shutdown T.Limit Temp | -5°C (below current) |
| GPU Slowdown T.Limit Temp | -2°C (below current) |
| GPU Max Operating T.Limit Temp | 0°C |
| Memory Current Temp | N/A (unified memory) |

> The T.Limit values are **offsets from the throttle point**, not absolute temps.
> A "T.Limit Temp" of 57°C means the GPU has 57°C of headroom before throttling begins.

### Power Readings

| Metric | Value |
|--------|-------|
| **Average Power Draw** | **3.92 – 4.19 W** |
| **Instantaneous Power Draw** | **3.92 – 4.51 W** |
| Current Power Limit | N/A |
| Default Power Limit | N/A |
| Min/Max Power Limit | N/A |
| GPU Memory Power | N/A |
| Module Power | N/A |

> At idle, the GB10 sips **~4W** — remarkably efficient for a Blackwell GPU.
> Power limits are reported as N/A, suggesting firmware-managed power envelopes
> rather than user-configurable TDP like discrete GPUs.

### Clock Speeds

| Clock Domain | Current (Idle) | Application Default | Max |
|-------------|---------------|--------------------|----|
| **Graphics** | 208 MHz | 2,418 MHz | **3,003 MHz** |
| **SM** | 208 MHz | — | **3,003 MHz** |
| **Video** | 598 MHz | — | **3,003 MHz** |
| **Memory** | N/A | N/A | N/A |

> The max graphics clock of **3,003 MHz** is aggressive. At idle, the GPU parks at 208 MHz (P8 state).
> Memory clocks report N/A because memory is unified — it's managed by the memory controller, not the GPU.

### Performance State

| Property | Value |
|----------|-------|
| Current State | **P8** (idle) |
| Active Throttle Reasons | None |
| HW Thermal Slowdown | Not Active |
| SW Power Cap | Not Active |
| SW Power Capping Total | 328,215,602 µs (~5.5 min cumulative) |
| HW Thermal Slowdown Total | 0 µs |

### Utilization

| Engine | Utilization |
|--------|------------|
| GPU | 0% |
| Memory | 0% |
| Encoder | 0% |
| Decoder | 0% |
| JPEG | 0% |
| OFA (Optical Flow) | 0% |

### PCIe Link

| Property | Current | Max |
|----------|---------|-----|
| Generation | 1 | 5 |
| Width | 1x | 16x |

> PCIe is running at minimum (Gen1 x1) because the GPU primarily communicates via **C2C**,
> not PCIe. The PCIe link is likely only used for display output and legacy device access.

### Active GPU Processes

| PID | Type | Process | GPU Memory |
|-----|------|---------|------------|
| 3196 | G | `/usr/lib/xorg/Xorg` | 117 MiB |
| 3333 | G | `/usr/bin/gnome-shell` | 147 MiB |
| 4368 | G | Firefox (snap) | 313–317 MiB |
| 5491 | G | `/usr/bin/gnome-control-center` | 31–32 MiB |

> Total GPU memory used by desktop: **~610 MiB** — all graphics (G), no compute (C) workloads running.

### Key nvidia-smi Commands

```bash
# One-liner summary
nvidia-smi

# Full XML-like query
nvidia-smi -q

# CSV query (scriptable)
nvidia-smi --query-gpu=name,temperature.gpu,power.draw,utilization.gpu,clocks.gr \
  --format=csv,noheader,nounits

# Real-time monitoring (continuous, 1-second intervals)
nvidia-smi dmon -d 1

# Single sample
nvidia-smi dmon -c 1

# Process monitoring
nvidia-smi pmon -c 1
```

---

## 4. Unified Memory — C2C Architecture

### What C2C Means

The DGX Spark uses NVIDIA's **Chip-to-Chip (C2C)** interconnect to share all 128 GB of LPDDR5X memory between the ARM CPU and Blackwell GPU. This is architecturally similar to Apple Silicon's unified memory, but using NVIDIA's own coherency protocol.

### Evidence of Unified Memory

| Indicator | Value | Interpretation |
|-----------|-------|----------------|
| `nvidia-smi` FB Memory | N/A | No dedicated VRAM partition |
| `nvidia-smi` BAR1 Memory | N/A | No PCIe memory mapping |
| `GPU C2C Mode` | Enabled | Direct CPU↔GPU memory coherency |
| `Addressing Mode` | ATS | Hardware address translation |
| `PCIe Link` | Gen1 x1 | Not used for data — C2C handles it |
| `free -h` Total | 119 GiB | All 128 GB visible as system RAM |

### Memory Snapshot

```
               total        used        free      shared  buff/cache   available
Mem:           119Gi       6.5-7.0Gi   109Gi      119-122Mi  3.6-3.8Gi  112-113Gi
Swap:           15Gi          0B        15Gi
```

### How to Monitor Memory

```bash
# Human-readable
free -h

# Detailed (includes GPU-allocated pages)
cat /proc/meminfo | head -20

# Per-process GPU memory (via nvidia-smi)
nvidia-smi pmon -c 1
```

---

## 5. Thermal Zones — ACPI Sensor Map

### Overview

The kernel exposes **7 ACPI thermal zones**, all of type `acpitz` (ACPI Thermal Zone). These represent board-level temperature sensors distributed across the SoC/motherboard.

### Readings

| Zone | Type | Temperature | Path |
|------|------|-------------|------|
| `thermal_zone0` | acpitz | 40.9 – 41.8°C | `/sys/class/thermal/thermal_zone0/temp` |
| `thermal_zone1` | acpitz | 40.9 – 41.8°C | `/sys/class/thermal/thermal_zone1/temp` |
| `thermal_zone2` | acpitz | 40.2 – 41.8°C | `/sys/class/thermal/thermal_zone2/temp` |
| `thermal_zone3` | acpitz | 40.1 – 40.9°C | `/sys/class/thermal/thermal_zone3/temp` |
| `thermal_zone4` | acpitz | 40.4 – 41.1°C | `/sys/class/thermal/thermal_zone4/temp` |
| `thermal_zone5` | acpitz | 39.8 – 40.7°C | `/sys/class/thermal/thermal_zone5/temp` |
| `thermal_zone6` | acpitz | 40.0 – 40.9°C | `/sys/class/thermal/thermal_zone6/temp` |

> **Range at idle**: 39.8°C – 41.8°C across all 7 zones  
> **Spread**: ~2°C max delta — very uniform cooling  
> **Values are in millidegrees C** (divide `/sys` values by 1000)

### Interpretation

Without ACPI table documentation, the exact mapping of zones to physical locations is unknown. However:

- **Zone 0–2** run slightly warmer (~41°C) — likely near the SoC/CPU cluster
- **Zone 5** runs coolest (~40°C) — likely near the board edge or an intake vent
- The narrow spread suggests excellent thermal design for an idle desktop workload

### How to Read

```bash
# All zones, one-liner
for tz in /sys/class/thermal/thermal_zone*/; do
  printf "%s: %s = %.1f°C\n" "$(basename $tz)" "$(cat $tz/type)" \
    "$(echo "scale=1; $(cat $tz/temp) / 1000" | bc)"
done

# Single zone
echo "scale=1; $(cat /sys/class/thermal/thermal_zone0/temp) / 1000" | bc
```

---

## 6. Hardware Monitors (hwmon) — Full Enumeration

### Summary

| hwmon | Driver | Device | Sensors Available |
|-------|--------|--------|-------------------|
| `hwmon0` | `acpi_fan` | System Fan | `fan1_input`, `fan1_target`, `power1_input` |
| `hwmon1` | `acpitz` | ACPI Thermal | `temp1` through `temp7` |
| `hwmon2` | `nvme` | NVMe SSD | `temp1` (Composite), `temp2` (Sensor 1), thresholds |
| `hwmon3` | `mlx5` | ConnectX-7 NIC #1 | `temp1` (asic) with crit + highest |
| `hwmon4` | `mlx5` | ConnectX-7 NIC #2 | `temp1` (asic) with crit + highest |
| `hwmon5` | `mlx5` | ConnectX-7 NIC #3 | `temp1` (asic) with crit + highest |
| `hwmon6` | `mlx5` | ConnectX-7 NIC #4 | `temp1` (asic) with crit + highest |
| `hwmon7` | `mt7925_phy0` | WiFi Adapter | `temp1` |

### hwmon0 — System Fan (`acpi_fan`)

| File | Value | Unit | Description |
|------|-------|------|-------------|
| `fan1_input` | 2 | state | Current fan state |
| `fan1_target` | 3 | state | Target fan state |
| `power1_input` | 5000 | µW | Fan controller power draw |

> **Important**: The `fan1_input` / `fan1_target` values are **ACPI fan performance states** (0–3),
> not RPM. State 2 = medium, state 3 = high. The DGX Spark does not expose true RPM to userspace.

```bash
cat /sys/class/hwmon/hwmon0/fan1_input   # Current state
cat /sys/class/hwmon/hwmon0/fan1_target  # Target state
cat /sys/class/hwmon/hwmon0/power1_input # Controller power (µW)
```

### hwmon1 — ACPI Thermal (`acpitz`)

7 temperature sensors, mirrors the thermal zone data:

| Sensor | Sample 1 | Sample 2 | Path |
|--------|----------|----------|------|
| temp1 | 41.0°C | 41.8°C | `hwmon1/temp1_input` |
| temp2 | 41.0°C | 41.8°C | `hwmon1/temp2_input` |
| temp3 | 40.3°C | 41.8°C | `hwmon1/temp3_input` |
| temp4 | 40.1°C | 40.9°C | `hwmon1/temp4_input` |
| temp5 | 40.5°C | 41.1°C | `hwmon1/temp5_input` |
| temp6 | 39.8°C | 40.7°C | `hwmon1/temp6_input` |
| temp7 | 40.3°C | 40.9°C | `hwmon1/temp7_input` |

```bash
# Read all temps
for i in 1 2 3 4 5 6 7; do
  echo "temp${i}: $(echo "scale=1; $(cat /sys/class/hwmon/hwmon1/temp${i}_input) / 1000" | bc)°C"
done
```

### hwmon2 — NVMe SSD

Detailed in [Section 7](#7-storage--nvme-ssd-sensors).

### hwmon3–6 — ConnectX-7 NICs

Detailed in [Section 8](#8-networking--connectx-7--wifi-sensors).

### hwmon7 — WiFi

Detailed in [Section 8](#8-networking--connectx-7--wifi-sensors).

---

## 7. Storage — NVMe SSD Sensors

### Device Info

| Property | Value |
|----------|-------|
| Model | `ESL01TBTLCZ-27J2-TYN` |
| Controller | Phison PS5027-E27T (DRAM-less) |
| Interface | PCIe Gen 4 |
| Capacity | 1 TB (916 GB formatted) |
| Usage | 34 GB used (4%) |

### Temperature Sensors (hwmon2)

| Sensor | Label | Value | Min | Max | Critical | Alarm |
|--------|-------|-------|-----|-----|----------|-------|
| `temp1` | Composite | **37.85°C** | -5.15°C | 82.85°C | **84.85°C** | 0 (OK) |
| `temp2` | Sensor 1 | **37.85°C** | -273.15°C | 65,261.85°C | — | — |

> `temp2_max` of 65,261°C is obviously a sentinel/invalid value — sensor 2
> likely doesn't have calibrated thresholds.

### Available Files

```
/sys/class/hwmon/hwmon2/
├── temp1_alarm      → 0 (no alarm)
├── temp1_crit       → 84850 (84.85°C)
├── temp1_input      → 37850 (37.85°C)
├── temp1_label      → "Composite"
├── temp1_max        → 82850 (82.85°C)
├── temp1_min        → -5150 (-5.15°C)
├── temp2_input      → 37850 (37.85°C)
├── temp2_label      → "Sensor 1"
├── temp2_max        → 65261850
└── temp2_min        → -273150
```

### How to Read

```bash
# Quick composite temp
echo "NVMe: $(echo "scale=1; $(cat /sys/class/hwmon/hwmon2/temp1_input) / 1000" | bc)°C"

# With thresholds
echo "Current:  $(echo "scale=1; $(cat /sys/class/hwmon/hwmon2/temp1_input) / 1000" | bc)°C"
echo "Warning:  $(echo "scale=1; $(cat /sys/class/hwmon/hwmon2/temp1_max) / 1000" | bc)°C"
echo "Critical: $(echo "scale=1; $(cat /sys/class/hwmon/hwmon2/temp1_crit) / 1000" | bc)°C"
echo "Alarm:    $(cat /sys/class/hwmon/hwmon2/temp1_alarm)"
```

---

## 8. Networking — ConnectX-7 & WiFi Sensors

### Mellanox ConnectX-7 (4 ports / 2 dual-port cards)

The system has **4 ConnectX-7 Ethernet controllers** (Mellanox MT2910 family) across two PCI domains:

| hwmon | PCI Address | ASIC Temp | Label | Critical | Highest |
|-------|-------------|-----------|-------|----------|---------|
| `hwmon3` | `0000:01:00.0` | **42.0–43.0°C** | asic | available | available |
| `hwmon4` | `0000:01:00.1` | **42.0–43.0°C** | asic | available | available |
| `hwmon5` | `0002:01:00.0` | **42.0–43.0°C** | asic | available | available |
| `hwmon6` | `0002:01:00.1` | **42.0–43.0°C** | asic | available | available |

#### Available Sensor Files (per NIC)

```
/sys/class/hwmon/hwmon{3,4,5,6}/
├── temp1_crit           → Critical threshold
├── temp1_highest        → Peak temperature since boot
├── temp1_input          → Current ASIC temperature
├── temp1_label          → "asic"
└── temp1_reset_history  → Write to reset peak tracking
```

#### How to Read

```bash
for h in 3 4 5 6; do
  temp=$(echo "scale=1; $(cat /sys/class/hwmon/hwmon${h}/temp1_input) / 1000" | bc)
  label=$(cat /sys/class/hwmon/hwmon${h}/temp1_label)
  echo "ConnectX-7 [hwmon${h}] ${label}: ${temp}°C"
done
```

### MediaTek MT7925 WiFi

| hwmon | Temp | Path |
|-------|------|------|
| `hwmon7` | **42.0°C** | `/sys/class/hwmon/hwmon7/temp1_input` |

```bash
echo "WiFi: $(echo "scale=1; $(cat /sys/class/hwmon/hwmon7/temp1_input) / 1000" | bc)°C"
```

---

## 9. Fan & Cooling

### ACPI Fan Control

The DGX Spark uses firmware-managed fan control exposed through ACPI. The Linux kernel sees it as a simple performance-state interface:

| Property | Value |
|----------|-------|
| Current State | 2 (medium) |
| Target State | 3 (high) |
| Controller Power | 5 mW |
| True RPM | **Not exposed** |

### Fan State Interpretation

| State | Likely Meaning |
|-------|---------------|
| 0 | Off |
| 1 | Low |
| 2 | Medium (current) |
| 3 | High (target) |

> The discrepancy between current (2) and target (3) suggests the fan is ramping up
> or the target is the maximum allowed state, not the commanded speed.

### nvidia-smi Fan Reporting

`nvidia-smi` reports `Fan Speed: N/A` — the GPU does not have its own fan. System-level cooling
is handled by the chassis fan(s) managed by ACPI/firmware, not by the GPU driver.

---

## 10. Sensor Source Audit — What Works, What Doesn't

### ✅ Working (9 sources)

| # | Source | Interface | Data Types |
|---|--------|-----------|------------|
| 1 | `nvidia-smi` | CLI tool | GPU temp, power (avg+instant), clocks, utilization, perf state, throttle reasons, process memory |
| 2 | `/sys/class/thermal/thermal_zone*` | sysfs | 7× board temps (millidegrees C) |
| 3 | `/sys/class/hwmon/hwmon0` | sysfs | Fan state, fan target, fan power |
| 4 | `/sys/class/hwmon/hwmon1` | sysfs | 7× board temps (mirrors thermal zones) |
| 5 | `/sys/class/hwmon/hwmon2` | sysfs | NVMe composite + sensor temp, critical/max thresholds, alarm |
| 6 | `/sys/class/hwmon/hwmon3-6` | sysfs | 4× ConnectX-7 ASIC temps, critical threshold, peak tracking |
| 7 | `/sys/class/hwmon/hwmon7` | sysfs | WiFi adapter temp |
| 8 | `/sys/devices/system/cpu/cpu*/cpufreq` | sysfs | Per-core CPU frequency (real-time) |
| 9 | `/proc/meminfo` + `free` | procfs | Unified memory total/used/available/swap |

### ❌ Not Available (6 sources tested)

| Source | Status | Detail |
|--------|--------|--------|
| `sensors` (lm-sensors) | ❌ Not installed | `sudo apt install lm-sensors` would add it |
| `tegrastats` | ❌ Not found | This is **not** a Jetson. Despite ARM+NVIDIA, it's a full desktop platform |
| `jetson_clocks` | ❌ Not found | Same — Jetson-specific tool |
| `ipmitool` | ⚠️ Installed, no device | Binary exists at `/usr/bin/ipmitool` but `/dev/ipmi0` doesn't exist. **No BMC/IPMI on this platform** |
| RAPL / `powercap` | ❌ N/A | Intel-specific energy counters. This is ARM |
| `/sys/class/power_supply/` | ❌ Empty | Desktop, no battery |
| `powertop` | ❌ Not installed | Could be installed |

### ⚠️ Could Be Enabled

| Source | Effort | What It Would Add |
|--------|--------|-------------------|
| `lm-sensors` | `sudo apt install lm-sensors && sudo sensors-detect` | May discover additional sensors not in hwmon |
| `nvme-cli` | `sudo nvme smart-log /dev/nvme0n1` | SMART data, endurance, power-on hours |
| `smartmontools` | `sudo smartctl -a /dev/nvme0n1` | Full SMART attributes |
| `powertop` | `sudo apt install powertop` | Per-process power estimates |

---

## 11. Command Reference

### One-Liners

```bash
# GPU temp + power + util (CSV, no headers)
nvidia-smi --query-gpu=temperature.gpu,power.draw,utilization.gpu --format=csv,noheader,nounits

# All thermal zones
paste <(cat /sys/class/thermal/thermal_zone*/type) <(cat /sys/class/thermal/thermal_zone*/temp)

# All hwmon names
for h in /sys/class/hwmon/hwmon*/; do echo "$(basename $h): $(cat $h/name)"; done

# NVMe temp with alarm check
echo "$(cat /sys/class/hwmon/hwmon2/temp1_label): $(cat /sys/class/hwmon/hwmon2/temp1_input)m°C alarm=$(cat /sys/class/hwmon/hwmon2/temp1_alarm)"

# CPU frequencies as GHz
awk '{printf "%.2f GHz\n", $1/1000000}' /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq

# Memory summary
free -h | head -2

# Fan state
echo "Fan: state=$(cat /sys/class/hwmon/hwmon0/fan1_input) target=$(cat /sys/class/hwmon/hwmon0/fan1_target)"
```

### Monitoring Scripts

```bash
# Continuous GPU monitoring (nvidia-smi built-in)
nvidia-smi dmon -d 1

# Custom dashboard (if uploaded)
bash /tmp/dgx_spark_sensor_monitor.sh          # one-shot
bash /tmp/dgx_spark_sensor_monitor.sh --watch  # continuous
bash /tmp/dgx_spark_sensor_monitor.sh --json   # machine-readable

# Quick watch with standard tools
watch -n 2 'nvidia-smi; echo; for tz in /sys/class/thermal/thermal_zone*/; do
  echo "$(basename $tz): $(cat $tz/type) $(echo "scale=1;$(cat $tz/temp)/1000"|bc)°C"
done'
```

### JSON Snapshot (from monitoring script)

```json
{
  "timestamp": "2026-03-28T10:19:54-07:00",
  "gpu": {
    "name": "NVIDIA GB10 (Blackwell)",
    "temp_c": 39,
    "power_w": 4.13,
    "utilization_pct": 0,
    "clock_mhz": 208
  },
  "thermal_zones": [
    {"zone": "thermal_zone0", "type": "acpitz", "temp_c": 40.9},
    {"zone": "thermal_zone1", "type": "acpitz", "temp_c": 40.9},
    {"zone": "thermal_zone2", "type": "acpitz", "temp_c": 40.2},
    {"zone": "thermal_zone3", "type": "acpitz", "temp_c": 40.1},
    {"zone": "thermal_zone4", "type": "acpitz", "temp_c": 40.7},
    {"zone": "thermal_zone5", "type": "acpitz", "temp_c": 39.8},
    {"zone": "thermal_zone6", "type": "acpitz", "temp_c": 40.6}
  ],
  "cpu_freqs_khz": [
    2808000, 2808000, 2808000, 2808000, 2808000, 2808000,
    3900000, 3900000, 3900000, 3900000, 3900000,
    2808000, 2808000, 2808000, 2808000,
    3900000, 3900000, 3900000, 3900000, 3900000
  ],
  "memory": {
    "total_kb": 125442868,
    "available_kb": 118629056
  }
}
```

---

## 12. Comparison with macOS `silicon` Tool

### Feature Matrix

| Metric | macOS `silicon` | DGX Spark | How (on DGX Spark) |
|--------|----------------|-----------|---------------------|
| CPU per-core frequency | ✅ | ✅ | `cpufreq/scaling_cur_freq` |
| CPU per-core power | ✅ Per-cluster W | ❌ | Not exposed on ARM (no RAPL) |
| CPU per-core temp | ✅ | ❌ | Only 7 board-level ACPI zones |
| GPU temp | ✅ | ✅ | `nvidia-smi` (39°C) |
| GPU power | ✅ | ✅ | `nvidia-smi` avg + instant (4W idle) |
| GPU utilization | ✅ | ✅ | `nvidia-smi` per-engine (GPU/Enc/Dec/JPEG/OFA) |
| GPU clocks | ✅ | ✅ | Graphics/SM/Video clocks + max headroom |
| GPU throttle reasons | ❌ | ✅ | Full breakdown (thermal/power/sync) |
| Memory power | ✅ DRAM W | ❌ | Not exposed |
| Memory bandwidth | ❌ | ❌ | Neither exposes this directly |
| Total system power | ✅ Package W | ⚠️ | GPU-only; no CPU/IO/DRAM power |
| Fan RPM | ✅ True RPM | ⚠️ | ACPI states only (0-3) |
| NVMe temp | ❌ | ✅ | Composite + thresholds + alarm |
| NIC temp | ❌ | ✅ | 4× ConnectX-7 ASIC temps |
| WiFi temp | ❌ | ✅ | MT7925 temp sensor |
| Process GPU memory | ❌ | ✅ | Per-process VRAM via nvidia-smi |
| Unified memory detail | ✅ | ✅ | Via free/meminfo + nvidia-smi process |

### Where DGX Spark Wins 🏆

1. **GPU detail is unmatched** — nvidia-smi provides avg+instant power, 6 utilization engines, 4 clock domains, throttle reasons with cumulative counters, thermal headroom, and per-process memory
2. **Enterprise NIC monitoring** — 4× ConnectX-7 temps with critical thresholds and peak tracking
3. **NVMe SSD monitoring** — Full thermal envelope (min/max/critical/alarm)
4. **Throttle forensics** — Cumulative counters tell you exactly *how long* the GPU has been power-capped (328s total since boot)

### Where macOS `silicon` Wins 🏆

1. **Per-subsystem power** — CPU cluster, GPU, DRAM, Neural Engine all broken out in watts
2. **Per-core CPU temp** — Individual die temperatures
3. **True fan RPM** — Actual rotational speed, not ACPI states
4. **Total package power** — Single number for "how much is this machine eating from the wall"

### The Architectural Similarity

Both Apple Silicon and DGX Spark use **unified memory** with CPU and GPU sharing the same physical RAM pool. Both use a custom chip-to-chip interconnect (Apple's Fabric vs NVIDIA's C2C). Both report `N/A` for dedicated GPU VRAM. This is the future of heterogeneous computing.

---

## 13. Raw Data Appendices

### A. Full `nvidia-smi` Summary

```
+-----------------------------------------------------------------------------------------+
| NVIDIA-SMI 580.126.09             Driver Version: 580.126.09     CUDA Version: 13.0     |
+-----------------------------------------+------------------------+----------------------+
| GPU  Name                 Persistence-M | Bus-Id          Disp.A | Volatile Uncorr. ECC |
| Fan  Temp   Perf          Pwr:Usage/Cap |           Memory-Usage | GPU-Util  Compute M. |
|                                         |                        |               MIG M. |
|=========================================+========================+======================|
|   0  NVIDIA GB10                    On  |   0000000F:01:00.0 Off |                  N/A |
| N/A   39C    P8              4W /  N/A  | Not Supported          |      0%      Default |
|                                         |                        |                  N/A |
+-----------------------------------------+------------------------+----------------------+

+-----------------------------------------------------------------------------------------+
| Processes:                                                                              |
|  GPU   GI   CI              PID   Type   Process name                        GPU Memory |
|        ID   ID                                                               Usage      |
|=========================================================================================|
|    0   N/A  N/A            3196      G   /usr/lib/xorg/Xorg                      117MiB |
|    0   N/A  N/A            3333      G   /usr/bin/gnome-shell                    147MiB |
|    0   N/A  N/A            4368      G   .../7965/usr/lib/firefox/firefox        313MiB |
|    0   N/A  N/A            5491      G   /usr/bin/gnome-control-center            31MiB |
+-----------------------------------------------------------------------------------------+
```

### B. `nvidia-smi dmon` Sample

```
# gpu    pwr  gtemp  mtemp     sm    mem    enc    dec    jpg    ofa   mclk   pclk
# Idx      W      C      C      %      %      %      %      %      %    MHz    MHz
    0      3     38      -      0      0      0      0      0      0      -    208
```

### C. CPU Info (first core)

```
processor       : 0
BogoMIPS        : 2000.00
Features        : fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics fphp asimdhp
                  cpuid asimdrdm jscvt fcma lrcpc dcpop sha3 sm3 sm4 asimddp sha512
                  sve asimdfhm dit uscat ilrcpc flagm sb paca pacg dcpodp sve2 sveaes
                  svepmull svebitperm svesha3 svesm4 flagm2 frint svei8mm svebf16
                  i8mm bf16 dgh bti ecv afp wfxt
CPU implementer : 0x41
CPU architecture: 8
CPU variant     : 0x0
CPU part        : 0xd87
CPU revision    : 1
```

### D. Collected Data Files

The following files were collected from the DGX Spark at `2026-03-28 10:23:50`:

```
sensor_data_20260328_102350/
├── discover_sensors.txt   (6.3 KB)  — Full system probe
├── nvidia_sensors.txt     (49 KB)   — GPU data + pmon samples
└── system_info.txt        (3.6 KB)  — CPU, memory, disk, modules
```

### E. Collection Scripts Available

The `yhacktemp/` directory contains a full sensor toolkit:

| Script | Purpose |
|--------|---------|
| `discover_sensors.sh` | Full system probe — runs everything |
| `nvidia_sensors.sh` | NVIDIA GPU stats |
| `thermal_zones.sh` | Linux thermal zones |
| `hwmon_sensors.sh` | Hardware monitoring |
| `ipmi_sensors.sh` | IPMI/BMC sensors |
| `lm_sensors.sh` | lm-sensors setup and readings |
| `collect_all.sh` | Collects everything + raw sysfs dumps |
| `monitor.sh` | Live dashboard (continuous refresh) |

Additionally, `dgx_spark_sensors/dgx_spark_sensor_monitor.sh` provides a polished dashboard with `--watch` and `--json` modes.

---

*Report generated by PiercePuppy 🐶 — a sassy, open-source AI code agent.*
