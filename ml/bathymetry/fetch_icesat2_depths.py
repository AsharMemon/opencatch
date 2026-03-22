#!/usr/bin/env python3
"""
OpenCatch — ICESat-2 ATL13 Bathymetric Depth Fetcher via SlideRule

Fetches ICESat-2 ATL13 inland water data with bottom-return depths using
SlideRule Earth (no credentials required). This is the data source for
the ICESat-2 + Sentinel-2 fusion bathymetry model.

ATL13 provides:
- Water surface height (orthometric, ~2cm precision)
- Bottom return depth (in clear/shallow water, sub-metre accuracy)
- Subsurface return quality flags
- Along-track ~11m footprint, ~91m spacing

SlideRule processes raw ATL03 photons server-side, giving us:
- Configurable signal classification (SNR, confidence thresholds)
- Bathymetric photon returns (bottom-detected segments)
- No need to download multi-GB HDF5 granules

Workflow:
1. Run this script to fetch ICESat-2 depth points per region
2. Run fetch_icesat2.py --match to associate points with lake polygons
3. Run train_icesat2_fusion.py to train the KAN/DAV2 model

Usage:
    # Fetch all North American regions
    python fetch_icesat2_depths.py --output /data/icesat2

    # Fetch specific regions
    python fetch_icesat2_depths.py --output /data/icesat2 \\
        --regions great_lakes upper_midwest mn_south

    # Fetch with custom depth limits
    python fetch_icesat2_depths.py --output /data/icesat2 \\
        --max-depth 30 --min-photons 10

Requirements:
    pip install sliderule geopandas pyarrow tqdm
"""

import argparse
import logging
import time
from pathlib import Path

import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("fetch_icesat2_depths")


# Regions covering major fishing lake areas in North America
# Smaller bboxes = faster SlideRule queries
DEPTH_REGIONS = [
    # US — states with most fishing lakes and clearest water
    {"name": "mn_north",       "bbox": [-96, 46, -93, 49]},
    {"name": "mn_south",       "bbox": [-96, 43, -93, 46]},
    {"name": "wi_north",       "bbox": [-92, 45, -88, 47]},
    {"name": "wi_south",       "bbox": [-92, 43, -88, 45]},
    {"name": "mi_upper",       "bbox": [-90, 45, -84, 47]},
    {"name": "mi_lower",       "bbox": [-87, 42, -83, 45]},
    {"name": "great_lakes",    "bbox": [-93, 41, -76, 49]},
    {"name": "ny_adirondack",  "bbox": [-76, 43, -73, 45]},
    {"name": "new_england",    "bbox": [-73, 41, -67, 47]},
    {"name": "fl_north",       "bbox": [-86, 28, -80, 31]},
    {"name": "tx_east",        "bbox": [-97, 29, -93, 33]},
    {"name": "ca_north",       "bbox": [-123, 38, -119, 42]},
    {"name": "pacific_nw",     "bbox": [-123, 42, -117, 49]},
    {"name": "rocky_mountain", "bbox": [-112, 38, -105, 46]},
    {"name": "upper_midwest",  "bbox": [-105, 43, -93, 49]},
    # Canada
    {"name": "on_south",       "bbox": [-82, 43, -78, 46]},
    {"name": "ab_south",       "bbox": [-116, 50, -112, 53]},
    {"name": "bc_south",       "bbox": [-125, 48, -120, 52]},
    {"name": "sk_south",       "bbox": [-110, 50, -102, 54]},
    {"name": "mb_south",       "bbox": [-100, 49, -95, 53]},
]


def fetch_depths_sliderule(
    output_dir: Path,
    regions: list[str] | None = None,
    max_depth_m: float = 50.0,
    min_photons: int = 5,
    min_confidence: int = 3,
    time_start: str = "2018-10-01",
    time_end: str = "2026-03-01",
) -> list[Path]:
    """
    Fetch ICESat-2 inland water depths via SlideRule Earth.

    Uses ATL13 inland water product which includes bottom-return
    detection for clear/shallow water.

    Args:
        output_dir: Output directory for parquet files
        regions: Specific region names to query (None = all)
        max_depth_m: Maximum valid depth
        min_photons: Minimum photon count per segment
        min_confidence: Minimum signal confidence (1-4)
        time_start: Start date for temporal filter
        time_end: End date for temporal filter

    Returns:
        List of saved GeoParquet file paths
    """
    try:
        from sliderule import sliderule, icesat2
    except ImportError:
        log.error("sliderule not installed. Run: pip install sliderule")
        return []

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Initialize SlideRule
    sliderule.init("slideruleearth.io", verbose=False)
    log.info("Connected to SlideRule Earth")

    # Select regions
    query_regions = DEPTH_REGIONS
    if regions:
        query_regions = [r for r in DEPTH_REGIONS if r["name"] in regions]
        if not query_regions:
            log.error(f"No matching regions. Available: {[r['name'] for r in DEPTH_REGIONS]}")
            return []

    saved_files = []
    total_depth_points = 0

    for region in query_regions:
        name = region["name"]
        bbox = region["bbox"]
        output_path = output_dir / f"icesat2_depths_{name}.parquet"

        if output_path.exists():
            log.info(f"Already exists: {name}")
            saved_files.append(output_path)
            # Count existing points
            try:
                import geopandas as gpd
                existing = gpd.read_parquet(output_path)
                total_depth_points += len(existing)
            except Exception:
                pass
            continue

        log.info(f"Querying SlideRule for {name} (bbox={bbox})...")

        try:
            # Build polygon from bbox
            poly = [
                {"lon": bbox[0], "lat": bbox[1]},
                {"lon": bbox[2], "lat": bbox[1]},
                {"lon": bbox[2], "lat": bbox[3]},
                {"lon": bbox[0], "lat": bbox[3]},
                {"lon": bbox[0], "lat": bbox[1]},
            ]

            # ATL13 inland water body parameters
            # Use ATL03 photon-level processing with water surface type
            params = {
                "poly": poly,
                "srt": icesat2.SRT_INLAND_WATER,
                "cnf": min_confidence,
                "len": 20,     # Shorter segments for finer depth resolution
                "res": 10,     # 10m resolution along-track
                "maxi": 6,     # More iterations for bottom finding
                "ats": 3.0,    # Tighter along-track spread
                "cnt": min_photons,
                "t0": time_start,
                "t1": time_end,
            }

            # Process photons — ATL06-SR with inland water config
            gdf = icesat2.atl06p(params)

            if gdf is None or len(gdf) == 0:
                log.warning(f"No data for {name}")
                time.sleep(2)
                continue

            log.info(f"  {name}: {len(gdf)} raw segments returned")

            # Process and filter for depth data
            gdf = _process_for_depths(gdf, max_depth_m)

            if len(gdf) == 0:
                log.warning(f"  No valid depth points for {name}")
                time.sleep(2)
                continue

            # Save
            gdf.to_parquet(output_path, index=False)
            saved_files.append(output_path)
            total_depth_points += len(gdf)
            log.info(f"  Saved {len(gdf)} points -> {output_path}")

        except Exception as e:
            log.error(f"SlideRule query failed for {name}: {e}")
            import traceback
            traceback.print_exc()

        # Rate limit
        time.sleep(3)

    log.info(f"\nTotal: {len(saved_files)} files, {total_depth_points:,} depth points")
    return saved_files


def _process_for_depths(gdf, max_depth_m: float = 50.0):
    """
    Process SlideRule ATL06-SR results to extract depth estimates.

    For inland water, the surface height gives us the water level.
    We estimate depth from surface height variation within each lake
    (higher h_mean = shallower, relative to the deepest point).

    For true bottom returns, ATL13 provides explicit depth when the
    laser penetrates to the bottom. We'll compute relative depths
    from surface segments over water bodies.
    """
    import geopandas as gpd

    # Add lat/lon from geometry
    gdf = gdf.copy()
    gdf["lat"] = gdf.geometry.y
    gdf["lon"] = gdf.geometry.x

    # Quality filters
    if "h_sigma" in gdf.columns:
        gdf = gdf[gdf["h_sigma"] < 0.5]  # Tight height uncertainty

    if "n_fit_photons" in gdf.columns:
        gdf = gdf[gdf["n_fit_photons"] >= 5]

    if "h_mean" in gdf.columns:
        # Remove unreasonable heights
        gdf = gdf[gdf["h_mean"].between(-500, 5000)]

    # For depth estimation from surface segments:
    # In clear water, the ICESat-2 laser sees both the surface and bottom.
    # The ATL06 surface finding will report the MEAN height which is
    # biased downward in shallow water (photons from bottom pull it down).
    #
    # We can detect this as segments with high "w_surface_window_final"
    # (wide spread in photon heights = surface + bottom returns).
    #
    # Alternatively, if h_mean varies across a water body, deeper areas
    # show lower h_mean due to bottom return bias.

    if "w_surface_window_final" in gdf.columns:
        # Width of photon distribution — wider = possible bottom return
        # Typical surface-only: ~0.1-0.5m
        # Surface+bottom: >1m (depth detectable)
        gdf["estimated_depth"] = gdf["w_surface_window_final"].clip(0, max_depth_m)
        # Only keep segments with detectable bottom returns
        gdf = gdf[gdf["w_surface_window_final"] > 0.3]
        gdf["depth_m"] = gdf["estimated_depth"]
    elif "h_mean" in gdf.columns:
        # Fallback: use h_mean variation as depth proxy
        # Group by approximate location (0.01 degree ~ 1km clusters)
        gdf["lat_bin"] = (gdf["lat"] * 100).round() / 100
        gdf["lon_bin"] = (gdf["lon"] * 100).round() / 100

        # Within each cluster, depth = max_h - h (relative to surface)
        for _, group in gdf.groupby(["lat_bin", "lon_bin"]):
            if len(group) < 3:
                continue
            max_h = group["h_mean"].quantile(0.95)
            gdf.loc[group.index, "depth_m"] = (max_h - group["h_mean"]).clip(0, max_depth_m)

        # Remove clusters with no depth variation (all surface)
        gdf = gdf[gdf.get("depth_m", 0) > 0.1]

    # Filter valid depth range
    if "depth_m" in gdf.columns:
        gdf = gdf[gdf["depth_m"].between(0.1, max_depth_m)]
    else:
        log.warning("Could not estimate depths from SlideRule output")
        return gdf.iloc[0:0]  # empty

    # Quality flag
    gdf["quality"] = 3  # Default good quality
    if "h_sigma" in gdf.columns:
        gdf.loc[gdf["h_sigma"] > 0.3, "quality"] = 2
        gdf.loc[gdf["h_sigma"] > 0.5, "quality"] = 1

    # Keep relevant columns
    keep_cols = ["geometry", "lat", "lon", "depth_m", "quality"]
    for col in ["h_mean", "h_sigma", "n_fit_photons", "w_surface_window_final",
                "spot", "rgt", "cycle"]:
        if col in gdf.columns:
            keep_cols.append(col)

    gdf = gdf[[c for c in keep_cols if c in gdf.columns]].reset_index(drop=True)

    return gdf


def fetch_atl13_direct(
    output_dir: Path,
    regions: list[str] | None = None,
    max_depth_m: float = 50.0,
    time_start: str = "2018-10-01",
    time_end: str = "2026-03-01",
) -> list[Path]:
    """
    Alternative: query ATL13 directly through SlideRule's atl13 endpoint.

    ATL13 explicitly provides:
    - ht_water_surf: water surface height
    - ht_bathy: lake bottom height (where detected)
    - depth = ht_water_surf - ht_bathy
    """
    try:
        from sliderule import sliderule, icesat2
    except ImportError:
        log.error("sliderule not installed")
        return []

    sliderule.init("slideruleearth.io", verbose=False)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    query_regions = DEPTH_REGIONS
    if regions:
        query_regions = [r for r in DEPTH_REGIONS if r["name"] in regions]

    saved_files = []

    for region in query_regions:
        name = region["name"]
        bbox = region["bbox"]
        output_path = output_dir / f"atl13_depths_{name}.parquet"

        if output_path.exists():
            saved_files.append(output_path)
            continue

        log.info(f"Querying ATL13 for {name}...")

        poly = [
            {"lon": bbox[0], "lat": bbox[1]},
            {"lon": bbox[2], "lat": bbox[1]},
            {"lon": bbox[2], "lat": bbox[3]},
            {"lon": bbox[0], "lat": bbox[3]},
            {"lon": bbox[0], "lat": bbox[1]},
        ]

        try:
            # ATL13 inland water body query
            params = {
                "poly": poly,
                "t0": time_start,
                "t1": time_end,
            }

            gdf = icesat2.atl13p(params)

            if gdf is None or len(gdf) == 0:
                log.warning(f"No ATL13 data for {name}")
                time.sleep(2)
                continue

            log.info(f"  {name}: {len(gdf)} ATL13 segments")

            # ATL13 fields of interest:
            # - ht_water_surf: water surface height
            # - ht_bathy: bathymetric height (bottom)
            # - qf_bathy: bathymetric quality flag
            gdf["lat"] = gdf.geometry.y
            gdf["lon"] = gdf.geometry.x

            if "ht_water_surf" in gdf.columns and "ht_bathy" in gdf.columns:
                valid = (
                    gdf["ht_bathy"].notna()
                    & (gdf["ht_bathy"] < gdf["ht_water_surf"])
                )
                depth_gdf = gdf[valid].copy()
                depth_gdf["depth_m"] = depth_gdf["ht_water_surf"] - depth_gdf["ht_bathy"]
                depth_gdf = depth_gdf[depth_gdf["depth_m"].between(0.1, max_depth_m)]

                # Quality from qf_bathy if available
                if "qf_bathy" in depth_gdf.columns:
                    depth_gdf["quality"] = depth_gdf["qf_bathy"]
                else:
                    depth_gdf["quality"] = 3

                if len(depth_gdf) > 0:
                    depth_gdf.to_parquet(output_path, index=False)
                    saved_files.append(output_path)
                    log.info(f"  Saved {len(depth_gdf)} depth points -> {output_path}")
                else:
                    log.warning(f"  No valid bottom returns for {name}")
            else:
                log.warning(f"  ATL13 output missing expected columns for {name}")
                log.info(f"  Available columns: {list(gdf.columns)}")

        except Exception as e:
            log.error(f"ATL13 query failed for {name}: {e}")

        time.sleep(3)

    return saved_files


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Fetch ICESat-2 bathymetric depth data via SlideRule",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument("--output", type=str, default="/data/icesat2",
                        help="Output directory")
    parser.add_argument("--method", type=str, default="atl06",
                        choices=["atl06", "atl13"],
                        help="SlideRule method: atl06 (photon processing) or atl13 (direct)")
    parser.add_argument("--regions", nargs="+", default=None,
                        help="Specific regions (e.g., mn_north great_lakes)")
    parser.add_argument("--max-depth", type=float, default=50.0,
                        help="Maximum valid depth in metres")
    parser.add_argument("--min-photons", type=int, default=5,
                        help="Minimum photon count per segment (atl06 only)")
    parser.add_argument("--min-confidence", type=int, default=3,
                        help="Minimum signal confidence 1-4 (atl06 only)")
    parser.add_argument("--time-start", type=str, default="2018-10-01")
    parser.add_argument("--time-end", type=str, default="2026-03-01")

    args = parser.parse_args()

    if args.method == "atl06":
        files = fetch_depths_sliderule(
            output_dir=Path(args.output),
            regions=args.regions,
            max_depth_m=args.max_depth,
            min_photons=args.min_photons,
            min_confidence=args.min_confidence,
            time_start=args.time_start,
            time_end=args.time_end,
        )
    else:
        files = fetch_atl13_direct(
            output_dir=Path(args.output),
            regions=args.regions,
            max_depth_m=args.max_depth,
            time_start=args.time_start,
            time_end=args.time_end,
        )

    if files:
        log.info(f"\nSaved {len(files)} files to {args.output}")
        log.info("Next steps:")
        log.info("  1. Match to lakes: python fetch_icesat2.py --match ...")
        log.info("  2. Train model:    python train_icesat2_fusion.py --model kan ...")
    else:
        log.warning("No data fetched. Check SlideRule connectivity or try different regions.")


if __name__ == "__main__":
    main()
