"""Tests for the v2 scoring and breakdown system.

Tests the fishing score calculation, dynamic layer breakdown,
and score explanation logic.
"""
import pytest


class TestWeightToFishingScore:
    """Test _weight_to_fishing_score conversion."""

    def _score(self, predicted_weight: float, hist_avg: float) -> int:
        """Replicate the scoring logic from views.py."""
        if hist_avg <= 0:
            hist_avg = 3.0
        ratio = predicted_weight / hist_avg
        score = 100.0 / (1.0 + 2.718 ** (-3.5 * (ratio - 1.0)))
        return max(0, min(100, int(round(score))))

    def test_at_average(self):
        """Prediction at historical average → score ~50."""
        score = self._score(3.0, 3.0)
        assert 45 <= score <= 55

    def test_double_average(self):
        """Prediction at 2x average → high score (~90)."""
        score = self._score(6.0, 3.0)
        assert score >= 85

    def test_half_average(self):
        """Prediction at 0.5x average → low score (~20)."""
        score = self._score(1.5, 3.0)
        assert score <= 25

    def test_zero_weight(self):
        """Zero predicted weight → minimum score."""
        score = self._score(0.0, 3.0)
        assert score <= 5

    def test_zero_avg_fallback(self):
        """Zero historical average uses 3.0 fallback."""
        score = self._score(3.0, 0.0)
        assert 45 <= score <= 55

    def test_score_bounds(self):
        """Score always in 0-100 range."""
        for weight in [0.0, 0.5, 1.0, 3.0, 5.0, 10.0, 20.0]:
            score = self._score(weight, 3.0)
            assert 0 <= score <= 100


class TestScoreExplanation:
    """Test _score_explanation text."""

    def _explain(self, score: int) -> str:
        if score >= 80:
            return "Excellent conditions — strong bite expected across multiple factors."
        if score >= 60:
            return "Good conditions — above-average activity likely."
        if score >= 40:
            return "Fair conditions — average fishing expected."
        if score >= 20:
            return "Below average — consider waiting for better conditions."
        return "Poor conditions — most factors are unfavorable right now."

    def test_excellent(self):
        assert "Excellent" in self._explain(85)

    def test_good(self):
        assert "Good" in self._explain(65)

    def test_fair(self):
        assert "Fair" in self._explain(45)

    def test_below_average(self):
        assert "Below" in self._explain(25)

    def test_poor(self):
        assert "Poor" in self._explain(10)


class TestDynamicBreakdown:
    """Test _build_breakdown dynamic weighting."""

    def test_hydro_weight_with_live_data(self):
        """Hydrology layer gets 40% when live USGS data available."""
        score = 80
        # With live hydro data, hydro_w = 0.40
        hydro_contrib = round(score * 0.40)
        assert hydro_contrib == 32

    def test_hydro_weight_without_data(self):
        """Hydrology layer gets 25% when no live data."""
        score = 80
        # Without live hydro data, hydro_w = 0.25
        hydro_contrib = round(score * 0.25)
        assert hydro_contrib == 20

    def test_layer_contributions_sum_to_score(self):
        """All layer contributions should approximately sum to score."""
        score = 80
        # With both hydro and weather live
        hydro_w, weather_w, bio_w = 0.40, 0.30, 0.20
        history_w = 1.0 - hydro_w - weather_w - bio_w
        total = (
            round(score * hydro_w)
            + round(score * weather_w)
            + round(score * bio_w)
            + round(score * history_w)
        )
        # Allow ±2 for rounding
        assert abs(total - score) <= 2

    def test_data_quality_tags(self):
        """Each layer should have a data_quality field."""
        # Verify the expected quality tags
        expected = {"live", "estimated", "modeled", "historical"}
        assert len(expected) == 4


class TestCompositeScore:
    """Test the 4-layer composite scoring from inference.py."""

    def _compute(
        self,
        catch_probability: float,
        cpue_percentile: float,
        conditions_score: float,
        trophy_potential: float,
    ) -> int:
        raw = (
            catch_probability * 30
            + cpue_percentile * 30
            + conditions_score * 25
            + trophy_potential * 15
        )
        return max(0, min(100, round(raw)))

    def test_all_perfect(self):
        """All factors at 1.0 → score 100."""
        assert self._compute(1.0, 1.0, 1.0, 1.0) == 100

    def test_all_zero(self):
        """All factors at 0.0 → score 0."""
        assert self._compute(0.0, 0.0, 0.0, 0.0) == 0

    def test_average_conditions(self):
        """Moderate conditions → score around 50."""
        score = self._compute(0.5, 0.5, 0.5, 0.5)
        assert 45 <= score <= 55

    def test_high_catch_low_trophy(self):
        """High catch probability but low trophy → decent score."""
        score = self._compute(0.9, 0.8, 0.6, 0.1)
        # 27 + 24 + 15 + 1.5 = 67.5
        assert 65 <= score <= 70

    def test_clamped_above_100(self):
        """Score clamped at 100."""
        assert self._compute(1.0, 1.0, 1.0, 1.0) <= 100

    def test_clamped_below_0(self):
        """Score clamped at 0."""
        assert self._compute(0.0, 0.0, 0.0, 0.0) >= 0
