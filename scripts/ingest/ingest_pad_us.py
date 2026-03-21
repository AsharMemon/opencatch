#!/usr/bin/env python3
"""Ingest PAD-US 4.1 (Protected Areas Database) into PostGIS public_lands table.

Downloads polygon features from the ArcGIS FeatureServer in batches and
inserts them via asyncpg.

Usage
-----
    python -m scripts.ingest.ingest_pad_us --state TX
    python -m scripts.ingest.ingest_pad_us --bbox "-97.5,30.0,-96.5,31.0"
    python -m scripts.ingest.ingest_pad_us          # full US (takes a while)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from typing import Any

import httpx

from scripts.ingest.db_utils import (
    esri_json_to_wkt,
    get_db_connection,
    insert_geometry_batch,
)

logger = logging.getLogger(__name__)

PADUS_URL = (
    "https://services.arcgis.com/v01gqwM5QqNysAAi/arcgis/rest/services/"
    "Fee_Managers_PADUS/FeatureServer/0/query"
)

# Fields we pull from PAD-US 4.1 (Fee_Managers_PADUS schema)
OUT_FIELDS = "Unit_Nm,Own_Type,Mang_Name,Des_Tp,Access,State_Nm,GIS_Acres"

# DB columns (order must match the record tuples we build)
DB_COLUMNS = ("name", "agency", "designation", "access_type", "state", "gis_acres", "geom")

BATCH_SIZE = 2000  # ArcGIS server max per request


def _build_where(*, state: str | None = None) -> str:
    """Build a WHERE clause for the ArcGIS query."""
    clauses: list[str] = []
    if state:
        clauses.append(f"State_Nm = '{state}'")
    return " AND ".join(clauses) if clauses else "1=1"


def _bbox_to_envelope(bbox_str: str) -> dict:
    """Convert 'xmin,ymin,xmax,ymax' string to Esri envelope JSON."""
    parts = [float(x.strip()) for x in bbox_str.split(",")]
    if len(parts) != 4:
        raise ValueError("--bbox must be xmin,ymin,xmax,ymax")
    return {
        "xmin": parts[0],
        "ymin": parts[1],
        "xmax": parts[2],
        "ymax": parts[3],
        "spatialReference": {"wkid": 4326},
    }


async def fetch_features(
    client: httpx.AsyncClient,
    where: str,
    *,
    bbox: dict | None = None,
    offset: int = 0,
) -> tuple[list[dict], bool]:
    """Fetch one page of features from the ArcGIS FeatureServer.

    Returns (features_list, exceeded_transfer_limit).
    """
    params: dict[str, Any] = {
        "where": where,
        "outFields": OUT_FIELDS,
        "outSR": 4326,
        "f": "json",
        "resultOffset": offset,
        "resultRecordCount": BATCH_SIZE,
        "returnGeometry": "true",
    }
    if bbox:
        import json as _json

        params["geometry"] = _json.dumps(bbox)
        params["geometryType"] = "esriGeometryEnvelope"
        params["spatialRel"] = "esriSpatialRelIntersects"
        params["inSR"] = 4326

    resp = await client.get(PADUS_URL, params=params, timeout=120)
    resp.raise_for_status()
    data = resp.json()

    if "error" in data:
        raise RuntimeError(f"ArcGIS error: {data['error']}")

    features = data.get("features", [])
    exceeded = data.get("exceededTransferLimit", False)
    return features, exceeded


def _feature_to_record(feat: dict) -> tuple | None:
    """Convert an ArcGIS feature dict to a DB record tuple."""
    attrs = feat.get("attributes", {})
    geom_json = feat.get("geometry")
    wkt = esri_json_to_wkt(geom_json)
    if wkt is None:
        return None

    name = (attrs.get("Unit_Nm") or "")[:256]
    agency = (attrs.get("Mang_Name") or attrs.get("Own_Type") or "")[:64]
    designation = (attrs.get("Des_Tp") or "")[:128]
    access_type = (attrs.get("Access") or "")[:32]
    state = (attrs.get("State_Nm") or "")[:2]
    gis_acres = attrs.get("GIS_Acres")

    return (name, agency, designation, access_type, state, gis_acres, wkt)


async def ingest(
    *,
    database_url: str | None = None,
    state: str | None = None,
    bbox_str: str | None = None,
    dry_run: bool = False,
) -> int:
    """Run the full PAD-US ingestion pipeline. Returns total rows inserted."""
    where = _build_where(state=state)
    bbox = _bbox_to_envelope(bbox_str) if bbox_str else None

    logger.info("PAD-US ingestion starting (where=%s, bbox=%s)", where, bbox_str or "none")

    total_fetched = 0
    total_inserted = 0
    offset = 0

    conn = None if dry_run else await get_db_connection(database_url)

    try:
        async with httpx.AsyncClient() as client:
            while True:
                t0 = time.monotonic()
                features, exceeded = await fetch_features(
                    client, where, bbox=bbox, offset=offset
                )
                elapsed = time.monotonic() - t0
                total_fetched += len(features)
                logger.info(
                    "Fetched %d features (offset=%d, %.1fs). Total fetched: %d",
                    len(features),
                    offset,
                    elapsed,
                    total_fetched,
                )

                if not features:
                    break

                records = []
                for feat in features:
                    rec = _feature_to_record(feat)
                    if rec is not None:
                        records.append(rec)

                if records and not dry_run:
                    n = await insert_geometry_batch(
                        conn,
                        "public_lands",
                        DB_COLUMNS,
                        records,
                        geom_column="geom",
                    )
                    total_inserted += n
                elif records and dry_run:
                    total_inserted += len(records)
                    logger.info("  [dry-run] would insert %d records", len(records))

                skipped = len(features) - len(records)
                if skipped:
                    logger.warning("  Skipped %d features (bad geometry)", skipped)

                if not exceeded:
                    break
                offset += len(features)

    finally:
        if conn is not None:
            await conn.close()

    logger.info(
        "PAD-US ingestion complete: %d fetched, %d inserted", total_fetched, total_inserted
    )
    return total_inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest PAD-US into PostGIS")
    parser.add_argument("--database-url", default=None, help="PostgreSQL connection URL")
    parser.add_argument("--state", default=None, help="Two-letter state filter (e.g. TX)")
    parser.add_argument(
        "--bbox",
        default=None,
        help="Bounding box as xmin,ymin,xmax,ymax in EPSG:4326",
    )
    parser.add_argument("--dry-run", action="store_true", help="Fetch but do not insert")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    inserted = asyncio.run(
        ingest(
            database_url=args.database_url,
            state=args.state,
            bbox_str=args.bbox,
            dry_run=args.dry_run,
        )
    )
    print(f"Done. Rows inserted: {inserted}")


if __name__ == "__main__":
    main()
