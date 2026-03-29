#!/usr/bin/env python3
"""
Software Carbon Intensity (SCI) Calculator
Formula: SCI = (E × I) + M  per R

  E  — Energy consumed (kWh), derived from gpu_power_w in sensor log
  I  — Carbon intensity (gCO2/kWh), varies by region / energy source
  M  — Embodied carbon (gCO2), amortised share for the measurement window
  R  — Functional unit (per transaction, per user, per API call, …)
"""

import argparse
import csv
import glob
import os
import sys
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

CARBON_INTENSITY = {
    # Regions (grid average, gCO2/kWh)
    "us_average":       {"label": "US Average",            "value": 386},
    "california":       {"label": "California (CAISO)",    "value": 200},
    "texas":            {"label": "Texas (ERCOT)",          "value": 400},
    "new_york":         {"label": "New York",               "value": 196},
    "pnw":              {"label": "Pacific Northwest",      "value":  88},
    "europe_average":   {"label": "Europe Average",         "value": 275},
    "germany":          {"label": "Germany",                "value": 350},
    "france":           {"label": "France (nuclear-heavy)", "value":  60},
    "uk":               {"label": "United Kingdom",         "value": 233},
    "norway":           {"label": "Norway (hydro-heavy)",   "value":  28},
    "australia":        {"label": "Australia",              "value": 530},
    "china":            {"label": "China",                  "value": 581},
    "india":            {"label": "India",                  "value": 713},
    # Pure energy sources
    "solar":            {"label": "Solar PV",               "value":  41},
    "wind":             {"label": "Wind",                   "value":  11},
    "hydro":            {"label": "Hydro",                  "value":  24},
    "nuclear":          {"label": "Nuclear",                "value":  12},
    "natural_gas":      {"label": "Natural Gas",            "value": 490},
    "coal":             {"label": "Coal",                   "value": 820},
}

# Embodied carbon presets.
# Values represent total lifecycle gCO2 for the device;
# we amortise down to gCO2-per-hour using the assumed lifespan.
HARDWARE_PRESETS = {
    "dgx_a100": {
        "label": "NVIDIA DGX A100 (server)",
        "total_gco2": 3_500_000,
        "lifespan_years": 5,
    },
    "dgx_h100": {
        "label": "NVIDIA DGX H100 (server)",
        "total_gco2": 4_200_000,
        "lifespan_years": 5,
    },
    "rack_server": {
        "label": "Generic rack server",
        "total_gco2": 1_000_000,
        "lifespan_years": 4,
    },
    "workstation": {
        "label": "High-end workstation / desktop",
        "total_gco2":   600_000,
        "lifespan_years": 4,
    },
    "laptop": {
        "label": "Laptop",
        "total_gco2":   300_000,
        "lifespan_years": 4,
    },
    "raspberry_pi": {
        "label": "Raspberry Pi / edge device",
        "total_gco2":    12_500,
        "lifespan_years": 5,
    },
    "custom": {
        "label": "Custom (enter your own values)",
        "total_gco2": None,
        "lifespan_years": None,
    },
}

FUNCTIONAL_UNITS = {
    "transaction": "transaction",
    "api_call":    "API call",
    "user":        "user",
    "request":     "request",
    "image":       "image processed",
    "batch":       "batch job",
    "custom":      "custom unit",
}


# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------

def find_sensor_logs(directory="."):
    """Return all sensor_log_*.csv files in *directory*, sorted newest-first."""
    pattern = os.path.join(directory, "sensor_log_*.csv")
    files = sorted(glob.glob(pattern), reverse=True)
    return files


def load_csv(path):
    rows = []
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rows.append(row)
    return rows


def parse_timestamp(ts_str):
    """Parse ISO-8601 timestamp (with or without timezone offset)."""
    ts_str = ts_str.strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(ts_str, fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse timestamp: {ts_str!r}")


# ---------------------------------------------------------------------------
# Energy calculation  (E)
# ---------------------------------------------------------------------------

def compute_energy_kwh(rows, power_col="gpu_power_w"):
    """
    Integrate power (W) over time (s) using the trapezoidal rule.
    Returns energy in kWh.
    """
    if len(rows) < 2:
        raise ValueError("Need at least 2 data rows to compute energy.")

    timestamps = [parse_timestamp(r["timestamp"]) for r in rows]
    powers_w   = [float(r[power_col]) for r in rows]

    energy_wh = 0.0
    for i in range(1, len(rows)):
        dt_seconds = (timestamps[i] - timestamps[i - 1]).total_seconds()
        avg_power  = (powers_w[i] + powers_w[i - 1]) / 2.0
        energy_wh += avg_power * (dt_seconds / 3600.0)

    duration_seconds = (timestamps[-1] - timestamps[0]).total_seconds()
    return energy_wh / 1000.0, duration_seconds  # kWh, seconds


# ---------------------------------------------------------------------------
# Embodied carbon  (M)
# ---------------------------------------------------------------------------

def compute_embodied_gco2(total_gco2, lifespan_years, duration_seconds):
    """Amortise total embodied carbon to the measurement window."""
    lifespan_seconds = lifespan_years * 365.25 * 24 * 3600
    return total_gco2 * (duration_seconds / lifespan_seconds)


# ---------------------------------------------------------------------------
# Interactive prompts
# ---------------------------------------------------------------------------

def _numbered_menu(title, options_dict):
    """Print a numbered menu and return the chosen key."""
    keys = list(options_dict.keys())
    print(f"\n{title}")
    print("-" * len(title))
    for i, k in enumerate(keys, 1):
        item = options_dict[k]
        if isinstance(item, dict):
            label = item.get("label", k)
            val   = item.get("value")
            extra = f"  ({val} gCO₂/kWh)" if val is not None else ""
        else:
            label = item
            extra = ""
        print(f"  {i:2}. {label}{extra}")
    print(f"  {len(keys)+1:2}. Enter a custom value")

    while True:
        raw = input("\nYour choice: ").strip()
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(keys):
                return keys[idx - 1]
            if idx == len(keys) + 1:
                return "__custom__"
        print("  Invalid choice, try again.")


def prompt_carbon_intensity():
    key = _numbered_menu("Carbon Intensity (I) — choose your region or energy source",
                         CARBON_INTENSITY)
    if key == "__custom__":
        while True:
            raw = input("Enter carbon intensity in gCO₂/kWh: ").strip()
            try:
                return float(raw), "custom"
            except ValueError:
                print("  Please enter a number.")
    info = CARBON_INTENSITY[key]
    print(f"  -> Using {info['label']}: {info['value']} gCO₂/kWh")
    return float(info["value"]), info["label"]


def prompt_hardware():
    key = _numbered_menu("Embodied Carbon (M) — choose your hardware",
                         HARDWARE_PRESETS)
    if key == "__custom__":
        while True:
            raw = input("Total embodied carbon for your hardware (gCO₂): ").strip()
            try:
                total = float(raw)
                break
            except ValueError:
                print("  Please enter a number.")
        while True:
            raw = input("Expected hardware lifespan (years): ").strip()
            try:
                life = float(raw)
                break
            except ValueError:
                print("  Please enter a number.")
        return total, life, "custom hardware"

    preset = HARDWARE_PRESETS[key]
    if preset["total_gco2"] is None:   # shouldn't happen for non-custom
        return prompt_hardware()
    print(f"  -> Using {preset['label']}: "
          f"{preset['total_gco2']:,} gCO₂ over {preset['lifespan_years']} yrs")
    return preset["total_gco2"], preset["lifespan_years"], preset["label"]


def prompt_functional_unit():
    key = _numbered_menu("Functional Unit (R) — normalise SCI per …",
                         FUNCTIONAL_UNITS)
    if key == "__custom__":
        label = input("Describe your functional unit (e.g. 'inference run'): ").strip()
        while True:
            raw = input(f"How many '{label}' occurred during this measurement window? ").strip()
            try:
                return float(raw), label
            except ValueError:
                print("  Please enter a number.")

    label = FUNCTIONAL_UNITS[key]
    while True:
        raw = input(f"How many {label}s occurred during this measurement window? ").strip()
        try:
            return float(raw), label
        except ValueError:
            print("  Please enter a number.")


def prompt_csv_file(default=None):
    logs = find_sensor_logs()
    if not logs and default is None:
        path = input("No sensor_log_*.csv files found. Enter CSV path: ").strip()
        return path

    options = {}
    for p in logs:
        options[p] = {"label": os.path.basename(p)}
    options["__other__"] = {"label": "Enter a different path…"}

    print("\nSensor Log — choose input file")
    print("-------------------------------")
    keys = list(options.keys())
    for i, k in enumerate(keys, 1):
        label = options[k]["label"]
        marker = " (default)" if k == default else ""
        print(f"  {i:2}. {label}{marker}")

    raw = input("\nYour choice [press Enter for default]: ").strip()
    if raw == "" and default:
        print(f"  -> {default}")
        return default
    if raw.isdigit() and 1 <= int(raw) <= len(keys):
        chosen = keys[int(raw) - 1]
        if chosen == "__other__":
            return input("Enter CSV path: ").strip()
        return chosen
    return default or logs[0]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_report(*, csv_path, duration_s, energy_kwh, intensity_gco2_kwh,
                 intensity_label, operational_gco2, hardware_label,
                 embodied_gco2, total_gco2, r_count, r_label, sci):
    duration_min = duration_s / 60
    bar = "=" * 60

    print(f"\n{bar}")
    print("  SOFTWARE CARBON INTENSITY (SCI) REPORT")
    print(bar)
    print(f"  Data source : {os.path.basename(csv_path)}")
    print(f"  Window      : {duration_s:.1f} s  ({duration_min:.2f} min)")
    print()
    print("  ── Energy (E) ──────────────────────────────────────────")
    print(f"  Total energy consumed   : {energy_kwh*1000:.4f} Wh  ({energy_kwh:.6f} kWh)")
    print()
    print("  ── Carbon Intensity (I) ────────────────────────────────")
    print(f"  Source / region         : {intensity_label}")
    print(f"  Intensity               : {intensity_gco2_kwh:.1f} gCO₂/kWh")
    print(f"  Operational emissions   : {operational_gco2:.4f} gCO₂  (E × I)")
    print()
    print("  ── Embodied Carbon (M) ─────────────────────────────────")
    print(f"  Hardware                : {hardware_label}")
    print(f"  Amortised for window    : {embodied_gco2:.4f} gCO₂")
    print()
    print("  ── Total Carbon ────────────────────────────────────────")
    print(f"  (E × I) + M             : {total_gco2:.4f} gCO₂")
    print()
    print("  ── Functional Unit (R) ─────────────────────────────────")
    print(f"  Unit                    : per {r_label}")
    print(f"  Count in window         : {r_count:,.0f}")
    print()
    print("  ┌──────────────────────────────────────────────────────┐")
    print(f"  │  SCI = {sci:.6f} gCO₂ per {r_label:<30}  │")
    print("  └──────────────────────────────────────────────────────┘")
    print(bar)
    print()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        description="Calculate Software Carbon Intensity (SCI = (E×I)+M / R)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Fully interactive
  python sci_calculator.py

  # Specify CSV, use US average grid, generic rack server, 1000 transactions
  python sci_calculator.py --csv sensor_log_20260328_111338.csv \\
      --intensity us_average --hardware rack_server \\
      --transactions 1000 --unit transaction

  # Custom carbon intensity and hardware values
  python sci_calculator.py --intensity-value 250 \\
      --embodied-total 2000000 --embodied-lifespan 4 \\
      --transactions 500
        """,
    )
    p.add_argument("--csv", metavar="PATH",
                   help="Path to sensor log CSV (default: auto-detect latest sensor_log_*.csv)")
    p.add_argument("--power-col", default="gpu_power_w", metavar="COL",
                   help="Column name for power readings in Watts (default: gpu_power_w)")

    gi = p.add_argument_group("Carbon Intensity (I)")
    gi.add_argument("--intensity", choices=list(CARBON_INTENSITY.keys()), metavar="KEY",
                    help="Preset region/source key (use --list-presets to see all)")
    gi.add_argument("--intensity-value", type=float, metavar="GRAMS",
                    help="Custom carbon intensity in gCO₂/kWh (overrides --intensity)")

    gm = p.add_argument_group("Embodied Carbon (M)")
    gm.add_argument("--hardware", choices=list(k for k in HARDWARE_PRESETS if k != "custom"),
                    metavar="KEY", help="Hardware preset key")
    gm.add_argument("--embodied-total", type=float, metavar="GRAMS",
                    help="Total lifecycle embodied carbon in gCO₂ (custom override)")
    gm.add_argument("--embodied-lifespan", type=float, metavar="YEARS",
                    help="Hardware lifespan in years (used with --embodied-total)")

    gr = p.add_argument_group("Functional Unit (R)")
    gr.add_argument("--transactions", type=float, metavar="N",
                    help="Number of functional units in the measurement window")
    gr.add_argument("--unit", default="transaction", metavar="LABEL",
                    help="Label for the functional unit (default: transaction)")

    p.add_argument("--list-presets", action="store_true",
                   help="List all available presets and exit")
    return p


def list_presets():
    print("\n── Carbon Intensity presets (--intensity KEY) ──────────────")
    for k, v in CARBON_INTENSITY.items():
        print(f"  {k:<20} {v['label']:<35} {v['value']:>5} gCO₂/kWh")
    print("\n── Hardware presets (--hardware KEY) ───────────────────────")
    for k, v in HARDWARE_PRESETS.items():
        if k == "custom":
            continue
        print(f"  {k:<20} {v['label']:<40} {v['total_gco2']:>10,} gCO₂ / {v['lifespan_years']} yrs")
    print()


def main():
    parser = build_parser()
    args   = parser.parse_args()

    if args.list_presets:
        list_presets()
        sys.exit(0)

    print("\n╔══════════════════════════════════════════════════════════╗")
    print("║        Software Carbon Intensity (SCI) Calculator        ║")
    print("║              SCI = (E × I) + M   per R                   ║")
    print("╚══════════════════════════════════════════════════════════╝")

    # ── CSV file ──────────────────────────────────────────────────────────
    default_csv = "sensor_log_20260328_111338.csv"
    if args.csv:
        csv_path = args.csv
    else:
        csv_path = prompt_csv_file(default=default_csv if os.path.exists(default_csv) else None)

    if not os.path.exists(csv_path):
        print(f"Error: file not found — {csv_path}", file=sys.stderr)
        sys.exit(1)

    print(f"\nLoading {csv_path} …", end=" ")
    rows = load_csv(csv_path)
    print(f"{len(rows)} rows loaded.")

    # ── E: Energy ─────────────────────────────────────────────────────────
    power_col = args.power_col
    if power_col not in rows[0]:
        available = ", ".join(rows[0].keys())
        print(f"\nColumn '{power_col}' not found. Available columns:\n  {available}")
        power_col = input("Enter the power column name: ").strip()

    energy_kwh, duration_s = compute_energy_kwh(rows, power_col)
    print(f"\nEnergy (E): {energy_kwh*1000:.4f} Wh over {duration_s:.1f} s")

    # ── I: Carbon intensity ───────────────────────────────────────────────
    if args.intensity_value is not None:
        intensity_gco2_kwh = args.intensity_value
        intensity_label    = "custom"
    elif args.intensity:
        info = CARBON_INTENSITY[args.intensity]
        intensity_gco2_kwh = info["value"]
        intensity_label    = info["label"]
    else:
        intensity_gco2_kwh, intensity_label = prompt_carbon_intensity()

    # ── M: Embodied carbon ────────────────────────────────────────────────
    if args.embodied_total is not None:
        if args.embodied_lifespan is None:
            parser.error("--embodied-lifespan is required when --embodied-total is set")
        hw_total_gco2   = args.embodied_total
        hw_lifespan_yrs = args.embodied_lifespan
        hardware_label  = "custom hardware"
    elif args.hardware:
        preset          = HARDWARE_PRESETS[args.hardware]
        hw_total_gco2   = preset["total_gco2"]
        hw_lifespan_yrs = preset["lifespan_years"]
        hardware_label  = preset["label"]
    else:
        hw_total_gco2, hw_lifespan_yrs, hardware_label = prompt_hardware()

    # ── R: Functional unit ────────────────────────────────────────────────
    if args.transactions is not None:
        r_count = args.transactions
        r_label = args.unit
    else:
        r_count, r_label = prompt_functional_unit()

    # ── SCI calculation ───────────────────────────────────────────────────
    operational_gco2 = energy_kwh * intensity_gco2_kwh          # E × I
    embodied_gco2    = compute_embodied_gco2(                    # M
        hw_total_gco2, hw_lifespan_yrs, duration_s
    )
    total_gco2 = operational_gco2 + embodied_gco2                # (E×I) + M
    sci        = total_gco2 / r_count                            # per R

    # ── Report ────────────────────────────────────────────────────────────
    print_report(
        csv_path           = csv_path,
        duration_s         = duration_s,
        energy_kwh         = energy_kwh,
        intensity_gco2_kwh = intensity_gco2_kwh,
        intensity_label    = intensity_label,
        operational_gco2   = operational_gco2,
        hardware_label     = hardware_label,
        embodied_gco2      = embodied_gco2,
        total_gco2         = total_gco2,
        r_count            = r_count,
        r_label            = r_label,
        sci                = sci,
    )


if __name__ == "__main__":
    main()
