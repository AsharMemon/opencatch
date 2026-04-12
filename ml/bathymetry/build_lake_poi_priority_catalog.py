#!/usr/bin/env python3
"""
Build a priority subset of the GPS lake catalog for POI harvesting.

This is useful when we want fresh lake-local POIs quickly without waiting for
the full 29k+ lake wave to finish. The subset favors larger, route-relevant
waterbodies where gas, marinas, launches, and access infrastructure are more
likely to exist.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any


DEFAULT_INPUT_JSON = Path("/Users/Ashar/Documents/fish/data/bathymetry/gpsnautical/gpsnautical_app_lake_catalog.json")
DEFAULT_OUTPUT_JSON = Path("/Users/Ashar/Documents/fish/data/bathymetry/gpsnautical/gpsnautical_priority_poi_catalog.json")
DEFAULT_SUMMARY_JSON = Path("/Users/Ashar/Documents/fish/data/bathymetry/gpsnautical/gpsnautical_priority_poi_catalog_summary.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-json", type=Path, default=DEFAULT_INPUT_JSON)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY_JSON)
    parser.add_argument("--max-rows", type=int, default=750)
    parser.add_argument("--country-filter", default="US,CA")
    parser.add_argument("--jurisdiction-filter", default="")
    parser.add_argument("--min-diagonal-km", type=float, default=3.0)
    parser.add_argument("--max-diagonal-km", type=float, default=45.0)
    parser.add_argument("--allowed-waterbody-types", default="", help="Comma-separated allowed water body types (lake,reservoir,river,canal,bay,stream,pond).")
    parser.add_argument("--name-exclude-regex", default="", help="Optional regex to exclude noisy names such as river sections or lock segments.")
    return parser.parse_args()


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def country_allowed(row: dict[str, Any], allowed: set[str]) -> bool:
    return not allowed or str(row.get("country") or "").strip() in allowed


def jurisdiction_allowed(row: dict[str, Any], allowed: set[str]) -> bool:
    return not allowed or str(row.get("jurisdiction") or "").strip() in allowed


def priority_score(row: dict[str, Any]) -> tuple[float, float, float, str]:
    waterbody_type = str(row.get("waterBodyType") or "").strip().lower()
    diagonal = float(row.get("bboxDiagonalKm") or 0.0)
    area_acres = float(row.get("areaAcres") or 0.0)
    shoreline = float(row.get("shorelineMiles") or 0.0)
    type_bonus = {
        "bay": 8.0,
        "canal": 7.0,
        "reservoir": 6.0,
        "river": 5.0,
        "lake": 4.0,
        "stream": 1.0,
        "pond": -3.0,
    }.get(waterbody_type, 0.0)
    return (
        type_bonus + diagonal,
        area_acres,
        shoreline,
        str(row.get("name") or ""),
    )


def main() -> None:
    args = parse_args()
    rows = json.loads(args.input_json.read_text(encoding="utf-8"))
    keep_countries = {part.strip() for part in args.country_filter.split(",") if part.strip()}
    keep_jurisdictions = {part.strip() for part in args.jurisdiction_filter.split(",") if part.strip()}
    keep_types = {part.strip().lower() for part in args.allowed_waterbody_types.split(",") if part.strip()}
    name_exclude = None
    if args.name_exclude_regex:
        import re

        name_exclude = re.compile(args.name_exclude_regex, re.IGNORECASE)

    deduped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        if not country_allowed(row, keep_countries):
            continue
        if not jurisdiction_allowed(row, keep_jurisdictions):
            continue
        name = str(row.get("name") or "").strip()
        waterbody_type = str(row.get("waterBodyType") or "").strip().lower()
        diagonal = float(row.get("bboxDiagonalKm") or 0.0)
        if not name:
            continue
        if waterbody_type == "pond":
            continue
        if keep_types and waterbody_type not in keep_types:
            continue
        if name_exclude and name_exclude.search(name):
            continue
        if diagonal < args.min_diagonal_km or diagonal > args.max_diagonal_km:
            continue
        key = (
            str(row.get("country") or "").strip(),
            str(row.get("jurisdiction") or "").strip(),
            name.lower(),
        )
        existing = deduped.get(key)
        if existing is None or priority_score(row) > priority_score(existing):
            deduped[key] = row

    eligible = list(deduped.values())
    eligible.sort(key=priority_score, reverse=True)
    subset = eligible[: args.max_rows]

    write_json(args.output_json, subset)
    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "row_count": len(subset),
        "max_rows": args.max_rows,
        "country_filter": args.country_filter,
        "jurisdiction_filter": args.jurisdiction_filter,
        "min_diagonal_km": args.min_diagonal_km,
        "max_diagonal_km": args.max_diagonal_km,
        "allowed_waterbody_types": sorted(keep_types),
        "name_exclude_regex": args.name_exclude_regex,
        "input_json": str(args.input_json),
        "output_json": str(args.output_json),
        "top_examples": [
            {
                "name": row.get("name"),
                "jurisdiction": row.get("jurisdiction"),
                "country": row.get("country"),
                "waterBodyType": row.get("waterBodyType"),
                "bboxDiagonalKm": row.get("bboxDiagonalKm"),
                "areaAcres": row.get("areaAcres"),
            }
            for row in subset[:15]
        ],
    }
    write_json(args.summary_json, summary)

    print(f"Wrote {len(subset)} priority lakes")
    print(f"Catalog: {args.output_json}")
    print(f"Summary: {args.summary_json}")


if __name__ == "__main__":
    main()
