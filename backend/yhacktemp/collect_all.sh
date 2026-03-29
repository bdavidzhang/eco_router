#!/bin/bash
#
# Comprehensive sensor data collector
# Runs all sensor scripts and aggregates output
#

set -e

OUTPUT_DIR="sensor_data_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUTPUT_DIR"

echo "========================================"
echo "DGX Spark Sensor Data Collection"
echo "========================================"
echo "Output directory: $OUTPUT_DIR"
echo "Started: $(date)"
echo ""

# System info
echo "Collecting system info..."
{
    echo "Hostname: $(hostname)"
    echo "Date: $(date)"
    echo "Uptime: $(uptime)"
    echo "Kernel: $(uname -a)"
    echo ""
    echo "=== /proc/cpuinfo ==="
    head -30 /proc/cpuinfo
    echo ""
    echo "=== Memory ==="
    free -h 2>/dev/null || cat /proc/meminfo | head -10
    echo ""
    echo "=== Disk Usage ==="
    df -h
    echo ""
    echo "=== Loaded Modules ==="
    lsmod 2>/dev/null | head -30 || echo "lsmod not available"
} > "$OUTPUT_DIR/system_info.txt"

# Run all sensor discovery scripts
echo "Running sensor discovery..."
if [[ -x "./discover_sensors.sh" ]]; then
    bash ./discover_sensors.sh > "$OUTPUT_DIR/discover_sensors.txt" 2>&1 || echo "discover_sensors.sh failed" >> "$OUTPUT_DIR/discover_sensors.txt"
fi

echo "Collecting NVIDIA sensors..."
if [[ -x "./nvidia_sensors.sh" ]]; then
    bash ./nvidia_sensors.sh > "$OUTPUT_DIR/nvidia_sensors.txt" 2>&1 || echo "nvidia_sensors.sh failed" >> "$OUTPUT_DIR/nvidia_sensors.txt"
fi

echo "Collecting thermal zone data..."
if [[ -x "./thermal_zones.sh" ]]; then
    bash ./thermal_zones.sh > "$OUTPUT_DIR/thermal_zones.txt" 2>&1 || echo "thermal_zones.sh failed" >> "$OUTPUT_DIR/thermal_zones.txt"
fi

echo "Collecting hwmon data..."
if [[ -x "./hwmon_sensors.sh" ]]; then
    bash ./hwmon_sensors.sh > "$OUTPUT_DIR/hwmon_sensors.txt" 2>&1 || echo "hwmon_sensors.sh failed" >> "$OUTPUT_DIR/hwmon_sensors.txt"
fi

echo "Collecting IPMI data..."
if [[ -x "./ipmi_sensors.sh" ]]; then
    bash ./ipmi_sensors.sh > "$OUTPUT_DIR/ipmi_sensors.txt" 2>&1 || echo "ipmi_sensors.sh failed" >> "$OUTPUT_DIR/ipmi_sensors.txt"
fi

echo "Collecting lm-sensors data..."
if [[ -x "./lm_sensors.sh" ]]; then
    bash ./lm_sensors.sh > "$OUTPUT_DIR/lm_sensors.txt" 2>&1 || echo "lm_sensors.sh failed" >> "$OUTPUT_DIR/lm_sensors.txt"
fi

# Raw sysfs dumps
echo "Dumping raw sysfs data..."

# Thermal zones
echo "  - /sys/class/thermal"
mkdir -p "$OUTPUT_DIR/sysfs/thermal"
for zone in /sys/class/thermal/thermal_zone*; do
    if [[ -d "$zone" ]]; then
        zone_name=$(basename "$zone")
        mkdir -p "$OUTPUT_DIR/sysfs/thermal/$zone_name"
        for f in "$zone"/*; do
            if [[ -f "$f" && -r "$f" ]]; then
                cp "$f" "$OUTPUT_DIR/sysfs/thermal/$zone_name/" 2>/dev/null || true
            fi
        done
    fi
done

# hwmon
echo "  - /sys/class/hwmon"
mkdir -p "$OUTPUT_DIR/sysfs/hwmon"
for hwmon in /sys/class/hwmon/hwmon*; do
    if [[ -d "$hwmon" ]]; then
        hwmon_name=$(basename "$hwmon")
        mkdir -p "$OUTPUT_DIR/sysfs/hwmon/$hwmon_name"
        for f in "$hwmon"/*; do
            if [[ -f "$f" && -r "$f" ]]; then
                cp "$f" "$OUTPUT_DIR/sysfs/hwmon/$hwmon_name/" 2>/dev/null || true
            fi
        done
    fi
done

# Power supply
echo "  - /sys/class/power_supply"
mkdir -p "$OUTPUT_DIR/sysfs/power_supply"
for psu in /sys/class/power_supply/*; do
    if [[ -d "$psu" ]]; then
        psu_name=$(basename "$psu")
        mkdir -p "$OUTPUT_DIR/sysfs/power_supply/$psu_name"
        for f in "$psu"/*; do
            if [[ -f "$f" && -r "$f" ]]; then
                cp "$f" "$OUTPUT_DIR/sysfs/power_supply/$psu_name/" 2>/dev/null || true
            fi
        done
    fi
done

# Create summary
echo "Creating summary..."
{
    echo "DGX Spark Sensor Collection Summary"
    echo "===================================="
    echo "Timestamp: $(date)"
    echo ""
    echo "Files collected:"
    find "$OUTPUT_DIR" -type f | wc -l | xargs echo "  Total files:"
    echo ""
    echo "Directory structure:"
    tree "$OUTPUT_DIR" 2>/dev/null || find "$OUTPUT_DIR" -type f | head -50
    echo ""
    echo "Quick stats:"
    echo "  Thermal zones: $(ls /sys/class/thermal/thermal_zone* 2>/dev/null | wc -l)"
    echo "  HWMON devices: $(ls /sys/class/hwmon/hwmon* 2>/dev/null | wc -l)"
    echo "  Power supplies: $(ls /sys/class/power_supply/* 2>/dev/null | wc -l)"
    echo ""
    
    # Temperature summary
    echo "Current temperatures:"
    for zone in /sys/class/thermal/thermal_zone*; do
        if [[ -r "$zone/temp" && -r "$zone/type" ]]; then
            temp=$(cat "$zone/temp")
            type=$(cat "$zone/type")
            temp_c=$(echo "scale=1; $temp / 1000" | bc 2>/dev/null || echo "$temp")
            echo "  $type: ${temp_c}°C"
        fi
    done
    
} > "$OUTPUT_DIR/SUMMARY.txt"

echo ""
echo "========================================"
echo "Collection Complete!"
echo "========================================"
echo "Output: $OUTPUT_DIR/"
echo "Summary: $OUTPUT_DIR/SUMMARY.txt"
echo "Ended: $(date)"
echo ""
echo "To view:"
echo "  cat $OUTPUT_DIR/SUMMARY.txt"
