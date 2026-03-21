"""Compute NegativeSignalSummary aggregations from PredictionLog data.

Usage:
    python manage.py compute_signal_summary            # last 30 days
    python manage.py compute_signal_summary --days 90  # last 90 days
"""
from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Avg, Count, Q
from django.utils import timezone

from castline.backend.apps.core.models import (
    NegativeSignalSummary,
    PredictionLog,
)


class Command(BaseCommand):
    help = "Aggregate PredictionLog data into NegativeSignalSummary per location"

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=30,
            help="Lookback window in days (default: 30)",
        )

    def handle(self, *args, **options):
        days = options["days"]
        now = timezone.now()
        period_start = (now - timedelta(days=days)).date()
        period_end = now.date()

        self.stdout.write(f"Computing signal summaries for {period_start} to {period_end}...")

        # Group resolved predictions by location
        locations = (
            PredictionLog.objects.filter(
                predicted_at__date__gte=period_start,
                predicted_at__date__lte=period_end,
            )
            .exclude(signal_type=PredictionLog.SignalType.UNRESOLVED)
            .values("location", "species")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        created = 0
        updated = 0

        for loc_entry in locations:
            location = loc_entry["location"]
            species = loc_entry["species"]

            qs = PredictionLog.objects.filter(
                location=location,
                species=species,
                predicted_at__date__gte=period_start,
                predicted_at__date__lte=period_end,
            )

            total = qs.count()
            resolved_qs = qs.exclude(
                signal_type=PredictionLog.SignalType.UNRESOLVED,
            )
            resolved = resolved_qs.count()

            fp = resolved_qs.filter(signal_type="FP").count()
            fn = resolved_qs.filter(signal_type="FN").count()
            tp = resolved_qs.filter(signal_type="TP").count()
            tn = resolved_qs.filter(signal_type="TN").count()

            # Error stats
            error_stats = resolved_qs.aggregate(
                mean_error=Avg("prediction_error"),
                mean_svr=Avg("score_vs_rating"),
            )

            # Identify failure conditions for false positives
            failure_conditions = {}
            fp_logs = resolved_qs.filter(signal_type="FP").values_list(
                "conditions_at_prediction", flat=True,
            )
            for cond in fp_logs:
                if not cond:
                    continue
                # Count weather regime occurrences in false positives
                regime = cond.get("regime_at_event", "unknown")
                failure_conditions[regime] = failure_conditions.get(regime, 0) + 1

            summary, was_created = NegativeSignalSummary.objects.update_or_create(
                location=location,
                species=species,
                period_start=period_start,
                period_end=period_end,
                defaults={
                    "total_predictions": total,
                    "total_resolved": resolved,
                    "false_positives": fp,
                    "false_negatives": fn,
                    "true_positives": tp,
                    "true_negatives": tn,
                    "mean_prediction_error": error_stats["mean_error"],
                    "mean_score_vs_rating": error_stats["mean_svr"],
                    "failure_conditions": failure_conditions,
                },
            )

            if was_created:
                created += 1
            else:
                updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Created {created}, updated {updated} summaries "
                f"across {len(locations)} location(s)."
            )
        )

        # Report top false-positive locations
        top_fp = NegativeSignalSummary.objects.filter(
            period_start=period_start,
            period_end=period_end,
        ).order_by("-false_positives")[:10]

        if top_fp:
            self.stdout.write("\nTop false-positive locations (model over-predicts):")
            for s in top_fp:
                self.stdout.write(
                    f"  {s.location}: {s.false_positives} FP, "
                    f"{s.false_negatives} FN, "
                    f"error={s.mean_prediction_error:.2f} lb"
                    if s.mean_prediction_error is not None
                    else f"  {s.location}: {s.false_positives} FP, {s.false_negatives} FN"
                )
