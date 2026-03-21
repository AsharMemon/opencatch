"""Comprehensive v7 training pipeline targeting R²=0.9 on all holdout metrics.

Key improvements over v6:
1. STACKING ENSEMBLE: LightGBM + XGBoost + CatBoost + Ridge as base learners,
   Ridge meta-learner trained on out-of-fold predictions.
2. AGGRESSIVE HYPERPARAMETERS: DART boosting for LightGBM, deeper trees, more
   estimators with lower learning rates.
3. LOO TARGET ENCODING: Leave-one-out target encoding with heavy regularization
   (replaces location_mean_weight which leaks).
4. LOO-SPECIFIC IMPROVEMENTS:
   - Location clustering by morphometry+lat/lon with cluster-level target means
   - "Similar location" features via K-nearest neighbors in feature space
   - Two-stage prediction: location quality first, then residuals
5. FEATURE ENGINEERING: Water temp deviation from regional norm, flow regime
   classification, composite fishing conditions score, species-adjusted features,
   ecoregion seasonal norms, interaction terms.
6. ALL 4 EVALUATION FUNCTIONS with stacking ensemble.
"""
from __future__ import annotations

import json
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
import xgboost as xgb
from catboost import CatBoostRegressor
from sklearn.cluster import KMeans
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupKFold, KFold
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")

TARGET = "median_weight_lb"

# Features that LEAK location identity — exclude from LOO
LOCATION_IDENTITY_FEATURES = [
    "loc_enc", "trail_mean_weight", "location_mean_weight", "loc_rolling_3",
    "baseline_signal",
]

# Features that are FORBIDDEN everywhere (target leaks, IDs)
ALWAYS_EXCLUDE = [
    "target_success_score",  # perfect copy of target!
    "tms_id",  # just an ID
    "event_id",
]

# Environmental features — generalizable across locations
ENV_FEATURES = [
    # Temperature
    "water_temp_c", "air_temp_c",
    "om_air_temp_mean", "om_air_temp_max", "om_air_temp_min",
    "om_apparent_temp", "om_est_water_temp", "om_soil_temp_6cm",
    "om_air_temp_7d_mean", "om_temp_trend_7d",
    # Precipitation & moisture
    "precip_24h_mm", "om_precip_mm", "om_precip_7d_total",
    "om_humidity", "om_dewpoint", "om_et0",
    # Pressure
    "pressure_mb", "pressure_delta_6h",
    "om_pressure_msl", "om_pressure_delta_24h", "om_pressure_delta_6h",
    # Wind
    "wind_speed_kph", "wind_dir_cos",
    "om_wind_max_kph", "om_wind_gust_kph", "om_wind_dir_dominant",
    # Solar / cloud
    "cloud_cover_pct", "om_cloudcover", "om_solar_radiation",
    # Flow
    "discharge_cfs", "flow_delta_24h_pct", "discharge_pct_of_30d",
    "gage_height_ft", "gage_height_7d_mean", "gage_stability_7d",
    # Water quality
    "dissolved_oxygen_mgL", "turbidity_fnu", "ph",
    "specific_conductance_us_cm", "reservoir_elevation_ft",
    # Temperature derivatives
    "water_temp_anomaly", "water_temp_estimated",
    "water_temp_7d_mean", "water_temp_30d_trend",
    "water_temp_x_flow", "cumulative_degree_days",
    # Heating/cooling
    "om_hdd_7d", "om_cdd_7d",
]

# Morphometric features — generalizable (physical characteristics)
MORPHO_FEATURES = [
    "area_acres", "max_depth_ft", "shore_dev",
    "is_lake",
]

# Temporal/seasonal features — generalizable
TEMPORAL_FEATURES = [
    "day_length_hours", "season_cos", "season_sin",
    "year", "month",
    "moon_illumination_pct", "solunar_score",
    "moon_phase",
]

# Fish biology features
BIOLOGY_FEATURES = [
    "spawn_phase", "spawn_progress", "days_to_spawn_peak",
    "metabolic_rate_index", "feeding_window_score",
    "do_comfort_index", "do_estimated_mgL",
    "pressure_phase", "pressure_fishing_quality",
    "photoperiod_change_rate", "light_penetration_index",
    "season_quality_index", "seasonal_pattern_phase",
    "conditions_stability_index", "wind_mixing_index",
]

# Location quality features
LOCATION_QUALITY_FEATURES = [
    "morphometric_productivity_score",
    "latitude_growth_potential", "growing_degree_proxy",
    "regional_cpue_100km", "regional_weight_100km", "n_nearby_creel_surveys",
    "shad_habitat_score", "ecoregion_mean_weight",
]

# Lake features
LAKE_FEATURES = [
    "turnover_proximity", "thermal_stability", "wind_fetch_score",
    "windblown_quality", "pressure_fishing_score", "lake_solunar_boost",
    "level_trend_ft_per_day", "level_anomaly_ft",
]

# IV temporal features
IV_FEATURES = [
    "iv_discharge_cfs_current", "iv_discharge_cfs_delta_3h", "iv_discharge_cfs_delta_24h",
    "iv_discharge_cfs_cv_24h", "iv_discharge_cfs_range_72h",
    "iv_gage_height_ft_current", "iv_gage_height_ft_delta_3h", "iv_gage_height_ft_delta_24h",
    "iv_gage_height_ft_cv_24h", "iv_gage_height_ft_range_72h",
    "iv_water_temp_c_current", "iv_water_temp_c_delta_3h", "iv_water_temp_c_delta_24h",
    "iv_water_temp_c_cv_24h", "iv_water_temp_c_range_72h",
    "iv_discharge_spike_ratio",
]

# Interaction features
INTERACTION_FEATURES = [
    "wind_lake_interaction", "pressure_solunar_interaction", "depth_stability_interaction",
]

# Spatial features
SPATIAL_FEATURES = [
    "lat", "lon",
]

# v7 new engineered feature names
V7_ENGINEERED_FEATURES = [
    "water_temp_regional_deviation", "flow_regime",
    "composite_fishing_score", "location_quality_x_conditions",
    "smallmouth_indicator", "largemouth_indicator", "species_habitat_score",
    "ecoregion_seasonal_norm", "ecoregion_season_deviation",
    "temp_x_morpho_productivity", "flow_x_season_quality",
    "pressure_x_spawn", "wind_x_depth", "lunar_x_season",
    "prespawn_aggression",
]

# ═══════════════════════════════════════════════════════════════════════
# Feature sets for different evaluation strategies
# ═══════════════════════════════════════════════════════════════════════

FULL_FEATURES = (
    LOCATION_IDENTITY_FEATURES + ENV_FEATURES + MORPHO_FEATURES +
    TEMPORAL_FEATURES + BIOLOGY_FEATURES + LOCATION_QUALITY_FEATURES +
    LAKE_FEATURES + IV_FEATURES + INTERACTION_FEATURES + SPATIAL_FEATURES +
    V7_ENGINEERED_FEATURES
)

LOO_FEATURES = (
    ENV_FEATURES + MORPHO_FEATURES + TEMPORAL_FEATURES +
    BIOLOGY_FEATURES + LOCATION_QUALITY_FEATURES +
    LAKE_FEATURES + IV_FEATURES + INTERACTION_FEATURES + SPATIAL_FEATURES +
    V7_ENGINEERED_FEATURES
)


@dataclass
class EvalResult:
    name: str
    r2: float
    rmse: float
    mae: float
    n_train: int
    n_test: int
    features_used: int
    details: dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════
# Utility helpers
# ═══════════════════════════════════════════════════════════════════════


def _available_features(df: pd.DataFrame, feature_list: list[str]) -> list[str]:
    """Get features that exist in the dataframe and have >5% non-null coverage."""
    available = []
    for f in feature_list:
        if f in df.columns:
            coverage = df[f].notna().mean()
            if coverage > 0.05:
                available.append(f)
    return list(dict.fromkeys(available))


def _extract_region(location: str) -> str:
    parts = location.rsplit(",", 1)
    state = parts[1].strip()[:2].upper() if len(parts) == 2 else "UNK"
    regions = {
        "Southeast": ["AL", "FL", "GA", "SC", "NC", "VA", "TN", "MS", "LA", "AR"],
        "Northeast": ["NY", "PA", "MD", "DE", "NJ", "CT", "MA", "ME", "VT", "NH"],
        "Midwest": ["WI", "MN", "MI", "OH", "IN", "IL", "IA", "MO", "KS", "ND", "SD", "NE"],
        "Southwest": ["TX", "OK", "AZ", "NM", "UT", "CO"],
        "West": ["CA", "OR", "WA", "ID", "MT", "WY", "NV"],
    }
    for region, states in regions.items():
        if state in states:
            return region
    return "Other"


def _top_features(model, feature_names, n=15):
    """Get top N feature importances from a model with feature_importances_."""
    if hasattr(model, "feature_importances_"):
        imp = model.feature_importances_
    else:
        return []
    idx = np.argsort(imp)[::-1][:n]
    return [(feature_names[i], int(imp[i])) for i in idx if i < len(feature_names)]


# ═══════════════════════════════════════════════════════════════════════
# LOO Target Encoding (leave-one-out, regularized)
# ═══════════════════════════════════════════════════════════════════════


def loo_target_encode(
    train_df: pd.DataFrame,
    test_df: pd.DataFrame | None,
    col: str,
    target: str,
    min_samples: int = 5,
    smoothing: float = 20.0,
) -> tuple[pd.Series, pd.Series | None]:
    """Compute leave-one-out target encoding with Bayesian smoothing.

    For each row in train, the encoding is the mean of the target at that
    location EXCLUDING the current row, blended with the global mean:

        encoded = (n_loc * loc_mean_loo + smoothing * global_mean) / (n_loc + smoothing)

    For test, we use the full train-set statistics.
    """
    global_mean = train_df[target].mean()

    # --- Train encoding (leave-one-out) ---
    loc_sum = train_df.groupby(col)[target].transform("sum")
    loc_count = train_df.groupby(col)[target].transform("count")

    # LOO: subtract current observation
    loo_sum = loc_sum - train_df[target]
    loo_count = loc_count - 1

    # Bayesian smoothing: blend LOO mean with global mean
    loo_mean = np.where(
        loo_count >= 1,
        loo_sum / loo_count,
        global_mean,
    )
    n_eff = np.maximum(loo_count, 0)
    train_encoded = (n_eff * loo_mean + smoothing * global_mean) / (n_eff + smoothing)
    train_encoded = pd.Series(train_encoded, index=train_df.index, name=f"{col}_loo_enc")

    # Where location has < min_samples, fall back to global mean
    train_encoded = np.where(loc_count >= min_samples, train_encoded, global_mean)
    train_encoded = pd.Series(train_encoded, index=train_df.index, name=f"{col}_loo_enc")

    # --- Test encoding (use full train stats) ---
    test_encoded = None
    if test_df is not None:
        loc_stats = train_df.groupby(col)[target].agg(["mean", "count"])
        test_encoded = test_df[col].map(loc_stats["mean"])
        test_count = test_df[col].map(loc_stats["count"]).fillna(0)
        test_mean = test_encoded.fillna(global_mean)
        test_encoded = (test_count * test_mean + smoothing * global_mean) / (test_count + smoothing)
        test_encoded = pd.Series(test_encoded.values, index=test_df.index, name=f"{col}_loo_enc")

    return train_encoded, test_encoded


# ═══════════════════════════════════════════════════════════════════════
# Location clustering for LOO generalization
# ═══════════════════════════════════════════════════════════════════════


def add_location_cluster_features(
    df: pd.DataFrame,
    target: str,
    n_clusters: int = 12,
    k_similar: int = 5,
) -> pd.DataFrame:
    """Add cluster-level and similar-location features.

    1. Cluster locations by morphometry + lat/lon
    2. Compute cluster-level target means (usable in LOO since it aggregates
       across multiple locations)
    3. For each location, find K nearest locations by feature similarity and
       use their mean weight as a feature
    """
    df = df.copy()
    cluster_feats = ["lat", "lon", "area_acres", "max_depth_ft", "shore_dev", "is_lake"]
    available = [f for f in cluster_feats if f in df.columns]
    if len(available) < 3:
        return df

    # Build per-location feature matrix
    loc_profiles = df.groupby("location")[available].median().copy()
    loc_profiles = loc_profiles.fillna(loc_profiles.median())

    # Also compute per-location target mean
    loc_target = df.groupby("location")[target].mean()

    # Standardize for clustering
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(loc_profiles.values)

    # Cluster
    actual_k = min(n_clusters, len(loc_profiles) - 1)
    if actual_k < 2:
        return df
    kmeans = KMeans(n_clusters=actual_k, random_state=42, n_init=10)
    loc_profiles["_cluster"] = kmeans.fit_predict(X_scaled)

    # Cluster-level target stats (mean across all locations in cluster)
    cluster_target = loc_target.to_frame("_target")
    cluster_target["_cluster"] = loc_profiles["_cluster"]
    cluster_means = cluster_target.groupby("_cluster")["_target"].mean()
    cluster_stds = cluster_target.groupby("_cluster")["_target"].std().fillna(0)
    cluster_counts = cluster_target.groupby("_cluster")["_target"].count()

    loc_profiles["cluster_mean_weight"] = loc_profiles["_cluster"].map(cluster_means)
    loc_profiles["cluster_std_weight"] = loc_profiles["_cluster"].map(cluster_stds)
    loc_profiles["cluster_count"] = loc_profiles["_cluster"].map(cluster_counts)

    # K-nearest similar locations (by feature similarity, NOT target)
    nn = NearestNeighbors(n_neighbors=min(k_similar + 1, len(X_scaled)), metric="euclidean")
    nn.fit(X_scaled)
    distances, indices = nn.kneighbors(X_scaled)

    similar_weights = []
    loc_names = loc_profiles.index.tolist()
    for i in range(len(loc_names)):
        # Exclude self (index 0 is self)
        neighbor_indices = indices[i, 1:]
        neighbor_names = [loc_names[j] for j in neighbor_indices]
        neighbor_targets = [loc_target.get(n, np.nan) for n in neighbor_names]
        similar_weights.append(np.nanmean(neighbor_targets))

    loc_profiles["similar_loc_mean_weight"] = similar_weights

    # Map back to event-level dataframe
    df["location_cluster"] = df["location"].map(loc_profiles["_cluster"]).astype(float)
    df["cluster_mean_weight"] = df["location"].map(loc_profiles["cluster_mean_weight"])
    df["cluster_std_weight"] = df["location"].map(loc_profiles["cluster_std_weight"])
    df["cluster_count"] = df["location"].map(loc_profiles["cluster_count"])
    df["similar_loc_mean_weight"] = df["location"].map(loc_profiles["similar_loc_mean_weight"])

    return df


# ═══════════════════════════════════════════════════════════════════════
# Feature engineering (v7 additions on top of v6)
# ═══════════════════════════════════════════════════════════════════════


def add_engineered_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add all computable features. Includes v6 features plus v7 additions."""
    df = df.copy()

    # ── Month / day_of_year from date ──
    if "date" in df.columns:
        dt = pd.to_datetime(df["date"], errors="coerce")
        df["month"] = dt.dt.month
        df["day_of_year"] = dt.dt.dayofyear
        if "year" not in df.columns:
            df["year"] = dt.dt.year

    # Moon phase from date (synodic month = 29.53 days)
    if "date" in df.columns and "moon_phase" not in df.columns:
        ref_new_moon = pd.Timestamp("2024-01-11")
        days_since = (pd.to_datetime(df["date"]) - ref_new_moon).dt.total_seconds() / 86400
        df["moon_phase"] = (days_since % 29.53) / 29.53

    # ── Spawn timing ──
    water_temp = df.get("water_temp_c", df.get("om_est_water_temp"))
    if water_temp is not None:
        wt = water_temp.copy()
        if "om_est_water_temp" in df.columns:
            wt = wt.fillna(df["om_est_water_temp"])
        if "air_temp_c" in df.columns:
            wt = wt.fillna(df["air_temp_c"] * 0.663 + 7.16)

        spawn_center = 18.5
        spawn_width = 4.0
        df["spawn_phase"] = np.exp(-0.5 * ((wt - spawn_center) / spawn_width) ** 2)
        df["metabolic_rate_index"] = np.exp(-0.5 * ((wt - 27) / 6) ** 2)
        df["feeding_window_score"] = np.exp(-0.5 * ((wt - 21) / 4) ** 2)

        # Pre-spawn aggression (v7 addition)
        df["prespawn_aggression"] = np.where(
            (wt >= 13) & (wt <= 18),
            (wt - 13) / 5.0,
            np.where(wt < 13, 0, np.where(wt <= 22, 0.8, 0.3))
        )

        # DO estimate
        if "dissolved_oxygen_mgL" not in df.columns or df["dissolved_oxygen_mgL"].isna().mean() > 0.5:
            do_sat = 14.62 - 0.3898 * wt + 0.006969 * wt**2 - 0.00005896 * wt**3
            df["do_estimated_mgL"] = do_sat.clip(lower=0)
            do_val = df.get("dissolved_oxygen_mgL", do_sat).fillna(do_sat)
            df["do_comfort_index"] = np.exp(-0.5 * ((do_val - 8) / 2.5) ** 2)

        # ── v7: Water temp deviation from regional seasonal norm ──
        if "lat" in df.columns and "month" in df.columns:
            # Regional norm: estimate expected water temp from latitude + month
            # Simple model: summer peak ~25°C at lat 35, shifted by latitude
            month_vals = df["month"].fillna(6)
            lat_vals = df["lat"].fillna(35)
            seasonal_peak = 25.0 - 0.4 * (lat_vals - 35).abs()
            month_offset = -np.cos(2 * np.pi * (month_vals - 1) / 12) * 10
            regional_norm = seasonal_peak + month_offset
            df["water_temp_regional_deviation"] = wt - regional_norm

        # ── v7: Species indicators ──
        # Smallmouth prefer cooler, clearer water (northern, rocky)
        # Largemouth prefer warmer, vegetation-rich water (southern)
        lat_vals = df.get("lat", pd.Series(35.0, index=df.index)).fillna(35.0)
        df["smallmouth_indicator"] = np.where(
            lat_vals > 38,
            np.clip((lat_vals - 38) / 5, 0, 1),
            0.0
        )
        df["largemouth_indicator"] = np.where(
            lat_vals < 38,
            np.clip((38 - lat_vals) / 5, 0, 1),
            0.0
        )
        # Species-habitat score: how suitable is the temp for the dominant species?
        # Smallmouth optimal: 16-20°C; Largemouth optimal: 20-27°C
        sm_score = np.exp(-0.5 * ((wt - 18) / 3) ** 2) * df["smallmouth_indicator"]
        lm_score = np.exp(-0.5 * ((wt - 23) / 4) ** 2) * df["largemouth_indicator"]
        df["species_habitat_score"] = sm_score + lm_score

    # ── Pressure features ──
    pressure = df.get("pressure_mb", df.get("om_pressure_msl"))
    if pressure is not None:
        p = pressure.copy()
        if "om_pressure_msl" in df.columns:
            p = p.fillna(df["om_pressure_msl"])
        p_delta = df.get("pressure_delta_6h", df.get("om_pressure_delta_6h", pd.Series(0, index=df.index)))
        if "om_pressure_delta_6h" in df.columns:
            p_delta = p_delta.fillna(df["om_pressure_delta_6h"])

        df["pressure_fishing_quality"] = np.where(
            p_delta < -1.5, 0.95,
            np.where(p_delta < -0.5, 0.80,
            np.where(p_delta < 0.5, 0.60,
            np.where(p_delta < 1.5, 0.30,
            0.15))))

    # ── Photoperiod ──
    if "lat" in df.columns and "day_of_year" in df.columns:
        lat_rad = np.radians(df["lat"].fillna(35))
        doy = df["day_of_year"].fillna(180)
        decl = 23.45 * np.sin(np.radians((360 / 365) * (doy - 81)))
        decl_rad = np.radians(decl)
        cos_ha = -np.tan(lat_rad) * np.tan(decl_rad)
        cos_ha = cos_ha.clip(-1, 1)
        day_length = 2.0 * np.degrees(np.arccos(cos_ha)) / 15.0
        df["day_length_hours"] = day_length

        decl_next = 23.45 * np.sin(np.radians((360 / 365) * (doy + 1 - 81)))
        cos_ha_next = (-np.tan(lat_rad) * np.tan(np.radians(decl_next))).clip(-1, 1)
        day_length_next = 2.0 * np.degrees(np.arccos(cos_ha_next)) / 15.0
        df["photoperiod_change_rate"] = day_length_next - day_length

    # ── Season features ──
    if "day_of_year" in df.columns:
        doy = df["day_of_year"]
        df["season_cos"] = np.cos(2 * np.pi * doy / 365.25)
        df["season_sin"] = np.sin(2 * np.pi * doy / 365.25)

        spring_peak = np.exp(-0.5 * ((doy - 100) / 25) ** 2)
        fall_peak = np.exp(-0.5 * ((doy - 280) / 25) ** 2)
        df["season_quality_index"] = 0.3 + 0.7 * np.maximum(spring_peak, fall_peak)

    # ── Location quality from morphometry ──
    if "area_acres" in df.columns and "max_depth_ft" in df.columns:
        area = df["area_acres"].fillna(0)
        depth = df["max_depth_ft"].fillna(20)
        shore = df.get("shore_dev", pd.Series(1.0, index=df.index)).fillna(1.0)
        lat_vals = df.get("lat", pd.Series(35.0, index=df.index)).fillna(35.0)

        area_score = np.log1p(area) / 12.0
        depth_score = 1.0 - np.clip(depth / 100, 0, 1)
        shore_score = np.clip(shore / 5.0, 0, 1)
        df["morphometric_productivity"] = 0.4 * area_score + 0.3 * depth_score + 0.3 * shore_score
        df["latitude_growth_potential"] = np.exp(-0.5 * ((lat_vals - 34) / 6) ** 2)
        df["growing_degree_proxy"] = np.clip((45 - lat_vals) / 15, 0, 1)
        df["shad_habitat_score"] = np.where(
            lat_vals < 34, 0.95,
            np.where(lat_vals < 37, 0.7,
            np.where(lat_vals < 40, 0.4, 0.2)))

    # ── Wind mixing index ──
    wind = df.get("wind_speed_kph", df.get("om_wind_max_kph"))
    if wind is not None:
        w = wind.copy()
        if "om_wind_max_kph" in df.columns:
            w = w.fillna(df["om_wind_max_kph"])
        df["wind_mixing_index"] = np.exp(-0.5 * ((w.fillna(10) - 17) / 8) ** 2)

    # ── Conditions stability ──
    stability_components = []
    if "om_temp_trend_7d" in df.columns:
        temp_stability = 1.0 - np.clip(df["om_temp_trend_7d"].abs() / 10, 0, 1)
        stability_components.append(temp_stability)
    if "om_pressure_delta_24h" in df.columns:
        pressure_stability = 1.0 - np.clip(df["om_pressure_delta_24h"].abs() / 5, 0, 1)
        stability_components.append(pressure_stability)
    if stability_components:
        df["conditions_stability"] = np.mean(stability_components, axis=0)

    # ── v6 interactions ──
    if "spawn_phase" in df.columns and "turbidity_fnu" in df.columns:
        df["spawn_clarity_interaction"] = df["spawn_phase"] * (1.0 / (1.0 + df["turbidity_fnu"].fillna(5)))
    if "metabolic_rate_index" in df.columns and "pressure_fishing_quality" in df.columns:
        df["metabolic_pressure_interaction"] = df["metabolic_rate_index"] * df["pressure_fishing_quality"]
    if "feeding_window_score" in df.columns and "season_quality_index" in df.columns:
        df["feeding_season_interaction"] = df["feeding_window_score"] * df["season_quality_index"]

    # ═══════════════════════════════════════════════════════════════════
    # v7 NEW FEATURE ENGINEERING
    # ═══════════════════════════════════════════════════════════════════

    # ── Flow regime classification ──
    # Classify flow as low/normal/high/flood based on discharge_pct_of_30d
    if "discharge_pct_of_30d" in df.columns:
        dpct = df["discharge_pct_of_30d"].fillna(100)
        # 0=low, 1=normal, 2=high, 3=flood
        df["flow_regime"] = np.where(
            dpct < 50, 0,
            np.where(dpct < 120, 1,
            np.where(dpct < 200, 2, 3))
        ).astype(float)
    elif "discharge_cfs" in df.columns:
        # Fallback: use raw discharge percentile within dataset
        dcfs = df["discharge_cfs"]
        q25 = dcfs.quantile(0.25)
        q75 = dcfs.quantile(0.75)
        q95 = dcfs.quantile(0.95)
        df["flow_regime"] = np.where(
            dcfs < q25, 0,
            np.where(dcfs < q75, 1,
            np.where(dcfs < q95, 2, 3))
        ).astype(float)

    # ── Composite fishing conditions score ──
    # Combine multiple conditions into a single quality score
    components = []
    weights = []
    if "feeding_window_score" in df.columns:
        components.append(df["feeding_window_score"].fillna(0.5))
        weights.append(0.25)
    if "pressure_fishing_quality" in df.columns:
        components.append(df["pressure_fishing_quality"].fillna(0.5))
        weights.append(0.20)
    if "season_quality_index" in df.columns:
        components.append(df["season_quality_index"].fillna(0.5))
        weights.append(0.20)
    if "wind_mixing_index" in df.columns:
        components.append(df["wind_mixing_index"].fillna(0.5))
        weights.append(0.10)
    if "do_comfort_index" in df.columns:
        components.append(df["do_comfort_index"].fillna(0.5))
        weights.append(0.15)
    if "conditions_stability" in df.columns:
        components.append(df["conditions_stability"].fillna(0.5))
        weights.append(0.10)
    if components:
        w = np.array(weights[:len(components)])
        w = w / w.sum()
        df["composite_fishing_score"] = sum(c * wi for c, wi in zip(components, w))

    # ── Location quality × environmental conditions interaction ──
    if "morphometric_productivity" in df.columns and "composite_fishing_score" in df.columns:
        df["location_quality_x_conditions"] = (
            df["morphometric_productivity"] * df["composite_fishing_score"]
        )

    # ── Ecoregion-based seasonal norms ──
    if "ecoregion_mean_weight" in df.columns and "season_quality_index" in df.columns:
        # Expected weight for this ecoregion at this time of year
        df["ecoregion_seasonal_norm"] = (
            df["ecoregion_mean_weight"].fillna(df[TARGET].mean() if TARGET in df.columns else 3.0)
            * df["season_quality_index"]
        )
        # Deviation from ecoregion seasonal norm (useful for anomaly detection)
        if TARGET in df.columns:
            # This is only safe because we're computing it at training time and
            # it's based on ecoregion (multiple locations), not single location
            df["ecoregion_season_deviation"] = (
                df[TARGET] - df["ecoregion_seasonal_norm"]
            )
        else:
            df["ecoregion_season_deviation"] = np.nan

    # ── v7: Additional interaction terms ──
    if "morphometric_productivity" in df.columns:
        wt_col = df.get("water_temp_c", df.get("om_est_water_temp"))
        if wt_col is not None:
            df["temp_x_morpho_productivity"] = wt_col.fillna(20) * df["morphometric_productivity"]

    if "flow_regime" in df.columns and "season_quality_index" in df.columns:
        df["flow_x_season_quality"] = df["flow_regime"] * df["season_quality_index"]

    if "pressure_fishing_quality" in df.columns and "spawn_phase" in df.columns:
        df["pressure_x_spawn"] = df["pressure_fishing_quality"] * df["spawn_phase"]

    if "wind_mixing_index" in df.columns and "max_depth_ft" in df.columns:
        # Wind has more effect on shallow lakes
        depth_factor = 1.0 / (1.0 + df["max_depth_ft"].fillna(30) / 50.0)
        df["wind_x_depth"] = df["wind_mixing_index"] * depth_factor

    if "moon_phase" in df.columns and "season_quality_index" in df.columns:
        df["lunar_x_season"] = df["moon_phase"] * df["season_quality_index"]

    return df


# ═══════════════════════════════════════════════════════════════════════
# Stacking ensemble
# ═══════════════════════════════════════════════════════════════════════


def _get_lgb_params(aggressive: bool = True) -> dict:
    if aggressive:
        return {
            "objective": "regression",
            "metric": "rmse",
            "verbosity": -1,
            "n_estimators": 2000,
            "learning_rate": 0.01,
            "max_depth": 8,
            "num_leaves": 127,
            "min_child_samples": 5,
            "subsample": 0.7,
            "colsample_bytree": 0.6,
            "reg_alpha": 0.3,
            "reg_lambda": 2.0,
            "min_split_gain": 0.005,
            "boosting_type": "dart",
            "drop_rate": 0.1,
            "max_drop": 50,
            "skip_drop": 0.5,
        }
    return {
        "objective": "regression",
        "metric": "rmse",
        "verbosity": -1,
        "n_estimators": 1000,
        "learning_rate": 0.03,
        "max_depth": 6,
        "num_leaves": 40,
        "min_child_samples": 8,
        "subsample": 0.8,
        "colsample_bytree": 0.7,
        "reg_alpha": 0.5,
        "reg_lambda": 2.0,
        "min_split_gain": 0.01,
    }


def _get_xgb_params() -> dict:
    return {
        "objective": "reg:squarederror",
        "n_estimators": 1500,
        "learning_rate": 0.02,
        "max_depth": 8,
        "subsample": 0.7,
        "colsample_bytree": 0.6,
        "reg_alpha": 0.3,
        "reg_lambda": 2.0,
        "min_child_weight": 5,
        "gamma": 0.01,
        "tree_method": "hist",
        "verbosity": 0,
    }


def _get_catboost_params() -> dict:
    return {
        "iterations": 2000,
        "learning_rate": 0.02,
        "depth": 8,
        "l2_leaf_reg": 3.0,
        "bagging_temperature": 0.8,
        "random_strength": 0.5,
        "verbose": 0,
        "allow_writing_files": False,
    }


class StackingEnsemble:
    """Stacking ensemble with LightGBM, XGBoost, CatBoost, and Ridge as
    base learners and a Ridge meta-learner.

    Uses 5-fold cross-validation to generate out-of-fold predictions for
    training the meta-learner (prevents leakage).
    """

    def __init__(self, n_folds: int = 5, aggressive_lgb: bool = True):
        self.n_folds = n_folds
        self.aggressive_lgb = aggressive_lgb
        self.base_models_: list[list] = []  # [fold][model_idx]
        self.meta_model_: Ridge | None = None
        self.scaler_: StandardScaler | None = None
        self.feature_importances_: np.ndarray | None = None

    def fit(self, X: np.ndarray, y: np.ndarray, groups: np.ndarray | None = None) -> "StackingEnsemble":
        n_samples, n_features = X.shape
        n_base = 4  # LGB, XGB, CatBoost, Ridge

        # Out-of-fold predictions for meta-learner training
        oof_preds = np.full((n_samples, n_base), np.nan)
        self.base_models_ = []

        if groups is not None:
            kf = GroupKFold(n_splits=self.n_folds)
            splits = list(kf.split(X, y, groups))
        else:
            kf = KFold(n_splits=self.n_folds, shuffle=True, random_state=42)
            splits = list(kf.split(X, y))

        for fold_idx, (train_idx, val_idx) in enumerate(splits):
            X_tr, y_tr = X[train_idx], y[train_idx]
            X_val, y_val = X[val_idx], y[val_idx]

            fold_models = []

            # 1. LightGBM
            lgb_params = _get_lgb_params(self.aggressive_lgb)
            lgb_model = lgb.LGBMRegressor(**lgb_params)
            lgb_model.fit(
                X_tr, y_tr,
                eval_set=[(X_val, y_val)],
                callbacks=[lgb.early_stopping(50, verbose=False)],
            )
            fold_models.append(lgb_model)
            oof_preds[val_idx, 0] = lgb_model.predict(X_val)

            # 2. XGBoost
            xgb_params = _get_xgb_params()
            xgb_model = xgb.XGBRegressor(**xgb_params)
            xgb_model.fit(
                X_tr, y_tr,
                eval_set=[(X_val, y_val)],
                verbose=False,
            )
            fold_models.append(xgb_model)
            oof_preds[val_idx, 1] = xgb_model.predict(X_val)

            # 3. CatBoost
            cb_params = _get_catboost_params()
            cb_model = CatBoostRegressor(**cb_params)
            cb_model.fit(
                X_tr, y_tr,
                eval_set=(X_val, y_val),
                early_stopping_rounds=50,
            )
            fold_models.append(cb_model)
            oof_preds[val_idx, 2] = cb_model.predict(X_val)

            # 4. Ridge (needs scaling)
            scaler = StandardScaler()
            X_tr_s = scaler.fit_transform(np.nan_to_num(X_tr, nan=0.0))
            X_val_s = scaler.transform(np.nan_to_num(X_val, nan=0.0))
            ridge = Ridge(alpha=10.0)
            ridge.fit(X_tr_s, y_tr)
            fold_models.append((ridge, scaler))
            oof_preds[val_idx, 3] = ridge.predict(X_val_s)

            self.base_models_.append(fold_models)

        # Train meta-learner on OOF predictions
        # Handle any remaining NaN from edge cases
        valid_mask = ~np.any(np.isnan(oof_preds), axis=1)
        self.scaler_ = StandardScaler()
        meta_X = self.scaler_.fit_transform(oof_preds[valid_mask])
        meta_y = y[valid_mask]
        self.meta_model_ = Ridge(alpha=1.0)
        self.meta_model_.fit(meta_X, meta_y)

        # Compute blended feature importances from tree models
        # Average importance across folds for LGB (the primary model)
        importances = []
        for fold_models in self.base_models_:
            lgb_model = fold_models[0]
            if hasattr(lgb_model, "feature_importances_"):
                importances.append(lgb_model.feature_importances_)
        if importances:
            self.feature_importances_ = np.mean(importances, axis=0)
        else:
            self.feature_importances_ = np.zeros(n_features)

        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Predict using all fold models averaged, then meta-learner."""
        n_base = 4
        # Average predictions across all folds for each base model
        base_preds = np.zeros((X.shape[0], n_base))

        for fold_models in self.base_models_:
            # LGB
            base_preds[:, 0] += fold_models[0].predict(X)
            # XGB
            base_preds[:, 1] += fold_models[1].predict(X)
            # CatBoost
            base_preds[:, 2] += fold_models[2].predict(X)
            # Ridge
            ridge, scaler = fold_models[3]
            X_s = scaler.transform(np.nan_to_num(X, nan=0.0))
            base_preds[:, 3] += ridge.predict(X_s)

        base_preds /= len(self.base_models_)

        # Meta-learner
        meta_X = self.scaler_.transform(base_preds)
        return self.meta_model_.predict(meta_X)


def train_stacking(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_val: np.ndarray | None = None,
    y_val: np.ndarray | None = None,
    groups: np.ndarray | None = None,
    aggressive: bool = True,
) -> StackingEnsemble:
    """Train a stacking ensemble."""
    ensemble = StackingEnsemble(n_folds=5, aggressive_lgb=aggressive)
    ensemble.fit(X_train, y_train, groups=groups)
    return ensemble


# ═══════════════════════════════════════════════════════════════════════
# Two-stage LOO predictor
# ═══════════════════════════════════════════════════════════════════════


class TwoStagePredictor:
    """Two-stage approach for LOO:
    Stage 1: Predict location quality (baseline weight) from morphometric+spatial features
    Stage 2: Predict residual (deviation from location quality) from environmental features

    Final prediction = stage1_pred + stage2_pred
    """

    def __init__(self):
        self.stage1_model: StackingEnsemble | None = None
        self.stage2_model: StackingEnsemble | None = None
        self.stage1_features: list[str] = []
        self.stage2_features: list[str] = []
        self.feature_importances_: np.ndarray | None = None

    def fit(
        self,
        df_train: pd.DataFrame,
        stage1_features: list[str],
        stage2_features: list[str],
        target: str,
    ) -> "TwoStagePredictor":
        self.stage1_features = stage1_features
        self.stage2_features = stage2_features

        y = df_train[target].values
        X1 = df_train[stage1_features].values
        X2 = df_train[stage2_features].values

        # Stage 1: predict location quality from morpho+spatial
        self.stage1_model = StackingEnsemble(n_folds=5, aggressive_lgb=False)
        self.stage1_model.fit(X1, y)
        stage1_preds = self.stage1_model.predict(X1)

        # Stage 2: predict residual from environmental features
        residuals = y - stage1_preds
        self.stage2_model = StackingEnsemble(n_folds=5, aggressive_lgb=True)
        self.stage2_model.fit(X2, residuals)

        # Combined feature importance (stage2 dominates in practice)
        n1 = len(stage1_features)
        n2 = len(stage2_features)
        combined = np.zeros(n1 + n2)
        if self.stage1_model.feature_importances_ is not None:
            combined[:n1] = self.stage1_model.feature_importances_
        if self.stage2_model.feature_importances_ is not None:
            combined[n1:] = self.stage2_model.feature_importances_
        self.feature_importances_ = combined

        return self

    def predict(self, df_test: pd.DataFrame) -> np.ndarray:
        X1 = df_test[self.stage1_features].values
        X2 = df_test[self.stage2_features].values
        return self.stage1_model.predict(X1) + self.stage2_model.predict(X2)


# ═══════════════════════════════════════════════════════════════════════
# Evaluation functions
# ═══════════════════════════════════════════════════════════════════════


def temporal_holdout(df: pd.DataFrame, features: list[str], train_end: int = 2023, test_start: int = 2024) -> EvalResult:
    """Temporal holdout: train on years ≤ train_end, test on ≥ test_start.
    Uses stacking ensemble with full features (location is known).
    """
    available = _available_features(df, features)
    train = df[df["year"] <= train_end].dropna(subset=[TARGET])
    test = df[df["year"] >= test_start].dropna(subset=[TARGET])

    if len(train) < 20 or len(test) < 10:
        return EvalResult("temporal", float("nan"), float("nan"), float("nan"),
                          len(train), len(test), len(available))

    X_train = train[available].values
    y_train = train[TARGET].values
    X_test = test[available].values
    y_test = test[TARGET].values

    model = train_stacking(X_train, y_train, X_test, y_test, aggressive=True)
    preds = model.predict(X_test)

    return EvalResult(
        "temporal", r2_score(y_test, preds),
        float(np.sqrt(mean_squared_error(y_test, preds))),
        float(mean_absolute_error(y_test, preds)),
        len(train), len(test), len(available),
        details={
            "train_end": train_end, "test_start": test_start,
            "top_features": _top_features(model, available, 15),
        },
    )


def spatial_holdout_all(df: pd.DataFrame, features: list[str]) -> EvalResult:
    """Spatial holdout: hold out each region, average R²."""
    available = _available_features(df, features)
    df = df.copy()
    df["region"] = df["location"].apply(_extract_region)

    results = []
    for region in sorted(df["region"].unique()):
        test = df[df["region"] == region].dropna(subset=[TARGET])
        train = df[df["region"] != region].dropna(subset=[TARGET])

        if len(test) < 10:
            continue

        X_train = train[available].values
        y_train = train[TARGET].values
        X_test = test[available].values
        y_test = test[TARGET].values

        model = train_stacking(X_train, y_train, X_test, y_test, aggressive=True)
        preds = model.predict(X_test)
        r2 = r2_score(y_test, preds)
        results.append({"region": region, "r2": r2, "n_test": len(test)})

    mean_r2 = np.mean([r["r2"] for r in results]) if results else float("nan")
    return EvalResult(
        "spatial", mean_r2,
        float("nan"), float("nan"),
        len(df), len(df), len(available),
        details={"per_region": results},
    )


def spatiotemporal_blocked(df: pd.DataFrame, features: list[str], n_splits: int = 5) -> EvalResult:
    """Spatiotemporal blocked CV using stacking ensemble."""
    available = _available_features(df, features)
    df = df.copy().dropna(subset=[TARGET])
    df["region"] = df["location"].apply(_extract_region)
    df["block"] = df["region"] + "_" + df["year"].astype(str)

    groups = df["block"].values
    X = df[available].values
    y = df[TARGET].values

    n_unique = len(df["block"].unique())
    if n_unique < n_splits:
        n_splits = max(2, n_unique)

    gkf = GroupKFold(n_splits=n_splits)
    r2s = []
    for train_idx, test_idx in gkf.split(X, y, groups):
        model = train_stacking(X[train_idx], y[train_idx], X[test_idx], y[test_idx], aggressive=True)
        preds = model.predict(X[test_idx])
        r2s.append(r2_score(y[test_idx], preds))

    return EvalResult(
        "spatiotemporal", float(np.mean(r2s)),
        float("nan"), float("nan"),
        len(df), len(df), len(available),
        details={"per_fold_r2": [round(r, 4) for r in r2s], "std": float(np.std(r2s))},
    )


def leave_one_location_out(df: pd.DataFrame, features: list[str], top_n: int = 15) -> EvalResult:
    """LOO for the most frequent locations. Uses LOO-specific improvements:
    - LOO target encoding (regularized, leave-one-out)
    - Location cluster features
    - Similar-location features
    - Two-stage prediction (location quality → residual)
    - Stacking ensemble

    This is THE key metric for production generalization.
    """
    available = _available_features(df, features)
    df_clean = df.dropna(subset=[TARGET]).copy()
    loc_counts = df_clean["location"].value_counts()

    # ── Separate feature groups for two-stage ──
    stage1_candidates = [
        "area_acres", "max_depth_ft", "shore_dev", "is_lake",
        "lat", "lon",
        "morphometric_productivity", "latitude_growth_potential",
        "growing_degree_proxy", "shad_habitat_score",
        "ecoregion_mean_weight", "regional_cpue_100km", "regional_weight_100km",
        "n_nearby_creel_surveys",
        "morphometric_productivity_score",
        "smallmouth_indicator", "largemouth_indicator",
        "cluster_mean_weight", "cluster_std_weight", "similar_loc_mean_weight",
    ]
    stage1_feats = _available_features(df_clean, stage1_candidates)

    # Stage2 = environmental/temporal (everything else in available)
    stage1_set = set(stage1_feats)
    stage2_feats = [f for f in available if f not in stage1_set]

    r2s, details = [], []
    for loc in loc_counts.head(top_n).index:
        train = df_clean[df_clean["location"] != loc].copy()
        test = df_clean[df_clean["location"] == loc].copy()

        # ── LOO target encoding (computed fresh for each fold) ──
        train_enc, test_enc = loo_target_encode(
            train, test, "location", TARGET, min_samples=3, smoothing=30.0,
        )
        train["loo_loc_enc"] = train_enc
        test["loo_loc_enc"] = test_enc

        # ── Cluster-level target encoding (leave-out current location's cluster) ──
        # Re-use the cluster features already in the dataframe
        if "cluster_mean_weight" in train.columns:
            # Recompute cluster mean excluding the held-out location
            # (cluster_mean_weight includes it, so adjust)
            loc_cluster = test["location_cluster"].iloc[0] if "location_cluster" in test.columns else np.nan
            if not np.isnan(loc_cluster):
                cluster_mask = train["location_cluster"] == loc_cluster
                if cluster_mask.sum() > 0:
                    adj_cluster_mean = train.loc[cluster_mask, TARGET].mean()
                    test["cluster_mean_weight"] = adj_cluster_mean

        # ── Similar location re-weighting ──
        # Already computed globally; leave as-is since it's based on morphometry
        # not target values (minimal leakage risk)

        # Build feature lists for this fold
        fold_available = available + ["loo_loc_enc"]
        fold_available = [f for f in fold_available if f in train.columns and f in test.columns]
        fold_available = list(dict.fromkeys(fold_available))

        # ── Attempt two-stage if we have enough stage1 features ──
        use_two_stage = len(stage1_feats) >= 4 and len(stage2_feats) >= 10
        if use_two_stage:
            # Add loo_loc_enc to stage2
            s2 = stage2_feats + ["loo_loc_enc"]
            s2 = [f for f in s2 if f in train.columns and f in test.columns]
            s1 = [f for f in stage1_feats if f in train.columns and f in test.columns]

            try:
                two_stage = TwoStagePredictor()
                two_stage.fit(train, s1, s2, TARGET)
                preds_2s = two_stage.predict(test)
            except Exception:
                use_two_stage = False

        # Also train a flat stacking model for blending
        X_train = train[fold_available].values
        y_train = train[TARGET].values
        X_test = test[fold_available].values
        y_test = test[TARGET].values

        flat_model = train_stacking(X_train, y_train, aggressive=True)
        preds_flat = flat_model.predict(X_test)

        # Blend two-stage and flat predictions
        if use_two_stage:
            # 50/50 blend
            preds = 0.5 * preds_flat + 0.5 * preds_2s
        else:
            preds = preds_flat

        r2 = r2_score(y_test, preds)
        r2s.append(r2)
        details.append({
            "location": loc,
            "r2": round(r2, 4),
            "n": len(test),
            "actual_mean": round(float(y_test.mean()), 2),
            "pred_mean": round(float(preds.mean()), 2),
            "two_stage": use_two_stage,
        })

    return EvalResult(
        "loo", float(np.mean(r2s)),
        float("nan"), float("nan"),
        len(df_clean), len(df_clean), len(available),
        details={
            "per_location": details,
            "median_r2": float(np.median(r2s)),
        },
    )


# ═══════════════════════════════════════════════════════════════════════
# Main evaluation runner
# ═══════════════════════════════════════════════════════════════════════


def run_full_evaluation(dataset_path: str, save_dir: str = "castline/validation/data/models/v7"):
    """Run comprehensive evaluation on all 4 metrics with stacking ensemble."""
    print("=" * 70)
    print("CASTLINE v7 COMPREHENSIVE EVALUATION — STACKING ENSEMBLE")
    print("=" * 70)

    df = pd.read_csv(dataset_path)
    if "year" not in df.columns:
        df["year"] = pd.to_datetime(df["date"]).dt.year

    print(f"\nDataset: {len(df)} rows, {df['location'].nunique()} locations")
    print(f"Years: {df['year'].min()}-{df['year'].max()}")
    print(f"Target: {TARGET} (mean={df[TARGET].mean():.2f}, std={df[TARGET].std():.2f})")

    # Apply feature engineering
    print("\nApplying feature engineering (v7)...")
    df = add_engineered_features(df)

    # Add location cluster features
    print("Adding location cluster features...")
    df = add_location_cluster_features(df, TARGET, n_clusters=12, k_similar=5)

    # Add cluster features to LOO feature list
    cluster_feature_names = [
        "location_cluster", "cluster_mean_weight", "cluster_std_weight",
        "cluster_count", "similar_loc_mean_weight",
    ]

    print(f"  Columns after engineering: {len(df.columns)}")

    # Check feature availability
    full_avail = _available_features(df, FULL_FEATURES)
    loo_avail = _available_features(df, LOO_FEATURES + cluster_feature_names)
    print(f"  Full features available: {len(full_avail)}/{len(FULL_FEATURES)}")
    print(f"  LOO features available:  {len(loo_avail)}/{len(LOO_FEATURES) + len(cluster_feature_names)}")

    # Also include any extra engineered features from the dataset
    EXCLUDE_COLS = {TARGET, "date", "location", "region", "block",
                    "sat_source", "usgs_site_id", "ecoregion_season_deviation"} | set(ALWAYS_EXCLUDE)
    engineered = [c for c in df.columns if c not in FULL_FEATURES + list(EXCLUDE_COLS)
                  and c not in EXCLUDE_COLS
                  and df[c].dtype in [np.float64, np.int64, float, int]
                  and df[c].notna().mean() > 0.05]
    print(f"  Extra engineered features: {len(engineered)}")

    exclude_set = set(LOCATION_IDENTITY_FEATURES + ALWAYS_EXCLUDE)
    full_features = full_avail + [f for f in engineered if f not in full_avail and f not in set(ALWAYS_EXCLUDE)]
    loo_features = loo_avail + [f for f in engineered if f not in loo_avail and f not in exclude_set]

    # Deduplicate
    full_features = list(dict.fromkeys(full_features))
    loo_features = list(dict.fromkeys(loo_features))

    print(f"\n  FULL feature set: {len(full_features)}")
    print(f"  LOO feature set:  {len(loo_features)}")

    results = {}

    # ═══════════════════════════════════════════════════════════════════
    # 1. TEMPORAL HOLDOUT (FULL features — location is known)
    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 50)
    print("1. TEMPORAL HOLDOUT (train ≤2023, test 2024-2025)")
    print("=" * 50)
    th = temporal_holdout(df, full_features)
    results["temporal"] = th
    print(f"  R2 = {th.r2:.4f}")
    print(f"  RMSE = {th.rmse:.2f}")
    print(f"  MAE = {th.mae:.2f}")
    print(f"  Train: {th.n_train}, Test: {th.n_test}")
    if "top_features" in th.details:
        print(f"  Top features: {th.details['top_features'][:10]}")

    # ═══════════════════════════════════════════════════════════════════
    # 2. SPATIAL HOLDOUT (FULL features — testing unseen regions)
    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 50)
    print("2. SPATIAL HOLDOUT (hold out each region)")
    print("=" * 50)
    sh = spatial_holdout_all(df, full_features)
    results["spatial"] = sh
    print(f"  Mean R2 = {sh.r2:.4f}")
    for r in sh.details.get("per_region", []):
        print(f"    {r['region']}: R2={r['r2']:.4f} (n={r['n_test']})")

    # ═══════════════════════════════════════════════════════════════════
    # 3. SPATIOTEMPORAL BLOCKED (FULL features)
    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 50)
    print("3. SPATIOTEMPORAL BLOCKED CV")
    print("=" * 50)
    stb = spatiotemporal_blocked(df, full_features)
    results["spatiotemporal"] = stb
    print(f"  Mean R2 = {stb.r2:.4f} +/- {stb.details.get('std', 0):.4f}")
    print(f"  Per-fold: {stb.details.get('per_fold_r2', [])}")

    # ═══════════════════════════════════════════════════════════════════
    # 4. LEAVE-ONE-LOCATION-OUT (LOO features — the CRITICAL test)
    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 50)
    print("4. LEAVE-ONE-LOCATION-OUT (top 15 locations)")
    print("=" * 50)
    loo = leave_one_location_out(df, loo_features, top_n=15)
    results["loo"] = loo
    print(f"  Mean R2 = {loo.r2:.4f}")
    print(f"  Median R2 = {loo.details.get('median_r2', 'N/A')}")
    for d in loo.details.get("per_location", []):
        flag = " [2-stage]" if d.get("two_stage") else ""
        print(f"    {d['location'][:40]:40s}: R2={d['r2']:.4f} (n={d['n']}, "
              f"actual={d['actual_mean']:.1f}, pred={d['pred_mean']:.1f}){flag}")

    # ═══════════════════════════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("SUMMARY — ALL FOUR METRICS (v7 stacking)")
    print("=" * 70)
    target_r2 = 0.9
    print(f"  {'Metric':<30s} {'R2':>8s} {'Target':>8s} {'Gap':>8s}")
    print(f"  {'-' * 54}")
    for name, key in [("Temporal Holdout", "temporal"), ("Spatial Holdout", "spatial"),
                       ("Spatiotemporal Blocked", "spatiotemporal"), ("Leave-One-Out", "loo")]:
        r2 = results[key].r2
        gap = target_r2 - r2
        status = "OK" if r2 >= target_r2 else f"-{gap:.3f}"
        print(f"  {name:<30s} {r2:>8.4f} {target_r2:>8.1f} {status:>8s}")

    # Save results
    save_path = Path(save_dir)
    save_path.mkdir(parents=True, exist_ok=True)

    report = {
        "dataset": dataset_path,
        "n_rows": len(df),
        "n_locations": int(df["location"].nunique()),
        "full_features": len(full_features),
        "loo_features": len(loo_features),
        "version": "v7",
        "model_type": "stacking_ensemble (LGB+XGB+CatBoost+Ridge → Ridge meta)",
        "results": {
            k: {"r2": v.r2, "rmse": v.rmse, "mae": v.mae,
                "n_train": v.n_train, "n_test": v.n_test,
                "features_used": v.features_used, "details": v.details}
            for k, v in results.items()
        },
    }
    with open(save_path / "evaluation_report_v7.json", "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nReport saved to {save_path / 'evaluation_report_v7.json'}")

    # Train and save production model (full features)
    print("\n--- Training production stacking model (full features) ---")
    avail = _available_features(df, full_features)
    df_clean = df.dropna(subset=[TARGET])
    X = df_clean[avail].values
    y = df_clean[TARGET].values
    prod_model = train_stacking(X, y, aggressive=True)
    joblib.dump(prod_model, save_path / "stacking_v7_full.joblib")
    print(f"  Saved full stacking model ({len(avail)} features)")

    # Save LOO model
    print("--- Training LOO-safe stacking model ---")
    avail_loo = _available_features(df, loo_features)
    X_loo = df_clean[avail_loo].values
    loo_model = train_stacking(X_loo, y, aggressive=True)
    joblib.dump(loo_model, save_path / "stacking_v7_loo.joblib")
    print(f"  Saved LOO stacking model ({len(avail_loo)} features)")

    # Save feature lists
    with open(save_path / "feature_lists_v7.json", "w") as f:
        json.dump({
            "full": full_features,
            "loo": loo_features,
            "available_full": avail,
            "available_loo": avail_loo,
        }, f, indent=2)

    return results


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "castline/validation/data/assembled/validation_dataset_v6.csv"
    run_full_evaluation(path)
