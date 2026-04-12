#!/usr/bin/env python3
"""
Build a Prince Edward Island publication inventory with direct PDF URLs.

PEI still is not a bathymetry-rich inland jurisdiction, but the angling summary
and fishing locations map are directly downloadable official PDFs and are worth
capturing as a more executable subset than a generic project index alone.
"""

from __future__ import annotations

import csv
from pathlib import Path


OUT_DIR = Path("/Users/Ashar/Documents/fish/data/bathymetry/pe")
OUT_CSV = OUT_DIR / "pei_publication_inventory.csv"

ROWS = [
    {
        "lake_name": "Prince Edward Island Angling Summary",
        "waterbody": "Prince Edward Island",
        "pdf_url": "https://www.princeedwardisland.ca/sites/default/files/23d9/Angling%20Summary.pdf",
        "source_page": "https://www.princeedwardisland.ca/en/publication/angling-summary-0",
        "evidence_url": "https://www.princeedwardisland.ca/en/information/land-and-environment/angling-resources-and-information-centre",
        "report_type": "Angling Summary",
        "jurisdiction": "pe",
        "status": "verified_pdf",
    },
    {
        "lake_name": "PEI Fishing Locations Map",
        "waterbody": "Prince Edward Island",
        "pdf_url": "https://www.princeedwardisland.ca/sites/default/files/publications/pei_fishing_locations_map.pdf",
        "source_page": "https://www.princeedwardisland.ca/en/information/land-and-environment/angling-resources-and-information-centre",
        "evidence_url": "https://www.princeedwardisland.ca/en/information/land-and-environment/angling-resources-and-information-centre",
        "report_type": "Fishing Locations Map",
        "jurisdiction": "pe",
        "status": "verified_pdf",
    },
]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "lake_name",
                "waterbody",
                "pdf_url",
                "source_page",
                "evidence_url",
                "report_type",
                "jurisdiction",
                "status",
            ],
        )
        writer.writeheader()
        writer.writerows(ROWS)

    print(f"Wrote {OUT_CSV} ({len(ROWS)} PEI official PDFs)")


if __name__ == "__main__":
    main()
