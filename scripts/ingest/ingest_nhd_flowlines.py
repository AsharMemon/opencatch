#!/usr/bin/env python3
"""Ingest NHDPlus HR flowlines into PostGIS nhd_flowlines table.

Downloads flowline features from the USGS National Hydrography Dataset
MapServer in paginated batches and inserts them via asyncpg.

Usage
-----
    python -m scripts.ingest.ingest_nhd_flowlines --bbox "-97.5,30.0,-96.5,31.0"
    python -m scripts.ingest.ingest_nhd_flowlines --min-stream-order 4
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from typing import Any

import httpx

from scripts.ingest.db_utils import (
    esri_json_to_wkt,
    get_db_connection,
    insert_geometry_batch,
)

logger = logging.getLogger(__name__)

# NHDPlus HR flowlines — NetworkNHDFlowline (layer 3)
NHD_URL = (
    "https://hydro.nationalmap.gov/arcgis/rest/services/NHDPlus_HR/MapServer/3/query"
)

OUT_FIELDS = "COMID,GNIS_Name,FCode,LengthKM,StreamOrde"

DB_COLUMNS = ("comid", "gnis_name", "stream_order", "fcode", "geom")

BATCH_SIZE = 2000  # ArcGIS server max


def _bbox_to_envelope(bbox_str: str) -> dict:
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


def _build_where(min_order: int) -> str:
    return f"StreamOrde >= {min_order}"


async def fetch_features(
    client: httpx.AsyncClient,
    where: str,
    *,
    bbox: dict | None = None,
    offset: int = 0,
) -> tuple[list[dict], bool]:
    """Fetch one page of NHD flowline features."""
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
        params["geometry"] = json.dumps(bbox)
        params["geometryType"] = "esriGeometryEnvelope"
        params["spatialRel"] = "esriSpatialRelIntersects"
        params["inSR"] = 4326

    resp = await client.get(NHD_URL, params=params, timeout=120)
    resp.raise_for_status()
    data = resp.json()

    if "error" in data:
        raise RuntimeError(f"ArcGIS error: {data['error']}")

    features = data.get("features", [])
    exceeded = data.get("exceededTransferLimit", False)
    return features, exceeded


def _linestring_wkt_to_multilinestring(wkt: str) -> str:
    """Ensure WKT is MULTILINESTRING (the DB column type)."""
    if wkt.startswith("LINESTRING"):
        inner = wkt[len("LINESTRING"):]
        return f"MULTILINESTRING({inner})"
    return wkt


def _feature_to_record(feat: dict) -> tuple | None:
    attrs = feat.get("attributes", {})
    geom_json = feat.get("geometry")
    wkt = esri_json_to_wkt(geom_json)
    if wkt is None:
        return None

    # Ensure MultiLineString for the DB column
    wkt = _linestring_wkt_to_multilinestring(wkt)

    comid = attrs.get("COMID")
    gnis_name = (attrs.get("GNIS_Name") or "")[:256] or None
    stream_order = attrs.get("StreamOrde")
    fcode = attrs.get("FCode")

    return (comid, gnis_name, stream_order, fcode, wkt)


async def ingest(
    *,
    database_url: str | None = None,
    bbox_str: str | None = None,
    min_stream_order: int = 3,
    dry_run: bool = False,
) -> int:
    """Run the NHD flowlines ingestion. Returns total rows inserted."""
    if bbox_str is None:
        logger.error("--bbox is required for NHD flowlines (dataset is too large for full US)")
        raise ValueError("Please supply --bbox to limit the query area")

    bbox = _bbox_to_envelope(bbox_str)
    where = _build_where(min_stream_order)

    logger.info(
        "NHD flowlines ingestion starting (where=%s, bbox=%s)",
        where,
        bbox_str,
    )

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
                    "Fetched %d flowlines (offset=%d, %.1fs). Total: %d",
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
                        "nhd_flowlines",
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
        "NHD flowlines ingestion complete: %d fetched, %d inserted",
        total_fetched,
        total_inserted,
    )
    return total_inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest NHDPlus HR flowlines into PostGIS")
    parser.add_argument("--database-url", default=None, help="PostgreSQL connection URL")
    parser.add_argument(
        "--bbox",
        required=True,
        help="Bounding box as xmin,ymin,xmax,ymax in EPSG:4326",
    )
    parser.add_argument(
        "--min-stream-order",
        type=int,
        default=3,
        help="Minimum Strahler stream order to include (default: 3)",
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
            bbox_str=args.bbox,
            min_stream_order=args.min_stream_order,
            dry_run=args.dry_run,
        )
    )
    print(f"Done. Rows inserted: {inserted}")


if __name__ == "__main__":
    main()
