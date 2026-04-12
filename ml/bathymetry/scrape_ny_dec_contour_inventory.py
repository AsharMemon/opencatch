#!/usr/bin/env python3
"""
Build NY DEC map inventories from the public DEC sitemap.

The lake contour search page itself is Cloudflare-protected, but the public
DEC sitemap is accessible and still gives us two useful official inventories:

1. A broad candidate map inventory
2. A refined list of contour-map landing pages (`-contour-map-`)
"""

from __future__ import annotations

import csv
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.request import Request, urlopen

INDEX_URL = "https://dec.ny.gov/sitemap.xml"
OUT_DIR = Path("/Users/Ashar/Documents/fish/data/bathymetry/ny")
OUT_CSV = OUT_DIR / "ny_dec_map_inventory.csv"
OUT_CONTOUR_CSV = OUT_DIR / "ny_dec_contour_pages.csv"
NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def fetch_text(url: str) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urlopen(req, timeout=60).read().decode("utf-8", "ignore")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    index_root = ET.fromstring(fetch_text(INDEX_URL))
    page_urls = [loc.text for loc in index_root.findall("sm:sitemap/sm:loc", NS) if loc.text]

    urls: list[str] = []
    for page_url in page_urls:
        page_root = ET.fromstring(fetch_text(page_url))
        for loc in page_root.findall("sm:url/sm:loc", NS):
            url = (loc.text or "").strip()
            low = url.lower()
            if "/places-to-go/maps/" in low or "/outdoor/" in low:
                if any(token in low for token in ("lake", "pond", "reservoir", "fishing", "map")):
                    urls.append(url)

    unique_urls = sorted(set(urls))
    contour_urls = [url for url in unique_urls if "-contour-map-" in url.lower()]

    with OUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["url"])
        for url in unique_urls:
            writer.writerow([url])

    with OUT_CONTOUR_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["url", "slug"])
        for url in contour_urls:
            writer.writerow([url, url.rstrip("/").split("/")[-1]])

    print(f"Wrote {OUT_CSV} ({len(unique_urls)} candidate URLs)")
    print(f"Wrote {OUT_CONTOUR_CSV} ({len(contour_urls)} contour-map pages)")


if __name__ == "__main__":
    main()
