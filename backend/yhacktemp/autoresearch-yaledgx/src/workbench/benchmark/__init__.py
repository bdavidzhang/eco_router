"""Benchmark engine — power, thermal, system, quality, carbon, and the full harness."""

from workbench.benchmark.carbon import SciConfig, SciScore, compute_sci, sci_at_scale
from workbench.benchmark.harness import run_benchmark
from workbench.benchmark.power import PowerMonitor, PowerTrace
from workbench.benchmark.system import SystemMonitor, SystemSnapshot
from workbench.benchmark.thermal import ThermalAbortError, ThermalMonitor
from workbench.benchmark.quality import evaluate_quality

__all__ = [
    "PowerMonitor",
    "PowerTrace",
    "SciConfig",
    "SciScore",
    "SystemMonitor",
    "SystemSnapshot",
    "ThermalAbortError",
    "ThermalMonitor",
    "compute_sci",
    "evaluate_quality",
    "run_benchmark",
    "sci_at_scale",
]
