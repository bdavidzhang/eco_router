#!/usr/bin/env python3
"""Extract sensor aggregate data from DGX Spark runs into sensor_data.json.

Reads all sensor_log.csv files from the runs directory and produces
aggregate statistics matching the sensor analysis in analyze_all.py.

Usage:
    python extract_sensor_data.py                              # default
    python extract_sensor_data.py --runs-dir /path/to/runs     # custom path
    python extract_sensor_data.py --output sensor_data.json    # custom output
"""

import argparse
import csv
import json
import statistics
from datetime import datetime
from pathlib import Path


def safe_float(val):
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def compute_stats(values):
    if not values:
        return None
    values = sorted(values)
    n = len(values)
    return {
        "min": round(values[0], 2),
        "mean": round(statistics.mean(values), 2),
        "median": round(statistics.median(values), 2),
        "p95": round(values[int(n * 0.95)] if n >= 2 else values[-1], 2),
        "max": round(values[-1], 2),
        "count": n,
    }


def col_stats(all_rows, col):
    vals = [v for r in all_rows if (v := safe_float(r.get(col))) is not None]
    return compute_stats(vals)


def main():
    parser = argparse.ArgumentParser(description="Extract sensor aggregates to JSON.")
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent
        / "yhacktemp"
        / "yhacktemp"
        / "autoresearch-yaledgx"
        / "runs",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent / "sensor_data.json",
    )
    args = parser.parse_args()

    all_rows = []
    per_run = []

    for run_dir in sorted(args.runs_dir.glob("run_*")):
        sensor_path = run_dir / "sensor_log.csv"
        if not sensor_path.exists():
            continue
        rows = []
        with sensor_path.open() as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
        if not rows:
            continue
        all_rows.extend(rows)

        gpu_pow_vals = [v for r in rows if (v := safe_float(r.get("gpu_power_w"))) is not None]
        gpu_temp_vals = [v for r in rows if (v := safe_float(r.get("gpu_temp_c"))) is not None]
        gpu_util_vals = [v for r in rows if (v := safe_float(r.get("gpu_util_pct"))) is not None]
        load_vals = [v for r in rows if (v := safe_float(r.get("load_avg_1m"))) is not None]
        mem_vals = [v for r in rows if (v := safe_float(r.get("mem_used_kb"))) is not None]

        duration = None
        if len(rows) >= 2:
            try:
                t0 = datetime.fromisoformat(rows[0]["timestamp"])
                t1 = datetime.fromisoformat(rows[-1]["timestamp"])
                duration = int((t1 - t0).total_seconds())
            except (KeyError, ValueError):
                pass

        per_run.append(
            {
                "name": run_dir.name,
                "samples": len(rows),
                "duration_sec": duration,
                "gpu_power_avg_w": round(statistics.mean(gpu_pow_vals), 1) if gpu_pow_vals else None,
                "gpu_temp_max_c": round(max(gpu_temp_vals), 0) if gpu_temp_vals else None,
                "gpu_util_max_pct": round(max(gpu_util_vals), 0) if gpu_util_vals else None,
                "load_avg_mean": round(statistics.mean(load_vals), 1) if load_vals else None,
                "mem_used_max_gb": round(max(mem_vals) / 1024 / 1024, 1) if mem_vals else None,
            }
        )

    # GPU Profile
    gpu_profile = {}
    for col, label in [
        ("gpu_temp_c", "Temperature (\u00b0C)"),
        ("gpu_power_w", "Power avg (W)"),
        ("gpu_power_instant_w", "Power instant (W)"),
        ("gpu_util_pct", "Utilization (%)"),
        ("gpu_mem_util_pct", "Mem util (%)"),
        ("gpu_clock_mhz", "Clock (MHz)"),
        ("gpu_sm_clock_mhz", "SM Clock (MHz)"),
        ("gpu_max_clock_mhz", "Max Clock (MHz)"),
        ("gpu_tlimit_c", "Thermal limit (\u00b0C)"),
    ]:
        s = col_stats(all_rows, col)
        if s:
            gpu_profile[col] = {"label": label, **s}

    # Throttle events
    throttle_events = {}
    for col, label in [
        ("gpu_hw_thermal_throttle", "GPU HW Thermal Throttle"),
        ("gpu_hw_slowdown", "GPU HW Slowdown"),
        ("gpu_sw_power_cap", "GPU SW Power Cap"),
        ("gpu_power_brake", "GPU Power Brake"),
        ("gpu_idle", "GPU Idle Event"),
        ("cpu_throttle_max", "CPU Throttle (max state)"),
        ("nvme_temp_alarm", "NVMe Thermal Alarm"),
    ]:
        vals = [safe_float(r.get(col)) for r in all_rows]
        total = sum(1 for v in vals if v is not None)
        nonzero = sum(1 for v in vals if v is not None and v > 0)
        if total > 0:
            throttle_events[col] = {
                "label": label,
                "total": total,
                "triggered": nonzero,
                "pct": round(nonzero / total * 100, 1),
            }

    # Board thermal
    board_thermal = {}
    sensors = [(f"thermal_zone{i}_c", f"Zone {i}") for i in range(7)]
    sensors += [("nvme_temp_c", "NVMe Composite"), ("nvme_temp2_c", "NVMe Sensor 1")]
    sensors += [(f"nic{i}_temp_c", f"NIC {i}") for i in range(4)]
    sensors += [("wifi_temp_c", "WiFi")]
    for col, label in sensors:
        s = col_stats(all_rows, col)
        if s:
            board_thermal[col] = {"label": label, "mean": s["mean"], "max": s["max"]}

    # Memory profile
    memory_profile = {}
    for col, label, divisor, unit in [
        ("mem_used_kb", "Used", 1024 * 1024, "GB"),
        ("mem_available_kb", "Available", 1024 * 1024, "GB"),
        ("mem_cached_kb", "File Cache", 1024 * 1024, "GB"),
        ("mem_file_hugepages_kb", "HugePages (model)", 1024 * 1024, "GB"),
        ("mem_anon_kb", "Anonymous", 1024 * 1024, "GB"),
        ("mem_dirty_kb", "Dirty", 1024, "MB"),
        ("swap_used_kb", "Swap Used", 1024, "MB"),
    ]:
        s = col_stats(all_rows, col)
        if s:
            memory_profile[col] = {
                "label": label,
                "unit": unit,
                "mean": round(s["mean"] / divisor, 1),
                "max": round(s["max"] / divisor, 1),
            }

    # PSI
    psi_data = {}
    for col, label in [
        ("psi_cpu_avg10", "CPU some"),
        ("psi_mem_some_avg10", "Memory some"),
        ("psi_mem_full_avg10", "Memory full"),
        ("psi_io_some_avg10", "IO some"),
    ]:
        s = col_stats(all_rows, col)
        if s:
            psi_data[col] = {"label": label, "mean": s["mean"], "p95": s["p95"], "max": s["max"]}

    # System
    system_stats = {}
    for col, label in [
        ("cpu_big_avg_mhz", "CPU big avg (MHz)"),
        ("cpu_little_avg_mhz", "CPU little avg (MHz)"),
        ("load_avg_1m", "Load avg (1m)"),
        ("fan_state", "Fan state"),
    ]:
        s = col_stats(all_rows, col)
        if s:
            system_stats[col] = {"label": label, "mean": s["mean"], "max": s["max"]}

    # PCIe
    pcie_gen = col_stats(all_rows, "pcie_gen")
    pcie_width = col_stats(all_rows, "pcie_width")
    pcie_data = None
    if pcie_gen and pcie_width:
        pcie_data = {
            "gen": {"min": pcie_gen["min"], "mean": pcie_gen["mean"], "max": pcie_gen["max"]},
            "width": {"min": pcie_width["min"], "mean": pcie_width["mean"], "max": pcie_width["max"]},
        }

    result = {
        "total_samples": len(all_rows),
        "total_runs": len(per_run),
        "gpu_profile": gpu_profile,
        "throttle_events": throttle_events,
        "board_thermal": board_thermal,
        "memory_profile": memory_profile,
        "psi": psi_data,
        "system": system_stats,
        "pcie": pcie_data,
        "per_run": per_run,
    }

    args.output.write_text(json.dumps(result, indent=2))
    print(f"Wrote {args.output} ({len(all_rows)} samples from {len(per_run)} runs)")


if __name__ == "__main__":
    main()
