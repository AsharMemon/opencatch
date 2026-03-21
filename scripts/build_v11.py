"""Build v11 dataset: v10 + recompute fish biology features using NASA POWER water temp.

Key insight: metabolic_rate_index, feeding_window_score, spawn_progress etc.
have only 7.3% coverage because they depend on water_temp_c (USGS data).
But np_est_water_temp_c (NASA POWER, 90.3% coverage) correlates r=0.857
with actual water temp. Recompute biology features using it as fallback.
"""
import pandas as pd
import numpy as np
import math

V10_PATH = "castline/validation/data/assembled/validation_dataset_v10.csv"
OUT_PATH = "castline/validation/data/assembled/validation_dataset_v11.csv"


def _clamp(v, lo=0.0, hi=1.0):
    if np.isnan(v):
        return np.nan
    return max(lo, min(hi, v))


def compute_spawn_progress(water_temp):
    """Linear mapping from 10C to 24C."""
    if np.isnan(water_temp):
        return np.nan
    return _clamp((water_temp - 10.0) / 14.0)


def compute_prespawn_intensity(water_temp):
    """Gaussian centered at 15C."""
    if np.isnan(water_temp):
        return np.nan
    return math.exp(-0.5 * ((water_temp - 15.0) / 2.0) ** 2)


def compute_spawn_bedding_prob(water_temp):
    """Gaussian centered at 19.5C."""
    if np.isnan(water_temp):
        return np.nan
    return math.exp(-0.5 * ((water_temp - 19.5) / 2.5) ** 2)


def compute_postspawn_lethargy(water_temp):
    """Gaussian centered at 24C."""
    if np.isnan(water_temp):
        return np.nan
    return math.exp(-0.5 * ((water_temp - 24.0) / 2.0) ** 2)


def compute_metabolic_rate(water_temp):
    """Q10-based metabolic rate, normalized."""
    if np.isnan(water_temp):
        return np.nan
    q10 = 2.3
    raw_rate = q10 ** ((water_temp - 25.0) / 10.0)
    if water_temp > 33:
        stress = max(0.1, 1.0 - (water_temp - 33.0) / 7.0)
        raw_rate *= stress
    return _clamp(raw_rate / 1.5)


def compute_feeding_window(water_temp):
    """Gaussian centered at 23C with extreme suppression."""
    if np.isnan(water_temp):
        return np.nan
    base = math.exp(-0.5 * ((water_temp - 23.0) / 6.0) ** 2)
    if water_temp < 7:
        base *= max(0.05, water_temp / 7.0)
    elif water_temp > 32:
        base *= max(0.1, 1.0 - (water_temp - 32.0) / 8.0)
    return _clamp(base)


def compute_cumulative_degree_days(water_temp):
    """Approximate: (water_temp - 15) * 30 when no daily history."""
    if np.isnan(water_temp):
        return np.nan
    return max(0, (water_temp - 15.0) * 30.0)


def main():
    print("=" * 60)
    print("Building v11 Dataset (Biology features from NASA POWER)")
    print("=" * 60)

    df = pd.read_csv(V10_PATH, low_memory=False)
    df = df[df["median_weight_lb"].notna()].copy()
    print(f"v10 input: {len(df)} rows, {df.columns.shape[0]} columns")

    # Determine effective water temperature:
    # Priority: water_temp_c (USGS) > np_est_water_temp_c (NASA POWER estimate)
    df["_eff_water_temp"] = df["water_temp_c"]

    # Fill with NASA POWER estimated water temp where water_temp_c is NaN
    mask_need = df["_eff_water_temp"].isna() & df["np_est_water_temp_c"].notna()
    df.loc[mask_need, "_eff_water_temp"] = df.loc[mask_need, "np_est_water_temp_c"]

    # Fall back to air temp empirical relationship if still NaN
    if "np_temp_mean_c" in df.columns:
        mask_need2 = df["_eff_water_temp"].isna() & df["np_temp_mean_c"].notna()
        df.loc[mask_need2, "_eff_water_temp"] = (
            df.loc[mask_need2, "np_temp_mean_c"] * 0.663 + 7.16
        )

    eff_cov = df["_eff_water_temp"].notna().mean() * 100
    print(f"\nEffective water temp coverage: {eff_cov:.1f}%")
    print(f"  From USGS water_temp_c: {df.water_temp_c.notna().mean()*100:.1f}%")
    print(f"  From np_est_water_temp_c: {mask_need.sum()} rows")
    print(f"  From air temp fallback: {mask_need2.sum()} rows")

    # Recompute biology features using effective water temp
    print("\nRecomputing biology features...")

    wt = df["_eff_water_temp"].values

    # Vectorized computation
    df["spawn_progress"] = np.where(
        np.isnan(wt), np.nan,
        np.clip((wt - 10.0) / 14.0, 0, 1)
    )

    df["prespawn_intensity"] = np.where(
        np.isnan(wt), np.nan,
        np.exp(-0.5 * ((wt - 15.0) / 2.0) ** 2)
    )

    df["spawn_bedding_prob"] = np.where(
        np.isnan(wt), np.nan,
        np.exp(-0.5 * ((wt - 19.5) / 2.5) ** 2)
    )

    df["postspawn_lethargy"] = np.where(
        np.isnan(wt), np.nan,
        np.exp(-0.5 * ((wt - 24.0) / 2.0) ** 2)
    )

    # Metabolic rate (with stress penalty)
    raw_rate = 2.3 ** ((wt - 25.0) / 10.0)
    stress_mask = wt > 33
    stress_penalty = np.where(stress_mask,
                              np.maximum(0.1, 1.0 - (wt - 33.0) / 7.0), 1.0)
    metabolic = np.clip(raw_rate * stress_penalty / 1.5, 0, 1)
    df["metabolic_rate_index"] = np.where(np.isnan(wt), np.nan, metabolic)

    # Feeding window
    feeding_base = np.exp(-0.5 * ((wt - 23.0) / 6.0) ** 2)
    cold_suppress = np.where(wt < 7, np.maximum(0.05, wt / 7.0), 1.0)
    hot_suppress = np.where(wt > 32, np.maximum(0.1, 1.0 - (wt - 32.0) / 8.0), 1.0)
    feeding = np.clip(feeding_base * cold_suppress * hot_suppress, 0, 1)
    df["feeding_window_score"] = np.where(np.isnan(wt), np.nan, feeding)

    # Cumulative degree days (approximation)
    df["cumulative_degree_days"] = np.where(
        np.isnan(wt), np.nan,
        np.maximum(0, (wt - 15.0) * 30.0)
    )

    # Water temp × spawn phase interaction
    if "spawn_phase_score" in df.columns:
        df["water_temp_spawn"] = np.where(
            np.isnan(wt) | df["spawn_phase_score"].isna(),
            np.nan,
            wt * df["spawn_phase_score"]
        )

    # DO/temp ratio (approximate if we don't have real DO)
    # Skip - can't estimate DO from air temp

    # Add a flag for data source of water temp
    df["water_temp_source"] = 0  # unknown
    df.loc[df["water_temp_c"].notna(), "water_temp_source"] = 3  # USGS (best)
    df.loc[mask_need, "water_temp_source"] = 2  # NASA POWER estimate
    df.loc[mask_need2, "water_temp_source"] = 1  # air temp empirical

    # Clean up
    df.drop(columns=["_eff_water_temp"], inplace=True)

    # Report coverage improvement
    bio_cols = ["spawn_progress", "prespawn_intensity", "spawn_bedding_prob",
                "postspawn_lethargy", "metabolic_rate_index", "feeding_window_score",
                "cumulative_degree_days", "water_temp_spawn"]

    print("\nBiology feature coverage (before → after):")
    before = {"spawn_progress": 7.3, "prespawn_intensity": 7.3,
              "spawn_bedding_prob": 7.3, "postspawn_lethargy": 7.3,
              "metabolic_rate_index": 7.3, "feeding_window_score": 7.3,
              "cumulative_degree_days": 3.4, "water_temp_spawn": 7.3}
    for c in bio_cols:
        if c in df.columns:
            after = df[c].notna().mean() * 100
            b = before.get(c, 0)
            print(f"  {c:35s}: {b:.1f}% → {after:.1f}%")

    print(f"\nv11 dataset: {len(df)} rows, {df.columns.shape[0]} columns")
    print(f"Locations: {df.location.nunique()}")

    df.to_csv(OUT_PATH, index=False)
    print(f"Saved to {OUT_PATH}")


if __name__ == "__main__":
    main()
