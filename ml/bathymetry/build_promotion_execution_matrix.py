#!/usr/bin/env python3
"""
Build a machine-readable execution matrix for non-survey inland jurisdictions.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

ROOT = Path("/Users/Ashar/Documents/fish")
TRACKER = ROOT / "docs" / "jurisdiction-survey-grade-tracker.md"
OUTPUT = ROOT / "data" / "bathymetry" / "promotion_execution_matrix.csv"


INVENTORY_MAP = {
    "Georgia": ROOT / "data" / "bathymetry" / "ga" / "ga_pfa_pdf_inventory.csv",
    "Louisiana": ROOT / "data" / "bathymetry" / "la" / "la_ldwf_plan_inventory.csv",
    "Mississippi": ROOT / "data" / "bathymetry" / "ms" / "ms_lake_depth_inventory.csv",
    "South Dakota": ROOT / "data" / "bathymetry" / "sd" / "sd_gfp_bathymetry_inventory.csv",
    "New Brunswick": ROOT / "data" / "bathymetry" / "nb" / "nb_lake_depth_inventory.csv",
    "Nova Scotia": ROOT / "data" / "bathymetry" / "ns" / "ns_lake_inventory.csv",
    "North Carolina": ROOT / "data" / "bathymetry" / "nc" / "nc_reservoir_report_inventory.csv",
}


def parse_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in TRACKER.read_text().splitlines():
        if not line.startswith("| "):
            continue
        if line.startswith("| State") or line.startswith("| Province") or "---" in line:
            continue
        parts = [part.strip() for part in line.strip("|").split("|")]
        if len(parts) < 4:
            continue
        name, status, lead, next_move = parts[:4]
        if status == "Survey live":
            continue
        rows.append(
            {
                "jurisdiction": name,
                "status": status,
                "best_lead": lead,
                "next_move": next_move,
                "inventory_path": str(INVENTORY_MAP.get(name, "")),
            }
        )
    return rows


def classify(row: dict[str, str]) -> dict[str, str]:
    status = row["status"]
    name = row["jurisdiction"]
    lane = "research"
    launcher = ""
    ready = "discovery"
    parallel_group = "manual"

    if status == "PDF upgrade":
        lane = "pdf_promotion"
        launcher = "deploy_pdf_promotion_vast.sh"
        ready = "inventory_ready" if row["inventory_path"] else "needs_inventory"
        parallel_group = "pdf"
    elif status == "Reservoir subset":
        lane = "reservoir_subset"
        launcher = "deploy_usbr_survey_batch_vast.sh"
        ready = "group_ready" if name in {
            "Arizona", "California", "Colorado", "Idaho", "Nevada",
            "New Mexico", "Oregon", "Utah", "Wyoming"
        } else "research_ready"
        parallel_group = "reservoir"
    elif status == "Supporting live":
        lane = "supporting_upgrade"
        launcher = "source-specific"
        ready = "needs_promotion"
        parallel_group = "supporting"
    elif status == "Project/index-supported":
        lane = "index_upgrade"
        launcher = "source-specific"
        ready = "needs_index_harvest"
        parallel_group = "index"
    elif status == "Withdrawn official data":
        lane = "hold_fallback"
        launcher = ""
        ready = "blocked"
        parallel_group = "fallback"

    row = dict(row)
    row.update(
        {
            "lane": lane,
            "launcher": launcher,
            "ready_state": ready,
            "parallel_group": parallel_group,
        }
    )
    return row


def main() -> None:
    rows = [classify(row) for row in parse_rows()]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "jurisdiction",
        "status",
        "lane",
        "ready_state",
        "parallel_group",
        "launcher",
        "inventory_path",
        "best_lead",
        "next_move",
    ]
    with OUTPUT.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {OUTPUT} ({len(rows)} non-survey jurisdictions)")


if __name__ == "__main__":
    main()
