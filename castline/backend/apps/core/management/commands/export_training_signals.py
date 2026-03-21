"""Export resolved prediction signals as training data for model retraining.

Produces a CSV with prediction features + actual outcomes, with extra weight
on false positive/negative cases to help the model learn from its mistakes.

Usage:
    python manage.py export_training_signals
    python manage.py export_training_signals --output signals.csv --days 90
"""
from __future__ import annotations

import csv
from datetime import timedelta
from pathlib import Path

from django.core.management.base import BaseCommand
from django.utils import timezone

from castline.backend.apps.core.models import PredictionLog


class Command(BaseCommand):
    help = "Export resolved prediction signals as model training data"

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            type=str,
            default="training_signals.csv",
            help="Output CSV file path",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=90,
            help="Lookback window in days (default: 90)",
        )
        parser.add_argument(
            "--min-effort",
            type=float,
            default=1.0,
            help="Minimum fishing effort hours to include (default: 1.0)",
        )

    def handle(self, *args, **options):
        output_path = Path(options["output"])
        days = options["days"]
        min_effort = options["min_effort"]

        cutoff = timezone.now() - timedelta(days=days)

        resolved = PredictionLog.objects.filter(
            predicted_at__gte=cutoff,
            catch_report__isnull=False,
            actual_effort_hours__gte=min_effort,
        ).exclude(
            signal_type=PredictionLog.SignalType.UNRESOLVED,
        ).select_related("catch_report").order_by("prediction_date")

        self.stdout.write(f"Found {resolved.count()} resolved predictions...")

        # CSV columns
        fieldnames = [
            "prediction_date",
            "location",
            "species",
            "lat",
            "lon",
            # Model prediction
            "predicted_weight_lb",
            "fishing_score",
            "confidence",
            "model_version",
            # Actual outcome
            "actual_catch_count",
            "actual_weight_lb",
            "actual_rating",
            "actual_effort_hours",
            "actual_cpue",
            # Signal
            "signal_type",
            "prediction_error",
            "score_vs_rating",
            # Training weight (upweight mistakes)
            "sample_weight",
            # USGS
            "usgs_site_id",
        ]

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            fp_count = fn_count = tp_count = tn_count = 0

            for pred in resolved:
                # Compute sample weight: upweight false signals
                if pred.signal_type in ("FP", "FN"):
                    weight = 3.0  # 3x weight for model mistakes
                else:
                    weight = 1.0

                # Compute actual CPUE
                actual_cpue = None
                if pred.actual_effort_hours and pred.actual_effort_hours > 0:
                    actual_cpue = (
                        pred.actual_catch_count / pred.actual_effort_hours
                        if pred.actual_catch_count is not None
                        else None
                    )

                writer.writerow({
                    "prediction_date": pred.prediction_date.isoformat(),
                    "location": pred.location,
                    "species": pred.species,
                    "lat": pred.lat,
                    "lon": pred.lon,
                    "predicted_weight_lb": pred.predicted_weight_lb,
                    "fishing_score": pred.fishing_score,
                    "confidence": pred.confidence,
                    "model_version": pred.model_version,
                    "actual_catch_count": pred.actual_catch_count,
                    "actual_weight_lb": pred.actual_weight_lb,
                    "actual_rating": pred.actual_rating,
                    "actual_effort_hours": pred.actual_effort_hours,
                    "actual_cpue": round(actual_cpue, 4) if actual_cpue else "",
                    "signal_type": pred.signal_type,
                    "prediction_error": pred.prediction_error,
                    "score_vs_rating": pred.score_vs_rating,
                    "sample_weight": weight,
                    "usgs_site_id": pred.usgs_site_id,
                })

                if pred.signal_type == "FP":
                    fp_count += 1
                elif pred.signal_type == "FN":
                    fn_count += 1
                elif pred.signal_type == "TP":
                    tp_count += 1
                elif pred.signal_type == "TN":
                    tn_count += 1

        total = fp_count + fn_count + tp_count + tn_count
        self.stdout.write(
            self.style.SUCCESS(
                f"Exported {total} signals to {output_path}\n"
                f"  TP={tp_count} TN={tn_count} FP={fp_count} FN={fn_count}\n"
                f"  Accuracy: {(tp_count + tn_count) / max(1, total):.1%}"
            )
        )
