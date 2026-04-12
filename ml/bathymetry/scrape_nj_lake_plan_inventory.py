#!/usr/bin/env python3
"""
Scrape the official New Jersey Fish & Wildlife lake management plan page.

This page is accessible even though the primary NJDEP freshwater lake-survey
page is Incapsula-protected. It gives us a small official PDF inventory that
can anchor a better NJ lake-depth acquisition path.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

URL = "https://www.nj.gov/dep/fgw/fshresmgt_lakeplans.htm"
OUT_DIR = Path("/Users/Ashar/Documents/fish/data/bathymetry/nj")
OUT_CSV = OUT_DIR / "nj_lake_plan_inventory.csv"


def fetch_text(url: str) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urlopen(req, timeout=60).read().decode("utf-8", "ignore")


def prettify_name(url: str) -> str:
    stem = Path(url).stem
    stem = re.sub(r"^lakeplan_", "", stem, flags=re.I)
    return stem.replace("_", " ").replace("-", " ").title()


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    html = fetch_text(URL)
    pdfs = []
    for match in re.finditer(r'href=["\']([^"\']+\.pdf)["\']', html, re.I):
        href = urljoin(URL, match.group(1))
        pdfs.append(href)

    unique_pdfs = sorted(set(pdfs))
    with OUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["lake_name", "url"])
        for url in unique_pdfs:
            writer.writerow([prettify_name(url), url])

    print(f"Wrote {OUT_CSV} ({len(unique_pdfs)} official PDFs)")


if __name__ == "__main__":
    main()

