#!/usr/bin/env python3
"""
Build a New Jersey official lake-plan index GeoJSON lane.

The official NJDEP freshwater survey page is bot-protected, but the statewide
lake-plan PDF inventory is already acquired locally. This script turns that
inventory into an honest state-specific survey-index lane with stable map
points and direct PDF links.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path


INPUT_CSV = Path("/Users/Ashar/Documents/fish/data/bathymetry/nj/nj_lake_plan_inventory.csv")
OUTPUT_GEOJSON = Path("/Users/Ashar/Documents/fish/data/bathymetry/nj/nj_waterbody_index.geojson")

# Stable lake-center points for the official NJ lake-plan PDFs.
# These are approximate public geocodes suitable for a survey-index layer,
# not contour geometry or survey shoreline coordinates.
COORDS = {
    "hopatcong": {"lat": 40.9468311, "lon": -74.6366239, "coords_source": "osm_public_geocode"},
    "musconetcong": {"lat": 40.9070411, "lon": -74.6945939, "coords_source": "osm_public_geocode"},
    "union": {"lat": 39.4242739, "lon": -75.0635672, "coords_source": "osm_public_geocode"},
}


def main() -> None:
    rows = list(csv.DictReader(INPUT_CSV.open()))
    features = []
    for row in rows:
        key = (row.get("lake_name") or "").strip().lower()
        coords = COORDS.get(key)
        if not coords:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [coords["lon"], coords["lat"]],
                },
                "properties": {
                    "lake_name": f"{row['lake_name']} Lake" if key != "union" else "Union Lake",
                    "slug": key,
                    "waterbody_url": None,
                    "map_pdf_url": row["url"],
                    "report_pdf_urls": "",
                    "coords_source": coords["coords_source"],
                },
            }
        )

    OUTPUT_GEOJSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_GEOJSON.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}),
        encoding="utf-8",
    )
    print(f"New Jersey lake-plan index: {len(features)} features -> {OUTPUT_GEOJSON}")


if __name__ == "__main__":
    main()
