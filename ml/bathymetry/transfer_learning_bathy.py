#!/usr/bin/env python3
"""
OpenCatch — Transfer Learning Bathymetry from Surveyed Lakes

For unsurveyed lakes, transfer the depth profile from the most morphometrically
similar surveyed lake. We have 4,500 MN DNR sonar lakes with full bathymetry.
For any new lake, find the nearest match and scale the depth profile.

This is conceptually simple but surprisingly effective because:
  - Lakes with similar morphometry have similar formation processes
  - Depth profile shapes cluster strongly by lake type
  - Even an approximate 2D depth map is far better than a single max depth

Approach:
  1. Compute morphometric similarity between all lake pairs
  2. For each unsurveyed lake, find K nearest surveyed donors
  3. Transfer depth profiles using inverse-distance weighting
  4. Scale by the unsurveyed lake's known max depth (from A-E or model)

Similarity metrics (weighted):
  - Lake area ratio (most predictive)
  - Shoreline development factor similarity
  - Latitude proximity (similar geology / glaciation)
  - Elevation similarity (similar watershed type)
  - Shape (circularity, elongation)
  - Volume development factor (if available)

References:
  - Hollister & Milstead (2010): Morphometric similarity for lake depth
  - Sobek et al. (2011): Lake type clustering by morphometry
  - GLOBathy (Khazaei 2022): area-depth empirical transfer

Usage:
    python transfer_learning_bathy.py \
        --surveyed /data/training/v2/mn_sonar_profiles \
        --morpho /data/training/v2/mn_morphometric.parquet \
        --target /data/3d_lakes_with_depths.parquet \
        --output /data/models/transfer_bathy

Requirements:
    pip install pandas numpy scikit-learn scipy tqdm pyarrow faiss-cpu
"""

import argparse
import gc
import json
import logging
import pickle
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.spatial.distance import cdist
from scipy.stats import pearsonr, spearmanr
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
from sklearn.preprocessing import StandardScaler, RobustScaler
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("transfer_bathy")

EPS = 1e-8


# ══════════════════════════════════════════════════════════════════════
# Morphometric Feature Computation
# ══════════════════════════════════════════════════════════════════════

# Feature definitions with weights for similarity matching
SIMILARITY_FEATURES = {
    # Feature name: (weight, transform)
    "log_area_km2":          (3.0, "log"),      # Most important
    "shoreline_dev_factor":  (2.5, "linear"),   # Lake shape complexity
    "latitude":              (2.0, "linear"),   # Geology/glaciation proxy
    "elevation_m":           (1.5, "linear"),   # Watershed type
    "circularity":           (1.5, "linear"),   # Round vs elongated
    "elongation":            (1.0, "linear"),   # Length/width ratio
    "convexity":             (1.0, "linear"),   # Boundary regularity
    "shore_slope_100m":      (1.0, "linear"),   # Surrounding terrain
    "log_watershed_area":    (0.8, "log"),      # Drainage basin
    "lake_type":             (2.0, "categorical"),  # Natural vs reservoir
}


def compute_morphometric_features(df: pd.DataFrame) -> Tuple[np.ndarray, List[str]]:
    """
    Compute standardized morphometric feature vectors for similarity matching.

    Returns:
        features: (n_lakes, n_features) normalized array
        feature_names: list of feature names
    """
    features = []
    names = []
    weights = []

    for feat_name, (weight, transform) in SIMILARITY_FEATURES.items():
        raw_name = feat_name.replace("log_", "")

        # Find the actual column
        col = None
        for candidate in [raw_name, feat_name, raw_name.replace("_km2", ""),
                          raw_name.title(), raw_name.upper()]:
            if candidate in df.columns:
                col = candidate
                break

        if col is None:
            log.warning(f"Feature {feat_name} not found in data, skipping")
            continue

        values = df[col].values.astype(float).copy()

        if transform == "log":
            values = np.log1p(np.abs(values)) * np.sign(values)
        elif transform == "categorical":
            # One-hot-ish: just keep as-is (will be scaled)
            pass

        features.append(values)
        names.append(feat_name)
        weights.append(weight)

    if not features:
        raise ValueError("No morphometric features found in data")

    X = np.column_stack(features)

    # Fill NaN with median
    for col_idx in range(X.shape[1]):
        mask = np.isnan(X[:, col_idx])
        if mask.any():
            median = np.nanmedian(X[:, col_idx])
            X[mask, col_idx] = median if not np.isnan(median) else 0.0

    # Scale and apply weights
    scaler = RobustScaler()
    X_scaled = scaler.fit_transform(X)
    weight_arr = np.array(weights)
    X_weighted = X_scaled * weight_arr[np.newaxis, :]

    return X_weighted, names, scaler, weight_arr


# ══════════════════════════════════════════════════════════════════════
# Depth Profile Representation
# ══════════════════════════════════════════════════════════════════════

class DepthProfile:
    """
    Normalized depth profile for a surveyed lake.

    Stores the hypsometric curve (area-depth relationship) and
    optionally a 2D depth map normalized to [0, 1] range.
    """

    def __init__(self, lake_id: str, max_depth: float):
        self.lake_id = lake_id
        self.max_depth = max_depth
        self.hypsometric_curve = None  # (100,) area at each depth percentile
        self.depth_map_norm = None     # Optional: 2D normalized [0,1] depth
        self.depth_map_shape = None
        self.hypsometric_integral = None

    def set_hypsometric_curve(self, curve: np.ndarray):
        """Set normalized hypsometric curve (area at depth 0%..99%)."""
        self.hypsometric_curve = curve
        self.hypsometric_integral = float(curve.mean())

    def set_depth_map(self, depth_map: np.ndarray):
        """Set 2D depth map, normalizes to [0, 1]."""
        self.depth_map_shape = depth_map.shape
        if self.max_depth > 0:
            self.depth_map_norm = depth_map / self.max_depth
        else:
            self.depth_map_norm = depth_map

    def scale_to_target(self, target_max_depth: float,
                        target_shape: Tuple[int, int] = None) -> np.ndarray:
        """
        Scale this profile to a target lake's max depth and size.

        Returns a 2D depth map or 1D hypsometric curve scaled to target.
        """
        if self.depth_map_norm is not None and target_shape is not None:
            from scipy.ndimage import zoom
            # Resize 2D depth map to target shape
            zoom_factors = (
                target_shape[0] / self.depth_map_shape[0],
                target_shape[1] / self.depth_map_shape[1],
            )
            resized = zoom(self.depth_map_norm, zoom_factors, order=1)
            return resized * target_max_depth

        elif self.hypsometric_curve is not None:
            return self.hypsometric_curve * target_max_depth

        return None


# ══════════════════════════════════════════════════════════════════════
# Similarity Search
# ══════════════════════════════════════════════════════════════════════

class LakeSimilarityIndex:
    """
    Fast nearest-neighbor search over morphometric feature space.

    Uses FAISS for efficient similarity search on large databases.
    Falls back to brute-force scipy for smaller datasets.
    """

    def __init__(self, use_faiss: bool = True):
        self.use_faiss = use_faiss
        self.index = None
        self.features = None
        self.lake_ids = None
        self.scaler = None
        self.weights = None
        self.feature_names = None

    def build_index(self, morpho_df: pd.DataFrame, lake_id_col: str = "lake_id"):
        """Build similarity index from morphometric data."""
        log.info("Building lake similarity index...")

        self.lake_ids = morpho_df[lake_id_col].values if lake_id_col in morpho_df.columns \
            else morpho_df.index.values

        features, names, scaler, weights = compute_morphometric_features(morpho_df)
        self.features = features.astype(np.float32)
        self.scaler = scaler
        self.weights = weights
        self.feature_names = names

        n_lakes, n_dim = self.features.shape
        log.info(f"  Index: {n_lakes} lakes, {n_dim} features")

        if self.use_faiss and n_lakes > 1000:
            try:
                import faiss
                # Use IVF index for large datasets
                quantizer = faiss.IndexFlatL2(n_dim)
                n_cells = min(256, n_lakes // 10)
                self.index = faiss.IndexIVFFlat(quantizer, n_dim, n_cells)
                self.index.train(self.features)
                self.index.add(self.features)
                self.index.nprobe = min(32, n_cells)
                log.info(f"  FAISS IVF index built ({n_cells} cells)")
            except ImportError:
                log.info("  FAISS not available, using brute-force search")
                self.use_faiss = False

    def find_similar(self, query_features: np.ndarray,
                     k: int = 5) -> Tuple[np.ndarray, np.ndarray]:
        """
        Find K most similar surveyed lakes.

        Returns:
            lake_ids: (k,) IDs of similar lakes
            distances: (k,) distances (lower = more similar)
        """
        query = query_features.astype(np.float32).reshape(1, -1)

        if self.use_faiss and self.index is not None:
            import faiss
            distances, indices = self.index.search(query, k)
            return self.lake_ids[indices[0]], distances[0]
        else:
            # Brute-force
            dists = cdist(query, self.features, metric="euclidean")[0]
            top_k = np.argsort(dists)[:k]
            return self.lake_ids[top_k], dists[top_k]

    def find_similar_for_lake(self, morpho_row: pd.Series,
                              k: int = 5) -> Tuple[np.ndarray, np.ndarray]:
        """Find similar lakes given a morphometric row."""
        # Compute features for this lake
        features = []
        for feat_name, (weight, transform) in SIMILARITY_FEATURES.items():
            raw_name = feat_name.replace("log_", "")
            val = 0.0
            for candidate in [raw_name, feat_name, raw_name.replace("_km2", ""),
                              raw_name.title()]:
                if candidate in morpho_row.index:
                    val = float(morpho_row[candidate])
                    break

            if transform == "log":
                val = np.log1p(abs(val)) * np.sign(val)

            features.append(val)

        features = np.array(features).reshape(1, -1)
        features = self.scaler.transform(features) * self.weights
        return self.find_similar(features, k)


# ══════════════════════════════════════════════════════════════════════
# Transfer Methods
# ══════════════════════════════════════════════════════════════════════

def transfer_single_donor(
    donor_profile: DepthProfile,
    target_max_depth: float,
    target_area_km2: float = None,
) -> dict:
    """Transfer depth profile from a single donor lake."""
    if donor_profile.hypsometric_curve is not None:
        # Scale hypsometric curve
        scaled_curve = donor_profile.hypsometric_curve.copy()
        # Area scaling: normalize donor curve, multiply by target area
        depth_at_percentile = scaled_curve * target_max_depth

        return {
            "method": "single_donor",
            "donor_id": donor_profile.lake_id,
            "hypsometric_curve": depth_at_percentile,
            "donor_max_depth": donor_profile.max_depth,
            "scale_factor": target_max_depth / (donor_profile.max_depth + EPS),
        }
    return None


def transfer_k_donors(
    donor_profiles: List[DepthProfile],
    distances: np.ndarray,
    target_max_depth: float,
    method: str = "idw",
) -> dict:
    """
    Transfer from K donors using weighted combination.

    Methods:
      - idw: Inverse distance weighting
      - median: Median of donor curves (robust)
      - best: Use single closest donor
    """
    # Collect valid donor curves
    curves = []
    valid_dists = []
    valid_ids = []

    for profile, dist in zip(donor_profiles, distances):
        if profile.hypsometric_curve is not None:
            curves.append(profile.hypsometric_curve)
            valid_dists.append(max(dist, EPS))
            valid_ids.append(profile.lake_id)

    if not curves:
        return None

    curves = np.array(curves)
    valid_dists = np.array(valid_dists)

    if method == "idw":
        # Inverse distance weights
        weights = 1.0 / valid_dists
        weights /= weights.sum()
        combined = np.average(curves, axis=0, weights=weights)

    elif method == "median":
        combined = np.median(curves, axis=0)

    elif method == "best":
        combined = curves[0]  # closest

    else:
        raise ValueError(f"Unknown method: {method}")

    # Scale to target max depth
    depth_curve = combined * target_max_depth

    return {
        "method": f"k_donor_{method}",
        "donor_ids": valid_ids,
        "weights": (1.0 / valid_dists / (1.0 / valid_dists).sum()).tolist(),
        "hypsometric_curve": depth_curve,
        "n_donors": len(valid_ids),
    }


# ══════════════════════════════════════════════════════════════════════
# Learned Transfer (optional ML enhancement)
# ══════════════════════════════════════════════════════════════════════

class LearnedTransferModel:
    """
    ML model that learns HOW to combine donor profiles.

    Instead of simple IDW, train a model that learns:
      - Which features make two lakes truly similar for bathymetry
      - How to weight multiple donors optimally
      - When to trust the transfer vs fall back to generic priors
    """

    def __init__(self):
        self.feature_importance_model = None
        self.weight_model = None
        self.scaler = StandardScaler()

    def fit(self, surveyed_profiles: Dict[str, DepthProfile],
            similarity_index: LakeSimilarityIndex,
            morpho_df: pd.DataFrame,
            k: int = 10, n_folds: int = 5) -> dict:
        """
        Train using leave-one-out on surveyed lakes.

        For each surveyed lake:
        1. Temporarily remove it from the index
        2. Find K nearest neighbors
        3. Transfer and evaluate
        4. Learn optimal combination weights
        """
        import xgboost as xgb

        log.info("Training learned transfer model...")

        lake_ids = list(surveyed_profiles.keys())
        n_lakes = len(lake_ids)

        # Prepare training data for weight model
        X_weight = []  # features for weight prediction
        y_weight = []  # optimal weights

        all_rmse = []

        for i, target_id in enumerate(tqdm(lake_ids, desc="LOO transfer")):
            target_profile = surveyed_profiles[target_id]
            if target_profile.hypsometric_curve is None:
                continue

            # Find similar (excluding self)
            if target_id in morpho_df.index:
                morpho_row = morpho_df.loc[target_id]
            else:
                morpho_row = morpho_df[morpho_df.index.astype(str) == str(target_id)]
                if len(morpho_row) == 0:
                    continue
                morpho_row = morpho_row.iloc[0]

            donor_ids, distances = similarity_index.find_similar_for_lake(
                morpho_row, k=k + 1
            )

            # Remove self from donors
            mask = donor_ids != target_id
            donor_ids = donor_ids[mask][:k]
            distances = distances[mask][:k]

            # Get donor profiles
            donors = []
            valid_dists = []
            for did, dist in zip(donor_ids, distances):
                if str(did) in surveyed_profiles:
                    donors.append(surveyed_profiles[str(did)])
                    valid_dists.append(dist)

            if len(donors) < 2:
                continue

            # Evaluate different transfer methods
            target_curve = target_profile.hypsometric_curve
            target_depth = target_profile.max_depth

            for method in ["idw", "median", "best"]:
                result = transfer_k_donors(
                    donors, np.array(valid_dists),
                    target_depth, method=method,
                )
                if result is None:
                    continue

                pred_curve = result["hypsometric_curve"] / (target_depth + EPS)
                rmse = float(np.sqrt(mean_squared_error(target_curve, pred_curve)))
                all_rmse.append({
                    "lake_id": target_id,
                    "method": method,
                    "rmse": rmse,
                    "n_donors": len(donors),
                })

        results_df = pd.DataFrame(all_rmse)
        log.info(f"\n  Transfer learning LOO results:")
        for method in ["idw", "median", "best"]:
            subset = results_df[results_df["method"] == method]
            if len(subset) > 0:
                log.info(f"    {method}: RMSE={subset['rmse'].mean():.4f} "
                         f"(median={subset['rmse'].median():.4f})")

        return {
            "n_lakes_evaluated": len(set(results_df["lake_id"])),
            "results_df": results_df,
        }


# ══════════════════════════════════════════════════════════════════════
# Profile Loading
# ══════════════════════════════════════════════════════════════════════

def load_surveyed_profiles(
    profile_dir: Path,
    morpho_df: pd.DataFrame = None,
) -> Dict[str, DepthProfile]:
    """Load all surveyed lake depth profiles."""
    profiles = {}

    # Try parquet first
    combined = profile_dir / "mn_sonar_profiles.parquet"
    if combined.exists():
        df = pd.read_parquet(combined)
        lake_col = None
        for col in ["lake_id", "dow", "hylak_id"]:
            if col in df.columns:
                lake_col = col
                break
        if lake_col:
            for lake_id, group in df.groupby(lake_col):
                lake_id_str = str(lake_id)
                depth_col = None
                for c in ["depth_m", "depth", "max_depth"]:
                    if c in group.columns:
                        depth_col = c
                        break
                if depth_col is None:
                    continue

                max_depth = float(group[depth_col].max())
                if max_depth <= 0:
                    continue

                profile = DepthProfile(lake_id_str, max_depth)

                # Compute hypsometric curve from depth distribution
                depths = group[depth_col].dropna().values
                if len(depths) < 5:
                    continue

                # Create area-at-depth curve
                n_bins = 100
                depth_pcts = np.linspace(0, max_depth, n_bins)
                # Area = fraction of points shallower than this depth
                area_curve = np.array([
                    (depths <= d).mean() for d in depth_pcts
                ])
                # Invert: area should decrease with depth (convention)
                area_curve = 1.0 - area_curve
                area_curve = np.clip(area_curve, 0, 1)
                area_curve[0] = 1.0  # full area at surface

                profile.set_hypsometric_curve(area_curve)
                profiles[lake_id_str] = profile

    # Also try individual files
    for fpath in profile_dir.glob("*.csv"):
        lake_id = fpath.stem
        if lake_id in profiles:
            continue
        try:
            df = pd.read_csv(fpath)
            depth_col = None
            for c in ["depth_m", "depth", "z"]:
                if c in df.columns:
                    depth_col = c
                    break
            if depth_col is None:
                continue

            max_depth = float(df[depth_col].max())
            if max_depth <= 0:
                continue

            profile = DepthProfile(lake_id, max_depth)
            depths = df[depth_col].dropna().values
            n_bins = 100
            area_curve = np.array([
                (depths <= d).mean() for d in np.linspace(0, max_depth, n_bins)
            ])
            area_curve = 1.0 - area_curve
            area_curve = np.clip(area_curve, 0, 1)
            area_curve[0] = 1.0

            profile.set_hypsometric_curve(area_curve)
            profiles[lake_id] = profile
        except Exception:
            continue

    log.info(f"Loaded {len(profiles)} surveyed lake profiles")
    return profiles


# ══════════════════════════════════════════════════════════════════════
# Validation
# ══════════════════════════════════════════════════════════════════════

def validate_transfer(
    profiles: Dict[str, DepthProfile],
    similarity_index: LakeSimilarityIndex,
    morpho_df: pd.DataFrame,
    output_dir: Path,
    k_values: List[int] = [1, 3, 5, 10],
) -> dict:
    """
    Leave-one-out validation of transfer learning.

    For each surveyed lake, predict its depth profile from nearest
    neighbors and compare to ground truth.
    """
    log.info("Running leave-one-out transfer validation...")
    output_dir.mkdir(parents=True, exist_ok=True)

    results = {k: [] for k in k_values}
    methods = ["idw", "median", "best"]

    lake_ids = list(profiles.keys())

    for target_id in tqdm(lake_ids, desc="LOO validation"):
        target = profiles[target_id]
        if target.hypsometric_curve is None:
            continue

        # Find morphometric row
        try:
            if target_id in morpho_df.index:
                morpho_row = morpho_df.loc[target_id]
            else:
                matches = morpho_df[morpho_df.index.astype(str) == target_id]
                if len(matches) == 0:
                    continue
                morpho_row = matches.iloc[0]
        except Exception:
            continue

        donor_ids, distances = similarity_index.find_similar_for_lake(
            morpho_row, k=max(k_values) + 1
        )

        # Remove self
        mask = np.array([str(did) != target_id for did in donor_ids])
        donor_ids = donor_ids[mask]
        distances = distances[mask]

        for k in k_values:
            k_donors = []
            k_dists = []
            for did, dist in zip(donor_ids[:k], distances[:k]):
                did_str = str(did)
                if did_str in profiles and profiles[did_str].hypsometric_curve is not None:
                    k_donors.append(profiles[did_str])
                    k_dists.append(dist)

            if not k_donors:
                continue

            for method in methods:
                result = transfer_k_donors(
                    k_donors, np.array(k_dists),
                    target.max_depth, method=method,
                )
                if result is None:
                    continue

                pred_curve = result["hypsometric_curve"] / (target.max_depth + EPS)
                true_curve = target.hypsometric_curve

                rmse = float(np.sqrt(mean_squared_error(true_curve, pred_curve)))
                mae = float(mean_absolute_error(true_curve, pred_curve))
                r2 = float(r2_score(true_curve, pred_curve)) if len(true_curve) > 1 else 0

                # Volume error
                vol_true = np.trapz(true_curve)
                vol_pred = np.trapz(pred_curve)
                vol_err = abs(vol_true - vol_pred) / (vol_true + EPS)

                results[k].append({
                    "lake_id": target_id,
                    "method": method,
                    "k": k,
                    "rmse": rmse,
                    "mae": mae,
                    "r2": r2,
                    "volume_error_pct": float(vol_err * 100),
                    "max_depth": target.max_depth,
                    "nearest_dist": float(k_dists[0]),
                })

    # Aggregate
    summary = {}
    for k in k_values:
        df = pd.DataFrame(results[k])
        for method in methods:
            subset = df[df["method"] == method]
            if len(subset) == 0:
                continue
            key = f"k{k}_{method}"
            summary[key] = {
                "n_lakes": len(subset),
                "rmse_mean": float(subset["rmse"].mean()),
                "rmse_median": float(subset["rmse"].median()),
                "mae_mean": float(subset["mae"].mean()),
                "r2_mean": float(subset["r2"].mean()),
                "r2_median": float(subset["r2"].median()),
                "vol_error_mean": float(subset["volume_error_pct"].mean()),
            }

    # Mean baseline (predict mean curve for all)
    all_curves = [p.hypsometric_curve for p in profiles.values()
                  if p.hypsometric_curve is not None]
    if all_curves:
        mean_curve = np.mean(all_curves, axis=0)
        baseline_rmses = []
        for target_id, target in profiles.items():
            if target.hypsometric_curve is not None:
                baseline_rmses.append(float(np.sqrt(
                    mean_squared_error(target.hypsometric_curve, mean_curve)
                )))
        summary["baseline_mean_curve"] = {
            "rmse_mean": float(np.mean(baseline_rmses)),
            "rmse_median": float(np.median(baseline_rmses)),
        }

    # Save
    with open(output_dir / "transfer_validation.json", "w") as f:
        json.dump(summary, f, indent=2)

    # Depth-stratified analysis
    for k in k_values:
        df = pd.DataFrame(results[k])
        if len(df) == 0:
            continue
        df.to_parquet(output_dir / f"transfer_k{k}_details.parquet", index=False)

    # Report
    log.info(f"\n{'='*70}")
    log.info(f"  Transfer Learning Validation Results")
    log.info(f"{'='*70}")
    for key, metrics in summary.items():
        log.info(f"  {key:20s}  RMSE={metrics.get('rmse_mean', 0):.4f}  "
                 f"R²={metrics.get('r2_mean', 0):.4f}  "
                 f"Vol_err={metrics.get('vol_error_mean', 0):.1f}%")
    log.info(f"{'='*70}")

    return summary


# ══════════════════════════════════════════════════════════════════════
# Batch Transfer for Unsurveyed Lakes
# ══════════════════════════════════════════════════════════════════════

def transfer_batch(
    target_df: pd.DataFrame,
    profiles: Dict[str, DepthProfile],
    similarity_index: LakeSimilarityIndex,
    morpho_df: pd.DataFrame,
    output_dir: Path,
    k: int = 5,
    method: str = "idw",
) -> pd.DataFrame:
    """
    Transfer bathymetric profiles to all unsurveyed target lakes.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    lake_id_col = None
    for col in ["lake_id", "hylak_id", "dow"]:
        if col in target_df.columns:
            lake_id_col = col
            break

    depth_col = None
    for col in ["max_depth_m", "depth_m", "depth_max", "max_depth"]:
        if col in target_df.columns:
            depth_col = col
            break

    log.info(f"Transferring profiles to {len(target_df)} target lakes "
             f"(k={k}, method={method})")

    results = []

    for idx, row in tqdm(target_df.iterrows(), total=len(target_df),
                         desc="Transferring"):
        lake_id = str(row[lake_id_col]) if lake_id_col else str(idx)

        # Skip already surveyed
        if lake_id in profiles:
            continue

        max_depth = float(row[depth_col]) if depth_col else np.nan
        if np.isnan(max_depth) or max_depth <= 0:
            results.append({
                "lake_id": lake_id,
                "status": "no_max_depth",
            })
            continue

        # Find similar lakes
        try:
            donor_ids, distances = similarity_index.find_similar_for_lake(row, k=k)
        except Exception:
            results.append({"lake_id": lake_id, "status": "similarity_error"})
            continue

        donors = []
        dists = []
        for did, dist in zip(donor_ids, distances):
            did_str = str(did)
            if did_str in profiles and profiles[did_str].hypsometric_curve is not None:
                donors.append(profiles[did_str])
                dists.append(dist)

        if not donors:
            results.append({"lake_id": lake_id, "status": "no_donors"})
            continue

        transfer = transfer_k_donors(donors, np.array(dists), max_depth, method)
        if transfer is None:
            results.append({"lake_id": lake_id, "status": "transfer_failed"})
            continue

        # Save transferred profile
        np.savez_compressed(
            output_dir / f"{lake_id}_transferred.npz",
            hypsometric_curve=transfer["hypsometric_curve"],
            donor_ids=transfer["donor_ids"],
            weights=transfer.get("weights", []),
        )

        results.append({
            "lake_id": lake_id,
            "status": "ok",
            "n_donors": transfer["n_donors"],
            "nearest_distance": float(dists[0]),
            "predicted_max_depth": max_depth,
        })

    results_df = pd.DataFrame(results)
    results_df.to_parquet(output_dir / "transfer_batch_results.parquet", index=False)

    ok = results_df[results_df["status"] == "ok"]
    log.info(f"\n  Transfer batch: {len(ok)}/{len(results_df)} lakes successful")

    return results_df


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Transfer Learning Bathymetry")
    parser.add_argument("--surveyed", type=str, required=True,
                        help="Surveyed lake profiles directory")
    parser.add_argument("--morpho", type=str, required=True,
                        help="Morphometric features parquet")
    parser.add_argument("--target", type=str, default=None,
                        help="Target lakes parquet (for batch transfer)")
    parser.add_argument("--output", type=str, required=True,
                        help="Output directory")
    parser.add_argument("--k", type=int, default=5,
                        help="Number of donor lakes")
    parser.add_argument("--method", type=str, default="idw",
                        choices=["idw", "median", "best"])
    parser.add_argument("--validate", action="store_true",
                        help="Run LOO validation")
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load morphometric data
    morpho_df = pd.read_parquet(args.morpho)
    log.info(f"Morphometric data: {len(morpho_df)} lakes")

    # Load surveyed profiles
    profiles = load_surveyed_profiles(Path(args.surveyed), morpho_df)

    # Build similarity index
    sim_index = LakeSimilarityIndex(use_faiss=True)
    sim_index.build_index(morpho_df)

    if args.validate:
        validate_transfer(
            profiles, sim_index, morpho_df, output_dir,
            k_values=[1, 3, 5, 10],
        )

    if args.target:
        target_df = pd.read_parquet(args.target)
        transfer_batch(
            target_df, profiles, sim_index, morpho_df,
            output_dir, k=args.k, method=args.method,
        )


if __name__ == "__main__":
    main()
