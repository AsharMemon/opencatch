"""Composite fishing score calculator for CASTLINE.

Combines weight-model predictions, CPUE estimates, historical condition
ratios, and trophy potential into a single 0-100 fishing score suitable
for end-user display.

Usage (standalone)::

    python scripts/composite_scorer.py --demo

Usage (import)::

    from scripts.composite_scorer import FishingScoreCalculator

    calc = FishingScoreCalculator()
    result = calc.calculate_score(
        catch_prob=0.72,
        expected_cpue=1.8,
        conditions_ratio=1.10,
        trophy_potential=0.25,
    )
    print(result)  # {'total': 68, 'breakdown': {...}, ...}
"""
from __future__ import annotations

import math
import unittest
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Percentile calibration tables
# ---------------------------------------------------------------------------
# Built from the CASTLINE historical dataset distributions.  Each list is
# a sorted sequence of breakpoints; the percentile of a value is its rank
# position within the list (linear interpolation between neighbours).

_CPUE_PERCENTILES: list[float] = [
    0.0, 0.05, 0.12, 0.20, 0.30, 0.42, 0.55, 0.70, 0.90, 1.10,
    1.30, 1.55, 1.80, 2.10, 2.50, 3.00, 3.60, 4.30, 5.20, 6.50,
    8.00,
]
"""21 breakpoints → 20 equal-width percentile bins (0-100)."""

_CONDITIONS_RATIO_PERCENTILES: list[float] = [
    0.50, 0.60, 0.70, 0.78, 0.85, 0.90, 0.95, 1.00, 1.05,
    1.10, 1.15, 1.22, 1.30, 1.40, 1.55, 1.75, 2.00,
]
"""17 breakpoints for conditions-vs-historical ratio."""


def _percentile_rank(value: float, breakpoints: list[float]) -> float:
    """Return the percentile (0.0-1.0) of *value* within *breakpoints*.

    Uses linear interpolation between the two nearest breakpoints.
    Values below the minimum map to 0.0; above the maximum to 1.0.
    """
    if math.isnan(value):
        return float("nan")
    n = len(breakpoints)
    if n == 0:
        return 0.5
    if value <= breakpoints[0]:
        return 0.0
    if value >= breakpoints[-1]:
        return 1.0
    for i in range(1, n):
        if value <= breakpoints[i]:
            lo = breakpoints[i - 1]
            hi = breakpoints[i]
            frac = (value - lo) / (hi - lo) if hi != lo else 0.5
            return (i - 1 + frac) / (n - 1)
    return 1.0  # pragma: no cover


# ---------------------------------------------------------------------------
# Score breakdown weights
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScoreWeights:
    """Maximum point contribution for each component (must sum to 100)."""

    catch_probability: int = 30
    cpue_percentile: int = 30
    conditions_vs_historical: int = 25
    trophy_potential: int = 15

    def __post_init__(self) -> None:
        total = (
            self.catch_probability
            + self.cpue_percentile
            + self.conditions_vs_historical
            + self.trophy_potential
        )
        if total != 100:
            raise ValueError(f"Weights must sum to 100, got {total}")


# ---------------------------------------------------------------------------
# Main calculator
# ---------------------------------------------------------------------------

@dataclass
class ScoreBreakdown:
    """Detailed breakdown of how the composite score was computed."""

    catch_probability_pts: float
    cpue_percentile_pts: float
    conditions_vs_historical_pts: float
    trophy_potential_pts: float
    total: float
    rating: str
    inputs: dict[str, float | None] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": round(self.total, 1),
            "rating": self.rating,
            "breakdown": {
                "catch_probability_pts": round(self.catch_probability_pts, 2),
                "cpue_percentile_pts": round(self.cpue_percentile_pts, 2),
                "conditions_vs_historical_pts": round(self.conditions_vs_historical_pts, 2),
                "trophy_potential_pts": round(self.trophy_potential_pts, 2),
            },
            "inputs": {k: (round(v, 4) if v is not None else None) for k, v in self.inputs.items()},
        }


class FishingScoreCalculator:
    """Combine weight-model and CPUE-model outputs into a 0-100 fishing score.

    Parameters
    ----------
    weights : ScoreWeights, optional
        Override the default component weights.
    cpue_breakpoints : list[float], optional
        Custom percentile calibration breakpoints for CPUE values.
    conditions_breakpoints : list[float], optional
        Custom breakpoints for condition-ratio percentile mapping.
    """

    def __init__(
        self,
        weights: ScoreWeights | None = None,
        cpue_breakpoints: list[float] | None = None,
        conditions_breakpoints: list[float] | None = None,
    ) -> None:
        self.weights = weights or ScoreWeights()
        self._cpue_bp = cpue_breakpoints or _CPUE_PERCENTILES
        self._cond_bp = conditions_breakpoints or _CONDITIONS_RATIO_PERCENTILES

    # ---- public API -------------------------------------------------------

    def calculate_score(
        self,
        catch_prob: float | None = None,
        expected_cpue: float | None = None,
        conditions_ratio: float | None = None,
        trophy_potential: float | None = None,
    ) -> ScoreBreakdown:
        """Compute the composite 0-100 fishing score.

        Parameters
        ----------
        catch_prob : float or None
            Probability of catching at least one fish (0.0-1.0).
        expected_cpue : float or None
            Expected catch-per-unit-effort (fish/hour).  Mapped to a
            percentile via the calibration table.
        conditions_ratio : float or None
            Predicted weight (or CPUE) divided by the historical mean for
            the same site.  >1.0 means better-than-average conditions.
        trophy_potential : float or None
            Probability of a trophy-class catch (0.0-1.0).

        Returns
        -------
        ScoreBreakdown
            Full breakdown including total score, per-component points,
            human rating, and raw inputs.
        """
        w = self.weights

        # -- catch probability component (0 to w.catch_probability pts) --
        cp = self._safe_clamp(catch_prob, 0.0, 1.0)
        catch_pts = cp * w.catch_probability if cp is not None else 0.0

        # -- CPUE percentile component (0 to w.cpue_percentile pts) --
        if expected_cpue is not None and not math.isnan(expected_cpue):
            cpue_pct = _percentile_rank(max(expected_cpue, 0.0), self._cpue_bp)
            cpue_pts = cpue_pct * w.cpue_percentile
        else:
            cpue_pts = 0.0

        # -- conditions vs historical (0 to w.conditions_vs_historical pts) --
        if conditions_ratio is not None and not math.isnan(conditions_ratio):
            cond_pct = _percentile_rank(conditions_ratio, self._cond_bp)
            cond_pts = cond_pct * w.conditions_vs_historical
        else:
            cond_pts = 0.0

        # -- trophy potential (0 to w.trophy_potential pts) --
        tp = self._safe_clamp(trophy_potential, 0.0, 1.0)
        trophy_pts = tp * w.trophy_potential if tp is not None else 0.0

        total = catch_pts + cpue_pts + cond_pts + trophy_pts
        # Final clamp to [0, 100]
        total = max(0.0, min(100.0, total))

        rating = self.get_rating(total)

        return ScoreBreakdown(
            catch_probability_pts=catch_pts,
            cpue_percentile_pts=cpue_pts,
            conditions_vs_historical_pts=cond_pts,
            trophy_potential_pts=trophy_pts,
            total=total,
            rating=rating,
            inputs={
                "catch_prob": catch_prob,
                "expected_cpue": expected_cpue,
                "conditions_ratio": conditions_ratio,
                "trophy_potential": trophy_potential,
            },
        )

    @staticmethod
    def get_rating(score: float) -> str:
        """Map a 0-100 score to a human-readable quality rating.

        Returns
        -------
        str
            One of ``"poor"``, ``"fair"``, ``"good"``, ``"great"``,
            ``"excellent"``.
        """
        if score < 30:
            return "poor"
        if score < 50:
            return "fair"
        if score < 70:
            return "good"
        if score < 85:
            return "great"
        return "excellent"

    @staticmethod
    def get_confidence(
        site_coverage: float,
        data_freshness: float,
        model_ood_flag: bool,
    ) -> str:
        """Assess overall prediction confidence.

        Parameters
        ----------
        site_coverage : float
            Fraction of expected environmental features that are present
            and non-null (0.0-1.0).
        data_freshness : float
            Hours since the most recent sensor reading.  Lower is better.
        model_ood_flag : bool
            True when the feature vector is flagged as out-of-distribution
            by the model's OOD detector.

        Returns
        -------
        str
            One of ``"high"``, ``"medium"``, ``"low"``, ``"extrapolating"``.
        """
        if model_ood_flag:
            return "extrapolating"
        if site_coverage < 0.3 or data_freshness > 72:
            return "low"
        if site_coverage < 0.6 or data_freshness > 24:
            return "medium"
        return "high"

    @staticmethod
    def explain(breakdown: ScoreBreakdown) -> str:
        """Produce a human-readable explanation of a score breakdown.

        Parameters
        ----------
        breakdown : ScoreBreakdown
            The result from :meth:`calculate_score`.

        Returns
        -------
        str
            Multi-line plain-text explanation suitable for display in a
            mobile app or terminal.
        """
        lines: list[str] = []
        lines.append(
            f"Fishing Score: {breakdown.total:.0f}/100 ({breakdown.rating.upper()})"
        )
        lines.append("")

        def _bar(pts: float, max_pts: int) -> str:
            filled = round(pts / max_pts * 10) if max_pts > 0 else 0
            return "#" * filled + "." * (10 - filled)

        w = ScoreWeights()
        components = [
            ("Catch probability", breakdown.catch_probability_pts, w.catch_probability),
            ("CPUE percentile", breakdown.cpue_percentile_pts, w.cpue_percentile),
            ("Conditions vs history", breakdown.conditions_vs_historical_pts, w.conditions_vs_historical),
            ("Trophy potential", breakdown.trophy_potential_pts, w.trophy_potential),
        ]
        for label, pts, mx in components:
            lines.append(f"  {label:<24s} [{_bar(pts, mx)}] {pts:5.1f}/{mx}")

        # Add context sentences
        lines.append("")
        cr = breakdown.inputs.get("conditions_ratio")
        if cr is not None and cr > 1.05:
            lines.append("Conditions are above average for this site.")
        elif cr is not None and cr < 0.90:
            lines.append("Conditions are below average for this site.")
        else:
            lines.append("Conditions are near the historical average for this site.")

        tp = breakdown.inputs.get("trophy_potential")
        if tp is not None and tp > 0.5:
            lines.append("Elevated chance of a trophy-class catch.")

        return "\n".join(lines)

    # ---- internals --------------------------------------------------------

    @staticmethod
    def _safe_clamp(
        value: float | None, lo: float, hi: float
    ) -> float | None:
        """Clamp *value* to [lo, hi], returning None if value is None or NaN."""
        if value is None:
            return None
        if math.isnan(value):
            return None
        return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestFishingScoreCalculator(unittest.TestCase):
    """Tests for FishingScoreCalculator."""

    def setUp(self) -> None:
        self.calc = FishingScoreCalculator()

    # -- calculate_score ----------------------------------------------------

    def test_perfect_score(self) -> None:
        """All inputs maxed out should yield 100."""
        result = self.calc.calculate_score(
            catch_prob=1.0,
            expected_cpue=10.0,  # well above top breakpoint
            conditions_ratio=2.5,  # well above top breakpoint
            trophy_potential=1.0,
        )
        self.assertEqual(result.total, 100.0)
        self.assertEqual(result.rating, "excellent")

    def test_zero_score(self) -> None:
        """All inputs at minimum should yield 0."""
        result = self.calc.calculate_score(
            catch_prob=0.0,
            expected_cpue=0.0,
            conditions_ratio=0.0,  # far below lowest breakpoint
            trophy_potential=0.0,
        )
        self.assertEqual(result.total, 0.0)
        self.assertEqual(result.rating, "poor")

    def test_all_none_inputs(self) -> None:
        """Missing inputs should yield score 0 without errors."""
        result = self.calc.calculate_score()
        self.assertEqual(result.total, 0.0)
        self.assertEqual(result.rating, "poor")

    def test_partial_inputs(self) -> None:
        """Only catch_prob supplied; other components should be 0."""
        result = self.calc.calculate_score(catch_prob=0.5)
        self.assertAlmostEqual(result.catch_probability_pts, 15.0)
        self.assertEqual(result.cpue_percentile_pts, 0.0)
        self.assertEqual(result.conditions_vs_historical_pts, 0.0)
        self.assertEqual(result.trophy_potential_pts, 0.0)
        self.assertAlmostEqual(result.total, 15.0)

    def test_nan_inputs_treated_as_missing(self) -> None:
        """NaN values should behave like None (contribute 0)."""
        result = self.calc.calculate_score(
            catch_prob=float("nan"),
            expected_cpue=float("nan"),
            conditions_ratio=float("nan"),
            trophy_potential=float("nan"),
        )
        self.assertEqual(result.total, 0.0)

    def test_clamp_catch_prob(self) -> None:
        """catch_prob > 1 should be clamped to 1."""
        result = self.calc.calculate_score(catch_prob=1.5)
        self.assertAlmostEqual(result.catch_probability_pts, 30.0)

    def test_negative_cpue(self) -> None:
        """Negative CPUE should be clamped to 0."""
        result = self.calc.calculate_score(expected_cpue=-1.0)
        self.assertAlmostEqual(result.cpue_percentile_pts, 0.0)

    def test_score_monotonic_with_cpue(self) -> None:
        """Higher CPUE should never produce a lower score."""
        prev = 0.0
        for cpue in [0.1, 0.5, 1.0, 2.0, 4.0, 8.0]:
            result = self.calc.calculate_score(expected_cpue=cpue)
            self.assertGreaterEqual(result.cpue_percentile_pts, prev)
            prev = result.cpue_percentile_pts

    def test_conditions_ratio_midpoint(self) -> None:
        """A ratio of 1.0 (average) should yield roughly half the max points."""
        result = self.calc.calculate_score(conditions_ratio=1.0)
        # 1.0 is roughly at the 7th breakpoint out of 16 intervals = 43.75%
        self.assertGreater(result.conditions_vs_historical_pts, 8.0)
        self.assertLess(result.conditions_vs_historical_pts, 16.0)

    # -- get_rating ---------------------------------------------------------

    def test_rating_boundaries(self) -> None:
        self.assertEqual(self.calc.get_rating(0), "poor")
        self.assertEqual(self.calc.get_rating(29.9), "poor")
        self.assertEqual(self.calc.get_rating(30), "fair")
        self.assertEqual(self.calc.get_rating(49.9), "fair")
        self.assertEqual(self.calc.get_rating(50), "good")
        self.assertEqual(self.calc.get_rating(69.9), "good")
        self.assertEqual(self.calc.get_rating(70), "great")
        self.assertEqual(self.calc.get_rating(84.9), "great")
        self.assertEqual(self.calc.get_rating(85), "excellent")
        self.assertEqual(self.calc.get_rating(100), "excellent")

    # -- get_confidence -----------------------------------------------------

    def test_confidence_high(self) -> None:
        self.assertEqual(
            self.calc.get_confidence(0.9, 2.0, False), "high"
        )

    def test_confidence_medium(self) -> None:
        self.assertEqual(
            self.calc.get_confidence(0.5, 10.0, False), "medium"
        )

    def test_confidence_low(self) -> None:
        self.assertEqual(
            self.calc.get_confidence(0.2, 5.0, False), "low"
        )

    def test_confidence_extrapolating(self) -> None:
        self.assertEqual(
            self.calc.get_confidence(1.0, 1.0, True), "extrapolating"
        )

    def test_confidence_stale_data(self) -> None:
        """Data older than 72h should be low confidence."""
        self.assertEqual(
            self.calc.get_confidence(0.9, 80.0, False), "low"
        )

    # -- explain ------------------------------------------------------------

    def test_explain_returns_string(self) -> None:
        result = self.calc.calculate_score(
            catch_prob=0.6,
            expected_cpue=1.5,
            conditions_ratio=1.1,
            trophy_potential=0.3,
        )
        explanation = self.calc.explain(result)
        self.assertIsInstance(explanation, str)
        self.assertIn("Fishing Score:", explanation)
        self.assertIn("Catch probability", explanation)

    def test_explain_above_average(self) -> None:
        result = self.calc.calculate_score(conditions_ratio=1.2)
        explanation = self.calc.explain(result)
        self.assertIn("above average", explanation)

    def test_explain_below_average(self) -> None:
        result = self.calc.calculate_score(conditions_ratio=0.7)
        explanation = self.calc.explain(result)
        self.assertIn("below average", explanation)

    def test_explain_trophy(self) -> None:
        result = self.calc.calculate_score(trophy_potential=0.8)
        explanation = self.calc.explain(result)
        self.assertIn("trophy", explanation.lower())

    # -- to_dict ------------------------------------------------------------

    def test_to_dict_keys(self) -> None:
        result = self.calc.calculate_score(catch_prob=0.5, expected_cpue=1.0)
        d = result.to_dict()
        self.assertIn("total", d)
        self.assertIn("rating", d)
        self.assertIn("breakdown", d)
        self.assertIn("inputs", d)

    # -- percentile_rank internals ------------------------------------------

    def test_percentile_rank_edges(self) -> None:
        self.assertAlmostEqual(_percentile_rank(0.0, _CPUE_PERCENTILES), 0.0)
        self.assertAlmostEqual(_percentile_rank(8.0, _CPUE_PERCENTILES), 1.0)
        self.assertAlmostEqual(_percentile_rank(-5.0, _CPUE_PERCENTILES), 0.0)
        self.assertAlmostEqual(_percentile_rank(100.0, _CPUE_PERCENTILES), 1.0)

    def test_percentile_rank_nan(self) -> None:
        self.assertTrue(math.isnan(_percentile_rank(float("nan"), _CPUE_PERCENTILES)))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _demo() -> None:
    """Run a quick demonstration of the scoring system."""
    calc = FishingScoreCalculator()

    scenarios = [
        ("Great day", dict(catch_prob=0.85, expected_cpue=3.0, conditions_ratio=1.20, trophy_potential=0.40)),
        ("Average day", dict(catch_prob=0.55, expected_cpue=1.0, conditions_ratio=1.00, trophy_potential=0.10)),
        ("Tough day", dict(catch_prob=0.20, expected_cpue=0.15, conditions_ratio=0.75, trophy_potential=0.02)),
        ("Missing data", dict(catch_prob=0.60)),
    ]

    for label, kwargs in scenarios:
        print(f"=== {label} ===")
        result = calc.calculate_score(**kwargs)
        print(calc.explain(result))
        conf = calc.get_confidence(
            site_coverage=0.8, data_freshness=6.0, model_ood_flag=False
        )
        print(f"  Confidence: {conf}")
        print()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="CASTLINE composite fishing scorer")
    parser.add_argument("--demo", action="store_true", help="Run demo scenarios")
    parser.add_argument("--test", action="store_true", help="Run unit tests")
    args = parser.parse_args()

    if args.test:
        unittest.main(argv=[""], exit=True)
    elif args.demo:
        _demo()
    else:
        parser.print_help()
