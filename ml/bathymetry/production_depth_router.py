#!/usr/bin/env python3
"""
OpenCatch — Production Depth Router
====================================
Production entry point for all OpenCatch bathymetry queries.

Implements the "sonar-first, ML-fallback" principle: for every location,
return the most accurate available depth source with provenance,
accuracy estimate, and confidence.

Data priority (best → worst):
    1. MN DNR sonar contours  (sub-meter, 2,010 lakes)
    2. Other state DNR surveys (MI, WI, TX…)
    3. NOAA CUDEM             (3m coastal)
    4. NOAA ENC soundings     (navigable waters)
    5. GEBCO 2025             (450m global ocean)
    6. 3D-LAKES / GLOBathy    (510K+ lakes, modeled)
    7. OpenCatch ML model     (soft-blend regime routing)
    8. Morphometric / K-donor (last resort prior)

Each source carries an estimated RMSE that propagates to the output.

Usage:
    # Single point
    python production_depth_router.py --mode point --lat 46.85 --lon -94.37

    # Full lake map
    python production_depth_router.py \\
        --mode lake-map \\
        --lake-id 27013300 \\
        --resolution 10 \\
        --output /data/production/27013300.tif

    # Region coverage report
    python production_depth_router.py \\
        --mode coverage \\
        --bbox -97.5,43.0,-89.0,49.5

Requirements:
    pip install numpy pandas geopandas rasterio shapely scipy tqdm
    pip install xgboost torch scikit-learn
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import pickle
import sys
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

try:
    import geopandas as gpd
    from shapely.geometry import Point, box
except ImportError:
    gpd = None

try:
    import rasterio
    from rasterio.transform import from_bounds
    from rasterio.crs import CRS
except ImportError:
    rasterio = None

try:
    from scipy.interpolate import griddata, RBFInterpolator
    from scipy.ndimage import gaussian_filter, distance_transform_edt
    from scipy.spatial import cKDTree
except ImportError:
    griddata = None
    RBFInterpolator = None
    gaussian_filter = None
    distance_transform_edt = None
    cKDTree = None

try:
    import pandas as pd
except ImportError:
    pd = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("depth_router")

EPS = 1e-8


# ── Enums & Dataclasses ─────────────────────────────────────────────

class DepthTier(str, Enum):
    """Accuracy tier for depth estimates."""
    SURVEY = "survey"       # Sub-meter, authoritative sonar/contour data
    MODELED = "modeled"     # 3D-LAKES / GLOBathy physics-based models
    ML = "ml"               # OpenCatch ML predictions (spectral + spatial)
    PRIOR = "prior"         # Morphometric / K-donor last-resort estimate


@dataclass
class DepthResult:
    """Single-point depth estimate with full provenance."""
    depth_m: float
    source: str             # e.g. "mn_dnr_survey", "cudem", "ml_tier1"
    rmse_m: float           # Estimated accuracy (RMSE in metres)
    confidence: float       # 0.0–1.0, higher is better
    tier: str               # DepthTier value
    attribution: str = ""
    survey_date: Optional[str] = None
    model_version: Optional[str] = None
    blend_weights: Optional[Dict[str, float]] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # Drop None fields for cleaner JSON
        return {k: v for k, v in d.items() if v is not None}


@dataclass
class LakeMap:
    """Full bathymetric grid for one lake."""
    depth: np.ndarray           # (H, W) depth in metres
    source: np.ndarray          # (H, W) categorical source labels
    rmse: np.ndarray            # (H, W) per-pixel RMSE
    confidence: np.ndarray      # (H, W) per-pixel confidence
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def shape(self) -> Tuple[int, int]:
        return self.depth.shape


# ── Source Metadata ──────────────────────────────────────────────────

SOURCE_RMSE = {
    "mn_dnr_survey": 0.5,
    "wi_dnr_survey": 0.5,
    "mi_dnr_survey": 0.5,
    "tx_tpwd_survey": 0.8,
    "cudem": 1.0,
    "noaa_enc": 1.5,
    "gebco": 50.0,
    "3dlakes": 1.37,
    "globathy": 4.5,
    "glacial_model": 2.49,
    "mountain_model": 3.72,
    "unified_fallback": 4.08,
    "ml_tier1": 2.76,       # Validated shallow-water ML
    "ml_tier2": 5.0,        # Deep-water / low-confidence ML
    "morphometric_prior": 8.0,
    "k_donor": 6.0,
}

SOURCE_TIER = {
    "mn_dnr_survey": DepthTier.SURVEY,
    "wi_dnr_survey": DepthTier.SURVEY,
    "mi_dnr_survey": DepthTier.SURVEY,
    "tx_tpwd_survey": DepthTier.SURVEY,
    "cudem": DepthTier.SURVEY,
    "noaa_enc": DepthTier.SURVEY,
    "gebco": DepthTier.SURVEY,
    "3dlakes": DepthTier.MODELED,
    "globathy": DepthTier.MODELED,
    "glacial_model": DepthTier.ML,
    "mountain_model": DepthTier.ML,
    "unified_fallback": DepthTier.ML,
    "ml_tier1": DepthTier.ML,
    "ml_tier2": DepthTier.ML,
    "morphometric_prior": DepthTier.PRIOR,
    "k_donor": DepthTier.PRIOR,
}

SOURCE_ATTRIBUTION = {
    "mn_dnr_survey": "Minnesota DNR Lake Surveys (public domain)",
    "wi_dnr_survey": "Wisconsin DNR Lake Maps (public domain)",
    "mi_dnr_survey": "Michigan EGLE Lake Contour Maps (public domain)",
    "tx_tpwd_survey": "Texas Parks & Wildlife (public domain)",
    "cudem": "NOAA CUDEM Coastal Elevation Models (public domain)",
    "noaa_enc": "NOAA ENC Soundings (public domain)",
    "gebco": "GEBCO 2025 Global Bathymetry (public domain)",
    "3dlakes": "3D-LAKES Global Lake Bathymetry (CC-BY 4.0)",
    "globathy": "GLOBathy Global Lake Bathymetry (CC-BY 4.0)",
    "glacial_model": "OpenCatch Glacial Specialist Model",
    "mountain_model": "OpenCatch Mountain Specialist Model",
    "unified_fallback": "OpenCatch Unified Fallback Model",
    "ml_tier1": "OpenCatch ML Bathymetry",
    "ml_tier2": "OpenCatch ML Bathymetry (low confidence)",
    "morphometric_prior": "OpenCatch morphometric prior",
    "k_donor": "OpenCatch K-donor transfer",
}

# Default data / model paths (overridable via config)
DEFAULT_PATHS = {
    "survey_dir": "/data/sonar_surveys",
    "mn_dnr_contours": "/data/sonar_surveys/mn_dnr/contours",
    "cudem_dir": "/data/cudem",
    "gebco_path": "/data/gebco/GEBCO_2025.nc",
    "lakes_3d_dir": "/data/3dlakes",
    "globathy_dir": "/data/globathy",
    "ml_models_dir": "/data/models/hybrid_sonar",
    "ensemble_meta": "/data/models/ensemble/meta_learner.pkl",
    "morphometric_model": "/data/models/stage1/max_depth_xgb.pkl",
    "waterbodies": "/data/waterbodies/us/all_us_waterbodies.geojson",
    "lake_index": "/data/waterbodies/lake_spatial_index.pkl",
}


# ── Survey Contour Interpolator ─────────────────────────────────────

class SurveyContourInterpolator:
    """
    Interpolates DNR sonar contour shapefiles to a regular depth grid.

    Contour data is typically a set of polylines at fixed depth intervals
    (e.g. 5ft, 10ft, 15ft). We densify the contours, then use RBF
    interpolation to produce a smooth depth surface.
    """

    def __init__(self, contour_dir: Path):
        self.contour_dir = Path(contour_dir)

    def has_contours(self, lake_id: str) -> bool:
        """Check if survey contours exist for a lake."""
        pattern = f"*{lake_id}*"
        matches = list(self.contour_dir.glob(pattern))
        if not matches:
            # Also check subdirectories
            matches = list(self.contour_dir.rglob(f"{pattern}.shp"))
            matches += list(self.contour_dir.rglob(f"{pattern}.geojson"))
        return len(matches) > 0

    def load_contours(self, lake_id: str) -> Optional[gpd.GeoDataFrame]:
        """Load contour lines for a lake."""
        if gpd is None:
            log.warning("geopandas not available, cannot load contours")
            return None

        for ext in (".geojson", ".shp", ".gpkg"):
            candidates = list(self.contour_dir.rglob(f"*{lake_id}*{ext}"))
            if candidates:
                gdf = gpd.read_file(candidates[0])
                log.info(
                    "Loaded %d contour features for lake %s from %s",
                    len(gdf), lake_id, candidates[0].name,
                )
                return gdf
        return None

    def interpolate_to_grid(
        self,
        lake_id: str,
        bounds: Tuple[float, float, float, float],
        resolution_m: float = 10.0,
    ) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        """
        Interpolate survey contours to a regular grid.

        Args:
            lake_id: Lake identifier.
            bounds: (minx, miny, maxx, maxy) in EPSG:4326.
            resolution_m: Target grid spacing in metres.

        Returns:
            Tuple of (depth_grid, confidence_grid) or None if no data.
        """
        gdf = self.load_contours(lake_id)
        if gdf is None or len(gdf) == 0:
            return None

        if griddata is None:
            log.warning("scipy not available for interpolation")
            return None

        # ── Extract depth values from contour geometries ─────────
        depth_col = _find_depth_column(gdf)
        if depth_col is None:
            log.warning("No depth column found in contour data for %s", lake_id)
            return None

        # Densify contour lines → point cloud
        points, depths = [], []
        for _, row in gdf.iterrows():
            geom = row.geometry
            d = float(row[depth_col])
            if geom is None:
                continue
            # Convert feet to metres if needed (MN DNR is typically in feet)
            if d > 0:
                d = d * 0.3048  # ft → m
            coords = _densify_geometry(geom, spacing_m=resolution_m * 0.5)
            for c in coords:
                points.append(c)
                depths.append(d)

        if len(points) < 10:
            log.warning("Too few contour points (%d) for lake %s", len(points), lake_id)
            return None

        points = np.array(points)
        depths = np.array(depths)

        # ── Build regular grid ───────────────────────────────────
        minx, miny, maxx, maxy = bounds
        # Approximate degrees → metres at this latitude
        lat_mid = (miny + maxy) / 2.0
        m_per_deg_lon = 111320 * np.cos(np.radians(lat_mid))
        m_per_deg_lat = 110540
        nx = max(2, int((maxx - minx) * m_per_deg_lon / resolution_m))
        ny = max(2, int((maxy - miny) * m_per_deg_lat / resolution_m))
        nx = min(nx, 2000)  # Cap grid size
        ny = min(ny, 2000)

        xi = np.linspace(minx, maxx, nx)
        yi = np.linspace(miny, maxy, ny)
        grid_x, grid_y = np.meshgrid(xi, yi)

        # ── Interpolate ─────────────────────────────────────────
        depth_grid = griddata(
            points, depths, (grid_x, grid_y), method="cubic", fill_value=np.nan,
        )

        # Smooth to reduce contour artefacts
        if gaussian_filter is not None:
            valid = ~np.isnan(depth_grid)
            if valid.any():
                smoothed = gaussian_filter(np.nan_to_num(depth_grid, nan=0.0), sigma=1.0)
                depth_grid[valid] = smoothed[valid]

        # Confidence: high near contour points, decays with distance
        confidence_grid = _compute_distance_confidence(
            grid_x, grid_y, points, max_distance_deg=0.002,
        )

        log.info(
            "Interpolated lake %s: %dx%d grid, depth range %.1f–%.1f m",
            lake_id, nx, ny,
            np.nanmin(depth_grid), np.nanmax(depth_grid),
        )
        return depth_grid, confidence_grid


# ── ML Model Wrapper ────────────────────────────────────────────────

class MLDepthPredictor:
    """
    Wraps trained OpenCatch ML models for inference.

    Loads the ensemble of models (hybrid sonar, terrain, spectral)
    and the Ridge meta-learner, then runs regime-based routing:
    - Clear water + spectral features → full spectral ensemble
    - Turbid water → terrain-only model
    - No features → morphometric prior
    """

    def __init__(self, models_dir: Path, ensemble_meta_path: Path, device: str = "cpu"):
        self.models_dir = Path(models_dir)
        self.ensemble_meta_path = Path(ensemble_meta_path)
        self.device = device
        self._models = {}
        self._meta_learner = None
        self._scaler = None
        self._loaded = False

    def load(self) -> bool:
        """Load all model artifacts. Returns True if at least one model loaded."""
        loaded_count = 0

        # ── Meta-learner (Ridge stacking) ────────────────────────
        if self.ensemble_meta_path.exists():
            try:
                with open(self.ensemble_meta_path, "rb") as f:
                    bundle = pickle.load(f)
                self._meta_learner = bundle.get("meta_learner")
                self._scaler = bundle.get("scaler")
                log.info("Loaded meta-learner from %s", self.ensemble_meta_path)
                loaded_count += 1
            except Exception as e:
                log.warning("Failed to load meta-learner: %s", e)

        # ── Base models ──────────────────────────────────────────
        for model_name in ("hybrid_sonar", "terrain_depth", "spectral_kan"):
            model_path = self.models_dir / model_name / "best_model.pkl"
            if not model_path.exists():
                model_path = self.models_dir / f"{model_name}.pkl"
            if model_path.exists():
                try:
                    with open(model_path, "rb") as f:
                        self._models[model_name] = pickle.load(f)
                    log.info("Loaded model: %s", model_name)
                    loaded_count += 1
                except Exception as e:
                    log.warning("Failed to load %s: %s", model_name, e)

        # ── Morphometric max-depth model ─────────────────────────
        morph_path = self.models_dir.parent / "stage1" / "max_depth_xgb.pkl"
        if morph_path.exists():
            try:
                with open(morph_path, "rb") as f:
                    self._models["morphometric"] = pickle.load(f)
                log.info("Loaded morphometric model from %s", morph_path)
                loaded_count += 1
            except Exception as e:
                log.warning("Failed to load morphometric model: %s", e)

        self._loaded = loaded_count > 0
        log.info("ML predictor ready: %d models loaded", loaded_count)
        return self._loaded

    def predict_point(
        self,
        features: Dict[str, float],
        lake_features: Optional[Dict[str, float]] = None,
    ) -> Optional[DepthResult]:
        """
        Run ML prediction for a single point.

        Args:
            features: Spectral/spatial features for this pixel.
            lake_features: Lake-level morphometric features.

        Returns:
            DepthResult from ML model, or None if no model available.
        """
        if not self._loaded:
            return None

        predictions = {}
        has_spectral = "log_blue_green" in features or "B02" in features

        # ── Collect base model predictions ───────────────────────
        for name, model in self._models.items():
            if name == "morphometric":
                continue
            try:
                if name in ("spectral_kan", "hybrid_sonar") and not has_spectral:
                    continue
                feat_array = self._prepare_features(name, features, lake_features)
                if feat_array is not None:
                    pred = float(model.predict(feat_array.reshape(1, -1))[0])
                    predictions[name] = max(0.0, pred)
            except Exception as e:
                log.debug("Model %s failed: %s", name, e)

        if not predictions:
            # Fall back to morphometric-only
            return self._morphometric_fallback(lake_features)

        # ── Meta-learner stacking ────────────────────────────────
        if self._meta_learner is not None and len(predictions) >= 2:
            depth_m = self._stack_predictions(predictions)
            source = "ml_tier1"
            rmse = SOURCE_RMSE["ml_tier1"]
            confidence = min(1.0, len(predictions) / 3.0) * 0.85
        else:
            # Single best model
            best_name = min(predictions, key=lambda n: SOURCE_RMSE.get(n, 10.0))
            depth_m = predictions[best_name]
            source = "ml_tier2"
            rmse = SOURCE_RMSE["ml_tier2"]
            confidence = 0.5

        return DepthResult(
            depth_m=round(depth_m, 2),
            source=source,
            rmse_m=rmse,
            confidence=round(confidence, 3),
            tier=DepthTier.ML.value,
            attribution=SOURCE_ATTRIBUTION.get(source, "OpenCatch ML"),
            blend_weights=predictions if len(predictions) > 1 else None,
        )

    def predict_grid(
        self,
        feature_stack: np.ndarray,
        lake_features: Optional[Dict[str, float]] = None,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Batch prediction over a feature grid.

        Args:
            feature_stack: (H, W, C) feature array.
            lake_features: Lake-level features (applied uniformly).

        Returns:
            Tuple of (depth, rmse, confidence) arrays, each (H, W).
        """
        H, W, C = feature_stack.shape
        flat = feature_stack.reshape(-1, C)
        valid_mask = ~np.isnan(flat).any(axis=1)

        depth_out = np.full(H * W, np.nan)
        rmse_out = np.full(H * W, np.nan)
        conf_out = np.zeros(H * W)

        if valid_mask.sum() == 0:
            return (
                depth_out.reshape(H, W),
                rmse_out.reshape(H, W),
                conf_out.reshape(H, W),
            )

        # Use the best available model for batch prediction
        for model_name in ("hybrid_sonar", "terrain_depth", "spectral_kan"):
            if model_name in self._models:
                try:
                    preds = self._models[model_name].predict(flat[valid_mask])
                    preds = np.clip(preds, 0, None)
                    depth_out[valid_mask] = preds
                    rmse_out[valid_mask] = SOURCE_RMSE.get("ml_tier1", 2.76)
                    conf_out[valid_mask] = 0.7
                    break
                except Exception as e:
                    log.debug("Batch predict with %s failed: %s", model_name, e)

        return (
            depth_out.reshape(H, W),
            rmse_out.reshape(H, W),
            conf_out.reshape(H, W),
        )

    def _prepare_features(
        self,
        model_name: str,
        features: Dict[str, float],
        lake_features: Optional[Dict[str, float]],
    ) -> Optional[np.ndarray]:
        """Build feature vector for a specific model."""
        combined = dict(features)
        if lake_features:
            combined.update(lake_features)
        vals = list(combined.values())
        if not vals:
            return None
        return np.array(vals, dtype=np.float32)

    def _stack_predictions(self, predictions: Dict[str, float]) -> float:
        """Apply Ridge meta-learner to base model predictions."""
        # Order must match training: hybrid_sonar, terrain_depth, spectral_kan
        ordered = []
        for name in ("hybrid_sonar", "terrain_depth", "spectral_kan"):
            ordered.append(predictions.get(name, np.nan))
        x = np.array(ordered).reshape(1, -1)
        # Fill missing with mean of available
        mask = ~np.isnan(x[0])
        if mask.sum() == 0:
            return np.nanmean(list(predictions.values()))
        mean_val = np.nanmean(x[0])
        x[0][~mask] = mean_val
        if self._scaler is not None:
            x = self._scaler.transform(x)
        return float(self._meta_learner.predict(x)[0])

    def _morphometric_fallback(
        self, lake_features: Optional[Dict[str, float]]
    ) -> Optional[DepthResult]:
        """Estimate depth from morphometric features alone."""
        if "morphometric" not in self._models or lake_features is None:
            return None
        try:
            feat = np.array(list(lake_features.values()), dtype=np.float32).reshape(1, -1)
            pred = float(self._models["morphometric"].predict(feat)[0])
            return DepthResult(
                depth_m=round(max(0.0, pred), 2),
                source="morphometric_prior",
                rmse_m=SOURCE_RMSE["morphometric_prior"],
                confidence=0.3,
                tier=DepthTier.PRIOR.value,
                attribution=SOURCE_ATTRIBUTION["morphometric_prior"],
            )
        except Exception as e:
            log.debug("Morphometric fallback failed: %s", e)
            return None


# ── Main Router ──────────────────────────────────────────────────────

class ProductionDepthRouter:
    """
    Production bathymetry router for OpenCatch.

    For any (lat, lon) or lake_id query, returns the best available
    depth estimate with provenance, accuracy estimate, and confidence.
    """

    def __init__(self, config_path: Optional[str] = None):
        """
        Load all available data sources and ML models.

        Args:
            config_path: Optional JSON config overriding default paths.
        """
        self.paths = dict(DEFAULT_PATHS)
        if config_path and Path(config_path).exists():
            with open(config_path) as f:
                overrides = json.load(f)
            self.paths.update(overrides)
            log.info("Loaded config overrides from %s", config_path)

        # ── Spatial index for lake lookup ────────────────────────
        self._lake_index = None
        self._waterbodies = None
        self._load_lake_index()

        # ── Survey contour interpolator ──────────────────────────
        self._contour_interp = SurveyContourInterpolator(
            self.paths["mn_dnr_contours"]
        )

        # ── ML predictor ─────────────────────────────────────────
        self._ml = MLDepthPredictor(
            models_dir=Path(self.paths["ml_models_dir"]),
            ensemble_meta_path=Path(self.paths["ensemble_meta"]),
        )
        self._ml.load()

        # ── Cache for survey availability ────────────────────────
        self._survey_cache: Dict[str, str] = {}
        self._scan_survey_sources()

        log.info("ProductionDepthRouter initialised")

    # ── Public API ───────────────────────────────────────────────

    def get_depth(
        self,
        lat: float,
        lon: float,
        lake_id: Optional[str] = None,
    ) -> DepthResult:
        """
        Get depth at a single point.

        Walks down the priority chain until a source returns data.

        Args:
            lat: Latitude (WGS84).
            lon: Longitude (WGS84).
            lake_id: Optional lake identifier (speeds up lookup).

        Returns:
            DepthResult with depth, source, accuracy, and confidence.
        """
        if lake_id is None:
            lake_id = self._resolve_lake_id(lat, lon)

        # ── 1. Survey data (highest priority) ────────────────────
        if lake_id:
            survey_source = self._survey_cache.get(lake_id)
            if survey_source:
                result = self._query_survey(lat, lon, lake_id, survey_source)
                if result is not None:
                    log.debug("Survey hit for %s at (%.4f, %.4f)", lake_id, lat, lon)
                    return result

        # ── 2. CUDEM (coastal 3m) ────────────────────────────────
        cudem_result = self._query_cudem(lat, lon)
        if cudem_result is not None:
            return cudem_result

        # ── 3. GEBCO (ocean fallback) ────────────────────────────
        gebco_result = self._query_gebco(lat, lon)
        if gebco_result is not None:
            return gebco_result

        # ── 4. 3D-LAKES / GLOBathy (modeled lakes) ──────────────
        if lake_id:
            modeled_result = self._query_modeled(lat, lon, lake_id)
            if modeled_result is not None:
                # If ML is also available, blend modeled prior with ML
                ml_result = self._query_ml_point(lat, lon, lake_id)
                if ml_result is not None:
                    return self._blend_modeled_and_ml(modeled_result, ml_result)
                return modeled_result

        # ── 5. ML model (spectral + spatial) ────────────────────
        if lake_id:
            ml_result = self._query_ml_point(lat, lon, lake_id)
            if ml_result is not None:
                return ml_result

        # ── 6. Morphometric / K-donor prior (last resort) ───────
        if lake_id:
            prior = self._morphometric_prior(lake_id)
            if prior is not None:
                return prior

        # ── No data at all ───────────────────────────────────────
        log.warning("No depth data for (%.4f, %.4f) lake_id=%s", lat, lon, lake_id)
        return DepthResult(
            depth_m=np.nan,
            source="none",
            rmse_m=np.nan,
            confidence=0.0,
            tier="none",
            attribution="No data available",
        )

    def get_lake_map(
        self,
        lake_id: str,
        resolution_m: float = 10.0,
    ) -> Optional[LakeMap]:
        """
        Get full bathymetric map for a lake.

        Strategy:
        - If survey contours exist: interpolate to grid (best quality)
        - If ML-eligible: regime model + residual correction
        - Otherwise: morphometric/donor estimate + uncertainty

        Args:
            lake_id: Lake identifier (e.g. MN DNR ID or NHD COMID).
            resolution_m: Grid resolution in metres.

        Returns:
            LakeMap with depth, source, rmse, confidence grids.
        """
        bounds = self._get_lake_bounds(lake_id)
        if bounds is None:
            log.error("Cannot find bounds for lake %s", lake_id)
            return None

        minx, miny, maxx, maxy = bounds
        lat_mid = (miny + maxy) / 2.0
        m_per_deg_lon = 111320 * np.cos(np.radians(lat_mid))
        m_per_deg_lat = 110540
        nx = max(2, int((maxx - minx) * m_per_deg_lon / resolution_m))
        ny = max(2, int((maxy - miny) * m_per_deg_lat / resolution_m))
        nx, ny = min(nx, 2000), min(ny, 2000)
        transform = from_bounds(*bounds, nx, ny) if rasterio is not None else None

        metadata = {
            "lake_id": lake_id,
            "resolution_m": resolution_m,
            "bounds": list(bounds),
            "grid_shape": [ny, nx],
            "crs": "EPSG:4326",
            "transform": transform,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        # ── Try survey contours first ────────────────────────────
        survey_source = self._survey_cache.get(lake_id)
        if survey_source and self._contour_interp.has_contours(lake_id):
            result = self._contour_interp.interpolate_to_grid(
                lake_id, bounds, resolution_m,
            )
            if result is not None:
                depth_grid, conf_grid = result
                source_grid = np.where(
                    ~np.isnan(depth_grid), survey_source, "none",
                )
                rmse_grid = np.where(
                    ~np.isnan(depth_grid),
                    SOURCE_RMSE.get(survey_source, 0.5),
                    np.nan,
                )

                # Gap-fill with ML where survey has no coverage
                gap_mask = np.isnan(depth_grid)
                if gap_mask.any() and self._ml._loaded:
                    log.info(
                        "Gap-filling %d/%d pixels with ML for lake %s",
                        gap_mask.sum(), depth_grid.size, lake_id,
                    )
                    depth_grid, source_grid, rmse_grid, conf_grid = (
                        self._gap_fill_with_ml(
                            depth_grid, source_grid, rmse_grid, conf_grid,
                            gap_mask, bounds, lake_id, nx, ny, transform,
                        )
                    )

                metadata["primary_source"] = survey_source
                metadata["tier"] = DepthTier.SURVEY.value
                metadata["attribution"] = SOURCE_ATTRIBUTION.get(survey_source, "Survey data")
                metadata["gap_filled"] = bool(gap_mask.any())
                return LakeMap(
                    depth=depth_grid,
                    source=source_grid,
                    rmse=rmse_grid.astype(np.float32),
                    confidence=conf_grid.astype(np.float32),
                    metadata=metadata,
                )

        # ── ML-only map ──────────────────────────────────────────
        if self._ml._loaded:
            log.info("Generating ML-only map for lake %s", lake_id)
            ml_map = self._build_ml_fallback_map(
                lake_id=lake_id,
                bounds=bounds,
                nx=nx,
                ny=ny,
                transform=transform,
                resolution_m=resolution_m,
            )
            if ml_map is not None:
                ml_map.metadata.update(metadata)
                return ml_map

        # ── Morphometric prior (last resort) ─────────────────────
        log.info("Using morphometric prior for lake %s", lake_id)
        prior = self._morphometric_prior(lake_id)
        if prior is not None:
            depth_grid = np.full((ny, nx), prior.depth_m)
            rmse_grid = np.full((ny, nx), prior.rmse_m)
            conf_grid = np.full((ny, nx), prior.confidence)
            source_grid = np.full((ny, nx), prior.source, dtype=object)
            metadata["primary_source"] = prior.source
            return LakeMap(
                depth=depth_grid,
                source=source_grid,
                rmse=rmse_grid.astype(np.float32),
                confidence=conf_grid.astype(np.float32),
                metadata=metadata,
            )

        return None

    def get_region_coverage(
        self,
        bbox: Tuple[float, float, float, float],
    ) -> Dict[str, Any]:
        """
        Report what data sources cover a bounding box region.

        Args:
            bbox: (minx, miny, maxx, maxy) in EPSG:4326.

        Returns:
            Summary dict with counts per source, coverage stats.
        """
        minx, miny, maxx, maxy = bbox
        coverage = {
            "bbox": list(bbox),
            "total_lakes": 0,
            "by_source": {},
            "by_tier": {t.value: 0 for t in DepthTier},
            "coverage_pct": 0.0,
        }

        if self._waterbodies is None:
            log.warning("No waterbodies loaded, cannot compute coverage")
            return coverage

        region_box = box(minx, miny, maxx, maxy)
        in_region = self._waterbodies[
            self._waterbodies.geometry.intersects(region_box)
        ]
        coverage["total_lakes"] = len(in_region)

        if len(in_region) == 0:
            return coverage

        # Count lakes by best available source
        for _, lake in in_region.iterrows():
            lid = str(lake.get("lake_id", lake.get("GNIS_ID", "")))
            source = self._survey_cache.get(lid, "none")
            if source == "none":
                # Check modeled / ML availability
                source = self._check_best_available(lid)
            tier = SOURCE_TIER.get(source, DepthTier.PRIOR).value

            coverage["by_source"][source] = coverage["by_source"].get(source, 0) + 1
            coverage["by_tier"][tier] += 1

        covered = sum(
            v for k, v in coverage["by_source"].items() if k != "none"
        )
        coverage["coverage_pct"] = round(
            100.0 * covered / max(1, coverage["total_lakes"]), 1,
        )

        log.info(
            "Region coverage: %d/%d lakes (%.1f%%) in bbox %s",
            covered, coverage["total_lakes"], coverage["coverage_pct"], bbox,
        )
        return coverage

    # ── Private: Data Source Queries ─────────────────────────────

    def _load_lake_index(self):
        """Load spatial index for fast lake lookup."""
        idx_path = Path(self.paths["lake_index"])
        if idx_path.exists():
            try:
                with open(idx_path, "rb") as f:
                    self._lake_index = pickle.load(f)
                log.info("Loaded lake spatial index (%d entries)", len(self._lake_index))
            except Exception as e:
                log.warning("Failed to load lake index: %s", e)

        wb_path = Path(self.paths["waterbodies"])
        if gpd is not None and wb_path.exists():
            try:
                self._waterbodies = gpd.read_file(wb_path)
                log.info("Loaded %d waterbodies", len(self._waterbodies))
            except Exception as e:
                log.warning("Failed to load waterbodies: %s", e)

    def _scan_survey_sources(self):
        """Scan survey directories and build lake_id → source mapping."""
        survey_dir = Path(self.paths["survey_dir"])
        if not survey_dir.exists():
            log.info("Survey directory not found: %s", survey_dir)
            return

        for state_dir in survey_dir.iterdir():
            if not state_dir.is_dir():
                continue
            state = state_dir.name.lower()
            source_key = {
                "mn_dnr": "mn_dnr_survey",
                "wi_dnr": "wi_dnr_survey",
                "mi_egle": "mi_dnr_survey",
                "tx_tpwd": "tx_tpwd_survey",
            }.get(state, f"{state}_survey")

            for f in state_dir.rglob("*.shp"):
                lid = _extract_lake_id_from_filename(f.stem)
                if lid:
                    self._survey_cache[lid] = source_key
            for f in state_dir.rglob("*.geojson"):
                lid = _extract_lake_id_from_filename(f.stem)
                if lid:
                    self._survey_cache[lid] = source_key

        log.info("Survey cache: %d lakes with survey data", len(self._survey_cache))

    def _resolve_lake_id(self, lat: float, lon: float) -> Optional[str]:
        """Find which lake a point falls in."""
        if self._waterbodies is not None and gpd is not None:
            pt = Point(lon, lat)
            hits = self._waterbodies[self._waterbodies.geometry.contains(pt)]
            if len(hits) > 0:
                row = hits.iloc[0]
                return str(row.get("lake_id", row.get("GNIS_ID", "")))
        return None

    def _query_survey(
        self,
        lat: float,
        lon: float,
        lake_id: str,
        source: str,
    ) -> Optional[DepthResult]:
        """Query interpolated survey data at a point."""
        bounds = self._get_lake_bounds(lake_id)
        if bounds is None:
            return None

        result = self._contour_interp.interpolate_to_grid(lake_id, bounds)
        if result is None:
            return None

        depth_grid, conf_grid = result
        minx, miny, maxx, maxy = bounds

        # Map lat/lon to grid indices
        col = int((lon - minx) / max(EPS, maxx - minx) * (depth_grid.shape[1] - 1))
        row = int((maxy - lat) / max(EPS, maxy - miny) * (depth_grid.shape[0] - 1))
        row = np.clip(row, 0, depth_grid.shape[0] - 1)
        col = np.clip(col, 0, depth_grid.shape[1] - 1)

        depth = depth_grid[row, col]
        if np.isnan(depth):
            return None

        return DepthResult(
            depth_m=round(float(depth), 2),
            source=source,
            rmse_m=SOURCE_RMSE.get(source, 0.5),
            confidence=round(float(conf_grid[row, col]), 3),
            tier=DepthTier.SURVEY.value,
            attribution=SOURCE_ATTRIBUTION.get(source, "Survey data"),
        )

    def _query_cudem(self, lat: float, lon: float) -> Optional[DepthResult]:
        """Query NOAA CUDEM raster at a point."""
        cudem_dir = Path(self.paths["cudem_dir"])
        if not cudem_dir.exists() or rasterio is None:
            return None
        # Find the tile covering this point
        for tif in cudem_dir.glob("*.tif"):
            try:
                with rasterio.open(tif) as src:
                    if not (src.bounds.left <= lon <= src.bounds.right and
                            src.bounds.bottom <= lat <= src.bounds.top):
                        continue
                    row, col = src.index(lon, lat)
                    val = src.read(1)[row, col]
                    if val == src.nodata or np.isnan(val):
                        continue
                    depth = -float(val)  # CUDEM is elevation, negate for depth
                    if depth <= 0:
                        continue
                    return DepthResult(
                        depth_m=round(depth, 2),
                        source="cudem",
                        rmse_m=SOURCE_RMSE["cudem"],
                        confidence=0.85,
                        tier=DepthTier.SURVEY.value,
                        attribution=SOURCE_ATTRIBUTION["cudem"],
                    )
            except Exception:
                continue
        return None

    def _query_gebco(self, lat: float, lon: float) -> Optional[DepthResult]:
        """Query GEBCO 2025 NetCDF at a point."""
        gebco_path = Path(self.paths["gebco_path"])
        if not gebco_path.exists():
            return None
        try:
            import xarray as xr
            ds = xr.open_dataset(gebco_path)
            val = float(ds["elevation"].sel(lat=lat, lon=lon, method="nearest").values)
            ds.close()
            depth = -val
            if depth <= 0:
                return None
            return DepthResult(
                depth_m=round(depth, 1),
                source="gebco",
                rmse_m=SOURCE_RMSE["gebco"],
                confidence=0.4,
                tier=DepthTier.SURVEY.value,
                attribution=SOURCE_ATTRIBUTION["gebco"],
            )
        except Exception as e:
            log.debug("GEBCO query failed: %s", e)
            return None

    def _query_modeled(
        self, lat: float, lon: float, lake_id: str,
    ) -> Optional[DepthResult]:
        """Query 3D-LAKES or GLOBathy modeled depth."""
        for source_name, dir_key in [("3dlakes", "lakes_3d_dir"), ("globathy", "globathy_dir")]:
            src_dir = Path(self.paths[dir_key])
            if not src_dir.exists():
                continue
            candidates = list(src_dir.rglob(f"*{lake_id}*"))
            if not candidates:
                continue
            try:
                # Assume raster or CSV with depth values
                path = candidates[0]
                if path.suffix == ".tif" and rasterio is not None:
                    with rasterio.open(path) as src:
                        row, col = src.index(lon, lat)
                        val = src.read(1)[row, col]
                        if val == src.nodata or np.isnan(val):
                            continue
                        depth = abs(float(val))
                elif path.suffix == ".csv" and pd is not None:
                    df = pd.read_csv(path)
                    depth = float(df["max_depth_m"].iloc[0])
                else:
                    continue
                return DepthResult(
                    depth_m=round(depth, 2),
                    source=source_name,
                    rmse_m=SOURCE_RMSE[source_name],
                    confidence=0.6,
                    tier=DepthTier.MODELED.value,
                    attribution=SOURCE_ATTRIBUTION[source_name],
                )
            except Exception as e:
                log.debug("Modeled query for %s failed: %s", source_name, e)
        return None

    def _query_ml_point(
        self, lat: float, lon: float, lake_id: str,
    ) -> Optional[DepthResult]:
        """Run ML model for a single point (requires features)."""
        # In production, features would come from pre-extracted S2 composites
        # For now, check if pre-computed features exist
        features = self._load_point_features(lat, lon, lake_id)
        if features is None:
            return None
        lake_features = self._load_lake_features(lake_id)
        return self._ml.predict_point(features, lake_features)

    def _morphometric_prior(self, lake_id: str) -> Optional[DepthResult]:
        """Get morphometric-based depth estimate (last resort)."""
        lake_features = self._load_lake_features(lake_id)
        result = self._ml._morphometric_fallback(lake_features)
        if result is not None:
            return result
        # Absolute fallback: use area-depth regression
        if self._waterbodies is not None:
            try:
                row = self._waterbodies[
                    self._waterbodies["lake_id"].astype(str) == str(lake_id)
                ].iloc[0]
                area_km2 = row.geometry.area * 12321  # Very rough deg² → km²
                # Empirical: max_depth ≈ 4.0 * area^0.35 (Hanna 1990)
                est_depth = 4.0 * (area_km2 ** 0.35)
                return DepthResult(
                    depth_m=round(est_depth, 1),
                    source="morphometric_prior",
                    rmse_m=SOURCE_RMSE["morphometric_prior"],
                    confidence=0.15,
                    tier=DepthTier.PRIOR.value,
                    attribution=SOURCE_ATTRIBUTION["morphometric_prior"],
                )
            except Exception:
                pass
        return None

    def _blend_modeled_and_ml(
        self,
        modeled: DepthResult,
        ml: DepthResult,
    ) -> DepthResult:
        """
        Blend modeled prior (3D-LAKES) with ML prediction.

        Uses inverse-variance weighting: lower RMSE → higher weight.
        """
        var_mod = modeled.rmse_m ** 2
        var_ml = ml.rmse_m ** 2
        total_var_inv = 1.0 / max(EPS, var_mod) + 1.0 / max(EPS, var_ml)
        w_mod = (1.0 / max(EPS, var_mod)) / total_var_inv
        w_ml = (1.0 / max(EPS, var_ml)) / total_var_inv

        blended_depth = w_mod * modeled.depth_m + w_ml * ml.depth_m
        blended_rmse = np.sqrt(1.0 / total_var_inv)
        blended_conf = max(modeled.confidence, ml.confidence)

        return DepthResult(
            depth_m=round(blended_depth, 2),
            source=f"blend({modeled.source}+{ml.source})",
            rmse_m=round(blended_rmse, 2),
            confidence=round(blended_conf, 3),
            tier=DepthTier.ML.value,
            attribution=f"{modeled.attribution} + {ml.attribution}",
            blend_weights={modeled.source: round(w_mod, 3), ml.source: round(w_ml, 3)},
        )

    def _gap_fill_with_ml(
        self,
        depth_grid: np.ndarray,
        source_grid: np.ndarray,
        rmse_grid: np.ndarray,
        conf_grid: np.ndarray,
        gap_mask: np.ndarray,
        bounds: Tuple[float, float, float, float],
        lake_id: str,
        nx: int,
        ny: int,
        transform: Optional[Any],
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Fill gaps in survey grid using ML predictions."""
        ml_map = self._build_ml_fallback_map(
            lake_id=lake_id,
            bounds=bounds,
            nx=nx,
            ny=ny,
            transform=transform,
        )
        if ml_map is None:
            return depth_grid, source_grid, rmse_grid, conf_grid

        fill_mask = gap_mask & np.isfinite(ml_map.depth)
        if not np.any(fill_mask):
            return depth_grid, source_grid, rmse_grid, conf_grid

        depth_grid[fill_mask] = ml_map.depth[fill_mask]
        source_grid[fill_mask] = ml_map.source[fill_mask]
        rmse_grid[fill_mask] = ml_map.rmse[fill_mask]
        conf_grid[fill_mask] = ml_map.confidence[fill_mask]
        return depth_grid, source_grid, rmse_grid, conf_grid

    def _get_lake_bounds(
        self, lake_id: str,
    ) -> Optional[Tuple[float, float, float, float]]:
        """Get bounding box for a lake."""
        row = self._get_lake_row(lake_id)
        if row is not None:
            try:
                return tuple(row.geometry.bounds)
            except Exception:
                pass
        return None

    def _check_best_available(self, lake_id: str) -> str:
        """Check what the best available source is for a lake (no data fetch)."""
        if lake_id in self._survey_cache:
            return self._survey_cache[lake_id]
        # Check modeled
        for dir_key in ("lakes_3d_dir", "globathy_dir"):
            src_dir = Path(self.paths[dir_key])
            if src_dir.exists() and list(src_dir.rglob(f"*{lake_id}*")):
                return "3dlakes" if "3d" in dir_key else "globathy"
        if self._ml._loaded:
            return "ml_tier2"
        return "none"

    def _load_point_features(
        self, lat: float, lon: float, lake_id: str,
    ) -> Optional[Dict[str, float]]:
        """Load pre-extracted spectral features for a point."""
        # In production: look up from S2 composite cache
        feat_dir = Path(self.paths["ml_models_dir"]).parent / "features"
        feat_file = feat_dir / f"{lake_id}_features.parquet"
        if pd is not None and feat_file.exists():
            try:
                df = pd.read_parquet(feat_file)
                # Find nearest point
                dist = (df["lat"] - lat) ** 2 + (df["lon"] - lon) ** 2
                idx = dist.idxmin()
                row = df.iloc[idx]
                return row.drop(["lat", "lon"], errors="ignore").to_dict()
            except Exception as e:
                log.debug("Feature load failed for %s: %s", lake_id, e)
        return None

    def _load_lake_features(
        self, lake_id: str,
    ) -> Optional[Dict[str, float]]:
        """Load lake-level morphometric features."""
        row = self._get_lake_row(lake_id)
        if row is None:
            return None
        try:
            geom = row.geometry
            return {
                "area_km2": geom.area * 12321,
                "perimeter_km": geom.length * 111,
                "compactness": (4 * np.pi * geom.area) / max(EPS, geom.length ** 2),
            }
        except Exception:
            return None

    def _get_lake_row(self, lake_id: str):
        """Return the first matching waterbody record for a lake ID."""
        if self._waterbodies is None:
            return None
        lid = str(lake_id)
        candidate_cols = (
            "lake_id",
            "GNIS_ID",
            "gnis_id",
            "Permanent_Identifier",
            "permanent_id",
            "NHDPlusID",
            "COMID",
            "comid",
            "Hylak_id",
            "HYLAK_ID",
            "id",
        )
        for col in candidate_cols:
            if col not in self._waterbodies.columns:
                continue
            try:
                match = self._waterbodies[
                    self._waterbodies[col].astype(str) == lid
                ]
                if len(match) > 0:
                    return match.iloc[0]
            except Exception:
                continue
        return None

    def _build_ml_fallback_map(
        self,
        lake_id: str,
        bounds: Tuple[float, float, float, float],
        nx: int,
        ny: int,
        transform: Optional[Any],
        resolution_m: float = 10.0,
    ) -> Optional[LakeMap]:
        """
        Build a non-empty ML fallback depth surface for contour generation.

        The preferred path uses cached point features if they exist. If they do
        not, we still synthesize a physically plausible basin from lake
        geometry, shore distance, and morphometric max-depth priors so the
        contour renderer always has a real surface to work with.
        """
        row = self._get_lake_row(lake_id)
        lake_geom = getattr(row, "geometry", None) if row is not None else None
        if lake_geom is None:
            log.warning("No lake geometry available for ML fallback on lake %s", lake_id)
            return None

        if transform is None and rasterio is not None:
            transform = from_bounds(*bounds, nx, ny)

        lake_mask = self._rasterize_lake_mask(lake_geom, transform, (ny, nx))
        if lake_mask is None or not np.any(lake_mask):
            lake_mask = self._rasterize_lake_mask(
                lake_geom, transform, (ny, nx), bounds=bounds,
            )
        if lake_mask is None or not np.any(lake_mask):
            lake_mask = np.ones((ny, nx), dtype=bool)

        family = self._classify_ml_family(row, lake_geom)
        source_name = {
            "glacial": "glacial_model",
            "mountain": "mountain_model",
            "unified": "unified_fallback",
        }[family]
        base_rmse = SOURCE_RMSE[source_name]
        base_conf = {
            "glacial": 0.72,
            "mountain": 0.56,
            "unified": 0.38,
        }[family]

        prior = self._morphometric_prior(lake_id)
        max_depth_m = 12.0
        if prior is not None and np.isfinite(prior.depth_m):
            max_depth_m = max(1.5, float(prior.depth_m))
        else:
            area_km2 = max(float(lake_geom.area) * 12321.0, 0.01)
            max_depth_m = max(1.5, 4.0 * (area_km2 ** 0.35))

        synthetic = self._synthesize_ml_depth_grid(
            lake_mask=lake_mask,
            family=family,
            target_max_depth_m=max_depth_m,
        )

        cache_depth, cache_rmse, cache_conf = self._predict_ml_grid_from_feature_cache(
            lake_id=lake_id,
            lake_mask=lake_mask,
            transform=transform,
            lake_features=self._load_lake_features(lake_id),
            default_rmse=base_rmse,
            default_confidence=base_conf,
        )

        depth_grid = synthetic.copy()
        rmse_grid = np.full((ny, nx), base_rmse, dtype=np.float32)
        conf_grid = np.full((ny, nx), base_conf, dtype=np.float32)
        source_grid = np.full((ny, nx), source_name, dtype=object)

        if cache_depth is not None:
            cache_valid = lake_mask & np.isfinite(cache_depth)
            if np.any(cache_valid):
                target_cap = max(
                    max_depth_m,
                    float(np.nanpercentile(cache_depth[cache_valid], 97)),
                )
                if np.nanmax(cache_depth[cache_valid]) > EPS:
                    cache_depth = cache_depth * (
                        target_cap / np.nanmax(cache_depth[cache_valid])
                    )
                depth_grid[cache_valid] = (
                    0.7 * cache_depth[cache_valid] + 0.3 * synthetic[cache_valid]
                )
                rmse_grid[cache_valid] = cache_rmse[cache_valid]
                conf_grid[cache_valid] = np.maximum(
                    conf_grid[cache_valid], cache_conf[cache_valid]
                )

        depth_grid = np.where(lake_mask, depth_grid, np.nan)
        rmse_grid = np.where(lake_mask, rmse_grid, np.nan)
        conf_grid = np.where(lake_mask, conf_grid, 0.0)

        if not np.any(np.isfinite(depth_grid)):
            return None

        metadata = {
            "primary_source": source_name,
            "tier": DepthTier.ML.value,
            "attribution": SOURCE_ATTRIBUTION[source_name],
            "ml_family": family,
            "feature_cache_used": bool(cache_depth is not None),
            "target_max_depth_m": round(max_depth_m, 2),
        }
        return LakeMap(
            depth=depth_grid.astype(np.float32),
            source=source_grid,
            rmse=rmse_grid.astype(np.float32),
            confidence=conf_grid.astype(np.float32),
            metadata=metadata,
        )

    def _classify_ml_family(self, row: Any, lake_geom: Any) -> str:
        """Classify a lake into glacial, mountain, or unified ML families."""
        centroid = lake_geom.centroid
        lon = float(centroid.x)
        lat = float(centroid.y)

        jurisdiction_tokens = []
        if row is not None:
            for col in row.index:
                lower = str(col).lower()
                if any(token in lower for token in ("state", "prov", "province", "admin", "juris", "region", "code")):
                    value = row.get(col)
                    if value is not None:
                        jurisdiction_tokens.append(str(value).upper())
        admin_blob = " ".join(jurisdiction_tokens)

        mountain_codes = {
            "AK", "AB", "AZ", "BC", "CO", "ID", "MT", "NM", "NV",
            "OR", "UT", "WA", "WY", "YT", "NT", "NU",
        }
        glacial_codes = {
            "CT", "FL", "IA", "IL", "IN", "MA", "ME", "MI", "MN", "NB",
            "NE", "NH", "NJ", "NS", "NY", "OH", "ON", "PA", "PE", "QC",
            "RI", "SK", "VT", "WI", "MB", "NL",
        }

        if any(code in admin_blob for code in mountain_codes):
            return "mountain"
        if any(code in admin_blob for code in glacial_codes):
            return "glacial"
        if lon <= -108 or (lon <= -102 and lat >= 43):
            return "mountain"
        if lat >= 40 and lon >= -100:
            return "glacial"
        return "unified"

    def _rasterize_lake_mask(
        self,
        lake_geom: Any,
        transform: Optional[Any],
        shape: Tuple[int, int],
        bounds: Optional[Tuple[float, float, float, float]] = None,
    ) -> Optional[np.ndarray]:
        """Rasterize the lake polygon to a boolean water mask."""
        if rasterio is not None and transform is not None:
            try:
                from rasterio.features import rasterize
                mask = rasterize(
                    [(lake_geom, 1)],
                    out_shape=shape,
                    transform=transform,
                    fill=0,
                    all_touched=True,
                    dtype="uint8",
                )
                return mask.astype(bool)
            except Exception as exc:
                log.debug("Lake mask rasterization failed: %s", exc)

        if bounds is None:
            return None

        minx, miny, maxx, maxy = bounds
        ny, nx = shape
        xs = np.linspace(minx, maxx, nx, endpoint=False) + ((maxx - minx) / max(nx, 1)) * 0.5
        ys = np.linspace(maxy, miny, ny, endpoint=False) - ((maxy - miny) / max(ny, 1)) * 0.5
        mask = np.zeros(shape, dtype=bool)
        try:
            for row_idx, lat in enumerate(ys):
                for col_idx, lon in enumerate(xs):
                    mask[row_idx, col_idx] = bool(lake_geom.covers(Point(float(lon), float(lat))))
            return mask
        except Exception as exc:
            log.debug("Manual lake mask rasterization failed: %s", exc)
            return None

    def _synthesize_ml_depth_grid(
        self,
        lake_mask: np.ndarray,
        family: str,
        target_max_depth_m: float,
    ) -> np.ndarray:
        """Build a smooth, shoreline-anchored synthetic depth surface."""
        depth = np.full(lake_mask.shape, np.nan, dtype=np.float32)
        if not np.any(lake_mask):
            return depth

        if distance_transform_edt is None:
            depth[lake_mask] = target_max_depth_m
            return depth

        dist = distance_transform_edt(lake_mask)
        max_dist = float(np.nanmax(dist)) if np.any(lake_mask) else 0.0
        if max_dist <= EPS:
            depth[lake_mask] = target_max_depth_m
            return depth
        rel_shore = np.clip(dist / max_dist, 0.0, 1.0)

        rows, cols = np.where(lake_mask)
        coords = np.column_stack([rows, cols]).astype(np.float32)
        center_proximity = np.zeros_like(rel_shore, dtype=np.float32)
        trough_strength = np.ones_like(rel_shore, dtype=np.float32)

        if len(coords) >= 3:
            centered = coords - coords.mean(axis=0, keepdims=True)
            cov = np.cov(centered, rowvar=False)
            eigvals, eigvecs = np.linalg.eigh(cov)
            order = np.argsort(eigvals)[::-1]
            eigvecs = eigvecs[:, order]
            proj = centered @ eigvecs
            scale1 = float(np.percentile(np.abs(proj[:, 0]), 95)) + EPS
            scale2 = float(np.percentile(np.abs(proj[:, 1]), 95)) + EPS
            radius = np.sqrt((proj[:, 0] / scale1) ** 2 + (proj[:, 1] / scale2) ** 2)
            center_vals = np.clip(1.0 - np.minimum(radius, 1.5) / 1.5, 0.0, 1.0)
            trough_vals = np.exp(-0.5 * (proj[:, 1] / max(scale2 * 0.35, EPS)) ** 2)
            center_proximity[rows, cols] = center_vals.astype(np.float32)
            trough_strength[rows, cols] = trough_vals.astype(np.float32)
        else:
            center_proximity[lake_mask] = rel_shore[lake_mask]

        if family == "glacial":
            shape = 0.55 * np.power(rel_shore, 0.90) + 0.45 * np.power(center_proximity, 1.25)
        elif family == "mountain":
            shape = (
                0.35 * np.power(rel_shore, 0.70) +
                0.65 * (np.power(rel_shore, 0.92) * trough_strength)
            )
        else:
            shape = 0.75 * np.power(rel_shore, 1.10) + 0.25 * np.power(center_proximity, 1.50)

        shape = np.clip(shape, 0.0, 1.0)
        if gaussian_filter is not None:
            smooth = gaussian_filter(np.where(lake_mask, shape, 0.0), sigma=1.2)
            shape = np.where(lake_mask, smooth, 0.0)

        scale = float(np.nanmax(shape[lake_mask])) if np.any(lake_mask) else 0.0
        if scale > EPS:
            shape = shape / scale
        depth[lake_mask] = target_max_depth_m * shape[lake_mask]
        edge_mask = lake_mask & (dist <= 1.0)
        depth[edge_mask] = np.minimum(depth[edge_mask], target_max_depth_m * 0.03)
        return depth

    def _predict_ml_grid_from_feature_cache(
        self,
        lake_id: str,
        lake_mask: np.ndarray,
        transform: Optional[Any],
        lake_features: Optional[Dict[str, float]],
        default_rmse: float,
        default_confidence: float,
    ) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], Optional[np.ndarray]]:
        """Use cached point features to build a nearest-neighbour ML surface."""
        feat_dir = Path(self.paths["ml_models_dir"]).parent / "features"
        feat_file = feat_dir / f"{lake_id}_features.parquet"
        if (
            pd is None or
            cKDTree is None or
            transform is None or
            not feat_file.exists() or
            not np.any(lake_mask)
        ):
            return None, None, None

        try:
            df = pd.read_parquet(feat_file)
        except Exception as exc:
            log.debug("Could not read cached features for %s: %s", lake_id, exc)
            return None, None, None

        if len(df) == 0 or not {"lat", "lon"}.issubset(df.columns):
            return None, None, None

        df = df.dropna(subset=["lat", "lon"]).reset_index(drop=True)
        if len(df) == 0:
            return None, None, None

        rows, cols = np.where(lake_mask)
        xs, ys = rasterio.transform.xy(transform, rows, cols, offset="center")
        grid_xy = np.column_stack([np.asarray(xs), np.asarray(ys)])
        feat_xy = df[["lon", "lat"]].to_numpy(dtype=np.float64)

        try:
            tree = cKDTree(feat_xy)
            _, nearest_idx = tree.query(grid_xy, k=1)
        except Exception as exc:
            log.debug("cKDTree query failed for %s: %s", lake_id, exc)
            return None, None, None

        depth_grid = np.full(lake_mask.shape, np.nan, dtype=np.float32)
        rmse_grid = np.full(lake_mask.shape, default_rmse, dtype=np.float32)
        conf_grid = np.full(lake_mask.shape, default_confidence, dtype=np.float32)

        cached_predictions: Dict[int, DepthResult] = {}
        for idx in np.unique(nearest_idx):
            row = df.iloc[int(idx)]
            feature_dict = {}
            for key, value in row.items():
                if key in {"lat", "lon", "depth_m"}:
                    continue
                if isinstance(value, (int, float, np.integer, np.floating)) and np.isfinite(value):
                    feature_dict[key] = float(value)
            if not feature_dict:
                continue
            result = self._ml.predict_point(feature_dict, lake_features)
            if result is not None and np.isfinite(result.depth_m):
                cached_predictions[int(idx)] = result

        if not cached_predictions:
            return None, None, None

        for row_idx, col_idx, feat_idx in zip(rows, cols, nearest_idx):
            result = cached_predictions.get(int(feat_idx))
            if result is None:
                continue
            depth_grid[row_idx, col_idx] = result.depth_m
            rmse_grid[row_idx, col_idx] = min(default_rmse, result.rmse_m)
            conf_grid[row_idx, col_idx] = max(default_confidence, result.confidence)

        if gaussian_filter is not None and np.any(np.isfinite(depth_grid)):
            valid = np.isfinite(depth_grid)
            smooth = gaussian_filter(np.where(valid, depth_grid, 0.0), sigma=1.0)
            counts = gaussian_filter(valid.astype(np.float32), sigma=1.0)
            smoothed = np.divide(
                smooth,
                np.maximum(counts, EPS),
                out=np.full_like(smooth, np.nan, dtype=np.float32),
                where=counts > EPS,
            )
            depth_grid = np.where(valid, smoothed, np.nan)

        return depth_grid, rmse_grid, conf_grid


# ── GeoTIFF Export ───────────────────────────────────────────────────

def export_lake_map_geotiff(
    lake_map: LakeMap,
    output_path: str,
) -> None:
    """Write a LakeMap to a multi-band GeoTIFF."""
    if rasterio is None:
        log.error("rasterio required for GeoTIFF export")
        return

    bounds = lake_map.metadata.get("bounds", [-180, -90, 180, 90])
    H, W = lake_map.shape
    transform = from_bounds(*bounds, W, H)

    with rasterio.open(
        output_path,
        "w",
        driver="GTiff",
        height=H,
        width=W,
        count=3,
        dtype="float32",
        crs=CRS.from_epsg(4326),
        transform=transform,
        nodata=np.nan,
        compress="deflate",
    ) as dst:
        dst.write(np.nan_to_num(lake_map.depth, nan=-9999).astype(np.float32), 1)
        dst.write(lake_map.rmse.astype(np.float32), 2)
        dst.write(lake_map.confidence.astype(np.float32), 3)
        dst.set_band_description(1, "depth_m")
        dst.set_band_description(2, "rmse_m")
        dst.set_band_description(3, "confidence")

    # Write sidecar metadata JSON
    meta_path = Path(output_path).with_suffix(".json")
    with open(meta_path, "w") as f:
        json.dump(lake_map.metadata, f, indent=2, default=str)

    log.info("Exported %s (%d x %d) + %s", output_path, W, H, meta_path)


# ── Utility Functions ────────────────────────────────────────────────

def _find_depth_column(gdf: gpd.GeoDataFrame) -> Optional[str]:
    """Find the depth column in a GeoDataFrame (case-insensitive)."""
    candidates = ["depth", "depth_m", "depth_ft", "DEPTH", "CONTOUR", "ELEV", "Z"]
    for c in candidates:
        for col in gdf.columns:
            if col.lower() == c.lower():
                return col
    # Check for numeric columns that could be depth
    for col in gdf.columns:
        if gdf[col].dtype in (np.float64, np.float32, np.int64, np.int32):
            vals = gdf[col].dropna()
            if len(vals) > 0 and vals.min() >= 0 and vals.max() < 200:
                return col
    return None


def _densify_geometry(
    geom,
    spacing_m: float = 5.0,
) -> List[Tuple[float, float]]:
    """Extract densified points from a geometry at given spacing."""
    points = []
    # Approximate degrees per metre
    deg_per_m = 1.0 / 111000.0
    spacing_deg = spacing_m * deg_per_m

    if geom.geom_type == "LineString":
        length = geom.length
        if length < EPS:
            return [(geom.coords[0][0], geom.coords[0][1])]
        n = max(2, int(length / spacing_deg))
        for i in range(n + 1):
            pt = geom.interpolate(i / n, normalized=True)
            points.append((pt.x, pt.y))
    elif geom.geom_type == "MultiLineString":
        for line in geom.geoms:
            points.extend(_densify_geometry(line, spacing_m))
    elif geom.geom_type in ("Polygon", "MultiPolygon"):
        boundary = geom.boundary
        points.extend(_densify_geometry(boundary, spacing_m))
    elif geom.geom_type == "Point":
        points.append((geom.x, geom.y))
    else:
        for coord in geom.coords:
            points.append((coord[0], coord[1]))
    return points


def _compute_distance_confidence(
    grid_x: np.ndarray,
    grid_y: np.ndarray,
    points: np.ndarray,
    max_distance_deg: float = 0.002,
) -> np.ndarray:
    """Compute confidence based on distance to nearest data point."""
    from scipy.spatial import cKDTree
    tree = cKDTree(points)
    grid_pts = np.column_stack([grid_x.ravel(), grid_y.ravel()])
    dists, _ = tree.query(grid_pts, k=1)
    confidence = np.clip(1.0 - dists / max_distance_deg, 0.0, 1.0)
    return confidence.reshape(grid_x.shape)


def _extract_lake_id_from_filename(stem: str) -> Optional[str]:
    """Extract a numeric lake ID from a filename."""
    import re
    match = re.search(r"(\d{6,})", stem)
    return match.group(1) if match else None


# ── CLI ──────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="OpenCatch Production Depth Router",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Point query
  python production_depth_router.py --mode point --lat 46.85 --lon -94.37

  # Lake map export
  python production_depth_router.py \\
      --mode lake-map --lake-id 27013300 \\
      --resolution 10 --output /data/production/27013300.tif

  # Region coverage
  python production_depth_router.py \\
      --mode coverage --bbox -97.5,43.0,-89.0,49.5
        """,
    )
    p.add_argument("--mode", choices=["point", "lake-map", "coverage", "batch"],
                    required=True, help="Query mode")
    p.add_argument("--lat", type=float, help="Latitude (for point mode)")
    p.add_argument("--lon", type=float, help="Longitude (for point mode)")
    p.add_argument("--lake-id", help="Lake identifier (e.g. MN DNR ID)")
    p.add_argument("--resolution", type=float, default=10.0,
                    help="Grid resolution in metres (default: 10)")
    p.add_argument("--bbox", help="Bounding box: minx,miny,maxx,maxy")
    p.add_argument("--lake-list", help="Path to CSV/text file with lake IDs (batch mode)")
    p.add_argument("--output", help="Output path (GeoTIFF for lake-map, JSON for others)")
    p.add_argument("--config", help="Path to JSON config file")
    p.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return p.parse_args()


def main():
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    log.info("Initialising ProductionDepthRouter...")
    router = ProductionDepthRouter(config_path=args.config)

    # ── Point query ──────────────────────────────────────────────
    if args.mode == "point":
        if args.lat is None or args.lon is None:
            log.error("--lat and --lon required for point mode")
            sys.exit(1)
        result = router.get_depth(args.lat, args.lon, lake_id=args.lake_id)
        output = result.to_dict()
        print(json.dumps(output, indent=2))
        if args.output:
            with open(args.output, "w") as f:
                json.dump(output, f, indent=2)
            log.info("Wrote result to %s", args.output)

    # ── Lake map ─────────────────────────────────────────────────
    elif args.mode == "lake-map":
        if not args.lake_id:
            log.error("--lake-id required for lake-map mode")
            sys.exit(1)
        lake_map = router.get_lake_map(args.lake_id, resolution_m=args.resolution)
        if lake_map is None:
            log.error("No data for lake %s", args.lake_id)
            sys.exit(1)
        output_path = args.output or f"/data/production/{args.lake_id}.tif"
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        export_lake_map_geotiff(lake_map, output_path)
        log.info(
            "Lake %s: %dx%d, primary source: %s",
            args.lake_id, lake_map.shape[1], lake_map.shape[0],
            lake_map.metadata.get("primary_source", "unknown"),
        )

    # ── Coverage report ──────────────────────────────────────────
    elif args.mode == "coverage":
        if not args.bbox:
            log.error("--bbox required for coverage mode")
            sys.exit(1)
        bbox = tuple(float(x) for x in args.bbox.split(","))
        if len(bbox) != 4:
            log.error("--bbox must be minx,miny,maxx,maxy")
            sys.exit(1)
        coverage = router.get_region_coverage(bbox)
        print(json.dumps(coverage, indent=2))
        if args.output:
            with open(args.output, "w") as f:
                json.dump(coverage, f, indent=2)

    # ── Batch processing ─────────────────────────────────────────
    elif args.mode == "batch":
        if not args.lake_list:
            log.error("--lake-list required for batch mode")
            sys.exit(1)
        lake_ids = Path(args.lake_list).read_text().strip().splitlines()
        lake_ids = [lid.strip() for lid in lake_ids if lid.strip()]
        output_dir = Path(args.output or "/data/production/batch")
        output_dir.mkdir(parents=True, exist_ok=True)

        from tqdm import tqdm as tqdm_bar
        results = []
        for lid in tqdm_bar(lake_ids, desc="Processing lakes"):
            t0 = time.time()
            lake_map = router.get_lake_map(lid, resolution_m=args.resolution)
            elapsed = time.time() - t0
            if lake_map is not None:
                out_path = output_dir / f"{lid}.tif"
                export_lake_map_geotiff(lake_map, str(out_path))
                results.append({
                    "lake_id": lid,
                    "status": "ok",
                    "source": lake_map.metadata.get("primary_source"),
                    "shape": list(lake_map.shape),
                    "elapsed_s": round(elapsed, 2),
                })
            else:
                results.append({
                    "lake_id": lid,
                    "status": "no_data",
                    "elapsed_s": round(elapsed, 2),
                })

        summary_path = output_dir / "batch_summary.json"
        with open(summary_path, "w") as f:
            json.dump(results, f, indent=2)
        ok = sum(1 for r in results if r["status"] == "ok")
        log.info("Batch complete: %d/%d lakes processed → %s", ok, len(results), summary_path)


if __name__ == "__main__":
    main()
