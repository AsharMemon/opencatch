#!/usr/bin/env python3
"""
OpenCatch -- Multi-Source Fusion Model for Lake Bathymetry

Combines ALL available depth estimation approaches into one optimally
weighted prediction using a stacking ensemble. Each data source has
different strengths, and the meta-learner discovers when each is reliable.

Input sources (as base model predictions):
  1. SWOT-enhanced A-E curve (primary) -- best for medium-large lakes
  2. Terrain DEM prior (secondary) -- works for all lakes, even turbid
  3. Morphometric features (area, perimeter, shape -> depth) -- baseline
  4. Within-lake spectral variation (Sentinel-2) -- clear lakes only
  5. 3D-LAKES baseline -- sparse but global

Meta-learners:
  - Ridge regression (simple, interpretable)
  - XGBoost (captures non-linear interactions)
  - Optionally: LightGBM, stacked neural net

Evaluation:
  - Trained on MN DNR sonar ground truth (4,500 lakes)
  - Spatial CV by lake (no lake in both train and validation)
  - Honest metrics: RMSE, MAE, R2, depth-stratified analysis

Usage:
    python fusion_bathymetry.py \
        --swot-ae /data/enhanced_ae/enhanced_ae_metrics.parquet \
        --terrain /data/terrain_depth_predictions.parquet \
        --morphometric /data/training/global_510k.parquet \
        --spectral /data/sdb_preprocessed.parquet \
        --threedlakes /data/3d_lakes_with_depths.parquet \
        --ground-truth /data/mn_sonar_depths.parquet \
        --output /data/models/fusion

Requirements:
    pip install xgboost lightgbm scikit-learn pandas numpy pyarrow tqdm
"""

import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge, RidgeCV
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
from sklearn.model_selection import GroupKFold, KFold
from sklearn.preprocessing import StandardScaler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("fusion")

EPS = 1e-8


# == Data Source Loaders ======================================================

def load_swot_predictions(path: str) -> Optional[pd.DataFrame]:
    """Load SWOT-enhanced A-E depth predictions."""
    if not path or not os.path.exists(path):
        log.warning(f"SWOT data not found: {path}")
        return None

    df = pd.read_parquet(path)
    # Rename columns for fusion
    col_map = {}
    for c in df.columns:
        if c in ("max_depth_m", "mean_depth_m", "volume_m3"):
            col_map[c] = f"swot_{c}"

    df = df.rename(columns=col_map)

    # Add source quality indicators
    if "n_ae_points" in df.columns:
        df["swot_data_quality"] = np.clip(df["n_ae_points"] / 30.0, 0, 1)
    if "fit_r2" in df.columns:
        df["swot_fit_quality"] = np.clip(df["fit_r2"], 0, 1)

    id_col = "lake_id" if "lake_id" in df.columns else "hylak_id"
    log.info(f"SWOT predictions: {len(df)} lakes")
    return df


def load_terrain_predictions(path: str) -> Optional[pd.DataFrame]:
    """Load terrain DEM prior depth predictions."""
    if not path or not os.path.exists(path):
        log.warning(f"Terrain data not found: {path}")
        return None

    df = pd.read_parquet(path)
    # Rename
    col_map = {}
    if "terrain_predicted_depth_m" in df.columns:
        col_map["terrain_predicted_depth_m"] = "terrain_max_depth_m"
    if "terrain_prediction_confidence" in df.columns:
        col_map["terrain_prediction_confidence"] = "terrain_confidence"

    df = df.rename(columns=col_map)
    log.info(f"Terrain predictions: {len(df)} lakes")
    return df


def load_morphometric_features(path: str) -> Optional[pd.DataFrame]:
    """Load morphometric features (area, perimeter, shape metrics)."""
    if not path or not os.path.exists(path):
        log.warning(f"Morphometric data not found: {path}")
        return None

    df = pd.read_parquet(path)

    # Compute additional morphometric depth predictors
    if "Lake_area" in df.columns:
        area = df["Lake_area"]
    elif "lake_area_km2" in df.columns:
        area = df["lake_area_km2"]
    elif "area_km2" in df.columns:
        area = df["area_km2"]
    else:
        area = None

    if area is not None:
        # GLOBathy-style area-depth relationship: depth ~ area^0.356
        df["morpho_depth_estimate"] = 3.14 * np.power(np.clip(area, EPS, None), 0.356)

    log.info(f"Morphometric features: {len(df)} lakes")
    return df


def load_spectral_predictions(path: str) -> Optional[pd.DataFrame]:
    """Load within-lake spectral SDB predictions (Sentinel-2 based)."""
    if not path or not os.path.exists(path):
        log.warning(f"Spectral data not found: {path}")
        return None

    df = pd.read_parquet(path)

    # Per-lake aggregated spectral depth (mean of pixel-level predictions)
    if "predicted_depth" in df.columns and "lake_id" in df.columns:
        agg = df.groupby("lake_id").agg(
            spectral_mean_depth=("predicted_depth", "mean"),
            spectral_max_depth=("predicted_depth", "max"),
            spectral_std_depth=("predicted_depth", "std"),
            spectral_n_pixels=("predicted_depth", "count"),
        ).reset_index()
        log.info(f"Spectral predictions: {len(agg)} lakes")
        return agg

    log.info(f"Spectral predictions: {len(df)} lakes")
    return df


def load_3dlakes_predictions(path: str) -> Optional[pd.DataFrame]:
    """Load 3D-LAKES baseline depth estimates."""
    if not path or not os.path.exists(path):
        log.warning(f"3D-LAKES data not found: {path}")
        return None

    df = pd.read_parquet(path)
    col_map = {}
    for c in df.columns:
        if c == "max_depth_m":
            col_map[c] = "tdl_max_depth_m"
        elif c == "n_ae_points":
            col_map[c] = "tdl_n_ae_points"

    df = df.rename(columns=col_map)
    log.info(f"3D-LAKES predictions: {len(df)} lakes")
    return df


def load_ground_truth(path: str, target: str = "max_depth_m") -> pd.DataFrame:
    """Load MN DNR sonar ground truth."""
    df = pd.read_parquet(path)
    id_col = "lake_id" if "lake_id" in df.columns else "hylak_id"

    if target not in df.columns:
        # Try alternatives
        for alt in ["max_depth_ft", "MAX_DEPTH_FT", "max_depth"]:
            if alt in df.columns:
                df[target] = df[alt] * 0.3048 if "ft" in alt.lower() else df[alt]
                break

    log.info(f"Ground truth: {len(df)} lakes with {target}")
    return df


# == Feature Assembly =========================================================

def assemble_features(
    swot_df: Optional[pd.DataFrame],
    terrain_df: Optional[pd.DataFrame],
    morpho_df: Optional[pd.DataFrame],
    spectral_df: Optional[pd.DataFrame],
    threedlakes_df: Optional[pd.DataFrame],
    ground_truth: pd.DataFrame,
    target: str = "max_depth_m",
) -> tuple:
    """
    Merge all data sources into a unified feature matrix.

    Returns (X_df, y, lake_ids, feature_names)
    """
    # Start with ground truth as base
    id_col = "lake_id" if "lake_id" in ground_truth.columns else "hylak_id"
    merged = ground_truth[[id_col, target]].copy()
    if "lat" in ground_truth.columns:
        merged["lat"] = ground_truth["lat"]
    if "lon" in ground_truth.columns:
        merged["lon"] = ground_truth["lon"]

    # Merge each source
    sources_merged = {}

    if swot_df is not None:
        swot_id = "lake_id" if "lake_id" in swot_df.columns else "hylak_id"
        swot_cols = [c for c in swot_df.columns if c.startswith("swot_") or c in [
            "n_ae_points", "wse_range_m", "a_max_km2", "volume_dev_ratio",
            "fit_r2", "fit_model"
        ]]
        if swot_id != id_col:
            swot_df = swot_df.rename(columns={swot_id: id_col})
        merged = merged.merge(
            swot_df[[id_col] + swot_cols].drop_duplicates(subset=[id_col]),
            on=id_col, how="left",
        )
        sources_merged["swot"] = len(swot_cols)

    if terrain_df is not None:
        terr_id = "lake_id" if "lake_id" in terrain_df.columns else "hylak_id"
        terr_cols = [c for c in terrain_df.columns if c.startswith("terrain_") or c.startswith("shore_") or c.startswith("tri_") or c.startswith("valley_") or c.startswith("slope_") or c.startswith("profile_") or c.startswith("plan_") or c.startswith("concavity") or c.startswith("hypsometric")]
        if terr_id != id_col:
            terrain_df = terrain_df.rename(columns={terr_id: id_col})
        merged = merged.merge(
            terrain_df[[id_col] + terr_cols].drop_duplicates(subset=[id_col]),
            on=id_col, how="left",
        )
        sources_merged["terrain"] = len(terr_cols)

    if morpho_df is not None:
        morph_id = "lake_id" if "lake_id" in morpho_df.columns else "hylak_id"
        morph_cols = [c for c in morpho_df.columns if c in [
            "morpho_depth_estimate", "Lake_area", "Shore_len", "lake_area_km2",
            "shore_len_km", "shoreline_dev", "circularity", "elongation",
        ]]
        if morph_id != id_col:
            morpho_df = morpho_df.rename(columns={morph_id: id_col})
        if morph_cols:
            merged = merged.merge(
                morpho_df[[id_col] + morph_cols].drop_duplicates(subset=[id_col]),
                on=id_col, how="left",
            )
            sources_merged["morphometric"] = len(morph_cols)

    if spectral_df is not None:
        spec_id = "lake_id" if "lake_id" in spectral_df.columns else "hylak_id"
        spec_cols = [c for c in spectral_df.columns if c.startswith("spectral_")]
        if spec_id != id_col:
            spectral_df = spectral_df.rename(columns={spec_id: id_col})
        if spec_cols:
            merged = merged.merge(
                spectral_df[[id_col] + spec_cols].drop_duplicates(subset=[id_col]),
                on=id_col, how="left",
            )
            sources_merged["spectral"] = len(spec_cols)

    if threedlakes_df is not None:
        tdl_id = "lake_id" if "lake_id" in threedlakes_df.columns else "hylak_id"
        tdl_cols = [c for c in threedlakes_df.columns if c.startswith("tdl_")]
        if tdl_id != id_col:
            threedlakes_df = threedlakes_df.rename(columns={tdl_id: id_col})
        if tdl_cols:
            merged = merged.merge(
                threedlakes_df[[id_col] + tdl_cols].drop_duplicates(subset=[id_col]),
                on=id_col, how="left",
            )
            sources_merged["3dlakes"] = len(tdl_cols)

    log.info(f"\nMerged dataset: {len(merged)} lakes")
    log.info(f"Sources merged: {sources_merged}")

    # Separate features and target
    non_feature_cols = [id_col, target, "lat", "lon"]
    feature_cols = [c for c in merged.columns
                    if c not in non_feature_cols
                    and merged[c].dtype in ["float64", "float32", "int64", "int32"]]

    # Add source availability indicators
    for src_name in ["swot", "terrain", "spectral", "tdl"]:
        src_cols = [c for c in feature_cols if c.startswith(src_name)]
        if src_cols:
            merged[f"has_{src_name}"] = merged[src_cols].notna().any(axis=1).astype(float)
            feature_cols.append(f"has_{src_name}")

    X = merged[feature_cols].copy()
    y = merged[target].values
    lake_ids = merged[id_col].values
    lats = merged["lat"].values if "lat" in merged.columns else None
    lons = merged["lon"].values if "lon" in merged.columns else None

    # Remove features with >80% NaN
    nan_frac = X.isna().mean()
    good_features = nan_frac[nan_frac < 0.8].index.tolist()
    X = X[good_features]

    log.info(f"Final features: {len(good_features)} (dropped {len(feature_cols) - len(good_features)} with >80% NaN)")

    return X, y, lake_ids, good_features, lats, lons


# == Stacking Ensemble ========================================================

class FusionEnsemble:
    """
    Stacking ensemble with two levels:
      Level 0: XGBoost + Ridge (diverse base learners on raw features)
      Level 1: Ridge meta-learner on base predictions + source quality features
    """

    def __init__(self):
        self.base_models = {}
        self.meta_model = None
        self.scaler = StandardScaler()
        self.feature_cols = []

    def fit(
        self,
        X: pd.DataFrame,
        y: np.ndarray,
        lake_ids: np.ndarray,
        lats: np.ndarray = None,
        lons: np.ndarray = None,
        n_folds: int = 5,
    ) -> dict:
        """
        Train the stacking ensemble with spatial CV.

        Returns dict with metrics.
        """
        import xgboost as xgb

        self.feature_cols = list(X.columns)

        # Fill NaN with median
        X_filled = X.fillna(X.median())
        X_scaled = pd.DataFrame(
            self.scaler.fit_transform(X_filled),
            columns=X_filled.columns,
            index=X_filled.index,
        )

        # Spatial CV groups
        if lats is not None and lons is not None:
            groups = (
                (lats * 2).astype(int).astype(str) + "_" +
                (lons * 2).astype(int).astype(str)
            )
            group_ids = pd.factorize(groups)[0]
            n_groups = len(np.unique(group_ids))
            n_splits = min(n_folds, n_groups)
            if n_splits < 2:
                log.warning("Not enough spatial groups, falling back to random CV")
                cv = KFold(n_splits=n_folds, shuffle=True, random_state=42)
                split_gen = cv.split(X_scaled, y)
            else:
                cv = GroupKFold(n_splits=n_splits)
                split_gen = cv.split(X_scaled, y, groups=group_ids)
        else:
            cv = KFold(n_splits=n_folds, shuffle=True, random_state=42)
            split_gen = cv.split(X_scaled, y)

        # Level 0: Generate out-of-fold predictions for each base model
        oof_xgb = np.full(len(y), np.nan)
        oof_ridge = np.full(len(y), np.nan)

        fold_metrics = []

        for fold_i, (train_idx, val_idx) in enumerate(split_gen):
            Xt, Xv = X_filled.iloc[train_idx], X_filled.iloc[val_idx]
            Xs_t, Xs_v = X_scaled.iloc[train_idx], X_scaled.iloc[val_idx]
            yt, yv = y[train_idx], y[val_idx]

            # XGBoost base
            xgb_model = xgb.XGBRegressor(
                n_estimators=500,
                max_depth=6,
                learning_rate=0.05,
                subsample=0.8,
                colsample_bytree=0.8,
                reg_alpha=0.1,
                reg_lambda=1.0,
                min_child_weight=5,
                random_state=42,
                n_jobs=-1,
            )
            xgb_model.fit(Xt, yt, eval_set=[(Xv, yv)], verbose=False)
            oof_xgb[val_idx] = np.clip(xgb_model.predict(Xv), 0, None)

            # Ridge base
            ridge_model = RidgeCV(alphas=[0.01, 0.1, 1, 10, 100])
            ridge_model.fit(Xs_t, yt)
            oof_ridge[val_idx] = np.clip(ridge_model.predict(Xs_v), 0, None)

            # Fold metrics for each base
            for name, preds in [("xgb", oof_xgb[val_idx]), ("ridge", oof_ridge[val_idx])]:
                rmse = np.sqrt(mean_squared_error(yv, preds))
                r2 = r2_score(yv, preds)
                fold_metrics.append({"fold": fold_i, "model": name, "rmse": rmse, "r2": r2})

        # Level 1: Meta-learner
        # Stack base predictions + source quality features
        meta_features = pd.DataFrame({
            "xgb_pred": oof_xgb,
            "ridge_pred": oof_ridge,
        })

        # Add source quality indicators
        for col in X.columns:
            if col.startswith("has_") or col.endswith("_quality") or col.endswith("_confidence"):
                meta_features[col] = X_filled[col].values

        valid = ~(np.isnan(oof_xgb) | np.isnan(oof_ridge))
        meta_X = meta_features[valid].values
        meta_y = y[valid]

        self.meta_model = RidgeCV(alphas=[0.01, 0.1, 1, 10, 100])
        self.meta_model.fit(meta_X, meta_y)

        # Final ensemble OOF predictions
        oof_ensemble = np.full(len(y), np.nan)
        oof_ensemble[valid] = np.clip(
            self.meta_model.predict(meta_X), 0, None
        )

        # Train final base models on all data
        self.base_models["xgb"] = xgb.XGBRegressor(
            n_estimators=500, max_depth=6, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8,
            reg_alpha=0.1, reg_lambda=1.0, min_child_weight=5,
            random_state=42, n_jobs=-1,
        )
        self.base_models["xgb"].fit(X_filled, y, verbose=False)

        self.base_models["ridge"] = RidgeCV(alphas=[0.01, 0.1, 1, 10, 100])
        self.base_models["ridge"].fit(
            self.scaler.transform(X_filled), y
        )

        # Compute overall metrics
        overall = {}
        for name, preds in [("xgb", oof_xgb), ("ridge", oof_ridge), ("ensemble", oof_ensemble)]:
            mask = ~np.isnan(preds)
            if mask.sum() > 0:
                overall[name] = {
                    "rmse": float(np.sqrt(mean_squared_error(y[mask], preds[mask]))),
                    "mae": float(mean_absolute_error(y[mask], preds[mask])),
                    "r2": float(r2_score(y[mask], preds[mask])),
                    "n_lakes": int(mask.sum()),
                }

        # Depth-stratified analysis
        depth_strat = self._depth_stratified_analysis(y, oof_ensemble)

        # Meta-learner weights (shows source contribution)
        meta_weights = dict(zip(
            meta_features.columns,
            self.meta_model.coef_,
        ))

        results = {
            "overall": overall,
            "fold_metrics": fold_metrics,
            "depth_stratified": depth_strat,
            "meta_weights": {k: float(v) for k, v in meta_weights.items()},
            "meta_alpha": float(self.meta_model.alpha_),
        }

        return results

    def _depth_stratified_analysis(
        self, y_true: np.ndarray, y_pred: np.ndarray
    ) -> list:
        """Analyze performance by depth range."""
        valid = ~np.isnan(y_pred)
        y_t = y_true[valid]
        y_p = y_pred[valid]

        bins = [(0, 3), (3, 6), (6, 10), (10, 20), (20, 50), (50, 200)]
        results = []

        for lo, hi in bins:
            mask = (y_t >= lo) & (y_t < hi)
            n = mask.sum()
            if n < 5:
                continue
            rmse = np.sqrt(mean_squared_error(y_t[mask], y_p[mask]))
            mae = mean_absolute_error(y_t[mask], y_p[mask])
            r2 = r2_score(y_t[mask], y_p[mask]) if n > 1 else np.nan
            results.append({
                "depth_range": f"{lo}-{hi}m",
                "n_lakes": int(n),
                "rmse": float(rmse),
                "mae": float(mae),
                "r2": float(r2),
            })

        return results

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """Predict depth using the trained ensemble."""
        X_filled = X[self.feature_cols].fillna(X[self.feature_cols].median())
        X_scaled = pd.DataFrame(
            self.scaler.transform(X_filled),
            columns=X_filled.columns,
        )

        # Base predictions
        xgb_pred = np.clip(self.base_models["xgb"].predict(X_filled), 0, None)
        ridge_pred = np.clip(self.base_models["ridge"].predict(X_scaled), 0, None)

        # Meta features
        meta_features = pd.DataFrame({
            "xgb_pred": xgb_pred,
            "ridge_pred": ridge_pred,
        })

        for col in X_filled.columns:
            if col.startswith("has_") or col.endswith("_quality") or col.endswith("_confidence"):
                meta_features[col] = X_filled[col].values

        # Ensure same columns as training
        meta_cols = list(self.meta_model.feature_names_in_) if hasattr(self.meta_model, "feature_names_in_") else None
        if meta_cols:
            for c in meta_cols:
                if c not in meta_features.columns:
                    meta_features[c] = 0

        ensemble_pred = np.clip(self.meta_model.predict(meta_features.values), 0, None)
        return ensemble_pred

    def save(self, output_dir: str):
        """Save ensemble model to disk."""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        import joblib
        joblib.dump(self.base_models, str(out / "base_models.joblib"))
        joblib.dump(self.meta_model, str(out / "meta_model.joblib"))
        joblib.dump(self.scaler, str(out / "scaler.joblib"))

        with open(str(out / "feature_cols.json"), "w") as f:
            json.dump(self.feature_cols, f)

        log.info(f"Ensemble saved to {output_dir}")

    @classmethod
    def load(cls, model_dir: str) -> "FusionEnsemble":
        """Load ensemble from disk."""
        import joblib
        d = Path(model_dir)
        ens = cls()
        ens.base_models = joblib.load(str(d / "base_models.joblib"))
        ens.meta_model = joblib.load(str(d / "meta_model.joblib"))
        ens.scaler = joblib.load(str(d / "scaler.joblib"))
        with open(str(d / "feature_cols.json")) as f:
            ens.feature_cols = json.load(f)
        return ens


# == Main Pipeline ============================================================

def main():
    parser = argparse.ArgumentParser(description="Multi-source fusion bathymetry model")
    parser.add_argument("--swot-ae", default="/data/enhanced_ae/enhanced_ae_metrics.parquet",
                       help="SWOT-enhanced A-E metrics")
    parser.add_argument("--terrain", default="/data/terrain_depth_predictions.parquet",
                       help="Terrain DEM prior predictions")
    parser.add_argument("--morphometric", default="/data/training/global_510k.parquet",
                       help="Morphometric features")
    parser.add_argument("--spectral", default="/data/sdb_preprocessed.parquet",
                       help="Spectral SDB predictions")
    parser.add_argument("--threedlakes", default="/data/3d_lakes_with_depths.parquet",
                       help="3D-LAKES baseline")
    parser.add_argument("--ground-truth", required=True,
                       help="MN DNR sonar ground truth")
    parser.add_argument("--target", default="max_depth_m",
                       choices=["max_depth_m", "mean_depth_m"])
    parser.add_argument("--output", default="/data/models/fusion",
                       help="Output directory for model and results")
    parser.add_argument("--n-folds", type=int, default=5)
    args = parser.parse_args()

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    log.info("=" * 70)
    log.info("  MULTI-SOURCE FUSION BATHYMETRY MODEL")
    log.info("=" * 70)

    # Load all sources
    swot_df = load_swot_predictions(args.swot_ae)
    terrain_df = load_terrain_predictions(args.terrain)
    morpho_df = load_morphometric_features(args.morphometric)
    spectral_df = load_spectral_predictions(args.spectral)
    tdl_df = load_3dlakes_predictions(args.threedlakes)
    gt_df = load_ground_truth(args.ground_truth, args.target)

    # Assemble unified feature matrix
    X, y, lake_ids, feature_names, lats, lons = assemble_features(
        swot_df, terrain_df, morpho_df, spectral_df, tdl_df,
        gt_df, target=args.target,
    )

    if len(X) < 50:
        log.error(f"Only {len(X)} lakes with features + ground truth. Need >= 50.")
        return

    # Train fusion ensemble
    log.info("\n--- Training Stacking Ensemble ---")
    ensemble = FusionEnsemble()
    results = ensemble.fit(X, y, lake_ids, lats, lons, n_folds=args.n_folds)

    # Report results
    log.info("\n" + "=" * 70)
    log.info("  HONEST RESULTS (Spatial CV)")
    log.info("=" * 70)

    for model_name, metrics in results["overall"].items():
        log.info(f"\n  {model_name.upper()}:")
        log.info(f"    RMSE:    {metrics['rmse']:.2f}m")
        log.info(f"    MAE:     {metrics['mae']:.2f}m")
        log.info(f"    R2:      {metrics['r2']:.3f}")
        log.info(f"    N lakes: {metrics['n_lakes']}")

    # Depth-stratified
    log.info("\n  DEPTH-STRATIFIED (Ensemble):")
    for strat in results["depth_stratified"]:
        log.info(
            f"    {strat['depth_range']:>10s}: "
            f"RMSE={strat['rmse']:.2f}m, MAE={strat['mae']:.2f}m, "
            f"R2={strat['r2']:.3f}, N={strat['n_lakes']}"
        )

    # Meta-learner weights
    log.info("\n  META-LEARNER WEIGHTS:")
    for name, weight in sorted(results["meta_weights"].items(), key=lambda x: -abs(x[1])):
        log.info(f"    {weight:+.4f}  {name}")

    # Save model and results
    ensemble.save(str(out))

    results_path = out / "fusion_results.json"
    with open(str(results_path), "w") as f:
        json.dump(results, f, indent=2, default=str)
    log.info(f"\nResults saved to {results_path}")

    # Generate predictions for all lakes (not just ground truth)
    log.info("\n--- Generating Predictions for All Lakes ---")
    all_sources = [swot_df, terrain_df, morpho_df, spectral_df, tdl_df]
    all_source_names = ["swot", "terrain", "morphometric", "spectral", "3dlakes"]

    # Find the largest source for prediction base
    largest = None
    for src, name in zip(all_sources, all_source_names):
        if src is not None and (largest is None or len(src) > len(largest)):
            largest = src

    if largest is not None:
        try:
            # Assemble features for all lakes (not just ground truth)
            # Use a dummy ground truth with all lake IDs
            id_col = "lake_id" if "lake_id" in largest.columns else "hylak_id"
            all_ids = largest[[id_col]].copy()
            all_ids["max_depth_m"] = np.nan  # Placeholder

            X_all, _, ids_all, _, _, _ = assemble_features(
                swot_df, terrain_df, morpho_df, spectral_df, tdl_df,
                all_ids, target="max_depth_m",
            )

            if len(X_all) > 0:
                all_preds = ensemble.predict(X_all)
                pred_df = pd.DataFrame({
                    "lake_id": ids_all,
                    "fusion_predicted_depth_m": all_preds,
                })
                pred_path = out / "fusion_all_predictions.parquet"
                pred_df.to_parquet(str(pred_path), index=False)
                log.info(f"All-lake predictions saved: {pred_path} ({len(pred_df)} lakes)")
        except Exception as e:
            log.warning(f"All-lake prediction failed: {e}")

    log.info("\nFusion model training complete!")


if __name__ == "__main__":
    main()
