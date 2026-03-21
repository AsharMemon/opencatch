#!/usr/bin/env python3
"""Download CreelCat data and build a bass CPUE dataset.

Steps:
1. Download CreelCat CSVs from USGS ScienceBase to data/raw/creel/
2. Build normalized bass CPUE dataset
3. Print summary statistics
4. Analyze waterbody matching against tournament locations
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
from castline.validation.collectors.creel import download_creel_data, build_creel_cpue_dataset

RAW_DIR = PROJECT_ROOT / "castline" / "validation" / "data" / "raw"
CREEL_DIR = RAW_DIR / "creel"
CPUE_OUTPUT = RAW_DIR / "creel_cpue_bass.csv"
ENRICHED_PATH = PROJECT_ROOT / "castline" / "validation" / "data" / "assembled" / "validation_dataset_enriched.csv"


def main() -> None:
    # ── 1. Download CreelCat data ─────────────────────────────────────
    print("=" * 60)
    print("STEP 1: Download CreelCat data")
    print("=" * 60)
    try:
        paths = download_creel_data(CREEL_DIR)
        print(f"\nDownloaded {len(paths)} files:")
        for key, p in paths.items():
            print(f"  {key}: {p}  ({p.stat().st_size:,} bytes)")
    except Exception as exc:
        print(f"\nERROR downloading CreelCat data: {exc}")
        # Try to use existing files if download failed
        paths = {}
        for key, fname in [("fish", "FishDataCompiled.csv"),
                           ("survey", "Survey_Data.csv"),
                           ("effort", "AngEffort_Data.csv")]:
            p = CREEL_DIR / fname
            if p.exists():
                paths[key] = p
                print(f"  Using existing file: {p}")
        if len(paths) < 3:
            print("Cannot proceed without all 3 files. Exiting.")
            sys.exit(1)

    # ── 2. Build CPUE dataset ─────────────────────────────────────────
    print("\n" + "=" * 60)
    print("STEP 2: Build bass CPUE dataset")
    print("=" * 60)
    try:
        df = build_creel_cpue_dataset(
            fish_data_path=paths["fish"],
            survey_data_path=paths["survey"],
            effort_data_path=paths["effort"],
            output_path=CPUE_OUTPUT,
        )
    except Exception as exc:
        print(f"\nERROR building CPUE dataset: {exc}")
        sys.exit(1)

    # ── 3. Summary statistics ─────────────────────────────────────────
    print("\n" + "=" * 60)
    print("STEP 3: Summary statistics")
    print("=" * 60)

    print(f"\nTotal rows: {len(df):,}")

    # Species distribution
    print("\nSpecies distribution:")
    species_counts = df["species"].value_counts()
    for sp, cnt in species_counts.items():
        print(f"  {sp}: {cnt:,} ({100 * cnt / len(df):.1f}%)")

    # State distribution
    print(f"\nStates represented: {df['state'].nunique()}")
    state_counts = df["state"].value_counts()
    print("Top 15 states:")
    for st, cnt in state_counts.head(15).items():
        print(f"  {st}: {cnt:,}")

    # Date range
    dates = pd.to_datetime(df["date"], errors="coerce").dropna()
    if len(dates) > 0:
        print(f"\nDate range: {dates.min().strftime('%Y-%m-%d')} to {dates.max().strftime('%Y-%m-%d')}")
        print(f"Rows with valid dates: {len(dates):,} / {len(df):,}")
    else:
        print("\nNo valid dates found.")

    # Waterbody count
    waterbodies = df["waterbody_name"].dropna()
    waterbodies = waterbodies[waterbodies.str.strip() != ""]
    print(f"\nUnique waterbodies: {waterbodies.nunique():,}")

    # CPUE stats
    cpue = df["cpue_fish_per_hour"].dropna()
    if len(cpue) > 0:
        print(f"\nCPUE (fish/hour) stats:")
        print(f"  count: {len(cpue):,}")
        print(f"  mean:  {cpue.mean():.4f}")
        print(f"  median:{cpue.median():.4f}")
        print(f"  min:   {cpue.min():.4f}")
        print(f"  max:   {cpue.max():.4f}")

    # ── 4. Waterbody matching with tournament locations ────────────────
    print("\n" + "=" * 60)
    print("STEP 4: Waterbody matching analysis")
    print("=" * 60)

    if not ENRICHED_PATH.exists():
        print(f"\nEnriched dataset not found at {ENRICHED_PATH}. Skipping.")
        return

    enriched = pd.read_csv(ENRICHED_PATH)
    tourn_locations = enriched["location"].dropna().unique()
    print(f"\nTournament locations (unique): {len(tourn_locations)}")

    creel_waterbodies = waterbodies.str.strip().str.lower().unique()
    print(f"CreelCat waterbodies (unique): {len(creel_waterbodies)}")

    # Normalize tournament locations: extract primary waterbody name
    def extract_waterbody(loc: str) -> str:
        """Extract the primary waterbody name from a tournament location string."""
        # Typically "Lake X, County, ST" or "River X, City, ST"
        parts = loc.split(",")
        return parts[0].strip().lower()

    tourn_waterbody_names = {extract_waterbody(loc): loc for loc in tourn_locations}

    # Exact match
    exact_matches = []
    for twb_lower, twb_orig in tourn_waterbody_names.items():
        if twb_lower in creel_waterbodies:
            exact_matches.append((twb_orig, twb_lower))

    print(f"\nExact matches (case-insensitive): {len(exact_matches)}")
    for orig, matched in sorted(exact_matches):
        print(f"  {orig}")

    # Substring / fuzzy matching: check if creel waterbody appears in tournament name or vice versa
    substring_matches = []
    already_matched = {m[1] for m in exact_matches}

    for twb_lower, twb_orig in tourn_waterbody_names.items():
        if twb_lower in already_matched:
            continue
        for cwb in creel_waterbodies:
            # Skip very short names to avoid false matches
            if len(cwb) < 4:
                continue
            if cwb in twb_lower or twb_lower in cwb:
                substring_matches.append((twb_orig, cwb))
                break

    print(f"\nSubstring matches: {len(substring_matches)}")
    for orig, cwb in sorted(substring_matches):
        print(f"  Tournament: {orig}")
        print(f"    CreelCat: {cwb}")

    # Word-overlap matching for remaining unmatched
    matched_so_far = already_matched | {extract_waterbody(m[0]) for m in substring_matches}
    STOP_WORDS = {"lake", "river", "creek", "reservoir", "the", "of", "pond", "bay", "fork"}

    word_matches = []
    for twb_lower, twb_orig in tourn_waterbody_names.items():
        if twb_lower in matched_so_far:
            continue
        twb_words = set(twb_lower.split()) - STOP_WORDS
        if len(twb_words) < 1:
            continue
        best_score = 0
        best_cwb = ""
        for cwb in creel_waterbodies:
            cwb_words = set(cwb.split()) - STOP_WORDS
            if len(cwb_words) < 1:
                continue
            overlap = len(twb_words & cwb_words)
            score = overlap / max(len(twb_words), len(cwb_words))
            if score > best_score and score >= 0.5:
                best_score = score
                best_cwb = cwb
        if best_cwb:
            word_matches.append((twb_orig, best_cwb, best_score))

    print(f"\nWord-overlap matches (>=50% overlap): {len(word_matches)}")
    for orig, cwb, score in sorted(word_matches, key=lambda x: -x[2]):
        print(f"  Tournament: {orig}")
        print(f"    CreelCat: {cwb}  (score: {score:.2f})")

    total_matched = len(exact_matches) + len(substring_matches) + len(word_matches)
    total_tourn = len(tourn_waterbody_names)
    print(f"\n{'─' * 40}")
    print(f"MATCHING SUMMARY:")
    print(f"  Total tournament locations:  {total_tourn}")
    print(f"  Exact matches:               {len(exact_matches)}")
    print(f"  Substring matches:           {len(substring_matches)}")
    print(f"  Word-overlap matches:        {len(word_matches)}")
    print(f"  Total matched:               {total_matched} / {total_tourn} ({100 * total_matched / max(total_tourn, 1):.1f}%)")
    print(f"  Unmatched:                   {total_tourn - total_matched}")

    # List unmatched tournament locations
    all_matched_lower = matched_so_far | {extract_waterbody(m[0]) for m in word_matches}
    unmatched = [
        twb_orig
        for twb_lower, twb_orig in tourn_waterbody_names.items()
        if twb_lower not in all_matched_lower
    ]
    if unmatched:
        print(f"\nUnmatched tournament locations:")
        for loc in sorted(unmatched):
            print(f"  {loc}")

    print(f"\nOutput saved to: {CPUE_OUTPUT}")


if __name__ == "__main__":
    main()
