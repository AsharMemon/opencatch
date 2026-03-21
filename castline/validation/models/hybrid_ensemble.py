"""Hybrid Ensemble: GNN location embeddings + GBM for all 4 metrics.

Strategy:
- For TEMPORAL/SPATIAL/SPATIOTEMPORAL: Use stacking ensemble of LightGBM + XGBoost + CatBoost
  with location identity features (known location at test time)
- For LOO: Use GNN to produce location embeddings, then feed those as features into GBM
  alongside environmental features (unseen location gets embedding from graph neighbors)
- Final production model: if location seen → use full GBM; if unseen → use GNN+GBM hybrid

This file runs all 4 evaluations and reports results.
"""
from __future__ import annotations

import json
import math
import sys
import time
import warnings
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupKFold, KFold
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")

try:
    import xgboost as xgb
    _HAS_XGB = True
except ImportError:
    _HAS_XGB = False

try:
    import catboost as cb
    _HAS_CB = True
except ImportError:
    _HAS_CB = False

TARGET = "median_weight_lb"

# ─── Feature sets ───────────────────────────────────────────

ALWAYS_EXCLUDE = {"target_success_score", "tms_id", "event_id"}

LOCATION_IDENTITY = [
    "loc_enc", "trail_mean_weight", "location_mean_weight",
    "loc_rolling_3", "baseline_signal",
]

META_COLS = {
    "date", "location", "region", "block", "sat_source",
    "usgs_site_id", "event_name", "tournament_slug",
    "site_id", "observation_date", "iem_station",
    "species", "results_source", "trail", "source_mode",
    "day_number",
}


def get_feature_cols(df, include_location_identity=True):
    """Get all numeric feature columns, optionally excluding location identity."""
    exclude = ALWAYS_EXCLUDE | META_COLS | {TARGET}
    if not include_location_identity:
        exclude |= set(LOCATION_IDENTITY)

    features = []
    for col in df.columns:
        if col in exclude:
            continue
        if df[col].dtype in (np.float64, np.float32, np.int64, np.int32, float, int):
            if df[col].notna().mean() > 0.03:
                features.append(col)
    return features


def _extract_region(location: str) -> str:
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


# ─── Stacking Ensemble ─────────────────────────────────────

def build_stacking_ensemble(X_train, y_train, X_test, n_folds=5):
    """Build a stacking ensemble of LightGBM + XGBoost + CatBoost with Ridge meta-learner."""
    kf = KFold(n_splits=n_folds, shuffle=True, random_state=42)

    # Base learner configs
    lgb_params = {
        "objective": "regression", "metric": "rmse", "verbosity": -1,
        "n_estimators": 1500, "learning_rate": 0.02, "max_depth": 7,
        "num_leaves": 63, "min_child_samples": 5,
        "subsample": 0.8, "colsample_bytree": 0.6,
        "reg_alpha": 0.3, "reg_lambda": 2.0,
        "extra_trees": True,
    }

    base_learners = [("lgb", lgb_params)]

    if _HAS_XGB:
        xgb_params = {
            "objective": "reg:squarederror", "verbosity": 0,
            "n_estimators": 1500, "learning_rate": 0.02, "max_depth": 7,
            "subsample": 0.8, "colsample_bytree": 0.6,
            "reg_alpha": 0.3, "reg_lambda": 2.0,
            "tree_method": "hist",
        }
        base_learners.append(("xgb", xgb_params))

    if _HAS_CB:
        cb_params = {
            "iterations": 1500, "learning_rate": 0.02, "depth": 7,
            "l2_leaf_reg": 3.0, "verbose": 0,
            "random_seed": 42,
        }
        base_learners.append(("cb", cb_params))

    # Generate OOF predictions for stacking
    n_models = len(base_learners)
    oof_preds = np.zeros((len(X_train), n_models))
    test_preds = np.zeros((len(X_test), n_models))

    for m_idx, (name, params) in enumerate(base_learners):
        fold_test_preds = []
        for fold_idx, (tr_idx, val_idx) in enumerate(kf.split(X_train)):
            X_tr, X_val = X_train[tr_idx], X_train[val_idx]
            y_tr, y_val = y_train[tr_idx], y_train[val_idx]

            if name == "lgb":
                model = lgb.LGBMRegressor(**params)
                model.fit(X_tr, y_tr, eval_set=[(X_val, y_val)],
                         callbacks=[lgb.early_stopping(50, verbose=False)])
            elif name == "xgb":
                model = xgb.XGBRegressor(**params)
                model.fit(X_tr, y_tr, eval_set=[(X_val, y_val)],
                         verbose=False)
            elif name == "cb":
                model = cb.CatBoostRegressor(**params)
                model.fit(X_tr, y_tr, eval_set=(X_val, y_val),
                         early_stopping_rounds=50)

            oof_preds[val_idx, m_idx] = model.predict(X_val)
            fold_test_preds.append(model.predict(X_test))

        test_preds[:, m_idx] = np.mean(fold_test_preds, axis=0)

    # Meta-learner: Ridge regression on OOF predictions
    meta = Ridge(alpha=1.0)
    meta.fit(oof_preds, y_train)
    final_pred = meta.predict(test_preds)

    return final_pred, meta.coef_


def train_single_lgb(X_train, y_train, X_test=None, params=None):
    """Train a single LightGBM model. Returns (model, predictions)."""
    default_params = {
        "objective": "regression", "metric": "rmse", "verbosity": -1,
        "n_estimators": 1500, "learning_rate": 0.02, "max_depth": 7,
        "num_leaves": 63, "min_child_samples": 5,
        "subsample": 0.8, "colsample_bytree": 0.6,
        "reg_alpha": 0.3, "reg_lambda": 2.0,
        "extra_trees": True,
    }
    if params:
        default_params.update(params)

    model = lgb.LGBMRegressor(**default_params)
    model.fit(X_train, y_train)
    preds = model.predict(X_test) if X_test is not None else None
    return model, preds


# ─── LOO with target encoding ──────────────────────────────

def add_loo_target_encoding(df, train_mask, regularization=10):
    """Add leave-one-out target encoding for location clusters.

    For each event, compute:
    1. Regularized location mean (blended with global mean)
    2. Cluster-level mean (locations grouped by features)
    3. Ecoregion-level mean
    """
    df = df.copy()
    train_df = df[train_mask]
    global_mean = train_df[TARGET].mean()

    # 1. Regularized location mean (LOO within training set)
    loc_stats = train_df.groupby("location").agg(
        loc_sum=(TARGET, "sum"),
        loc_count=(TARGET, "count"),
    ).reset_index()
    loc_stats["loc_mean_reg"] = (
        (loc_stats["loc_sum"] + regularization * global_mean) /
        (loc_stats["loc_count"] + regularization)
    )
    loc_mean_map = dict(zip(loc_stats["location"], loc_stats["loc_mean_reg"]))
    df["_loo_loc_mean"] = df["location"].map(loc_mean_map).fillna(global_mean)

    # 2. Cluster locations by features and compute cluster means
    cluster_features = ["lat", "lon", "area_acres", "max_depth_ft", "is_lake"]
    avail_cf = [f for f in cluster_features if f in df.columns]
    if len(avail_cf) >= 3:
        from sklearn.cluster import KMeans
        loc_df = train_df.groupby("location").agg(
            **{f: (f, "first") for f in avail_cf},
            cluster_target=(TARGET, "mean"),
        ).reset_index()

        imp = SimpleImputer(strategy="median")
        scaler = StandardScaler()
        X_cluster = scaler.fit_transform(imp.fit_transform(loc_df[avail_cf].values))

        n_clusters = min(15, len(loc_df) // 5)
        km = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        loc_df["_cluster"] = km.fit_predict(X_cluster)

        cluster_means = loc_df.groupby("_cluster")["cluster_target"].mean().to_dict()
        loc_cluster_map = dict(zip(loc_df["location"], loc_df["_cluster"]))

        # For test locations: assign to nearest cluster
        def assign_cluster(row):
            loc = row["location"]
            if loc in loc_cluster_map:
                return loc_cluster_map[loc]
            # Assign based on feature distance
            feat_vals = np.array([[row.get(f, np.nan) for f in avail_cf]])
            feat_vals = scaler.transform(imp.transform(feat_vals))
            return km.predict(feat_vals)[0]

        df["_cluster_id"] = df.apply(assign_cluster, axis=1)
        df["_cluster_mean"] = df["_cluster_id"].map(cluster_means).fillna(global_mean)

    # 3. Region-level mean
    df["_region"] = df["location"].apply(_extract_region)
    region_means = train_df.copy()
    region_means["_region"] = region_means["location"].apply(_extract_region)
    region_mean_map = region_means.groupby("_region")[TARGET].mean().to_dict()
    df["_region_mean"] = df["_region"].map(region_mean_map).fillna(global_mean)

    return df


# ─── Evaluation functions ──────────────────────────────────

def temporal_holdout(df, features, train_end=2023, test_start=2024):
    """Temporal holdout with stacking ensemble."""
    train = df[df["year"] <= train_end].dropna(subset=[TARGET])
    test = df[df["year"] >= test_start].dropna(subset=[TARGET])

    if len(train) < 20 or len(test) < 10:
        return {"r2": float("nan")}

    imp = SimpleImputer(strategy="median")
    X_train = imp.fit_transform(train[features].values)
    y_train = train[TARGET].values
    X_test = imp.transform(test[features].values)
    y_test = test[TARGET].values

    preds, weights = build_stacking_ensemble(X_train, y_train, X_test)
    r2 = r2_score(y_test, preds)
    rmse = math.sqrt(mean_squared_error(y_test, preds))
    mae = mean_absolute_error(y_test, preds)

    return {
        "r2": round(r2, 4),
        "rmse": round(rmse, 4),
        "mae": round(mae, 4),
        "n_train": len(train),
        "n_test": len(test),
        "ensemble_weights": weights.tolist() if weights is not None else None,
    }


def spatial_holdout(df, features):
    """Spatial holdout: hold out each region."""
    df = df.copy()
    df["region"] = df["location"].apply(_extract_region)

    results = []
    for region in sorted(df["region"].unique()):
        test = df[df["region"] == region].dropna(subset=[TARGET])
        train = df[df["region"] != region].dropna(subset=[TARGET])
        if len(test) < 10:
            continue

        imp = SimpleImputer(strategy="median")
        X_train = imp.fit_transform(train[features].values)
        y_train = train[TARGET].values
        X_test = imp.transform(test[features].values)
        y_test = test[TARGET].values

        preds, _ = build_stacking_ensemble(X_train, y_train, X_test)
        r2 = r2_score(y_test, preds)
        results.append({"region": region, "r2": round(r2, 4), "n": len(test)})

    mean_r2 = np.mean([r["r2"] for r in results])
    return {"r2": round(mean_r2, 4), "per_region": results}


def spatiotemporal_blocked(df, features, n_splits=5):
    """Spatiotemporal blocked CV with stacking ensemble."""
    df = df.copy().dropna(subset=[TARGET])
    df["region"] = df["location"].apply(_extract_region)
    df["block"] = df["region"] + "_" + df["year"].astype(str)

    groups = df["block"].values
    X = df[features].values
    y = df[TARGET].values

    imp = SimpleImputer(strategy="median")
    X = imp.fit_transform(X)

    n_unique = len(np.unique(groups))
    actual_splits = min(n_splits, n_unique)

    gkf = GroupKFold(n_splits=actual_splits)
    r2s = []
    for train_idx, test_idx in gkf.split(X, y, groups):
        preds, _ = build_stacking_ensemble(
            X[train_idx], y[train_idx], X[test_idx], n_folds=3
        )
        r2s.append(r2_score(y[test_idx], preds))

    return {
        "r2": round(float(np.mean(r2s)), 4),
        "per_fold": [round(r, 4) for r in r2s],
        "std": round(float(np.std(r2s)), 4),
    }


def loo_evaluation(df, features, top_n=15):
    """Leave-one-location-out with target encoding + stacking ensemble."""
    df = df.copy().dropna(subset=[TARGET])
    loc_counts = df["location"].value_counts()

    all_true, all_pred = [], []
    per_location = []

    for loc in loc_counts.head(top_n).index:
        train_mask = df["location"] != loc
        test_mask = df["location"] == loc

        # Add LOO target encoding
        df_enc = add_loo_target_encoding(df, train_mask, regularization=10)

        # Extended feature list with target encoding columns
        ext_features = features + [
            c for c in ["_loo_loc_mean", "_cluster_mean", "_region_mean"]
            if c in df_enc.columns
        ]
        ext_features = [f for f in ext_features if f in df_enc.columns]

        train_data = df_enc[train_mask]
        test_data = df_enc[test_mask]

        imp = SimpleImputer(strategy="median")
        X_train = imp.fit_transform(train_data[ext_features].values)
        y_train = train_data[TARGET].values
        X_test = imp.transform(test_data[ext_features].values)
        y_test = test_data[TARGET].values

        # Use stacking for LOO too
        preds, _ = build_stacking_ensemble(X_train, y_train, X_test, n_folds=3)

        r2 = r2_score(y_test, preds) if len(y_test) > 1 else float("nan")
        all_true.extend(y_test.tolist())
        all_pred.extend(preds.tolist())

        per_location.append({
            "location": loc,
            "r2": round(r2, 4),
            "n": len(test_data),
            "actual_mean": round(float(y_test.mean()), 2),
            "pred_mean": round(float(preds.mean()), 2),
        })

    overall_r2 = r2_score(all_true, all_pred)
    return {
        "r2": round(overall_r2, 4),
        "median_r2": round(float(np.median([r["r2"] for r in per_location])), 4),
        "per_location": per_location,
    }


# ─── Main ──────────────────────────────────────────────────

def run_full_evaluation(dataset_path):
    """Run all 4 evaluations."""
    print("=" * 70)
    print("HYBRID ENSEMBLE EVALUATION")
    print("=" * 70)

    df = pd.read_csv(dataset_path)
    if "year" not in df.columns and "date" in df.columns:
        df["year"] = pd.to_datetime(df["date"]).dt.year

    # Apply feature engineering
    try:
        import importlib.util
        tv6_path = Path(__file__).resolve().parent.parent / "train_v6.py"
        spec = importlib.util.spec_from_file_location("train_v6", str(tv6_path))
        mod = importlib.util.module_from_spec(spec)
        mod.__name__ = "train_v6"
        sys.modules["train_v6"] = mod
        spec.loader.exec_module(mod)
        df = mod.add_engineered_features(df)
        print(f"Feature engineering: {len(df.columns)} columns")
    except Exception as e:
        print(f"Warning: {e}")

    full_features = get_feature_cols(df, include_location_identity=True)
    loo_features = get_feature_cols(df, include_location_identity=False)

    print(f"Dataset: {len(df)} rows, {df['location'].nunique()} locations")
    print(f"Full features: {len(full_features)}")
    print(f"LOO features: {len(loo_features)}")
    print(f"Base learners: LightGBM" +
          (", XGBoost" if _HAS_XGB else "") +
          (", CatBoost" if _HAS_CB else ""))

    results = {}

    # 1. Temporal holdout
    print("\n" + "=" * 50)
    print("1. TEMPORAL HOLDOUT")
    print("=" * 50)
    t0 = time.time()
    th = temporal_holdout(df, full_features)
    results["temporal"] = th
    print(f"  R2 = {th['r2']:.4f}  RMSE = {th.get('rmse', 'n/a')}  [{time.time()-t0:.1f}s]")
    if th.get("ensemble_weights"):
        names = ["LightGBM"] + (["XGBoost"] if _HAS_XGB else []) + (["CatBoost"] if _HAS_CB else [])
        for n, w in zip(names, th["ensemble_weights"]):
            print(f"    {n}: weight={w:.3f}")

    # 2. Spatial holdout
    print("\n" + "=" * 50)
    print("2. SPATIAL HOLDOUT")
    print("=" * 50)
    t0 = time.time()
    sh = spatial_holdout(df, full_features)
    results["spatial"] = sh
    print(f"  R2 = {sh['r2']:.4f}  [{time.time()-t0:.1f}s]")
    for r in sh.get("per_region", []):
        print(f"    {r['region']}: R2={r['r2']:.4f} (n={r['n']})")

    # 3. Spatiotemporal blocked
    print("\n" + "=" * 50)
    print("3. SPATIOTEMPORAL BLOCKED")
    print("=" * 50)
    t0 = time.time()
    st = spatiotemporal_blocked(df, full_features)
    results["spatiotemporal"] = st
    print(f"  R2 = {st['r2']:.4f} ± {st.get('std', 0):.4f}  [{time.time()-t0:.1f}s]")

    # 4. LOO
    print("\n" + "=" * 50)
    print("4. LEAVE-ONE-LOCATION-OUT")
    print("=" * 50)
    t0 = time.time()
    loo = loo_evaluation(df, loo_features, top_n=15)
    results["loo"] = loo
    print(f"  R2 = {loo['r2']:.4f}  Median = {loo.get('median_r2', 'n/a')}  [{time.time()-t0:.1f}s]")
    for r in loo.get("per_location", []):
        status = "OK" if r["r2"] > 0 else "BAD"
        print(f"    [{status:3s}] {r['location'][:45]:45s} R2={r['r2']:7.4f} actual={r['actual_mean']:5.1f} pred={r['pred_mean']:5.1f}")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for name, res in results.items():
        r2 = res.get("r2", "n/a")
        target = "0.90"
        gap = f"gap={0.90 - r2:+.4f}" if isinstance(r2, float) else ""
        print(f"  {name:20s}: R2={r2}  target={target}  {gap}")

    # Save
    out_path = (
        Path(__file__).resolve().parent.parent
        / "artifacts" / "hybrid_ensemble_results.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {out_path}")

    return results


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else str(
        Path(__file__).resolve().parent.parent
        / "data" / "assembled" / "validation_dataset_v6.csv"
    )
    run_full_evaluation(path)
