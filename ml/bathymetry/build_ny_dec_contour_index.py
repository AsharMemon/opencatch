#!/usr/bin/env python3
"""
Build a New York DEC contour-page index GeoJSON lane.

The official DEC contour-map landing pages are already inventoried locally from
the public sitemap, but direct page fetches are bot-protected from this shell.
This script turns that official page inventory into an honest state-specific
survey-index lane by geocoding lake names from the page slugs with public OSM
geocodes. It does not claim vector contour geometry or direct PDF extraction.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from pathlib import Path

import requests


INPUT_CSV = Path("/Users/Ashar/Documents/fish/data/bathymetry/ny/ny_dec_contour_pages.csv")
OUTPUT_CSV = Path("/Users/Ashar/Documents/fish/data/bathymetry/ny/ny_dec_contour_inventory.csv")
OUTPUT_GEOJSON = Path("/Users/Ashar/Documents/fish/data/bathymetry/ny/ny_waterbody_index.geojson")
USER_AGENT = "OpenCatch/1.0 (+https://opencatch.app)"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-csv", type=Path, default=INPUT_CSV)
    parser.add_argument("--output-csv", type=Path, default=OUTPUT_CSV)
    parser.add_argument("--output-geojson", type=Path, default=OUTPUT_GEOJSON)
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    return parser.parse_args()


def parse_slug(slug: str) -> tuple[str, str]:
    match = re.match(r"(?P<name>.+)-contour-map-region-(?P<region>\d+)$", slug)
    base = match.group("name") if match else slug
    region = match.group("region") if match else ""
    name = re.sub(r"\s+", " ", base.replace("-", " ").strip())
    return name.title(), region


def geocode_lake(name: str) -> tuple[float | None, float | None, str]:
    relaxed_name = re.sub(r"\b(North|South|East|West|Upper|Lower)\b", "", name).strip()
    queries = [f"{name}, New York"]
    if relaxed_name and relaxed_name != name:
        queries.append(f"{relaxed_name}, New York")

    for query in queries:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "jsonv2", "limit": 8},
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
            if "new york" not in display:
                continue
            if category in {"natural", "water"} or item_type in {"water", "reservoir", "lake", "pond"}:
                preferred.append(item)

        chosen = preferred[0] if preferred else (items[0] if items else None)
        if chosen:
            return float(chosen["lat"]), float(chosen["lon"]), "osm_public_geocode"

    return None, None, "unmatched"


def main() -> None:
    args = parse_args()
    rows = list(csv.DictReader(args.input_csv.open()))
    features = []

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(
            csvfile,
            fieldnames=[
                "lake_name",
                "region",
                "slug",
                "waterbody_url",
                "latitude",
                "longitude",
                "coords_source",
            ],
        )
        writer.writeheader()

        for idx, row in enumerate(rows, start=1):
            slug = (row.get("slug") or "").strip()
            waterbody_url = (row.get("url") or "").strip()
            lake_name, region = parse_slug(slug)
            lat, lon, coords_source = geocode_lake(lake_name)
            writer.writerow(
                {
                    "lake_name": lake_name,
                    "region": region,
                    "slug": slug,
                    "waterbody_url": waterbody_url,
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
                            "lake_name": lake_name,
                            "slug": slug,
                            "region": region,
                            "waterbody_url": waterbody_url,
                            "map_pdf_url": None,
                            "report_pdf_urls": "",
                            "coords_source": coords_source,
                        },
                    }
                )
            if idx % 25 == 0 or idx == len(rows):
                print(f"processed {idx}/{len(rows)} rows, geocoded {len(features)}")
            time.sleep(args.delay_seconds)

    args.output_geojson.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}),
        encoding="utf-8",
    )
    print(f"New York DEC contour-page index: {len(features)} features -> {args.output_geojson}")


if __name__ == "__main__":
    main()
