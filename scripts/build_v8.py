"""Build v8 dataset: per-survey creel + tournament + enriched features.

Key changes from v7:
- Creel data uses per-survey CPUE targets (temporal variance)
- Morphometry filled from creel Survey_Data + KNN imputation
- Weather imputed from lat/lon/season model where real data unavailable
"""
import json
import os
import ssl
import time
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

BASE = "castline/validation"
OUT_PATH = f"{BASE}/data/assembled/validation_dataset_v8.csv"


def load_tournament_data():
    """Load bassmaster + tourneyx outcomes."""
    bass = pd.read_csv(f"{BASE}/data/raw/historical_outcomes.csv", low_memory=False)
    bass["source"] = "bassmaster"

    files = [
        (f"{BASE}/data/raw/elite_outcomes.csv", "bassmaster"),
        (f"{BASE}/data/raw/tourneyx_outcomes.csv", "tourneyx"),
        (f"{BASE}/data/raw/mlf_outcomes.csv", "bassmaster"),
    ]
    dfs = [bass]
    for path, src in files:
        if os.path.exists(path):
            df = pd.read_csv(path, low_memory=False)
            df["source"] = src
            dfs.append(df)

    combined = pd.concat(dfs, ignore_index=True)
    # Standardize columns
    if "median_weight_lb" not in combined.columns and "median_weight" in combined.columns:
        combined["median_weight_lb"] = combined["median_weight"]

    combined = combined[combined.median_weight_lb.notna()].copy()
    print(f"Tournament events: {len(combined)} ({combined.location.nunique()} locations)")
    return combined


def load_creel_data():
    """Load per-survey creel events."""
    creel = pd.read_csv(f"{BASE}/data/raw/creel_per_survey_events.csv", low_memory=False)
    print(f"Creel events: {len(creel)} ({creel.location.nunique()} locations)")
    return creel


def geocode_locations(df):
    """Add lat/lon from geocode cache."""
    cache_path = f"{BASE}/data/raw/geocode_cache.json"
    with open(cache_path) as f:
        cache = json.load(f)

    # Fill from cache
    for loc in df.location.unique():
        if loc in cache and cache[loc]:
            coords = cache[loc]
            if isinstance(coords, (list, tuple)) and len(coords) >= 2:
                mask = df.location == loc
                if df.loc[mask, "lat"].isna().any():
                    df.loc[mask, "lat"] = coords[0]
                    df.loc[mask, "lon"] = coords[1]
            elif isinstance(coords, dict) and "lat" in coords:
                mask = df.location == loc
                if df.loc[mask, "lat"].isna().any():
                    df.loc[mask, "lat"] = coords["lat"]
                    df.loc[mask, "lon"] = coords["lon"]

    # Fill remaining from creel data (already has lat/lon)
    has_ll = df.lat.notna()
    print(f"Geocoded: {has_ll.sum()}/{len(df)} ({has_ll.mean()*100:.1f}%)")
    return df


def add_morphometry(df):
    """Fill area from creel Survey_Data + KNN imputation."""
    survey = pd.read_csv(f"{BASE}/data/raw/creel/Survey_Data.csv", low_memory=False)
    survey["best_acres"] = survey["Survey_Acres"].fillna(
        survey["NHD_Acres"].fillna(survey["Calc_Acres"].fillna(survey["Reported_Acres"]))
    )

    # Match by waterbody name
    survey["_key"] = (
        survey.Waterbody_Name.str.strip().str.lower()
        + "|"
        + survey.State.str.strip().str.lower()
    )
    wb_area = survey.groupby("_key")["best_acres"].median().to_dict()

    # Build location → key mapping from creel_gnn
    creel_gnn_path = f"{BASE}/data/raw/creel_gnn_locations_expanded.csv"
    if os.path.exists(creel_gnn_path):
        creel_gnn = pd.read_csv(creel_gnn_path, low_memory=False)
        creel_gnn["_key"] = (
            creel_gnn.waterbody_name.str.strip().str.lower()
            + "|"
            + creel_gnn.state.str.strip().str.lower()
        )
        creel_gnn["location"] = (
            creel_gnn.waterbody_name.str.strip() + ", " + creel_gnn.state.str.strip()
        )
        loc_to_key = creel_gnn.set_index("location")["_key"].to_dict()

        filled = 0
        for loc, key in loc_to_key.items():
            if key in wb_area and pd.notna(wb_area[key]):
                mask = (df.location == loc) & df.area_acres.isna()
                cnt = mask.sum()
                if cnt > 0:
                    df.loc[mask, "area_acres"] = wb_area[key]
                    filled += cnt
        print(f"Filled {filled} rows from creel survey area data")

    # Also fill from creel events that already have area
    creel_area = df[df.area_acres.notna()].groupby("location")["area_acres"].first()
    for loc, area in creel_area.items():
        mask = (df.location == loc) & df.area_acres.isna()
        df.loc[mask, "area_acres"] = area

    # KNN imputation for remaining
    loc_data = df.groupby("location").agg(
        lat=("lat", "first"),
        lon=("lon", "first"),
        area_acres=("area_acres", "first"),
    ).reset_index()

    has = loc_data[loc_data.area_acres.notna()]
    missing = loc_data[loc_data.area_acres.isna()]
    if len(has) > 5 and len(missing) > 0:
        X = has[["lat", "lon"]].fillna(0).values
        scaler = StandardScaler().fit(X)
        nn = NearestNeighbors(n_neighbors=5)
        nn.fit(scaler.transform(X))

        for _, row in missing.iterrows():
            lat_val = row.lat if pd.notna(row.lat) else 0
            lon_val = row.lon if pd.notna(row.lon) else 0
            x = scaler.transform([[lat_val, lon_val]])
            dists, idxs = nn.kneighbors(x)
            w = 1 / (dists[0] + 0.01)
            area = np.average(has.iloc[idxs[0]].area_acres.values, weights=w)
            mask = (df.location == row.location) & df.area_acres.isna()
            df.loc[mask, "area_acres"] = area

    print(f"area_acres coverage: {df.area_acres.notna().mean()*100:.1f}%")
    return df


def add_temporal_features(df):
    """Add date-based features."""
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["year"] = df.date.dt.year
    df["month"] = df.date.dt.month
    df["day_of_year"] = df.date.dt.dayofyear
    df["day_number"] = (df.date - pd.Timestamp("2000-01-01")).dt.days
    df["season_sin"] = np.sin(2 * np.pi * df.day_of_year / 365.25)
    df["season_cos"] = np.cos(2 * np.pi * df.day_of_year / 365.25)

    # Day length approximation
    lat_rad = np.radians(df.lat.fillna(35))
    decl = 23.45 * np.sin(np.radians(360 / 365 * (df.day_of_year.fillna(180) - 81)))
    decl_rad = np.radians(decl)
    ha = np.degrees(np.arccos(
        np.clip(-np.tan(lat_rad) * np.tan(decl_rad), -1, 1)
    ))
    df["day_length_hours"] = 2 * ha / 15

    # Moon phase
    ref = pd.Timestamp("2000-01-06")  # Known new moon
    days_since = (df.date - ref).dt.days
    df["moon_phase"] = (days_since % 29.53) / 29.53
    df["moon_phase_sin"] = np.sin(2 * np.pi * df.moon_phase)
    df["moon_phase_cos"] = np.cos(2 * np.pi * df.moon_phase)

    return df


def add_creel_species_features(df):
    """Add species composition from creel data."""
    cpue_path = f"{BASE}/data/raw/creel_cpue_bass.csv"
    if not os.path.exists(cpue_path):
        print("No creel CPUE file found, skipping species features")
        return df

    cpue = pd.read_csv(cpue_path, low_memory=False)

    # Merge by location name matching
    loc_species = {}
    for _, row in cpue.iterrows():
        loc = row.get("location", row.get("waterbody", ""))
        if pd.notna(loc):
            loc_species[loc] = {
                "creel_lmb_ratio": row.get("lmb_ratio", np.nan),
                "creel_smb_ratio": row.get("smb_ratio", np.nan),
                "creel_spotted_ratio": row.get("spotted_ratio", np.nan),
                "creel_cpue_mean": row.get("cpue_mean", np.nan),
            }

    for col in ["creel_lmb_ratio", "creel_smb_ratio", "creel_spotted_ratio", "creel_cpue_mean"]:
        if col not in df.columns:
            df[col] = np.nan

    for loc, species in loc_species.items():
        mask = df.location == loc
        if mask.any():
            for col, val in species.items():
                df.loc[mask & df[col].isna(), col] = val

    # KNN fill for remaining
    has = df[df.creel_lmb_ratio.notna()].groupby("location").first()[["lat", "lon", "creel_lmb_ratio", "creel_smb_ratio", "creel_spotted_ratio"]].reset_index()
    missing_locs = df[df.creel_lmb_ratio.isna()].location.unique()
    if len(has) > 5 and len(missing_locs) > 0:
        X = has[["lat", "lon"]].fillna(0).values
        scaler = StandardScaler().fit(X)
        nn = NearestNeighbors(n_neighbors=5)
        nn.fit(scaler.transform(X))

        loc_ll = df.groupby("location")[["lat", "lon"]].first()
        for loc in missing_locs:
            if loc in loc_ll.index:
                x = scaler.transform([[loc_ll.loc[loc, "lat"] or 0, loc_ll.loc[loc, "lon"] or 0]])
                _, idxs = nn.kneighbors(x)
                for col in ["creel_lmb_ratio", "creel_smb_ratio", "creel_spotted_ratio"]:
                    val = has.iloc[idxs[0]][col].mean()
                    df.loc[df.location == loc, col] = df.loc[df.location == loc, col].fillna(val)

    for col in ["creel_lmb_ratio", "creel_smb_ratio", "creel_spotted_ratio"]:
        print(f"{col} coverage: {df[col].notna().mean()*100:.1f}%")

    return df


def merge_weather(df):
    """Merge Open-Meteo weather cache."""
    cache_path = f"{BASE}/data/raw/openmeteo_weather_cache.csv"
    if not os.path.exists(cache_path):
        print("No weather cache found")
        return df

    weather = pd.read_csv(cache_path, low_memory=False)
    weather["_lat_r"] = weather.lat.round(2)
    weather["_lon_r"] = weather.lon.round(2)
    weather["_date"] = weather.date.astype(str)

    df["_lat_r"] = df.lat.round(2)
    df["_lon_r"] = df.lon.round(2)
    df["_date"] = pd.to_datetime(df.date).dt.strftime("%Y-%m-%d")

    # Weather columns
    om_cols = [c for c in weather.columns if c.startswith("om_")]

    merged = df.merge(
        weather[["_lat_r", "_lon_r", "_date"] + om_cols],
        on=["_lat_r", "_lon_r", "_date"],
        how="left",
        suffixes=("", "_new"),
    )

    # Fill existing NaN om_ columns from merge
    for col in om_cols:
        new_col = col + "_new"
        if new_col in merged.columns:
            if col in merged.columns:
                merged[col] = merged[col].fillna(merged[new_col])
            else:
                merged[col] = merged[new_col]
            merged.drop(columns=[new_col], inplace=True)
        elif col not in merged.columns:
            merged[col] = np.nan

    merged.drop(columns=["_lat_r", "_lon_r", "_date"], inplace=True)
    df = merged

    # Impute missing weather from lat/lon/season model
    from catboost import CatBoostRegressor

    impute_feats = ["lat", "lon", "day_of_year", "month", "season_sin", "season_cos", "year"]
    impute_feats = [f for f in impute_feats if f in df.columns]

    has_weather = df[om_cols[0]].notna() if om_cols else pd.Series(False, index=df.index)
    if has_weather.sum() > 50:
        train_w = df[has_weather]
        impute_w = df[~has_weather]
        for wf in om_cols:
            valid = train_w[wf].notna()
            if valid.sum() < 30:
                continue
            X = train_w.loc[valid, impute_feats].fillna(0)
            y = train_w.loc[valid, wf]
            model = CatBoostRegressor(iterations=200, depth=4, verbose=0, random_seed=42)
            model.fit(X, y)
            X_imp = impute_w[impute_feats].fillna(0)
            df.loc[~has_weather, wf] = model.predict(X_imp)

    for col in om_cols[:3]:
        print(f"{col} coverage: {df[col].notna().mean()*100:.1f}%")

    return df


def merge_usgs(df):
    """Merge USGS sensor data."""
    for path in [f"{BASE}/data/raw/usgs_history_v2.csv", f"{BASE}/data/raw/usgs_history.csv"]:
        if os.path.exists(path):
            usgs = pd.read_csv(path, low_memory=False)
            print(f"USGS data: {len(usgs)} rows from {path}")
            # TODO: merge by site_id and date
            break
    return df


def add_biology_features(df):
    """Add fish biology and interaction features."""
    temp = df.get("om_air_temp_mean", df.get("water_temp_c", pd.Series(np.nan, index=df.index)))

    # Spawn phase estimation
    lat = df.lat.fillna(35)
    spawn_start = 60 + (lat - 25) * 3  # Day of year spawn starts
    spawn_end = spawn_start + 45
    doy = df.day_of_year.fillna(150)
    df["spawn_progress"] = np.clip((doy - spawn_start) / (spawn_end - spawn_start), 0, 1)

    # Water type flags
    loc_lower = df.location.str.lower()
    df["is_lake"] = loc_lower.str.contains("lake|reservoir|pond", na=False).astype(float)
    df["wtype_river"] = loc_lower.str.contains("river|creek|stream", na=False).astype(float)

    return df


def clean_dataset(df):
    """Final cleanup."""
    # Remove rows without target
    df = df[df.median_weight_lb.notna()].copy()

    # Remove rows without coordinates
    df = df[df.lat.notna() & df.lon.notna()].copy()

    # Clip target to reasonable range
    df["median_weight_lb"] = df.median_weight_lb.clip(0.5, 30)

    # Convert date to string
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")

    print(f"\nFinal dataset: {len(df)} rows, {df.location.nunique()} locations")
    print(f"Columns: {len(df.columns)}")
    print(f"Sources: {df.source.value_counts().to_dict()}")
    print(f"Date range: {df.date.min()} to {df.date.max()}")

    return df


def main():
    print("=" * 60)
    print("Building v8 dataset")
    print("=" * 60)

    # 1. Load tournament data
    tournament = load_tournament_data()

    # 2. Load per-survey creel data
    creel = load_creel_data()

    # 3. Combine
    # Standardize columns
    common_cols = ["location", "date", "median_weight_lb", "lat", "lon",
                   "area_acres", "source", "year"]
    for col in common_cols:
        if col not in tournament.columns:
            tournament[col] = np.nan
        if col not in creel.columns:
            creel[col] = np.nan

    df = pd.concat([tournament, creel], ignore_index=True)
    print(f"\nCombined: {len(df)} rows, {df.location.nunique()} locations")

    # 4. Geocode
    df = geocode_locations(df)

    # 5. Add morphometry
    df = add_morphometry(df)

    # 6. Add temporal features
    df = add_temporal_features(df)

    # 7. Add species features
    df = add_creel_species_features(df)

    # 8. Merge weather
    df = merge_weather(df)

    # 9. Add biology features
    df = add_biology_features(df)

    # 10. Clean
    df = clean_dataset(df)

    # 11. Save
    df.to_csv(OUT_PATH, index=False)
    print(f"\nSaved to {OUT_PATH}")


if __name__ == "__main__":
    main()
