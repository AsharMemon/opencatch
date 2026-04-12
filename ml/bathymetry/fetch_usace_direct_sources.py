#!/usr/bin/env python3
"""
Build a USACE-oriented direct-source inventory for a jurisdiction inventory.

This does not claim every lake is survey-ready. It does the honest official
upstream work we need next:
1. downloads the current USACE National Inventory of Dams (NID) catalog
2. filters it to the inventory's state(s)
3. fuzzy-matches requested lake/reservoir names against NID dam names
4. fetches the current USACE IENC manifest for surveyed navigation charts

Outputs are structured inventories we can fan into later fetch/normalize steps.
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

import pandas as pd
import requests


NID_CSV_URL = "https://nid.sec.usace.army.mil/api/nation/csv"
IENC_SHP_URL = "https://ienccloud.us/ienc_shp.html"
USER_AGENT = "OpenCatch/1.0 (research; contact@opencatch.app)"

JURISDICTION_TO_STATE_CODE = {
    "Arizona": "AZ",
    "California": "CA",
    "Colorado": "CO",
    "Idaho": "ID",
    "Iowa": "IA",
    "Missouri": "MO",
    "New Mexico": "NM",
    "North Carolina": "NC",
    "Oklahoma": "OK",
    "Oregon": "OR",
    "Pennsylvania": "PA",
    "Tennessee": "TN",
    "Virginia": "VA",
    "West Virginia": "WV",
}

STOPWORDS = {
    "lake",
    "reservoir",
    "dam",
    "pool",
    "project",
    "river",
    "creek",
    "fork",
    "branch",
    "state",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


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


def normalize_name(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[/(),.-]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    tokens = [token for token in value.split() if token not in STOPWORDS]
    return " ".join(tokens) if tokens else value


def token_set(value: str) -> set[str]:
    return {token for token in normalize_name(value).split() if token}


def parse_nid_csv(csv_path: Path) -> pd.DataFrame:
    with csv_path.open("r", encoding="utf-8-sig") as handle:
        first_line = handle.readline()
    skiprows = 1 if first_line.startswith("Data Last Updated") else 0
    df = pd.read_csv(csv_path, low_memory=False, encoding="utf-8-sig", skiprows=skiprows)
    rename_map = {
        "Dam Name": "dam_name",
        "NID ID": "nid_id",
        "Other Names": "other_names",
        "State": "state",
        "County": "county",
        "Latitude": "latitude",
        "Longitude": "longitude",
        "Primary Purpose": "primary_purpose",
        "Purposes": "purposes",
        "River or Stream Name": "river_name",
        "Surface Area (Acres)": "surface_area_acres",
        "Normal Storage (Acre-Ft)": "normal_storage_acft",
        "Max Storage (Acre-Ft)": "max_storage_acft",
        "Dam Height (Ft)": "dam_height_ft",
        "Primary Owner Type": "owner_type",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
    keep = [value for value in rename_map.values() if value in df.columns]
    if not keep:
        raise RuntimeError("NID CSV parsed, but expected columns were missing.")
    return df[keep].copy()


def download_nid_csv(dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 1_000_000:
        return dest
    resp = requests.get(
        NID_CSV_URL,
        timeout=180,
        headers={"User-Agent": USER_AGENT, "Accept": "text/csv"},
        stream=True,
    )
    resp.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as handle:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            if chunk:
                handle.write(chunk)
    return dest


def fetch_ienc_manifest() -> dict[str, Any]:
    resp = requests.get(IENC_SHP_URL, timeout=120, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    html = resp.text
    master = None
    chart_urls: list[str] = []
    for href in re.findall(r'href=["\']([^"\']+)', html, re.I):
        normalized = href.replace("\\", "/")
        lower = normalized.lower()
        if lower.endswith("master_service_gdb.zip"):
            master = normalized
        elif lower.endswith("_shape.zip") and "ienc_shp/" in lower:
            chart_urls.append(normalized)
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": IENC_SHP_URL,
        "master_service_gdb": master,
        "chart_count": len(set(chart_urls)),
        "charts": [{"id": Path(url).stem.replace("_SHAPE", ""), "url": url} for url in sorted(set(chart_urls))],
    }


def score_match(query: str, candidate: str) -> float:
    q_norm = normalize_name(query)
    c_norm = normalize_name(candidate)
    if not q_norm or not c_norm:
        return 0.0
    ratio = SequenceMatcher(None, q_norm, c_norm).ratio()
    q_tokens = token_set(query)
    c_tokens = token_set(candidate)
    overlap = len(q_tokens & c_tokens) / max(1, len(q_tokens))
    return 0.65 * ratio + 0.35 * overlap


def best_match(lake_name: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    scored: list[tuple[float, str, dict[str, Any]]] = []
    for candidate in candidates:
        for field in ("dam_name", "other_names", "river_name"):
            value = str(candidate.get(field) or "").strip()
            if not value:
                continue
            score = score_match(lake_name, value)
            if score > 0:
                scored.append((score, field, candidate))
    if not scored:
        return None
    score, field, candidate = max(scored, key=lambda item: item[0])
    if score < 0.52:
        return None
    return {
        "match_score": round(score, 4),
        "matched_field": field,
        **candidate,
    }


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    inventory_rows = read_inventory(args.inventory_csv)
    if not inventory_rows:
        raise SystemExit(f"No rows in inventory: {args.inventory_csv}")

    state_names = sorted({row["jurisdiction"] for row in inventory_rows if row.get("jurisdiction")})
    state_codes = sorted(
        {
            JURISDICTION_TO_STATE_CODE[row["jurisdiction"]]
            for row in inventory_rows
            if row.get("jurisdiction") in JURISDICTION_TO_STATE_CODE
        }
    )
    if not state_names:
        raise SystemExit("Could not resolve any USACE states from inventory rows.")

    nid_csv = download_nid_csv(args.output_dir / "nid_raw.csv")
    nid_df = parse_nid_csv(nid_csv)
    nid_df = nid_df[nid_df["state"].isin(state_names + state_codes)].copy()
    candidate_rows = nid_df.fillna("").to_dict(orient="records")

    match_rows: list[dict[str, Any]] = []
    unmatched_rows: list[dict[str, Any]] = []
    for row in inventory_rows:
        match = best_match(row["lake_name"], candidate_rows)
        output_row = {
                **row,
                "state_codes": "|".join(state_codes),
            }
        if match is None:
            unmatched_rows.append(output_row)
            continue
        match_rows.append(
            {
                **output_row,
                "matched_field": match["matched_field"],
                "match_score": match["match_score"],
                "nid_id": match.get("nid_id", ""),
                "dam_name": match.get("dam_name", ""),
                "other_names": match.get("other_names", ""),
                "state": match.get("state", ""),
                "county": match.get("county", ""),
                "latitude": match.get("latitude", ""),
                "longitude": match.get("longitude", ""),
                "primary_purpose": match.get("primary_purpose", ""),
                "purposes": match.get("purposes", ""),
                "river_name": match.get("river_name", ""),
                "surface_area_acres": match.get("surface_area_acres", ""),
                "normal_storage_acft": match.get("normal_storage_acft", ""),
                "max_storage_acft": match.get("max_storage_acft", ""),
                "dam_height_ft": match.get("dam_height_ft", ""),
                "owner_type": match.get("owner_type", ""),
            }
        )

    ienc_manifest = fetch_ienc_manifest()
    (args.output_dir / "usace_ienc_manifest.json").write_text(
        json.dumps(ienc_manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    write_csv(args.output_dir / "usace_nid_matches.csv", match_rows)
    write_csv(args.output_dir / "usace_unmatched_inventory.csv", unmatched_rows)

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "inventory_csv": str(args.inventory_csv),
        "states": state_names,
        "state_codes": state_codes,
        "inventory_row_count": len(inventory_rows),
        "nid_candidate_count": len(candidate_rows),
        "matched_count": len(match_rows),
        "unmatched_count": len(unmatched_rows),
        "ienc_chart_count": ienc_manifest["chart_count"],
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
