from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

from castline.validation.types import ComparisonSummary


BASELINE_FEATURES = ['baseline_signal']
FULL_FEATURES = [
    'baseline_signal',
    'water_temp_c',
    'discharge_cfs',
    'gage_height_ft',
    'temp_delta_24h_c',
    'flow_delta_24h_pct',
    'env_signal',
    'water_temp_x_flow',
]
TARGET = 'target_success_score'


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


def compare_models(dataset_path: Path, output_path: Path) -> ComparisonSummary:
    df = pd.read_csv(dataset_path)
    baseline = _fit_and_score(df, BASELINE_FEATURES)
    full = _fit_and_score(df, FULL_FEATURES)
    improvement_pct = ((full['r2'] - baseline['r2']) / max(abs(baseline['r2']), 1e-6)) * 100
    thesis_rating = _judge(improvement_pct)
    report = {
        'baseline': baseline,
        'full': full,
        'improvement_pct': improvement_pct,
        'thesis_rating': thesis_rating,
        'decision_rule': {'weak_lt': 5, 'viable_lte': 15, 'strong_gt': 15},
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2))
    return ComparisonSummary(
        baseline_r2=baseline['r2'],
        full_r2=full['r2'],
        improvement_pct=improvement_pct,
        thesis_rating=thesis_rating,
    )
