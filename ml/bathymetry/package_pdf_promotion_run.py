#!/usr/bin/env python3
"""
Normalize a finished PDF-promotion run and optionally build/upload PMTiles.

This bridges the digitized PDF outputs into the same contour tile lane as the
rest of the survey-backed bathymetry stack.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Iterable

from build_survey_pmtiles import build_pmtiles, write_feature_stream

try:
    from shapely.geometry import shape, mapping
except Exception:  # pragma: no cover
    shape = mapping = None


def slugify(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return value or "lake"


def parse_bounds(text: str | None) -> tuple[float, float, float, float] | None:
    if not text:
        return None
    parts = [float(part.strip()) for part in text.split(",")]
    if len(parts) != 4:
        raise ValueError("--bounds must be minLon,minLat,maxLon,maxLat")
    return tuple(parts)  # type: ignore[return-value]


def load_quality_map(path: Path) -> dict[str, float]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    out: dict[str, float] = {}
    for row in rows:
        name = (row.get("pdf_name") or row.get("file_name") or row.get("pdf") or "").strip()
        if not name:
            continue
        try:
            out[Path(name).stem] = float(row.get("score") or 0)
        except Exception:
            continue
    return out


def feature_bounds(geometry_obj) -> tuple[float, float, float, float]:
    minx, miny, maxx, maxy = geometry_obj.bounds
    return float(minx), float(miny), float(maxx), float(maxy)


def iter_positions(coordinates):
    if not isinstance(coordinates, list):
        return
    if coordinates and isinstance(coordinates[0], (int, float)):
        if len(coordinates) >= 2:
            yield float(coordinates[0]), float(coordinates[1])
        return
    for child in coordinates:
        yield from iter_positions(child)


def raw_geometry_bounds(geometry: dict | None) -> tuple[float, float, float, float] | None:
    if not geometry:
        return None
    coords = list(iter_positions(geometry.get("coordinates")))
    if not coords:
        return None
    xs = [pt[0] for pt in coords]
    ys = [pt[1] for pt in coords]
    return min(xs), min(ys), max(xs), max(ys)


def looks_geographic(bounds: tuple[float, float, float, float], clip_bounds: tuple[float, float, float, float] | None) -> bool:
    minx, miny, maxx, maxy = bounds
    if minx < -180 or maxx > 180 or miny < -90 or maxy > 90:
        return False
    if clip_bounds is not None:
        cminx, cminy, cmaxx, cmaxy = clip_bounds
        intersects = not (maxx < cminx or minx > cmaxx or maxy < cminy or miny > cmaxy)
        if not intersects:
            return False
    return True


def representative_point_feature(geometry_obj, properties: dict) -> dict | None:
    if shape is None or mapping is None:
        return None
    try:
        point = geometry_obj.representative_point()
    except Exception:
        return None
    if point.is_empty:
        return None
    return {
        "type": "Feature",
        "geometry": mapping(point),
        "properties": properties,
    }


def iter_digitized_features(run_root: Path) -> Iterable[tuple[Path, dict]]:
    for path in sorted((run_root / "digitized").glob("*.geojson")):
        try:
            payload = json.loads(path.read_text())
        except Exception:
            continue
        for feature in payload.get("features", []):
            yield path, feature


def normalize_run(
    run_root: Path,
    source_id: str,
    source_name: str,
    attribution: str,
    contour_quality: str,
    min_score: float,
    clip_bounds: tuple[float, float, float, float] | None,
) -> tuple[list[dict], list[dict], dict]:
    quality_map = load_quality_map(run_root / "digitized" / "_quality_report.csv")
    normalized: list[dict] = []
    labels: list[dict] = []
    stats = {
        "input_files": 0,
        "normalized_features": 0,
        "label_features": 0,
        "skipped_low_score": 0,
        "skipped_non_geographic": 0,
        "skipped_invalid": 0,
    }
    seen_files: set[Path] = set()

    for path, feature in iter_digitized_features(run_root):
        seen_files.add(path)
        props = dict(feature.get("properties") or {})
        lake_name = str(props.get("lake_name") or path.stem.replace("_", " ").title()).strip()
        lake_id = f"{source_id}-{slugify(path.stem)}"
        score = float(props.get("quality_score") or quality_map.get(path.stem) or 0.0)
        if score < min_score:
            stats["skipped_low_score"] += 1
            continue

        raw_geometry = feature.get("geometry") or {}
        geom_type = raw_geometry.get("type")
        geom = None
        bounds = None
        if shape is not None and mapping is not None:
            try:
                geom = shape(raw_geometry)
            except Exception:
                geom = None
            if geom is None or geom.is_empty:
                stats["skipped_invalid"] += 1
                continue
            bounds = feature_bounds(geom)
        else:
            bounds = raw_geometry_bounds(raw_geometry)
            if bounds is None:
                stats["skipped_invalid"] += 1
                continue

        if not looks_geographic(bounds, clip_bounds):
            stats["skipped_non_geographic"] += 1
            continue

        depth_ft = props.get("depth_ft")
        depth_m = props.get("depth_m")
        try:
            depth_ft = round(float(depth_ft), 2) if depth_ft is not None else None
        except Exception:
            depth_ft = None
        try:
            depth_m = round(float(depth_m), 3) if depth_m is not None else None
        except Exception:
            depth_m = None

        feature_kind = props.get("feature_kind")
        line_geometry = geom_type in {"LineString", "MultiLineString"} if geom is None else geom.geom_type in {"LineString", "MultiLineString"}
        polygon_geometry = geom_type in {"Polygon", "MultiPolygon"} if geom is None else geom.geom_type in {"Polygon", "MultiPolygon"}
        if feature_kind == "contour_line" or line_geometry:
            normalized_props = {
                "lake_id": lake_id,
                "lake_name": lake_name,
                "depth_m": depth_m,
                "depth_ft": depth_ft,
                "source": f"{source_id}_pdf_digitized",
                "source_id": source_id,
                "contour_quality": contour_quality,
                "attribution": attribution,
                "survey_date": None,
                "feature_kind": "contour_line",
                "quality_score": round(score, 3),
            }
        elif polygon_geometry and (depth_ft is not None or depth_m is not None):
            normalized_props = {
                "lake_id": lake_id,
                "lake_name": lake_name,
                "depth_m": depth_m,
                "depth_ft": depth_ft,
                "source": f"{source_id}_pdf_digitized",
                "source_id": source_id,
                "contour_quality": contour_quality,
                "attribution": attribution,
                "survey_date": None,
                "feature_kind": "depth_band",
                "quality_score": round(score, 3),
                "relative_depth": props.get("relative_depth"),
                "depth_band_label": f"{round(depth_ft)} ft" if depth_ft is not None else None,
            }
        else:
            stats["skipped_invalid"] += 1
            continue

        normalized_feature = {
            "type": "Feature",
            "geometry": mapping(geom) if geom is not None and mapping is not None else raw_geometry,
            "properties": normalized_props,
        }
        normalized.append(normalized_feature)
        stats["normalized_features"] += 1

        if (depth_ft is not None or depth_m is not None) and geom is not None:
            label = representative_point_feature(
                geom,
                {
                    **normalized_props,
                    "feature_kind": "depth_label",
                },
            )
            if label is not None:
                labels.append(label)
                stats["label_features"] += 1

    stats["input_files"] = len(seen_files)
    return normalized, labels, stats


def write_feature_collection(path: Path, features: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))


def upload_pmtiles(path: Path, bucket: str, prefix: str, key_id: str, app_key: str) -> str:
    from b2sdk.v2 import B2Api, InMemoryAccountInfo

    remote_name = f"{prefix.rstrip('/')}/{path.name}"
    info = InMemoryAccountInfo()
    api = B2Api(info)
    api.authorize_account("production", key_id, app_key)
    bucket_obj = api.get_bucket_by_name(bucket)
    bucket_obj.upload_local_file(local_file=str(path), file_name=remote_name)
    return f"b2://{bucket}/{remote_name}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Package a finished PDF-promotion run into normalized contours and PMTiles.")
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--source-name", required=True)
    parser.add_argument("--attribution", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--tile-output", type=Path, default=None)
    parser.add_argument("--contour-quality", default="digitized_pdf")
    parser.add_argument("--min-score", type=float, default=0.0)
    parser.add_argument("--bounds", default=None, help="Optional minLon,minLat,maxLon,maxLat clip for valid georeferenced outputs.")
    parser.add_argument("--bucket", default=None)
    parser.add_argument("--prefix", default="production/tiles")
    parser.add_argument("--key-id", default=None)
    parser.add_argument("--app-key", default=None)
    args = parser.parse_args()

    clip_bounds = parse_bounds(args.bounds)
    normalized, labels, stats = normalize_run(
        args.run_root,
        args.source_id,
        args.source_name,
        args.attribution,
        args.contour_quality,
        args.min_score,
        clip_bounds,
    )

    normalized_path = args.output_dir / f"{args.source_id}_normalized.geojson"
    labels_path = args.output_dir / f"{args.source_id}_labels.geojson"
    write_feature_collection(normalized_path, normalized)
    write_feature_collection(labels_path, labels)

    pmtiles_path = args.tile_output
    bucket = args.bucket or os.environ.get("B2_BUCKET")
    key_id = args.key_id or os.environ.get("B2_KEY_ID")
    app_key = args.app_key or os.environ.get("B2_APP_KEY")

    uploaded = None
    if pmtiles_path is not None:
        with tempfile.TemporaryDirectory(prefix=f"{args.source_id}-pdf-tile-") as tmp:
            combined = Path(tmp) / f"{args.source_id}_combined.ndjson"
            write_feature_stream([*normalized, *labels], combined)
            build_pmtiles(combined, pmtiles_path, min_zoom=5, max_zoom=16, tmpdir=Path(tmp))
        if bucket and key_id and app_key:
            uploaded = upload_pmtiles(pmtiles_path, bucket, args.prefix, key_id, app_key)

    print(json.dumps({
        "run_root": str(args.run_root),
        "source_id": args.source_id,
        "normalized": str(normalized_path),
        "labels": str(labels_path),
        "pmtiles": str(pmtiles_path) if pmtiles_path else None,
        "uploaded": uploaded,
        "stats": stats,
    }, indent=2))


if __name__ == "__main__":
    main()
