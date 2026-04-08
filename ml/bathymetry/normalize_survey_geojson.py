#!/usr/bin/env python3
"""
Normalize raw state/provincial GeoJSON bathymetry sources into OpenCatch schema.

This bridge handles the lightweight sources we can process without geopandas/Fiona:
direct GeoJSON contour exports and survey index catalogs. The output is designed
to feed `contour_tile_pipeline.py` and Martin/MapLibre with a stable property
schema, while also writing a manifest that marks which sources are tile-ready
today versus catalog-only.

Supported today
---------------
- Alberta (`ab_contours.geojson`)      -> tile-ready contour lines + labels
- Florida (`fl_contours.geojson`)      -> tile-ready contour lines + labels
- Illinois (`il_contours.geojson`)     -> tile-ready contour lines + labels
- New Hampshire (`nh_contours.geojson`)-> tile-ready depth bands + labels
- Michigan (`mi_full_contours.geojson`)-> tile-ready contour lines + labels
- Massachusetts (`ma_contours.geojson`)-> tile-ready contour lines + labels
- Montana (`mt_contours.geojson`)      -> tile-ready contour lines + labels
- Ontario (`on_contours.geojson`)      -> tile-ready contour lines + labels
- Ohio (`oh_contours.geojson`)         -> tile-ready contour lines + labels
- Quebec (`qc_contours.geojson`)       -> tile-ready contour lines + labels
- Vermont (`vt_data.geojson`)          -> tile-ready contour lines + labels
- Washington (`wa_contours.geojson`)   -> tile-ready contour lines + labels
- Iowa (`ia_contours.geojson`)         -> lake summary polygons only (not tile-ready)
- Saskatchewan (`sk_contours.geojson`) -> survey index points only
- Manitoba (`mb_data.geojson`)         -> waterbody/survey index points only

Usage
-----
python normalize_survey_geojson.py \
    --sources ab,fl,il,nh,mi,ma,mt,on,oh,qc,vt,wa,ia,sk,mb \
    --output-dir /Users/Ashar/Documents/fish/data/bathymetry/normalized
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Optional

M_TO_FT = 3.28084
FT_TO_M = 1.0 / M_TO_FT
CHUNK_SIZE = 1 << 20

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("normalize_survey_geojson")


@dataclass(frozen=True)
class SourceConfig:
    source_id: str
    input_path: Path
    output_mode: str
    source_name: str
    attribution: str
    default_lake_name: str
    tile_ready: bool
    notes: str = ""


SOURCES: Dict[str, SourceConfig] = {
    "ab": SourceConfig(
        source_id="ab",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/ab/ab_contours.geojson"),
        output_mode="contour_lines",
        source_name="ab_survey",
        attribution="Alberta AGS Lake Bathymetry",
        default_lake_name="Unknown Alberta Lake",
        tile_ready=True,
        notes="Raw GeoJSON contour lines with calculated depth in metres.",
    ),
    "fl": SourceConfig(
        source_id="fl",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/fl/fl_contours.geojson"),
        output_mode="contour_lines",
        source_name="fl_survey",
        attribution="Florida FWC Lake Bathymetry",
        default_lake_name="Florida bathymetry",
        tile_ready=True,
        notes="Depth values are stored negative; normalized to positive depth.",
    ),
    "il": SourceConfig(
        source_id="il",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/il/il_contours.geojson"),
        output_mode="contour_lines",
        source_name="il_survey",
        attribution="Illinois DNR Lake Depth Contours",
        default_lake_name="Illinois survey lake",
        tile_ready=True,
        notes="Contour lines with depth stored in CONTOUR feet.",
    ),
    "nh": SourceConfig(
        source_id="nh",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/nh/nh_contours.geojson"),
        output_mode="depth_bands",
        source_name="nh_survey",
        attribution="New Hampshire GRANIT Bathymetry",
        default_lake_name="New Hampshire survey lake",
        tile_ready=True,
        notes="Polygon depth bands with DEPTHMIN/DEPTHMAX values in feet.",
    ),
    "mi": SourceConfig(
        source_id="mi",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/mi/mi_full_contours.geojson"),
        output_mode="contour_lines",
        source_name="mi_survey",
        attribution="Michigan Inland Lake Contours",
        default_lake_name="Michigan survey lake",
        tile_ready=True,
        notes="Contour lines with depth in feet; statewide IDs preserved as lake_id.",
    ),
    "ma": SourceConfig(
        source_id="ma",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/gdal_exports/ma_contours.geojson"),
        output_mode="contour_lines",
        source_name="ma_survey",
        attribution="MassWildlife Inland Bathymetry",
        default_lake_name="Massachusetts survey lake",
        tile_ready=True,
        notes="Contour lines exported from statewide shapefile. Depth values are documented in feet.",
    ),
    "mt": SourceConfig(
        source_id="mt",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/gdal_exports/mt_contours.geojson"),
        output_mode="contour_lines",
        source_name="mt_survey",
        attribution="Montana FWP Lake Bathymetry",
        default_lake_name="Montana survey lake",
        tile_ready=True,
        notes="Contour lines exported from statewide shapefile. Metadata documents contour depth in feet.",
    ),
    "on": SourceConfig(
        source_id="on",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/gdal_exports/on_contours.geojson"),
        output_mode="contour_lines",
        source_name="on_survey",
        attribution="Ontario Fish Habitat Bathymetry",
        default_lake_name="Ontario survey lake",
        tile_ready=True,
        notes="Contour lines exported from FGDB. DEPTH values are normalized from negative metres.",
    ),
    "oh": SourceConfig(
        source_id="oh",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/oh/oh_contours.geojson"),
        output_mode="contour_lines",
        source_name="oh_survey",
        attribution="Ohio DNR Lakes Bathymetry",
        default_lake_name="Ohio survey lake",
        tile_ready=True,
        notes="Contour lines with negative depth values in feet; normalized to positive depth.",
    ),
    "qc": SourceConfig(
        source_id="qc",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/gdal_exports/qc_contours.geojson"),
        output_mode="contour_lines",
        source_name="qc_survey",
        attribution="Quebec Government Lake Bathymetry",
        default_lake_name="Quebec survey lake",
        tile_ready=True,
        notes="Contour lines exported from FGDB with named waterbodies and depths in metres.",
    ),
    "vt": SourceConfig(
        source_id="vt",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/vt/vt_data.geojson"),
        output_mode="contour_lines",
        source_name="vt_survey",
        attribution="Vermont Fish and Wildlife Bathymetry",
        default_lake_name="Vermont survey lake",
        tile_ready=True,
        notes="Contour lines with depth in feet and lake names in GeoJSON.",
    ),
    "wa": SourceConfig(
        source_id="wa",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/gdal_exports/wa_contours.geojson"),
        output_mode="contour_lines",
        source_name="wa_survey",
        attribution="Washington Lake Bathymetry",
        default_lake_name="Washington survey lake",
        tile_ready=True,
        notes="Contour lines exported from statewide geodatabase. Depth values are treated as feet.",
    ),
    "ia": SourceConfig(
        source_id="ia",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/ia/ia_contours.geojson"),
        output_mode="lake_summary",
        source_name="ia_summary",
        attribution="Iowa DNR Lake Summary Polygons",
        default_lake_name="Iowa lake",
        tile_ready=False,
        notes="Lake summary polygons only; useful for priors, not direct contour tiles.",
    ),
    "sk": SourceConfig(
        source_id="sk",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/sk/sk_contours.geojson"),
        output_mode="survey_index",
        source_name="sk_index",
        attribution="Saskatchewan Bathymetric Survey Index",
        default_lake_name="Saskatchewan survey",
        tile_ready=False,
        notes="Point index to scanned PDFs, not contour geometry.",
    ),
    "mb": SourceConfig(
        source_id="mb",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/mb/mb_data.geojson"),
        output_mode="survey_index",
        source_name="mb_index",
        attribution="Manitoba Fisheries Waterbody Index",
        default_lake_name="Manitoba waterbody",
        tile_ready=False,
        notes="Waterbody catalog with contour availability flags and printable map links.",
    ),
}


@dataclass
class NormalizeResult:
    source_id: str
    input_path: str
    output_mode: str
    tile_ready: bool
    normalized_features: int = 0
    label_features: int = 0
    skipped_features: int = 0
    outputs: Dict[str, str] | None = None
    notes: str = ""


def iter_geojson_features(path: Path) -> Iterator[dict]:
    """Yield features from a GeoJSON FeatureCollection without loading all of it."""
    with path.open("r", encoding="utf-8") as handle:
        buffer = ""
        in_features = False
        collecting = False
        feature_chars: list[str] = []
        depth = 0
        in_string = False
        escape = False
        eof = False

        while True:
            if not eof:
                chunk = handle.read(CHUNK_SIZE)
                if chunk:
                    buffer += chunk
                else:
                    eof = True

            index = 0
            while index < len(buffer):
                ch = buffer[index]

                if not in_features:
                    marker = '"features"'
                    marker_idx = buffer.find(marker, index)
                    if marker_idx == -1:
                        break
                    bracket_idx = buffer.find("[", marker_idx + len(marker))
                    if bracket_idx == -1:
                        break
                    in_features = True
                    index = bracket_idx + 1
                    continue

                if not collecting:
                    if ch in " \r\n\t,":
                        index += 1
                        continue
                    if ch == "]":
                        return
                    if ch != "{":
                        index += 1
                        continue
                    collecting = True
                    feature_chars = ["{"]
                    depth = 1
                    in_string = False
                    escape = False
                    index += 1
                    continue

                feature_chars.append(ch)
                if in_string:
                    if escape:
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == '"':
                        in_string = False
                else:
                    if ch == '"':
                        in_string = True
                    elif ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            yield json.loads("".join(feature_chars))
                            collecting = False
                            feature_chars = []
                index += 1

            buffer = buffer[index:]

            if eof:
                if collecting:
                    raise ValueError(f"Unexpected EOF while parsing feature in {path}")
                if not in_features:
                    raise ValueError(f"Could not find features array in {path}")
                return


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_feature(handle, feature: dict, first: bool) -> bool:
    if not first:
        handle.write(",\n")
    json.dump(feature, handle, ensure_ascii=True)
    return False


def fc_start(handle) -> None:
    handle.write('{"type":"FeatureCollection","features":[\n')


def fc_end(handle) -> None:
    handle.write("\n]}\n")


def slugify(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower())
    return text.strip("-") or "unknown"


def safe_float(value: Any) -> Optional[float]:
    if value in (None, "", " ", "None"):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def pick_line_midpoint(coordinates: list[list[float]]) -> Optional[list[float]]:
    if len(coordinates) == 1:
        return coordinates[0]
    if len(coordinates) < 2:
        return None

    segments = []
    total = 0.0
    for a, b in zip(coordinates[:-1], coordinates[1:]):
        seg_len = math.hypot(b[0] - a[0], b[1] - a[1])
        segments.append((a, b, seg_len))
        total += seg_len
    if total <= 0:
        return coordinates[len(coordinates) // 2]

    halfway = total / 2.0
    walked = 0.0
    for a, b, seg_len in segments:
        if walked + seg_len >= halfway and seg_len > 0:
            ratio = (halfway - walked) / seg_len
            return [
                a[0] + (b[0] - a[0]) * ratio,
                a[1] + (b[1] - a[1]) * ratio,
            ]
        walked += seg_len
    return coordinates[len(coordinates) // 2]


def pick_label_point(geometry: dict) -> Optional[dict]:
    geom_type = geometry.get("type")
    coords = geometry.get("coordinates")
    if geom_type == "Point" and isinstance(coords, list) and len(coords) >= 2:
        return {"type": "Point", "coordinates": coords[:2]}
    if geom_type == "LineString" and isinstance(coords, list):
        point = pick_line_midpoint(coords)
        if point:
            return {"type": "Point", "coordinates": point}
    if geom_type == "MultiLineString" and isinstance(coords, list) and coords:
        line = max(coords, key=len)
        point = pick_line_midpoint(line)
        if point:
            return {"type": "Point", "coordinates": point}
    if geom_type == "Polygon" and isinstance(coords, list) and coords:
        ring = coords[0]
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        return {"type": "Point", "coordinates": [(min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2]}
    if geom_type == "MultiPolygon" and isinstance(coords, list) and coords:
        ring = coords[0][0]
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        return {"type": "Point", "coordinates": [(min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2]}
    return None


def standard_props(config: SourceConfig, feature: dict, props: dict) -> Optional[dict]:
    source_id = config.source_id
    lake_name = None
    lake_id = None
    depth_m = None
    depth_ft = None
    survey_date = None

    if source_id == "ab":
        lake_name = props.get("LAKE_NAME") or config.default_lake_name
        lake_id = props.get("BATHYMETRY_DIG_NUM") or lake_name
        depth_m = safe_float(props.get("CALC_DEP_M"))
        depth_ft = round(depth_m * M_TO_FT, 1) if depth_m is not None else None
    elif source_id == "fl":
        lake_name = config.default_lake_name
        lake_id = f"fl-{props.get('OBJECTID', feature.get('id', 'unknown'))}"
        depth_m = safe_float(props.get("DEPTHM"))
        if depth_m is not None:
            depth_m = abs(depth_m)
        depth_ft = safe_float(props.get("DEPTHF"))
        if depth_ft is not None:
            depth_ft = abs(depth_ft)
        if depth_m is None and depth_ft is not None:
            depth_m = round(depth_ft * FT_TO_M, 3)
        if depth_ft is None and depth_m is not None:
            depth_ft = round(depth_m * M_TO_FT, 1)
        survey_date = props.get("last_edited_date")
    elif source_id == "nh":
        lake_name = props.get("LAKE") or config.default_lake_name
        raw_id = props.get("AU_ID") or feature.get("id") or slugify(lake_name)
        lake_id = f"nh-{raw_id}"
        depth_min_ft = safe_float(props.get("DEPTHMIN"))
        depth_max_ft = safe_float(props.get("DEPTHMAX"))
        if depth_min_ft is not None and depth_max_ft is not None:
            depth_ft = round((depth_min_ft + depth_max_ft) / 2.0, 1)
        elif depth_max_ft is not None:
            depth_ft = round(depth_max_ft, 1)
        elif depth_min_ft is not None:
            depth_ft = round(depth_min_ft, 1)
        depth_m = round(depth_ft * FT_TO_M, 3) if depth_ft is not None else None
        survey_date = props.get("YEAR1")
    elif source_id == "il":
        raw_id = props.get("OBJECTID") or feature.get("id") or "unknown"
        lake_id = f"il-{raw_id}"
        lake_name = props.get("LAKE_NAME") or config.default_lake_name
        depth_ft = safe_float(props.get("CONTOUR"))
        depth_m = round(depth_ft * FT_TO_M, 3) if depth_ft is not None else None
    elif source_id == "mi":
        statewide = props.get("STATEWIDE_") or props.get("STATEWIDE1") or props.get("OBJECTID")
        lake_id = f"mi-{statewide}"
        lake_name = f"Michigan survey lake {statewide}"
        depth_ft = safe_float(props.get("DEPTH"))
        depth_m = round(depth_ft * FT_TO_M, 3) if depth_ft is not None else None
    elif source_id == "ma":
        lake_name = props.get("NAME") or config.default_lake_name
        palis_id = props.get("PALIS_ID") or feature.get("id") or slugify(lake_name)
        lake_id = f"ma-{palis_id}"
        depth_ft = safe_float(props.get("DEPTH"))
        depth_m = round(depth_ft * FT_TO_M, 3) if depth_ft is not None else None
    elif source_id == "mt":
        lake_name = props.get("LAKENAME") or config.default_lake_name
        llid = props.get("LLID") or feature.get("id") or slugify(lake_name)
        lake_id = f"mt-{llid}"
        depth_ft = safe_float(props.get("CONTOUR"))
        depth_m = round(depth_ft * FT_TO_M, 3) if depth_ft is not None else None
        survey_date = props.get("LASTEDIT") or props.get("CREATED")
    elif source_id == "on":
        ogf_id = props.get("OGF_ID") or feature.get("id") or "unknown"
        lake_id = f"on-{ogf_id}"
        lake_name = f"Ontario survey lake {ogf_id}"
        depth_m = safe_float(props.get("DEPTH"))
        if depth_m is not None:
            depth_m = abs(depth_m)
            depth_ft = round(depth_m * M_TO_FT, 1)
        survey_date = props.get("SURVEY_DATE") or props.get("EFFECTIVE_DATETIME")
    elif source_id == "oh":
        lake_name = props.get("LAKE_NAME") or config.default_lake_name
        raw_id = props.get("OBJECTID") or feature.get("id") or slugify(lake_name)
        lake_id = f"oh-{raw_id}"
        depth_ft = safe_float(props.get("DEPTH"))
        if depth_ft is not None:
            depth_ft = abs(depth_ft)
            depth_m = round(depth_ft * FT_TO_M, 3)
        survey_date = props.get("DT_ADDED")
    elif source_id == "qc":
        lake_name = props.get("HYDRONYME") or config.default_lake_name
        raw_id = props.get("NO_LCE_L") or props.get("NO_RSVL") or feature.get("id") or slugify(lake_name)
        lake_id = f"qc-{raw_id}"
        depth_m = safe_float(props.get("PROFONDEUR_M"))
        depth_ft = round(depth_m * M_TO_FT, 1) if depth_m is not None else None
        survey_date = props.get("ANNEE")
    elif source_id == "vt":
        lake_name = props.get("LakeName") or config.default_lake_name
        lake_id = f"vt-{slugify(lake_name)}-{props.get('index', feature.get('id', 'unknown'))}"
        depth_ft = safe_float(props.get("DepthInFeet"))
        depth_m = round(depth_ft * FT_TO_M, 3) if depth_ft is not None else None
    elif source_id == "wa":
        lake_name = props.get("GNIS_Name") or config.default_lake_name
        reach = props.get("ReachCode") or feature.get("id") or slugify(lake_name)
        lake_id = f"wa-{reach}"
        depth_ft = safe_float(props.get("Depth"))
        depth_m = round(depth_ft * FT_TO_M, 3) if depth_ft is not None else None
    elif source_id == "ia":
        lake_name = props.get("LakeName") or props.get("GNIS_Name") or config.default_lake_name
        lake_id = props.get("LakeCode") or props.get("HydrographyID") or slugify(lake_name)
        max_depth_ft = safe_float(props.get("Max_CONTOUR"))
        depth_ft = max_depth_ft
        depth_m = round(max_depth_ft * FT_TO_M, 3) if max_depth_ft is not None else None
    elif source_id == "sk":
        lake_name = props.get("MAP_NAME") or config.default_lake_name
        lake_id = props.get("NRCAN_ID") or slugify(lake_name)
    elif source_id == "mb":
        lake_name = props.get("WATERBODY_NAME") or config.default_lake_name
        lake_id = props.get("WATERBODY_ID") or slugify(lake_name)
        avg_depth = safe_float(props.get("AVERAGE_DEPTH_M"))
        if avg_depth is not None:
            depth_m = avg_depth
            depth_ft = round(avg_depth * M_TO_FT, 1)
    else:
        return None

    return {
        "lake_id": str(lake_id or slugify(lake_name)),
        "lake_name": str(lake_name or config.default_lake_name),
        "depth_m": round(depth_m, 3) if depth_m is not None else None,
        "depth_ft": round(depth_ft, 1) if depth_ft is not None else None,
        "source": config.source_name,
        "source_id": source_id,
        "contour_quality": "survey" if config.tile_ready else "estimate",
        "attribution": config.attribution,
        "survey_date": survey_date,
    }


def normalize_feature(config: SourceConfig, feature: dict) -> Optional[dict]:
    props = feature.get("properties") or {}
    geometry = feature.get("geometry") or {}
    geom_type = geometry.get("type")
    if not geom_type:
        return None

    base = standard_props(config, feature, props)
    if base is None:
        return None

    if config.output_mode == "contour_lines":
        if geom_type not in {"LineString", "MultiLineString"}:
            return None
        if base["depth_m"] is None:
            return None
        base["feature_kind"] = "contour_line"
    elif config.output_mode == "depth_bands":
        if geom_type not in {"Polygon", "MultiPolygon"}:
            return None
        depth_min_ft = safe_float(props.get("DEPTHMIN"))
        depth_max_ft = safe_float(props.get("DEPTHMAX"))
        if depth_min_ft is None and depth_max_ft is None:
            return None
        base["feature_kind"] = "depth_band"
        base["depth_min_ft"] = round(depth_min_ft, 1) if depth_min_ft is not None else None
        base["depth_max_ft"] = round(depth_max_ft, 1) if depth_max_ft is not None else None
        base["depth_min_m"] = round(depth_min_ft * FT_TO_M, 3) if depth_min_ft is not None else None
        base["depth_max_m"] = round(depth_max_ft * FT_TO_M, 3) if depth_max_ft is not None else None
        base["contour_interval_ft"] = safe_float(props.get("BATHY_INT"))
        base["depth_band_label"] = (
            f"{round(depth_min_ft)}-{round(depth_max_ft)} ft"
            if depth_min_ft is not None and depth_max_ft is not None
            else None
        )
    elif config.output_mode == "lake_summary":
        if geom_type not in {"Polygon", "MultiPolygon"}:
            return None
        base["feature_kind"] = "lake_summary"
        base["max_depth_ft"] = base["depth_ft"]
        base["max_depth_m"] = base["depth_m"]
        base["contour_count"] = props.get("ContourCount")
    elif config.output_mode == "survey_index":
        if geom_type != "Point":
            return None
        base["feature_kind"] = "survey_index"
        if config.source_id == "sk":
            base["scan_link"] = props.get("SCAN_LINK")
            base["map_scale"] = props.get("SCALE")
            base["contour_interval"] = props.get("CONTOUR_INT")
            base["quality_code"] = props.get("QUALITY")
        if config.source_id == "mb":
            base["has_contours"] = props.get("CONTOURS")
            base["printable_map"] = props.get("PRINTABLE_MAP")
            base["boat_launch"] = props.get("BOAT_LAUNCH")
            base["secchi_depth"] = props.get("SECCHI_DEPTH")
    else:
        return None

    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": base,
    }


def make_label_feature(feature: dict) -> Optional[dict]:
    point = pick_label_point(feature.get("geometry") or {})
    if point is None:
        return None
    props = dict(feature.get("properties") or {})
    depth_ft = props.get("depth_ft")
    depth_m = props.get("depth_m")
    depth_min_ft = props.get("depth_min_ft")
    depth_max_ft = props.get("depth_max_ft")
    label = None
    if depth_min_ft is not None and depth_max_ft is not None:
        label = f"{round(float(depth_min_ft))}-{round(float(depth_max_ft))} ft"
    elif depth_ft is not None:
        label = f"{round(float(depth_ft))} ft"
    elif depth_m is not None:
        label = f"{round(float(depth_m), 1)} m"
    props.update(
        {
            "feature_kind": "depth_label",
            "label": label,
            "minzoom": 10,
        }
    )
    return {
        "type": "Feature",
        "geometry": point,
        "properties": props,
    }


def normalize_source(config: SourceConfig, output_dir: Path) -> NormalizeResult:
    ensure_dir(output_dir)
    result = NormalizeResult(
        source_id=config.source_id,
        input_path=str(config.input_path),
        output_mode=config.output_mode,
        tile_ready=config.tile_ready,
        notes=config.notes,
        outputs={},
    )

    if not config.input_path.exists():
        result.notes = f"Missing input: {config.input_path}"
        return result

    lines_path = output_dir / f"{config.source_id}_normalized.geojson"
    labels_path = output_dir / f"{config.source_id}_labels.geojson"

    first_feature = True
    first_label = True
    line_handle = None
    label_handle = None

    try:
        if config.output_mode in {"contour_lines", "depth_bands", "lake_summary", "survey_index"}:
            line_handle = lines_path.open("w", encoding="utf-8")
            fc_start(line_handle)
            result.outputs["normalized"] = str(lines_path)

            if config.tile_ready:
                label_handle = labels_path.open("w", encoding="utf-8")
                fc_start(label_handle)
                result.outputs["labels"] = str(labels_path)

            for raw_feature in iter_geojson_features(config.input_path):
                normalized = normalize_feature(config, raw_feature)
                if normalized is None:
                    result.skipped_features += 1
                    continue

                first_feature = write_feature(line_handle, normalized, first_feature)
                result.normalized_features += 1

                if label_handle is not None:
                    label = make_label_feature(normalized)
                    if label is not None:
                        first_label = write_feature(label_handle, label, first_label)
                        result.label_features += 1
        else:
            result.notes = f"Unsupported output mode: {config.output_mode}"
    finally:
        if line_handle is not None:
            fc_end(line_handle)
            line_handle.close()
        if label_handle is not None:
            fc_end(label_handle)
            label_handle.close()

    if result.normalized_features == 0:
        result.notes = f"{config.notes} No normalized features emitted."

    return result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Normalize raw GeoJSON bathymetry sources.")
    parser.add_argument(
        "--sources",
        default="ab,fl,mi,ma,mt,on,qc,vt,wa,ia,sk,mb",
        help="Comma-separated source ids or 'all'",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("/Users/Ashar/Documents/fish/data/bathymetry/normalized"),
        help="Destination for normalized outputs and manifest.",
    )
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    selected = list(SOURCES) if args.sources == "all" else [s.strip() for s in args.sources.split(",") if s.strip()]
    ensure_dir(args.output_dir)

    results = []
    for source_id in selected:
        config = SOURCES.get(source_id)
        if config is None:
            log.warning("Unknown source: %s", source_id)
            continue
        log.info("Normalizing %s from %s", source_id, config.input_path.name)
        result = normalize_source(config, args.output_dir)
        results.append(result)
        log.info(
            "  %s -> %s normalized, %s labels, %s skipped",
            source_id,
            result.normalized_features,
            result.label_features,
            result.skipped_features,
        )

    manifest_path = args.output_dir / "manifest.json"
    existing: Dict[str, dict] = {}
    if manifest_path.exists():
        try:
            for entry in json.loads(manifest_path.read_text()):
                if isinstance(entry, dict) and entry.get("source_id"):
                    existing[str(entry["source_id"])] = entry
        except json.JSONDecodeError:
            log.warning("Existing manifest is invalid JSON; rebuilding it from selected sources only.")

    for result in results:
        existing[result.source_id] = asdict(result)

    ordered = []
    for source_id in SOURCES:
        if source_id in existing:
            ordered.append(existing[source_id])
    for source_id, entry in sorted(existing.items()):
        if source_id not in SOURCES:
            ordered.append(entry)

    with manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(ordered, handle, indent=2)
    log.info("Wrote manifest: %s", manifest_path)


if __name__ == "__main__":
    main()
