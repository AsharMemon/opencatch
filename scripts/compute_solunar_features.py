#!/usr/bin/env python3
"""
Compute solunar major/minor feeding window features for the V16 dataset.

Uses moon_phase (0-1) to approximate solunar feeding periods:
- Major periods (~2h each): moon overhead + moon underfoot
- Minor periods (~1h each): moonrise + moonset
- Overlap with dawn/dusk windows drives fishing quality

Moon phase mapping:
- New moon (0.0): moon transits at ~noon → major periods near dawn/dusk (best!)
- Full moon (0.5): moon transits at ~midnight → major periods near midnight/noon
- First quarter (0.25): moon transits at ~6pm → major period near dusk
- Third quarter (0.75): moon transits at ~6am → major period near dawn
"""

import numpy as np
import pandas as pd
from pathlib import Path

INPUT_PATH = Path(__file__).resolve().parent.parent / "castline/validation/data/assembled/validation_dataset_v16.csv"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "castline/validation/data/raw/solunar_features.csv"

# Solunar period durations (hours)
MAJOR_PERIOD_DURATION = 2.0  # each major period lasts ~2h
MINOR_PERIOD_DURATION = 1.0  # each minor period lasts ~1h

# Dawn/dusk windows for overlap calculation
DAWN_CENTER_HOUR = 6.5   # approximate dawn (6:30 AM)
DUSK_CENTER_HOUR = 19.0  # approximate dusk (7:00 PM)
OVERLAP_WINDOW = 1.0     # ±1 hour


def moon_phase_to_transit_hour(phase: np.ndarray) -> np.ndarray:
    """Convert moon phase (0-1) to approximate lunar transit hour (0-24).

    New moon (0.0) transits at ~12:00 (noon) with the sun.
    Full moon (0.5) transits at ~0:00 (midnight), opposite the sun.
    Linear interpolation between.
    """
    # phase=0 → 12h, phase=0.5 → 0h (24h), phase=1.0 → 12h
    transit = (12.0 - phase * 24.0) % 24.0
    return transit


def compute_solunar_times(phase: np.ndarray):
    """Compute approximate solunar period center times from moon phase.

    Returns dict of arrays for major/minor period centers (hours 0-24).
    """
    transit = moon_phase_to_transit_hour(phase)

    # Major periods: moon overhead (transit) and underfoot (transit + 12h)
    major1 = transit
    major2 = (transit + 12.0) % 24.0

    # Minor periods: moonrise (~transit - 6h) and moonset (~transit + 6h)
    minor1 = (transit - 6.0) % 24.0
    minor2 = (transit + 6.0) % 24.0

    return {
        "major1": major1,
        "major2": major2,
        "minor1": minor1,
        "minor2": minor2,
    }


def periods_overlap(center: np.ndarray, duration: float,
                     window_center: float, window_radius: float) -> np.ndarray:
    """Check if a solunar period overlaps with a dawn/dusk window.

    Handles wrap-around at 0/24h boundary.
    Returns boolean array.
    """
    half = duration / 2.0
    # Compute minimum circular distance between period center and window center
    diff = np.abs(center - window_center)
    diff = np.minimum(diff, 24.0 - diff)
    # Overlap if distance < sum of half-widths
    return diff < (half + window_radius)


def moon_phase_category(phase: np.ndarray) -> np.ndarray:
    """Categorize moon phase into 8 standard categories."""
    cats = np.empty(len(phase), dtype=object)
    cats[:] = "new"  # default

    cats[(phase >= 0.0) & (phase < 0.0625)] = "new"
    cats[(phase >= 0.0625) & (phase < 0.1875)] = "waxing_crescent"
    cats[(phase >= 0.1875) & (phase < 0.3125)] = "first_quarter"
    cats[(phase >= 0.3125) & (phase < 0.4375)] = "waxing_gibbous"
    cats[(phase >= 0.4375) & (phase < 0.5625)] = "full"
    cats[(phase >= 0.5625) & (phase < 0.6875)] = "waning_gibbous"
    cats[(phase >= 0.6875) & (phase < 0.8125)] = "third_quarter"
    cats[(phase >= 0.8125) & (phase < 0.9375)] = "waning_crescent"
    cats[(phase >= 0.9375) & (phase <= 1.0)] = "new"

    return cats


def main():
    print(f"Loading V16 dataset from {INPUT_PATH}")
    df = pd.read_csv(INPUT_PATH)
    print(f"  Loaded {len(df)} events, {len(df.columns)} columns")

    # Validate required columns
    assert "moon_phase" in df.columns, "moon_phase column missing"
    assert "date" in df.columns, "date column missing"
    assert "location" in df.columns, "location column missing"

    phase = df["moon_phase"].values.copy()
    # Handle NaN: fill with 0.25 (neutral quarter moon) for computation, flag later
    has_phase = ~np.isnan(phase)
    phase[~has_phase] = 0.25
    print(f"  moon_phase available: {has_phase.sum()}/{len(df)} events")

    # --- Compute solunar period times ---
    times = compute_solunar_times(phase)

    # Major/minor hours per day (fixed: 2 major x 2h + 2 minor x 1h)
    solunar_major_hours = np.full(len(df), 2 * MAJOR_PERIOD_DURATION)
    solunar_minor_hours = np.full(len(df), 2 * MINOR_PERIOD_DURATION)
    solunar_total = solunar_major_hours + solunar_minor_hours

    # --- Dawn/dusk overlap ---
    # Check if ANY major or minor period overlaps dawn
    overlap_dawn = (
        periods_overlap(times["major1"], MAJOR_PERIOD_DURATION, DAWN_CENTER_HOUR, OVERLAP_WINDOW)
        | periods_overlap(times["major2"], MAJOR_PERIOD_DURATION, DAWN_CENTER_HOUR, OVERLAP_WINDOW)
        | periods_overlap(times["minor1"], MINOR_PERIOD_DURATION, DAWN_CENTER_HOUR, OVERLAP_WINDOW)
        | periods_overlap(times["minor2"], MINOR_PERIOD_DURATION, DAWN_CENTER_HOUR, OVERLAP_WINDOW)
    ).astype(int)

    overlap_dusk = (
        periods_overlap(times["major1"], MAJOR_PERIOD_DURATION, DUSK_CENTER_HOUR, OVERLAP_WINDOW)
        | periods_overlap(times["major2"], MAJOR_PERIOD_DURATION, DUSK_CENTER_HOUR, OVERLAP_WINDOW)
        | periods_overlap(times["minor1"], MINOR_PERIOD_DURATION, DUSK_CENTER_HOUR, OVERLAP_WINDOW)
        | periods_overlap(times["minor2"], MINOR_PERIOD_DURATION, DUSK_CENTER_HOUR, OVERLAP_WINDOW)
    ).astype(int)

    # --- Solunar quality score ---
    # Base quality: peaks at new moon (0.0) and full moon (0.5)
    # cos(2π * (phase - 0.5))^2 → peaks at phase=0.0 and 0.5
    base_quality = np.cos(2.0 * np.pi * (phase - 0.5)) ** 2

    # Dawn/dusk bonus: +20% if major period overlaps dawn or dusk
    dawn_dusk_bonus = 1.0 + 0.1 * overlap_dawn + 0.1 * overlap_dusk
    solunar_quality = np.clip(base_quality * dawn_dusk_bonus, 0.0, 1.0)

    # --- Moon phase category ---
    categories = moon_phase_category(phase)

    # --- Is major solunar day (within 2 days of new or full moon) ---
    # Phase distance to new (0.0) or full (0.5)
    dist_new = np.minimum(phase, 1.0 - phase)
    dist_full = np.abs(phase - 0.5)
    min_dist = np.minimum(dist_new, dist_full)
    # ~2 days out of 29.5 day cycle ≈ 0.068 phase
    is_major_day = (min_dist <= 0.068).astype(int)

    # --- Set NaN for events without moon_phase ---
    solunar_major_hours[~has_phase] = np.nan
    solunar_minor_hours[~has_phase] = np.nan
    solunar_total[~has_phase] = np.nan
    overlap_dawn[~has_phase] = -1  # will convert to NaN via float
    overlap_dusk[~has_phase] = -1
    solunar_quality[~has_phase] = np.nan
    is_major_day[~has_phase] = -1

    # Build output
    out = pd.DataFrame({
        "location": df["location"],
        "date": df["date"],
        "solunar_major_hours": solunar_major_hours,
        "solunar_minor_hours": solunar_minor_hours,
        "solunar_total_feeding_hours": solunar_total,
        "solunar_period_overlap_dawn": overlap_dawn.astype(float),
        "solunar_period_overlap_dusk": overlap_dusk.astype(float),
        "solunar_quality_score": np.round(solunar_quality, 4),
        "moon_phase_category": categories,
        "is_major_solunar_day": is_major_day.astype(float),
    })

    # Replace sentinel -1 with NaN
    out.replace(-1.0, np.nan, inplace=True)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUTPUT_PATH, index=False)
    print(f"\nWrote {len(out)} rows to {OUTPUT_PATH}")

    # --- Summary statistics ---
    print("\n=== Solunar Feature Summary ===")
    print(f"Total events: {len(out)}")
    print(f"Events with moon_phase: {has_phase.sum()}")
    print(f"\nmoon_phase_category distribution:")
    print(out["moon_phase_category"].value_counts().sort_index().to_string())
    print(f"\nis_major_solunar_day: {(out['is_major_solunar_day'] == 1).sum()} / {has_phase.sum()}"
          f" ({100 * (out['is_major_solunar_day'] == 1).sum() / has_phase.sum():.1f}%)")
    print(f"\nsolunar_period_overlap_dawn: {(out['solunar_period_overlap_dawn'] == 1).sum()} / {has_phase.sum()}"
          f" ({100 * (out['solunar_period_overlap_dawn'] == 1).sum() / has_phase.sum():.1f}%)")
    print(f"solunar_period_overlap_dusk: {(out['solunar_period_overlap_dusk'] == 1).sum()} / {has_phase.sum()}"
          f" ({100 * (out['solunar_period_overlap_dusk'] == 1).sum() / has_phase.sum():.1f}%)")
    print(f"\nsolunar_quality_score stats:")
    print(out["solunar_quality_score"].describe().to_string())

    # Show a few examples
    print("\n=== Sample rows ===")
    sample_cols = ["location", "date", "moon_phase_category", "solunar_quality_score",
                   "solunar_period_overlap_dawn", "solunar_period_overlap_dusk", "is_major_solunar_day"]
    # Pick one from each phase category
    samples = out.drop_duplicates("moon_phase_category").head(8)
    print(samples[sample_cols].to_string(index=False))


if __name__ == "__main__":
    main()
