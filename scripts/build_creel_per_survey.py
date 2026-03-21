"""Build creel events with per-survey targets (temporal variance)."""
import pandas as pd
import numpy as np

survey = pd.read_csv("castline/validation/data/raw/creel/Survey_Data.csv", low_memory=False)
fish = pd.read_csv("castline/validation/data/raw/creel/FishDataCompiled.csv", low_memory=False)

# Black bass CPUE per survey
bass_mask = fish.Taxa.str.contains("Micropterus|Largemouth Bass", case=False, na=False)
bass = fish[bass_mask].copy()
print(f"Black bass records: {len(bass)}")

survey_cpue = bass.groupby("Survey_ID").agg(
    total_catch_per_hour=("Catch_Per_Hour", "sum"),
).reset_index()

# Merge with survey metadata
merged = survey_cpue.merge(
    survey[["Survey_ID", "Waterbody_Unique_Name", "State",
            "Year", "Start_Date", "Survey_Acres", "Lat", "Lon", "WB_Type"]],
    on="Survey_ID", how="left",
)

# Filter valid CPUE
merged = merged[merged.total_catch_per_hour.notna() & (merged.total_catch_per_hour > 0)].copy()
print(f"Surveys with bass CPUE: {len(merged)}")
print(f"Waterbodies: {merged.Waterbody_Unique_Name.nunique()}")

# Convert CPUE to synthetic weight
merged["synthetic_weight_lb"] = (11.60 * merged.total_catch_per_hour + 2.93).clip(2, 25)

# Build date
merged["date"] = pd.to_datetime(merged.Start_Date, errors="coerce")
missing_date = merged.date.isna()
merged.loc[missing_date, "date"] = pd.to_datetime(
    merged.loc[missing_date, "Year"].fillna(2000).astype(int).astype(str) + "-06-15"
)

# Location name: strip the code in parentheses
# "Alan Henry (TX_Gar)" → "Alan Henry"
wb_name = merged.Waterbody_Unique_Name.str.replace(r"\s*\(.*?\)\s*$", "", regex=True).str.strip()
merged["location"] = wb_name + ", " + merged.State.str.strip()

# Build output
output = pd.DataFrame({
    "location": merged["location"],
    "date": merged["date"].dt.strftime("%Y-%m-%d"),
    "median_weight_lb": merged["synthetic_weight_lb"],
    "lat": merged["Lat"],
    "lon": merged["Lon"],
    "area_acres": merged["Survey_Acres"],
    "creel_cpue_total": merged["total_catch_per_hour"],
    "wb_type": merged["WB_Type"],
    "year": merged["Year"],
    "source": "creel",
})

print(f"\nCreel events: {len(output)}")
print(f"Locations: {output.location.nunique()}")
print(f"Date range: {output.date.min()} to {output.date.max()}")

# Variance check
var_check = output.groupby("location").agg(
    n=("median_weight_lb", "count"),
    std=("median_weight_lb", "std"),
).reset_index()
multi = var_check[var_check.n >= 2]
print(f"Locations with 2+ surveys: {len(multi)}")
print(f"Mean target std: {multi['std'].mean():.2f}")
print(f"Locations with std > 0.01: {(multi['std'] > 0.01).sum()}/{len(multi)}")
print(f"Locations with n >= 15: {(var_check.n >= 15).sum()}")

output.to_csv("castline/validation/data/raw/creel_per_survey_events.csv", index=False)
print(f"Saved to creel_per_survey_events.csv")
