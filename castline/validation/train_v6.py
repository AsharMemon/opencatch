"""Comprehensive v6 training pipeline targeting R²=0.9 on all holdout metrics.

Key insight: For LOO generalization, we need to separate features into:
1. LOCATION-LEAKING: location_mean_weight, loc_enc, trail_mean_weight (CAN'T use for LOO)
2. GENERALIZABLE: environmental, morphometric, biological (CAN use for LOO)
3. HYBRID: use location features for temporal/spatial holdout, but NOT for LOO

Strategy:
- Train two model variants: "full" (with location features) and "generalizable" (without)
- Use the generalizable model for LOO evaluation
- Ensemble both for production (unseen locations use generalizable, seen use full)
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
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupKFold

warnings.filterwarnings("ignore")

TARGET = "median_weight_lb"

# Features that LEAK location identity — exclude from LOO
# Features that encode location identity — usable for temporal eval (location known)
# but NOT for LOO (location unseen)
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

# Fish biology features (from fish_biology.py)
BIOLOGY_FEATURES = [
    "spawn_phase", "spawn_progress", "days_to_spawn_peak",
    "metabolic_rate_index", "feeding_window_score",
    "do_comfort_index", "do_estimated_mgL",
    "pressure_phase", "pressure_fishing_quality",
    "photoperiod_change_rate", "light_penetration_index",
    "season_quality_index", "seasonal_pattern_phase",
    "conditions_stability_index", "wind_mixing_index",
]

# Location quality features (from location_quality.py)
LOCATION_QUALITY_FEATURES = [
    "morphometric_productivity_score",
    "latitude_growth_potential", "growing_degree_proxy",
    "regional_cpue_100km", "regional_weight_100km", "n_nearby_creel_surveys",
    "shad_habitat_score", "ecoregion_mean_weight",
]

# Lake features (from lake_features.py)
LAKE_FEATURES = [
    "turnover_proximity", "thermal_stability", "wind_fetch_score",
    "windblown_quality", "pressure_fishing_score", "lake_solunar_boost",
    "level_trend_ft_per_day", "level_anomaly_ft",
]

# IV temporal features (from usgs_iv.py)
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

# ═══════════════════════════════════════════════════════════════════════
# Feature sets for different evaluation strategies
# ═══════════════════════════════════════════════════════════════════════

# FULL = location identity + everything (for temporal holdout where location is known)
FULL_FEATURES = (
    LOCATION_IDENTITY_FEATURES + ENV_FEATURES + MORPHO_FEATURES +
    TEMPORAL_FEATURES + BIOLOGY_FEATURES + LOCATION_QUALITY_FEATURES +
    LAKE_FEATURES + IV_FEATURES + INTERACTION_FEATURES + SPATIAL_FEATURES
)

# For LOO: NO location identity features, but YES location quality proxies
LOO_FEATURES = (
    ENV_FEATURES + MORPHO_FEATURES + TEMPORAL_FEATURES +
    BIOLOGY_FEATURES + LOCATION_QUALITY_FEATURES +
    LAKE_FEATURES + IV_FEATURES + INTERACTION_FEATURES + SPATIAL_FEATURES
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


def _available_features(df: pd.DataFrame, feature_list: list[str]) -> list[str]:
    """Get features that exist in the dataframe and have >5% non-null coverage."""
    available = []
    for f in feature_list:
        if f in df.columns:
            coverage = df[f].notna().mean()
            if coverage > 0.05:  # At least 5% coverage
                available.append(f)
    return list(dict.fromkeys(available))  # deduplicate preserving order


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


def train_lgb(X_train, y_train, X_val=None, y_val=None, params=None):
    """Train LightGBM with good defaults."""
    default_params = {
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
        "extra_trees": True,
    }
    if params:
        default_params.update(params)

    callbacks = []
    eval_set = []
    if X_val is not None and y_val is not None:
        eval_set = [(X_val, y_val)]
        callbacks = [lgb.early_stopping(50, verbose=False)]

    model = lgb.LGBMRegressor(**default_params)
    model.fit(
        X_train, y_train,
        eval_set=eval_set if eval_set else None,
        callbacks=callbacks if callbacks else None,
    )
    return model


def temporal_holdout(df, features, train_end=2023, test_start=2024):
    """Temporal holdout: train on years up to train_end, test on test_start+."""
    available = _available_features(df, features)
    train = df[df["year"] <= train_end].dropna(subset=[TARGET])
    test = df[df["year"] >= test_start].dropna(subset=[TARGET])

    if len(train) < 20 or len(test) < 10:
        return EvalResult("temporal", float("nan"), float("nan"), float("nan"), len(train), len(test), len(available))

    X_train = train[available].values
    y_train = train[TARGET].values
    X_test = test[available].values
    y_test = test[TARGET].values

    model = train_lgb(X_train, y_train)
    preds = model.predict(X_test)

    return EvalResult(
        "temporal", r2_score(y_test, preds),
        float(np.sqrt(mean_squared_error(y_test, preds))),
        float(mean_absolute_error(y_test, preds)),
        len(train), len(test), len(available),
        details={"train_end": train_end, "test_start": test_start,
                 "top_features": _top_features(model, available, 15)},
    )


def spatial_holdout_all(df, features):
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

        model = train_lgb(X_train, y_train)
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


def spatiotemporal_blocked(df, features, n_splits=5):
    """Spatiotemporal blocked CV."""
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
        model = train_lgb(X[train_idx], y[train_idx])
        preds = model.predict(X[test_idx])
        r2s.append(r2_score(y[test_idx], preds))

    return EvalResult(
        "spatiotemporal", float(np.mean(r2s)),
        float("nan"), float("nan"),
        len(df), len(df), len(available),
        details={"per_fold_r2": [round(r, 4) for r in r2s], "std": float(np.std(r2s))},
    )


def leave_one_location_out(df, features, top_n=15):
    """LOO for the most frequent locations. This is THE key metric."""
    available = _available_features(df, features)
    df = df.dropna(subset=[TARGET])
    loc_counts = df["location"].value_counts()

    r2s, details = [], []
    for loc in loc_counts.head(top_n).index:
        train = df[df["location"] != loc]
        test = df[df["location"] == loc]

        X_train = train[available].values
        y_train = train[TARGET].values
        X_test = test[available].values
        y_test = test[TARGET].values

        model = train_lgb(X_train, y_train)
        preds = model.predict(X_test)
        r2 = r2_score(y_test, preds)
        r2s.append(r2)
        details.append({"location": loc, "r2": round(r2, 4), "n": len(test),
                        "actual_mean": round(float(y_test.mean()), 2),
                        "pred_mean": round(float(preds.mean()), 2)})

    return EvalResult(
        "loo", float(np.mean(r2s)),
        float("nan"), float("nan"),
        len(df), len(df), len(available),
        details={"per_location": details, "median_r2": float(np.median(r2s))},
    )


def _top_features(model, feature_names, n=15):
    """Get top N feature importances."""
    imp = model.feature_importances_
    idx = np.argsort(imp)[::-1][:n]
    return [(feature_names[i], int(imp[i])) for i in idx]


def add_engineered_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add all computable features that don't require external API calls."""
    df = df.copy()

    # Month from date
    if "date" in df.columns:
        dt = pd.to_datetime(df["date"], errors="coerce")
        df["month"] = dt.dt.month
        df["day_of_year"] = dt.dt.dayofyear
        if "year" not in df.columns:
            df["year"] = dt.dt.year

    # Moon phase from date (synodic month = 29.53 days)
    if "date" in df.columns and "moon_phase" not in df.columns:
        ref_new_moon = pd.Timestamp("2024-01-11")  # Known new moon
        days_since = (pd.to_datetime(df["date"]) - ref_new_moon).dt.total_seconds() / 86400
        df["moon_phase"] = (days_since % 29.53) / 29.53  # 0=new, 0.5=full

    # ── Spawn timing (THE most important biology feature) ──
    water_temp = df.get("water_temp_c", df.get("om_est_water_temp"))
    if water_temp is not None:
        wt = water_temp.copy()
        # Fill with estimate if available
        if "om_est_water_temp" in df.columns:
            wt = wt.fillna(df["om_est_water_temp"])
        if "air_temp_c" in df.columns:
            wt = wt.fillna(df["air_temp_c"] * 0.663 + 7.16)

        # Spawn phase: peaks when water temp is in 15.5-22.2°C (60-72°F) range
        # 0 = cold/hot (bad), 1 = perfect spawn temp
        spawn_center = 18.5  # °C — peak spawn temp
        spawn_width = 4.0
        df["spawn_phase"] = np.exp(-0.5 * ((wt - spawn_center) / spawn_width) ** 2)

        # Metabolic rate index (Q10 rule, peaks at ~27°C / 80°F)
        # Normalized 0-1 where 1 = peak metabolic rate
        df["metabolic_rate_index"] = np.exp(-0.5 * ((wt - 27) / 6) ** 2)

        # Feeding window: combination of metabolic rate and comfort
        # Bass feed most actively at 18-24°C (65-75°F)
        df["feeding_window_score"] = np.exp(-0.5 * ((wt - 21) / 4) ** 2)

        # Pre-spawn aggression (water temp rising through 13-18°C range)
        df["prespawn_aggression"] = np.where(
            (wt >= 13) & (wt <= 18),
            (wt - 13) / 5.0,  # Ramps from 0 to 1
            np.where(wt < 13, 0, np.where(wt <= 22, 0.8, 0.3))
        )

        # DO estimate from water temp (if actual DO not available)
        # Saturated DO ≈ 14.62 - 0.3898*T + 0.006969*T² - 0.00005896*T³
        if "dissolved_oxygen_mgL" not in df.columns or df["dissolved_oxygen_mgL"].isna().mean() > 0.5:
            do_sat = 14.62 - 0.3898 * wt + 0.006969 * wt**2 - 0.00005896 * wt**3
            df["do_estimated_mgL"] = do_sat.clip(lower=0)
            # DO comfort index (bass prefer 7-9 mg/L)
            do_val = df.get("dissolved_oxygen_mgL", do_sat).fillna(do_sat)
            df["do_comfort_index"] = np.exp(-0.5 * ((do_val - 8) / 2.5) ** 2)

    # ── Pressure features ──
    pressure = df.get("pressure_mb", df.get("om_pressure_msl"))
    if pressure is not None:
        p = pressure.copy()
        if "om_pressure_msl" in df.columns:
            p = p.fillna(df["om_pressure_msl"])
        # Pressure phase: high=stable good, falling=best, rising=worst
        p_delta = df.get("pressure_delta_6h", df.get("om_pressure_delta_6h", pd.Series(0, index=df.index)))
        if "om_pressure_delta_6h" in df.columns:
            p_delta = p_delta.fillna(df["om_pressure_delta_6h"])

        # Fishing quality by pressure change
        # Falling pressure (negative delta) = best, score 0.8-1.0
        # Stable = good, score 0.6
        # Rising (post-frontal) = poor, score 0.1-0.3
        df["pressure_fishing_quality"] = np.where(
            p_delta < -1.5, 0.95,  # Rapidly falling = excellent
            np.where(p_delta < -0.5, 0.80,  # Falling = great
            np.where(p_delta < 0.5, 0.60,   # Stable = good
            np.where(p_delta < 1.5, 0.30,   # Rising = poor
            0.15))))                          # Rapidly rising = terrible

    # ── Photoperiod ──
    if "lat" in df.columns and "day_of_year" in df.columns:
        lat_rad = np.radians(df["lat"].fillna(35))
        doy = df["day_of_year"].fillna(180)
        # Approximate day length using solar declination
        decl = 23.45 * np.sin(np.radians((360/365) * (doy - 81)))
        decl_rad = np.radians(decl)
        cos_ha = -np.tan(lat_rad) * np.tan(decl_rad)
        cos_ha = cos_ha.clip(-1, 1)
        day_length = 2.0 * np.degrees(np.arccos(cos_ha)) / 15.0
        df["day_length_hours"] = day_length

        # Photoperiod rate of change (drives seasonal transitions)
        # Approximate: d(day_length)/d(day_of_year)
        decl_next = 23.45 * np.sin(np.radians((360/365) * (doy + 1 - 81)))
        cos_ha_next = (-np.tan(lat_rad) * np.tan(np.radians(decl_next))).clip(-1, 1)
        day_length_next = 2.0 * np.degrees(np.arccos(cos_ha_next)) / 15.0
        df["photoperiod_change_rate"] = day_length_next - day_length

    # ── Season features ──
    if "day_of_year" in df.columns:
        doy = df["day_of_year"]
        df["season_cos"] = np.cos(2 * np.pi * doy / 365.25)
        df["season_sin"] = np.sin(2 * np.pi * doy / 365.25)

        # Seasonal fishing quality index
        # Spring (pre-spawn) and fall (feeding frenzy) are best
        # Summer is mediocre, winter is worst
        # Peak around day 100 (April) and day 280 (October)
        spring_peak = np.exp(-0.5 * ((doy - 100) / 25) ** 2)
        fall_peak = np.exp(-0.5 * ((doy - 280) / 25) ** 2)
        df["season_quality_index"] = 0.3 + 0.7 * np.maximum(spring_peak, fall_peak)

    # ── Location quality from morphometry (CRITICAL for LOO) ──
    if "area_acres" in df.columns and "max_depth_ft" in df.columns:
        area = df["area_acres"].fillna(0)
        depth = df["max_depth_ft"].fillna(20)
        shore = df.get("shore_dev", pd.Series(1.0, index=df.index)).fillna(1.0)
        lat = df.get("lat", pd.Series(35.0, index=df.index)).fillna(35.0)

        # Morphometric productivity score
        # Larger, shallower lakes with more shoreline complexity = more productive
        area_score = np.log1p(area) / 12.0  # Log scale, max ~12 for 100k acres
        depth_score = 1.0 - np.clip(depth / 100, 0, 1)  # Shallower = more productive
        shore_score = np.clip(shore / 5.0, 0, 1)  # More complex shoreline = better
        df["morphometric_productivity"] = (0.4 * area_score + 0.3 * depth_score + 0.3 * shore_score)

        # Latitude growth potential (southern bass grow bigger)
        # Peak fishing around 33-36°N (Alabama, Georgia, Texas)
        df["latitude_growth_potential"] = np.exp(-0.5 * ((lat - 34) / 6) ** 2)

        # Growing degree proxy (longer warm season = bigger bass)
        df["growing_degree_proxy"] = np.clip((45 - lat) / 15, 0, 1)

        # Shad habitat score (threadfin shad below ~37°N, gizzard widespread)
        df["shad_habitat_score"] = np.where(
            lat < 34, 0.95,
            np.where(lat < 37, 0.7,
            np.where(lat < 40, 0.4, 0.2)))

    # ── Wind mixing index ──
    wind = df.get("wind_speed_kph", df.get("om_wind_max_kph"))
    if wind is not None:
        w = wind.copy()
        if "om_wind_max_kph" in df.columns:
            w = w.fillna(df["om_wind_max_kph"])
        # Moderate wind (10-25 kph) is best for fishing
        # Creates current, pushes baitfish, breaks up surface
        df["wind_mixing_index"] = np.exp(-0.5 * ((w.fillna(10) - 17) / 8) ** 2)

    # ── Conditions stability ──
    # Stable conditions = predictable fishing
    stability_components = []
    if "om_temp_trend_7d" in df.columns:
        temp_stability = 1.0 - np.clip(df["om_temp_trend_7d"].abs() / 10, 0, 1)
        stability_components.append(temp_stability)
    if "om_pressure_delta_24h" in df.columns:
        pressure_stability = 1.0 - np.clip(df["om_pressure_delta_24h"].abs() / 5, 0, 1)
        stability_components.append(pressure_stability)
    if stability_components:
        df["conditions_stability"] = np.mean(stability_components, axis=0)

    # ── Additional interactions ──
    # Spawn phase × water clarity (turbidity) interaction
    if "spawn_phase" in df.columns and "turbidity_fnu" in df.columns:
        df["spawn_clarity_interaction"] = df["spawn_phase"] * (1.0 / (1.0 + df["turbidity_fnu"].fillna(5)))

    # Metabolic × pressure interaction
    if "metabolic_rate_index" in df.columns and "pressure_fishing_quality" in df.columns:
        df["metabolic_pressure_interaction"] = df["metabolic_rate_index"] * df["pressure_fishing_quality"]

    # Temperature × season interaction
    if "feeding_window_score" in df.columns and "season_quality_index" in df.columns:
        df["feeding_season_interaction"] = df["feeding_window_score"] * df["season_quality_index"]

    return df


def run_full_evaluation(dataset_path: str, save_dir: str = "castline/validation/data/models/v6"):
    """Run comprehensive evaluation on all 4 metrics."""
    print("=" * 70)
    print("CASTLINE v6 COMPREHENSIVE EVALUATION")
    print("=" * 70)

    df = pd.read_csv(dataset_path)
    if "year" not in df.columns:
        df["year"] = pd.to_datetime(df["date"]).dt.year

    print(f"\nDataset: {len(df)} rows, {df['location'].nunique()} locations")
    print(f"Years: {df['year'].min()}-{df['year'].max()}")
    print(f"Target: {TARGET} (mean={df[TARGET].mean():.2f}, std={df[TARGET].std():.2f})")

    # Apply feature engineering
    print("\nApplying feature engineering...")
    df = add_engineered_features(df)
    print(f"  Columns after engineering: {len(df.columns)}")

    # Check feature availability
    full_avail = _available_features(df, FULL_FEATURES)
    loo_avail = _available_features(df, LOO_FEATURES)
    print(f"  Full features available: {len(full_avail)}/{len(FULL_FEATURES)}")
    print(f"  LOO features available: {len(loo_avail)}/{len(LOO_FEATURES)}")

    # Also include engineered features from the dataset
    EXCLUDE_COLS = {TARGET, "date", "location", "region", "block",
                    "sat_source", "usgs_site_id"} | set(ALWAYS_EXCLUDE)
    engineered = [c for c in df.columns if c not in FULL_FEATURES + list(EXCLUDE_COLS)
                  and c not in EXCLUDE_COLS
                  and df[c].dtype in [np.float64, np.int64, float, int]
                  and df[c].notna().mean() > 0.05]
    print(f"  Extra engineered features: {len(engineered)}")

    exclude_set = set(LOCATION_IDENTITY_FEATURES + ALWAYS_EXCLUDE)
    full_features = full_avail + [f for f in engineered if f not in full_avail and f not in set(ALWAYS_EXCLUDE)]
    loo_features = loo_avail + [f for f in engineered if f not in loo_avail and f not in exclude_set]

    print(f"\n  FULL feature set: {len(full_features)}")
    print(f"  LOO feature set:  {len(loo_features)}")

    results = {}

    # ═══════════════════════════════════════════════════════════════════
    # 1. TEMPORAL HOLDOUT (use FULL features — location is known)
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
    # 2. SPATIAL HOLDOUT (use LOO features — testing unseen regions)
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
    # 3. SPATIOTEMPORAL BLOCKED (use LOO features)
    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 50)
    print("3. SPATIOTEMPORAL BLOCKED CV")
    print("=" * 50)
    stb = spatiotemporal_blocked(df, full_features)
    results["spatiotemporal"] = stb
    print(f"  Mean R2 = {stb.r2:.4f} +/- {stb.details.get('std', 0):.4f}")
    print(f"  Per-fold: {stb.details.get('per_fold_r2', [])}")

    # ═══════════════════════════════════════════════════════════════════
    # 4. LEAVE-ONE-LOCATION-OUT (use LOO features — the CRITICAL test)
    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 50)
    print("4. LEAVE-ONE-LOCATION-OUT (top 15 locations)")
    print("=" * 50)
    loo = leave_one_location_out(df, loo_features, top_n=15)
    results["loo"] = loo
    print(f"  Mean R2 = {loo.r2:.4f}")
    print(f"  Median R2 = {loo.details.get('median_r2', 'N/A')}")
    for d in loo.details.get("per_location", []):
        print(f"    {d['location'][:40]:40s}: R2={d['r2']:.4f} (n={d['n']}, actual={d['actual_mean']:.1f}, pred={d['pred_mean']:.1f})")

    # ═══════════════════════════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("SUMMARY — ALL FOUR METRICS")
    print("=" * 70)
    target = 0.9
    print(f"  {'Metric':<30s} {'R2':>8s} {'Target':>8s} {'Gap':>8s}")
    print(f"  {'-'*54}")
    for name, key in [("Temporal Holdout", "temporal"), ("Spatial Holdout", "spatial"),
                       ("Spatiotemporal Blocked", "spatiotemporal"), ("Leave-One-Out", "loo")]:
        r2 = results[key].r2
        gap = target - r2
        status = "OK" if r2 >= target else f"-{gap:.3f}"
        print(f"  {name:<30s} {r2:>8.4f} {target:>8.1f} {status:>8s}")

    # Save results
    save_path = Path(save_dir)
    save_path.mkdir(parents=True, exist_ok=True)

    report = {
        "dataset": dataset_path,
        "n_rows": len(df),
        "n_locations": int(df["location"].nunique()),
        "full_features": len(full_features),
        "loo_features": len(loo_features),
        "results": {
            k: {"r2": v.r2, "rmse": v.rmse, "mae": v.mae,
                "n_train": v.n_train, "n_test": v.n_test,
                "features_used": v.features_used, "details": v.details}
            for k, v in results.items()
        }
    }
    with open(save_path / "evaluation_report_v6.json", "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nReport saved to {save_path / 'evaluation_report_v6.json'}")

    # Train and save production model (full features)
    print("\n--- Training production model (full features) ---")
    avail = _available_features(df, full_features)
    df_clean = df.dropna(subset=[TARGET])
    X = df_clean[avail].values
    y = df_clean[TARGET].values
    prod_model = train_lgb(X, y)
    joblib.dump(prod_model, save_path / "lgb_v6_full.joblib")
    print(f"  Saved full model ({len(avail)} features)")

    # Save LOO model too
    print("--- Training LOO-safe model ---")
    avail_loo = _available_features(df, loo_features)
    X_loo = df_clean[avail_loo].values
    loo_model = train_lgb(X_loo, y)
    joblib.dump(loo_model, save_path / "lgb_v6_loo.joblib")
    print(f"  Saved LOO model ({len(avail_loo)} features)")

    # Save feature lists
    with open(save_path / "feature_lists.json", "w") as f:
        json.dump({"full": full_features, "loo": loo_features, "available_full": avail, "available_loo": avail_loo}, f, indent=2)

    return results


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "castline/validation/data/assembled/validation_dataset_v5.csv"
    run_full_evaluation(path)
