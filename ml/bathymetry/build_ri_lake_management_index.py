#!/usr/bin/env python3
"""
Build a Rhode Island official lake-management index GeoJSON lane.

Rhode Island DEM's direct bathymetry PDF endpoints currently return HTTP 403
from this machine, but the official lake-management planning inventory is still
discoverable from public DEM pages. This script turns that inventory into an
honest state-specific survey-index lane with public geocodes and official DEM
URLs rather than leaving Rhode Island on a coarse national fallback.
"""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path

import requests


OUTPUT_DIR = Path("/Users/Ashar/Documents/fish/data/bathymetry/ri")
OUTPUT_CSV = OUTPUT_DIR / "ri_lake_management_inventory.csv"
OUTPUT_GEOJSON = OUTPUT_DIR / "ri_waterbody_index.geojson"
USER_AGENT = "OpenCatch/1.0 (+https://opencatch.app)"
PROJECT_PAGE = "https://dem.ri.gov/node/28786"

WATERBODIES = [
    {
        "lake_name": "Bowdish Lake",
        "geocode_query": "Bowdish Reservoir",
        "waterbody_url": "https://dem.ri.gov/sites/g/files/xkgbur861/files/2025-12/bowdish-lake-mgnt-plan.pdf",
        "map_pdf_url": "https://dem.ri.gov/sites/g/files/xkgbur861/files/2025-12/bowdish-lake-mgnt-plan.pdf",
    },
    {
        "lake_name": "Smith and Sayles Reservoir",
        "geocode_query": "Smith and Sayles Reservoir",
        "waterbody_url": "https://dem.ri.gov/sites/g/files/xkgbur861/files/2025-12/smith-sayles-lake-mgnt-plan.pdf",
        "map_pdf_url": "https://dem.ri.gov/sites/g/files/xkgbur861/files/2025-12/smith-sayles-lake-mgnt-plan.pdf",
    },
    {"lake_name": "Central Pond", "geocode_query": "Central Pond", "waterbody_url": PROJECT_PAGE, "map_pdf_url": ""},
    {"lake_name": "Turner Reservoir", "geocode_query": "Turner Reservoir", "waterbody_url": PROJECT_PAGE, "map_pdf_url": ""},
    {"lake_name": "Georgiaville Pond", "geocode_query": "Georgiaville Pond", "waterbody_url": PROJECT_PAGE, "map_pdf_url": ""},
    {"lake_name": "Indian Lake", "geocode_query": "Indian Lake", "waterbody_url": PROJECT_PAGE, "map_pdf_url": ""},
    {"lake_name": "Tiogue Lake", "geocode_query": "Tiogue Lake", "waterbody_url": PROJECT_PAGE, "map_pdf_url": ""},
    {"lake_name": "Upper Dam Pond", "geocode_query": "Upper Dam Pond", "waterbody_url": PROJECT_PAGE, "map_pdf_url": ""},
]


def geocode_lake(query_name: str) -> tuple[float | None, float | None, str]:
    resp = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": f"{query_name}, Rhode Island", "format": "jsonv2", "limit": 8},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    resp.raise_for_status()
    items = resp.json()

    preferred = []
    for item in items:
        display = (item.get("display_name") or "").lower()
        category = item.get("category")
        item_type = item.get("type")
        if "rhode island" not in display:
            continue
        if category in {"natural", "water"} or item_type in {"water", "reservoir", "lake", "pond"}:
            preferred.append(item)

    chosen = preferred[0] if preferred else (items[0] if items else None)
    if not chosen:
        return None, None, "unmatched"

    return float(chosen["lat"]), float(chosen["lon"]), "osm_public_geocode"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    features = []

    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(
            csvfile,
            fieldnames=[
                "lake_name",
                "waterbody_url",
                "map_pdf_url",
                "latitude",
                "longitude",
                "coords_source",
            ],
        )
        writer.writeheader()

        for idx, row in enumerate(WATERBODIES, start=1):
            lat, lon, coords_source = geocode_lake(row.get("geocode_query") or row["lake_name"])
            writer.writerow(
                {
                    "lake_name": row["lake_name"],
                    "waterbody_url": row["waterbody_url"],
                    "map_pdf_url": row["map_pdf_url"],
                    "latitude": lat,
                    "longitude": lon,
                    "coords_source": coords_source,
                }
            )
            if lat is not None and lon is not None:
                features.append(
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [lon, lat]},
                        "properties": {
                            "lake_name": row["lake_name"],
                            "slug": row["lake_name"].lower().replace(" ", "-"),
                            "waterbody_url": row["waterbody_url"],
                            "map_pdf_url": row["map_pdf_url"] or None,
                            "report_pdf_urls": "",
                            "coords_source": coords_source,
                        },
                    }
                )
            print(f"processed {idx}/{len(WATERBODIES)} rows, geocoded {len(features)}")
            time.sleep(1.0)

    OUTPUT_GEOJSON.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}),
        encoding="utf-8",
    )
    print(f"Rhode Island lake-management index: {len(features)} features -> {OUTPUT_GEOJSON}")


if __name__ == "__main__":
    main()
