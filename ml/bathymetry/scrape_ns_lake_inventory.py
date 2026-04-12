#!/usr/bin/env python3
"""
Scrape the official Nova Scotia lake inventory page into a PDF inventory.

Source:
  https://novascotia.ca/fish/sportfishing/our-lakes/lake-inventory/
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from urllib.parse import urljoin

import requests


URL = "https://novascotia.ca/fish/sportfishing/our-lakes/lake-inventory/"
PDF_ROOT_FRAGMENT = "/fish/documents/lake-inventory-maps/"
OUTPUT_DIR = Path("/Users/Ashar/Documents/fish/data/bathymetry/ns")
OUTPUT_CSV = OUTPUT_DIR / "ns_lake_inventory.csv"


def parse_region_and_name(url: str) -> tuple[str, str]:
    stem = Path(url).stem
    parts = stem.split("-")
    region = parts[1] if len(parts) > 2 else ""
    lake_name = "-".join(parts[2:] if len(parts) > 2 else parts).replace("-", " ").strip()
    lake_name = re.sub(r"\s+", " ", lake_name)
    return region, lake_name.title()


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    html = requests.get(URL, timeout=60, headers={"User-Agent": "Mozilla/5.0"}).text

    pdfs: list[str] = []
    for match in re.finditer(r'href=["\']([^"\']+\.pdf)["\']', html, re.I):
        href = urljoin(URL, match.group(1))
        if PDF_ROOT_FRAGMENT not in href:
            continue
        pdfs.append(href)

    unique_pdfs = sorted(set(pdfs))
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["region_code", "lake_name", "pdf_url", "source_page", "jurisdiction"])
        for pdf_url in unique_pdfs:
            region, lake_name = parse_region_and_name(pdf_url)
            writer.writerow([region, lake_name, pdf_url, URL, "ns"])

    print(f"Wrote {OUTPUT_CSV} ({len(unique_pdfs)} official Nova Scotia inventory PDFs)")


if __name__ == "__main__":
    main()
