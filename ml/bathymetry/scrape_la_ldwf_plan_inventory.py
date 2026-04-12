#!/usr/bin/env python3
"""
Build a Louisiana LDWF inland-lake plan inventory.

Louisiana's LDWF site exposes real inland-lake management and aquatic vegetation
plan PDFs, but the category pages and direct asset URLs are bot-protected for
simple scripted requests. We still want a repeatable, executable official lane,
so this script materializes a curated seed inventory of verified official URLs
that can later be downloaded with a browser-capable workflow or remote runner.
"""

from __future__ import annotations

import csv
from pathlib import Path


OUT_DIR = Path("/Users/Ashar/Documents/fish/data/bathymetry/la")
OUT_CSV = OUT_DIR / "la_ldwf_plan_inventory.csv"

SOURCE_PAGE = "https://www.wlf.louisiana.gov/resources/category/freshwater-inland-fish/inland-waterbody-management-plans"
VEG_PAGE = "https://www.wlf.louisiana.gov/resources/category/freshwater-inland-fish/aquatic-vegetation-control-plans"

SEEDS = [
    {
        "lake_name": "Bussey Brake",
        "pdf_url": "https://www.wlf.louisiana.gov/assets/Resources/Publications/Freshwater_Inland_Fish/Inland-Waterbody-Management-Plans/Bussey-Brake-MP-A_2021.pdf",
        "source_page": SOURCE_PAGE,
        "evidence_url": "https://www.wlf.louisiana.gov/news/drawdown-of-lake-bistineau-concludes",
        "report_type": "Inland Waterbody Management Plan",
        "jurisdiction": "la",
        "status": "seeded_official_url",
        "access_mode": "browser_or_search_index",
    },
    {
        "lake_name": "Bundick Lake",
        "pdf_url": "https://www.wlf.louisiana.gov/assets/Resources/Publications/Freshwater_Inland_Fish/Inland-Waterbody-Management-Plans/Bundick-Lake-MP-B-2020.pdf",
        "source_page": SOURCE_PAGE,
        "evidence_url": "https://www.wlf.louisiana.gov/news/ldwf-stocks-bundick-lake-following-recent-hurricanerelated-fish-kills",
        "report_type": "Inland Waterbody Management Plan",
        "jurisdiction": "la",
        "status": "seeded_official_url",
        "access_mode": "browser_or_search_index",
    },
    {
        "lake_name": "Chicot Lake",
        "pdf_url": "https://www.wlf.louisiana.gov/assets/Resources/Publications/Freshwater_Inland_Fish/Inland-Waterbody-Management-Plans/Chicot-Lake-MP-B-2021.pdf",
        "source_page": SOURCE_PAGE,
        "evidence_url": SOURCE_PAGE,
        "report_type": "Inland Waterbody Management Plan",
        "jurisdiction": "la",
        "status": "seeded_official_url",
        "access_mode": "browser_or_search_index",
    },
    {
        "lake_name": "Saline Lake",
        "pdf_url": "https://www.wlf.louisiana.gov/assets/Resources/Publications/Freshwater_Inland_Fish/Inland-Waterbody-Management-Plans/Saline_Lake_MP-B_2019.pdf",
        "source_page": SOURCE_PAGE,
        "evidence_url": SOURCE_PAGE,
        "report_type": "Inland Waterbody Management Plan",
        "jurisdiction": "la",
        "status": "seeded_official_url",
        "access_mode": "browser_or_search_index",
    },
    {
        "lake_name": "Iatt Lake",
        "pdf_url": "https://www.wlf.louisiana.gov/assets/Resources/Publications/Freshwater_Inland_Fish/Inland-Waterbody-Management-Plans/Iatt-Lake-MP-B-2022.pdf",
        "source_page": SOURCE_PAGE,
        "evidence_url": "https://www.wlf.louisiana.gov/news/ldwf-schedules-drawdown-for-iatt-lake-2022",
        "report_type": "Inland Waterbody Management Plan",
        "jurisdiction": "la",
        "status": "seeded_official_url",
        "access_mode": "browser_or_search_index",
    },
    {
        "lake_name": "Lake Fields / Lake Long / Company Canal",
        "pdf_url": "https://www.wlf.louisiana.gov/assets/Resources/Publications/Freshwater_Inland_Fish/Aquatic-Vegetation-Control-Plans/Lake-Fields-Lake-Long-Company-Canal-AVCP-2024.pdf",
        "source_page": VEG_PAGE,
        "evidence_url": VEG_PAGE,
        "report_type": "Aquatic Vegetation Control Plan",
        "jurisdiction": "la",
        "status": "seeded_official_url",
        "access_mode": "browser_or_search_index",
    },
]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "lake_name",
                "pdf_url",
                "source_page",
                "evidence_url",
                "report_type",
                "jurisdiction",
                "status",
                "access_mode",
            ],
        )
        writer.writeheader()
        writer.writerows(SEEDS)

    print(f"Wrote {OUT_CSV} ({len(SEEDS)} seeded official LDWF plan URLs)")


if __name__ == "__main__":
    main()
