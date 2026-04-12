#!/usr/bin/env python3
"""
Scrape the official South Dakota GFP fisheries reports app into a bathymetry PDF inventory.

Source:
  https://apps.sd.gov/GF56FisheriesReports/

The reports app embeds a JSON payload of report records in the page HTML and
downloads individual PDFs via ExportPDF.ashx?ReportID=<id>.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import requests


URL = "https://apps.sd.gov/GF56FisheriesReports/"
EXPORT_BASE = "https://apps.sd.gov/GF56FisheriesReports/ExportPDF.ashx?ReportID={report_id}"
OUTPUT_DIR = Path("/Users/Ashar/Documents/fish/data/bathymetry/sd")
OUTPUT_CSV = OUTPUT_DIR / "sd_gfp_fisheries_inventory.csv"
OUTPUT_BATHY_CSV = OUTPUT_DIR / "sd_gfp_bathymetry_inventory.csv"
TARGET_TYPES = {"Lake Maps", "Lake Survey Report"}


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    html = requests.get(URL, timeout=60, headers={"User-Agent": "Mozilla/5.0"}).text
    match = re.search(r"var reports = (\[.*?\]);", html, re.S)
    if not match:
        raise RuntimeError("Could not find embedded reports JSON on South Dakota GFP page")

    reports = json.loads(match.group(1))

    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["report_id", "report_type", "year", "waterbody", "county", "site", "pdf_url", "source_page", "jurisdiction"]
        )
        for report in reports:
            writer.writerow(
                [
                    report.get("ID"),
                    report.get("ReportType"),
                    report.get("Year"),
                    report.get("Waterbody"),
                    report.get("County"),
                    report.get("Site"),
                    EXPORT_BASE.format(report_id=report.get("ID")),
                    URL,
                    "sd",
                ]
            )

    bathy_reports = [report for report in reports if report.get("ReportType") in TARGET_TYPES]
    with OUTPUT_BATHY_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["report_id", "report_type", "year", "waterbody", "county", "site", "pdf_url", "source_page", "jurisdiction"]
        )
        for report in bathy_reports:
            writer.writerow(
                [
                    report.get("ID"),
                    report.get("ReportType"),
                    report.get("Year"),
                    report.get("Waterbody"),
                    report.get("County"),
                    report.get("Site"),
                    EXPORT_BASE.format(report_id=report.get("ID")),
                    URL,
                    "sd",
                ]
            )

    print(
        f"Wrote {OUTPUT_CSV} ({len(reports)} total reports) and "
        f"{OUTPUT_BATHY_CSV} ({len(bathy_reports)} bathymetry PDFs)"
    )


if __name__ == "__main__":
    main()
