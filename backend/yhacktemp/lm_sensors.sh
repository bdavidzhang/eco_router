#!/bin/bash
#
# lm-sensors setup and probe script
# Checks for lm-sensors, helps configure it, and displays readings
#

set -e

YELLOW='\033[1;33m'
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

echo "========================================"
echo "lm-sensors Setup and Probe"
echo "========================================"
echo ""

# Check if sensors is installed
echo "Checking for lm-sensors..."
if ! command -v sensors &> /dev/null; then
    echo -e "${RED}lm-sensors not installed${NC}"
    echo ""
    echo "To install:"
    echo "  sudo apt update"
    echo "  sudo apt install lm-sensors"
    echo ""
    echo "After installation, run this script again."
    exit 1
fi

echo -e "${GREEN}✓ lm-sensors is installed${NC}"
echo ""

# Check if sensors are configured
if [[ ! -f /etc/sensors3.conf ]] && [[ ! -f /etc/sensors.conf ]]; then
    echo -e "${YELLOW}⚠ Sensors may not be configured${NC}"
    echo ""
    echo "To configure sensors (this will ask you some questions):"
    echo "  sudo sensors-detect"
    echo ""
    echo "Or auto-configure with:"
    echo "  sudo sensors-detect --auto"
    echo ""
fi

# Show current sensor readings
echo "========================================"
echo "Current Sensor Readings"
echo "========================================"
echo ""

echo "Running: sensors"
sensors 2>/dev/null || {
    echo -e "${RED}sensors command failed${NC}"
    echo ""
    echo "Try running with sudo:"
    echo "  sudo sensors"
}

echo ""
echo "========================================"
echo "Raw Sensor Files"
echo "========================================"
echo ""

# Check /etc/sensors.d for custom configs
if [[ -d /etc/sensors.d ]]; then
    echo "Custom configs in /etc/sensors.d:"
    ls -la /etc/sensors.d/
    echo ""
fi

# List all chips that sensors can see
echo "Available sensor chips:"
sensors --list 2>/dev/null || echo "sensors --list not supported"

echo ""
echo "========================================"
echo "Sensor Bus Detection"
echo "========================================"
echo ""

# Check for I2C buses
if [[ -d /sys/bus/i2c ]]; then
    echo "I2C buses available:"
    ls /sys/bus/i2c/devices/ 2>/dev/null | head -20 || echo "No I2C devices found"
else
    echo "I2C bus not available"
fi

echo ""

# Check for platform drivers
if [[ -d /sys/bus/platform/drivers ]]; then
    echo "Platform drivers:"
    ls /sys/bus/platform/drivers/ 2>/dev/null | grep -E "(hwmon|thermal|temp)" || echo "No obvious hwmon drivers found"
fi

echo ""
echo "========================================"
echo "Hardware Monitoring Drivers"
echo "========================================"
echo ""

# List loaded hwmon drivers
if command -v lsmod &> /dev/null; then
    echo "Loaded hwmon drivers:"
    lsmod | grep -E "(hwmon|thermal|temp|coretemp|k10temp|ina2|jc42|nct|f718|lm75|tmp10|adt|drivetemp)" || echo "No standard hwmon drivers loaded"
    echo ""
fi

# Check for module availability
if [[ -d /lib/modules/$(uname -r)/kernel/drivers/hwmon ]]; then
    echo "Available hwmon drivers:"
    ls /lib/modules/$(uname -r)/kernel/drivers/hwmon/ | head -20
fi

echo ""
echo "========================================"
echo "Debug Info"
echo "========================================"
echo ""

# Run sensors with debug output
if sensors --help 2>&1 | grep -q "debug"; then
    echo "Debug output (first 50 lines):"
    sensors --debug 2>&1 | head -50 || true
else
    echo "Debug flag not supported"
fi

echo ""
echo "========================================"
echo "Summary"
echo "========================================"
echo ""

echo "lm-sensors status:"
echo "  Binary: $(which sensors)"
echo "  Version: $(sensors -v 2>&1 | head -1)"
echo ""
echo "If sensors are not detected:"
echo "  1. Run: sudo sensors-detect"
echo "  2. Follow the prompts to detect chips"
echo "  3. Save the configuration"
echo "  4. Re-run this script"
echo ""
echo "For NVIDIA DGX Spark, sensors may be accessible via:"
echo "  - /sys/class/thermal/ (thermal zones)"
echo "  - /sys/class/hwmon/ (hardware monitoring)"
echo "  - nvidia-smi (GPU sensors)"
