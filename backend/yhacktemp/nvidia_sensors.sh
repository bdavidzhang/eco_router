#!/bin/bash
#
# NVIDIA GPU and Tegra-specific sensors
# For DGX Spark / Jetson / NVIDIA ARM64 systems
#

set -e

echo "========================================"
echo "NVIDIA GPU/Tegra Sensor Collector"
echo "========================================"
echo "Timestamp: $(date)"
echo ""

# nvidia-smi queries
if command -v nvidia-smi &> /dev/null; then
    echo "=== nvidia-smi Basic ==="
    nvidia-smi
    echo ""
    
    echo "=== GPU Temperature ==="
    nvidia-smi --query-gpu=temperature.gpu,temperature.memory --format=csv
    echo ""
    
    echo "=== Power Usage ==="
    nvidia-smi --query-gpu=power.draw,power.limit,power.default_limit --format=csv
    echo ""
    
    echo "=== Clocks ==="
    nvidia-smi --query-gpu=clocks.current.graphics,clocks.current.sm,clocks.current.memory,clocks.current.video --format=csv
    echo ""
    
    echo "=== Utilization ==="
    nvidia-smi --query-gpu=utilization.gpu,utilization.memory --format=csv
    echo ""
    
    echo "=== Memory ==="
    nvidia-smi --query-gpu=memory.total,memory.used,memory.free --format=csv
    echo ""
    
    echo "=== PCIe ==="
    nvidia-smi --query-gpu=pcie.link.gen.current,pcie.link.width.current --format=csv
    echo ""
    
    echo "=== Processes ==="
    nvidia-smi pmon -s um 2>/dev/null || nvidia-smi
    echo ""
    
    echo "=== Full Query (all sensors) ==="
    nvidia-smi --query-gpu=timestamp,name,pci.bus_id,driver_version,pstate,pcie.link.gen.max,pcie.link.gen.current,temperature.gpu,temperature.memory,utilization.gpu,utilization.memory,memory.total,memory.free,memory.used,clocks.current.graphics,clocks.current.sm,clocks.current.memory,clocks.current.video,power.draw,power.limit,enforced.power.limit --format=csv
else
    echo "nvidia-smi not found"
    echo "Install: sudo apt install nvidia-utils-*"
fi

echo ""
echo "=== Tegra Stats ==="
if command -v tegrastats &> /dev/null; then
    echo "Running tegrastats (1 second sample)..."
    tegrastats --interval 1000 --count 1
else
    echo "tegrastats not found (Jetson-specific tool)"
fi

echo ""
echo "=== jetson_clocks Status ==="
if command -v jetson_clocks &> /dev/null; then
    jetson_clocks --show
else
    echo "jetson_clocks not found"
fi

echo ""
echo "=== NVPMODEL ==="
if command -v nvpmodel &> /dev/null; then
    echo "Current power model:"
    nvpmodel -q 2>/dev/null || echo "nvpmodel query failed"
else
    echo "nvpmodel not found"
fi

echo ""
echo "=== NVidia Debugfs ==="
if [[ -d /sys/kernel/debug/nvidia ]]; then
    echo "NVIDIA debugfs entries:"
    ls -la /sys/kernel/debug/nvidia/ 2>/dev/null || echo "Cannot list (may need root)"
fi

# Check for GPU thermal zone
for zone in /sys/class/thermal/thermal_zone*; do
    if [[ -r "$zone/type" ]]; then
        zone_type=$(cat "$zone/type")
        if [[ "$zone_type" == *"GPU"* || "$zone_type" == *"gpu"* || "$zone_type" == *"tdp"* ]]; then
            echo ""
            echo "=== GPU Thermal Zone: $zone_type ==="
            echo "Temperature: $(cat "$zone/temp")"
            [[ -r "$zone/passive" ]] && echo "Passive: $(cat "$zone/passive")"
            [[ -r "$zone/critical" ]] && echo "Critical: $(cat "$zone/critical")"
        fi
    fi
done

echo ""
echo "=== GPU HWMON ==="
for hwmon in /sys/class/hwmon/hwmon*; do
    if [[ -r "$hwmon/name" ]]; then
        name=$(cat "$hwmon/name")
        if [[ "$name" == *"nvidia"* || "$name" == *"gpu"* ]]; then
            echo "Device: $name"
            for entry in "$hwmon"/*; do
                if [[ -f "$entry" && -r "$entry" ]]; then
                    echo "  $(basename "$entry"): $(cat "$entry")"
                fi
            done
        fi
    fi
done

echo ""
echo "Done!"
