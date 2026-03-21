#!/usr/bin/env python3
"""
OpenCatch — ICESat-2 ATL13 Inland Water Depth Downloader

Downloads ICESat-2 ATL13 inland water surface height data and derives
sparse bathymetry labels for North American lakes.

Two approaches:
1. SlideRule Earth (preferred): Server-side ATL03 photon processing
   - Faster, handles photon classification server-side
   - Returns georeferenced depth points directly

2. NASA CMR API (fallback): Query ATL13 granules directly
   - Downloads HDF5 granules from NSIDC
   - Requires Earthdata login

ICESat-2 ATL13 provides:
- Water surface height (orthometric)
- Significant wave height
- Bottom return depths (in clear water)
- Along-track ~11m footprint, ~91m spacing

Usage:
    # Download via SlideRule (preferred)
    python fetch_icesat2.py \\
        --method sliderule \\
        --output /data/icesat2 \\
        --region north_america

    # Download via NASA CMR
    python fetch_icesat2.py \\
        --method cmr \\
        --output /data/icesat2 \\
        --earthdata-user YOUR_USER \\
        --earthdata-pass YOUR_PASS

Requirements:
    pip install sliderule geopandas pyarrow requests tqdm
"""

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Optional

import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("fetch_icesat2")

# North America bounding box
NA_BOUNDS = {
    "min_lat": 25.0,
    "max_lat": 70.0,
    "min_lon": -170.0,
    "max_lon": -50.0,
}

# HUC2 regions for partitioning (major US river basins)
HUC2_REGIONS = {
    "01": "New England",
    "02": "Mid-Atlantic",
    "03": "South Atlantic-Gulf",
    "04": "Great Lakes",
    "05": "Ohio",
    "06": "Tennessee",
    "07": "Upper Mississippi",
    "08": "Lower Mississippi",
    "09": "Souris-Red-Rainy",
    "10": "Missouri",
    "11": "Arkansas-White-Red",
    "12": "Texas-Gulf",
    "13": "Rio Grande",
    "14": "Upper Colorado",
    "15": "Lower Colorado",
    "16": "Great Basin",
    "17": "Pacific Northwest",
    "18": "California",
}

# Regional bounding boxes for chunked queries
QUERY_REGIONS = [
    {"name": "northeast",    "bbox": [-80, 40, -66, 48]},
    {"name": "southeast",    "bbox": [-90, 25, -75, 40]},
    {"name": "great_lakes",  "bbox": [-93, 41, -76, 49]},
    {"name": "upper_midwest","bbox": [-105, 43, -85, 50]},
    {"name": "northern_plains","bbox": [-115, 44, -96, 50]},
    {"name": "southern_plains","bbox": [-106, 28, -90, 40]},
    {"name": "rocky_mountain","bbox": [-117, 35, -102, 49]},
    {"name": "pacific_nw",   "bbox": [-125, 42, -110, 49]},
    {"name": "california",   "bbox": [-125, 32, -114, 42]},
    {"name": "alaska",       "bbox": [-170, 54, -130, 70]},
    {"name": "canada_east",  "bbox": [-80, 43, -52, 63]},
    {"name": "canada_central","bbox": [-105, 49, -80, 63]},
    {"name": "canada_west",  "bbox": [-140, 49, -105, 65]},
]


# ── SlideRule Approach ───────────────────────────────────────────────

def fetch_sliderule(
    output_dir: Path,
    regions: Optional[list[str]] = None,
    min_confidence: int = 3,
    max_depth_m: float = 50.0,
) -> list[Path]:
    """
    Fetch ICESat-2 inland water depths via SlideRule Earth.

    SlideRule processes raw ATL03 photon data server-side, applying
    custom classification and returning clean results.

    Args:
        output_dir: Output directory for GeoParquet files
        regions: Specific regions to query (None = all)
        min_confidence: Minimum photon signal confidence (1-4)
        max_depth_m: Maximum depth to consider valid

    Returns:
        List of saved GeoParquet file paths
    """
    try:
        from sliderule import sliderule, icesat2
    except ImportError:
        log.error("sliderule not installed. Run: pip install sliderule")
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    saved_files = []

    # Initialize SlideRule
    sliderule.init("slideruleearth.io", verbose=False)
    log.info("Connected to SlideRule Earth")

    # Select regions
    query_regions = QUERY_REGIONS
    if regions:
        query_regions = [r for r in QUERY_REGIONS if r["name"] in regions]

    for region in query_regions:
        name = region["name"]
        bbox = region["bbox"]
        output_path = output_dir / f"icesat2_{name}.parquet"

        if output_path.exists():
            log.info(f"Already downloaded: {name}")
            saved_files.append(output_path)
            continue

        log.info(f"Querying SlideRule for region: {name} (bbox={bbox})...")

        try:
            # Define polygon from bbox
            poly = [
                {"lon": bbox[0], "lat": bbox[1]},
                {"lon": bbox[2], "lat": bbox[1]},
                {"lon": bbox[2], "lat": bbox[3]},
                {"lon": bbox[0], "lat": bbox[3]},
                {"lon": bbox[0], "lat": bbox[1]},
            ]

            # ATL03 subsetting parameters for inland water
            params = {
                "poly": poly,
                "srt": icesat2.SRT_INLAND_WATER,  # Inland water surface type
                "cnf": min_confidence,
                "len": 40,    # Segment length in metres
                "res": 20,    # Resolution in metres
                "maxi": 5,    # Max iterations for surface finding
                "ats": 5.0,   # Along-track spread
                "cnt": 5,     # Minimum photon count per segment
                "t0": "2018-10-01",  # ICESat-2 launch date
                "t1": "2026-03-01",
            }

            # Run ATL06 surface finding on inland water
            gdf = icesat2.atl06p(params)

            if gdf is None or len(gdf) == 0:
                log.warning(f"No data returned for region {name}")
                continue

            log.info(f"  {name}: {len(gdf)} surface segments")

            # Process results
            gdf = _process_sliderule_results(gdf, max_depth_m)

            if len(gdf) == 0:
                log.warning(f"  No valid depth points after filtering for {name}")
                continue

            # Save as GeoParquet
            gdf.to_parquet(output_path, index=False)
            saved_files.append(output_path)
            log.info(f"  Saved {len(gdf)} points -> {output_path}")

        except Exception as e:
            log.error(f"SlideRule query failed for {name}: {e}")
            continue

        # Rate limiting
        time.sleep(2)

    log.info(f"Total files saved: {len(saved_files)}")
    return saved_files


def _process_sliderule_results(gdf, max_depth_m: float = 50.0):
    """
    Process SlideRule results to extract bathymetry-relevant data.

    Extracts:
    - Surface height (orthometric)
    - Bottom return depths (where detected)
    - Signal confidence
    - Along-track position
    """
    import geopandas as gpd

    # Expected columns from ATL06-SR
    keep_cols = []
    for col in ["geometry", "h_mean", "h_sigma", "spot", "rgt", "cycle",
                "segment_id", "n_fit_photons", "w_surface_window_final"]:
        if col in gdf.columns:
            keep_cols.append(col)

    if not keep_cols:
        log.warning("Unexpected SlideRule output columns, keeping all")
        keep_cols = list(gdf.columns)

    gdf = gdf[keep_cols].copy()

    # Add lat/lon from geometry
    gdf["lat"] = gdf.geometry.y
    gdf["lon"] = gdf.geometry.x

    # Filter outliers
    if "h_mean" in gdf.columns:
        # Remove segments with unreasonable heights (likely land or errors)
        gdf = gdf[gdf["h_mean"].between(-500, 5000)]

    if "h_sigma" in gdf.columns:
        # Remove noisy segments
        gdf = gdf[gdf["h_sigma"] < 1.0]

    if "n_fit_photons" in gdf.columns:
        # Keep only well-constrained segments
        gdf = gdf[gdf["n_fit_photons"] >= 5]

    # Reset index
    gdf = gdf.reset_index(drop=True)

    return gdf


# ── NASA CMR Approach (Fallback) ─────────────────────────────────────

def fetch_cmr(
    output_dir: Path,
    earthdata_user: Optional[str] = None,
    earthdata_pass: Optional[str] = None,
    max_granules: int = 1000,
) -> list[Path]:
    """
    Fetch ICESat-2 ATL13 data via NASA CMR API.

    Downloads HDF5 granules from NSIDC DAAC. Requires NASA Earthdata
    login credentials.

    Args:
        output_dir: Output directory
        earthdata_user: Earthdata username
        earthdata_pass: Earthdata password
        max_granules: Maximum number of granules to download

    Returns:
        List of downloaded file paths
    """
    import requests
    from tqdm import tqdm

    output_dir.mkdir(parents=True, exist_ok=True)
    saved_files = []

    # CMR search for ATL13 inland water
    cmr_url = "https://cmr.earthdata.nasa.gov/search/granules.json"

    # Query parameters
    params = {
        "short_name": "ATL13",
        "version": "006",
        "bounding_box": f"{NA_BOUNDS['min_lon']},{NA_BOUNDS['min_lat']},"
                        f"{NA_BOUNDS['max_lon']},{NA_BOUNDS['max_lat']}",
        "temporal": "2018-10-01,2026-03-01",
        "page_size": min(max_granules, 200),
        "page_num": 1,
    }

    log.info("Querying NASA CMR for ATL13 granules...")

    all_granules = []
    while len(all_granules) < max_granules:
        try:
            resp = requests.get(cmr_url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.error(f"CMR query failed: {e}")
            break

        entries = data.get("feed", {}).get("entry", [])
        if not entries:
            break

        all_granules.extend(entries)
        log.info(f"Found {len(all_granules)} granules so far...")

        if len(entries) < params["page_size"]:
            break
        params["page_num"] += 1
        time.sleep(0.5)

    log.info(f"Total ATL13 granules found: {len(all_granules)}")

    if not all_granules:
        return []

    # Setup Earthdata authentication
    if not earthdata_user or not earthdata_pass:
        # Try .netrc
        import netrc
        try:
            auth = netrc.netrc().authenticators("urs.earthdata.nasa.gov")
            if auth:
                earthdata_user, _, earthdata_pass = auth
                log.info("Using .netrc credentials for Earthdata")
        except Exception:
            pass

    if not earthdata_user:
        log.error("Earthdata credentials required. Set --earthdata-user/--earthdata-pass")
        log.info("Or add to ~/.netrc: machine urs.earthdata.nasa.gov login USER password PASS")
        return []

    session = requests.Session()
    session.auth = (earthdata_user, earthdata_pass)

    # Download granules
    granules_dir = output_dir / "granules"
    granules_dir.mkdir(parents=True, exist_ok=True)

    downloaded = []
    for entry in tqdm(all_granules[:max_granules], desc="Downloading ATL13"):
        granule_id = entry.get("id", "unknown")
        links = entry.get("links", [])

        # Find the data download link
        data_url = None
        for link in links:
            href = link.get("href", "")
            if href.endswith(".h5") and "data" in link.get("rel", ""):
                data_url = href
                break

        if not data_url:
            continue

        filename = data_url.split("/")[-1]
        dest = granules_dir / filename

        if dest.exists():
            downloaded.append(dest)
            continue

        try:
            resp = session.get(data_url, stream=True, timeout=60,
                             allow_redirects=True)
            resp.raise_for_status()

            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)

            downloaded.append(dest)
        except Exception as e:
            log.warning(f"Download failed for {filename}: {e}")
            continue

        time.sleep(0.2)  # Rate limiting

    log.info(f"Downloaded {len(downloaded)} ATL13 granules")

    # Process HDF5 files to extract depth data
    if downloaded:
        processed = _process_atl13_granules(downloaded, output_dir)
        saved_files.extend(processed)

    return saved_files


def _process_atl13_granules(
    granule_paths: list[Path],
    output_dir: Path,
) -> list[Path]:
    """
    Process ATL13 HDF5 granules to extract inland water depth data.

    Extracts:
    - Water surface height
    - Lake bottom height (where detected)
    - Depth = surface - bottom
    - Signal quality flags
    """
    try:
        import h5py
    except ImportError:
        log.error("h5py not installed. Run: pip install h5py")
        return []

    import geopandas as gpd
    from shapely.geometry import Point
    from tqdm import tqdm

    all_records = []

    for path in tqdm(granule_paths, desc="Processing ATL13"):
        try:
            with h5py.File(path, "r") as f:
                # ATL13 has data organized by ground track (gt1l, gt1r, etc.)
                for gt in ["gt1l", "gt1r", "gt2l", "gt2r", "gt3l", "gt3r"]:
                    group_path = f"{gt}/inland_water_body_data"

                    if group_path not in f:
                        continue

                    grp = f[group_path]

                    # Required fields
                    try:
                        lat = grp["latitude"][:]
                        lon = grp["longitude"][:]
                        ht_water_surf = grp["ht_water_surf"][:]
                    except KeyError:
                        continue

                    # Optional bottom detection
                    ht_bottom = None
                    if "bottom/ht_bottom" in grp:
                        ht_bottom = grp["bottom/ht_bottom"][:]
                    elif "ht_bckgrd" in grp:
                        ht_bottom = grp["ht_bckgrd"][:]

                    # Quality flags
                    quality = np.ones(len(lat), dtype=np.int32) * 3
                    if "qf_subsurf" in grp:
                        quality = grp["qf_subsurf"][:]
                    elif "water_body_id" in grp:
                        # At least we have a water body association
                        pass

                    # Extract depth where bottom is detected
                    for i in range(len(lat)):
                        record = {
                            "lat": float(lat[i]),
                            "lon": float(lon[i]),
                            "surface_ht": float(ht_water_surf[i]),
                            "quality": int(quality[i]) if i < len(quality) else 0,
                            "ground_track": gt,
                            "granule": path.stem,
                        }

                        if ht_bottom is not None and i < len(ht_bottom):
                            bottom = float(ht_bottom[i])
                            if np.isfinite(bottom) and bottom < ht_water_surf[i]:
                                depth = ht_water_surf[i] - bottom
                                if 0 < depth < 50:  # Reasonable inland depth
                                    record["depth_m"] = depth
                                    record["bottom_ht"] = bottom

                        all_records.append(record)

        except Exception as e:
            log.warning(f"Failed to process {path.name}: {e}")
            continue

    if not all_records:
        log.warning("No records extracted from ATL13 granules")
        return []

    log.info(f"Extracted {len(all_records)} total records")

    # Convert to GeoDataFrame
    import pandas as pd
    df = pd.DataFrame(all_records)
    geometry = [Point(lon, lat) for lon, lat in zip(df["lon"], df["lat"])]
    gdf = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")

    # Filter to valid entries
    gdf = gdf[gdf["lat"].between(NA_BOUNDS["min_lat"], NA_BOUNDS["max_lat"])]
    gdf = gdf[gdf["lon"].between(NA_BOUNDS["min_lon"], NA_BOUNDS["max_lon"])]

    # Separate depth-detected vs surface-only
    has_depth = gdf["depth_m"].notna() if "depth_m" in gdf.columns else gdf.index < 0
    n_with_depth = has_depth.sum()
    log.info(f"Records with bottom depth: {n_with_depth}/{len(gdf)}")

    # Save partitioned by rough region
    saved = []
    output_path = output_dir / "icesat2_atl13_all.parquet"
    gdf.to_parquet(output_path, index=False)
    saved.append(output_path)
    log.info(f"Saved {len(gdf)} records -> {output_path}")

    # Also save depth-only subset
    if n_with_depth > 0:
        depth_path = output_dir / "icesat2_atl13_depths.parquet"
        gdf[has_depth].to_parquet(depth_path, index=False)
        saved.append(depth_path)
        log.info(f"Saved {n_with_depth} depth records -> {depth_path}")

    return saved


# ── Lake Matching ────────────────────────────────────────────────────

def match_to_lakes(
    icesat2_dir: Path,
    lakes_path: Path,
    output_dir: Path,
    buffer_m: float = 50.0,
) -> Path:
    """
    Match ICESat-2 points to known lake polygons.

    Creates per-lake files with ICESat-2 depth transects that can be
    used as sparse training labels for the V2 model.

    Args:
        icesat2_dir: Directory with ICESat-2 parquet files
        lakes_path: Lake polygon GeoJSON/GeoPackage
        output_dir: Output directory for per-lake files
        buffer_m: Buffer around lake polygon in metres

    Returns:
        Path to summary CSV
    """
    import geopandas as gpd
    import pandas as pd
    from tqdm import tqdm

    output_dir.mkdir(parents=True, exist_ok=True)

    # Load lake polygons
    log.info(f"Loading lake polygons from {lakes_path}...")
    lakes = gpd.read_file(lakes_path)
    log.info(f"Loaded {len(lakes)} lake polygons")

    # Load all ICESat-2 data
    log.info("Loading ICESat-2 data...")
    icesat_files = list(Path(icesat2_dir).glob("*.parquet"))
    if not icesat_files:
        log.error(f"No parquet files found in {icesat2_dir}")
        return output_dir / "summary.csv"

    gdfs = []
    for f in icesat_files:
        try:
            gdf = gpd.read_parquet(f)
            gdfs.append(gdf)
        except Exception as e:
            log.warning(f"Failed to load {f}: {e}")

    if not gdfs:
        log.error("No ICESat-2 data loaded")
        return output_dir / "summary.csv"

    icesat = gpd.GeoDataFrame(pd.concat(gdfs, ignore_index=True))
    log.info(f"Total ICESat-2 points: {len(icesat)}")

    # Ensure same CRS
    if lakes.crs != icesat.crs:
        icesat = icesat.to_crs(lakes.crs)

    # Spatial join: match points to lakes
    log.info("Performing spatial join...")

    # Buffer lakes slightly for matching
    lakes_buffered = lakes.copy()
    lakes_buffered.geometry = lakes_buffered.geometry.buffer(buffer_m / 111000)  # Approx degrees

    matched = gpd.sjoin(icesat, lakes_buffered, how="inner", predicate="within")
    log.info(f"Matched {len(matched)} points to {matched['index_right'].nunique()} lakes")

    # Save per-lake files
    summary_records = []
    lake_id_col = "GNIS_Name" if "GNIS_Name" in lakes.columns else lakes.columns[0]

    for lake_idx, group in tqdm(matched.groupby("index_right"), desc="Saving per-lake"):
        if lake_idx >= len(lakes):
            continue

        lake_row = lakes.iloc[lake_idx]
        lake_name = str(lake_row.get(lake_id_col, f"lake_{lake_idx}"))
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in lake_name)

        lake_dir = output_dir / safe_name
        lake_dir.mkdir(parents=True, exist_ok=True)

        # Save ICESat-2 points for this lake
        group.to_parquet(lake_dir / "icesat2_points.parquet", index=False)

        # Summary
        n_depth = group["depth_m"].notna().sum() if "depth_m" in group.columns else 0
        summary_records.append({
            "lake_name": lake_name,
            "n_points": len(group),
            "n_with_depth": n_depth,
            "mean_depth_m": group["depth_m"].mean() if n_depth > 0 else np.nan,
            "max_depth_m": group["depth_m"].max() if n_depth > 0 else np.nan,
        })

    # Save summary
    summary_path = output_dir / "lake_summary.csv"
    summary_df = pd.DataFrame(summary_records)
    summary_df.to_csv(summary_path, index=False)
    log.info(f"Summary: {len(summary_records)} lakes with ICESat-2 data -> {summary_path}")

    return summary_path


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Download ICESat-2 ATL13 inland water depth data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # SlideRule (recommended, no credentials needed)
    python fetch_icesat2.py --method sliderule --output /data/icesat2

    # Specific regions only
    python fetch_icesat2.py --method sliderule --output /data/icesat2 \\
        --regions great_lakes upper_midwest

    # NASA CMR (requires Earthdata login)
    python fetch_icesat2.py --method cmr --output /data/icesat2 \\
        --earthdata-user USER --earthdata-pass PASS

    # Match to lake polygons
    python fetch_icesat2.py --match \\
        --icesat2-dir /data/icesat2 \\
        --lakes /data/waterbodies/us/all_us_waterbodies.geojson \\
        --output /data/training/v2/icesat2_labels
        """,
    )

    parser.add_argument("--method", choices=["sliderule", "cmr"],
                        default="sliderule",
                        help="Download method")
    parser.add_argument("--output", type=str, default="/data/icesat2",
                        help="Output directory")

    # SlideRule options
    parser.add_argument("--regions", nargs="+", default=None,
                        help="Specific regions to query (e.g., great_lakes upper_midwest)")
    parser.add_argument("--min-confidence", type=int, default=3,
                        help="Minimum photon signal confidence (1-4)")

    # CMR options
    parser.add_argument("--earthdata-user", type=str, default=None,
                        help="NASA Earthdata username")
    parser.add_argument("--earthdata-pass", type=str, default=None,
                        help="NASA Earthdata password")
    parser.add_argument("--max-granules", type=int, default=1000,
                        help="Maximum granules to download")

    # Lake matching
    parser.add_argument("--match", action="store_true",
                        help="Match ICESat-2 data to lake polygons")
    parser.add_argument("--icesat2-dir", type=str, default=None,
                        help="ICESat-2 data directory (for --match)")
    parser.add_argument("--lakes", type=str, default=None,
                        help="Lake polygons file (for --match)")

    args = parser.parse_args()

    if args.match:
        if not args.icesat2_dir or not args.lakes:
            parser.error("--match requires --icesat2-dir and --lakes")
        match_to_lakes(
            Path(args.icesat2_dir),
            Path(args.lakes),
            Path(args.output),
        )
    elif args.method == "sliderule":
        fetch_sliderule(
            Path(args.output),
            regions=args.regions,
            min_confidence=args.min_confidence,
        )
    elif args.method == "cmr":
        fetch_cmr(
            Path(args.output),
            earthdata_user=args.earthdata_user,
            earthdata_pass=args.earthdata_pass,
            max_granules=args.max_granules,
        )


if __name__ == "__main__":
    main()
