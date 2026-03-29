#!/usr/bin/env python3
"""Predict SCI scores for frontier models by fitting scaling laws to Qwen experiment data.

Fits multiple regression models (linear, log-linear, power-law) on measured
SCI-per-token data from Qwen 0.8B / 4B / 9B experiments, then extrapolates
to frontier-scale models (GPT-4, Claude, Gemini, Grok, Llama, etc.).

Usage:
    python predict_sci.py                # print predictions
    python predict_sci.py --plot         # also save plots
"""

import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy import optimize
from sklearn.linear_model import LinearRegression
from sklearn.metrics import r2_score

# ---------------------------------------------------------------------------
# 1. Load experiment data
# ---------------------------------------------------------------------------
RUNS_DIR = Path(__file__).parent / "runs" / "combined"
DATA_FILE = RUNS_DIR / "all_results.json"


def load_completed_experiments() -> list[dict]:
    with open(DATA_FILE) as f:
        results = json.load(f)
    return [
        r for r in results
        if r["status"] == "completed"
        and r["metrics"].get("sci_per_token") is not None
        and r["metrics"]["sci_per_token"] > 0
    ]


# Map model name -> parameter count in billions
MODEL_PARAMS_B = {
    "Qwen/Qwen3.5-0.8B": 0.8,
    "Qwen/Qwen3.5-4B": 4.0,
    "Qwen/Qwen3.5-9B": 9.0,
    "mistralai/Mistral-7B-v0.1": 7.0,
}

# Frontier models to predict (name, param_count_billions, notes)
FRONTIER_MODELS = [
    ("Llama 3.1 70B", 70),
    ("Llama 3.1 405B", 405),
    ("Mistral Large 2 (123B)", 123),
    ("GPT-4 (est. ~200B active MoE)", 200),
    ("GPT-4o (est. ~80B)", 80),
    ("Claude Sonnet 4 (est. ~100B)", 100),
    ("Claude Opus 4 (est. ~300B)", 300),
    ("Gemini 1.5 Pro (est. ~200B active)", 200),
    ("Gemini Ultra (est. ~500B active)", 500),
    ("Grok 2 (est. ~300B)", 300),
    ("DeepSeek-V3 (37B active MoE)", 37),
    ("Qwen 2.5 72B", 72),
]


# ---------------------------------------------------------------------------
# 2. Aggregate data: per-model median SCI at controlled config
# ---------------------------------------------------------------------------
def aggregate_by_model(experiments: list[dict]) -> tuple[np.ndarray, np.ndarray, dict]:
    """Return (params_B, median_sci, details_dict) per model."""
    from collections import defaultdict

    by_model = defaultdict(list)
    for exp in experiments:
        model = exp["config"]["model_name"]
        sci = exp["metrics"]["sci_per_token"]
        by_model[model].append(sci)

    params_list = []
    sci_medians = []
    details = {}
    for model, scis in sorted(by_model.items()):
        if model not in MODEL_PARAMS_B:
            continue
        p = MODEL_PARAMS_B[model]
        med = float(np.median(scis))
        mn = float(np.min(scis))
        mx = float(np.max(scis))
        params_list.append(p)
        sci_medians.append(med)
        details[model] = {"params_B": p, "n": len(scis), "median": med, "min": mn, "max": mx}

    return np.array(params_list), np.array(sci_medians), details


def aggregate_all_points(experiments: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """Return (params_B, sci) for every individual experiment."""
    params = []
    scis = []
    for exp in experiments:
        model = exp["config"]["model_name"]
        if model not in MODEL_PARAMS_B:
            continue
        params.append(MODEL_PARAMS_B[model])
        scis.append(exp["metrics"]["sci_per_token"])
    return np.array(params), np.array(scis)


# ---------------------------------------------------------------------------
# 3. Fit models
# ---------------------------------------------------------------------------

def fit_log_linear(x: np.ndarray, y: np.ndarray):
    """log(SCI) = a * log(params) + b  →  SCI = exp(b) * params^a  (power law)."""
    log_x = np.log(x).reshape(-1, 1)
    log_y = np.log(y)
    reg = LinearRegression().fit(log_x, log_y)
    a = reg.coef_[0]
    b = reg.intercept_
    y_pred = np.exp(reg.predict(log_x))
    r2 = r2_score(y, y_pred)
    return a, b, r2


def fit_power_law_scipy(x: np.ndarray, y: np.ndarray):
    """SCI = c * params^alpha  using nonlinear least squares."""
    def power_law(p, c, alpha):
        return c * np.power(p, alpha)

    try:
        popt, pcov = optimize.curve_fit(power_law, x, y, p0=[1e-4, 0.5], maxfev=10000)
        c, alpha = popt
        y_pred = power_law(x, c, alpha)
        r2 = r2_score(y, y_pred)
        # Standard errors from covariance matrix
        perr = np.sqrt(np.diag(pcov))
        return c, alpha, r2, perr
    except Exception as e:
        return None, None, None, None


def fit_linear(x: np.ndarray, y: np.ndarray):
    """SCI = a * params + b  (simple linear)."""
    reg = LinearRegression().fit(x.reshape(-1, 1), y)
    a = reg.coef_[0]
    b = reg.intercept_
    y_pred = reg.predict(x.reshape(-1, 1))
    r2 = r2_score(y, y_pred)
    return a, b, r2


# ---------------------------------------------------------------------------
# 4. Predict for frontier models
# ---------------------------------------------------------------------------
def predict_frontier(a: float, b: float, model_type: str = "power") -> list[dict]:
    results = []
    for name, params_b in FRONTIER_MODELS:
        if model_type == "power":
            # SCI = exp(b) * params^a
            sci = math.exp(b) * (params_b ** a)
        elif model_type == "linear":
            sci = a * params_b + b
        else:
            raise ValueError(model_type)
        results.append({"model": name, "params_B": params_b, "predicted_sci_per_token": sci})
    return results


# ---------------------------------------------------------------------------
# 5. Main
# ---------------------------------------------------------------------------
def main():
    do_plot = "--plot" in sys.argv

    experiments = load_completed_experiments()
    print(f"Loaded {len(experiments)} completed experiments with SCI data\n")

    # Aggregate
    params_med, sci_med, details = aggregate_by_model(experiments)
    params_all, sci_all = aggregate_all_points(experiments)

    print("=" * 70)
    print("MEASURED DATA SUMMARY (per model)")
    print("=" * 70)
    for model, d in sorted(details.items(), key=lambda kv: kv[1]["params_B"]):
        print(f"  {model:<30s}  {d['params_B']:>5.1f}B params | "
              f"n={d['n']:>3d} | SCI median={d['median']:.6f} "
              f"[{d['min']:.6f}, {d['max']:.6f}]")

    # ---- Fit on ALL individual data points (not just medians) ----
    print("\n" + "=" * 70)
    print("REGRESSION MODELS (fit on all individual experiments)")
    print("=" * 70)

    # 1. Power law via log-linear regression
    a_ll, b_ll, r2_ll = fit_log_linear(params_all, sci_all)
    print(f"\n1. Log-Linear (Power Law via OLS on log-log):")
    print(f"   SCI = exp({b_ll:.4f}) * params^{a_ll:.4f}")
    print(f"   SCI = {math.exp(b_ll):.6f} * params^{a_ll:.4f}")
    print(f"   R² = {r2_ll:.4f}")

    # 2. Power law via scipy curve_fit
    c_pl, alpha_pl, r2_pl, perr_pl = fit_power_law_scipy(params_all, sci_all)
    if c_pl is not None:
        print(f"\n2. Power Law (nonlinear least squares):")
        print(f"   SCI = {c_pl:.6f} * params^{alpha_pl:.4f}")
        print(f"   R² = {r2_pl:.4f}")
        print(f"   Std errors: c ± {perr_pl[0]:.6f}, alpha ± {perr_pl[1]:.4f}")

    # 3. Simple linear
    a_lin, b_lin, r2_lin = fit_linear(params_all, sci_all)
    print(f"\n3. Simple Linear:")
    print(f"   SCI = {a_lin:.8f} * params + {b_lin:.8f}")
    print(f"   R² = {r2_lin:.4f}")

    # ---- Also fit on medians (more robust to Qwen-0.8B overrepresentation) ----
    print("\n" + "=" * 70)
    print("REGRESSION MODELS (fit on per-model medians, equal weight per model)")
    print("=" * 70)

    a_ll_m, b_ll_m, r2_ll_m = fit_log_linear(params_med, sci_med)
    print(f"\n1. Log-Linear (Power Law):")
    print(f"   SCI = {math.exp(b_ll_m):.6f} * params^{a_ll_m:.4f}")
    print(f"   R² = {r2_ll_m:.4f}")

    c_pl_m, alpha_pl_m, r2_pl_m, perr_pl_m = fit_power_law_scipy(params_med, sci_med)
    if c_pl_m is not None:
        print(f"\n2. Power Law (NLS):")
        print(f"   SCI = {c_pl_m:.6f} * params^{alpha_pl_m:.4f}")
        print(f"   R² = {r2_pl_m:.4f}")

    a_lin_m, b_lin_m, r2_lin_m = fit_linear(params_med, sci_med)
    print(f"\n3. Simple Linear:")
    print(f"   SCI = {a_lin_m:.8f} * params + {b_lin_m:.8f}")
    print(f"   R² = {r2_lin_m:.4f}")

    # ---- Predictions using the median-based power law (less biased) ----
    print("\n" + "=" * 70)
    print("PREDICTED SCI FOR FRONTIER MODELS")
    print("(using median-based power law: SCI = {:.6f} * params^{:.4f})".format(
        math.exp(b_ll_m), a_ll_m))
    print("=" * 70)
    print(f"  {'Model':<40s} {'Params':>8s} {'Pred SCI/token':>16s} {'vs Qwen-0.8B':>14s}")
    print(f"  {'-'*40} {'-'*8} {'-'*16} {'-'*14}")

    qwen08_median = details.get("Qwen/Qwen3.5-0.8B", {}).get("median", 1)
    predictions = predict_frontier(a_ll_m, b_ll_m, model_type="power")
    for pred in predictions:
        ratio = pred["predicted_sci_per_token"] / qwen08_median
        print(f"  {pred['model']:<40s} {pred['params_B']:>6.0f}B "
              f"{pred['predicted_sci_per_token']:>16.6f} "
              f"{ratio:>13.1f}x")

    # Also show measured models for comparison
    print(f"\n  {'--- Measured (median) ---':<40s}")
    for model, d in sorted(details.items(), key=lambda kv: kv[1]["params_B"]):
        ratio = d["median"] / qwen08_median
        print(f"  {model:<40s} {d['params_B']:>6.1f}B "
              f"{d['median']:>16.6f} "
              f"{ratio:>13.1f}x")

    # ---- Caveats ----
    print("\n" + "=" * 70)
    print("CAVEATS & ASSUMPTIONS")
    print("=" * 70)
    print("""
  - Extrapolating from 0.8B-9B to 70B-500B is a ~50-100x range extension.
    Predictions carry HIGH uncertainty and should be treated as rough estimates.
  - Power law assumes SCI scales as a power of parameter count, holding config
    (batch size, seq length, etc.) constant. In practice, larger models use
    different serving configs (tensor parallelism, quantization, etc.).
  - Frontier model parameter counts are estimates (especially MoE models where
    only a fraction of parameters are active per token).
  - The SCI formula depends on grid carbon intensity (region-specific) and
    embodied emissions. Our measurements use us_average (400 gCO2/kWh).
  - Real-world SCI for frontier models also depends on datacenter PUE,
    hardware generation (H100 vs GB200 vs TPU), and serving optimizations.
""")

    # ---- Save predictions as JSON ----
    output = {
        "measured": details,
        "regression": {
            "type": "power_law_log_linear",
            "equation": f"SCI = {math.exp(b_ll_m):.6f} * params_B^{a_ll_m:.4f}",
            "coefficient": math.exp(b_ll_m),
            "exponent": a_ll_m,
            "r_squared": r2_ll_m,
            "fit_on": "per_model_medians",
            "n_models": len(params_med),
            "n_experiments": len(experiments),
        },
        "predictions": predictions,
    }
    out_path = RUNS_DIR / "frontier_predictions.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"Predictions saved to: {out_path}")

    # ---- Plot ----
    if do_plot:
        plot_results(params_all, sci_all, params_med, sci_med, details,
                     a_ll_m, b_ll_m, predictions)


def plot_results(params_all, sci_all, params_med, sci_med, details,
                 a, b, predictions):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 7))

    # --- Left: Log-log scatter with fit line ---
    ax1.scatter(params_all, sci_all, alpha=0.4, s=20, label="Individual experiments", color="steelblue")
    ax1.scatter(params_med, sci_med, s=120, marker="D", color="red",
                edgecolors="black", zorder=5, label="Per-model median")

    # Fit line
    x_range = np.logspace(np.log10(0.5), np.log10(600), 200)
    y_fit = math.exp(b) * x_range ** a
    ax1.plot(x_range, y_fit, "r--", linewidth=2, label=f"Power law (R²={1 - np.mean((sci_med - math.exp(b) * params_med**a)**2) / np.var(sci_med):.2f})")

    # Frontier predictions
    pred_params = [p["params_B"] for p in predictions]
    pred_sci = [p["predicted_sci_per_token"] for p in predictions]
    ax1.scatter(pred_params, pred_sci, s=80, marker="*", color="gold",
                edgecolors="black", zorder=5, label="Frontier predictions")
    for p in predictions:
        if p["params_B"] >= 100:
            ax1.annotate(p["model"].split("(")[0].strip(), (p["params_B"], p["predicted_sci_per_token"]),
                         fontsize=7, ha="left", va="bottom", rotation=15)

    ax1.set_xscale("log")
    ax1.set_yscale("log")
    ax1.set_xlabel("Model Parameters (Billions)", fontsize=12)
    ax1.set_ylabel("SCI per Token (gCO₂/token)", fontsize=12)
    ax1.set_title("SCI Scaling Law: Measured + Predicted", fontsize=14)
    ax1.legend(fontsize=9)
    ax1.grid(True, alpha=0.3)

    # --- Right: Bar chart of predictions ---
    all_models = []
    all_sci = []
    all_colors = []
    for model, d in sorted(details.items(), key=lambda kv: kv[1]["params_B"]):
        short = model.split("/")[-1]
        all_models.append(f"{short}\n({d['params_B']:.1f}B)")
        all_sci.append(d["median"])
        all_colors.append("steelblue")

    for p in sorted(predictions, key=lambda x: x["params_B"]):
        short = p["model"].split("(")[0].strip()
        all_models.append(f"{short}\n({p['params_B']:.0f}B)")
        all_sci.append(p["predicted_sci_per_token"])
        all_colors.append("coral")

    bars = ax2.barh(range(len(all_models)), all_sci, color=all_colors)
    ax2.set_yticks(range(len(all_models)))
    ax2.set_yticklabels(all_models, fontsize=8)
    ax2.set_xlabel("SCI per Token (gCO₂/token)", fontsize=12)
    ax2.set_title("Predicted Carbon Intensity by Model", fontsize=14)
    ax2.set_xscale("log")

    # Legend
    from matplotlib.patches import Patch
    ax2.legend([Patch(color="steelblue"), Patch(color="coral")],
               ["Measured (median)", "Predicted"], fontsize=9)

    plt.tight_layout()
    plot_path = Path(__file__).parent / "runs" / "combined" / "sci_predictions.png"
    plt.savefig(plot_path, dpi=150, bbox_inches="tight")
    print(f"Plot saved to: {plot_path}")


if __name__ == "__main__":
    main()
