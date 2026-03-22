#!/usr/bin/env python3
"""
OpenCatch — Large-Scale ICESat-2 + Sentinel-2 Bathymetry Training Set Builder

Builds a 100K+ point training dataset matching the scale of Tibetan Plateau studies
(100K-1.4M ICESat-2 depth points). Uses SlideRule Earth for server-side ICESat-2
processing and Element84 Earth Search for Sentinel-2 spectral extraction.

Pipeline:
1. Query SlideRule for ATL06-SR inland water segments across all major US lake regions
2. Also query ATL13 (explicit bathymetric returns) where available
3. For each depth point, query nearest cloud-free S2 scene from Element84
4. Extract 10-band spectral values at each point location
5. Compute derived features (log-ratios, NDWI, MNDWI, turbidity indices)
6. Assign lake_id via spatial clustering for proper cross-validation
7. Save as GeoParquet with train/val/test splits by lake

Output columns:
  lat, lon, depth_m, quality,
  blue, green, red, rededge1, rededge2, rededge3, nir, nir08, swir16, swir22,
  log_blue, log_green, log_red, log_nir,
  log_blue_green, log_blue_red, green_red_ratio, blue_green_ratio,
  ndwi, mndwi, ndti, fai, cdom_proxy,
  blue_minus_green, green_minus_red, red_minus_nir,
  green_sq, blue_sq,
  lake_id, split

Usage:
    python build_icesat2_training_set.py --output /data/icesat2_s2_training.parquet

    # Specific regions only
    python build_icesat2_training_set.py --output /data/icesat2_s2_training.parquet \
        --regions mn_north mn_south wi_north

    # Skip S2 extraction (depth points only)
    python build_icesat2_training_set.py --output /data/icesat2_depths_only.parquet \
        --skip-s2

Requirements:
    pip install sliderule geopandas pyarrow pystac-client odc-stac rasterio \
                numpy scipy tqdm shapely
"""

import argparse
import hashlib
import logging
import time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("build_training")


# ── Region definitions ──────────────────────────────────────────────
# Smaller bboxes = faster SlideRule queries, less timeout risk

REGIONS = {
    # Minnesota — 10,000+ lakes, clear water, excellent training data
    "mn_north":       {"bbox": [-96, 46, -93, 49], "priority": 1},
    "mn_south":       {"bbox": [-96, 43, -93, 46], "priority": 1},
    "mn_arrowhead":   {"bbox": [-93, 47, -89, 49], "priority": 1},

    # Wisconsin — 15,000+ lakes
    "wi_north":       {"bbox": [-92, 45, -88, 47], "priority": 1},
    "wi_south":       {"bbox": [-92, 43, -88, 45], "priority": 2},

    # Michigan — 11,000+ lakes
    "mi_upper":       {"bbox": [-90, 45, -84, 47], "priority": 1},
    "mi_lower_w":     {"bbox": [-87, 42, -85, 45], "priority": 2},
    "mi_lower_e":     {"bbox": [-85, 42, -83, 45], "priority": 2},

    # New York / Adirondacks — 7,600+ lakes, clear mountain water
    "ny_adirondack":  {"bbox": [-76, 43, -73, 45], "priority": 1},
    "ny_fingerlakes": {"bbox": [-77.5, 42, -76, 43.5], "priority": 2},

    # New England
    "me_north":       {"bbox": [-70, 44, -67, 47], "priority": 2},
    "nh_vt":          {"bbox": [-73, 43, -71, 45], "priority": 2},

    # Florida — 30,000+ lakes (mostly shallow, good for training)
    "fl_north":       {"bbox": [-86, 28, -80, 31], "priority": 2},
    "fl_central":     {"bbox": [-82, 27, -80, 29], "priority": 2},

    # Texas reservoirs
    "tx_east":        {"bbox": [-97, 30, -94, 33], "priority": 2},
    "tx_central":     {"bbox": [-99, 30, -96, 33], "priority": 3},

    # California / Oregon — clear mountain lakes
    "ca_tahoe":       {"bbox": [-120.5, 38.5, -119.5, 39.5], "priority": 1},
    "ca_north":       {"bbox": [-123, 40, -120, 42], "priority": 3},
    "or_cascades":    {"bbox": [-122.5, 42, -121, 44], "priority": 2},

    # Rocky Mountain / Intermountain
    "mt_west":        {"bbox": [-115, 46, -112, 49], "priority": 3},
    "co_mountain":    {"bbox": [-107, 38, -105, 40], "priority": 3},
    "id_central":     {"bbox": [-116, 43, -114, 45], "priority": 3},

    # Dakotas / Prairie pothole
    "sd_east":        {"bbox": [-100, 43, -96, 46], "priority": 3},
    "nd_east":        {"bbox": [-100, 46, -96, 49], "priority": 3},

    # Great Lakes nearshore (shallow bays)
    "great_lakes_w":  {"bbox": [-88, 43, -84, 46], "priority": 2},
    "great_lakes_e":  {"bbox": [-84, 42, -76, 46], "priority": 3},

    # Canada
    "on_south":       {"bbox": [-82, 43, -78, 46], "priority": 3},
    "ab_south":       {"bbox": [-116, 50, -112, 53], "priority": 3},
    "bc_okanagan":    {"bbox": [-120, 49, -119, 51], "priority": 3},
}


# ── Sentinel-2 band names (Element84 Earth Search) ─────────────────

S2_BANDS = [
    "blue", "green", "red",
    "rededge1", "rededge2", "rededge3",
    "nir", "nir08",
    "swir16", "swir22",
]

E84_STAC_URL = "https://earth-search.aws.element84.com/v1"


# ── Step 1: Fetch ICESat-2 depths via SlideRule ─────────────────────

def _subdivide_bbox(bbox, max_deg=1.0):
    """
    Subdivide a large bounding box into smaller tiles to stay under
    SlideRule's CMR granule limit (300 per query).
    Each sub-tile is at most max_deg x max_deg.
    """
    lon_min, lat_min, lon_max, lat_max = bbox
    tiles = []
    lat = lat_min
    while lat < lat_max:
        lon = lon_min
        while lon < lon_max:
            tiles.append([
                lon,
                lat,
                min(lon + max_deg, lon_max),
                min(lat + max_deg, lat_max),
            ])
            lon += max_deg
        lat += max_deg
    return tiles


# Time windows to keep CMR hits under 300 per query
TIME_WINDOWS = [
    ("2019-01-01", "2020-06-30"),
    ("2020-07-01", "2021-12-31"),
    ("2022-01-01", "2023-06-30"),
    ("2023-07-01", "2025-12-31"),
]


def fetch_icesat2_all_regions(
    regions: list[str] | None = None,
    max_depth_m: float = 50.0,
    min_photons: int = 5,
    min_confidence: int = 3,
    time_start: str = "2019-01-01",
    time_end: str = "2026-03-01",
    cache_dir: Optional[Path] = None,
    priority_cutoff: int = 99,
) -> "gpd.GeoDataFrame":
    """Fetch ICESat-2 inland water depths for all regions via SlideRule.

    Subdivides large bboxes into 1-degree tiles and splits time into
    18-month windows to stay under SlideRule's 300-granule CMR limit.
    """
    import geopandas as gpd
    import pandas as pd
    from sliderule import sliderule, icesat2

    sliderule.init("slideruleearth.io", verbose=False)
    log.info("Connected to SlideRule Earth")

    # Select regions
    if regions:
        query_regions = {k: v for k, v in REGIONS.items() if k in regions}
    else:
        query_regions = {k: v for k, v in REGIONS.items()
                         if v["priority"] <= priority_cutoff}

    if cache_dir:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)

    all_gdfs = []
    total_points = 0

    # Sort by priority
    sorted_regions = sorted(query_regions.items(), key=lambda x: x[1]["priority"])

    for name, info in sorted_regions:
        bbox = info["bbox"]

        # Check cache
        if cache_dir:
            cache_file = cache_dir / f"icesat2_{name}.parquet"
            if cache_file.exists():
                try:
                    gdf = gpd.read_parquet(cache_file)
                    log.info(f"  Cache hit: {name} ({len(gdf)} points)")
                    all_gdfs.append(gdf)
                    total_points += len(gdf)
                    continue
                except Exception:
                    pass

        log.info(f"Querying SlideRule for {name} (bbox={bbox}, priority={info['priority']})...")

        # Subdivide bbox into 1-degree tiles
        tiles = _subdivide_bbox(bbox, max_deg=1.0)
        log.info(f"  Split into {len(tiles)} sub-tiles x {len(TIME_WINDOWS)} time windows")

        region_parts = []

        for tile_idx, tile_bbox in enumerate(tiles):
            for t_start, t_end in TIME_WINDOWS:
                poly = [
                    {"lon": tile_bbox[0], "lat": tile_bbox[1]},
                    {"lon": tile_bbox[2], "lat": tile_bbox[1]},
                    {"lon": tile_bbox[2], "lat": tile_bbox[3]},
                    {"lon": tile_bbox[0], "lat": tile_bbox[3]},
                    {"lon": tile_bbox[0], "lat": tile_bbox[1]},
                ]

                gdf_part = _query_atl06(icesat2, poly, min_confidence, min_photons,
                                        t_start, t_end, max_depth_m,
                                        f"{name}_t{tile_idx}_{t_start[:4]}")

                if gdf_part is not None and len(gdf_part) > 0:
                    gdf_part["source"] = "atl06"
                    region_parts.append(gdf_part)

                time.sleep(1)  # Rate limit between queries

        if region_parts:
            gdf = pd.concat(region_parts, ignore_index=True)
            # De-duplicate by proximity (within ~20m)
            gdf = _deduplicate_points(gdf, tolerance_deg=0.0002)

            gdf["region"] = name
            all_gdfs.append(gdf)
            total_points += len(gdf)

            if cache_dir:
                gdf.to_parquet(cache_file, index=False)
            log.info(f"  {name}: {len(gdf)} depth points (total: {total_points:,})")
        else:
            log.warning(f"  {name}: no depth data returned")

    if not all_gdfs:
        log.error("No ICESat-2 data fetched from any region!")
        import geopandas as gpd
        return gpd.GeoDataFrame()

    combined = pd.concat(all_gdfs, ignore_index=True)
    log.info(f"\nTotal ICESat-2 depth points: {len(combined):,}")
    if "depth_m" in combined.columns and len(combined) > 0:
        log.info(f"Depth range: {combined['depth_m'].min():.1f} - {combined['depth_m'].max():.1f}m")
        log.info(f"Mean depth: {combined['depth_m'].mean():.1f}m")

    return combined


def _query_atl06(icesat2, poly, min_confidence, min_photons,
                 time_start, time_end, max_depth_m, name):
    """Query ATL06-SR with inland water configuration."""
    try:
        params = {
            "poly": poly,
            "srt": icesat2.SRT_INLAND_WATER,
            "cnf": min_confidence,
            "len": 20,
            "res": 10,
            "maxi": 6,
            "ats": 3.0,
            "cnt": min_photons,
            "t0": time_start,
            "t1": time_end,
        }

        gdf = icesat2.atl06p(params)

        if gdf is None or len(gdf) == 0:
            return None

        log.info(f"  {name} ATL06: {len(gdf)} raw segments")

        # Process for depths
        gdf = gdf.copy()
        gdf["lat"] = gdf.geometry.y
        gdf["lon"] = gdf.geometry.x

        # Quality filters
        if "h_sigma" in gdf.columns:
            gdf = gdf[gdf["h_sigma"] < 0.5]
        if "n_fit_photons" in gdf.columns:
            gdf = gdf[gdf["n_fit_photons"] >= 5]
        if "h_mean" in gdf.columns:
            gdf = gdf[gdf["h_mean"].between(-500, 5000)]

        # Depth from photon spread (bottom returns widen the window)
        if "w_surface_window_final" in gdf.columns:
            gdf["depth_m"] = gdf["w_surface_window_final"].clip(0, max_depth_m)
            gdf = gdf[gdf["w_surface_window_final"] > 0.3]
        elif "h_mean" in gdf.columns:
            # Relative depth from height variation in clusters
            gdf["lat_bin"] = (gdf["lat"] * 100).round() / 100
            gdf["lon_bin"] = (gdf["lon"] * 100).round() / 100
            depths = []
            for _, group in gdf.groupby(["lat_bin", "lon_bin"]):
                if len(group) < 3:
                    continue
                max_h = group["h_mean"].quantile(0.95)
                group = group.copy()
                group["depth_m"] = (max_h - group["h_mean"]).clip(0, max_depth_m)
                depths.append(group)
            if depths:
                import pandas as _pd
                gdf = _pd.concat(depths, ignore_index=True) if len(depths) > 1 else depths[0]
            else:
                return None
        else:
            return None

        if "depth_m" not in gdf.columns or len(gdf) == 0:
            return None

        gdf = gdf[gdf["depth_m"].between(0.1, max_depth_m)]

        # Quality score
        gdf["quality"] = 3
        if "h_sigma" in gdf.columns:
            gdf.loc[gdf["h_sigma"] > 0.2, "quality"] = 2
            gdf.loc[gdf["h_sigma"] > 0.4, "quality"] = 1

        keep = ["geometry", "lat", "lon", "depth_m", "quality"]
        for col in ["h_mean", "h_sigma", "n_fit_photons", "w_surface_window_final",
                     "spot", "rgt", "cycle"]:
            if col in gdf.columns:
                keep.append(col)
        gdf = gdf[[c for c in keep if c in gdf.columns]].reset_index(drop=True)

        return gdf

    except Exception as e:
        log.warning(f"  ATL06 query failed for {name}: {e}")
        return None


## NOTE: ATL13 (atl13p) is not available in the current SlideRule version.
## When SlideRule adds ATL13 support, add a _query_atl13 function here
## to get explicit bathymetric bottom returns (ht_water_surf - ht_bathy).


def _deduplicate_points(gdf, tolerance_deg: float = 0.0002):
    """Remove near-duplicate points (within ~20m), keeping highest quality."""
    gdf = gdf.sort_values("quality", ascending=False)
    gdf["lat_round"] = (gdf["lat"] / tolerance_deg).round() * tolerance_deg
    gdf["lon_round"] = (gdf["lon"] / tolerance_deg).round() * tolerance_deg
    gdf = gdf.drop_duplicates(subset=["lat_round", "lon_round"], keep="first")
    gdf = gdf.drop(columns=["lat_round", "lon_round"])
    return gdf.reset_index(drop=True)


# ── Step 2: Assign lake IDs via spatial clustering ──────────────────

def assign_lake_ids(gdf, cluster_distance_deg: float = 0.005):
    """
    Assign lake_id via DBSCAN spatial clustering.
    Points within ~500m (0.005 deg) of each other are grouped as the same lake.
    """
    from sklearn.cluster import DBSCAN

    log.info("Assigning lake IDs via spatial clustering...")
    coords = gdf[["lat", "lon"]].values
    clustering = DBSCAN(eps=cluster_distance_deg, min_samples=3, metric="euclidean")
    labels = clustering.fit_predict(coords)

    gdf = gdf.copy()
    gdf["lake_id"] = labels

    # Remove noise points (label == -1)
    n_noise = (labels == -1).sum()
    gdf = gdf[gdf["lake_id"] >= 0].reset_index(drop=True)

    n_lakes = gdf["lake_id"].nunique()
    log.info(f"  {n_lakes} lakes identified, {n_noise} noise points removed")
    log.info(f"  Points per lake: min={gdf.groupby('lake_id').size().min()}, "
             f"median={gdf.groupby('lake_id').size().median():.0f}, "
             f"max={gdf.groupby('lake_id').size().max()}")

    # Make lake_id a stable string hash (for cross-instance consistency)
    lake_centers = gdf.groupby("lake_id")[["lat", "lon"]].mean()
    id_map = {}
    for lid, row in lake_centers.iterrows():
        raw = f"{row['lat']:.4f}_{row['lon']:.4f}"
        id_map[lid] = hashlib.md5(raw.encode()).hexdigest()[:12]
    gdf["lake_id"] = gdf["lake_id"].map(id_map)

    return gdf


# ── Step 3: Extract Sentinel-2 spectral features ───────────────────

def extract_s2_features(
    gdf,
    date_window_days: int = 60,
    max_cloud: float = 20.0,
    batch_size: int = 500,
) -> "pd.DataFrame":
    """
    For each ICESat-2 point, find the nearest cloud-free Sentinel-2 scene
    and extract spectral band values.

    Uses Element84 Earth Search (free, no auth needed).
    Processes points in spatial batches to minimize STAC queries.
    """
    import pandas as pd
    from pystac_client import Client

    log.info(f"Extracting S2 spectral features for {len(gdf):,} points...")
    client = Client.open(E84_STAC_URL)

    # Group points into spatial tiles (~0.5 deg ~ 50km)
    gdf = gdf.copy()
    gdf["tile_lat"] = (gdf["lat"] * 2).round() / 2
    gdf["tile_lon"] = (gdf["lon"] * 2).round() / 2

    tiles = gdf.groupby(["tile_lat", "tile_lon"])
    log.info(f"  {len(tiles)} spatial tiles to process")

    results = []
    total_matched = 0
    total_failed = 0

    for (tlat, tlon), tile_points in tiles:
        # Bounding box for this tile (with small buffer)
        bbox = (
            tlon - 0.3,
            tlat - 0.3,
            tlon + 0.3,
            tlat + 0.3,
        )

        # Search for S2 scenes covering this tile (summer months, low cloud)
        try:
            search = client.search(
                collections=["sentinel-2-l2a"],
                bbox=bbox,
                datetime="2021-05-01/2025-09-30",
                query={"eo:cloud_cover": {"lt": max_cloud}},
                sortby=["+properties.eo:cloud_cover"],
                max_items=10,
            )
            items = list(search.items())
        except Exception as e:
            log.warning(f"  S2 search failed for tile ({tlat:.1f}, {tlon:.1f}): {e}")
            total_failed += len(tile_points)
            time.sleep(1)
            continue

        if not items:
            log.warning(f"  No S2 scenes for tile ({tlat:.1f}, {tlon:.1f})")
            total_failed += len(tile_points)
            continue

        # Extract pixel values from the best (least cloudy) scene
        matched = _extract_pixels_from_scene(items[0], tile_points)
        if matched is not None and len(matched) > 0:
            results.append(matched)
            total_matched += len(matched)
        else:
            # Try next scene
            for item in items[1:3]:
                matched = _extract_pixels_from_scene(item, tile_points)
                if matched is not None and len(matched) > 0:
                    results.append(matched)
                    total_matched += len(matched)
                    break
            else:
                total_failed += len(tile_points)

        if total_matched % 5000 < batch_size:
            log.info(f"  Progress: {total_matched:,} matched, "
                     f"{total_failed:,} failed")

        time.sleep(0.3)  # Rate limit

    if not results:
        log.error("No S2 features extracted!")
        return pd.DataFrame()

    df = pd.concat(results, ignore_index=True)
    log.info(f"\nS2 extraction complete: {len(df):,} points with spectral features")
    log.info(f"  Failed/no-data: {total_failed:,}")

    return df


def _extract_pixels_from_scene(item, points_gdf) -> Optional["pd.DataFrame"]:
    """Extract S2 pixel values at ICESat-2 point locations from a single scene."""
    import pandas as pd
    import rasterio
    from rasterio.windows import from_bounds

    try:
        # Get the blue band href to check accessibility
        blue_href = item.assets["blue"].href

        rows = []
        # Open each band and extract values at point locations
        band_data = {}
        for band_name in S2_BANDS:
            if band_name not in item.assets:
                return None
            href = item.assets[band_name].href
            try:
                with rasterio.open(href) as ds:
                    # Read a window covering all points
                    lons = points_gdf["lon"].values
                    lats = points_gdf["lat"].values

                    for i in range(len(points_gdf)):
                        try:
                            py, px = ds.index(lons[i], lats[i])
                            if 0 <= py < ds.height and 0 <= px < ds.width:
                                # Read a small window (3x3) and take center
                                window = rasterio.windows.Window(
                                    max(0, px - 1), max(0, py - 1), 3, 3
                                )
                                data = ds.read(1, window=window)
                                if data.size > 0:
                                    # Center pixel (or mean of 3x3 for robustness)
                                    val = float(np.nanmean(data))
                                    if i not in band_data:
                                        band_data[i] = {}
                                    band_data[i][band_name] = val / 10000.0  # To reflectance
                        except Exception:
                            continue
            except Exception as e:
                log.debug(f"  Failed to open {band_name}: {e}")
                return None

        # Build rows
        for i, (_, point) in enumerate(points_gdf.iterrows()):
            if i in band_data and len(band_data[i]) == len(S2_BANDS):
                row = {
                    "lat": point["lat"],
                    "lon": point["lon"],
                    "depth_m": point["depth_m"],
                    "quality": point.get("quality", 3),
                    "lake_id": point.get("lake_id", "unknown"),
                    "region": point.get("region", "unknown"),
                }
                row.update(band_data[i])
                rows.append(row)

        if rows:
            return pd.DataFrame(rows)
        return None

    except Exception as e:
        log.debug(f"Scene extraction failed: {e}")
        return None


# ── Step 4: Compute derived spectral features ──────────────────────

def compute_derived_features(df) -> "pd.DataFrame":
    """Compute log-ratios, indices, and other derived spectral features."""
    import pandas as pd

    log.info("Computing derived spectral features...")
    eps = 1e-6

    df = df.copy()

    # Log-transformed bands
    df["log_blue"] = np.log(df["blue"].clip(eps))
    df["log_green"] = np.log(df["green"].clip(eps))
    df["log_red"] = np.log(df["red"].clip(eps))
    df["log_nir"] = np.log(df["nir"].clip(eps))

    # Stumpf log-ratio (standard SDB)
    df["log_blue_green"] = df["log_blue"] / df["log_green"].clip(eps)

    # Lyzenga ratio
    df["log_blue_red"] = df["log_blue"] / df["log_red"].clip(eps)

    # Band ratios
    df["green_red_ratio"] = df["green"] / df["red"].clip(eps)
    df["blue_green_ratio"] = df["blue"] / df["green"].clip(eps)

    # Water indices
    df["ndwi"] = (df["green"] - df["nir"]) / (df["green"] + df["nir"] + eps)
    df["mndwi"] = (df["green"] - df["swir16"]) / (df["green"] + df["swir16"] + eps)

    # Turbidity
    df["ndti"] = (df["red"] - df["green"]) / (df["red"] + df["green"] + eps)

    # Floating Algae Index
    fai_factor = (833 - 665) / (1610 - 665)
    df["fai"] = df["nir"] - df["red"] - (df["swir16"] - df["red"]) * fai_factor

    # CDOM proxy
    df["cdom_proxy"] = df["blue"] / df["rededge1"].clip(eps)

    # Band differences
    df["blue_minus_green"] = df["blue"] - df["green"]
    df["green_minus_red"] = df["green"] - df["red"]
    df["red_minus_nir"] = df["red"] - df["nir"]

    # Second-order
    df["green_sq"] = df["green"] ** 2
    df["blue_sq"] = df["blue"] ** 2

    # Replace inf/nan
    for col in df.select_dtypes(include=[np.number]).columns:
        df[col] = df[col].replace([np.inf, -np.inf], np.nan)
        df[col] = df[col].fillna(0.0)

    log.info(f"  {len(df.columns)} total columns")
    return df


# ── Step 5: Train/Val/Test split by lake ────────────────────────────

def split_by_lake(df, train_frac: float = 0.70, val_frac: float = 0.15):
    """
    Split data by lake_id for proper spatial cross-validation.
    No lake appears in multiple splits — tests true generalization.
    """
    lake_ids = df["lake_id"].unique()
    np.random.seed(42)
    np.random.shuffle(lake_ids)

    n_train = int(len(lake_ids) * train_frac)
    n_val = int(len(lake_ids) * val_frac)

    train_lakes = set(lake_ids[:n_train])
    val_lakes = set(lake_ids[n_train:n_train + n_val])
    test_lakes = set(lake_ids[n_train + n_val:])

    df = df.copy()
    df["split"] = "test"
    df.loc[df["lake_id"].isin(train_lakes), "split"] = "train"
    df.loc[df["lake_id"].isin(val_lakes), "split"] = "val"

    for split in ["train", "val", "test"]:
        n = (df["split"] == split).sum()
        n_l = len(df[df["split"] == split]["lake_id"].unique())
        log.info(f"  {split}: {n:,} points, {n_l} lakes")

    return df


# ── Main pipeline ───────────────────────────────────────────────────

def build_training_set(
    output_path: str,
    regions: list[str] | None = None,
    max_depth_m: float = 50.0,
    skip_s2: bool = False,
    cache_dir: str = "/data/icesat2_cache",
    priority_cutoff: int = 2,
) -> Path:
    """End-to-end pipeline: ICESat-2 fetch -> S2 extract -> feature engineering -> save."""
    import geopandas as gpd
    import pandas as pd

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    # Step 1: Fetch ICESat-2 depths
    log.info("=" * 60)
    log.info("STEP 1: Fetching ICESat-2 depth points via SlideRule")
    log.info("=" * 60)

    gdf = fetch_icesat2_all_regions(
        regions=regions,
        max_depth_m=max_depth_m,
        cache_dir=Path(cache_dir),
        priority_cutoff=priority_cutoff,
    )

    if len(gdf) == 0:
        log.error("No ICESat-2 data. Exiting.")
        return output

    # Step 2: Assign lake IDs
    log.info("\n" + "=" * 60)
    log.info("STEP 2: Assigning lake IDs via spatial clustering")
    log.info("=" * 60)

    gdf = assign_lake_ids(gdf)

    if skip_s2:
        # Save depth-only dataset
        log.info(f"\nSaving depth-only dataset to {output}")
        gdf["split"] = ""
        gdf = split_by_lake(gdf)
        gdf.to_parquet(output, index=False)
        log.info(f"Saved {len(gdf):,} points to {output}")
        return output

    # Step 3: Extract S2 features
    log.info("\n" + "=" * 60)
    log.info("STEP 3: Extracting Sentinel-2 spectral features")
    log.info("=" * 60)

    df = extract_s2_features(gdf)

    if len(df) == 0:
        log.error("No S2 features extracted. Saving depth-only data.")
        gdf.to_parquet(output, index=False)
        return output

    # Step 4: Derived features
    log.info("\n" + "=" * 60)
    log.info("STEP 4: Computing derived spectral features")
    log.info("=" * 60)

    df = compute_derived_features(df)

    # Step 5: Split
    log.info("\n" + "=" * 60)
    log.info("STEP 5: Train/Val/Test split by lake")
    log.info("=" * 60)

    df = split_by_lake(df)

    # Step 6: Save
    log.info("\n" + "=" * 60)
    log.info("STEP 6: Saving GeoParquet")
    log.info("=" * 60)

    df.to_parquet(output, index=False)

    log.info(f"\nFinal dataset: {len(df):,} points")
    log.info(f"Columns: {list(df.columns)}")
    log.info(f"Saved to: {output}")
    log.info(f"File size: {output.stat().st_size / 1024 / 1024:.1f} MB")

    # Summary stats
    for col in ["depth_m"] + S2_BANDS[:4]:
        if col in df.columns:
            log.info(f"  {col}: mean={df[col].mean():.4f}, "
                     f"std={df[col].std():.4f}, "
                     f"range=[{df[col].min():.4f}, {df[col].max():.4f}]")

    return output


# ── CLI ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Build large-scale ICESat-2 + S2 bathymetry training set",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--output", type=str,
                        default="/data/icesat2_s2_training.parquet",
                        help="Output GeoParquet path")
    parser.add_argument("--regions", nargs="+", default=None,
                        help="Specific regions to query (default: all priority 1-2)")
    parser.add_argument("--max-depth", type=float, default=50.0,
                        help="Maximum valid depth in metres")
    parser.add_argument("--skip-s2", action="store_true",
                        help="Skip S2 extraction (depth points only)")
    parser.add_argument("--cache-dir", type=str, default="/data/icesat2_cache",
                        help="Cache directory for ICESat-2 parquet files")
    parser.add_argument("--priority", type=int, default=2,
                        help="Maximum region priority to include (1=core, 2=secondary, 3=all)")
    parser.add_argument("--all-regions", action="store_true",
                        help="Query all regions (priority 1-3)")

    args = parser.parse_args()

    priority = 99 if args.all_regions else args.priority

    build_training_set(
        output_path=args.output,
        regions=args.regions,
        max_depth_m=args.max_depth,
        skip_s2=args.skip_s2,
        cache_dir=args.cache_dir,
        priority_cutoff=priority,
    )


if __name__ == "__main__":
    main()
