from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from castline.validation.dataset import ValidationDataset
from castline.validation.models.linear import LinearRegressionModel, decile_lift, fit_linear_regression, mae, r2, rmse


@dataclass(frozen=True)
class ModelResult:
    name: str
    feature_names: list[str]
    rmse: float
    mae: float
    r2: float
    decile_lift: float


@dataclass(frozen=True)
class ValidationComparison:
    baseline: ModelResult
    enriched: ModelResult
    r2_improvement_pct: float
    rmse_reduction_pct: float
    judgment: str


BASELINE_FEATURES = [
    "moon_phase",
    "air_temp_c",
    "pressure_mb",
    "wind_speed_kph",
    "cloud_cover_pct",
]

ENRICHED_EXTRA_FEATURES = [
    "water_temp_c",
    "water_temp_6h_delta",
    "discharge_cfs",
    "discharge_6h_pct_change",
    "gage_height_ft",
    "precip_24h_mm",
]


def evaluate_csv(path: str | Path, *, train_max_year: int, test_min_year: int) -> ValidationComparison:
    dataset = ValidationDataset.from_csv(path)
    train = dataset.filter_years(max_year=train_max_year)
    test = dataset.filter_years(min_year=test_min_year)
    if not train.rows or not test.rows:
        raise ValueError("Need both training and testing rows for evaluation")

    baseline = _evaluate_split(train, test, BASELINE_FEATURES, "baseline")
    enriched = _evaluate_split(train, test, BASELINE_FEATURES + ENRICHED_EXTRA_FEATURES, "environmental")

    if baseline.r2 == 0:
        r2_improvement_pct = float("inf") if enriched.r2 > 0 else 0.0
    else:
        r2_improvement_pct = ((enriched.r2 - baseline.r2) / abs(baseline.r2)) * 100.0

    rmse_reduction_pct = ((baseline.rmse - enriched.rmse) / baseline.rmse) * 100.0 if baseline.rmse else 0.0
    judgment = judge_thesis(r2_improvement_pct)

    return ValidationComparison(
        baseline=baseline,
        enriched=enriched,
        r2_improvement_pct=r2_improvement_pct,
        rmse_reduction_pct=rmse_reduction_pct,
        judgment=judgment,
    )


def judge_thesis(r2_improvement_pct: float) -> str:
    if r2_improvement_pct < 5.0:
        return "weak"
    if r2_improvement_pct <= 15.0:
        return "viable"
    return "strong"


def _evaluate_split(train: ValidationDataset, test: ValidationDataset, feature_names: Sequence[str], name: str) -> ModelResult:
    train_x, train_y, _ = train.to_matrix(feature_names)
    test_x, test_y, _ = test.to_matrix(feature_names)
    model: LinearRegressionModel = fit_linear_regression(train_x, train_y)
    predictions = model.predict(test_x)
    return ModelResult(
        name=name,
        feature_names=list(feature_names),
        rmse=rmse(test_y, predictions),
        mae=mae(test_y, predictions),
        r2=r2(test_y, predictions),
        decile_lift=decile_lift(test_y, predictions),
    )
