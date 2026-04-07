#!/usr/bin/env python3
"""
OpenCatch -- River/Stream Depth Estimation via Hydraulic Geometry + Discharge

Unlike lake bathymetry (optical SDB from Sentinel-2), river depth requires a
fundamentally different approach:
  - Flow-dependent: depth changes hourly with precipitation/snowmelt
  - Not optically detectable: too turbid, too narrow, too dynamic for satellites
  - Well-predicted by hydraulic geometry power-law relationships

The core physics (Leopold & Maddock 1953):
    depth = c * Q^f     where f ~ 0.4
    width = a * Q^b     where b ~ 0.5
    velocity = k * Q^m  where m ~ 0.1
    (continuity: Q = w * d * v, so b + f + m = 1.0)

Scaling from bankfull reference:
    current_depth = bankfull_depth * (Q_current / Q_bankfull)^0.4

Data sources:
  1. NHDPlus V2.1 bankfull hydraulic geometry attributes
     (bankfull depth, width, cross-sectional area for 2.7M stream reaches)
     Source: https://data.usgs.gov/datacatalog/data/USGS:5cf02bdae4b0b51330e22b85
     Based on Bieger et al. (2015) regional regression equations for
     8 CONUS Physiographic Divisions.
  2. USGS NWIS real-time instantaneous values at 8,000+ stream gages
     API: https://waterservices.usgs.gov/nwis/iv/
  3. NOAA National Water Model (NWM) for ungaged reaches
  4. Leopold & Maddock (1953) hydraulic geometry power laws

References:
  - Leopold, L.B. & Maddock, T. (1953). The hydraulic geometry of stream
    channels and some physiographic implications. USGS Prof. Paper 252.
  - Bieger, K. et al. (2015). Development and evaluation of bankfull
    hydraulic geometry relationships for the physiographic regions of the
    United States. JAWRA 51(3):842-858.

Usage:
    # Current depth at a lat/lon
    python river_depth.py --lat 44.95 --lon -93.27 --mode current-depth

    # Depth profile along a reach
    python river_depth.py --reach-comid 5429397 --mode reach-profile \
        --output /data/rivers/reach_5429397.json

    # Download NHDPlus bankfull dataset
    python river_depth.py --mode download-bankfull --output /data/nhdplus

Requirements:
    pip install requests pandas numpy geopandas tqdm
"""

import argparse
import json
import logging
import os
import time
import hashlib
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional, List, Dict, Tuple

import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("river_depth")


# -- Constants ---------------------------------------------------------------

# USGS NWIS API endpoints
NWIS_IV_URL = "https://waterservices.usgs.gov/nwis/iv/"
NWIS_SITE_URL = "https://waterservices.usgs.gov/nwis/site/"
NWIS_STATS_URL = "https://waterservices.usgs.gov/nwis/stat/"

# USGS parameter codes
PARAM_DISCHARGE_CFS = "00060"   # Discharge, cubic feet per second
PARAM_GAGE_HEIGHT_FT = "00065"  # Gage height, feet

# NHDPlus bankfull data on USGS ScienceBase
NHDPLUS_BANKFULL_URL = (
    "https://www.sciencebase.gov/catalog/file/get/5cf02bdae4b0b51330e22b85"
)

# Conversion factors
CFS_TO_CMS = 0.028316846592  # cubic feet/sec -> cubic meters/sec
FT_TO_M = 0.3048

# Hydraulic geometry exponents (Leopold & Maddock 1953 median values)
HG_DEPTH_EXPONENT = 0.40   # f: depth ~ Q^0.40
HG_WIDTH_EXPONENT = 0.50   # b: width ~ Q^0.50
HG_VELOCITY_EXPONENT = 0.10  # m: velocity ~ Q^0.10

# Rate limiting: USGS asks max ~5 requests/sec
USGS_MIN_REQUEST_INTERVAL = 0.22  # seconds between requests (~4.5 req/sec)

# Cache TTL
DISCHARGE_CACHE_TTL = 900      # 15 minutes for real-time discharge
SITE_CACHE_TTL = 86400         # 24 hours for site metadata
BANKFULL_CACHE_TTL = 604800    # 7 days for bankfull geometry (static data)


# -- Data Classes ------------------------------------------------------------

@dataclass
class BankfullGeometry:
    """NHDPlus bankfull hydraulic geometry for a stream reach."""
    comid: int
    bankfull_depth_m: float
    bankfull_width_m: float
    bankfull_xsec_area_m2: float
    drainage_area_km2: float
    bankfull_discharge_cms: float
    stream_order: int = 0
    physiographic_division: str = ""
    regression_r2: float = np.nan


@dataclass
class RiverDepthResult:
    """Result of river depth estimation at a point."""
    depth_m: float              # Estimated current thalweg depth
    bankfull_depth_m: float     # Depth at bankfull discharge
    width_m: float              # Estimated current top width
    velocity_ms: float          # Estimated current mean velocity
    discharge_cms: float        # Current discharge (m^3/s)
    flow_percentile: float      # Where current flow sits in historical (0-100)
    is_wadeable: bool           # depth < 1.0m AND velocity < 1.5 m/s
    habitat_type: str           # pool | riffle | run | glide | deep_pool
    source: str                 # "usgs_gage" | "nwm_model" | "hydraulic_geometry"
    confidence: float           # 0-1 confidence score
    last_updated: str           # ISO timestamp of discharge observation
    reach_comid: Optional[int] = None
    gage_id: Optional[str] = None
    uncertainty_m: float = np.nan  # ±1σ depth uncertainty


@dataclass
class ReachProfilePoint:
    """A single point along a reach depth profile."""
    distance_m: float           # Distance downstream from reach start
    depth_m: float
    width_m: float
    habitat_type: str
    lat: float
    lon: float


# -- Caching Helpers ---------------------------------------------------------

class DiskCache:
    """Simple disk-backed JSON cache with TTL."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _key_path(self, key: str) -> Path:
        hashed = hashlib.md5(key.encode()).hexdigest()
        return self.cache_dir / f"{hashed}.json"

    def get(self, key: str, ttl: int) -> Optional[dict]:
        """Return cached value if it exists and is not expired."""
        path = self._key_path(key)
        if not path.exists():
            return None
        try:
            with open(path, "r") as f:
                entry = json.load(f)
            if time.time() - entry.get("ts", 0) > ttl:
                path.unlink(missing_ok=True)
                return None
            return entry.get("data")
        except (json.JSONDecodeError, OSError):
            path.unlink(missing_ok=True)
            return None

    def put(self, key: str, data: dict):
        """Store a value in the cache."""
        path = self._key_path(key)
        try:
            with open(path, "w") as f:
                json.dump({"ts": time.time(), "data": data}, f)
        except OSError as e:
            log.warning("Cache write failed for %s: %s", key, e)


# -- Rate Limiter ------------------------------------------------------------

class RateLimiter:
    """Token-bucket rate limiter for USGS API compliance."""

    def __init__(self, min_interval: float = USGS_MIN_REQUEST_INTERVAL):
        self.min_interval = min_interval
        self._last_request = 0.0

    def wait(self):
        """Block until it is safe to make the next request."""
        now = time.time()
        elapsed = now - self._last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request = time.time()


# -- Main Estimator ----------------------------------------------------------

class RiverDepthEstimator:
    """
    Estimate river/stream depth using hydraulic geometry + real-time discharge.

    Unlike lake bathymetry (optical SDB), river depth is:
    - Flow-dependent (changes hourly with precipitation/snowmelt)
    - Not optically detectable (too turbid, too narrow, too dynamic)
    - Well-predicted by hydraulic geometry relationships

    Data sources:
    1. NHDPlus V2.1 bankfull hydraulic geometry attributes
       (bankfull depth, width, cross-sectional area for every stream reach)
    2. USGS NWIS real-time discharge at 8,000+ gages
    3. NOAA National Water Model for ungaged reaches
    4. Leopold & Maddock (1953) hydraulic geometry power laws

    The key relationship:
        depth = c * Q^f  where f ~ 0.4
        width = a * Q^b  where b ~ 0.5
        velocity = k * Q^m  where m ~ 0.1

    So: current_depth = bankfull_depth * (Q_current / Q_bankfull)^0.4
    """

    def __init__(self, cache_dir: str = "/tmp/opencatch_river_cache",
                 nhdplus_dir: Optional[str] = None):
        self.cache = DiskCache(Path(cache_dir))
        self.rate_limiter = RateLimiter()
        self.nhdplus_dir = Path(nhdplus_dir) if nhdplus_dir else None
        self._bankfull_df: Optional[pd.DataFrame] = None

    # ── NHDPlus Bankfull Data ───────────────────────────────────────────

    def download_nhdplus_bankfull(self, output_dir: str):
        """Download NHDPlus V2.1 bankfull hydraulic geometry attributes.

        Source: USGS ScienceBase Item 5cf02bdae4b0b51330e22b85
        Format: CSV with COMID, bankfull width/depth/area, drainage area
        Size: ~200MB for all CONUS

        The dataset contains bankfull hydraulic geometry for 2.7M NHDPlus
        stream reaches, derived from regional regression equations in
        Bieger et al. (2015) for 8 CONUS Physiographic Divisions.

        Args:
            output_dir: Directory to save the downloaded files.
        """
        import requests

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        target = output_path / "nhdplus_bankfull_conus.csv"

        if target.exists():
            size_mb = target.stat().st_size / 1e6
            log.info("Bankfull dataset already exists: %s (%.1f MB)", target, size_mb)
            return target

        log.info("Downloading NHDPlus bankfull hydraulic geometry from USGS...")
        log.info("URL: %s", NHDPLUS_BANKFULL_URL)

        try:
            resp = requests.get(
                NHDPLUS_BANKFULL_URL,
                params={"f": "__disk__", "names": "bankfull_conus.csv"},
                stream=True,
                timeout=300,
            )
            resp.raise_for_status()

            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(target, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0 and downloaded % (10 * 1024 * 1024) == 0:
                        log.info("  %.1f / %.1f MB", downloaded / 1e6, total / 1e6)

            log.info("Downloaded bankfull dataset: %s (%.1f MB)",
                     target, target.stat().st_size / 1e6)
            return target

        except Exception as e:
            log.error("Failed to download bankfull dataset: %s", e)
            if target.exists():
                target.unlink()
            raise

    def _load_bankfull(self) -> pd.DataFrame:
        """Load and cache the NHDPlus bankfull geometry dataframe."""
        if self._bankfull_df is not None:
            return self._bankfull_df

        if self.nhdplus_dir is None:
            raise FileNotFoundError(
                "NHDPlus directory not set. Pass nhdplus_dir to constructor "
                "or run download_nhdplus_bankfull() first."
            )

        csv_path = self.nhdplus_dir / "nhdplus_bankfull_conus.csv"
        if not csv_path.exists():
            raise FileNotFoundError(
                f"Bankfull CSV not found at {csv_path}. "
                "Run download_nhdplus_bankfull() first."
            )

        log.info("Loading NHDPlus bankfull geometry from %s ...", csv_path)
        df = pd.read_csv(csv_path, dtype={"COMID": int})

        # Standardize column names
        col_map = {
            "COMID": "comid",
            "BANKFULL_WIDTH_M": "bankfull_width_m",
            "BANKFULL_DEPTH_M": "bankfull_depth_m",
            "BANKFULL_XSEC_AREA_M2": "bankfull_xsec_area_m2",
            "DRAINAGE_AREA_KM2": "drainage_area_km2",
            "BANKFULL_DISCHARGE_CMS": "bankfull_discharge_cms",
            "STREAM_ORDER": "stream_order",
            "PHYS_DIV": "physiographic_division",
            "REG_R2": "regression_r2",
        }
        # Apply mapping for columns that exist
        rename = {k: v for k, v in col_map.items() if k in df.columns}
        df = df.rename(columns=rename)

        if "comid" not in df.columns:
            raise ValueError("Bankfull CSV missing COMID column")

        df = df.set_index("comid")
        self._bankfull_df = df
        log.info("Loaded bankfull geometry for %d reaches", len(df))
        return df

    def fetch_nhdplus_bankfull(self, comid: Optional[int] = None,
                                bbox: Optional[Tuple[float, float, float, float]] = None
                                ) -> Optional[BankfullGeometry]:
        """Fetch NHDPlus bankfull hydraulic geometry for a reach.

        Source: https://data.usgs.gov/datacatalog/data/USGS:5cf02bdae4b0b51330e22b85

        Returns per-reach: bankfull_depth_m, bankfull_width_m,
        bankfull_xsec_area_m2, drainage_area_km2.

        Based on Bieger et al. (2015) regional regression equations
        for 8 CONUS Physiographic Divisions.

        Args:
            comid: NHDPlus COMID for the stream reach.
            bbox: Bounding box (minlon, minlat, maxlon, maxlat) to query.
                  Returns the first matching reach.

        Returns:
            BankfullGeometry or None if reach not found.
        """
        if comid is None and bbox is None:
            raise ValueError("Must provide either comid or bbox")

        # Try cache first
        cache_key = f"bankfull_{comid}" if comid else f"bankfull_bbox_{bbox}"
        cached = self.cache.get(cache_key, BANKFULL_CACHE_TTL)
        if cached:
            return BankfullGeometry(**cached)

        df = self._load_bankfull()

        if comid is not None:
            if comid not in df.index:
                log.warning("COMID %d not found in bankfull dataset", comid)
                return None
            row = df.loc[comid]
        else:
            # bbox query requires geometry -- fall back to filtering by drainage area
            log.warning("Bbox queries require geopandas + NHDPlus flowlines. "
                        "Use comid for direct lookup.")
            return None

        result = BankfullGeometry(
            comid=comid,
            bankfull_depth_m=float(row.get("bankfull_depth_m", np.nan)),
            bankfull_width_m=float(row.get("bankfull_width_m", np.nan)),
            bankfull_xsec_area_m2=float(row.get("bankfull_xsec_area_m2", np.nan)),
            drainage_area_km2=float(row.get("drainage_area_km2", np.nan)),
            bankfull_discharge_cms=float(row.get("bankfull_discharge_cms", np.nan)),
            stream_order=int(row.get("stream_order", 0)),
            physiographic_division=str(row.get("physiographic_division", "")),
            regression_r2=float(row.get("regression_r2", np.nan)),
        )

        self.cache.put(cache_key, asdict(result))
        return result

    # ── USGS Discharge ──────────────────────────────────────────────────

    def fetch_usgs_discharge(self, site_id: str, period: str = "P7D"
                              ) -> Optional[pd.DataFrame]:
        """Fetch real-time discharge from USGS NWIS instantaneous values.

        API: https://waterservices.usgs.gov/nwis/iv/
        Parameters: sites, parameterCd=00060 (discharge), period
        Returns: DataFrame with columns [datetime, discharge_cfs, discharge_cms]

        Args:
            site_id: USGS gage site ID (e.g., "05331000").
            period: ISO 8601 duration (default P7D = past 7 days).

        Returns:
            DataFrame of discharge time series, or None on failure.
        """
        import requests

        cache_key = f"discharge_{site_id}_{period}"
        cached = self.cache.get(cache_key, DISCHARGE_CACHE_TTL)
        if cached:
            return pd.DataFrame(cached)

        self.rate_limiter.wait()
        params = {
            "format": "json",
            "sites": site_id,
            "parameterCd": PARAM_DISCHARGE_CFS,
            "period": period,
            "siteStatus": "active",
        }

        log.info("Fetching discharge for USGS site %s (period=%s)", site_id, period)
        try:
            resp = requests.get(NWIS_IV_URL, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.error("NWIS discharge request failed for site %s: %s", site_id, e)
            return None

        # Parse NWIS JSON response
        try:
            ts_list = data["value"]["timeSeries"]
            if not ts_list:
                log.warning("No discharge data for site %s", site_id)
                return None

            values = ts_list[0]["values"][0]["value"]
            records = []
            for v in values:
                q_cfs = float(v["value"]) if v["value"] not in ("-999999", "") else np.nan
                records.append({
                    "datetime": v["dateTime"],
                    "discharge_cfs": q_cfs,
                    "discharge_cms": q_cfs * CFS_TO_CMS if not np.isnan(q_cfs) else np.nan,
                })

            df = pd.DataFrame(records)
            df["datetime"] = pd.to_datetime(df["datetime"])
            df = df.dropna(subset=["discharge_cfs"])

            if df.empty:
                log.warning("All discharge values are NaN for site %s", site_id)
                return None

            self.cache.put(cache_key, df.to_dict(orient="list"))
            log.info("Got %d discharge observations for site %s", len(df), site_id)
            return df

        except (KeyError, IndexError) as e:
            log.error("Failed to parse NWIS response for site %s: %s", site_id, e)
            return None

    def _fetch_flow_statistics(self, site_id: str) -> Optional[Dict]:
        """Fetch flow duration statistics for a gage (median, percentiles).

        Used to compute flow_percentile for current conditions.
        """
        import requests

        cache_key = f"flow_stats_{site_id}"
        cached = self.cache.get(cache_key, SITE_CACHE_TTL)
        if cached:
            return cached

        self.rate_limiter.wait()
        params = {
            "format": "json",
            "sites": site_id,
            "parameterCd": PARAM_DISCHARGE_CFS,
            "statReportType": "daily",
            "statTypeCd": "all",
        }

        try:
            resp = requests.get(NWIS_STATS_URL, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            stats = data.get("value", {}).get("timeSeries", [])
            if stats:
                result = {"raw": stats}
                self.cache.put(cache_key, result)
                return result
        except Exception as e:
            log.debug("Flow stats request failed for %s: %s", site_id, e)

        return None

    def find_nearest_gage(self, lat: float, lon: float, max_dist_km: float = 50
                           ) -> Optional[Dict]:
        """Find the nearest USGS stream gage to a geographic point.

        Uses USGS site service:
        https://waterservices.usgs.gov/nwis/site/

        Args:
            lat: Latitude in decimal degrees.
            lon: Longitude in decimal degrees.
            max_dist_km: Maximum search radius in kilometers.

        Returns:
            Dict with keys: site_id, name, lat, lon, distance_km, drainage_area_km2
            or None if no gage found within radius.
        """
        import requests

        cache_key = f"nearest_gage_{lat:.4f}_{lon:.4f}_{max_dist_km}"
        cached = self.cache.get(cache_key, SITE_CACHE_TTL)
        if cached:
            return cached

        # USGS uses miles for bounding distance
        max_dist_mi = max_dist_km * 0.621371

        self.rate_limiter.wait()
        params = {
            "format": "rdb",
            "bBox": f"{lon - 0.5},{lat - 0.5},{lon + 0.5},{lat + 0.5}",
            "parameterCd": PARAM_DISCHARGE_CFS,
            "siteType": "ST",           # Stream sites only
            "siteStatus": "active",
            "hasDataTypeCd": "iv",      # Must have instantaneous values
        }

        log.info("Searching for nearest gage to (%.4f, %.4f) within %.0f km",
                 lat, lon, max_dist_km)

        try:
            resp = requests.get(NWIS_SITE_URL, params=params, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            log.error("NWIS site search failed: %s", e)
            return None

        # Parse RDB (tab-delimited) response
        lines = resp.text.strip().split("\n")
        header_idx = None
        for i, line in enumerate(lines):
            if line.startswith("agency_cd"):
                header_idx = i
                break

        if header_idx is None:
            log.warning("No gages found near (%.4f, %.4f)", lat, lon)
            return None

        headers = lines[header_idx].split("\t")
        # Skip the format line (line after header)
        data_lines = lines[header_idx + 2:]

        best = None
        best_dist = float("inf")

        for line in data_lines:
            if not line.strip():
                continue
            fields = line.split("\t")
            if len(fields) < len(headers):
                continue
            row = dict(zip(headers, fields))

            try:
                site_lat = float(row.get("dec_lat_va", 0))
                site_lon = float(row.get("dec_long_va", 0))
            except (ValueError, TypeError):
                continue

            # Haversine distance
            dist_km = self._haversine(lat, lon, site_lat, site_lon)

            if dist_km <= max_dist_km and dist_km < best_dist:
                drain_area = row.get("drain_area_va", "")
                best = {
                    "site_id": row.get("site_no", "").strip(),
                    "name": row.get("station_nm", "").strip(),
                    "lat": site_lat,
                    "lon": site_lon,
                    "distance_km": round(dist_km, 2),
                    "drainage_area_km2": (
                        float(drain_area) * 2.58999 if drain_area.strip() else np.nan
                    ),  # sq miles -> km2
                }
                best_dist = dist_km

        if best:
            log.info("Found gage %s (%s) at %.1f km",
                     best["site_id"], best["name"], best["distance_km"])
            self.cache.put(cache_key, best)
        else:
            log.warning("No active stream gage found within %.0f km of (%.4f, %.4f)",
                        max_dist_km, lat, lon)

        return best

    @staticmethod
    def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Great-circle distance in km between two points."""
        R = 6371.0
        dlat = np.radians(lat2 - lat1)
        dlon = np.radians(lon2 - lon1)
        a = (np.sin(dlat / 2) ** 2 +
             np.cos(np.radians(lat1)) * np.cos(np.radians(lat2)) *
             np.sin(dlon / 2) ** 2)
        return R * 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))

    # ── Hydraulic Geometry Scaling ──────────────────────────────────────

    def estimate_depth_at_flow(self, bankfull_depth: float,
                                Q_current: float, Q_bankfull: float,
                                bankfull_width: float = np.nan,
                                regression_r2: float = np.nan
                                ) -> Dict:
        """Apply hydraulic geometry scaling to estimate depth at current flow.

        Uses Leopold & Maddock (1953) power-law relationships:
            depth = bankfull_depth * (Q_current / Q_bankfull)^f
            width = bankfull_width * (Q_current / Q_bankfull)^b
            velocity = Q / (width * depth)

        where f ~ 0.4, b ~ 0.5 (Leopold & Maddock 1953).

        The uncertainty is estimated from Bieger et al. (2015) regional
        regression R^2 values, plus additional uncertainty from flow ratio
        extrapolation beyond the calibrated range.

        Args:
            bankfull_depth: Depth at bankfull discharge (meters).
            Q_current: Current discharge (m^3/s).
            Q_bankfull: Bankfull discharge (m^3/s).
            bankfull_width: Width at bankfull discharge (meters).
            regression_r2: R^2 of the regional regression (for uncertainty).

        Returns:
            Dict with: depth_m, width_m, velocity_ms, is_wadeable,
            uncertainty_m, flow_ratio.
        """
        if Q_bankfull <= 0 or bankfull_depth <= 0:
            return {
                "depth_m": np.nan, "width_m": np.nan, "velocity_ms": np.nan,
                "is_wadeable": False, "uncertainty_m": np.nan, "flow_ratio": np.nan,
            }

        flow_ratio = Q_current / Q_bankfull

        # Clamp extreme flow ratios for safety (model not calibrated beyond)
        clamped_ratio = np.clip(flow_ratio, 0.01, 5.0)
        if flow_ratio != clamped_ratio:
            log.warning("Flow ratio %.2f clamped to [0.01, 5.0]", flow_ratio)

        # Apply power laws
        depth_m = bankfull_depth * (clamped_ratio ** HG_DEPTH_EXPONENT)
        depth_m = max(depth_m, 0.01)  # Minimum 1 cm

        width_m = np.nan
        if not np.isnan(bankfull_width) and bankfull_width > 0:
            width_m = bankfull_width * (clamped_ratio ** HG_WIDTH_EXPONENT)
            width_m = max(width_m, 0.1)

        # Mean velocity from continuity: Q = w * d * v
        velocity_ms = np.nan
        if not np.isnan(width_m) and width_m > 0 and depth_m > 0:
            velocity_ms = Q_current / (width_m * depth_m)

        # Wadeability: depth < 1.0m AND velocity < 1.5 m/s (safe wading)
        is_wadeable = (
            depth_m < 1.0
            and (np.isnan(velocity_ms) or velocity_ms < 1.5)
        )

        # Uncertainty: base from regression R^2, inflated for extreme flows
        base_uncertainty = 0.3 if np.isnan(regression_r2) else (1.0 - regression_r2)
        flow_penalty = max(0, abs(np.log10(max(clamped_ratio, 0.01))) - 0.3) * 0.2
        rel_uncertainty = min(base_uncertainty + flow_penalty, 0.8)
        uncertainty_m = depth_m * rel_uncertainty

        return {
            "depth_m": round(depth_m, 3),
            "width_m": round(width_m, 2) if not np.isnan(width_m) else np.nan,
            "velocity_ms": round(velocity_ms, 3) if not np.isnan(velocity_ms) else np.nan,
            "is_wadeable": is_wadeable,
            "uncertainty_m": round(uncertainty_m, 3),
            "flow_ratio": round(flow_ratio, 4),
        }

    # ── Habitat Classification ──────────────────────────────────────────

    def classify_habitat(self, depth_m: float, velocity_ms: float,
                          width_m: float) -> str:
        """Classify stream habitat type for fishing applications.

        Based on simplified Rosgen classification adapted for anglers:
          - deep_pool: depth > 2.0m, velocity < 0.3 m/s (big fish holding)
          - pool: depth > 1.0m, velocity < 0.5 m/s (fish resting areas)
          - run: depth > 0.5m, velocity 0.3-1.0 m/s (active feeding)
          - glide: depth 0.3-1.0m, velocity 0.1-0.5 m/s (trout lies)
          - riffle: depth < 0.5m, velocity > 0.5 m/s (oxygenated, insects)

        Args:
            depth_m: Water depth in meters.
            velocity_ms: Mean velocity in m/s (can be NaN).
            width_m: Channel width in meters (can be NaN).

        Returns:
            Habitat type string: "pool" | "riffle" | "run" | "glide" | "deep_pool"
        """
        if np.isnan(depth_m):
            return "unknown"

        vel = velocity_ms if not np.isnan(velocity_ms) else 0.3  # assume moderate

        if depth_m > 2.0 and vel < 0.3:
            return "deep_pool"
        elif depth_m > 1.0 and vel < 0.5:
            return "pool"
        elif depth_m > 0.5 and 0.3 <= vel <= 1.0:
            return "run"
        elif 0.3 <= depth_m <= 1.0 and vel < 0.5:
            return "glide"
        elif depth_m < 0.5 and vel > 0.5:
            return "riffle"
        elif depth_m < 0.3:
            return "riffle"
        else:
            return "run"

    # ── Flow Percentile ─────────────────────────────────────────────────

    def _compute_flow_percentile(self, Q_current: float,
                                  discharge_df: pd.DataFrame) -> float:
        """Compute where current flow sits relative to recent history (0-100).

        Uses the 7-day discharge record to estimate exceedance percentile.
        Higher percentile = higher flow relative to recent conditions.
        """
        if discharge_df is None or discharge_df.empty:
            return np.nan

        discharges = discharge_df["discharge_cms"].dropna().values
        if len(discharges) < 5:
            return np.nan

        percentile = float(np.searchsorted(np.sort(discharges), Q_current) /
                           len(discharges) * 100.0)
        return round(percentile, 1)

    # ── Main Entry Points ───────────────────────────────────────────────

    def get_river_depth(self, lat: float, lon: float,
                         reach_comid: Optional[int] = None
                         ) -> Optional[RiverDepthResult]:
        """Main entry point: get current depth estimate for any river location.

        Workflow:
        1. Find nearest USGS gage (or use NWM for ungaged reaches)
        2. Fetch real-time discharge
        3. Look up NHDPlus bankfull geometry for the reach
        4. Apply hydraulic geometry scaling
        5. Return RiverDepthResult with depth, width, habitat, confidence

        Args:
            lat: Latitude of the river point.
            lon: Longitude of the river point.
            reach_comid: Optional NHDPlus COMID (skips spatial lookup).

        Returns:
            RiverDepthResult with current depth estimate, or None on failure.
        """
        # Step 1: Find nearest gage
        gage = self.find_nearest_gage(lat, lon)
        if gage is None:
            log.warning("No gage found near (%.4f, %.4f) -- trying NWM fallback",
                        lat, lon)
            return self._nwm_fallback(lat, lon, reach_comid)

        site_id = gage["site_id"]
        source = "usgs_gage"

        # Step 2: Fetch current discharge
        discharge_df = self.fetch_usgs_discharge(site_id, period="P7D")
        if discharge_df is None or discharge_df.empty:
            log.warning("No discharge data for gage %s", site_id)
            return self._nwm_fallback(lat, lon, reach_comid)

        # Most recent observation
        latest = discharge_df.iloc[-1]
        Q_current = latest["discharge_cms"]
        last_updated = str(latest["datetime"])

        # Step 3: Get bankfull geometry
        bankfull = None
        if reach_comid is not None:
            bankfull = self.fetch_nhdplus_bankfull(comid=reach_comid)

        if bankfull is None:
            # Estimate bankfull from drainage area using Bieger et al. (2015)
            drain_area = gage.get("drainage_area_km2", np.nan)
            if not np.isnan(drain_area) and drain_area > 0:
                bankfull = self._estimate_bankfull_from_drainage(drain_area)
                log.info("Estimated bankfull from drainage area %.1f km2", drain_area)
            else:
                log.warning("No bankfull geometry and no drainage area for gage %s",
                            site_id)
                return None

        # Step 4: Hydraulic geometry scaling
        scaled = self.estimate_depth_at_flow(
            bankfull_depth=bankfull.bankfull_depth_m,
            Q_current=Q_current,
            Q_bankfull=bankfull.bankfull_discharge_cms,
            bankfull_width=bankfull.bankfull_width_m,
            regression_r2=bankfull.regression_r2,
        )

        if np.isnan(scaled["depth_m"]):
            log.error("Depth scaling returned NaN")
            return None

        # Step 5: Classify habitat
        habitat = self.classify_habitat(
            scaled["depth_m"], scaled["velocity_ms"], scaled["width_m"]
        )

        # Flow percentile
        flow_pct = self._compute_flow_percentile(Q_current, discharge_df)

        # Confidence: based on gage distance, regression R2, data freshness
        gage_dist = gage.get("distance_km", 50)
        dist_penalty = min(gage_dist / 100.0, 0.4)
        r2_score = bankfull.regression_r2 if not np.isnan(bankfull.regression_r2) else 0.5
        confidence = round(max(0.1, r2_score - dist_penalty), 2)

        return RiverDepthResult(
            depth_m=scaled["depth_m"],
            bankfull_depth_m=bankfull.bankfull_depth_m,
            width_m=scaled["width_m"],
            velocity_ms=scaled["velocity_ms"],
            discharge_cms=round(Q_current, 3),
            flow_percentile=flow_pct,
            is_wadeable=scaled["is_wadeable"],
            habitat_type=habitat,
            source=source,
            confidence=confidence,
            last_updated=last_updated,
            reach_comid=reach_comid or bankfull.comid,
            gage_id=site_id,
            uncertainty_m=scaled["uncertainty_m"],
        )

    def _estimate_bankfull_from_drainage(self, drainage_area_km2: float
                                          ) -> BankfullGeometry:
        """Estimate bankfull geometry from drainage area alone.

        Uses continent-wide regressions from Bieger et al. (2015) Table 3
        (Interior Plains / Central Lowland as default):
            W_bf = 2.76 * DA^0.399
            D_bf = 0.23 * DA^0.294
            Q_bf = 0.41 * DA^0.796
        where DA is in km^2, outputs in meters and m^3/s.
        """
        da = max(drainage_area_km2, 0.1)
        return BankfullGeometry(
            comid=0,
            bankfull_depth_m=round(0.23 * (da ** 0.294), 3),
            bankfull_width_m=round(2.76 * (da ** 0.399), 2),
            bankfull_xsec_area_m2=round(0.23 * (da ** 0.294) * 2.76 * (da ** 0.399), 2),
            drainage_area_km2=drainage_area_km2,
            bankfull_discharge_cms=round(0.41 * (da ** 0.796), 3),
            stream_order=0,
            physiographic_division="estimated",
            regression_r2=0.55,  # Conservative R^2 for generic regression
        )

    def _nwm_fallback(self, lat: float, lon: float,
                       reach_comid: Optional[int]) -> Optional[RiverDepthResult]:
        """Fallback: use NOAA National Water Model for ungaged reaches.

        NWM provides modeled streamflow for all 2.7M NHDPlus reaches.
        Currently returns None -- placeholder for NWM integration.
        """
        # TODO: Integrate NOAA NWM API (https://api.water.noaa.gov/nwps/v1/)
        # NWM provides modeled discharge for every NHDPlus COMID, which
        # covers ungaged reaches where USGS NWIS has no data.
        log.info("NWM fallback not yet implemented for (%.4f, %.4f)", lat, lon)
        return None

    # ── Reach Profile ───────────────────────────────────────────────────

    def get_reach_profile(self, reach_comid: int, n_points: int = 20
                           ) -> Optional[List[ReachProfilePoint]]:
        """Get depth profile along a river reach.

        Uses NHDPlus flowline geometry to sample points along the reach,
        and returns depth estimates at each point. Includes pool/riffle
        classification based on depth variation.

        The depth varies along a reach due to:
        - Pool-riffle sequences (spacing ~ 5-7x bankfull width)
        - Channel sinuosity (deeper on outside of bends)
        - Gradient changes

        We model this as sinusoidal variation around the mean:
            depth(x) = mean_depth * (1 + A * sin(2*pi*x / wavelength))
        where A ~ 0.3 and wavelength ~ 6 * bankfull_width.

        Args:
            reach_comid: NHDPlus COMID for the stream reach.
            n_points: Number of points to sample along the reach.

        Returns:
            List of ReachProfilePoint, or None if reach not found.
        """
        bankfull = self.fetch_nhdplus_bankfull(comid=reach_comid)
        if bankfull is None:
            log.error("Cannot get profile: COMID %d not found", reach_comid)
            return None

        # Estimate current depth (use bankfull as reference if no gage data)
        # For the profile, we use bankfull depth as the baseline
        base_depth = bankfull.bankfull_depth_m
        base_width = bankfull.bankfull_width_m

        if np.isnan(base_depth) or base_depth <= 0:
            log.error("Invalid bankfull depth for COMID %d", reach_comid)
            return None

        # Pool-riffle wavelength ~ 5-7x bankfull width (Leopold 1953)
        wavelength = 6.0 * base_width if not np.isnan(base_width) else 50.0
        amplitude = 0.30  # 30% depth variation (typical pool-riffle ratio)

        # Estimate reach length from drainage area (rough power law)
        reach_length_m = min(bankfull.drainage_area_km2 * 50, 10000)  # cap at 10km
        reach_length_m = max(reach_length_m, 100)

        points = []
        for i in range(n_points):
            dist = (i / max(n_points - 1, 1)) * reach_length_m

            # Sinusoidal depth variation for pool-riffle sequence
            phase = 2.0 * np.pi * dist / wavelength
            depth_factor = 1.0 + amplitude * np.sin(phase)
            depth = base_depth * depth_factor

            # Width inversely correlated with depth (narrower at pools)
            width = base_width * (1.0 - 0.15 * np.sin(phase)) if not np.isnan(base_width) else np.nan

            # Velocity from continuity (assuming constant Q = bankfull)
            vel = np.nan
            if not np.isnan(width) and width > 0 and depth > 0:
                vel = bankfull.bankfull_discharge_cms / (width * depth)

            habitat = self.classify_habitat(depth, vel, width)

            # Placeholder coordinates (would come from NHDPlus flowline geometry)
            points.append(ReachProfilePoint(
                distance_m=round(dist, 1),
                depth_m=round(depth, 3),
                width_m=round(width, 2) if not np.isnan(width) else np.nan,
                habitat_type=habitat,
                lat=np.nan,  # Requires NHDPlus flowline geometry
                lon=np.nan,
            ))

        log.info("Generated %d-point profile for COMID %d (%.0fm reach)",
                 n_points, reach_comid, reach_length_m)
        return points


# -- CLI ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="OpenCatch River Depth Estimator — hydraulic geometry + USGS discharge",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Current depth at a location
  python river_depth.py --lat 44.95 --lon -93.27 --mode current-depth

  # Depth profile along a reach
  python river_depth.py --reach-comid 5429397 --mode reach-profile \\
      --output /data/rivers/reach_5429397.json

  # Download NHDPlus bankfull dataset
  python river_depth.py --mode download-bankfull --output /data/nhdplus
        """,
    )

    parser.add_argument("--lat", type=float, help="Latitude (decimal degrees)")
    parser.add_argument("--lon", type=float, help="Longitude (decimal degrees)")
    parser.add_argument("--reach-comid", type=int, help="NHDPlus COMID for reach")
    parser.add_argument("--mode", required=True,
                        choices=["current-depth", "reach-profile", "download-bankfull"],
                        help="Operation mode")
    parser.add_argument("--output", type=str, help="Output file or directory path")
    parser.add_argument("--nhdplus-dir", type=str, default="/data/nhdplus",
                        help="Directory containing NHDPlus bankfull CSV")
    parser.add_argument("--cache-dir", type=str, default="/tmp/opencatch_river_cache",
                        help="Cache directory for API responses")
    parser.add_argument("--max-gage-dist", type=float, default=50,
                        help="Max distance to USGS gage (km)")
    parser.add_argument("--n-points", type=int, default=20,
                        help="Number of profile sample points")

    args = parser.parse_args()

    estimator = RiverDepthEstimator(
        cache_dir=args.cache_dir,
        nhdplus_dir=args.nhdplus_dir,
    )

    if args.mode == "download-bankfull":
        out_dir = args.output or args.nhdplus_dir
        estimator.download_nhdplus_bankfull(out_dir)
        return

    if args.mode == "current-depth":
        if args.lat is None or args.lon is None:
            parser.error("--lat and --lon required for current-depth mode")

        result = estimator.get_river_depth(
            lat=args.lat, lon=args.lon, reach_comid=args.reach_comid
        )

        if result is None:
            log.error("Could not estimate depth at (%.4f, %.4f)", args.lat, args.lon)
            return

        output = asdict(result)
        # Clean up NaN for JSON serialization
        for k, v in output.items():
            if isinstance(v, float) and np.isnan(v):
                output[k] = None

        print(json.dumps(output, indent=2, default=str))

        if args.output:
            with open(args.output, "w") as f:
                json.dump(output, f, indent=2, default=str)
            log.info("Saved result to %s", args.output)

    elif args.mode == "reach-profile":
        comid = args.reach_comid
        if comid is None:
            parser.error("--reach-comid required for reach-profile mode")

        points = estimator.get_reach_profile(comid, n_points=args.n_points)
        if points is None:
            log.error("Could not generate profile for COMID %d", comid)
            return

        output = [asdict(p) for p in points]
        for pt in output:
            for k, v in pt.items():
                if isinstance(v, float) and np.isnan(v):
                    pt[k] = None

        print(json.dumps(output, indent=2))

        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            with open(args.output, "w") as f:
                json.dump(output, f, indent=2)
            log.info("Saved %d-point profile to %s", len(points), args.output)


if __name__ == "__main__":
    main()
