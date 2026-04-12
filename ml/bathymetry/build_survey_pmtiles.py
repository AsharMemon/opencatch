#!/usr/bin/env python3
"""
Build PMTiles from normalized survey GeoJSON outputs.

This uses the manifest produced by `normalize_survey_geojson.py`, combines the
normalized contour features plus any label points into a newline-delimited
GeoJSON stream for tippecanoe, renders an intermediate MBTiles archive, and
then converts that archive into a real PMTiles v3 file.

The resulting files are named `<source>_contours.pmtiles`, which matches the
existing Martin registry and mobile contour source ids.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Iterator, List

from normalize_survey_geojson import iter_geojson_features
from pmtiles_cli import ensure_pmtiles_cli

try:
    from shapely.geometry import MultiLineString, Polygon, shape, mapping
    from shapely.ops import polygonize, unary_union
except Exception:  # pragma: no cover - optional dependency in some environments
    MultiLineString = Polygon = None
    shape = mapping = polygonize = unary_union = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("build_survey_pmtiles")

MAX_DEPTH_RIBBON_SOURCE_FEATURES = 30_000
MAX_SUMMARY_BAND_SOURCE_FEATURES = 5_000


def find_binary(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    for candidate in (
        f"/usr/local/bin/{name}",
        f"/opt/homebrew/bin/{name}",
        f"/usr/bin/{name}",
    ):
        if Path(candidate).exists():
            return candidate
    raise FileNotFoundError(f"Required binary not found: {name}")


def load_manifest(path: Path) -> List[dict]:
    return json.loads(path.read_text())


def load_feature_list(path: Path) -> List[dict]:
    if not path.exists():
        return []
    return list(iter_geojson_features(path))


def _extract_closed_polygons(feature: dict) -> List["Polygon"]:
    if shape is None or Polygon is None:
        return []

    try:
        geom = shape(feature.get("geometry") or {})
    except Exception:
        return []

    if geom.is_empty:
        return []

    polygons = []

    if geom.geom_type == "LineString":
        if geom.is_ring:
            try:
                poly = Polygon(geom)
            except Exception:
                poly = None
            if poly is not None and poly.is_valid and not poly.is_empty:
                polygons.append(poly)
    elif geom.geom_type == "MultiLineString":
        try:
            polygons.extend(
                poly for poly in polygonize(geom.geoms)
                if poly.is_valid and not poly.is_empty
            )
        except Exception:
            for line in geom.geoms:
                if line.is_ring:
                    try:
                        poly = Polygon(line)
                    except Exception:
                        poly = None
                    if poly is not None and poly.is_valid and not poly.is_empty:
                        polygons.append(poly)
    elif geom.geom_type in {"Polygon", "MultiPolygon"}:
        try:
            polygons.extend(
                poly for poly in (geom.geoms if geom.geom_type == "MultiPolygon" else [geom])
                if poly.is_valid and not poly.is_empty
            )
        except Exception:
            return []

    return polygons


def derive_depth_bands_from_contours(contour_features: List[dict]) -> List[dict]:
    """
    Derive nested depth-band polygons from closed contour lines.

    This is a best-effort pass for line-only survey sources. It works well when
    contours are closed rings (common for many surveyed lakes) and gracefully
    skips open linework. The result gives the app true `depth_band` polygons,
    which render much closer to the papercut chart style than thick contour
    strokes alone.
    """
    if shape is None or unary_union is None or polygonize is None:
        return []

    grouped: Dict[str, Dict[float, Dict[str, object]]] = defaultdict(dict)

    for feature in contour_features:
        props = feature.get("properties") or {}
        if props.get("feature_kind") != "contour_line":
            continue

        depth_ft = props.get("depth_ft")
        if depth_ft is None:
            continue

        try:
            depth_ft = round(float(depth_ft), 1)
        except Exception:
            continue

        polygons = _extract_closed_polygons(feature)
        if not polygons:
            continue

        lake_key = str(props.get("lake_id") or props.get("lake_name") or "__unknown__")
        bucket = grouped[lake_key].setdefault(depth_ft, {"props": props, "polys": []})
        bucket["polys"].extend(polygons)  # type: ignore[index]

    derived: List[dict] = []

    for depth_map in grouped.values():
        depths = sorted(depth_map.keys())
        if len(depths) < 2:
            continue

        merged_by_depth: Dict[float, object] = {}
        for depth in depths:
            polys = depth_map[depth]["polys"]  # type: ignore[index]
            if not polys:
                continue
            try:
                merged = unary_union([poly.buffer(0) for poly in polys if not poly.is_empty])
            except Exception:
                continue
            if merged.is_empty:
                continue
            merged_by_depth[depth] = merged

        deeper_union = None
        for idx in range(len(depths) - 1, -1, -1):
            depth_min_ft = depths[idx]
            current = merged_by_depth.get(depth_min_ft)
            if current is None:
                continue

            band_geom = current if deeper_union is None else current.difference(deeper_union)
            if band_geom.is_empty:
                deeper_union = current if deeper_union is None else unary_union([deeper_union, current])
                continue

            depth_max_ft = depths[idx + 1] if idx + 1 < len(depths) else depth_min_ft
            display_depth_ft = round(
                (depth_min_ft + depth_max_ft) / 2.0 if depth_max_ft > depth_min_ft else depth_min_ft,
                1,
            )
            props = dict(depth_map[depth_min_ft]["props"])  # type: ignore[index]

            geoms = band_geom.geoms if getattr(band_geom, "geom_type", "") == "MultiPolygon" else [band_geom]
            for geom in geoms:
                if geom.is_empty or geom.area <= 1e-12:
                    continue
                derived.append({
                    "type": "Feature",
                    "geometry": mapping(geom),
                    "properties": {
                        **props,
                        "feature_kind": "depth_band",
                        "derived_from": "contour_line",
                        "depth_ft": display_depth_ft,
                        "depth_m": round(display_depth_ft * 0.3048, 3),
                        "depth_min_ft": round(depth_min_ft, 1),
                        "depth_max_ft": round(depth_max_ft, 1),
                        "depth_min_m": round(depth_min_ft * 0.3048, 3),
                        "depth_max_m": round(depth_max_ft * 0.3048, 3),
                        "depth_band_label": (
                            f"{round(depth_min_ft)}-{round(depth_max_ft)} ft"
                            if depth_max_ft > depth_min_ft
                            else f"{round(depth_min_ft)}+ ft"
                        ),
                    },
                })

            deeper_union = current if deeper_union is None else unary_union([deeper_union, current])

    return derived


def derive_depth_ribbons_from_contours(
    contour_features: List[dict],
    *,
    buffer_degrees: float = 0.00008,
) -> List[dict]:
    """
    Derive buffered contour ribbons for line-only sources.

    This is a fallback for sources that do not contain closed contours we can
    polygonize into full depth bands. The ribbons give the renderer actual
    depth-coloured polygons around each contour so lakes/ocean tiles read more
    like papercut depth layers instead of a pile of monochrome lines.
    """
    if shape is None or mapping is None:
        return []

    if len(contour_features) > MAX_DEPTH_RIBBON_SOURCE_FEATURES:
        return []

    derived: List[dict] = []
    for feature in contour_features:
        props = feature.get("properties") or {}
        if props.get("feature_kind") != "contour_line":
            continue

        try:
            geom = shape(feature.get("geometry") or {})
        except Exception:
            continue

        if geom.is_empty or geom.geom_type not in {"LineString", "MultiLineString"}:
            continue

        try:
            ribbon = geom.buffer(buffer_degrees, cap_style=1, join_style=1)
        except Exception:
            continue

        if ribbon.is_empty:
            continue

        derived.append({
            "type": "Feature",
            "geometry": mapping(ribbon),
            "properties": {
                **props,
                "feature_kind": "depth_ribbon",
                "derived_from": "contour_line",
            },
        })

    return derived


def derive_depth_bands_from_lake_summaries(summary_features: List[dict]) -> List[dict]:
    """
    Derive nested pseudo depth bands from lake-summary polygons.

    This is a visual-quality upgrade for jurisdictions where we only have a
    per-lake max-depth polygon but still want the map to read like a real depth
    chart. The bands are generated as inward buffers from the shoreline and
    scaled by each lake's max depth.
    """
    if shape is None or mapping is None or Polygon is None:
        return []

    if len(summary_features) > MAX_SUMMARY_BAND_SOURCE_FEATURES:
        return []

    derived: List[dict] = []

    for feature in summary_features:
        props = feature.get("properties") or {}
        if props.get("feature_kind") != "lake_summary":
            continue

        max_depth_ft = props.get("max_depth_ft", props.get("depth_ft"))
        try:
            max_depth_ft = float(max_depth_ft)
        except Exception:
            continue
        if max_depth_ft <= 1.0:
            continue

        try:
            geom = shape(feature.get("geometry") or {})
        except Exception:
            continue
        if geom.is_empty or geom.geom_type not in {"Polygon", "MultiPolygon"}:
            continue

        # Use the available contour_count when present, but keep the visual
        # density in a sane range for mobile rendering.
        contour_count = props.get("contour_count")
        try:
            contour_count = int(contour_count) if contour_count is not None else None
        except Exception:
            contour_count = None

        if contour_count and contour_count > 1:
            band_count = max(3, min(8, contour_count))
        else:
            if max_depth_ft < 8:
                band_count = 3
            elif max_depth_ft < 18:
                band_count = 4
            elif max_depth_ft < 35:
                band_count = 5
            elif max_depth_ft < 60:
                band_count = 6
            else:
                band_count = 7

        area = float(getattr(geom, "area", 0.0) or 0.0)
        if area <= 0.0:
            continue

        # Characteristic radius in source units (degrees). We keep the deepest
        # band away from total collapse so narrow lakes still get multiple bands.
        radius = max((area / 3.141592653589793) ** 0.5, 0.00015)
        max_inset = radius * 0.72
        prev_geom = geom

        for idx in range(band_count):
            next_offset = max_inset * ((idx + 1) / band_count)
            try:
                inner_geom = geom.buffer(-next_offset)
            except Exception:
                inner_geom = None

            if inner_geom is None or inner_geom.is_empty:
                band_geom = prev_geom
            else:
                try:
                    band_geom = prev_geom.difference(inner_geom)
                except Exception:
                    band_geom = prev_geom

            if band_geom.is_empty:
                prev_geom = inner_geom if inner_geom is not None else prev_geom
                continue

            depth_min_ft = round((idx / band_count) * max_depth_ft, 1)
            depth_max_ft = round(((idx + 1) / band_count) * max_depth_ft, 1)
            display_depth_ft = round((depth_min_ft + depth_max_ft) / 2.0, 1)

            geoms = band_geom.geoms if getattr(band_geom, "geom_type", "") == "MultiPolygon" else [band_geom]
            for poly in geoms:
                if poly.is_empty or getattr(poly, "area", 0.0) <= 1e-12:
                    continue
                derived.append({
                    "type": "Feature",
                    "geometry": mapping(poly),
                    "properties": {
                        **props,
                        "feature_kind": "depth_band",
                        "derived_from": "lake_summary",
                        "depth_ft": display_depth_ft,
                        "depth_m": round(display_depth_ft * 0.3048, 3),
                        "depth_min_ft": depth_min_ft,
                        "depth_max_ft": depth_max_ft,
                        "depth_min_m": round(depth_min_ft * 0.3048, 3),
                        "depth_max_m": round(depth_max_ft * 0.3048, 3),
                        "depth_band_label": f"{round(depth_min_ft)}-{round(depth_max_ft)} ft",
                    },
                })

            prev_geom = inner_geom if inner_geom is not None and not inner_geom.is_empty else prev_geom

    return derived


def write_feature_stream(features: List[dict], output_path: Path) -> int:
    count = 0
    with output_path.open("w", encoding="utf-8") as out:
        for feature in features:
            json.dump(feature, out, ensure_ascii=True)
            out.write("\n")
            count += 1
    return count


def build_pmtiles(input_geojson: Path, output_path: Path, min_zoom: int, max_zoom: int, tmpdir: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    mbtiles_path = tmpdir / f"{output_path.stem}.mbtiles"
    tippecanoe_bin = find_binary("tippecanoe")
    try:
        pmtiles_bin = find_binary("pmtiles")
    except FileNotFoundError:
        pmtiles_bin = ensure_pmtiles_cli()
    cmd = [
        tippecanoe_bin,
        f"--minimum-zoom={min_zoom}",
        f"--maximum-zoom={max_zoom}",
        "--drop-densest-as-needed",
        "--extend-zooms-if-still-dropping",
        "--read-parallel",
        "--no-tile-compression",
        "--force",
        "--layer=contours",
        "--name=OpenCatch Contours",
        "--description=Survey-backed bathymetry contour tiles for OpenCatch",
        "--attribution=OpenCatch",
        "-o",
        str(mbtiles_path),
        str(input_geojson),
    ]
    log.info("Running %s", " ".join(cmd))
    subprocess.run(cmd, check=True)
    convert_cmd = [
        pmtiles_bin,
        "convert",
        "--force",
        "--tmpdir",
        str(tmpdir),
        str(mbtiles_path),
        str(output_path),
    ]
    log.info("Running %s", " ".join(convert_cmd))
    subprocess.run(convert_cmd, check=True)


def select_tile_ready(manifest: List[dict], selected: List[str] | None) -> List[dict]:
    if selected:
        selected_set = set(selected)
        explicit = [entry for entry in manifest if entry.get("source_id") in selected_set]
        return explicit
    return [entry for entry in manifest if entry.get("tile_ready")]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Martin-ready PMTiles from normalized survey outputs.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("/Users/Ashar/Documents/fish/data/bathymetry/normalized/manifest.json"),
    )
    parser.add_argument(
        "--tile-dir",
        type=Path,
        default=Path("/Users/Ashar/Documents/fish/infra/martin/tiles"),
    )
    parser.add_argument(
        "--sources",
        default="all",
        help="Comma-separated source ids or 'all'",
    )
    parser.add_argument("--min-zoom", type=int, default=5)
    parser.add_argument("--max-zoom", type=int, default=16)
    parser.add_argument("--keep-combined", action="store_true")
    parser.add_argument(
        "--no-derive-depth-bands",
        action="store_true",
        help="Disable derived depth-band polygons for line-based survey sources.",
    )
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    selected = None if args.sources == "all" else [s.strip() for s in args.sources.split(",") if s.strip()]
    targets = select_tile_ready(manifest, selected)
    if not targets:
        raise SystemExit("No tile-ready sources selected.")

    args.tile_dir.mkdir(parents=True, exist_ok=True)

    for entry in targets:
        source_id = entry["source_id"]
        outputs: Dict[str, str] = entry.get("outputs") or {}
        normalized = Path(outputs["normalized"])
        labels = Path(outputs["labels"]) if outputs.get("labels") else None

        with tempfile.TemporaryDirectory(prefix=f"{source_id}_tiles_") as tmp:
            combined = Path(tmp) / f"{source_id}_combined.geojsonl"
            normalized_features = load_feature_list(normalized)
            label_features = load_feature_list(labels) if labels else []
            combined_features = list(normalized_features)
            if not args.no_derive_depth_bands:
                if entry.get("output_mode") == "contour_lines":
                    derived_bands = derive_depth_bands_from_contours(normalized_features)
                    if derived_bands:
                        log.info("%s derived depth-band features: %s", source_id, len(derived_bands))
                        combined_features.extend(derived_bands)
                    else:
                        derived_ribbons = derive_depth_ribbons_from_contours(normalized_features)
                        if derived_ribbons:
                            log.info("%s derived depth-ribbon features: %s", source_id, len(derived_ribbons))
                            combined_features.extend(derived_ribbons)
                        elif len(normalized_features) > MAX_DEPTH_RIBBON_SOURCE_FEATURES:
                            log.info(
                                "%s skipped depth-ribbon derivation for %s contour features",
                                source_id,
                                len(normalized_features),
                            )
                elif entry.get("output_mode") == "lake_summary":
                    derived_summary_bands = derive_depth_bands_from_lake_summaries(normalized_features)
                    if derived_summary_bands:
                        log.info("%s derived summary depth-band features: %s", source_id, len(derived_summary_bands))
                        combined_features.extend(derived_summary_bands)
                    elif len(normalized_features) > MAX_SUMMARY_BAND_SOURCE_FEATURES:
                        log.info(
                            "%s skipped lake-summary band derivation for %s polygons",
                            source_id,
                            len(normalized_features),
                        )
            combined_features.extend(label_features)
            feature_count = write_feature_stream(combined_features, combined)
            log.info("%s combined feature count: %s", source_id, feature_count)

            out_path = args.tile_dir / f"{source_id}_contours.pmtiles"
            build_pmtiles(combined, out_path, args.min_zoom, args.max_zoom, Path(tmp))
            log.info("%s -> %s (%.1f MB)", source_id, out_path, out_path.stat().st_size / 1e6)

            if args.keep_combined:
                keep_path = args.tile_dir / f"{source_id}_combined.geojsonl"
                keep_path.write_text(combined.read_text())


if __name__ == "__main__":
    main()
