#!/usr/bin/env python3
"""
Build a compact app-ready lake catalog from GPS Nautical discovery inventories.

This converts the lake discovery CSVs into a lightweight JSON bundle the mobile
app can use for:

- Nearby lake suggestions in the map bottom sheet
- Searchable fallback lake results when backend locations are sparse
- Lake-aware access-point / marina fetches using real waterbody bounds

The GPS Nautical data remains discovery-only provenance. We keep only factual
lake metadata (name, approximate bounds, chart id, etc.), not proprietary
bathymetry.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any


BASE_DIR = Path("/Users/Ashar/Documents/fish/data/bathymetry/gpsnautical")
DEFAULT_US_INPUT = BASE_DIR / "gpsnautical_lake_inventory.csv"
DEFAULT_CA_INPUT = BASE_DIR / "gpsnautical_ca_lake_inventory.csv"
DEFAULT_OUTPUT_JSON = BASE_DIR / "gpsnautical_app_lake_catalog.json"
DEFAULT_SUMMARY_JSON = BASE_DIR / "gpsnautical_app_lake_catalog_summary.json"
DEFAULT_MOBILE_JSON = Path("/Users/Ashar/Documents/fish/mobile/src/data/generated/gpsLakeCatalog.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-us", type=Path, default=DEFAULT_US_INPUT)
    parser.add_argument("--input-ca", type=Path, default=DEFAULT_CA_INPUT)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY_JSON)
    parser.add_argument("--mobile-json", type=Path, default=DEFAULT_MOBILE_JSON)
    parser.add_argument("--max-rows", type=int, default=0, help="Limit rows for debugging. 0 = all.")
    return parser.parse_args()


def read_inventory(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def parse_float(value: str) -> float | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def clean_city_list(raw: str) -> list[str]:
    if not raw.strip():
        return []
    return [part.strip() for part in raw.split(",") if part.strip()][:10]


def infer_waterbody_type(name: str, chart_id: str) -> str:
    text = f"{name} {chart_id}".lower()
    if "river" in text:
        return "river"
    if "creek" in text or "stream" in text:
        return "stream"
    if "reservoir" in text or "flowage" in text:
        return "reservoir"
    if "pond" in text:
        return "pond"
    return "lake"


def stable_id(country: str, chart_id: str, lake_url: str, name: str) -> str:
    source = chart_id or lake_url or name
    normalized = re.sub(r"[^a-z0-9]+", "-", source.lower()).strip("-")
    return f"gps-{country.lower()}-{normalized}"[:140]


def to_catalog_row(catalog: str, row: dict[str, str]) -> dict[str, Any] | None:
    south = parse_float(row.get("min_latitude", ""))
    north = parse_float(row.get("max_latitude", ""))
    west = parse_float(row.get("min_longitude", ""))
    east = parse_float(row.get("max_longitude", ""))
    if None in {south, north, west, east}:
        return None

    center_lat = (south + north) / 2
    center_lon = (west + east) / 2
    lat_span = abs(north - south)
    lon_span = abs(east - west)
    diagonal_km = math.sqrt((lat_span * 111.32) ** 2 + (lon_span * 111.32 * math.cos(math.radians(center_lat))) ** 2)

    name = (row.get("lake_name") or row.get("title") or "").strip()
    if not name:
        return None

    jurisdiction = (row.get("state") or row.get("region") or "").strip()
    subregion = (row.get("county") or row.get("subregion") or "").strip()
    chart_id = (row.get("chart_id") or "").strip()
    lake_url = (row.get("lake_url") or "").strip()
    country = "US" if catalog == "us" else "CA"

    return {
        "id": stable_id(country, chart_id, lake_url, name),
        "catalog": catalog,
        "country": country,
        "jurisdiction": jurisdiction,
        "subregion": subregion,
        "name": name,
        "waterBodyType": infer_waterbody_type(name, chart_id),
        "lat": round(center_lat, 6),
        "lon": round(center_lon, 6),
        "bbox": {
            "south": round(south, 6),
            "west": round(west, 6),
            "north": round(north, 6),
            "east": round(east, 6),
        },
        "bboxDiagonalKm": round(diagonal_km, 3),
        "listedScale": (row.get("listed_scale") or row.get("scale") or "").strip(),
        "chartId": chart_id,
        "lakeUrl": lake_url,
        "sourceUrl": (row.get("state_url") or row.get("region_url") or "").strip(),
        "subregionUrl": (row.get("county_url") or row.get("subregion_url") or "").strip(),
        "nearbyCities": clean_city_list(row.get("nearby_cities", "")),
        "areaAcres": parse_float(row.get("area_acres", "")),
        "shorelineMiles": parse_float(row.get("shoreline_miles", "")),
        "inventorySource": (row.get("inventory_source") or "").strip(),
        "licenseNote": (row.get("license_note") or "").strip(),
    }


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def to_mobile_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "country": row["country"],
        "jurisdiction": row["jurisdiction"],
        "subregion": row["subregion"],
        "name": row["name"],
        "waterBodyType": row["waterBodyType"],
        "lat": row["lat"],
        "lon": row["lon"],
        "bbox": row["bbox"],
        "bboxDiagonalKm": row["bboxDiagonalKm"],
        "listedScale": row["listedScale"],
        "chartId": row["chartId"],
        "nearbyCities": row["nearbyCities"],
        "areaAcres": row["areaAcres"],
        "shorelineMiles": row["shorelineMiles"],
    }


def main() -> None:
    args = parse_args()
    rows: list[dict[str, Any]] = []

    for raw in read_inventory(args.input_us):
        item = to_catalog_row("us", raw)
        if item:
            rows.append(item)
        if args.max_rows and len(rows) >= args.max_rows:
            break

    if not args.max_rows or len(rows) < args.max_rows:
        for raw in read_inventory(args.input_ca):
            item = to_catalog_row("ca", raw)
            if item:
                rows.append(item)
            if args.max_rows and len(rows) >= args.max_rows:
                break

    rows.sort(key=lambda row: (row["country"], row["jurisdiction"], row["subregion"], row["name"]))
    write_json(args.output_json, rows)
    write_json(args.mobile_json, [to_mobile_row(row) for row in rows])

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "row_count": len(rows),
        "country_counts": dict(Counter(row["country"] for row in rows)),
        "jurisdiction_counts": dict(Counter(row["jurisdiction"] for row in rows)),
        "waterbody_type_counts": dict(Counter(row["waterBodyType"] for row in rows)),
        "inputs": {
            "us": str(args.input_us),
            "ca": str(args.input_ca),
        },
        "outputs": {
            "catalog_json": str(args.output_json),
            "mobile_json": str(args.mobile_json),
        },
    }
    write_json(args.summary_json, summary)

    print(f"Wrote {len(rows)} catalog rows")
    print(f"Catalog: {args.output_json}")
    print(f"Mobile: {args.mobile_json}")
    print(f"Summary: {args.summary_json}")


if __name__ == "__main__":
    main()
