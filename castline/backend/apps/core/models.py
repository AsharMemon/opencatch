"""CASTLINE core models."""
from __future__ import annotations

from django.db import models


class CatchReport(models.Model):
    """User-submitted catch report for ground-truth data collection."""

    user_id = models.CharField(
        max_length=128,
        db_index=True,
        help_text="External user identifier (Firebase UID, etc.)",
    )
    lat = models.FloatField(help_text="Latitude of fishing location")
    lon = models.FloatField(help_text="Longitude of fishing location")
    reported_at = models.DateTimeField(
        help_text="When the user submitted the report",
    )
    trip_start = models.DateTimeField(help_text="Start of fishing trip")
    trip_end = models.DateTimeField(help_text="End of fishing trip")
    effort_hours = models.FloatField(
        help_text="Total hours actively fishing",
    )
    species = models.CharField(
        max_length=64,
        default="largemouth_bass",
        help_text="Target or primary species caught",
    )
    catch_count = models.PositiveIntegerField(
        default=0,
        help_text="Total fish caught",
    )
    kept_count = models.PositiveIntegerField(
        default=0,
        help_text="Fish kept (not released)",
    )
    largest_weight_lb = models.FloatField(
        null=True,
        blank=True,
        help_text="Weight of largest fish in pounds",
    )
    rating = models.PositiveSmallIntegerField(
        help_text="User satisfaction rating 1-5",
    )
    conditions_snapshot = models.JSONField(
        default=dict,
        blank=True,
        help_text="Snapshot of environmental conditions at time of trip",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["species", "created_at"]),
            models.Index(fields=["lat", "lon"]),
        ]

    def __str__(self) -> str:
        return (
            f"CatchReport({self.user_id}, {self.species}, "
            f"{self.catch_count} fish, {self.trip_start:%Y-%m-%d})"
        )

    @property
    def is_skunked(self) -> bool:
        """True if the angler caught zero fish."""
        return self.catch_count == 0

    @property
    def cpue(self) -> float | None:
        """Catch per unit effort (fish/hour). None if effort is zero."""
        if self.effort_hours and self.effort_hours > 0:
            return self.catch_count / self.effort_hours
        return None


class PredictionLog(models.Model):
    """Log every prediction served, enabling negative-signal analysis.

    Tracks what was predicted vs. what actually happened (via linked
    CatchReport). Negative signals — where the model predicted good
    fishing but the user reported poor results (or vice versa) — are
    the most valuable training signal for model improvement.
    """

    class SignalType(models.TextChoices):
        TRUE_POSITIVE = "TP", "True Positive (predicted good, was good)"
        TRUE_NEGATIVE = "TN", "True Negative (predicted bad, was bad)"
        FALSE_POSITIVE = "FP", "False Positive (predicted good, was bad)"
        FALSE_NEGATIVE = "FN", "False Negative (predicted bad, was good)"
        UNRESOLVED = "UN", "Unresolved (no outcome report yet)"

    # --- Request context ---
    user_id = models.CharField(
        max_length=128,
        blank=True,
        db_index=True,
        help_text="User who requested the prediction (if authenticated)",
    )
    location = models.CharField(
        max_length=256,
        help_text="Location string used in the prediction request",
    )
    lat = models.FloatField(
        null=True,
        blank=True,
        help_text="Resolved latitude",
    )
    lon = models.FloatField(
        null=True,
        blank=True,
        help_text="Resolved longitude",
    )
    prediction_date = models.DateField(
        help_text="Date the prediction was for",
    )
    species = models.CharField(
        max_length=64,
        default="largemouth_bass",
    )

    # --- Model output ---
    predicted_weight_lb = models.FloatField(
        help_text="Model's predicted CPUE weight (lb/angler)",
    )
    fishing_score = models.PositiveSmallIntegerField(
        help_text="Composite fishing score (0-100)",
    )
    confidence = models.FloatField(
        default=0.0,
        help_text="Model confidence (0-1)",
    )
    model_version = models.CharField(
        max_length=32,
        default="v5",
        help_text="Model version that produced this prediction",
    )
    feature_snapshot = models.JSONField(
        default=dict,
        blank=True,
        help_text="Key feature values used (weather, hydro, etc.)",
    )

    # --- Environmental conditions at prediction time ---
    usgs_site_id = models.CharField(
        max_length=32,
        blank=True,
        help_text="USGS gauge site used for hydro features",
    )
    conditions_at_prediction = models.JSONField(
        default=dict,
        blank=True,
        help_text="Full environmental snapshot when prediction was made",
    )

    # --- Outcome (filled in when CatchReport arrives) ---
    catch_report = models.ForeignKey(
        CatchReport,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="prediction_logs",
        help_text="Linked catch report (outcome truth)",
    )
    actual_catch_count = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Actual fish caught (from linked CatchReport)",
    )
    actual_weight_lb = models.FloatField(
        null=True,
        blank=True,
        help_text="Actual largest weight (from linked CatchReport)",
    )
    actual_rating = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="User satisfaction rating (1-5)",
    )
    actual_effort_hours = models.FloatField(
        null=True,
        blank=True,
        help_text="Actual hours fished",
    )

    # --- Signal classification ---
    signal_type = models.CharField(
        max_length=2,
        choices=SignalType.choices,
        default=SignalType.UNRESOLVED,
        db_index=True,
        help_text="Whether this was a true/false positive/negative",
    )
    prediction_error = models.FloatField(
        null=True,
        blank=True,
        help_text="predicted_weight - actual_weight (positive = overpredict)",
    )
    score_vs_rating = models.FloatField(
        null=True,
        blank=True,
        help_text="Normalized score vs rating comparison (-1 to 1)",
    )

    # --- Timestamps ---
    predicted_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When the prediction was served",
    )
    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the catch report was linked",
    )

    class Meta:
        ordering = ["-predicted_at"]
        indexes = [
            models.Index(fields=["signal_type", "predicted_at"]),
            models.Index(fields=["location", "prediction_date"]),
            models.Index(fields=["user_id", "predicted_at"]),
            models.Index(
                fields=["prediction_date", "signal_type"],
                name="idx_date_signal",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"PredictionLog({self.location}, {self.prediction_date}, "
            f"score={self.fishing_score}, signal={self.signal_type})"
        )

    def resolve(self, catch_report: CatchReport) -> None:
        """Link a CatchReport and classify the prediction signal.

        This is where negative signal detection happens:
        - False Positive: predicted score >= 60 but rating <= 2 or skunked
        - False Negative: predicted score < 40 but rating >= 4
        - True Positive: predicted good and was good
        - True Negative: predicted poor and was poor
        """
        from django.utils import timezone

        self.catch_report = catch_report
        self.actual_catch_count = catch_report.catch_count
        self.actual_weight_lb = catch_report.largest_weight_lb
        self.actual_rating = catch_report.rating
        self.actual_effort_hours = catch_report.effort_hours
        self.resolved_at = timezone.now()

        # Compute prediction error
        if catch_report.largest_weight_lb is not None:
            self.prediction_error = (
                self.predicted_weight_lb - catch_report.largest_weight_lb
            )

        # Score vs rating comparison: normalize both to 0-1 scale
        # Score: 0-100 -> 0-1, Rating: 1-5 -> 0-1
        norm_score = self.fishing_score / 100.0
        norm_rating = (catch_report.rating - 1) / 4.0
        self.score_vs_rating = norm_score - norm_rating

        # Classify signal
        predicted_good = self.fishing_score >= 60
        predicted_bad = self.fishing_score < 40
        was_good = catch_report.rating >= 4 and catch_report.catch_count > 0
        was_bad = (
            catch_report.rating <= 2
            or catch_report.catch_count == 0
        )

        if predicted_good and was_bad:
            self.signal_type = self.SignalType.FALSE_POSITIVE
        elif predicted_bad and was_good:
            self.signal_type = self.SignalType.FALSE_NEGATIVE
        elif predicted_good and was_good:
            self.signal_type = self.SignalType.TRUE_POSITIVE
        elif predicted_bad and was_bad:
            self.signal_type = self.SignalType.TRUE_NEGATIVE
        else:
            # Ambiguous middle zone (score 40-60 or rating 3)
            # Still classify based on error direction
            if self.prediction_error is not None:
                if self.prediction_error > 1.0:
                    self.signal_type = self.SignalType.FALSE_POSITIVE
                elif self.prediction_error < -1.0:
                    self.signal_type = self.SignalType.FALSE_NEGATIVE
                else:
                    self.signal_type = self.SignalType.TRUE_POSITIVE
            else:
                self.signal_type = self.SignalType.TRUE_POSITIVE

        self.save()


class NegativeSignalSummary(models.Model):
    """Aggregated negative signal analysis per location+conditions combo.

    Updated periodically by a management command to identify systematic
    prediction failures — locations or conditions where the model
    consistently over- or under-predicts.
    """

    location = models.CharField(max_length=256, db_index=True)
    species = models.CharField(max_length=64, default="largemouth_bass")

    # Aggregation window
    period_start = models.DateField()
    period_end = models.DateField()

    # Counts
    total_predictions = models.PositiveIntegerField(default=0)
    total_resolved = models.PositiveIntegerField(default=0)
    false_positives = models.PositiveIntegerField(default=0)
    false_negatives = models.PositiveIntegerField(default=0)
    true_positives = models.PositiveIntegerField(default=0)
    true_negatives = models.PositiveIntegerField(default=0)

    # Error statistics
    mean_prediction_error = models.FloatField(
        null=True,
        blank=True,
        help_text="Mean (predicted - actual) weight. Positive = systematic over-prediction",
    )
    mean_score_vs_rating = models.FloatField(
        null=True,
        blank=True,
        help_text="Mean score-vs-rating gap. Positive = model more optimistic than users",
    )

    # Dominant failure conditions (JSON dict of condition -> count)
    failure_conditions = models.JSONField(
        default=dict,
        blank=True,
        help_text="Environmental conditions most associated with false predictions",
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["location", "species", "period_start", "period_end"]
        ordering = ["-false_positives"]

    def __str__(self) -> str:
        return (
            f"NegativeSignalSummary({self.location}, "
            f"FP={self.false_positives}, FN={self.false_negatives})"
        )

    @property
    def false_positive_rate(self) -> float:
        """Rate of false positives among resolved predictions."""
        if self.total_resolved == 0:
            return 0.0
        return self.false_positives / self.total_resolved

    @property
    def accuracy(self) -> float:
        """Overall accuracy (TP + TN) / total resolved."""
        if self.total_resolved == 0:
            return 0.0
        return (self.true_positives + self.true_negatives) / self.total_resolved
