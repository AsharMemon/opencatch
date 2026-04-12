#!/usr/bin/env python3
"""
Match Pennsylvania direct-source lakes against the official PFBC footprint set.

This turns the already-downloaded PFBC statewide lake layer into a direct-source
subset inventory for the specific lakes flagged by the GPS direct-source queue.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


STOPWORDS = {
    "lake",
    "pond",
    "reservoir",
    "dam",
    "state",
    "park",
    "run",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory-csv", type=Path, required=True)
    parser.add_argument("--footprints-geojson", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def normalize_name(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[/(),.-]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    tokens = [token for token in value.split() if token not in STOPWORDS]
    return " ".join(tokens) if tokens else value


def score_match(query: str, candidate: str) -> float:
    return SequenceMatcher(None, normalize_name(query), normalize_name(candidate)).ratio()


def read_inventory(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    inventory_rows = read_inventory(args.inventory_csv)
    geojson = json.loads(args.footprints_geojson.read_text(encoding="utf-8"))
    features = geojson.get("features", [])

    candidates: list[dict[str, Any]] = []
    for feature in features:
        props = feature.get("properties", {}) or {}
        for field in ("water_name", "gnis_name"):
            name = str(props.get(field) or "").strip()
            if not name:
                continue
            candidates.append(
                {
                    "candidate_name": name,
                    "field": field,
                    "feature": feature,
                    "props": props,
                }
            )

    matched_rows: list[dict[str, Any]] = []
    subset_features: list[dict[str, Any]] = []
    unmatched_rows: list[dict[str, Any]] = []

    for row in inventory_rows:
        best = None
        best_score = 0.0
        for candidate in candidates:
            score = score_match(row["lake_name"], candidate["candidate_name"])
            if score > best_score:
                best_score = score
                best = candidate
        if not best or best_score < 0.72:
            unmatched_rows.append(row)
            continue

        props = dict(best["props"])
        matched_rows.append(
            {
                **row,
                "match_score": round(best_score, 4),
                "matched_name": best["candidate_name"],
                "matched_field": best["field"],
                "gnis_id": props.get("gnis_id", ""),
                "comid": props.get("comid", ""),
                "county": props.get("county", ""),
                "area_acres": props.get("area_acres", ""),
                "web_link": props.get("web_link", ""),
                "web_link2": props.get("web_link2", ""),
            }
        )
        subset_feature = {
            "type": "Feature",
            "geometry": best["feature"].get("geometry"),
            "properties": {
                "lake_name": row["lake_name"],
                "matched_name": best["candidate_name"],
                "match_score": round(best_score, 4),
                "web_link": props.get("web_link", ""),
                "web_link2": props.get("web_link2", ""),
                "county": props.get("county", ""),
            },
        }
        subset_features.append(subset_feature)

    write_csv(args.output_dir / "pa_pfbc_matched_inventory.csv", matched_rows)
    write_csv(args.output_dir / "pa_pfbc_unmatched_inventory.csv", unmatched_rows)
    (args.output_dir / "pa_pfbc_matched_subset.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": subset_features}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "inventory_csv": str(args.inventory_csv),
        "footprints_geojson": str(args.footprints_geojson),
        "inventory_row_count": len(inventory_rows),
        "matched_count": len(matched_rows),
        "unmatched_count": len(unmatched_rows),
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
