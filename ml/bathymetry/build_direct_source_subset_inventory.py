#!/usr/bin/env python3
"""
Materialize the direct-source probe hits into per-jurisdiction fetch inventories.

This is the direct-source analogue to the matched PDF subset inventories: it
turns a merged `fetch_direct_official_source` queue into concrete CSVs grouped
by jurisdiction, annotated with the upstream program/fetch lane we should run.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path("/Users/Ashar/Documents/fish")
DEFAULT_INPUT = ROOT / "data" / "bathymetry" / "gpsnautical" / "gpsnautical_direct_source_queue_parallel_batch_partial_allwaves.csv"
DEFAULT_MANIFEST_CSV = ROOT / "data" / "bathymetry" / "gpsnautical" / "gpsnautical_direct_source_inventory_manifest.csv"
DEFAULT_SUMMARY_JSON = ROOT / "data" / "bathymetry" / "gpsnautical" / "gpsnautical_direct_source_inventory_manifest_summary.json"

JURISDICTION_DIRS = {
    "Arizona": "az",
    "California": "ca",
    "Colorado": "co",
    "Idaho": "id_state",
    "Iowa": "ia",
    "Missouri": "mo",
    "Nevada": "nv",
    "New Mexico": "nm",
    "New York": "ny",
    "North Carolina": "nc",
    "Oklahoma": "ok",
    "Oregon": "or_state",
    "Pennsylvania": "pa",
    "Tennessee": "tn",
    "Virginia": "va",
    "West Virginia": "wv",
    "Wisconsin": "wi",
    "Wyoming": "wy",
}

FAMILY_FETCH_HINTS = {
    "USACE": {
        "fetch_inventory_lane": "usace_reservoir_program",
        "fetcher_hint": str(ROOT / "ml" / "bathymetry" / "fetch_usace_ienc.py"),
        "upstream_search_scope": "USACE district reservoir/project pages, chart PDFs, and survey attachments",
    },
    "USBR": {
        "fetch_inventory_lane": "usbr_reservoir_program",
        "fetcher_hint": str(ROOT / "ml" / "bathymetry" / "reservoir" / "fetch_usbr_surveys.py"),
        "upstream_search_scope": "USBR reservoir survey catalog, published PDFs, and A-E tables",
    },
    "TVA": {
        "fetch_inventory_lane": "tva_reservoir_program",
        "fetcher_hint": str(ROOT / "ml" / "bathymetry" / "fetch_state_bathymetry.py"),
        "upstream_search_scope": "TVA reservoir charts, contour maps, and navigation/publication pages",
    },
    "DEC": {
        "fetch_inventory_lane": "state_dec_program",
        "fetcher_hint": str(ROOT / "ml" / "bathymetry" / "build_ny_dec_contour_index.py"),
        "upstream_search_scope": "New York DEC contour map pages and linked official assets",
    },
    "DNR": {
        "fetch_inventory_lane": "state_dnr_program",
        "fetcher_hint": str(ROOT / "ml" / "bathymetry" / "fetch_state_bathymetry.py"),
        "upstream_search_scope": "state DNR lake survey portal, GIS services, and downloadable contour assets",
    },
    "DEP/DEQ/DEM/IFW/PFBC": {
        "fetch_inventory_lane": "state_agency_program",
        "fetcher_hint": str(ROOT / "ml" / "bathymetry" / "fetch_state_bathymetry.py"),
        "upstream_search_scope": "state agency GIS, contour pages, and public survey downloads",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--manifest-csv", type=Path, default=DEFAULT_MANIFEST_CSV)
    parser.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY_JSON)
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def normalize_rows(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, str, str, str], dict[str, Any]] = {}

    for row in rows:
        if row.get("retrieval_stage") != "fetch_direct_official_source":
            continue
        jurisdiction = (row.get("jurisdiction") or "").strip()
        lake_name = (row.get("lake_name") or "").strip()
        lake_url = (row.get("lake_url") or "").strip()
        family = (row.get("resolved_source_family") or row.get("source_hint_family") or "").strip()
        if not (jurisdiction and lake_name and lake_url and family):
            continue

        key = (jurisdiction, lake_name, lake_url, family)
        hints = FAMILY_FETCH_HINTS.get(family, {
            "fetch_inventory_lane": "direct_official_source_search",
            "fetcher_hint": "",
            "upstream_search_scope": "official source pages linked from the probe hint",
        })
        deduped[key] = {
            "catalog": (row.get("catalog") or "").strip(),
            "jurisdiction": jurisdiction,
            "lake_name": lake_name,
            "lake_url": lake_url,
            "resolved_source_family": family,
            "retrieval_stage": "fetch_direct_official_source",
            "retrieval_status": (row.get("retrieval_status") or "").strip(),
            "retrieval_confidence": (row.get("retrieval_confidence") or "").strip(),
            "fetch_inventory_lane": hints["fetch_inventory_lane"],
            "fetcher_hint": hints["fetcher_hint"],
            "upstream_search_scope": hints["upstream_search_scope"],
            "source_hint_snippet": (row.get("source_hint_snippet") or "").strip(),
            "retrieval_note": (row.get("retrieval_note") or "").strip(),
        }

    output = sorted(
        deduped.values(),
        key=lambda row: (
            row["jurisdiction"],
            row["resolved_source_family"],
            row["lake_name"].lower(),
        ),
    )
    for index, row in enumerate(output, start=1):
        row["priority_rank"] = index
    return output


def materialize(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["jurisdiction"]].append(row)

    manifest_rows: list[dict[str, Any]] = []
    missing_dir_jurisdictions: list[str] = []

    for jurisdiction, jurisdiction_rows in sorted(grouped.items()):
        dir_slug = JURISDICTION_DIRS.get(jurisdiction)
        if not dir_slug:
            missing_dir_jurisdictions.append(jurisdiction)
            continue

        target_dir = ROOT / "data" / "bathymetry" / dir_slug
        target_dir.mkdir(parents=True, exist_ok=True)
        inventory_csv = target_dir / f"{dir_slug}_gpsnautical_direct_source_inventory.csv"
        summary_json = target_dir / f"{dir_slug}_gpsnautical_direct_source_inventory_summary.json"

        jurisdiction_rows = sorted(
            jurisdiction_rows,
            key=lambda row: (row["resolved_source_family"], row["lake_name"].lower()),
        )
        for index, row in enumerate(jurisdiction_rows, start=1):
            row["priority_rank"] = index

        write_csv(inventory_csv, jurisdiction_rows)
        summary = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "jurisdiction": jurisdiction,
            "dir_slug": dir_slug,
            "row_count": len(jurisdiction_rows),
            "family_counts": dict(Counter(row["resolved_source_family"] for row in jurisdiction_rows)),
            "fetch_lane_counts": dict(Counter(row["fetch_inventory_lane"] for row in jurisdiction_rows)),
            "example_lakes": [row["lake_name"] for row in jurisdiction_rows[:10]],
        }
        write_json(summary_json, summary)

        manifest_rows.append({
            "jurisdiction": jurisdiction,
            "dir_slug": dir_slug,
            "inventory_csv": str(inventory_csv),
            "summary_json": str(summary_json),
            "row_count": len(jurisdiction_rows),
            "family_counts": json.dumps(summary["family_counts"], ensure_ascii=False),
            "fetch_lane_counts": json.dumps(summary["fetch_lane_counts"], ensure_ascii=False),
        })

    manifest_summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "row_count": len(rows),
        "jurisdiction_count": len(manifest_rows),
        "manifest_rows": len(manifest_rows),
        "family_counts": dict(Counter(row["resolved_source_family"] for row in rows)),
        "fetch_lane_counts": dict(Counter(row["fetch_inventory_lane"] for row in rows)),
        "missing_dir_jurisdictions": missing_dir_jurisdictions,
    }
    return manifest_rows, manifest_summary


def main() -> None:
    args = parse_args()
    source_rows = read_csv(args.input)
    normalized_rows = normalize_rows(source_rows)
    manifest_rows, manifest_summary = materialize(normalized_rows)
    write_csv(args.manifest_csv, manifest_rows)
    write_json(args.summary_json, manifest_summary)
    print(f"Wrote {len(manifest_rows)} jurisdiction inventories")
    print(f"Manifest CSV: {args.manifest_csv}")
    print(f"Summary JSON: {args.summary_json}")


if __name__ == "__main__":
    main()
