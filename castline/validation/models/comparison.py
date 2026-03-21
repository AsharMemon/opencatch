from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

from castline.validation.types import ComparisonSummary


BASELINE_FEATURES = ['baseline_signal']
FULL_FEATURES = [
    'baseline_signal',
    'air_temp_c',
    'pressure_mb',
    'wind_speed_kph',
    'cloud_cover_pct',
    'precip_24h_mm',
    'water_temp_c',
    'discharge_cfs',
    'gage_height_ft',
    'temp_delta_24h_c',
    'flow_delta_24h_pct',
    'dissolved_oxygen_mgL',
    'turbidity_fnu',
    'gage_delta_24h_ft',
    'specific_conductance_us_cm',
    'ph',
    'reservoir_elevation_ft',
    'water_temp_7d_mean',
    'water_temp_30d_trend',
    'discharge_7d_mean',
    'gage_height_7d_mean',
    'env_signal',
    'water_temp_x_flow',
    'weather_stability_index',
]
TARGET = 'target_success_score'
MIN_COMPARISON_ROWS = max(8, len(FULL_FEATURES) + 2)


def _judge(improvement_pct: float) -> str:
    if improvement_pct < 5:
        return 'weak'
    if improvement_pct <= 15:
        return 'viable'
    return 'strong'


def _fit_and_score(df: pd.DataFrame, feature_names: list[str]) -> dict:
    model = LinearRegression()
    X = df[feature_names]
    y = df[TARGET]
    model.fit(X, y)
    preds = model.predict(X)
    return {
        'r2': r2_score(y, preds),
        'rmse': mean_squared_error(y, preds) ** 0.5,
        'mae': mean_absolute_error(y, preds),
        'coefficients': dict(zip(feature_names, model.coef_)),
        'intercept': float(model.intercept_),
    }


def _insufficient_summary(*, row_count: int, usable_row_count: int, reason: str, output_path: Path) -> ComparisonSummary:
    report = {
        'baseline': None,
        'full': None,
        'improvement_pct': None,
        'thesis_rating': 'insufficient_data',
        'decision_rule': {'weak_lt': 5, 'viable_lte': 15, 'strong_gt': 15},
        'row_count': row_count,
        'usable_row_count': usable_row_count,
        'withheld_reason': reason,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2))
    return ComparisonSummary(
        baseline_r2=float('nan'),
        full_r2=float('nan'),
        improvement_pct=float('nan'),
        thesis_rating='insufficient_data',
        row_count=row_count,
        usable_row_count=usable_row_count,
        withheld_reason=reason,
    )


def compare_models(dataset_path: Path, output_path: Path) -> ComparisonSummary:
    df = pd.read_csv(dataset_path)
    row_count = len(df)
    required_columns = list(dict.fromkeys(BASELINE_FEATURES + FULL_FEATURES + [TARGET]))
    for column in required_columns:
        if column not in df.columns:
            df[column] = float('nan')

    # Only require target + baseline features to be non-null.
    # Sparse sensor columns get median-imputed so we don't discard rows.
    core_columns = [TARGET] + BASELINE_FEATURES
    usable = df.dropna(subset=core_columns).copy()
    for col in FULL_FEATURES:
        if col not in core_columns and col in usable.columns:
            median_val = usable[col].median()
            # If entire column is NaN (no data at all), fill with 0 (neutral)
            fill_val = median_val if pd.notna(median_val) else 0.0
            usable[col] = usable[col].fillna(fill_val)
    usable_row_count = len(usable)
    if usable_row_count < MIN_COMPARISON_ROWS:
        return _insufficient_summary(
            row_count=row_count,
            usable_row_count=usable_row_count,
            reason=(
                f'Need at least {MIN_COMPARISON_ROWS} fully populated validation rows for a trustworthy '
                f'baseline-vs-environment comparison; only found {usable_row_count}.'
            ),
            output_path=output_path,
        )

    baseline = _fit_and_score(usable, BASELINE_FEATURES)
    full = _fit_and_score(usable, FULL_FEATURES)
    metrics = [baseline['r2'], baseline['rmse'], baseline['mae'], full['r2'], full['rmse'], full['mae']]
    if not all(math.isfinite(value) for value in metrics):
        return _insufficient_summary(
            row_count=row_count,
            usable_row_count=usable_row_count,
            reason='Model metrics were non-finite; the dataset is still too thin or degenerate for a trustworthy thesis judgment.',
            output_path=output_path,
        )

    improvement_pct = ((full['r2'] - baseline['r2']) / max(abs(baseline['r2']), 1e-6)) * 100
    thesis_rating = _judge(improvement_pct)
    report = {
        'baseline': baseline,
        'full': full,
        'improvement_pct': improvement_pct,
        'thesis_rating': thesis_rating,
        'decision_rule': {'weak_lt': 5, 'viable_lte': 15, 'strong_gt': 15},
        'row_count': row_count,
        'usable_row_count': usable_row_count,
        'withheld_reason': None,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2))
    return ComparisonSummary(
        baseline_r2=baseline['r2'],
        full_r2=full['r2'],
        improvement_pct=improvement_pct,
        thesis_rating=thesis_rating,
        row_count=row_count,
        usable_row_count=usable_row_count,
        withheld_reason=None,
    )
