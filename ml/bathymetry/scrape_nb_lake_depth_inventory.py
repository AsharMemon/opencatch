#!/usr/bin/env python3
"""
Scrape the official New Brunswick lake depths page into a PDF inventory.

Source:
  https://www2.gnb.ca/content/gnb/en/departments/erd/fish-and-wildlife/content/go-fishing/content/lake_depths.html
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from urllib.parse import urljoin

import requests


URL = (
    "https://www2.gnb.ca/content/gnb/en/departments/erd/fish-and-wildlife/"
    "content/go-fishing/content/lake_depths.html"
)
OUTPUT_DIR = Path("/Users/Ashar/Documents/fish/data/bathymetry/nb")
OUTPUT_CSV = OUTPUT_DIR / "nb_lake_depth_inventory.csv"


def prettify_name(url: str) -> str:
    stem = Path(url).stem.replace("_", " ").replace("-", " ").strip()
    return re.sub(r"\s+", " ", stem).title()


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    html = requests.get(URL, timeout=60, headers={"User-Agent": "Mozilla/5.0"}).text

    pdfs: list[str] = []
    for match in re.finditer(r'href=["\']([^"\']+\.pdf)["\']', html, re.I):
        href = urljoin(URL, match.group(1))
        if "/lake_depths/" not in href:
            continue
        pdfs.append(href)

    unique_pdfs = sorted(set(pdfs))
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["lake_name", "pdf_url", "source_page", "jurisdiction"])
        for pdf_url in unique_pdfs:
            writer.writerow([prettify_name(pdf_url), pdf_url, URL, "nb"])

    print(f"Wrote {OUTPUT_CSV} ({len(unique_pdfs)} official New Brunswick depth PDFs)")


if __name__ == "__main__":
    main()
