#!/usr/bin/env python3
"""
Materialize a subset inventory for matched official KML/vector contour sources.

This is the KML/vector sibling of `build_matched_pdf_subset_inventory.py`.
Today it is primarily used for Kentucky KDFWR contour maps exposed through
public Google My Maps KML exports.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_QUEUE = Path("/Users/Ashar/Documents/fish/data/bathymetry/gpsnautical/gpsnautical_easy_retrieval_queue.csv")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument("--jurisdiction", required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--summary-json", type=Path, required=True)
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


def main() -> None:
    args = parse_args()
    rows = read_csv(args.queue)
    matched = [
        row
        for row in rows
        if row.get("jurisdiction") == args.jurisdiction
        and row.get("retrieval_action") == "fetch_kml_then_normalize"
        and (row.get("matched_inventory_url") or "").strip()
    ]

    deduped: dict[str, dict[str, Any]] = {}
    duplicate_count = 0
    for row in matched:
        kml_url = row["matched_inventory_url"].strip()
        if not kml_url:
            continue
        if kml_url in deduped:
            duplicate_count += 1
            existing = deduped[kml_url]
            gps_names = {
                name.strip()
                for name in str(existing.get("gps_lake_name") or "").split(" | ")
                if name.strip()
            }
            gps_names.add((row.get("lake_name") or "").strip())
            existing["gps_lake_name"] = " | ".join(sorted(gps_names))
            continue
        deduped[kml_url] = {
            "kml_url": kml_url,
            "lake_name": (row.get("matched_inventory_name") or row.get("lake_name") or "").strip(),
            "gps_lake_name": (row.get("lake_name") or "").strip(),
            "jurisdiction": args.jurisdiction,
            "source_queue_stage": row.get("retrieval_stage") or "",
            "source_queue_status": row.get("retrieval_status") or "",
            "inventory_match_score": row.get("inventory_match_score") or "",
            "inventory_artifact_path": row.get("inventory_artifact_path") or "",
            "resolved_source_family": row.get("resolved_source_family") or "",
        }

    subset_rows = sorted(deduped.values(), key=lambda row: str(row["lake_name"]).lower())
    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "jurisdiction": args.jurisdiction,
        "matched_queue_rows": len(matched),
        "unique_inventory_rows": len(subset_rows),
        "deduped_duplicates": duplicate_count,
        "queue_status_counts": dict(Counter(row.get("source_queue_status") or "" for row in subset_rows)),
        "lake_name_examples": [row["lake_name"] for row in subset_rows[:10]],
    }

    write_csv(args.output_csv, subset_rows)
    write_json(args.summary_json, summary)
    print(f"Wrote {len(subset_rows)} KML rows for {args.jurisdiction}")
    print(f"CSV: {args.output_csv}")
    print(f"Summary: {args.summary_json}")


if __name__ == "__main__":
    main()
