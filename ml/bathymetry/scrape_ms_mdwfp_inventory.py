#!/usr/bin/env python3
"""
Scrape the official Mississippi MDWFP lake depth maps page into a PDF inventory.

Source:
  https://www.mdwfp.com/fishing-boating/lake-depth-maps/
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from urllib.parse import urljoin

import requests


URL = "https://www.mdwfp.com/fishing-boating/lake-depth-maps/"
OUTPUT_DIR = Path("/Users/Ashar/Documents/fish/data/bathymetry/ms")
OUTPUT_CSV = OUTPUT_DIR / "ms_lake_depth_inventory.csv"


def prettify_name(url: str) -> str:
    stem = Path(url).stem
    stem = stem.replace("%20", " ")
    stem = re.sub(r"\s+", " ", stem.replace("-", " ")).strip()
    return stem.title()


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    resp = requests.get(URL, timeout=60, headers={"User-Agent": "Mozilla/5.0"}, verify=False)
    resp.raise_for_status()
    html = resp.text

    pdfs: list[str] = []
    for match in re.finditer(r'href=["\']([^"\']+\.pdf)["\']', html, re.I):
        href = urljoin(URL, match.group(1))
        pdfs.append(href)

    unique_pdfs = sorted(set(pdfs))
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["lake_name", "pdf_url", "source_page", "jurisdiction"])
        for pdf_url in unique_pdfs:
            writer.writerow([prettify_name(pdf_url), pdf_url, URL, "ms"])

    print(f"Wrote {OUTPUT_CSV} ({len(unique_pdfs)} official MDWFP PDFs)")


if __name__ == "__main__":
    main()
