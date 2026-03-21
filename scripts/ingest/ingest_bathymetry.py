#!/usr/bin/env python3
"""Ingest bathymetry contour data into PostGIS bathymetry_contours table.

Reads a local GeoJSON or Shapefile containing depth contours and inserts
them into the database.  The input file must have a numeric depth property
and line/multiline geometries.

Usage
-----
    python -m scripts.ingest.ingest_bathymetry \\
        --input-file data/bathy/lake_travis_contours.geojson \\
        --lake-name "Lake Travis" \\
        --depth-field depth_ft

    python -m scripts.ingest.ingest_bathymetry \\
        --input-file data/bathy/toledo_bend.shp \\
        --lake-name "Toledo Bend Reservoir" \\
        --depth-field CONTOUR \\
        --depth-unit meters
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from scripts.ingest.db_utils import (
    geojson_to_wkt,
    get_db_connection,
    insert_geometry_batch,
)

logger = logging.getLogger(__name__)

DB_COLUMNS = ("lake_id", "depth_ft", "geom")

METERS_TO_FEET = 3.28084


def _load_geojson(path: Path) -> list[dict]:
    """Load features from a GeoJSON file."""
    with open(path) as f:
        data = json.load(f)
    if data.get("type") == "FeatureCollection":
        return data.get("features", [])
    if data.get("type") == "Feature":
        return [data]
    raise ValueError(f"Unrecognized GeoJSON structure in {path}")


def _load_shapefile(path: Path) -> list[dict]:
    """Load features from a Shapefile using fiona (optional dependency)."""
    try:
        import fiona
    except ImportError:
        logger.error(
            "fiona is required to read Shapefiles. Install with: pip install fiona"
        )
        sys.exit(1)

    features = []
    with fiona.open(str(path)) as src:
        for feat in src:
            # fiona returns mapping-like objects; convert to plain dicts
            features.append({
                "type": "Feature",
                "properties": dict(feat.get("properties", {})),
                "geometry": dict(feat.get("geometry", {})),
            })
    return features


def _load_features(path: Path) -> list[dict]:
    suffix = path.suffix.lower()
    if suffix == ".geojson" or suffix == ".json":
        return _load_geojson(path)
    if suffix == ".shp":
        return _load_shapefile(path)
    raise ValueError(f"Unsupported file type: {suffix}. Use .geojson, .json, or .shp")


def _ensure_multilinestring(wkt: str) -> str:
    """Promote LINESTRING to MULTILINESTRING if needed."""
    if wkt.startswith("LINESTRING"):
        inner = wkt[len("LINESTRING"):]
        return f"MULTILINESTRING({inner})"
    return wkt


async def _resolve_lake_id(conn, lake_name: str) -> int | None:
    """Look up a location by name and return its id."""
    row = await conn.fetchrow(
        "SELECT id FROM locations WHERE LOWER(name) = LOWER($1)", lake_name
    )
    if row:
        return row["id"]
    return None


async def ingest(
    *,
    database_url: str | None = None,
    input_file: str,
    lake_name: str,
    depth_field: str = "depth_ft",
    depth_unit: str = "feet",
    dry_run: bool = False,
) -> int:
    """Ingest bathymetry contours from a local file. Returns rows inserted."""
    path = Path(input_file)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    logger.info("Loading features from %s", path)
    features = _load_features(path)
    logger.info("Loaded %d features", len(features))

    conn = None if dry_run else await get_db_connection(database_url)
    lake_id: int | None = None

    if conn is not None:
        lake_id = await _resolve_lake_id(conn, lake_name)
        if lake_id is None:
            logger.warning(
                "Lake '%s' not found in locations table. "
                "Inserting contours with lake_id = NULL. "
                "You can update them later with: "
                "UPDATE bathymetry_contours SET lake_id = <id> WHERE lake_id IS NULL",
                lake_name,
            )

    convert_m = depth_unit.lower().startswith("m")
    records: list[tuple] = []
    skipped = 0

    for feat in features:
        props = feat.get("properties", {})
        geom = feat.get("geometry")

        depth_raw = props.get(depth_field)
        if depth_raw is None:
            skipped += 1
            continue
        try:
            depth_val = float(depth_raw)
        except (TypeError, ValueError):
            skipped += 1
            continue

        if convert_m:
            depth_val = depth_val * METERS_TO_FEET

        wkt = geojson_to_wkt(geom)
        if wkt is None:
            skipped += 1
            continue

        # Ensure multilinestring for DB column type
        if "LINESTRING" in wkt:
            wkt = _ensure_multilinestring(wkt)
        elif "POLYGON" in wkt:
            # Some bathymetry data uses polygons for depth zones;
            # extract boundary as multilinestring
            logger.debug("Converting polygon contour to boundary linestring")
            # For polygon WKT, we cannot trivially convert here; skip
            skipped += 1
            continue

        records.append((lake_id, depth_val, wkt))

    if skipped:
        logger.warning("Skipped %d features (missing depth or bad geometry)", skipped)

    logger.info("Prepared %d contour records for insertion", len(records))

    total_inserted = 0
    if records and not dry_run and conn is not None:
        try:
            total_inserted = await insert_geometry_batch(
                conn,
                "bathymetry_contours",
                DB_COLUMNS,
                records,
                geom_column="geom",
            )
        finally:
            await conn.close()
    elif records and dry_run:
        total_inserted = len(records)
        logger.info("[dry-run] would insert %d contour records", len(records))
    else:
        if conn is not None:
            await conn.close()

    logger.info("Bathymetry ingestion complete: %d rows inserted", total_inserted)
    return total_inserted


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest bathymetry contours into PostGIS"
    )
    parser.add_argument("--database-url", default=None, help="PostgreSQL connection URL")
    parser.add_argument(
        "--input-file",
        required=True,
        help="Path to GeoJSON or Shapefile with contour data",
    )
    parser.add_argument(
        "--lake-name",
        required=True,
        help="Name of the lake (must match locations.name for linking)",
    )
    parser.add_argument(
        "--depth-field",
        default="depth_ft",
        help="Property name for depth values (default: depth_ft)",
    )
    parser.add_argument(
        "--depth-unit",
        choices=["feet", "meters"],
        default="feet",
        help="Unit of depth values in the source file (default: feet)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Parse but do not insert")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    inserted = asyncio.run(
        ingest(
            database_url=args.database_url,
            input_file=args.input_file,
            lake_name=args.lake_name,
            depth_field=args.depth_field,
            depth_unit=args.depth_unit,
            dry_run=args.dry_run,
        )
    )
    print(f"Done. Rows inserted: {inserted}")


if __name__ == "__main__":
    main()
