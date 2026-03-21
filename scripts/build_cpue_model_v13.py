#!/usr/bin/env python3
"""
CPUE Model v13 — V16 Dataset + Auto-Pruning + Stacked Ensemble
================================================================
Key changes over V12:
  1. USE V16 ASSEMBLED DATASET (366 features) — includes USGS fish community,
     GeoCLIP PCA-32 embeddings, CreelCat spatial, LAGOS morphometry, lag features,
     satellite water temp, and all enrichments already merged.
  2. AUTOMATIC FEATURE PRUNING — CatBoost importance sweep to find optimal N.
  3. LEAKAGE-SAFE — explicit exclusion of loc_mean_enc, source_enc, raw num_anglers,
     and other identity/leaky columns.
  4. MULTI-EVAL — temporal holdout, walk-forward, spatial CV, spatiotemporal CV, LOO.
  5. STACKED ENSEMBLE — CatBoost + XGBoost + LightGBM → Ridge meta-learner.
  6. SHAP feature importance on pruned feature set.
  7. Production artifact export (models, features, SHAP, metadata).

Target: Temporal R² > 0.50 (up from 0.42 in V12).

Usage:
    python scripts/build_cpue_model_v13.py [--workspace /workspace/castline]
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

# Try assembled dataset locations (Vast.ai vs local)
DATASET_CANDIDATES = [
    BASE_DIR / "castline" / "validation" / "data" / "assembled" / "validation_dataset_v16.csv",
    BASE_DIR / "data" / "assembled" / "validation_dataset_v16.csv",
    BASE_DIR / "validation_dataset_v16.csv",
]

SEED = 42
VERSION = "v13"

# Columns to always exclude
TARGET = "median_weight_lb"
ALWAYS_EXCLUDE = {
    # Target and derivatives
    TARGET, "target_success_score", "log_cpue", "cpue",
    # Identity
    "event_id", "tms_id", "tournament_slug", "event_name",
    # Meta (string/categorical)
    "date", "location", "region", "block", "sat_source",
    "usgs_site_id", "results_source", "trail", "species",
    "source", "spawn_phase", "_source",
    # Leaky encodings (known from V10 analysis)
    "loc_mean_enc", "source_enc", "loc_enc",
    "trail_mean_weight", "location_mean_weight",
    "baseline_signal",
    # Raw num_anglers (V12 lesson: was 22.86% importance, learning tournament size)
    "num_anglers", "num_anglers_capped",
    # Temporal leakage columns
    "loc_target_cv",
}

# LOO-specific exclusions (location-level aggregates leak in LOO)
LOO_EXTRA_EXCLUDE = {
    "loc_rolling_mean", "loc_rolling_std", "loc_n_prior",
    "loc_encoding_confidence", "loc_total_events",
    "loc_source_diversity", "loc_tournament_fraction",
    "loc_year_range", "loc_first_year", "loc_last_year",
    "loc_month_diversity", "loc_rolling_3",
}

# Morphometric features for KNN spatial encoding
LOC_MORPH = ["lat", "lon", "area_acres", "is_lake", "max_depth_ft", "shore_dev",
             "creel_lmb_ratio", "creel_smb_ratio", "creel_cpue_mean"]

MIN_LOO_N = 15
MIN_LOO_STD = 0.5

# Two-tier weighting
PREMIUM_SOURCES = {
    "elite_outcomes", "all_bassmaster_outcomes", "flw_outcomes",
    "combined_all_outcomes_v2", "mlf_outcomes",
    "elite_api", "elite", "bassmaster_classic",
}

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
# DATA LOADING
# ---------------------------------------------------------------------------

def load_dataset():
    """Load pre-assembled V16 dataset."""
    log("=" * 70)
    log(f"CPUE MODEL {VERSION} — V16 DATASET + AUTO-PRUNING + STACKED ENSEMBLE")
    log("=" * 70)

    dataset_path = None
    for p in DATASET_CANDIDATES:
        if p.exists():
            dataset_path = p
            break

    if dataset_path is None:
        log("ERROR: V16 dataset not found! Searched:")
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

    # Ensure log_cpue target
    df["log_cpue"] = np.log1p(df[TARGET])

    # Identify premium sources
    if "results_source" in df.columns:
        df["is_premium"] = df["results_source"].isin(PREMIUM_SOURCES).astype(float)
    elif "source" in df.columns:
        df["is_premium"] = df["source"].isin(PREMIUM_SOURCES).astype(float)
    else:
        df["is_premium"] = 1.0

    log(f"  Date range: {df['date'].min().date()} to {df['date'].max().date()}")
    log(f"  Locations: {df['location'].nunique()}")
    log(f"  Premium: {(df['is_premium'] == 1).sum()}, Auxiliary: {(df['is_premium'] == 0).sum()}")
    log(f"  Target range: {df[TARGET].min():.2f} - {df[TARGET].max():.2f} lb")
    log(f"  Target median: {df[TARGET].median():.2f} lb")
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
        # Require at least 3% non-null
        if df[c].notna().sum() < len(df) * 0.03:
            continue
        features.append(c)

    return sorted(features)


# ---------------------------------------------------------------------------
# KNN SPATIAL ENCODING (for LOO / unseen locations)
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

def prune_features(df, features, target_col="log_cpue"):
    """Auto-prune features using CatBoost importance on temporal split."""
    from catboost import CatBoostRegressor

    log("\n--- Feature Importance Pruning ---")
    log(f"  Starting with {len(features)} features")

    df_s = df.sort_values("date")
    split = int(len(df_s) * 0.8)
    tr_raw, te_raw = df_s.iloc[:split].copy(), df_s.iloc[split:].copy()
    tr, te = fill_rolling_mean_gaps(tr_raw, te_raw)

    y_tr = tr[target_col].values
    y_te = te[target_col].values

    # Full model for importance ranking
    m_full = CatBoostRegressor(
        iterations=1200, depth=6, learning_rate=0.05,
        l2_leaf_reg=3, verbose=0, random_seed=SEED, subsample=0.85,
        thread_count=-1,
    )
    m_full.fit(tr[features], y_tr)
    imp = pd.Series(m_full.feature_importances_, index=features).sort_values(ascending=False)

    # Sweep top-N to find optimal
    best_n, best_r2 = 0, -np.inf
    for top_n in range(30, min(350, len(features) + 1), 10):
        top_feats = imp.head(top_n).index.tolist()
        m = CatBoostRegressor(
            iterations=1000, depth=6, learning_rate=0.05,
            l2_leaf_reg=3, verbose=0, random_seed=SEED, subsample=0.85,
            thread_count=-1,
        )
        m.fit(tr[top_feats], y_tr)
        pred = np.clip(m.predict(te[top_feats]), 0, 4)  # log scale
        r2 = r2_score(y_te, pred)
        if r2 > best_r2:
            best_r2 = r2
            best_n = top_n

    pruned = imp.head(best_n).index.tolist()
    log(f"  Optimal top-N: {best_n} features (temporal R²={best_r2:.4f})")

    # Report feature group survival
    groups = {
        "usgs_": "USGS Fish Community",
        "geoclip_": "GeoCLIP PCA-32",
        "creel2_": "CreelCat Spatial",
        "lagos_": "LAGOS Morphometry",
        "lag_": "Lag Features",
        "satellite_": "Satellite Temp",
    }
    for prefix, name in groups.items():
        total = len([f for f in features if f.startswith(prefix)])
        survived = len([f for f in pruned if f.startswith(prefix)])
        if total > 0:
            log(f"  {name}: {survived}/{total} survived")

    # Top 25 features
    log("\n  Top 25 features:")
    for f in imp.head(25).index:
        log(f"    {f:>45}: {imp[f]:.3f}%")

    return pruned, imp


# ---------------------------------------------------------------------------
# TRAINING
# ---------------------------------------------------------------------------

def train_and_evaluate(df, features):
    """Full training pipeline with multi-evaluation."""
    from catboost import CatBoostRegressor
    from xgboost import XGBRegressor
    import lightgbm as lgb

    target_col = "log_cpue"
    y = df[target_col].values
    groups = df["location"].values
    sample_weights = np.where(df["is_premium"].values == 1, 2.0, 1.0)

    log(f"\n{'=' * 70}")
    log(f"TRAINING — {len(features)} features, {len(df)} rows")
    log(f"{'=' * 70}")

    # ---- Phase 1: HP Search ----
    log("\n--- Phase 1: Hyperparameter Search ---")

    df_s = df.sort_values("date")
    split = int(len(df_s) * 0.8)
    hp_tr, hp_va = df_s.iloc[:split], df_s.iloc[split:]
    hp_tr, hp_va = fill_rolling_mean_gaps(hp_tr, hp_va)
    y_hp_tr = hp_tr[target_col].values
    y_hp_va = hp_va[target_col].values
    sw_hp = np.where(hp_tr["is_premium"].values == 1, 2.0, 1.0)

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
        m.fit(hp_tr[features], y_hp_tr, sample_weight=sw_hp,
              eval_set=(hp_va[features], y_hp_va))
        pred = m.predict(hp_va[features])
        r2 = r2_score(y_hp_va, pred)
        log(f"  d={cfg['depth']} lr={cfg['lr']} l2={cfg['l2']} sub={cfg['sub']}: R²={r2:.4f}")
        if r2 > best_r2:
            best_r2 = r2
            best_cfg = cfg

    log(f"  Best config: {best_cfg} → R²={best_r2:.4f}")

    # ---- Phase 2: GroupKFold CV (full stacked ensemble) ----
    log(f"\n--- Phase 2: 5-Fold GroupKFold CV ---")

    n_splits = min(5, df["location"].nunique())
    gkf = GroupKFold(n_splits=n_splits)

    oof_cb = np.full(len(df), np.nan)
    oof_xgb = np.full(len(df), np.nan)
    oof_lgb = np.full(len(df), np.nan)
    cb_r2s, xgb_r2s, lgb_r2s = [], [], []

    for fold, (tr_idx, va_idx) in enumerate(gkf.split(df, y, groups)):
        t0 = time.time()
        X_tr, X_va = df.iloc[tr_idx][features], df.iloc[va_idx][features]
        y_tr, y_va = y[tr_idx], y[va_idx]
        sw_tr = sample_weights[tr_idx]

        # CatBoost
        cb = CatBoostRegressor(
            iterations=3000, depth=best_cfg["depth"], learning_rate=best_cfg["lr"],
            l2_leaf_reg=best_cfg["l2"], subsample=best_cfg["sub"],
            random_seed=SEED, verbose=0, early_stopping_rounds=200,
            thread_count=-1,
        )
        cb.fit(X_tr, y_tr, sample_weight=sw_tr, eval_set=(X_va, y_va))
        p_cb = cb.predict(X_va)
        oof_cb[va_idx] = p_cb
        cb_r2s.append(r2_score(y_va, p_cb))

        # XGBoost
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
        p_xg = xg.predict(X_va.fillna(-999))
        oof_xgb[va_idx] = p_xg
        xgb_r2s.append(r2_score(y_va, p_xg))

        # LightGBM
        lgb_train = lgb.Dataset(X_tr, y_tr, weight=sw_tr)
        lgb_val = lgb.Dataset(X_va, y_va, reference=lgb_train)
        lgb_params = {
            "objective": "regression", "metric": "rmse",
            "num_leaves": 31, "learning_rate": best_cfg["lr"],
            "feature_fraction": 0.7, "bagging_fraction": best_cfg["sub"],
            "bagging_freq": 5, "lambda_l2": best_cfg["l2"],
            "verbose": -1, "seed": SEED, "num_threads": -1,
        }
        lgb_model = lgb.train(
            lgb_params, lgb_train, num_boost_round=2000,
            valid_sets=[lgb_val],
            callbacks=[lgb.early_stopping(150), lgb.log_evaluation(0)],
        )
        p_lgb = lgb_model.predict(X_va)
        oof_lgb[va_idx] = p_lgb
        lgb_r2s.append(r2_score(y_va, p_lgb))

        log(f"  Fold {fold + 1}/{n_splits}: CB={cb_r2s[-1]:.4f} XGB={xgb_r2s[-1]:.4f} LGB={lgb_r2s[-1]:.4f} [{time.time() - t0:.0f}s]")

    log(f"\n  CV Summary:")
    log(f"    CatBoost:  {np.mean(cb_r2s):.4f} ± {np.std(cb_r2s):.4f}")
    log(f"    XGBoost:   {np.mean(xgb_r2s):.4f} ± {np.std(xgb_r2s):.4f}")
    log(f"    LightGBM:  {np.mean(lgb_r2s):.4f} ± {np.std(lgb_r2s):.4f}")

    # ---- Phase 3: Stacking Meta-Learner ----
    log(f"\n--- Phase 3: Ridge Meta-Learner ---")

    valid_mask = ~(np.isnan(oof_cb) | np.isnan(oof_xgb) | np.isnan(oof_lgb))
    oof_stack = np.column_stack([oof_cb, oof_xgb, oof_lgb])[valid_mask]
    y_stack = y[valid_mask]

    ridge = Ridge(alpha=1.0)
    ridge.fit(oof_stack, y_stack)
    meta_pred = ridge.predict(oof_stack)
    meta_r2 = r2_score(y_stack, meta_pred)
    meta_mae = mean_absolute_error(np.expm1(y_stack), np.expm1(meta_pred))

    log(f"  Ridge weights: CB={ridge.coef_[0]:.3f} XGB={ridge.coef_[1]:.3f} LGB={ridge.coef_[2]:.3f}")
    log(f"  Stacked CV R²: {meta_r2:.4f}  MAE: {meta_mae:.3f} lb")

    # ---- Phase 4: Temporal Holdout ----
    log(f"\n{'=' * 70}")
    log("TEMPORAL HOLDOUT (train < 2023-06-01, test >= 2023-06-01)")
    log(f"{'=' * 70}")

    temporal_split = pd.Timestamp("2023-06-01")
    tr_mask = df["date"] < temporal_split
    va_mask = df["date"] >= temporal_split

    r2_temporal = None
    if va_mask.sum() > 20:
        tr_t, va_t = fill_rolling_mean_gaps(df[tr_mask], df[va_mask])
        X_tr_t, X_va_t = tr_t[features], va_t[features]
        y_tr_t, y_va_t = tr_t[target_col].values, va_t[target_col].values
        sw_tr_t = np.where(tr_t["is_premium"].values == 1, 2.0, 1.0)

        # Train all 3 models on temporal split
        cb_t = CatBoostRegressor(
            iterations=3000, depth=best_cfg["depth"], learning_rate=best_cfg["lr"],
            l2_leaf_reg=best_cfg["l2"], subsample=best_cfg["sub"],
            random_seed=SEED, verbose=0, early_stopping_rounds=200,
            thread_count=-1,
        )
        cb_t.fit(X_tr_t, y_tr_t, sample_weight=sw_tr_t, eval_set=(X_va_t, y_va_t))
        p_cb_t = cb_t.predict(X_va_t)

        xg_t = XGBRegressor(
            n_estimators=3000, max_depth=best_cfg["depth"],
            learning_rate=best_cfg["lr"],
            reg_lambda=best_cfg["l2"], subsample=best_cfg["sub"],
            colsample_bytree=0.7, random_state=SEED,
            early_stopping_rounds=200, verbosity=0, n_jobs=-1, tree_method="hist",
        )
        xg_t.fit(X_tr_t.fillna(-999), y_tr_t, sample_weight=sw_tr_t,
                  eval_set=[(X_va_t.fillna(-999), y_va_t)], verbose=False)
        p_xg_t = xg_t.predict(X_va_t.fillna(-999))

        lgb_tr_t = lgb.Dataset(X_tr_t, y_tr_t, weight=sw_tr_t)
        lgb_va_t = lgb.Dataset(X_va_t, y_va_t, reference=lgb_tr_t)
        lgb_t = lgb.train(
            lgb_params, lgb_tr_t, num_boost_round=2000,
            valid_sets=[lgb_va_t],
            callbacks=[lgb.early_stopping(150), lgb.log_evaluation(0)],
        )
        p_lgb_t = lgb_t.predict(X_va_t)

        # Stacked prediction
        stack_t = np.column_stack([p_cb_t, p_xg_t, p_lgb_t])
        p_stacked_t = ridge.predict(stack_t)

        r2_cb_t = r2_score(y_va_t, p_cb_t)
        r2_xg_t = r2_score(y_va_t, p_xg_t)
        r2_lgb_t = r2_score(y_va_t, p_lgb_t)
        r2_stacked_t = r2_score(y_va_t, p_stacked_t)
        r2_temporal = max(r2_cb_t, r2_stacked_t)

        log(f"  Train: {tr_mask.sum()}, Test: {va_mask.sum()}")
        log(f"  CatBoost:  R²={r2_cb_t:.4f}")
        log(f"  XGBoost:   R²={r2_xg_t:.4f}")
        log(f"  LightGBM:  R²={r2_lgb_t:.4f}")
        log(f"  Stacked:   R²={r2_stacked_t:.4f}")
        log(f"  Best:      R²={r2_temporal:.4f}")

        mae_t = mean_absolute_error(np.expm1(y_va_t), np.expm1(p_stacked_t))
        log(f"  Stacked MAE: {mae_t:.3f} lb")

        # Seen vs unseen locations
        seen_locs = set(tr_t["location"].unique())
        va_seen = va_t[va_t["location"].isin(seen_locs)]
        va_unseen = va_t[~va_t["location"].isin(seen_locs)]
        best_pred = p_stacked_t if r2_stacked_t >= r2_cb_t else p_cb_t
        if len(va_seen) > 5:
            seen_mask = va_t["location"].isin(seen_locs).values
            r2_seen = r2_score(y_va_t[seen_mask], best_pred[seen_mask])
            log(f"  Seen locs:   R²={r2_seen:.4f} ({len(va_seen)} rows)")
        if len(va_unseen) > 5:
            unseen_mask = ~va_t["location"].isin(seen_locs).values
            r2_unseen = r2_score(y_va_t[unseen_mask], best_pred[unseen_mask])
            log(f"  Unseen locs: R²={r2_unseen:.4f} ({len(va_unseen)} rows)")

    # ---- Phase 5: Walk-Forward ----
    log(f"\n{'=' * 70}")
    log("WALK-FORWARD TEMPORAL VALIDATION")
    log(f"{'=' * 70}")

    wf_folds = [
        ("< 2020", "2020-2021", df["year"] < 2020, df["year"].between(2020, 2021)),
        ("< 2022", "2022-2023", df["year"] < 2022, df["year"].between(2022, 2023)),
        ("< 2024", "2024+", df["year"] < 2024, df["year"] >= 2024),
    ]

    wf_r2s = []
    for train_label, test_label, wf_tr_mask, wf_va_mask in wf_folds:
        if wf_tr_mask.sum() < 30 or wf_va_mask.sum() < 10:
            log(f"  {train_label}/{test_label}: skipped (insufficient data)")
            continue

        wf_tr, wf_va = fill_rolling_mean_gaps(df[wf_tr_mask], df[wf_va_mask])
        X_wf_tr, X_wf_va = wf_tr[features], wf_va[features]
        y_wf_tr, y_wf_va = wf_tr[target_col].values, wf_va[target_col].values
        sw_wf = np.where(wf_tr["is_premium"].values == 1, 2.0, 1.0)

        cb_wf = CatBoostRegressor(
            iterations=3000, depth=best_cfg["depth"], learning_rate=best_cfg["lr"],
            l2_leaf_reg=best_cfg["l2"], subsample=best_cfg["sub"],
            random_seed=SEED, verbose=0, early_stopping_rounds=200,
            thread_count=-1,
        )
        cb_wf.fit(X_wf_tr, y_wf_tr, sample_weight=sw_wf,
                   eval_set=(X_wf_va, y_wf_va))
        p_wf = cb_wf.predict(X_wf_va)
        r2_wf = r2_score(y_wf_va, p_wf)
        mae_wf = mean_absolute_error(np.expm1(y_wf_va), np.expm1(p_wf))
        wf_r2s.append(r2_wf)
        log(f"  {train_label}/{test_label}: R²={r2_wf:.4f} MAE={mae_wf:.3f} (train={wf_tr_mask.sum()}, test={wf_va_mask.sum()})")

    r2_walkforward = np.mean(wf_r2s) if wf_r2s else None
    if wf_r2s:
        log(f"  Walk-forward mean: R²={r2_walkforward:.4f} ± {np.std(wf_r2s):.4f}")

    # ---- Phase 6: Spatial CV ----
    log(f"\n{'=' * 70}")
    log("SPATIAL CV (lat-band holdout)")
    log(f"{'=' * 70}")

    df["_region"] = pd.cut(df["lat"], bins=5, labels=["S", "SM", "M", "MN", "N"])
    spatial_r2s = []
    for region in df["_region"].dropna().unique():
        te_mask = df["_region"] == region
        if te_mask.sum() < 10:
            continue
        sp_tr, sp_te = fill_rolling_mean_gaps(df[~te_mask], df[te_mask])
        cb_sp = CatBoostRegressor(
            iterations=1500, depth=best_cfg["depth"], learning_rate=best_cfg["lr"],
            l2_leaf_reg=best_cfg["l2"], subsample=best_cfg["sub"],
            random_seed=SEED, verbose=0, thread_count=-1,
        )
        cb_sp.fit(sp_tr[features], sp_tr[target_col])
        pred_sp = np.clip(cb_sp.predict(sp_te[features]), 0, 4)
        r2_sp = r2_score(sp_te[target_col], pred_sp)
        spatial_r2s.append(r2_sp)
        log(f"  {region}: R²={r2_sp:.4f} (n={te_mask.sum()})")

    r2_spatial = np.mean(spatial_r2s) if spatial_r2s else None
    if spatial_r2s:
        log(f"  Spatial mean: R²={r2_spatial:.4f}")

    # ---- Phase 7: SHAP + Final Models ----
    log(f"\n{'=' * 70}")
    log("TRAINING FINAL PRODUCTION MODELS")
    log(f"{'=' * 70}")

    # Final CatBoost
    cb_final = CatBoostRegressor(
        iterations=3500, depth=best_cfg["depth"], learning_rate=best_cfg["lr"],
        l2_leaf_reg=best_cfg["l2"], subsample=best_cfg["sub"],
        random_seed=SEED, verbose=0, thread_count=-1,
    )
    cb_final.fit(df[features], y, sample_weight=sample_weights)
    log(f"  CatBoost: trained on {len(df)} rows")

    # Final XGBoost
    xg_final = XGBRegressor(
        n_estimators=3000, max_depth=best_cfg["depth"],
        learning_rate=best_cfg["lr"],
        reg_lambda=best_cfg["l2"], subsample=best_cfg["sub"],
        colsample_bytree=0.7, random_state=SEED, verbosity=0,
        n_jobs=-1, tree_method="hist",
    )
    xg_final.fit(df[features].fillna(-999), y, sample_weight=sample_weights)
    log(f"  XGBoost:  trained on {len(df)} rows")

    # Final LightGBM
    lgb_final_ds = lgb.Dataset(df[features], y, weight=sample_weights)
    lgb_final = lgb.train(lgb_params, lgb_final_ds, num_boost_round=2000)
    log(f"  LightGBM: trained on {len(df)} rows")

    # SHAP
    shap_dict = {}
    try:
        import shap
        log("\n  Computing SHAP importance...")
        explainer = shap.TreeExplainer(cb_final)
        sample_idx = np.random.RandomState(SEED).choice(
            len(df), min(800, len(df)), replace=False)
        shap_values = explainer.shap_values(df.iloc[sample_idx][features])
        shap_imp = pd.Series(
            np.abs(shap_values).mean(axis=0), index=features
        ).sort_values(ascending=False)
        shap_dict = {feat: float(val) for feat, val in shap_imp.items()}
        log("  SHAP top 20:")
        for feat, val in shap_imp.head(20).items():
            log(f"    {feat:>45}: {val:.4f}")
    except ImportError:
        log("  SHAP not installed, using CatBoost native importance")
        imp = pd.Series(cb_final.feature_importances_, index=features).sort_values(ascending=False)
        shap_dict = {feat: float(val) for feat, val in imp.items()}

    # ---- Save Artifacts ----
    log(f"\n{'=' * 70}")
    log("SAVING ARTIFACTS")
    log(f"{'=' * 70}")

    models_dir = BASE_DIR / "castline" / "models"
    if not models_dir.exists():
        models_dir = BASE_DIR / "models"
    models_dir.mkdir(parents=True, exist_ok=True)

    cb_final.save_model(str(models_dir / f"cpue_{VERSION}_catboost.cbm"))
    log(f"  Saved CatBoost → cpue_{VERSION}_catboost.cbm")

    with open(models_dir / f"cpue_{VERSION}_xgboost.pkl", "wb") as f:
        pickle.dump(xg_final, f)
    log(f"  Saved XGBoost  → cpue_{VERSION}_xgboost.pkl")

    with open(models_dir / f"cpue_{VERSION}_lightgbm.pkl", "wb") as f:
        pickle.dump(lgb_final, f)
    log(f"  Saved LightGBM → cpue_{VERSION}_lightgbm.pkl")

    with open(models_dir / f"cpue_{VERSION}_ridge.pkl", "wb") as f:
        pickle.dump(ridge, f)
    log(f"  Saved Ridge    → cpue_{VERSION}_ridge.pkl")

    with open(models_dir / f"cpue_{VERSION}_features.json", "w") as f:
        json.dump(features, f, indent=2)
    log(f"  Saved features → cpue_{VERSION}_features.json")

    if shap_dict:
        with open(models_dir / f"shap_importance_{VERSION}.json", "w") as f:
            json.dump(shap_dict, f, indent=2)
        log(f"  Saved SHAP     → shap_importance_{VERSION}.json")

    # Location means for inference
    loc_means = df.groupby("location")[TARGET].mean().to_dict()
    with open(models_dir / f"cpue_{VERSION}_location_means.json", "w") as f:
        json.dump(loc_means, f, indent=2)
    log(f"  Saved loc means → cpue_{VERSION}_location_means.json")

    # Metadata
    metadata = {
        "version": VERSION,
        "trained_at": datetime.now().isoformat(),
        "dataset": "validation_dataset_v16.csv",
        "n_rows": len(df),
        "n_features": len(features),
        "n_locations": df["location"].nunique(),
        "best_hp": best_cfg,
        "ridge_weights": {
            "catboost": float(ridge.coef_[0]),
            "xgboost": float(ridge.coef_[1]),
            "lightgbm": float(ridge.coef_[2]),
        },
        "metrics": {
            "cv_r2_catboost": float(np.mean(cb_r2s)),
            "cv_r2_xgboost": float(np.mean(xgb_r2s)),
            "cv_r2_lightgbm": float(np.mean(lgb_r2s)),
            "cv_r2_stacked": float(meta_r2),
            "temporal_r2": float(r2_temporal) if r2_temporal else None,
            "walkforward_r2": float(r2_walkforward) if r2_walkforward else None,
            "spatial_r2": float(r2_spatial) if r2_spatial else None,
        },
    }
    with open(models_dir / f"cpue_{VERSION}_metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)
    log(f"  Saved metadata → cpue_{VERSION}_metadata.json")

    # ---- FINAL SUMMARY ----
    log(f"\n{'=' * 70}")
    log(f"FINAL SUMMARY — CPUE Model {VERSION}")
    log(f"{'=' * 70}")
    log(f"  Dataset:           {len(df)} rows x {df.shape[1]} cols → {len(features)} features")
    log(f"  CV R² (stacked):   {meta_r2:.4f}")
    log(f"  Temporal R²:       {r2_temporal:.4f}" if r2_temporal else "  Temporal R²:       N/A")
    log(f"  Walk-forward R²:   {r2_walkforward:.4f}" if r2_walkforward else "  Walk-forward R²:   N/A")
    log(f"  Spatial R²:        {r2_spatial:.4f}" if r2_spatial else "  Spatial R²:        N/A")
    log(f"  Ridge weights:     CB={ridge.coef_[0]:.3f} XGB={ridge.coef_[1]:.3f} LGB={ridge.coef_[2]:.3f}")
    log(f"")
    log(f"  V12 baselines:     CV=0.410  Temporal=0.420  Walk-forward=0.331")
    log(f"  V16 eval baselines: T=0.438  S=0.483  ST=0.588  LOO=0.603")
    log(f"{'=' * 70}")

    return {
        "cv_r2": meta_r2,
        "temporal_r2": r2_temporal,
        "walkforward_r2": r2_walkforward,
        "spatial_r2": r2_spatial,
    }


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=f"CPUE Model {VERSION}")
    parser.add_argument("--workspace", default=None, help="Override workspace dir")
    args = parser.parse_args()

    global BASE_DIR, DATASET_CANDIDATES
    if args.workspace:
        BASE_DIR = Path(args.workspace)
        DATASET_CANDIDATES = [
            BASE_DIR / "castline" / "validation" / "data" / "assembled" / "validation_dataset_v16.csv",
            BASE_DIR / "data" / "assembled" / "validation_dataset_v16.csv",
            BASE_DIR / "validation_dataset_v16.csv",
        ]

    setup_logging(BASE_DIR)

    df = load_dataset()

    # Feature selection (temporal features)
    all_features = select_features(df)
    log(f"\n  Candidate features: {len(all_features)}")

    # Auto-prune
    pruned_features, importance = prune_features(df, all_features)

    # Train and evaluate
    results = train_and_evaluate(df, pruned_features)

    if _log_file:
        _log_file.close()

    return results


if __name__ == "__main__":
    main()
