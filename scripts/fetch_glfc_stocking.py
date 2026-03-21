"""
Fetch bass stocking records from the Great Lakes Fish Stocking database (fsis.glfc.org).

The GLFC FSIS xlsx API returns Excel files where row 0 is a title row
("Stocking Data") and row 1 contains actual column headers. We use header=1
to parse correctly.

Species codes: LMB (Largemouth Bass), SMB (Smallmouth Bass)
"""

import io
import requests
import pandas as pd
from pathlib import Path

BASE = "https://fsis.glfc.org"
OUT = Path(__file__).resolve().parent.parent / "castline/validation/data/raw/glfc_stocking.csv"

LAKE_NAMES = {
    "ER": "Lake Erie",
    "HU": "Lake Huron",
    "MI": "Lake Michigan",
    "ON": "Lake Ontario",
    "SC": "Lake St. Clair",
    "SU": "Lake Superior",
}

SPECIES = {
    "LMB": "Largemouth Bass",
    "SMB": "Smallmouth Bass",
}

# Columns we want in the final output
KEEP_COLS = [
    "stock_id", "agency", "lake", "lake_name", "state_prov",
    "location_primary", "location_secondary",
    "latitude", "longitude",
    "species_code", "species_common", "strain",
    "year", "month", "day",
    "year_class", "life_stage", "stocking_method",
    "number_stocked", "total_weight_kg", "mean_length_mm",
    "hatchery", "notes",
]


def fetch_xlsx(species_code: str) -> pd.DataFrame:
    """Download stocking records for a species via the xlsx API."""
    url = f"{BASE}/api/v1/stocking/events_xlsx/"
    params = {"species": species_code}
    print(f"  GET {url}?species={species_code} ...")
    resp = requests.get(url, params=params, timeout=60)
    resp.raise_for_status()

    if len(resp.content) < 500:
        print(f"    Response too small ({len(resp.content)} bytes), likely empty")
        return pd.DataFrame()

    # Row 0 is a title row ("Stocking Data"), row 1 has real headers
    df = pd.read_excel(io.BytesIO(resp.content), engine="openpyxl", header=1)
    print(f"    Got {len(df)} rows, columns: {list(df.columns)}")
    return df


def main():
    all_frames = []

    print("=== Fetching bass stocking from GLFC FSIS ===")
    for code, name in SPECIES.items():
        print(f"\n[{code}] {name}")
        df = fetch_xlsx(code)
        if len(df) > 0:
            df["species_common"] = name
            all_frames.append(df)

    if not all_frames:
        print("\nNo bass stocking records found.")
        empty = pd.DataFrame(columns=KEEP_COLS)
        OUT.parent.mkdir(parents=True, exist_ok=True)
        empty.to_csv(OUT, index=False)
        print(f"Wrote empty CSV to {OUT}")
        return

    combined = pd.concat(all_frames, ignore_index=True)

    # Standardize column names
    col_map = {}
    for col in combined.columns:
        c = col.strip().lower().replace(" ", "_")
        col_map[col] = c
    combined.rename(columns=col_map, inplace=True)

    # Rename API columns to our standard names
    rename = {
        "glfsd_stock_id": "stock_id",
        "agency_code": "agency",
        "_lake": "lake",
        "_strain": "strain",
        "yearclass": "year_class",
        "stock_method": "stocking_method",
        "hatchery_abbrev": "hatchery",
    }
    combined.rename(columns={k: v for k, v in rename.items() if k in combined.columns}, inplace=True)

    # Add lake name
    if "lake" in combined.columns:
        combined["lake_name"] = combined["lake"].map(LAKE_NAMES)

    # Select and reorder columns that exist
    final_cols = [c for c in KEEP_COLS if c in combined.columns]
    combined = combined[final_cols]

    print(f"\n=== Results ===")
    print(f"Total records: {len(combined)}")
    print(f"Species: {combined['species_code'].value_counts().to_dict()}")
    print(f"Lakes: {combined['lake_name'].value_counts().to_dict()}")
    print(f"Year range: {combined['year'].min()} - {combined['year'].max()}")
    print(f"\nColumns: {list(combined.columns)}")
    print(combined.to_string())

    OUT.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(OUT, index=False)
    print(f"\nWrote {len(combined)} rows to {OUT}")


if __name__ == "__main__":
    main()
