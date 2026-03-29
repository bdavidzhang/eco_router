"""Software Carbon Intensity (SCI) calculator.

SCI = (E × I) + M  per R

Where:
    E = Energy consumed (kWh per functional unit)
    I = Carbon intensity of the grid (gCO₂/kWh)
    M = Embodied emissions (gCO₂ per functional unit)
    R = Functional unit (per token, per request, per user)

Based on the Green Software Foundation's ISO standard.
"You can't optimize what you don't measure."

Reference: https://sci-guide.greensoftware.foundation/
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


# Regional carbon intensity presets (gCO₂ per kWh)
# Source: Electricity Maps, IEA, WattTime (2024 averages)
CARBON_INTENSITY_PRESETS: dict[str, float] = {
    "us_average": 400.0,       # US national average (mixed grid)
    "us_california": 210.0,    # CA — high solar/wind penetration
    "us_texas": 350.0,         # ERCOT — gas + wind
    "us_virginia": 310.0,      # Major cloud region (us-east-1)
    "us_oregon": 80.0,         # Hydro-heavy (us-west-2)
    "eu_average": 230.0,       # EU average
    "eu_france": 55.0,         # Nuclear-dominant
    "eu_germany": 340.0,       # Still coal-heavy
    "eu_sweden": 25.0,         # Hydro + nuclear, the dream
    "eu_poland": 650.0,        # Coal-dominant
    "uk": 200.0,               # Wind + gas
    "china_average": 530.0,    # Coal-heavy
    "india_average": 630.0,    # Coal-dominant
    "japan": 450.0,            # Gas + coal
    "brazil": 70.0,            # Hydro-dominant
    "iceland": 10.0,           # Geothermal + hydro (greenest)
    "australia": 510.0,        # Coal + gas
    "canada": 120.0,           # Hydro-heavy
    "renewable_100": 0.0,      # 100% renewable — the goal
}


@dataclass
class SciConfig:
    """Configuration for SCI computation.

    All values are per functional unit (R).
    For this workbench, R = 1 token by default.
    """

    # I: Carbon intensity of the electricity grid (gCO₂ per kWh)
    carbon_intensity_gco2_per_kwh: float = 400.0  # US average default

    # M: Embodied emissions per functional unit (gCO₂)
    # For DGX Spark: ~200 kg CO₂ manufacturing ÷ 5-year lifespan
    #   = 40 kg/year = ~0.00000127 gCO₂/second
    #   At ~50 tokens/sec = ~0.0000000254 gCO₂/token
    # We use a conservative default that's easy to override.
    embodied_gco2_per_token: float = 0.00003

    # R: What's the functional unit?
    # "per_token" for LLM workloads (our default)
    # Could also be "per_request" for API-style measurement
    functional_unit: str = "per_token"

    # Complexity energy scale factor: scales E upward based on cyclomatic
    # complexity.  E_adjusted = E × (1 + complexity_energy_scale × complexity)
    # Default 0.0 = no effect (backwards-compatible).
    # A value of 0.01 means each unit of cyclomatic complexity adds 1% to E.
    complexity_energy_scale: float = 0.0

    @classmethod
    def from_region(cls, region: str, **kwargs) -> SciConfig:
        """Create config from a region preset."""
        intensity = CARBON_INTENSITY_PRESETS.get(region)
        if intensity is None:
            available = ", ".join(sorted(CARBON_INTENSITY_PRESETS.keys()))
            raise ValueError(
                f"Unknown region '{region}'. Available: {available}"
            )
        return cls(carbon_intensity_gco2_per_kwh=intensity, **kwargs)

    @classmethod
    def for_dgx_spark(
        cls,
        region: str = "us_average",
        lifespan_years: float = 5.0,
        manufacturing_co2_kg: float = 200.0,
        avg_tokens_per_sec: float = 50.0,
    ) -> SciConfig:
        """Create config tuned for DGX Spark hardware.

        Computes embodied emissions (M) from hardware lifecycle data.
        """
        intensity = CARBON_INTENSITY_PRESETS.get(region, 400.0)
        # manufacturing_co2_kg → gCO₂/second → gCO₂/token
        seconds_in_lifespan = lifespan_years * 365.25 * 24 * 3600
        gco2_per_second = (manufacturing_co2_kg * 1000) / seconds_in_lifespan
        gco2_per_token = gco2_per_second / avg_tokens_per_sec
        return cls(
            carbon_intensity_gco2_per_kwh=intensity,
            embodied_gco2_per_token=gco2_per_token,
        )


@dataclass
class SciScore:
    """Computed SCI score with full breakdown."""

    sci: float                    # Total SCI (gCO₂ per functional unit)
    operational_carbon_g: float   # E × I component (gCO₂)
    embodied_carbon_g: float      # M component (gCO₂)
    energy_kwh: float             # E in kWh (converted from joules, complexity-adjusted)
    carbon_intensity: float       # I (gCO₂/kWh) used
    cyclomatic_complexity: int = 0  # complexity score used to adjust E (0 = no adjustment)

    @property
    def operational_pct(self) -> float:
        """What % of the SCI is from operational energy vs embodied?"""
        return (self.operational_carbon_g / self.sci * 100) if self.sci > 0 else 0.0

    @property
    def embodied_pct(self) -> float:
        return (self.embodied_carbon_g / self.sci * 100) if self.sci > 0 else 0.0


def compute_sci(
    energy_per_token_j: float,
    config: SciConfig | None = None,
    cyclomatic_complexity: int = 0,
) -> SciScore:
    """Compute Software Carbon Intensity for a single token.

    Args:
        energy_per_token_j: Energy consumed per token in Joules.
        config: SCI parameters (grid intensity, embodied emissions).
        cyclomatic_complexity: Optional complexity score from
            :func:`compute_cyclomatic_complexity`.  When
            ``config.complexity_energy_scale > 0`` this scales E upward:
            ``E_adjusted = E × (1 + scale × complexity)``.

    Returns:
        Full SCI breakdown.

    The formula: SCI = (E × I) + M
        E = energy_per_token_j / 3_600_000  (convert J → kWh)
            optionally scaled by cyclomatic complexity
        I = config.carbon_intensity_gco2_per_kwh
        M = config.embodied_gco2_per_token
    """
    if config is None:
        config = SciConfig()

    # E: Convert joules to kWh, then apply complexity scaling
    energy_kwh = energy_per_token_j / 3_600_000
    if config.complexity_energy_scale and cyclomatic_complexity:
        energy_kwh *= 1.0 + config.complexity_energy_scale * cyclomatic_complexity

    # E × I: Operational carbon (gCO₂)
    operational = energy_kwh * config.carbon_intensity_gco2_per_kwh

    # M: Embodied carbon (gCO₂)
    embodied = config.embodied_gco2_per_token

    # SCI = (E × I) + M
    sci_total = operational + embodied

    return SciScore(
        sci=sci_total,
        operational_carbon_g=operational,
        embodied_carbon_g=embodied,
        energy_kwh=energy_kwh,
        carbon_intensity=config.carbon_intensity_gco2_per_kwh,
        cyclomatic_complexity=cyclomatic_complexity,
    )


def sci_at_scale(
    sci_per_token: float,
    tokens_per_day: int = 1_000_000,
) -> dict[str, float]:
    """Project SCI to real-world scale for intuition.

    Returns gCO₂/day, kgCO₂/day, and driving-miles equivalent.
    """
    gco2_per_day = sci_per_token * tokens_per_day
    kg_per_day = gco2_per_day / 1000
    # Average car: ~404 gCO₂/mile (EPA)
    driving_miles = gco2_per_day / 404
    return {
        "tokens_per_day": tokens_per_day,
        "gco2_per_day": round(gco2_per_day, 2),
        "kg_co2_per_day": round(kg_per_day, 4),
        "driving_miles_equivalent": round(driving_miles, 2),
    }


# ── Code complexity & dependency analysis ────────────────────────────────────


@dataclass
class CodeMetrics:
    """Code complexity and dependency metrics for a set of files.

    Designed to pair with SCI scoring so that carbon footprint can be
    weighted or normalised against how complex / coupled the code is.
    """

    loc: int                   # Total lines of code (every line, including blanks/comments)
    cyclomatic_complexity: int # Sum of cyclomatic complexity scores across all files
    external_dep_count: int    # Unique external packages imported (not relative, not own)


def compute_loc(source: str) -> int:
    """Count all lines in *source*, including blank lines and comments.

    Matches the TypeScript definition: every newline-separated element counts.
    """
    return len(source.splitlines())


def compute_cyclomatic_complexity(source: str) -> int:
    """Compute cyclomatic complexity by walking the Python AST.

    Increments the counter for each of the following constructs — every one
    represents an additional independent execution path through the code:

    Construct              | AST node
    -----------------------|-----------------------------
    if statement           | ast.If
    for / async for loop   | ast.For, ast.AsyncFor
    while loop             | ast.While
    match case clause      | ast.match_case  (Python 3.10+)
    ternary expression     | ast.IfExp  (x if cond else y)
    and / or expression    | ast.BoolOp  (short-circuit paths)

    Returns 0 on parse failure (e.g. non-Python or syntax error).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0

    complexity = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While)):
            complexity += 1
        elif isinstance(node, ast.IfExp):        # ternary: a if cond else b
            complexity += 1
        elif isinstance(node, ast.BoolOp):       # and / or chains
            complexity += 1
        # match_case was added in Python 3.10 — guard with getattr for safety
        elif isinstance(node, getattr(ast, "match_case", type(None))):
            complexity += 1

    return complexity


def compute_external_deps(
    source: str,
    own_module_names: set[str] | None = None,
) -> set[str]:
    """Return the set of unique external package names imported by *source*.

    A dependency is classified as **external** when it meets all three criteria:
    1. Not a relative import  (no leading ``.`` — e.g. ``from . import foo``).
    2. Not an absolute path   (``/``-prefixed strings are never valid Python imports,
       but we mirror the TypeScript rule for completeness).
    3. Its top-level package is not in *own_module_names* (i.e. not a sibling
       module within the same feature/package being analysed).

    Returns an empty set on parse failure.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    own = own_module_names or set()
    deps: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                pkg = alias.name.split(".")[0]
                if not pkg.startswith("/") and pkg not in own:
                    deps.add(pkg)
        elif isinstance(node, ast.ImportFrom):
            if (node.level or 0) > 0:   # relative import — skip
                continue
            if node.module:
                pkg = node.module.split(".")[0]
                if not pkg.startswith("/") and pkg not in own:
                    deps.add(pkg)

    return deps


def analyze_code_metrics(
    files: list[str | Path],
    dep_graph: dict[str, list[str]] | None = None,
) -> CodeMetrics:
    """Aggregate LOC, cyclomatic complexity, and external deps across *files*.

    Args:
        files: Python source files to analyse. Each file is read from disk.
        dep_graph: Optional pre-built dependency graph mapping each file path to
                   a list of raw import strings (as they appear in the source).
                   When provided, dependency resolution uses the graph instead of
                   re-parsing — matching the TypeScript implementation's approach.
                   Files within *files* are always treated as internal.

    Returns:
        A single :class:`CodeMetrics` instance with totals across all files.
    """
    # Build the set of "own" module names so intra-feature imports are excluded
    own_module_names: set[str] = {Path(f).stem for f in files}

    total_loc = 0
    total_complexity = 0
    all_external_deps: set[str] = set()

    for fpath in files:
        path = Path(fpath)
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        total_loc += compute_loc(source)
        total_complexity += compute_cyclomatic_complexity(source)

        if dep_graph is not None:
            # Use the pre-built graph; apply the same three-criteria filter
            for dep in dep_graph.get(str(fpath), []):
                if (
                    not dep.startswith(".")
                    and not dep.startswith("/")
                    and dep.split(".")[0] not in own_module_names
                ):
                    all_external_deps.add(dep.split(".")[0])
        else:
            all_external_deps |= compute_external_deps(source, own_module_names)

    return CodeMetrics(
        loc=total_loc,
        cyclomatic_complexity=total_complexity,
        external_dep_count=len(all_external_deps),
    )
