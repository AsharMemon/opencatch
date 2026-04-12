#!/usr/bin/env python3
"""
Build a North Carolina reservoir-report inventory.

North Carolina does not currently expose one clean statewide inland bathymetry
service, but official NCDEQ/NC State reservoir reports and assessments provide
an executable reservoir-subset path. This script materializes that path into a
downloadable inventory CSV.
"""

from __future__ import annotations

import csv
from pathlib import Path

import requests


USER_AGENT = "Mozilla/5.0 (compatible; OpenCatch/1.0; +https://opencatch.app)"
OUT_DIR = Path("/Users/Ashar/Documents/fish/data/bathymetry/nc")
OUT_CSV = OUT_DIR / "nc_reservoir_report_inventory.csv"

SEEDS = [
    {
        "reservoir_name": "Jordan Lake",
        "lake_name": "Jordan Lake",
        "pdf_url": "https://files.nc.gov/ncdeq/Water%20Quality/Planning/NPU/Nutrient%20Scientific%20Advisory%20Board/Reservoir-Model-Jordan-NCSU_Obenour.pdf",
        "source_page": "https://files.nc.gov/ncdeq/Water%20Quality/Planning/NPU/Nutrient%20Scientific%20Advisory%20Board/",
        "evidence_url": "https://files.nc.gov/ncdeq/Water%20Quality/Planning/NPU/Jordan/Development%20of%20the%20Jordan%20Lake%20Nutrient%20Strategy.pdf",
        "report_type": "Reservoir Model Report",
        "jurisdiction": "nc",
    },
    {
        "reservoir_name": "Jordan Lake / Neuse Basin Reservoirs",
        "lake_name": "Neuse River Basin Reservoir Assessments",
        "pdf_url": "https://files.nc.gov/ncdeq/Water%20Quality/Environmental%20Sciences/Reports/NEUSE%20%20RIVER%20BASIN%20ALL%202015.pdf",
        "source_page": "https://files.nc.gov/ncdeq/Water%20Quality/Environmental%20Sciences/Reports/",
        "evidence_url": "https://files.nc.gov/ncdeq/Water%20Quality/Environmental%20Sciences/Reports/NEUSE%20%20RIVER%20BASIN%20ALL%202015.pdf",
        "report_type": "Lake and Reservoir Assessments",
        "jurisdiction": "nc",
    },
    {
        "reservoir_name": "Jordan Lake",
        "lake_name": "Jordan Lake Nutrient Strategy",
        "pdf_url": "https://files.nc.gov/ncdeq/Water%20Quality/Planning/NPU/Jordan/Development%20of%20the%20Jordan%20Lake%20Nutrient%20Strategy.pdf",
        "source_page": "https://files.nc.gov/ncdeq/Water%20Quality/Planning/NPU/Jordan/",
        "evidence_url": "https://files.nc.gov/ncdeq/Water%20Quality/Planning/NPU/Jordan/Development%20of%20the%20Jordan%20Lake%20Nutrient%20Strategy.pdf",
        "report_type": "Reservoir Strategy Report",
        "jurisdiction": "nc",
    },
]


def probe_pdf(url: str) -> str:
    try:
        resp = requests.head(url, headers={"User-Agent": USER_AGENT}, timeout=30, allow_redirects=True)
        content_type = (resp.headers.get("content-type") or "").lower()
        return "verified_pdf" if resp.status_code == 200 and "pdf" in content_type else f"http_{resp.status_code}"
    except Exception:
        return "probe_error"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for seed in SEEDS:
        row = dict(seed)
        row["status"] = probe_pdf(seed["pdf_url"])
        rows.append(row)

    with OUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "reservoir_name",
                "lake_name",
                "pdf_url",
                "source_page",
                "evidence_url",
                "report_type",
                "jurisdiction",
                "status",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    verified = sum(1 for row in rows if row["status"] == "verified_pdf")
    print(f"Wrote {OUT_CSV} ({len(rows)} rows, {verified} verified PDFs)")


if __name__ == "__main__":
    main()
