#!/usr/bin/env python3
"""Ingest access-point GeoJSON into PostGIS access_points.

Supports mixed point/line geometries from the access-point pipeline, including:
- boat launches / ramps
- shore-access points
- parking
- campgrounds
- trails
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from scripts.ingest.db_utils import geojson_to_wkt, get_db_connection, insert_geometry_batch

logger = logging.getLogger(__name__)

DB_COLUMNS = (
    "location_id",
    "name",
    "access_type",
    "source",
    "source_id",
    "nearest_waterbody",
    "distance_to_water_m",
    "fee",
    "is_free",
    "public_access",
    "capacity",
    "difficulty",
    "surface",
    "tags",
    "geom",
)


def _normalize_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"yes", "true", "1", "public", "permissive"}:
            return True
        if lowered in {"no", "false", "0", "private"}:
            return False
    return bool(value)


async def _resolve_location_id(conn: Any, nearest_waterbody: str | None) -> int | None:
    if not nearest_waterbody:
        return None

    row = await conn.fetchrow(
        """
        SELECT id
        FROM locations
        WHERE LOWER(name) = LOWER($1)
           OR LOWER(name) LIKE LOWER($2)
        ORDER BY id
        LIMIT 1
        """,
        nearest_waterbody,
        f"{nearest_waterbody}%",
    )
    return row["id"] if row else None


async def ingest(
    *,
    database_url: str | None = None,
    input_file: str,
    dry_run: bool = False,
) -> int:
    path = Path(input_file)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    with open(path) as f:
        payload = json.load(f)

    features = payload.get("features", [])
    logger.info("Loaded %d access features from %s", len(features), path)

    conn = None if dry_run else await get_db_connection(database_url)
    records: list[tuple[Any, ...]] = []
    skipped = 0

    try:
        for feat in features:
            props = feat.get("properties", {})
            geom = feat.get("geometry")
            wkt = geojson_to_wkt(geom)
            if wkt is None:
                skipped += 1
                continue

            nearest_waterbody = props.get("nearest_waterbody")
            location_id = None
            if conn is not None:
                location_id = await _resolve_location_id(conn, nearest_waterbody)

            record = (
                location_id,
                (props.get("name") or "Unnamed Access")[:256],
                (props.get("access_type") or "unknown")[:32],
                (props.get("source") or "osm")[:32],
                str(props.get("osm_id") or props.get("source_id") or "")[:128] or None,
                (nearest_waterbody or "")[:256] or None,
                props.get("distance_to_water_m"),
                _normalize_bool(props.get("fee")),
                _normalize_bool(props.get("free")),
                _normalize_bool(props.get("public")),
                props.get("capacity"),
                (props.get("difficulty") or "")[:64] or None,
                (props.get("surface") or "")[:64] or None,
                json.dumps(props.get("tags") or props),
                wkt,
            )
            records.append(record)
    finally:
        if skipped:
            logger.warning("Skipped %d access features with unsupported geometry", skipped)

    if not records:
        if conn is not None:
            await conn.close()
        logger.info("No valid access-point records to insert")
        return 0

    if dry_run:
        logger.info("[dry-run] would insert %d records", len(records))
        return len(records)

    assert conn is not None
    try:
        inserted = await insert_geometry_batch(
            conn,
            "access_points",
            DB_COLUMNS,
            records,
            geom_column="geom",
        )
    finally:
        await conn.close()

    logger.info("Access-point ingestion complete: %d rows inserted", inserted)
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest access-point GeoJSON into PostGIS")
    parser.add_argument("--database-url", default=None, help="PostgreSQL connection URL")
    parser.add_argument("--input-file", required=True, help="Path to GeoJSON FeatureCollection")
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
            dry_run=args.dry_run,
        )
    )
    print(f"Done. Rows inserted: {inserted}")


if __name__ == "__main__":
    main()
