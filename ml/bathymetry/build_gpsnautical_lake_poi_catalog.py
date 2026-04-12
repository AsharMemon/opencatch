#!/usr/bin/env python3
"""
Build a compact app-ready lake POI catalog from harvested JSONL output.

The full JSONL harvest remains the durable source artifact. This script groups
POIs by lake id so the mobile app can quickly load lake-local points such as
ramps, marinas, bait shops, parking, and piers without re-querying every time.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


DEFAULT_INPUT_JSONL = Path("/Users/Ashar/Documents/fish/data/bathymetry/gpsnautical/gpsnautical_lake_pois.jsonl")
DEFAULT_OUTPUT_JSON = Path("/Users/Ashar/Documents/fish/data/bathymetry/gpsnautical/gpsnautical_lake_poi_catalog.json")
DEFAULT_SUMMARY_JSON = Path("/Users/Ashar/Documents/fish/data/bathymetry/gpsnautical/gpsnautical_lake_poi_catalog_summary.json")
DEFAULT_MOBILE_JSON = Path("/Users/Ashar/Documents/fish/mobile/src/data/generated/gpsLakePoiCatalog.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-jsonl", type=Path, default=DEFAULT_INPUT_JSONL)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY_JSON)
    parser.add_argument("--mobile-json", type=Path, default=DEFAULT_MOBILE_JSON)
    parser.add_argument("--country-filter", default="", help="Comma-separated countries to include.")
    parser.add_argument("--jurisdiction-filter", default="", help="Comma-separated jurisdictions to include.")
    return parser.parse_args()


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def normalize_row(row: dict[str, Any]) -> dict[str, Any] | None:
    lake_id = str(row.get("lake_id") or "").strip()
    poi_id = str(row.get("osm_id") or "").strip()
    if not lake_id or not poi_id:
        return None
    return {
        "id": poi_id,
        "name": str(row.get("name") or "").strip(),
        "lat": row.get("lat"),
        "lon": row.get("lon"),
        "type": str(row.get("poi_type") or "").strip(),
        "fuelTypes": row.get("fuel_types") or [],
        "amenities": row.get("amenities") or [],
        "phone": str(row.get("phone") or "").strip(),
        "website": str(row.get("website") or "").strip(),
        "address": str(row.get("address") or "").strip(),
        "operator": str(row.get("operator") or "").strip(),
        "source": str(row.get("source") or "").strip(),
    }


def main() -> None:
    args = parse_args()
    keep_countries = {part.strip() for part in args.country_filter.split(",") if part.strip()}
    keep_jurisdictions = {part.strip() for part in args.jurisdiction_filter.split(",") if part.strip()}

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    lake_meta: dict[str, dict[str, str]] = {}
    poi_type_counts: Counter[str] = Counter()
    kept_rows = 0

    if args.input_jsonl.exists():
        with args.input_jsonl.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                country = str(row.get("country") or "").strip()
                jurisdiction = str(row.get("jurisdiction") or "").strip()
                if keep_countries and country not in keep_countries:
                    continue
                if keep_jurisdictions and jurisdiction not in keep_jurisdictions:
                    continue
                normalized = normalize_row(row)
                if not normalized:
                    continue
                lake_id = str(row["lake_id"])
                grouped[lake_id].append(normalized)
                lake_meta[lake_id] = {
                    "lakeName": str(row.get("lake_name") or "").strip(),
                    "jurisdiction": jurisdiction,
                    "country": country,
                }
                poi_type_counts[normalized["type"]] += 1
                kept_rows += 1

    # Stable ordering for deterministic bundles.
    grouped_out: dict[str, Any] = {}
    for lake_id in sorted(grouped):
        rows = grouped[lake_id]
        rows.sort(key=lambda row: (row["type"], row["name"], row["id"]))
        grouped_out[lake_id] = {
            **lake_meta.get(lake_id, {}),
            "pois": rows,
        }

    write_json(args.output_json, grouped_out)
    write_json(args.mobile_json, grouped_out)

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "lake_count": len(grouped_out),
        "poi_count": kept_rows,
        "poi_type_counts": dict(poi_type_counts),
        "input_jsonl": str(args.input_jsonl),
        "output_json": str(args.output_json),
        "mobile_json": str(args.mobile_json),
    }
    write_json(args.summary_json, summary)

    print(f"Wrote {kept_rows} POIs across {len(grouped_out)} lakes")
    print(f"Catalog: {args.output_json}")
    print(f"Mobile: {args.mobile_json}")
    print(f"Summary: {args.summary_json}")


if __name__ == "__main__":
    main()
