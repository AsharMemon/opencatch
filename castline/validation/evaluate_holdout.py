"""Rigorous holdout evaluation for CASTLINE models.

Implements three evaluation strategies:
1. Temporal holdout: Train on years 1-N, test on N+1 through N+K
2. Spatial holdout: Train on locations A-X, test on unseen location Y
3. Spatiotemporal blocked: Hold out entire region-years

This is the honest evaluation that determines if the model can actually
predict fishing conditions for new times and places.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupKFold, GroupShuffleSplit


@dataclass
class HoldoutResult:
    strategy: str
    r2: float
    rmse: float
    mae: float
    train_rows: int
    test_rows: int
    details: dict = field(default_factory=dict)


FEATURES = [
    "loc_enc", "trail_mean_weight", "location_mean_weight", "loc_rolling_3",
    "area_acres", "max_depth_ft", "shore_dev", "lat",
    "baseline_signal", "day_length_hours", "season_cos", "season_sin",
    "air_temp_c", "flow_delta_24h_pct", "wind_speed_kph", "cloud_cover_pct",
    "water_temp_c", "discharge_cfs", "precip_24h_mm", "ph",
    "spawn_phase_score", "year",
    "gage_height_7d_mean", "water_temp_x_flow", "discharge_pct_of_30d",
    "cumulative_degree_days", "pressure_mb", "pressure_delta_6h",
    "moon_illumination_pct", "solunar_score", "dissolved_oxygen_mgL",
    "gage_height_ft", "gage_stability_7d", "wind_dir_cos",
    "front_pre_frontal", "front_post_frontal",
    "water_temp_anomaly", "water_temp_estimated",
]

TARGET = "median_weight_lb"


def load_dataset(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "year" not in df.columns and "date" in df.columns:
        df["year"] = pd.to_datetime(df["date"]).dt.year
    return df


def _extract_state(location: str) -> str:
    """Extract state abbreviation from location string."""
    parts = location.rsplit(",", 1)
    if len(parts) == 2:
        state = parts[1].strip()
        return state[:2].upper()
    return "UNK"


def _extract_region(location: str) -> str:
    """Map state to geographic region for spatial blocking."""
    state = _extract_state(location)
    regions = {
        "Southeast": ["AL", "FL", "GA", "SC", "NC", "VA", "TN", "MS", "LA", "AR"],
        "Northeast": ["NY", "PA", "MD", "DE", "NJ", "CT", "MA", "ME", "VT", "NH"],
        "Midwest": ["WI", "MN", "MI", "OH", "IN", "IL", "IA", "MO", "KS", "ND", "SD", "NE"],
        "Southwest": ["TX", "OK", "AZ", "NM", "UT", "CO"],
        "West": ["CA", "OR", "WA", "ID", "MT", "WY", "NV"],
        "Canada": ["ON", "QC", "BC", "AB"],
    }
    for region, states in regions.items():
        if state in states:
            return region
    return "Other"


def _train_lgb(X_train, y_train, X_val=None, y_val=None):
    """Train a LightGBM model."""
    params = {
        "objective": "regression",
        "metric": "rmse",
        "verbosity": -1,
        "n_estimators": 500,
        "learning_rate": 0.05,
        "max_depth": 5,
        "num_leaves": 31,
        "min_child_samples": 10,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "reg_alpha": 0.1,
        "reg_lambda": 1.0,
    }

    callbacks = []
    eval_set = []
    if X_val is not None and y_val is not None:
        eval_set = [(X_val, y_val)]
        callbacks = [lgb.early_stopping(50, verbose=False)]

    model = lgb.LGBMRegressor(**params)
    model.fit(
        X_train, y_train,
        eval_set=eval_set if eval_set else None,
        callbacks=callbacks if callbacks else None,
    )
    return model


def temporal_holdout(df: pd.DataFrame, train_years: tuple[int, int],
                     test_years: tuple[int, int]) -> HoldoutResult:
    """Train on years [a,b], test on years [c,d]."""
    available = [f for f in FEATURES if f in df.columns]

    train_mask = (df["year"] >= train_years[0]) & (df["year"] <= train_years[1])
    test_mask = (df["year"] >= test_years[0]) & (df["year"] <= test_years[1])

    train = df[train_mask]
    test = df[test_mask]

    X_train = train[available].values
    y_train = train[TARGET].values
    X_test = test[available].values
    y_test = test[TARGET].values

    model = _train_lgb(X_train, y_train)
    preds = model.predict(X_test)

    return HoldoutResult(
        strategy=f"temporal_{train_years[0]}-{train_years[1]}_test_{test_years[0]}-{test_years[1]}",
        r2=r2_score(y_test, preds),
        rmse=np.sqrt(mean_squared_error(y_test, preds)),
        mae=mean_absolute_error(y_test, preds),
        train_rows=len(train),
        test_rows=len(test),
        details={
            "train_years": list(train_years),
            "test_years": list(test_years),
            "train_locations": int(train["location"].nunique()),
            "test_locations": int(test["location"].nunique()),
            "test_locations_unseen": int(len(set(test["location"].unique()) - set(train["location"].unique()))),
        },
    )


def spatial_holdout(df: pd.DataFrame, holdout_region: str | None = None) -> HoldoutResult:
    """Hold out an entire geographic region."""
    available = [f for f in FEATURES if f in df.columns]

    df = df.copy()
    df["region"] = df["location"].apply(_extract_region)

    if holdout_region is None:
        # Pick the region with the most data for a meaningful test
        region_counts = df["region"].value_counts()
        holdout_region = region_counts.index[0]

    train = df[df["region"] != holdout_region]
    test = df[df["region"] == holdout_region]

    if len(test) < 10:
        return HoldoutResult(
            strategy=f"spatial_holdout_{holdout_region}",
            r2=float("nan"), rmse=float("nan"), mae=float("nan"),
            train_rows=len(train), test_rows=len(test),
            details={"error": f"Too few test rows for region {holdout_region}"},
        )

    X_train = train[available].values
    y_train = train[TARGET].values
    X_test = test[available].values
    y_test = test[TARGET].values

    model = _train_lgb(X_train, y_train)
    preds = model.predict(X_test)

    return HoldoutResult(
        strategy=f"spatial_holdout_{holdout_region}",
        r2=r2_score(y_test, preds),
        rmse=np.sqrt(mean_squared_error(y_test, preds)),
        mae=mean_absolute_error(y_test, preds),
        train_rows=len(train),
        test_rows=len(test),
        details={
            "holdout_region": holdout_region,
            "train_regions": sorted(train["region"].unique().tolist()),
            "test_locations": sorted(test["location"].unique().tolist())[:20],
            "test_location_count": int(test["location"].nunique()),
        },
    )


def spatiotemporal_blocked(df: pd.DataFrame, n_splits: int = 5) -> HoldoutResult:
    """Spatiotemporal blocking: hold out entire region-year blocks.

    This is the gold standard from the mackerel study — ensures the model
    can't memorize either spatial or temporal patterns.
    """
    available = [f for f in FEATURES if f in df.columns]

    df = df.copy()
    df["region"] = df["location"].apply(_extract_region)
    df["block"] = df["region"] + "_" + df["year"].astype(str)

    groups = df["block"].values
    X = df[available].values
    y = df[TARGET].values

    gkf = GroupKFold(n_splits=min(n_splits, len(df["block"].unique())))

    all_r2, all_rmse, all_mae = [], [], []
    for train_idx, test_idx in gkf.split(X, y, groups):
        X_train, X_test = X[train_idx], X[test_idx]
        y_train, y_test = y[train_idx], y[test_idx]

        model = _train_lgb(X_train, y_train)
        preds = model.predict(X_test)

        all_r2.append(r2_score(y_test, preds))
        all_rmse.append(np.sqrt(mean_squared_error(y_test, preds)))
        all_mae.append(mean_absolute_error(y_test, preds))

    return HoldoutResult(
        strategy="spatiotemporal_blocked",
        r2=float(np.mean(all_r2)),
        rmse=float(np.mean(all_rmse)),
        mae=float(np.mean(all_mae)),
        train_rows=len(df),
        test_rows=len(df),
        details={
            "n_splits": n_splits,
            "r2_std": float(np.std(all_r2)),
            "per_fold_r2": [round(r, 4) for r in all_r2],
            "n_blocks": int(df["block"].nunique()),
            "blocks": sorted(df["block"].unique().tolist()),
        },
    )


def location_leave_one_out(df: pd.DataFrame, top_n: int = 10) -> list[HoldoutResult]:
    """Leave-one-location-out for the most frequent locations."""
    available = [f for f in FEATURES if f in df.columns]

    loc_counts = df["location"].value_counts()
    results = []

    for loc in loc_counts.head(top_n).index:
        train = df[df["location"] != loc]
        test = df[df["location"] == loc]

        X_train = train[available].values
        y_train = train[TARGET].values
        X_test = test[available].values
        y_test = test[TARGET].values

        model = _train_lgb(X_train, y_train)
        preds = model.predict(X_test)

        results.append(HoldoutResult(
            strategy=f"leave_one_out_{loc}",
            r2=r2_score(y_test, preds),
            rmse=np.sqrt(mean_squared_error(y_test, preds)),
            mae=mean_absolute_error(y_test, preds),
            train_rows=len(train),
            test_rows=len(test),
            details={"location": loc},
        ))

    return results


def run_full_evaluation(dataset_path: str | Path) -> dict:
    """Run all holdout strategies and return a comprehensive report."""
    df = load_dataset(dataset_path)
    print(f"Dataset: {len(df)} rows, {df['location'].nunique()} locations, years {df['year'].min()}-{df['year'].max()}")
    print(f"Features available: {sum(1 for f in FEATURES if f in df.columns)}/{len(FEATURES)}")

    results = {}

    # 1. Temporal holdout
    print("\n=== Temporal Holdout ===")
    years = sorted(df["year"].unique())
    if len(years) >= 3:
        # Train on all but last 2 years
        split_year = years[-2]
        th = temporal_holdout(df, (years[0], split_year - 1), (split_year, years[-1]))
        results["temporal"] = th
        print(f"  Train {th.details['train_years']} ({th.train_rows} rows, {th.details['train_locations']} locs)")
        print(f"  Test  {th.details['test_years']} ({th.test_rows} rows, {th.details['test_locations']} locs, {th.details['test_locations_unseen']} unseen)")
        print(f"  R²={th.r2:.4f}  RMSE={th.rmse:.2f}  MAE={th.mae:.2f}")

    # 2. Spatial holdout (each region)
    print("\n=== Spatial Holdout (by region) ===")
    df_tmp = df.copy()
    df_tmp["region"] = df_tmp["location"].apply(_extract_region)
    spatial_results = []
    for region in sorted(df_tmp["region"].unique()):
        if df_tmp[df_tmp["region"] == region].shape[0] >= 10:
            sh = spatial_holdout(df, region)
            spatial_results.append(sh)
            print(f"  Holdout {region}: R²={sh.r2:.4f}  RMSE={sh.rmse:.2f}  ({sh.test_rows} test rows, {sh.details.get('test_location_count', '?')} locs)")
    results["spatial"] = spatial_results

    # 3. Spatiotemporal blocked
    print("\n=== Spatiotemporal Blocked CV ===")
    stb = spatiotemporal_blocked(df)
    results["spatiotemporal"] = stb
    print(f"  Mean R²={stb.r2:.4f} ± {stb.details['r2_std']:.4f}")
    print(f"  Per-fold R²: {stb.details['per_fold_r2']}")
    print(f"  {stb.details['n_blocks']} blocks across {stb.details['n_splits']} folds")

    # 4. Leave-one-location-out
    print("\n=== Leave-One-Location-Out (top 10) ===")
    lolo = location_leave_one_out(df, top_n=10)
    results["lolo"] = lolo
    for r in lolo:
        loc = r.details["location"]
        print(f"  {loc}: R²={r.r2:.4f}  RMSE={r.rmse:.2f}  ({r.test_rows} rows)")

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY — Model Generalization")
    print("=" * 60)
    if "temporal" in results:
        print(f"  Temporal holdout R²:       {results['temporal'].r2:.4f}")
    if spatial_results:
        sp_r2s = [s.r2 for s in spatial_results if not np.isnan(s.r2)]
        print(f"  Spatial holdout R² (mean): {np.mean(sp_r2s):.4f}")
        print(f"  Spatial holdout R² (min):  {min(sp_r2s):.4f}")
    print(f"  Spatiotemporal blocked R²: {stb.r2:.4f} ± {stb.details['r2_std']:.4f}")
    if lolo:
        lolo_r2s = [r.r2 for r in lolo]
        print(f"  Leave-one-out R² (mean):   {np.mean(lolo_r2s):.4f}")

    return results


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "castline/validation/data/assembled/validation_dataset_v4.csv"
    run_full_evaluation(path)
