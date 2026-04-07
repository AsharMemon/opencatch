#!/usr/bin/env python3
"""
Unified survey-grade bathymetry aggregator for OpenCatch.

Principle: use the most accurate data where it exists (survey/sonar bathymetry),
and only fall back to ML-predicted bathymetry where no survey data is available.

Pulls from multiple free/open bathymetry sources and creates a unified depth
layer with provenance tracking.
"""

import argparse
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import requests
from tqdm import tqdm

try:
    import rasterio
    from rasterio.transform import from_bounds
    from rasterio.crs import CRS
except ImportError:
    rasterio = None

try:
    import geopandas as gpd
except ImportError:
    gpd = None

try:
    import xarray as xr
except ImportError:
    xr = None

logger = logging.getLogger("sonar_bathy_aggregator")

# ---------------------------------------------------------------------------
# Source metadata
# ---------------------------------------------------------------------------

PRIORITY_ORDER = [
    "mn_dnr_survey",
    "wi_dnr_survey",
    "mi_dnr_survey",
    "cudem",
    "noaa_multibeam",
    "gebco",
    "emodnet",
    "openseamap",
    "3dlakes",
    "opencatch_ml",
]

SOURCE_META = {
    "mn_dnr_survey": {
        "name": "Minnesota DNR Lake Surveys",
        "accuracy_rmse_m": 0.5,
        "license": "Public domain (MN state government)",
        "url": "https://gisdata.mn.gov/dataset/water-lake-bathymetry",
    },
    "wi_dnr_survey": {
        "name": "Wisconsin DNR Lake Maps",
        "accuracy_rmse_m": 0.5,
        "license": "Public domain (WI state government)",
        "url": "https://dnr.wisconsin.gov/topic/Lakes/maps",
    },
    "mi_dnr_survey": {
        "name": "Michigan EGLE Lake Contour Maps",
        "accuracy_rmse_m": 0.5,
        "license": "Public domain (MI state government)",
        "url": "https://www.michigan.gov/egle/maps-data/lake-contour-maps",
    },
    "cudem": {
        "name": "NOAA CUDEM Coastal Elevation Models",
        "accuracy_rmse_m": 1.0,
        "license": "US Government, public domain",
        "url": "https://www.ncei.noaa.gov/products/coastal-elevation-models",
    },
    "noaa_multibeam": {
        "name": "NOAA Multibeam/Singlebeam Archives",
        "accuracy_rmse_m": 1.5,
        "license": "US Government, public domain",
        "url": "https://www.ngdc.noaa.gov/mgg/bathymetry/multibeam.html",
    },
    "gebco": {
        "name": "GEBCO 2025 Global Bathymetry",
        "accuracy_rmse_m": 50.0,
        "license": "Public domain, commercial use allowed",
        "url": "https://www.gebco.net/data-products/gridded-bathymetry-data/",
    },
    "emodnet": {
        "name": "EMODnet European Bathymetry",
        "accuracy_rmse_m": 10.0,
        "license": "Open access with attribution (EMODnet)",
        "url": "https://emodnet.ec.europa.eu/en/bathymetry",
    },
    "openseamap": {
        "name": "OpenSeaMap Community Soundings",
        "accuracy_rmse_m": 2.0,
        "license": "ODbL (OpenStreetMap compatible)",
        "url": "https://depth.openseamap.org/",
    },
    "3dlakes": {
        "name": "3D-LAKES Global Lake Bathymetry",
        "accuracy_rmse_m": 1.37,
        "license": "CC-BY 4.0",
        "url": "https://zenodo.org/records/14629125",
    },
    "opencatch_ml": {
        "name": "OpenCatch ML Predicted Bathymetry",
        "accuracy_rmse_m": 2.76,
        "license": "OpenCatch proprietary",
        "url": None,
    },
}

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class DepthEstimate:
    """Single depth estimate with provenance."""

    depth_m: float
    source: str
    accuracy_rmse_m: float
    confidence: float
    survey_date: Optional[str] = None
    attribution: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SourceCoverage:
    """Tracks what a source covers spatially."""

    source: str
    bbox: Tuple[float, float, float, float]  # (min_lat, min_lon, max_lat, max_lon)
    lake_ids: List[str] = field(default_factory=list)
    file_paths: List[str] = field(default_factory=list)
    fetched_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


class RateLimiter:
    """Simple per-domain rate limiter."""

    def __init__(self, requests_per_second: float = 2.0):
        self.min_interval = 1.0 / requests_per_second
        self._last_call: Dict[str, float] = {}

    def wait(self, domain: str):
        now = time.time()
        last = self._last_call.get(domain, 0.0)
        wait_time = self.min_interval - (now - last)
        if wait_time > 0:
            time.sleep(wait_time)
        self._last_call[domain] = time.time()


# ---------------------------------------------------------------------------
# Downloader helper
# ---------------------------------------------------------------------------


def _download_file(
    url: str,
    dest: Path,
    rate_limiter: Optional[RateLimiter] = None,
    chunk_size: int = 8192,
    timeout: int = 120,
    description: str = "",
) -> Path:
    """Download a file with progress bar and caching.

    Skips download if dest already exists and has nonzero size.
    """
    if dest.exists() and dest.stat().st_size > 0:
        logger.info("Cached: %s", dest.name)
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)

    if rate_limiter:
        from urllib.parse import urlparse

        domain = urlparse(url).netloc
        rate_limiter.wait(domain)

    try:
        resp = requests.get(url, stream=True, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Download failed for %s: %s", url, exc)
        raise

    total = int(resp.headers.get("content-length", 0))
    label = description or dest.name
    with open(dest, "wb") as f, tqdm(
        total=total, unit="B", unit_scale=True, desc=label, disable=total == 0
    ) as pbar:
        for chunk in resp.iter_content(chunk_size=chunk_size):
            f.write(chunk)
            pbar.update(len(chunk))

    logger.info("Downloaded: %s (%d bytes)", dest.name, dest.stat().st_size)
    return dest


# ---------------------------------------------------------------------------
# Main aggregator
# ---------------------------------------------------------------------------


class SonarBathymetryAggregator:
    """
    Aggregate survey-grade bathymetry from multiple open sources.

    Priority order (most accurate first):
    1. State DNR lake surveys (sonar contours, sub-meter accuracy)
    2. NOAA CUDEM (3m coastal, continuously updated)
    3. NOAA multibeam/singlebeam archives (ship tracks)
    4. GEBCO 2025 (450m global ocean)
    5. EMODnet (115m European seas)
    6. OpenSeaMap community soundings
    7. 3D-LAKES A-E derived bathymetry (510K lakes)
    8. OpenCatch ML predictions (fallback)

    Each source has:
    - fetch method
    - accuracy estimate (RMSE)
    - spatial coverage (polygon/bbox)
    - temporal freshness
    - license/attribution requirements
    """

    def __init__(self, cache_dir: str = "~/.opencatch/bathy_cache"):
        self.cache_dir = Path(cache_dir).expanduser()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.rate_limiter = RateLimiter(requests_per_second=2.0)
        self.coverage_index: Dict[str, SourceCoverage] = {}
        self._index_path = self.cache_dir / "coverage_index.json"
        self._load_index()

    # -- Persistence ---------------------------------------------------------

    def _load_index(self):
        if self._index_path.exists():
            try:
                data = json.loads(self._index_path.read_text())
                for key, val in data.items():
                    self.coverage_index[key] = SourceCoverage(**val)
            except Exception:
                logger.warning("Could not load coverage index; starting fresh.")

    def _save_index(self):
        data = {k: asdict(v) for k, v in self.coverage_index.items()}
        self._index_path.write_text(json.dumps(data, indent=2))

    # -----------------------------------------------------------------------
    # 1. State DNR lake surveys
    # -----------------------------------------------------------------------

    def fetch_mn_dnr_surveys(self, output_dir: str) -> SourceCoverage:
        """Fetch MN DNR lake survey contours.

        Source: https://gisdata.mn.gov/dataset/water-lake-bathymetry
        Format: Shapefiles with depth contours + point soundings
        Coverage: ~4,500 MN lakes
        Accuracy: Sub-meter (professional sonar surveys)
        License: Public domain (MN state government)
        """
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        source = "mn_dnr_survey"

        # MN Geospatial Commons WFS endpoint
        base_url = (
            "https://resources.gisdata.mn.gov/pub/gdrs/data/pub/us_mn_state_dnr/"
            "water_lake_bathymetry/shp_water_lake_bathymetry.zip"
        )
        zip_path = out / "mn_dnr_bathy.zip"

        logger.info("Fetching MN DNR lake bathymetry shapefile...")
        _download_file(
            base_url,
            zip_path,
            rate_limiter=self.rate_limiter,
            description="MN DNR Bathymetry",
        )

        # Extract if not already done
        extract_dir = out / "mn_dnr_bathy"
        if not extract_dir.exists():
            import zipfile

            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)
            logger.info("Extracted MN DNR bathymetry to %s", extract_dir)

        cov = SourceCoverage(
            source=source,
            bbox=(43.5, -97.2, 49.4, -89.5),
            file_paths=[str(extract_dir)],
            fetched_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        self.coverage_index[source] = cov
        self._save_index()
        return cov

    def fetch_wi_dnr_surveys(self, output_dir: str) -> SourceCoverage:
        """Fetch WI DNR lake maps.

        Source: https://dnr.wisconsin.gov/topic/Lakes/maps
        Format: PDF contour maps (need digitization) + some GIS layers
        Coverage: ~15,000 WI lakes (subset have digital data)
        Accuracy: Sub-meter where digital
        License: Public domain (WI state government)
        """
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        source = "wi_dnr_survey"

        # WI DNR provides lake data via the WDNR Open Data portal (ArcGIS REST)
        wfs_url = (
            "https://dnrmaps.wi.gov/arcgis/rest/services/DW_Map_Dynamic/"
            "EN_Lake_Bathymetry_WTM_Ext/MapServer/0/query"
        )
        params = {
            "where": "1=1",
            "outFields": "*",
            "f": "geojson",
            "resultRecordCount": 5000,
        }

        geojson_path = out / "wi_dnr_bathy.geojson"
        if not geojson_path.exists():
            logger.info("Fetching WI DNR bathymetry features via ArcGIS REST...")
            self.rate_limiter.wait("dnrmaps.wi.gov")
            try:
                resp = requests.get(wfs_url, params=params, timeout=120)
                resp.raise_for_status()
                geojson_path.write_text(resp.text)
                logger.info("Saved WI DNR bathymetry: %s", geojson_path)
            except requests.RequestException as exc:
                logger.error("WI DNR fetch failed: %s", exc)
                raise
        else:
            logger.info("Cached: %s", geojson_path.name)

        cov = SourceCoverage(
            source=source,
            bbox=(42.5, -92.9, 47.1, -86.8),
            file_paths=[str(geojson_path)],
            fetched_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        self.coverage_index[source] = cov
        self._save_index()
        return cov

    def fetch_mi_dnr_surveys(self, output_dir: str) -> SourceCoverage:
        """Fetch MI DNR (EGLE) lake contour maps.

        Source: https://www.michigan.gov/egle/maps-data/lake-contour-maps
        Format: PDF contour maps + some GIS layers via MI Open Data
        Coverage: ~3,000+ MI inland lakes
        Accuracy: Sub-meter where digital
        License: Public domain
        """
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        source = "mi_dnr_survey"

        # MI GIS Open Data ArcGIS REST endpoint
        rest_url = (
            "https://services1.arcgis.com/JjVcwlpLAoEYBdnb/arcgis/rest/services/"
            "Lake_Bathymetry/FeatureServer/0/query"
        )
        params = {
            "where": "1=1",
            "outFields": "*",
            "f": "geojson",
            "resultRecordCount": 5000,
        }

        geojson_path = out / "mi_egle_bathy.geojson"
        if not geojson_path.exists():
            logger.info("Fetching MI EGLE lake bathymetry features...")
            self.rate_limiter.wait("services1.arcgis.com")
            try:
                resp = requests.get(rest_url, params=params, timeout=120)
                resp.raise_for_status()
                geojson_path.write_text(resp.text)
                logger.info("Saved MI EGLE bathymetry: %s", geojson_path)
            except requests.RequestException as exc:
                logger.error("MI EGLE fetch failed: %s", exc)
                raise
        else:
            logger.info("Cached: %s", geojson_path.name)

        cov = SourceCoverage(
            source=source,
            bbox=(41.7, -90.4, 48.3, -82.1),
            file_paths=[str(geojson_path)],
            fetched_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        self.coverage_index[source] = cov
        self._save_index()
        return cov

    # -----------------------------------------------------------------------
    # 2. NOAA CUDEM (US Coastal)
    # -----------------------------------------------------------------------

    def fetch_cudem_tiles(self, bbox: Tuple[float, float, float, float], output_dir: str) -> SourceCoverage:
        """Fetch NOAA CUDEM tiles for coastal bathymetry.

        Source: https://www.ncei.noaa.gov/products/coastal-elevation-models
        Tiles via: https://coast.noaa.gov/htdata/raster2/elevation/
        Format: GeoTIFF, 1/9 arc-second (~3m) nearshore
        Coverage: US coastline
        Accuracy: Best available, integrates multibeam + LiDAR
        License: US Government, public domain
        """
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        source = "cudem"
        min_lat, min_lon, max_lat, max_lon = bbox

        # CUDEM is available via THREDDS/OPeNDAP. Use the NCEI THREDDS catalog.
        thredds_base = (
            "https://www.ngdc.noaa.gov/thredds/dodsC/"
            "crm/cudem_v2_ninth_arc_second.nc"
        )

        nc_path = out / f"cudem_{min_lat}_{min_lon}_{max_lat}_{max_lon}.nc"
        if not nc_path.exists():
            if xr is None:
                logger.error("xarray required for CUDEM OPeNDAP access. pip install xarray netcdf4")
                raise ImportError("xarray required for CUDEM fetch")

            logger.info("Opening CUDEM via OPeNDAP for bbox %s...", bbox)
            try:
                ds = xr.open_dataset(thredds_base)
                subset = ds.sel(lat=slice(min_lat, max_lat), lon=slice(min_lon, max_lon))
                subset.to_netcdf(nc_path)
                ds.close()
                logger.info("Saved CUDEM subset: %s", nc_path)
            except Exception as exc:
                logger.error("CUDEM OPeNDAP fetch failed: %s", exc)
                raise
        else:
            logger.info("Cached: %s", nc_path.name)

        cov = SourceCoverage(
            source=source,
            bbox=bbox,
            file_paths=[str(nc_path)],
            fetched_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        self.coverage_index[source] = cov
        self._save_index()
        return cov

    # -----------------------------------------------------------------------
    # 3. GEBCO 2025 (Global Ocean)
    # -----------------------------------------------------------------------

    def fetch_gebco_subset(self, bbox: Tuple[float, float, float, float], output_dir: str) -> SourceCoverage:
        """Fetch GEBCO 2025 bathymetry subset.

        Source: https://www.gebco.net/data-products/gridded-bathymetry-data/
        Also via OPeNDAP: https://www.gebco.net/data-products/gridded-bathymetry-data/gebco_2024/
        Format: NetCDF
        Resolution: 15 arc-seconds (~450m)
        Coverage: Global ocean
        License: Public domain, commercial use allowed
        """
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        source = "gebco"
        min_lat, min_lon, max_lat, max_lon = bbox

        # GEBCO OPeNDAP endpoint
        opendap_url = (
            "https://www.gebco.net/data_and_products/gridded_bathymetry_data/"
            "gebco_2024/gebco_2024_sub_ice_n90.0_s-90.0_w-180.0_e180.0.nc"
        )

        nc_path = out / f"gebco_{min_lat}_{min_lon}_{max_lat}_{max_lon}.nc"
        if not nc_path.exists():
            if xr is None:
                logger.error("xarray required for GEBCO access. pip install xarray netcdf4")
                raise ImportError("xarray required for GEBCO fetch")

            logger.info("Opening GEBCO via OPeNDAP for bbox %s...", bbox)
            try:
                ds = xr.open_dataset(opendap_url)
                subset = ds.sel(lat=slice(min_lat, max_lat), lon=slice(min_lon, max_lon))
                subset.to_netcdf(nc_path)
                ds.close()
                logger.info("Saved GEBCO subset: %s", nc_path)
            except Exception as exc:
                logger.error("GEBCO fetch failed: %s", exc)
                raise
        else:
            logger.info("Cached: %s", nc_path.name)

        cov = SourceCoverage(
            source=source,
            bbox=bbox,
            file_paths=[str(nc_path)],
            fetched_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        self.coverage_index[source] = cov
        self._save_index()
        return cov

    # -----------------------------------------------------------------------
    # 4. EMODnet (European Seas)
    # -----------------------------------------------------------------------

    def fetch_emodnet_tiles(self, bbox: Tuple[float, float, float, float], output_dir: str) -> SourceCoverage:
        """Fetch EMODnet bathymetry for European waters.

        Source: https://emodnet.ec.europa.eu/en/bathymetry
        WCS endpoint for programmatic access
        Format: GeoTIFF
        Resolution: ~115m
        Coverage: European seas
        License: Open access with attribution
        """
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        source = "emodnet"
        min_lat, min_lon, max_lat, max_lon = bbox

        wcs_url = "https://ows.emodnet-bathymetry.eu/wcs"
        params = {
            "service": "WCS",
            "version": "2.0.1",
            "request": "GetCoverage",
            "CoverageId": "emodnet:mean",
            "format": "image/tiff",
            "subset": [
                f"Lat({min_lat},{max_lat})",
                f"Long({min_lon},{max_lon})",
            ],
        }

        tif_path = out / f"emodnet_{min_lat}_{min_lon}_{max_lat}_{max_lon}.tif"
        if not tif_path.exists():
            logger.info("Fetching EMODnet bathymetry for bbox %s...", bbox)
            self.rate_limiter.wait("ows.emodnet-bathymetry.eu")
            try:
                resp = requests.get(wcs_url, params=params, timeout=180)
                resp.raise_for_status()
                tif_path.write_bytes(resp.content)
                logger.info("Saved EMODnet tile: %s (%d bytes)", tif_path, len(resp.content))
            except requests.RequestException as exc:
                logger.error("EMODnet fetch failed: %s", exc)
                raise
        else:
            logger.info("Cached: %s", tif_path.name)

        cov = SourceCoverage(
            source=source,
            bbox=bbox,
            file_paths=[str(tif_path)],
            fetched_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        self.coverage_index[source] = cov
        self._save_index()
        return cov

    # -----------------------------------------------------------------------
    # 5. OpenSeaMap Community Soundings
    # -----------------------------------------------------------------------

    def fetch_openseamap_depths(self, bbox: Tuple[float, float, float, float], output_dir: str) -> SourceCoverage:
        """Fetch OpenSeaMap community depth data.

        Source: https://depth.openseamap.org/
        API: REST API for depth points
        Format: JSON/CSV point data
        Coverage: Global but sparse (community contributed)
        License: ODbL (like OpenStreetMap)
        """
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        source = "openseamap"
        min_lat, min_lon, max_lat, max_lon = bbox

        api_url = "https://depth.openseamap.org/depth/api/tracks"
        params = {
            "minLat": min_lat,
            "minLon": min_lon,
            "maxLat": max_lat,
            "maxLon": max_lon,
            "format": "json",
        }

        json_path = out / f"openseamap_{min_lat}_{min_lon}_{max_lat}_{max_lon}.json"
        if not json_path.exists():
            logger.info("Fetching OpenSeaMap depths for bbox %s...", bbox)
            self.rate_limiter.wait("depth.openseamap.org")
            try:
                resp = requests.get(api_url, params=params, timeout=120)
                resp.raise_for_status()
                json_path.write_text(resp.text)
                logger.info("Saved OpenSeaMap depths: %s", json_path)
            except requests.RequestException as exc:
                logger.error("OpenSeaMap fetch failed: %s", exc)
                raise
        else:
            logger.info("Cached: %s", json_path.name)

        cov = SourceCoverage(
            source=source,
            bbox=bbox,
            file_paths=[str(json_path)],
            fetched_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        self.coverage_index[source] = cov
        self._save_index()
        return cov

    # -----------------------------------------------------------------------
    # 6. 3D-LAKES Global
    # -----------------------------------------------------------------------

    def fetch_3dlakes(self, lake_ids: List[str], output_dir: str) -> SourceCoverage:
        """Fetch 3D-LAKES bathymetry for specific lakes.

        Source: https://zenodo.org/records/14629125
        Format: NetCDF per lake
        Coverage: 510,530 lakes globally
        Accuracy: 1.37m RMSE (cross-validated)
        License: CC-BY 4.0
        """
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        source = "3dlakes"

        # Zenodo API for 3D-LAKES dataset
        zenodo_record = "14629125"
        api_url = f"https://zenodo.org/api/records/{zenodo_record}"

        # Get file listing from the record
        manifest_path = self.cache_dir / "3dlakes_manifest.json"
        if not manifest_path.exists():
            logger.info("Fetching 3D-LAKES Zenodo manifest...")
            self.rate_limiter.wait("zenodo.org")
            try:
                resp = requests.get(api_url, timeout=60)
                resp.raise_for_status()
                manifest_path.write_text(resp.text)
            except requests.RequestException as exc:
                logger.error("3D-LAKES manifest fetch failed: %s", exc)
                raise

        manifest = json.loads(manifest_path.read_text())
        files = {f["key"]: f["links"]["self"] for f in manifest.get("files", [])}

        downloaded = []
        for lake_id in tqdm(lake_ids, desc="3D-LAKES"):
            # File naming convention: 3dlakes expects lake IDs mapped to archive files
            # The dataset is partitioned by region; search for matching file
            nc_path = out / f"3dlakes_{lake_id}.nc"
            if nc_path.exists():
                downloaded.append(str(nc_path))
                continue

            # Look for the lake in the archive files
            matching_file = None
            for fname, url in files.items():
                if lake_id in fname:
                    matching_file = (fname, url)
                    break

            if matching_file:
                fname, url = matching_file
                _download_file(
                    url,
                    nc_path,
                    rate_limiter=self.rate_limiter,
                    description=f"3D-LAKES {lake_id}",
                )
                downloaded.append(str(nc_path))
            else:
                logger.warning("Lake %s not found in 3D-LAKES dataset", lake_id)

        cov = SourceCoverage(
            source=source,
            bbox=(0, 0, 0, 0),  # Global; individual lakes
            lake_ids=lake_ids,
            file_paths=downloaded,
            fetched_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        self.coverage_index[source] = cov
        self._save_index()
        return cov

    # -----------------------------------------------------------------------
    # Data routing layer
    # -----------------------------------------------------------------------

    def _sources_for_point(self, lat: float, lon: float) -> List[str]:
        """Return sources that cover a given point, in priority order."""
        available = []
        for src in PRIORITY_ORDER:
            cov = self.coverage_index.get(src)
            if cov is None:
                continue
            min_lat, min_lon, max_lat, max_lon = cov.bbox
            # bbox=(0,0,0,0) means global/lake-specific; always include
            if (min_lat == 0 and max_lat == 0) or (
                min_lat <= lat <= max_lat and min_lon <= lon <= max_lon
            ):
                available.append(src)
        return available

    def get_best_depth(
        self, lat: float, lon: float, lake_id: Optional[str] = None
    ) -> DepthEstimate:
        """Return the best available depth estimate with provenance.

        Iterates sources in priority order. The first source with actual data
        at this coordinate wins.

        Returns:
            DepthEstimate with depth, source, accuracy, confidence, and attribution.
        """
        sources = self._sources_for_point(lat, lon)

        for src in sources:
            meta = SOURCE_META[src]
            depth = self._query_source(src, lat, lon, lake_id)
            if depth is not None and not np.isnan(depth):
                # Confidence is inversely proportional to RMSE, scaled 0-1
                rmse = meta["accuracy_rmse_m"]
                confidence = max(0.0, min(1.0, 1.0 - (rmse / 50.0)))
                return DepthEstimate(
                    depth_m=float(depth),
                    source=src,
                    accuracy_rmse_m=rmse,
                    confidence=confidence,
                    survey_date=self.coverage_index.get(src, SourceCoverage(src, (0, 0, 0, 0))).fetched_at,
                    attribution=meta["license"],
                )

        # Fallback: no data at all
        return DepthEstimate(
            depth_m=np.nan,
            source="none",
            accuracy_rmse_m=np.nan,
            confidence=0.0,
            attribution="No bathymetry data available",
        )

    def _query_source(
        self, source: str, lat: float, lon: float, lake_id: Optional[str] = None
    ) -> Optional[float]:
        """Query a single source for depth at a coordinate.

        Loads the cached data file and performs spatial lookup. Returns depth
        in meters (positive down) or None if no data at that location.
        """
        cov = self.coverage_index.get(source)
        if cov is None or not cov.file_paths:
            return None

        file_path = Path(cov.file_paths[0])
        if not file_path.exists():
            return None

        suffix = file_path.suffix.lower()

        try:
            if suffix == ".nc":
                return self._query_netcdf(file_path, lat, lon)
            elif suffix == ".tif" or suffix == ".tiff":
                return self._query_geotiff(file_path, lat, lon)
            elif suffix == ".geojson":
                return self._query_geojson(file_path, lat, lon)
            elif suffix == ".json":
                return self._query_json_points(file_path, lat, lon)
            elif file_path.is_dir():
                # Shapefile directory (e.g., MN DNR)
                return self._query_shapefile_dir(file_path, lat, lon)
        except Exception as exc:
            logger.debug("Query failed for %s at (%s,%s): %s", source, lat, lon, exc)

        return None

    def _query_netcdf(self, path: Path, lat: float, lon: float) -> Optional[float]:
        """Extract depth from a NetCDF file at the nearest grid cell."""
        if xr is None:
            return None
        ds = xr.open_dataset(path)
        try:
            val = ds.sel(lat=lat, lon=lon, method="nearest")
            # Try common variable names
            for var in ["elevation", "depth", "z", "Band1"]:
                if var in val:
                    depth = float(val[var].values)
                    # GEBCO/CUDEM use negative for below sea level
                    return abs(depth) if depth < 0 else depth
        finally:
            ds.close()
        return None

    def _query_geotiff(self, path: Path, lat: float, lon: float) -> Optional[float]:
        """Extract depth from a GeoTIFF at pixel nearest to coordinate."""
        if rasterio is None:
            return None
        with rasterio.open(path) as src:
            row, col = src.index(lon, lat)
            if 0 <= row < src.height and 0 <= col < src.width:
                val = src.read(1)[row, col]
                if val != src.nodata:
                    return abs(float(val))
        return None

    def _query_geojson(self, path: Path, lat: float, lon: float) -> Optional[float]:
        """Find nearest feature in GeoJSON and extract depth attribute."""
        if gpd is None:
            return None
        gdf = gpd.read_file(path)
        if gdf.empty:
            return None
        from shapely.geometry import Point

        pt = Point(lon, lat)
        gdf["_dist"] = gdf.geometry.distance(pt)
        nearest = gdf.loc[gdf["_dist"].idxmin()]

        # Try common depth column names
        for col in ["DEPTH", "depth", "MAX_DEPTH", "max_depth", "CONTOUR", "contour"]:
            if col in nearest.index and nearest[col] is not None:
                try:
                    return abs(float(nearest[col]))
                except (ValueError, TypeError):
                    continue
        return None

    def _query_json_points(self, path: Path, lat: float, lon: float) -> Optional[float]:
        """Find nearest depth point from a JSON point file (e.g., OpenSeaMap)."""
        data = json.loads(path.read_text())
        if not isinstance(data, list):
            data = data.get("features", data.get("points", []))
        if not data:
            return None

        best_dist = float("inf")
        best_depth = None
        for pt in data:
            plat = pt.get("lat", pt.get("latitude", 0))
            plon = pt.get("lon", pt.get("longitude", 0))
            pdepth = pt.get("depth", pt.get("z", None))
            if pdepth is None:
                continue
            dist = (plat - lat) ** 2 + (plon - lon) ** 2
            if dist < best_dist:
                best_dist = dist
                best_depth = abs(float(pdepth))

        # Only return if within ~500m (~0.005 degrees)
        if best_dist < 0.005 ** 2:
            return best_depth
        return None

    def _query_shapefile_dir(self, dir_path: Path, lat: float, lon: float) -> Optional[float]:
        """Query shapefiles in a directory for depth contours."""
        if gpd is None:
            return None
        shp_files = list(dir_path.rglob("*.shp"))
        if not shp_files:
            return None

        from shapely.geometry import Point

        pt = Point(lon, lat)
        for shp in shp_files:
            try:
                gdf = gpd.read_file(shp)
                if gdf.empty:
                    continue
                gdf["_dist"] = gdf.geometry.distance(pt)
                nearest = gdf.loc[gdf["_dist"].idxmin()]
                if nearest["_dist"] > 0.01:
                    continue
                for col in ["DEPTH", "depth", "CONTOUR", "contour", "Z", "z"]:
                    if col in nearest.index and nearest[col] is not None:
                        try:
                            return abs(float(nearest[col]))
                        except (ValueError, TypeError):
                            continue
            except Exception:
                continue
        return None

    # -----------------------------------------------------------------------
    # Unified raster builder
    # -----------------------------------------------------------------------

    def build_unified_layer(
        self,
        region_bbox: Tuple[float, float, float, float],
        resolution_m: float = 10.0,
        output_dir: str = ".",
        include_sources: Optional[List[str]] = None,
    ) -> Dict[str, str]:
        """Build a unified depth raster for a region.

        For each pixel, use the most accurate available source.
        Output includes:
        - depth raster (GeoTIFF)
        - source raster (categorical: which source provided this pixel)
        - accuracy raster (estimated RMSE per pixel)
        - attribution text file
        """
        if rasterio is None:
            raise ImportError("rasterio required for raster output. pip install rasterio")

        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        min_lat, min_lon, max_lat, max_lon = region_bbox

        # Convert resolution from meters to approximate degrees
        res_deg = resolution_m / 111_000.0

        n_rows = int(np.ceil((max_lat - min_lat) / res_deg))
        n_cols = int(np.ceil((max_lon - min_lon) / res_deg))
        logger.info(
            "Building unified layer: %d x %d pixels (%.1fm resolution)",
            n_cols, n_rows, resolution_m,
        )

        depth_grid = np.full((n_rows, n_cols), np.nan, dtype=np.float32)
        source_grid = np.zeros((n_rows, n_cols), dtype=np.uint8)  # 0=no data
        accuracy_grid = np.full((n_rows, n_cols), np.nan, dtype=np.float32)
        attributions_used = set()

        # Source name -> integer code mapping
        source_codes = {src: i + 1 for i, src in enumerate(PRIORITY_ORDER)}

        active_sources = include_sources or PRIORITY_ORDER

        total_pixels = n_rows * n_cols
        with tqdm(total=total_pixels, desc="Building unified layer", unit="px") as pbar:
            for row in range(n_rows):
                lat = max_lat - (row + 0.5) * res_deg
                for col in range(n_cols):
                    lon = min_lon + (col + 0.5) * res_deg

                    for src in active_sources:
                        if src not in PRIORITY_ORDER:
                            continue
                        depth = self._query_source(src, lat, lon)
                        if depth is not None and not np.isnan(depth):
                            depth_grid[row, col] = depth
                            source_grid[row, col] = source_codes.get(src, 0)
                            accuracy_grid[row, col] = SOURCE_META[src]["accuracy_rmse_m"]
                            attributions_used.add(src)
                            break
                    pbar.update(1)

        # Write outputs
        transform = from_bounds(min_lon, min_lat, max_lon, max_lat, n_cols, n_rows)
        crs = CRS.from_epsg(4326)
        profile = {
            "driver": "GTiff",
            "height": n_rows,
            "width": n_cols,
            "count": 1,
            "dtype": "float32",
            "crs": crs,
            "transform": transform,
            "nodata": np.nan,
            "compress": "deflate",
        }

        depth_path = out / "unified_depth_m.tif"
        with rasterio.open(depth_path, "w", **profile) as dst:
            dst.write(depth_grid, 1)
            dst.set_band_description(1, "depth_m (positive down)")

        profile_uint8 = {**profile, "dtype": "uint8", "nodata": 0}
        source_path = out / "unified_source.tif"
        with rasterio.open(source_path, "w", **profile_uint8) as dst:
            dst.write(source_grid, 1)
            dst.set_band_description(1, "source_code")

        accuracy_path = out / "unified_accuracy_rmse_m.tif"
        with rasterio.open(accuracy_path, "w", **profile) as dst:
            dst.write(accuracy_grid, 1)
            dst.set_band_description(1, "estimated_rmse_m")

        # Attribution file
        attr_path = out / "attribution.txt"
        lines = ["OpenCatch Unified Bathymetry Layer", "=" * 40, ""]
        lines.append("Source code mapping:")
        for src, code in source_codes.items():
            marker = " *" if src in attributions_used else ""
            lines.append(f"  {code}: {SOURCE_META[src]['name']}{marker}")
        lines.append("")
        lines.append("License/attribution requirements:")
        for src in sorted(attributions_used):
            meta = SOURCE_META[src]
            lines.append(f"  - {meta['name']}: {meta['license']}")
            if meta.get("url"):
                lines.append(f"    {meta['url']}")
        attr_path.write_text("\n".join(lines))

        coverage_pct = np.count_nonzero(~np.isnan(depth_grid)) / total_pixels * 100
        logger.info(
            "Unified layer complete: %.1f%% coverage from %d sources",
            coverage_pct, len(attributions_used),
        )

        return {
            "depth": str(depth_path),
            "source": str(source_path),
            "accuracy": str(accuracy_path),
            "attribution": str(attr_path),
            "coverage_pct": coverage_pct,
            "sources_used": sorted(attributions_used),
        }

    # -----------------------------------------------------------------------
    # Convenience: fetch all for a region
    # -----------------------------------------------------------------------

    def fetch_all(
        self,
        bbox: Tuple[float, float, float, float],
        output_dir: str,
        include_sources: Optional[List[str]] = None,
    ) -> Dict[str, SourceCoverage]:
        """Fetch all relevant sources for a bounding box.

        Args:
            bbox: (min_lat, min_lon, max_lat, max_lon)
            output_dir: Base directory for downloads
            include_sources: Subset of sources to fetch (default: all applicable)

        Returns:
            Dict mapping source name to SourceCoverage
        """
        out = Path(output_dir)
        results = {}
        sources = include_sources or [
            "mnDNR", "wiDNR", "miDNR", "cudem", "gebco", "emodnet", "openseamap",
        ]

        source_map = {
            "mnDNR": ("mn_dnr_survey", lambda: self.fetch_mn_dnr_surveys(str(out / "mn_dnr"))),
            "wiDNR": ("wi_dnr_survey", lambda: self.fetch_wi_dnr_surveys(str(out / "wi_dnr"))),
            "miDNR": ("mi_dnr_survey", lambda: self.fetch_mi_dnr_surveys(str(out / "mi_dnr"))),
            "cudem": ("cudem", lambda: self.fetch_cudem_tiles(bbox, str(out / "cudem"))),
            "gebco": ("gebco", lambda: self.fetch_gebco_subset(bbox, str(out / "gebco"))),
            "emodnet": ("emodnet", lambda: self.fetch_emodnet_tiles(bbox, str(out / "emodnet"))),
            "openseamap": ("openseamap", lambda: self.fetch_openseamap_depths(bbox, str(out / "openseamap"))),
        }

        for src_key in sources:
            if src_key not in source_map:
                logger.warning("Unknown source: %s", src_key)
                continue
            internal_name, fetch_fn = source_map[src_key]
            try:
                logger.info("Fetching %s...", internal_name)
                cov = fetch_fn()
                results[internal_name] = cov
            except Exception as exc:
                logger.error("Failed to fetch %s: %s", internal_name, exc)

        return results

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------

    def summary(self) -> str:
        """Return a human-readable summary of available data."""
        lines = ["OpenCatch Bathymetry Sources", "=" * 35]
        for src in PRIORITY_ORDER:
            meta = SOURCE_META[src]
            cov = self.coverage_index.get(src)
            status = "fetched" if cov else "not fetched"
            n_files = len(cov.file_paths) if cov else 0
            lines.append(
                f"  {meta['name']:<45s} RMSE={meta['accuracy_rmse_m']:<6.1f}m  "
                f"{status} ({n_files} files)"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Region presets
# ---------------------------------------------------------------------------

REGION_PRESETS = {
    "us-midwest": (43.0, -97.0, 49.0, -89.0),
    "us-great-lakes": (41.0, -93.0, 49.0, -76.0),
    "us-northeast": (40.0, -80.0, 47.0, -67.0),
    "us-southeast": (25.0, -90.0, 37.0, -75.0),
    "us-west": (32.0, -125.0, 49.0, -104.0),
    "europe-north": (54.0, -10.0, 72.0, 30.0),
    "europe-med": (30.0, -6.0, 46.0, 36.0),
    "global": (-90.0, -180.0, 90.0, 180.0),
}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="OpenCatch Unified Bathymetry Aggregator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Fetch MN DNR surveys
  python sonar_bathymetry_aggregator.py --region us-midwest --output /data/bathy/ --include-sources mnDNR

  # Build unified layer for US Midwest
  python sonar_bathymetry_aggregator.py --region us-midwest --output /data/unified_bathy/ \\
      --include-sources mnDNR,cudem,gebco,3dlakes --resolution 10

  # Custom bounding box
  python sonar_bathymetry_aggregator.py --bbox 43,-97,49,-89 --output /data/bathy/ --resolution 30

  # Show available data summary
  python sonar_bathymetry_aggregator.py --summary
        """,
    )
    parser.add_argument(
        "--region", choices=list(REGION_PRESETS.keys()),
        help="Named region preset",
    )
    parser.add_argument(
        "--bbox", type=str,
        help="Bounding box: min_lat,min_lon,max_lat,max_lon",
    )
    parser.add_argument(
        "--output", type=str, default="./bathy_output",
        help="Output directory (default: ./bathy_output)",
    )
    parser.add_argument(
        "--include-sources", type=str,
        help="Comma-separated list of sources: mnDNR,wiDNR,miDNR,cudem,gebco,emodnet,openseamap,3dlakes",
    )
    parser.add_argument(
        "--resolution", type=float, default=10.0,
        help="Output resolution in meters (default: 10)",
    )
    parser.add_argument(
        "--build-layer", action="store_true",
        help="Build unified raster layer after fetching",
    )
    parser.add_argument(
        "--summary", action="store_true",
        help="Show summary of available cached data",
    )
    parser.add_argument(
        "--cache-dir", type=str, default="~/.opencatch/bathy_cache",
        help="Cache directory (default: ~/.opencatch/bathy_cache)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    agg = SonarBathymetryAggregator(cache_dir=args.cache_dir)

    if args.summary:
        print(agg.summary())
        return

    # Determine bounding box
    if args.bbox:
        parts = [float(x.strip()) for x in args.bbox.split(",")]
        if len(parts) != 4:
            parser.error("--bbox must be min_lat,min_lon,max_lat,max_lon")
        bbox = tuple(parts)
    elif args.region:
        bbox = REGION_PRESETS[args.region]
    else:
        parser.error("Provide --region or --bbox")
        return

    sources = None
    if args.include_sources:
        sources = [s.strip() for s in args.include_sources.split(",")]

    # Fetch data
    logger.info("Fetching bathymetry for bbox=%s", bbox)
    results = agg.fetch_all(bbox, args.output, include_sources=sources)

    for src, cov in results.items():
        logger.info("  %s: %d files", src, len(cov.file_paths))

    # Build unified layer if requested
    if args.build_layer:
        logger.info("Building unified raster at %.1fm resolution...", args.resolution)
        layer = agg.build_unified_layer(
            region_bbox=bbox,
            resolution_m=args.resolution,
            output_dir=args.output,
            include_sources=[s for s in PRIORITY_ORDER if s in agg.coverage_index],
        )
        logger.info("Unified layer outputs:")
        for key, val in layer.items():
            logger.info("  %s: %s", key, val)

    print(agg.summary())


if __name__ == "__main__":
    main()
