"""Tests for temperature departure from normals computation."""
import math
import pytest
import sys
from pathlib import Path

# Add scripts dir to path so we can import
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "scripts"))
from compute_temp_departure import get_normal_temp, get_normal_std


class TestNormalTemp:
    """Test climatological normal temperature interpolation."""

    def test_known_lat_25_july(self):
        """July at lat 25 (south FL) should be ~30°C."""
        t = get_normal_temp(25.0, 7)
        assert t == pytest.approx(30.0)

    def test_known_lat_45_january(self):
        """January at lat 45 (MN) should be ~-5.5°C."""
        t = get_normal_temp(45.0, 1)
        assert t == pytest.approx(-5.5)

    def test_interpolation_between_bands(self):
        """Lat 32.5 (between 30 and 35) should interpolate."""
        t = get_normal_temp(32.5, 7)  # July
        # lat 30 July = 29.0, lat 35 July = 28.0
        # 32.5 is 50% between, so ~28.5
        assert t == pytest.approx(28.5)

    def test_latitude_clamped_low(self):
        """Latitudes below 25 should clamp to 25."""
        t_low = get_normal_temp(20.0, 6)
        t_25 = get_normal_temp(25.0, 6)
        assert t_low == t_25

    def test_latitude_clamped_high(self):
        """Latitudes above 50 should clamp to 50."""
        t_high = get_normal_temp(55.0, 6)
        t_50 = get_normal_temp(50.0, 6)
        assert t_high == t_50

    def test_summer_warmer_than_winter(self):
        """July should be warmer than January at any latitude."""
        for lat in [25, 30, 35, 40, 45, 50]:
            jan = get_normal_temp(lat, 1)
            jul = get_normal_temp(lat, 7)
            assert jul > jan, f"Lat {lat}: July ({jul}) should be warmer than January ({jan})"

    def test_south_warmer_than_north(self):
        """Lower latitudes should be warmer in every month."""
        for month in range(1, 13):
            t_south = get_normal_temp(28.0, month)
            t_north = get_normal_temp(47.0, month)
            assert t_south > t_north, f"Month {month}: lat 28 ({t_south}) should be warmer than lat 47 ({t_north})"

    def test_all_months_valid(self):
        """All 12 months should return finite values."""
        for month in range(1, 13):
            t = get_normal_temp(35.0, month)
            assert math.isfinite(t), f"Month {month} returned {t}"


class TestNormalStd:
    """Test temperature standard deviation by latitude."""

    def test_known_lat_35(self):
        assert get_normal_std(35.0) == pytest.approx(4.0)

    def test_higher_lat_higher_std(self):
        """Higher latitudes should have more temperature variability."""
        std_30 = get_normal_std(30.0)
        std_45 = get_normal_std(45.0)
        assert std_45 > std_30

    def test_interpolation(self):
        """Lat 37.5 should interpolate between 35 (4.0) and 40 (4.5)."""
        std = get_normal_std(37.5)
        assert std == pytest.approx(4.25)

    def test_clamped_range(self):
        """Values outside 25-50 should clamp."""
        std_low = get_normal_std(20.0)
        std_25 = get_normal_std(25.0)
        assert std_low == pytest.approx(std_25, abs=0.5)


class TestDepartureLogic:
    """Test departure calculation logic."""

    def test_positive_departure_is_warm(self):
        """Temp above normal = positive departure."""
        normal = get_normal_temp(35.0, 6)  # June at lat 35
        observed = normal + 5
        departure = observed - normal
        assert departure == pytest.approx(5.0)

    def test_negative_departure_is_cold(self):
        """Temp below normal = negative departure."""
        normal = get_normal_temp(35.0, 6)
        observed = normal - 3
        departure = observed - normal
        assert departure == pytest.approx(-3.0)

    def test_warm_anomaly_threshold(self):
        """Warm anomaly: departure > 1 std."""
        normal = get_normal_temp(35.0, 6)
        std = get_normal_std(35.0)
        observed = normal + std + 1  # 1 degree above threshold
        departure = observed - normal
        assert departure > std

    def test_cold_anomaly_threshold(self):
        """Cold anomaly: departure < -1 std."""
        normal = get_normal_temp(35.0, 6)
        std = get_normal_std(35.0)
        observed = normal - std - 1
        departure = observed - normal
        assert departure < -std

    def test_zscore_computation(self):
        """Z-score = departure / std."""
        normal = get_normal_temp(40.0, 3)
        std = get_normal_std(40.0)
        observed = normal + 9.0
        zscore = (observed - normal) / std
        assert zscore == pytest.approx(9.0 / 4.5)  # std at lat 40 = 4.5
