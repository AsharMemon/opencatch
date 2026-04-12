#!/usr/bin/env python3
"""
Build vector PMTiles for ocean/coastal bathymetry contours.

This bridges the existing ocean contour generation scripts into the same Martin
tile pipeline used for inland bathymetry. The output uses the same `contours`
layer/schema so the mobile app can style ocean/coastal depth in the same
papercut system as lakes once the tile is published.

Usage
-----
python build_ocean_pmtiles.py \
  --input /Users/Ashar/Documents/fish/data/bathymetry/ocean/ocean_contours.geojson \
  --output /Users/Ashar/Documents/fish/infra/martin/tiles/ocean_contours.pmtiles
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Iterable, List, Sequence

from build_survey_pmtiles import (
    build_pmtiles,
    derive_depth_bands_from_contours,
    derive_depth_ribbons_from_contours,
    write_feature_stream,
)
from normalize_survey_geojson import iter_geojson_features


def prepare_ocean_features(path: Path) -> List[dict]:
    prepared: List[dict] = []
    for feature in iter_geojson_features(path):
        geometry = feature.get("geometry") or {}
        props = dict(feature.get("properties") or {})
        geom_type = geometry.get("type")

        if not props.get("feature_kind"):
            if geom_type in {"Polygon", "MultiPolygon"}:
                props["feature_kind"] = "depth_band"
            elif geom_type in {"LineString", "MultiLineString"}:
                props["feature_kind"] = "contour_line"
            elif geom_type == "Point":
                props["feature_kind"] = "depth_label"

        depth_ft = props.get("depth_ft")
        if depth_ft is not None:
            try:
                depth_ft = round(float(depth_ft), 1)
            except Exception:
                depth_ft = None
        if depth_ft is not None and props.get("depth_m") is None:
            props["depth_m"] = round(depth_ft * 0.3048, 3)
        if depth_ft is not None:
            props["depth_ft"] = depth_ft

        props.setdefault("source", "gebco_vector")
        props.setdefault("source_id", "ocean")
        props.setdefault("contour_quality", "survey")
        props.setdefault("water_body_type", "ocean")
        props.setdefault("attribution", "GEBCO / OpenCatch vectorized bathymetry")

        if props.get("feature_kind") == "depth_label":
            props.setdefault("label", f"{round(depth_ft)} ft" if depth_ft is not None else None)

        prepared.append({
            "type": "Feature",
            "geometry": geometry,
            "properties": props,
        })

    return prepared


def parse_bbox(raw: str) -> tuple[float, float, float, float]:
    south, west, north, east = [float(part.strip()) for part in raw.split(",")]
    return south, west, north, east


CHS_NONNA_REGIONS: dict[str, tuple[float, float, float, float]] = {
    # British Columbia / Salish Sea
    "salish_sea": (47.0, -128.8, 50.6, -122.0),
    "north_bc": (50.0, -132.8, 55.8, -126.4),
    "haida_gwaii": (51.5, -135.0, 54.8, -130.0),
    # Atlantic Canada
    "atlantic_maritimes": (42.0, -68.8, 46.8, -59.0),
    "gulf_st_lawrence": (45.2, -67.8, 50.8, -57.0),
    "newfoundland": (46.0, -60.2, 52.5, -51.8),
    "labrador": (52.0, -64.5, 61.8, -55.0),
    # Great Lakes
    "great_lakes_west": (41.2, -93.0, 49.2, -83.5),
    "great_lakes_east": (41.0, -84.5, 46.8, -75.0),
    # Arctic / Hudson approaches
    "hudson_bay": (51.5, -96.5, 64.8, -76.0),
    "baffin": (61.5, -80.5, 73.8, -60.0),
    "beaufort_mackenzie": (67.0, -141.0, 71.8, -125.0),
}


def build_ocean_features_from_bbox(
    bbox: tuple[float, float, float, float],
    data_dir: Path,
    contour_intervals: List[float] | None = None,
    layer=None,
    preferred_cudem_region: str | None = None,
) -> List[dict]:
    from ocean_bathymetry import OceanBathymetryLayer

    layer = layer or OceanBathymetryLayer(data_dir=str(data_dir))
    tile = layer.get_tile(bbox, preferred_cudem_region=preferred_cudem_region)
    if tile is None:
        raise RuntimeError(f"No ocean bathymetry tile available for bbox={bbox}")

    contours = layer.generate_contours(tile.data, bbox, intervals=contour_intervals)
    features: List[dict] = []

    for depth_m, geom in contours:
        depth_ft = round(depth_m * 3.28084, 1)
        features.append({
            "type": "Feature",
            "geometry": geom.__geo_interface__,
            "properties": {
                "feature_kind": "contour_line",
                "depth_m": round(depth_m, 2),
                "depth_ft": depth_ft,
                "label": f"{round(depth_ft)} ft",
                "source": tile.source,
                "source_id": "ocean",
                "contour_quality": "survey" if tile.source in {"cudem", "nonna"} else "coarse",
                "water_body_type": "ocean",
                "attribution": f"{tile.source.upper()} / OpenCatch vectorized bathymetry",
            },
        })

    derived_bands = derive_depth_bands_from_contours(features)
    if derived_bands:
        features.extend(derived_bands)
    else:
        features.extend(derive_depth_ribbons_from_contours(features))

    return features


def _bbox_cache_key(bbox: tuple[float, float, float, float], prefix: str) -> str:
    raw = f"{prefix}_{bbox[0]:.6f}_{bbox[1]:.6f}_{bbox[2]:.6f}_{bbox[3]:.6f}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def iter_tiled_bboxes(
    bbox: tuple[float, float, float, float],
    tile_span_deg: float,
) -> Iterable[tuple[float, float, float, float]]:
    if tile_span_deg <= 0:
        raise ValueError("tile_span_deg must be > 0")

    south, west, north, east = bbox
    eps = 1e-6
    lat = south
    while lat < north - eps:
        next_lat = min(north, lat + tile_span_deg)
        if next_lat - lat <= eps:
            break
        lon = west
        while lon < east - eps:
            next_lon = min(east, lon + tile_span_deg)
            if next_lon - lon <= eps:
                break
            yield (lat, lon, next_lat, next_lon)
            if math.isclose(next_lon, lon):
                break
            lon = next_lon
        lat = next_lat


def build_chs_features_from_bbox(
    bbox: tuple[float, float, float, float],
    data_dir: Path,
    contour_intervals: List[float] | None = None,
    *,
    resolution_m: int = 10,
) -> List[dict]:
    from fetch_chs_nonna_subset import build_contours, fetch_subset

    cache_key = _bbox_cache_key(bbox, f"nonna{resolution_m}")
    subset_dir = data_dir / "subsets"
    subset_dir.mkdir(parents=True, exist_ok=True)
    tiff_path = subset_dir / f"nonna_{resolution_m}_{cache_key}.tif"
    contours_path = subset_dir / f"nonna_{resolution_m}_{cache_key}_contours.geojson"

    if not tiff_path.exists():
        fetch_subset(bbox, tiff_path, resolution=resolution_m)

    if not contours_path.exists():
        build_contours(
            tiff_path,
            contours_path,
            contour_intervals_m=contour_intervals,
        )

    return prepare_ocean_features(contours_path)


def build_chs_features_from_many_bboxes(
    bboxes: Sequence[tuple[tuple[float, float, float, float], str | None]],
    data_dir: Path,
    contour_intervals: List[float] | None = None,
    *,
    resolution_m: int = 10,
    tile_span_deg: float = 1.5,
) -> List[dict]:
    features: List[dict] = []
    failures: list[dict] = []
    for bbox, region_name in bboxes:
        for tiled_bbox in iter_tiled_bboxes(bbox, tile_span_deg):
            try:
                features.extend(
                    build_chs_features_from_bbox(
                        tiled_bbox,
                        data_dir,
                        contour_intervals=contour_intervals,
                        resolution_m=resolution_m,
                    )
                )
            except Exception as exc:
                failures.append({
                    "bbox": tiled_bbox,
                    "region": region_name,
                    "error": str(exc),
                })
    if failures:
        print(json.dumps({"skipped_chs_bboxes": failures[:50], "skipped_count": len(failures)}, indent=2))
    return features


def build_ocean_features_from_many_bboxes(
    bboxes: Sequence[tuple[tuple[float, float, float, float], str | None]],
    data_dir: Path,
    contour_intervals: List[float] | None = None,
    tile_span_deg: float = 1.25,
) -> List[dict]:
    from ocean_bathymetry import OceanBathymetryLayer

    layer = OceanBathymetryLayer(data_dir=str(data_dir))
    features: List[dict] = []
    failures: list[dict] = []
    for bbox, preferred_region in bboxes:
        for tiled_bbox in iter_tiled_bboxes(bbox, tile_span_deg):
            try:
                features.extend(
                    build_ocean_features_from_bbox(
                        tiled_bbox,
                        data_dir,
                        contour_intervals=contour_intervals,
                        layer=layer,
                        preferred_cudem_region=preferred_region,
                    )
                )
            except Exception as exc:
                failures.append({
                    "bbox": tiled_bbox,
                    "error": str(exc),
                })
    if failures:
        print(json.dumps({"skipped_bboxes": failures[:50], "skipped_count": len(failures)}, indent=2))
    return features


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Martin-ready vector ocean bathymetry PMTiles.")
    parser.add_argument(
        "--input",
        type=Path,
        help="Ocean contour GeoJSON with lines/polygons/labels.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/Users/Ashar/Documents/fish/infra/martin/tiles/ocean_contours.pmtiles"),
    )
    parser.add_argument("--min-zoom", type=int, default=4)
    parser.add_argument("--max-zoom", type=int, default=14)
    parser.add_argument("--keep-combined", action="store_true")
    parser.add_argument(
        "--bbox",
        type=str,
        action="append",
        help="Generate contours from live ocean bathymetry for south,west,north,east. Repeatable.",
    )
    parser.add_argument(
        "--cudem-region",
        action="append",
        help="Append a named NOAA CUDEM region from ocean_bathymetry.CUDEM_REGIONS. Repeatable.",
    )
    parser.add_argument(
        "--all-cudem-regions",
        action="store_true",
        help="Generate from every named NOAA CUDEM region, including Great Lakes.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("/Users/Ashar/Documents/fish/data/bathymetry/ocean"),
    )
    parser.add_argument(
        "--tile-span-deg",
        type=float,
        default=1.25,
        help="Chunk large bboxes into smaller requests of this many degrees per side.",
    )
    parser.add_argument(
        "--chs-bbox",
        type=str,
        action="append",
        help="Generate contours from CHS NONNA WCS for south,west,north,east. Repeatable.",
    )
    parser.add_argument(
        "--chs-region",
        action="append",
        help="Append a named Canada CHS NONNA region. Repeatable.",
    )
    parser.add_argument(
        "--all-chs-regions",
        action="store_true",
        help="Generate from every named CHS NONNA region.",
    )
    parser.add_argument(
        "--chs-data-dir",
        type=Path,
        default=Path("/Users/Ashar/Documents/fish/data/bathymetry/chs_nonna"),
    )
    parser.add_argument(
        "--chs-resolution",
        type=int,
        default=10,
        choices=[10, 100],
        help="CHS NONNA resolution in metres.",
    )
    parser.add_argument(
        "--contour-intervals",
        type=str,
        help="Comma-separated contour intervals in metres when generating from bbox.",
    )
    args = parser.parse_args()

    if (
        not args.input
        and not args.bbox
        and not args.cudem_region
        and not args.all_cudem_regions
        and not args.chs_bbox
        and not args.chs_region
        and not args.all_chs_regions
    ):
        raise SystemExit(
            "Provide --input, --bbox, --cudem-region, --all-cudem-regions, "
            "--chs-bbox, --chs-region, or --all-chs-regions."
        )

    contour_intervals = (
        [float(x.strip()) for x in args.contour_intervals.split(",") if x.strip()]
        if args.contour_intervals
        else None
    )

    if args.bbox or args.cudem_region or args.all_cudem_regions or args.chs_bbox or args.chs_region or args.all_chs_regions:
        from ocean_bathymetry import CUDEM_REGIONS

        requested_bboxes: list[tuple[tuple[float, float, float, float], str | None]] = []
        for raw_bbox in args.bbox or []:
            requested_bboxes.append((parse_bbox(raw_bbox), None))

        region_names = list(args.cudem_region or [])
        if args.all_cudem_regions:
            region_names.extend(CUDEM_REGIONS.keys())
        for region_name in dict.fromkeys(region_names):
            info = CUDEM_REGIONS.get(region_name)
            if not info:
                raise SystemExit(f"Unknown CUDEM region: {region_name}")
            requested_bboxes.append((tuple(info["bbox"]), region_name))

        chs_bboxes: list[tuple[tuple[float, float, float, float], str | None]] = []
        for raw_bbox in args.chs_bbox or []:
            chs_bboxes.append((parse_bbox(raw_bbox), None))
        chs_region_names = list(args.chs_region or [])
        if args.all_chs_regions:
            chs_region_names.extend(CHS_NONNA_REGIONS.keys())
        for region_name in dict.fromkeys(chs_region_names):
            bbox = CHS_NONNA_REGIONS.get(region_name)
            if not bbox:
                raise SystemExit(f"Unknown CHS NONNA region: {region_name}")
            chs_bboxes.append((bbox, region_name))

        features: List[dict] = []
        if requested_bboxes:
            features.extend(
                build_ocean_features_from_many_bboxes(
                    requested_bboxes,
                    args.data_dir,
                    contour_intervals=contour_intervals,
                    tile_span_deg=args.tile_span_deg,
                )
            )
        if chs_bboxes:
            features.extend(
                build_chs_features_from_many_bboxes(
                    chs_bboxes,
                    args.chs_data_dir,
                    contour_intervals=contour_intervals,
                    resolution_m=args.chs_resolution,
                    tile_span_deg=args.tile_span_deg,
                )
            )
    else:
        features = prepare_ocean_features(args.input)

    if not features:
      raise SystemExit("No ocean contour features found in input.")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp_combined = args.output.with_suffix(".combined.geojsonl")
    write_feature_stream(features, tmp_combined)
    build_pmtiles(tmp_combined, args.output, args.min_zoom, args.max_zoom, args.output.parent)

    if not args.keep_combined and tmp_combined.exists():
        tmp_combined.unlink()

    summary = {
        "output": str(args.output),
        "feature_count": len(features),
        "min_zoom": args.min_zoom,
        "max_zoom": args.max_zoom,
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
