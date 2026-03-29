"""Tests for SCI (Software Carbon Intensity) calculator.

SCI = (E x I) + M per R
The Green Software Foundation's ISO standard for software carbon scoring.
"""

import pytest

from workbench.benchmark.carbon import (
    CARBON_INTENSITY_PRESETS,
    SciConfig,
    SciScore,
    compute_sci,
    sci_at_scale,
)


class TestSciConfig:
    def test_default_config(self):
        config = SciConfig()
        assert config.carbon_intensity_gco2_per_kwh == 400.0
        assert config.embodied_gco2_per_token == 0.00003
        assert config.functional_unit == "per_token"

    def test_from_region(self):
        config = SciConfig.from_region("eu_france")
        assert config.carbon_intensity_gco2_per_kwh == 55.0

    def test_from_region_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown region"):
            SciConfig.from_region("narnia")

    def test_for_dgx_spark(self):
        config = SciConfig.for_dgx_spark(region="us_oregon")
        assert config.carbon_intensity_gco2_per_kwh == 80.0
        assert config.embodied_gco2_per_token > 0

    def test_renewable_100_zero_intensity(self):
        config = SciConfig.from_region("renewable_100")
        assert config.carbon_intensity_gco2_per_kwh == 0.0


class TestComputeSci:
    def test_basic_computation(self):
        """Worked example from the SCI poster."""
        # 0.0005 kWh per request, 400 gCO2/kWh, 0.05 gCO2 embodied
        config = SciConfig(
            carbon_intensity_gco2_per_kwh=400.0,
            embodied_gco2_per_token=0.05,
        )
        # 0.0005 kWh = 1.8 J
        energy_j = 0.0005 * 3_600_000  # Convert kWh to J
        result = compute_sci(energy_j, config)

        assert result.energy_kwh == pytest.approx(0.0005, rel=1e-6)
        assert result.operational_carbon_g == pytest.approx(0.2, rel=1e-6)
        assert result.embodied_carbon_g == pytest.approx(0.05, rel=1e-6)
        assert result.sci == pytest.approx(0.25, rel=1e-6)

    def test_zero_energy(self):
        result = compute_sci(0.0)
        assert result.operational_carbon_g == 0.0
        assert result.sci == result.embodied_carbon_g  # Only M remains

    def test_renewable_grid(self):
        """On 100% renewables, operational carbon is zero."""
        config = SciConfig(carbon_intensity_gco2_per_kwh=0.0)
        result = compute_sci(100.0, config)
        assert result.operational_carbon_g == 0.0
        assert result.sci == config.embodied_gco2_per_token

    def test_high_coal_grid(self):
        """Poland's coal grid should produce much higher SCI."""
        clean = compute_sci(1.0, SciConfig.from_region("eu_sweden"))
        dirty = compute_sci(1.0, SciConfig.from_region("eu_poland"))
        assert dirty.sci > clean.sci
        assert dirty.operational_carbon_g > clean.operational_carbon_g

    def test_breakdown_percentages(self):
        config = SciConfig(
            carbon_intensity_gco2_per_kwh=400.0,
            embodied_gco2_per_token=0.01,
        )
        result = compute_sci(3.6, config)  # 0.001 kWh
        assert result.operational_pct + result.embodied_pct == pytest.approx(100.0)

    def test_default_config_used(self):
        """compute_sci without config uses US average defaults."""
        result = compute_sci(1.0)
        assert result.carbon_intensity == 400.0


class TestSciAtScale:
    def test_scale_projection(self):
        scale = sci_at_scale(0.25, tokens_per_day=1_000_000)
        assert scale["tokens_per_day"] == 1_000_000
        assert scale["gco2_per_day"] == 250_000.0
        assert scale["kg_co2_per_day"] == 250.0
        assert scale["driving_miles_equivalent"] > 0

    def test_zero_sci(self):
        scale = sci_at_scale(0.0)
        assert scale["gco2_per_day"] == 0.0
        assert scale["driving_miles_equivalent"] == 0.0


class TestCarbonIntensityPresets:
    def test_all_presets_positive_or_zero(self):
        for region, intensity in CARBON_INTENSITY_PRESETS.items():
            assert intensity >= 0, f"Region {region} has negative intensity"

    def test_iceland_greenest(self):
        """Iceland should be among the greenest (geothermal + hydro)."""
        non_zero = {
            k: v for k, v in CARBON_INTENSITY_PRESETS.items()
            if v > 0 and k != "renewable_100"
        }
        assert CARBON_INTENSITY_PRESETS["iceland"] == min(non_zero.values())

    def test_renewable_is_zero(self):
        assert CARBON_INTENSITY_PRESETS["renewable_100"] == 0.0
