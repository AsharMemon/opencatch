#!/usr/bin/env python3
"""
Fetch ICESat-2 ATL24 bathymetry data for inland lakes.

Downloads along-track bathymetric depth points from NASA's ICESat-2
ATL24 product to use as free training labels for the OpenCatch
satellite bathymetry pipeline. ATL24 provides depth measurements at
~0.7m along-track spacing with RMSD 0.34-0.89m accuracy using
532nm green photon-counting LiDAR.

Two backends:
  1. SlideRule (preferred): On-demand processing via sliderule.icesat2.org
  2. CMR + direct download: Search NASA CMR, download HDF5 granules

Usage:
    python fetch_icesat2_atl24.py \
        --lakes /data/waterbodies/mn_lakes.geojson \
        --output /data/icesat2/atl24/ \
        --start-date 2018-10-01 \
        --end-date 2026-03-25 \
        --max-lakes 500 \
        --min-confidence 3
"""

import argparse
import json
import logging
import netrc
import os
import sys
import time
from pathlib import Path
from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from scipy.spatial import cKDTree
from shapely.geometry import box, mapping, shape

logger = logging.getLogger(__name__)

CMR_SEARCH_URL = "https://cmr.earthdata.nasa.gov/search/granules.json"
SLIDERULE_URL = "https://sliderule.icesat2.org"
ATL24_CONCEPT_ID = "C2596864127-NSIDC_CPRD"
ATL24_SHORT_NAME = "ATL24"

OUTPUT_COLUMNS = ["lake_id", "lat", "lon", "depth_m", "confidence", "track_id", "date"]


class ICESat2ATL24Fetcher:
    """
    Fetch ICESat-2 ATL24 bathymetry for inland lakes.

    ATL24 is the Level 3 along-track bathymetry product providing
    classified bathymetric photons with refraction correction applied
    (Snell's law correction already baked in).

    Two backends:
    1. SlideRule (preferred): On-demand processing via sliderule.icesat2.org
    2. CMR + direct download: Search NASA CMR, download HDF5 granules
    """

    def __init__(self, earthdata_token: Optional[str] = None, min_confidence: int = 3):
        self.min_confidence = min_confidence
        self.token = earthdata_token or self._resolve_token()
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        if self.token:
            self.session.headers.update({"Authorization": f"Bearer {self.token}"})
        self._sliderule_available = None

    def _resolve_token(self) -> Optional[str]:
        """Resolve Earthdata token from environment or ~/.netrc."""
        token = os.environ.get("EARTHDATA_TOKEN")
        if token:
            logger.info("Using Earthdata token from EARTHDATA_TOKEN env var")
            return token

        try:
            nrc = netrc.netrc()
            auth = nrc.authenticators("urs.earthdata.nasa.gov")
            if auth:
                logger.info("Using credentials from ~/.netrc for urs.earthdata.nasa.gov")
                return auth[2]  # password field used as token
        except (FileNotFoundError, netrc.NetrcParseError):
            pass

        logger.warning(
            "No Earthdata token found. Set EARTHDATA_TOKEN env var or add "
            "urs.earthdata.nasa.gov to ~/.netrc. Some endpoints may fail."
        )
        return None

    def _check_sliderule(self) -> bool:
        """Check if SlideRule service is reachable."""
        if self._sliderule_available is not None:
            return self._sliderule_available
        try:
            resp = self.session.get(f"{SLIDERULE_URL}/source/version", timeout=10)
            self._sliderule_available = resp.status_code == 200
            if self._sliderule_available:
                logger.info("SlideRule service available: %s", resp.text.strip())
            else:
                logger.warning("SlideRule returned status %d", resp.status_code)
        except requests.RequestException as e:
            logger.warning("SlideRule unavailable: %s — will use CMR fallback", e)
            self._sliderule_available = False
        return self._sliderule_available

    # ------------------------------------------------------------------
    # CMR search
    # ------------------------------------------------------------------

    def search_granules(
        self,
        bbox: tuple[float, float, float, float],
        start_date: str = "2018-10-01",
        end_date: str = "2026-03-25",
    ) -> list[dict]:
        """
        Search CMR for ATL24 granules intersecting a bounding box.

        Parameters
        ----------
        bbox : tuple
            (west, south, east, north) in EPSG:4326 degrees.
        start_date, end_date : str
            ISO date strings for temporal search.

        Returns
        -------
        list[dict]
            Granule metadata dicts with keys: granule_id, url, time_start, bbox.
        """
        params = {
            "short_name": ATL24_SHORT_NAME,
            "bounding_box": f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}",
            "temporal": f"{start_date}T00:00:00Z,{end_date}T23:59:59Z",
            "page_size": 200,
            "sort_key": "-start_date",
        }
        granules = []
        page = 1

        while True:
            params["page_num"] = page
            try:
                resp = self.session.get(CMR_SEARCH_URL, params=params, timeout=30)
                resp.raise_for_status()
            except requests.RequestException as e:
                logger.error("CMR search failed for bbox %s: %s", bbox, e)
                break

            data = resp.json()
            entries = data.get("feed", {}).get("entry", [])
            if not entries:
                break

            for entry in entries:
                urls = [
                    link["href"]
                    for link in entry.get("links", [])
                    if link.get("href", "").endswith(".h5")
                ]
                granules.append(
                    {
                        "granule_id": entry.get("id", ""),
                        "title": entry.get("title", ""),
                        "time_start": entry.get("time_start", ""),
                        "url": urls[0] if urls else None,
                        "bbox": entry.get("boxes", [None])[0],
                    }
                )

            if len(entries) < 200:
                break
            page += 1
            time.sleep(0.5)

        logger.info("CMR found %d ATL24 granules for bbox %s", len(granules), bbox)
        return granules

    # ------------------------------------------------------------------
    # SlideRule backend
    # ------------------------------------------------------------------

    def fetch_sliderule(
        self, lake_polygon: dict, lake_id: str
    ) -> Optional[pd.DataFrame]:
        """
        Use SlideRule for on-demand ATL24 processing.

        Parameters
        ----------
        lake_polygon : dict
            GeoJSON geometry (Polygon/MultiPolygon) of the lake.
        lake_id : str
            Identifier for the lake.

        Returns
        -------
        pd.DataFrame or None
            DataFrame with OUTPUT_COLUMNS, or None on failure.
        """
        if not self._check_sliderule():
            return None

        geojson_coords = lake_polygon.get("coordinates", [])
        if lake_polygon.get("type") == "MultiPolygon":
            # Use the largest polygon ring
            geojson_coords = max(geojson_coords, key=lambda c: len(c[0]))
            geojson_coords = geojson_coords[0]
        elif lake_polygon.get("type") == "Polygon":
            geojson_coords = geojson_coords[0]
        else:
            logger.warning("Unsupported geometry type for lake %s", lake_id)
            return None

        # SlideRule expects lon/lat coordinate pairs
        poly_coords = [{"lon": c[0], "lat": c[1]} for c in geojson_coords]

        request_body = {
            "poly": poly_coords,
            "cnf": self.min_confidence,
            "cnt": 5,
            "len": 10.0,
            "srt": 3,  # surface type: inland water
            "pass_invalid": False,
            "timeout": 120,
        }

        try:
            resp = self.session.post(
                f"{SLIDERULE_URL}/source/atl24p",
                json=request_body,
                timeout=180,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error("SlideRule request failed for lake %s: %s", lake_id, e)
            return None

        try:
            result = resp.json()
        except ValueError:
            logger.error("SlideRule returned non-JSON for lake %s", lake_id)
            return None

        if not result or not isinstance(result, list):
            logger.info("No ATL24 photons from SlideRule for lake %s", lake_id)
            return None

        df = pd.DataFrame(result)
        if df.empty:
            return None

        # Map SlideRule output columns to our schema
        col_map = {
            "lat": "lat",
            "lon": "lon",
            "ortho_h": "depth_m",
            "confidence": "confidence",
            "rgt": "track_id",
            "time": "date",
        }
        available = {k: v for k, v in col_map.items() if k in df.columns}
        df = df.rename(columns=available)

        # Depth is negative below water surface in ATL24; convert to positive
        if "depth_m" in df.columns:
            df["depth_m"] = df["depth_m"].abs()

        df["lake_id"] = lake_id

        # Apply confidence filter
        if "confidence" in df.columns:
            df = df[df["confidence"] >= self.min_confidence]

        # Parse date if present
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date

        # Keep only output columns that exist
        keep = [c for c in OUTPUT_COLUMNS if c in df.columns]
        df = df[keep]

        logger.info(
            "SlideRule returned %d bathymetric photons for lake %s", len(df), lake_id
        )
        return df

    # ------------------------------------------------------------------
    # CMR + HDF5 fallback
    # ------------------------------------------------------------------

    def _download_and_extract_granule(
        self, granule: dict, lake_geom: shape, lake_id: str
    ) -> Optional[pd.DataFrame]:
        """
        Download an ATL24 HDF5 granule and extract bathymetric photons
        within the lake boundary.
        """
        url = granule.get("url")
        if not url:
            return None

        try:
            import h5py
            import tempfile
        except ImportError:
            logger.error("h5py required for CMR fallback. Install with: pip install h5py")
            return None

        try:
            resp = self.session.get(url, timeout=120, stream=True)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error("Failed to download granule %s: %s", granule["granule_id"], e)
            return None

        with tempfile.NamedTemporaryFile(suffix=".h5", delete=True) as tmp:
            for chunk in resp.iter_content(chunk_size=8192):
                tmp.write(chunk)
            tmp.flush()

            try:
                return self._parse_atl24_h5(tmp.name, lake_geom, lake_id, granule)
            except Exception as e:
                logger.error(
                    "Failed to parse granule %s: %s", granule["granule_id"], e
                )
                return None

    def _parse_atl24_h5(
        self, h5_path: str, lake_geom, lake_id: str, granule: dict
    ) -> Optional[pd.DataFrame]:
        """Parse ATL24 HDF5 file and extract photons within lake boundary."""
        import h5py
        from shapely.geometry import Point

        rows = []
        with h5py.File(h5_path, "r") as f:
            # ATL24 has beam groups: gt1l, gt1r, gt2l, gt2r, gt3l, gt3r
            beam_groups = [k for k in f.keys() if k.startswith("gt")]
            for beam in beam_groups:
                grp = f.get(beam)
                if grp is None:
                    continue

                # Look for bathymetry subgroup
                bathy = grp.get("bathy_photons") or grp.get("photons")
                if bathy is None:
                    continue

                lat = np.array(bathy.get("lat_ph", []))
                lon = np.array(bathy.get("lon_ph", []))
                depth = np.array(bathy.get("depth", bathy.get("ortho_h", [])))
                conf = np.array(bathy.get("confidence", []))

                if len(lat) == 0 or len(depth) == 0:
                    continue

                # Ensure arrays are same length
                min_len = min(len(lat), len(lon), len(depth))
                lat, lon, depth = lat[:min_len], lon[:min_len], depth[:min_len]
                if len(conf) > 0:
                    conf = conf[:min_len]
                else:
                    conf = np.full(min_len, 4)

                # Pre-filter by bounding box before point-in-polygon
                bounds = lake_geom.bounds  # (minx, miny, maxx, maxy)
                mask = (
                    (lon >= bounds[0])
                    & (lon <= bounds[2])
                    & (lat >= bounds[1])
                    & (lat <= bounds[3])
                    & (conf >= self.min_confidence)
                    & (np.isfinite(depth))
                    & (depth != 0.0)
                )

                idxs = np.where(mask)[0]
                for i in idxs:
                    pt = Point(lon[i], lat[i])
                    if lake_geom.contains(pt):
                        rows.append(
                            {
                                "lake_id": lake_id,
                                "lat": float(lat[i]),
                                "lon": float(lon[i]),
                                "depth_m": float(abs(depth[i])),
                                "confidence": int(conf[i]),
                                "track_id": beam,
                                "date": granule.get("time_start", "")[:10],
                            }
                        )

        if not rows:
            return None

        df = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
        logger.info(
            "Extracted %d photons from granule %s for lake %s",
            len(df), granule["granule_id"], lake_id,
        )
        return df

    # ------------------------------------------------------------------
    # Batch fetch
    # ------------------------------------------------------------------

    def fetch_for_lakes(
        self,
        lakes_gdf: gpd.GeoDataFrame,
        output_dir: str,
        max_lakes: Optional[int] = None,
        start_date: str = "2018-10-01",
        end_date: str = "2026-03-25",
    ) -> pd.DataFrame:
        """
        Batch fetch ATL24 for multiple lakes.

        Parameters
        ----------
        lakes_gdf : GeoDataFrame
            Must have 'geometry' column and a lake identifier column
            (tries 'lake_id', 'id', 'GNIS_ID', 'permanent_identifier').
        output_dir : str
            Directory to write per-lake parquet files.
        max_lakes : int, optional
            Limit number of lakes to process.
        start_date, end_date : str
            Temporal bounds for granule search.

        Returns
        -------
        pd.DataFrame
            Combined bathymetry points for all lakes.
        """
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        # Resolve lake ID column
        id_col = None
        for candidate in ["lake_id", "id", "GNIS_ID", "permanent_identifier", "FID"]:
            if candidate in lakes_gdf.columns:
                id_col = candidate
                break
        if id_col is None:
            lakes_gdf = lakes_gdf.reset_index()
            id_col = "index"
            logger.warning("No lake ID column found; using row index")

        if max_lakes is not None:
            lakes_gdf = lakes_gdf.head(max_lakes)

        use_sliderule = self._check_sliderule()
        all_results = []
        n_total = len(lakes_gdf)

        for idx, (_, row) in enumerate(lakes_gdf.iterrows()):
            lake_id = str(row[id_col])
            geom = row.geometry
            lake_parquet = out_path / f"{lake_id}.parquet"

            if lake_parquet.exists():
                logger.info("[%d/%d] Skipping lake %s — already fetched", idx + 1, n_total, lake_id)
                try:
                    existing = pd.read_parquet(lake_parquet)
                    all_results.append(existing)
                except Exception:
                    pass
                continue

            logger.info("[%d/%d] Fetching ATL24 for lake %s", idx + 1, n_total, lake_id)
            lake_df = None

            # Try SlideRule first
            if use_sliderule:
                poly_geojson = mapping(geom)
                lake_df = self.fetch_sliderule(poly_geojson, lake_id)

            # Fallback to CMR + HDF5 download
            if lake_df is None or lake_df.empty:
                bbox = geom.bounds  # (minx, miny, maxx, maxy)
                granules = self.search_granules(bbox, start_date, end_date)
                if granules:
                    parts = []
                    for g in granules[:10]:  # Limit granules per lake
                        part = self._download_and_extract_granule(g, geom, lake_id)
                        if part is not None:
                            parts.append(part)
                        time.sleep(1)  # Rate limiting
                    if parts:
                        lake_df = pd.concat(parts, ignore_index=True)
                        lake_df = lake_df.drop_duplicates(subset=["lat", "lon"])

            # Save results
            if lake_df is not None and not lake_df.empty:
                lake_df.to_parquet(lake_parquet, index=False)
                all_results.append(lake_df)
                logger.info(
                    "  -> %d points, depth range %.1f–%.1f m",
                    len(lake_df),
                    lake_df["depth_m"].min(),
                    lake_df["depth_m"].max(),
                )
            else:
                logger.info("  -> No bathymetric photons found for lake %s", lake_id)

            # Be polite to NASA servers
            time.sleep(0.5)

        if all_results:
            combined = pd.concat(all_results, ignore_index=True)
            combined_path = out_path / "all_lakes_atl24.parquet"
            combined.to_parquet(combined_path, index=False)
            logger.info(
                "Combined ATL24: %d points across %d lakes -> %s",
                len(combined),
                combined["lake_id"].nunique(),
                combined_path,
            )
            return combined

        logger.warning("No ATL24 bathymetry data found for any lakes")
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    # ------------------------------------------------------------------
    # Sentinel-2 matching
    # ------------------------------------------------------------------

    def merge_with_sentinel2(
        self,
        icesat2_points: pd.DataFrame,
        sentinel2_data: pd.DataFrame,
        max_distance_m: float = 15.0,
    ) -> pd.DataFrame:
        """
        Match ICESat-2 depth points to nearest Sentinel-2 pixels.

        For each ICESat-2 point, find the Sentinel-2 pixel within
        max_distance_m and create a training sample with spectral
        features + depth label.

        Parameters
        ----------
        icesat2_points : pd.DataFrame
            Must have columns: lat, lon, depth_m.
        sentinel2_data : pd.DataFrame
            Must have columns: lat, lon, and spectral band columns
            (e.g., B2, B3, B4, B8).
        max_distance_m : float
            Maximum matching distance in meters (default 15m for
            10m Sentinel-2 pixel scale).

        Returns
        -------
        pd.DataFrame
            Merged training samples with spectral + depth columns.
        """
        if icesat2_points.empty or sentinel2_data.empty:
            logger.warning("Empty input to merge_with_sentinel2")
            return pd.DataFrame()

        # Build KD-tree from Sentinel-2 pixel coordinates
        # Approximate degrees-to-meters at mid-latitude (~46N for MN)
        lat_mid = icesat2_points["lat"].mean()
        m_per_deg_lat = 111_320.0
        m_per_deg_lon = 111_320.0 * np.cos(np.radians(lat_mid))

        s2_coords = np.column_stack(
            [
                sentinel2_data["lon"].values * m_per_deg_lon,
                sentinel2_data["lat"].values * m_per_deg_lat,
            ]
        )
        tree = cKDTree(s2_coords)

        is2_coords = np.column_stack(
            [
                icesat2_points["lon"].values * m_per_deg_lon,
                icesat2_points["lat"].values * m_per_deg_lat,
            ]
        )

        distances, indices = tree.query(is2_coords, k=1)

        # Filter to matches within max_distance_m
        mask = distances <= max_distance_m
        matched_is2 = icesat2_points.iloc[mask].reset_index(drop=True)
        matched_s2 = sentinel2_data.iloc[indices[mask]].reset_index(drop=True)

        # Combine: depth from ICESat-2, spectral from Sentinel-2
        spectral_cols = [c for c in matched_s2.columns if c not in ("lat", "lon")]
        result = matched_is2.copy()
        for col in spectral_cols:
            result[col] = matched_s2[col].values

        result["match_distance_m"] = distances[mask]

        logger.info(
            "Matched %d / %d ICESat-2 points to Sentinel-2 pixels (%.1f%%)",
            len(result),
            len(icesat2_points),
            100 * len(result) / max(len(icesat2_points), 1),
        )
        return result


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Fetch ICESat-2 ATL24 bathymetry for inland lakes"
    )
    parser.add_argument(
        "--lakes",
        required=True,
        help="Path to lake polygons (GeoJSON, GeoParquet, or Shapefile)",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output directory for per-lake parquet files",
    )
    parser.add_argument(
        "--start-date",
        default="2018-10-01",
        help="Start date for granule search (default: 2018-10-01, ICESat-2 launch)",
    )
    parser.add_argument(
        "--end-date",
        default="2026-03-25",
        help="End date for granule search (default: 2026-03-25)",
    )
    parser.add_argument(
        "--max-lakes",
        type=int,
        default=None,
        help="Maximum number of lakes to process",
    )
    parser.add_argument(
        "--min-confidence",
        type=int,
        default=3,
        choices=[0, 1, 2, 3, 4],
        help="Minimum ATL24 confidence level (0-4, default: 3)",
    )
    parser.add_argument(
        "--earthdata-token",
        default=None,
        help="NASA Earthdata bearer token (or set EARTHDATA_TOKEN env var)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable debug logging"
    )
    return parser.parse_args(argv)


def load_lakes(path: str) -> gpd.GeoDataFrame:
    """Load lake polygons from GeoJSON, GeoParquet, or Shapefile."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Lakes file not found: {path}")

    suffix = path.suffix.lower()
    if suffix in (".parquet", ".geoparquet"):
        gdf = gpd.read_parquet(path)
    elif suffix in (".geojson", ".json"):
        gdf = gpd.read_file(path)
    elif suffix == ".shp":
        gdf = gpd.read_file(path)
    else:
        raise ValueError(f"Unsupported file format: {suffix}")

    if gdf.crs and gdf.crs.to_epsg() != 4326:
        logger.info("Reprojecting lakes from %s to EPSG:4326", gdf.crs)
        gdf = gdf.to_crs(epsg=4326)

    logger.info("Loaded %d lake polygons from %s", len(gdf), path)
    return gdf


def main(argv=None):
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    logger.info("ICESat-2 ATL24 bathymetry fetcher")
    logger.info("  Lakes: %s", args.lakes)
    logger.info("  Output: %s", args.output)
    logger.info("  Date range: %s to %s", args.start_date, args.end_date)
    logger.info("  Min confidence: %d", args.min_confidence)

    lakes_gdf = load_lakes(args.lakes)

    fetcher = ICESat2ATL24Fetcher(
        earthdata_token=args.earthdata_token,
        min_confidence=args.min_confidence,
    )

    result = fetcher.fetch_for_lakes(
        lakes_gdf=lakes_gdf,
        output_dir=args.output,
        max_lakes=args.max_lakes,
        start_date=args.start_date,
        end_date=args.end_date,
    )

    if not result.empty:
        logger.info("Summary:")
        logger.info("  Total points: %d", len(result))
        logger.info("  Lakes with data: %d", result["lake_id"].nunique())
        logger.info("  Depth range: %.2f – %.2f m", result["depth_m"].min(), result["depth_m"].max())
        logger.info("  Mean depth: %.2f m", result["depth_m"].mean())
    else:
        logger.warning("No bathymetry data retrieved")

    return result


if __name__ == "__main__":
    main()
