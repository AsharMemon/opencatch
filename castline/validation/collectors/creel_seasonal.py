"""CreelCat seasonal productivity features.

Builds monthly bass CPUE baselines from CreelCat survey data,
providing seasonal productivity signals for tournament locations.
Instead of raw CPUE (which is redundant with location encoding),
this module provides:
  - state_month_cpue_mean: average CPUE for bass in that state+month
  - seasonal_cpue_ratio: how the current month compares to the annual avg
  - cpue_trend_3m: 3-month rolling CPUE trend

These capture *when* fish are most catchable, not just *where*.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import numpy as np

# Month name to number mapping
MONTH_MAP = {
    "January": 1, "February": 2, "March": 3, "April": 4,
    "May": 5, "June": 6, "July": 7, "August": 8,
    "September": 9, "October": 10, "November": 11, "December": 12,
}

# US state abbreviation to full name (for matching)
STATE_ABBREV = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}

# Reverse: full name -> abbreviation
STATE_TO_ABBREV = {v.upper(): k for k, v in STATE_ABBREV.items()}


def build_seasonal_baselines(
    cpue_path: Path,
    survey_path: Path,
    output_path: Path,
) -> pd.DataFrame:
    """Build monthly CPUE baselines by state from CreelCat data.

    Returns a DataFrame with columns:
        state_ab, month, cpue_mean, cpue_median, cpue_std, n_surveys,
        annual_mean, seasonal_ratio
    """
    cpue = pd.read_csv(cpue_path)
    survey = pd.read_csv(survey_path, low_memory=False)

    # Get survey month info
    survey_info = survey[["Survey_ID", "Start_Month", "State_Ab"]].copy()
    survey_info.columns = ["survey_id", "start_month", "state_ab"]

    merged = cpue.merge(survey_info, on="survey_id", how="left", suffixes=("", "_s"))
    merged["month_num"] = merged["start_month"].map(MONTH_MAP)

    # Use state from survey if available, else from cpue
    merged["state_final"] = merged["state_ab"].fillna(merged["state"])

    # Filter to rows with valid CPUE and month
    valid = merged.dropna(subset=["month_num", "cpue_fish_per_hour"]).copy()
    valid = valid[valid["cpue_fish_per_hour"] > 0]

    print(f"creel_seasonal: {len(valid):,} valid rows for seasonal baselines", file=sys.stderr)

    # Build monthly aggregates by state
    monthly = (
        valid.groupby(["state_final", "month_num"])["cpue_fish_per_hour"]
        .agg(["mean", "median", "std", "count"])
        .reset_index()
    )
    monthly.columns = ["state_ab", "month", "cpue_mean", "cpue_median", "cpue_std", "n_surveys"]

    # Compute annual means per state
    annual = (
        valid.groupby("state_final")["cpue_fish_per_hour"]
        .mean()
        .reset_index()
    )
    annual.columns = ["state_ab", "annual_mean"]

    # Merge and compute ratio
    monthly = monthly.merge(annual, on="state_ab", how="left")
    monthly["seasonal_ratio"] = monthly["cpue_mean"] / monthly["annual_mean"].clip(lower=0.001)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    monthly.to_csv(output_path, index=False)
    print(
        f"creel_seasonal: wrote {len(monthly)} state-month baselines to {output_path}",
        file=sys.stderr,
    )
    return monthly


def extract_state_from_location(location: str) -> str:
    """Extract 2-letter state abbreviation from a tournament location string.

    Handles formats like:
        'Lake Guntersville, Guntersville, AL'
        'Kentucky Lake, Paris, TN'
    """
    parts = [p.strip() for p in location.split(",")]
    for part in reversed(parts):
        # Check if it's a 2-letter state code
        upper = part.upper().strip()
        if len(upper) == 2 and upper in STATE_ABBREV:
            return upper
        # Check full state name
        if upper in STATE_TO_ABBREV:
            return STATE_TO_ABBREV[upper]
    return ""


def enrich_with_seasonal_cpue(
    dataset: pd.DataFrame,
    baselines: pd.DataFrame,
) -> pd.DataFrame:
    """Add seasonal CPUE features to a tournament dataset.

    Parameters
    ----------
    dataset : pd.DataFrame
        Must have 'date' and 'location' columns.
    baselines : pd.DataFrame
        Output of build_seasonal_baselines().

    Returns
    -------
    pd.DataFrame
        Original dataset with added columns:
        - creel_seasonal_cpue: expected CPUE for state+month
        - creel_seasonal_ratio: how that month compares to annual avg
    """
    df = dataset.copy()

    # Extract state and month
    df["_state_ab"] = df["location"].apply(extract_state_from_location)
    df["_month"] = pd.to_datetime(df["date"], errors="coerce").dt.month

    # Merge baselines
    df = df.merge(
        baselines[["state_ab", "month", "cpue_mean", "seasonal_ratio"]].rename(
            columns={"cpue_mean": "creel_seasonal_cpue", "seasonal_ratio": "creel_seasonal_ratio"}
        ),
        left_on=["_state_ab", "_month"],
        right_on=["state_ab", "month"],
        how="left",
    )

    # Clean up temp columns
    df.drop(columns=["_state_ab", "_month", "state_ab", "month"], inplace=True, errors="ignore")

    matched = df["creel_seasonal_cpue"].notna().sum()
    print(
        f"creel_seasonal: matched {matched}/{len(df)} rows "
        f"({100*matched/len(df):.0f}%) with seasonal CPUE baselines",
        file=sys.stderr,
    )
    return df


if __name__ == "__main__":
    baselines = build_seasonal_baselines(
        cpue_path=Path("castline/validation/data/raw/creel_cpue_bass.csv"),
        survey_path=Path("castline/validation/data/raw/creel/Survey_Data.csv"),
        output_path=Path("castline/validation/data/raw/creel_seasonal_baselines.csv"),
    )
    print(f"\n{len(baselines)} state-month baselines")
    print(baselines.sort_values(["state_ab", "month"]).head(20).to_string())
