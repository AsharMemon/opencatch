#!/usr/bin/env python3
"""
Extract Pennsylvania PFBC statewide lake polygons into a lightweight GeoJSON.

The PASDA/PFBC package is not bathymetry contour geometry, but it is a strong
official statewide waterbody footprint layer with names and coordinates. We use
it as a state-specific coverage/footprint lane so Pennsylvania no longer has to
fall back entirely to coarse LAGOS styling.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pyogrio


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("extract_pa_pfbc_footprints")


INPUT_PATH = Path(
    "/Users/Ashar/Documents/fish/data/bathymetry/pa/extracted/Lakes_PFBCDatabase202411.shp"
)
OUTPUT_PATH = Path(
    "/Users/Ashar/Documents/fish/data/bathymetry/pa/pa_lake_footprints.geojson"
)


def main() -> None:
    if not INPUT_PATH.exists():
        raise SystemExit(f"Missing Pennsylvania source shapefile: {INPUT_PATH}")

    columns = [
        "COMID",
        "GNIS_ID",
        "GNIS_NAME",
        "WtrName",
        "WtrTypeCod",
        "AreaAcres",
        "County",
        "Latitude",
        "Longitude",
        "Web_Link",
        "Web_Link2",
    ]
    gdf = pyogrio.read_dataframe(INPUT_PATH, columns=columns)
    if gdf.empty:
        raise SystemExit("Pennsylvania PFBC shapefile loaded but returned no features.")

    rename = {
        "COMID": "comid",
        "GNIS_ID": "gnis_id",
        "GNIS_NAME": "gnis_name",
        "WtrName": "water_name",
        "WtrTypeCod": "water_type",
        "AreaAcres": "area_acres",
        "County": "county",
        "Latitude": "latitude",
        "Longitude": "longitude",
        "Web_Link": "web_link",
        "Web_Link2": "web_link2",
    }
    gdf = gdf.rename(columns=rename)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    pyogrio.write_dataframe(gdf, OUTPUT_PATH, driver="GeoJSON")
    log.info("Wrote %s Pennsylvania lake footprints to %s", len(gdf), OUTPUT_PATH)


if __name__ == "__main__":
    main()
