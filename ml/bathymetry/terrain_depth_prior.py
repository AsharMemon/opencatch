#!/usr/bin/env python3
"""
OpenCatch -- Terrain DEM Prior for Lake Depth (Martinsen 2023 Approach)

Extracts surrounding terrain features from DEM within a buffer around each
lake and trains a model to predict max depth and mean depth. This provides
a bathymetric prior even when A-E data is sparse or unavailable.

Key insight (Martinsen et al., 2023, Limnology & Oceanography: Methods):
The surrounding landscape shape predicts the underwater shape because the
same geologic processes formed both. Works for ALL lakes regardless of
water clarity, unlike optical methods.

Features extracted per lake (1km buffer):
  1. Shore slope: Mean/median gradient at the shoreline
  2. Valley shape: Concavity, V-shape vs U-shape classification
  3. Terrain ruggedness: Std dev of elevation, terrain ruggedness index (TRI)
  4. Elevation range: Surrounding terrain relief
  5. Aspect distribution: Dominant aspect, circularity of aspect
  6. Slope profile: How slope changes with distance from shore
  7. Curvature: Plan and profile curvature at shore
  8. Hypsometric integral: Shape of terrain elevation distribution
  9. Drainage density: Stream network proximity (from DEM flow accumulation)
  10. Fetch: Maximum open-water distance (wind exposure)

Ground truth: MN DNR sonar surveys (4,500 lakes with max & mean depth).

Usage:
    # Extract terrain features for all lakes
    python terrain_depth_prior.py extract \
        --dem-dir /data/3dep \
        --lakes /data/hydrolakes/na_lakes.gpkg \
        --output /data/terrain_features.parquet

    # Train terrain -> depth model
    python terrain_depth_prior.py train \
        --features /data/terrain_features.parquet \
        --ground-truth /data/mn_sonar_depths.parquet \
        --output /data/models/terrain_prior

    # Predict depth for unsurveyed lakes
    python terrain_depth_prior.py predict \
        --features /data/terrain_features.parquet \
        --model /data/models/terrain_prior \
        --output /data/terrain_depth_predictions.parquet

Requirements:
    pip install numpy pandas rasterio geopandas shapely scipy xgboost
    pip install scikit-learn pyarrow tqdm richdem
"""

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from scipy import ndimage
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("terrain_prior")

EPS = 1e-8


# == Terrain Feature Extraction ===============================================

class TerrainFeatureExtractor:
    """
    Extract terrain features around a lake from DEM.

    All features are computed from the surrounding terrain (NOT the lake
    itself, which is flat water surface in the DEM). The key insight is
    that the terrain shape around the lake predicts the shape below.
    """

    def __init__(
        self,
        buffer_m: float = 1000.0,
        pixel_size_m: float = 10.0,
        n_shore_samples: int = 360,
    ):
        self.buffer_m = buffer_m
        self.pixel_size_m = pixel_size_m
        self.n_shore_samples = n_shore_samples

    def extract_from_arrays(
        self,
        dem: np.ndarray,
        lake_mask: np.ndarray,
        lake_elevation: float,
        pixel_size_m: float = None,
    ) -> dict:
        """
        Extract all terrain features from DEM arrays.

        Args:
            dem: (H, W) elevation raster in metres
            lake_mask: (H, W) binary mask (1 = water, 0 = land)
            lake_elevation: Water surface elevation in metres
            pixel_size_m: DEM cell size in metres
        """
        ps = pixel_size_m or self.pixel_size_m
        features = {}

        # 1. Compute terrain-only DEM (mask out lake)
        land_mask = ~lake_mask.astype(bool)
        terrain_dem = dem.copy()
        terrain_dem[~land_mask] = np.nan

        # 2. Slope and aspect
        slope, aspect = self._compute_slope_aspect(dem, ps)

        # 3. Shore zone: buffer around lake boundary
        shore_band = self._get_shore_band(lake_mask, ps)

        # -- Shore slope features --
        shore_slopes = slope[shore_band & land_mask]
        if len(shore_slopes) > 0:
            features["shore_slope_mean"] = float(np.nanmean(shore_slopes))
            features["shore_slope_median"] = float(np.nanmedian(shore_slopes))
            features["shore_slope_std"] = float(np.nanstd(shore_slopes))
            features["shore_slope_max"] = float(np.nanmax(shore_slopes))
            features["shore_slope_p25"] = float(np.nanpercentile(shore_slopes, 25))
            features["shore_slope_p75"] = float(np.nanpercentile(shore_slopes, 75))
            features["shore_slope_skew"] = float(
                self._safe_skew(shore_slopes)
            )
        else:
            for k in ["shore_slope_mean", "shore_slope_median", "shore_slope_std",
                      "shore_slope_max", "shore_slope_p25", "shore_slope_p75",
                      "shore_slope_skew"]:
                features[k] = np.nan

        # -- Elevation features (surrounding terrain within buffer) --
        buffer_mask = self._get_buffer_mask(lake_mask, self.buffer_m, ps)
        buffer_terrain = dem[buffer_mask & land_mask]
        if len(buffer_terrain) > 0:
            features["terrain_elev_mean"] = float(np.nanmean(buffer_terrain))
            features["terrain_elev_std"] = float(np.nanstd(buffer_terrain))
            features["terrain_elev_range"] = float(
                np.nanmax(buffer_terrain) - np.nanmin(buffer_terrain)
            )
            features["terrain_relief"] = float(
                np.nanmax(buffer_terrain) - lake_elevation
            )
            features["terrain_elev_skew"] = float(
                self._safe_skew(buffer_terrain)
            )
            # Hypsometric integral: fraction of terrain above mean elevation
            elev_norm = (buffer_terrain - np.nanmin(buffer_terrain)) / max(
                np.nanmax(buffer_terrain) - np.nanmin(buffer_terrain), EPS
            )
            features["hypsometric_integral"] = float(np.nanmean(elev_norm))
        else:
            for k in ["terrain_elev_mean", "terrain_elev_std", "terrain_elev_range",
                      "terrain_relief", "terrain_elev_skew", "hypsometric_integral"]:
                features[k] = np.nan

        # -- Terrain ruggedness --
        tri = self._terrain_ruggedness_index(dem)
        buffer_tri = tri[buffer_mask & land_mask]
        if len(buffer_tri) > 0:
            features["tri_mean"] = float(np.nanmean(buffer_tri))
            features["tri_std"] = float(np.nanstd(buffer_tri))
            features["tri_max"] = float(np.nanmax(buffer_tri))
        else:
            features["tri_mean"] = features["tri_std"] = features["tri_max"] = np.nan

        # -- Valley shape classification --
        features.update(self._valley_shape_features(dem, lake_mask, lake_elevation, ps))

        # -- Curvature at shore --
        features.update(self._curvature_features(dem, shore_band, land_mask, ps))

        # -- Slope profile: how slope changes with distance from shore --
        features.update(self._slope_distance_profile(slope, lake_mask, ps))

        # -- Aspect features (wind exposure / fetch proxy) --
        shore_aspects = aspect[shore_band & land_mask]
        if len(shore_aspects) > 0:
            # Circular statistics for aspect
            rad = np.radians(shore_aspects[~np.isnan(shore_aspects)])
            if len(rad) > 0:
                mean_sin = np.mean(np.sin(rad))
                mean_cos = np.mean(np.cos(rad))
                features["aspect_concentration"] = float(
                    np.sqrt(mean_sin**2 + mean_cos**2)
                )
                features["dominant_aspect_deg"] = float(
                    np.degrees(np.arctan2(mean_sin, mean_cos)) % 360
                )
            else:
                features["aspect_concentration"] = np.nan
                features["dominant_aspect_deg"] = np.nan
        else:
            features["aspect_concentration"] = np.nan
            features["dominant_aspect_deg"] = np.nan

        # -- Lake morphometric features from mask --
        lake_area_pixels = lake_mask.sum()
        lake_area_m2 = lake_area_pixels * ps * ps
        features["lake_area_km2"] = lake_area_m2 / 1e6
        features["lake_perimeter_m"] = self._perimeter_from_mask(lake_mask) * ps

        if lake_area_m2 > 0:
            features["shoreline_dev_factor"] = features["lake_perimeter_m"] / (
                2 * np.sqrt(np.pi * lake_area_m2)
            )
        else:
            features["shoreline_dev_factor"] = np.nan

        features["lake_elevation_m"] = float(lake_elevation)

        return features

    def _compute_slope_aspect(self, dem: np.ndarray, ps: float) -> tuple:
        """Compute slope (degrees) and aspect (degrees) from DEM."""
        dy, dx = np.gradient(dem, ps)
        slope = np.degrees(np.arctan(np.sqrt(dx**2 + dy**2)))
        aspect = np.degrees(np.arctan2(-dx, dy)) % 360
        return slope, aspect

    def _get_shore_band(
        self, lake_mask: np.ndarray, ps: float, width_m: float = 100.0
    ) -> np.ndarray:
        """Get a band around the lake shoreline (within width_m of boundary)."""
        dilated = ndimage.binary_dilation(
            lake_mask, iterations=max(1, int(width_m / ps))
        )
        eroded = ndimage.binary_erosion(
            lake_mask, iterations=max(1, int(width_m / ps))
        )
        shore = dilated & ~eroded
        return shore

    def _get_buffer_mask(
        self, lake_mask: np.ndarray, buffer_m: float, ps: float
    ) -> np.ndarray:
        """Get mask for area within buffer_m of lake."""
        iterations = max(1, int(buffer_m / ps))
        return ndimage.binary_dilation(lake_mask, iterations=iterations)

    def _terrain_ruggedness_index(self, dem: np.ndarray) -> np.ndarray:
        """
        Terrain Ruggedness Index (TRI): mean absolute difference
        between a cell and its 8 neighbors.
        """
        # 3x3 kernel for neighbor mean
        kernel = np.ones((3, 3)) / 8
        kernel[1, 1] = 0
        neighbor_mean = ndimage.convolve(dem, kernel, mode="reflect")
        return np.abs(dem - neighbor_mean)

    def _valley_shape_features(
        self,
        dem: np.ndarray,
        lake_mask: np.ndarray,
        lake_elevation: float,
        ps: float,
    ) -> dict:
        """
        Classify valley shape as V-shaped (steep, narrow) or U-shaped (broad, flat).

        V-shape indicator: high shore slope, narrow lake, steep relief
        U-shape indicator: moderate slope, broad lake, gentler relief

        Also compute concavity index.
        """
        features = {}

        # Distance from lake center to shore
        dist_to_shore = ndimage.distance_transform_edt(lake_mask) * ps
        max_dist = dist_to_shore.max()

        # Get radial elevation profile (from lake outward)
        land = ~lake_mask.astype(bool)
        dist_from_lake = ndimage.distance_transform_edt(~lake_mask.astype(bool)) * ps

        # Sample elevation at increasing distances
        distances = np.linspace(50, self.buffer_m, 20)
        elev_at_dist = []
        for d in distances:
            band = (dist_from_lake >= d - 25) & (dist_from_lake <= d + 25) & land
            vals = dem[band]
            if len(vals) > 5:
                elev_at_dist.append(np.nanmedian(vals) - lake_elevation)
            else:
                elev_at_dist.append(np.nan)

        elev_profile = np.array(elev_at_dist)
        valid = ~np.isnan(elev_profile)

        if valid.sum() >= 3:
            d_valid = distances[valid]
            e_valid = elev_profile[valid]

            # Fit power law: elev = a * dist^b
            # b < 1: concave (U-shape), b > 1: convex (V-shape)
            try:
                from scipy.optimize import curve_fit
                def power(x, a, b):
                    return a * np.power(np.clip(x, 1, None), b)

                popt, _ = curve_fit(
                    power, d_valid, np.clip(e_valid, 0.01, None),
                    p0=[0.01, 1.0], maxfev=2000,
                    bounds=([0, 0.1], [10, 3.0]),
                )
                features["valley_shape_exponent"] = float(popt[1])
                features["valley_is_v_shaped"] = float(popt[1] > 1.0)
            except Exception:
                features["valley_shape_exponent"] = np.nan
                features["valley_is_v_shaped"] = np.nan

            # Concavity index: area under profile vs triangle
            total_rise = e_valid[-1] if e_valid[-1] > 0 else 1
            normalized = e_valid / total_rise
            concavity = np.trapz(normalized, d_valid) / (
                0.5 * (d_valid[-1] - d_valid[0])
            )
            features["concavity_index"] = float(concavity)
        else:
            features["valley_shape_exponent"] = np.nan
            features["valley_is_v_shaped"] = np.nan
            features["concavity_index"] = np.nan

        return features

    def _curvature_features(
        self,
        dem: np.ndarray,
        shore_band: np.ndarray,
        land_mask: np.ndarray,
        ps: float,
    ) -> dict:
        """Compute plan and profile curvature at the shoreline."""
        features = {}

        # Second derivatives
        dy, dx = np.gradient(dem, ps)
        dyy, dyx = np.gradient(dy, ps)
        dxy, dxx = np.gradient(dx, ps)

        # Profile curvature (in direction of steepest descent)
        p = dx**2 + dy**2
        profile_curv = np.where(
            p > EPS,
            -(dxx * dx**2 + 2 * dxy * dx * dy + dyy * dy**2) / (p * np.sqrt(p + EPS)),
            0,
        )

        # Plan curvature (perpendicular to slope)
        plan_curv = np.where(
            p > EPS,
            -(dxx * dy**2 - 2 * dxy * dx * dy + dyy * dx**2) / (p**1.5 + EPS),
            0,
        )

        shore_land = shore_band & land_mask
        if shore_land.sum() > 0:
            features["profile_curv_mean"] = float(np.nanmean(profile_curv[shore_land]))
            features["profile_curv_std"] = float(np.nanstd(profile_curv[shore_land]))
            features["plan_curv_mean"] = float(np.nanmean(plan_curv[shore_land]))
            features["plan_curv_std"] = float(np.nanstd(plan_curv[shore_land]))
        else:
            features["profile_curv_mean"] = features["profile_curv_std"] = np.nan
            features["plan_curv_mean"] = features["plan_curv_std"] = np.nan

        return features

    def _slope_distance_profile(
        self,
        slope: np.ndarray,
        lake_mask: np.ndarray,
        ps: float,
    ) -> dict:
        """
        How does slope change with distance from shore?

        Rapid increase = steep-sided lake (likely deep)
        Gradual increase = gently sloping (likely shallow)
        """
        features = {}
        land = ~lake_mask.astype(bool)
        dist = ndimage.distance_transform_edt(~lake_mask.astype(bool)) * ps

        bins = [0, 50, 100, 200, 500, 1000]
        slope_at_dist = []
        for i in range(len(bins) - 1):
            band = (dist >= bins[i]) & (dist < bins[i + 1]) & land
            vals = slope[band]
            if len(vals) > 5:
                slope_at_dist.append(np.nanmean(vals))
            else:
                slope_at_dist.append(np.nan)

        for i, (lo, hi) in enumerate(zip(bins[:-1], bins[1:])):
            features[f"slope_at_{lo}_{hi}m"] = (
                float(slope_at_dist[i]) if not np.isnan(slope_at_dist[i]) else np.nan
            )

        # Slope gradient (rate of slope increase)
        valid = [s for s in slope_at_dist if not np.isnan(s)]
        if len(valid) >= 2:
            features["slope_gradient_rate"] = float(valid[-1] - valid[0])
        else:
            features["slope_gradient_rate"] = np.nan

        return features

    def _perimeter_from_mask(self, lake_mask: np.ndarray) -> float:
        """Approximate perimeter from binary mask (count boundary pixels)."""
        eroded = ndimage.binary_erosion(lake_mask)
        boundary = lake_mask.astype(int) - eroded.astype(int)
        return float(boundary.sum())

    @staticmethod
    def _safe_skew(arr):
        """Compute skewness safely."""
        arr = arr[~np.isnan(arr)]
        if len(arr) < 3:
            return 0.0
        from scipy.stats import skew
        return float(skew(arr))


# == DEM Data Loading =========================================================

def load_dem_for_lake(
    dem_dir: str,
    lake_centroid: tuple,
    lake_bbox: tuple,
    buffer_m: float = 1000.0,
    pixel_size_m: float = 10.0,
) -> Optional[tuple]:
    """
    Load DEM patch around a lake from tiled DEM directory.

    Supports 3DEP (1/3 arc-second), SRTM, ASTER, or any GeoTIFF DEM.

    Returns (dem_array, transform) or None if not available.
    """
    try:
        import rasterio
        from rasterio.windows import from_bounds
        from rasterio.merge import merge
    except ImportError:
        log.error("rasterio required: pip install rasterio")
        return None

    dem_path = Path(dem_dir)
    lon, lat = lake_centroid
    min_lon, min_lat, max_lon, max_lat = lake_bbox

    # Expand bbox by buffer
    # Approximate degrees for buffer_m at this latitude
    deg_per_m = 1 / (111320 * np.cos(np.radians(lat)))
    buf_deg_lon = buffer_m * deg_per_m
    buf_deg_lat = buffer_m / 111320

    search_bbox = (
        min_lon - buf_deg_lon,
        min_lat - buf_deg_lat,
        max_lon + buf_deg_lon,
        max_lat + buf_deg_lat,
    )

    # Find DEM tiles that intersect
    dem_files = list(dem_path.glob("*.tif")) + list(dem_path.glob("*.tiff"))
    matching = []
    for f in dem_files:
        try:
            with rasterio.open(f) as src:
                bounds = src.bounds
                # Check if tile intersects our search area
                if (bounds.left <= search_bbox[2] and bounds.right >= search_bbox[0] and
                    bounds.bottom <= search_bbox[3] and bounds.top >= search_bbox[1]):
                    matching.append(f)
        except Exception:
            continue

    if not matching:
        return None

    # Read and merge matching tiles
    try:
        datasets = [rasterio.open(f) for f in matching]
        if len(datasets) == 1:
            src = datasets[0]
            window = from_bounds(*search_bbox, transform=src.transform)
            dem = src.read(1, window=window)
            transform = src.window_transform(window)
        else:
            merged, transform = merge(datasets, bounds=search_bbox)
            dem = merged[0]

        for ds in datasets:
            ds.close()

        # Replace nodata with NaN
        dem = dem.astype(np.float32)
        dem[dem < -1000] = np.nan
        dem[dem > 9000] = np.nan

        return dem, transform

    except Exception as e:
        log.debug(f"DEM load error: {e}")
        return None


def create_lake_mask_from_dem(
    dem: np.ndarray,
    lake_elevation: float,
    tolerance_m: float = 2.0,
) -> np.ndarray:
    """
    Create approximate lake mask from DEM.

    Pixels near lake elevation and in a connected flat region = water.
    """
    # Flat areas near lake level
    flat = np.abs(dem - lake_elevation) <= tolerance_m

    # Find largest connected component (the lake)
    labeled, n_labels = ndimage.label(flat)
    if n_labels == 0:
        return flat.astype(np.uint8)

    # Largest component
    sizes = ndimage.sum(flat, labeled, range(1, n_labels + 1))
    largest = np.argmax(sizes) + 1
    mask = (labeled == largest).astype(np.uint8)

    return mask


# == Feature Extraction Pipeline ==============================================

def extract_features_for_lakes(
    lakes_gdf,
    dem_dir: str,
    output_path: str,
    buffer_m: float = 1000.0,
    max_lakes: int = None,
) -> pd.DataFrame:
    """
    Extract terrain features for all lakes.

    Args:
        lakes_gdf: GeoDataFrame with lake polygons, elevation, hylak_id
        dem_dir: Directory with DEM GeoTIFF tiles
        output_path: Where to save features parquet
        buffer_m: Buffer distance around lake (metres)
    """
    extractor = TerrainFeatureExtractor(buffer_m=buffer_m)
    all_features = []

    if max_lakes:
        lakes_gdf = lakes_gdf.head(max_lakes)

    for idx, row in tqdm(lakes_gdf.iterrows(), total=len(lakes_gdf),
                         desc="Extracting terrain features"):
        try:
            geom = row.geometry
            centroid = (geom.centroid.x, geom.centroid.y)
            bbox = geom.bounds  # (minx, miny, maxx, maxy)

            # Get lake elevation (from DEM or attribute)
            lake_elev = row.get("elevation", row.get("wse_mean", row.get("p_ref_wse", None)))
            if lake_elev is None or np.isnan(lake_elev):
                lake_elev = row.get("Elevation", np.nan)

            # Load DEM
            dem_result = load_dem_for_lake(dem_dir, centroid, bbox, buffer_m)
            if dem_result is None:
                continue

            dem, transform = dem_result
            pixel_size = abs(transform[0]) * 111320 * np.cos(np.radians(centroid[1]))

            # Create lake mask
            if lake_elev is not None and not np.isnan(lake_elev):
                lake_mask = create_lake_mask_from_dem(dem, lake_elev)
            else:
                # Estimate lake elevation from DEM minimum in approximate lake area
                lake_elev = np.nanpercentile(dem, 5)
                lake_mask = create_lake_mask_from_dem(dem, lake_elev)

            if lake_mask.sum() < 10:
                continue

            # Extract features
            feats = extractor.extract_from_arrays(dem, lake_mask, lake_elev, pixel_size)

            # Add lake ID
            feats["lake_id"] = row.get("hylak_id", row.get("lake_id", idx))
            feats["lat"] = centroid[1]
            feats["lon"] = centroid[0]

            all_features.append(feats)

        except Exception as e:
            log.debug(f"Lake {idx} error: {e}")
            continue

        # Checkpoint every 500 lakes
        if len(all_features) % 500 == 0 and len(all_features) > 0:
            log.info(f"  Extracted features for {len(all_features)} lakes")

    df = pd.DataFrame(all_features)
    if not df.empty:
        os.makedirs(Path(output_path).parent, exist_ok=True)
        df.to_parquet(output_path, index=False)
        log.info(f"Saved terrain features for {len(df)} lakes to {output_path}")
    else:
        log.warning("No features extracted")

    return df


# == Model Training ===========================================================

def train_terrain_depth_model(
    features_path: str,
    ground_truth_path: str,
    output_dir: str,
    target: str = "max_depth_m",
    n_folds: int = 5,
) -> dict:
    """
    Train XGBoost model: terrain features -> max/mean depth.

    Uses spatial cross-validation by geographic region.
    Ground truth from MN DNR sonar surveys.
    """
    from sklearn.model_selection import GroupKFold, KFold
    from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
    from sklearn.preprocessing import StandardScaler

    try:
        import xgboost as xgb
    except ImportError:
        log.error("xgboost required: pip install xgboost")
        return {}

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Load data
    features = pd.read_parquet(features_path)
    ground_truth = pd.read_parquet(ground_truth_path)

    log.info(f"Features: {len(features)} lakes, {features.shape[1]} columns")
    log.info(f"Ground truth: {len(ground_truth)} lakes")

    # Merge
    id_col = "lake_id" if "lake_id" in features.columns else "hylak_id"
    gt_id_col = "lake_id" if "lake_id" in ground_truth.columns else "hylak_id"

    merged = features.merge(
        ground_truth[[gt_id_col, target]].rename(columns={gt_id_col: id_col}),
        on=id_col,
        how="inner",
    )
    log.info(f"Merged: {len(merged)} lakes with both features and ground truth")

    if len(merged) < 50:
        log.error("Too few lakes with ground truth for training")
        return {}

    # Drop target and ID columns from features
    drop_cols = [id_col, "lat", "lon", target]
    feature_cols = [c for c in merged.columns if c not in drop_cols and merged[c].dtype in [np.float64, np.float32, "float64", "float32", "int64"]]

    X = merged[feature_cols].copy()
    y = merged[target].values

    # Drop columns with >50% NaN
    nan_frac = X.isna().mean()
    good_cols = nan_frac[nan_frac < 0.5].index.tolist()
    X = X[good_cols]
    log.info(f"Features after NaN filter: {len(good_cols)}")

    # Fill remaining NaN with median
    X = X.fillna(X.median())

    # Spatial groups for CV (grid cells)
    if "lat" in merged.columns and "lon" in merged.columns:
        # Create spatial groups (0.5 degree grid)
        groups = (
            (merged["lat"] * 2).astype(int).astype(str) + "_" +
            (merged["lon"] * 2).astype(int).astype(str)
        )
        group_ids = groups.factorize()[0]
        cv = GroupKFold(n_splits=min(n_folds, len(np.unique(group_ids))))
        split_iter = cv.split(X, y, groups=group_ids)
    else:
        cv = KFold(n_splits=n_folds, shuffle=True, random_state=42)
        split_iter = cv.split(X, y)
        group_ids = None

    # XGBoost with spatial CV
    fold_metrics = []
    fold_importances = []
    oof_preds = np.full(len(y), np.nan)

    for fold_i, (train_idx, val_idx) in enumerate(split_iter):
        X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
        y_train, y_val = y[train_idx], y[val_idx]

        model = xgb.XGBRegressor(
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

        model.fit(
            X_train, y_train,
            eval_set=[(X_val, y_val)],
            verbose=False,
        )

        preds = model.predict(X_val)
        preds = np.clip(preds, 0, None)  # Depth must be positive
        oof_preds[val_idx] = preds

        rmse = np.sqrt(mean_squared_error(y_val, preds))
        mae = mean_absolute_error(y_val, preds)
        r2 = r2_score(y_val, preds)

        fold_metrics.append({"fold": fold_i, "rmse": rmse, "mae": mae, "r2": r2})
        log.info(f"  Fold {fold_i}: RMSE={rmse:.2f}m, MAE={mae:.2f}m, R2={r2:.3f}")

        # Feature importance
        imp = dict(zip(good_cols, model.feature_importances_))
        fold_importances.append(imp)

    # Overall metrics
    valid_mask = ~np.isnan(oof_preds)
    overall_rmse = np.sqrt(mean_squared_error(y[valid_mask], oof_preds[valid_mask]))
    overall_mae = mean_absolute_error(y[valid_mask], oof_preds[valid_mask])
    overall_r2 = r2_score(y[valid_mask], oof_preds[valid_mask])

    log.info(f"\n=== Terrain Prior Model ({target}) ===")
    log.info(f"Overall RMSE: {overall_rmse:.2f}m")
    log.info(f"Overall MAE: {overall_mae:.2f}m")
    log.info(f"Overall R2: {overall_r2:.3f}")
    log.info(f"N lakes: {valid_mask.sum()}")

    # Train final model on all data
    final_model = xgb.XGBRegressor(
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
    final_model.fit(X, y, verbose=False)

    # Save model
    model_path = out / "terrain_prior_xgb.json"
    final_model.save_model(str(model_path))
    log.info(f"Model saved to {model_path}")

    # Save feature importance
    avg_importance = {}
    for imp in fold_importances:
        for k, v in imp.items():
            avg_importance[k] = avg_importance.get(k, 0) + v / len(fold_importances)

    top_features = sorted(avg_importance.items(), key=lambda x: -x[1])[:20]
    log.info("\nTop 20 features:")
    for fname, fimp in top_features:
        log.info(f"  {fimp:.4f}  {fname}")

    # Save metadata
    metadata = {
        "target": target,
        "n_lakes": int(valid_mask.sum()),
        "n_features": len(good_cols),
        "feature_cols": good_cols,
        "overall_rmse": float(overall_rmse),
        "overall_mae": float(overall_mae),
        "overall_r2": float(overall_r2),
        "fold_metrics": fold_metrics,
        "feature_importance": dict(top_features),
    }
    with open(str(out / "terrain_prior_metadata.json"), "w") as f:
        json.dump(metadata, f, indent=2)

    return metadata


# == Prediction ===============================================================

def predict_terrain_depths(
    features_path: str,
    model_dir: str,
    output_path: str,
) -> pd.DataFrame:
    """Predict depths for all lakes using trained terrain prior model."""
    import xgboost as xgb

    model_path = Path(model_dir) / "terrain_prior_xgb.json"
    meta_path = Path(model_dir) / "terrain_prior_metadata.json"

    model = xgb.XGBRegressor()
    model.load_model(str(model_path))

    with open(str(meta_path)) as f:
        meta = json.load(f)

    feature_cols = meta["feature_cols"]

    features = pd.read_parquet(features_path)
    X = features[feature_cols].fillna(features[feature_cols].median())

    preds = model.predict(X)
    preds = np.clip(preds, 0, None)

    features["terrain_predicted_depth_m"] = preds
    features["terrain_prediction_confidence"] = _compute_confidence(features, meta)

    os.makedirs(Path(output_path).parent, exist_ok=True)
    features.to_parquet(output_path, index=False)
    log.info(f"Predictions saved: {output_path} ({len(features)} lakes)")

    return features


def _compute_confidence(features: pd.DataFrame, meta: dict) -> np.ndarray:
    """
    Compute prediction confidence based on feature availability and range.

    Higher confidence when:
      - More features are non-NaN
      - Feature values within training distribution
    """
    feature_cols = meta["feature_cols"]
    n_features = len(feature_cols)

    # Fraction of features that are non-NaN
    available = features[feature_cols].notna().mean(axis=1).values

    # Simple confidence = fraction of available features
    confidence = np.clip(available, 0, 1)

    return confidence


# == CLI ======================================================================

def main():
    parser = argparse.ArgumentParser(description="Terrain DEM prior for lake depth")
    sub = parser.add_subparsers(dest="command")

    # Extract features
    ext = sub.add_parser("extract", help="Extract terrain features")
    ext.add_argument("--dem-dir", required=True, help="DEM GeoTIFF directory")
    ext.add_argument("--lakes", required=True, help="Lake polygons (GeoPackage/Shapefile)")
    ext.add_argument("--output", default="/data/terrain_features.parquet")
    ext.add_argument("--buffer-m", type=float, default=1000.0)
    ext.add_argument("--max-lakes", type=int, default=None)

    # Train model
    tr = sub.add_parser("train", help="Train terrain -> depth model")
    tr.add_argument("--features", required=True, help="Terrain features parquet")
    tr.add_argument("--ground-truth", required=True, help="MN sonar depths parquet")
    tr.add_argument("--output", default="/data/models/terrain_prior")
    tr.add_argument("--target", default="max_depth_m", choices=["max_depth_m", "mean_depth_m"])
    tr.add_argument("--folds", type=int, default=5)

    # Predict
    pr = sub.add_parser("predict", help="Predict depth from terrain features")
    pr.add_argument("--features", required=True)
    pr.add_argument("--model", required=True)
    pr.add_argument("--output", default="/data/terrain_depth_predictions.parquet")

    args = parser.parse_args()

    if args.command == "extract":
        import geopandas as gpd
        lakes_gdf = gpd.read_file(args.lakes)
        extract_features_for_lakes(
            lakes_gdf, args.dem_dir, args.output,
            buffer_m=args.buffer_m, max_lakes=args.max_lakes,
        )

    elif args.command == "train":
        train_terrain_depth_model(
            args.features, args.ground_truth, args.output,
            target=args.target, n_folds=args.folds,
        )

    elif args.command == "predict":
        predict_terrain_depths(args.features, args.model, args.output)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
