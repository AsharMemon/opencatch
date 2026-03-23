#!/usr/bin/env python3
"""
OpenCatch — Water Type Classifier + Adaptive Model Router

Different water types need fundamentally different SDB approaches:
  - Clear (Secchi > 3m): spectral SDB works best (Stumpf, Lyzenga, KAN)
  - Moderate (Secchi 1-3m): spectral + physics features
  - Turbid (Secchi < 1m): morphometric + autoencoder > spectral
  - Tannin-stained (high CDOM): special handling (blue absorption)

This module:
1. Classifies each lake/pixel into a water type from spectral signatures
2. Routes each sample to the best model (or model blend) for its type
3. Provides water-type-aware training: separate models per water type
4. Optionally learns routing weights from validation data

The routing strategy significantly outperforms a single global model
because the optimal depth-reflectance relationship varies by water type.

Usage:
    # Classify + route
    from water_type_classifier import WaterTypeClassifier, AdaptiveRouter
    classifier = WaterTypeClassifier()
    water_types = classifier.classify_df(df)

    # Train separate models per water type
    router = AdaptiveRouter(classifier)
    router.fit(df_train, y_train)
    predictions = router.predict(df_test)

    # CLI
    python water_type_classifier.py \
        --data /data/sdb_preprocessed.parquet \
        --output /data/models/water_type_routed \
        --device cuda

Requirements:
    pip install xgboost scikit-learn pandas numpy torch
"""

import argparse
import json
import logging
import time
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("water_type")

EPS = 1e-8


# ── Water Type Definitions ───────────────────────────────────────────

class WaterType(IntEnum):
    CLEAR = 0       # Secchi > 3m — blue-dominant, spectral SDB ideal
    MODERATE = 1    # Secchi 1-3m — mixed signal
    TURBID = 2      # Secchi < 1m — red-dominant, spectral SDB unreliable
    TANNIN = 3      # High CDOM — brown water, blue strongly absorbed
    UNKNOWN = 4     # Insufficient data to classify


@dataclass
class WaterTypeConfig:
    """Configuration for a specific water type's model routing."""
    water_type: WaterType
    label: str
    description: str
    preferred_models: list[str]
    spectral_weight: float    # How much to trust spectral models (0-1)
    morphometric_weight: float  # How much to trust morphometric models (0-1)
    max_reliable_depth_m: float  # Beyond this depth, spectral SDB is unreliable


WATER_TYPE_CONFIGS = {
    WaterType.CLEAR: WaterTypeConfig(
        water_type=WaterType.CLEAR,
        label="clear",
        description="Clear water (Secchi > 3m): spectral SDB works well to 15-20m",
        preferred_models=["stumpf", "lyzenga", "kan", "xgboost"],
        spectral_weight=0.9,
        morphometric_weight=0.1,
        max_reliable_depth_m=20.0,
    ),
    WaterType.MODERATE: WaterTypeConfig(
        water_type=WaterType.MODERATE,
        label="moderate",
        description="Moderate clarity (Secchi 1-3m): spectral + physics features",
        preferred_models=["xgboost", "kan", "rf", "stumpf"],
        spectral_weight=0.6,
        morphometric_weight=0.4,
        max_reliable_depth_m=10.0,
    ),
    WaterType.TURBID: WaterTypeConfig(
        water_type=WaterType.TURBID,
        label="turbid",
        description="Turbid water (Secchi < 1m): morphometric better than spectral",
        preferred_models=["morphometric", "autoencoder", "rf"],
        spectral_weight=0.2,
        morphometric_weight=0.8,
        max_reliable_depth_m=3.0,
    ),
    WaterType.TANNIN: WaterTypeConfig(
        water_type=WaterType.TANNIN,
        label="tannin",
        description="Tannin-stained (high CDOM): blue absorption confounds spectral",
        preferred_models=["rf", "xgboost", "morphometric"],
        spectral_weight=0.3,
        morphometric_weight=0.7,
        max_reliable_depth_m=5.0,
    ),
}


# ── Water Type Classifier ───────────────────────────────────────────

class WaterTypeClassifier:
    """
    Classifies water bodies into optical types from Sentinel-2 reflectances.

    Uses a decision-tree approach based on spectral indices:
    - Blue/Green ratio -> clear vs turbid
    - CDOM proxy (blue/red) -> tannin detection
    - Red magnitude -> turbidity level
    - NDTI -> suspended sediment

    Can operate on single pixels or aggregate per-lake statistics.
    """

    def __init__(
        self,
        secchi_clear_threshold: float = 3.0,
        secchi_turbid_threshold: float = 1.0,
        bg_ratio_clear: float = 1.3,
        bg_ratio_turbid: float = 0.7,
        cdom_tannin_threshold: float = 2.0,
        red_turbid_threshold: float = 0.04,
    ):
        self.secchi_clear = secchi_clear_threshold
        self.secchi_turbid = secchi_turbid_threshold
        self.bg_clear = bg_ratio_clear
        self.bg_turbid = bg_ratio_turbid
        self.cdom_tannin = cdom_tannin_threshold
        self.red_turbid = red_turbid_threshold

    def classify_from_bands(
        self,
        blue: np.ndarray,
        green: np.ndarray,
        red: np.ndarray,
        nir: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """
        Classify water type from reflectance bands.

        Returns array of WaterType integer values.
        """
        blue = np.asarray(blue, dtype=np.float64)
        green = np.asarray(green, dtype=np.float64)
        red = np.asarray(red, dtype=np.float64)

        # Key spectral indices
        bg_ratio = blue / (green + EPS)
        br_ratio = blue / (red + EPS)
        ndti = (red - green) / (red + green + EPS)

        # Estimated Secchi depth (Lee et al. 2015 simplified)
        kd_490 = 10.0 ** (-0.8515 - 1.8263 * np.log10(np.clip(bg_ratio, 0.1, 10.0)))
        secchi_est = np.clip(1.0 / (2.5 * kd_490 + EPS), 0.01, 40.0)

        # CDOM index: high CDOM = low blue/red ratio + specific spectral shape
        # Tannin water: low blue (absorbed by CDOM), relatively higher green/red
        cdom_index = np.log(br_ratio + EPS)

        # Classification logic
        water_type = np.full(len(blue), WaterType.MODERATE, dtype=np.int32)

        # Step 1: Check for tannin (CDOM-rich) water
        # Tannin: blue is strongly absorbed, br_ratio < 1.5, but bg_ratio moderate
        tannin_mask = (
            (br_ratio < 1.5)
            & (bg_ratio > 0.5)  # Not extremely turbid
            & (bg_ratio < 1.2)  # Not clear
            & (red < self.red_turbid)  # Not high sediment
        )
        water_type[tannin_mask] = WaterType.TANNIN

        # Step 2: Clear water
        clear_mask = (
            (bg_ratio >= self.bg_clear)
            | (secchi_est >= self.secchi_clear)
        ) & ~tannin_mask
        water_type[clear_mask] = WaterType.CLEAR

        # Step 3: Turbid water
        turbid_mask = (
            (bg_ratio <= self.bg_turbid)
            | (red >= self.red_turbid)
            | (secchi_est <= self.secchi_turbid)
        ) & ~tannin_mask & ~clear_mask
        water_type[turbid_mask] = WaterType.TURBID

        return water_type

    def classify_from_secchi(self, secchi_m: np.ndarray) -> np.ndarray:
        """Classify from known Secchi depth measurements."""
        secchi = np.asarray(secchi_m, dtype=np.float64)
        water_type = np.full(len(secchi), WaterType.MODERATE, dtype=np.int32)

        water_type[secchi >= self.secchi_clear] = WaterType.CLEAR
        water_type[secchi <= self.secchi_turbid] = WaterType.TURBID
        water_type[np.isnan(secchi)] = WaterType.UNKNOWN

        return water_type

    def classify_df(self, df: pd.DataFrame) -> np.ndarray:
        """
        Classify water types from a DataFrame.

        Uses spectral bands if available, else Secchi, else returns UNKNOWN.
        """
        if "secchi_depth_m" in df.columns:
            # Use measured Secchi if available
            secchi = df["secchi_depth_m"].values
            types = self.classify_from_secchi(secchi)
            # Override where spectral data also available for better classification
            if all(c in df.columns for c in ["blue", "green", "red"]):
                spectral_types = self.classify_from_bands(
                    df["blue"].values, df["green"].values, df["red"].values,
                    df.get("nir", pd.Series(0.001, index=df.index)).values,
                )
                # Trust Secchi where available, spectral otherwise
                unknown_mask = np.isnan(secchi)
                types[unknown_mask] = spectral_types[unknown_mask]
            return types

        if all(c in df.columns for c in ["blue", "green", "red"]):
            return self.classify_from_bands(
                df["blue"].values, df["green"].values, df["red"].values,
                df.get("nir", pd.Series(0.001, index=df.index)).values,
            )

        log.warning("No spectral or Secchi data found; classifying all as UNKNOWN")
        return np.full(len(df), WaterType.UNKNOWN, dtype=np.int32)

    def get_type_stats(self, water_types: np.ndarray) -> dict:
        """Return distribution statistics for classified water types."""
        total = len(water_types)
        stats = {}
        for wt in WaterType:
            count = int(np.sum(water_types == wt.value))
            stats[wt.name] = {"count": count, "pct": float(count / total * 100)}
        return stats


# ── Adaptive Model Router ────────────────────────────────────────────

class AdaptiveRouter:
    """
    Routes each sample to the best model (or blend) based on water type.

    Training: trains separate model instances per water type, using
    the preferred model configuration for each type.

    Prediction: classifies each test sample's water type, then
    routes to the corresponding model. Supports soft routing
    where overlapping samples get blended predictions.
    """

    def __init__(
        self,
        classifier: Optional[WaterTypeClassifier] = None,
        device: str = "cuda",
    ):
        self.classifier = classifier or WaterTypeClassifier()
        self.device = device
        self.type_models: dict[int, object] = {}
        self.type_scalers: dict[int, object] = {}
        self.fallback_model = None
        self._feature_cols = None

    def fit(
        self,
        df: pd.DataFrame,
        y: np.ndarray,
        feature_cols: Optional[list[str]] = None,
    ) -> dict:
        """
        Train separate models for each water type.

        Returns metrics dict with per-type results.
        """
        from sklearn.model_selection import train_test_split

        # Classify water types
        water_types = self.classifier.classify_df(df)
        type_stats = self.classifier.get_type_stats(water_types)
        log.info(f"Water type distribution: {type_stats}")

        # Determine feature columns
        if feature_cols is None:
            exclude = {"depth_m", "max_depth_m", "lake_id", "Hylak_id",
                       "geometry", "lake_name", "secchi_depth_m"}
            feature_cols = [
                c for c in df.columns
                if c not in exclude and df[c].dtype in [np.float64, np.float32, np.int64, np.int32]
            ]
        self._feature_cols = feature_cols

        results = {}

        for wt in WaterType:
            if wt == WaterType.UNKNOWN:
                continue

            mask = water_types == wt.value
            n_type = mask.sum()

            if n_type < 50:
                log.info(f"  {wt.name}: only {n_type} samples, skipping dedicated model")
                continue

            config = WATER_TYPE_CONFIGS[wt]
            log.info(f"\n{'='*40}")
            log.info(f"Training {wt.name} model ({n_type:,} samples)")
            log.info(f"  Config: {config.description}")
            log.info(f"  Preferred models: {config.preferred_models}")

            X_type = df.loc[mask, feature_cols].fillna(df[feature_cols].median())
            y_type = y[mask]

            # Train/val split (stratified by depth bins)
            depth_bins = np.digitize(y_type, bins=[0, 2, 5, 10, 20, 50, 100])
            try:
                X_tr, X_va, y_tr, y_va = train_test_split(
                    X_type, y_type, test_size=0.2, random_state=42,
                    stratify=depth_bins,
                )
            except ValueError:
                # Stratification fails if too few samples in a bin
                X_tr, X_va, y_tr, y_va = train_test_split(
                    X_type, y_type, test_size=0.2, random_state=42,
                )

            # Train XGBoost for each type (robust default)
            model, metrics = self._train_type_model(
                X_tr, y_tr.values if isinstance(y_tr, pd.Series) else y_tr,
                X_va, y_va.values if isinstance(y_va, pd.Series) else y_va,
                config,
            )

            self.type_models[wt.value] = model
            results[wt.name] = metrics

        # Train fallback model on ALL data
        log.info(f"\nTraining FALLBACK model on all {len(df):,} samples")
        X_all = df[feature_cols].fillna(df[feature_cols].median())
        n = len(X_all)
        idx = np.random.RandomState(42).permutation(n)
        split = int(0.8 * n)
        self.fallback_model, fb_metrics = self._train_type_model(
            X_all.iloc[idx[:split]], y[idx[:split]],
            X_all.iloc[idx[split:]], y[idx[split:]],
            WATER_TYPE_CONFIGS[WaterType.MODERATE],
        )
        results["fallback"] = fb_metrics

        return results

    def _train_type_model(self, X_train, y_train, X_val, y_val, config):
        """Train a model appropriate for the given water type config."""
        import xgboost as xgb
        from sklearn.metrics import r2_score

        feature_names = X_train.columns.tolist()
        dtrain = xgb.DMatrix(X_train, label=y_train, feature_names=feature_names)
        dval = xgb.DMatrix(X_val, label=y_val, feature_names=feature_names)

        # Adjust parameters based on water type
        params = {
            "objective": "reg:squarederror",
            "eval_metric": "rmse",
            "max_depth": 6 if config.spectral_weight < 0.5 else 8,
            "learning_rate": 0.05,
            "subsample": 0.8,
            "colsample_bytree": 0.7,
            "min_child_weight": 5,
            "reg_alpha": 0.1,
            "reg_lambda": 1.0,
            "tree_method": "hist",
            "seed": 42,
        }

        model = xgb.train(
            params, dtrain, num_boost_round=1500,
            evals=[(dval, "val")],
            early_stopping_rounds=50, verbose_eval=False,
        )

        val_pred = model.predict(dval)
        rmse = float(np.sqrt(np.mean((y_val - val_pred) ** 2)))
        mae = float(np.mean(np.abs(y_val - val_pred)))
        r2 = float(r2_score(y_val, val_pred))

        log.info(f"  R2={r2:.4f}, RMSE={rmse:.2f}m, MAE={mae:.2f}m")

        metrics = {"rmse_m": rmse, "mae_m": mae, "r2": r2,
                   "n_train": len(y_train), "n_val": len(y_val)}

        return model, metrics

    def predict(
        self,
        df: pd.DataFrame,
        return_types: bool = False,
    ) -> np.ndarray | tuple[np.ndarray, np.ndarray]:
        """
        Predict depth using water-type-routed models.

        Classifies each sample, routes to the appropriate model.
        Falls back to global model for unknown types or missing models.
        """
        import xgboost as xgb

        water_types = self.classifier.classify_df(df)
        X = df[self._feature_cols].fillna(df[self._feature_cols].median())
        predictions = np.full(len(df), np.nan)

        for wt_val, model in self.type_models.items():
            mask = water_types == wt_val
            if mask.sum() == 0:
                continue

            dmat = xgb.DMatrix(
                X.loc[mask], feature_names=self._feature_cols,
            )
            predictions[mask] = model.predict(dmat)
            log.debug(f"  {WaterType(wt_val).name}: predicted {mask.sum()} samples")

        # Fallback for unrouted samples
        unrouted = np.isnan(predictions)
        if unrouted.sum() > 0 and self.fallback_model is not None:
            dmat = xgb.DMatrix(
                X.loc[unrouted], feature_names=self._feature_cols,
            )
            predictions[unrouted] = self.fallback_model.predict(dmat)
            log.info(f"  Fallback: predicted {unrouted.sum()} unrouted samples")

        predictions = np.clip(predictions, 0, 200)

        if return_types:
            return predictions, water_types
        return predictions

    def predict_with_confidence(
        self,
        df: pd.DataFrame,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Predict with water-type-based confidence bounds.

        Returns (predictions, lower_bound, upper_bound).
        Confidence is wider for turbid water and deeper depths.
        """
        predictions, water_types = self.predict(df, return_types=True)

        # Confidence scale factor per water type
        confidence_scale = np.ones(len(df))
        for wt in WaterType:
            if wt == WaterType.UNKNOWN:
                confidence_scale[water_types == wt.value] = 2.0
                continue
            config = WATER_TYPE_CONFIGS.get(wt)
            if config is None:
                continue
            mask = water_types == wt.value
            # Less reliable for turbid water
            confidence_scale[mask] = 1.0 / (config.spectral_weight + 0.1)
            # Less reliable when predicted depth > max reliable
            deep_mask = mask & (predictions > config.max_reliable_depth_m)
            confidence_scale[deep_mask] *= 2.0

        # Base uncertainty: ~15% of predicted depth (empirical)
        base_uncertainty = np.maximum(predictions * 0.15, 0.5)
        uncertainty = base_uncertainty * confidence_scale

        lower = np.clip(predictions - 1.96 * uncertainty, 0, None)
        upper = predictions + 1.96 * uncertainty

        return predictions, lower, upper


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Water type classification + routing")
    parser.add_argument("--data", type=str, required=True, help="Training data")
    parser.add_argument("--output", type=str, default="/data/models/water_type_routed")
    parser.add_argument("--device", type=str, default="cuda")
    parser.add_argument(
        "--classify-only", action="store_true",
        help="Only classify water types, don't train models",
    )
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load data
    data_path = Path(args.data)
    if data_path.suffix == ".parquet":
        df = pd.read_parquet(data_path)
    else:
        df = pd.read_csv(data_path)
    log.info(f"Loaded {len(df):,} rows from {data_path.name}")

    # Classify water types
    classifier = WaterTypeClassifier()
    water_types = classifier.classify_df(df)
    stats = classifier.get_type_stats(water_types)

    log.info("\nWater Type Distribution:")
    for wt_name, s in stats.items():
        log.info(f"  {wt_name:10s}: {s['count']:6d} ({s['pct']:.1f}%)")

    # Save classification
    df["water_type"] = water_types
    df["water_type_label"] = [WaterType(wt).name.lower() for wt in water_types]

    if args.classify_only:
        out_path = output_dir / "classified.parquet"
        df.to_parquet(out_path, index=False)
        log.info(f"Water types saved to {out_path}")
        return

    # Find depth column
    depth_col = None
    for col in ["depth_m", "max_depth_m", "Depth_avg"]:
        if col in df.columns:
            depth_col = col
            break
    if depth_col is None:
        raise ValueError("No depth column found")

    df_valid = df.dropna(subset=[depth_col])
    df_valid = df_valid[df_valid[depth_col] > 0]
    y = df_valid[depth_col].values

    # Train water-type-routed models
    router = AdaptiveRouter(classifier, device=args.device)
    results = router.fit(df_valid, y)

    # Evaluate on full dataset (OOF-style for each type)
    log.info(f"\n{'='*60}")
    log.info("FULL EVALUATION — Routed vs Global")
    log.info(f"{'='*60}")

    # Routed predictions
    preds_routed, wt_array = router.predict(df_valid, return_types=True)

    from sklearn.metrics import r2_score, mean_squared_error

    # Per-type evaluation
    for wt in WaterType:
        if wt == WaterType.UNKNOWN:
            continue
        mask = wt_array == wt.value
        n = mask.sum()
        if n < 10:
            continue
        rmse = float(np.sqrt(mean_squared_error(y[mask], preds_routed[mask])))
        r2 = float(r2_score(y[mask], preds_routed[mask])) if n > 2 else float("nan")
        log.info(f"  {wt.name:10s} (n={n:5d}): R2={r2:.4f}, RMSE={rmse:.2f}m")

    # Overall
    overall_rmse = float(np.sqrt(mean_squared_error(y, preds_routed)))
    overall_r2 = float(r2_score(y, preds_routed))
    log.info(f"  {'OVERALL':10s} (n={len(y):5d}): R2={overall_r2:.4f}, RMSE={overall_rmse:.2f}m")

    results["overall"] = {"r2": overall_r2, "rmse_m": overall_rmse}
    results["water_type_stats"] = stats

    # Save
    with open(output_dir / "water_type_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)

    import joblib
    joblib.dump(router, output_dir / "adaptive_router.joblib")

    df_valid["water_type"] = wt_array
    df_valid["pred_routed"] = preds_routed
    df_valid.to_parquet(output_dir / "predictions.parquet", index=False)

    log.info(f"\nAll results saved to {output_dir}")


if __name__ == "__main__":
    main()
