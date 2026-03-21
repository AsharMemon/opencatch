"""
CreelCat Site Prior Lookup Service for CASTLINE production.

Provides Layer 1 "Site Prior" for any given location by combining:
- CreelCat spatial creel survey features (CPUE, species, nearby waterbodies)
- LAGOS morphometry data (depth, area, shoreline development index)
- GeoCLIP PCA-32 location embeddings

Uses BallTree for fast spatial nearest-neighbor lookup.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Comprehensive Species Taxonomy
# ---------------------------------------------------------------------------
# Each species entry contains biology-driven parameters used for
# conditions scoring, thermal comfort, and habitat suitability.
#
# Keys:
#   optimal_temp_c    – Peak activity water temperature (°C)
#   temp_range_c      – (low, high) active feeding range
#   pressure_sensitivity – "high" | "medium" | "low"
#   spawn_months      – Primary spawn window (1-12)
#   peak_feed_months  – Peak feeding activity months
#   depth_pref_m      – (shallow, deep) typical depth range
#   habitat           – Primary habitat descriptor
#   lat_range         – (south, north) approximate US latitude range
#   frontal_response  – How species responds to frontal passages
#   flow_preference   – Preferred flow conditions

SPECIES_CATALOG = {
    # ── BLACK BASS ──────────────────────────────────────────────
    "largemouth_bass": {
        "common_name": "Largemouth Bass",
        "optimal_temp_c": 23.5,
        "temp_range_c": (15.0, 30.0),
        "pressure_sensitivity": "high",
        "spawn_months": [3, 4, 5],
        "peak_feed_months": [4, 5, 6, 9, 10],
        "depth_pref_m": (1.0, 8.0),
        "habitat": "vegetation_structure",
        "lat_range": (25.0, 46.0),
        "frontal_response": "negative_strong",
        "flow_preference": "stable_slow",
    },
    "smallmouth_bass": {
        "common_name": "Smallmouth Bass",
        "optimal_temp_c": 18.5,
        "temp_range_c": (12.0, 24.0),
        "pressure_sensitivity": "high",
        "spawn_months": [4, 5, 6],
        "peak_feed_months": [5, 6, 9, 10],
        "depth_pref_m": (2.0, 15.0),
        "habitat": "rock_current",
        "lat_range": (33.0, 48.0),
        "frontal_response": "negative_strong",
        "flow_preference": "moderate_current",
    },
    "spotted_bass": {
        "common_name": "Spotted Bass",
        "optimal_temp_c": 21.0,
        "temp_range_c": (14.0, 27.0),
        "pressure_sensitivity": "high",
        "spawn_months": [3, 4, 5],
        "peak_feed_months": [4, 5, 6, 9, 10],
        "depth_pref_m": (3.0, 20.0),
        "habitat": "deep_rock_bluff",
        "lat_range": (30.0, 40.0),
        "frontal_response": "negative_moderate",
        "flow_preference": "moderate_current",
    },
    # ── TEMPERATE BASS ──────────────────────────────────────────
    "striped_bass": {
        "common_name": "Striped Bass",
        "optimal_temp_c": 18.0,
        "temp_range_c": (10.0, 24.0),
        "pressure_sensitivity": "medium",
        "spawn_months": [3, 4, 5],
        "peak_feed_months": [3, 4, 5, 10, 11],
        "depth_pref_m": (5.0, 30.0),
        "habitat": "open_water_pelagic",
        "lat_range": (28.0, 45.0),
        "frontal_response": "positive_moderate",
        "flow_preference": "current_oriented",
    },
    "hybrid_striped_bass": {
        "common_name": "Hybrid Striped Bass",
        "optimal_temp_c": 20.0,
        "temp_range_c": (12.0, 27.0),
        "pressure_sensitivity": "medium",
        "spawn_months": [4, 5],
        "peak_feed_months": [4, 5, 6, 9, 10],
        "depth_pref_m": (3.0, 20.0),
        "habitat": "open_water_pelagic",
        "lat_range": (30.0, 42.0),
        "frontal_response": "positive_moderate",
        "flow_preference": "current_oriented",
    },
    "white_bass": {
        "common_name": "White Bass",
        "optimal_temp_c": 18.0,
        "temp_range_c": (10.0, 25.0),
        "pressure_sensitivity": "medium",
        "spawn_months": [3, 4, 5],
        "peak_feed_months": [3, 4, 5, 9, 10],
        "depth_pref_m": (2.0, 15.0),
        "habitat": "open_water_river",
        "lat_range": (30.0, 46.0),
        "frontal_response": "neutral",
        "flow_preference": "current_oriented",
    },
    # ── WALLEYE / SAUGER ────────────────────────────────────────
    "walleye": {
        "common_name": "Walleye",
        "optimal_temp_c": 16.5,
        "temp_range_c": (8.0, 22.0),
        "pressure_sensitivity": "high",
        "spawn_months": [3, 4, 5],
        "peak_feed_months": [5, 6, 9, 10, 11],
        "depth_pref_m": (3.0, 15.0),
        "habitat": "rock_gravel_deep",
        "lat_range": (36.0, 49.0),
        "frontal_response": "negative_moderate",
        "flow_preference": "moderate_current",
    },
    "sauger": {
        "common_name": "Sauger",
        "optimal_temp_c": 15.0,
        "temp_range_c": (7.0, 20.0),
        "pressure_sensitivity": "high",
        "spawn_months": [3, 4],
        "peak_feed_months": [4, 5, 10, 11],
        "depth_pref_m": (3.0, 20.0),
        "habitat": "turbid_river",
        "lat_range": (35.0, 49.0),
        "frontal_response": "negative_moderate",
        "flow_preference": "current_oriented",
    },
    # ── PIKE / MUSKY ────────────────────────────────────────────
    "northern_pike": {
        "common_name": "Northern Pike",
        "optimal_temp_c": 18.0,
        "temp_range_c": (10.0, 24.0),
        "pressure_sensitivity": "medium",
        "spawn_months": [3, 4],
        "peak_feed_months": [5, 6, 9, 10],
        "depth_pref_m": (1.0, 8.0),
        "habitat": "vegetation_weed_edge",
        "lat_range": (38.0, 49.0),
        "frontal_response": "positive_slight",
        "flow_preference": "stable_slow",
    },
    "muskellunge": {
        "common_name": "Muskellunge",
        "optimal_temp_c": 20.0,
        "temp_range_c": (12.0, 25.0),
        "pressure_sensitivity": "high",
        "spawn_months": [4, 5],
        "peak_feed_months": [6, 9, 10, 11],
        "depth_pref_m": (2.0, 15.0),
        "habitat": "vegetation_structure",
        "lat_range": (38.0, 49.0),
        "frontal_response": "positive_moderate",
        "flow_preference": "stable_slow",
    },
    # ── CATFISH ─────────────────────────────────────────────────
    "channel_catfish": {
        "common_name": "Channel Catfish",
        "optimal_temp_c": 27.0,
        "temp_range_c": (18.0, 32.0),
        "pressure_sensitivity": "low",
        "spawn_months": [5, 6, 7],
        "peak_feed_months": [5, 6, 7, 8, 9],
        "depth_pref_m": (1.0, 12.0),
        "habitat": "channel_structure",
        "lat_range": (25.0, 46.0),
        "frontal_response": "positive_strong",
        "flow_preference": "rising_current",
    },
    "blue_catfish": {
        "common_name": "Blue Catfish",
        "optimal_temp_c": 25.0,
        "temp_range_c": (16.0, 30.0),
        "pressure_sensitivity": "low",
        "spawn_months": [5, 6, 7],
        "peak_feed_months": [3, 4, 5, 9, 10, 11],
        "depth_pref_m": (3.0, 25.0),
        "habitat": "deep_channel_river",
        "lat_range": (28.0, 42.0),
        "frontal_response": "positive_strong",
        "flow_preference": "rising_current",
    },
    "flathead_catfish": {
        "common_name": "Flathead Catfish",
        "optimal_temp_c": 26.0,
        "temp_range_c": (18.0, 30.0),
        "pressure_sensitivity": "low",
        "spawn_months": [5, 6, 7],
        "peak_feed_months": [6, 7, 8, 9],
        "depth_pref_m": (2.0, 15.0),
        "habitat": "structure_ambush",
        "lat_range": (28.0, 44.0),
        "frontal_response": "neutral",
        "flow_preference": "moderate_current",
    },
    # ── PANFISH ─────────────────────────────────────────────────
    "black_crappie": {
        "common_name": "Black Crappie",
        "optimal_temp_c": 20.0,
        "temp_range_c": (12.0, 25.0),
        "pressure_sensitivity": "high",
        "spawn_months": [3, 4, 5],
        "peak_feed_months": [3, 4, 5, 10, 11],
        "depth_pref_m": (2.0, 10.0),
        "habitat": "brush_timber",
        "lat_range": (27.0, 46.0),
        "frontal_response": "negative_strong",
        "flow_preference": "stable_slow",
    },
    "white_crappie": {
        "common_name": "White Crappie",
        "optimal_temp_c": 21.0,
        "temp_range_c": (14.0, 27.0),
        "pressure_sensitivity": "high",
        "spawn_months": [3, 4, 5],
        "peak_feed_months": [3, 4, 5, 10, 11],
        "depth_pref_m": (2.0, 12.0),
        "habitat": "brush_timber_turbid",
        "lat_range": (28.0, 44.0),
        "frontal_response": "negative_strong",
        "flow_preference": "stable_slow",
    },
    "bluegill": {
        "common_name": "Bluegill",
        "optimal_temp_c": 25.0,
        "temp_range_c": (18.0, 30.0),
        "pressure_sensitivity": "medium",
        "spawn_months": [5, 6, 7, 8],
        "peak_feed_months": [5, 6, 7, 8],
        "depth_pref_m": (0.5, 5.0),
        "habitat": "vegetation_shallow",
        "lat_range": (25.0, 46.0),
        "frontal_response": "negative_slight",
        "flow_preference": "stable_slow",
    },
    "redear_sunfish": {
        "common_name": "Redear Sunfish",
        "optimal_temp_c": 24.0,
        "temp_range_c": (16.0, 28.0),
        "pressure_sensitivity": "medium",
        "spawn_months": [4, 5, 6],
        "peak_feed_months": [4, 5, 6, 7],
        "depth_pref_m": (1.0, 8.0),
        "habitat": "vegetation_hard_bottom",
        "lat_range": (27.0, 40.0),
        "frontal_response": "negative_slight",
        "flow_preference": "stable_slow",
    },
    "yellow_perch": {
        "common_name": "Yellow Perch",
        "optimal_temp_c": 18.0,
        "temp_range_c": (10.0, 22.0),
        "pressure_sensitivity": "medium",
        "spawn_months": [3, 4, 5],
        "peak_feed_months": [5, 6, 9, 10],
        "depth_pref_m": (2.0, 12.0),
        "habitat": "weeds_gravel",
        "lat_range": (38.0, 49.0),
        "frontal_response": "negative_moderate",
        "flow_preference": "stable_slow",
    },
    # ── TROUT / SALMON ──────────────────────────────────────────
    "rainbow_trout": {
        "common_name": "Rainbow Trout",
        "optimal_temp_c": 13.0,
        "temp_range_c": (7.0, 18.0),
        "pressure_sensitivity": "medium",
        "spawn_months": [1, 2, 3, 4],
        "peak_feed_months": [3, 4, 5, 9, 10, 11],
        "depth_pref_m": (0.5, 6.0),
        "habitat": "cold_stream_tailwater",
        "lat_range": (32.0, 49.0),
        "frontal_response": "positive_moderate",
        "flow_preference": "rising_current",
    },
    "brown_trout": {
        "common_name": "Brown Trout",
        "optimal_temp_c": 14.0,
        "temp_range_c": (7.0, 19.0),
        "pressure_sensitivity": "high",
        "spawn_months": [10, 11, 12],
        "peak_feed_months": [3, 4, 5, 9, 10],
        "depth_pref_m": (0.5, 8.0),
        "habitat": "cold_stream_undercut",
        "lat_range": (33.0, 49.0),
        "frontal_response": "positive_slight",
        "flow_preference": "rising_current",
    },
    "brook_trout": {
        "common_name": "Brook Trout",
        "optimal_temp_c": 12.0,
        "temp_range_c": (5.0, 16.0),
        "pressure_sensitivity": "medium",
        "spawn_months": [9, 10, 11],
        "peak_feed_months": [4, 5, 6, 9, 10],
        "depth_pref_m": (0.3, 4.0),
        "habitat": "cold_headwater",
        "lat_range": (34.0, 49.0),
        "frontal_response": "neutral",
        "flow_preference": "moderate_current",
    },
    "lake_trout": {
        "common_name": "Lake Trout",
        "optimal_temp_c": 10.0,
        "temp_range_c": (4.0, 14.0),
        "pressure_sensitivity": "low",
        "spawn_months": [10, 11],
        "peak_feed_months": [5, 6, 9, 10],
        "depth_pref_m": (10.0, 60.0),
        "habitat": "deep_cold_lake",
        "lat_range": (42.0, 49.0),
        "frontal_response": "neutral",
        "flow_preference": "stable_slow",
    },
    "steelhead": {
        "common_name": "Steelhead",
        "optimal_temp_c": 12.0,
        "temp_range_c": (5.0, 16.0),
        "pressure_sensitivity": "medium",
        "spawn_months": [1, 2, 3, 4],
        "peak_feed_months": [10, 11, 12, 1, 2, 3],
        "depth_pref_m": (0.5, 6.0),
        "habitat": "cold_river_gravel",
        "lat_range": (38.0, 49.0),
        "frontal_response": "positive_moderate",
        "flow_preference": "rising_current",
    },
    "chinook_salmon": {
        "common_name": "Chinook Salmon",
        "optimal_temp_c": 11.0,
        "temp_range_c": (5.0, 15.0),
        "pressure_sensitivity": "low",
        "spawn_months": [9, 10, 11],
        "peak_feed_months": [4, 5, 6, 7, 8],
        "depth_pref_m": (5.0, 40.0),
        "habitat": "deep_cold_lake_river",
        "lat_range": (42.0, 49.0),
        "frontal_response": "neutral",
        "flow_preference": "current_oriented",
    },
    "coho_salmon": {
        "common_name": "Coho Salmon",
        "optimal_temp_c": 12.0,
        "temp_range_c": (6.0, 16.0),
        "pressure_sensitivity": "low",
        "spawn_months": [9, 10, 11],
        "peak_feed_months": [4, 5, 6, 7, 8],
        "depth_pref_m": (3.0, 25.0),
        "habitat": "cold_lake_tributary",
        "lat_range": (42.0, 49.0),
        "frontal_response": "neutral",
        "flow_preference": "current_oriented",
    },
    # ── ROUGH FISH / OTHER ──────────────────────────────────────
    "smallmouth_buffalo": {
        "common_name": "Smallmouth Buffalo",
        "optimal_temp_c": 24.0,
        "temp_range_c": (16.0, 30.0),
        "pressure_sensitivity": "low",
        "spawn_months": [4, 5, 6],
        "peak_feed_months": [4, 5, 6, 7, 8, 9],
        "depth_pref_m": (2.0, 12.0),
        "habitat": "river_reservoir",
        "lat_range": (28.0, 46.0),
        "frontal_response": "neutral",
        "flow_preference": "moderate_current",
    },
    "paddlefish": {
        "common_name": "Paddlefish",
        "optimal_temp_c": 16.0,
        "temp_range_c": (10.0, 24.0),
        "pressure_sensitivity": "low",
        "spawn_months": [3, 4, 5],
        "peak_feed_months": [3, 4, 5, 6, 9, 10],
        "depth_pref_m": (3.0, 20.0),
        "habitat": "large_river_reservoir",
        "lat_range": (30.0, 46.0),
        "frontal_response": "neutral",
        "flow_preference": "rising_current",
    },
    "bowfin": {
        "common_name": "Bowfin",
        "optimal_temp_c": 25.0,
        "temp_range_c": (18.0, 30.0),
        "pressure_sensitivity": "low",
        "spawn_months": [4, 5, 6],
        "peak_feed_months": [5, 6, 7, 8],
        "depth_pref_m": (0.5, 5.0),
        "habitat": "swamp_vegetation",
        "lat_range": (27.0, 44.0),
        "frontal_response": "neutral",
        "flow_preference": "stable_slow",
    },
    "longnose_gar": {
        "common_name": "Longnose Gar",
        "optimal_temp_c": 26.0,
        "temp_range_c": (18.0, 32.0),
        "pressure_sensitivity": "low",
        "spawn_months": [4, 5, 6],
        "peak_feed_months": [5, 6, 7, 8, 9],
        "depth_pref_m": (0.5, 6.0),
        "habitat": "backwater_shallow",
        "lat_range": (28.0, 44.0),
        "frontal_response": "neutral",
        "flow_preference": "stable_slow",
    },
    "alligator_gar": {
        "common_name": "Alligator Gar",
        "optimal_temp_c": 28.0,
        "temp_range_c": (20.0, 34.0),
        "pressure_sensitivity": "low",
        "spawn_months": [4, 5, 6],
        "peak_feed_months": [5, 6, 7, 8, 9],
        "depth_pref_m": (1.0, 8.0),
        "habitat": "backwater_river",
        "lat_range": (26.0, 36.0),
        "frontal_response": "neutral",
        "flow_preference": "stable_slow",
    },
    # ── CARP ────────────────────────────────────────────────────
    "common_carp": {
        "common_name": "Common Carp",
        "optimal_temp_c": 25.0,
        "temp_range_c": (15.0, 30.0),
        "pressure_sensitivity": "low",
        "spawn_months": [4, 5, 6],
        "peak_feed_months": [4, 5, 6, 7, 8, 9],
        "depth_pref_m": (1.0, 8.0),
        "habitat": "shallow_mud_vegetation",
        "lat_range": (25.0, 49.0),
        "frontal_response": "positive_slight",
        "flow_preference": "stable_slow",
    },
    "grass_carp": {
        "common_name": "Grass Carp",
        "optimal_temp_c": 24.0,
        "temp_range_c": (15.0, 30.0),
        "pressure_sensitivity": "low",
        "spawn_months": [4, 5, 6],
        "peak_feed_months": [5, 6, 7, 8],
        "depth_pref_m": (1.0, 6.0),
        "habitat": "vegetation_shallow",
        "lat_range": (28.0, 44.0),
        "frontal_response": "neutral",
        "flow_preference": "stable_slow",
    },
    # ── SUNFISH (additional) ────────────────────────────────────
    "green_sunfish": {
        "common_name": "Green Sunfish",
        "optimal_temp_c": 26.0,
        "temp_range_c": (16.0, 32.0),
        "pressure_sensitivity": "medium",
        "spawn_months": [5, 6, 7],
        "peak_feed_months": [5, 6, 7, 8, 9],
        "depth_pref_m": (0.5, 3.0),
        "habitat": "creek_pool",
        "lat_range": (28.0, 46.0),
        "frontal_response": "negative_slight",
        "flow_preference": "stable_slow",
    },
    "pumpkinseed": {
        "common_name": "Pumpkinseed",
        "optimal_temp_c": 23.0,
        "temp_range_c": (14.0, 28.0),
        "pressure_sensitivity": "medium",
        "spawn_months": [5, 6, 7],
        "peak_feed_months": [5, 6, 7, 8, 9],
        "depth_pref_m": (0.5, 4.0),
        "habitat": "vegetation_shallow",
        "lat_range": (30.0, 48.0),
        "frontal_response": "negative_slight",
        "flow_preference": "stable_slow",
    },
    "warmouth": {
        "common_name": "Warmouth",
        "optimal_temp_c": 26.0,
        "temp_range_c": (17.0, 32.0),
        "pressure_sensitivity": "low",
        "spawn_months": [4, 5, 6],
        "peak_feed_months": [4, 5, 6, 7, 8, 9],
        "depth_pref_m": (0.5, 3.0),
        "habitat": "vegetation_shallow",
        "lat_range": (27.0, 42.0),
        "frontal_response": "neutral",
        "flow_preference": "stable_slow",
    },
    "rock_bass": {
        "common_name": "Rock Bass",
        "optimal_temp_c": 20.0,
        "temp_range_c": (12.0, 26.0),
        "pressure_sensitivity": "medium",
        "spawn_months": [5, 6],
        "peak_feed_months": [5, 6, 7, 8, 9],
        "depth_pref_m": (1.0, 6.0),
        "habitat": "rocky_structure",
        "lat_range": (33.0, 48.0),
        "frontal_response": "negative_slight",
        "flow_preference": "moderate_current",
    },
    "longear_sunfish": {
        "common_name": "Longear Sunfish",
        "optimal_temp_c": 25.0,
        "temp_range_c": (16.0, 30.0),
        "pressure_sensitivity": "low",
        "spawn_months": [5, 6, 7],
        "peak_feed_months": [5, 6, 7, 8, 9],
        "depth_pref_m": (0.3, 3.0),
        "habitat": "creek_pool",
        "lat_range": (28.0, 42.0),
        "frontal_response": "neutral",
        "flow_preference": "moderate_current",
    },
    # ── TEMPERATE BASS (additional) ─────────────────────────────
    "yellow_bass": {
        "common_name": "Yellow Bass",
        "optimal_temp_c": 22.0,
        "temp_range_c": (14.0, 28.0),
        "pressure_sensitivity": "medium",
        "spawn_months": [4, 5],
        "peak_feed_months": [4, 5, 6, 9, 10],
        "depth_pref_m": (2.0, 10.0),
        "habitat": "open_water",
        "lat_range": (30.0, 44.0),
        "frontal_response": "positive_slight",
        "flow_preference": "moderate_current",
    },
    # ── PERCH FAMILY (additional) ───────────────────────────────
    "saugeye": {
        "common_name": "Saugeye",
        "optimal_temp_c": 17.0,
        "temp_range_c": (8.0, 24.0),
        "pressure_sensitivity": "high",
        "spawn_months": [3, 4, 5],
        "peak_feed_months": [4, 5, 6, 9, 10, 11],
        "depth_pref_m": (2.0, 12.0),
        "habitat": "rocky_structure",
        "lat_range": (35.0, 46.0),
        "frontal_response": "negative_moderate",
        "flow_preference": "moderate_current",
    },
    # ── CATFISH (additional) ────────────────────────────────────
    "white_catfish": {
        "common_name": "White Catfish",
        "optimal_temp_c": 25.0,
        "temp_range_c": (16.0, 30.0),
        "pressure_sensitivity": "low",
        "spawn_months": [5, 6, 7],
        "peak_feed_months": [5, 6, 7, 8, 9],
        "depth_pref_m": (1.0, 8.0),
        "habitat": "soft_bottom",
        "lat_range": (28.0, 42.0),
        "frontal_response": "positive_moderate",
        "flow_preference": "stable_slow",
    },
    "bullhead_catfish": {
        "common_name": "Brown Bullhead",
        "optimal_temp_c": 24.0,
        "temp_range_c": (14.0, 30.0),
        "pressure_sensitivity": "low",
        "spawn_months": [5, 6, 7],
        "peak_feed_months": [5, 6, 7, 8, 9],
        "depth_pref_m": (0.5, 5.0),
        "habitat": "soft_bottom",
        "lat_range": (28.0, 48.0),
        "frontal_response": "positive_slight",
        "flow_preference": "stable_slow",
    },
    # ── PIKE FAMILY (additional) ────────────────────────────────
    "chain_pickerel": {
        "common_name": "Chain Pickerel",
        "optimal_temp_c": 20.0,
        "temp_range_c": (10.0, 27.0),
        "pressure_sensitivity": "medium",
        "spawn_months": [2, 3, 4],
        "peak_feed_months": [4, 5, 6, 9, 10, 11],
        "depth_pref_m": (0.5, 4.0),
        "habitat": "vegetation_shallow",
        "lat_range": (30.0, 46.0),
        "frontal_response": "negative_slight",
        "flow_preference": "stable_slow",
    },
    "tiger_muskie": {
        "common_name": "Tiger Muskie",
        "optimal_temp_c": 19.0,
        "temp_range_c": (10.0, 26.0),
        "pressure_sensitivity": "high",
        "spawn_months": [4, 5],
        "peak_feed_months": [5, 6, 9, 10, 11],
        "depth_pref_m": (2.0, 10.0),
        "habitat": "weedline_structure",
        "lat_range": (38.0, 48.0),
        "frontal_response": "negative_moderate",
        "flow_preference": "stable_slow",
    },
    # ── TROUT/SALMON (additional) ───────────────────────────────
    "cutthroat_trout": {
        "common_name": "Cutthroat Trout",
        "optimal_temp_c": 12.0,
        "temp_range_c": (5.0, 18.0),
        "pressure_sensitivity": "medium",
        "spawn_months": [2, 3, 4, 5],
        "peak_feed_months": [5, 6, 7, 8, 9, 10],
        "depth_pref_m": (0.5, 5.0),
        "habitat": "cold_stream",
        "lat_range": (36.0, 49.0),
        "frontal_response": "positive_slight",
        "flow_preference": "high_current",
    },
    "golden_trout": {
        "common_name": "Golden Trout",
        "optimal_temp_c": 11.0,
        "temp_range_c": (4.0, 17.0),
        "pressure_sensitivity": "medium",
        "spawn_months": [6, 7, 8],
        "peak_feed_months": [6, 7, 8, 9],
        "depth_pref_m": (0.3, 3.0),
        "habitat": "alpine_stream",
        "lat_range": (35.0, 48.0),
        "frontal_response": "positive_slight",
        "flow_preference": "high_current",
    },
    "atlantic_salmon": {
        "common_name": "Atlantic Salmon",
        "optimal_temp_c": 14.0,
        "temp_range_c": (6.0, 20.0),
        "pressure_sensitivity": "medium",
        "spawn_months": [10, 11],
        "peak_feed_months": [4, 5, 6, 7, 8, 9],
        "depth_pref_m": (1.0, 8.0),
        "habitat": "cold_river",
        "lat_range": (42.0, 49.0),
        "frontal_response": "positive_moderate",
        "flow_preference": "high_current",
    },
    "pink_salmon": {
        "common_name": "Pink Salmon",
        "optimal_temp_c": 11.0,
        "temp_range_c": (5.0, 16.0),
        "pressure_sensitivity": "low",
        "spawn_months": [8, 9, 10],
        "peak_feed_months": [5, 6, 7, 8],
        "depth_pref_m": (1.0, 10.0),
        "habitat": "cold_river",
        "lat_range": (44.0, 49.0),
        "frontal_response": "neutral",
        "flow_preference": "high_current",
    },
    "sockeye_salmon": {
        "common_name": "Sockeye Salmon",
        "optimal_temp_c": 12.0,
        "temp_range_c": (5.0, 18.0),
        "pressure_sensitivity": "low",
        "spawn_months": [7, 8, 9, 10],
        "peak_feed_months": [5, 6, 7, 8],
        "depth_pref_m": (2.0, 15.0),
        "habitat": "deep_cold_lake",
        "lat_range": (44.0, 49.0),
        "frontal_response": "neutral",
        "flow_preference": "moderate_current",
    },
    # ── DRUM / SHEEPSHEAD ───────────────────────────────────────
    "freshwater_drum": {
        "common_name": "Freshwater Drum",
        "optimal_temp_c": 24.0,
        "temp_range_c": (14.0, 30.0),
        "pressure_sensitivity": "low",
        "spawn_months": [5, 6, 7],
        "peak_feed_months": [5, 6, 7, 8, 9],
        "depth_pref_m": (2.0, 12.0),
        "habitat": "open_water",
        "lat_range": (28.0, 48.0),
        "frontal_response": "neutral",
        "flow_preference": "moderate_current",
    },
    # ── SUCKERS ─────────────────────────────────────────────────
    "white_sucker": {
        "common_name": "White Sucker",
        "optimal_temp_c": 18.0,
        "temp_range_c": (8.0, 24.0),
        "pressure_sensitivity": "low",
        "spawn_months": [3, 4, 5],
        "peak_feed_months": [4, 5, 6, 7, 8, 9],
        "depth_pref_m": (1.0, 8.0),
        "habitat": "river_pool",
        "lat_range": (32.0, 49.0),
        "frontal_response": "neutral",
        "flow_preference": "moderate_current",
    },
    "bigmouth_buffalo": {
        "common_name": "Bigmouth Buffalo",
        "optimal_temp_c": 24.0,
        "temp_range_c": (14.0, 30.0),
        "pressure_sensitivity": "low",
        "spawn_months": [4, 5, 6],
        "peak_feed_months": [5, 6, 7, 8],
        "depth_pref_m": (1.0, 8.0),
        "habitat": "open_water",
        "lat_range": (30.0, 48.0),
        "frontal_response": "neutral",
        "flow_preference": "stable_slow",
    },
    # ── STURGEON ────────────────────────────────────────────────
    "lake_sturgeon": {
        "common_name": "Lake Sturgeon",
        "optimal_temp_c": 16.0,
        "temp_range_c": (6.0, 22.0),
        "pressure_sensitivity": "low",
        "spawn_months": [4, 5, 6],
        "peak_feed_months": [5, 6, 7, 8, 9, 10],
        "depth_pref_m": (3.0, 20.0),
        "habitat": "deep_lake_river",
        "lat_range": (38.0, 49.0),
        "frontal_response": "neutral",
        "flow_preference": "moderate_current",
    },
    "shovelnose_sturgeon": {
        "common_name": "Shovelnose Sturgeon",
        "optimal_temp_c": 20.0,
        "temp_range_c": (8.0, 26.0),
        "pressure_sensitivity": "low",
        "spawn_months": [4, 5, 6],
        "peak_feed_months": [5, 6, 7, 8, 9],
        "depth_pref_m": (1.0, 10.0),
        "habitat": "large_river",
        "lat_range": (30.0, 48.0),
        "frontal_response": "neutral",
        "flow_preference": "high_current",
    },
    # ── SHAD ────────────────────────────────────────────────────
    "american_shad": {
        "common_name": "American Shad",
        "optimal_temp_c": 16.0,
        "temp_range_c": (10.0, 22.0),
        "pressure_sensitivity": "low",
        "spawn_months": [3, 4, 5, 6],
        "peak_feed_months": [3, 4, 5, 6],
        "depth_pref_m": (1.0, 8.0),
        "habitat": "large_river",
        "lat_range": (30.0, 46.0),
        "frontal_response": "neutral",
        "flow_preference": "high_current",
    },
    # ── BURBOT ──────────────────────────────────────────────────
    "burbot": {
        "common_name": "Burbot",
        "optimal_temp_c": 8.0,
        "temp_range_c": (2.0, 15.0),
        "pressure_sensitivity": "medium",
        "spawn_months": [1, 2, 3],
        "peak_feed_months": [10, 11, 12, 1, 2, 3],
        "depth_pref_m": (5.0, 30.0),
        "habitat": "deep_cold_lake",
        "lat_range": (42.0, 49.0),
        "frontal_response": "positive_slight",
        "flow_preference": "stable_slow",
    },
    # ── CISCO / WHITEFISH ───────────────────────────────────────
    "lake_whitefish": {
        "common_name": "Lake Whitefish",
        "optimal_temp_c": 12.0,
        "temp_range_c": (4.0, 18.0),
        "pressure_sensitivity": "medium",
        "spawn_months": [10, 11, 12],
        "peak_feed_months": [5, 6, 7, 8, 9, 10],
        "depth_pref_m": (5.0, 30.0),
        "habitat": "deep_cold_lake",
        "lat_range": (42.0, 49.0),
        "frontal_response": "neutral",
        "flow_preference": "stable_slow",
    },
    # ── TEMPERATE / COASTAL (freshwater accessible) ─────────────
    "red_drum": {
        "common_name": "Red Drum",
        "optimal_temp_c": 24.0,
        "temp_range_c": (15.0, 32.0),
        "pressure_sensitivity": "medium",
        "spawn_months": [8, 9, 10, 11],
        "peak_feed_months": [3, 4, 5, 9, 10, 11],
        "depth_pref_m": (0.5, 6.0),
        "habitat": "coastal_estuary",
        "lat_range": (26.0, 38.0),
        "frontal_response": "positive_moderate",
        "flow_preference": "moderate_current",
    },
    "spotted_seatrout": {
        "common_name": "Spotted Seatrout",
        "optimal_temp_c": 24.0,
        "temp_range_c": (14.0, 32.0),
        "pressure_sensitivity": "medium",
        "spawn_months": [4, 5, 6, 7, 8, 9],
        "peak_feed_months": [3, 4, 5, 9, 10, 11],
        "depth_pref_m": (0.5, 5.0),
        "habitat": "coastal_estuary",
        "lat_range": (26.0, 38.0),
        "frontal_response": "positive_slight",
        "flow_preference": "moderate_current",
    },
    "snook": {
        "common_name": "Snook",
        "optimal_temp_c": 27.0,
        "temp_range_c": (20.0, 33.0),
        "pressure_sensitivity": "medium",
        "spawn_months": [5, 6, 7, 8, 9],
        "peak_feed_months": [3, 4, 5, 9, 10, 11],
        "depth_pref_m": (0.3, 6.0),
        "habitat": "coastal_estuary",
        "lat_range": (25.0, 30.0),
        "frontal_response": "negative_moderate",
        "flow_preference": "moderate_current",
    },
    "tarpon": {
        "common_name": "Tarpon",
        "optimal_temp_c": 28.0,
        "temp_range_c": (22.0, 34.0),
        "pressure_sensitivity": "high",
        "spawn_months": [5, 6, 7],
        "peak_feed_months": [4, 5, 6, 7, 8, 9, 10],
        "depth_pref_m": (0.5, 15.0),
        "habitat": "coastal_estuary",
        "lat_range": (25.0, 32.0),
        "frontal_response": "negative_strong",
        "flow_preference": "moderate_current",
    },
    "peacock_bass": {
        "common_name": "Peacock Bass",
        "optimal_temp_c": 28.0,
        "temp_range_c": (22.0, 34.0),
        "pressure_sensitivity": "medium",
        "spawn_months": [3, 4, 5, 6, 7, 8, 9, 10],
        "peak_feed_months": [3, 4, 5, 6, 7, 8, 9, 10],
        "depth_pref_m": (0.5, 6.0),
        "habitat": "tropical_canal",
        "lat_range": (25.0, 28.0),
        "frontal_response": "negative_slight",
        "flow_preference": "stable_slow",
    },
    # ── MISCELLANEOUS BASS ──────────────────────────────────────
    "shoal_bass": {
        "common_name": "Shoal Bass",
        "optimal_temp_c": 22.0,
        "temp_range_c": (14.0, 28.0),
        "pressure_sensitivity": "high",
        "spawn_months": [4, 5],
        "peak_feed_months": [4, 5, 6, 9, 10],
        "depth_pref_m": (0.5, 4.0),
        "habitat": "rocky_shoal",
        "lat_range": (30.0, 35.0),
        "frontal_response": "negative_moderate",
        "flow_preference": "high_current",
    },
    "guadalupe_bass": {
        "common_name": "Guadalupe Bass",
        "optimal_temp_c": 22.0,
        "temp_range_c": (14.0, 28.0),
        "pressure_sensitivity": "high",
        "spawn_months": [3, 4, 5],
        "peak_feed_months": [4, 5, 6, 9, 10],
        "depth_pref_m": (0.3, 3.0),
        "habitat": "rocky_stream",
        "lat_range": (29.0, 33.0),
        "frontal_response": "negative_moderate",
        "flow_preference": "high_current",
    },
    # ── GAR (additional) ────────────────────────────────────────
    "florida_gar": {
        "common_name": "Florida Gar",
        "optimal_temp_c": 27.0,
        "temp_range_c": (18.0, 34.0),
        "pressure_sensitivity": "low",
        "spawn_months": [2, 3, 4, 5],
        "peak_feed_months": [3, 4, 5, 6, 7, 8, 9, 10],
        "depth_pref_m": (0.3, 4.0),
        "habitat": "vegetation_shallow",
        "lat_range": (25.0, 32.0),
        "frontal_response": "neutral",
        "flow_preference": "stable_slow",
    },
    "spotted_gar": {
        "common_name": "Spotted Gar",
        "optimal_temp_c": 26.0,
        "temp_range_c": (16.0, 32.0),
        "pressure_sensitivity": "low",
        "spawn_months": [3, 4, 5],
        "peak_feed_months": [4, 5, 6, 7, 8, 9],
        "depth_pref_m": (0.5, 5.0),
        "habitat": "vegetation_shallow",
        "lat_range": (28.0, 40.0),
        "frontal_response": "neutral",
        "flow_preference": "stable_slow",
    },
}

# Frontal response numeric mapping for model features
_FRONTAL_RESPONSE_SCORES = {
    "positive_strong": 0.15,
    "positive_moderate": 0.08,
    "positive_slight": 0.03,
    "neutral": 0.0,
    "negative_slight": -0.03,
    "negative_moderate": -0.08,
    "negative_strong": -0.15,
}

# Pressure sensitivity numeric mapping
_PRESSURE_SENSITIVITY_SCORES = {
    "high": 1.0,
    "medium": 0.5,
    "low": 0.2,
}

# Flow preference numeric mapping
_FLOW_PREFERENCE_SCORES = {
    "stable_slow": 0.0,
    "moderate_current": 0.4,
    "current_oriented": 0.7,
    "high_current": 0.85,
    "rising_current": 0.9,
}


def get_species_thermal_optimum(species_key: str) -> float:
    """Return optimal water temperature for a species."""
    sp = SPECIES_CATALOG.get(species_key)
    return sp["optimal_temp_c"] if sp else float("nan")


def get_species_thermal_comfort(species_key: str, water_temp_c: float) -> float:
    """Compute thermal comfort score (0-1) for a species at a given water temp.

    Returns 1.0 at optimal temperature, decreasing linearly toward 0 at
    the edges of the species' active temperature range.
    """
    sp = SPECIES_CATALOG.get(species_key)
    if sp is None or np.isnan(water_temp_c):
        return float("nan")

    opt = sp["optimal_temp_c"]
    lo, hi = sp["temp_range_c"]

    if water_temp_c < lo or water_temp_c > hi:
        return 0.0
    if water_temp_c <= opt:
        return (water_temp_c - lo) / (opt - lo) if opt > lo else 1.0
    else:
        return (hi - water_temp_c) / (hi - opt) if hi > opt else 1.0


def compute_species_probabilities(
    lat: float,
    lon: float,
    depth_m: float = float("nan"),
    lake_area_ha: float = float("nan"),
) -> dict[str, float]:
    """Estimate species composition probabilities for a location.

    Uses latitude, depth, and lake area to compute rough probability
    estimates for which species are likely present. Returns a dict of
    species_key -> probability (0-1).

    This is a heuristic model — ideally replaced with actual stocking/survey
    data when available.
    """
    import math

    probs = {}

    for key, sp in SPECIES_CATALOG.items():
        lat_lo, lat_hi = sp["lat_range"]

        # Base probability from latitude range (0 outside, peaks in center)
        if lat < lat_lo or lat > lat_hi:
            probs[key] = 0.0
            continue

        # Latitude suitability: bell curve centered in range
        lat_center = (lat_lo + lat_hi) / 2.0
        lat_width = (lat_hi - lat_lo) / 2.0
        lat_score = math.exp(-0.5 * ((lat - lat_center) / max(lat_width * 0.6, 1.0)) ** 2)

        # Depth suitability
        depth_score = 1.0
        if not np.isnan(depth_m):
            d_lo, d_hi = sp["depth_pref_m"]
            d_center = (d_lo + d_hi) / 2.0
            d_width = (d_hi - d_lo) / 2.0
            if d_width > 0:
                depth_score = math.exp(-0.5 * ((depth_m - d_center) / max(d_width, 1.0)) ** 2)

        # Lake area suitability (some species need large water)
        area_score = 1.0
        if not np.isnan(lake_area_ha):
            habitat = sp["habitat"]
            if habitat in ("deep_cold_lake", "deep_cold_lake_river", "large_river_reservoir"):
                # Needs large water
                area_score = min(1.0, lake_area_ha / 500.0)
            elif habitat in ("cold_headwater", "cold_stream_tailwater"):
                # Prefers smaller water
                area_score = min(1.0, 200.0 / max(lake_area_ha, 1.0))

        probs[key] = round(lat_score * depth_score * area_score, 4)

    # Apply tournament prevalence weights — bass tournaments dominate
    # the US tournament scene, followed by walleye, crappie, catfish
    _TOURNAMENT_WEIGHTS = {
        "largemouth_bass": 5.0, "smallmouth_bass": 3.0, "spotted_bass": 2.5,
        "walleye": 2.5, "sauger": 1.5,
        "black_crappie": 2.0, "white_crappie": 2.0,
        "striped_bass": 1.5, "hybrid_striped_bass": 1.2, "white_bass": 1.0,
        "channel_catfish": 1.5, "blue_catfish": 1.5, "flathead_catfish": 1.2,
        "northern_pike": 1.2, "muskellunge": 1.0,
        "rainbow_trout": 1.0, "brown_trout": 1.0, "steelhead": 0.8,
        "chinook_salmon": 0.8, "coho_salmon": 0.8,
    }

    for k in probs:
        probs[k] *= _TOURNAMENT_WEIGHTS.get(k, 0.3)

    # Normalize so top species probabilities are reasonable
    total = sum(probs.values())
    if total > 0:
        for k in probs:
            probs[k] = round(probs[k] / total, 4)

    return probs


def compute_multi_species_features(
    lat: float,
    lon: float,
    water_temp_c: float = float("nan"),
    month: int = 0,
    depth_m: float = float("nan"),
    lake_area_ha: float = float("nan"),
) -> dict[str, float]:
    """Compute comprehensive multi-species features for model input.

    Returns a dict with:
    - weighted_optimal_temp: probability-weighted optimal temperature
    - weighted_thermal_comfort: probability-weighted thermal comfort
    - pressure_sensitivity_score: weighted pressure sensitivity
    - frontal_response_score: weighted frontal passage response
    - flow_preference_score: weighted flow preference
    - spawn_activity: fraction of likely species currently spawning
    - peak_feed_activity: fraction of likely species in peak feeding
    - dominant_species_group: categorical (bass/panfish/catfish/predator/trout/other)
    - top_species_prob: probability of most likely species
    """
    probs = compute_species_probabilities(lat, lon, depth_m, lake_area_ha)

    result = {
        "weighted_optimal_temp": float("nan"),
        "weighted_thermal_comfort": float("nan"),
        "pressure_sensitivity_score": 0.0,
        "frontal_response_score": 0.0,
        "flow_preference_score": 0.0,
        "spawn_activity": 0.0,
        "peak_feed_activity": 0.0,
        "dominant_species_group": "unknown",
        "top_species_prob": 0.0,
    }

    if not probs or sum(probs.values()) == 0:
        return result

    # Weighted features
    w_opt_temp = 0.0
    w_comfort = 0.0
    w_pressure = 0.0
    w_frontal = 0.0
    w_flow = 0.0
    w_spawn = 0.0
    w_feed = 0.0
    w_total = 0.0

    group_weights = {}  # species_group -> total weight

    for key, prob in probs.items():
        if prob < 0.001:
            continue

        sp = SPECIES_CATALOG[key]
        w_total += prob

        w_opt_temp += prob * sp["optimal_temp_c"]

        if not np.isnan(water_temp_c):
            comfort = get_species_thermal_comfort(key, water_temp_c)
            w_comfort += prob * comfort

        w_pressure += prob * _PRESSURE_SENSITIVITY_SCORES.get(
            sp["pressure_sensitivity"], 0.5
        )
        w_frontal += prob * _FRONTAL_RESPONSE_SCORES.get(
            sp["frontal_response"], 0.0
        )
        w_flow += prob * _FLOW_PREFERENCE_SCORES.get(
            sp["flow_preference"], 0.0
        )

        if month > 0 and month in sp["spawn_months"]:
            w_spawn += prob
        if month > 0 and month in sp["peak_feed_months"]:
            w_feed += prob

        # Categorize species group
        group = _species_group(key)
        group_weights[group] = group_weights.get(group, 0.0) + prob

    if w_total > 0:
        result["weighted_optimal_temp"] = round(w_opt_temp / w_total, 2)
        if not np.isnan(water_temp_c):
            result["weighted_thermal_comfort"] = round(w_comfort / w_total, 4)
        result["pressure_sensitivity_score"] = round(w_pressure / w_total, 4)
        result["frontal_response_score"] = round(w_frontal / w_total, 4)
        result["flow_preference_score"] = round(w_flow / w_total, 4)
        result["spawn_activity"] = round(w_spawn / w_total, 4)
        result["peak_feed_activity"] = round(w_feed / w_total, 4)

    if group_weights:
        result["dominant_species_group"] = max(group_weights, key=group_weights.get)

    if probs:
        top_species = max(probs, key=probs.get)
        result["top_species_prob"] = probs[top_species]

    return result


def _species_group(species_key: str) -> str:
    """Classify a species into a broad group for categorical features."""
    bass_keys = {"largemouth_bass", "smallmouth_bass", "spotted_bass",
                 "striped_bass", "hybrid_striped_bass", "white_bass",
                 "yellow_bass", "shoal_bass", "guadalupe_bass", "peacock_bass"}
    panfish_keys = {"black_crappie", "white_crappie", "bluegill",
                    "redear_sunfish", "yellow_perch", "green_sunfish",
                    "pumpkinseed", "warmouth", "rock_bass", "longear_sunfish"}
    catfish_keys = {"channel_catfish", "blue_catfish", "flathead_catfish",
                    "white_catfish", "bullhead_catfish"}
    predator_keys = {"northern_pike", "muskellunge", "walleye", "sauger",
                     "saugeye", "chain_pickerel", "tiger_muskie"}
    trout_keys = {"rainbow_trout", "brown_trout", "brook_trout",
                  "lake_trout", "steelhead", "chinook_salmon", "coho_salmon",
                  "cutthroat_trout", "golden_trout", "atlantic_salmon",
                  "pink_salmon", "sockeye_salmon"}
    coastal_keys = {"red_drum", "spotted_seatrout", "snook", "tarpon"}

    if species_key in bass_keys:
        return "bass"
    if species_key in panfish_keys:
        return "panfish"
    if species_key in catfish_keys:
        return "catfish"
    if species_key in predator_keys:
        return "predator"
    if species_key in trout_keys:
        return "trout"
    if species_key in coastal_keys:
        return "coastal"
    return "other"


# Default data paths relative to project root
_DATA_DIR = Path(__file__).resolve().parent.parent / "validation" / "data" / "raw"
_CREELCAT_PATH = _DATA_DIR / "creelcat_spatial_features.csv"
_LAGOS_PATH = _DATA_DIR / "lagos_matches.csv"
_GEOCLIP_PATH = _DATA_DIR / "location_embeddings_geoclip_pca32.csv"

# Radius thresholds in km for match quality tiers
_EXACT_KM = 1.0
_NEARBY_KM = 25.0
_REGIONAL_KM = 100.0

# Earth radius in km (for BallTree haversine)
_EARTH_RADIUS_KM = 6371.0


@dataclass
class SitePrior:
    """Site prior estimate for a single location.

    Attributes:
        creel_cpue_hour_max: Maximum CPUE (fish/angler-hour) from nearby creel surveys. NaN if no match.
        creel_cpue_hour_median: Median CPUE (fish/angler-hour) from nearby creel surveys. NaN if no match.
        creel_nearby_waterbodies: Number of waterbodies with creel data within the search radius.
        creel_nearest_km: Distance in km to the nearest creel survey site.
        creel_match_quality: Quality tier of the spatial match ("exact", "nearby", "regional", "none").
        lagos_max_depth_m: Maximum depth in meters from LAGOS morphometry. NaN if unavailable.
        lagos_area_ha: Lake area in hectares from LAGOS. NaN if unavailable.
        lagos_sdi: Shoreline Development Index from LAGOS. NaN if unavailable.
        embedding: 32-dimensional GeoCLIP location embedding. Zeros if unavailable.
        confidence: Overall confidence score (0-1) based on match quality and data availability.
    """

    creel_cpue_hour_max: float = float("nan")
    creel_cpue_hour_median: float = float("nan")
    creel_nearby_waterbodies: int = 0
    creel_nearest_km: float = float("nan")
    creel_match_quality: str = "none"
    lagos_max_depth_m: float = float("nan")
    lagos_area_ha: float = float("nan")
    lagos_sdi: float = float("nan")
    embedding: np.ndarray = field(default_factory=lambda: np.zeros(32, dtype=np.float32))
    confidence: float = 0.0
    # Species proxy features (v6 — legacy, kept for backward compat)
    smb_probability: float = float("nan")
    lmb_probability: float = float("nan")
    species_thermal_optimum: float = float("nan")
    depth_smb_signal: float = float("nan")
    # Multi-species features (v7)
    weighted_optimal_temp: float = float("nan")
    weighted_thermal_comfort: float = float("nan")
    pressure_sensitivity_score: float = float("nan")
    frontal_response_score: float = float("nan")
    flow_preference_score: float = float("nan")
    spawn_activity: float = float("nan")
    peak_feed_activity: float = float("nan")
    dominant_species_group: str = "unknown"
    top_species_prob: float = float("nan")

    def to_dict(self) -> dict:
        """Serialize to dictionary (embedding as list for JSON compat)."""
        d = {
            "creel_cpue_hour_max": self.creel_cpue_hour_max,
            "creel_cpue_hour_median": self.creel_cpue_hour_median,
            "creel_nearby_waterbodies": self.creel_nearby_waterbodies,
            "creel_nearest_km": self.creel_nearest_km,
            "creel_match_quality": self.creel_match_quality,
            "lagos_max_depth_m": self.lagos_max_depth_m,
            "lagos_area_ha": self.lagos_area_ha,
            "lagos_sdi": self.lagos_sdi,
            # Legacy species proxy
            "smb_probability": self.smb_probability,
            "lmb_probability": self.lmb_probability,
            "species_thermal_optimum": self.species_thermal_optimum,
            "depth_smb_signal": self.depth_smb_signal,
            # Multi-species (v7)
            "weighted_optimal_temp": self.weighted_optimal_temp,
            "weighted_thermal_comfort": self.weighted_thermal_comfort,
            "pressure_sensitivity_score": self.pressure_sensitivity_score,
            "frontal_response_score": self.frontal_response_score,
            "flow_preference_score": self.flow_preference_score,
            "spawn_activity": self.spawn_activity,
            "peak_feed_activity": self.peak_feed_activity,
            "dominant_species_group": self.dominant_species_group,
            "top_species_prob": self.top_species_prob,
            "embedding": self.embedding.tolist(),
            "confidence": self.confidence,
        }
        return d


class SitePriorService:
    """Fast spatial lookup service for CreelCat site priors.

    Loads CreelCat spatial features, LAGOS morphometry, and GeoCLIP embeddings
    at initialization and builds BallTree indices for O(log n) spatial queries.

    Example::

        svc = SitePriorService()
        prior = svc.lookup(lat=34.05, lon=-84.23)
        print(prior.creel_cpue_hour_median, prior.creel_match_quality)
    """

    def __init__(
        self,
        creelcat_path: Optional[str | Path] = None,
        lagos_path: Optional[str | Path] = None,
        geoclip_path: Optional[str | Path] = None,
    ) -> None:
        self._creelcat_path = Path(creelcat_path) if creelcat_path else _CREELCAT_PATH
        self._lagos_path = Path(lagos_path) if lagos_path else _LAGOS_PATH
        self._geoclip_path = Path(geoclip_path) if geoclip_path else _GEOCLIP_PATH

        self._creel_df: Optional[pd.DataFrame] = None
        self._creel_tree: Optional[BallTree] = None
        self._lagos_df: Optional[pd.DataFrame] = None
        self._lagos_tree: Optional[BallTree] = None
        self._geoclip_df: Optional[pd.DataFrame] = None
        self._geoclip_tree: Optional[BallTree] = None
        self._geoclip_cols: list[str] = []

        self._load_creelcat()
        self._load_lagos()
        self._load_geoclip()

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_creelcat(self) -> None:
        """Load CreelCat spatial features and build BallTree."""
        if not self._creelcat_path.exists():
            logger.warning("CreelCat features not found at %s", self._creelcat_path)
            return
        df = pd.read_csv(self._creelcat_path)
        # Drop rows without coordinates
        df = df.dropna(subset=["lat", "lon"]).reset_index(drop=True)
        self._creel_df = df
        coords_rad = np.deg2rad(df[["lat", "lon"]].values)
        self._creel_tree = BallTree(coords_rad, metric="haversine")
        logger.info("Loaded %d CreelCat sites", len(df))

    def _load_lagos(self) -> None:
        """Load LAGOS morphometry matches and build BallTree.

        The LAGOS CSV uses location names rather than coordinates, so we
        join it against the CreelCat coordinate table. Rows that cannot be
        geo-located are kept with NaN coordinates (and excluded from the
        BallTree).
        """
        if not self._lagos_path.exists():
            logger.warning("LAGOS matches not found at %s", self._lagos_path)
            return
        df = pd.read_csv(self._lagos_path)

        # Convert acres -> hectares, feet -> meters where columns exist
        if "lagos_area_acres" in df.columns:
            df["lagos_area_ha"] = df["lagos_area_acres"] * 0.404686
        if "lagos_depth_ft" in df.columns:
            df["lagos_max_depth_m"] = df["lagos_depth_ft"] * 0.3048

        # If LAGOS has its own lat/lon, use them; otherwise attempt merge
        if "lat" not in df.columns and self._creel_df is not None:
            # Merge on location name if available
            if "location" in df.columns and "location" in self._creel_df.columns:
                creel_coords = self._creel_df[["location", "lat", "lon"]].drop_duplicates("location")
                df = df.merge(creel_coords, on="location", how="left")

        if "lat" in df.columns and "lon" in df.columns:
            valid = df.dropna(subset=["lat", "lon"])
            if len(valid) > 0:
                self._lagos_df = valid.reset_index(drop=True)
                coords_rad = np.deg2rad(valid[["lat", "lon"]].values)
                self._lagos_tree = BallTree(coords_rad, metric="haversine")
                logger.info("Loaded %d LAGOS morphometry records with coords", len(valid))
                return

        # Fallback: keep dataframe for location-name lookups but no spatial tree
        self._lagos_df = df
        logger.info("Loaded %d LAGOS records (no spatial index)", len(df))

    def _load_geoclip(self) -> None:
        """Load GeoCLIP PCA-32 embeddings and build BallTree."""
        if not self._geoclip_path.exists():
            logger.warning("GeoCLIP embeddings not found at %s", self._geoclip_path)
            return
        df = pd.read_csv(self._geoclip_path)
        df = df.dropna(subset=["lat", "lon"]).reset_index(drop=True)
        self._geoclip_cols = [c for c in df.columns if c.startswith("geoclip_")]
        self._geoclip_df = df
        coords_rad = np.deg2rad(df[["lat", "lon"]].values)
        self._geoclip_tree = BallTree(coords_rad, metric="haversine")
        logger.info("Loaded %d GeoCLIP embeddings (%d dims)", len(df), len(self._geoclip_cols))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def lookup(
        self,
        lat: float,
        lon: float,
        water_temp_c: float = float("nan"),
        month: int = 0,
    ) -> SitePrior:
        """Return site prior for a single location.

        Args:
            lat: Latitude in decimal degrees.
            lon: Longitude in decimal degrees.
            water_temp_c: Current water temperature in °C (optional, for thermal comfort).
            month: Current month 1-12 (optional, for spawn/feed activity).

        Returns:
            SitePrior with all available fields populated.
        """
        prior = SitePrior()
        self._fill_creel(prior, lat, lon)
        self._fill_lagos(prior, lat, lon)
        self._fill_embedding(prior, lat, lon)
        self._fill_species_proxy(prior, lat, lon, water_temp_c, month)
        prior.confidence = self._compute_confidence(prior)
        return prior

    def lookup_batch(self, lats: list[float], lons: list[float]) -> list[SitePrior]:
        """Return site priors for a batch of locations.

        Args:
            lats: List of latitudes in decimal degrees.
            lons: List of longitudes in decimal degrees.

        Returns:
            List of SitePrior objects, one per input location.
        """
        if len(lats) != len(lons):
            raise ValueError(f"lats ({len(lats)}) and lons ({len(lons)}) must have same length")
        return [self.lookup(lat, lon) for lat, lon in zip(lats, lons)]

    # ------------------------------------------------------------------
    # Internal fill methods
    # ------------------------------------------------------------------

    def _fill_creel(self, prior: SitePrior, lat: float, lon: float) -> None:
        """Fill CreelCat fields on the prior via spatial lookup."""
        if self._creel_tree is None or self._creel_df is None:
            return

        query_rad = np.deg2rad([[lat, lon]])
        dist_rad, idx = self._creel_tree.query(query_rad, k=1)
        dist_km = dist_rad[0, 0] * _EARTH_RADIUS_KM
        nearest_idx = idx[0, 0]

        prior.creel_nearest_km = round(dist_km, 3)

        # Determine match quality
        if dist_km <= _EXACT_KM:
            prior.creel_match_quality = "exact"
        elif dist_km <= _NEARBY_KM:
            prior.creel_match_quality = "nearby"
        elif dist_km <= _REGIONAL_KM:
            prior.creel_match_quality = "regional"
        else:
            prior.creel_match_quality = "none"
            return

        row = self._creel_df.iloc[nearest_idx]

        cpue_max = row.get("creel2_cpue_hour_max")
        if cpue_max is not None and not pd.isna(cpue_max):
            prior.creel_cpue_hour_max = float(cpue_max)

        cpue_med = row.get("creel2_cpue_hour_median")
        if cpue_med is not None and not pd.isna(cpue_med):
            prior.creel_cpue_hour_median = float(cpue_med)

        nearby = row.get("creel2_nearby_waterbodies")
        if nearby is not None and not pd.isna(nearby):
            prior.creel_nearby_waterbodies = int(nearby)

    def _fill_lagos(self, prior: SitePrior, lat: float, lon: float) -> None:
        """Fill LAGOS morphometry fields via spatial lookup."""
        if self._lagos_tree is None or self._lagos_df is None:
            return

        query_rad = np.deg2rad([[lat, lon]])
        dist_rad, idx = self._lagos_tree.query(query_rad, k=1)
        dist_km = dist_rad[0, 0] * _EARTH_RADIUS_KM

        # Only use LAGOS data if reasonably close
        if dist_km > _NEARBY_KM:
            return

        row = self._lagos_df.iloc[idx[0, 0]]

        depth_col = "lagos_max_depth_m" if "lagos_max_depth_m" in row.index else "lagos_depth_ft"
        depth_val = row.get(depth_col)
        if depth_val is not None and not pd.isna(depth_val):
            if depth_col == "lagos_depth_ft":
                prior.lagos_max_depth_m = float(depth_val) * 0.3048
            else:
                prior.lagos_max_depth_m = float(depth_val)

        area_col = "lagos_area_ha" if "lagos_area_ha" in row.index else "lagos_area_acres"
        area_val = row.get(area_col)
        if area_val is not None and not pd.isna(area_val):
            if area_col == "lagos_area_acres":
                prior.lagos_area_ha = float(area_val) * 0.404686
            else:
                prior.lagos_area_ha = float(area_val)

        sdi_val = row.get("lagos_sdi")
        if sdi_val is not None and not pd.isna(sdi_val):
            prior.lagos_sdi = float(sdi_val)

    def _fill_embedding(self, prior: SitePrior, lat: float, lon: float) -> None:
        """Fill GeoCLIP embedding via nearest-neighbor lookup."""
        if self._geoclip_tree is None or self._geoclip_df is None:
            return

        query_rad = np.deg2rad([[lat, lon]])
        dist_rad, idx = self._geoclip_tree.query(query_rad, k=1)
        dist_km = dist_rad[0, 0] * _EARTH_RADIUS_KM

        # Use embedding from nearest point (embeddings vary slowly over space)
        if dist_km > _REGIONAL_KM:
            return

        row = self._geoclip_df.iloc[idx[0, 0]]
        prior.embedding = row[self._geoclip_cols].values.astype(np.float32)

    @staticmethod
    def _fill_species_proxy(
        prior: SitePrior,
        lat: float,
        lon: float = float("nan"),
        water_temp_c: float = float("nan"),
        month: int = 0,
    ) -> None:
        """Compute comprehensive multi-species features.

        v6 legacy: latitude-based LMB/SMB split (kept for backward compat).
        v7 new: full 30-species probability model with weighted features.
        """
        import math

        # ── v6 legacy: LMB/SMB split ──
        smb_prob = 1.0 / (1.0 + math.exp(-0.5 * (lat - 42.0)))
        lmb_prob = 1.0 - smb_prob
        prior.smb_probability = round(smb_prob, 4)
        prior.lmb_probability = round(lmb_prob, 4)

        LMB_OPTIMUM = 23.5
        SMB_OPTIMUM = 18.5
        prior.species_thermal_optimum = round(
            lmb_prob * LMB_OPTIMUM + smb_prob * SMB_OPTIMUM, 2
        )

        if not np.isnan(prior.lagos_max_depth_m):
            depth_signal = 1.0 / (1.0 + math.exp(-0.2 * (prior.lagos_max_depth_m - 15.0)))
            prior.depth_smb_signal = round(depth_signal, 4)

        # ── v7: full multi-species features ──
        lon_val = lon if not np.isnan(lon) else -90.0
        depth_m = prior.lagos_max_depth_m if not np.isnan(prior.lagos_max_depth_m) else float("nan")
        area_ha = prior.lagos_area_ha if not np.isnan(prior.lagos_area_ha) else float("nan")

        multi = compute_multi_species_features(
            lat=lat,
            lon=lon_val,
            water_temp_c=water_temp_c,
            month=month,
            depth_m=depth_m,
            lake_area_ha=area_ha,
        )
        prior.weighted_optimal_temp = multi["weighted_optimal_temp"]
        prior.weighted_thermal_comfort = multi["weighted_thermal_comfort"]
        prior.pressure_sensitivity_score = multi["pressure_sensitivity_score"]
        prior.frontal_response_score = multi["frontal_response_score"]
        prior.flow_preference_score = multi["flow_preference_score"]
        prior.spawn_activity = multi["spawn_activity"]
        prior.peak_feed_activity = multi["peak_feed_activity"]
        prior.dominant_species_group = multi["dominant_species_group"]
        prior.top_species_prob = multi["top_species_prob"]

    @staticmethod
    def _compute_confidence(prior: SitePrior) -> float:
        """Compute overall confidence score from match quality and data availability.

        Confidence is a weighted combination of:
        - CreelCat match quality (50% weight)
        - LAGOS data availability (25% weight)
        - Embedding availability (25% weight)
        """
        # CreelCat match component
        quality_scores = {"exact": 1.0, "nearby": 0.7, "regional": 0.3, "none": 0.0}
        creel_score = quality_scores.get(prior.creel_match_quality, 0.0)

        # Bonus if actual CPUE data is present (not just spatial proximity)
        if not np.isnan(prior.creel_cpue_hour_median):
            creel_score = min(1.0, creel_score + 0.1)

        # LAGOS component
        lagos_score = 0.0
        if not np.isnan(prior.lagos_max_depth_m):
            lagos_score += 0.4
        if not np.isnan(prior.lagos_area_ha):
            lagos_score += 0.3
        if not np.isnan(prior.lagos_sdi):
            lagos_score += 0.3

        # Embedding component
        emb_score = 1.0 if np.any(prior.embedding != 0) else 0.0

        confidence = 0.5 * creel_score + 0.25 * lagos_score + 0.25 * emb_score
        return round(confidence, 3)


class UserPriorUpdater:
    """Bayesian-style updater that blends CreelCat priors with user reports.

    Uses an alpha-weighted blend where the weight on the CreelCat prior
    decreases as more user reports accumulate:

        alpha = max(0.2, 1 - n_reports / 50)
        updated = alpha * creelcat_prior + (1 - alpha) * user_cpue

    This ensures:
    - With 0 user reports, alpha=1.0 -> full CreelCat prior
    - With 50+ reports, alpha=0.2 -> 80% user data, 20% CreelCat anchor

    The CreelCat anchor (20% floor) prevents the estimate from diverging
    entirely from survey-based baselines even with heavy user reporting.

    Example::

        updater = UserPriorUpdater()
        updated_cpue = updater.update(
            location_key="lake_lanier_ga",
            user_cpue=0.35,
            n_reports=12,
            creelcat_prior=0.22,
        )
    """

    def __init__(self, alpha_floor: float = 0.2, alpha_decay_n: int = 50) -> None:
        """Initialize the updater.

        Args:
            alpha_floor: Minimum weight on CreelCat prior (default 0.2).
            alpha_decay_n: Number of user reports at which alpha reaches the floor (default 50).
        """
        self.alpha_floor = alpha_floor
        self.alpha_decay_n = alpha_decay_n

    def update(
        self,
        location_key: str,
        user_cpue: float,
        n_reports: int,
        creelcat_prior: float,
    ) -> float:
        """Compute updated CPUE estimate blending CreelCat prior with user data.

        Args:
            location_key: Unique identifier for the location (for logging/tracking).
            user_cpue: Mean CPUE from user reports (fish per angler-hour).
            n_reports: Number of user reports contributing to user_cpue.
            creelcat_prior: CreelCat survey-based CPUE prior.

        Returns:
            Updated CPUE estimate as a float. Returns NaN if both inputs are NaN.
        """
        # Handle missing data gracefully
        has_prior = not np.isnan(creelcat_prior)
        has_user = not np.isnan(user_cpue) and n_reports > 0

        if not has_prior and not has_user:
            return float("nan")
        if not has_prior:
            return float(user_cpue)
        if not has_user:
            return float(creelcat_prior)

        alpha = max(self.alpha_floor, 1.0 - n_reports / self.alpha_decay_n)
        updated = alpha * creelcat_prior + (1.0 - alpha) * user_cpue

        logger.debug(
            "Updated %s: alpha=%.3f, prior=%.4f, user=%.4f (n=%d) -> %.4f",
            location_key,
            alpha,
            creelcat_prior,
            user_cpue,
            n_reports,
            updated,
        )
        return round(float(updated), 6)
