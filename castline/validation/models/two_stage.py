"""Two-stage model for LOO generalization.

Stage 1: Predict location_mean_weight from physical/geographic features.
         This stage MUST work for unseen locations.
         Features: morphometry, lat/lon, creel proximity, ecoregion, species habitat.

Stage 2: Predict residual (deviation from location mean) from environmental conditions.
         Features: water temp, pressure, wind, spawn phase, etc.

Final: prediction = stage1_pred + stage2_pred

This decomposition is key because:
- Location identity explains ~66% of variance → Stage 1
- Environmental conditions explain ~10-15% within each location → Stage 2
- By training Stage 1 on location-level aggregates (258 data points),
  we can use all the location quality proxy features
- Stage 2 can learn temporal patterns without location bias
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.neighbors import KNeighborsRegressor

warnings.filterwarnings("ignore")

TARGET = "median_weight_lb"

# Stage 1 features: location-level characteristics (must generalize to unseen locations)
STAGE1_FEATURES = [
    # Morphometry
    "area_acres", "max_depth_ft", "shore_dev", "is_lake",
    # Geography
    "lat", "lon",
    # Location quality proxies
    "morphometric_productivity_score", "latitude_growth_potential",
    "growing_degree_proxy", "shad_habitat_score",
    "ecoregion_mean_weight", "ecoregion_mean_cpue", "ecoregion_n_surveys",
    "ecoregion_cluster",
    "regional_cpue_100km", "regional_weight_100km", "n_nearby_creel_surveys",
    "regional_weight_x_shad", "productivity_x_latitude",
    "wtype_river", "wtype_reservoir", "wtype_natural_lake", "reservoir_score",
    # Species habitat (smallmouth vs largemouth)
    "smallmouth_habitat_score", "species_diversity_potential", "northern_trophy_potential",
]

# Stage 2 features: environmental conditions (temporal signal)
STAGE2_FEATURES = [
    # Temperature
    "water_temp_c", "air_temp_c",
    "om_air_temp_mean", "om_air_temp_max", "om_est_water_temp",
    "om_air_temp_7d_mean", "om_temp_trend_7d",
    "sat_estimated_water_temp_c", "sat_air_temp_7d_mean",
    # Pressure
    "pressure_mb", "pressure_delta_6h",
    "om_pressure_msl", "om_pressure_delta_24h", "om_pressure_delta_6h",
    # Wind
    "wind_speed_kph", "wind_dir_cos",
    "om_wind_max_kph", "om_wind_gust_kph",
    # Moisture
    "precip_24h_mm", "om_precip_mm", "om_precip_7d_total",
    "om_humidity", "om_dewpoint", "om_cloudcover",
    # Flow
    "discharge_cfs", "flow_delta_24h_pct", "discharge_pct_of_30d",
    "gage_height_ft", "gage_height_7d_mean", "gage_stability_7d",
    # Water quality
    "dissolved_oxygen_mgL", "turbidity_fnu", "ph",
    # Temporal
    "day_length_hours", "season_cos", "season_sin", "year", "month",
    "moon_illumination_pct", "solunar_score", "moon_phase",
    # Fish biology
    "spawn_phase", "metabolic_rate_index", "feeding_window_score",
    "prespawn_intensity", "postspawn_lethargy", "do_comfort_index",
    "pressure_fishing_quality", "season_quality_index",
    "wind_mixing_index", "conditions_stability_index",
    "catch_potential_index",
    # IV temporal features
    "discharge_cfs_delta_3h", "discharge_cfs_delta_24h", "discharge_cfs_cv_24h",
    "gage_height_ft_delta_3h", "gage_height_ft_delta_24h",
    "water_temp_c_delta_3h", "water_temp_c_range_72h",
    "discharge_spike_ratio",
    # Lake features
    "turnover_proximity", "thermal_stability", "wind_fetch_score",
    "windblown_quality", "pressure_fishing_score", "lake_solunar_boost",
    # Interactions
    "wind_lake_interaction", "pressure_solunar_interaction",
]


def _avail(df, features):
    return [f for f in features if f in df.columns and df[f].notna().mean() > 0.03]


def _extract_region(location):
    parts = location.rsplit(",", 1)
    state = parts[1].strip()[:2].upper() if len(parts) == 2 else "UNK"
    regions = {
        "SE": ["AL", "FL", "GA", "SC", "NC", "VA", "TN", "MS", "LA", "AR"],
        "NE": ["NY", "PA", "MD", "DE", "NJ", "CT", "MA", "ME", "VT", "NH"],
        "MW": ["WI", "MN", "MI", "OH", "IN", "IL", "IA", "MO", "KS", "ND", "SD", "NE"],
        "SW": ["TX", "OK", "AZ", "NM", "UT", "CO"],
        "W": ["CA", "OR", "WA", "ID", "MT", "WY", "NV"],
    }
    for region, states in regions.items():
        if state in states:
            return region
    return "OT"


def build_location_summary(df):
    """Build location-level summary for Stage 1 training."""
    loc_agg = df.groupby("location").agg(
        mean_weight=(TARGET, "mean"),
        n_events=(TARGET, "count"),
        weight_std=(TARGET, "std"),
    ).reset_index()

    # Get first row's static features for each location
    static_cols = _avail(df, STAGE1_FEATURES)
    for col in static_cols:
        loc_agg[col] = df.groupby("location")[col].first().values

    return loc_agg


def train_stage1(loc_summary, features):
    """Train Stage 1: location quality prediction.

    Use multiple models and ensemble for robustness.
    """
    avail = [f for f in features if f in loc_summary.columns and loc_summary[f].notna().mean() > 0.1]

    X = loc_summary[avail].values
    y = loc_summary["mean_weight"].values

    # Fill NaN with column median
    from sklearn.impute import SimpleImputer
    imputer = SimpleImputer(strategy="median")
    X = imputer.fit_transform(X)

    # Ensemble of diverse models
    models = {
        "rf": RandomForestRegressor(n_estimators=200, max_depth=8, min_samples_leaf=3, random_state=42),
        "gbr": GradientBoostingRegressor(n_estimators=200, max_depth=4, min_samples_leaf=3,
                                          learning_rate=0.05, random_state=42),
        "knn": KNeighborsRegressor(n_neighbors=min(7, len(X) // 3), weights="distance"),
        "ridge": Ridge(alpha=10.0),
    }

    for name, model in models.items():
        model.fit(X, y)

    return models, avail, imputer


def predict_stage1(models, imputer, features, X_raw):
    """Predict location mean weight using ensemble."""
    X = imputer.transform(X_raw)
    preds = []
    for name, model in models.items():
        preds.append(model.predict(X))
    # Weighted average (GBR and RF tend to be best)
    weights = {"rf": 0.35, "gbr": 0.35, "knn": 0.15, "ridge": 0.15}
    weighted_pred = sum(preds[i] * weights[name] for i, (name, _) in enumerate(models.items()))
    return weighted_pred


def train_stage2(df, features, location_preds):
    """Train Stage 2: predict residual from environmental features."""
    avail = _avail(df, features)

    # Residual = actual - stage1_predicted_location_mean
    df = df.copy()
    df["_stage1_pred"] = df["location"].map(location_preds)
    df["_residual"] = df[TARGET] - df["_stage1_pred"]

    X = df[avail].values
    y = df["_residual"].values

    # Fill NaN
    from sklearn.impute import SimpleImputer
    imputer = SimpleImputer(strategy="median")
    X = imputer.fit_transform(X)

    model = lgb.LGBMRegressor(
        n_estimators=500, learning_rate=0.03, max_depth=5,
        num_leaves=31, min_child_samples=8,
        subsample=0.8, colsample_bytree=0.7,
        reg_alpha=0.5, reg_lambda=2.0,
        verbosity=-1,
    )
    model.fit(X, y)

    return model, avail, imputer


def two_stage_loo(df, top_n=15):
    """Run leave-one-location-out evaluation with two-stage model."""
    s1_avail = _avail(df, STAGE1_FEATURES)
    s2_avail = _avail(df, STAGE2_FEATURES)

    if "year" not in df.columns and "date" in df.columns:
        df["year"] = pd.to_datetime(df["date"]).dt.year
    if "month" not in df.columns and "date" in df.columns:
        df["month"] = pd.to_datetime(df["date"]).dt.month

    loc_counts = df["location"].value_counts()

    results = []
    for loc in loc_counts.head(top_n).index:
        train_df = df[df["location"] != loc].copy()
        test_df = df[df["location"] == loc].copy()

        # Stage 1: Train location quality model on training locations
        loc_summary = build_location_summary(train_df)
        s1_models, s1_features, s1_imputer = train_stage1(loc_summary, STAGE1_FEATURES)

        # Predict location mean for ALL training locations
        train_loc_preds = {}
        for _, row in loc_summary.iterrows():
            X_row = row[s1_features].values.reshape(1, -1)
            pred = predict_stage1(s1_models, s1_imputer, s1_features, X_row)
            train_loc_preds[row["location"]] = float(pred[0])

        # Predict location mean for test location (unseen!)
        test_row_features = test_df[s1_features].iloc[0:1].values
        test_loc_pred = float(predict_stage1(s1_models, s1_imputer, s1_features, test_row_features)[0])

        # Stage 2: Train residual model
        s2_model, s2_features, s2_imputer = train_stage2(train_df, STAGE2_FEATURES, train_loc_preds)

        # Predict for test
        X_test = test_df[s2_features].values
        X_test = s2_imputer.transform(X_test)
        residual_pred = s2_model.predict(X_test)

        # Final prediction
        final_pred = test_loc_pred + residual_pred
        y_test = test_df[TARGET].values

        r2 = r2_score(y_test, final_pred)
        results.append({
            "location": loc,
            "r2": round(r2, 4),
            "n": len(test_df),
            "actual_mean": round(float(y_test.mean()), 2),
            "pred_loc_mean": round(test_loc_pred, 2),
            "pred_final_mean": round(float(final_pred.mean()), 2),
            "actual_std": round(float(y_test.std()), 2),
        })

    return results


def run_two_stage_evaluation(dataset_path):
    """Run the two-stage model evaluation."""
    print("=" * 70)
    print("TWO-STAGE MODEL EVALUATION (for LOO generalization)")
    print("=" * 70)

    df = pd.read_csv(dataset_path)
    if "year" not in df.columns and "date" in df.columns:
        df["year"] = pd.to_datetime(df["date"]).dt.year
    if "month" not in df.columns and "date" in df.columns:
        df["month"] = pd.to_datetime(df["date"]).dt.month

    print(f"\nDataset: {len(df)} rows, {df['location'].nunique()} locations")

    # Apply feature engineering from train_v6
    sys.path.insert(0, ".")
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("train_v6", "castline/validation/train_v6.py")
        mod = importlib.util.module_from_spec(spec)
        mod.__name__ = "train_v6"
        sys.modules["train_v6"] = mod
        spec.loader.exec_module(mod)
        df = mod.add_engineered_features(df)
        print(f"Applied feature engineering: {len(df.columns)} columns")
    except Exception as e:
        print(f"Warning: Could not load feature engineering: {e}")

    # Stage 1 analysis: How well can we predict location quality?
    print("\n--- Stage 1: Location Quality Prediction ---")
    loc_summary = build_location_summary(df)
    s1_avail = _avail(loc_summary, STAGE1_FEATURES)
    print(f"  Locations: {len(loc_summary)}")
    print(f"  Stage 1 features available: {len(s1_avail)}/{len(STAGE1_FEATURES)}")
    print(f"  Features: {s1_avail}")

    # LOO on locations for Stage 1
    print("\n  Location-LOO for Stage 1 (predicting mean_weight):")
    loc_preds = {}
    for i, (_, row) in enumerate(loc_summary.iterrows()):
        train = loc_summary.drop(i)
        test_X = row[s1_avail].values.reshape(1, -1)

        from sklearn.impute import SimpleImputer
        imputer = SimpleImputer(strategy="median")
        X_train = imputer.fit_transform(train[s1_avail].values)
        y_train = train["mean_weight"].values
        test_X_imp = imputer.transform(test_X)

        # Use RF for single-model LOO
        rf = RandomForestRegressor(n_estimators=100, max_depth=6, min_samples_leaf=3, random_state=42)
        rf.fit(X_train, y_train)
        pred = float(rf.predict(test_X_imp)[0])
        loc_preds[row["location"]] = pred

    actual = loc_summary["mean_weight"].values
    predicted = np.array([loc_preds[loc] for loc in loc_summary["location"]])
    s1_r2 = r2_score(actual, predicted)
    s1_mae = mean_absolute_error(actual, predicted)
    print(f"  Stage 1 LOO R2: {s1_r2:.4f}")
    print(f"  Stage 1 LOO MAE: {s1_mae:.2f} lbs")

    # Show worst predictions
    errors = np.abs(actual - predicted)
    worst_idx = np.argsort(errors)[-10:]
    print("\n  Worst Stage 1 predictions:")
    for i in worst_idx[::-1]:
        loc = loc_summary.iloc[i]["location"]
        print(f"    {loc[:40]:40s}: actual={actual[i]:.1f}, pred={predicted[i]:.1f}, err={errors[i]:.1f}")

    # Full LOO evaluation
    print("\n--- Full Two-Stage LOO ---")
    results = two_stage_loo(df, top_n=15)

    r2s = [r["r2"] for r in results]
    print(f"\n  Mean LOO R2: {np.mean(r2s):.4f}")
    print(f"  Median LOO R2: {np.median(r2s):.4f}")

    for r in results:
        status = "OK" if r["r2"] > 0 else "BAD"
        print(f"  {r['location'][:45]:45s}: R2={r['r2']:7.4f} (n={r['n']:3d}, actual={r['actual_mean']:5.1f}, pred_loc={r['pred_loc_mean']:5.1f}, pred_final={r['pred_final_mean']:5.1f}) [{status}]")

    return results


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "castline/validation/data/assembled/validation_dataset_v6.csv"
    run_two_stage_evaluation(path)
