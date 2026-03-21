#!/usr/bin/env python3
"""
OpenCatch — Access Points & POI Data Pipeline

Fetches boat launches, fishing access points, parking areas, hiking trails,
fishing piers, kayak/canoe launches, swimming areas, and fish cleaning stations
from OpenStreetMap via the Overpass API.

Designed to handle millions of water bodies by:
    - Batching queries by HUC4 (US) or province (Canada) bounding boxes
    - Checkpoint/resume support for interrupted runs
    - Rate limiting to respect Overpass API policies
    - Streaming output to GeoParquet per batch

Size estimates:
    - North America access points: ~5–10M POIs
    - Output GeoParquet (all types): ~3 GB
    - Peak RAM per batch: ~500 MB

Usage:
    # Fetch all POI types for the entire US (batched by HUC4 bbox)
    python fetch_access_points.py --mode catalog \
        --waterbody-catalog /data/nhdplus/us_waterbody_catalog.parquet \
        --output /data/access_points

    # Fetch for a single bounding box
    python fetch_access_points.py --mode bbox \
        --bbox -97,44,-89,48 \
        --output /data/access_points

    # Fetch only specific types
    python fetch_access_points.py --mode bbox \
        --bbox -97,44,-89,48 \
        --types boat_launch,fishing_pier,kayak_launch \
        --output /data/access_points

    # Resume interrupted catalog run
    python fetch_access_points.py --mode catalog \
        --waterbody-catalog /data/nhdplus/us_waterbody_catalog.parquet \
        --output /data/access_points --resume

Requirements:
    pip install requests geopandas pyarrow shapely tqdm
"""

import argparse
import json
import logging
import time
from collections import Counter
from pathlib import Path
from typing import Optional

import requests
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("fetch_access_points.log")],
)
log = logging.getLogger("fetch_access_points")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Rate limiting — Overpass allows ~2 requests per 10 seconds from one IP
OVERPASS_MIN_DELAY = 5  # seconds between requests
OVERPASS_TIMEOUT = 180  # query timeout in seconds

# Maximum retry attempts
MAX_RETRIES = 3

CHECKPOINT_FILE = "access_points_checkpoint.json"

# ---------------------------------------------------------------------------
# POI type definitions — each maps to one or more Overpass queries
# ---------------------------------------------------------------------------

POI_TYPES = {
    "boat_launch": {
        "description": "Boat ramps, slipways, marinas",
        "tags": [
            '["leisure"="slipway"]',
            '["leisure"="marina"]',
            '["seamark:type"="small_craft_facility"]',
            '["waterway"="boat_ramp"]',
        ],
    },
    "fishing_pier": {
        "description": "Fishing piers, fishing docks, fishing platforms",
        "tags": [
            '["man_made"="pier"]["fishing"="yes"]',
            '["man_made"="pier"]["leisure"="fishing"]',
            '["leisure"="fishing"]["man_made"="pier"]',
            '["man_made"="pier"]["sport"="fishing"]',
        ],
    },
    "shore_access": {
        "description": "Shore fishing spots, bank fishing areas",
        "tags": [
            '["leisure"="fishing"]',
            '["sport"="fishing"]',
        ],
    },
    "kayak_launch": {
        "description": "Kayak/canoe launches and put-ins",
        "tags": [
            '["canoe"="put_in"]',
            '["canoe"="yes"]',
            '["kayak"="yes"]',
            '["leisure"="slipway"]["canoe"="yes"]',
            '["waterway"="canoe_pass"]',
        ],
    },
    "swimming_area": {
        "description": "Swimming areas near water (for safety warnings)",
        "tags": [
            '["leisure"="swimming_area"]',
            '["leisure"="bathing_place"]',
            '["sport"="swimming"]["natural"="water"]',
            '["leisure"="beach_resort"]',
            '["natural"="beach"]',
        ],
    },
    "fish_cleaning_station": {
        "description": "Fish cleaning stations",
        "tags": [
            '["amenity"="fish_cleaning"]',
            '["man_made"="fish_cleaning_table"]',
        ],
    },
    "parking": {
        "description": "Parking areas (non-private)",
        "tags": [
            '["amenity"="parking"]["access"!="private"]',
        ],
    },
    "campground": {
        "description": "Campgrounds and caravan sites",
        "tags": [
            '["tourism"="camp_site"]',
            '["tourism"="caravan_site"]',
        ],
    },
    "trail": {
        "description": "Hiking/walking trails with names",
        "tags": [
            '["highway"="path"]["name"]',
            '["highway"="footway"]["name"]',
            '["highway"="track"]["name"]',
        ],
    },
    "bait_shop": {
        "description": "Bait and tackle shops",
        "tags": [
            '["shop"="fishing"]',
            '["shop"="outdoor"]["fishing"="yes"]',
        ],
    },
}

DEFAULT_TYPES = [
    "boat_launch",
    "fishing_pier",
    "shore_access",
    "kayak_launch",
    "swimming_area",
    "fish_cleaning_station",
    "parking",
    "campground",
    "trail",
    "bait_shop",
]


# ---------------------------------------------------------------------------
# Overpass querying
# ---------------------------------------------------------------------------


def _build_overpass_query(
    poi_type: str,
    bbox: tuple[float, float, float, float],
    timeout: int = OVERPASS_TIMEOUT,
) -> str:
    """
    Build an Overpass QL query for a POI type within a bbox.

    bbox: (min_lon, min_lat, max_lon, max_lat) — note Overpass uses (S,W,N,E)
    """
    tags = POI_TYPES[poi_type]["tags"]
    south, west, north, east = bbox[1], bbox[0], bbox[3], bbox[2]
    bb = f"{south},{west},{north},{east}"

    union_parts = []
    for tag in tags:
        union_parts.append(f"  node{tag}({bb});")
        union_parts.append(f"  way{tag}({bb});")

    union = "\n".join(union_parts)

    return f"""[out:json][timeout:{timeout}];
(
{union}
);
out center tags;"""


def query_overpass(
    query: str,
    timeout: int = OVERPASS_TIMEOUT,
    max_retries: int = MAX_RETRIES,
) -> Optional[dict]:
    """Execute an Overpass query with retry and backoff."""
    for attempt in range(max_retries):
        try:
            resp = requests.post(
                OVERPASS_URL,
                data={"data": query},
                timeout=timeout + 60,
            )

            if resp.status_code == 429:
                wait = 30 * (attempt + 1)
                log.warning(f"  Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue

            if resp.status_code == 504:
                log.warning(f"  Gateway timeout, attempt {attempt + 1}")
                time.sleep(10)
                continue

            resp.raise_for_status()
            return resp.json()

        except requests.Timeout:
            log.warning(f"  Timeout on attempt {attempt + 1}")
            time.sleep(10)
        except Exception as e:
            log.warning(f"  Overpass error on attempt {attempt + 1}: {e}")
            time.sleep(5 * (attempt + 1))

    return None


def _extract_coords(element: dict) -> Optional[tuple[float, float]]:
    """Extract (lat, lon) from an Overpass element."""
    if element["type"] == "node":
        return element.get("lat"), element.get("lon")
    if "center" in element:
        return element["center"]["lat"], element["center"]["lon"]
    if "bounds" in element:
        b = element["bounds"]
        return (b["minlat"] + b["maxlat"]) / 2, (b["minlon"] + b["maxlon"]) / 2
    return None


def fetch_poi_type(
    poi_type: str,
    bbox: tuple[float, float, float, float],
) -> list[dict]:
    """Fetch all POIs of a given type within a bbox. Returns GeoJSON features."""
    query = _build_overpass_query(poi_type, bbox)
    data = query_overpass(query)

    if data is None:
        return []

    features = []
    seen_ids = set()

    for element in data.get("elements", []):
        osm_id = element.get("id")
        if osm_id in seen_ids:
            continue
        seen_ids.add(osm_id)

        coords = _extract_coords(element)
        if coords is None:
            continue

        lat, lon = coords
        tags = element.get("tags", {})

        feature = {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "name": tags.get("name", tags.get("description", "")),
                "access_type": poi_type,
                "fee": tags.get("fee", "unknown") not in ("no", "unknown"),
                "public": tags.get("access", "yes") in ("yes", "public", "permissive"),
                "source": "osm",
                "osm_id": osm_id,
                "osm_type": element["type"],
                # Preserve useful tags
                "capacity": tags.get("capacity"),
                "surface": tags.get("surface"),
                "wheelchair": tags.get("wheelchair"),
                "opening_hours": tags.get("opening_hours"),
                "phone": tags.get("phone"),
                "website": tags.get("website"),
            },
        }

        # Type-specific properties
        if poi_type == "fishing_pier":
            feature["properties"]["pier_type"] = tags.get("man_made", "pier")
        elif poi_type == "swimming_area":
            feature["properties"]["supervised"] = tags.get("supervised", "unknown")
        elif poi_type == "trail":
            feature["properties"]["difficulty"] = tags.get("sac_scale", "unknown")
            feature["properties"]["surface"] = tags.get("surface", "unknown")
            feature["properties"]["length"] = tags.get("distance")
        elif poi_type == "boat_launch":
            feature["properties"]["motorboat"] = tags.get("motorboat", "unknown")
            feature["properties"]["sailboat"] = tags.get("sailboat", "unknown")

        features.append(feature)

    return features


# ---------------------------------------------------------------------------
# Batch processing by region
# ---------------------------------------------------------------------------


def compute_huc4_bboxes(catalog_path: Path) -> dict[str, tuple[float, float, float, float]]:
    """
    Compute bounding boxes for each HUC4 from the waterbody catalog.
    Returns: {huc4_or_province: (min_lon, min_lat, max_lon, max_lat)}
    """
    import pandas as pd

    log.info(f"Loading waterbody catalog from {catalog_path}...")
    df = pd.read_parquet(catalog_path)

    bboxes = {}

    # Group by HUC4 (US) or province (Canada)
    if "huc4" in df.columns and df["huc4"].notna().any():
        for huc4, group in df.groupby("huc4"):
            if not huc4 or str(huc4).strip() == "":
                continue
            bboxes[f"huc4_{huc4}"] = (
                group["centroid_lon"].min() - 0.1,
                group["centroid_lat"].min() - 0.1,
                group["centroid_lon"].max() + 0.1,
                group["centroid_lat"].max() + 0.1,
            )

    if "province" in df.columns and df["province"].notna().any():
        for prov, group in df.groupby("province"):
            if not prov or str(prov).strip() == "":
                continue
            bboxes[f"prov_{prov}"] = (
                group["centroid_lon"].min() - 0.1,
                group["centroid_lat"].min() - 0.1,
                group["centroid_lon"].max() + 0.1,
                group["centroid_lat"].max() + 0.1,
            )

    log.info(f"Computed {len(bboxes)} region bounding boxes")
    return bboxes


def _split_large_bbox(
    bbox: tuple[float, float, float, float],
    max_degrees: float = 3.0,
) -> list[tuple[float, float, float, float]]:
    """Split a bbox into sub-tiles if it's too large for Overpass."""
    min_lon, min_lat, max_lon, max_lat = bbox
    width = max_lon - min_lon
    height = max_lat - min_lat

    if width <= max_degrees and height <= max_degrees:
        return [bbox]

    tiles = []
    lon = min_lon
    while lon < max_lon:
        lat = min_lat
        while lat < max_lat:
            tiles.append((
                lon,
                lat,
                min(lon + max_degrees, max_lon),
                min(lat + max_degrees, max_lat),
            ))
            lat += max_degrees
        lon += max_degrees

    return tiles


def load_checkpoint(output_dir: Path) -> dict:
    cp = output_dir / CHECKPOINT_FILE
    if cp.exists():
        with open(cp) as f:
            return json.load(f)
    return {"completed_regions": {}, "total_features": 0}


def save_checkpoint(output_dir: Path, checkpoint: dict):
    with open(output_dir / CHECKPOINT_FILE, "w") as f:
        json.dump(checkpoint, f, indent=2)


def run_catalog_mode(
    catalog_path: Path,
    output_dir: Path,
    types: list[str],
    resume: bool = True,
):
    """Batch-fetch POIs for all regions in the waterbody catalog."""
    output_dir.mkdir(parents=True, exist_ok=True)
    parquet_dir = output_dir / "parquet"
    parquet_dir.mkdir(parents=True, exist_ok=True)

    checkpoint = load_checkpoint(output_dir) if resume else {"completed_regions": {}, "total_features": 0}
    bboxes = compute_huc4_bboxes(catalog_path)

    log.info(f"Processing {len(bboxes)} regions for {len(types)} POI types...")

    for region_key, bbox in tqdm(sorted(bboxes.items()), desc="Regions"):
        # Check if already done for all types
        done_types = checkpoint["completed_regions"].get(region_key, [])
        remaining_types = [t for t in types if t not in done_types]

        if not remaining_types:
            continue

        # Split large bboxes into manageable tiles
        tiles = _split_large_bbox(bbox, max_degrees=2.0)

        region_features = []

        for poi_type in remaining_types:
            type_features = []

            for tile in tiles:
                log.info(f"  [{region_key}] {poi_type} tile {tile}")
                features = fetch_poi_type(poi_type, tile)
                type_features.extend(features)

                # Rate limit
                time.sleep(OVERPASS_MIN_DELAY)

            region_features.extend(type_features)

            # Mark this type as done for this region
            if region_key not in checkpoint["completed_regions"]:
                checkpoint["completed_regions"][region_key] = []
            checkpoint["completed_regions"][region_key].append(poi_type)
            save_checkpoint(output_dir, checkpoint)

            log.info(f"  [{region_key}] {poi_type}: {len(type_features)} features")

        # Save region parquet
        if region_features:
            _save_features_parquet(region_features, parquet_dir / f"{region_key}.parquet")
            checkpoint["total_features"] += len(region_features)
            save_checkpoint(output_dir, checkpoint)

    log.info(f"Total features collected: {checkpoint['total_features']}")


def run_bbox_mode(
    bbox: tuple[float, float, float, float],
    output_dir: Path,
    types: list[str],
    waterbodies_path: Optional[Path] = None,
):
    """Fetch POIs for a single bounding box."""
    output_dir.mkdir(parents=True, exist_ok=True)

    all_features = []

    for poi_type in types:
        log.info(f"Fetching {poi_type}...")
        features = fetch_poi_type(poi_type, bbox)
        all_features.extend(features)
        log.info(f"  {poi_type}: {len(features)} features")
        time.sleep(OVERPASS_MIN_DELAY)

    # Optional: associate with waterbodies
    if waterbodies_path and waterbodies_path.exists():
        all_features = associate_with_waterbodies(all_features, waterbodies_path)

    # Save as GeoJSON and Parquet
    geojson = {"type": "FeatureCollection", "features": all_features}
    geojson_path = output_dir / "access_points.geojson"
    with open(geojson_path, "w") as f:
        json.dump(geojson, f)
    log.info(f"Saved GeoJSON: {geojson_path}")

    _save_features_parquet(all_features, output_dir / "access_points.parquet")

    # Summary
    type_counts = Counter(f["properties"]["access_type"] for f in all_features)
    log.info(f"\nTotal: {len(all_features)} access points")
    for t, c in type_counts.most_common():
        log.info(f"  {t}: {c}")


# ---------------------------------------------------------------------------
# Waterbody association
# ---------------------------------------------------------------------------


def associate_with_waterbodies(
    features: list[dict],
    waterbodies_path: Path,
    max_distance_deg: float = 0.005,  # ~500m
) -> list[dict]:
    """Associate access points with the nearest water body."""
    try:
        import geopandas as gpd
        from shapely.geometry import Point

        wb = gpd.read_parquet(waterbodies_path) if str(waterbodies_path).endswith(".parquet") else gpd.read_file(waterbodies_path)

        if wb.empty:
            return features

        # Build spatial index
        sindex = wb.sindex

        for feat in features:
            coords = feat["geometry"]["coordinates"]
            pt = Point(coords[0], coords[1])

            # Query spatial index
            buffer = pt.buffer(max_distance_deg)
            candidates = list(sindex.intersection(buffer.bounds))

            if not candidates:
                continue

            # Find nearest
            distances = wb.iloc[candidates].geometry.distance(pt)
            nearest_idx = distances.idxmin()
            nearest_dist = distances[nearest_idx]

            if nearest_dist <= max_distance_deg:
                row = wb.loc[nearest_idx]
                feat["properties"]["nearest_waterbody"] = str(
                    row.get("name", row.get("GNIS_Name", ""))
                )
                feat["properties"]["waterbody_id"] = str(
                    row.get("permanent_id", "")
                )
                feat["properties"]["distance_to_water_m"] = round(
                    nearest_dist * 111000, 0
                )

    except ImportError:
        log.warning("geopandas not available — skipping waterbody association")
    except Exception as e:
        log.warning(f"Waterbody association failed: {e}")

    return features


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _save_features_parquet(features: list[dict], path: Path):
    """Save GeoJSON features to GeoParquet."""
    try:
        import geopandas as gpd
        from shapely.geometry import shape

        if not features:
            return

        rows = []
        for f in features:
            row = dict(f["properties"])
            row["geometry"] = shape(f["geometry"])
            rows.append(row)

        gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
        gdf.to_parquet(path, index=False)
        log.info(f"  Saved {len(gdf)} features → {path.name}")

    except ImportError:
        # Fallback to GeoJSON
        geojson_path = path.with_suffix(".geojson")
        with open(geojson_path, "w") as f:
            json.dump({"type": "FeatureCollection", "features": features}, f)
        log.info(f"  Saved {len(features)} features → {geojson_path.name} (parquet unavailable)")


def merge_all_parquets(output_dir: Path):
    """Merge all region parquets into a single file per POI type."""
    import geopandas as gpd
    import pandas as pd

    parquet_dir = output_dir / "parquet"
    if not parquet_dir.exists():
        return

    files = list(parquet_dir.glob("*.parquet"))
    if not files:
        return

    log.info(f"Merging {len(files)} region parquet files...")

    all_dfs = []
    for f in tqdm(files, desc="Reading"):
        try:
            gdf = gpd.read_parquet(f)
            all_dfs.append(gdf)
        except Exception as e:
            log.warning(f"Cannot read {f.name}: {e}")

    if not all_dfs:
        return

    merged = pd.concat(all_dfs, ignore_index=True)

    # Deduplicate by osm_id
    if "osm_id" in merged.columns:
        before = len(merged)
        merged = merged.drop_duplicates(subset="osm_id", keep="first")
        log.info(f"Deduped: {before} → {len(merged)}")

    gdf = gpd.GeoDataFrame(merged, geometry="geometry", crs="EPSG:4326")

    # Save per-type and merged
    for access_type, group in gdf.groupby("access_type"):
        out = output_dir / f"{access_type}.parquet"
        group.to_parquet(out, index=False)
        log.info(f"  {access_type}: {len(group)} → {out.name}")

    all_path = output_dir / "all_access_points.parquet"
    gdf.to_parquet(all_path, index=False)
    log.info(f"Total merged: {len(gdf)} → {all_path.name}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="OpenCatch — Access points & POI pipeline"
    )
    parser.add_argument(
        "--mode",
        choices=["bbox", "catalog", "merge"],
        default="bbox",
        help="Pipeline mode: bbox (single area), catalog (batch by region), merge",
    )
    parser.add_argument(
        "--bbox", type=str, default=None,
        help="Bounding box: min_lon,min_lat,max_lon,max_lat",
    )
    parser.add_argument(
        "--waterbody-catalog", type=str, default=None,
        help="Path to waterbody catalog parquet for catalog mode",
    )
    parser.add_argument(
        "--output", type=str, default="/data/access_points",
        help="Output directory",
    )
    parser.add_argument(
        "--types", type=str, default=",".join(DEFAULT_TYPES),
        help="Comma-separated POI types to fetch",
    )
    parser.add_argument(
        "--waterbodies", type=str, default=None,
        help="Waterbody file for association (bbox mode)",
    )
    parser.add_argument(
        "--resume", action="store_true", default=True,
        help="Resume from checkpoint",
    )
    parser.add_argument(
        "--no-resume", action="store_true",
        help="Start fresh",
    )
    args = parser.parse_args()

    types = [t.strip() for t in args.types.split(",")]
    output_dir = Path(args.output)
    resume = not args.no_resume

    if args.mode == "bbox":
        if not args.bbox:
            log.error("--bbox required for bbox mode")
            return
        bbox = tuple(float(x) for x in args.bbox.split(","))
        wb_path = Path(args.waterbodies) if args.waterbodies else None
        run_bbox_mode(bbox, output_dir, types, wb_path)

    elif args.mode == "catalog":
        if not args.waterbody_catalog:
            log.error("--waterbody-catalog required for catalog mode")
            return
        run_catalog_mode(Path(args.waterbody_catalog), output_dir, types, resume)

    elif args.mode == "merge":
        merge_all_parquets(output_dir)


if __name__ == "__main__":
    main()
