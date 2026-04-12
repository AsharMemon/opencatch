#!/usr/bin/env python3
"""
Build a concrete per-lake retrieval queue for the easiest GPS Nautical stages.

This script narrows the general completion queue down to the stages we can
already automate today:

- crosswalk_existing_live
- fetch_direct_official_source
- resolve_official_pdf_and_digitize (only where we already have an inventory)

The goal is to turn lake inventory rows into actionable records with a concrete
next step, matched upstream asset, and a simple confidence score.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


BASE_DIR = Path("/Users/Ashar/Documents/fish/data/bathymetry/gpsnautical")
DEFAULT_INPUT = BASE_DIR / "gpsnautical_completion_queue.csv"
DEFAULT_OUTPUT_CSV = BASE_DIR / "gpsnautical_easy_retrieval_queue.csv"
DEFAULT_OUTPUT_JSON = BASE_DIR / "gpsnautical_easy_retrieval_queue.json"
DEFAULT_SUMMARY_JSON = BASE_DIR / "gpsnautical_easy_retrieval_queue_summary.json"

TARGET_STAGES = {
    "crosswalk_existing_live",
    "fetch_direct_official_source",
    "resolve_official_pdf_and_digitize",
}

SUPPORTING_PDF_JURISDICTIONS = {
    "Maine",
    "New Jersey",
    "Virginia",
    "West Virginia",
}

JURISDICTION_INVENTORIES: dict[str, dict[str, str]] = {
    "Georgia": {
        "inventory_path": "/Users/Ashar/Documents/fish/data/bathymetry/ga/ga_pfa_pdf_inventory.csv",
        "manifest_path": "/Users/Ashar/Documents/fish/data/bathymetry/ga/pdfs/download_manifest.json",
    },
    "Kentucky": {
        "inventory_path": "/Users/Ashar/Documents/fish/data/bathymetry/ky/ky_kdfwr_contour_inventory.csv",
        "manifest_path": "",
    },
    "Louisiana": {
        "inventory_path": "/Users/Ashar/Documents/fish/data/bathymetry/la/la_ldwf_plan_inventory.csv",
        "manifest_path": "",
    },
    "Maine": {
        "inventory_path": "/Users/Ashar/Documents/fish/data/bathymetry/me/me_ifw_lake_survey_inventory.csv",
        "manifest_path": "",
    },
    "Maryland": {
        "inventory_path": "/Users/Ashar/Documents/fish/data/bathymetry/md/md_mgs_bathymetry_inventory.csv",
        "manifest_path": "",
    },
    "Mississippi": {
        "inventory_path": "/Users/Ashar/Documents/fish/data/bathymetry/ms/ms_lake_depth_inventory.csv",
        "manifest_path": "",
    },
    "New Brunswick": {
        "inventory_path": "/Users/Ashar/Documents/fish/data/bathymetry/nb/nb_lake_depth_inventory.csv",
        "manifest_path": "",
    },
    "Nova Scotia": {
        "inventory_path": "/Users/Ashar/Documents/fish/data/bathymetry/ns/ns_lake_inventory.csv",
        "manifest_path": "",
    },
    "South Dakota": {
        "inventory_path": "/Users/Ashar/Documents/fish/data/bathymetry/sd/sd_gfp_bathymetry_inventory.csv",
        "manifest_path": "",
    },
    "South Carolina": {
        "inventory_path": "/Users/Ashar/Documents/fish/data/bathymetry/sc/scdnr_lake_brochure_inventory.csv",
        "manifest_path": "",
    },
    "Virginia": {
        "inventory_path": "/Users/Ashar/Documents/fish/data/bathymetry/va/va_dwr_waterbody_inventory.csv",
        "manifest_path": "",
    },
    "West Virginia": {
        "inventory_path": "/Users/Ashar/Documents/fish/data/bathymetry/wv/wv_lake_map_inventory.csv",
        "manifest_path": "",
    },
    "New Jersey": {
        "inventory_path": "/Users/Ashar/Documents/fish/data/bathymetry/nj/nj_lake_plan_inventory.csv",
        "manifest_path": "",
    },
}

LIVE_LANE_HINTS = {
    "Alabama": "survey_live_state_lane",
    "Alaska": "survey_live_state_lane",
    "Alberta": "survey_live_province_lane",
    "Arkansas": "survey_live_state_lane",
    "British Columbia": "survey_live_province_lane",
    "Connecticut": "survey_live_state_lane",
    "Delaware": "survey_live_state_lane",
    "Florida": "survey_live_state_lane",
    "Illinois": "survey_live_state_lane",
    "Indiana": "survey_live_state_lane",
    "Kansas": "survey_live_state_lane",
    "Massachusetts": "survey_live_state_lane",
    "Michigan": "survey_live_state_lane",
    "Minnesota": "survey_live_state_lane",
    "Montana": "survey_live_state_lane",
    "Nebraska": "survey_live_state_lane",
    "New Hampshire": "survey_live_state_lane",
    "North Dakota": "survey_live_state_lane",
    "Ohio": "survey_live_state_lane",
    "Ontario": "survey_live_province_lane",
    "Quebec": "survey_live_province_lane",
    "Texas": "survey_live_state_lane",
    "Vermont": "survey_live_state_lane",
    "Washington": "survey_live_state_lane",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-csv", type=Path, default=DEFAULT_OUTPUT_CSV)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--summary-json", type=Path, default=DEFAULT_SUMMARY_JSON)
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def read_manifest(path: Path) -> list[dict[str, Any]]:
    if not path or not path.exists():
        return []
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        return list(data.get("downloads") or data.get("results") or [])
    return read_csv(path)  # type: ignore[return-value]


def ascii_fold(value: str) -> str:
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")


def normalize_name(value: str) -> str:
    value = ascii_fold(value or "").lower()
    value = value.replace("&", " and ")
    value = re.sub(r"\([^)]*\)", " ", value)
    value = re.sub(r"\b(pfa|lake|reservoir|pond|dam|site|waterbody|county|co)\b", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def name_variants(value: str) -> set[str]:
    raw = ascii_fold(value or "").strip()
    if not raw:
        return set()
    variants = {normalize_name(raw)}
    no_parens = re.sub(r"\([^)]*\)", " ", raw)
    variants.add(normalize_name(no_parens))
    no_suffix = re.sub(r"\b(Lake|Reservoir|Pond|Dam|PFA)\b", " ", raw, flags=re.I)
    variants.add(normalize_name(no_suffix))
    return {variant for variant in variants if variant and len(variant) >= 3}


def choose_name_fields(row: dict[str, str]) -> list[str]:
    fields = []
    for candidate in ("lake_name", "waterbody", "title", "slug"):
        value = (row.get(candidate) or "").strip()
        if value and normalize_name(value):
            fields.append(value)
    return fields


def inventory_url_for_row(row: dict[str, str]) -> str:
    for candidate in (
        "pdf_url",
        "high_res_map_url",
        "map_pdf_url",
        "contour_kml_url",
        "contour_map_url",
        "report_pdf_urls",
        "url",
    ):
        value = (row.get(candidate) or "").strip()
        if value:
            return value
    return ""


def filter_inventory_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    if not rows:
        return rows
    if any("status" in row for row in rows):
        filtered = [
            row
            for row in rows
            if (row.get("status") or "").strip().lower()
            not in {"unresolved_candidate", "candidate", "possible_candidate"}
        ]
        if filtered:
            return filtered
    return rows


def build_inventory_index(rows: list[dict[str, str]]) -> tuple[dict[str, list[dict[str, str]]], list[dict[str, str]]]:
    index: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        names = choose_name_fields(row)
        for name in names:
            for variant in name_variants(name):
                index[variant].append(row)
    return index, rows


def build_manifest_index(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        name = str(row.get("lake_name") or row.get("waterbody") or "").strip()
        for variant in name_variants(name):
            index[variant].append(row)
    return index


def score_match(lake_name: str, candidate_names: list[str]) -> tuple[float, str]:
    lake_norm = normalize_name(lake_name)
    best_score = 0.0
    best_name = ""
    for candidate in candidate_names:
        cand_norm = normalize_name(candidate)
        if not cand_norm:
            continue
        if cand_norm == lake_norm:
            return 1.0, candidate
        score = SequenceMatcher(None, lake_norm, cand_norm).ratio()
        lake_tokens = set(lake_norm.split())
        cand_tokens = set(cand_norm.split())
        shorter = min(len(lake_tokens), len(cand_tokens))
        token_overlap = len(lake_tokens & cand_tokens)
        if (
            lake_norm
            and cand_norm
            and shorter >= 2
            and token_overlap == shorter
            and (lake_norm in cand_norm or cand_norm in lake_norm)
        ):
            score = max(score, 0.92)
        if score > best_score:
            best_score = score
            best_name = candidate
    return best_score, best_name


def select_inventory_match(
    lake_name: str,
    inventory_index: dict[str, list[dict[str, str]]],
    inventory_rows: list[dict[str, str]],
) -> tuple[dict[str, str] | None, float, str]:
    exact_candidates: list[dict[str, str]] = []
    for variant in name_variants(lake_name):
        exact_candidates.extend(inventory_index.get(variant, []))

    pool = exact_candidates if exact_candidates else inventory_rows

    best_row = None
    best_score = 0.0
    best_name = ""
    for candidate in pool:
        candidate_names = choose_name_fields(candidate)
        score, matched_name = score_match(lake_name, candidate_names)
        if score > best_score:
            best_row = candidate
            best_score = score
            best_name = matched_name

    if best_score < 0.82:
        return None, best_score, best_name
    return best_row, best_score, best_name


def select_manifest_match(
    inventory_row: dict[str, str] | None,
    lake_name: str,
    manifest_index: dict[str, list[dict[str, Any]]],
    manifest_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, float]:
    if not manifest_rows:
        return None, 0.0

    candidate_name = ""
    if inventory_row:
        candidate_name = (
            inventory_row.get("lake_name")
            or inventory_row.get("waterbody")
            or ""
        ).strip()
    search_name = candidate_name or lake_name

    exact_candidates: list[dict[str, Any]] = []
    for variant in name_variants(search_name):
        exact_candidates.extend(manifest_index.get(variant, []))
    pool = exact_candidates if exact_candidates else manifest_rows

    best_row = None
    best_score = 0.0
    for candidate in pool:
        candidate_names = [
            str(candidate.get("lake_name") or "").strip(),
            str(candidate.get("waterbody") or "").strip(),
        ]
        score, _ = score_match(search_name, candidate_names)
        if score > best_score:
            best_row = candidate
            best_score = score
    if best_score < 0.82:
        return None, best_score
    return best_row, best_score


def load_source_assets() -> dict[str, dict[str, Any]]:
    assets: dict[str, dict[str, Any]] = {}
    for jurisdiction, meta in JURISDICTION_INVENTORIES.items():
        inventory_path = Path(meta["inventory_path"]) if meta["inventory_path"] else None
        manifest_path = Path(meta["manifest_path"]) if meta["manifest_path"] else None
        inventory_rows = filter_inventory_rows(read_csv(inventory_path)) if inventory_path else []
        manifest_rows = read_manifest(manifest_path) if manifest_path else []
        inventory_index, inventory_all = build_inventory_index(inventory_rows)
        manifest_index = build_manifest_index(manifest_rows)
        assets[jurisdiction] = {
            "inventory_path": str(inventory_path or ""),
            "manifest_path": str(manifest_path or ""),
            "inventory_rows": inventory_all,
            "inventory_index": inventory_index,
            "manifest_rows": manifest_rows,
            "manifest_index": manifest_index,
        }
    return assets


def build_crosswalk_row(row: dict[str, str]) -> dict[str, Any]:
    jurisdiction = row["jurisdiction"]
    return {
        **row,
        "retrieval_stage": "crosswalk_existing_live",
        "retrieval_status": "covered_live_lane",
        "retrieval_confidence": 1.0,
        "matched_inventory_name": "",
        "matched_inventory_url": "",
        "matched_manifest_path": "",
        "matched_manifest_status": "",
        "resolved_source_family": LIVE_LANE_HINTS.get(jurisdiction, "existing_live_official_survey"),
        "retrieval_action": "crosswalk_live_jurisdiction_lane",
        "retrieval_note": "Lake is already in a survey-live jurisdiction; map it against the existing live official lane.",
        "inventory_match_score": 1.0,
        "manifest_match_score": 0.0,
    }


def build_direct_source_row(row: dict[str, str]) -> dict[str, Any]:
    return {
        **row,
        "retrieval_stage": "fetch_direct_official_source",
        "retrieval_status": "page_source_hint_found",
        "retrieval_confidence": row.get("page_source_confidence") or "medium",
        "matched_inventory_name": row.get("page_source_family") or "",
        "matched_inventory_url": row.get("lake_url") or "",
        "matched_manifest_path": "",
        "matched_manifest_status": "",
        "resolved_source_family": row.get("page_source_family") or "",
        "retrieval_action": "fetch_upstream_official_source",
        "retrieval_note": row.get("page_source_hint") or "Official source hint found on the lake page.",
        "inventory_match_score": 1.0,
        "manifest_match_score": 0.0,
    }


def build_pdf_row(row: dict[str, str], assets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    jurisdiction = row["jurisdiction"]
    asset = assets.get(jurisdiction, {})
    inventory_rows = asset.get("inventory_rows") or []
    inventory_index = asset.get("inventory_index") or {}
    manifest_rows = asset.get("manifest_rows") or []
    manifest_index = asset.get("manifest_index") or {}

    inventory_row, inventory_score, matched_inventory_name = select_inventory_match(
        row["lake_name"], inventory_index, inventory_rows
    )
    manifest_row, manifest_score = select_manifest_match(
        inventory_row, row["lake_name"], manifest_index, manifest_rows
    )

    inventory_url = ""
    manifest_path = ""
    manifest_status = ""
    action = "inventory_match_needed"
    status = "unmatched"
    note = "No confident inventory match found yet."

    if inventory_row:
        inventory_url = inventory_url_for_row(inventory_row)
        inventory_url_lower = inventory_url.lower()
        if inventory_url_lower.endswith(".kml") or "maps/d/kml" in inventory_url_lower:
            action = "fetch_kml_then_normalize"
            note = "Matched the lake against an official vector contour/KML inventory row."
        else:
            action = "download_pdf_then_digitize" if inventory_url else "inventory_match_found"
            note = "Matched the lake against an existing official PDF/chart inventory row."
        status = "inventory_match_found"
    if manifest_row:
        manifest_path = str(manifest_row.get("local_path") or "").strip()
        manifest_status = str(manifest_row.get("status") or "").strip()
        action = "digitize_downloaded_pdf"
        status = "manifest_match_found"
        note = "Matched the lake against a downloaded official PDF manifest entry."

    confidence = round(max(inventory_score, manifest_score), 3)
    return {
        **row,
        "retrieval_stage": "resolve_official_pdf_and_digitize",
        "retrieval_status": status,
        "retrieval_confidence": confidence,
        "matched_inventory_name": matched_inventory_name,
        "matched_inventory_url": inventory_url,
        "matched_manifest_path": manifest_path,
        "matched_manifest_status": manifest_status,
        "resolved_source_family": row.get("default_source_family") or "",
        "retrieval_action": action,
        "retrieval_note": note,
        "inventory_match_score": round(inventory_score, 3),
        "manifest_match_score": round(manifest_score, 3),
        "inventory_artifact_path": asset.get("inventory_path", ""),
        "manifest_artifact_path": asset.get("manifest_path", ""),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    queue_rows = read_csv(args.input)
    assets = load_source_assets()

    output_rows: list[dict[str, Any]] = []
    for row in queue_rows:
        stage = row.get("completion_stage") or ""
        eligible_supporting_pdf = (
            stage == "supporting_lane_upgrade_search"
            and row.get("jurisdiction") in SUPPORTING_PDF_JURISDICTIONS
        )
        if stage not in TARGET_STAGES and not eligible_supporting_pdf:
            continue
        if stage == "crosswalk_existing_live":
            output_rows.append(build_crosswalk_row(row))
            continue
        if stage == "fetch_direct_official_source":
            output_rows.append(build_direct_source_row(row))
            continue
        if stage == "resolve_official_pdf_and_digitize" or eligible_supporting_pdf:
            output_rows.append(build_pdf_row(row, assets))

    output_rows.sort(
        key=lambda row: (
            row["retrieval_stage"],
            row["retrieval_status"],
            row["jurisdiction"],
            row["lake_name"],
        )
    )

    write_csv(args.output_csv, output_rows)
    write_json(args.output_json, output_rows)

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "row_count": len(output_rows),
        "retrieval_stage_counts": dict(Counter(row["retrieval_stage"] for row in output_rows)),
        "retrieval_status_counts": dict(Counter(row["retrieval_status"] for row in output_rows)),
        "retrieval_action_counts": dict(Counter(row["retrieval_action"] for row in output_rows)),
        "jurisdiction_counts": dict(Counter(row["jurisdiction"] for row in output_rows)),
        "matched_pdf_counts": dict(
            Counter(row["jurisdiction"] for row in output_rows if row["retrieval_status"] in {"inventory_match_found", "manifest_match_found"})
        ),
        "input": str(args.input),
    }
    write_json(args.summary_json, summary)

    print(f"Wrote {len(output_rows)} rows")
    print(f"CSV: {args.output_csv}")
    print(f"JSON: {args.output_json}")
    print(f"Summary: {args.summary_json}")
    print("Retrieval status counts:")
    for key, value in summary["retrieval_status_counts"].items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
