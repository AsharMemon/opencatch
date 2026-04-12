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
- Alabama (`al_contours.geojson`)     -> tile-ready contour lines + labels
- Alaska (`ak_bathymetry.geojson`)    -> tile-ready depth-band polygons
- Arkansas (`ar_contours.geojson`)     -> tile-ready contour lines + labels
- Alberta (`ab_contours.geojson`)      -> tile-ready contour lines + labels
- British Columbia (`bc_bathymetric_polygons.geojson`) -> tile-ready depth-band polygons + labels
- Delaware (`de_bathymetry.geojson`)   -> tile-ready contour lines + labels
- New Brunswick (`nb_bathy_full.geojson`) -> depth-point survey soundings (catalog-only)
- Missouri (`mo_clearwater_depth_points.geojson`) -> depth-point sounding lane
- Connecticut (`ct_full_contours.geojson`) -> tile-ready contour lines + labels
- Florida (`fl_contours.geojson`)      -> tile-ready contour lines + labels
- Indiana (`in_full_contours.geojson`) -> tile-ready contour lines + labels
- Illinois (`il_contours.geojson`)     -> tile-ready contour lines + labels
- Kansas (`ks_contours.geojson`)       -> tile-ready contour lines + labels
- New Hampshire (`nh_contours.geojson`)-> tile-ready depth bands + labels
- Nebraska (`ne_contours.geojson`)     -> tile-ready contour lines + labels
- North Dakota (`nd_full_contours.geojson`) -> tile-ready contour lines + labels
- Nova Scotia (`ns_lake_survey_points.geojson`) -> surveyed lake footprints (catalog-only)
- Maine (`me_regions.geojson`)         -> survey region index points only
- Oklahoma (`ok_depth_points.geojson`) -> official statewide max-depth points
- Michigan (`mi_full_contours.geojson`)-> tile-ready contour lines + labels
- Massachusetts (`ma_contours.geojson`)-> tile-ready contour lines + labels
- Montana (`mt_contours.geojson`)      -> tile-ready contour lines + labels
- Ontario (`on_contours.geojson`)      -> tile-ready contour lines + labels
- Ohio (`oh_contours.geojson`)         -> tile-ready contour lines + labels
- Texas (`tx_contours.geojson`)        -> tile-ready contour lines + labels
- Hawaii (`hi_hydrolakes.geojson`)     -> coarse HydroLAKES summary polygons
- Newfoundland & Labrador (`nl_hydrolakes.geojson`) -> coarse HydroLAKES summary polygons
- Northwest Territories (`nt_hydrolakes.geojson`) -> coarse HydroLAKES summary polygons
- Nunavut (`nu_hydrolakes.geojson`)    -> coarse HydroLAKES summary polygons
- Prince Edward Island (`pe_hydrolakes.geojson`) -> coarse HydroLAKES summary polygons
- Quebec (`qc_contours.geojson`)       -> tile-ready contour lines + labels
- Vermont (`vt_data.geojson`)          -> tile-ready contour lines + labels
- Washington (`wa_contours.geojson`)   -> tile-ready contour lines + labels
- Wisconsin (`wi_hypsography_polygons.geojson`) -> lake summary polygons only
- Iowa (`ia_contours.geojson`)         -> lake summary polygons only (not tile-ready)
- Saskatchewan (`sk_contours.geojson`) -> survey index points only
- Manitoba (`mb_data.geojson`)         -> waterbody/survey index points only
- Yukon (`yt_hydrolakes.geojson`)      -> coarse HydroLAKES summary polygons

Usage
-----
python normalize_survey_geojson.py \
    --sources al,ak,ar,ab,bc,de,nb,mo,ct,fl,in,il,ks,nh,ne,nd,ns,me,mi,ma,mt,on,oh,ok,pa,tx,hi,nl,nt,nu,pe,qc,vt,wa,wi,ia,sk,mb,yt \
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
    "al": SourceConfig(
        source_id="al",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/al/al_contours.geojson"),
        output_mode="contour_lines",
        source_name="al_survey",
        attribution="Alabama ADCNR Lake Contours",
        default_lake_name="Alabama survey lake",
        tile_ready=True,
        notes="Official Alabama Department of Conservation and Natural Resources bathymetry contours; VALUE stores contour depth in feet.",
    ),
    "ak": SourceConfig(
        source_id="ak",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/ak/ak_bathymetry.geojson"),
        output_mode="depth_polygons",
        source_name="ak_survey",
        attribution="Alaska ADF&G Lake Bathymetry",
        default_lake_name="Alaska survey lake",
        tile_ready=True,
        notes="Official Alaska Department of Fish and Game bathymetry polygons with per-polygon depth in feet.",
    ),
    "ar": SourceConfig(
        source_id="ar",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/ar/ar_contours.geojson"),
        output_mode="contour_lines",
        source_name="ar_survey",
        attribution="USGS Nimrod Lake Bathymetry Contours",
        default_lake_name="Arkansas survey lake",
        tile_ready=True,
        notes="Contours exported from the Nimrod Lake USGS geodatabase. Contour values are stored in feet.",
    ),
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
    "bc": SourceConfig(
        source_id="bc",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/bc/bc_bathymetric_polygons.geojson"),
        output_mode="depth_polygons",
        source_name="bc_survey",
        attribution="BC Bathymetric Maps of Surveyed Lakes",
        default_lake_name="British Columbia survey lake",
        tile_ready=True,
        notes="Official BC bathymetric polygon slices from the WHSE_FISH.BATH_LAKE_BATHYMETRIC_SP WFS.",
    ),
    "de": SourceConfig(
        source_id="de",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/de/de_contours.geojson"),
        output_mode="contour_lines",
        source_name="de_survey",
        attribution="Delaware DNREC Public Ponds Bathymetry",
        default_lake_name="Delaware public pond",
        tile_ready=True,
        notes="Contour lines from the Delaware FirstMap DE_Public_Ponds bathymetry layer; LABEL stores depth in feet.",
    ),
    "nb": SourceConfig(
        source_id="nb",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/nb/nb_bathy_full.geojson"),
        output_mode="depth_points",
        source_name="nb_depth_points",
        attribution="New Brunswick ERD Lake Depth Bathymetry Points",
        default_lake_name="New Brunswick bathymetry point",
        tile_ready=False,
        notes="Survey sounding points only; useful for training and future sounding layers, not direct contour fills.",
    ),
    "mo": SourceConfig(
        source_id="mo",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/mo/mo_clearwater_depth_points.geojson"),
        output_mode="depth_points",
        source_name="mo_depth_points",
        attribution="USGS Clearwater Lake 1 m Bathymetry",
        default_lake_name="Missouri bathymetry point",
        tile_ready=False,
        notes="Downsampled sounding points derived from the Clearwater Lake 1 m bathymetry survey.",
    ),
    "ct": SourceConfig(
        source_id="ct",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/ct/ct_full_contours.geojson"),
        output_mode="contour_lines",
        source_name="ct_survey",
        attribution="Connecticut DEEP Lake Bathymetry",
        default_lake_name="Connecticut survey lake",
        tile_ready=True,
        notes="Contour lines from CT DEEP layer 1 with DEPTH_FT and WBNAME fields.",
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
    "in": SourceConfig(
        source_id="in",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/in/in_full_contours.geojson"),
        output_mode="contour_lines",
        source_name="in_survey",
        attribution="Indiana DNR Lake Bathymetry",
        default_lake_name="Indiana survey lake",
        tile_ready=True,
        notes="Contour lines from gisdata.in.gov Hosted/Lake_Bathymetry_RO with contour values in feet.",
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
    "ks": SourceConfig(
        source_id="ks",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/ks/ks_contours.geojson"),
        output_mode="contour_lines",
        source_name="ks_survey",
        attribution="Kansas Biological Survey Bathymetry Contours",
        default_lake_name="Kansas survey lake",
        tile_ready=True,
        notes="Official Kansas Biological Survey reservoir bathymetry contours with depth stored in CONTOUR feet.",
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
    "ne": SourceConfig(
        source_id="ne",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/ne/ne_contours.geojson"),
        output_mode="contour_lines",
        source_name="ne_survey",
        attribution="Nebraska NGPC Lake Contours",
        default_lake_name="Nebraska survey lake",
        tile_ready=True,
        notes="Contour lines with depth stored in feet in the Depth field.",
    ),
    "nd": SourceConfig(
        source_id="nd",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/nd/nd_full_contours.geojson"),
        output_mode="contour_lines",
        source_name="nd_survey",
        attribution="North Dakota Game and Fish Lake Contours",
        default_lake_name="North Dakota survey lake",
        tile_ready=True,
        notes="Contour lines from ND GIS Hub layer 0 with CONTOUR values in feet below pool.",
    ),
    "ns": SourceConfig(
        source_id="ns",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/ns/ns_lake_survey_points.geojson"),
        output_mode="survey_footprint",
        source_name="ns_survey_footprint",
        attribution="Nova Scotia Lake Survey Lakes Locations",
        default_lake_name="Nova Scotia surveyed lake",
        tile_ready=False,
        notes="Surveyed-lake footprint polygons only; useful for coverage/catalog and downstream joins, not direct contours.",
    ),
    "me": SourceConfig(
        source_id="me",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/me/me_regions.geojson"),
        output_mode="survey_index",
        source_name="me_index",
        attribution="Maine IF&W Lake Depth Regions",
        default_lake_name="Maine lake depth region",
        tile_ready=False,
        notes="Regional KMZ index for Maine depth maps; useful as a catalog/coverage lane.",
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
    "ok": SourceConfig(
        source_id="ok",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/ok/ok_depth_points.geojson"),
        output_mode="depth_points",
        source_name="ok_depth_points",
        attribution="Oklahoma OWRB Lakes Inventory",
        default_lake_name="Oklahoma lake",
        tile_ready=True,
        notes="Official statewide OWRB lake inventory with coordinates and max depth in feet.",
    ),
    "pa": SourceConfig(
        source_id="pa",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/pa/pa_lake_footprints.geojson"),
        output_mode="survey_footprint",
        source_name="pa_pfbc_footprint",
        attribution="Pennsylvania PFBC Lakes Database",
        default_lake_name="Pennsylvania lake",
        tile_ready=False,
        notes="Official statewide PFBC lake polygons; useful as a state-specific coverage and lake-footprint lane.",
    ),
    "ri": SourceConfig(
        source_id="ri",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/ri/ri_waterbody_index.geojson"),
        output_mode="survey_index",
        source_name="ri_lake_management_index",
        attribution="Rhode Island DEM Lake Management Plans",
        default_lake_name="Rhode Island lake",
        tile_ready=False,
        notes="Official DEM lake-management/project index with approximate public geocodes for map placement.",
    ),
    "ny": SourceConfig(
        source_id="ny",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/ny/ny_waterbody_index.geojson"),
        output_mode="survey_index",
        source_name="ny_dec_contour_index",
        attribution="New York DEC Contour Maps",
        default_lake_name="New York lake",
        tile_ready=False,
        notes="Official statewide DEC contour-map landing-page index with approximate public geocodes for map placement.",
    ),
    "nj": SourceConfig(
        source_id="nj",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/nj/nj_waterbody_index.geojson"),
        output_mode="survey_index",
        source_name="nj_lake_plan_index",
        attribution="New Jersey DEP Lake Management Plans",
        default_lake_name="New Jersey lake",
        tile_ready=False,
        notes="Official NJ lake-plan PDF index with approximate public geocodes for map placement.",
    ),
    "va": SourceConfig(
        source_id="va",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/va/va_waterbody_index.geojson"),
        output_mode="survey_index",
        source_name="va_dwr_index",
        attribution="Virginia DWR Waterbody Pages",
        default_lake_name="Virginia waterbody",
        tile_ready=False,
        notes="Official statewide Virginia waterbody index built from DWR pages with embedded map coordinates and lake map/report links.",
    ),
    "wv": SourceConfig(
        source_id="wv",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/wv/wv_waterbody_index.geojson"),
        output_mode="survey_index",
        source_name="wv_lake_map_index",
        attribution="West Virginia DNR Lake Map Links",
        default_lake_name="West Virginia lake",
        tile_ready=False,
        notes="Official WVDNR lake-map PDF index with approximate public geocodes for map placement.",
    ),
    "tx": SourceConfig(
        source_id="tx",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/tx/tx_contours.geojson"),
        output_mode="contour_lines",
        source_name="tx_twdb_survey",
        attribution="Texas TWDB Completed Lake Surveys",
        default_lake_name="Texas survey lake",
        tile_ready=True,
        notes="Contours extracted from TWDB survey bundles with lake depth derived from water-surface elevation minus contour elevation.",
    ),
    "hi": SourceConfig(
        source_id="hi",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/hydrolakes/hi_hydrolakes.geojson"),
        output_mode="lake_summary",
        source_name="hi_hydrolakes",
        attribution="HydroLAKES coarse Hawaii lake summaries",
        default_lake_name="Hawaii lake",
        tile_ready=False,
        notes="HydroLAKES polygon fallback clipped to Hawaii; uses average depth with a conservative visual-only max-depth heuristic.",
    ),
    "nl": SourceConfig(
        source_id="nl",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/hydrolakes/nl_hydrolakes.geojson"),
        output_mode="lake_summary",
        source_name="nl_hydrolakes",
        attribution="HydroLAKES coarse Newfoundland and Labrador lake summaries",
        default_lake_name="Newfoundland and Labrador lake",
        tile_ready=False,
        notes="HydroLAKES polygon fallback clipped to Newfoundland and Labrador; uses average depth with a conservative visual-only max-depth heuristic.",
    ),
    "nt": SourceConfig(
        source_id="nt",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/hydrolakes/nt_hydrolakes.geojson"),
        output_mode="depth_points",
        source_name="nt_hydrolakes",
        attribution="HydroLAKES coarse Northwest Territories lake summaries",
        default_lake_name="Northwest Territories lake",
        tile_ready=False,
        notes="HydroLAKES pour-point fallback clipped to the Northwest Territories; uses average depth with a conservative visual-only max-depth heuristic.",
    ),
    "nu": SourceConfig(
        source_id="nu",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/hydrolakes/nu_hydrolakes.geojson"),
        output_mode="depth_points",
        source_name="nu_hydrolakes",
        attribution="HydroLAKES coarse Nunavut lake summaries",
        default_lake_name="Nunavut lake",
        tile_ready=False,
        notes="HydroLAKES pour-point fallback clipped to Nunavut; uses average depth with a conservative visual-only max-depth heuristic.",
    ),
    "pe": SourceConfig(
        source_id="pe",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/hydrolakes/pe_hydrolakes.geojson"),
        output_mode="depth_points",
        source_name="pe_hydrolakes",
        attribution="HydroLAKES coarse Prince Edward Island lake summaries",
        default_lake_name="Prince Edward Island lake",
        tile_ready=False,
        notes="HydroLAKES pour-point fallback clipped to Prince Edward Island; uses average depth with a conservative visual-only max-depth heuristic.",
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
    "wi": SourceConfig(
        source_id="wi",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/wi/wi_hypsography_polygons.geojson"),
        output_mode="lake_summary",
        source_name="wi_hypsography",
        attribution="Wisconsin hypsography joined to DNR hydro polygons",
        default_lake_name="Wisconsin lake",
        tile_ready=False,
        notes="Lake summary polygons with joined max depth from the Wisconsin hypsography package.",
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
    "yt": SourceConfig(
        source_id="yt",
        input_path=Path("/Users/Ashar/Documents/fish/data/bathymetry/hydrolakes/yt_hydrolakes.geojson"),
        output_mode="depth_points",
        source_name="yt_hydrolakes",
        attribution="HydroLAKES coarse Yukon lake summaries",
        default_lake_name="Yukon lake",
        tile_ready=False,
        notes="HydroLAKES pour-point fallback clipped to Yukon; uses average depth with a conservative visual-only max-depth heuristic.",
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


def is_valid_point_coords(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) >= 2
        and isinstance(value[0], (int, float))
        and isinstance(value[1], (int, float))
        and math.isfinite(value[0])
        and math.isfinite(value[1])
    )


def web_mercator_to_wgs84(x: float, y: float) -> list[float]:
    lon = (x / 20037508.34) * 180.0
    lat = (y / 20037508.34) * 180.0
    lat = 180.0 / math.pi * (2.0 * math.atan(math.exp(lat * math.pi / 180.0)) - math.pi / 2.0)
    return [lon, lat]


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

    if source_id == "al":
        lake_name = config.default_lake_name
        raw_id = props.get("OBJECTID") or feature.get("id") or "unknown"
        lake_id = f"al-{raw_id}"
        depth_ft = safe_float(props.get("VALUE"))
        depth_m = round(depth_ft * FT_TO_M, 3) if depth_ft is not None else None
    elif source_id == "ak":
        lake_name = props.get("Lake") or config.default_lake_name
        raw_id = props.get("InPoly_FID") or props.get("OBJECTID") or feature.get("id") or slugify(lake_name)
        lake_id = f"ak-{raw_id}"
        depth_ft = safe_float(props.get("Depth"))
        depth_m = round(depth_ft * FT_TO_M, 3) if depth_ft is not None else None
    elif source_id == "ar":
        lake_name = config.default_lake_name
        raw_id = props.get("OBJECTID") or feature.get("id") or "nimrod"
        lake_id = f"ar-{raw_id}"
        depth_ft = safe_float(props.get("Contour"))
        depth_m = round(depth_ft * FT_TO_M, 3) if depth_ft is not None else None
    elif source_id == "ab":
        lake_name = props.get("LAKE_NAME") or config.default_lake_name
        lake_id = props.get("BATHYMETRY_DIG_NUM") or lake_name
        depth_m = safe_float(props.get("CALC_DEP_M"))
        depth_ft = round(depth_m * M_TO_FT, 1) if depth_m is not None else None
    elif source_id == "bc":
        lake_name = props.get("LAKE_GAZETTED_NAME") or config.default_lake_name
        raw_id = props.get("LAKE_WSA_WATERBODY_IDENTIFIER") or props.get("LAKE_BATHYMETRIC_ID") or feature.get("id") or slugify(lake_name)
        lake_id = f"bc-{raw_id}"
        depth_m = safe_float(props.get("CONTOUR_DEPTH_M"))
        depth_ft = round(depth_m * M_TO_FT, 1) if depth_m is not None else None
        survey_date = props.get("LAKE_SURVEY_DATE")
    elif source_id == "nb":
        lake_name = config.default_lake_name
        raw_id = props.get("OBJECTID") or feature.get("id") or "unknown"
        lake_id = f"nb-{raw_id}"
        depth_ft = safe_float(props.get("DEPTH_FT"))
        depth_m = safe_float(props.get("DEPTH_M"))
        if depth_ft is not None:
            depth_ft = abs(depth_ft)
        if depth_m is not None:
            depth_m = abs(depth_m)
    elif source_id == "mo":
        lake_name = props.get("LAKE_NAME") or config.default_lake_name
        raw_id = props.get("LAKE_ID") or props.get("OBJECTID") or feature.get("id") or slugify(lake_name)
        lake_id = f"mo-{raw_id}"
        depth_ft = safe_float(props.get("DEPTH_FT"))
        depth_m = safe_float(props.get("DEPTH_M"))
    elif source_id == "ct":
        lake_name = props.get("WBNAME") or config.default_lake_name
        raw_id = props.get("OBJECTID") or feature.get("id") or slugify(lake_name)
        lake_id = f"ct-{raw_id}"
        depth_ft = safe_float(props.get("DEPTH_FT"))
        depth_m = round(depth_ft * FT_TO_M, 3) if depth_ft is not None else None
        survey_date = props.get("DATADATE")
    elif source_id == "de":
        lake_name = props.get("POND") or config.default_lake_name
        raw_id = feature.get("id") or slugify(lake_name)
        lake_id = f"de-{raw_id}"
        depth_ft = safe_float(props.get("LABEL") or props.get("DEPTH"))
        depth_m = round(depth_ft * FT_TO_M, 3) if depth_ft is not None else None
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
    elif source_id == "in":
        lake_name = props.get("lake_name") or config.default_lake_name
        raw_id = props.get("finfo_lakeid") or props.get("globalid") or props.get("objectid") or feature.get("id") or slugify(lake_name)
        lake_id = f"in-{raw_id}"
        depth_ft = safe_float(props.get("contour"))
        depth_m = round(depth_ft * FT_TO_M, 3) if depth_ft is not None else None
        survey_date = props.get("survey_date")
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
    elif source_id == "ne":
        lake_name = props.get("Name") or config.default_lake_name
        raw_id = props.get("OBJECTID") or feature.get("id") or slugify(lake_name)
        lake_id = f"ne-{raw_id}"
        depth_ft = safe_float(props.get("Depth"))
        depth_m = round(depth_ft * FT_TO_M, 3) if depth_ft is not None else None
    elif source_id == "nd":
        lake_name = props.get("LAKE_NAME") or config.default_lake_name
        raw_id = props.get("LAKE") or props.get("OBJECTID") or feature.get("id") or slugify(lake_name)
        lake_id = f"nd-{raw_id}"
        depth_ft = safe_float(props.get("CONTOUR"))
        if depth_ft is not None:
            depth_ft = abs(depth_ft)
            depth_m = round(depth_ft * FT_TO_M, 3)
    elif source_id == "ns":
        lake_name = props.get("lake_name") or props.get("alt_name") or config.default_lake_name
        raw_id = props.get("objectid") or feature.get("id") or slugify(lake_name)
        lake_id = f"ns-{raw_id}"
        survey_date = props.get("date_chg")
    elif source_id == "me":
        lake_name = config.default_lake_name
        raw_id = props.get("REGION_ID") or feature.get("id") or "unknown"
        lake_id = f"me-{raw_id}"
    elif source_id == "il":
        raw_id = props.get("OBJECTID") or feature.get("id") or "unknown"
        lake_id = f"il-{raw_id}"
        lake_name = props.get("LAKE_NAME") or config.default_lake_name
        depth_ft = safe_float(props.get("CONTOUR"))
        depth_m = round(depth_ft * FT_TO_M, 3) if depth_ft is not None else None
    elif source_id == "ks":
        raw_id = props.get("FID") or props.get("OBJECTID") or feature.get("id") or "unknown"
        lake_id = f"ks-{raw_id}"
        lake_name = props.get("RES_NAME") or props.get("RESERVOIR") or config.default_lake_name
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
    elif source_id == "ok":
        lake_name = props.get("LAKE_NAME") or config.default_lake_name
        lake_id = f"ok-{slugify(lake_name)}"
        depth_ft = safe_float(props.get("MAX_DEPTH_FT"))
        depth_m = round(depth_ft * FT_TO_M, 3) if depth_ft is not None else None
    elif source_id == "pa":
        lake_name = props.get("water_name") or props.get("gnis_name") or config.default_lake_name
        raw_id = props.get("comid") or props.get("gnis_id") or feature.get("id") or slugify(lake_name)
        lake_id = f"pa-{raw_id}"
    elif source_id == "tx":
        lake_name = props.get("LAKE_NAME") or config.default_lake_name
        raw_id = props.get("LAKE_SLUG") or props.get("lake_slug") or slugify(lake_name)
        lake_id = f"tx-{raw_id}"
        depth_ft = safe_float(props.get("DEPTH_FT"))
        if depth_ft is None:
            depth_ft = safe_float(props.get("Contour")) or safe_float(props.get("ELEV_FT"))
        depth_m = round(depth_ft * FT_TO_M, 3) if depth_ft is not None else None
        survey_date = props.get("SURVEY_DATE")
    elif source_id in {"hi", "nl", "nt", "nu", "pe", "yt"}:
        lake_name = props.get("Lake_name") or props.get("DefaultName") or config.default_lake_name
        raw_id = props.get("Hylak_id") or feature.get("id") or slugify(lake_name)
        lake_id = f"{source_id}-{raw_id}"
        depth_m = safe_float(props.get("Est_max_depth_m")) or safe_float(props.get("Depth_avg"))
        depth_ft = round(depth_m * M_TO_FT, 1) if depth_m is not None else None
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
    elif source_id == "wi":
        lake_name = props.get("WATERBODY_NAME") or config.default_lake_name
        raw_id = props.get("lake_code") or props.get("WATERBODY_WBIC") or feature.get("id") or slugify(lake_name)
        lake_id = f"wi-{raw_id}"
        depth_ft = safe_float(props.get("max_depth_ft"))
        depth_m = safe_float(props.get("max_depth_m"))
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
    elif source_id == "va":
        lake_name = props.get("lake_name") or config.default_lake_name
        lake_id = f"va-{props.get('slug') or slugify(lake_name)}"
    elif source_id == "ri":
        lake_name = props.get("lake_name") or config.default_lake_name
        lake_id = f"ri-{props.get('slug') or slugify(lake_name)}"
    elif source_id == "ny":
        lake_name = props.get("lake_name") or config.default_lake_name
        lake_id = f"ny-{props.get('slug') or slugify(lake_name)}"
    elif source_id == "nj":
        lake_name = props.get("lake_name") or config.default_lake_name
        lake_id = f"nj-{props.get('slug') or slugify(lake_name)}"
    elif source_id == "wv":
        lake_name = props.get("lake_name") or config.default_lake_name
        lake_id = f"wv-{props.get('slug') or slugify(lake_name)}"
    else:
        return None

    return {
        "lake_id": str(lake_id or slugify(lake_name)),
        "lake_name": str(lake_name or config.default_lake_name),
        "depth_m": round(depth_m, 3) if depth_m is not None else None,
        "depth_ft": round(depth_ft, 1) if depth_ft is not None else None,
        "source": config.source_name,
        "source_id": source_id,
        "contour_quality": (
            "moderate"
            if source_id == "ok"
            else ("survey" if config.tile_ready else "estimate")
        ),
        "attribution": config.attribution,
        "survey_date": survey_date,
    }


def normalize_feature(config: SourceConfig, feature: dict) -> Optional[dict]:
    props = feature.get("properties") or {}
    geometry = feature.get("geometry") or {}
    geom_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
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
    elif config.output_mode == "depth_points":
        if geom_type != "Point":
            return None
        if not is_valid_point_coords(coordinates):
            return None
        if base["depth_m"] is None and base["depth_ft"] is None:
            return None
        base["feature_kind"] = "depth_point"
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
    elif config.output_mode == "depth_polygons":
        if geom_type not in {"Polygon", "MultiPolygon"}:
            return None
        if base["depth_m"] is None and base["depth_ft"] is None:
            return None
        base["feature_kind"] = "depth_band"
        base["depth_min_ft"] = None
        base["depth_max_ft"] = base["depth_ft"]
        base["depth_min_m"] = None
        base["depth_max_m"] = base["depth_m"]
        base["depth_band_label"] = props.get("CONTOUR_DEPTH_LABEL") or (
            f"{round(float(base['depth_ft']))} ft" if base["depth_ft"] is not None else None
        )
        base["lake_max_depth_m"] = safe_float(props.get("LAKE_MAX_DEPTH_M"))
        base["lake_mean_depth_m"] = safe_float(props.get("LAKE_MEAN_DEPTH_M"))
        base["bathymap_pdf_url"] = props.get("BATHYMAP_PDF_URL_1")
    elif config.output_mode == "lake_summary":
        if geom_type not in {"Polygon", "MultiPolygon"}:
            return None
        base["feature_kind"] = "lake_summary"
        base["max_depth_ft"] = base["depth_ft"]
        base["max_depth_m"] = base["depth_m"]
        base["contour_count"] = props.get("ContourCount")
        if config.source_id in {"hi", "nl", "nt", "nu", "pe", "yt"}:
            base["mean_depth_m"] = safe_float(props.get("Depth_avg"))
            base["mean_depth_ft"] = (
                round(base["mean_depth_m"] * M_TO_FT, 1)
                if base.get("mean_depth_m") is not None
                else None
            )
            base["depth_method"] = "hydrolakes_avg_x2"
            base["lake_area_km2"] = safe_float(props.get("Lake_area"))
    elif config.output_mode == "survey_index":
        if geom_type != "Point":
            return None
        if not is_valid_point_coords(coordinates):
            return None
        if config.source_id == "mb":
            geometry = {
                **geometry,
                "coordinates": web_mercator_to_wgs84(float(coordinates[0]), float(coordinates[1])),
            }
        base["feature_kind"] = "survey_index"
        if config.source_id == "sk":
            base["scan_link"] = props.get("SCAN_LINK")
            base["map_scale"] = props.get("SCALE")
            base["contour_interval"] = props.get("CONTOUR_INT")
            base["quality_code"] = props.get("QUALITY")
        if config.source_id == "me":
            base["scan_link"] = props.get("KMZ_URL")
            base["region_north"] = props.get("NORTH")
            base["region_south"] = props.get("SOUTH")
            base["region_east"] = props.get("EAST")
            base["region_west"] = props.get("WEST")
        if config.source_id == "mb":
            base["has_contours"] = props.get("CONTOURS")
            base["printable_map"] = props.get("PRINTABLE_MAP")
            base["boat_launch"] = props.get("BOAT_LAUNCH")
            base["secchi_depth"] = props.get("SECCHI_DEPTH")
        if config.source_id == "va":
            base["waterbody_url"] = props.get("waterbody_url")
            base["map_pdf_url"] = props.get("map_pdf_url")
            base["report_pdf_urls"] = props.get("report_pdf_urls")
            base["coords_source"] = props.get("coords_source")
        if config.source_id == "ri":
            base["waterbody_url"] = props.get("waterbody_url")
            base["map_pdf_url"] = props.get("map_pdf_url")
            base["coords_source"] = props.get("coords_source")
        if config.source_id == "ny":
            base["waterbody_url"] = props.get("waterbody_url")
            base["region"] = props.get("region")
            base["coords_source"] = props.get("coords_source")
        if config.source_id == "nj":
            base["map_pdf_url"] = props.get("map_pdf_url")
            base["coords_source"] = props.get("coords_source")
        if config.source_id == "wv":
            base["waterbody_url"] = props.get("waterbody_url")
            base["map_pdf_url"] = props.get("map_pdf_url")
            base["report_pdf_urls"] = props.get("report_pdf_urls")
            base["coords_source"] = props.get("coords_source")
    elif config.output_mode == "survey_footprint":
        if geom_type not in {"Polygon", "MultiPolygon"}:
            return None
        base["feature_kind"] = "survey_footprint"
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
        if config.output_mode in {
            "contour_lines",
            "depth_points",
            "depth_bands",
            "depth_polygons",
            "lake_summary",
            "survey_index",
            "survey_footprint",
        }:
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
        default="ab,de,nb,ct,fl,in,me,mi,ma,mt,ne,nd,ns,on,qc,tx,vt,wa,wi,ia,sk,mb",
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
