"""Tests for the multi-species system (v7).

Tests species catalog, probability calculations, thermal comfort,
multi-species features, and species group classification.
"""
import math
import pytest

from castline.services.site_prior import (
    SPECIES_CATALOG,
    compute_species_probabilities,
    compute_multi_species_features,
    get_species_thermal_comfort,
    get_species_thermal_optimum,
    _species_group,
    _FRONTAL_RESPONSE_SCORES,
    _PRESSURE_SENSITIVITY_SCORES,
    _FLOW_PREFERENCE_SCORES,
)


class TestSpeciesCatalog:
    """Verify species catalog completeness and data quality."""

    def test_catalog_has_minimum_species(self):
        assert len(SPECIES_CATALOG) >= 30

    def test_all_species_have_required_fields(self):
        required = {
            "common_name", "optimal_temp_c", "temp_range_c",
            "pressure_sensitivity", "spawn_months", "peak_feed_months",
            "depth_pref_m", "habitat", "lat_range",
            "frontal_response", "flow_preference",
        }
        for key, sp in SPECIES_CATALOG.items():
            missing = required - set(sp.keys())
            assert not missing, f"{key} missing fields: {missing}"

    def test_temp_range_surrounds_optimal(self):
        for key, sp in SPECIES_CATALOG.items():
            lo, hi = sp["temp_range_c"]
            opt = sp["optimal_temp_c"]
            assert lo < opt < hi, f"{key}: temp_range {lo}-{hi} doesn't surround optimal {opt}"

    def test_lat_range_valid(self):
        for key, sp in SPECIES_CATALOG.items():
            lo, hi = sp["lat_range"]
            assert 20.0 <= lo < hi <= 55.0, f"{key}: invalid lat_range {lo}-{hi}"

    def test_known_species_present(self):
        expected = [
            "largemouth_bass", "smallmouth_bass", "spotted_bass",
            "walleye", "channel_catfish", "black_crappie",
            "rainbow_trout", "northern_pike", "striped_bass",
        ]
        for sp in expected:
            assert sp in SPECIES_CATALOG, f"{sp} not in catalog"

    def test_frontal_response_valid(self):
        for key, sp in SPECIES_CATALOG.items():
            assert sp["frontal_response"] in _FRONTAL_RESPONSE_SCORES, \
                f"{key}: invalid frontal_response '{sp['frontal_response']}'"

    def test_pressure_sensitivity_valid(self):
        for key, sp in SPECIES_CATALOG.items():
            assert sp["pressure_sensitivity"] in _PRESSURE_SENSITIVITY_SCORES, \
                f"{key}: invalid pressure_sensitivity"

    def test_flow_preference_valid(self):
        for key, sp in SPECIES_CATALOG.items():
            assert sp["flow_preference"] in _FLOW_PREFERENCE_SCORES, \
                f"{key}: invalid flow_preference"


class TestThermalComfort:
    """Test species thermal comfort calculations."""

    def test_optimal_temp_returns_max_comfort(self):
        for key, sp in SPECIES_CATALOG.items():
            comfort = get_species_thermal_comfort(key, sp["optimal_temp_c"])
            assert comfort == pytest.approx(1.0), f"{key} at optimal temp should be 1.0"

    def test_outside_range_returns_zero(self):
        comfort = get_species_thermal_comfort("largemouth_bass", 5.0)
        assert comfort == 0.0

    def test_comfort_decreases_from_optimal(self):
        opt = SPECIES_CATALOG["walleye"]["optimal_temp_c"]
        c1 = get_species_thermal_comfort("walleye", opt)
        c2 = get_species_thermal_comfort("walleye", opt - 3)
        c3 = get_species_thermal_comfort("walleye", opt - 6)
        assert c1 > c2 > c3

    def test_unknown_species_returns_nan(self):
        result = get_species_thermal_comfort("nonexistent_fish", 20.0)
        assert math.isnan(result)

    def test_nan_temp_returns_nan(self):
        result = get_species_thermal_comfort("largemouth_bass", float("nan"))
        assert math.isnan(result)

    def test_cold_water_species_at_warm_temp(self):
        # Trout should be uncomfortable at 25°C
        comfort = get_species_thermal_comfort("rainbow_trout", 25.0)
        assert comfort == 0.0  # Above range

    def test_warm_water_species_at_cold_temp(self):
        # Channel catfish should be uncomfortable at 10°C
        comfort = get_species_thermal_comfort("channel_catfish", 10.0)
        assert comfort == 0.0  # Below range


class TestSpeciesProbabilities:
    """Test location-based species probability estimation."""

    def test_florida_lmb_dominant(self):
        probs = compute_species_probabilities(28.5, -81.5, 5.0, 200.0)
        assert probs["largemouth_bass"] > probs.get("smallmouth_bass", 0)
        assert probs["largemouth_bass"] > 0.2

    def test_minnesota_walleye_prominent(self):
        probs = compute_species_probabilities(46.5, -94.0, 12.0, 500.0)
        assert probs["walleye"] > 0.1
        assert probs["smallmouth_bass"] > 0.1

    def test_probabilities_sum_to_one(self):
        probs = compute_species_probabilities(35.0, -85.0, 8.0, 300.0)
        total = sum(probs.values())
        assert total == pytest.approx(1.0, abs=0.01)

    def test_extreme_south_no_trout(self):
        probs = compute_species_probabilities(26.0, -81.0, 3.0, 100.0)
        assert probs.get("lake_trout", 0) == 0.0
        assert probs.get("brook_trout", 0) == 0.0

    def test_extreme_north_no_alligator_gar(self):
        probs = compute_species_probabilities(48.0, -92.0, 10.0, 500.0)
        assert probs.get("alligator_gar", 0) == 0.0


class TestMultiSpeciesFeatures:
    """Test comprehensive feature computation."""

    def test_returns_all_keys(self):
        feats = compute_multi_species_features(35.0, -85.0)
        expected_keys = {
            "weighted_optimal_temp", "weighted_thermal_comfort",
            "pressure_sensitivity_score", "frontal_response_score",
            "flow_preference_score", "spawn_activity",
            "peak_feed_activity", "dominant_species_group",
            "top_species_prob",
        }
        assert expected_keys.issubset(set(feats.keys()))

    def test_weighted_temp_in_range(self):
        feats = compute_multi_species_features(35.0, -85.0)
        assert 10.0 <= feats["weighted_optimal_temp"] <= 30.0

    def test_spawn_activity_in_spring(self):
        feats = compute_multi_species_features(35.0, -85.0, month=4)
        assert feats["spawn_activity"] > 0.3  # April = heavy spawn

    def test_no_spawn_in_summer(self):
        feats = compute_multi_species_features(35.0, -85.0, month=8)
        assert feats["spawn_activity"] < 0.3  # August = minimal spawn

    def test_thermal_comfort_with_water_temp(self):
        feats = compute_multi_species_features(35.0, -85.0, water_temp_c=22.0, month=5)
        assert not math.isnan(feats["weighted_thermal_comfort"])
        assert 0.0 <= feats["weighted_thermal_comfort"] <= 1.0

    def test_thermal_comfort_nan_without_water_temp(self):
        feats = compute_multi_species_features(35.0, -85.0, month=5)
        assert math.isnan(feats["weighted_thermal_comfort"])

    def test_bass_dominates_se_us(self):
        feats = compute_multi_species_features(33.0, -85.0)
        assert feats["dominant_species_group"] == "bass"

    def test_pressure_sensitivity_score_range(self):
        feats = compute_multi_species_features(35.0, -85.0)
        assert 0.0 <= feats["pressure_sensitivity_score"] <= 1.0


class TestSpeciesGroups:
    """Test species group classification."""

    def test_bass_group(self):
        assert _species_group("largemouth_bass") == "bass"
        assert _species_group("smallmouth_bass") == "bass"
        assert _species_group("striped_bass") == "bass"
        assert _species_group("peacock_bass") == "bass"
        assert _species_group("shoal_bass") == "bass"
        assert _species_group("guadalupe_bass") == "bass"

    def test_panfish_group(self):
        assert _species_group("black_crappie") == "panfish"
        assert _species_group("bluegill") == "panfish"
        assert _species_group("yellow_perch") == "panfish"
        assert _species_group("pumpkinseed") == "panfish"
        assert _species_group("rock_bass") == "panfish"

    def test_catfish_group(self):
        assert _species_group("channel_catfish") == "catfish"
        assert _species_group("blue_catfish") == "catfish"
        assert _species_group("bullhead_catfish") == "catfish"

    def test_predator_group(self):
        assert _species_group("walleye") == "predator"
        assert _species_group("northern_pike") == "predator"
        assert _species_group("saugeye") == "predator"
        assert _species_group("chain_pickerel") == "predator"
        assert _species_group("tiger_muskie") == "predator"

    def test_trout_group(self):
        assert _species_group("rainbow_trout") == "trout"
        assert _species_group("chinook_salmon") == "trout"
        assert _species_group("cutthroat_trout") == "trout"
        assert _species_group("atlantic_salmon") == "trout"

    def test_coastal_group(self):
        assert _species_group("red_drum") == "coastal"
        assert _species_group("snook") == "coastal"
        assert _species_group("tarpon") == "coastal"
        assert _species_group("spotted_seatrout") == "coastal"

    def test_other_group(self):
        assert _species_group("common_carp") == "other"
        assert _species_group("bowfin") == "other"
        assert _species_group("freshwater_drum") == "other"
        assert _species_group("lake_sturgeon") == "other"

    def test_catalog_has_65_species(self):
        assert len(SPECIES_CATALOG) >= 65
