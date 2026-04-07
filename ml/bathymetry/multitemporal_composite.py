#!/usr/bin/env python3
"""
OpenCatch — Multi-Temporal Sentinel-2 Scene Compositing for SDB

Composites multiple Sentinel-2 acquisitions to improve satellite-derived
bathymetry accuracy. Literature shows 30-42% RMSE reduction from compositing
~16 scenes vs single-scene SDB (MIWC method, ISPRS 2024).

Physics: water surface conditions (waves, glint, turbidity) vary between
acquisitions, but the lake bottom doesn't move. By compositing multiple scenes,
surface noise averages out and the bottom signal strengthens.

Compositing methods:
  1. Median — simple, robust to outliers
  2. MIWC (Multi-Image Weighted Composite) — inverse distance weighting
     with power=4, as found optimal by ISPRS 2024 study
  3. Best-scene selection — average of top N scenes by quality score
  4. Trimmed mean — remove top/bottom 10%, mean the rest

Usage:
    python multitemporal_composite.py \\
        --lake-id 10001300 \\
        --lake-polygon /data/waterbodies/10001300.geojson \\
        --date-range 2023-06-01,2023-09-30 \\
        --n-scenes 16 \\
        --method miwc \\
        --output /data/composites/10001300 \\
        --stac-url https://planetarycomputer.microsoft.com/api/stac/v1

Requirements:
    pip install pystac-client planetary-computer rasterio numpy pandas
    pip install geopandas shapely pyproj tqdm
"""

import argparse
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("multitemporal_composite")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Sentinel-2 L2A band mapping (Planetary Computer asset names)
S2_BANDS = {
    "B02": "blue",       # 490nm — water penetrating
    "B03": "green",      # 560nm — water penetrating
    "B04": "red",        # 665nm — shallow reference
    "B08": "nir",        # 842nm — water mask / glint detection
    "B11": "swir16",     # 1610nm — water mask
}

# SDB spectral features computed per-pixel
SDB_FEATURES = [
    "B02", "B03", "B04", "B08",
    "stumpf_ratio",       # ln(B02) / ln(B03)
    "lyzenga_B02_B03",    # ln(B02) - ln(B03)
    "lyzenga_B02_B04",    # ln(B02) - ln(B04)
    "lyzenga_B03_B04",    # ln(B03) - ln(B04)
    "ndwi",               # (B03 - B08) / (B03 + B08)
    "ratio_B02_B04",      # B02 / B04
    "ratio_B03_B04",      # B03 / B04
    "ln_B02", "ln_B03", "ln_B04",
]

# Default STAC endpoint
DEFAULT_STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"

# Scene quality weights
QUALITY_WEIGHTS = {
    "cloud_cover": 0.30,
    "sun_zenith": 0.20,
    "turbidity": 0.25,
    "glint_risk": 0.10,
    "season": 0.15,
}

# MIWC optimal power parameter from ISPRS 2024
MIWC_POWER = 4

# Minimum reflectance to avoid log(0)
REFLECTANCE_FLOOR = 1e-4

# MN lake optimal season (late summer — post-stratification, pre-turnover)
OPTIMAL_DOY_START = 196   # ~July 15
OPTIMAL_DOY_END = 258     # ~Sep 15


# ---------------------------------------------------------------------------
# Scene quality scoring
# ---------------------------------------------------------------------------


def score_cloud_cover(cloud_pct: float) -> float:
    """Score cloud cover: 0% → 1.0, 30% → 0.0, linear."""
    return max(0.0, 1.0 - cloud_pct / 30.0)


def score_sun_zenith(zenith_deg: float) -> float:
    """
    Score sun zenith angle.

    Prefer 20-50°: enough light for bottom signal but minimal surface glint.
    Below 20° = high glint risk. Above 50° = weak signal.
    """
    if 20.0 <= zenith_deg <= 50.0:
        return 1.0
    elif zenith_deg < 20.0:
        return zenith_deg / 20.0
    else:
        # Linear decay from 50 to 70
        return max(0.0, 1.0 - (zenith_deg - 50.0) / 20.0)


def score_turbidity_proxy(ndti: float) -> float:
    """
    Score water clarity using NDTI (Normalized Difference Turbidity Index).

    NDTI = (Red - Green) / (Red + Green)
    Clear water: NDTI < -0.1 → score 1.0
    Turbid:      NDTI > 0.1  → score 0.0
    """
    return max(0.0, min(1.0, (0.1 - ndti) / 0.2))


def score_glint_risk(sun_zenith: float, sun_azimuth: float,
                     view_zenith: float, view_azimuth: float) -> float:
    """
    Score glint risk from sun-sensor geometry.

    Specular reflection occurs when the sun reflection angle aligns with the
    sensor view angle. Higher score = lower risk.
    """
    # Relative azimuth between sun and sensor
    rel_azimuth = abs(sun_azimuth - view_azimuth)
    if rel_azimuth > 180.0:
        rel_azimuth = 360.0 - rel_azimuth

    # Specular condition: sun_zenith ≈ view_zenith and rel_azimuth ≈ 180°
    zenith_diff = abs(sun_zenith - view_zenith)
    azimuth_from_specular = abs(rel_azimuth - 180.0)

    # If both are near specular, high glint risk (low score)
    if zenith_diff < 5.0 and azimuth_from_specular < 20.0:
        return 0.1
    elif zenith_diff < 10.0 and azimuth_from_specular < 40.0:
        return 0.4

    return min(1.0, (zenith_diff + azimuth_from_specular) / 60.0)


def score_season(acquisition_date: datetime) -> float:
    """
    Score season preference for MN lakes.

    Late summer (Jul 15 — Sep 15) is optimal: post-stratification, clear water,
    pre-fall turnover.
    """
    doy = acquisition_date.timetuple().tm_yday
    if OPTIMAL_DOY_START <= doy <= OPTIMAL_DOY_END:
        return 1.0
    # Linear ramp: 30 days outside window → 0.5
    distance = min(
        abs(doy - OPTIMAL_DOY_START),
        abs(doy - OPTIMAL_DOY_END),
    )
    return max(0.3, 1.0 - distance / 60.0)


# ---------------------------------------------------------------------------
# Multi-Temporal Compositor
# ---------------------------------------------------------------------------


class MultiTemporalCompositor:
    """
    Composite multiple Sentinel-2 scenes for improved satellite-derived
    bathymetry.

    Methods:
    1. Median composite (simple, robust to outliers)
    2. MIWC (Multi-Image Weighted Composite) — inverse distance weighting
       with power=4, as found optimal by ISPRS 2024 study
    3. Best-scene selection (pick top N scenes by quality score)
    4. Temporal trimmed mean (remove top/bottom 10%, average rest)
    """

    def __init__(
        self,
        stac_url: str = DEFAULT_STAC_URL,
        n_scenes: int = 16,
        method: str = "miwc",
        max_cloud: float = 30.0,
        miwc_power: int = MIWC_POWER,
        trimmed_frac: float = 0.1,
    ):
        self.stac_url = stac_url
        self.n_scenes = n_scenes
        self.method = method
        self.max_cloud = max_cloud
        self.miwc_power = miwc_power
        self.trimmed_frac = trimmed_frac

        log.info(
            "Compositor initialized: method=%s, n_scenes=%d, stac=%s",
            method, n_scenes, stac_url,
        )

    # ------------------------------------------------------------------
    # STAC scene query
    # ------------------------------------------------------------------

    def query_scenes(
        self,
        lake_bbox: Tuple[float, float, float, float],
        date_range: Tuple[str, str],
        max_cloud: Optional[float] = None,
    ) -> List[Dict]:
        """
        Query Planetary Computer STAC for Sentinel-2 L2A scenes.

        Parameters
        ----------
        lake_bbox : (west, south, east, north) in EPSG:4326
        date_range : (start_date, end_date) as ISO strings
        max_cloud : maximum cloud cover %, defaults to self.max_cloud

        Returns
        -------
        List of scene metadata dicts with id, datetime, properties, assets.
        """
        import pystac_client
        try:
            import planetary_computer as pc
        except ImportError:
            pc = None

        if max_cloud is None:
            max_cloud = self.max_cloud

        date_str = f"{date_range[0]}/{date_range[1]}"
        log.info(
            "Querying STAC: bbox=%s, dates=%s, max_cloud=%.0f%%",
            lake_bbox, date_str, max_cloud,
        )

        catalog = pystac_client.Client.open(
            self.stac_url,
            modifier=pc.sign_inplace if pc else None,
        )

        search = catalog.search(
            collections=["sentinel-2-l2a"],
            bbox=lake_bbox,
            datetime=date_str,
            query={"eo:cloud_cover": {"lt": max_cloud}},
            max_items=200,
        )

        items = list(search.items())
        log.info("Found %d scenes under %.0f%% cloud cover", len(items), max_cloud)

        scenes = []
        for item in items:
            props = item.properties
            scenes.append({
                "id": item.id,
                "datetime": item.datetime,
                "cloud_cover": props.get("eo:cloud_cover", 100.0),
                "sun_zenith": props.get("s2:mean_solar_zenith", 45.0),
                "sun_azimuth": props.get("s2:mean_solar_azimuth", 180.0),
                "view_zenith": props.get("view:off_nadir", 5.0),
                "view_azimuth": props.get("view:azimuth", 0.0),
                "item": item,
            })

        return scenes

    # ------------------------------------------------------------------
    # Scene scoring
    # ------------------------------------------------------------------

    def score_scene(self, scene: Dict, ndti: Optional[float] = None) -> float:
        """
        Score a scene for SDB suitability (0-1).

        Combines cloud cover, sun geometry, turbidity proxy, glint risk,
        and season preference using literature-derived weights.
        """
        scores = {
            "cloud_cover": score_cloud_cover(scene["cloud_cover"]),
            "sun_zenith": score_sun_zenith(scene["sun_zenith"]),
            "turbidity": score_turbidity_proxy(ndti if ndti is not None else -0.05),
            "glint_risk": score_glint_risk(
                scene["sun_zenith"], scene["sun_azimuth"],
                scene["view_zenith"], scene["view_azimuth"],
            ),
            "season": score_season(scene["datetime"]),
        }

        total = sum(
            QUALITY_WEIGHTS[k] * scores[k] for k in QUALITY_WEIGHTS
        )

        log.debug(
            "Scene %s quality=%.3f (cloud=%.2f zen=%.2f turb=%.2f "
            "glint=%.2f season=%.2f)",
            scene["id"], total,
            scores["cloud_cover"], scores["sun_zenith"],
            scores["turbidity"], scores["glint_risk"], scores["season"],
        )

        return total

    # ------------------------------------------------------------------
    # Feature extraction from a single scene
    # ------------------------------------------------------------------

    def extract_scene_features(
        self,
        scene: Dict,
        lake_polygon,
    ) -> Optional[Dict]:
        """
        Extract spectral features from one Sentinel-2 scene over a lake.

        Reads B02, B03, B04, B08 bands, computes SDB features, applies
        water mask (NDWI > 0). Returns per-pixel feature arrays with
        coordinates, or None if extraction fails.

        Parameters
        ----------
        scene : dict with 'item' key containing a pystac Item
        lake_polygon : shapely Polygon or GeoJSON of the lake boundary

        Returns
        -------
        dict with keys: 'features' (n_pixels x n_features ndarray),
                        'coords' (n_pixels x 2 ndarray of lon/lat),
                        'quality_score' (float),
                        'ndti' (float, median NDTI over water)
        """
        import rasterio
        from rasterio.mask import mask as rio_mask
        from shapely.geometry import mapping, shape

        item = scene["item"]

        try:
            import planetary_computer as pc
            signed_item = pc.sign(item)
        except ImportError:
            signed_item = item

        # Convert polygon to shapely if needed
        if isinstance(lake_polygon, dict):
            geom = shape(lake_polygon)
        else:
            geom = lake_polygon

        geom_geojson = [mapping(geom)]

        try:
            # Read bands
            bands = {}
            for band_key, asset_name in S2_BANDS.items():
                if asset_name not in signed_item.assets:
                    log.warning(
                        "Scene %s missing asset '%s', skipping",
                        scene["id"], asset_name,
                    )
                    return None

                href = signed_item.assets[asset_name].href
                with rasterio.open(href) as src:
                    out_image, out_transform = rio_mask(
                        src, geom_geojson, crop=True, all_touched=True,
                    )
                    bands[band_key] = out_image[0].astype(np.float32)
                    if band_key == "B02":
                        transform = out_transform
                        crs = src.crs
                        height, width = bands[band_key].shape

        except Exception as e:
            log.warning("Failed to read scene %s: %s", scene["id"], e)
            return None

        # Convert DN to reflectance (S2 L2A scale = 10000)
        for k in bands:
            bands[k] = np.clip(bands[k] / 10000.0, REFLECTANCE_FLOOR, 1.0)

        # Water mask via NDWI
        ndwi = (bands["B03"] - bands["B08"]) / (bands["B03"] + bands["B08"] + 1e-8)
        water_mask = ndwi > 0.0

        n_water = int(water_mask.sum())
        if n_water < 10:
            log.warning(
                "Scene %s: only %d water pixels, skipping", scene["id"], n_water,
            )
            return None

        # Compute NDTI for turbidity scoring
        ndti_arr = (bands["B04"] - bands["B03"]) / (
            bands["B04"] + bands["B03"] + 1e-8
        )
        median_ndti = float(np.median(ndti_arr[water_mask]))

        # Compute SDB features over water pixels
        b02 = bands["B02"][water_mask]
        b03 = bands["B03"][water_mask]
        b04 = bands["B04"][water_mask]
        b08 = bands["B08"][water_mask]

        ln_b02 = np.log(b02)
        ln_b03 = np.log(b03)
        ln_b04 = np.log(b04)

        features = np.column_stack([
            b02,
            b03,
            b04,
            b08,
            ln_b02 / (ln_b03 + 1e-8),    # stumpf_ratio
            ln_b02 - ln_b03,              # lyzenga_B02_B03
            ln_b02 - ln_b04,              # lyzenga_B02_B04
            ln_b03 - ln_b04,              # lyzenga_B03_B04
            ndwi[water_mask],             # ndwi
            b02 / (b04 + 1e-8),           # ratio_B02_B04
            b03 / (b04 + 1e-8),           # ratio_B03_B04
            ln_b02,
            ln_b03,
            ln_b04,
        ])

        # Build coordinate arrays (pixel centers → lon/lat)
        rows, cols = np.where(water_mask)
        from rasterio.transform import xy as rasterio_xy
        from pyproj import Transformer

        xs, ys = rasterio_xy(transform, rows, cols)
        xs = np.array(xs)
        ys = np.array(ys)

        if crs and not crs.is_geographic:
            transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
            lons, lats = transformer.transform(xs, ys)
        else:
            lons, lats = xs, ys

        coords = np.column_stack([lons, lats])

        quality = self.score_scene(scene, ndti=median_ndti)

        log.info(
            "Scene %s: %d water pixels, NDTI=%.3f, quality=%.3f",
            scene["id"], n_water, median_ndti, quality,
        )

        return {
            "features": features,
            "coords": coords,
            "quality_score": quality,
            "ndti": median_ndti,
            "scene_id": scene["id"],
            "datetime": scene["datetime"],
        }

    # ------------------------------------------------------------------
    # Compositing methods
    # ------------------------------------------------------------------

    def composite(
        self,
        scene_features_list: List[Dict],
        method: Optional[str] = None,
    ) -> Optional[Dict]:
        """
        Composite multiple scenes into one feature set.

        All scenes must share the same pixel grid (coords). Scenes with
        missing pixels are handled via masking.

        Parameters
        ----------
        scene_features_list : list of dicts from extract_scene_features
        method : compositing method, defaults to self.method

        Returns
        -------
        dict with 'features' (n_pixels x n_features), 'coords',
        'n_scenes_used', 'method'.
        """
        if method is None:
            method = self.method

        if not scene_features_list:
            log.error("No scenes to composite")
            return None

        if len(scene_features_list) == 1:
            log.warning("Only 1 scene available, returning as-is")
            result = scene_features_list[0].copy()
            result["n_scenes_used"] = 1
            result["method"] = "single"
            return result

        # Use the coordinate grid from the first scene as reference
        ref_coords = scene_features_list[0]["coords"]
        n_pixels = ref_coords.shape[0]
        n_features = scene_features_list[0]["features"].shape[1]
        n_scenes = len(scene_features_list)

        log.info(
            "Compositing %d scenes (%d pixels, %d features) via %s",
            n_scenes, n_pixels, n_features, method,
        )

        # Stack all scenes: (n_scenes, n_pixels, n_features)
        # Scenes with different pixel counts are aligned by coordinate matching
        stack = np.full((n_scenes, n_pixels, n_features), np.nan, dtype=np.float32)
        quality_scores = np.zeros(n_scenes, dtype=np.float32)

        for i, sf in enumerate(scene_features_list):
            if sf["features"].shape[0] == n_pixels:
                stack[i] = sf["features"]
            else:
                # Coordinate matching for misaligned grids
                log.debug(
                    "Scene %d has %d pixels vs reference %d, aligning",
                    i, sf["features"].shape[0], n_pixels,
                )
                from scipy.spatial import cKDTree
                tree = cKDTree(sf["coords"])
                dists, idxs = tree.query(ref_coords, k=1)
                match_mask = dists < 15.0  # within ~15m (half a S2 pixel)
                valid_idxs = idxs[match_mask]
                stack[i, match_mask] = sf["features"][valid_idxs]

            quality_scores[i] = sf["quality_score"]

        # Count valid observations per pixel
        valid_count = np.sum(~np.isnan(stack[:, :, 0]), axis=0)
        min_obs = max(2, n_scenes // 4)
        pixel_mask = valid_count >= min_obs

        log.info(
            "Pixel coverage: min=%d, max=%d, mean=%.1f (threshold=%d)",
            valid_count.min(), valid_count.max(), valid_count.mean(), min_obs,
        )

        if method == "median":
            composited = np.nanmedian(stack, axis=0)

        elif method == "miwc":
            composited = self._miwc_composite(stack, self.miwc_power)

        elif method == "trimmed_mean":
            composited = self._trimmed_mean_composite(
                stack, frac=self.trimmed_frac,
            )

        elif method == "best_n":
            # Use top half of scenes by quality
            best_n = max(3, n_scenes // 2)
            top_indices = np.argsort(quality_scores)[-best_n:]
            composited = np.nanmean(stack[top_indices], axis=0)
            log.info(
                "Best-N: using top %d scenes (quality range %.3f–%.3f)",
                best_n,
                quality_scores[top_indices[0]],
                quality_scores[top_indices[-1]],
            )

        else:
            log.error("Unknown compositing method: %s", method)
            return None

        # Mask out pixels with insufficient observations
        composited[~pixel_mask] = np.nan

        n_valid = int(pixel_mask.sum())
        log.info(
            "Composite complete: %d/%d valid pixels, method=%s",
            n_valid, n_pixels, method,
        )

        return {
            "features": composited,
            "coords": ref_coords,
            "feature_names": SDB_FEATURES,
            "n_scenes_used": n_scenes,
            "n_valid_pixels": n_valid,
            "method": method,
        }

    @staticmethod
    def _miwc_composite(
        stack: np.ndarray, power: int = 4,
    ) -> np.ndarray:
        """
        Multi-Image Weighted Composite (ISPRS 2024).

        For each pixel and feature, compute the median across scenes, then
        weight each scene's value by 1 / |value - median|^power.
        Observations closer to the median get exponentially more weight,
        suppressing outliers from glint, clouds, turbidity spikes.
        """
        median = np.nanmedian(stack, axis=0)  # (n_pixels, n_features)

        # Distance from median per scene
        dist = np.abs(stack - median[np.newaxis, :, :])  # (n_scenes, n_pix, n_feat)
        dist = np.clip(dist, 1e-6, None)

        weights = 1.0 / (dist ** power)
        # Zero weight for NaN observations
        weights[np.isnan(stack)] = 0.0

        weight_sum = weights.sum(axis=0)
        weight_sum = np.clip(weight_sum, 1e-10, None)

        composited = np.nansum(stack * weights, axis=0) / weight_sum

        return composited

    @staticmethod
    def _trimmed_mean_composite(
        stack: np.ndarray, frac: float = 0.1,
    ) -> np.ndarray:
        """
        Trimmed mean: remove top and bottom `frac` of values per pixel,
        then average the rest.
        """
        from scipy.stats import trim_mean as scipy_trim_mean

        n_scenes, n_pixels, n_features = stack.shape
        composited = np.full((n_pixels, n_features), np.nan, dtype=np.float32)

        for j in range(n_features):
            for i in range(n_pixels):
                vals = stack[:, i, j]
                valid = vals[~np.isnan(vals)]
                if len(valid) >= 3:
                    composited[i, j] = scipy_trim_mean(valid, frac)
                elif len(valid) > 0:
                    composited[i, j] = np.mean(valid)

        return composited

    # ------------------------------------------------------------------
    # Full lake pipeline
    # ------------------------------------------------------------------

    def run_lake(
        self,
        lake_id: str,
        lake_polygon,
        date_range: Tuple[str, str],
        lake_bbox: Optional[Tuple[float, float, float, float]] = None,
        output_dir: Optional[Path] = None,
    ) -> Optional[Dict]:
        """
        Full multi-temporal compositing pipeline for one lake.

        Steps:
        1. Query STAC for Sentinel-2 scenes
        2. Score and rank scenes by SDB quality
        3. Select top N scenes
        4. Extract spectral features from each
        5. Composite per-pixel across scenes
        6. Save composited features

        Parameters
        ----------
        lake_id : unique lake identifier
        lake_polygon : shapely Polygon or GeoJSON geometry
        date_range : (start_date, end_date) ISO strings
        lake_bbox : optional bounding box, computed from polygon if None
        output_dir : directory to save results, optional

        Returns
        -------
        Composite result dict, or None on failure.
        """
        from shapely.geometry import shape

        t0 = time.time()
        log.info("=== Lake %s: starting multi-temporal composite ===", lake_id)

        # Resolve polygon
        if isinstance(lake_polygon, dict):
            geom = shape(lake_polygon)
        elif isinstance(lake_polygon, str) or isinstance(lake_polygon, Path):
            import geopandas as gpd
            gdf = gpd.read_file(lake_polygon)
            geom = gdf.geometry.iloc[0]
        else:
            geom = lake_polygon

        # Compute bbox if not provided
        if lake_bbox is None:
            lake_bbox = geom.bounds  # (minx, miny, maxx, maxy)

        # 1. Query scenes
        try:
            scenes = self.query_scenes(lake_bbox, date_range)
        except Exception as e:
            log.error("STAC query failed for lake %s: %s", lake_id, e)
            return None

        if not scenes:
            log.warning("No scenes found for lake %s", lake_id)
            return None

        # 2. Score scenes (initial scoring without NDTI)
        for s in scenes:
            s["quality_score"] = self.score_scene(s)

        scenes.sort(key=lambda s: s["quality_score"], reverse=True)

        # 3. Select top N
        selected = scenes[: self.n_scenes]
        log.info(
            "Selected %d/%d scenes (quality range: %.3f–%.3f)",
            len(selected), len(scenes),
            selected[-1]["quality_score"] if selected else 0,
            selected[0]["quality_score"] if selected else 0,
        )

        # 4. Extract features from each scene
        scene_features = []
        for i, scene in enumerate(selected):
            log.info(
                "Extracting scene %d/%d: %s (quality=%.3f)",
                i + 1, len(selected), scene["id"], scene["quality_score"],
            )
            try:
                result = self.extract_scene_features(scene, geom)
            except Exception as e:
                log.warning(
                    "Feature extraction failed for %s: %s", scene["id"], e,
                )
                continue

            if result is not None:
                scene_features.append(result)

        log.info(
            "Successfully extracted %d/%d scenes",
            len(scene_features), len(selected),
        )

        if not scene_features:
            log.error("No valid scene extractions for lake %s", lake_id)
            return None

        # 5. Composite
        result = self.composite(scene_features, method=self.method)

        if result is None:
            log.error("Compositing failed for lake %s", lake_id)
            return None

        result["lake_id"] = lake_id
        result["date_range"] = date_range
        result["scenes_queried"] = len(scenes)
        result["scenes_extracted"] = len(scene_features)

        elapsed = time.time() - t0
        log.info(
            "=== Lake %s complete: %d valid pixels from %d scenes "
            "in %.1fs (method=%s) ===",
            lake_id, result["n_valid_pixels"], result["n_scenes_used"],
            elapsed, result["method"],
        )

        # 6. Save if output dir specified
        if output_dir is not None:
            self._save_results(result, output_dir, lake_id)

        return result

    @staticmethod
    def _save_results(result: Dict, output_dir: Path, lake_id: str):
        """Save composited features to disk as .npz and metadata .json."""
        import pandas as pd

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Save features as compressed npz
        npz_path = output_dir / f"{lake_id}_composite.npz"
        np.savez_compressed(
            npz_path,
            features=result["features"],
            coords=result["coords"],
        )
        log.info("Saved features: %s", npz_path)

        # Save metadata
        meta = {
            "lake_id": result["lake_id"],
            "method": result["method"],
            "n_scenes_used": result["n_scenes_used"],
            "n_valid_pixels": result["n_valid_pixels"],
            "date_range": list(result["date_range"]),
            "feature_names": result["feature_names"],
            "scenes_queried": result.get("scenes_queried"),
            "scenes_extracted": result.get("scenes_extracted"),
        }
        meta_path = output_dir / f"{lake_id}_composite_meta.json"
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2, default=str)
        log.info("Saved metadata: %s", meta_path)

        # Also save as parquet for easy downstream use
        try:
            features_df = pd.DataFrame(
                result["features"], columns=result["feature_names"],
            )
            features_df["lon"] = result["coords"][:, 0]
            features_df["lat"] = result["coords"][:, 1]

            # Drop rows with NaN features
            features_df = features_df.dropna(subset=result["feature_names"])

            pq_path = output_dir / f"{lake_id}_composite.parquet"
            features_df.to_parquet(pq_path, index=False)
            log.info(
                "Saved parquet: %s (%d rows)", pq_path, len(features_df),
            )
        except Exception as e:
            log.warning("Parquet save failed: %s", e)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Multi-temporal Sentinel-2 compositing for SDB",
    )
    parser.add_argument(
        "--lake-id", required=True,
        help="Unique lake identifier",
    )
    parser.add_argument(
        "--lake-polygon", required=True,
        help="Path to GeoJSON file with lake boundary polygon",
    )
    parser.add_argument(
        "--date-range", required=True,
        help="Date range as START,END (e.g. 2023-06-01,2023-09-30)",
    )
    parser.add_argument(
        "--n-scenes", type=int, default=16,
        help="Number of scenes to composite (default: 16)",
    )
    parser.add_argument(
        "--method", default="miwc",
        choices=["median", "miwc", "trimmed_mean", "best_n"],
        help="Compositing method (default: miwc)",
    )
    parser.add_argument(
        "--output", required=True,
        help="Output directory for composited features",
    )
    parser.add_argument(
        "--stac-url", default=DEFAULT_STAC_URL,
        help="STAC API endpoint URL",
    )
    parser.add_argument(
        "--max-cloud", type=float, default=30.0,
        help="Maximum cloud cover %% for scene filtering (default: 30)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Parse date range
    start, end = args.date_range.split(",")
    date_range = (start.strip(), end.strip())

    compositor = MultiTemporalCompositor(
        stac_url=args.stac_url,
        n_scenes=args.n_scenes,
        method=args.method,
        max_cloud=args.max_cloud,
    )

    result = compositor.run_lake(
        lake_id=args.lake_id,
        lake_polygon=args.lake_polygon,
        date_range=date_range,
        output_dir=Path(args.output),
    )

    if result is None:
        log.error("Pipeline failed for lake %s", args.lake_id)
        raise SystemExit(1)

    log.info(
        "Done. Composited %d pixels from %d scenes → %s",
        result["n_valid_pixels"],
        result["n_scenes_used"],
        args.output,
    )


if __name__ == "__main__":
    main()
