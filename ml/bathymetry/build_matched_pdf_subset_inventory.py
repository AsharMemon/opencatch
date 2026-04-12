#!/usr/bin/env python3
"""
Materialize a subset inventory CSV for lakes already matched in the easy
retrieval queue.

This lets us push only the lakes we have already crosswalked to official PDF
inventory rows through the existing download/digitize pipeline, instead of
rerunning a full jurisdiction blindly.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

from build_gpsnautical_easy_retrieval_queue import (
    SUPPORTING_PDF_JURISDICTIONS,
    build_pdf_row,
    load_source_assets,
)

DEFAULT_QUEUE = Path("/Users/Ashar/Documents/fish/data/bathymetry/gpsnautical/gpsnautical_easy_retrieval_queue.csv")
DEFAULT_COMPLETION_QUEUE = Path("/Users/Ashar/Documents/fish/data/bathymetry/gpsnautical/gpsnautical_completion_queue.csv")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument("--completion-queue", type=Path, default=DEFAULT_COMPLETION_QUEUE)
    parser.add_argument("--jurisdiction", required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--summary-json", type=Path, required=True)
    parser.add_argument(
        "--direct-from-completion",
        action="store_true",
        help="Match the jurisdiction directly from gpsnautical_completion_queue.csv instead of relying on a prebuilt easy queue.",
    )
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


def build_subset(queue_rows: list[dict[str, str]], jurisdiction: str) -> tuple[list[dict[str, str]], dict[str, Any]]:
    matched = [
        row
        for row in queue_rows
        if row.get("jurisdiction") == jurisdiction
        and row.get("retrieval_status") in {"inventory_match_found", "manifest_match_found"}
        and row.get("matched_inventory_url")
    ]

    deduped: dict[str, dict[str, str]] = {}
    duplicate_count = 0
    for row in matched:
        key = row["matched_inventory_url"].strip()
        if not key:
            continue
        if key in deduped:
            duplicate_count += 1
            existing = deduped[key]
            gps_names = {
                name.strip()
                for name in (existing.get("gps_lake_name") or "").split(" | ")
                if name.strip()
            }
            gps_names.add((row.get("lake_name") or "").strip())
            existing["gps_lake_name"] = " | ".join(sorted(gps_names))
            continue

        deduped[key] = {
            "pdf_url": row["matched_inventory_url"].strip(),
            "lake_name": (row.get("matched_inventory_name") or row.get("lake_name") or "").strip(),
            "gps_lake_name": (row.get("lake_name") or "").strip(),
            "jurisdiction": jurisdiction,
            "source_queue_stage": row.get("retrieval_stage") or "",
            "source_queue_status": row.get("retrieval_status") or "",
            "inventory_match_score": row.get("inventory_match_score") or "",
            "manifest_match_score": row.get("manifest_match_score") or "",
            "inventory_artifact_path": row.get("inventory_artifact_path") or "",
            "manifest_artifact_path": row.get("manifest_artifact_path") or "",
        }

    subset_rows = sorted(deduped.values(), key=lambda row: row["lake_name"].lower())
    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "jurisdiction": jurisdiction,
        "matched_queue_rows": len(matched),
        "unique_inventory_rows": len(subset_rows),
        "deduped_duplicates": duplicate_count,
        "queue_status_counts": dict(Counter(row.get("retrieval_status") or "" for row in matched)),
        "lake_name_examples": [row["lake_name"] for row in subset_rows[:10]],
    }
    return subset_rows, summary


def build_subset_from_completion(
    completion_rows: list[dict[str, str]],
    jurisdiction: str,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    assets = load_source_assets()
    easy_rows: list[dict[str, str]] = []

    for row in completion_rows:
        if row.get("jurisdiction") != jurisdiction:
            continue
        stage = row.get("completion_stage") or ""
        eligible_supporting_pdf = (
            stage == "supporting_lane_upgrade_search"
            and jurisdiction in SUPPORTING_PDF_JURISDICTIONS
        )
        if stage != "resolve_official_pdf_and_digitize" and not eligible_supporting_pdf:
            continue
        easy_row = build_pdf_row(row, assets)
        if easy_row.get("retrieval_status") in {"inventory_match_found", "manifest_match_found"}:
            easy_rows.append(easy_row)

    subset_rows, summary = build_subset(easy_rows, jurisdiction)
    summary["source_mode"] = "direct_from_completion"
    summary["completion_queue_input"] = str(DEFAULT_COMPLETION_QUEUE)
    return subset_rows, summary


def main() -> None:
    args = parse_args()
    if args.direct_from_completion:
        completion_rows = read_csv(args.completion_queue)
        subset_rows, summary = build_subset_from_completion(completion_rows, args.jurisdiction)
    else:
        queue_rows = read_csv(args.queue)
        subset_rows, summary = build_subset(queue_rows, args.jurisdiction)
    write_csv(args.output_csv, subset_rows)
    write_json(args.summary_json, summary)
    print(f"Wrote {len(subset_rows)} rows for {args.jurisdiction}")
    print(f"CSV: {args.output_csv}")
    print(f"Summary: {args.summary_json}")


if __name__ == "__main__":
    main()
