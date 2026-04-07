#!/usr/bin/env python3
"""
Match lake contour depth points to Sentinel-2 spectral features.

This is the critical pipeline for achieving sub-3m RMSE in regional models.
It creates the same training data format as MN's preprocessed_v2 by pairing
contour-derived depth_m with 30+ spectral features from Sentinel-2 pixels.

Usage:
    python match_contours_to_s2.py \
        --contours /data/western_surveys/mt_training_points.parquet \
        --polygons /data/shorelines/hydrolakes_na.parquet \
        --output /data/training/mt_spectral.parquet \
        --state MT
"""
import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("s2_match")


def compute_sdb_features(blue: float, green: float, red: float, nir: float) -> Dict[str, float]:
    """Compute all 30 SDB features from spectral bands (surface reflectance)."""
    EPS = 1e-6
    b = max(blue, EPS)
    g = max(green, EPS)
    r = max(red, EPS)
    n = max(nir, EPS)

    features = {
        "blue": blue, "green": green, "red": red, "nir": nir,
        "log_blue": np.log(b), "log_green": np.log(g),
        "log_red": np.log(r), "log_nir": np.log(n),
        "stumpf_ratio": np.log(b) / (np.log(g) + EPS),
        "lyzenga_bg": np.log(b) - np.log(g),
        "lyzenga_br": np.log(b) - np.log(r),
        "lyzenga_gr": np.log(g) - np.log(r),
        "ndwi": (g - n) / (g + n + EPS),
        "mndwi": (g - 0) / (g + EPS),  # Simplified without SWIR
        "ndvi": (n - r) / (n + r + EPS),
        "blue_green_ratio": b / (g + EPS),
        "blue_red_ratio": b / (r + EPS),
        "green_red_ratio": g / (r + EPS),
        "turbidity_index": r / (g + EPS),
        "cdom_proxy": b / (r + EPS),
        "ndti": (r - g) / (r + g + EPS),
        "rel_blue": b / (b + g + r + EPS),
        "rel_green": g / (b + g + r + EPS),
        "rel_red": r / (b + g + r + EPS),
        "blue_x_green": b * g,
        "blue_x_red": b * r,
        "green_x_red": g * r,
        "blue_sq": b * b,
        "green_sq": g * g,
        "blue_minus_green": b - g,
        "green_minus_red": g - r,
        "red_minus_nir": r - n,
    }
    return features


def compute_spatial_features(
    lat: float, lon: float,
    lake_lats: np.ndarray, lake_lons: np.ndarray,
    shore_lats: Optional[np.ndarray] = None,
    shore_lons: Optional[np.ndarray] = None,
) -> Dict[str, float]:
    """Compute spatial features for a point within a lake."""
    lat_c, lon_c = lake_lats.mean(), lake_lons.mean()
    m_per_lon = 111320 * np.cos(np.radians(lat_c))
    xm = (lon - lon_c) * m_per_lon
    ym = (lat - lat_c) * 110540
    dc = np.sqrt(xm ** 2 + ym ** 2)

    all_xm = (lake_lons - lon_c) * m_per_lon
    all_ym = (lake_lats - lat_c) * 110540
    all_dc = np.sqrt(all_xm ** 2 + all_ym ** 2)
    md = max(all_dc.max(), 1)

    features = {
        "sp_dcn": dc / md,
        "sp_cp": 1 - dc / md,
        "sp_shore_prox": dc / md,
    }

    if len(lake_lats) > 3:
        coords = np.column_stack([all_xm, all_ym])
        c = coords - coords.mean(0)
        try:
            ev, evec = np.linalg.eigh(np.cov(c.T))
            al = c @ evec[:, -1]
            ac = c @ evec[:, 0]
            ar = max(al.max() - al.min(), 1)
            acr = max(ac.max() - ac.min(), 1)
            pt_al = np.array([xm, ym]) @ evec[:, -1]
            pt_ac = np.array([xm, ym]) @ evec[:, 0]
            features["sp_al"] = (pt_al - al.min()) / ar
            features["sp_ac"] = (pt_ac - ac.min()) / acr
            features["sp_dcl"] = abs(pt_ac) / max(acr / 2, 1)
            features["sp_el"] = ar / max(acr, 1)
        except Exception:
            features.update({"sp_al": 0.5, "sp_ac": 0.5, "sp_dcl": 0, "sp_el": 1})
    else:
        features.update({"sp_al": 0.5, "sp_ac": 0.5, "sp_dcl": 0, "sp_el": 1})

    features["sp_sin"] = np.sin(np.arctan2(xm, ym))
    features["sp_cos"] = np.cos(np.arctan2(xm, ym))

    # Shore distance (if shore polygon provided)
    if shore_lats is not None and len(shore_lats) > 0:
        shore_xm = (shore_lons - lon_c) * m_per_lon
        shore_ym = (shore_lats - lat_c) * 110540
        dists = np.sqrt((shore_xm - xm) ** 2 + (shore_ym - ym) ** 2)
        features["true_dist_shore_m"] = float(dists.min())
        features["rel_shore_pos"] = float(dists.min() / max(all_dc.max(), 1))
        features["log_dist_shore"] = float(np.log1p(dists.min()))
    else:
        features["true_dist_shore_m"] = dc
        features["rel_shore_pos"] = dc / md
        features["log_dist_shore"] = float(np.log1p(dc))

    return features


def query_s2_pixel(lat: float, lon: float, date_range: str = "2023-06-01/2023-09-30",
                   max_cloud: int = 20) -> Optional[Dict[str, float]]:
    """Query Sentinel-2 spectral values at a single pixel via Planetary Computer.

    Returns dict with blue/green/red/nir or None if no scene found.
    """
    try:
        import pystac_client
        import planetary_computer
        import rasterio
        from rasterio.windows import from_bounds

        catalog = pystac_client.Client.open(
            "https://planetarycomputer.microsoft.com/api/stac/v1",
            modifier=planetary_computer.sign_inplace,
        )

        search = catalog.search(
            collections=["sentinel-2-l2a"],
            bbox=[lon - 0.01, lat - 0.01, lon + 0.01, lat + 0.01],
            datetime=date_range,
            query={"eo:cloud_cover": {"lt": max_cloud}},
            max_items=5,
        )
        items = list(search.items())
        if not items:
            return None

        item = items[0]  # Best match
        bands = {}
        for band_name, asset_key in [("blue", "B02"), ("green", "B03"),
                                      ("red", "B04"), ("nir", "B08")]:
            href = item.assets[asset_key].href
            with rasterio.open(href) as src:
                # Convert lat/lon to raster CRS (typically UTM)
                from pyproj import Transformer as ProjTransformer
                from rasterio.transform import rowcol
                t = ProjTransformer.from_crs("EPSG:4326", src.crs, always_xy=True)
                x_proj, y_proj = t.transform(lon, lat)
                row, col = rowcol(src.transform, x_proj, y_proj)
                if 0 <= row < src.height and 0 <= col < src.width:
                    window = rasterio.windows.Window(col, row, 1, 1)
                    val = src.read(1, window=window)
                    bands[band_name] = float(val[0, 0]) / 10000.0
                else:
                    return None  # Point outside scene

        return bands
    except Exception as e:
        log.debug("S2 query failed for (%.4f, %.4f): %s" % (lat, lon, e))
        return None


def match_lake(
    contour_df: pd.DataFrame,
    lake_id: str,
    lake_polygon=None,
    date_range: str = "2023-06-01/2023-09-30",
    max_points: int = 200,
) -> pd.DataFrame:
    """Match contour points for one lake to S2 spectral features."""
    if len(contour_df) > max_points:
        contour_df = contour_df.sample(max_points, random_state=42)

    results = []
    lake_lats = contour_df.lat.values
    lake_lons = contour_df.lon.values

    # Get shore coordinates from polygon if available
    shore_lats, shore_lons = None, None
    if lake_polygon is not None:
        try:
            coords = np.array(lake_polygon.exterior.coords)
            shore_lons, shore_lats = coords[:, 0], coords[:, 1]
        except Exception:
            pass

    # Query one S2 scene for the lake centroid
    lat_c, lon_c = lake_lats.mean(), lake_lons.mean()
    s2_bands = query_s2_pixel(lat_c, lon_c, date_range)

    for _, row in contour_df.iterrows():
        # Get pixel-level S2 if centroid scene worked
        pixel_bands = query_s2_pixel(row.lat, row.lon, date_range) if s2_bands else None

        if pixel_bands is None:
            # Use centroid bands as fallback (same scene, slightly wrong pixel)
            pixel_bands = s2_bands

        if pixel_bands is None:
            continue

        # Compute SDB features
        sdb = compute_sdb_features(
            pixel_bands["blue"], pixel_bands["green"],
            pixel_bands["red"], pixel_bands["nir"],
        )

        # Compute spatial features
        spatial = compute_spatial_features(
            row.lat, row.lon, lake_lats, lake_lons, shore_lats, shore_lons,
        )

        # Combine
        record = {
            **sdb,
            **spatial,
            "depth_m": row.depth_m,
            "lat": row.lat,
            "lon": row.lon,
            "lake_id": lake_id,
        }
        results.append(record)

    if results:
        return pd.DataFrame(results)
    return pd.DataFrame()


def batch_match(
    contour_path: str,
    polygon_path: str,
    output_path: str,
    state: str = "XX",
    date_range: str = "2023-06-01/2023-09-30",
    max_lakes: int = None,
):
    """Process all lakes in a contour dataset."""
    log.info("Loading contour data: %s" % contour_path)
    contours = pd.read_parquet(contour_path)
    log.info("  %d points, %d lakes" % (len(contours), contours.lake_id.nunique()))

    log.info("Loading lake polygons: %s" % polygon_path)
    import geopandas as gpd
    from scipy.spatial import cKDTree

    # Load only centroids first (much faster than full geometries)
    log.info("  Building centroid index (loading geometries lazily)...")
    polygons = gpd.read_parquet(polygon_path)
    # Pre-compute centroids to avoid repeated geometry access
    centroids = polygons.geometry.centroid
    poly_cents_y = centroids.y.values
    poly_cents_x = centroids.x.values
    valid = np.isfinite(poly_cents_y) & np.isfinite(poly_cents_x)
    tree = cKDTree(np.column_stack([poly_cents_y[valid], poly_cents_x[valid]]))
    valid_idx = np.where(valid)[0]

    all_results = []
    lake_ids = contours.lake_id.unique()
    if max_lakes:
        lake_ids = lake_ids[:max_lakes]

    for i, lid in enumerate(lake_ids):
        lake_df = contours[contours.lake_id == lid]
        lat_c = lake_df.lat.mean()
        lon_c = lake_df.lon.mean()

        # Find nearest polygon
        dist, idx = tree.query([lat_c, lon_c])
        real_idx = valid_idx[idx]
        polygon = polygons.iloc[real_idx].geometry if dist < 0.01 else None

        result = match_lake(lake_df, str(lid), polygon, date_range)
        if len(result) > 0:
            all_results.append(result)

        if (i + 1) % 50 == 0:
            n_pts = sum(len(r) for r in all_results)
            log.info("  %d/%d lakes processed (%d matched points)" % (i + 1, len(lake_ids), n_pts))

        time.sleep(0.1)  # Rate limit

    if all_results:
        combined = pd.concat(all_results, ignore_index=True)
        combined["state"] = state

        # Create train/test split (70/15/15 by lake)
        lakes = combined.lake_id.unique()
        np.random.seed(42)
        np.random.shuffle(lakes)
        n = len(lakes)
        train_lakes = set(lakes[: int(0.7 * n)])
        val_lakes = set(lakes[int(0.7 * n) : int(0.85 * n)])
        combined["split"] = "test"
        combined.loc[combined.lake_id.isin(train_lakes), "split"] = "train"
        combined.loc[combined.lake_id.isin(val_lakes), "split"] = "val"

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        combined.to_parquet(output_path, index=False)
        log.info("Saved: %s (%d points, %d lakes)" % (output_path, len(combined), combined.lake_id.nunique()))
    else:
        log.warning("No matched points produced")


def main():
    parser = argparse.ArgumentParser(description="Match contour points to Sentinel-2 spectra")
    parser.add_argument("--contours", required=True, help="Parquet with depth points")
    parser.add_argument("--polygons", required=True, help="HydroLAKES parquet with lake polygons")
    parser.add_argument("--output", required=True, help="Output parquet path")
    parser.add_argument("--state", default="XX", help="State code")
    parser.add_argument("--date-range", default="2023-06-01/2023-09-30")
    parser.add_argument("--max-lakes", type=int, default=None)
    args = parser.parse_args()

    batch_match(args.contours, args.polygons, args.output,
                state=args.state, date_range=args.date_range,
                max_lakes=args.max_lakes)


if __name__ == "__main__":
    main()
