import json
import os
import re
from pathlib import Path
from typing import List, Optional

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sklearn.linear_model import LinearRegression

# ---------------------------------------------------------------------------
# Data path (Qwen benchmark — used as regression reference only)
# ---------------------------------------------------------------------------
DATA_PATH = Path(
    os.getenv(
        "COMBINED_RESULTS_PATH",
        str(
            Path(__file__).parent.parent
            / "yhacktemp/yhacktemp/autoresearch-yaledgx/runs/combined/combined_results.json"
        ),
    )
)

# Reference baseline model used for carbon savings comparison when no model can be detected
BASELINE_MODEL = "claude-sonnet-4-6"

# SCI values in gCO₂/token, derived from:
#   sci = (gpu_count × tdp_w / tokens_per_sec / 3_600_000) × GRID_INTENSITY × PUE + EMBODIED
# where GRID_INTENSITY = 400 gCO₂/kWh (US avg), PUE = 1.3 (data center overhead),
# EMBODIED = 0.00003 gCO₂/token (matches benchmark default).
MODEL_SCI_DB: dict[str, float] = {
    # OpenAI — current generation
    "gpt-4o": 0.01544,  # 8× A100 400W / 30 tok/s
    "gpt-4o-mini": 0.00147,  # 2× A100 400W / 80 tok/s
    "gpt-4-turbo": 0.01855,  # 8× A100 400W / 25 tok/s
    "gpt-4": 0.01855,
    "gpt-3.5-turbo": 0.00196,  # 2× A100 400W / 60 tok/s
    # OpenAI — GPT-5 family (Zed defaults)
    "gpt-5.4": 0.02080,  # 8× A100 400W / 25 tok/s
    "gpt-5.3-codex": 0.01544,  # 8× A100 400W / 30 tok/s
    "gpt-5.2": 0.01236,  # 8× A100 400W / 40 tok/s
    "gpt-5.2-codex": 0.01014,  # 4× H100 700W / 40 tok/s
    "gpt-5-mini": 0.00196,  # 2× A100 400W / 60 tok/s
    "gpt-5-nano": 0.00098,  # 1× A100 400W / 80 tok/s
    # Anthropic
    "claude-opus-4": 0.10804,  # 16× H100 700W / 15 tok/s
    "claude-opus-4-5": 0.10804,
    "claude-sonnet-4-6": 0.01014,  # 4× H100 700W / 40 tok/s
    "claude-sonnet-4-5": 0.01014,
    "claude-3-5-sonnet": 0.01014,
    "claude-haiku-4-5": 0.00072,  # 1× A100 400W / 80 tok/s
    "claude-3-haiku": 0.00072,
    "claude-haiku": 0.00072,
    # xAI / Grok (Zed defaults)
    "grok-4": 0.02080,  # 8× H100 700W / 25 tok/s
    "grok-4-fast": 0.01014,  # 4× H100 700W / 40 tok/s
    "grok-code-fast-1": 0.00760,  # 4× H100 700W / 50 tok/s
    # Google — Gemini 3.x (Zed defaults)
    "gemini-3.1-pro": 0.01170,  # 8× TPUv5 300W / 30 tok/s
    "gemini-3-flash": 0.00137,  # 2× TPUv5 300W / 80 tok/s
    # Google — older
    "gemini-1.5-pro": 0.01062,  # 8× TPUv4 275W / 30 tok/s
    "gemini-1.5-flash": 0.00112,  # 2× TPUv5 300W / 80 tok/s
    "gemini-2.0-flash": 0.00112,
    # Meta / open (self-hosted estimates)
    "llama-3-70b": 0.00928,  # 4× A100 400W / 25 tok/s
    "llama-3-8b": 0.00072,  # 1× A100 400W / 80 tok/s
    "llama-3.1-405b": 0.10804,  # 16× H100 700W / 15 tok/s
}

# Quality tiers (1–10) derived from public benchmark rankings.
# Cloud/API models are assigned here; local benchmark models get tiers from val_bpb.
MODEL_QUALITY_TIER: dict[str, int] = {
    # GPT-5 family
    "gpt-5.4": 9,
    "gpt-5.3-codex": 8,
    "gpt-5.2": 8,
    "gpt-5.2-codex": 8,
    "gpt-5-mini": 6,
    "gpt-5-nano": 5,
    # Grok
    "grok-4": 9,
    "grok-4-fast": 8,
    "grok-code-fast-1": 8,
    # Gemini 3.x
    "gemini-3.1-pro": 9,
    "gemini-3-flash": 7,
    # Anthropic
    "claude-opus-4": 9,
    "claude-opus-4-5": 9,
    "claude-sonnet-4-6": 8,
    "claude-sonnet-4-5": 8,
    "claude-3-5-sonnet": 8,
    "claude-haiku-4-5": 6,
    "claude-3-haiku": 6,
    "claude-haiku": 6,
    # OpenAI current
    "gpt-4o": 8,
    "gpt-4o-mini": 6,
    "gpt-4-turbo": 8,
    "gpt-4": 8,
    "gpt-3.5-turbo": 6,
    # Google older
    "gemini-1.5-pro": 8,
    "gemini-1.5-flash": 6,
    "gemini-2.0-flash": 7,
    # Meta
    "llama-3.1-405b": 9,
    "llama-3-70b": 7,
    "llama-3-8b": 5,
}

# Models available by default in Zed's AI panel
ZED_DEFAULT_MODELS: set[str] = {
    "claude-sonnet-4-6",
    "claude-sonnet-4-5",
    "claude-haiku-4-5",
    "gpt-5.4",
    "gpt-5.3-codex",
    "gpt-5.2",
    "gpt-5.2-codex",
    "gpt-5-mini",
    "gpt-5-nano",
    "grok-4-fast",
    "grok-4",
    "grok-code-fast-1",
    "gemini-3.1-pro",
    "gemini-3-flash",
}

BASELINE_SCI = MODEL_SCI_DB[BASELINE_MODEL]

# Deployment type for each known model — used to label responses
MODEL_DEPLOYMENT: dict[str, str] = {k: "api" for k in MODEL_SCI_DB}

TASK_MIN_TIER = {
    "autocomplete": 4,
    "chat": 5,
    "debug": 6,
    "refactor": 7,
}

TASK_COMPLEXITY = {
    "autocomplete": 1,
    "chat": 2,
    "debug": 3,
    "refactor": 4,
}

# Path to Zed settings — read at startup to pick up user-configured models
ZED_SETTINGS = Path.home() / ".config" / "zed" / "settings.json"

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="EcoRoute Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Module-level state populated at startup
MODELS: List[dict] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_quality_tier(bpb: float) -> int:
    thresholds = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]
    tiers = [10, 9, 8, 7, 6, 5, 4, 3, 2]
    for threshold, tier in zip(thresholds, tiers):
        if bpb < threshold:
            return tier
    return 1


def load_zed_models() -> set[str]:
    """Return model names from Zed default_model + favorite_models."""
    try:
        text = ZED_SETTINGS.read_text()
        # Strip JS-style line comments and trailing commas so json.loads doesn't choke
        text = re.sub(r"//[^\n]*", "", text)
        text = re.sub(r",\s*([}\]])", r"\1", text)
        cfg = json.loads(text)
        agent = cfg.get("agent", {})
        names: set[str] = set()
        # default model
        dm = agent.get("default_model", {})
        if dm.get("model"):
            names.add(dm["model"].split("/")[-1].lower())
        # user-starred / favorite models
        for fm in agent.get("favorite_models", []):
            if isinstance(fm, dict) and fm.get("model"):
                names.add(fm["model"].split("/")[-1].lower())
        return names
    except Exception:
        return set()


def build_cloud_models() -> List[dict]:
    """Build recommendation pool from Zed-configured cloud API models."""
    active = ZED_DEFAULT_MODELS | load_zed_models()
    pool = []
    for name in active:
        key = name.lower()
        sci = next((v for k, v in MODEL_SCI_DB.items() if k.lower() == key), None)
        if sci is None:
            continue  # no SCI data for this model — skip
        tier = next((v for k, v in MODEL_QUALITY_TIER.items() if k.lower() == key), 5)
        pool.append(
            {
                "model": name,
                "sci_per_token": sci,
                "quality_tier": tier,
                "bpb": None,
                "pareto_rank": None,
                "quantization": "none",
                "tokens_per_sec": None,
                "latency_p50_ms": None,
            }
        )
    if not pool:
        return []
    scis = [m["sci_per_token"] for m in pool]
    mn, mx = min(scis), max(scis)
    for m in pool:
        m["efficiency_score"] = (
            round((mx - m["sci_per_token"]) / (mx - mn), 4) if mx > mn else 0.5
        )
    pool.sort(key=lambda x: (-x["efficiency_score"], x["sci_per_token"]))
    return pool


def load_and_process() -> List[dict]:
    """Load Qwen benchmark data — kept for reference/regression baseline."""
    with open(DATA_PATH) as f:
        raw = json.load(f)

    records = [
        r
        for r in raw
        if r.get("status") == "completed"
        and r.get("metrics", {}).get("val_bpb") is not None
        and r.get("metrics", {}).get("sci_per_token") is not None
    ]

    by_model: dict = {}
    for r in records:
        name = r["config"]["model_name"]
        sci = r["metrics"]["sci_per_token"]
        if name not in by_model or sci < by_model[name]["metrics"]["sci_per_token"]:
            by_model[name] = r
    records = list(by_model.values())

    if not records:
        return []

    bpb_vals = np.array([r["metrics"]["val_bpb"] for r in records])
    sci_vals = np.array([r["metrics"]["sci_per_token"] for r in records])
    X = bpb_vals.reshape(-1, 1)
    reg = LinearRegression().fit(X, sci_vals)
    predicted = reg.predict(X)

    residuals = predicted - sci_vals
    r_min, r_max = residuals.min(), residuals.max()
    if r_max > r_min:
        normalized = (residuals - r_min) / (r_max - r_min)
    else:
        normalized = np.zeros_like(residuals)

    result = []
    for i, r in enumerate(records):
        m = r["metrics"]
        result.append(
            {
                "model": r["config"]["model_name"],
                "quantization": r["config"].get("quantization", "none"),
                "sci_per_token": m["sci_per_token"],
                "bpb": m["val_bpb"],
                "efficiency_score": round(float(normalized[i]), 4),
                "quality_tier": get_quality_tier(m["val_bpb"]),
                "tokens_per_sec": m.get("tokens_per_sec"),
                "latency_p50_ms": m.get("latency_p50_ms"),
                "pareto_rank": r.get("pareto_rank"),
            }
        )

    result.sort(key=lambda x: (-x["efficiency_score"], x["sci_per_token"]))
    return result


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def startup_event():
    global MODELS
    MODELS = build_cloud_models()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class TaskScoreRequest(BaseModel):
    task_type: str
    context_size: int = 0
    current_model: Optional[str] = None  # e.g. "claude-sonnet-4-6" from Zed agent


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
def health():
    return {"status": "ok", "models_loaded": len(MODELS)}


@app.get("/models/rankings")
def get_rankings():
    return MODELS


@app.post("/tasks/score")
def task_score(req: TaskScoreRequest):
    task_type = req.task_type.lower()
    if task_type not in TASK_MIN_TIER:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown task_type '{req.task_type}'. Valid: {list(TASK_MIN_TIER)}",
        )

    min_tier = TASK_MIN_TIER[task_type]
    complexity = TASK_COMPLEXITY[task_type]

    # Find best model (highest efficiency_score) meeting quality threshold
    candidates = [m for m in MODELS if m["quality_tier"] >= min_tier]
    if not candidates:
        candidates = MODELS

    if not candidates:
        raise HTTPException(status_code=503, detail="No models loaded")

    best = candidates[0]  # already sorted by efficiency_score desc

    # Resolve current model: caller-supplied → Zed settings → hardcoded baseline
    current_name = req.current_model
    if not current_name:
        detected = load_zed_models()
        # prefer the active model if it's one we know
        for m in detected:
            if next(
                (v for k, v in MODEL_SCI_DB.items() if k.lower() == m.lower()), None
            ):
                current_name = m
                break
    current_name = current_name or BASELINE_MODEL
    # Normalize: strip provider prefix (e.g. "anthropic/claude-sonnet-4-6" → "claude-sonnet-4-6")
    lookup_name = current_name.split("/")[-1].lower()
    current_sci = next(
        (v for k, v in MODEL_SCI_DB.items() if k.lower() == lookup_name),
        BASELINE_SCI,
    )

    recommended_sci = best["sci_per_token"]
    carbon_savings_pct = int((1 - recommended_sci / current_sci) * 100)
    carbon_savings_pct = max(0, min(99, carbon_savings_pct))

    tier_surplus = best["quality_tier"] - min_tier
    quality_confidence_pct = min(
        99, int(50 + tier_surplus * 10 + best["efficiency_score"] * 40)
    )

    return {
        "current_model": current_name,
        "current_sci": current_sci,
        "current_deployment": MODEL_DEPLOYMENT.get(lookup_name, "api"),
        "task_complexity": complexity,
        "task_type": task_type,
        "recommended_model": best["model"],
        "recommended_sci": recommended_sci,
        "recommended_deployment": "api",
        "carbon_savings_pct": carbon_savings_pct,
        "quality_confidence_pct": quality_confidence_pct,
        "efficiency_score": best["efficiency_score"],
        "above_frontier": best["pareto_rank"] == 0,
        "all_rankings": MODELS,
    }
