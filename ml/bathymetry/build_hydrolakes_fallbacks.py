#!/usr/bin/env python3
"""
Build jurisdiction-specific HydroLAKES fallback lanes for uncovered regions.

These lanes are intentionally coarse. They give OpenCatch an honest
jurisdiction-specific inland layer for places where we do not yet have
state/provincial survey contours, using HydroLAKES polygons plus average depth
attributes. We derive a conservative estimated max depth purely so the existing
`lake_summary` map styling can show something useful on-device.

Outputs GeoJSON files to:
    /Users/Ashar/Documents/fish/data/bathymetry/hydrolakes/<source>_hydrolakes.geojson
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import pyogrio


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("build_hydrolakes_fallbacks")


ADMIN_PATH = (
    "/vsizip//Users/Ashar/Documents/fish/data/admin/"
    "ne_10m_admin_1_states_provinces.zip/ne_10m_admin_1_states_provinces.shp"
)
HYDROLAKES_PATH = (
    "/vsizip//Users/Ashar/Documents/fish/data/bathymetry/global/"
    "HydroLAKES_polys_v10.zip/HydroLAKES_polys_v10_shp/HydroLAKES_polys_v10.shp"
)
OUTPUT_DIR = Path("/Users/Ashar/Documents/fish/data/bathymetry/hydrolakes")


@dataclass(frozen=True)
class Target:
    source_id: str
    iso_3166_2: str
    country: str
    default_name: str
    geometry_mode: str = "polygon"
    min_lake_area_km2: float = 0.0


TARGETS: dict[str, Target] = {
    "hi": Target("hi", "US-HI", "United States of America", "Hawaii lake"),
    "nl": Target("nl", "CA-NL", "Canada", "Newfoundland and Labrador lake", "polygon", 1.0),
    "nt": Target("nt", "CA-NT", "Canada", "Northwest Territories lake", "point", 1.0),
    "nu": Target("nu", "CA-NU", "Canada", "Nunavut lake", "point", 10.0),
    "pe": Target("pe", "CA-PE", "Canada", "Prince Edward Island lake", "point"),
    "yt": Target("yt", "CA-YT", "Canada", "Yukon lake", "point"),
}


def load_boundaries() -> gpd.GeoDataFrame:
    admin = gpd.read_file(ADMIN_PATH)
    admin = admin[admin["iso_3166_2"].isin([t.iso_3166_2 for t in TARGETS.values()])].copy()
    admin = admin[["iso_3166_2", "admin", "name", "postal", "geometry"]]
    if admin.empty:
        raise SystemExit("No admin boundaries found for HydroLAKES fallback targets.")
    return admin


def derive_estimated_max_depth(depth_avg: gpd.GeoSeries) -> gpd.GeoSeries:
    # Conservative heuristic: keep the coarse fallback visually useful without
    # pretending we know the true max depth. This is only used for lake-summary
    # styling in uncovered jurisdictions.
    return np.maximum(depth_avg * 2.0, depth_avg + 1.0).round(3)


def build_target(admin_row, target: Target) -> Path | None:
    geom = admin_row.geometry
    point_filter_geom = (
        geom.simplify(0.05, preserve_topology=True)
        if target.geometry_mode == "point" and target.min_lake_area_km2 >= 1.0
        else geom
    )
    minx, miny, maxx, maxy = geom.bounds
    log.info("Reading HydroLAKES for %s within bbox %s", target.iso_3166_2, (minx, miny, maxx, maxy))
    where_clause = f"Country = '{target.country}' AND Depth_avg > 0"
    if target.geometry_mode == "point":
        read_kwargs = dict(
            bbox=(minx, miny, maxx, maxy),
            columns=[
                "Hylak_id",
                "Lake_name",
                "Country",
                "Continent",
                "Lake_area",
                "Depth_avg",
                "Vol_total",
                "Pour_long",
                "Pour_lat",
            ],
            read_geometry=False,
            where=where_clause,
        )
        try:
            lakes = pyogrio.read_dataframe(
                HYDROLAKES_PATH,
                use_arrow=True,
                **read_kwargs,
            )
        except Exception:
            lakes = pyogrio.read_dataframe(
                HYDROLAKES_PATH,
                **read_kwargs,
            )
        if lakes.empty:
            log.warning("%s: no HydroLAKES records in bbox", target.source_id)
            return None
        if target.min_lake_area_km2 > 0:
            lakes = lakes[lakes["Lake_area"].fillna(0) >= target.min_lake_area_km2].copy()
            if lakes.empty:
                log.warning(
                    "%s: no HydroLAKES records survived lake-area threshold >= %.2f km²",
                    target.source_id,
                    target.min_lake_area_km2,
                )
                return None
        log.info("%s: %s HydroLAKES point candidates after bbox/attribute filters", target.source_id, len(lakes))
        pour_points = gpd.GeoSeries(
            gpd.points_from_xy(lakes["Pour_long"], lakes["Pour_lat"]),
            index=lakes.index,
            crs="EPSG:4326",
        )
        lakes = lakes[pour_points.within(point_filter_geom) | pour_points.touches(point_filter_geom)].copy()
        if lakes.empty:
            log.warning("%s: no HydroLAKES points survived jurisdiction filtering", target.source_id)
            return None
        log.info("%s: %s HydroLAKES point features after jurisdiction filter", target.source_id, len(lakes))
        lakes = gpd.GeoDataFrame(
            lakes,
            geometry=gpd.points_from_xy(lakes["Pour_long"], lakes["Pour_lat"]),
            crs="EPSG:4326",
        )
    else:
        lakes = gpd.read_file(
            HYDROLAKES_PATH,
            bbox=(minx, miny, maxx, maxy),
            where=where_clause,
        )
        if lakes.empty:
            log.warning("%s: no HydroLAKES polygons in bbox", target.source_id)
            return None
        if target.min_lake_area_km2 > 0:
            lakes = lakes[lakes["Lake_area"].fillna(0) >= target.min_lake_area_km2].copy()
            if lakes.empty:
                log.warning(
                    "%s: no HydroLAKES polygons survived lake-area threshold >= %.2f km²",
                    target.source_id,
                    target.min_lake_area_km2,
                )
                return None
        log.info("%s: %s HydroLAKES polygon candidates after bbox/attribute filters", target.source_id, len(lakes))

        # HydroLAKES polygons can be extremely complex in northern Canada. Filtering
        # by pour-point location is much cheaper than polygon intersection while
        # still giving a reliable jurisdiction assignment for this coarse fallback.
        pour_points = gpd.GeoSeries(
            gpd.points_from_xy(lakes["Pour_long"], lakes["Pour_lat"]),
            index=lakes.index,
            crs="EPSG:4326",
        )
        lakes = lakes[pour_points.within(geom) | pour_points.touches(geom)].copy()
        log.info("%s: %s HydroLAKES polygons after jurisdiction filter", target.source_id, len(lakes))

    if lakes.empty:
        log.warning("%s: no HydroLAKES polygons survived jurisdiction filtering", target.source_id)
        return None

    lakes["Est_max_depth_m"] = derive_estimated_max_depth(lakes["Depth_avg"])
    lakes["Jurisdiction"] = admin_row["name"]
    lakes["JurisdictionISO"] = target.iso_3166_2
    lakes["DefaultName"] = target.default_name

    keep = [
        "Hylak_id",
        "Lake_name",
        "Country",
        "Continent",
        "Lake_area",
        "Depth_avg",
        "Est_max_depth_m",
        "Vol_total",
        "Pour_long",
        "Pour_lat",
        "Jurisdiction",
        "JurisdictionISO",
        "DefaultName",
        "geometry",
    ]
    lakes = lakes[keep]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{target.source_id}_hydrolakes.geojson"
    pyogrio.write_dataframe(lakes, out_path, driver="GeoJSON")
    log.info(
        "%s -> %s features written to %s (bounds=%s)",
        target.source_id,
        len(lakes),
        out_path,
        tuple(round(v, 4) for v in lakes.total_bounds),
    )
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build coarse jurisdiction-specific HydroLAKES fallback lanes.")
    parser.add_argument(
        "--sources",
        default="all",
        help="Comma-separated source ids or 'all' (default: all supported uncovered jurisdictions)",
    )
    args = parser.parse_args()

    selected = set(TARGETS) if args.sources == "all" else {s.strip() for s in args.sources.split(",") if s.strip()}
    missing = sorted(selected - set(TARGETS))
    if missing:
        raise SystemExit(f"Unsupported sources requested: {', '.join(missing)}")

    admin = load_boundaries()
    for source_id in sorted(selected):
        target = TARGETS[source_id]
        row = admin[admin["iso_3166_2"] == target.iso_3166_2]
        if row.empty:
            log.warning("%s: missing admin boundary", source_id)
            continue
        build_target(row.iloc[0], target)


if __name__ == "__main__":
    main()
