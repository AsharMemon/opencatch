#!/usr/bin/env python3
"""
Normalize marine / river-navigation official sources into OpenCatch schema.

This script converts the newly acquired official marine sources into stable
GeoJSON overlays that can be tiled and served through Martin:

1. NOAA theme layers
   - Coastal Maintained Channels
   - Shipping Lanes and Regulations
   - U.S. Maritime Limits & Boundaries

2. USACE IENC chart exports
   - DEPARE depth areas
   - DEPCNT depth contours
   - LNDARE land polygons
   - WRECKS hazards
   - bridge obstacles

Usage:
    python normalize_marine_navigation.py \
      --output-dir /Users/Ashar/Documents/fish/data/bathymetry/marine_normalized \
      --usace-dir /Users/Ashar/Documents/fish/data/bathymetry/usace_ienc/downloads \
      --usace-limit 12
"""

from __future__ import annotations

import argparse
import json
import math
import re
import zipfile
from pathlib import Path
from typing import Iterable, Iterator, List

import geopandas as gpd
from shapely.geometry import LineString, Point

M_TO_FT = 3.28084


def _read_zipped_shapefile(zip_path: Path, inner_name: str) -> gpd.GeoDataFrame:
    return gpd.read_file(f"zip://{zip_path}!{inner_name}")


def _read_date(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def _float_or_none(value) -> float | None:
    try:
        if value is None:
            return None
        value = float(value)
        if math.isnan(value):
            return None
        return value
    except Exception:
        return None


def _to_feature(geometry, properties: dict) -> dict | None:
    if geometry is None or geometry.is_empty:
        return None
    return {
        "type": "Feature",
        "geometry": geometry.__geo_interface__,
        "properties": properties,
    }


def normalize_noaa_theme_layers(noaa_dir: Path) -> tuple[list[dict], dict]:
    features: List[dict] = []
    stats: dict = {}

    channel_zip = noaa_dir / "downloads" / "coastal_maintained_channels.zip"
    if channel_zip.exists():
        gdf = _read_zipped_shapefile(channel_zip, "maintainedchannels.shp")
        stats["noaa_channels_count"] = int(len(gdf))
        for _, row in gdf.iterrows():
            depth_m = _float_or_none(row.get("DRVAL1"))
            feature = _to_feature(
                row.geometry,
                {
                    "source": "noaa_maintained_channels",
                    "source_label": "NOAA Coastal Maintained Channels",
                    "feature_kind": "maintained_channel",
                    "channel_name": row.get("FAIRWAY") or row.get("OBJNAM") or "Maintained Channel",
                    "theme_layer": row.get("THEMELAYER"),
                    "depth_m": depth_m,
                    "depth_ft": round(depth_m * M_TO_FT, 1) if depth_m is not None else None,
                    "quality_of_sounding": row.get("QUASOU_TXT"),
                    "survey_date": _read_date(row.get("SORDAT")),
                    "data_type": row.get("DATATYPE"),
                    "data_access": row.get("DATAACCESS"),
                    "contour_quality": "survey",
                    "water_body_type": "marine",
                    "attribution": "NOAA ENC Direct to GIS - Coastal Maintained Channels",
                },
            )
            if feature:
                features.append(feature)

    shipping_zip = noaa_dir / "downloads" / "shipping_lanes_and_regulations.zip"
    if shipping_zip.exists():
        gdf = _read_zipped_shapefile(shipping_zip, "shippinglanes.shp")
        stats["noaa_shipping_count"] = int(len(gdf))
        for _, row in gdf.iterrows():
            feature = _to_feature(
                row.geometry,
                {
                    "source": "noaa_shipping_lanes",
                    "source_label": "NOAA Shipping Lanes and Regulations",
                    "feature_kind": "shipping_regulation",
                    "name": row.get("OBJNAM") or row.get("THEMELAYER") or "Shipping Regulation",
                    "theme_layer": row.get("THEMELAYER"),
                    "information": row.get("INFORM"),
                    "contour_quality": "survey",
                    "water_body_type": "marine",
                    "attribution": "NOAA ENC Direct to GIS - Shipping Lanes and Regulations",
                },
            )
            if feature:
                features.append(feature)

    bounds_zip = noaa_dir / "downloads" / "us_maritime_limits_and_boundaries.zip"
    if bounds_zip.exists():
        gdf = _read_zipped_shapefile(bounds_zip, "USMaritimeLimitsNBoundaries.shp")
        stats["noaa_bounds_count"] = int(len(gdf))
        for _, row in gdf.iterrows():
            feature = _to_feature(
                row.geometry,
                {
                    "source": "noaa_maritime_boundaries",
                    "source_label": "NOAA U.S. Maritime Limits and Boundaries",
                    "feature_kind": "maritime_boundary",
                    "boundary_id": row.get("BOUND_ID"),
                    "region": row.get("REGION"),
                    "feature_type": row.get("FEAT_TYPE"),
                    "territorial_sea": _float_or_none(row.get("TS")),
                    "contiguous_zone": _float_or_none(row.get("CZ")),
                    "eez": _float_or_none(row.get("EEZ")),
                    "fisheries_eez": _float_or_none(row.get("F_EEZ")),
                    "public_date": _read_date(row.get("PUB_DATE")),
                    "approved_date": _read_date(row.get("APPRV_DATE")),
                    "legal_authority": row.get("LEGAL_AUTH"),
                    "note": row.get("NOTE"),
                    "contour_quality": "survey",
                    "water_body_type": "marine",
                    "attribution": "NOAA U.S. Maritime Limits and Boundaries",
                },
            )
            if feature:
                features.append(feature)

    return features, stats


def _find_chart_members(zip_path: Path, suffixes: Iterable[str]) -> list[str]:
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
    matched = []
    for name in names:
        lower = name.lower()
        if any(lower.endswith(sfx.lower()) for sfx in suffixes):
            matched.append(name)
    return matched


def _line_midpoint(line) -> Point | None:
    try:
        if line is None or line.is_empty:
            return None
        if isinstance(line, LineString):
            return line.interpolate(0.5, normalized=True)
        return line.representative_point()
    except Exception:
        return None


def normalize_usace_ienc(usace_dir: Path, limit: int | None = None) -> tuple[list[dict], dict]:
    features: List[dict] = []
    stats = {
        "charts_processed": 0,
        "depth_areas": 0,
        "depth_contours": 0,
        "land_areas": 0,
        "wrecks": 0,
        "bridges": 0,
    }

    chart_zips = sorted(usace_dir.glob("U37*_SHAPE.zip"))
    if limit:
        chart_zips = chart_zips[:limit]

    for chart_zip in chart_zips:
        chart_id = chart_zip.stem.replace("_SHAPE", "")
        stats["charts_processed"] += 1

        for shp_name in _find_chart_members(chart_zip, ["DEPARE(A).shp"]):
            gdf = _read_zipped_shapefile(chart_zip, shp_name)
            for _, row in gdf.iterrows():
                depth_min_m = _float_or_none(row.get("DRVAL1"))
                depth_max_m = _float_or_none(row.get("DRVAL2"))
                feature = _to_feature(
                    row.geometry,
                    {
                        "source": "usace_ienc",
                        "source_label": "USACE IENC",
                        "feature_kind": "depth_area",
                        "chart_id": chart_id,
                        "depth_min_m": depth_min_m,
                        "depth_max_m": depth_max_m,
                        "depth_min_ft": round(depth_min_m * M_TO_FT, 1) if depth_min_m is not None else None,
                        "depth_max_ft": round(depth_max_m * M_TO_FT, 1) if depth_max_m is not None else None,
                        "depth_m": depth_max_m if depth_max_m is not None else depth_min_m,
                        "depth_ft": round((depth_max_m if depth_max_m is not None else depth_min_m) * M_TO_FT, 1)
                        if (depth_max_m is not None or depth_min_m is not None)
                        else None,
                        "survey_date": _read_date(row.get("SORDAT")),
                        "source_ref": row.get("SORIND"),
                        "contour_quality": "survey",
                        "water_body_type": "river_navigation",
                        "attribution": "USACE Inland Electronic Navigational Charts",
                    },
                )
                if feature:
                    features.append(feature)
                    stats["depth_areas"] += 1

        for shp_name in _find_chart_members(chart_zip, ["DEPCNT(L).shp"]):
            gdf = _read_zipped_shapefile(chart_zip, shp_name)
            for _, row in gdf.iterrows():
                depth_m = _float_or_none(row.get("VALDCO"))
                line = row.geometry
                feature = _to_feature(
                    line,
                    {
                        "source": "usace_ienc",
                        "source_label": "USACE IENC",
                        "feature_kind": "depth_contour",
                        "chart_id": chart_id,
                        "depth_m": depth_m,
                        "depth_ft": round(depth_m * M_TO_FT, 1) if depth_m is not None else None,
                        "scale_min": _float_or_none(row.get("SCAMIN")),
                        "survey_date": _read_date(row.get("SORDAT")),
                        "source_ref": row.get("SORIND"),
                        "contour_quality": "survey",
                        "water_body_type": "river_navigation",
                        "attribution": "USACE Inland Electronic Navigational Charts",
                    },
                )
                if feature:
                    features.append(feature)
                    stats["depth_contours"] += 1
                    label_point = _line_midpoint(line)
                    label_feature = _to_feature(
                        label_point,
                        {
                            "source": "usace_ienc",
                            "source_label": "USACE IENC",
                            "feature_kind": "depth_label",
                            "chart_id": chart_id,
                            "depth_m": depth_m,
                            "depth_ft": round(depth_m * M_TO_FT, 1) if depth_m is not None else None,
                            "label": f"{round(depth_m * M_TO_FT)} ft" if depth_m is not None else None,
                            "survey_date": _read_date(row.get("SORDAT")),
                            "source_ref": row.get("SORIND"),
                            "contour_quality": "survey",
                            "water_body_type": "river_navigation",
                            "attribution": "USACE Inland Electronic Navigational Charts",
                        },
                    )
                    if label_feature:
                        features.append(label_feature)

        for shp_name in _find_chart_members(chart_zip, ["LNDARE(A).shp"]):
            gdf = _read_zipped_shapefile(chart_zip, shp_name)
            for _, row in gdf.iterrows():
                feature = _to_feature(
                    row.geometry,
                    {
                        "source": "usace_ienc",
                        "source_label": "USACE IENC",
                        "feature_kind": "land_area",
                        "chart_id": chart_id,
                        "name": row.get("OBJNAM"),
                        "survey_date": _read_date(row.get("SORDAT")),
                        "source_ref": row.get("SORIND"),
                        "contour_quality": "survey",
                        "water_body_type": "river_navigation",
                        "attribution": "USACE Inland Electronic Navigational Charts",
                    },
                )
                if feature:
                    features.append(feature)
                    stats["land_areas"] += 1

        for shp_name in _find_chart_members(chart_zip, ["WRECKS(P).shp"]):
            gdf = _read_zipped_shapefile(chart_zip, shp_name)
            for _, row in gdf.iterrows():
                depth_m = _float_or_none(row.get("VALSOU"))
                feature = _to_feature(
                    row.geometry,
                    {
                        "source": "usace_ienc",
                        "source_label": "USACE IENC",
                        "feature_kind": "wreck",
                        "chart_id": chart_id,
                        "name": row.get("OBJNAM"),
                        "wreck_type": row.get("CATWRK"),
                        "water_level": row.get("WATLEV"),
                        "status": row.get("STATUS"),
                        "depth_m": depth_m,
                        "depth_ft": round(depth_m * M_TO_FT, 1) if depth_m is not None else None,
                        "survey_date": _read_date(row.get("SORDAT")),
                        "source_ref": row.get("SORIND"),
                        "contour_quality": "survey",
                        "water_body_type": "river_navigation",
                        "attribution": "USACE Inland Electronic Navigational Charts",
                    },
                )
                if feature:
                    features.append(feature)
                    stats["wrecks"] += 1

        for shp_name in _find_chart_members(chart_zip, ["bridge(A).shp"]):
            gdf = _read_zipped_shapefile(chart_zip, shp_name)
            for _, row in gdf.iterrows():
                feature = _to_feature(
                    row.geometry,
                    {
                        "source": "usace_ienc",
                        "source_label": "USACE IENC",
                        "feature_kind": "bridge",
                        "chart_id": chart_id,
                        "survey_date": _read_date(row.get("SORDAT")),
                        "source_ref": row.get("SORIND"),
                        "contour_quality": "survey",
                        "water_body_type": "river_navigation",
                        "attribution": "USACE Inland Electronic Navigational Charts",
                    },
                )
                if feature:
                    features.append(feature)
                    stats["bridges"] += 1

    return features, stats


def write_feature_collection(path: Path, features: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize official marine and river-navigation sources into OpenCatch GeoJSON.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("/Users/Ashar/Documents/fish/data/bathymetry/marine_normalized"),
    )
    parser.add_argument(
        "--noaa-dir",
        type=Path,
        default=Path("/Users/Ashar/Documents/fish/data/bathymetry/noaa_enc"),
    )
    parser.add_argument(
        "--usace-dir",
        type=Path,
        default=Path("/Users/Ashar/Documents/fish/data/bathymetry/usace_ienc/downloads"),
    )
    parser.add_argument(
        "--usace-limit",
        type=int,
        default=0,
        help="How many downloaded USACE chart ZIPs to parse. Use 0 to parse all downloaded charts.",
    )
    args = parser.parse_args()

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    noaa_features, noaa_stats = normalize_noaa_theme_layers(args.noaa_dir)
    usace_limit = None if args.usace_limit <= 0 else args.usace_limit
    usace_features, usace_stats = normalize_usace_ienc(args.usace_dir, usace_limit)

    noaa_path = output_dir / "noaa_marine_navigation.geojson"
    usace_path = output_dir / "usace_river_navigation.geojson"
    write_feature_collection(noaa_path, noaa_features)
    write_feature_collection(usace_path, usace_features)

    manifest = {
        "generated_files": {
            "noaa_marine_navigation": str(noaa_path),
            "usace_river_navigation": str(usace_path),
        },
        "noaa_stats": noaa_stats,
        "usace_stats": usace_stats,
        "usace_limit": usace_limit,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
