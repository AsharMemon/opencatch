"""LOO specialist model: designed specifically to maximize leave-one-location-out R².

Key strategies:
1. Location similarity transfer: Find K most similar training locations,
   use their mean_weight as a prior for the unseen location.
2. Environmental deviation model: Predict how much each event deviates
   from the location mean based on environmental conditions.
3. Regularized target encoding: Use creel data + morphometry + geography
   to estimate location quality with heavy regularization.
4. Ensemble prediction: Combine multiple approaches.

The fundamental challenge: location identity explains ~66% of variance,
and we need to predict this from observable features alone.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import r2_score
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")

TARGET = "median_weight_lb"

# Features for location similarity matching
LOCATION_FEATURES = [
    "area_acres", "max_depth_ft", "shore_dev", "lat", "lon", "is_lake",
    "morphometric_productivity_score", "latitude_growth_potential",
    "growing_degree_proxy", "shad_habitat_score",
    "ecoregion_mean_weight", "ecoregion_mean_cpue",
    "n_nearby_creel_surveys", "regional_cpue_100km", "regional_weight_100km",
    "wtype_river", "wtype_reservoir", "wtype_natural_lake",
    "smallmouth_habitat_score", "northern_trophy_potential",
    "species_diversity_potential",
]

# Environmental features for temporal prediction
ENV_FEATURES = [
    "water_temp_c", "air_temp_c", "pressure_mb", "wind_speed_kph",
    "discharge_cfs", "gage_height_ft",
    "om_pressure_msl", "om_humidity", "om_cloudcover",
    "om_air_temp_7d_mean", "om_temp_trend_7d",
    "om_pressure_delta_24h", "om_pressure_delta_6h",
    "spawn_phase", "metabolic_rate_index", "feeding_window_score",
    "pressure_fishing_quality", "season_quality_index",
    "wind_mixing_index", "do_comfort_index",
    "catch_potential_index",
    "day_length_hours", "season_cos", "season_sin",
    "moon_phase", "solunar_score",
    "turnover_proximity", "thermal_stability",
    "discharge_cfs_delta_24h", "gage_height_ft_delta_24h",
    "discharge_spike_ratio",
]


def _avail(df, features):
    return [f for f in features if f in df.columns and df[f].notna().mean() > 0.03]


def _build_location_profiles(df, loc_features):
    """Build a feature profile for each location."""
    avail = _avail(df, loc_features)
    profiles = df.groupby("location").agg(
        mean_weight=(TARGET, "mean"),
        n_events=(TARGET, "count"),
        weight_std=(TARGET, "std"),
        **{f: (f, "first") for f in avail},
    ).reset_index()
    return profiles, avail


def loo_with_similarity_transfer(df, top_n=15, k_neighbors=5):
    """LOO evaluation using location similarity transfer.

    For each held-out location:
    1. Find K most similar locations in training set (by feature similarity)
    2. Use their weighted mean weight as the location quality estimate
    3. Train an environmental model on ALL training data to predict residuals
    4. Combine: prediction = similarity_weighted_mean + env_residual
    """
    if "year" not in df.columns and "date" in df.columns:
        df["year"] = pd.to_datetime(df["date"]).dt.year
    if "month" not in df.columns and "date" in df.columns:
        df["month"] = pd.to_datetime(df["date"]).dt.month

    loc_counts = df["location"].value_counts()
    loc_features = _avail(df, LOCATION_FEATURES)
    env_features = _avail(df, ENV_FEATURES)

    results = []

    for test_loc in loc_counts.head(top_n).index:
        train_df = df[df["location"] != test_loc].copy()
        test_df = df[df["location"] == test_loc].copy()

        # Build training location profiles
        train_profiles, prof_features = _build_location_profiles(train_df, LOCATION_FEATURES)
        if len(prof_features) < 3:
            continue

        # Prepare location feature matrices
        imp = SimpleImputer(strategy="median")
        scaler = StandardScaler()

        X_train_loc = imp.fit_transform(train_profiles[prof_features].values)
        X_train_loc = scaler.fit_transform(X_train_loc)

        # Test location features (from first row)
        test_loc_features = test_df[prof_features].iloc[0:1].values
        X_test_loc = scaler.transform(imp.transform(test_loc_features))

        # Find K nearest neighbors
        k = min(k_neighbors, len(train_profiles) - 1)
        nn = NearestNeighbors(n_neighbors=k, metric="euclidean")
        nn.fit(X_train_loc)
        distances, indices = nn.kneighbors(X_test_loc)

        # Inverse-distance weighted mean of neighbor location mean weights
        distances = distances[0]
        indices = indices[0]
        if distances.min() == 0:
            weights = np.zeros_like(distances)
            weights[distances == 0] = 1.0
        else:
            weights = 1.0 / distances
        weights /= weights.sum()

        neighbor_means = train_profiles.iloc[indices]["mean_weight"].values
        similarity_pred = float(np.dot(weights, neighbor_means))

        # Also try a regression approach to predict location mean
        X_all_loc = imp.transform(train_profiles[prof_features].values)
        y_loc = train_profiles["mean_weight"].values

        rf_loc = RandomForestRegressor(
            n_estimators=200, max_depth=6, min_samples_leaf=3, random_state=42
        )
        rf_loc.fit(X_all_loc, y_loc)
        regression_pred = float(rf_loc.predict(imp.transform(test_loc_features))[0])

        # Blend: 50% similarity, 50% regression
        loc_pred = 0.5 * similarity_pred + 0.5 * regression_pred

        # Stage 2: Environmental residual prediction
        # Train on all training data, predicting deviation from location mean
        train_loc_means = train_df.groupby("location")[TARGET].transform("mean")
        train_df["_residual"] = train_df[TARGET] - train_loc_means

        imp_env = SimpleImputer(strategy="median")
        X_train_env = imp_env.fit_transform(train_df[env_features].values)
        y_train_env = train_df["_residual"].values

        import lightgbm as lgb
        env_model = lgb.LGBMRegressor(
            n_estimators=300, learning_rate=0.05, max_depth=4,
            num_leaves=20, min_child_samples=10,
            subsample=0.8, colsample_bytree=0.7,
            reg_alpha=1.0, reg_lambda=3.0,
            verbosity=-1,
        )
        env_model.fit(X_train_env, y_train_env)

        # Predict for test
        X_test_env = imp_env.transform(test_df[env_features].values)
        env_pred = env_model.predict(X_test_env)

        # Final prediction
        final_pred = loc_pred + env_pred
        y_test = test_df[TARGET].values

        r2 = r2_score(y_test, final_pred)

        neighbor_locs = train_profiles.iloc[indices]["location"].values
        results.append({
            "location": test_loc,
            "r2": round(r2, 4),
            "n": len(test_df),
            "actual_mean": round(float(y_test.mean()), 2),
            "similarity_pred": round(similarity_pred, 2),
            "regression_pred": round(regression_pred, 2),
            "loc_pred": round(loc_pred, 2),
            "final_mean": round(float(final_pred.mean()), 2),
            "neighbors": [f"{n[:30]}({w:.2f})" for n, w in zip(neighbor_locs, weights)],
        })

    return results


def run_evaluation(dataset_path):
    """Run the LOO specialist evaluation."""
    print("=" * 70)
    print("LOO SPECIALIST MODEL")
    print("=" * 70)

    df = pd.read_csv(dataset_path)
    if "year" not in df.columns and "date" in df.columns:
        df["year"] = pd.to_datetime(df["date"]).dt.year
    if "month" not in df.columns and "date" in df.columns:
        df["month"] = pd.to_datetime(df["date"]).dt.month

    # Apply feature engineering
    sys.path.insert(0, ".")
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("train_v6", "castline/validation/train_v6.py")
        mod = importlib.util.module_from_spec(spec)
        mod.__name__ = "train_v6"
        sys.modules["train_v6"] = mod
        spec.loader.exec_module(mod)
        df = mod.add_engineered_features(df)
    except Exception as e:
        print(f"Warning: {e}")

    print(f"Dataset: {len(df)} rows, {df['location'].nunique()} locations")

    # Try different K values
    for k in [3, 5, 8, 12]:
        print(f"\n--- K={k} neighbors ---")
        results = loo_with_similarity_transfer(df, top_n=15, k_neighbors=k)
        r2s = [r["r2"] for r in results]
        print(f"  Mean LOO R2: {np.mean(r2s):.4f}")
        print(f"  Median LOO R2: {np.median(r2s):.4f}")
        positive = sum(1 for r in r2s if r > 0)
        print(f"  Locations with R2 > 0: {positive}/{len(r2s)}")

    # Show detailed results for best K
    print(f"\n--- Detailed Results (K=5) ---")
    results = loo_with_similarity_transfer(df, top_n=15, k_neighbors=5)
    r2s = [r["r2"] for r in results]
    print(f"\nMean LOO R2: {np.mean(r2s):.4f}")
    print(f"Median LOO R2: {np.median(r2s):.4f}")

    for r in results:
        status = "OK " if r["r2"] > 0 else "BAD"
        print(f"  [{status}] {r['location'][:40]:40s}: R2={r['r2']:7.4f} actual={r['actual_mean']:5.1f} sim={r['similarity_pred']:5.1f} reg={r['regression_pred']:5.1f} final={r['final_mean']:5.1f}")
        # Show neighbors
        print(f"        Neighbors: {', '.join(r['neighbors'][:3])}")

    return results


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "castline/validation/data/assembled/validation_dataset_v6.csv"
    run_evaluation(path)
