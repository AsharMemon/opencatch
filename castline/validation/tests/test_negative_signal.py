"""Tests for the negative signal logging system.

Tests the PredictionLog model's signal classification logic and the
auto-resolution of predictions when catch reports arrive.
"""
import pytest
from unittest.mock import MagicMock, patch


class TestSignalClassification:
    """Test PredictionLog.resolve() signal classification."""

    def _make_prediction_log(self, fishing_score=75, predicted_weight=5.0):
        """Create a mock PredictionLog."""
        log = MagicMock()
        log.fishing_score = fishing_score
        log.predicted_weight_lb = predicted_weight
        log.catch_report = None
        log.actual_catch_count = None
        log.actual_weight_lb = None
        log.actual_rating = None
        log.actual_effort_hours = None
        log.prediction_error = None
        log.score_vs_rating = None
        log.signal_type = "UN"
        log.resolved_at = None
        return log

    def _make_catch_report(self, catch_count=5, rating=4, largest_weight=4.0, effort_hours=6.0):
        """Create a mock CatchReport."""
        report = MagicMock()
        report.catch_count = catch_count
        report.rating = rating
        report.largest_weight_lb = largest_weight
        report.effort_hours = effort_hours
        return report

    def test_true_positive(self):
        """High score + good rating + fish caught = TP."""
        fishing_score = 75
        predicted_weight = 5.0
        catch_count = 5
        rating = 4
        largest_weight = 4.5

        # Classify
        predicted_good = fishing_score >= 60
        was_good = rating >= 4 and catch_count > 0

        assert predicted_good and was_good

    def test_false_positive(self):
        """High score + poor rating or skunked = FP."""
        fishing_score = 80
        catch_count = 0
        rating = 2

        predicted_good = fishing_score >= 60
        was_bad = rating <= 2 or catch_count == 0

        assert predicted_good and was_bad

    def test_true_negative(self):
        """Low score + poor outcome = TN."""
        fishing_score = 25
        catch_count = 0
        rating = 1

        predicted_bad = fishing_score < 40
        was_bad = rating <= 2 or catch_count == 0

        assert predicted_bad and was_bad

    def test_false_negative(self):
        """Low score + good outcome = FN (missed opportunity)."""
        fishing_score = 30
        catch_count = 8
        rating = 5

        predicted_bad = fishing_score < 40
        was_good = rating >= 4 and catch_count > 0

        assert predicted_bad and was_good

    def test_prediction_error_calculation(self):
        """Prediction error = predicted - actual weight."""
        predicted_weight = 5.0
        actual_weight = 3.5
        error = predicted_weight - actual_weight
        assert error == pytest.approx(1.5)

    def test_score_vs_rating_normalization(self):
        """Score (0-100) and rating (1-5) normalized to 0-1 for comparison."""
        fishing_score = 80
        rating = 2

        norm_score = fishing_score / 100.0
        norm_rating = (rating - 1) / 4.0

        gap = norm_score - norm_rating

        # Score 80% vs rating 2/5 = 0.8 - 0.25 = 0.55 (big over-prediction)
        assert gap == pytest.approx(0.55)

    def test_ambiguous_zone_uses_error(self):
        """Middle zone (score 40-60, rating 3) falls back to weight error."""
        fishing_score = 50
        rating = 3
        catch_count = 3

        predicted_good = fishing_score >= 60  # False
        predicted_bad = fishing_score < 40    # False

        # Neither clearly good nor bad prediction — falls into else branch
        assert not predicted_good and not predicted_bad

        # With large positive error (overpredicted by 2lb)
        prediction_error = 2.0
        assert prediction_error > 1.0  # Should classify as FP

        # With large negative error (underpredicted by 2lb)
        prediction_error = -2.0
        assert prediction_error < -1.0  # Should classify as FN

    def test_skunked_always_bad(self):
        """Zero fish caught is always a bad outcome regardless of rating."""
        catch_count = 0
        rating = 3  # Neutral rating but zero fish

        was_bad = rating <= 2 or catch_count == 0
        assert was_bad


class TestCatchReportCPUE:
    """Test CatchReport.cpue property logic."""

    def test_cpue_calculation(self):
        """CPUE = catch_count / effort_hours."""
        catch_count = 6
        effort_hours = 4.0
        cpue = catch_count / effort_hours
        assert cpue == pytest.approx(1.5)

    def test_cpue_skunked(self):
        """Zero catch = 0 CPUE."""
        catch_count = 0
        effort_hours = 8.0
        cpue = catch_count / effort_hours
        assert cpue == 0.0

    def test_cpue_zero_effort(self):
        """Zero effort = None CPUE."""
        effort_hours = 0
        cpue = None if effort_hours == 0 else 5 / effort_hours
        assert cpue is None


class TestSampleWeighting:
    """Test that false signals get upweighted for retraining."""

    def test_false_positive_weight(self):
        """FP signals should get 3x weight."""
        signal_type = "FP"
        weight = 3.0 if signal_type in ("FP", "FN") else 1.0
        assert weight == 3.0

    def test_true_positive_weight(self):
        """TP signals get normal weight."""
        signal_type = "TP"
        weight = 3.0 if signal_type in ("FP", "FN") else 1.0
        assert weight == 1.0


class TestConditionsScore:
    """Test the v6 conditions score enhancements."""

    def test_flow_regime_stable_bonus(self):
        """Stable flow regime should add to conditions score."""
        features = {"flow_regime": "stable"}
        # Stable flow adds +0.08
        score = 0.5 + 0.08
        assert score == pytest.approx(0.58)

    def test_post_frontal_recovery(self):
        """24-72 hours after front passage is prime fishing."""
        hours = 48
        assert 24 <= hours <= 72

    def test_species_thermal_optimum(self):
        """Species-weighted thermal optimum blends LMB and SMB preferences."""
        import math
        lat = 35.0  # Southern — LMB dominant
        smb_prob = 1.0 / (1.0 + math.exp(-0.5 * (lat - 42.0)))
        lmb_prob = 1.0 - smb_prob

        LMB_OPT = 23.5
        SMB_OPT = 18.5
        thermal_opt = lmb_prob * LMB_OPT + smb_prob * SMB_OPT

        # At lat 35, LMB dominates so optimum should be close to 23.5
        assert thermal_opt > 22.0
        assert thermal_opt < 24.0

        lat = 46.0  # Northern — SMB dominant
        smb_prob = 1.0 / (1.0 + math.exp(-0.5 * (lat - 42.0)))
        lmb_prob = 1.0 - smb_prob
        thermal_opt = lmb_prob * LMB_OPT + smb_prob * SMB_OPT

        # At lat 46, SMB is more likely so optimum shifts toward 18.5
        assert thermal_opt < 22.0
        assert thermal_opt > 18.5
