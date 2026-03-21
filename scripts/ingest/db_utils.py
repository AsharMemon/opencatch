"""Shared database utilities for CASTLINE data ingestion scripts."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Sequence

import asyncpg

logger = logging.getLogger(__name__)

DEFAULT_DATABASE_URL = os.getenv(
    "CASTLINE_DATABASE_URL",
    "postgresql://castline:castline@localhost:5432/castline",
)


async def get_db_connection(
    database_url: str | None = None,
) -> asyncpg.Connection:
    """Return an asyncpg connection to the PostGIS database."""
    url = database_url or DEFAULT_DATABASE_URL
    conn = await asyncpg.connect(url)
    # Ensure PostGIS is available on this connection
    await conn.execute("SELECT PostGIS_Version()")
    logger.info("Connected to PostGIS database")
    return conn


async def get_db_pool(
    database_url: str | None = None,
    min_size: int = 2,
    max_size: int = 10,
) -> asyncpg.Pool:
    """Return an asyncpg connection pool."""
    url = database_url or DEFAULT_DATABASE_URL
    pool = await asyncpg.create_pool(url, min_size=min_size, max_size=max_size)
    logger.info("Created connection pool (min=%d, max=%d)", min_size, max_size)
    return pool


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def esri_json_to_wkt(geom: dict) -> str | None:
    """Convert an Esri JSON geometry dict to WKT.

    Handles Polygon and MultiPolygon ring structures returned by ArcGIS
    FeatureServer queries.  Returns None for unsupported/empty geometries.
    """
    if not geom:
        return None

    # --- Point ---
    if "x" in geom and "y" in geom:
        return f"POINT({geom['x']} {geom['y']})"

    # --- Polyline / paths ---
    if "paths" in geom:
        paths = geom["paths"]
        if not paths:
            return None
        lines = []
        for path in paths:
            coords = ", ".join(f"{p[0]} {p[1]}" for p in path)
            lines.append(f"({coords})")
        if len(lines) == 1:
            return f"LINESTRING{lines[0]}"
        return "MULTILINESTRING(" + ", ".join(lines) + ")"

    # --- Polygon / rings ---
    if "rings" in geom:
        rings = geom["rings"]
        if not rings:
            return None
        # ArcGIS returns all rings flat; outer rings are clockwise, holes
        # counter-clockwise.  For simplicity we wrap everything as a
        # MultiPolygon with one polygon (PostGIS handles ring direction).
        ring_strs = []
        for ring in rings:
            coords = ", ".join(f"{p[0]} {p[1]}" for p in ring)
            ring_strs.append(f"({coords})")
        polygon_wkt = "(" + ", ".join(ring_strs) + ")"
        return f"MULTIPOLYGON({polygon_wkt})"

    return None


def geojson_to_wkt(geom: dict) -> str | None:
    """Convert a GeoJSON geometry dict to WKT.

    Supports Point, LineString, MultiLineString, Polygon, MultiPolygon.
    """
    if not geom:
        return None

    gtype = geom.get("type", "")
    coords = geom.get("coordinates")
    if coords is None:
        return None

    if gtype == "Point":
        return f"POINT({coords[0]} {coords[1]})"

    if gtype == "LineString":
        pts = ", ".join(f"{c[0]} {c[1]}" for c in coords)
        return f"LINESTRING({pts})"

    if gtype == "MultiLineString":
        lines = []
        for line in coords:
            pts = ", ".join(f"{c[0]} {c[1]}" for c in line)
            lines.append(f"({pts})")
        return "MULTILINESTRING(" + ", ".join(lines) + ")"

    if gtype == "Polygon":
        rings = []
        for ring in coords:
            pts = ", ".join(f"{c[0]} {c[1]}" for c in ring)
            rings.append(f"({pts})")
        polygon = "(" + ", ".join(rings) + ")"
        return f"MULTIPOLYGON({polygon})"

    if gtype == "MultiPolygon":
        polys = []
        for poly in coords:
            rings = []
            for ring in poly:
                pts = ", ".join(f"{c[0]} {c[1]}" for c in ring)
                rings.append(f"({pts})")
            polys.append("(" + ", ".join(rings) + ")")
        return "MULTIPOLYGON(" + ", ".join(polys) + ")"

    logger.warning("Unsupported GeoJSON type: %s", gtype)
    return None


# ---------------------------------------------------------------------------
# Batch insert
# ---------------------------------------------------------------------------


async def insert_geometry_batch(
    conn: asyncpg.Connection,
    table: str,
    columns: Sequence[str],
    records: Sequence[Sequence[Any]],
    *,
    srid: int = 4326,
    geom_column: str = "geom",
    batch_size: int = 500,
) -> int:
    """Insert rows with a WKT geometry column into a PostGIS table.

    ``records`` is a list of tuples whose order matches ``columns``.
    The column listed as ``geom_column`` is expected to contain WKT strings;
    they are wrapped in ST_GeomFromText(..., srid) during insert.

    Returns the total number of rows inserted.
    """
    geom_idx = list(columns).index(geom_column)
    placeholders = []
    for i, col in enumerate(columns):
        ph = f"${i + 1}"
        if i == geom_idx:
            ph = f"ST_GeomFromText(${i + 1}, {srid})"
        placeholders.append(ph)

    col_list = ", ".join(columns)
    val_list = ", ".join(placeholders)
    stmt = f"INSERT INTO {table} ({col_list}) VALUES ({val_list}) ON CONFLICT DO NOTHING"

    total = 0
    for start in range(0, len(records), batch_size):
        batch = records[start : start + batch_size]
        async with conn.transaction():
            await conn.executemany(stmt, batch)
        total += len(batch)
        if total % 2000 == 0 or total == len(records):
            logger.info("  %s: inserted %d / %d rows", table, total, len(records))

    return total
