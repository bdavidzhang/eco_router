#!/bin/bash
#
# IPMI/BMC Sensor Reader
# For systems with IPMI support (many server-grade systems have this)
#

set -e

YELLOW='\033[1;33m'
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

echo "========================================"
echo "IPMI/BMC Sensor Reader"
echo "========================================"
echo ""

# Check if ipmitool is installed
if ! command -v ipmitool &> /dev/null; then
    echo -e "${RED}ipmitool not installed${NC}"
    echo ""
    echo "To install:"
    echo "  sudo apt update"
    echo "  sudo apt install ipmitool"
    echo ""
    echo "Note: IPMI requires hardware support."
    echo "Many laptops and consumer desktops do not have IPMI."
    exit 1
fi

echo -e "${GREEN}✓ ipmitool is installed${NC}"
echo ""

# Check IPMI availability
echo "Checking IPMI availability..."
echo ""

# Try different IPMI interfaces
interfaces=("open" "imb" "lan" "lanplus")
working_interface=""

for interface in "${interfaces[@]}"; do
    if ipmitool -I "$interface" chassis status &> /dev/null; then
        working_interface="$interface"
        echo -e "${GREEN}✓ IPMI interface '$interface' is working${NC}"
        break
    fi
done

if [[ -z "$working_interface" ]]; then
    echo -e "${YELLOW}⚠ No working IPMI interface found${NC}"
    echo ""
    echo "Common issues:"
    echo "  - IPMI kernel modules not loaded (try: sudo modprobe ipmi_si ipmi_devintf)"
    echo "  - No IPMI hardware present"
    echo "  - Need sudo privileges"
    echo ""
    
    # Try loading modules
    echo "Attempting to load IPMI kernel modules..."
    sudo modprobe ipmi_msghandler 2>/dev/null || true
    sudo modprobe ipmi_si 2>/dev/null || true
    sudo modprobe ipmi_devintf 2>/dev/null || true
    
    # Check if modules loaded
    if lsmod | grep -q ipmi; then
        echo -e "${GREEN}✓ IPMI modules loaded${NC}"
        working_interface="open"
    else
        echo -e "${RED}✗ Failed to load IPMI modules${NC}"
        echo ""
        echo "This system likely does not have IPMI hardware."
        exit 1
    fi
fi

IPMI_CMD="ipmitool -I $working_interface"

echo ""
echo "========================================"
echo "Chassis Status"
echo "========================================"
echo ""

$IPMI_CMD chassis status 2>/dev/null || echo "Failed to get chassis status"

echo ""
echo "========================================"
echo "Sensor List"
echo "========================================"
echo ""

$IPMI_CMD sensor list 2>/dev/null | head -50 || {
    echo "Failed to get sensor list"
    echo "Trying alternative method..."
    $IPMI_CMD sdr list 2>/dev/null | head -50 || echo "SDR list also failed"
}

echo ""
echo "========================================"
echo "Sensor Readings"
echo "========================================"
echo ""

$IPMI_CMD sensor reading 2>/dev/null || {
    echo "sensor reading failed, trying sdr..."
    $IPMI_CMD sdr 2>/dev/null | head -50 || echo "SDR also failed"
}

echo ""
echo "========================================"
echo "Temperature Sensors"
echo "========================================"
echo ""

$IPMI_CMD sensor reading 2>/dev/null | grep -i temp || {
    $IPMI_CMD sdr type Temperature 2>/dev/null || echo "No temperature sensors found"
}

echo ""
echo "========================================"
echo "Fan Sensors"
echo "========================================"
echo ""

$IPMI_CMD sensor reading 2>/dev/null | grep -i fan || {
    $IPMI_CMD sdr type Fan 2>/dev/null || echo "No fan sensors found"
}

echo ""
echo "========================================"
echo "Voltage Sensors"
echo "========================================"
echo ""

$IPMI_CMD sensor reading 2>/dev/null | grep -i volt || {
    $IPMI_CMD sdr type Voltage 2>/dev/null || echo "No voltage sensors found"
}

echo ""
echo "========================================"
echo "Power Sensors"
echo "========================================"
echo ""

$IPMI_CMD sensor reading 2>/dev/null | grep -i power || {
    $IPMI_CMD sdr type "Power Supply" 2>/dev/null || echo "No power sensors found"
}

echo ""
echo "========================================"
echo "FRU (Field Replaceable Unit) Info"
echo "========================================"
echo ""

$IPMI_CMD fru print 2>/dev/null | head -30 || echo "FRU read failed"

echo ""
echo "========================================"
echo "Entity ID Information"
echo "========================================"
echo ""

$IPMI_CMD sdr entity 2>/dev/null | head -30 || echo "Entity ID info not available"

echo ""
echo "========================================"
echo "MC (Management Controller) Info"
echo "========================================"
echo ""

$IPMI_CMD mc info 2>/dev/null || echo "MC info not available"

echo ""
echo "========================================"
echo "Watchdog Status"
echo "========================================"
echo ""

$IPMI_CMD mc watchdog get 2>/dev/null || echo "Watchdog not available"

echo ""
echo "========================================"
echo "Channel Info"
echo "========================================"
echo ""

$IPMI_CMD channel info 2>/dev/null || echo "Channel info not available"

echo ""
echo "========================================"
echo "Summary"
echo "========================================"
echo ""

echo "IPMI is available on this system!"
echo ""
echo "Useful commands:"
echo "  ipmitool sensor list           # All sensors"
echo "  ipmitool sdr list              # Sensor data repository"
echo "  ipmitool chassis status        # Chassis status"
echo "  ipmitool chassis power status  # Power status"
echo "  ipmitool fru print             # Hardware info"
