#!/bin/bash
#
# Thermal Zone Monitor for Linux
# Reads all thermal sensors from /sys/class/thermal
#

set -e

# Colors for output
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

# Convert millidegrees to celsius
convert_temp() {
    local temp=$1
    if [[ "$temp" == "N/A" || -z "$temp" ]]; then
        echo "N/A"
        return
    fi
    
    # Check if it looks like millidegrees (more than 3 digits and no decimal)
    if [[ ${#temp} -gt 3 && "$temp" != *.* ]]; then
        echo "scale=1; $temp / 1000" | bc 2>/dev/null || echo "$temp"
    else
        echo "$temp"
    fi
}

echo "=========================================="
echo "Linux Thermal Zone Monitor"
echo "=========================================="
echo "Timestamp: $(date)"
echo ""

if [[ ! -d /sys/class/thermal ]]; then
    echo "Error: /sys/class/thermal not found"
    exit 1
fi

echo "Thermal Zones Overview:"
echo "------------------------------------------"

# Header
printf "%-15s %-20s %10s %10s %10s %10s\n" "ZONE" "TYPE" "TEMP(°C)" "PASSIVE" "CRITICAL" "STATE"
echo "------------------------------------------"

for zone in /sys/class/thermal/thermal_zone*; do
    if [[ -d "$zone" ]]; then
        zone_num=$(basename "$zone" | sed 's/thermal_zone//')
        zone_type="N/A"
        zone_temp="N/A"
        zone_passive="N/A"
        zone_critical="N/A"
        zone_state="N/A"
        
        # Read type
        if [[ -r "$zone/type" ]]; then
            zone_type=$(cat "$zone/type" 2>/dev/null | tr -d '\n')
            # Truncate long types
            if [[ ${#zone_type} -gt 18 ]]; then
                zone_type="${zone_type:0:15}..."
            fi
        fi
        
        # Read temperature
        if [[ -r "$zone/temp" ]]; then
            zone_temp_raw=$(cat "$zone/temp" 2>/dev/null)
            zone_temp=$(convert_temp "$zone_temp_raw")
        fi
        
        # Read passive threshold
        if [[ -r "$zone/passive" ]]; then
            passive_raw=$(cat "$zone/passive" 2>/dev/null)
            zone_passive=$(convert_temp "$passive_raw")
        fi
        
        # Read critical threshold
        if [[ -r "$zone/critical" ]]; then
            critical_raw=$(cat "$zone/critical" 2>/dev/null)
            zone_critical=$(convert_temp "$critical_raw")
        fi
        
        # Read state
        if [[ -r "$zone/state" ]]; then
            zone_state=$(cat "$zone/state" 2>/dev/null | tr -d '\n')
        fi
        
        # Color code temperature
        temp_display="$zone_temp"
        if [[ "$zone_temp" != "N/A" ]]; then
            # Extract numeric part for comparison
            temp_num=$(echo "$zone_temp" | cut -d. -f1)
            if [[ "$temp_num" -gt 80 ]]; then
                temp_display="${RED}${zone_temp}${NC}"
            elif [[ "$temp_num" -gt 60 ]]; then
                temp_display="${YELLOW}${zone_temp}${NC}"
            else
                temp_display="${GREEN}${zone_temp}${NC}"
            fi
        fi
        
        printf "%-15s %-20s %10s %10s %10s %10s\n" \
            "zone$zone_num" \
            "$zone_type" \
            "$temp_display" \
            "$zone_passive" \
            "$zone_critical" \
            "$zone_state"
    fi
done

echo ""
echo "=========================================="
echo "Detailed Zone Information"
echo "=========================================="

for zone in /sys/class/thermal/thermal_zone*; do
    if [[ -d "$zone" ]]; then
        zone_name=$(basename "$zone")
        echo ""
        echo "--- $zone_name ---"
        
        # List all readable files in the zone
        for attr in type temp passive critical state policy available_policies; do
            if [[ -r "$zone/$attr" ]]; then
                value=$(cat "$zone/$attr" 2>/dev/null | head -c 100)
                
                # Convert temperatures
                if [[ "$attr" == "temp" || "$attr" == "passive" || "$attr" == "critical" ]]; then
                    value="$(convert_temp "$value")°C"
                fi
                
                echo "  $attr: $value"
            fi
        done
        
        # Check for trip points
        if ls "$zone"/trip_point_*_temp 1>/dev/null 2>&1; then
            echo "  Trip points:"
            for trip in "$zone"/trip_point_*_temp; do
                if [[ -r "$trip" ]]; then
                    trip_name=$(basename "$trip" | sed 's/_temp$//')
                    trip_type=$(cat "${trip/_temp/_type}" 2>/dev/null || echo "unknown")
                    trip_temp=$(convert_temp "$(cat "$trip")")
                    echo "    $trip_name ($trip_type): ${trip_temp}°C"
                fi
            done
        fi
        
        # Check for cooling devices
        if [[ -d "$zone/cooling_device" ]]; then
            echo "  Cooling devices:"
            ls -la "$zone/cooling_device/"
        fi
        
        # Check for cdev (cooling device) links
        if ls "$zone"/cdev* 1>/dev/null 2>&1; then
            echo "  Cooling device links:"
            ls -la "$zone"/cdev* 2>/dev/null || true
        fi
    fi
done

echo ""
echo "=========================================="
echo "Thermal Policy"
echo "=========================================="

if [[ -r /sys/class/thermal/thermal_zone0/policy ]]; then
    echo "Current policy: $(cat /sys/class/thermal/thermal_zone0/policy)"
fi

if [[ -r /sys/class/thermal/thermal_zone0/available_policies ]]; then
    echo "Available policies: $(cat /sys/class/thermal/thermal_zone0/available_policies)"
fi

echo ""
echo "Done!"
