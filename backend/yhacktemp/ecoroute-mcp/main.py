#!/usr/bin/env python3
"""
EcoRoute FastAPI Backend
POST /tasks/score  →  JSON recommendation payload consumed by the MCP server.
"""

import math

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="EcoRoute Backend", version="1.0.0")

# ---------------------------------------------------------------------------
# SCI Power-Law Regression (fit on Qwen 0.8B / 4B / 9B experiment medians)
# ---------------------------------------------------------------------------
# SCI_per_token = COEFFICIENT * params_B ^ EXPONENT   (gCO₂/token)
# Fitted R² = 0.979 on per-model medians from autoresearch-yaledgx runs.
# Multiply by 1000 to get gCO₂ per 1 000 tokens (the unit used in this API).

_SCI_COEFFICIENT = 0.000207
_SCI_EXPONENT = 0.3738


def predict_sci(params_b: float) -> float:
    """Predict SCI in gCO₂ per 1 000 tokens from model size in billions."""
    return _SCI_COEFFICIENT * math.pow(params_b, _SCI_EXPONENT) * 1000


# ---------------------------------------------------------------------------
# Model catalogue — parameter counts (billions) and quality scores
# ---------------------------------------------------------------------------
# Parameter counts are best public estimates.  For MoE models the "active"
# parameter count per forward pass is used (more relevant to per-token energy).

_MODEL_PARAMS: dict[str, dict] = {
    # OpenAI
    "gpt-4o":          {"params_b": 200, "quality": 100},
    "gpt-4":           {"params_b": 200, "quality": 98},
    "gpt-4o-mini":     {"params_b": 8,   "quality": 86},
    "gpt-3.5-turbo":   {"params_b": 20,  "quality": 84},
    # Anthropic
    "claude-opus":     {"params_b": 300, "quality": 99},
    "claude-sonnet":   {"params_b": 100, "quality": 95},
    "claude-haiku":    {"params_b": 20,  "quality": 88},
    # Google
    "gemini-pro":      {"params_b": 200, "quality": 93},
    "gemini-flash":    {"params_b": 30,  "quality": 86},
    "gemini-ultra":    {"params_b": 500, "quality": 99},
    # xAI
    "grok-2":          {"params_b": 300, "quality": 94},
    # Meta
    "llama-3-8b":      {"params_b": 8,   "quality": 78},
    "llama-3-70b":     {"params_b": 70,  "quality": 90},
    "llama-3-405b":    {"params_b": 405, "quality": 96},
    # Mistral
    "mistral-7b":      {"params_b": 7,   "quality": 80},
    "mistral-large":   {"params_b": 123, "quality": 93},
    # DeepSeek
    "deepseek-v3":     {"params_b": 37,  "quality": 94},
    # Qwen (measured)
    "qwen-0.8b":       {"params_b": 0.8, "quality": 65},
    "qwen-4b":         {"params_b": 4,   "quality": 74},
    "qwen-9b":         {"params_b": 9,   "quality": 80},
    "qwen-72b":        {"params_b": 72,  "quality": 92},
}

# Build the MODELS dict used by the recommendation engine
MODELS = {
    name: {"sci": round(predict_sci(info["params_b"]), 2), "quality": info["quality"]}
    for name, info in _MODEL_PARAMS.items()
}

# Task-type → complexity score (1-10) and minimum quality threshold
TASK_PROFILES = {
    "refactor": {"complexity": 2, "min_quality": 82},
    "debug": {"complexity": 5, "min_quality": 88},
    "chat": {"complexity": 1, "min_quality": 78},
    "autocomplete": {"complexity": 3, "min_quality": 80},
    "config": {"complexity": 2, "min_quality": 78},
    "general": {"complexity": 4, "min_quality": 82},
}

CURRENT_MODEL = "gpt-4o"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ScoreRequest(BaseModel):
    task_type: str = "general"
    context_size: int = 500


class ScoreResponse(BaseModel):
    current_model: str
    current_sci: float
    task_complexity: int
    recommended_model: str
    recommended_sci: float
    carbon_savings_pct: float
    quality_match_pct: float
    efficiency_score: float


# ---------------------------------------------------------------------------
# Recommendation logic
# ---------------------------------------------------------------------------


def _recommend(task_type: str, context_size: int) -> ScoreResponse:
    profile = TASK_PROFILES.get(task_type, TASK_PROFILES["general"])
    complexity = min(10, profile["complexity"] + context_size // 2000)
    min_quality = profile["min_quality"]
    current = MODELS[CURRENT_MODEL]

    candidates = sorted(
        [
            (name, info)
            for name, info in MODELS.items()
            if info["quality"] >= min_quality
        ],
        key=lambda x: x[1]["sci"],
    )
    best_name, best_info = candidates[0]

    current_sci = current["sci"]
    recommended_sci = best_info["sci"]

    return ScoreResponse(
        current_model=CURRENT_MODEL,
        current_sci=current_sci,
        task_complexity=complexity,
        recommended_model=best_name,
        recommended_sci=recommended_sci,
        carbon_savings_pct=round((1 - recommended_sci / current_sci) * 100, 1),
        quality_match_pct=round(best_info["quality"] / current["quality"] * 100, 1),
        efficiency_score=round(recommended_sci / current_sci, 2),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/tasks/score", response_model=ScoreResponse)
def score(req: ScoreRequest) -> ScoreResponse:
    return _recommend(req.task_type, req.context_size)
