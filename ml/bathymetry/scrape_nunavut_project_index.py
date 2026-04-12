#!/usr/bin/env python3
"""
Build a Nunavut bathymetry project/index lane.

Nunavut does not currently expose a territory-wide inland bathymetry dataset in
our sweep, but official procurement and coastal-resource inventory documents
confirm real bathymetry project activity.
"""

from __future__ import annotations

import csv
from pathlib import Path


OUT_DIR = Path("/Users/Ashar/Documents/fish/data/bathymetry/nu")
OUT_CSV = OUT_DIR / "nunavut_bathymetry_project_index.csv"

SEEDS = [
    {
        "kind": "procurement",
        "title": "2022-23 Procurement Activity Report",
        "url": "https://nni.gov.nu.ca/sites/default/files/PAR%202022-23%20EN.pdf",
        "notes": "Official procurement report that includes a third-party bathymetry survey line item for Iqaluit.",
    },
    {
        "kind": "inventory",
        "title": "Nunavut Coastal Resource Inventory - Cambridge Bay",
        "url": "https://www.gov.nu.ca/sites/default/files/documents/2022-07/ncri_cambridge_bay_en.pdf",
        "notes": "Official coastal resource inventory with mapped coastal and waterbody context.",
    },
    {
        "kind": "inventory",
        "title": "Nunavut Coastal Resource Inventory - Qikiqtarjuaq",
        "url": "https://www.gov.nu.ca/sites/default/files/documents/2022-07/ncri_qikiqtarjuaq_en.pdf",
        "notes": "Official coastal resource inventory with mapped coastal and waterbody context.",
    },
]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["kind", "title", "url", "notes"])
        writer.writeheader()
        writer.writerows(SEEDS)

    print(f"Wrote {OUT_CSV} ({len(SEEDS)} Nunavut project/index rows)")


if __name__ == "__main__":
    main()
