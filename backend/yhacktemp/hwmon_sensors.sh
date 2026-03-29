#!/bin/bash
#
# Hardware Monitoring (hwmon) Sensor Reader
# Reads from /sys/class/hwmon/*
#

set -e

# Colors
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
NC='\033[0m'

echo "========================================"
echo "Hardware Monitoring Sensors (hwmon)"
echo "========================================"
echo "Timestamp: $(date)"
echo ""

if [[ ! -d /sys/class/hwmon ]]; then
    echo "Error: /sys/class/hwmon not found"
    exit 1
fi

echo "Available HWMON devices:"
echo "========================================"

# List all hwmon devices with their names
for hwmon in /sys/class/hwmon/hwmon*; do
    if [[ -d "$hwmon" ]]; then
        hwmon_name=$(basename "$hwmon")
        device_name="unknown"
        
        if [[ -r "$hwmon/name" ]]; then
            device_name=$(cat "$hwmon/name")
        fi
        
        echo "  $hwmon_name: $device_name"
    fi
done

echo ""
echo "========================================"
echo "Detailed Sensor Readings"
echo "========================================"

for hwmon in /sys/class/hwmon/hwmon*; do
    if [[ -d "$hwmon" ]]; then
        hwmon_name=$(basename "$hwmon")
        device_name="unknown"
        
        if [[ -r "$hwmon/name" ]]; then
            device_name=$(cat "$hwmon/name")
        fi
        
        echo ""
        echo -e "${YELLOW}>>> $hwmon_name: $device_name${NC}"
        echo "----------------------------------------"
        
        # Read all *_input files (actual sensor values)
        for sensor in "$hwmon"/*_input; do
            if [[ -r "$sensor" ]]; then
                sensor_file=$(basename "$sensor")
                sensor_base="${sensor_file%_input}"
                
                # Get the value
                value=$(cat "$sensor")
                
                # Try to get label
                label="$sensor_base"
                if [[ -r "$hwmon/${sensor_base}_label" ]]; then
                    label=$(cat "$hwmon/${sensor_base}_label")
                fi
                
                # Try to get unit
                unit=""
                if [[ -r "$hwmon/${sensor_base}_unit" ]]; then
                    unit=$(cat "$hwmon/${sensor_base}_unit")
                fi
                
                # Determine sensor type and format
                display_value="$value"
                
                if [[ "$sensor_base" == temp* ]]; then
                    # Temperature - usually millidegrees
                    if [[ ${#value} -gt 3 && "$value" != *.* ]]; then
                        display_value=$(echo "scale=1; $value / 1000" | bc 2>/dev/null || echo "$value")
                    fi
                    unit="°C"
                    
                elif [[ "$sensor_base" == in* ]]; then
                    # Voltage - usually millivolts
                    if [[ ${#value} -gt 3 && "$value" != *.* ]]; then
                        display_value=$(echo "scale=3; $value / 1000" | bc 2>/dev/null || echo "$value")
                        unit="V"
                    fi
                    
                elif [[ "$sensor_base" == curr* ]]; then
                    # Current - usually milliamps
                    if [[ ${#value} -gt 3 && "$value" != *.* ]]; then
                        display_value=$(echo "scale=3; $value / 1000" | bc 2>/dev/null || echo "$value")
                        unit="A"
                    fi
                    
                elif [[ "$sensor_base" == power* ]]; then
                    # Power - usually microwatts
                    if [[ ${#value} -gt 6 && "$value" != *.* ]]; then
                        display_value=$(echo "scale=2; $value / 1000000" | bc 2>/dev/null || echo "$value")
                        unit="W"
                    elif [[ ${#value} -gt 3 && "$value" != *.* ]]; then
                        display_value=$(echo "scale=3; $value / 1000" | bc 2>/dev/null || echo "$value")
                        unit="mW"
                    fi
                    
                elif [[ "$sensor_base" == fan* || "$sensor_base" == pwm* ]]; then
                    unit="RPM"
                    
                elif [[ "$sensor_base" == energy* ]]; then
                    # Energy - usually microjoules
                    if [[ ${#value} -gt 6 && "$value" != *.* ]]; then
                        display_value=$(echo "scale=2; $value / 1000000" | bc 2>/dev/null || echo "$value")
                        unit="J"
                    fi
                fi
                
                echo "  $label: $display_value $unit"
                
                # Show min/max/highest if available
                extras=""
                if [[ -r "$hwmon/${sensor_base}_min" ]]; then
                    extras="min:$(cat "$hwmon/${sensor_base}_min")"
                fi
                if [[ -r "$hwmon/${sensor_base}_max" ]]; then
                    extras="$extras max:$(cat "$hwmon/${sensor_base}_max")"
                fi
                if [[ -r "$hwmon/${sensor_base}_highest" ]]; then
                    highest=$(cat "$hwmon/${sensor_base}_highest")
                    extras="$extras highest:$highest"
                fi
                if [[ -r "$hwmon/${sensor_base}_lowest" ]]; then
                    lowest=$(cat "$hwmon/${sensor_base}_lowest")
                    extras="$extras lowest:$lowest"
                fi
                if [[ -n "$extras" ]]; then
                    echo "    ($extras)"
                fi
            fi
        done
        
        # Show alarms if any
        for alarm in "$hwmon"/*_alarm; do
            if [[ -r "$alarm" ]]; then
                alarm_val=$(cat "$alarm")
                if [[ "$alarm_val" != "0" ]]; then
                    alarm_name=$(basename "$alarm" _alarm)
                    echo -e "  ${GREEN}ALARM:${NC} $alarm_name = $alarm_val"
                fi
            fi
        done
        
        # Show other interesting files
        echo ""
        echo "  Other attributes:"
        for attr in "$hwmon"/*; do
            if [[ -f "$attr" && ! "$attr" =~ _input$ && ! "$attr" =~ _label$ && ! "$attr" =~ _min$ && ! "$attr" =~ _max$ && ! "$attr" =~ _alarm$ ]]; then
                attr_name=$(basename "$attr")
                # Skip binary files and large files
                if [[ -r "$attr" && ! -d "$attr" ]]; then
                    size=$(stat -c%s "$attr" 2>/dev/null || stat -f%z "$attr" 2>/dev/null || echo "0")
                    if [[ "$size" -lt 1000 ]]; then
                        value=$(cat "$attr" 2>/dev/null | head -c 100)
                        echo "    $attr_name: $value"
                    fi
                fi
            fi
        done
    fi
done

echo ""
echo "========================================"
echo "HWMON Summary"
echo "========================================"

# Count sensors by type
temp_count=$(find /sys/class/hwmon -name "temp*_input" 2>/dev/null | wc -l)
in_count=$(find /sys/class/hwmon -name "in*_input" 2>/dev/null | wc -l)
curr_count=$(find /sys/class/hwmon -name "curr*_input" 2>/dev/null | wc -l)
power_count=$(find /sys/class/hwmon -name "power*_input" 2>/dev/null | wc -l)
fan_count=$(find /sys/class/hwmon -name "fan*_input" 2>/dev/null | wc -l)
energy_count=$(find /sys/class/hwmon -name "energy*_input" 2>/dev/null | wc -l)

echo "Sensor counts:"
echo "  Temperature: $temp_count"
echo "  Voltage:     $in_count"
echo "  Current:     $curr_count"
echo "  Power:       $power_count"
echo "  Fan:         $fan_count"
echo "  Energy:      $energy_count"

echo ""
echo "Done!"
