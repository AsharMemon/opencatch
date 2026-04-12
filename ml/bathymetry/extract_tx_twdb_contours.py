#!/usr/bin/env python3
"""
Extract contour line layers from downloaded Texas TWDB shapefile bundles.

TWDB ZIPs contain multiple layers (contours, topo contours, sounding points,
territory-derived polygons, etc.). This script picks the best contour line layer
per downloaded bundle, reprojects it to WGS84, annotates lake metadata, and
combines the results into a single GeoJSON ready for normalization.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio
from shapely.geometry import mapping


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("extract_tx_twdb_contours")

DEFAULT_MANIFEST = Path("/Users/Ashar/Documents/fish/data/bathymetry/tx/raw/tx_twdb_download_manifest.csv")
DEFAULT_OUTPUT = Path("/Users/Ashar/Documents/fish/data/bathymetry/tx/tx_contours.geojson")


def _json_safe(value):
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value


def write_geojson_fallback(gdf: gpd.GeoDataFrame, output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8") as fh:
        fh.write('{"type":"FeatureCollection","features":[')
        first = True
        for _, row in gdf.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty or not geom.is_valid:
                continue
            feature = {
                "type": "Feature",
                "geometry": mapping(geom),
                "properties": {
                    col: _json_safe(row[col])
                    for col in gdf.columns
                    if col != "geometry"
                },
            }
            if not first:
                fh.write(",")
            json.dump(feature, fh, separators=(",", ":"))
            first = False
        fh.write("]}")


def prettify_lake_slug(slug: str) -> str:
    text = slug.replace("_", " ").replace("-", " ").strip()
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.title()


def choose_contour_layer(zip_path: Path) -> str | None:
    layers = pyogrio.list_layers(f"/vsizip/{zip_path}")
    best_name = None
    best_score = -10**9
    for layer_name, geom_type in layers:
        geom = str(geom_type)
        name = str(layer_name)
        if "LineString" not in geom:
            continue

        lname = name.lower()
        score = 0
        if "1ftcont" in lname:
            score += 100
        if "cont" in lname:
            score += 30
        if "topo" in lname:
            score -= 40
        if "survey" in lname or "pts" in lname:
            score -= 100
        if score > best_score:
            best_score = score
            best_name = name
    return best_name


def pick_contour_column(columns: list[str]) -> str | None:
    lowered = {col.lower(): col for col in columns}
    for key in ("contour", "depth_ft", "depth", "elev_ft"):
        if key in lowered:
            return lowered[key]
    return None


def choose_surface_layer(zip_path: Path) -> str | None:
    layers = pyogrio.list_layers(f"/vsizip/{zip_path}")
    best_name = None
    best_score = -10**9
    for layer_name, geom_type in layers:
        geom = str(geom_type)
        name = str(layer_name)
        lname = name.lower()
        score = 0
        if "Point" not in geom:
            continue
        if "surveypts" in lname:
            score += 100
        if "aeidwpts" in lname:
            score += 60
        if "pts" in lname:
            score += 20
        if score > best_score:
            best_name = name
            best_score = score
    return best_name


def derive_surface_ft(zip_path: Path) -> float | None:
    layer_name = choose_surface_layer(zip_path)
    if not layer_name:
        return None

    path = f"/vsizip/{zip_path}"
    try:
        gdf = gpd.read_file(path, layer=layer_name, rows=2500)
    except Exception:
        return None

    lowered = {col.lower(): col for col in gdf.columns}
    if "lake_eleva" in lowered:
        values = pd.to_numeric(gdf[lowered["lake_eleva"]], errors="coerce").dropna()
        if not values.empty:
            return float(values.median())
    if "z" in lowered and "current_el" in lowered:
        z = pd.to_numeric(gdf[lowered["z"]], errors="coerce")
        elev = pd.to_numeric(gdf[lowered["current_el"]], errors="coerce")
        values = (z + elev).dropna()
        if not values.empty:
            return float(values.median())
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract Texas TWDB contour layers into one GeoJSON.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--lakes", help="Optional comma-separated lake slugs to extract.")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    with args.manifest.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    if args.lakes:
        wanted = {item.strip().lower() for item in args.lakes.split(",") if item.strip()}
        rows = [row for row in rows if row.get("lake_slug", "").lower() in wanted]

    if args.limit > 0:
        rows = rows[: args.limit]

    frames: list[gpd.GeoDataFrame] = []

    for row in rows:
        zip_path = Path(row["local_zip_path"])
        if not zip_path.exists():
            log.warning("Missing Texas ZIP: %s", zip_path)
            continue

        layer_name = choose_contour_layer(zip_path)
        if not layer_name:
            log.warning("No contour line layer found in %s", zip_path)
            continue

        path = f"/vsizip/{zip_path}"
        gdf = gpd.read_file(path, layer=layer_name)
        if gdf.empty:
            continue
        if gdf.crs is not None and gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs(4326)

        contour_column = pick_contour_column(list(gdf.columns))
        if contour_column is None:
            log.warning("No contour field found in %s layer %s", zip_path, layer_name)
            continue

        surface_ft = derive_surface_ft(zip_path)
        if surface_ft is None:
            log.warning("Could not derive lake surface elevation for %s", zip_path)
            continue

        keep = [contour_column, "geometry"]
        if "Type" in gdf.columns:
            keep.insert(1, "Type")
        gdf = gdf[keep].copy()
        if contour_column != "ELEV_FT":
            gdf = gdf.rename(columns={contour_column: "ELEV_FT"})

        gdf["ELEV_FT"] = pd.to_numeric(gdf["ELEV_FT"], errors="coerce")
        gdf["SURFACE_FT"] = round(surface_ft, 3)
        gdf["DEPTH_FT"] = (surface_ft - gdf["ELEV_FT"]).round(3)
        gdf = gdf[gdf["DEPTH_FT"].notna() & (gdf["DEPTH_FT"] >= 0)]
        if gdf.empty:
            log.warning("All derived Texas depths were negative/empty for %s", zip_path)
            continue

        lake_slug = row["lake_slug"]
        gdf["LAKE_SLUG"] = lake_slug
        gdf["LAKE_NAME"] = prettify_lake_slug(lake_slug)
        gdf["SURVEY_DATE"] = row.get("survey_date") or None
        gdf["RELEASE_FOLDER"] = row.get("release_folder") or None
        gdf["SOURCE_URL"] = row.get("url") or None
        gdf["SOURCE_FILE"] = zip_path.name
        gdf["SOURCE_LAYER"] = layer_name
        frames.append(gdf)
        log.info(
            "Extracted %s contour features from %s (%s, surface %.2f ft)",
            len(gdf),
            lake_slug,
            layer_name,
            surface_ft,
        )

    if not frames:
        raise SystemExit("No Texas contour features extracted.")

    combined = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), geometry="geometry", crs="EPSG:4326")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    combined = combined[
        combined.geometry.notna()
        & ~combined.geometry.is_empty
        & combined.geometry.is_valid
    ].copy()
    combined["geometry"] = combined.geometry.simplify(1e-5, preserve_topology=True)
    combined = combined[
        combined.geometry.notna()
        & ~combined.geometry.is_empty
        & combined.geometry.is_valid
    ].copy()
    write_geojson_fallback(combined, args.output)
    log.info("Wrote %s Texas contour features across %s lakes to %s", len(combined), combined["LAKE_SLUG"].nunique(), args.output)


if __name__ == "__main__":
    main()
