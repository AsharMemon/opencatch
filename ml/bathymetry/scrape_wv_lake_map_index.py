#!/usr/bin/env python3
"""
Scrape West Virginia DNR lake-map links into a state-specific survey-index lane.

This builds an honest official index from the WVDNR lake-map links table. It
geocodes lake names with OpenStreetMap Nominatim to place clickable map/PDF
links in the app, but it does not claim survey contours where they do not
exist as vector data.
"""

from __future__ import annotations

import csv
import html
import json
import re
import time
from pathlib import Path
from urllib.request import Request, urlopen

import requests


INDEX_URL = "https://wvdnr.gov/gis-mapping/lake-map-links/"
USER_AGENT = "OpenCatch/1.0 (+https://opencatch.app)"
OUTPUT_DIR = Path("/Users/Ashar/Documents/fish/data/bathymetry/wv")
CSV_PATH = OUTPUT_DIR / "wv_lake_map_inventory.csv"
GEOJSON_PATH = OUTPUT_DIR / "wv_waterbody_index.geojson"


def fetch_html(url: str) -> str:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def parse_rows(html_text: str) -> list[dict]:
    trs = re.findall(r"<tr>(.*?)</tr>", html_text, re.I | re.S)
    rows: list[dict] = []
    for tr in trs:
        cells = re.findall(r"<td>(.*?)</td>", tr, re.I | re.S)
        if len(cells) != 3:
            continue
        lake_name = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", cells[0])).split())
        high_match = re.search(r'href="([^"]+)"', cells[1], re.I)
        low_match = re.search(r'href="([^"]+)"', cells[2], re.I)
        if not lake_name or not high_match:
            continue
        rows.append(
            {
                "lake_name": lake_name,
                "high_res_map_url": high_match.group(1),
                "low_res_map_url": low_match.group(1) if low_match else "",
            }
        )
    return rows


def geocode_lake(name: str) -> tuple[float | None, float | None, str]:
    resp = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": f"{name}, West Virginia", "format": "jsonv2", "limit": 8},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    resp.raise_for_status()
    items = resp.json()

    preferred = []
    for item in items:
        category = item.get("category")
        item_type = item.get("type")
        display = (item.get("display_name") or "").lower()
        if "west virginia" not in display:
            continue
        if category in {"natural", "water"} or item_type in {"water", "reservoir", "lake"}:
            preferred.append(item)

    chosen = preferred[0] if preferred else (items[0] if items else None)
    if not chosen:
        return None, None, "unmatched"

    return float(chosen["lat"]), float(chosen["lon"]), "osm_public_geocode"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = parse_rows(fetch_html(INDEX_URL))
    features = []

    with CSV_PATH.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(
            csvfile,
            fieldnames=[
                "lake_name",
                "high_res_map_url",
                "low_res_map_url",
                "latitude",
                "longitude",
                "coords_source",
            ],
        )
        writer.writeheader()

        for idx, row in enumerate(rows, start=1):
            lat, lon, coords_source = geocode_lake(row["lake_name"])
            record = {
                **row,
                "latitude": lat,
                "longitude": lon,
                "coords_source": coords_source,
            }
            writer.writerow(record)
            if lat is not None and lon is not None:
                features.append(
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [lon, lat]},
                        "properties": {
                            "lake_name": row["lake_name"],
                            "slug": re.sub(r"[^a-z0-9]+", "-", row["lake_name"].lower()).strip("-"),
                            "waterbody_url": INDEX_URL,
                            "map_pdf_url": row["high_res_map_url"],
                            "report_pdf_urls": row["low_res_map_url"],
                            "coords_source": coords_source,
                        },
                    }
                )
            if idx % 10 == 0 or idx == len(rows):
                print(f"processed {idx}/{len(rows)} rows, geocoded {len(features)}")
            time.sleep(1.0)

    GEOJSON_PATH.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}),
        encoding="utf-8",
    )
    print(f"West Virginia lake-map index: {len(features)} features -> {GEOJSON_PATH}")


if __name__ == "__main__":
    main()
