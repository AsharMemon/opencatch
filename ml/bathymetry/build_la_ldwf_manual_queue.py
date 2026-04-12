#!/usr/bin/env python3
"""
Build a manual/search retrieval queue for blocked Louisiana LDWF plan PDFs.

Louisiana's seeded LDWF asset URLs are real enough to track, but both scripted
and Playwright-browser retrieval still return 403. This script packages the lane
into a concrete operator queue with suggested search strings, evidence pages,
and the latest blocked manifest so future work can continue efficiently.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path


INVENTORY_CSV = Path("/Users/Ashar/Documents/fish/data/bathymetry/la/la_ldwf_plan_inventory.csv")
REMOTE_MANIFEST = Path("/Users/Ashar/Documents/fish/data/bathymetry/la/remote_manifests/la_ldwf_retry_download_manifest.json")
OUT_DIR = Path("/Users/Ashar/Documents/fish/data/bathymetry/la/manual_queue")
OUT_CSV = OUT_DIR / "la_ldwf_manual_retrieval_queue.csv"
OUT_MD = OUT_DIR / "README.md"


def load_inventory() -> list[dict]:
    with INVENTORY_CSV.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_manifest() -> dict[str, dict]:
    if not REMOTE_MANIFEST.exists():
        return {}
    rows = json.loads(REMOTE_MANIFEST.read_text())
    return {row["pdf_url"]: row for row in rows}


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()
    rows = []
    for row in load_inventory():
        blocked = manifest.get(row["pdf_url"], {})
        lake = row["lake_name"]
        report_type = row["report_type"]
        rows.append(
            {
                "lake_name": lake,
                "report_type": report_type,
                "pdf_url": row["pdf_url"],
                "source_page": row["source_page"],
                "evidence_url": row["evidence_url"],
                "blocked_status": blocked.get("status", ""),
                "blocked_http_status": blocked.get("http_status", ""),
                "search_query_primary": f'site:wlf.louisiana.gov "{lake}" "{report_type}"',
                "search_query_exact_pdf": f'"{Path(row["pdf_url"]).name}"',
                "operator_next_step": "Open evidence/source pages in a real browser or search index and capture the official PDF manually.",
            }
        )

    fieldnames = [
        "lake_name",
        "report_type",
        "pdf_url",
        "source_page",
        "evidence_url",
        "blocked_status",
        "blocked_http_status",
        "search_query_primary",
        "search_query_exact_pdf",
        "operator_next_step",
    ]

    with OUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    OUT_MD.write_text(
        "\n".join(
            [
                "# Louisiana LDWF Manual Retrieval Queue",
                "",
                "These LDWF plan URLs are seeded from official evidence and inventories, but both direct",
                "requests and remote Playwright browser runs returned 403 for the asset URLs.",
                "",
                f"- Inventory: `{INVENTORY_CSV}`",
                f"- Blocked manifest: `{REMOTE_MANIFEST}`",
                f"- Queue CSV: `{OUT_CSV}`",
                "",
                "Recommended operator workflow:",
                "1. Open `evidence_url` and `source_page` in a real browser.",
                "2. Use the suggested search strings to find the same official PDF via search results or site search.",
                "3. Save the PDF and record the recovered local path beside the queue row.",
                "",
            ]
        )
    )

    print(f"Wrote {OUT_CSV} and {OUT_MD} ({len(rows)} queue rows)")


if __name__ == "__main__":
    main()
