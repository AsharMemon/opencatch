#!/usr/bin/env python3
"""
CPUE Model v14 — Two-Model Architecture + Enhanced Temporal Features
=====================================================================
Key changes over V13 (CV R²=0.705, Temporal=0.329, Walk-forward=0.429):

  1. TWO-MODEL ARCHITECTURE:
     - Seen-location model: uses spatial + temporal + historical features
     - Unseen-location model: uses only generalizable features (weather dynamics,
       season, biology) — no location-specific aggregates
     - At inference: route based on whether location has prior data
     - Why: V13 temporal holdout had 550/967 rows at unseen locations (R²=0.117)
       while seen locations achieved R²=0.541

  2. ENHANCED TEMPORAL / RATE-OF-CHANGE FEATURES:
     - 1-day and 30-day lag windows (V13 only had 3/7/14-day)
     - Rate-of-change: temp rising/falling rate, pressure change rate
     - Cold front detection: sharp pressure drops → bite suppression
     - Warming/cooling trends: spawn triggers
     - Interaction terms: pressure_change × temp_change, wind × pressure
     - Stability indices: how stable are conditions over last 3/7 days?

  3. USACE RESERVOIR DATA (if available):
     - Pool elevation changes are the #1 signal for reservoir bass fishing
     - Merged from scripts/fetch_usace_reservoir.py output

  4. IMPROVED STACKING:
     - Separate Ridge meta-learners for seen vs unseen models
     - Calibrated confidence based on location history depth

Target: Temporal R² > 0.45 (from 0.329), Unseen locations R² > 0.25 (from 0.117)

Usage:
    python scripts/build_cpue_model_v14.py [--workspace /workspace/castline]
"""

import argparse
import json
import logging
import os
import pickle
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.model_selection import GroupKFold
from sklearn.metrics import r2_score, mean_absolute_error, mean_squared_error
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# PATHS
# ---------------------------------------------------------------------------
WORKSPACE = os.environ.get("WORKSPACE", str(Path(__file__).resolve().parent.parent))
BASE_DIR = Path(WORKSPACE)

DATASET_CANDIDATES = [
    BASE_DIR / "castline" / "validation" / "data" / "assembled" / "validation_dataset_v16.csv",
    BASE_DIR / "data" / "assembled" / "validation_dataset_v16.csv",
    BASE_DIR / "validation_dataset_v16.csv",
]

USACE_DATA_CANDIDATES = [
    BASE_DIR / "castline" / "validation" / "data" / "raw" / "usace_reservoir_data.csv",
    BASE_DIR / "data" / "raw" / "usace_reservoir_data.csv",
]

SEED = 42
VERSION = "v14"

TARGET = "median_weight_lb"

# --- Columns to always exclude ---
ALWAYS_EXCLUDE = {
    TARGET, "target_success_score", "log_cpue", "cpue",
    "event_id", "tms_id", "tournament_slug", "event_name",
    "date", "location", "region", "block", "sat_source",
    "usgs_site_id", "results_source", "trail", "species",
    "source", "spawn_phase", "_source",
    "loc_mean_enc", "source_enc", "loc_enc",
    "trail_mean_weight", "location_mean_weight",
    "baseline_signal",
    "num_anglers", "num_anglers_capped",
    "loc_target_cv",
    # Internal columns we add
    "_region", "is_premium", "log_cpue",
}

# --- Additional exclusions for the UNSEEN model ---
# These features are location-specific aggregates that won't generalize
UNSEEN_EXTRA_EXCLUDE = {
    "loc_rolling_mean", "loc_rolling_std", "loc_n_prior",
    "loc_encoding_confidence", "loc_total_events",
    "loc_source_diversity", "loc_tournament_fraction",
    "loc_year_range", "loc_first_year", "loc_last_year",
    "loc_month_diversity", "loc_rolling_3",
    "source_rolling_mean",
    # Creel data is location-specific
    "creel_cpue_mean", "creel_n_surveys", "creel_lmb_cpue",
    "creel_smb_cpue", "creel_spotted_cpue",
    "creel_lmb_ratio", "creel_smb_ratio", "creel_spotted_ratio",
    "creel_nearest_dist_km",
    # USGS fish community is location-specific
    "usgs_largemouth_bass_presence", "usgs_smallmouth_bass_presence",
    "usgs_spotted_bass_presence", "usgs_redeye_bass_presence",
    "usgs_ozark_bass_presence", "usgs_shoal_bass_presence",
    "usgs_bass_richness", "usgs_predator_richness",
    "usgs_predator_competition", "usgs_habitat_richness",
    "usgs_forage_index", "usgs_total_species_richness",
    "usgs_community_diversity", "usgs_reaches_nearby",
    # GeoCLIP embeddings are location-specific
    *[f"geoclip_{i}" for i in range(32)],
    # LAGOS morphometry is location-specific
    "lagos_sdi", "lagos_connectivity_fluct", "lagos_upstream_lakes_n",
    "lagos_upstream_lakes_ha", "lagos_upstream_10ha_n",
    "lagos_glaciated", "lagos_elevation_m", "lagos_mbg_arearatio",
    "lagos_mean_width_m", "lagos_mbg_length_m", "lagos_elongation",
    "lagos_waterarea_ha", "lagos_island_pct",
    "lagos_connectivity_enc", "lagos_epanutr_zone_enc",
    "lagos_omernik3_zone_enc", "lagos_bailey_zone_enc",
    "lagos_wwf_zone_enc", "lagos_neon_zone_enc",
    "lagos_hu4_enc", "lagos_hu8_enc", "lagos_state_enc",
    "lagos_match_dist_km", "lagos_sdi_x_area", "lagos_sdi_x_depth",
    # CreelCat spatial is location-specific
    *[c for c in [] if c.startswith("creel2_")],
    # Morphometry
    "area_acres", "max_depth_ft", "shore_dev",
    "wtype_river", "wtype_reservoir", "wtype_natural_lake",
    "is_lake", "reservoir_score",
}

# Two-tier weighting
PREMIUM_SOURCES = {
    "elite_outcomes", "all_bassmaster_outcomes", "flw_outcomes",
    "combined_all_outcomes_v2", "mlf_outcomes",
    "elite_api", "elite", "bassmaster_classic",
}

MIN_LOC_HISTORY = 3  # minimum events at a location to use "seen" model

# Morphometric features for KNN spatial encoding
LOC_MORPH = ["lat", "lon", "area_acres", "is_lake", "max_depth_ft", "shore_dev",
             "creel_lmb_ratio", "creel_smb_ratio", "creel_cpue_mean"]


# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------
_log_file = None


def setup_logging(base_dir):
    global _log_file
    log_dir = base_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    _log_file = open(log_dir / f"{VERSION}_pipeline.log", "w")


def log(msg=""):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    if _log_file:
        _log_file.write(line + "\n")
        _log_file.flush()


# ---------------------------------------------------------------------------
# ENHANCED TEMPORAL FEATURES
# ---------------------------------------------------------------------------

def add_enhanced_temporal_features(df):
    """
    Add rate-of-change, cold front detection, stability indices, and
    interaction features that generalize across locations.
    """
    log("\n--- Adding Enhanced Temporal Features ---")
    n_before = df.shape[1]

    # ---- Rate-of-change features ----
    # These capture DYNAMICS, not absolutes — should generalize to unseen locations

    # Water temperature rate of change (if we have lag data)
    if "lag_temp_3d_mean" in df.columns and "om_air_temp_mean" in df.columns:
        # Approximate 1-day temp change from 3-day mean vs current
        df["temp_change_rate_1d"] = df["om_air_temp_mean"] - df["lag_temp_3d_mean"]
        df["temp_change_rate_1d"].fillna(0, inplace=True)

    if "lag_temp_trend" in df.columns:
        # Strengthen the warming/cooling signal
        df["is_warming_fast"] = (df["lag_temp_trend"] > 1.0).astype(float)
        df["is_cooling_fast"] = (df["lag_temp_trend"] < -1.0).astype(float)

    # ---- Cold Front Detection ----
    # A cold front is characterized by: rapid pressure drop, temp drop, wind shift
    if "lag_pressure_trend" in df.columns:
        df["cold_front_strength"] = np.clip(-df["lag_pressure_trend"], 0, None)
        df["cold_front_active"] = (df["lag_pressure_trend"] < -2.0).astype(float)

    if "lag_pressure_range" in df.columns:
        df["pressure_volatility"] = df["lag_pressure_range"]

    if "pressure_change_rate" in df.columns:
        # Rapid pressure changes indicate frontal passages
        df["frontal_passage_strength"] = np.abs(df["pressure_change_rate"])
        df["is_prefrontal"] = (df["pressure_change_rate"] < -0.5).astype(float)
        df["is_postfrontal"] = (df["pressure_change_rate"] > 0.5).astype(float)

    # ---- Stability Indices ----
    # How stable have conditions been? Stable conditions = predictable fishing
    stability_cols = []
    if "lag_temp_range" in df.columns:
        df["temp_stability_3d"] = 1.0 / (1.0 + df["lag_temp_range"].fillna(5))
        stability_cols.append("temp_stability_3d")

    if "lag_pressure_std" in df.columns:
        df["pressure_stability_3d"] = 1.0 / (1.0 + df["lag_pressure_std"].fillna(3))
        stability_cols.append("pressure_stability_3d")

    if stability_cols:
        df["conditions_stability_3d"] = df[stability_cols].mean(axis=1)

    # ---- Weather Pattern Interactions ----
    # These capture combined effects that matter for fishing

    if "lag_pressure_trend" in df.columns and "lag_temp_trend" in df.columns:
        # Rising pressure + warming = great fishing (high pressure, warming trend)
        df["pressure_x_temp_trend"] = df["lag_pressure_trend"] * df["lag_temp_trend"]
        # Falling pressure + cooling = terrible fishing (cold front)
        df["cold_front_combo"] = np.clip(
            -df["lag_pressure_trend"] * -df["lag_temp_trend"], 0, None
        )

    if "om_wind_max_kph" in df.columns and "lag_pressure_trend" in df.columns:
        # High wind + falling pressure = strong front
        df["wind_x_pressure_drop"] = (
            df["om_wind_max_kph"].fillna(10) *
            np.clip(-df["lag_pressure_trend"].fillna(0), 0, None)
        )

    if "om_precip_mm" in df.columns and "lag_temp_trend" in df.columns:
        # Rain + warming = good spring fishing (warm rain)
        df["warm_rain_index"] = (
            np.log1p(df["om_precip_mm"].fillna(0)) *
            np.clip(df["lag_temp_trend"].fillna(0), 0, None)
        )

    # ---- Seasonal Transition Strength ----
    # How fast is the season transitioning? Rapid transitions trigger fish activity
    if "photoperiod_change_min" in df.columns:
        df["photoperiod_change_abs"] = np.abs(df["photoperiod_change_min"].fillna(0))

    if "gdd_daily_rate" in df.columns:
        df["gdd_acceleration"] = df["gdd_daily_rate"].fillna(0)
        # Spring GDD acceleration = spawn approaching
        if "month" in df.columns:
            spring_mask = df["month"].between(3, 6)
            df["spring_gdd_signal"] = df["gdd_acceleration"] * spring_mask.astype(float)

    # ---- Multi-day Precipitation Pattern ----
    if "lag_precip_3d_sum" in df.columns and "lag_precip_7d_sum" in df.columns:
        # Recent rain relative to weekly average
        df["precip_recency"] = (
            df["lag_precip_3d_sum"].fillna(0) /
            (df["lag_precip_7d_sum"].fillna(0.1) + 0.1)
        )
        # Heavy recent rain = muddy water, reduced visibility
        df["heavy_recent_rain"] = (df["lag_precip_3d_sum"].fillna(0) > 25).astype(float)

    # ---- Cloud Cover Pattern ----
    if "lag_cloud_3d_mean" in df.columns and "lag_cloud_trend" in df.columns:
        # Increasing cloud cover = pre-frontal (often good fishing)
        df["cloud_increasing"] = (df["lag_cloud_trend"].fillna(0) > 5).astype(float)
        # Overcast + stable = great topwater fishing
        df["overcast_stable"] = (
            (df["lag_cloud_3d_mean"].fillna(50) > 70).astype(float) *
            (np.abs(df["lag_pressure_trend"].fillna(0)) < 1).astype(float)
            if "lag_pressure_trend" in df.columns
            else (df["lag_cloud_3d_mean"].fillna(50) > 70).astype(float)
        )

    # ---- Humidity-Comfort Index ----
    if "lag_humidity_3d_mean" in df.columns:
        # High humidity often correlates with pre-frontal conditions
        df["high_humidity_flag"] = (df["lag_humidity_3d_mean"].fillna(60) > 80).astype(float)

    # ---- Diurnal Temperature Range ----
    if "lag_diurnal_mean" in df.columns:
        # Large diurnal range = clear skies, stable high pressure
        df["large_diurnal_range"] = (df["lag_diurnal_mean"].fillna(10) > 15).astype(float)

    n_added = df.shape[1] - n_before
    log(f"  Added {n_added} enhanced temporal features")
    return df


# ---------------------------------------------------------------------------
# USACE RESERVOIR DATA MERGE
# ---------------------------------------------------------------------------

def merge_usace_data(df):
    """Merge USACE reservoir data if available."""
    usace_path = None
    for p in USACE_DATA_CANDIDATES:
        if p.exists():
            usace_path = p
            break

    if usace_path is None:
        log("  USACE reservoir data not found — skipping")
        return df

    log(f"\n--- Merging USACE Reservoir Data ---")
    usace = pd.read_csv(usace_path, low_memory=False)
    log(f"  Loaded {len(usace)} USACE records")

    # Merge on event_id or (location, date)
    if "event_id" in usace.columns and "event_id" in df.columns:
        df = df.merge(usace, on="event_id", how="left", suffixes=("", "_usace"))
    elif "location" in usace.columns and "date" in usace.columns:
        usace["date"] = pd.to_datetime(usace["date"])
        df = df.merge(usace, on=["location", "date"], how="left", suffixes=("", "_usace"))

    usace_cols = [c for c in df.columns if c.endswith("_usace") or c.startswith("pool_") or c.startswith("inflow_") or c.startswith("outflow_")]
    n_matched = df[usace_cols[0]].notna().sum() if usace_cols else 0
    log(f"  Matched {n_matched}/{len(df)} events ({100*n_matched/len(df):.1f}%)")
    return df


# ---------------------------------------------------------------------------
# DATA LOADING
# ---------------------------------------------------------------------------

def load_dataset():
    """Load pre-assembled V16 dataset with enhanced features."""
    log("=" * 70)
    log(f"CPUE MODEL {VERSION} — TWO-MODEL + ENHANCED TEMPORAL")
    log("=" * 70)

    dataset_path = None
    for p in DATASET_CANDIDATES:
        if p.exists():
            dataset_path = p
            break

    if dataset_path is None:
        log("ERROR: V16 dataset not found!")
        for p in DATASET_CANDIDATES:
            log(f"  {p}")
        sys.exit(1)

    log(f"\nLoading: {dataset_path}")
    df = pd.read_csv(dataset_path, low_memory=False)
    log(f"  Raw: {len(df)} rows x {df.shape[1]} columns")

    # Filter to valid target
    df = df[df[TARGET].notna() & (df[TARGET] > 0)].copy()
    log(f"  Valid target: {len(df)} rows")

    # Parse dates
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df[df["date"].notna()].reset_index(drop=True)
    df["year"] = df["date"].dt.year
    if "month" not in df.columns:
        df["month"] = df["date"].dt.month

    # Log-transform target
    df["log_cpue"] = np.log1p(df[TARGET])

    # Premium source identification
    if "results_source" in df.columns:
        df["is_premium"] = df["results_source"].isin(PREMIUM_SOURCES).astype(float)
    elif "source" in df.columns:
        df["is_premium"] = df["source"].isin(PREMIUM_SOURCES).astype(float)
    else:
        df["is_premium"] = 1.0

    # Add enhanced temporal features
    df = add_enhanced_temporal_features(df)

    # Merge USACE data if available
    df = merge_usace_data(df)

    log(f"\n  Final dataset: {len(df)} rows x {df.shape[1]} columns")
    log(f"  Date range: {df['date'].min().date()} to {df['date'].max().date()}")
    log(f"  Locations: {df['location'].nunique()}")
    log(f"  Premium: {(df['is_premium'] == 1).sum()}, Auxiliary: {(df['is_premium'] == 0).sum()}")
    log(f"  Target range: {df[TARGET].min():.2f} - {df[TARGET].max():.2f} lb")
    return df


# ---------------------------------------------------------------------------
# FEATURE SELECTION
# ---------------------------------------------------------------------------

def select_features(df, exclude_extra=None):
    """Select numeric features, excluding identity/leaky columns."""
    exclude = ALWAYS_EXCLUDE.copy()
    if exclude_extra:
        exclude |= exclude_extra

    features = []
    for c in df.columns:
        if c in exclude:
            continue
        if c.startswith("_"):
            continue
        if df[c].dtype not in ("float64", "int64", "float32", "int32"):
            continue
        if df[c].notna().sum() < len(df) * 0.03:
            continue
        features.append(c)

    return sorted(features)


# ---------------------------------------------------------------------------
# KNN SPATIAL ENCODING
# ---------------------------------------------------------------------------

def fill_rolling_mean_gaps(train_df, test_df=None, K=8):
    """Fill loc_rolling_mean gaps using KNN on morphometric features."""
    morph = [c for c in LOC_MORPH if c in train_df.columns]
    if not morph or "loc_rolling_mean" not in train_df.columns:
        return train_df, test_df

    loc_stats = train_df.groupby("location").agg(
        mean_target=(TARGET, "mean"), n_events=(TARGET, "count"),
        **{f: (f, "first") for f in morph}).reset_index()
    X = loc_stats[morph].fillna(loc_stats[morph].median()).values
    sc = StandardScaler().fit(X)
    Xs = sc.transform(X)
    nn = NearestNeighbors(n_neighbors=min(K + 1, len(loc_stats)))
    nn.fit(Xs)

    knn_prior = {}
    for i, row in loc_stats.iterrows():
        q = Xs[i:i + 1]
        d, ix = nn.kneighbors(q)
        nbrs = [(dd, j) for dd, j in zip(d[0], ix[0])
                if loc_stats.iloc[j]["location"] != row["location"]][:K]
        if nbrs:
            w = [loc_stats.iloc[j]["n_events"] / (dd + 0.01) for dd, j in nbrs]
            knn_prior[row["location"]] = sum(
                wi * loc_stats.iloc[j]["mean_target"]
                for wi, (_, j) in zip(w, nbrs)) / sum(w)
        else:
            knn_prior[row["location"]] = loc_stats["mean_target"].mean()

    train_out = train_df.copy()
    mask = train_out["loc_rolling_mean"].isna()
    train_out.loc[mask, "loc_rolling_mean"] = train_out.loc[mask, "location"].map(knn_prior)
    train_out["loc_rolling_mean"].fillna(train_df[TARGET].mean(), inplace=True)

    test_out = None
    if test_df is not None:
        test_out = test_df.copy()
        for loc in test_out.location.unique():
            loc_mask = test_out.location == loc
            if test_out.loc[loc_mask, "loc_rolling_mean"].isna().any():
                if loc in knn_prior:
                    test_out.loc[loc_mask & test_out["loc_rolling_mean"].isna(),
                                 "loc_rolling_mean"] = knn_prior[loc]
                else:
                    row = test_out[test_out.location == loc].iloc[0]
                    tv = pd.DataFrame([row])[morph].fillna(
                        loc_stats[morph].median().to_dict()).values
                    ts = sc.transform(tv)
                    d, ix = nn.kneighbors(ts, n_neighbors=min(K + 1, len(loc_stats)))
                    nbrs = [(dd, int(j)) for dd, j in zip(d[0], ix[0])][:K]
                    w = [loc_stats.iloc[j]["n_events"] / (dd + 0.01) for dd, j in nbrs]
                    val = sum(wi * loc_stats.iloc[j]["mean_target"]
                              for wi, (_, j) in zip(w, nbrs)) / sum(w)
                    test_out.loc[loc_mask & test_out["loc_rolling_mean"].isna(),
                                 "loc_rolling_mean"] = val
        test_out["loc_rolling_mean"].fillna(train_df[TARGET].mean(), inplace=True)

    return train_out, test_out


# ---------------------------------------------------------------------------
# FEATURE IMPORTANCE PRUNING
# ---------------------------------------------------------------------------

def prune_features(df, features, target_col="log_cpue", label=""):
    """Auto-prune features using CatBoost importance on temporal split."""
    from catboost import CatBoostRegressor

    log(f"\n--- Feature Pruning ({label or 'all'}) ---")
    log(f"  Starting with {len(features)} features")

    df_s = df.sort_values("date")
    split = int(len(df_s) * 0.8)
    tr_raw, te_raw = df_s.iloc[:split].copy(), df_s.iloc[split:].copy()
    tr, te = fill_rolling_mean_gaps(tr_raw, te_raw)

    y_tr = tr[target_col].values
    y_te = te[target_col].values

    m_full = CatBoostRegressor(
        iterations=1200, depth=6, learning_rate=0.05,
        l2_leaf_reg=3, verbose=0, random_seed=SEED, subsample=0.85,
        thread_count=-1,
    )
    m_full.fit(tr[features], y_tr)
    imp = pd.Series(m_full.feature_importances_, index=features).sort_values(ascending=False)

    # Sweep top-N
    best_n, best_r2 = 0, -np.inf
    for top_n in range(30, min(350, len(features) + 1), 10):
        top_feats = imp.head(top_n).index.tolist()
        m = CatBoostRegressor(
            iterations=1000, depth=6, learning_rate=0.05,
            l2_leaf_reg=3, verbose=0, random_seed=SEED, subsample=0.85,
            thread_count=-1,
        )
        m.fit(tr[top_feats], y_tr)
        pred = np.clip(m.predict(te[top_feats]), 0, 4)
        r2 = r2_score(y_te, pred)
        if r2 > best_r2:
            best_r2 = r2
            best_n = top_n

    pruned = imp.head(best_n).index.tolist()
    log(f"  Optimal top-N: {best_n} features (temporal R²={best_r2:.4f})")

    # Top 20 features
    log(f"\n  Top 20 features ({label}):")
    for feat in imp.head(20).index:
        log(f"    {feat:>45}: {imp[feat]:.3f}%")

    return pruned, imp


# ---------------------------------------------------------------------------
# TRAIN SINGLE MODEL (shared by seen and unseen pipelines)
# ---------------------------------------------------------------------------

def train_stacked_model(df, features, label, best_cfg, target_col="log_cpue"):
    """Train CatBoost + XGBoost + LightGBM stacked ensemble on given data."""
    from catboost import CatBoostRegressor
    from xgboost import XGBRegressor
    import lightgbm as lgb

    y = df[target_col].values
    groups = df["location"].values
    sample_weights = np.where(df["is_premium"].values == 1, 2.0, 1.0)

    log(f"\n  Training {label} model — {len(features)} features, {len(df)} rows")

    n_splits = min(5, df["location"].nunique())
    if n_splits < 2:
        log(f"  WARNING: Only {n_splits} groups, skipping CV for {label}")
        n_splits = 2

    gkf = GroupKFold(n_splits=n_splits)

    oof_cb = np.full(len(df), np.nan)
    oof_xgb = np.full(len(df), np.nan)
    oof_lgb = np.full(len(df), np.nan)

    lgb_params = {
        "objective": "regression", "metric": "rmse",
        "num_leaves": 31, "learning_rate": best_cfg["lr"],
        "feature_fraction": 0.7, "bagging_fraction": best_cfg["sub"],
        "bagging_freq": 5, "lambda_l2": best_cfg["l2"],
        "verbose": -1, "seed": SEED, "num_threads": -1,
    }

    for fold, (tr_idx, va_idx) in enumerate(gkf.split(df, y, groups)):
        X_tr, X_va = df.iloc[tr_idx][features], df.iloc[va_idx][features]
        y_tr, y_va = y[tr_idx], y[va_idx]
        sw_tr = sample_weights[tr_idx]

        cb = CatBoostRegressor(
            iterations=3000, depth=best_cfg["depth"], learning_rate=best_cfg["lr"],
            l2_leaf_reg=best_cfg["l2"], subsample=best_cfg["sub"],
            random_seed=SEED, verbose=0, early_stopping_rounds=200,
            thread_count=-1,
        )
        cb.fit(X_tr, y_tr, sample_weight=sw_tr, eval_set=(X_va, y_va))
        oof_cb[va_idx] = cb.predict(X_va)

        xg = XGBRegressor(
            n_estimators=3000, max_depth=best_cfg["depth"],
            learning_rate=best_cfg["lr"],
            reg_lambda=best_cfg["l2"], subsample=best_cfg["sub"],
            colsample_bytree=0.7, colsample_bylevel=0.7,
            random_state=SEED, early_stopping_rounds=200, verbosity=0,
            n_jobs=-1, tree_method="hist",
        )
        xg.fit(X_tr.fillna(-999), y_tr, sample_weight=sw_tr,
               eval_set=[(X_va.fillna(-999), y_va)], verbose=False)
        oof_xgb[va_idx] = xg.predict(X_va.fillna(-999))

        lgb_train = lgb.Dataset(X_tr, y_tr, weight=sw_tr)
        lgb_val = lgb.Dataset(X_va, y_va, reference=lgb_train)
        lgb_model = lgb.train(
            lgb_params, lgb_train, num_boost_round=2000,
            valid_sets=[lgb_val],
            callbacks=[lgb.early_stopping(150), lgb.log_evaluation(0)],
        )
        oof_lgb[va_idx] = lgb_model.predict(X_va)

    # Ridge meta-learner
    valid_mask = ~(np.isnan(oof_cb) | np.isnan(oof_xgb) | np.isnan(oof_lgb))
    oof_stack = np.column_stack([oof_cb, oof_xgb, oof_lgb])[valid_mask]
    y_stack = y[valid_mask]

    ridge = Ridge(alpha=1.0)
    ridge.fit(oof_stack, y_stack)
    meta_pred = ridge.predict(oof_stack)
    cv_r2 = r2_score(y_stack, meta_pred)

    log(f"  {label} CV R² (stacked): {cv_r2:.4f}")
    log(f"  Ridge weights: CB={ridge.coef_[0]:.3f} XGB={ridge.coef_[1]:.3f} LGB={ridge.coef_[2]:.3f}")

    return cv_r2, ridge, lgb_params


def train_final_models(df, features, best_cfg, target_col="log_cpue"):
    """Train final production models on all data."""
    from catboost import CatBoostRegressor
    from xgboost import XGBRegressor
    import lightgbm as lgb

    y = df[target_col].values
    sample_weights = np.where(df["is_premium"].values == 1, 2.0, 1.0)

    cb = CatBoostRegressor(
        iterations=3500, depth=best_cfg["depth"], learning_rate=best_cfg["lr"],
        l2_leaf_reg=best_cfg["l2"], subsample=best_cfg["sub"],
        random_seed=SEED, verbose=0, thread_count=-1,
    )
    cb.fit(df[features], y, sample_weight=sample_weights)

    xg = XGBRegressor(
        n_estimators=3000, max_depth=best_cfg["depth"],
        learning_rate=best_cfg["lr"],
        reg_lambda=best_cfg["l2"], subsample=best_cfg["sub"],
        colsample_bytree=0.7, random_state=SEED, verbosity=0,
        n_jobs=-1, tree_method="hist",
    )
    xg.fit(df[features].fillna(-999), y, sample_weight=sample_weights)

    lgb_params = {
        "objective": "regression", "metric": "rmse",
        "num_leaves": 31, "learning_rate": best_cfg["lr"],
        "feature_fraction": 0.7, "bagging_fraction": best_cfg["sub"],
        "bagging_freq": 5, "lambda_l2": best_cfg["l2"],
        "verbose": -1, "seed": SEED, "num_threads": -1,
    }
    lgb_ds = lgb.Dataset(df[features], y, weight=sample_weights)
    lgb_model = lgb.train(lgb_params, lgb_ds, num_boost_round=2000)

    return cb, xg, lgb_model


# ---------------------------------------------------------------------------
# HP SEARCH
# ---------------------------------------------------------------------------

def hp_search(df, features, label=""):
    """Find best hyperparameters on temporal split."""
    from catboost import CatBoostRegressor

    log(f"\n--- HP Search ({label}) ---")

    df_s = df.sort_values("date")
    split = int(len(df_s) * 0.8)
    hp_tr, hp_va = df_s.iloc[:split], df_s.iloc[split:]
    hp_tr, hp_va = fill_rolling_mean_gaps(hp_tr, hp_va)
    y_tr = hp_tr["log_cpue"].values
    y_va = hp_va["log_cpue"].values
    sw = np.where(hp_tr["is_premium"].values == 1, 2.0, 1.0)

    HP_CONFIGS = [
        {"depth": 4, "lr": 0.02, "l2": 10, "sub": 0.6},
        {"depth": 4, "lr": 0.02, "l2": 20, "sub": 0.5},
        {"depth": 3, "lr": 0.02, "l2": 20, "sub": 0.5},
        {"depth": 3, "lr": 0.03, "l2": 15, "sub": 0.6},
        {"depth": 4, "lr": 0.01, "l2": 15, "sub": 0.5},
        {"depth": 3, "lr": 0.01, "l2": 25, "sub": 0.5},
        {"depth": 5, "lr": 0.02, "l2": 10, "sub": 0.6},
        {"depth": 5, "lr": 0.03, "l2": 8, "sub": 0.7},
        {"depth": 6, "lr": 0.02, "l2": 5, "sub": 0.7},
        {"depth": 4, "lr": 0.03, "l2": 12, "sub": 0.65},
        # New configs: stronger regularization for temporal generalization
        {"depth": 3, "lr": 0.01, "l2": 30, "sub": 0.4},
        {"depth": 4, "lr": 0.01, "l2": 25, "sub": 0.4},
        {"depth": 3, "lr": 0.02, "l2": 30, "sub": 0.5},
    ]

    best_cfg = HP_CONFIGS[0]
    best_r2 = -999

    for cfg in HP_CONFIGS:
        m = CatBoostRegressor(
            iterations=2500, depth=cfg["depth"], learning_rate=cfg["lr"],
            l2_leaf_reg=cfg["l2"], subsample=cfg["sub"],
            random_seed=SEED, verbose=0, early_stopping_rounds=150,
            thread_count=-1,
        )
        m.fit(hp_tr[features], y_tr, sample_weight=sw,
              eval_set=(hp_va[features], y_va))
        pred = m.predict(hp_va[features])
        r2 = r2_score(y_va, pred)
        log(f"  d={cfg['depth']} lr={cfg['lr']} l2={cfg['l2']} sub={cfg['sub']}: R²={r2:.4f}")
        if r2 > best_r2:
            best_r2 = r2
            best_cfg = cfg

    log(f"  Best: {best_cfg} → R²={best_r2:.4f}")
    return best_cfg


# ---------------------------------------------------------------------------
# TEMPORAL EVALUATION (the key metric to improve)
# ---------------------------------------------------------------------------

def evaluate_temporal(df, seen_features, unseen_features, seen_cfg, unseen_cfg,
                      seen_ridge, unseen_ridge):
    """
    Evaluate temporal holdout using two-model routing.
    Route seen-location rows to seen model, unseen to unseen model.
    """
    from catboost import CatBoostRegressor
    from xgboost import XGBRegressor
    import lightgbm as lgb

    target_col = "log_cpue"
    temporal_split = pd.Timestamp("2023-06-01")

    tr_mask = df["date"] < temporal_split
    va_mask = df["date"] >= temporal_split

    if va_mask.sum() < 20:
        log("  Temporal holdout: insufficient data")
        return None

    log(f"\n{'=' * 70}")
    log("TEMPORAL HOLDOUT — TWO-MODEL ROUTING")
    log(f"{'=' * 70}")

    tr_df = df[tr_mask].copy()
    va_df = df[va_mask].copy()

    # Determine seen vs unseen locations in test set
    train_locs = set(tr_df["location"].unique())
    loc_counts = tr_df.groupby("location").size()
    seen_locs = set(loc_counts[loc_counts >= MIN_LOC_HISTORY].index)

    va_seen_mask = va_df["location"].isin(seen_locs)
    va_unseen_mask = ~va_seen_mask

    log(f"  Train: {len(tr_df)}, Test: {len(va_df)}")
    log(f"  Test seen: {va_seen_mask.sum()}, Test unseen: {va_unseen_mask.sum()}")

    # Fill rolling mean gaps
    tr_filled, va_filled = fill_rolling_mean_gaps(tr_df, va_df)

    sw_tr = np.where(tr_filled["is_premium"].values == 1, 2.0, 1.0)
    y_tr = tr_filled[target_col].values
    y_va = va_filled[target_col].values

    predictions = np.full(len(va_filled), np.nan)

    # ---- Train & predict with SEEN model ----
    if va_seen_mask.sum() > 0:
        log(f"\n  Training SEEN model for temporal eval...")

        cb_s = CatBoostRegressor(
            iterations=3000, depth=seen_cfg["depth"], learning_rate=seen_cfg["lr"],
            l2_leaf_reg=seen_cfg["l2"], subsample=seen_cfg["sub"],
            random_seed=SEED, verbose=0, early_stopping_rounds=200,
            thread_count=-1,
        )
        cb_s.fit(tr_filled[seen_features], y_tr, sample_weight=sw_tr,
                 eval_set=(va_filled[va_seen_mask.values][seen_features],
                           y_va[va_seen_mask.values]))
        p_cb = cb_s.predict(va_filled[va_seen_mask.values][seen_features])

        xg_s = XGBRegressor(
            n_estimators=3000, max_depth=seen_cfg["depth"],
            learning_rate=seen_cfg["lr"],
            reg_lambda=seen_cfg["l2"], subsample=seen_cfg["sub"],
            colsample_bytree=0.7, random_state=SEED,
            early_stopping_rounds=200, verbosity=0, n_jobs=-1, tree_method="hist",
        )
        xg_s.fit(tr_filled[seen_features].fillna(-999), y_tr, sample_weight=sw_tr,
                 eval_set=[(va_filled[va_seen_mask.values][seen_features].fillna(-999),
                            y_va[va_seen_mask.values])], verbose=False)
        p_xg = xg_s.predict(va_filled[va_seen_mask.values][seen_features].fillna(-999))

        lgb_params_s = {
            "objective": "regression", "metric": "rmse",
            "num_leaves": 31, "learning_rate": seen_cfg["lr"],
            "feature_fraction": 0.7, "bagging_fraction": seen_cfg["sub"],
            "bagging_freq": 5, "lambda_l2": seen_cfg["l2"],
            "verbose": -1, "seed": SEED, "num_threads": -1,
        }
        lgb_tr = lgb.Dataset(tr_filled[seen_features], y_tr, weight=sw_tr)
        lgb_va = lgb.Dataset(va_filled[va_seen_mask.values][seen_features],
                             y_va[va_seen_mask.values], reference=lgb_tr)
        lgb_s = lgb.train(lgb_params_s, lgb_tr, num_boost_round=2000,
                          valid_sets=[lgb_va],
                          callbacks=[lgb.early_stopping(150), lgb.log_evaluation(0)])
        p_lgb = lgb_s.predict(va_filled[va_seen_mask.values][seen_features])

        stack_seen = np.column_stack([p_cb, p_xg, p_lgb])
        p_seen = seen_ridge.predict(stack_seen)
        predictions[va_seen_mask.values] = p_seen

        r2_seen = r2_score(y_va[va_seen_mask.values], p_seen)
        log(f"  SEEN R²: {r2_seen:.4f} ({va_seen_mask.sum()} rows)")

    # ---- Train & predict with UNSEEN model ----
    if va_unseen_mask.sum() > 0:
        log(f"\n  Training UNSEEN model for temporal eval...")

        cb_u = CatBoostRegressor(
            iterations=3000, depth=unseen_cfg["depth"], learning_rate=unseen_cfg["lr"],
            l2_leaf_reg=unseen_cfg["l2"], subsample=unseen_cfg["sub"],
            random_seed=SEED, verbose=0, early_stopping_rounds=200,
            thread_count=-1,
        )
        cb_u.fit(tr_filled[unseen_features], y_tr, sample_weight=sw_tr,
                 eval_set=(va_filled[va_unseen_mask.values][unseen_features],
                           y_va[va_unseen_mask.values]))
        p_cb_u = cb_u.predict(va_filled[va_unseen_mask.values][unseen_features])

        xg_u = XGBRegressor(
            n_estimators=3000, max_depth=unseen_cfg["depth"],
            learning_rate=unseen_cfg["lr"],
            reg_lambda=unseen_cfg["l2"], subsample=unseen_cfg["sub"],
            colsample_bytree=0.7, random_state=SEED,
            early_stopping_rounds=200, verbosity=0, n_jobs=-1, tree_method="hist",
        )
        xg_u.fit(tr_filled[unseen_features].fillna(-999), y_tr, sample_weight=sw_tr,
                 eval_set=[(va_filled[va_unseen_mask.values][unseen_features].fillna(-999),
                            y_va[va_unseen_mask.values])], verbose=False)
        p_xg_u = xg_u.predict(va_filled[va_unseen_mask.values][unseen_features].fillna(-999))

        lgb_params_u = {
            "objective": "regression", "metric": "rmse",
            "num_leaves": 31, "learning_rate": unseen_cfg["lr"],
            "feature_fraction": 0.7, "bagging_fraction": unseen_cfg["sub"],
            "bagging_freq": 5, "lambda_l2": unseen_cfg["l2"],
            "verbose": -1, "seed": SEED, "num_threads": -1,
        }
        lgb_tr_u = lgb.Dataset(tr_filled[unseen_features], y_tr, weight=sw_tr)
        lgb_va_u = lgb.Dataset(va_filled[va_unseen_mask.values][unseen_features],
                               y_va[va_unseen_mask.values], reference=lgb_tr_u)
        lgb_u = lgb.train(lgb_params_u, lgb_tr_u, num_boost_round=2000,
                          valid_sets=[lgb_va_u],
                          callbacks=[lgb.early_stopping(150), lgb.log_evaluation(0)])
        p_lgb_u = lgb_u.predict(va_filled[va_unseen_mask.values][unseen_features])

        stack_unseen = np.column_stack([p_cb_u, p_xg_u, p_lgb_u])
        p_unseen = unseen_ridge.predict(stack_unseen)
        predictions[va_unseen_mask.values] = p_unseen

        r2_unseen = r2_score(y_va[va_unseen_mask.values], p_unseen)
        log(f"  UNSEEN R²: {r2_unseen:.4f} ({va_unseen_mask.sum()} rows)")

    # ---- Combined ----
    valid = ~np.isnan(predictions)
    if valid.sum() > 0:
        r2_combined = r2_score(y_va[valid], predictions[valid])
        mae_combined = mean_absolute_error(
            np.expm1(y_va[valid]), np.expm1(predictions[valid]))
        log(f"\n  COMBINED Temporal R²: {r2_combined:.4f}")
        log(f"  COMBINED Temporal MAE: {mae_combined:.3f} lb")
        return r2_combined

    return None


# ---------------------------------------------------------------------------
# WALK-FORWARD EVALUATION
# ---------------------------------------------------------------------------

def evaluate_walkforward(df, features, best_cfg, ridge):
    """Walk-forward temporal validation."""
    from catboost import CatBoostRegressor

    target_col = "log_cpue"

    log(f"\n{'=' * 70}")
    log("WALK-FORWARD TEMPORAL VALIDATION")
    log(f"{'=' * 70}")

    wf_folds = [
        ("< 2020", "2020-2021", df["year"] < 2020, df["year"].between(2020, 2021)),
        ("< 2022", "2022-2023", df["year"] < 2022, df["year"].between(2022, 2023)),
        ("< 2024", "2024+", df["year"] < 2024, df["year"] >= 2024),
    ]

    wf_r2s = []
    for train_label, test_label, tr_mask, va_mask in wf_folds:
        if tr_mask.sum() < 30 or va_mask.sum() < 10:
            continue

        wf_tr, wf_va = fill_rolling_mean_gaps(df[tr_mask], df[va_mask])
        y_tr = wf_tr[target_col].values
        y_va = wf_va[target_col].values
        sw = np.where(wf_tr["is_premium"].values == 1, 2.0, 1.0)

        cb = CatBoostRegressor(
            iterations=3000, depth=best_cfg["depth"], learning_rate=best_cfg["lr"],
            l2_leaf_reg=best_cfg["l2"], subsample=best_cfg["sub"],
            random_seed=SEED, verbose=0, early_stopping_rounds=200,
            thread_count=-1,
        )
        cb.fit(wf_tr[features], y_tr, sample_weight=sw,
               eval_set=(wf_va[features], y_va))
        p = cb.predict(wf_va[features])
        r2 = r2_score(y_va, p)
        mae = mean_absolute_error(np.expm1(y_va), np.expm1(p))
        wf_r2s.append(r2)
        log(f"  {train_label}/{test_label}: R²={r2:.4f} MAE={mae:.3f} (n={va_mask.sum()})")

    r2_wf = np.mean(wf_r2s) if wf_r2s else None
    if wf_r2s:
        log(f"  Walk-forward mean: R²={r2_wf:.4f} ± {np.std(wf_r2s):.4f}")
    return r2_wf


# ---------------------------------------------------------------------------
# SPATIAL CV
# ---------------------------------------------------------------------------

def evaluate_spatial(df, features, best_cfg):
    """Lat-band spatial holdout evaluation."""
    from catboost import CatBoostRegressor

    target_col = "log_cpue"

    log(f"\n{'=' * 70}")
    log("SPATIAL CV (lat-band holdout)")
    log(f"{'=' * 70}")

    df["_region"] = pd.cut(df["lat"], bins=5, labels=["S", "SM", "M", "MN", "N"])
    r2s = []
    for region in df["_region"].dropna().unique():
        te_mask = df["_region"] == region
        if te_mask.sum() < 10:
            continue
        sp_tr, sp_te = fill_rolling_mean_gaps(df[~te_mask], df[te_mask])
        cb = CatBoostRegressor(
            iterations=1500, depth=best_cfg["depth"], learning_rate=best_cfg["lr"],
            l2_leaf_reg=best_cfg["l2"], subsample=best_cfg["sub"],
            random_seed=SEED, verbose=0, thread_count=-1,
        )
        cb.fit(sp_tr[features], sp_tr[target_col])
        pred = np.clip(cb.predict(sp_te[features]), 0, 4)
        r2 = r2_score(sp_te[target_col], pred)
        r2s.append(r2)
        log(f"  {region}: R²={r2:.4f} (n={te_mask.sum()})")

    r2_spatial = np.mean(r2s) if r2s else None
    if r2s:
        log(f"  Spatial mean: R²={r2_spatial:.4f}")
    return r2_spatial


# ---------------------------------------------------------------------------
# SAVE ARTIFACTS
# ---------------------------------------------------------------------------

def save_artifacts(df, seen_features, unseen_features, seen_cfg, unseen_cfg,
                   seen_ridge, unseen_ridge, seen_cb, seen_xg, seen_lgb,
                   unseen_cb, unseen_xg, unseen_lgb, metrics, shap_dict):
    """Save all model artifacts for production deployment."""
    log(f"\n{'=' * 70}")
    log("SAVING ARTIFACTS")
    log(f"{'=' * 70}")

    models_dir = BASE_DIR / "castline" / "models"
    if not models_dir.exists():
        models_dir = BASE_DIR / "models"
    models_dir.mkdir(parents=True, exist_ok=True)

    # Seen model artifacts
    seen_cb.save_model(str(models_dir / f"cpue_{VERSION}_seen_catboost.cbm"))
    with open(models_dir / f"cpue_{VERSION}_seen_xgboost.pkl", "wb") as f:
        pickle.dump(seen_xg, f)
    with open(models_dir / f"cpue_{VERSION}_seen_lightgbm.pkl", "wb") as f:
        pickle.dump(seen_lgb, f)
    with open(models_dir / f"cpue_{VERSION}_seen_ridge.pkl", "wb") as f:
        pickle.dump(seen_ridge, f)
    with open(models_dir / f"cpue_{VERSION}_seen_features.json", "w") as f:
        json.dump(seen_features, f, indent=2)
    log(f"  Saved SEEN model ({len(seen_features)} features)")

    # Unseen model artifacts
    unseen_cb.save_model(str(models_dir / f"cpue_{VERSION}_unseen_catboost.cbm"))
    with open(models_dir / f"cpue_{VERSION}_unseen_xgboost.pkl", "wb") as f:
        pickle.dump(unseen_xg, f)
    with open(models_dir / f"cpue_{VERSION}_unseen_lightgbm.pkl", "wb") as f:
        pickle.dump(unseen_lgb, f)
    with open(models_dir / f"cpue_{VERSION}_unseen_ridge.pkl", "wb") as f:
        pickle.dump(unseen_ridge, f)
    with open(models_dir / f"cpue_{VERSION}_unseen_features.json", "w") as f:
        json.dump(unseen_features, f, indent=2)
    log(f"  Saved UNSEEN model ({len(unseen_features)} features)")

    # Location means for routing decision at inference
    loc_means = df.groupby("location").agg(
        mean_weight=(TARGET, "mean"),
        n_events=(TARGET, "count"),
    ).to_dict(orient="index")
    with open(models_dir / f"cpue_{VERSION}_location_stats.json", "w") as f:
        json.dump(loc_means, f, indent=2)
    log(f"  Saved location stats ({len(loc_means)} locations)")

    # SHAP importance
    if shap_dict:
        with open(models_dir / f"shap_importance_{VERSION}.json", "w") as f:
            json.dump(shap_dict, f, indent=2)

    # Metadata
    metadata = {
        "version": VERSION,
        "architecture": "two-model (seen + unseen location routing)",
        "trained_at": datetime.now().isoformat(),
        "dataset": "validation_dataset_v16.csv + enhanced temporal features",
        "n_rows": len(df),
        "n_locations": df["location"].nunique(),
        "seen_model": {
            "n_features": len(seen_features),
            "hp": seen_cfg,
            "ridge_weights": {
                "catboost": float(seen_ridge.coef_[0]),
                "xgboost": float(seen_ridge.coef_[1]),
                "lightgbm": float(seen_ridge.coef_[2]),
            },
        },
        "unseen_model": {
            "n_features": len(unseen_features),
            "hp": unseen_cfg,
            "ridge_weights": {
                "catboost": float(unseen_ridge.coef_[0]),
                "xgboost": float(unseen_ridge.coef_[1]),
                "lightgbm": float(unseen_ridge.coef_[2]),
            },
        },
        "min_loc_history": MIN_LOC_HISTORY,
        "metrics": metrics,
    }
    with open(models_dir / f"cpue_{VERSION}_metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)
    log(f"  Saved metadata → cpue_{VERSION}_metadata.json")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=f"CPUE Model {VERSION}")
    parser.add_argument("--workspace", default=None, help="Override workspace dir")
    args = parser.parse_args()

    global BASE_DIR, DATASET_CANDIDATES, USACE_DATA_CANDIDATES
    if args.workspace:
        BASE_DIR = Path(args.workspace)
        DATASET_CANDIDATES = [
            BASE_DIR / "castline" / "validation" / "data" / "assembled" / "validation_dataset_v16.csv",
            BASE_DIR / "data" / "assembled" / "validation_dataset_v16.csv",
            BASE_DIR / "validation_dataset_v16.csv",
        ]
        USACE_DATA_CANDIDATES = [
            BASE_DIR / "castline" / "validation" / "data" / "raw" / "usace_reservoir_data.csv",
            BASE_DIR / "data" / "raw" / "usace_reservoir_data.csv",
        ]

    setup_logging(BASE_DIR)

    # ---- Load data ----
    df = load_dataset()

    # ---- Feature selection for both models ----
    seen_features_raw = select_features(df)
    unseen_features_raw = select_features(df, exclude_extra=UNSEEN_EXTRA_EXCLUDE)

    log(f"\n  SEEN candidate features: {len(seen_features_raw)}")
    log(f"  UNSEEN candidate features: {len(unseen_features_raw)}")

    # ---- Prune features ----
    seen_features, seen_imp = prune_features(df, seen_features_raw, label="SEEN")
    unseen_features, unseen_imp = prune_features(df, unseen_features_raw, label="UNSEEN")

    # ---- HP search for both models ----
    seen_cfg = hp_search(df, seen_features, label="SEEN")
    unseen_cfg = hp_search(df, unseen_features, label="UNSEEN")

    # ---- Train both models with CV ----
    log(f"\n{'=' * 70}")
    log("TRAINING BOTH MODELS")
    log(f"{'=' * 70}")

    seen_cv_r2, seen_ridge, _ = train_stacked_model(df, seen_features, "SEEN", seen_cfg)
    unseen_cv_r2, unseen_ridge, _ = train_stacked_model(df, unseen_features, "UNSEEN", unseen_cfg)

    # ---- Temporal evaluation with two-model routing ----
    r2_temporal = evaluate_temporal(
        df, seen_features, unseen_features,
        seen_cfg, unseen_cfg, seen_ridge, unseen_ridge
    )

    # ---- Walk-forward (using seen model features for now) ----
    r2_walkforward = evaluate_walkforward(df, seen_features, seen_cfg, seen_ridge)

    # ---- Spatial CV ----
    r2_spatial = evaluate_spatial(df, seen_features, seen_cfg)

    # ---- Train final production models ----
    log(f"\n{'=' * 70}")
    log("TRAINING FINAL PRODUCTION MODELS")
    log(f"{'=' * 70}")

    seen_cb, seen_xg, seen_lgb = train_final_models(df, seen_features, seen_cfg)
    log(f"  SEEN models trained on {len(df)} rows")

    unseen_cb, unseen_xg, unseen_lgb = train_final_models(df, unseen_features, unseen_cfg)
    log(f"  UNSEEN models trained on {len(df)} rows")

    # ---- SHAP ----
    shap_dict = {}
    try:
        import shap
        log("\n  Computing SHAP (seen model)...")
        explainer = shap.TreeExplainer(seen_cb)
        sample_idx = np.random.RandomState(SEED).choice(
            len(df), min(800, len(df)), replace=False)
        shap_values = explainer.shap_values(df.iloc[sample_idx][seen_features])
        shap_imp = pd.Series(
            np.abs(shap_values).mean(axis=0), index=seen_features
        ).sort_values(ascending=False)
        shap_dict = {feat: float(val) for feat, val in shap_imp.items()}
        log("  SHAP top 20:")
        for feat, val in shap_imp.head(20).items():
            log(f"    {feat:>45}: {val:.4f}")
    except ImportError:
        log("  SHAP not available")

    # ---- Save ----
    metrics = {
        "seen_cv_r2": float(seen_cv_r2),
        "unseen_cv_r2": float(unseen_cv_r2),
        "temporal_r2_combined": float(r2_temporal) if r2_temporal else None,
        "walkforward_r2": float(r2_walkforward) if r2_walkforward else None,
        "spatial_r2": float(r2_spatial) if r2_spatial else None,
    }

    save_artifacts(
        df, seen_features, unseen_features, seen_cfg, unseen_cfg,
        seen_ridge, unseen_ridge, seen_cb, seen_xg, seen_lgb,
        unseen_cb, unseen_xg, unseen_lgb, metrics, shap_dict
    )

    # ---- FINAL SUMMARY ----
    log(f"\n{'=' * 70}")
    log(f"FINAL SUMMARY — CPUE Model {VERSION}")
    log(f"{'=' * 70}")
    log(f"  Architecture:      Two-model (seen={len(seen_features)}f, unseen={len(unseen_features)}f)")
    log(f"  Dataset:           {len(df)} rows, {df['location'].nunique()} locations")
    log(f"  SEEN CV R²:        {seen_cv_r2:.4f}")
    log(f"  UNSEEN CV R²:      {unseen_cv_r2:.4f}")
    log(f"  Temporal R²:       {r2_temporal:.4f}" if r2_temporal else "  Temporal R²:       N/A")
    log(f"  Walk-forward R²:   {r2_walkforward:.4f}" if r2_walkforward else "  Walk-forward R²:   N/A")
    log(f"  Spatial R²:        {r2_spatial:.4f}" if r2_spatial else "  Spatial R²:        N/A")
    log(f"")
    log(f"  V13 baselines:     CV=0.705  Temporal=0.329  Walk-fwd=0.429  Spatial=0.539")
    log(f"  V12 baselines:     CV=0.410  Temporal=0.420  Walk-fwd=0.331")
    log(f"{'=' * 70}")

    if _log_file:
        _log_file.close()

    return metrics


if __name__ == "__main__":
    main()
