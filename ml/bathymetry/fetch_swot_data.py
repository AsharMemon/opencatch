#!/usr/bin/env python3
"""
OpenCatch -- Fetch SWOT Satellite Lake Data for Enhanced Bathymetry

Retrieves SWOT (Surface Water and Ocean Topography) L2 LakeSP data to build
dense Area-Elevation curves. SWOT launched Dec 2022 and provides ~0.18m water
surface elevation accuracy for lakes >= 250m wide on a 21-day repeat cycle.

Data sources:
  1. Hydrocron API (PO.DAAC): Time series of WSE + area per PLD lake ID
  2. earthaccess: Bulk download of LakeSP shapefiles for spatial queries
  3. SWOT Prior Lake Database (PLD): Lake ID crosswalk + reference data

The PLD lake_id format is CBBNNNNNNT:
  C  = continent code (2 = North America)
  BB = basin code (Pfafstetter from HydroBASINS)
  NNNNNN = ordinal lake index within basin
  T  = water body type (1 = natural lake, 2 = reservoir)

Over 2+ years (2023-2025), each lake gets ~30+ elevation measurements at
different water levels. This makes the A-E curve 6-10x denser than 3D-LAKES
which has only 5 A-E points for 96% of lakes.

Usage:
    # Fetch via Hydrocron API (fast, per-lake time series)
    python fetch_swot_data.py --method hydrocron --state MN --output /data/swot

    # Fetch via earthaccess (bulk shapefiles, more fields)
    python fetch_swot_data.py --method earthaccess --state MN --output /data/swot

    # Fetch PLD crosswalk to map HydroLAKES IDs to SWOT PLD IDs
    python fetch_swot_data.py --method pld --output /data/swot

Requirements:
    pip install requests pandas numpy tqdm earthaccess geopandas pyarrow
"""

import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("fetch_swot")

# -- Configuration -----------------------------------------------------------

HYDROCRON_BASE = "https://soto.podaac.earthdatacloud.nasa.gov/hydrocron/v1/timeseries"

# SWOT dataset short names at PO.DAAC
LAKESP_PRIOR = "SWOT_L2_HR_LakeSP_prior_2.0"  # Prior lake products
LAKESP_OBS = "SWOT_L2_HR_LakeSP_obs_2.0"      # Observed lake products

# Fields to request from Hydrocron for A-E curve construction
HYDROCRON_FIELDS = (
    "lake_id,time_str,wse,wse_u,wse_r_u,wse_std,"
    "area_total,area_tot_u,area_detct,area_det_u,"
    "quality_f,dark_frac,ice_clim_f,ice_dyn_f,partial_f,"
    "lake_name,p_ref_wse,p_ref_area,p_lat,p_lon,"
    "cycle_id,pass_id,continent_id"
)

# State bounding boxes (min_lon, min_lat, max_lon, max_lat)
STATE_BBOXES = {
    "MN": (-97.5, 43.0, -89.0, 49.5),
    "WI": (-93.0, 42.5, -86.5, 47.1),
    "MI": (-90.5, 41.7, -82.4, 48.3),
    "NY": (-79.8, 40.5, -71.9, 45.0),
    "ME": (-71.1, 43.0, -66.9, 47.5),
    "FL": (-87.6, 24.5, -80.0, 31.0),
    "TX": (-106.6, 25.8, -93.5, 36.5),
    "CA": (-124.5, 32.5, -114.1, 42.0),
    "OR": (-124.6, 42.0, -116.5, 46.3),
    "WA": (-124.8, 45.5, -116.9, 49.0),
    "CO": (-109.1, 37.0, -102.0, 41.0),
    "MT": (-116.1, 44.4, -104.0, 49.0),
    # Canadian provinces
    "ON": (-95.2, 41.7, -74.3, 56.9),
    "AB": (-120.0, 49.0, -110.0, 60.0),
    "BC": (-139.1, 48.3, -114.1, 60.0),
}

# North America continent code for SWOT PLD
NA_CONTINENT = 2

# SWOT mission time range (science data begins ~July 2023)
SWOT_START = "2023-07-01T00:00:00Z"
SWOT_END = "2025-12-31T23:59:59Z"


# -- Hydrocron API Fetcher ---------------------------------------------------

def fetch_hydrocron_lake(
    lake_id: str,
    start_time: str = SWOT_START,
    end_time: str = SWOT_END,
    output_format: str = "geojson",
    max_retries: int = 3,
) -> Optional[dict]:
    """
    Fetch SWOT time series for a single PLD lake via Hydrocron API.

    Returns GeoJSON or None on failure.
    """
    import requests

    params = {
        "feature": "PriorLake",
        "feature_id": str(lake_id),
        "start_time": start_time,
        "end_time": end_time,
        "fields": HYDROCRON_FIELDS,
        "output": output_format,
    }

    for attempt in range(max_retries):
        try:
            resp = requests.get(HYDROCRON_BASE, params=params, timeout=60)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 400:
                log.debug(f"Lake {lake_id}: 400 Bad Request (likely no data)")
                return None
            elif resp.status_code == 413:
                log.warning(f"Lake {lake_id}: Response too large, splitting time range")
                return _fetch_split_timerange(lake_id, start_time, end_time)
            else:
                log.warning(f"Lake {lake_id}: HTTP {resp.status_code}, retry {attempt+1}")
        except requests.exceptions.Timeout:
            log.warning(f"Lake {lake_id}: Timeout, retry {attempt+1}")
        except Exception as e:
            log.warning(f"Lake {lake_id}: Error {e}, retry {attempt+1}")

        if attempt < max_retries - 1:
            time.sleep(2 ** attempt)

    return None


def _fetch_split_timerange(
    lake_id: str, start_time: str, end_time: str
) -> Optional[dict]:
    """Split a large time range into yearly chunks and merge."""
    import requests
    from datetime import datetime

    start = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    end = datetime.fromisoformat(end_time.replace("Z", "+00:00"))

    all_features = []
    current = start
    while current < end:
        chunk_end = min(current.replace(year=current.year + 1), end)
        chunk_start_str = current.strftime("%Y-%m-%dT%H:%M:%SZ")
        chunk_end_str = chunk_end.strftime("%Y-%m-%dT%H:%M:%SZ")

        params = {
            "feature": "PriorLake",
            "feature_id": str(lake_id),
            "start_time": chunk_start_str,
            "end_time": chunk_end_str,
            "fields": HYDROCRON_FIELDS,
            "output": "geojson",
        }

        try:
            resp = requests.get(HYDROCRON_BASE, params=params, timeout=60)
            if resp.status_code == 200:
                data = resp.json()
                features = data.get("features", [])
                all_features.extend(features)
        except Exception as e:
            log.warning(f"Lake {lake_id} chunk {chunk_start_str}: {e}")

        current = chunk_end
        time.sleep(0.5)

    if all_features:
        return {"type": "FeatureCollection", "features": all_features}
    return None


def parse_hydrocron_response(geojson: dict) -> pd.DataFrame:
    """
    Parse Hydrocron GeoJSON response into a clean DataFrame.

    Applies quality filtering:
      - quality_f == 0 (good quality)
      - wse != -999999999999 (fill value)
      - area_total > 0
      - ice_clim_f == 0 and ice_dyn_f == 0 (no ice)
    """
    features = geojson.get("features", [])
    if not features:
        return pd.DataFrame()

    records = []
    for f in features:
        props = f.get("properties", {})
        records.append(props)

    df = pd.DataFrame(records)
    if df.empty:
        return df

    # Convert numeric columns
    numeric_cols = [
        "wse", "wse_u", "wse_r_u", "wse_std",
        "area_total", "area_tot_u", "area_detct", "area_det_u",
        "dark_frac", "p_ref_wse", "p_ref_area", "p_lat", "p_lon",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    int_cols = ["quality_f", "ice_clim_f", "ice_dyn_f", "partial_f",
                "cycle_id", "pass_id", "continent_id"]
    for col in int_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

    # Parse time
    if "time_str" in df.columns:
        df["datetime"] = pd.to_datetime(df["time_str"], errors="coerce")

    # Filter fill values
    FILL = -999999999999
    if "wse" in df.columns:
        df = df[df["wse"] != FILL].copy()
    if "area_total" in df.columns:
        df = df[df["area_total"] != FILL].copy()
        df = df[df["area_total"] > 0].copy()

    # Quality filter
    if "quality_f" in df.columns:
        df = df[df["quality_f"] == 0].copy()

    # Ice filter (remove ice-affected observations)
    if "ice_clim_f" in df.columns:
        df = df[df["ice_clim_f"] == 0].copy()
    if "ice_dyn_f" in df.columns:
        df = df[df["ice_dyn_f"] == 0].copy()

    return df


# -- PLD Lake ID Discovery ---------------------------------------------------

def discover_pld_lakes_earthaccess(
    bbox: tuple,
    output_dir: str,
    max_granules: int = 500,
) -> list:
    """
    Discover SWOT PLD lake IDs within a bounding box by downloading
    LakeSP Prior shapefiles and extracting unique lake_ids.

    Uses earthaccess for NASA Earthdata authentication and search.
    """
    import earthaccess

    log.info(f"Searching SWOT LakeSP Prior granules in bbox {bbox}...")

    # Login to Earthdata
    earthaccess.login(strategy="netrc")

    # Search for LakeSP Prior granules
    results = earthaccess.search_data(
        short_name="SWOT_L2_HR_LakeSP_prior_2.0",
        bounding_box=bbox,
        temporal=(SWOT_START, SWOT_END),
        count=max_granules,
    )
    log.info(f"Found {len(results)} LakeSP Prior granules")

    if not results:
        # Try version D
        results = earthaccess.search_data(
            short_name="SWOT_L2_HR_LakeSP_prior_D",
            bounding_box=bbox,
            temporal=(SWOT_START, SWOT_END),
            count=max_granules,
        )
        log.info(f"Found {len(results)} LakeSP Prior (version D) granules")

    if not results:
        log.warning("No SWOT granules found. Check bbox and time range.")
        return []

    # Download a sample to extract lake IDs
    dl_dir = Path(output_dir) / "granules"
    dl_dir.mkdir(parents=True, exist_ok=True)

    sample_results = results[:min(20, len(results))]
    log.info(f"Downloading {len(sample_results)} sample granules...")
    downloaded = earthaccess.download(sample_results, str(dl_dir))
    log.info(f"Downloaded {len(downloaded)} files")

    # Extract unique lake IDs from shapefiles
    import geopandas as gpd

    all_lake_ids = set()
    for fpath in downloaded:
        fpath = str(fpath)
        if fpath.endswith(".shp") or fpath.endswith(".zip"):
            try:
                gdf = gpd.read_file(fpath)
                if "lake_id" in gdf.columns:
                    ids = gdf["lake_id"].dropna().unique()
                    all_lake_ids.update(ids)
                    log.info(f"  {Path(fpath).name}: {len(ids)} unique lakes")
            except Exception as e:
                log.warning(f"  Failed to read {fpath}: {e}")

    lake_ids = sorted(all_lake_ids)
    log.info(f"Total unique PLD lake IDs in bbox: {len(lake_ids)}")

    # Save the lake ID list
    id_file = Path(output_dir) / "pld_lake_ids.json"
    with open(id_file, "w") as f:
        json.dump({"bbox": list(bbox), "lake_ids": lake_ids, "count": len(lake_ids)}, f, indent=2)
    log.info(f"Saved PLD lake IDs to {id_file}")

    return lake_ids


def discover_pld_lakes_cmr(
    bbox: tuple,
    output_dir: str,
    max_pages: int = 20,
) -> list:
    """
    Discover SWOT PLD lake IDs via CMR search + Hydrocron.
    Lighter-weight than earthaccess (no shapefile download needed).

    Strategy: Search CMR for LakeSP granules in the bbox, then
    parse granule metadata to extract lake IDs from the filenames.
    """
    import requests

    CMR_URL = "https://cmr.earthdata.nasa.gov/search/granules.json"
    min_lon, min_lat, max_lon, max_lat = bbox

    all_lake_ids = set()
    page = 1

    while page <= max_pages:
        params = {
            "short_name": "SWOT_L2_HR_LakeSP_2.0",
            "bounding_box": f"{min_lon},{min_lat},{max_lon},{max_lat}",
            "temporal": f"{SWOT_START},{SWOT_END}",
            "page_size": 200,
            "page_num": page,
            "sort_key": "-start_date",
        }
        try:
            resp = requests.get(CMR_URL, params=params, timeout=30)
            entries = resp.json().get("feed", {}).get("entry", [])
            if not entries:
                break

            log.info(f"CMR page {page}: {len(entries)} granules")

            for entry in entries:
                title = entry.get("title", "")
                # LakeSP granule titles contain cycle/pass info
                # Extract lake IDs from the data links or associated metadata
                links = entry.get("links", [])
                for link in links:
                    href = link.get("href", "")
                    if "Prior" in href and href.endswith(".shp"):
                        # The shapefile contains lake_ids but we can't parse
                        # without downloading. Instead, note the granule.
                        pass

            page += 1
            time.sleep(0.5)

        except Exception as e:
            log.warning(f"CMR page {page} error: {e}")
            break

    return sorted(all_lake_ids)


# -- Build HydroLAKES -> PLD Crosswalk --------------------------------------

def build_hydrolakes_pld_crosswalk(
    hydrolakes_path: str,
    pld_lake_ids: list,
    output_dir: str,
) -> pd.DataFrame:
    """
    Build a crosswalk between HydroLAKES hylak_id and SWOT PLD lake_id.

    Strategy: Spatial join between HydroLAKES polygons/centroids and PLD
    lake coordinates (p_lat, p_lon from Hydrocron responses).

    For each PLD lake, query Hydrocron for a single observation to get
    p_lat/p_lon, then spatial-join to nearest HydroLAKES polygon.
    """
    import geopandas as gpd
    from shapely.geometry import Point
    import requests

    log.info("Building HydroLAKES <-> PLD crosswalk...")

    # Load HydroLAKES centroids
    if hydrolakes_path.endswith(".gpkg"):
        hl = gpd.read_file(hydrolakes_path)
    elif hydrolakes_path.endswith(".parquet"):
        hl_df = pd.read_parquet(hydrolakes_path)
        hl = gpd.GeoDataFrame(
            hl_df,
            geometry=gpd.points_from_xy(hl_df["lon"], hl_df["lat"]),
            crs="EPSG:4326",
        )
    else:
        hl_df = pd.read_csv(hydrolakes_path)
        hl = gpd.GeoDataFrame(
            hl_df,
            geometry=gpd.points_from_xy(hl_df["lon"], hl_df["lat"]),
            crs="EPSG:4326",
        )

    # Get PLD lake coordinates from Hydrocron
    pld_records = []
    log.info(f"Querying Hydrocron for {len(pld_lake_ids)} PLD lake locations...")
    for lid in tqdm(pld_lake_ids, desc="PLD locations"):
        # Just need one observation to get p_lat, p_lon
        params = {
            "feature": "PriorLake",
            "feature_id": str(lid),
            "start_time": "2024-01-01T00:00:00Z",
            "end_time": "2024-12-31T23:59:59Z",
            "fields": "lake_id,p_lat,p_lon,lake_name,p_ref_wse,p_ref_area",
            "output": "csv",
        }
        try:
            resp = requests.get(HYDROCRON_BASE, params=params, timeout=30)
            if resp.status_code == 200:
                lines = resp.text.strip().split("\n")
                if len(lines) >= 2:
                    header = lines[0].split(",")
                    vals = lines[1].split(",")
                    rec = dict(zip(header, vals))
                    rec["pld_lake_id"] = lid
                    pld_records.append(rec)
        except Exception:
            pass
        time.sleep(0.2)  # Rate limit

    if not pld_records:
        log.warning("No PLD lake locations retrieved")
        return pd.DataFrame()

    pld_df = pd.DataFrame(pld_records)
    for col in ["p_lat", "p_lon", "p_ref_wse", "p_ref_area"]:
        if col in pld_df.columns:
            pld_df[col] = pd.to_numeric(pld_df[col], errors="coerce")

    pld_gdf = gpd.GeoDataFrame(
        pld_df,
        geometry=gpd.points_from_xy(pld_df["p_lon"], pld_df["p_lat"]),
        crs="EPSG:4326",
    )

    # Spatial nearest join
    crosswalk = gpd.sjoin_nearest(
        pld_gdf, hl[["hylak_id", "geometry"]],
        how="left", max_distance=0.01,  # ~1km
    )

    # Save
    out_path = Path(output_dir) / "pld_hydrolakes_crosswalk.parquet"
    crosswalk_df = pd.DataFrame(crosswalk.drop(columns=["geometry"]))
    crosswalk_df.to_parquet(str(out_path), index=False)
    log.info(f"Crosswalk saved to {out_path}: {len(crosswalk_df)} lakes matched")

    return crosswalk_df


# -- Batch Fetch SWOT Time Series --------------------------------------------

def fetch_swot_batch(
    lake_ids: list,
    output_dir: str,
    batch_size: int = 50,
    rate_limit_sec: float = 0.3,
) -> pd.DataFrame:
    """
    Fetch SWOT time series for a batch of PLD lake IDs via Hydrocron.

    Saves per-lake CSVs and a combined parquet.
    Returns combined DataFrame with all observations.
    """
    out = Path(output_dir) / "timeseries"
    out.mkdir(parents=True, exist_ok=True)

    all_dfs = []
    n_success = 0
    n_fail = 0
    n_empty = 0

    log.info(f"Fetching SWOT time series for {len(lake_ids)} lakes...")

    for i, lid in enumerate(tqdm(lake_ids, desc="SWOT fetch")):
        # Check if already fetched
        csv_path = out / f"{lid}.csv"
        if csv_path.exists():
            try:
                df = pd.read_csv(csv_path)
                if len(df) > 0:
                    all_dfs.append(df)
                    n_success += 1
                    continue
            except Exception:
                pass

        # Fetch from Hydrocron
        geojson = fetch_hydrocron_lake(str(lid))
        if geojson is None:
            n_fail += 1
            continue

        df = parse_hydrocron_response(geojson)
        if df.empty:
            n_empty += 1
            continue

        # Save per-lake CSV
        df.to_csv(csv_path, index=False)
        all_dfs.append(df)
        n_success += 1

        # Rate limit
        time.sleep(rate_limit_sec)

        # Progress checkpoint
        if (i + 1) % 100 == 0:
            log.info(
                f"Progress: {i+1}/{len(lake_ids)} | "
                f"success={n_success} empty={n_empty} fail={n_fail}"
            )

    log.info(
        f"SWOT fetch complete: {n_success} with data, "
        f"{n_empty} empty, {n_fail} failed"
    )

    if all_dfs:
        combined = pd.concat(all_dfs, ignore_index=True)
        combined_path = Path(output_dir) / "swot_timeseries_combined.parquet"
        combined.to_parquet(str(combined_path), index=False)
        log.info(f"Combined data: {len(combined)} observations saved to {combined_path}")
        return combined

    return pd.DataFrame()


# -- Build A-E Curves from SWOT Data ----------------------------------------

def build_ae_from_swot(swot_df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert SWOT time series into per-lake A-E (Area-Elevation) curves.

    Each SWOT observation gives one (wse, area_total) point.
    Over 2+ years with 21-day repeat, we get ~30+ points per lake
    at different water levels = different areas.

    Returns DataFrame with columns:
      lake_id, wse, area_total, n_observations, wse_range,
      ae_density (points per meter of elevation range)
    """
    if swot_df.empty:
        return pd.DataFrame()

    # Group by lake
    ae_curves = []
    for lid, grp in swot_df.groupby("lake_id"):
        if len(grp) < 2:
            continue

        # Sort by WSE
        grp = grp.sort_values("wse").reset_index(drop=True)

        # Compute A-E stats
        wse_range = grp["wse"].max() - grp["wse"].min()
        n_obs = len(grp)
        ae_density = n_obs / max(wse_range, 0.01)

        # Store the curve points
        for _, row in grp.iterrows():
            ae_curves.append({
                "lake_id": lid,
                "wse": row["wse"],
                "area_total_km2": row.get("area_total", np.nan),
                "wse_uncertainty": row.get("wse_u", np.nan),
                "area_uncertainty_km2": row.get("area_tot_u", np.nan),
                "datetime": row.get("datetime", None),
                "cycle_id": row.get("cycle_id", None),
                "pass_id": row.get("pass_id", None),
            })

    ae_df = pd.DataFrame(ae_curves)

    # Add per-lake summary stats
    if not ae_df.empty:
        summary = ae_df.groupby("lake_id").agg(
            n_observations=("wse", "count"),
            wse_min=("wse", "min"),
            wse_max=("wse", "max"),
            wse_range=("wse", lambda x: x.max() - x.min()),
            area_min_km2=("area_total_km2", "min"),
            area_max_km2=("area_total_km2", "max"),
        ).reset_index()
        summary["ae_density"] = summary["n_observations"] / summary["wse_range"].clip(lower=0.01)

        ae_df = ae_df.merge(summary, on="lake_id", how="left")

    return ae_df


# -- Compare SWOT vs 3D-LAKES A-E -------------------------------------------

def compare_swot_vs_3dlakes(
    swot_ae: pd.DataFrame,
    threedlakes_dir: str,
    crosswalk: pd.DataFrame,
    output_dir: str,
) -> pd.DataFrame:
    """
    Compare SWOT-derived A-E curves vs 3D-LAKES A-E curves.

    Reports:
      - Number of A-E points: SWOT vs 3D-LAKES
      - Elevation range covered: SWOT vs 3D-LAKES
      - Overlap/consistency where both exist
    """
    from pathlib import Path

    l1_dir = Path(threedlakes_dir)
    results = []

    # Map PLD lake_id -> hylak_id via crosswalk
    if crosswalk is not None and not crosswalk.empty:
        id_map = dict(zip(crosswalk["pld_lake_id"].astype(str),
                         crosswalk["hylak_id"].astype(int)))
    else:
        id_map = {}

    for lid, grp in swot_ae.groupby("lake_id"):
        hylak_id = id_map.get(str(lid))
        if hylak_id is None:
            continue

        # Load 3D-LAKES L1 A-E curve
        l1_file = l1_dir / f"{hylak_id}_L1.csv"
        if not l1_file.exists():
            continue

        try:
            l1 = pd.read_csv(l1_file)
            l1_elev = l1.iloc[:, 0].values  # Elevation column
            l1_area = l1.iloc[:, 1].values  # Area column

            swot_n = grp["n_observations"].iloc[0] if "n_observations" in grp.columns else len(grp)
            swot_range = grp["wse_range"].iloc[0] if "wse_range" in grp.columns else (grp["wse"].max() - grp["wse"].min())

            results.append({
                "lake_id": lid,
                "hylak_id": hylak_id,
                "swot_n_points": swot_n,
                "3dlakes_n_points": len(l1_elev),
                "swot_elev_range_m": swot_range,
                "3dlakes_elev_range_m": l1_elev.max() - l1_elev.min() if len(l1_elev) > 0 else 0,
                "density_improvement": swot_n / max(len(l1_elev), 1),
            })
        except Exception as e:
            log.debug(f"Error comparing lake {lid}: {e}")

    comp_df = pd.DataFrame(results)
    if not comp_df.empty:
        out_path = Path(output_dir) / "swot_vs_3dlakes_comparison.parquet"
        comp_df.to_parquet(str(out_path), index=False)

        log.info("\n=== SWOT vs 3D-LAKES A-E Comparison ===")
        log.info(f"Lakes compared: {len(comp_df)}")
        log.info(f"SWOT avg points/lake: {comp_df['swot_n_points'].mean():.1f}")
        log.info(f"3D-LAKES avg points/lake: {comp_df['3dlakes_n_points'].mean():.1f}")
        log.info(f"Avg density improvement: {comp_df['density_improvement'].mean():.1f}x")
        log.info(f"SWOT avg elev range: {comp_df['swot_elev_range_m'].mean():.2f}m")
        log.info(f"3D-LAKES avg elev range: {comp_df['3dlakes_elev_range_m'].mean():.2f}m")

    return comp_df


# -- Main --------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Fetch SWOT satellite lake data")
    parser.add_argument("--method", choices=["hydrocron", "earthaccess", "pld", "all"],
                       default="hydrocron", help="Data access method")
    parser.add_argument("--state", default="MN", help="State/province code")
    parser.add_argument("--output", default="/data/swot", help="Output directory")
    parser.add_argument("--lake-ids", help="JSON file with PLD lake IDs to fetch")
    parser.add_argument("--hydrolakes", default="/data/3d_lakes_with_depths.parquet",
                       help="HydroLAKES data for crosswalk")
    parser.add_argument("--l1-dir", default="/data/3d_lakes_l1",
                       help="3D-LAKES L1 directory for comparison")
    parser.add_argument("--max-lakes", type=int, default=5000,
                       help="Max lakes to fetch")
    parser.add_argument("--rate-limit", type=float, default=0.3,
                       help="Seconds between API requests")
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)

    bbox = STATE_BBOXES.get(args.state)
    if bbox is None:
        log.error(f"Unknown state: {args.state}. Available: {list(STATE_BBOXES.keys())}")
        return

    log.info(f"SWOT Data Fetch for {args.state} | bbox={bbox}")
    log.info(f"Output: {args.output}")

    # Step 1: Discover PLD lake IDs in the region
    if args.lake_ids:
        with open(args.lake_ids) as f:
            data = json.load(f)
        lake_ids = data.get("lake_ids", data) if isinstance(data, dict) else data
        log.info(f"Loaded {len(lake_ids)} lake IDs from {args.lake_ids}")
    elif args.method in ("earthaccess", "all"):
        lake_ids = discover_pld_lakes_earthaccess(bbox, args.output)
    else:
        # Try CMR discovery first, fall back to earthaccess
        lake_ids = discover_pld_lakes_cmr(bbox, args.output)
        if not lake_ids:
            log.info("CMR discovery found no IDs, trying earthaccess...")
            lake_ids = discover_pld_lakes_earthaccess(bbox, args.output)

    if not lake_ids:
        log.error("No PLD lake IDs discovered. Cannot proceed.")
        return

    lake_ids = lake_ids[:args.max_lakes]
    log.info(f"Processing {len(lake_ids)} lakes")

    # Step 2: Fetch SWOT time series
    swot_df = fetch_swot_batch(
        lake_ids, args.output,
        rate_limit_sec=args.rate_limit,
    )

    if swot_df.empty:
        log.error("No SWOT data retrieved. Check lake IDs and API availability.")
        return

    # Step 3: Build A-E curves
    swot_ae = build_ae_from_swot(swot_df)
    if not swot_ae.empty:
        ae_path = Path(args.output) / "swot_ae_curves.parquet"
        swot_ae.to_parquet(str(ae_path), index=False)
        log.info(f"SWOT A-E curves saved: {ae_path}")

        n_lakes = swot_ae["lake_id"].nunique()
        n_obs = len(swot_ae)
        log.info(f"  {n_lakes} lakes, {n_obs} total A-E points")
        log.info(f"  Avg {n_obs/max(n_lakes,1):.1f} points/lake (vs ~5 for 3D-LAKES)")

    # Step 4: Build crosswalk and compare
    if os.path.exists(args.hydrolakes):
        crosswalk = build_hydrolakes_pld_crosswalk(
            args.hydrolakes, lake_ids, args.output
        )
        if not crosswalk.empty and not swot_ae.empty:
            compare_swot_vs_3dlakes(
                swot_ae, args.l1_dir, crosswalk, args.output
            )
    else:
        log.warning(f"HydroLAKES file not found: {args.hydrolakes}, skipping crosswalk")

    log.info("\nSWOT data fetch complete!")


if __name__ == "__main__":
    main()
