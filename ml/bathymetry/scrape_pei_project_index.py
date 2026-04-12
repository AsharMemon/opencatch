#!/usr/bin/env python3
"""
Build a Prince Edward Island inland-water project/index lane.

PEI does not currently expose a direct inland bathymetry dataset in our sweep,
but it does expose official angling, GIS, and project resources that are worth
tracking as a project/index-supported lane.
"""

from __future__ import annotations

import csv
from pathlib import Path


OUT_DIR = Path("/Users/Ashar/Documents/fish/data/bathymetry/pe")
OUT_CSV = OUT_DIR / "pei_project_index.csv"

SEEDS = [
    {
        "kind": "portal",
        "title": "Angling Resources and Information Centre",
        "url": "https://www.princeedwardisland.ca/en/information/environment-energy-and-climate-action/angling-resources-and-information-centre",
        "notes": "Official freshwater angling hub linking the PEI Fishing Locations Map and annual angling summaries.",
    },
    {
        "kind": "publication",
        "title": "2025 Angling Summary",
        "url": "https://www.princeedwardisland.ca/en/publication/2025-angling-summary",
        "notes": "Official PEI freshwater angling publication.",
    },
    {
        "kind": "catalog",
        "title": "PEI GIS catalog",
        "url": "https://gov.pe.ca/gis/index.php3?amp=&lang=E&number=77543",
        "notes": "Official PEI GIS catalog entry point.",
    },
    {
        "kind": "data",
        "title": "PEI open data portal",
        "url": "https://data.princeedwardisland.ca/",
        "notes": "Official PEI open data portal.",
    },
]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["kind", "title", "url", "notes"])
        writer.writeheader()
        writer.writerows(SEEDS)

    print(f"Wrote {OUT_CSV} ({len(SEEDS)} PEI project/index rows)")


if __name__ == "__main__":
    main()
