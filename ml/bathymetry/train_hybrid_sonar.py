#!/usr/bin/env python3
"""
OpenCatch — Hybrid Sonar + Spectral Trainer with Lake Context

This script is designed for the honest cross-lake benchmark stored in
`/data/sonar_s2/sonar_s2_split.parquet`. The key improvement over the older
spectral-only trainer is that we separate lake-scale depth from within-lake
shape:

1. Build lake-level context from spectral and water-quality summaries
2. Predict lake-scale max/mean depth priors from those summaries
3. Train point-level models with both raw features and within-lake centered
   features to predict:
   - direct depth in metres
   - relative depth fraction within the lake
4. Blend direct and prior-scaled relative predictions by water-clarity class

This is still spectral-first, so it will not solve every turbid-lake failure by
itself, but it is a much more honest baseline for the "all lakes" problem than
training on raw cross-lake reflectance alone.

Usage:
    python train_hybrid_sonar.py \
        --data /data/sonar_s2/sonar_s2_split.parquet \
        --output /data/models/hybrid_sonar \
        --device cuda
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import pickle
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor, RandomForestRegressor
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("hybrid_sonar")

EPS = 1e-6

POINT_BASE_FEATURES = [
    # Spectral bands
    "blue", "green", "red", "nir",
    "log_blue", "log_green", "log_red", "log_nir",
    "stumpf_ratio",
    "lyzenga_bg", "lyzenga_br", "lyzenga_gr",
    "ndwi", "mndwi", "ndvi",
    "blue_green_ratio", "blue_red_ratio", "green_red_ratio",
    "turbidity_index", "cdom_proxy", "ndti",
    "rel_blue", "rel_green", "rel_red",
    "blue_x_green", "blue_x_red", "green_x_red",
    "blue_sq", "green_sq",
    "blue_minus_green", "green_minus_red", "red_minus_nir",
    "rededge1", "rededge2", "rededge3", "nir08",
    "swir16", "swir22", "cdom_rededge", "fai",
    "temporal_match_score", "lat", "lon",
    # Morphometric features (from 3D-LAKES / HydroLAKES spatial join)
    "area_km2", "log_area_km2", "equiv_radius_m",
    "slope_mean", "slope_std", "slope_max",
    "curvature_mean", "convexity",
    "hypsometric_integral", "power_exponent",
    "shoreline_dev_proxy", "shape_factor", "volume_dev",
    "area_ratio", "relative_depth", "depth_area_ratio",
    "abs_lat", "climate_zone",
    # 3D-LAKES depth priors (equivalent to donor priors)
    "max_depth_m", "mean_depth_m", "log_depth",
    # A-E curve shape features
    "n_ae_points", "elev_range_m",
    "area_at_d25", "area_at_d50", "area_at_d75",
]

CENTER_FEATURES = [
    "blue", "green", "red", "nir", "stumpf_ratio",
    "blue_green_ratio", "blue_red_ratio", "green_red_ratio",
    "turbidity_index", "cdom_proxy", "ndti", "ndwi", "mndwi",
]

LAKE_CONTEXT_BASE = [
    "blue", "green", "red", "nir", "stumpf_ratio",
    "blue_green_ratio", "blue_red_ratio", "green_red_ratio",
    "turbidity_index", "cdom_proxy", "ndti", "ndwi", "mndwi",
    "rededge1", "rededge2", "rededge3", "nir08", "swir16", "swir22",
    "fai", "temporal_match_score", "lat", "lon",
]

PHYSICS_CENTER_FEATURES = [
    "phys_secchi_depth_m",
    "phys_kd_490",
    "phys_log_chl",
    "phys_log_a_cdom",
    "phys_turb_blend",
    "phys_turb_ndti",
    "phys_turb_class",
    "phys_water_type",
    "phys_clear_score",
    "phys_moderate_score",
    "phys_turbid_score",
    "phys_tannin_score",
    "phys_optical_depth_mean",
    "phys_od_ratio_bg",
    "phys_od_ratio_br",
    "phys_r_bottom_mean",
    "phys_bottom_bg_ratio",
    "phys_bottom_gr_ratio",
]

SPATIAL_POINT_FEATURES = [
    "coord_x_m",
    "coord_y_m",
    "coord_pc1_m",
    "coord_pc2_m",
    "coord_pc1_norm",
    "coord_pc2_norm",
    "coord_radius_norm",
    "coord_center_proximity",
]

SPATIAL_LAKE_FEATURES = [
    "lake_axis_major_m",
    "lake_axis_minor_m",
    "lake_aspect_ratio",
    "lake_radius95_m",
]

DEPTH_BINS = [
    (0, 2, "0-2m"),
    (2, 5, "2-5m"),
    (5, 10, "5-10m"),
    (10, 20, "10-20m"),
    (20, 50, "20-50m"),
]


def compute_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    valid = np.isfinite(y_true) & np.isfinite(y_pred)
    if valid.sum() < 2:
        return {"n": int(valid.sum()), "rmse": math.nan, "mae": math.nan, "r2": math.nan}
    t = y_true[valid]
    p = y_pred[valid]
    return {
        "n": int(valid.sum()),
        "rmse": float(np.sqrt(mean_squared_error(t, p))),
        "mae": float(mean_absolute_error(t, p)),
        "r2": float(r2_score(t, p)),
        "bias": float(np.mean(p - t)),
        "median_ae": float(np.median(np.abs(p - t))),
        "p90_ae": float(np.percentile(np.abs(p - t), 90)),
    }


def compute_depth_bins(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for lo, hi, label in DEPTH_BINS:
        mask = (y_true >= lo) & (y_true < hi)
        result[label] = compute_metrics(y_true[mask], y_pred[mask]) if mask.sum() >= 5 else {"n": int(mask.sum())}
    return result


def available_features(df: pd.DataFrame, names: Iterable[str]) -> list[str]:
    return [name for name in names if name in df.columns]


def physics_feature_columns(df: pd.DataFrame) -> list[str]:
    return sorted(
        col for col in df.columns
        if col.startswith("phys_") and pd.api.types.is_numeric_dtype(df[col])
    )


def add_spatial_context(df: pd.DataFrame) -> pd.DataFrame:
    if not {"lake_id", "lat", "lon"}.issubset(df.columns):
        return df

    pieces: list[pd.DataFrame] = []
    for _, group in df.groupby("lake_id", sort=False):
        part = group.copy()
        lat0 = float(part["lat"].mean())
        lon0 = float(part["lon"].mean())
        meters_per_lon = 111320.0 * math.cos(math.radians(lat0))
        x = (part["lon"].values - lon0) * meters_per_lon
        y = (part["lat"].values - lat0) * 110540.0
        coords = np.column_stack([x, y])

        if len(part) >= 3 and np.isfinite(coords).all():
            cov = np.cov(coords, rowvar=False)
            eigvals, eigvecs = np.linalg.eigh(cov)
            order = np.argsort(eigvals)[::-1]
            eigvals = eigvals[order]
            eigvecs = eigvecs[:, order]
            pcs = coords @ eigvecs
        else:
            eigvals = np.array([np.var(x), np.var(y)])
            pcs = coords

        scale1 = float(np.percentile(np.abs(pcs[:, 0]), 95)) + EPS
        scale2 = float(np.percentile(np.abs(pcs[:, 1]), 95)) + EPS
        radius = np.sqrt((pcs[:, 0] / scale1) ** 2 + (pcs[:, 1] / scale2) ** 2)

        part["coord_x_m"] = x
        part["coord_y_m"] = y
        part["coord_pc1_m"] = pcs[:, 0]
        part["coord_pc2_m"] = pcs[:, 1]
        part["coord_pc1_norm"] = pcs[:, 0] / scale1
        part["coord_pc2_norm"] = pcs[:, 1] / scale2
        part["coord_radius_norm"] = radius
        part["coord_center_proximity"] = np.clip(1.0 - np.minimum(radius, 1.5) / 1.5, 0.0, 1.0)

        major = math.sqrt(max(float(eigvals[0]), 0.0)) * 4.0 + EPS
        minor = math.sqrt(max(float(eigvals[1]), 0.0)) * 4.0 + EPS
        part["lake_axis_major_m"] = major
        part["lake_axis_minor_m"] = minor
        part["lake_aspect_ratio"] = major / minor
        part["lake_radius95_m"] = float(np.percentile(np.sqrt(x ** 2 + y ** 2), 95))
        pieces.append(part)

    return pd.concat(pieces, ignore_index=True) if pieces else df


def build_lake_context(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float]]:
    df = df.copy()

    agg_features = (
        available_features(df, LAKE_CONTEXT_BASE + SPATIAL_LAKE_FEATURES)
        + physics_feature_columns(df)
    )
    grouped = df.groupby("lake_id", sort=False)

    agg_frames = []
    for stat_name, func in [("mean", "mean"), ("median", "median"), ("std", "std")]:
        part = grouped[agg_features].agg(func)
        part.columns = [f"{col}_{stat_name}" for col in part.columns]
        agg_frames.append(part)

    lake_ctx = pd.concat(agg_frames, axis=1).reset_index()
    lake_ctx["lake_point_count"] = grouped.size().values
    lake_ctx["lake_lat_mean"] = grouped["lat"].mean().values if "lat" in df.columns else 0.0
    lake_ctx["lake_lon_mean"] = grouped["lon"].mean().values if "lon" in df.columns else 0.0

    lake_targets = grouped["depth_m"].agg(
        lake_max_depth_m="max",
        lake_mean_depth_m="mean",
        lake_median_depth_m="median",
    ).reset_index()

    if "split" in df.columns:
        lake_split = grouped["split"].agg(lambda s: s.mode().iloc[0]).reset_index()
        lake_ctx = lake_ctx.merge(lake_split, on="lake_id", how="left")

    lake_ctx = lake_ctx.merge(lake_targets, on="lake_id", how="left")

    secchi_col = "phys_secchi_depth_m_mean"
    turb_col = "turbidity_index_mean"
    q1 = math.nan
    q2 = math.nan

    if secchi_col in lake_ctx.columns:
        secchi_fallback = float(lake_ctx[secchi_col].median()) if lake_ctx[secchi_col].notna().any() else 1.5

        def classify_secchi(secchi: float) -> str:
            if secchi >= 3.0:
                return "clear"
            if secchi >= 1.0:
                return "moderate"
            return "turbid"

        lake_ctx["clarity_class"] = lake_ctx[secchi_col].fillna(secchi_fallback).map(classify_secchi)
        q1 = 1.0
        q2 = 3.0
    else:
        train_turb = lake_ctx.loc[lake_ctx["split"] == "train", turb_col] if turb_col in lake_ctx.columns else pd.Series(dtype=float)
        q1 = float(train_turb.quantile(0.33)) if len(train_turb) else 0.02
        q2 = float(train_turb.quantile(0.66)) if len(train_turb) else 0.05

        def classify_turbidity(turbidity: float) -> str:
            if turbidity <= q1:
                return "clear"
            if turbidity <= q2:
                return "moderate"
            return "turbid"

        lake_ctx["clarity_class"] = lake_ctx[turb_col].fillna(q2).map(classify_turbidity) if turb_col in lake_ctx.columns else "moderate"

    center_features = available_features(df, CENTER_FEATURES + PHYSICS_CENTER_FEATURES)
    feature_parts: dict[str, pd.Series] = {}
    for feature in center_features:
        mean_col = f"{feature}_mean"
        std_col = f"{feature}_std"
        median_col = f"{feature}_median"
        lookup = lake_ctx.set_index("lake_id")[[mean_col, std_col, median_col]]
        lake_mean = df["lake_id"].map(lookup[mean_col])
        lake_median = df["lake_id"].map(lookup[median_col])
        centered = df[feature] - lake_median
        feature_parts[f"{feature}_lake_mean"] = lake_mean
        feature_parts[f"{feature}_lake_median"] = lake_median
        feature_parts[f"{feature}_centered"] = centered
        feature_parts[f"{feature}_z"] = centered / (df["lake_id"].map(lookup[std_col]).fillna(0) + EPS)

    if feature_parts:
        df = pd.concat([df, pd.DataFrame(feature_parts, index=df.index)], axis=1)

    df = df.merge(
        lake_ctx[["lake_id", "clarity_class", "lake_point_count", "lake_max_depth_m", "lake_mean_depth_m"]],
        on="lake_id",
        how="left",
    )
    return df, lake_ctx, {"clarity_q33": q1, "clarity_q66": q2, "clarity_source": "secchi" if secchi_col in lake_ctx.columns else "turbidity_quantiles"}


def fill_missing(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    df = df.copy()
    for col in columns:
        if col not in df.columns:
            df[col] = 0.0
        if pd.api.types.is_numeric_dtype(df[col]):
            median = df[col].median() if df[col].notna().any() else 0.0
            df[col] = df[col].fillna(median)
    return df


def get_tree_models(device: str, model_names: Iterable[str] | None = None) -> dict[str, object]:
    models: dict[str, object] = {
        "hist_gbr": HistGradientBoostingRegressor(
            learning_rate=0.05,
            max_depth=8,
            max_iter=500,
            min_samples_leaf=20,
            l2_regularization=0.1,
            random_state=42,
        ),
        "extra_trees": ExtraTreesRegressor(
            n_estimators=700,
            max_depth=24,
            min_samples_leaf=3,
            max_features=0.5,
            n_jobs=-1,
            random_state=42,
        ),
        "rf": RandomForestRegressor(
            n_estimators=600,
            max_depth=22,
            min_samples_leaf=3,
            max_features=0.45,
            n_jobs=-1,
            random_state=42,
        ),
    }

    try:
        import xgboost as xgb

        models["xgboost"] = xgb.XGBRegressor(
            n_estimators=900,
            max_depth=8,
            learning_rate=0.03,
            subsample=0.8,
            colsample_bytree=0.7,
            reg_alpha=0.05,
            reg_lambda=1.0,
            min_child_weight=5,
            objective="reg:squarederror",
            tree_method="hist",
            device="cuda" if device == "cuda" else "cpu",
            random_state=42,
        )
    except Exception:
        log.warning("xgboost unavailable; using sklearn-only model set")

    if model_names is not None:
        wanted = set(model_names)
        models = {name: model for name, model in models.items() if name in wanted}

    return models


def compute_depth_weights(y: np.ndarray, floor: float = 1.0) -> np.ndarray:
    """Compute inverse-frequency depth-bin weights so deep points get more influence.

    Weight = max(floor, depth / 5.0) — so 20m+ points get 4x+ influence.
    Then re-weight by inverse bin frequency for balanced representation.
    """
    weights = np.maximum(floor, y / 5.0)
    # Also add inverse-frequency rebalancing per depth bin
    bin_edges = [0, 2, 5, 10, 20, 60]
    bin_idx = np.digitize(y, bin_edges) - 1
    bin_idx = np.clip(bin_idx, 0, len(bin_edges) - 2)
    for i in range(len(bin_edges) - 1):
        mask = bin_idx == i
        if mask.sum() > 0:
            freq_weight = len(y) / (len(bin_edges) * mask.sum())
            weights[mask] *= freq_weight
    return weights


def fit_best_regressor(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_val: np.ndarray,
    y_val: np.ndarray,
    device: str,
    label: str,
    model_names: Iterable[str] | None = None,
    sample_weight: np.ndarray | None = None,
) -> tuple[str, object, dict[str, dict[str, float]]]:
    results: dict[str, dict[str, float]] = {}
    best_name = ""
    best_model = None
    best_rmse = float("inf")

    for name, model in get_tree_models(device, model_names=model_names).items():
        log.info(f"Training {label}:{name} ...")
        if sample_weight is not None:
            try:
                model.fit(X_train, y_train, sample_weight=sample_weight)
            except TypeError:
                log.warning(f"  {name} does not support sample_weight, training without")
                model.fit(X_train, y_train)
        else:
            model.fit(X_train, y_train)
        pred = model.predict(X_val)
        metrics = compute_metrics(y_val, pred)
        results[name] = metrics
        log.info(
            f"  {label}:{name} -> RMSE={metrics['rmse']:.3f}m "
            f"MAE={metrics['mae']:.3f}m R2={metrics['r2']:.3f}"
        )
        if metrics["rmse"] < best_rmse:
            best_rmse = metrics["rmse"]
            best_name = name
            best_model = model

    if best_model is None:
        raise RuntimeError(f"No model trained successfully for {label}")
    return best_name, best_model, results


def tune_blend_by_clarity(
    val_df: pd.DataFrame,
    direct_pred: np.ndarray,
    rel_pred: np.ndarray,
) -> dict[str, float]:
    alphas: dict[str, float] = {}
    for clarity in ["clear", "moderate", "turbid"]:
        mask = val_df["clarity_class"] == clarity
        if mask.sum() < 10:
            alphas[clarity] = 0.5
            continue
        best_alpha = 0.5
        best_rmse = float("inf")
        y_true = val_df.loc[mask, "depth_m"].values
        d = direct_pred[mask]
        r = rel_pred[mask]
        for alpha in np.linspace(0.0, 1.0, 11):
            pred = alpha * d + (1.0 - alpha) * r
            rmse = compute_metrics(y_true, pred)["rmse"]
            if rmse < best_rmse:
                best_rmse = rmse
                best_alpha = float(alpha)
        alphas[clarity] = best_alpha
    return alphas


def apply_clarity_blend(
    df: pd.DataFrame,
    direct_pred: np.ndarray,
    rel_pred: np.ndarray,
    alphas: dict[str, float],
) -> np.ndarray:
    out = np.zeros(len(df), dtype=np.float32)
    for clarity in ["clear", "moderate", "turbid"]:
        mask = (df["clarity_class"] == clarity).values
        alpha = alphas.get(clarity, 0.5)
        out[mask] = alpha * direct_pred[mask] + (1.0 - alpha) * rel_pred[mask]
    return out


def build_similar_lake_priors(
    lake_ctx: pd.DataFrame,
    feature_cols: list[str],
    k: int = 5,
) -> pd.DataFrame:
    train_lakes = lake_ctx[lake_ctx["split"] == "train"].copy().reset_index(drop=True)
    if train_lakes.empty:
        return lake_ctx

    scaler = StandardScaler()
    X_train = scaler.fit_transform(train_lakes[feature_cols].values)
    nn = NearestNeighbors(n_neighbors=min(k + 1, len(train_lakes)), metric="euclidean")
    nn.fit(X_train)

    donor_rows = []
    for _, row in lake_ctx.iterrows():
        query = scaler.transform(np.asarray([row[feature_cols].values], dtype=np.float64))
        n_neighbors = min(k + (1 if row["split"] == "train" else 0), len(train_lakes))
        dists, inds = nn.kneighbors(query, n_neighbors=n_neighbors)
        cand = train_lakes.iloc[inds[0]].copy()
        cand["dist"] = dists[0]
        if row["split"] == "train":
            cand = cand[cand["lake_id"] != row["lake_id"]]
        cand = cand.head(k)
        if cand.empty:
            cand = train_lakes.head(min(k, len(train_lakes))).copy()
            cand["dist"] = 1.0
        weights = 1.0 / (cand["dist"].values + 1e-3)
        weights = weights / weights.sum()
        donor_rows.append(
            {
                "lake_id": row["lake_id"],
                "donor_mean_depth": float(np.sum(weights * cand["lake_mean_depth_m"].values)),
                "donor_max_depth": float(np.sum(weights * cand["lake_max_depth_m"].values)),
                "donor_median_depth": float(np.sum(weights * cand["lake_median_depth_m"].values)),
                "donor_distance": float(cand["dist"].values[0]),
            }
        )

    donor_df = pd.DataFrame(donor_rows)
    return lake_ctx.merge(donor_df, on="lake_id", how="left")


def tune_two_source_blend(
    y_true: np.ndarray,
    primary: np.ndarray,
    secondary: np.ndarray,
) -> tuple[float, np.ndarray]:
    best_alpha = 1.0
    best_pred = primary
    best_rmse = compute_metrics(y_true, primary)["rmse"]
    for alpha in np.linspace(0.0, 1.0, 11):
        pred = alpha * primary + (1.0 - alpha) * secondary
        rmse = compute_metrics(y_true, pred)["rmse"]
        if rmse < best_rmse:
            best_alpha = float(alpha)
            best_pred = pred
            best_rmse = rmse
    return best_alpha, best_pred


def train_range_experts(
    train_df: pd.DataFrame,
    val_df: pd.DataFrame,
    test_df: pd.DataFrame,
    point_feature_cols: list[str],
    device: str,
    base_val_pred: np.ndarray,
    base_test_pred: np.ndarray,
    output_dir: Path,
) -> tuple[np.ndarray, np.ndarray, dict[str, object]]:
    specs = {
        "shallow": (0.0, 6.0),
        "mid": (3.0, 18.0),
        "deep": (10.0, 60.0),
    }
    val_preds: dict[str, np.ndarray] = {}
    test_preds: dict[str, np.ndarray] = {}
    metrics: dict[str, dict[str, dict[str, float]]] = {}

    for name, (lo, hi) in specs.items():
        subset = train_df[(train_df["depth_m"] >= lo) & (train_df["depth_m"] < hi)].copy()
        if len(subset) < 1000:
            continue
        model_name, model, model_metrics = fit_best_regressor(
            subset[point_feature_cols].values,
            subset["depth_m"].values,
            val_df[point_feature_cols].values,
            val_df["depth_m"].values,
            device=device,
            label=f"stage2_{name}_expert",
            model_names=["hist_gbr", "xgboost"],
        )
        save_model(model, output_dir / f"stage2_{name}_expert_{model_name}.pkl")
        metrics[name] = model_metrics
        val_preds[name] = np.clip(model.predict(val_df[point_feature_cols].values), 0, 60)
        test_preds[name] = np.clip(model.predict(test_df[point_feature_cols].values), 0, 60)

    if {"shallow", "mid", "deep"} - set(val_preds):
        return base_val_pred, base_test_pred, {"thresholds": None, "metrics": metrics}

    best = {
        "rmse": compute_metrics(val_df["depth_m"].values, base_val_pred)["rmse"],
        "t1": None,
        "t2": None,
        "val_pred": base_val_pred,
        "test_pred": base_test_pred,
    }
    for t1 in [3.0, 4.0, 5.0, 6.0, 7.0]:
        for t2 in [10.0, 12.0, 14.0, 16.0, 18.0]:
            if t2 <= t1:
                continue
            val_pred = np.where(
                base_val_pred < t1,
                val_preds["shallow"],
                np.where(base_val_pred < t2, val_preds["mid"], val_preds["deep"]),
            )
            rmse = compute_metrics(val_df["depth_m"].values, val_pred)["rmse"]
            if rmse < best["rmse"]:
                test_pred = np.where(
                    base_test_pred < t1,
                    test_preds["shallow"],
                    np.where(base_test_pred < t2, test_preds["mid"], test_preds["deep"]),
                )
                best = {
                    "rmse": rmse,
                    "t1": float(t1),
                    "t2": float(t2),
                    "val_pred": val_pred,
                    "test_pred": test_pred,
                }

    return best["val_pred"], best["test_pred"], {
        "thresholds": {"t1": best["t1"], "t2": best["t2"]},
        "metrics": metrics,
    }


def fit_meta_stack(
    y_val: np.ndarray,
    val_features: dict[str, np.ndarray],
    test_features: dict[str, np.ndarray],
) -> tuple[np.ndarray, np.ndarray, dict[str, float]]:
    feature_names = list(val_features.keys())
    X_val = np.column_stack([val_features[name] for name in feature_names])
    X_test = np.column_stack([test_features[name] for name in feature_names])
    model = LinearRegression(positive=True)
    model.fit(X_val, y_val)
    val_pred = np.clip(model.predict(X_val), 0, 60)
    test_pred = np.clip(model.predict(X_test), 0, 60)
    weights = {name: float(weight) for name, weight in zip(feature_names, model.coef_)}
    weights["intercept"] = float(model.intercept_)
    return val_pred, test_pred, weights


def fit_isotonic_by_clarity(val_df: pd.DataFrame, pred: np.ndarray) -> dict[str, IsotonicRegression]:
    models: dict[str, IsotonicRegression] = {}
    for clarity in ["clear", "moderate", "turbid"]:
        mask = (val_df["clarity_class"] == clarity).values
        if mask.sum() < 200:
            continue
        iso = IsotonicRegression(y_min=0.0, y_max=60.0, out_of_bounds="clip")
        iso.fit(pred[mask], val_df.loc[mask, "depth_m"].values)
        models[clarity] = iso
    return models


def apply_isotonic_by_clarity(
    df: pd.DataFrame,
    pred: np.ndarray,
    models: dict[str, IsotonicRegression],
) -> np.ndarray:
    out = np.asarray(pred, dtype=np.float32).copy()
    for clarity, model in models.items():
        mask = (df["clarity_class"] == clarity).values
        if mask.any():
            out[mask] = np.clip(model.predict(pred[mask]), 0, 60)
    return out


def fit_isotonic_by_depth_bin(
    y_true: np.ndarray,
    pred: np.ndarray,
) -> dict[str, IsotonicRegression]:
    """Fit separate isotonic regressions per predicted depth range.

    This directly attacks the depth compression problem by learning separate
    calibration curves for shallow, mid, and deep predictions.
    """
    bins = {"shallow": (0, 5), "mid": (5, 15), "deep": (15, 30), "very_deep": (30, 61)}
    models: dict[str, IsotonicRegression] = {}
    for name, (lo, hi) in bins.items():
        mask = (pred >= lo) & (pred < hi)
        if mask.sum() < 100:
            continue
        iso = IsotonicRegression(y_min=0.0, y_max=60.0, out_of_bounds="clip")
        iso.fit(pred[mask], y_true[mask])
        models[name] = iso
        correction = float(np.mean(iso.predict(pred[mask]) - pred[mask]))
        log.info(f"  Depth-bin isotonic [{name}] n={mask.sum()} mean_correction={correction:+.2f}m")
    return models


def apply_isotonic_by_depth_bin(
    pred: np.ndarray,
    models: dict[str, IsotonicRegression],
) -> np.ndarray:
    """Apply depth-binned isotonic calibration."""
    bins = {"shallow": (0, 5), "mid": (5, 15), "deep": (15, 30), "very_deep": (30, 61)}
    out = np.asarray(pred, dtype=np.float32).copy()
    for name, (lo, hi) in bins.items():
        if name not in models:
            continue
        mask = (pred >= lo) & (pred < hi)
        if mask.any():
            out[mask] = np.clip(models[name].predict(pred[mask]), 0, 60)
    return out


def save_model(model: object, path: Path) -> None:
    if hasattr(model, "save_model"):
        model.save_model(str(path))
        return
    with open(path, "wb") as f:
        pickle.dump(model, f)


def main() -> None:
    parser = argparse.ArgumentParser(description="Train hybrid sonar+spectral bathymetry model")
    parser.add_argument("--data", type=str, required=True, help="Path to sonar_s2_split.parquet")
    parser.add_argument("--output", type=str, default="/data/models/hybrid_sonar", help="Output directory")
    parser.add_argument("--device", type=str, default="cuda", choices=["cuda", "cpu"])
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_parquet(args.data)
    if "split" not in df.columns:
        raise ValueError("Expected a `split` column with lake-level train/val/test assignments")

    df = add_spatial_context(df)
    df, lake_ctx, clarity_meta = build_lake_context(df)

    train_lakes = lake_ctx[lake_ctx["split"] == "train"].copy()
    val_lakes = lake_ctx[lake_ctx["split"] == "val"].copy()
    test_lakes = lake_ctx[lake_ctx["split"] == "test"].copy()
    log.info(
        f"Lakes: train={len(train_lakes)} val={len(val_lakes)} test={len(test_lakes)} | "
        f"points={len(df):,}"
    )

    lake_feature_cols = available_features(
        lake_ctx,
        [col for col in lake_ctx.columns if col not in {"lake_id", "split", "clarity_class", "lake_max_depth_m", "lake_mean_depth_m", "lake_median_depth_m"}],
    )
    lake_ctx = fill_missing(lake_ctx, lake_feature_cols)

    stage1_models = {}
    stage1_results = {}
    for target_col, artifact_name in [
        ("lake_max_depth_m", "max_depth"),
        ("lake_mean_depth_m", "mean_depth"),
    ]:
        name, model, metrics = fit_best_regressor(
            lake_ctx.loc[lake_ctx["split"] == "train", lake_feature_cols].values,
            lake_ctx.loc[lake_ctx["split"] == "train", target_col].values,
            lake_ctx.loc[lake_ctx["split"] == "val", lake_feature_cols].values,
            lake_ctx.loc[lake_ctx["split"] == "val", target_col].values,
            device=args.device,
            label=f"stage1_{artifact_name}",
        )
        stage1_models[target_col] = {"name": name, "model": model}
        stage1_results[target_col] = metrics

        lake_ctx[f"pred_{artifact_name}_model"] = model.predict(lake_ctx[lake_feature_cols].values)
        lake_ctx[f"pred_{artifact_name}_model"] = np.clip(lake_ctx[f"pred_{artifact_name}_model"], 0.1, None)
        save_model(model, output_dir / f"stage1_{artifact_name}_{name}.pkl")

    lake_ctx = build_similar_lake_priors(lake_ctx, lake_feature_cols, k=5)
    lake_ctx = fill_missing(
        lake_ctx,
        ["donor_mean_depth", "donor_max_depth", "donor_median_depth", "donor_distance"],
    )

    val_lake_mask = lake_ctx["split"] == "val"
    max_alpha, _ = tune_two_source_blend(
        lake_ctx.loc[val_lake_mask, "lake_max_depth_m"].values,
        lake_ctx.loc[val_lake_mask, "pred_max_depth_model"].values,
        lake_ctx.loc[val_lake_mask, "donor_max_depth"].values,
    )
    mean_alpha, _ = tune_two_source_blend(
        lake_ctx.loc[val_lake_mask, "lake_mean_depth_m"].values,
        lake_ctx.loc[val_lake_mask, "pred_mean_depth_model"].values,
        lake_ctx.loc[val_lake_mask, "donor_mean_depth"].values,
    )
    lake_ctx["pred_max_depth"] = np.clip(
        max_alpha * lake_ctx["pred_max_depth_model"] + (1.0 - max_alpha) * lake_ctx["donor_max_depth"],
        0.1,
        None,
    )
    lake_ctx["pred_mean_depth"] = np.clip(
        mean_alpha * lake_ctx["pred_mean_depth_model"] + (1.0 - mean_alpha) * lake_ctx["donor_mean_depth"],
        0.1,
        None,
    )

    df = df.drop(
        columns=[
            c
            for c in [
                "clarity_class",
                "pred_max_depth",
                "pred_mean_depth",
                "pred_max_depth_model",
                "pred_mean_depth_model",
                "donor_mean_depth",
                "donor_max_depth",
                "donor_median_depth",
                "donor_distance",
            ]
            if c in df.columns
        ],
        errors="ignore",
    )
    df = df.merge(
        lake_ctx[
            [
                "lake_id",
                "clarity_class",
                "pred_max_depth",
                "pred_mean_depth",
                "pred_max_depth_model",
                "pred_mean_depth_model",
                "donor_mean_depth",
                "donor_max_depth",
                "donor_median_depth",
                "donor_distance",
            ]
        ],
        on="lake_id",
        how="left",
    )

    df["depth_rel_target"] = np.clip(df["depth_m"] / (df["lake_max_depth_m"] + EPS), 0, 1.5)
    df["pred_max_depth"] = df["pred_max_depth"].fillna(df["pred_max_depth"].median())
    df["pred_mean_depth"] = df["pred_mean_depth"].fillna(df["pred_mean_depth"].median())

    center_features = available_features(df, CENTER_FEATURES + PHYSICS_CENTER_FEATURES)
    point_feature_cols = available_features(
        df,
        POINT_BASE_FEATURES
        + SPATIAL_POINT_FEATURES
        + physics_feature_columns(df)
        + [f"{feature}_centered" for feature in center_features]
        + [f"{feature}_z" for feature in center_features]
        + [f"{feature}_lake_mean" for feature in center_features]
        + [
            "pred_max_depth",
            "pred_mean_depth",
            "pred_max_depth_model",
            "pred_mean_depth_model",
            "donor_mean_depth",
            "donor_max_depth",
            "donor_median_depth",
            "donor_distance",
            "lake_point_count",
        ],
    )
    df = fill_missing(df, point_feature_cols)

    train_df = df[df["split"] == "train"].copy()
    val_df = df[df["split"] == "val"].copy()
    test_df = df[df["split"] == "test"].copy()

    # Depth-weighted sample weights for deep-water emphasis
    train_weights = compute_depth_weights(train_df["depth_m"].values)
    log.info(
        f"Depth weights: min={train_weights.min():.2f} "
        f"median={np.median(train_weights):.2f} "
        f"max={train_weights.max():.2f}"
    )

    direct_name, direct_model, direct_metrics = fit_best_regressor(
        train_df[point_feature_cols].values,
        train_df["depth_m"].values,
        val_df[point_feature_cols].values,
        val_df["depth_m"].values,
        device=args.device,
        label="stage2_direct_depth",
        model_names=["hist_gbr", "xgboost"],
        sample_weight=train_weights,
    )
    save_model(direct_model, output_dir / f"stage2_direct_{direct_name}.pkl")

    # Log-depth parallel model — expands dynamic range for deep lakes
    log.info("Training log-depth parallel model ...")
    y_train_log = np.log1p(train_df["depth_m"].values)
    y_val_log = np.log1p(val_df["depth_m"].values)
    log_name, log_model, log_metrics = fit_best_regressor(
        train_df[point_feature_cols].values,
        y_train_log,
        val_df[point_feature_cols].values,
        y_val_log,
        device=args.device,
        label="stage2_log_depth",
        model_names=["hist_gbr", "xgboost"],
        sample_weight=train_weights,
    )
    save_model(log_model, output_dir / f"stage2_log_depth_{log_name}.pkl")

    rel_name, rel_model, rel_metrics = fit_best_regressor(
        train_df[point_feature_cols].values,
        train_df["depth_rel_target"].values,
        val_df[point_feature_cols].values,
        val_df["depth_rel_target"].values,
        device=args.device,
        label="stage2_relative_depth",
        model_names=["hist_gbr", "xgboost"],
        sample_weight=train_weights,
    )
    save_model(rel_model, output_dir / f"stage2_relative_{rel_name}.pkl")

    val_direct = np.clip(direct_model.predict(val_df[point_feature_cols].values), 0, 60)
    test_direct = np.clip(direct_model.predict(test_df[point_feature_cols].values), 0, 60)

    # Log-depth predictions — transform back via expm1
    val_log_depth = np.clip(np.expm1(log_model.predict(val_df[point_feature_cols].values)), 0, 60)
    test_log_depth = np.clip(np.expm1(log_model.predict(test_df[point_feature_cols].values)), 0, 60)

    # Blend linear + log-depth: log-depth is better for deep, linear for shallow
    # Use depth-adaptive alpha: more log-depth weight as predicted depth increases
    def blend_linear_log(linear: np.ndarray, log_pred: np.ndarray) -> np.ndarray:
        """Blend linear and log-depth predictions. Log gets more weight for deep."""
        alpha = np.clip(linear / 20.0, 0.0, 0.7)  # 0→0, 10→0.5, 20+→0.7
        return (1.0 - alpha) * linear + alpha * log_pred

    val_direct_blended = blend_linear_log(val_direct, val_log_depth)
    test_direct_blended = blend_linear_log(test_direct, test_log_depth)
    log.info(
        f"Log-depth blend: val linear RMSE={compute_metrics(val_df['depth_m'].values, val_direct)['rmse']:.3f}m "
        f"→ blended RMSE={compute_metrics(val_df['depth_m'].values, val_direct_blended)['rmse']:.3f}m"
    )

    val_rel = np.clip(rel_model.predict(val_df[point_feature_cols].values), 0, 1.5) * val_df["pred_max_depth"].values
    test_rel = np.clip(rel_model.predict(test_df[point_feature_cols].values), 0, 1.5) * test_df["pred_max_depth"].values

    blend_alphas = tune_blend_by_clarity(val_df, val_direct_blended, val_rel)
    val_blend = apply_clarity_blend(val_df, val_direct_blended, val_rel, blend_alphas)
    test_blend = apply_clarity_blend(test_df, test_direct_blended, test_rel, blend_alphas)

    val_regime, test_regime, regime_meta = train_range_experts(
        train_df=train_df,
        val_df=val_df,
        test_df=test_df,
        point_feature_cols=point_feature_cols,
        device=args.device,
        base_val_pred=val_blend,
        base_test_pred=test_blend,
        output_dir=output_dir,
    )

    val_stack, test_stack, stack_weights = fit_meta_stack(
        y_val=val_df["depth_m"].values,
        val_features={
            "direct": val_direct,
            "direct_blended": val_direct_blended,
            "log_depth": val_log_depth,
            "relative": val_rel,
            "blend": val_blend,
            "regime": val_regime,
            "prior_mean": val_df["pred_mean_depth"].values,
            "donor_mean": val_df["donor_mean_depth"].values,
        },
        test_features={
            "direct": test_direct,
            "direct_blended": test_direct_blended,
            "log_depth": test_log_depth,
            "relative": test_rel,
            "blend": test_blend,
            "regime": test_regime,
            "prior_mean": test_df["pred_mean_depth"].values,
            "donor_mean": test_df["donor_mean_depth"].values,
        },
    )

    # Step 1: Isotonic by clarity class (existing)
    iso_models = fit_isotonic_by_clarity(val_df, val_stack)
    val_stack_cal = apply_isotonic_by_clarity(val_df, val_stack, iso_models)
    test_stack_cal = apply_isotonic_by_clarity(test_df, test_stack, iso_models)

    # Step 2: Isotonic by depth bin (NEW — addresses compression at tails)
    log.info("Fitting depth-binned isotonic calibration ...")
    depth_iso_models = fit_isotonic_by_depth_bin(
        val_df["depth_m"].values, val_stack_cal,
    )
    val_stack_cal = apply_isotonic_by_depth_bin(val_stack_cal, depth_iso_models)
    test_stack_cal = apply_isotonic_by_depth_bin(test_stack_cal, depth_iso_models)

    log.info(
        f"After depth-bin isotonic: val RMSE={compute_metrics(val_df['depth_m'].values, val_stack_cal)['rmse']:.3f}m"
    )

    baseline_depth = train_df["depth_m"].mean()
    baseline_pred = np.full(len(test_df), baseline_depth, dtype=np.float32)

    results = {
        "baseline_mean": compute_metrics(test_df["depth_m"].values, baseline_pred),
        "stage1_prior_only": compute_metrics(test_df["depth_m"].values, np.clip(test_df["pred_mean_depth"].values, 0, 60)),
        "donor_prior_only": compute_metrics(test_df["depth_m"].values, np.clip(test_df["donor_mean_depth"].values, 0, 60)),
        "direct_depth": compute_metrics(test_df["depth_m"].values, test_direct),
        "log_depth": compute_metrics(test_df["depth_m"].values, test_log_depth),
        "direct_blended": compute_metrics(test_df["depth_m"].values, test_direct_blended),
        "relative_depth_scaled": compute_metrics(test_df["depth_m"].values, test_rel),
        "clarity_blend": compute_metrics(test_df["depth_m"].values, test_blend),
        "range_expert": compute_metrics(test_df["depth_m"].values, test_regime),
        "stacked_meta": compute_metrics(test_df["depth_m"].values, test_stack),
        "calibrated_meta": compute_metrics(test_df["depth_m"].values, test_stack_cal),
    }

    results["depth_bins"] = {
        "direct_depth": compute_depth_bins(test_df["depth_m"].values, test_direct),
        "log_depth": compute_depth_bins(test_df["depth_m"].values, test_log_depth),
        "direct_blended": compute_depth_bins(test_df["depth_m"].values, test_direct_blended),
        "relative_depth_scaled": compute_depth_bins(test_df["depth_m"].values, test_rel),
        "clarity_blend": compute_depth_bins(test_df["depth_m"].values, test_blend),
        "range_expert": compute_depth_bins(test_df["depth_m"].values, test_regime),
        "calibrated_meta": compute_depth_bins(test_df["depth_m"].values, test_stack_cal),
    }

    clarity_breakdown: dict[str, dict[str, float]] = {}
    for clarity in ["clear", "moderate", "turbid"]:
        mask = test_df["clarity_class"] == clarity
        clarity_breakdown[clarity] = compute_metrics(
            test_df.loc[mask, "depth_m"].values,
            test_stack[mask.values],
        ) if mask.sum() >= 5 else {"n": int(mask.sum())}
    results["clarity_breakdown"] = clarity_breakdown

    results["blend_alphas"] = blend_alphas
    results["prior_blend_alphas"] = {"max_depth": max_alpha, "mean_depth": mean_alpha}
    results["stage1_validation"] = stage1_results
    results["stage2_validation"] = {
        "direct_depth": direct_metrics,
        "relative_depth": rel_metrics,
    }
    results["range_expert_validation"] = regime_meta
    results["meta_stack_weights"] = stack_weights
    results["final_model"] = "stacked_meta"
    results["clarity_quantiles"] = clarity_meta

    test_out = test_df[
        [
            "lake_id",
            "depth_m",
            "clarity_class",
            "pred_max_depth",
            "pred_mean_depth",
            "donor_mean_depth",
            "donor_max_depth",
            "coord_radius_norm",
            "coord_center_proximity",
        ]
    ].copy()
    test_out["pred_direct"] = test_direct
    test_out["pred_log_depth"] = test_log_depth
    test_out["pred_direct_blended"] = test_direct_blended
    test_out["pred_relative"] = test_rel
    test_out["pred_blend"] = test_blend
    test_out["pred_regime"] = test_regime
    test_out["pred_stack"] = test_stack
    test_out["pred_final"] = test_stack_cal
    test_out.to_parquet(output_dir / "test_predictions.parquet", index=False)

    per_lake = test_out.groupby("lake_id").apply(
        lambda g: pd.Series(compute_metrics(g["depth_m"].values, g["pred_final"].values))
    ).reset_index()
    per_lake.to_parquet(output_dir / "per_lake_metrics.parquet", index=False)

    config = {
        "input": args.data,
        "point_features": point_feature_cols,
        "lake_features": lake_feature_cols,
        "stage1_models": {k: v["name"] for k, v in stage1_models.items()},
        "stage2_models": {
            "direct": direct_name,
            "relative": rel_name,
        },
        "blend_alphas": blend_alphas,
        "prior_blend_alphas": {"max_depth": max_alpha, "mean_depth": mean_alpha},
        "range_expert_thresholds": regime_meta.get("thresholds"),
        "meta_stack_weights": stack_weights,
    }

    with open(output_dir / "metrics.json", "w") as f:
        json.dump(results, f, indent=2)
    with open(output_dir / "config.json", "w") as f:
        json.dump(config, f, indent=2)

    log.info("")
    log.info("=" * 72)
    log.info("HYBRID SONAR RESULTS")
    log.info("=" * 72)
    for name in ["baseline_mean", "direct_depth", "log_depth", "direct_blended", "relative_depth_scaled", "clarity_blend", "range_expert", "stacked_meta", "calibrated_meta"]:
        m = results[name]
        log.info(f"{name:22s} RMSE={m['rmse']:.3f}m MAE={m['mae']:.3f}m R2={m['r2']:.3f}")
    log.info("=" * 72)


if __name__ == "__main__":
    main()
