#!/usr/bin/env python3
"""Enrich tournament locations with USGS fish community features.

Uses the USGS Presence-Absence Database of Fish (35,918 stream reaches,
419 species) to compute spatial features for each tournament location:
- Bass species richness within radius
- Predator competition index
- Fish community diversity
- Bass presence probability in nearby reaches

Usage::
    python3 scripts/enrich_usgs_fish_features.py

Output: castline/validation/data/raw/usgs_fish_community_features.csv
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
FISH_DATA = (
    _PROJECT_ROOT
    / "castline/validation/data/raw/usgs_fish_occurrence/agap_fish_dataset_v2_0.csv"
)
SPECIES_LIST = (
    _PROJECT_ROOT
    / "castline/validation/data/raw/usgs_fish_occurrence/species_list_v2_0.csv"
)
DATASET_PATH = (
    _PROJECT_ROOT
    / "castline/validation/data/assembled/validation_dataset_v15.csv"
)
OUTPUT_PATH = (
    _PROJECT_ROOT
    / "castline/validation/data/raw/usgs_fish_community_features.csv"
)

# ---------------------------------------------------------------------------
# Species of interest (ITIS TSN codes)
# ---------------------------------------------------------------------------
# Bass species
BASS_TSNS = {
    168160: "largemouth_bass",
    550562: "smallmouth_bass",
    168161: "spotted_bass",
    168163: "redeye_bass",
    168100: "ozark_bass",
    564610: "shoal_bass",
}

# Competing predators
PREDATOR_TSNS = {
    650173: "walleye",
    650171: "sauger",
    162139: "northern_pike",
    162144: "muskellunge",
    162143: "chain_pickerel",
    161104: "bowfin",
}

# Forage/habitat indicators (sunfish family = productive bass habitat)
HABITAT_TSNS = {
    168141: "bluegill",
    168132: "green_sunfish",
    168097: "rock_bass",
    168167: "black_crappie",
    168166: "white_crappie",
    168154: "redear_sunfish",
    168144: "pumpkinseed",
    168138: "warmouth",
}

# Search radius in km
SEARCH_RADIUS_KM = 50.0
EARTH_RADIUS_KM = 6371.0


def main():
    print("=" * 60)
    print("USGS Fish Community Feature Enrichment")
    print("=" * 60)

    # Load species list for reference
    sp_df = pd.read_csv(SPECIES_LIST)
    tsn_to_name = dict(zip(sp_df["itis_tsn"], sp_df["common_name"]))
    print(f"Species list: {len(sp_df)} species")

    # Load fish occurrence data
    print("Loading USGS fish occurrence data...")
    fish = pd.read_csv(FISH_DATA, low_memory=False)
    print(f"Fish data: {fish.shape[0]} reaches, {fish.shape[1]} columns")

    # Get all species TSN columns (numeric columns after metadata)
    meta_cols = ["comid", "huc8", "latitude", "longitude", "source",
                 "sample_year", "sample_month", "sample_day"]
    species_cols = [c for c in fish.columns if c not in meta_cols]
    print(f"Species columns: {len(species_cols)}")

    # Convert TSN columns to int for matching
    species_tsns = []
    for c in species_cols:
        try:
            species_tsns.append(int(c))
        except ValueError:
            pass

    # Verify our target species exist
    all_target_tsns = {**BASS_TSNS, **PREDATOR_TSNS, **HABITAT_TSNS}
    found = {t: n for t, n in all_target_tsns.items() if str(t) in fish.columns}
    missing = {t: n for t, n in all_target_tsns.items() if str(t) not in fish.columns}
    print(f"Target species found: {len(found)}/{len(all_target_tsns)}")
    if missing:
        print(f"  Missing: {missing}")

    # Load tournament dataset for unique locations
    ds = pd.read_csv(DATASET_PATH, low_memory=False)
    loc_df = ds[["lat", "lon"]].drop_duplicates().reset_index(drop=True)
    print(f"\nTournament locations: {len(loc_df)}")

    # Build BallTree for fish reaches
    fish_coords = np.radians(fish[["latitude", "longitude"]].values)
    tree = BallTree(fish_coords, metric="haversine")
    print("BallTree built for fish reaches")

    # Search radius in radians
    radius_rad = SEARCH_RADIUS_KM / EARTH_RADIUS_KM

    # Compute features for each tournament location
    results = []
    for i, row in loc_df.iterrows():
        lat, lon = row["lat"], row["lon"]
        query = np.radians([[lat, lon]])

        # Find all reaches within radius
        indices = tree.query_radius(query, r=radius_rad)[0]

        feat = {"lat": lat, "lon": lon, "usgs_reaches_nearby": len(indices)}

        if len(indices) == 0:
            # No nearby reaches — fill with NaN
            for name in BASS_TSNS.values():
                feat[f"usgs_{name}_presence"] = np.nan
            feat["usgs_bass_richness"] = np.nan
            feat["usgs_predator_richness"] = np.nan
            feat["usgs_habitat_richness"] = np.nan
            feat["usgs_total_species_richness"] = np.nan
            feat["usgs_predator_competition"] = np.nan
            feat["usgs_forage_index"] = np.nan
            feat["usgs_community_diversity"] = np.nan
            results.append(feat)
            continue

        nearby = fish.iloc[indices]

        # Bass presence (fraction of nearby reaches where each bass species present)
        for tsn, name in BASS_TSNS.items():
            col = str(tsn)
            if col in nearby.columns:
                feat[f"usgs_{name}_presence"] = nearby[col].mean()
            else:
                feat[f"usgs_{name}_presence"] = 0.0

        # Bass species richness (mean number of bass species per reach)
        bass_cols = [str(t) for t in BASS_TSNS.keys() if str(t) in nearby.columns]
        if bass_cols:
            feat["usgs_bass_richness"] = nearby[bass_cols].sum(axis=1).mean()
        else:
            feat["usgs_bass_richness"] = 0.0

        # Predator richness
        pred_cols = [str(t) for t in PREDATOR_TSNS.keys() if str(t) in nearby.columns]
        if pred_cols:
            feat["usgs_predator_richness"] = nearby[pred_cols].sum(axis=1).mean()
        else:
            feat["usgs_predator_richness"] = 0.0

        # Predator competition index (predator richness / bass richness)
        if feat["usgs_bass_richness"] > 0:
            feat["usgs_predator_competition"] = (
                feat["usgs_predator_richness"] / feat["usgs_bass_richness"]
            )
        else:
            feat["usgs_predator_competition"] = np.nan

        # Habitat/forage indicator richness
        hab_cols = [str(t) for t in HABITAT_TSNS.keys() if str(t) in nearby.columns]
        if hab_cols:
            feat["usgs_habitat_richness"] = nearby[hab_cols].sum(axis=1).mean()
            # Forage index = fraction of reaches with bluegill + green sunfish
            forage_cols = [str(t) for t in [168141, 168132] if str(t) in nearby.columns]
            feat["usgs_forage_index"] = nearby[forage_cols].max(axis=1).mean() if forage_cols else 0.0
        else:
            feat["usgs_habitat_richness"] = 0.0
            feat["usgs_forage_index"] = 0.0

        # Total species richness (mean species count per reach)
        all_sp_cols = [str(t) for t in species_tsns if str(t) in nearby.columns]
        if all_sp_cols:
            richness_per_reach = nearby[all_sp_cols].sum(axis=1)
            feat["usgs_total_species_richness"] = richness_per_reach.mean()

            # Shannon diversity index (across reaches)
            total_presences = nearby[all_sp_cols].sum(axis=0)
            total_presences = total_presences[total_presences > 0]
            if len(total_presences) > 0:
                proportions = total_presences / total_presences.sum()
                feat["usgs_community_diversity"] = -(proportions * np.log(proportions)).sum()
            else:
                feat["usgs_community_diversity"] = 0.0
        else:
            feat["usgs_total_species_richness"] = 0.0
            feat["usgs_community_diversity"] = 0.0

        results.append(feat)

        if (i + 1) % 200 == 0:
            print(f"  {i+1}/{len(loc_df)} locations processed")

    print(f"  {len(loc_df)}/{len(loc_df)} locations processed")

    # Build output DataFrame
    out_df = pd.DataFrame(results)

    # Summary stats
    matched = (out_df["usgs_reaches_nearby"] > 0).sum()
    print(f"\nLocations with nearby reaches: {matched}/{len(out_df)} "
          f"({matched/len(out_df)*100:.1f}%)")
    print(f"Mean reaches per location: {out_df['usgs_reaches_nearby'].mean():.1f}")
    print(f"Median reaches per location: {out_df['usgs_reaches_nearby'].median():.0f}")

    # Feature correlations preview
    print("\nFeature summary:")
    feat_cols = [c for c in out_df.columns if c.startswith("usgs_")]
    for c in feat_cols:
        vals = out_df[c].dropna()
        if len(vals) > 0:
            print(f"  {c:40s} mean={vals.mean():.3f}  std={vals.std():.3f}  "
                  f"non-null={len(vals)}")

    # Save
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(OUTPUT_PATH, index=False)
    print(f"\nSaved to {OUTPUT_PATH}")
    print(f"Shape: {out_df.shape}")


if __name__ == "__main__":
    main()
