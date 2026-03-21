"""Enrich dataset with richer site-level inputs from LAGOS + computed features.

Addresses catastrophic LOO locations by adding:
1. LAGOS connectivity class (Drainage, Isolated, Headwater)
2. LAGOS ecoregion (EPA nutrient, Omernik III, Bailey)
3. LAGOS glaciation status
4. LAGOS upstream lake network (count + area)
5. LAGOS elevation
6. LAGOS HUC watershed
7. Tournament intensity (computed from our data)
8. Location data density (proxy for angling pressure)
9. Source diversity (creel vs tournament mix)
"""
import pandas as pd
import numpy as np
from sklearn.neighbors import BallTree
from pathlib import Path
import warnings

warnings.filterwarnings("ignore")

def p(msg=""):
    print(msg, flush=True)

BASE_DIR = Path("/Users/Ashar/Documents/fish")
V15_PATH = BASE_DIR / "castline/validation/data/assembled/validation_dataset_v15.csv"
LAGOS_INFO = BASE_DIR / "castline/validation/data/raw/lagos/lake_information.csv"
LAGOS_CHARS = BASE_DIR / "castline/validation/data/raw/lagos/lake_characteristics.csv"
OUTPUT_PATH = BASE_DIR / "castline/validation/data/assembled/validation_dataset_v15.csv"  # overwrite

MAX_MATCH_KM = 15  # Max distance for LAGOS matching


def match_lagos_rich(df):
    """Match locations to LAGOS and extract rich site features."""
    p("Matching LAGOS rich site features...")

    info = pd.read_csv(LAGOS_INFO, low_memory=False)
    chars = pd.read_csv(LAGOS_CHARS, low_memory=False)

    # Merge info + chars on lagoslakeid
    lagos = info.merge(chars, on="lagoslakeid", how="inner")
    p(f"  LAGOS merged: {len(lagos)} lakes")

    # Get unique locations from our dataset
    locs = df.groupby("location")[["lat", "lon"]].first().reset_index()

    # Build BallTree from LAGOS
    lagos_coords = np.radians(lagos[["lake_lat_decdeg", "lake_lon_decdeg"]].values)
    tree = BallTree(lagos_coords, metric="haversine")

    # Query for each location
    loc_coords = np.radians(locs[["lat", "lon"]].values)
    dists, idxs = tree.query(loc_coords, k=1)
    dists_km = dists[:, 0] * 6371  # Earth radius in km

    # Features to extract
    matches = []
    for i, (_, loc_row) in enumerate(locs.iterrows()):
        if dists_km[i] > MAX_MATCH_KM:
            matches.append({"location": loc_row["location"]})
            continue

        lago = lagos.iloc[idxs[i, 0]]
        match = {
            "location": loc_row["location"],
            "lagos_match_dist_km": dists_km[i],

            # Connectivity
            "lagos_connectivity": lago.get("lake_connectivity_permanent", np.nan),
            "lagos_connectivity_fluct": 1 if lago.get("lake_connectivity_fluctuates") == "Y" else 0,

            # Upstream network
            "lagos_upstream_lakes_n": lago.get("lake_lakes4ha_upstream_n", np.nan),
            "lagos_upstream_lakes_ha": lago.get("lake_lakes4ha_upstream_ha", np.nan),
            "lagos_upstream_10ha_n": lago.get("lake_lakes10ha_upstream_n", np.nan),

            # Glaciation
            "lagos_glaciated": 1 if lago.get("lake_glaciatedlatewisc") == "Glaciated" else 0,

            # Elevation
            "lagos_elevation_m": lago.get("lake_elevation_m", np.nan),

            # Shape features
            "lagos_mbg_arearatio": lago.get("lake_mbgrect_arearatio", np.nan),
            "lagos_mean_width_m": lago.get("lake_meanwidth_m", np.nan),
            "lagos_mbg_length_m": lago.get("lake_mbgconhull_length_m", np.nan),
            "lagos_elongation": (
                lago.get("lake_mbgconhull_length_m", 0) /
                max(lago.get("lake_mbgconhull_width_m", 1), 1)
            ),

            # Ecoregion IDs (will be encoded)
            "lagos_epanutr_zone": lago.get("epanutr_zoneid", np.nan),
            "lagos_omernik3_zone": lago.get("omernik3_zoneid", np.nan),
            "lagos_bailey_zone": lago.get("bailey_zoneid", np.nan),
            "lagos_wwf_zone": lago.get("wwf_zoneid", np.nan),
            "lagos_neon_zone": lago.get("neon_zoneid", np.nan),

            # State (for regional patterns)
            "lagos_state": lago.get("lake_centroidstate", np.nan),

            # HUC watershed (4-digit for broad region)
            "lagos_hu4": lago.get("hu4_zoneid", np.nan),
            "lagos_hu8": lago.get("hu8_zoneid", np.nan),

            # Water area (LAGOS measured, may differ from our area_acres)
            "lagos_waterarea_ha": lago.get("lake_waterarea_ha", np.nan),
            "lagos_island_pct": (
                lago.get("lake_islandarea_ha", 0) /
                max(lago.get("lake_totalarea_ha", 1), 0.01) * 100
            ),
        }
        matches.append(match)

    match_df = pd.DataFrame(matches)

    # Encode categorical features as numeric
    # Connectivity class
    conn_map = {"Drainage": 1, "DrainageLk": 2, "Headwater": 3, "Isolated": 4}
    match_df["lagos_connectivity_enc"] = match_df["lagos_connectivity"].map(conn_map).fillna(0)

    # Ecoregion encoding (ordinal by zone ID number)
    for col in ["lagos_epanutr_zone", "lagos_omernik3_zone", "lagos_bailey_zone",
                "lagos_wwf_zone", "lagos_neon_zone", "lagos_hu4", "lagos_hu8"]:
        if col in match_df.columns:
            # Extract numeric part
            match_df[f"{col}_enc"] = (
                match_df[col].astype(str)
                .str.extract(r'(\d+)', expand=False)
                .astype(float)
            )

    # State encoding (ordinal by alphabetical order)
    states = sorted(match_df["lagos_state"].dropna().unique())
    state_map = {s: i+1 for i, s in enumerate(states)}
    match_df["lagos_state_enc"] = match_df["lagos_state"].map(state_map).fillna(0)

    # Drop raw categorical columns (keep encoded versions)
    drop_cats = ["lagos_connectivity", "lagos_epanutr_zone", "lagos_omernik3_zone",
                 "lagos_bailey_zone", "lagos_wwf_zone", "lagos_neon_zone",
                 "lagos_hu4", "lagos_hu8", "lagos_state"]
    match_df.drop(columns=[c for c in drop_cats if c in match_df.columns], inplace=True)

    # Merge to main dataframe
    matched = match_df["lagos_match_dist_km"].notna().sum()
    p(f"  Matched: {matched}/{len(locs)} locations ({matched/len(locs)*100:.1f}%)")

    before = len(df)
    df = df.merge(match_df, on="location", how="left")
    assert len(df) == before

    # Report coverage
    new_cols = [c for c in match_df.columns if c != "location"]
    for c in sorted(new_cols):
        if c in df.columns:
            cov = df[c].notna().mean() * 100
            if cov > 0:
                corr = df[c].corr(df["median_weight_lb"]) if df[c].dtype in [float, np.float64] else np.nan
                p(f"    {c:35s} cov={cov:5.1f}%  r={corr:+.3f}" if not np.isnan(corr) else f"    {c:35s} cov={cov:5.1f}%")

    return df


def compute_dataset_features(df):
    """Compute features derivable from our own dataset."""
    p("\nComputing dataset-derived site features...")

    # Tournament intensity: how many events at this location?
    loc_counts = df.groupby("location").size().rename("loc_total_events")
    df = df.merge(loc_counts, on="location", how="left")

    # Source diversity: mix of creel vs tournament
    if "source" in df.columns:
        source_counts = df.groupby("location")["source"].nunique().rename("loc_source_diversity")
        df = df.merge(source_counts, on="location", how="left")

        # Fraction from tournaments (vs creel)
        is_tournament = df["source"].isin(["bassmaster", "tourneyx", "mlf"])
        tournament_frac = df.groupby("location").apply(
            lambda g: g["source"].isin(["bassmaster", "tourneyx", "mlf"]).mean()
        ).rename("loc_tournament_fraction")
        df = df.merge(tournament_frac, on="location", how="left")

    # Year range (how long has this location been observed?)
    if "year" in df.columns:
        year_range = df.groupby("location")["year"].agg(
            loc_year_range=lambda x: x.max() - x.min(),
            loc_first_year="min",
            loc_last_year="max",
        )
        df = df.merge(year_range, on="location", how="left")

    # Seasonal diversity: how many seasons represented?
    if "month" in df.columns:
        season_div = df.groupby("location")["month"].nunique().rename("loc_month_diversity")
        df = df.merge(season_div, on="location", how="left")

    # Target statistics (NOT leaky for LOO because these are location-level)
    # But we need to be careful - for LOO, the held-out location won't have these
    # So mark these as loc_rolling-like features to exclude from LOO
    loc_cv = df.groupby("location")["median_weight_lb"].agg(
        loc_target_cv=lambda x: x.std() / x.mean() if x.mean() > 0 else 0,
    )
    df = df.merge(loc_cv, on="location", how="left")

    new_cols = ["loc_total_events", "loc_source_diversity", "loc_tournament_fraction",
                "loc_year_range", "loc_first_year", "loc_last_year",
                "loc_month_diversity", "loc_target_cv"]

    for c in new_cols:
        if c in df.columns:
            cov = df[c].notna().mean() * 100
            corr = df[c].corr(df["median_weight_lb"])
            p(f"    {c:35s} cov={cov:5.1f}%  r={corr:+.3f}")

    return df


def analyze_catastrophic_locations(df):
    """Check how the new features look for catastrophic LOO locations."""
    p("\n" + "=" * 60)
    p("Catastrophic LOO location analysis with new features:")
    p("=" * 60)

    catastrophic = ["Sabine River", "Lake St. Clair", "St. Johns River",
                    "Huntley, IL", "Lake Istokpoga"]

    for loc_name in catastrophic:
        mask = df.location.str.contains(loc_name, na=False)
        if not mask.any():
            continue
        sub = df[mask]
        loc = sub["location"].iloc[0]

        p(f"\n  {loc}:")
        p(f"    n={len(sub)}, target_mean={sub.median_weight_lb.mean():.2f}, target_std={sub.median_weight_lb.std():.2f}")

        # Key distinguishing features
        for feat in ["lagos_connectivity_enc", "lagos_glaciated", "lagos_elevation_m",
                     "lagos_upstream_lakes_n", "lagos_elongation", "lagos_waterarea_ha",
                     "lagos_omernik3_zone_enc", "lagos_epanutr_zone_enc",
                     "loc_total_events", "loc_tournament_fraction",
                     "gdd_calibrated", "photoperiod_hrs"]:
            if feat in sub.columns:
                val = sub[feat].iloc[0]
                global_med = df[feat].median()
                p(f"    {feat:35s} = {val:8.2f}  (global median: {global_med:.2f})")


# ═══════════════════════════════════════
# MAIN
# ═══════════════════════════════════════
p("=" * 60)
p("Enriching v15 with site-level features")
p("=" * 60)

df = pd.read_csv(V15_PATH, low_memory=False)
df = df[df["median_weight_lb"].notna()].copy()
p(f"Input: {len(df)} rows, {df.shape[1]} columns")

# 1. LAGOS rich features
df = match_lagos_rich(df)

# 2. Dataset-derived features
df = compute_dataset_features(df)

# 3. Analyze catastrophic locations
analyze_catastrophic_locations(df)

# Save
df.to_csv(OUTPUT_PATH, index=False)
p(f"\n✓ Enriched v15: {len(df)} rows, {df.shape[1]} columns")
