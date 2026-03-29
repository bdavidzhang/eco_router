"""Data models for experiment configs, metrics, and results.

These are plain dataclasses — no ORM bloat, no magic. Just data.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Quantization(str, Enum):
    NONE = "none"
    GPTQ_4BIT = "gptq-4bit"
    GPTQ_8BIT = "gptq-8bit"
    AWQ_4BIT = "awq-4bit"
    AWQ_8BIT = "awq-8bit"


class SearchStrategy(str, Enum):
    GRID = "grid"
    RANDOM = "random"
    BAYESIAN = "bayesian"


class ExperimentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    DISCARDED = "discarded"  # Thermal throttle, OOM, etc.


@dataclass(frozen=True)
class ExperimentConfig:
    """Immutable experiment configuration. The hash is the identity."""

    model_name: str = "Qwen/Qwen3.5-0.8B"
    quantization: Quantization = Quantization.NONE
    batch_size: int = 1
    sequence_length: int = 512
    max_new_tokens: int = 128
    temperature: float = 1.0
    use_kv_cache: bool = True
    dtype: str = "float16"
    time_budget_sec: int = 300  # 5 min default

    @property
    def config_hash(self) -> str:
        """Deterministic hash of the config for deduplication."""
        blob = json.dumps(asdict(self), sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:12]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ExperimentConfig:
        d = d.copy()
        if "quantization" in d and isinstance(d["quantization"], str):
            d["quantization"] = Quantization(d["quantization"])
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class BenchmarkMetrics:
    """Raw + derived metrics collected from a single experiment run.

    Sensor coverage matches sensor_logger.sh:
    - GPU: power, temp, utilization, clock speed (via nvidia-smi)
    - System: memory, CPU cluster freqs, load avg (via /proc)
    - Carbon: SCI = (E x I) + M (via benchmark/carbon.py)
    """

    # Quality
    val_bpb: float | None = None  # Validation bits-per-byte

    # Latency (milliseconds per token)
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    latency_p99_ms: float | None = None

    # Throughput
    tokens_per_sec: float | None = None
    total_tokens: int = 0

    # Power & Energy (from PowerMonitor — matches sensor_logger.sh GPU fields)
    gpu_power_avg_w: float | None = None
    gpu_power_max_w: float | None = None
    energy_per_token_j: float | None = None

    # GPU utilization & clock (NEW — from sensor_logger.sh alignment)
    gpu_util_avg_pct: float | None = None    # If <50%, SCI may be inflated by idle power
    gpu_clock_avg_mhz: float | None = None   # Drops during thermal throttling
    gpu_clock_min_mhz: float | None = None   # Worst-case throttle point

    # Thermal
    gpu_temp_avg_c: float | None = None
    gpu_temp_max_c: float | None = None
    thermal_throttled: bool = False

    # System resources (NEW — matches sensor_logger.sh system fields)
    # Memory via /proc/meminfo — only reliable source on C2C unified memory
    mem_used_gb: float | None = None       # Unified memory used
    mem_available_gb: float | None = None  # Unified memory remaining
    mem_pressure_pct: float | None = None  # >90% = OOM danger zone
    # NVMe
    nvme_temp_c: float | None = None       # Storage thermal state
    # System load
    system_load_avg: float | None = None   # 1-min load avg

    # SCI (Software Carbon Intensity) — THE sustainability metric
    sci_per_token: float | None = None       # gCO2 per token (SCI score)
    carbon_operational_g: float | None = None  # E x I component
    carbon_embodied_g: float | None = None     # M component
    energy_kwh_per_token: float | None = None  # E in kWh

    # Derived
    gpu_efficiency: float | None = None  # tokens/sec/watt
    cost_per_token_usd: float | None = None

    def compute_derived(
        self,
        usd_per_kwh: float = 0.12,
        carbon_intensity_gco2_per_kwh: float = 400.0,
        embodied_gco2_per_token: float = 0.00003,
    ) -> None:
        """Compute derived metrics including SCI from raw measurements."""
        if self.tokens_per_sec and self.gpu_power_avg_w:
            self.gpu_efficiency = self.tokens_per_sec / self.gpu_power_avg_w
        if self.energy_per_token_j is not None:
            # J/token -> kWh/token -> $/token
            kwh_per_token = self.energy_per_token_j / 3_600_000
            self.cost_per_token_usd = kwh_per_token * usd_per_kwh
            # SCI = (E x I) + M
            self.energy_kwh_per_token = kwh_per_token
            self.carbon_operational_g = kwh_per_token * carbon_intensity_gco2_per_kwh
            self.carbon_embodied_g = embodied_gco2_per_token
            self.sci_per_token = self.carbon_operational_g + self.carbon_embodied_g

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BenchmarkMetrics:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class ExperimentResult:
    """A complete experiment record: config + metrics + metadata."""

    config: ExperimentConfig
    metrics: BenchmarkMetrics
    status: ExperimentStatus = ExperimentStatus.COMPLETED
    strategy_used: SearchStrategy = SearchStrategy.RANDOM
    pareto_rank: int | None = None  # 0 = on the frontier
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    error_message: str | None = None

    @property
    def config_hash(self) -> str:
        return self.config.config_hash

    @property
    def is_pareto_optimal(self) -> bool:
        return self.pareto_rank == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_hash": self.config_hash,
            "config": self.config.to_dict(),
            "metrics": self.metrics.to_dict(),
            "status": self.status.value,
            "strategy_used": self.strategy_used.value,
            "pareto_rank": self.pareto_rank,
            "created_at": self.created_at,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ExperimentResult:
        return cls(
            config=ExperimentConfig.from_dict(d["config"]),
            metrics=BenchmarkMetrics.from_dict(d["metrics"]),
            status=ExperimentStatus(d.get("status", "completed")),
            strategy_used=SearchStrategy(d.get("strategy_used", "random")),
            pareto_rank=d.get("pareto_rank"),
            created_at=d.get("created_at", datetime.now(timezone.utc).isoformat()),
            error_message=d.get("error_message"),
        )
