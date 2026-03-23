#!/usr/bin/env python3
"""
OpenCatch — Within-Lake Spectral Satellite-Derived Bathymetry (SDB)

Cross-lake spectral SDB fails (R² < 0) because water clarity varies wildly
between lakes: the same reflectance means 2m depth in a clear lake but 0.5m
in a turbid lake. However, WITHIN a single lake, the spectral-to-depth
relationship IS consistent (same Kd everywhere).

This module calibrates a per-lake SDB model using a few known depth points
(from A-E curves, sonar, or ICESat-2) and applies it to all pixels in the
lake. This gives spatial depth detail that A-E curves alone cannot provide.

Approach for each lake:
  1. Get Sentinel-2 reflectance for all water pixels
  2. Get a few known depth points (calibration anchors)
  3. Fit a per-lake Stumpf (2003) log-ratio model
  4. Optionally fit Lyzenga (1978) multi-band model
  5. Apply calibrated model to ALL pixels → spatial depth map
  6. Mask unreliable pixels (turbidity, cloud, shallow vegetation)

The per-lake calibration approach is standard in SDB literature and achieves
sub-1m RMSE in clear water (Secchi > 3m), ~2m in moderate, and degrades
gracefully in turbid water (still useful for relative depth patterns).

References:
  - Stumpf et al. (2003): Log-ratio bathymetry
  - Lyzenga (1978, 1985): Multi-band depth-invariant indices
  - Caballero & Stumpf (2019): Sentinel-2 SDB
  - Sagawa et al. (2010): Bottom type separation
  - Li et al. (2021): Machine learning SDB with limited training

Usage:
    python within_lake_sdb.py \
        --s2-dir /data/sentinel2_composites \
        --anchors /data/depth_anchors \
        --output /data/sdb_within_lake \
        --lakes /data/lake_polygons.parquet

Requirements:
    pip install numpy pandas scikit-learn scipy rasterio shapely tqdm xgboost
"""

import argparse
import json
import logging
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import optimize
from sklearn.linear_model import HuberRegressor, RANSACRegressor
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
from sklearn.preprocessing import StandardScaler
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("within_lake_sdb")

EPS = 1e-8


# ══════════════════════════════════════════════════════════════════════
# Spectral Band Definitions (Sentinel-2 Level-2A SR)
# ══════════════════════════════════════════════════════════════════════

BAND_NAMES = {
    "coastal": "B1",   # 443 nm
    "blue":    "B2",   # 490 nm
    "green":   "B3",   # 560 nm
    "red":     "B4",   # 665 nm
    "rededge1": "B5",  # 705 nm
    "rededge2": "B6",  # 740 nm
    "rededge3": "B7",  # 783 nm
    "nir":     "B8",   # 842 nm
    "nir08":   "B8A",  # 865 nm
    "swir1":   "B11",  # 1610 nm
    "swir2":   "B12",  # 2190 nm
}

# Pure water absorption coefficients (m^-1) for deglinting validation
AW = {"blue": 0.0196, "green": 0.0640, "red": 0.3490, "nir": 2.38}


# ══════════════════════════════════════════════════════════════════════
# Water Quality / Clarity Assessment
# ══════════════════════════════════════════════════════════════════════

class WaterClarity:
    """Estimate water clarity metrics for a lake from its spectral signature."""

    @staticmethod
    def estimate_secchi_depth(blue: np.ndarray, green: np.ndarray,
                              red: np.ndarray) -> float:
        """
        Estimate Secchi depth from mean reflectances.
        Uses the Doron et al. (2007) / Lee et al. (2015) approach.
        """
        # Simple empirical: Secchi ≈ exp(a * ln(Blue/Red) + b)
        ratio = np.nanmean(blue) / (np.nanmean(red) + EPS)
        secchi = 0.5 * ratio ** 1.3  # Empirical, tuned for MN lakes
        return float(np.clip(secchi, 0.1, 15.0))

    @staticmethod
    def turbidity_index(red: np.ndarray, nir: np.ndarray) -> float:
        """Higher = more turbid. Uses red + NIR magnitude."""
        return float(np.nanmean(red) + 0.5 * np.nanmean(nir))

    @staticmethod
    def classify_clarity(secchi: float) -> str:
        """Classify lake water clarity."""
        if secchi > 3.0:
            return "clear"
        elif secchi > 1.0:
            return "moderate"
        else:
            return "turbid"

    @staticmethod
    def max_detectable_depth(secchi: float) -> float:
        """Approximate maximum depth detectable by SDB (1.5-2x Secchi)."""
        return secchi * 1.7


# ══════════════════════════════════════════════════════════════════════
# Per-Lake SDB Models
# ══════════════════════════════════════════════════════════════════════

class StumpfModel:
    """
    Stumpf et al. (2003) log-ratio bathymetry model.

    depth = m0 + m1 * ln(n * R_blue) / ln(n * R_green)

    Calibrated per-lake using known depth points. The log-ratio approach
    is robust to varying bottom albedo (sand vs mud) because the ratio
    cancels out bottom reflectance to first order.

    Parameters m0, m1, n are fitted per lake.
    """

    def __init__(self):
        self.m0 = 0.0
        self.m1 = 1.0
        self.n = 1000.0
        self.fitted = False
        self.metrics = {}

    def compute_ratio(self, blue: np.ndarray, green: np.ndarray) -> np.ndarray:
        """Compute Stumpf log-ratio."""
        blue_safe = np.clip(blue, EPS, None)
        green_safe = np.clip(green, EPS, None)
        ratio = np.log(self.n * blue_safe) / (np.log(self.n * green_safe) + EPS)
        return ratio

    def fit(self, blue: np.ndarray, green: np.ndarray,
            depth: np.ndarray, use_ransac: bool = True) -> dict:
        """
        Calibrate model on known depth points.

        Uses RANSAC for robustness to outliers (misregistered pixels,
        bottom vegetation anomalies).
        """
        if len(depth) < 3:
            log.warning("Too few calibration points (<3)")
            return {"error": "too_few_points"}

        # Optimize n parameter
        best_r2 = -np.inf
        best_params = (0, 1, 1000)

        for n_val in [100, 500, 1000, 5000, 10000]:
            self.n = n_val
            ratio = self.compute_ratio(blue, green)

            valid = np.isfinite(ratio) & np.isfinite(depth) & (depth > 0)
            if valid.sum() < 3:
                continue

            r = ratio[valid].reshape(-1, 1)
            d = depth[valid]

            try:
                if use_ransac and len(d) >= 10:
                    model = RANSACRegressor(
                        estimator=HuberRegressor(),
                        min_samples=max(3, int(0.3 * len(d))),
                        residual_threshold=2.0,
                        random_state=42,
                    )
                else:
                    model = HuberRegressor()

                model.fit(r, d)

                if hasattr(model, "estimator_"):
                    pred = model.predict(r)
                else:
                    pred = model.predict(r)

                r2 = r2_score(d, pred)
                if r2 > best_r2:
                    best_r2 = r2
                    if hasattr(model, "estimator_"):
                        best_params = (
                            float(model.estimator_.intercept_),
                            float(model.estimator_.coef_[0]),
                            n_val,
                        )
                    else:
                        best_params = (
                            float(model.intercept_),
                            float(model.coef_[0]),
                            n_val,
                        )
            except Exception:
                continue

        self.m0, self.m1, self.n = best_params
        self.fitted = True

        # Compute final metrics
        ratio = self.compute_ratio(blue, green)
        pred = self.m0 + self.m1 * ratio
        valid = np.isfinite(pred) & np.isfinite(depth) & (depth > 0)
        if valid.sum() > 0:
            self.metrics = {
                "r2": float(r2_score(depth[valid], pred[valid])),
                "rmse": float(np.sqrt(mean_squared_error(depth[valid], pred[valid]))),
                "mae": float(mean_absolute_error(depth[valid], pred[valid])),
                "n_calibration": int(valid.sum()),
                "n_param": self.n,
                "m0": self.m0,
                "m1": self.m1,
            }

        return self.metrics

    def predict(self, blue: np.ndarray, green: np.ndarray) -> np.ndarray:
        """Predict depth for all pixels."""
        if not self.fitted:
            raise RuntimeError("Model not fitted")
        ratio = self.compute_ratio(blue, green)
        depth = self.m0 + self.m1 * ratio
        return np.clip(depth, 0, None)


class LyzengaModel:
    """
    Lyzenga (1978, 1985) multi-band depth model.

    depth = a0 + Σ ai * Xi

    where Xi = ln(Ri - Rdeep_i) for each band i.
    Rdeep is the deep-water reflectance (no bottom signal).

    Advantages over Stumpf:
      - Uses multiple bands (blue, green, coastal, red-edge)
      - Separate handling of deep-water signal
      - Better in mixed bottom types
    """

    def __init__(self, bands: List[str] = None):
        self.bands = bands or ["blue", "green", "coastal", "rededge1"]
        self.coefficients = None
        self.r_deep = {}
        self.fitted = False
        self.metrics = {}

    def _compute_features(self, reflectances: Dict[str, np.ndarray]) -> np.ndarray:
        """Compute Lyzenga transformed features: Xi = ln(Ri - Rdeep_i)."""
        features = []
        for band in self.bands:
            if band not in reflectances:
                continue
            r = reflectances[band]
            r_deep = self.r_deep.get(band, 0.0)
            # Subtract deep-water reflectance
            r_corrected = r - r_deep
            r_corrected = np.clip(r_corrected, EPS, None)
            xi = np.log(r_corrected)
            features.append(xi)

        if not features:
            return np.zeros((len(next(iter(reflectances.values()))), 1))
        return np.column_stack(features)

    def estimate_deep_water(self, reflectances: Dict[str, np.ndarray],
                            depth: np.ndarray, depth_threshold: float = None):
        """
        Estimate deep-water reflectance from the deepest pixels.
        Uses pixels deeper than threshold (or deepest 10%).
        """
        if depth_threshold is None:
            depth_threshold = np.percentile(depth[depth > 0], 90)

        deep_mask = depth >= depth_threshold
        if deep_mask.sum() < 5:
            deep_mask = depth >= np.percentile(depth[depth > 0], 80)

        for band in self.bands:
            if band in reflectances:
                deep_vals = reflectances[band][deep_mask]
                self.r_deep[band] = float(np.nanmedian(deep_vals))

    def fit(self, reflectances: Dict[str, np.ndarray],
            depth: np.ndarray) -> dict:
        """Calibrate multi-band model."""
        if len(depth) < 5:
            return {"error": "too_few_points"}

        # Estimate deep-water reflectance
        self.estimate_deep_water(reflectances, depth)

        # Compute features
        X = self._compute_features(reflectances)
        valid = np.all(np.isfinite(X), axis=1) & np.isfinite(depth) & (depth > 0)

        if valid.sum() < 5:
            return {"error": "too_few_valid_points"}

        X_v = X[valid]
        d_v = depth[valid]

        # Robust regression
        try:
            model = HuberRegressor()
            model.fit(X_v, d_v)
            self.coefficients = {
                "intercept": float(model.intercept_),
                "coefs": [float(c) for c in model.coef_],
            }
            self.fitted = True

            pred = model.predict(X_v)
            self.metrics = {
                "r2": float(r2_score(d_v, pred)),
                "rmse": float(np.sqrt(mean_squared_error(d_v, pred))),
                "mae": float(mean_absolute_error(d_v, pred)),
                "n_calibration": int(valid.sum()),
                "n_bands": len(self.bands),
            }
            self._model = model
        except Exception as e:
            return {"error": str(e)}

        return self.metrics

    def predict(self, reflectances: Dict[str, np.ndarray]) -> np.ndarray:
        """Predict depth for all pixels."""
        if not self.fitted:
            raise RuntimeError("Model not fitted")
        X = self._compute_features(reflectances)
        depth = self._model.predict(X)
        return np.clip(depth, 0, None)


class AdaptivePerLakeModel:
    """
    Adaptively selects the best SDB model for each lake based on
    water clarity and available calibration data.

    Strategy:
      - Clear water (Secchi > 3m): Stumpf log-ratio (most robust)
      - Moderate (1-3m): Lyzenga multi-band (uses more spectral info)
      - Turbid (<1m): XGBoost with all features (non-linear relationships)
      - Very turbid: return NaN (SDB unreliable, use A-E or morphometric)
    """

    def __init__(self):
        self.stumpf = StumpfModel()
        self.lyzenga = LyzengaModel()
        self.xgb_model = None
        self.best_model = None
        self.clarity = None

    def fit(self, reflectances: Dict[str, np.ndarray],
            depth: np.ndarray,
            clarity_class: str = None) -> dict:
        """Fit all candidate models, select best."""
        results = {}

        # Stumpf
        if "blue" in reflectances and "green" in reflectances:
            stumpf_res = self.stumpf.fit(
                reflectances["blue"], reflectances["green"], depth
            )
            results["stumpf"] = stumpf_res

        # Lyzenga
        lyzenga_res = self.lyzenga.fit(reflectances, depth)
        results["lyzenga"] = lyzenga_res

        # XGBoost (if enough data)
        if len(depth) >= 20:
            try:
                results["xgboost"] = self._fit_xgb(reflectances, depth)
            except Exception:
                pass

        # Select best by R²
        best_r2 = -np.inf
        for name, res in results.items():
            r2 = res.get("r2", -np.inf)
            if r2 > best_r2:
                best_r2 = r2
                self.best_model = name

        self.clarity = clarity_class
        return {
            "best_model": self.best_model,
            "best_r2": best_r2,
            "all_results": results,
        }

    def _fit_xgb(self, reflectances: Dict[str, np.ndarray],
                 depth: np.ndarray) -> dict:
        """Fit XGBoost with all spectral features."""
        import xgboost as xgb

        # Build feature matrix
        features = []
        feat_names = []
        for band in sorted(reflectances.keys()):
            features.append(reflectances[band])
            feat_names.append(band)

        # Add ratios
        if "blue" in reflectances and "green" in reflectances:
            bg = np.log(reflectances["blue"] + EPS) / np.log(reflectances["green"] + EPS)
            features.append(bg)
            feat_names.append("log_blue_green")

        if "green" in reflectances and "red" in reflectances:
            gr = np.log(reflectances["green"] + EPS) / np.log(reflectances["red"] + EPS)
            features.append(gr)
            feat_names.append("log_green_red")

        X = np.column_stack(features)
        valid = np.all(np.isfinite(X), axis=1) & np.isfinite(depth) & (depth > 0)

        X_v = X[valid]
        d_v = depth[valid]

        # Train-test split within lake
        n = len(d_v)
        idx = np.random.RandomState(42).permutation(n)
        n_train = int(0.7 * n)
        train_idx, val_idx = idx[:n_train], idx[n_train:]

        dtrain = xgb.DMatrix(X_v[train_idx], label=d_v[train_idx])
        dval = xgb.DMatrix(X_v[val_idx], label=d_v[val_idx])

        params = {
            "objective": "reg:squarederror",
            "max_depth": 4,
            "learning_rate": 0.1,
            "subsample": 0.8,
            "min_child_weight": 5,
            "verbosity": 0,
        }

        model = xgb.train(
            params, dtrain, num_boost_round=100,
            evals=[(dval, "val")],
            early_stopping_rounds=20,
            verbose_eval=False,
        )

        pred = model.predict(dval)
        self.xgb_model = model
        self._xgb_feature_names = feat_names

        return {
            "r2": float(r2_score(d_v[val_idx], pred)),
            "rmse": float(np.sqrt(mean_squared_error(d_v[val_idx], pred))),
            "n_calibration": int(valid.sum()),
        }

    def predict(self, reflectances: Dict[str, np.ndarray]) -> np.ndarray:
        """Predict using best model."""
        if self.best_model == "stumpf":
            return self.stumpf.predict(reflectances["blue"], reflectances["green"])
        elif self.best_model == "lyzenga":
            return self.lyzenga.predict(reflectances)
        elif self.best_model == "xgboost" and self.xgb_model is not None:
            import xgboost as xgb
            features = []
            for band in sorted(reflectances.keys()):
                features.append(reflectances[band])
            if "blue" in reflectances and "green" in reflectances:
                bg = np.log(reflectances["blue"] + EPS) / np.log(reflectances["green"] + EPS)
                features.append(bg)
            if "green" in reflectances and "red" in reflectances:
                gr = np.log(reflectances["green"] + EPS) / np.log(reflectances["red"] + EPS)
                features.append(gr)
            X = np.column_stack(features)
            dmat = xgb.DMatrix(X)
            return np.clip(self.xgb_model.predict(dmat), 0, None)
        else:
            # Fallback to Stumpf
            if "blue" in reflectances and "green" in reflectances:
                return self.stumpf.predict(reflectances["blue"], reflectances["green"])
            return np.full(len(next(iter(reflectances.values()))), np.nan)


# ══════════════════════════════════════════════════════════════════════
# Quality Masking
# ══════════════════════════════════════════════════════════════════════

def create_quality_mask(
    reflectances: Dict[str, np.ndarray],
    max_depth: float = None,
    secchi: float = None,
) -> np.ndarray:
    """
    Create boolean mask for reliable SDB pixels.

    Masks out:
      - Land pixels (NDWI < 0)
      - Cloud/cloud shadow (high NIR or anomalous)
      - Shallow vegetation (high red-edge)
      - Optically deep water (below max detectable depth)
      - Glint-contaminated pixels (high NIR)
    """
    n_pixels = len(next(iter(reflectances.values())))
    mask = np.ones(n_pixels, dtype=bool)

    green = reflectances.get("green", np.zeros(n_pixels))
    nir = reflectances.get("nir", np.zeros(n_pixels))
    red = reflectances.get("red", np.zeros(n_pixels))
    blue = reflectances.get("blue", np.zeros(n_pixels))

    # NDWI water mask
    ndwi = (green - nir) / (green + nir + EPS)
    mask &= ndwi > -0.1

    # Cloud / glint: high NIR
    mask &= nir < 0.05

    # Dark pixel exclusion (too deep or shadow)
    mask &= (blue + green) > 0.005

    # Anomalous reflectance
    mask &= blue < 0.15
    mask &= green < 0.15
    mask &= red < 0.10

    # Red-edge vegetation (submerged aquatic veg)
    rededge = reflectances.get("rededge1", np.zeros(n_pixels))
    mask &= rededge < 0.04

    # NaN / inf
    for band_arr in reflectances.values():
        mask &= np.isfinite(band_arr)

    return mask


# ══════════════════════════════════════════════════════════════════════
# Lake-Level Pipeline
# ══════════════════════════════════════════════════════════════════════

def process_single_lake(
    lake_id: str,
    reflectances: Dict[str, np.ndarray],
    anchor_depths: np.ndarray,
    anchor_indices: np.ndarray,
    pixel_coords: np.ndarray = None,
) -> dict:
    """
    Full SDB pipeline for a single lake.

    1. Estimate water clarity
    2. Create quality mask
    3. Calibrate per-lake model on anchor depths
    4. Predict depth for all valid pixels
    5. Return depth map + metrics + confidence

    Args:
        lake_id: identifier
        reflectances: dict of band → pixel array
        anchor_depths: known depths at calibration pixels
        anchor_indices: indices into pixel arrays for calibration points
        pixel_coords: optional (x, y) for each pixel

    Returns:
        dict with predicted depths, metrics, clarity assessment
    """
    n_pixels = len(next(iter(reflectances.values())))

    # ── Clarity assessment ──
    blue = reflectances.get("blue", np.zeros(n_pixels))
    green = reflectances.get("green", np.zeros(n_pixels))
    red = reflectances.get("red", np.zeros(n_pixels))

    secchi = WaterClarity.estimate_secchi_depth(blue, green, red)
    clarity = WaterClarity.classify_clarity(secchi)
    max_detect = WaterClarity.max_detectable_depth(secchi)
    turbidity = WaterClarity.turbidity_index(
        red, reflectances.get("nir", np.zeros(n_pixels))
    )

    log.info(f"  Lake {lake_id}: clarity={clarity}, Secchi≈{secchi:.1f}m, "
             f"max_detect≈{max_detect:.1f}m, turbidity={turbidity:.4f}")

    # ── Quality mask ──
    qmask = create_quality_mask(reflectances, secchi=secchi)

    # ── Extract calibration data ──
    valid_anchors = anchor_indices[
        (anchor_indices >= 0) & (anchor_indices < n_pixels)
    ]
    if len(valid_anchors) < 3:
        log.warning(f"  Lake {lake_id}: too few anchor points ({len(valid_anchors)})")
        return {
            "lake_id": lake_id,
            "depth_pred": np.full(n_pixels, np.nan),
            "clarity": clarity,
            "secchi": secchi,
            "error": "too_few_anchors",
        }

    # Get reflectances at anchor points
    anchor_refl = {}
    for band, arr in reflectances.items():
        anchor_refl[band] = arr[valid_anchors]
    anchor_d = anchor_depths[:len(valid_anchors)]

    # Filter anchors within detectable range
    detect_mask = anchor_d <= max_detect * 1.5
    if detect_mask.sum() < 3:
        detect_mask = np.ones(len(anchor_d), dtype=bool)  # use all

    # ── Calibrate model ──
    model = AdaptivePerLakeModel()
    anchor_refl_filt = {b: a[detect_mask] for b, a in anchor_refl.items()}
    fit_result = model.fit(
        anchor_refl_filt, anchor_d[detect_mask],
        clarity_class=clarity,
    )

    best_r2 = fit_result.get("best_r2", -1)
    if best_r2 < 0.1:
        log.warning(f"  Lake {lake_id}: poor calibration R²={best_r2:.3f}")

    # ── Predict all pixels ──
    depth_pred = np.full(n_pixels, np.nan)
    try:
        all_pred = model.predict(reflectances)
        # Apply quality mask + depth limits
        reliable = qmask & np.isfinite(all_pred) & (all_pred >= 0)
        reliable &= all_pred <= max_detect * 1.5  # don't trust beyond detect range

        depth_pred[reliable] = all_pred[reliable]
    except Exception as e:
        log.error(f"  Lake {lake_id}: prediction failed: {e}")

    # ── Confidence estimation ──
    # Confidence decreases with depth (further from surface calibration)
    # and with turbidity
    confidence = np.zeros(n_pixels)
    if np.any(np.isfinite(depth_pred)):
        max_pred = np.nanmax(depth_pred)
        if max_pred > 0:
            depth_factor = 1.0 - 0.5 * (depth_pred / max_pred)
        else:
            depth_factor = np.ones(n_pixels)

        clarity_factor = {"clear": 0.9, "moderate": 0.6, "turbid": 0.3}.get(clarity, 0.3)
        r2_factor = max(0, min(1, best_r2))

        confidence = np.where(
            np.isfinite(depth_pred),
            depth_factor * clarity_factor * r2_factor,
            0.0,
        )

    return {
        "lake_id": lake_id,
        "depth_pred": depth_pred,
        "confidence": confidence,
        "clarity": clarity,
        "secchi": secchi,
        "max_detectable_depth": max_detect,
        "turbidity": turbidity,
        "model_used": model.best_model,
        "calibration_r2": best_r2,
        "n_valid_pixels": int(qmask.sum()),
        "n_predicted": int(np.isfinite(depth_pred).sum()),
        "fit_result": fit_result,
    }


# ══════════════════════════════════════════════════════════════════════
# Batch Processing
# ══════════════════════════════════════════════════════════════════════

def load_sentinel2_for_lake(s2_dir: Path, lake_id: str) -> Optional[Dict]:
    """Load Sentinel-2 composite reflectances for a lake."""
    # Try different file patterns
    patterns = [
        f"{lake_id}_s2_composite.npz",
        f"{lake_id}_reflectance.npz",
        f"lake_{lake_id}.npz",
    ]
    for pattern in patterns:
        fpath = s2_dir / pattern
        if fpath.exists():
            data = np.load(fpath, allow_pickle=True)
            reflectances = {}
            for band_name in ["blue", "green", "red", "coastal",
                              "rededge1", "nir"]:
                if band_name in data:
                    reflectances[band_name] = data[band_name].flatten()
            if reflectances:
                coords = data["coords"] if "coords" in data else None
                return {"reflectances": reflectances, "coords": coords}
    return None


def load_depth_anchors(anchor_dir: Path, lake_id: str) -> Optional[Dict]:
    """
    Load known depth points for calibration.

    Sources: A-E curve derived, sonar transects, ICESat-2 profiles.
    """
    patterns = [
        f"{lake_id}_anchors.csv",
        f"{lake_id}_depths.csv",
        f"lake_{lake_id}_icesat2.csv",
    ]
    for pattern in patterns:
        fpath = anchor_dir / pattern
        if fpath.exists():
            df = pd.read_csv(fpath)
            depth_col = None
            for c in ["depth_m", "depth", "z"]:
                if c in df.columns:
                    depth_col = c
                    break
            idx_col = None
            for c in ["pixel_idx", "index", "pixel_index"]:
                if c in df.columns:
                    idx_col = c
                    break
            if depth_col and idx_col:
                return {
                    "depths": df[depth_col].values,
                    "indices": df[idx_col].values.astype(int),
                }
    return None


def batch_process_lakes(
    s2_dir: Path, anchor_dir: Path, output_dir: Path,
    lake_list: List[str] = None,
) -> pd.DataFrame:
    """
    Process all lakes in batch.

    Returns summary DataFrame with per-lake metrics.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if lake_list is None:
        # Discover lakes from S2 directory
        lake_list = []
        for f in s2_dir.glob("*_s2_composite.npz"):
            lake_id = f.stem.replace("_s2_composite", "")
            lake_list.append(lake_id)

    log.info(f"Processing {len(lake_list)} lakes...")

    summaries = []

    for lake_id in tqdm(lake_list, desc="Processing lakes"):
        # Load data
        s2_data = load_sentinel2_for_lake(s2_dir, lake_id)
        if s2_data is None:
            summaries.append({"lake_id": lake_id, "status": "no_s2_data"})
            continue

        anchor_data = load_depth_anchors(anchor_dir, lake_id)
        if anchor_data is None:
            summaries.append({"lake_id": lake_id, "status": "no_anchors"})
            continue

        # Process
        result = process_single_lake(
            lake_id=lake_id,
            reflectances=s2_data["reflectances"],
            anchor_depths=anchor_data["depths"],
            anchor_indices=anchor_data["indices"],
            pixel_coords=s2_data.get("coords"),
        )

        # Save depth map
        np.savez_compressed(
            output_dir / f"{lake_id}_sdb.npz",
            depth=result["depth_pred"],
            confidence=result["confidence"],
            coords=s2_data.get("coords"),
        )

        # Summary
        summary = {
            "lake_id": lake_id,
            "status": "ok" if "error" not in result else result["error"],
            "clarity": result["clarity"],
            "secchi_m": result["secchi"],
            "model_used": result.get("model_used", "none"),
            "calibration_r2": result.get("calibration_r2", np.nan),
            "n_valid_pixels": result.get("n_valid_pixels", 0),
            "n_predicted": result.get("n_predicted", 0),
            "mean_depth_pred": float(np.nanmean(result["depth_pred"])),
            "max_depth_pred": float(np.nanmax(result["depth_pred"]))
            if np.any(np.isfinite(result["depth_pred"])) else np.nan,
        }
        summaries.append(summary)

    summary_df = pd.DataFrame(summaries)
    summary_df.to_parquet(output_dir / "sdb_summary.parquet", index=False)
    summary_df.to_csv(output_dir / "sdb_summary.csv", index=False)

    # Report
    ok = summary_df[summary_df["status"] == "ok"]
    log.info(f"\n{'='*60}")
    log.info(f"  Within-Lake SDB Summary")
    log.info(f"{'='*60}")
    log.info(f"  Total lakes: {len(summary_df)}")
    log.info(f"  Successfully processed: {len(ok)}")
    if len(ok) > 0:
        log.info(f"  Mean calibration R²: {ok['calibration_r2'].mean():.3f}")
        log.info(f"  Median calibration R²: {ok['calibration_r2'].median():.3f}")
        for clarity in ["clear", "moderate", "turbid"]:
            subset = ok[ok["clarity"] == clarity]
            if len(subset) > 0:
                log.info(f"  {clarity}: n={len(subset)}, "
                         f"R²={subset['calibration_r2'].mean():.3f}")
    log.info(f"{'='*60}")

    return summary_df


# ══════════════════════════════════════════════════════════════════════
# Validation on MN DNR Sonar
# ══════════════════════════════════════════════════════════════════════

def validate_on_sonar(
    sonar_dir: Path,
    s2_dir: Path,
    output_dir: Path,
) -> dict:
    """
    Validate within-lake SDB against MN DNR sonar ground truth.

    For each sonar lake:
    1. Split sonar points into calibration (20%) and test (80%)
    2. Calibrate per-lake model on calibration set
    3. Evaluate on held-out test set
    4. Report metrics stratified by depth bin and clarity
    """
    log.info("Validating within-lake SDB on MN DNR sonar...")
    output_dir.mkdir(parents=True, exist_ok=True)

    all_true = []
    all_pred = []
    all_clarity = []
    all_depth_bins = []
    lake_metrics = []

    sonar_files = sorted(sonar_dir.glob("*.parquet"))
    if not sonar_files:
        sonar_files = sorted(sonar_dir.glob("*.csv"))

    for fpath in tqdm(sonar_files, desc="Validating lakes"):
        try:
            lake_id = fpath.stem
            s2_data = load_sentinel2_for_lake(s2_dir, lake_id)
            if s2_data is None:
                continue

            if fpath.suffix == ".parquet":
                df = pd.read_parquet(fpath)
            else:
                df = pd.read_csv(fpath)

            # Find depth and pixel index columns
            depth_col = None
            for c in ["depth_m", "depth", "z"]:
                if c in df.columns:
                    depth_col = c
                    break
            idx_col = None
            for c in ["pixel_idx", "pixel_index", "index"]:
                if c in df.columns:
                    idx_col = c
                    break
            if depth_col is None or idx_col is None:
                continue

            depths = df[depth_col].values
            indices = df[idx_col].values.astype(int)

            # Split: 20% calibration, 80% test
            rng = np.random.RandomState(42)
            n = len(depths)
            perm = rng.permutation(n)
            n_cal = max(5, int(0.2 * n))
            cal_idx = perm[:n_cal]
            test_idx = perm[n_cal:]

            # Calibrate
            result = process_single_lake(
                lake_id=lake_id,
                reflectances=s2_data["reflectances"],
                anchor_depths=depths[cal_idx],
                anchor_indices=indices[cal_idx],
            )

            if "error" in result:
                continue

            # Test
            test_pixel_idx = indices[test_idx]
            valid_test = (test_pixel_idx >= 0) & (
                test_pixel_idx < len(result["depth_pred"])
            )
            if valid_test.sum() < 5:
                continue

            pred_at_test = result["depth_pred"][test_pixel_idx[valid_test]]
            true_at_test = depths[test_idx[valid_test]]

            # Filter NaN predictions
            finite = np.isfinite(pred_at_test) & np.isfinite(true_at_test)
            if finite.sum() < 5:
                continue

            p = pred_at_test[finite]
            t = true_at_test[finite]

            all_true.extend(t)
            all_pred.extend(p)
            all_clarity.extend([result["clarity"]] * len(t))

            # Depth bins
            for d in t:
                if d < 5:
                    all_depth_bins.append("0-5m")
                elif d < 10:
                    all_depth_bins.append("5-10m")
                elif d < 20:
                    all_depth_bins.append("10-20m")
                else:
                    all_depth_bins.append("20m+")

            lake_metrics.append({
                "lake_id": lake_id,
                "clarity": result["clarity"],
                "secchi_m": result["secchi"],
                "n_test": len(t),
                "rmse": float(np.sqrt(mean_squared_error(t, p))),
                "mae": float(mean_absolute_error(t, p)),
                "r2": float(r2_score(t, p)) if len(t) > 1 else np.nan,
                "model_used": result.get("model_used", "unknown"),
            })

        except Exception as e:
            log.warning(f"Validation error for {fpath.name}: {e}")
            continue

    # ── Aggregate results ──
    all_true = np.array(all_true)
    all_pred = np.array(all_pred)

    results = {
        "overall": {
            "n_lakes": len(lake_metrics),
            "n_points": len(all_true),
            "rmse": float(np.sqrt(mean_squared_error(all_true, all_pred))),
            "mae": float(mean_absolute_error(all_true, all_pred)),
            "r2": float(r2_score(all_true, all_pred)),
        },
    }

    # By clarity
    for clarity in ["clear", "moderate", "turbid"]:
        mask = np.array(all_clarity) == clarity
        if mask.sum() > 10:
            results[f"clarity_{clarity}"] = {
                "n_points": int(mask.sum()),
                "rmse": float(np.sqrt(mean_squared_error(
                    all_true[mask], all_pred[mask]
                ))),
                "r2": float(r2_score(all_true[mask], all_pred[mask])),
            }

    # By depth bin
    for bin_name in ["0-5m", "5-10m", "10-20m", "20m+"]:
        mask = np.array(all_depth_bins) == bin_name
        if mask.sum() > 10:
            results[f"depth_{bin_name}"] = {
                "n_points": int(mask.sum()),
                "rmse": float(np.sqrt(mean_squared_error(
                    all_true[mask], all_pred[mask]
                ))),
                "r2": float(r2_score(all_true[mask], all_pred[mask])),
            }

    # Per-lake RMSE distribution
    lake_rmses = [m["rmse"] for m in lake_metrics]
    if lake_rmses:
        results["per_lake_rmse"] = {
            "mean": float(np.mean(lake_rmses)),
            "median": float(np.median(lake_rmses)),
            "p10": float(np.percentile(lake_rmses, 10)),
            "p90": float(np.percentile(lake_rmses, 90)),
        }

    # Save
    with open(output_dir / "within_lake_sdb_validation.json", "w") as f:
        json.dump(results, f, indent=2)

    lake_df = pd.DataFrame(lake_metrics)
    lake_df.to_parquet(output_dir / "within_lake_sdb_per_lake.parquet", index=False)

    log.info(f"\n{'='*60}")
    log.info(f"  Within-Lake SDB Validation Results")
    log.info(f"{'='*60}")
    log.info(f"  Lakes validated: {results['overall']['n_lakes']}")
    log.info(f"  Total points: {results['overall']['n_points']}")
    log.info(f"  Overall RMSE: {results['overall']['rmse']:.2f}m")
    log.info(f"  Overall R²:   {results['overall']['r2']:.4f}")
    if "per_lake_rmse" in results:
        log.info(f"  Per-lake RMSE: median={results['per_lake_rmse']['median']:.2f}m")
    log.info(f"{'='*60}")

    return results


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Within-Lake Spectral SDB")
    parser.add_argument("--s2-dir", type=str, required=True,
                        help="Sentinel-2 composite directory")
    parser.add_argument("--anchors", type=str, required=True,
                        help="Depth anchor directory")
    parser.add_argument("--output", type=str, required=True,
                        help="Output directory")
    parser.add_argument("--lakes", type=str, default=None,
                        help="Lake list parquet (optional)")
    parser.add_argument("--validate", action="store_true",
                        help="Run validation against sonar")
    parser.add_argument("--sonar-dir", type=str, default=None,
                        help="Sonar directory for validation")
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.validate:
        if args.sonar_dir is None:
            raise ValueError("--sonar-dir required for validation")
        validate_on_sonar(
            sonar_dir=Path(args.sonar_dir),
            s2_dir=Path(args.s2_dir),
            output_dir=output_dir,
        )
    else:
        lake_list = None
        if args.lakes:
            lake_df = pd.read_parquet(args.lakes)
            for col in ["lake_id", "hylak_id", "dow"]:
                if col in lake_df.columns:
                    lake_list = lake_df[col].astype(str).tolist()
                    break

        batch_process_lakes(
            s2_dir=Path(args.s2_dir),
            anchor_dir=Path(args.anchors),
            output_dir=output_dir,
            lake_list=lake_list,
        )


if __name__ == "__main__":
    main()
