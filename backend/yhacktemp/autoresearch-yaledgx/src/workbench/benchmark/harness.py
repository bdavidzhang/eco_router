"""Benchmark harness — orchestrates power monitoring, inference, and quality eval.

This is the "run one experiment" function. It:
1. Starts power monitoring (GPU: power, temp, util%, clock — same as sensor_logger.sh)
2. Takes a system snapshot (memory, CPU freqs, load — same as sensor_logger.sh)
3. Loads the model (cached across experiments with same model/quant/dtype)
4. Runs inference with the given config (hard-capped via max_time)
5. Evaluates quality
6. Stops power monitoring
7. Takes a post-run system snapshot
8. Computes all metrics

Every phase respects a hard wall-clock deadline so no single experiment
can hang the research loop.  The optional ``on_phase`` callback lets the
TUI dashboard display exactly which phase is active.
"""

from __future__ import annotations

import gc
import logging
import time
from typing import Any, Callable

import numpy as np
import torch

from workbench.benchmark.power import PowerMonitor
from workbench.benchmark.quality import evaluate_quality
from workbench.benchmark.system import SystemMonitor
from workbench.benchmark.thermal import ThermalMonitor
from workbench.store.models import BenchmarkMetrics, ExperimentConfig

logger = logging.getLogger(__name__)

# Type alias for the phase callback: receives one of
# "loading" | "inference" | "evaluating" | "done"
PhaseCallback = Callable[[str], None]

# ── Model cache ─────────────────────────────────────────────────────────────
# Reloading a multi-GB model every experiment is *brutal* (~30-40s each time).
# Cache key = (model_name, quantization, dtype). If the next experiment uses
# the same combo, we skip the load entirely.

_model_cache: dict[str, tuple[Any, Any]] = {}  # cache_key -> (model, tokenizer)
_model_cache_key: str | None = None


def _cache_key(config: ExperimentConfig) -> str:
    return f"{config.model_name}|{config.quantization.value}|{config.dtype}"


def clear_model_cache() -> None:
    """Evict the cached model (e.g. on OOM or between runs)."""
    global _model_cache, _model_cache_key
    _model_cache.clear()
    _model_cache_key = None
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    logger.info("Model cache cleared.")


def _resolve_device() -> str:
    """Pick the best available device."""
    if torch.cuda.is_available():
        return "cuda"
    elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _load_model_and_tokenizer(
    config: ExperimentConfig, device: str
) -> tuple[Any, Any]:
    """Load model and tokenizer, with caching.

    If the (model_name, quantization, dtype) combo is already loaded, return
    the cached version instantly. Otherwise evict the old model first.
    """
    global _model_cache, _model_cache_key

    key = _cache_key(config)
    if key == _model_cache_key and key in _model_cache:
        logger.info("♻️  Reusing cached model for %s", config.model_name)
        return _model_cache[key]

    # Different model needed — evict old one first to free VRAM
    if _model_cache:
        logger.info("Evicting cached model (new config: %s)", key)
        clear_model_cache()

    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(config.model_name, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    dtype_map = {
        "float16": torch.float16,
        "bfloat16": torch.bfloat16,
        "float32": torch.float32,
    }
    torch_dtype = dtype_map.get(config.dtype, torch.float16)

    load_kwargs: dict[str, Any] = {
        "pretrained_model_name_or_path": config.model_name,
        "torch_dtype": torch_dtype,
        "trust_remote_code": True,
        "low_cpu_mem_usage": True,
    }

    # Quantization handling
    if config.quantization.value.startswith("gptq"):
        try:
            import optimum  # noqa: F401
            from transformers import GPTQConfig

            bits = 4 if "4bit" in config.quantization.value else 8
            load_kwargs["quantization_config"] = GPTQConfig(
                bits=bits, disable_exllama=True,
            )
        except ImportError:
            logger.warning(
                "GPTQ requires 'optimum' + 'auto-gptq'. "
                "Falling back to native (unquantized) loading."
            )
    elif config.quantization.value.startswith("awq"):
        try:
            from awq import AutoAWQForCausalLM

            model = AutoAWQForCausalLM.from_quantized(
                config.model_name,
                fuse_layers=True,
                trust_remote_code=True,
            ).to(device)
            _model_cache[key] = (model, tokenizer)
            _model_cache_key = key
            return model, tokenizer
        except ImportError:
            logger.warning(
                "AWQ requires 'autoawq'. "
                "Falling back to native (unquantized) loading."
            )

    model = AutoModelForCausalLM.from_pretrained(**load_kwargs)
    model = model.to(device)

    # Cache it
    _model_cache[key] = (model, tokenizer)
    _model_cache_key = key
    logger.info("📦 Cached model: %s", key)

    return model, tokenizer


def _run_inference_pass(
    model: Any,
    tokenizer: Any,
    config: ExperimentConfig,
    device: str,
    hard_deadline: float,
) -> tuple[list[float], int]:
    """Run inference and collect per-token latencies.

    Every generate() call gets a max_time cap so a single call with
    aggressive params (big model + large batch + many tokens) can't hang.

    Returns:
        (latencies_ms, total_tokens_generated)
    """
    prompt = "The future of artificial intelligence is"
    inputs = tokenizer(
        [prompt] * config.batch_size,
        return_tensors="pt",
        padding=True,
        truncation=True,
        max_length=config.sequence_length,
    ).to(device)

    latencies: list[float] = []
    total_tokens = 0

    budget = config.time_budget_sec
    # Soft wall for inference phase: 70% of budget (rest for quality eval).
    # But never exceed the hard deadline.
    phase_deadline = min(time.time() + budget * 0.7, hard_deadline - 5)
    # Per-call cap: no single generate() hogs more than 40% of budget
    per_call_cap = max(5.0, budget * 0.4)

    # Warmup pass (not measured, but capped so it can't hang)
    with torch.no_grad():
        _ = model.generate(
            **inputs,
            max_new_tokens=min(16, config.max_new_tokens),
            do_sample=False,
            use_cache=config.use_kv_cache,
            max_time=per_call_cap,
        )

    # Timed inference passes — fill the time budget
    while time.time() < phase_deadline:
        remaining = max(1.0, phase_deadline - time.time())
        call_timeout = min(remaining, per_call_cap)

        if torch.cuda.is_available():
            torch.cuda.synchronize()
        t0 = time.perf_counter()

        with torch.no_grad():
            outputs = model.generate(
                **inputs,
                max_new_tokens=config.max_new_tokens,
                do_sample=config.temperature > 0,
                temperature=max(config.temperature, 1e-7),
                use_cache=config.use_kv_cache,
                max_time=call_timeout,
            )

        if torch.cuda.is_available():
            torch.cuda.synchronize()
        elapsed_ms = (time.perf_counter() - t0) * 1000

        tokens_generated = (outputs.shape[1] - inputs["input_ids"].shape[1]) * config.batch_size
        total_tokens += tokens_generated

        if tokens_generated > 0:
            per_token_ms = elapsed_ms / tokens_generated
            latencies.extend([per_token_ms] * tokens_generated)

    return latencies, total_tokens


# ── Public entry point ──────────────────────────────────────────────────


def run_benchmark(
    config: ExperimentConfig,
    on_phase: PhaseCallback | None = None,
) -> BenchmarkMetrics:
    """Execute a complete benchmark run for a single experiment config.

    Args:
        config: The experiment configuration.
        on_phase: Optional callback invoked with phase name strings
                  ("loading", "inference", "evaluating", "done") so the
                  dashboard can display what's actually happening.

    Enforces a hard wall-clock deadline of 2x the per-experiment budget
    so no single experiment can hang the entire research loop.
    """
    hard_deadline = time.time() + config.time_budget_sec * 2
    _emit = on_phase or (lambda _phase: None)
    return _run_benchmark_inner(config, hard_deadline, _emit)


def _run_benchmark_inner(
    config: ExperimentConfig,
    hard_deadline: float,
    emit: PhaseCallback,
) -> BenchmarkMetrics:
    """The actual benchmark — every phase checks the hard deadline."""
    device = _resolve_device()
    power_monitor = PowerMonitor()
    thermal_monitor = ThermalMonitor()
    system_monitor = SystemMonitor()

    # Pre-flight thermal check
    thermal_monitor.check_or_raise()

    # Pre-run system snapshot (baseline memory, CPU state)
    sys_pre = system_monitor.snapshot()

    logger.info(
        "Starting benchmark: %s [%s, batch=%d, seq=%d] on %s "
        "(mem: %.1f/%.1f GB, load: %.1f)",
        config.model_name,
        config.quantization.value,
        config.batch_size,
        config.sequence_length,
        device,
        sys_pre.mem_used_gb,
        sys_pre.mem_total_gb,
        sys_pre.load_avg_1m,
    )

    # Phase 1: Start power monitoring
    power_monitor.start()
    run_start = time.time()
    quality = None

    try:
        # Phase 2: Load model
        emit("loading")
        model, tokenizer = _load_model_and_tokenizer(config, device)

        if time.time() >= hard_deadline:
            raise TimeoutError("Hard deadline hit during model load")

        # Phase 3: Run inference (aware of hard deadline)
        emit("inference")
        latencies, total_tokens = _run_inference_pass(
            model, tokenizer, config, device, hard_deadline,
        )

        if time.time() >= hard_deadline:
            logger.warning("Hard deadline hit after inference — skipping quality eval")
        else:
            # Phase 4: Quality evaluation
            emit("evaluating")
            quality = evaluate_quality(model, tokenizer, device=device)

        emit("done")

    finally:
        # Phase 5: Stop power monitoring (always)
        power_trace = power_monitor.stop()
        run_duration = time.time() - run_start

    # Post-run snapshots
    thermal_snap = thermal_monitor.snapshot()
    sys_post = system_monitor.snapshot()

    peak_mem_used_gb = sys_post.mem_used_gb
    min_mem_available_gb = sys_post.mem_available_gb

    # Compute metrics
    latency_arr = np.array(latencies) if latencies else np.array([0.0])
    metrics = BenchmarkMetrics(
        val_bpb=quality.val_bpb if quality else None,
        latency_p50_ms=float(np.percentile(latency_arr, 50)) if latencies else None,
        latency_p95_ms=float(np.percentile(latency_arr, 95)) if latencies else None,
        latency_p99_ms=float(np.percentile(latency_arr, 99)) if latencies else None,
        tokens_per_sec=total_tokens / run_duration if run_duration > 0 else 0,
        total_tokens=total_tokens,
        # GPU power (from PowerMonitor — matches sensor_logger.sh)
        gpu_power_avg_w=power_trace.avg_power_w,
        gpu_power_max_w=power_trace.max_power_w,
        energy_per_token_j=(
            power_trace.total_energy_j / total_tokens if total_tokens > 0 else None
        ),
        # GPU utilization & clock
        gpu_util_avg_pct=power_trace.avg_gpu_util_pct,
        gpu_clock_avg_mhz=power_trace.avg_gpu_clock_mhz,
        gpu_clock_min_mhz=power_trace.min_gpu_clock_mhz,
        # Thermal
        gpu_temp_avg_c=power_trace.avg_temp_c or thermal_snap.avg_temp_c,
        gpu_temp_max_c=power_trace.max_temp_c or thermal_snap.max_temp_c,
        thermal_throttled=not thermal_snap.is_safe,
        # System resources
        mem_used_gb=round(peak_mem_used_gb, 2),
        mem_available_gb=round(min_mem_available_gb, 2),
        mem_pressure_pct=round(sys_post.mem_pressure_pct, 1),
        nvme_temp_c=sys_post.nvme_temp_c,
        system_load_avg=sys_post.load_avg_1m,
    )
    metrics.compute_derived()

    # NOTE: model stays in cache — no del/gc here.
    # Call clear_model_cache() explicitly if you need VRAM back.

    logger.info(
        "Benchmark complete: BPB=%.4f, %.1f tok/s, %.3f J/tok, "
        "%.1fW avg, GPU util=%.0f%%, mem=%.1f GB (%.1fs wall)",
        metrics.val_bpb or 0,
        metrics.tokens_per_sec or 0,
        metrics.energy_per_token_j or 0,
        metrics.gpu_power_avg_w or 0,
        metrics.gpu_util_avg_pct or 0,
        metrics.mem_used_gb or 0,
        run_duration,
    )
    return metrics
