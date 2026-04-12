#!/usr/bin/env python3
"""
Scrape Virginia DWR waterbody pages into a survey-index GeoJSON lane.

This does not invent bathymetry contours. It builds an honest official index of
Virginia DWR waterbody pages and map PDFs, using the coordinates embedded in
the official "Maps & Directions" page content so OpenCatch can show a
Virginia-specific official coverage/index layer instead of only falling back to
coarse national priors.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET


SITEMAP_URL = "https://dwr.virginia.gov/wp-sitemap-posts-waterbody-1.xml"
USER_AGENT = "Mozilla/5.0 (compatible; OpenCatch/1.0; +https://opencatch.app)"
OUTPUT_DIR = Path("/Users/Ashar/Documents/fish/data/bathymetry/va")
CSV_PATH = OUTPUT_DIR / "va_dwr_waterbody_inventory.csv"
GEOJSON_PATH = OUTPUT_DIR / "va_waterbody_index.geojson"
@dataclass
class WaterbodyRecord:
    waterbody_url: str
    slug: str
    lake_name: str
    latitude: Optional[float]
    longitude: Optional[float]
    map_pdf_url: Optional[str]
    report_pdf_urls: str
    coords_source: str


def fetch_text(url: str, timeout: int = 30) -> str:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def parse_sitemap(url: str) -> list[str]:
    xml = fetch_text(url, timeout=30)
    root = ET.fromstring(xml)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    return [loc.text.strip() for loc in root.findall(".//sm:url/sm:loc", ns) if loc.text]


def slug_to_name(slug: str) -> str:
    parts = slug.strip("/").split("/")[-1].split("-")
    return " ".join(part.capitalize() for part in parts)


def extract_pdf_links(html: str) -> tuple[Optional[str], list[str]]:
    pdfs = re.findall(r"https://dwr\.virginia\.gov/wp-content/uploads/[^\"'>]+\.pdf", html, re.I)
    if not pdfs:
        return None, []
    map_pdf = None
    reports: list[str] = []
    for pdf in pdfs:
        lower = pdf.lower()
        if "popular-report" in lower or "popularxreport" in lower:
            reports.append(pdf)
        elif map_pdf is None:
            map_pdf = pdf
        else:
            reports.append(pdf)
    return map_pdf, reports


def extract_page_coords(html: str) -> tuple[Optional[float], Optional[float], str]:
    patterns = [
        (
            r"https://www\.google\.com/maps/search/attractions/@(?P<lat>-?\d+\.\d+),(?P<lon>-?\d+\.\d+)",
            "google_maps_search",
        ),
        (
            r"https://maps\.google\.com/\?q=(?P<lat>-?\d+\.\d+),(?P<lon>-?\d+\.\d+)",
            "google_maps_q",
        ),
        (
            r"@(?P<lat>-?\d+\.\d+),(?P<lon>-?\d+\.\d+),\d+z",
            "inline_map_at",
        ),
    ]
    for pattern, label in patterns:
        match = re.search(pattern, html, re.I)
        if not match:
            continue
        try:
            return float(match.group("lat")), float(match.group("lon")), label
        except Exception:
            continue
    return None, None, "unmatched"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scrape Virginia DWR waterbody inventory into a survey-index lane.")
    parser.add_argument("--limit", type=int, default=0, help="Only process the first N sitemap URLs (0 = all).")
    parser.add_argument("--sleep-ms", type=int, default=25, help="Sleep between requests in milliseconds.")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    urls = parse_sitemap(SITEMAP_URL)
    if args.limit and args.limit > 0:
        urls = urls[: args.limit]

    records: list[WaterbodyRecord] = []
    features: list[dict] = []

    for idx, url in enumerate(urls, start=1):
        slug = url.rstrip("/").split("/")[-1]
        lake_name = slug_to_name(slug)
        html = fetch_text(url, timeout=20)
        title_match = re.search(r"<title>([^<|]+)", html, re.I)
        if title_match:
            title_name = title_match.group(1).strip()
            if title_name and "Page not found" not in title_name:
                lake_name = title_name
        map_pdf_url, report_urls = extract_pdf_links(html)
        latitude, longitude, coords_source = extract_page_coords(html)
        if latitude is not None and longitude is not None:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [longitude, latitude]},
                    "properties": {
                        "waterbody_url": url,
                        "lake_name": lake_name,
                        "slug": slug,
                        "map_pdf_url": map_pdf_url,
                        "report_pdf_urls": "|".join(report_urls),
                        "coords_source": coords_source,
                    },
                }
            )
        records.append(
            WaterbodyRecord(
                waterbody_url=url,
                slug=slug,
                lake_name=lake_name,
                latitude=latitude,
                longitude=longitude,
                map_pdf_url=map_pdf_url,
                report_pdf_urls="|".join(report_urls),
                coords_source=coords_source,
            )
        )
        if idx % 20 == 0 or idx == len(urls):
            geocoded_so_far = sum(1 for r in records if r.latitude is not None and r.longitude is not None)
            print(f"processed {idx}/{len(urls)} pages, geocoded {geocoded_so_far}")
        if args.sleep_ms > 0:
            time.sleep(args.sleep_ms / 1000.0)

    with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "waterbody_url",
                "slug",
                "lake_name",
                "latitude",
                "longitude",
                "map_pdf_url",
                "report_pdf_urls",
                "coords_source",
            ],
        )
        writer.writeheader()
        for record in records:
            writer.writerow(record.__dict__)

    if features:
        GEOJSON_PATH.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")

    geocoded = sum(1 for r in records if r.latitude is not None and r.longitude is not None)
    print(
        f"Virginia DWR waterbody inventory: {len(records)} pages, "
        f"{geocoded} geocoded from official map pages, output={CSV_PATH}"
    )
    if features:
        print(f"GeoJSON written: {GEOJSON_PATH}")


if __name__ == "__main__":
    main()
