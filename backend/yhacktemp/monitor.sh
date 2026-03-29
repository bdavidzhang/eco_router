#!/bin/bash
#
# Continuous sensor monitor
# Similar to 'watch' but with formatted output
#

INTERVAL=${1:-2}  # Default 2 seconds
CLEAR_SCREEN=${2:-true}

# Colors
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Convert millidegrees to celsius
convert_temp() {
    local temp=$1
    if [[ "$temp" == "N/A" || -z "$temp" || "$temp" == "0" ]]; then
        echo "N/A"
        return
    fi
    
    # Check if it looks like millidegrees
    if [[ ${#temp} -gt 3 && "$temp" != *.* && "$temp" -gt 1000 ]]; then
        echo "scale=1; $temp / 1000" | bc 2>/dev/null || echo "$temp"
    else
        echo "$temp"
    fi
}

# Color temperature
color_temp() {
    local temp=$1
    if [[ "$temp" == "N/A" ]]; then
        echo -e "${NC}N/A${NC}"
        return
    fi
    
    temp_int=$(echo "$temp" | cut -d. -f1)
    
    if [[ "$temp_int" -gt 80 ]]; then
        echo -e "${RED}${temp}°C${NC}"
    elif [[ "$temp_int" -gt 60 ]]; then
        echo -e "${YELLOW}${temp}°C${NC}"
    else
        echo -e "${GREEN}${temp}°C${NC}"
    fi
}

# Draw a bar graph
draw_bar() {
    local value=$1
    local max=$2
    local width=${3:-20}
    
    if [[ "$value" == "N/A" || -z "$value" ]]; then
        printf "[%${width}s]" ""
        return
    fi
    
    local filled=$((width * value / max))
    if [[ $filled -gt $width ]]; then
        filled=$width
    fi
    
    local empty=$((width - filled))
    
    printf "["
    printf "%${filled}s" | tr ' ' '█'
    printf "%${empty}s" | tr ' ' '░'
    printf "]"
}

# Main display function
show_sensors() {
    if [[ "$CLEAR_SCREEN" == "true" ]]; then
        clear
    fi
    
    echo -e "${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║${NC}           DGX Spark Sensor Monitor                      ${BLUE}║${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}"
    echo -e "  ${CYAN}$(date)${NC} | Update: ${INTERVAL}s | Ctrl+C to exit"
    echo ""
    
    # === GPU Section ===
    echo -e "${YELLOW}┌─ GPU ───────────────────────────────────────────────────────┐${NC}"
    
    if command -v nvidia-smi &> /dev/null; then
        gpu_info=$(nvidia-smi --query-gpu=temperature.gpu,power.draw,utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits 2>/dev/null || echo "")
        
        if [[ -n "$gpu_info" ]]; then
            IFS=',' read -r gpu_temp gpu_power gpu_util mem_used mem_total <<< "$gpu_info"
            
            echo -e "  Temperature: $(color_temp $(echo "$gpu_temp" | xargs))"
            echo -e "  Power:       ${gpu_power} W"
            echo -e "  Utilization: $(draw_bar $(echo "$gpu_util" | xargs) 100) ${gpu_util}%"
            mem_pct=$((mem_used * 100 / mem_total))
            echo -e "  Memory:      $(draw_bar $mem_pct 100) ${mem_used}/${mem_total} MB"
        else
            echo "  nvidia-smi query failed"
        fi
    else
        # Try thermal zone for GPU
        gpu_temp="N/A"
        for zone in /sys/class/thermal/thermal_zone*; do
            if [[ -r "$zone/type" ]]; then
                zone_type=$(cat "$zone/type")
                if [[ "$zone_type" == *"GPU"* || "$zone_type" == *"gpu"* ]]; then
                    temp_raw=$(cat "$zone/temp" 2>/dev/null)
                    gpu_temp=$(convert_temp "$temp_raw")
                    break
                fi
            fi
        done
        echo -e "  GPU Temp:    $(color_temp "$gpu_temp")"
    fi
    
    echo ""
    
    # === Thermal Zones ===
    echo -e "${YELLOW}┌─ Thermal Zones ─────────────────────────────────────────────┐${NC}"
    
    for zone in /sys/class/thermal/thermal_zone*; do
        if [[ -d "$zone" && -r "$zone/temp" ]]; then
            zone_type=$(cat "$zone/type" 2>/dev/null | cut -c1-20)
            temp_raw=$(cat "$zone/temp" 2>/dev/null)
            temp=$(convert_temp "$temp_raw")
            
            printf "  %-20s %s\n" "$zone_type" "$(color_temp "$temp")"
        fi
    done
    
    echo ""
    
    # === Power ===
    echo -e "${YELLOW}┌─ Power ─────────────────────────────────────────────────────┐${NC}"
    
    # Check hwmon for power sensors
    for hwmon in /sys/class/hwmon/hwmon*; do
        if [[ -d "$hwmon" ]]; then
            for power in "$hwmon"/power*_input; do
                if [[ -r "$power" ]]; then
                    name=$(basename "$power" _input)
                    value=$(cat "$power")
                    # Convert microwatts to watts
                    watts=$(echo "scale=2; $value / 1000000" | bc 2>/dev/null || echo "N/A")
                    label="Power"
                    if [[ -r "$hwmon/${name}_label" ]]; then
                        label=$(cat "$hwmon/${name}_label")
                    fi
                    printf "  %-20s %s W\n" "$label" "$watts"
                fi
            done
        fi
    done
    
    # NVMe power
    if command -v nvme &> /dev/null; then
        for nvme in /dev/nvme*n1; do
            if [[ -e "$nvme" ]]; then
                nvme_name=$(basename "$nvme")
                temp=$(nvme smart-log "$nvme" 2>/dev/null | grep temperature | awk '{print $3}' || echo "N/A")
                if [[ "$temp" != "N/A" ]]; then
                    printf "  %-20s %s\n" "NVMe $nvme_name" "$(color_temp "$temp")"
                fi
            fi
        done
    fi
    
    echo ""
    
    # === System Load ===
    echo -e "${YELLOW}┌─ System ────────────────────────────────────────────────────┐${NC}"
    
    # Load average
    load=$(cat /proc/loadavg | awk '{print $1}')
    cpus=$(nproc)
    load_pct=$(echo "scale=0; ($load / $cpus) * 100" | bc 2>/dev/null || echo "0")
    echo -e "  Load:        $(draw_bar $load_pct 100) ${load}/${cpus}"
    
    # Memory
    mem_info=$(free | grep Mem)
    mem_total=$(echo "$mem_info" | awk '{print $2}')
    mem_used=$(echo "$mem_info" | awk '{print $3}')
    mem_pct=$((mem_used * 100 / mem_total))
    mem_gb=$((mem_used / 1024 / 1024))
    mem_total_gb=$((mem_total / 1024 / 1024))
    echo -e "  Memory:      $(draw_bar $mem_pct 100) ${mem_gb}/${mem_total_gb} GB"
    
    # Uptime
    uptime_info=$(uptime | awk -F',' '{print $1}' | sed 's/^.*up //')
    echo "  Uptime:      $uptime_info"
    
    echo ""
}

# Main loop
echo "Starting monitor (interval: ${INTERVAL}s)..."
echo "Press Ctrl+C to exit"
sleep 1

while true; do
    show_sensors
    sleep "$INTERVAL"
done
