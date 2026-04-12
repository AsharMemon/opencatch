#!/usr/bin/env python3
"""
Build compact USGS waterbody polygon layers from local named point indexes.

This is a light-weight alternative to copying the full HydroLAKES reference to
every PDF-promotion worker. We start from a local point index (usually a
state/province-specific catalog with lake names and approximate coordinates),
query the official USGS NHD Waterbody - Large Scale layer near each point, and
keep the best name-matched polygon.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import requests


QUERY_URL = "https://hydro.nationalmap.gov/arcgis/rest/services/nhd/MapServer/12/query"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--point-index", type=Path, required=True)
    parser.add_argument("--output-geojson", type=Path, required=True)
    parser.add_argument("--summary-json", type=Path, required=True)
    parser.add_argument("--distance-m", default="5000,15000", help="Comma-separated search radii in meters.")
    parser.add_argument("--min-score", type=float, default=0.82)
    parser.add_argument("--sleep-s", type=float, default=0.1)
    return parser.parse_args()


def normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"\([^)]*\)", " ", text)
    text = text.replace("&", " and ")
    for ch in "/,-'":
        text = text.replace(ch, " ")
    text = re.sub(r"\b(lake|reservoir|pond|dam|river|canal|branch|creek)\b", " ", text)
    return " ".join(text.split())


def score_name(query: str, candidate: str) -> float:
    query_norm = normalize_name(query)
    candidate_norm = normalize_name(candidate)
    if not query_norm or not candidate_norm:
        return 0.0
    if query_norm == candidate_norm:
        return 1.0
    score = SequenceMatcher(None, query_norm, candidate_norm).ratio()
    q_tokens = set(query_norm.split())
    c_tokens = set(candidate_norm.split())
    shorter = min(len(q_tokens), len(c_tokens))
    overlap = len(q_tokens & c_tokens)
    if shorter >= 2 and overlap == shorter and (query_norm in candidate_norm or candidate_norm in query_norm):
        score = max(score, 0.92)
    return score


def load_point_features(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("features") or [])


def query_candidates(lon: float, lat: float, distance_m: int) -> list[dict[str, Any]]:
    params = {
        "where": "1=1",
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "distance": str(distance_m),
        "units": "esriSRUnit_Meter",
        "outFields": "GNIS_NAME,FTYPE,FCODE,AREASQKM,PERMANENT_IDENTIFIER",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
    }
    response = requests.get(QUERY_URL, params=params, timeout=60)
    response.raise_for_status()
    return list(response.json().get("features") or [])


def pick_best_feature(lake_name: str, candidates: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float]:
    best_feature = None
    best_score = 0.0
    best_area = -1.0

    for feature in candidates:
        props = feature.get("properties") or {}
        candidate_name = (props.get("GNIS_NAME") or "").strip()
        score = score_name(lake_name, candidate_name)
        if score <= 0:
            continue
        area = float(props.get("AREASQKM") or 0.0)
        if score > best_score or (score == best_score and area > best_area):
            best_feature = feature
            best_score = score
            best_area = area

    return best_feature, best_score


def build_output_feature(source_feature: dict[str, Any], matched_feature: dict[str, Any], score: float, distance_m: int) -> dict[str, Any]:
    source_props = source_feature.get("properties") or {}
    matched_props = matched_feature.get("properties") or {}
    return {
        "type": "Feature",
        "geometry": matched_feature.get("geometry"),
        "properties": {
            "lake_name": (source_props.get("lake_name") or "").strip(),
            "matched_name": (matched_props.get("GNIS_NAME") or "").strip(),
            "match_score": round(score, 3),
            "query_distance_m": distance_m,
            "permanent_identifier": matched_props.get("PERMANENT_IDENTIFIER") or "",
            "ftype": matched_props.get("FTYPE") or "",
            "fcode": matched_props.get("FCODE") or "",
            "area_sq_km": matched_props.get("AREASQKM") or "",
            "source_point_url": source_props.get("waterbody_url") or "",
            "source_map_pdf_url": source_props.get("map_pdf_url") or "",
            "source_report_pdf_urls": source_props.get("report_pdf_urls") or "",
            "match_query": source_props.get("match_query") or "",
        },
    }


def main() -> None:
    args = parse_args()
    distances = [int(part.strip()) for part in args.distance_m.split(",") if part.strip()]
    point_features = load_point_features(args.point_index)

    output_features: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []

    for feature in point_features:
        props = feature.get("properties") or {}
        lake_name = (props.get("lake_name") or "").strip()
        match_query = (props.get("match_query") or lake_name).strip()
        geom = feature.get("geometry") or {}
        if not lake_name or geom.get("type") != "Point":
            continue

        coords = geom.get("coordinates") or []
        if len(coords) != 2:
            continue
        lon, lat = float(coords[0]), float(coords[1])
        best_feature = None
        best_score = 0.0
        best_distance = 0
        for distance_m in distances:
            candidates = query_candidates(lon, lat, distance_m)
            matched, score = pick_best_feature(match_query, candidates)
            if matched and score >= args.min_score:
                best_feature = matched
                best_score = score
                best_distance = distance_m
                break
            if matched and score > best_score:
                best_feature = matched
                best_score = score
                best_distance = distance_m

        if best_feature and best_score >= args.min_score:
            output_features.append(build_output_feature(feature, best_feature, best_score, best_distance))
        else:
            unmatched.append(
                {
                    "lake_name": lake_name,
                    "coords": [lon, lat],
                    "best_score": round(best_score, 3),
                    "best_distance_m": best_distance,
                }
            )

        if args.sleep_s:
            time.sleep(args.sleep_s)

    fc = {"type": "FeatureCollection", "features": output_features}
    args.output_geojson.parent.mkdir(parents=True, exist_ok=True)
    args.output_geojson.write_text(json.dumps(fc, ensure_ascii=False) + "\n", encoding="utf-8")

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "point_index": str(args.point_index),
        "row_count": len(point_features),
        "matched_count": len(output_features),
        "unmatched_count": len(unmatched),
        "distances_m": distances,
        "min_score": args.min_score,
        "unmatched_examples": unmatched[:20],
    }
    args.summary_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Matched {len(output_features)}/{len(point_features)} point-index lakes")
    print(f"GeoJSON: {args.output_geojson}")
    print(f"Summary: {args.summary_json}")


if __name__ == "__main__":
    main()
