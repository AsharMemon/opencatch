"""Build v12 dataset: v11 + WQP chlorophyll-a & Secchi depth as location features.

WQP data provides lake productivity indicators:
- Chlorophyll-a (µg/L): measures phytoplankton → food chain → fish productivity
- Secchi depth (m): water clarity → vegetation → habitat quality
- Total Phosphorus: nutrient loading → productivity
"""
import pandas as pd
import numpy as np

V11_PATH = "castline/validation/data/assembled/validation_dataset_v11.csv"
WQP_PATH = "castline/validation/data/raw/wqp_lake_productivity.csv"
OUT_PATH = "castline/validation/data/assembled/validation_dataset_v12.csv"


def main():
    print("=" * 60)
    print("Building v12 Dataset (v11 + WQP Productivity Features)")
    print("=" * 60)

    df = pd.read_csv(V11_PATH, low_memory=False)
    df = df[df["median_weight_lb"].notna()].copy()
    print(f"v11 input: {len(df)} rows, {df.columns.shape[0]} columns")

    # Load and aggregate WQP data
    wqp = pd.read_csv(WQP_PATH, low_memory=False)
    print(f"WQP raw: {len(wqp)} rows, {wqp.location.nunique()} locations")

    # Compute location-level medians for each characteristic
    loc_chlor = wqp[wqp.characteristic == "Chlorophyll a"].groupby("location")["value"].median()
    loc_secchi = wqp[wqp.characteristic == "Depth, Secchi disk depth"].groupby("location")["value"].median()
    loc_phosph = wqp[wqp.characteristic == "Total Phosphorus, mixed forms"].groupby("location")["value"].median()
    loc_chlor_corrected = wqp[wqp.characteristic == "Chlorophyll a, corrected for pheophytin"].groupby("location")["value"].median()

    # Use corrected chlorophyll when available, otherwise raw
    loc_chlor_final = loc_chlor_corrected.reindex(loc_chlor.index).fillna(loc_chlor)

    # Create trophic state index (TSI) from chlorophyll-a (Carlson 1977)
    # TSI = 30.6 + 9.81 * ln(Chl-a)
    loc_tsi = 30.6 + 9.81 * np.log(loc_chlor_final.clip(lower=0.1))

    # Map to dataset
    df["wqp_chlorophyll_a"] = df.location.map(loc_chlor_final)
    df["wqp_secchi_depth_m"] = df.location.map(loc_secchi)
    df["wqp_phosphorus"] = df.location.map(loc_phosph)
    df["wqp_tsi"] = df.location.map(loc_tsi)

    # Derived features
    # Chlorophyll × area interaction (large productive lakes = better fishing)
    df["chlor_x_log_area"] = df["wqp_chlorophyll_a"] * np.log1p(df["area_acres"])

    # Secchi × depth interaction (clear deep lakes vs murky shallow)
    df["secchi_x_depth"] = df["wqp_secchi_depth_m"] * np.log1p(df["max_depth_ft"].fillna(0))

    # Has WQP data flag
    df["has_wqp_data"] = (df["wqp_chlorophyll_a"].notna()).astype(int)

    # Report coverage
    print(f"\nWQP feature coverage:")
    for col in ["wqp_chlorophyll_a", "wqp_secchi_depth_m", "wqp_phosphorus", "wqp_tsi"]:
        cov = df[col].notna().mean() * 100
        print(f"  {col:30s}: {cov:.1f}%")

    # How many events and locations covered?
    covered_events = df["wqp_chlorophyll_a"].notna().sum()
    covered_locs = df[df["wqp_chlorophyll_a"].notna()].location.nunique()
    print(f"\nEvents with WQP data: {covered_events} ({covered_events/len(df)*100:.1f}%)")
    print(f"Locations with WQP data: {covered_locs}")

    # Correlations with target
    target = "median_weight_lb"
    for col in ["wqp_chlorophyll_a", "wqp_secchi_depth_m", "wqp_phosphorus", "wqp_tsi",
                "chlor_x_log_area", "secchi_x_depth"]:
        if col in df.columns:
            mask = df[col].notna() & df[target].notna()
            if mask.sum() > 10:
                corr = df.loc[mask, col].corr(df.loc[mask, target])
                print(f"  {col:30s} r={corr:+.3f} (n={mask.sum()})")

    print(f"\nv12 dataset: {len(df)} rows, {df.columns.shape[0]} columns")
    df.to_csv(OUT_PATH, index=False)
    print(f"Saved to {OUT_PATH}")


if __name__ == "__main__":
    main()
