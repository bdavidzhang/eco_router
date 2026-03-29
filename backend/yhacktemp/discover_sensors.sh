#!/bin/bash
#
# DGX Spark Sensor Discovery Script
# Probes all possible sensor sources on NVIDIA ARM64 systems
#

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

HEADER="========================================"

echo -e "${BLUE}DGX Spark Sensor Discovery${NC}"
echo -e "Date: $(date)"
echo -e "Hostname: $(hostname)"
echo -e "Kernel: $(uname -r)"
echo -e "Architecture: $(uname -m)"
echo ""

# Helper function
check_cmd() {
    if command -v "$1" &> /dev/null; then
        echo -e "${GREEN}✓${NC} $1 available"
        return 0
    else
        echo -e "${RED}✗${NC} $1 not found"
        return 1
    fi
}

# Helper to read file safely
read_file() {
    if [[ -r "$1" ]]; then
        cat "$1" 2>/dev/null || echo "N/A"
    else
        echo "N/A"
    fi
}

echo "$HEADER"
echo "1. SYSTEM OVERVIEW"
echo "$HEADER"

echo -e "\n${YELLOW}CPU Info:${NC}"
cat /proc/cpuinfo 2>/dev/null | head -20 || echo "Cannot read CPU info"

echo -e "\n${YELLOW}Memory:${NC}"
free -h 2>/dev/null || cat /proc/meminfo | head -5

echo -e "\n${YELLOW}Uptime:${NC}"
uptime

echo ""
echo "$HEADER"
echo "2. NVIDIA GPU / TEGRA SENSORS"
echo "$HEADER"

echo -e "\n${YELLOW}nvidia-smi:${NC}"
if check_cmd nvidia-smi; then
    nvidia-smi 2>/dev/null || echo "nvidia-smi failed"
    echo ""
    nvidia-smi --query-gpu=name,temperature.gpu,power.draw,clocks.current.graphics,clocks.current.memory,utilization.gpu,utilization.memory --format=csv 2>/dev/null || echo "Query failed"
else
    echo "Install with: sudo apt install nvidia-utils-*"
fi

echo -e "\n${YELLOW}tegrastats:${NC}"
if check_cmd tegrastats; then
    tegrastats --interval 1000 --count 1 2>/dev/null || echo "tegrastats failed"
else
    echo "tegrastats not available (Jetson-specific tool)"
fi

echo -e "\n${YELLOW}jetson_clocks (status):${NC}"
if command -v jetson_clocks &> /dev/null; then
    jetson_clocks --show 2>/dev/null || echo "jetson_clocks failed"
else
    echo "jetson_clocks not available"
fi

echo ""
echo "$HEADER"
echo "3. THERMAL ZONES (/sys/class/thermal)"
echo "$HEADER"

if [[ -d /sys/class/thermal ]]; then
    echo -e "\n${YELLOW}Available thermal zones:${NC}"
    for zone in /sys/class/thermal/thermal_zone*; do
        if [[ -d "$zone" ]]; then
            zone_name=$(basename "$zone")
            zone_type=$(read_file "$zone/type")
            zone_temp=$(read_file "$zone/temp")
            
            # Convert millidegrees to degrees if needed
            if [[ "$zone_temp" != "N/A" && ${#zone_temp} -gt 3 ]]; then
                zone_temp_c=$(echo "scale=1; $zone_temp / 1000" | bc 2>/dev/null || echo "$zone_temp")
            else
                zone_temp_c="$zone_temp"
            fi
            
            echo "  $zone_name: $zone_type = ${zone_temp_c}°C"
            
            # Show additional info if available
            if [[ -r "$zone/passive" ]]; then
                echo "    Passive temp: $(read_file "$zone/passive")"
            fi
            if [[ -r "$zone/critical" ]]; then
                echo "    Critical temp: $(read_file "$zone/critical")"
            fi
        fi
    done
else
    echo -e "${RED}✗${NC} /sys/class/thermal not available"
fi

echo ""
echo "$HEADER"
echo "4. HARDWARE MONITORING (/sys/class/hwmon)"
echo "$HEADER"

if [[ -d /sys/class/hwmon ]]; then
    for hwmon in /sys/class/hwmon/hwmon*; do
        if [[ -d "$hwmon" ]]; then
            echo -e "\n${YELLOW}$(basename "$hwmon"):${NC}"
            
            # Read name
            if [[ -r "$hwmon/name" ]]; then
                echo "  Device: $(cat "$hwmon/name")"
            fi
            
            # List all sensor inputs
            for sensor in "$hwmon"/*_input; do
                if [[ -r "$sensor" ]]; then
                    sensor_name=$(basename "$sensor" _input)
                    sensor_value=$(cat "$sensor")
                    
                    # Try to get label
                    label_file="$hwmon/${sensor_name}_label"
                    if [[ -r "$label_file" ]]; then
                        label=$(cat "$label_file")
                    else
                        label="$sensor_name"
                    fi
                    
                    echo "  $label: $sensor_value"
                fi
            done
        fi
    done
else
    echo -e "${RED}✗${NC} /sys/class/hwmon not available"
fi

echo ""
echo "$HEADER"
echo "5. lm-sensors"
echo "$HEADER"

if check_cmd sensors; then
    echo -e "\n${YELLOW}Running sensors:${NC}"
    sensors 2>/dev/null || echo "sensors command failed"
    
    echo -e "\n${YELLOW}Detecting sensors (may need sudo):${NC}"
    echo "To configure: sudo sensors-detect"
else
    echo "Install with: sudo apt install lm-sensors"
    echo "Then run: sudo sensors-detect"
fi

echo ""
echo "$HEADER"
echo "6. IPMI SENSORS"
echo "$HEADER"

if check_cmd ipmitool; then
    echo -e "\n${YELLOW}IPMI sensor list:${NC}"
    sudo ipmitool sensor list 2>/dev/null || ipmitool sensor list 2>/dev/null || echo "IPMI access failed (may need sudo or IPMI not available)"
    
    echo -e "\n${YELLOW}IPMI sensor reading:${NC}"
    sudo ipmitool sensor reading 2>/dev/null || ipmitool sensor reading 2>/dev/null || echo "Reading failed"
    
    echo -e "\n${YELLOW}IPMI sdr:${NC}"
    sudo ipmitool sdr 2>/dev/null || ipmitool sdr 2>/dev/null || echo "SDR failed"
else
    echo "Install with: sudo apt install ipmitool"
fi

echo ""
echo "$HEADER"
echo "7. POWER SUPPLY"
echo "$HEADER"

if [[ -d /sys/class/power_supply ]]; then
    echo -e "\n${YELLOW}Power supplies:${NC}"
    for psu in /sys/class/power_supply/*; do
        if [[ -d "$psu" ]]; then
            echo "  $(basename "$psu"):"
            for attr in type status voltage_now current_now power_now energy_now capacity; do
                if [[ -r "$psu/$attr" ]]; then
                    echo "    $attr: $(cat "$psu/$attr")"
                fi
            done
        fi
    done
else
    echo "No power supply info available"
fi

echo ""
echo "$HEADER"
echo "8. NVMe STORAGE SENSORS"
echo "$HEADER"

if check_cmd nvme; then
    echo -e "\n${YELLOW}NVMe devices:${NC}"
    sudo nvme list 2>/dev/null || echo "nvme list failed (may need sudo)"
    
    # Get temperature for each NVMe
    for nvme_dev in /dev/nvme*n1; do
        if [[ -e "$nvme_dev" ]]; then
            echo -e "\n  $(basename "$nvme_dev"):"
            sudo nvme smart-log "$nvme_dev" 2>/dev/null | grep -i temp || echo "    Temperature info not available"
        fi
    done
else
    echo "Install with: sudo apt install nvme-cli"
fi

if check_cmd smartctl; then
    echo -e "\n${YELLOW}SMART data:${NC}"
    for disk in /dev/nvme* /dev/sd*; do
        if [[ -e "$disk" && -r "$disk" ]]; then
            echo "  $disk:"
            sudo smartctl -a "$disk" 2>/dev/null | grep -i temperature || echo "    No temp data"
        fi
    done
else
    echo "Install with: sudo apt install smartmontools"
fi

echo ""
echo "$HEADER"
echo "9. PMIC (Power Management IC)"
echo "$HEADER"

# Check for PMIC in hwmon
for hwmon in /sys/class/hwmon/hwmon*; do
    if [[ -r "$hwmon/name" ]]; then
        name=$(cat "$hwmon/name")
        if [[ "$name" == *"pmic"* || "$name" == *"ina"* || "$name" == *"max"* ]]; then
            echo -e "${YELLOW}PMIC found: $name${NC}"
            ls -la "$hwmon/"
        fi
    fi
done

echo ""
echo "$HEADER"
echo "10. BATTERY (if present)"
echo "$HEADER"

if [[ -d /sys/class/power_supply/BAT0 ]]; then
    for attr in status capacity voltage_now current_now power_now temp; do
        if [[ -r "/sys/class/power_supply/BAT0/$attr" ]]; then
            echo "$attr: $(cat "/sys/class/power_supply/BAT0/$attr")"
        fi
    done
else
    echo "No battery detected"
fi

echo ""
echo "$HEADER"
echo "11. SUMMARY"
echo "$HEADER"

echo ""
echo "Sensor sources found:"
echo "  - Thermal zones: $(ls /sys/class/thermal/thermal_zone* 2>/dev/null | wc -l)"
echo "  - HWMON devices: $(ls /sys/class/hwmon/hwmon* 2>/dev/null | wc -l)"
echo "  - Power supplies: $(ls /sys/class/power_supply/* 2>/dev/null | wc -l)"

echo ""
echo -e "${GREEN}Discovery complete!${NC}"
echo "Run 'bash collect_all.sh' for a full data collection."
