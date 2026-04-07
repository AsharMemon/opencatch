#!/usr/bin/env python3
"""
OpenCatch -- Fetch NHDPlus Flowlines for Reservoir Thalweg Tracing

Extracts the river channels (flowlines) that were flooded to create each
reservoir. The main thalweg (longest channel) defines the longitudinal
axis along which cross-sections are extracted for terrain extrapolation.

Data sources:
  1. USGS NLDI API: Navigate upstream/downstream from a point to get flowlines
  2. NHDPlus HR GeoPackages: Bulk download of flowlines by HUC4

For each reservoir, we:
  1. Find the flowline(s) that intersect the reservoir polygon
  2. Identify the main thalweg (longest segment) vs tributaries
  3. Compute channel geometry metrics (length, sinuosity, gradient, stream order)
  4. Output the flowline geometries for cross-section extraction

Usage:
    # Fetch flowlines for reservoirs in the NID catalog
    python fetch_nhdplus_flowlines.py \\
        --nid-catalog /data/reservoir/nid_reservoir_catalog.parquet \\
        --output /data/reservoir/flowlines

    # Fetch for a single reservoir by NID ID
    python fetch_nhdplus_flowlines.py \\
        --nid-id CO00734 \\
        --output /data/reservoir/flowlines

Requirements:
    pip install requests pandas geopandas shapely pyarrow tqdm
"""

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import requests
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("fetch_flowlines")

# -- Configuration -----------------------------------------------------------

# USGS NLDI (Network Linked Data Index) API
NLDI_BASE = "https://labs.waterdata.usgs.gov/api/nldi/linked-data"

# NHDPlus HR bulk download
NHDPLUS_HR_BASE = "https://prd-tnm.s3.amazonaws.com/StagedProducts/Hydrography/NHDPlusHR/Beta/GDB"

# Rate limiting
REQUEST_DELAY_S = 0.5  # seconds between API calls
MAX_RETRIES = 3


def fetch_flowlines_nldi(
    latitude: float,
    longitude: float,
    upstream_km: float = 50.0,
    downstream_km: float = 5.0,
) -> Optional[dict]:
    """Fetch flowlines near a point using the NLDI API.

    The NLDI provides network navigation from any point on the NHD network.
    We find the nearest comid (NHD reach) to the dam location, then navigate
    upstream to get all flowlines that feed into the reservoir.

    Returns GeoJSON FeatureCollection or None on failure.
    """
    # Step 1: Find the nearest comid to the dam location
    comid_url = f"{NLDI_BASE}/comid/position?coords=POINT({longitude} {latitude})"

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(comid_url, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("features"):
                    comid = data["features"][0]["properties"].get("comid")
                    if comid:
                        break
            time.sleep(REQUEST_DELAY_S * (attempt + 1))
        except requests.exceptions.RequestException:
            time.sleep(REQUEST_DELAY_S * (attempt + 1))
    else:
        log.warning(f"Could not find comid near ({latitude}, {longitude})")
        return None

    # Step 2: Navigate upstream from the dam to get tributary network
    upstream_url = (
        f"{NLDI_BASE}/comid/{comid}/navigation/UT/flowlines"
        f"?distance={upstream_km}"
    )

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(upstream_url, timeout=60)
            if resp.status_code == 200:
                return resp.json()
            time.sleep(REQUEST_DELAY_S * (attempt + 1))
        except requests.exceptions.RequestException:
            time.sleep(REQUEST_DELAY_S * (attempt + 1))

    log.warning(f"Failed to fetch upstream flowlines for comid {comid}")
    return None


def process_flowlines_for_reservoir(
    geojson: dict,
    reservoir_polygon=None,
    dam_lat: float = 0,
    dam_lon: float = 0,
) -> dict:
    """Process raw NLDI flowlines into reservoir-specific thalweg data.

    Identifies the main thalweg, clips to reservoir extent, and computes
    channel geometry metrics.

    Returns dict with:
        - thalweg_geometry: shapely LineString of main channel
        - tributary_geometries: list of shapely LineStrings
        - metrics: dict of computed channel properties
    """
    try:
        from shapely.geometry import shape, Point, MultiLineString
        from shapely.ops import linemerge, nearest_points
    except ImportError:
        log.error("shapely required for flowline processing")
        return {}

    if not geojson or not geojson.get("features"):
        return {}

    # Parse all flowlines from GeoJSON
    flowlines = []
    for feat in geojson["features"]:
        geom = shape(feat["geometry"])
        props = feat.get("properties", {})
        flowlines.append({
            "geometry": geom,
            "comid": props.get("comid"),
            "reachcode": props.get("reachcode"),
            "lengthkm": props.get("lengthkm", geom.length * 111),  # rough deg->km
            "stream_order": props.get("streamorde", props.get("stream_order", 0)),
        })

    if not flowlines:
        return {}

    # Sort by length to identify main channel
    flowlines.sort(key=lambda f: f["lengthkm"], reverse=True)

    # Try to merge connected flowlines into continuous paths
    all_geoms = [f["geometry"] for f in flowlines]
    try:
        merged = linemerge(MultiLineString(all_geoms))
        if merged.geom_type == "LineString":
            thalweg = merged
        elif merged.geom_type == "MultiLineString":
            # Take the longest continuous line as thalweg
            thalweg = max(merged.geoms, key=lambda g: g.length)
        else:
            thalweg = flowlines[0]["geometry"]
    except Exception:
        thalweg = flowlines[0]["geometry"]

    # Clip flowlines to reservoir polygon if available
    if reservoir_polygon is not None:
        try:
            thalweg_clipped = thalweg.intersection(reservoir_polygon)
            if not thalweg_clipped.is_empty:
                thalweg = thalweg_clipped
        except Exception:
            pass  # keep unclipped

    # Compute channel metrics
    dam_point = Point(dam_lon, dam_lat)

    # Length in meters (approximate)
    thalweg_length_m = thalweg.length * 111_000  # rough deg->m at mid-latitudes

    # Sinuosity = channel length / straight-line distance
    if thalweg.geom_type == "LineString" and len(thalweg.coords) >= 2:
        start = Point(thalweg.coords[0])
        end = Point(thalweg.coords[-1])
        straight_dist = start.distance(end) * 111_000
        sinuosity = thalweg_length_m / max(straight_dist, 1.0)
    else:
        sinuosity = 1.0

    # Count tributaries (flowlines that aren't part of the main thalweg)
    n_tributaries = max(0, len(flowlines) - 1)

    # Max stream order
    max_stream_order = max((f["stream_order"] for f in flowlines if f["stream_order"]), default=0)

    # Separate tributary geometries
    tributary_geoms = [
        f["geometry"] for f in flowlines[1:]
        if f["geometry"] != thalweg
    ]

    return {
        "thalweg_geometry": thalweg,
        "tributary_geometries": tributary_geoms,
        "metrics": {
            "thalweg_length_m": thalweg_length_m,
            "sinuosity": sinuosity,
            "n_tributaries": n_tributaries,
            "n_flowlines_total": len(flowlines),
            "max_stream_order": max_stream_order,
        },
    }


def fetch_reservoir_flowlines(
    nid_catalog: pd.DataFrame,
    output_dir: Path,
    max_reservoirs: Optional[int] = None,
) -> pd.DataFrame:
    """Fetch and process flowlines for all reservoirs in the NID catalog.

    Returns DataFrame with one row per reservoir containing thalweg metrics.
    Saves flowline geometries as GeoJSON files in output_dir.
    """
    results = []

    if max_reservoirs:
        nid_catalog = nid_catalog.head(max_reservoirs)

    geojson_dir = output_dir / "geojson"
    geojson_dir.mkdir(parents=True, exist_ok=True)

    for idx, row in tqdm(nid_catalog.iterrows(), total=len(nid_catalog), desc="Fetching flowlines"):
        nid_id = row.get("nid_id", f"dam_{idx}")
        lat = row["latitude"]
        lon = row["longitude"]

        # Check cache
        cache_path = geojson_dir / f"{nid_id}_flowlines.json"
        if cache_path.exists():
            try:
                with open(cache_path) as f:
                    geojson = json.load(f)
            except Exception:
                geojson = None
        else:
            # Fetch from NLDI
            geojson = fetch_flowlines_nldi(lat, lon)
            if geojson:
                with open(cache_path, "w") as f:
                    json.dump(geojson, f)
            time.sleep(REQUEST_DELAY_S)

        if not geojson:
            results.append({"nid_id": nid_id, "thalweg_length_m": np.nan})
            continue

        # Process
        result = process_flowlines_for_reservoir(
            geojson, dam_lat=lat, dam_lon=lon,
        )

        if result and result.get("metrics"):
            metrics = result["metrics"]
            metrics["nid_id"] = nid_id
            results.append(metrics)

            # Save thalweg geometry as WKT for later use
            if result.get("thalweg_geometry"):
                metrics["thalweg_wkt"] = result["thalweg_geometry"].wkt
        else:
            results.append({"nid_id": nid_id, "thalweg_length_m": np.nan})

    df_results = pd.DataFrame(results)
    log.info(f"Processed flowlines for {len(df_results)} reservoirs, "
             f"{df_results['thalweg_length_m'].notna().sum()} successful")

    return df_results


def main():
    parser = argparse.ArgumentParser(
        description="Fetch NHDPlus flowlines for reservoir thalweg tracing"
    )
    parser.add_argument(
        "--nid-catalog", type=str,
        help="Path to NID reservoir catalog Parquet",
    )
    parser.add_argument(
        "--nid-id", type=str, default=None,
        help="Single NID ID to process (for testing)",
    )
    parser.add_argument(
        "--output", type=str, required=True,
        help="Output directory for flowline data",
    )
    parser.add_argument(
        "--max-reservoirs", type=int, default=None,
        help="Limit number of reservoirs to process (for testing)",
    )
    parser.add_argument(
        "--upstream-km", type=float, default=50.0,
        help="Maximum upstream distance to trace (km)",
    )
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.nid_id:
        # Single reservoir mode
        log.info(f"Fetching flowlines for NID {args.nid_id}...")
        # Need lat/lon -- try to get from catalog or use a lookup
        if args.nid_catalog:
            catalog = pd.read_parquet(args.nid_catalog)
            row = catalog[catalog["nid_id"] == args.nid_id]
            if len(row) == 0:
                log.error(f"NID ID {args.nid_id} not found in catalog")
                return
            lat, lon = row.iloc[0]["latitude"], row.iloc[0]["longitude"]
        else:
            log.error("Must provide --nid-catalog with --nid-id")
            return

        geojson = fetch_flowlines_nldi(lat, lon, upstream_km=args.upstream_km)
        if geojson:
            result = process_flowlines_for_reservoir(geojson, dam_lat=lat, dam_lon=lon)
            log.info(f"Thalweg metrics: {result.get('metrics', {})}")
            # Save
            out_path = output_dir / f"{args.nid_id}_flowlines.json"
            with open(out_path, "w") as f:
                json.dump(geojson, f)
            log.info(f"Saved to {out_path}")
        else:
            log.error("Failed to fetch flowlines")
        return

    # Batch mode
    if not args.nid_catalog:
        log.error("Must provide --nid-catalog for batch mode")
        return

    catalog = pd.read_parquet(args.nid_catalog)
    log.info(f"Loaded {len(catalog)} reservoirs from catalog")

    df_flowlines = fetch_reservoir_flowlines(
        catalog, output_dir, max_reservoirs=args.max_reservoirs,
    )

    # Save metrics
    out_path = output_dir / "reservoir_flowline_metrics.parquet"
    df_flowlines.to_parquet(out_path, index=False, engine="pyarrow")
    log.info(f"Saved flowline metrics to {out_path}")

    # Summary
    log.info("=== Flowline Summary ===")
    valid = df_flowlines["thalweg_length_m"].notna()
    log.info(f"  Success rate:     {valid.sum()}/{len(df_flowlines)} "
             f"({valid.mean() * 100:.1f}%)")
    if valid.any():
        log.info(f"  Thalweg length:   median={df_flowlines.loc[valid, 'thalweg_length_m'].median():.0f}m, "
                 f"max={df_flowlines.loc[valid, 'thalweg_length_m'].max():.0f}m")
        log.info(f"  Sinuosity:        median={df_flowlines.loc[valid, 'sinuosity'].median():.2f}")
        log.info(f"  Tributaries:      median={df_flowlines.loc[valid, 'n_tributaries'].median():.0f}")


if __name__ == "__main__":
    main()
