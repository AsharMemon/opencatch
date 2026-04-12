#!/usr/bin/env python3
"""
Convert targeted page-source probe hits into concrete fetch_direct_official_source rows.

This is intentionally separate from the main source-master build so we can run
focused probe passes against high-priority unmatched lakes without rebuilding
the entire global queue every time.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_INPUT = Path("/Users/Ashar/Documents/fish/data/bathymetry/gpsnautical/gpsnautical_page_source_probe_priority.csv")
DEFAULT_OUTPUT_CSV = Path("/Users/Ashar/Documents/fish/data/bathymetry/gpsnautical/gpsnautical_direct_source_queue.csv")
DEFAULT_SUMMARY_JSON = Path("/Users/Ashar/Documents/fish/data/bathymetry/gpsnautical/gpsnautical_direct_source_queue_summary.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", default=[], help="Input CSV path. Repeatable.")
    parser.add_argument("--input-glob", default="", help="Glob for multiple probe CSVs.")
    parser.add_argument("--output-csv", type=Path, default=DEFAULT_OUTPUT_CSV)
    parser.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY_JSON)
    return parser.parse_args()


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def resolve_inputs(args: argparse.Namespace) -> list[Path]:
    explicit = [Path(value) for value in args.input]
    if explicit:
        return explicit
    if args.input_glob:
        root = Path("/")
        if args.input_glob.startswith("/"):
            return sorted(root.glob(args.input_glob.lstrip("/")))
        return sorted(Path.cwd().glob(args.input_glob))
    return [DEFAULT_INPUT]


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
    inputs = resolve_inputs(args)
    rows: list[dict[str, str]] = []
    for path in inputs:
        rows.extend(read_rows(path))
    deduped: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for row in rows:
        label = row.get("source_hint_family") or ""
        if not label:
            continue
        key = (
            row.get("jurisdiction") or "",
            row.get("lake_name") or "",
            row.get("lake_url") or "",
            label,
        )
        if key not in deduped:
            deduped[key] = row

    hits = [
        {
            **row,
            "retrieval_stage": "fetch_direct_official_source",
            "retrieval_status": "page_source_hint_found",
            "retrieval_confidence": "medium",
            "retrieval_action": "fetch_upstream_official_source",
            "resolved_source_family": row.get("source_hint_family") or "",
            "retrieval_note": row.get("source_hint_snippet") or "Official source hint found on page.",
        }
        for row in deduped.values()
    ]
    hits.sort(key=lambda row: (row["jurisdiction"], row["lake_name"], row["resolved_source_family"]))
    write_csv(args.output_csv, hits)

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "row_count": len(hits),
        "label_counts": dict(Counter(row["source_hint_family"] for row in hits)),
        "jurisdiction_counts": dict(Counter(row["jurisdiction"] for row in hits)),
        "inputs": [str(path) for path in inputs],
    }
    write_json(args.summary_json, summary)

    print(f"Wrote {len(hits)} direct-source rows")
    print(f"CSV: {args.output_csv}")
    print(f"Summary: {args.summary_json}")


if __name__ == "__main__":
    main()
