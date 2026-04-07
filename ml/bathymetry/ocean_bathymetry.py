#!/usr/bin/env python3
"""
OpenCatch — Ocean & Coastal Bathymetry Layer

Serve ocean/coastal depth from authoritative free data sources.
No ML needed — this is survey-grade data that already exists.

Data sources (priority order):
1. NOAA CUDEM  — ~3 m resolution US coastal (~1/9 arc-second)
2. GEBCO 2025  — ~450 m resolution global ocean (15 arc-second)
3. EMODnet     — ~115 m resolution European seas

For the OpenCatch app this provides:
- Coastal structure (reefs, channels, drop-offs) from CUDEM at 3 m
- Offshore depth from GEBCO at 450 m
- Seamless blending at the resolution transition
- Fishing-feature extraction (drop-offs, channels, humps, ledges)

Usage:
    python ocean_bathymetry.py \\
        --bbox 25.5,-80.5,26.0,-80.0 \\
        --output /data/ocean/florida_keys.tif \\
        --contours \\
        --fishing-features

Requirements:
    pip install xarray netCDF4 rasterio numpy scipy shapely requests tqdm
"""

import argparse
import hashlib
import json
import logging
import os
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default contour intervals in metres (fishing-relevant)
CONTOUR_INTERVALS_M = [1, 2, 3, 5, 10, 15, 20, 30, 50, 100, 200, 500, 1000]

# NOAA CUDEM OPeNDAP base (1/9 arc-second tiles, ~3 m)
CUDEM_THREDDS_BASE = (
    "https://www.ngdc.noaa.gov/thredds/dodsC/regional/"
)
# Direct GeoTIFF fallback
CUDEM_DIRECT_BASE = (
    "https://coast.noaa.gov/htdata/raster2/elevation/"
)

# GEBCO 2025 OPeNDAP
GEBCO_OPENDAP_URL = (
    "https://www.gebco.net/data_and_products/gridded_bathymetry_data/"
    "gebco_2024/gebco_2024_sub_ice_topo.nc"
)

# EMODnet WCS
EMODNET_WCS_URL = "https://ows.emodnet-bathymetry.eu/wcs"

# CUDEM regional tile catalogue — region name → THREDDS path suffix
# Subset of most-used coastal tiles; extend as needed.
CUDEM_REGIONS: Dict[str, dict] = {
    "gulf_of_mexico": {
        "thredds": "crm_vol7.nc",
        "bbox": (17.0, -98.0, 31.0, -80.0),
    },
    "southeast_atlantic": {
        "thredds": "crm_vol3.nc",
        "bbox": (24.0, -82.0, 37.0, -74.0),
    },
    "northeast_atlantic": {
        "thredds": "crm_vol1.nc",
        "bbox": (36.0, -78.0, 46.0, -62.0),
    },
    "southern_california": {
        "thredds": "crm_vol8.nc",
        "bbox": (30.0, -122.0, 37.0, -115.0),
    },
    "pacific_northwest": {
        "thredds": "crm_vol9.nc",
        "bbox": (37.0, -130.0, 49.0, -120.0),
    },
    "great_lakes": {
        "thredds": "crm_vol4.nc",
        "bbox": (40.0, -93.0, 50.0, -75.0),
    },
}

# Maximum tile size (degrees) for a single OPeNDAP request to avoid timeouts
MAX_OPENDAP_SPAN_DEG = 2.0

# Cache TTL in seconds (7 days — bathymetry data changes infrequently)
CACHE_TTL_S = 7 * 24 * 3600


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class OceanDepthResult:
    """Result from a single-point depth query."""
    depth_m: float
    source: str              # "cudem", "gebco", "emodnet"
    resolution_m: float
    uncertainty_m: float


@dataclass
class DepthTile:
    """Raster depth tile with metadata."""
    data: np.ndarray         # 2-D array, depth in metres (positive down)
    bbox: Tuple[float, float, float, float]  # (south, west, north, east)
    resolution_m: float
    source: str
    nodata: float = np.nan
    crs: str = "EPSG:4326"


@dataclass
class FishingFeature:
    """A fishing-relevant bathymetric feature."""
    feature_type: str        # drop_off, channel, hump, ledge
    geometry: object         # shapely geometry
    depth_range_m: Tuple[float, float]
    prominence_m: float      # how dramatic the feature is


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bbox_key(bbox: Tuple[float, float, float, float]) -> str:
    """Deterministic cache key for a bounding box."""
    raw = f"{bbox[0]:.6f}_{bbox[1]:.6f}_{bbox[2]:.6f}_{bbox[3]:.6f}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _find_cudem_region(lat: float, lon: float) -> Optional[str]:
    """Return the CUDEM region name covering (lat, lon), or None."""
    for name, info in CUDEM_REGIONS.items():
        s, w, n, e = info["bbox"]
        if s <= lat <= n and w <= lon <= e:
            return name
    return None


def _find_cudem_regions_for_bbox(
    bbox: Tuple[float, float, float, float],
) -> List[str]:
    """Return all CUDEM region names that overlap with bbox."""
    south, west, north, east = bbox
    regions = []
    for name, info in CUDEM_REGIONS.items():
        rs, rw, rn, re = info["bbox"]
        if south <= rn and north >= rs and west <= re and east >= rw:
            regions.append(name)
    return regions


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class OceanBathymetryLayer:
    """
    Serve ocean/coastal depth from authoritative free data sources.
    No ML needed — this is survey-grade data.

    For the OpenCatch app, this provides:
    - Coastal structure (reefs, channels, drop-offs) from CUDEM at 3 m
    - Offshore depth from GEBCO at 450 m
    - Seamless blending at the transition
    """

    def __init__(self, data_dir: str = "/data/ocean_bathy"):
        self.data_dir = Path(data_dir)
        self.cache_dir = _ensure_dir(self.data_dir / "cache")
        self._ds_cache: Dict[str, object] = {}  # open xarray datasets
        log.info("OceanBathymetryLayer initialised  data_dir=%s", self.data_dir)

    # ------------------------------------------------------------------
    # Public API — single point
    # ------------------------------------------------------------------

    def get_depth(self, lat: float, lon: float) -> OceanDepthResult:
        """Get ocean depth at a single point.

        Tries CUDEM first (higher resolution), then GEBCO as fallback.
        Returns OceanDepthResult with depth, source, resolution, uncertainty.
        """
        # 1. Try CUDEM (US coastal, ~3 m)
        region = _find_cudem_region(lat, lon)
        if region is not None:
            try:
                depth = self._point_from_cudem(lat, lon, region)
                if depth is not None and not np.isnan(depth):
                    return OceanDepthResult(
                        depth_m=float(depth),
                        source="cudem",
                        resolution_m=3.0,
                        uncertainty_m=0.5,
                    )
            except Exception as exc:
                log.warning("CUDEM point query failed: %s", exc)

        # 2. GEBCO global fallback (~450 m)
        try:
            depth = self._point_from_gebco(lat, lon)
            if depth is not None and not np.isnan(depth):
                return OceanDepthResult(
                    depth_m=float(depth),
                    source="gebco",
                    resolution_m=450.0,
                    uncertainty_m=10.0,
                )
        except Exception as exc:
            log.warning("GEBCO point query failed: %s", exc)

        # 3. Nothing available — land or no data
        return OceanDepthResult(
            depth_m=np.nan, source="none", resolution_m=np.nan, uncertainty_m=np.nan
        )

    # ------------------------------------------------------------------
    # Public API — raster tile
    # ------------------------------------------------------------------

    def get_tile(
        self,
        bbox: Tuple[float, float, float, float],
        resolution_m: Optional[float] = None,
    ) -> Optional[DepthTile]:
        """Get a depth raster tile for a bounding box.

        Parameters
        ----------
        bbox : (south, west, north, east) in decimal degrees.
        resolution_m : target resolution in metres. If None, auto-select
                       the best available source for the area.

        Returns
        -------
        DepthTile or None if no data available.
        """
        south, west, north, east = bbox
        log.info(
            "get_tile  bbox=(%.4f,%.4f,%.4f,%.4f)  target_res=%s",
            south, west, north, east, resolution_m,
        )

        # Check cache first
        cached = self._load_cached_tile(bbox)
        if cached is not None:
            log.info("Returning cached tile")
            return cached

        # Decide source
        tile = None
        cudem_regions = _find_cudem_regions_for_bbox(bbox)
        if cudem_regions and (resolution_m is None or resolution_m <= 100):
            tile = self.fetch_cudem_tile(bbox)

        if tile is None:
            # Check if in European waters for EMODnet
            if -35.0 <= west and east <= 45.0 and 15.0 <= south and north <= 90.0:
                tile = self.fetch_emodnet_subset(bbox)

        if tile is None:
            tile = self.fetch_gebco_subset(bbox)

        if tile is not None:
            self._save_cached_tile(bbox, tile)

        return tile

    # ------------------------------------------------------------------
    # Source fetchers
    # ------------------------------------------------------------------

    def fetch_cudem_tile(
        self, bbox: Tuple[float, float, float, float]
    ) -> Optional[DepthTile]:
        """Fetch CUDEM data via OPeNDAP.

        CUDEM tiles are organised by region.  The tile index is at:
        https://www.ncei.noaa.gov/metadata/geoportal/rest/metadata/item/
            gov.noaa.ngdc.mgg.dem:999919

        For programmatic access, use the THREDDS OPeNDAP endpoint:
        https://www.ngdc.noaa.gov/thredds/dodsC/regional/

        Or direct GeoTIFF tiles via:
        https://coast.noaa.gov/htdata/raster2/elevation/
        """
        import xarray as xr

        south, west, north, east = bbox
        regions = _find_cudem_regions_for_bbox(bbox)
        if not regions:
            log.debug("No CUDEM region covers bbox")
            return None

        # Use the first matching region (multi-region mosaic is a TODO)
        region_name = regions[0]
        region_info = CUDEM_REGIONS[region_name]
        url = CUDEM_THREDDS_BASE + region_info["thredds"]

        log.info("Fetching CUDEM from %s  region=%s", url, region_name)
        try:
            ds = self._open_opendap(url)
            # CUDEM variables vary; try common names
            depth_var = None
            for var_name in ("z", "Band1", "elevation", "topo"):
                if var_name in ds.data_vars:
                    depth_var = var_name
                    break
            if depth_var is None:
                log.warning("Could not identify depth variable in CUDEM dataset")
                return None

            # Subset by lat/lon
            lat_name = "lat" if "lat" in ds.coords else "y"
            lon_name = "lon" if "lon" in ds.coords else "x"
            subset = ds[depth_var].sel(
                **{
                    lat_name: slice(south, north),
                    lon_name: slice(west, east),
                }
            )
            data = subset.values.astype(np.float32)

            # CUDEM uses negative for water; invert to positive-down
            data = np.where(data < 0, -data, np.nan)

            return DepthTile(
                data=data,
                bbox=bbox,
                resolution_m=3.0,
                source="cudem",
            )
        except Exception as exc:
            log.error("CUDEM OPeNDAP fetch failed: %s", exc)
            return self._fetch_cudem_geotiff_fallback(bbox)

    def fetch_gebco_subset(
        self, bbox: Tuple[float, float, float, float]
    ) -> Optional[DepthTile]:
        """Fetch GEBCO 2025 data subset via OPeNDAP.

        OPeNDAP endpoint serves the global 15 arc-second grid.
        For small subsets, use OPeNDAP with lat/lon slicing.
        For large areas, download the appropriate 90x90 degree tile.
        """
        import xarray as xr

        south, west, north, east = bbox
        log.info("Fetching GEBCO subset  bbox=(%.2f,%.2f,%.2f,%.2f)", south, west, north, east)

        try:
            ds = self._open_opendap(GEBCO_OPENDAP_URL)

            # GEBCO uses 'elevation' variable, lat/lon coords
            lat_name = "lat" if "lat" in ds.coords else "y"
            lon_name = "lon" if "lon" in ds.coords else "x"
            var_name = "elevation" if "elevation" in ds.data_vars else "z"

            subset = ds[var_name].sel(
                **{
                    lat_name: slice(south, north),
                    lon_name: slice(west, east),
                }
            )
            data = subset.values.astype(np.float32)

            # GEBCO: negative = ocean depth, positive = land elevation
            # Convert to positive-down bathymetry; set land to NaN
            data = np.where(data < 0, -data, np.nan)

            return DepthTile(
                data=data,
                bbox=bbox,
                resolution_m=450.0,
                source="gebco",
            )
        except Exception as exc:
            log.error("GEBCO OPeNDAP fetch failed: %s", exc)
            return None

    def fetch_emodnet_subset(
        self, bbox: Tuple[float, float, float, float]
    ) -> Optional[DepthTile]:
        """Fetch EMODnet data for European waters via WCS.

        WCS endpoint: https://ows.emodnet-bathymetry.eu/wcs
        Coverage ID: emodnet:mean
        """
        import requests

        south, west, north, east = bbox
        log.info("Fetching EMODnet subset  bbox=(%.2f,%.2f,%.2f,%.2f)", south, west, north, east)

        params = {
            "service": "WCS",
            "version": "2.0.1",
            "request": "GetCoverage",
            "CoverageId": "emodnet:mean",
            "format": "application/x-netcdf",
            "subset": [
                f"Long({west},{east})",
                f"Lat({south},{north})",
            ],
        }

        cache_path = self.cache_dir / f"emodnet_{_bbox_key(bbox)}.nc"
        if cache_path.exists() and self._cache_valid(cache_path):
            return self._load_emodnet_nc(cache_path, bbox)

        try:
            resp = requests.get(EMODNET_WCS_URL, params=params, timeout=120)
            resp.raise_for_status()
            cache_path.write_bytes(resp.content)
            log.info("EMODnet data saved to %s  (%d bytes)", cache_path, len(resp.content))
            return self._load_emodnet_nc(cache_path, bbox)
        except Exception as exc:
            log.error("EMODnet WCS fetch failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Contour generation
    # ------------------------------------------------------------------

    def generate_contours(
        self,
        depth_grid: np.ndarray,
        bbox: Tuple[float, float, float, float],
        intervals: Optional[List[float]] = None,
    ) -> List[Tuple[float, object]]:
        """Generate depth contour lines from a raster.

        Parameters
        ----------
        depth_grid : 2-D array of depths (positive down, metres).
        bbox : (south, west, north, east) for geo-referencing.
        intervals : contour depths in metres.

        Returns
        -------
        List of (depth_m, shapely.geometry) tuples.
        """
        from shapely.geometry import LineString, MultiLineString
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        if intervals is None:
            max_depth = np.nanmax(depth_grid)
            intervals = [d for d in CONTOUR_INTERVALS_M if d <= max_depth]
            if not intervals:
                intervals = CONTOUR_INTERVALS_M[:3]

        south, west, north, east = bbox
        rows, cols = depth_grid.shape
        lon_arr = np.linspace(west, east, cols)
        lat_arr = np.linspace(south, north, rows)
        lon_grid, lat_grid = np.meshgrid(lon_arr, lat_arr)

        fig, ax = plt.subplots()
        cs = ax.contour(lon_grid, lat_grid, depth_grid, levels=intervals)
        plt.close(fig)

        contours: List[Tuple[float, object]] = []
        for i, level in enumerate(cs.levels):
            lines = []
            for seg in cs.allsegs[i]:
                if len(seg) >= 2:
                    lines.append(LineString(seg))
            if lines:
                geom = MultiLineString(lines) if len(lines) > 1 else lines[0]
                contours.append((float(level), geom))

        log.info(
            "Generated %d contour levels from %d intervals",
            len(contours), len(intervals),
        )
        return contours

    # ------------------------------------------------------------------
    # Fishing feature detection
    # ------------------------------------------------------------------

    def get_fishing_features(
        self,
        bbox: Tuple[float, float, float, float],
        depth_grid: Optional[np.ndarray] = None,
    ) -> List[FishingFeature]:
        """Extract fishing-relevant features from bathymetry.

        Detects:
        - drop_offs: locations where depth changes rapidly
        - channels: linear deep features
        - humps: isolated shallow features in deep water
        - ledges: depth contour edges with sharp gradient changes

        Parameters
        ----------
        bbox : (south, west, north, east).
        depth_grid : pre-fetched depth raster.  If None, will fetch via get_tile.

        Returns
        -------
        List of FishingFeature objects.
        """
        from scipy import ndimage
        from shapely.geometry import LineString, Point

        if depth_grid is None:
            tile = self.get_tile(bbox)
            if tile is None:
                log.warning("No depth data for fishing features")
                return []
            depth_grid = tile.data

        south, west, north, east = bbox
        rows, cols = depth_grid.shape
        if rows < 3 or cols < 3:
            log.warning("Depth grid too small for feature detection")
            return []

        # Approximate pixel size in metres
        lat_span_m = (north - south) * 111_320
        lon_span_m = (east - west) * 111_320 * np.cos(np.radians((south + north) / 2))
        dy = lat_span_m / rows
        dx = lon_span_m / cols

        features: List[FishingFeature] = []

        # Replace NaN with 0 for gradient computation
        grid = np.where(np.isnan(depth_grid), 0, depth_grid)

        # --- Gradient magnitude (slope) ---
        grad_y, grad_x = np.gradient(grid, dy, dx)
        slope = np.sqrt(grad_x**2 + grad_y**2)

        # --- Drop-offs: high slope areas ---
        slope_threshold = np.nanpercentile(slope[slope > 0], 90)
        drop_off_mask = slope > slope_threshold
        labeled, n_features = ndimage.label(drop_off_mask)
        for feat_id in range(1, min(n_features + 1, 50)):  # cap at 50
            ys, xs = np.where(labeled == feat_id)
            if len(ys) < 5:
                continue
            lats = south + (ys / rows) * (north - south)
            lons = west + (xs / cols) * (east - west)
            depths_at = depth_grid[ys, xs]
            valid = ~np.isnan(depths_at)
            if valid.sum() < 2:
                continue
            coords = list(zip(lons[valid], lats[valid]))
            if len(coords) >= 2:
                geom = LineString(coords)
                features.append(FishingFeature(
                    feature_type="drop_off",
                    geometry=geom,
                    depth_range_m=(float(np.nanmin(depths_at)), float(np.nanmax(depths_at))),
                    prominence_m=float(np.nanmax(depths_at) - np.nanmin(depths_at)),
                ))

        # --- Humps: local minima (shallow spots) surrounded by deeper water ---
        smoothed = ndimage.gaussian_filter(grid, sigma=3)
        local_min = ndimage.minimum_filter(smoothed, size=15)
        background = ndimage.uniform_filter(smoothed, size=31)
        hump_mask = (smoothed == local_min) & (smoothed > 0) & (background - smoothed > 2.0)
        hump_ys, hump_xs = np.where(hump_mask)
        for y, x in zip(hump_ys[:30], hump_xs[:30]):
            lat = south + (y / rows) * (north - south)
            lon = west + (x / cols) * (east - west)
            d = depth_grid[y, x]
            bg = background[y, x]
            if np.isnan(d) or np.isnan(bg):
                continue
            features.append(FishingFeature(
                feature_type="hump",
                geometry=Point(lon, lat),
                depth_range_m=(float(d), float(bg)),
                prominence_m=float(bg - d),
            ))

        # --- Channels: elongated deep features ---
        deep_threshold = np.nanpercentile(grid[grid > 0], 80) if np.any(grid > 0) else 0
        channel_mask = grid > deep_threshold
        # Use morphological skeleton to find linear features
        try:
            from skimage.morphology import skeletonize
            skeleton = skeletonize(channel_mask)
            ch_labeled, n_ch = ndimage.label(skeleton)
            for feat_id in range(1, min(n_ch + 1, 20)):
                ys, xs = np.where(ch_labeled == feat_id)
                if len(ys) < 10:
                    continue
                # Sort by position along principal axis
                coords_px = np.column_stack([xs, ys])
                order = np.argsort(coords_px[:, 0])
                coords_px = coords_px[order]
                lats = south + (coords_px[:, 1] / rows) * (north - south)
                lons = west + (coords_px[:, 0] / cols) * (east - west)
                # Subsample for cleaner lines
                step = max(1, len(lats) // 50)
                coords_geo = list(zip(lons[::step], lats[::step]))
                if len(coords_geo) >= 2:
                    depths_ch = depth_grid[coords_px[::step, 1], coords_px[::step, 0]]
                    features.append(FishingFeature(
                        feature_type="channel",
                        geometry=LineString(coords_geo),
                        depth_range_m=(
                            float(np.nanmin(depths_ch)),
                            float(np.nanmax(depths_ch)),
                        ),
                        prominence_m=float(
                            np.nanmax(depths_ch) - np.nanmin(depths_ch)
                        ),
                    ))
        except ImportError:
            log.debug("skimage not available; skipping channel detection")

        # --- Ledges: second derivative (curvature) highlights ---
        laplacian = ndimage.laplace(smoothed)
        ledge_threshold = np.nanpercentile(np.abs(laplacian[laplacian != 0]), 95)
        ledge_mask = np.abs(laplacian) > ledge_threshold
        lg_labeled, n_lg = ndimage.label(ledge_mask)
        for feat_id in range(1, min(n_lg + 1, 30)):
            ys, xs = np.where(lg_labeled == feat_id)
            if len(ys) < 5:
                continue
            lats = south + (ys / rows) * (north - south)
            lons = west + (xs / cols) * (east - west)
            depths_lg = depth_grid[ys, xs]
            valid = ~np.isnan(depths_lg)
            if valid.sum() < 2:
                continue
            coords = list(zip(lons[valid], lats[valid]))
            if len(coords) >= 2:
                features.append(FishingFeature(
                    feature_type="ledge",
                    geometry=LineString(coords),
                    depth_range_m=(
                        float(np.nanmin(depths_lg[valid])),
                        float(np.nanmax(depths_lg[valid])),
                    ),
                    prominence_m=float(
                        np.nanmax(depths_lg[valid]) - np.nanmin(depths_lg[valid])
                    ),
                ))

        log.info(
            "Detected %d fishing features  (drop_offs=%d  humps=%d  channels=%d  ledges=%d)",
            len(features),
            sum(1 for f in features if f.feature_type == "drop_off"),
            sum(1 for f in features if f.feature_type == "hump"),
            sum(1 for f in features if f.feature_type == "channel"),
            sum(1 for f in features if f.feature_type == "ledge"),
        )
        return features

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _open_opendap(self, url: str):
        """Open an OPeNDAP dataset, reusing cached connections."""
        import xarray as xr

        if url in self._ds_cache:
            return self._ds_cache[url]
        log.debug("Opening OPeNDAP dataset: %s", url)
        ds = xr.open_dataset(url, engine="netcdf4")
        self._ds_cache[url] = ds
        return ds

    def _point_from_cudem(self, lat: float, lon: float, region: str) -> Optional[float]:
        """Extract a single depth value from CUDEM via OPeNDAP."""
        import xarray as xr

        info = CUDEM_REGIONS[region]
        url = CUDEM_THREDDS_BASE + info["thredds"]
        ds = self._open_opendap(url)

        for var_name in ("z", "Band1", "elevation", "topo"):
            if var_name in ds.data_vars:
                break
        else:
            return None

        lat_name = "lat" if "lat" in ds.coords else "y"
        lon_name = "lon" if "lon" in ds.coords else "x"

        val = ds[var_name].sel(
            **{lat_name: lat, lon_name: lon}, method="nearest"
        ).values.item()

        # Negative = water depth in CUDEM; flip to positive-down
        return -val if val < 0 else None

    def _point_from_gebco(self, lat: float, lon: float) -> Optional[float]:
        """Extract a single depth value from GEBCO via OPeNDAP."""
        ds = self._open_opendap(GEBCO_OPENDAP_URL)
        var_name = "elevation" if "elevation" in ds.data_vars else "z"
        lat_name = "lat" if "lat" in ds.coords else "y"
        lon_name = "lon" if "lon" in ds.coords else "x"

        val = ds[var_name].sel(
            **{lat_name: lat, lon_name: lon}, method="nearest"
        ).values.item()

        return -val if val < 0 else None

    def _fetch_cudem_geotiff_fallback(
        self, bbox: Tuple[float, float, float, float]
    ) -> Optional[DepthTile]:
        """Attempt direct GeoTIFF download as fallback for CUDEM."""
        try:
            import rasterio
            from rasterio.windows import from_bounds

            south, west, north, east = bbox
            # Try cached GeoTIFF first
            for tif_path in self.cache_dir.glob("cudem_*.tif"):
                with rasterio.open(tif_path) as src:
                    if (src.bounds.left <= west and src.bounds.right >= east
                            and src.bounds.bottom <= south and src.bounds.top >= north):
                        window = from_bounds(west, south, east, north, src.transform)
                        data = src.read(1, window=window).astype(np.float32)
                        data = np.where(data < 0, -data, np.nan)
                        return DepthTile(
                            data=data, bbox=bbox, resolution_m=3.0, source="cudem"
                        )
        except ImportError:
            log.debug("rasterio not available for GeoTIFF fallback")
        except Exception as exc:
            log.warning("GeoTIFF fallback failed: %s", exc)
        return None

    def _load_emodnet_nc(
        self, nc_path: Path, bbox: Tuple[float, float, float, float]
    ) -> Optional[DepthTile]:
        """Load an EMODnet NetCDF file into a DepthTile."""
        import xarray as xr

        try:
            ds = xr.open_dataset(nc_path)
            # EMODnet variable is typically "elevation"
            var_name = None
            for vn in ("elevation", "depth", "z"):
                if vn in ds.data_vars:
                    var_name = vn
                    break
            if var_name is None:
                log.warning("No recognised variable in EMODnet file")
                return None

            data = ds[var_name].values.astype(np.float32)
            # EMODnet: negative = depth below sea level
            data = np.where(data < 0, -data, np.nan)
            ds.close()

            return DepthTile(
                data=data, bbox=bbox, resolution_m=115.0, source="emodnet"
            )
        except Exception as exc:
            log.error("Failed to load EMODnet NC: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Tile cache
    # ------------------------------------------------------------------

    def _cache_path(self, bbox: Tuple[float, float, float, float]) -> Path:
        return self.cache_dir / f"tile_{_bbox_key(bbox)}.npz"

    def _cache_valid(self, path: Path) -> bool:
        if not path.exists():
            return False
        age = time.time() - path.stat().st_mtime
        return age < CACHE_TTL_S

    def _load_cached_tile(
        self, bbox: Tuple[float, float, float, float]
    ) -> Optional[DepthTile]:
        cp = self._cache_path(bbox)
        if not self._cache_valid(cp):
            return None
        try:
            npz = np.load(cp, allow_pickle=True)
            meta = json.loads(str(npz["meta"]))
            return DepthTile(
                data=npz["data"],
                bbox=tuple(meta["bbox"]),
                resolution_m=meta["resolution_m"],
                source=meta["source"],
            )
        except Exception as exc:
            log.debug("Cache load failed: %s", exc)
            return None

    def _save_cached_tile(
        self, bbox: Tuple[float, float, float, float], tile: DepthTile
    ) -> None:
        cp = self._cache_path(bbox)
        meta = {
            "bbox": list(tile.bbox),
            "resolution_m": tile.resolution_m,
            "source": tile.source,
        }
        try:
            np.savez_compressed(cp, data=tile.data, meta=json.dumps(meta))
            log.debug("Cached tile to %s", cp)
        except Exception as exc:
            log.warning("Failed to cache tile: %s", exc)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def save_geotiff(
        self, tile: DepthTile, output_path: Path
    ) -> None:
        """Save a DepthTile as a GeoTIFF."""
        import rasterio
        from rasterio.transform import from_bounds

        south, west, north, east = tile.bbox
        rows, cols = tile.data.shape
        transform = from_bounds(west, south, east, north, cols, rows)

        with rasterio.open(
            output_path,
            "w",
            driver="GTiff",
            height=rows,
            width=cols,
            count=1,
            dtype=tile.data.dtype,
            crs="EPSG:4326",
            transform=transform,
            nodata=np.nan,
            compress="deflate",
        ) as dst:
            dst.write(tile.data, 1)

        log.info("Saved GeoTIFF: %s  (%d x %d)", output_path, cols, rows)

    def save_contours_geojson(
        self,
        contours: List[Tuple[float, object]],
        output_path: Path,
    ) -> None:
        """Save contour lines as GeoJSON."""
        from shapely.geometry import mapping

        features = []
        for depth_m, geom in contours:
            features.append({
                "type": "Feature",
                "properties": {
                    "depth_m": depth_m,
                    "depth_ft": round(depth_m * 3.28084, 1),
                },
                "geometry": mapping(geom),
            })

        geojson = {"type": "FeatureCollection", "features": features}
        output_path.write_text(json.dumps(geojson))
        log.info("Saved %d contour features to %s", len(features), output_path)

    def save_fishing_features_geojson(
        self,
        features: List[FishingFeature],
        output_path: Path,
    ) -> None:
        """Save fishing features as GeoJSON."""
        from shapely.geometry import mapping

        gj_features = []
        for feat in features:
            gj_features.append({
                "type": "Feature",
                "properties": {
                    "feature_type": feat.feature_type,
                    "depth_min_m": feat.depth_range_m[0],
                    "depth_max_m": feat.depth_range_m[1],
                    "prominence_m": feat.prominence_m,
                },
                "geometry": mapping(feat.geometry),
            })

        geojson = {"type": "FeatureCollection", "features": gj_features}
        output_path.write_text(json.dumps(geojson))
        log.info("Saved %d fishing features to %s", len(gj_features), output_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_bbox(s: str) -> Tuple[float, float, float, float]:
    """Parse 'south,west,north,east' string."""
    parts = [float(x.strip()) for x in s.split(",")]
    if len(parts) != 4:
        raise ValueError("bbox must be south,west,north,east")
    return tuple(parts)


def main():
    parser = argparse.ArgumentParser(
        description="OpenCatch — Ocean & Coastal Bathymetry Layer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--bbox", required=True, type=str,
        help="Bounding box: south,west,north,east (decimal degrees)",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Output GeoTIFF path",
    )
    parser.add_argument(
        "--contours", action="store_true",
        help="Generate depth contours and save as GeoJSON",
    )
    parser.add_argument(
        "--contour-intervals", type=str, default=None,
        help="Comma-separated contour depths in metres (default: auto)",
    )
    parser.add_argument(
        "--fishing-features", action="store_true",
        help="Detect and export fishing-relevant features",
    )
    parser.add_argument(
        "--data-dir", type=str, default="/data/ocean_bathy",
        help="Directory for caching downloaded data",
    )
    parser.add_argument(
        "--point", type=str, default=None,
        help="Single point query: lat,lon",
    )
    parser.add_argument(
        "--resolution", type=float, default=None,
        help="Target resolution in metres (default: auto-select best)",
    )

    args = parser.parse_args()
    bbox = parse_bbox(args.bbox)
    layer = OceanBathymetryLayer(data_dir=args.data_dir)

    # Single point query
    if args.point:
        lat, lon = [float(x.strip()) for x in args.point.split(",")]
        result = layer.get_depth(lat, lon)
        print(f"Depth at ({lat}, {lon}):")
        print(f"  depth_m     = {result.depth_m:.1f}")
        print(f"  source      = {result.source}")
        print(f"  resolution  = {result.resolution_m:.0f} m")
        print(f"  uncertainty = {result.uncertainty_m:.1f} m")
        return

    # Tile fetch
    log.info("Fetching depth tile for bbox %s", bbox)
    tile = layer.get_tile(bbox, resolution_m=args.resolution)
    if tile is None:
        log.error("No depth data available for the requested area")
        return

    log.info(
        "Tile: %d x %d  source=%s  resolution=%.0f m  depth range=%.1f–%.1f m",
        tile.data.shape[1], tile.data.shape[0],
        tile.source, tile.resolution_m,
        np.nanmin(tile.data), np.nanmax(tile.data),
    )

    # Save GeoTIFF
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        layer.save_geotiff(tile, output_path)

    # Contours
    if args.contours:
        intervals = None
        if args.contour_intervals:
            intervals = [float(x) for x in args.contour_intervals.split(",")]

        contours = layer.generate_contours(tile.data, bbox, intervals=intervals)

        contour_path = Path(args.output or "/tmp/ocean_contours").with_suffix(".geojson")
        layer.save_contours_geojson(contours, contour_path)
        print(f"Contours saved to {contour_path}")

    # Fishing features
    if args.fishing_features:
        features = layer.get_fishing_features(bbox, depth_grid=tile.data)

        feat_path = Path(args.output or "/tmp/ocean_features").with_suffix(
            ".features.geojson"
        )
        layer.save_fishing_features_geojson(features, feat_path)
        print(f"Fishing features saved to {feat_path}")

        # Summary
        for ft in ["drop_off", "channel", "hump", "ledge"]:
            count = sum(1 for f in features if f.feature_type == ft)
            if count > 0:
                print(f"  {ft}: {count}")


if __name__ == "__main__":
    main()
