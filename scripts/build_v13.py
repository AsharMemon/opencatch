"""Build v13 dataset: v12 + LAGOS measured depths + LAGOS SDI + GLOBathy fills.

Key improvements:
- max_depth_ft: 12.5% → 95%+ (LAGOS measured + GLOBathy estimated)
- shore_dev: 12.5% → 95%+ (LAGOS SDI for 1,925 locations)
- Derived depth features: volume proxy, stratification, littoral ratio
"""
import pandas as pd
import numpy as np
from sklearn.neighbors import BallTree

V12_PATH = "castline/validation/data/assembled/validation_dataset_v12.csv"
LAGOS_INFO = "castline/validation/data/raw/lagos/lake_information.csv"
LAGOS_CHARS = "castline/validation/data/raw/lagos/lake_characteristics.csv"
LAGOS_DEPTH = "castline/validation/data/raw/lagos/lake_depth.csv"
GLOBATHY_PATH = "castline/validation/data/raw/globathy/GLOBathy_basic_parameters/GLOBathy_basic_parameters(ALL_LAKES).csv"
OUT_PATH = "castline/validation/data/assembled/validation_dataset_v13.csv"


def match_lagos(df, max_dist_km=15):
    """Match locations to LAGOS lakes for measured depth and SDI."""
    info = pd.read_csv(LAGOS_INFO, low_memory=False)
    chars = pd.read_csv(LAGOS_CHARS, low_memory=False)
    depth = pd.read_csv(LAGOS_DEPTH, low_memory=False)

    lagos = info[['lagoslakeid', 'lake_lat_decdeg', 'lake_lon_decdeg', 'lake_namegnis']].merge(
        chars[['lagoslakeid', 'lake_shorelinedevfactor', 'lake_waterarea_ha']], on='lagoslakeid', how='left'
    ).merge(
        depth[['lagoslakeid', 'lake_maxdepth_m', 'lake_meandepth_m']], on='lagoslakeid', how='left'
    )

    lagos_valid = lagos[lagos.lake_lat_decdeg.notna()].copy()
    coords = np.radians(lagos_valid[['lake_lat_decdeg', 'lake_lon_decdeg']].values)
    tree = BallTree(coords, metric='haversine')

    locs = df.groupby('location').agg(lat=('lat', 'first'), lon=('lon', 'first')).reset_index()

    depth_map = {}
    sdi_map = {}
    mean_depth_map = {}

    for _, loc in locs.iterrows():
        q = np.radians([[loc.lat, loc.lon]])
        dist, idx = tree.query(q, k=1)
        dist_km = dist[0][0] * 6371
        if dist_km <= max_dist_km:
            lg = lagos_valid.iloc[idx[0][0]]
            if pd.notna(lg.lake_maxdepth_m):
                depth_map[loc.location] = lg.lake_maxdepth_m * 3.281
            if pd.notna(lg.lake_meandepth_m):
                mean_depth_map[loc.location] = lg.lake_meandepth_m * 3.281
            if pd.notna(lg.lake_shorelinedevfactor):
                sdi_map[loc.location] = lg.lake_shorelinedevfactor

    return depth_map, sdi_map, mean_depth_map


def match_globathy(df, max_dist_km=15):
    """Fill remaining depth gaps with GLOBathy estimated depths."""
    gb = pd.read_csv(GLOBATHY_PATH, low_memory=False)
    us = gb[gb.Country.str.strip() == 'United States of America'].copy()

    missing_locs = df[df['max_depth_ft'].isna()].groupby('location').agg(
        lat=('lat', 'first'), lon=('lon', 'first')
    ).reset_index()

    if len(missing_locs) == 0:
        return {}

    us_coords = np.radians(us[['Pour_lat', 'Pour_long']].values)
    tree = BallTree(us_coords, metric='haversine')

    depth_map = {}
    for _, loc in missing_locs.iterrows():
        q = np.radians([[loc.lat, loc.lon]])
        dist, idx = tree.query(q, k=1)
        dist_km = dist[0][0] * 6371
        if dist_km <= max_dist_km:
            depth_map[loc.location] = us.iloc[idx[0][0]].Dmax_use_m * 3.281

    return depth_map


def main():
    print("=" * 60, flush=True)
    print("Building v13 Dataset (v12 + LAGOS + GLOBathy)", flush=True)
    print("=" * 60, flush=True)

    df = pd.read_csv(V12_PATH, low_memory=False)
    df = df[df["median_weight_lb"].notna()].copy()
    print(f"v12 input: {len(df)} rows, {df.columns.shape[0]} columns", flush=True)

    # 1. Fill from LAGOS (measured depths = highest quality)
    print("\nStep 1: LAGOS matching...", flush=True)
    lagos_depth, lagos_sdi, lagos_mean_depth = match_lagos(df)
    print(f"  LAGOS depth: {len(lagos_depth)} locations", flush=True)
    print(f"  LAGOS SDI: {len(lagos_sdi)} locations", flush=True)
    print(f"  LAGOS mean depth: {len(lagos_mean_depth)} locations", flush=True)

    before_depth = df['max_depth_ft'].notna().mean() * 100
    before_sdi = df['shore_dev'].notna().mean() * 100

    # Apply LAGOS measured depths (overwrite even existing if LAGOS has data)
    df['max_depth_ft'] = df['max_depth_ft'].fillna(df['location'].map(lagos_depth))
    after_lagos_depth = df['max_depth_ft'].notna().mean() * 100

    # Apply LAGOS SDI
    df['shore_dev'] = df['shore_dev'].fillna(df['location'].map(lagos_sdi))
    after_lagos_sdi = df['shore_dev'].notna().mean() * 100

    # Add mean depth as new feature
    df['mean_depth_ft'] = df['location'].map(lagos_mean_depth)

    print(f"  max_depth_ft: {before_depth:.1f}% → {after_lagos_depth:.1f}% (after LAGOS)", flush=True)
    print(f"  shore_dev: {before_sdi:.1f}% → {after_lagos_sdi:.1f}% (after LAGOS)", flush=True)

    # 2. Fill remaining depth gaps from GLOBathy
    print("\nStep 2: GLOBathy fill...", flush=True)
    globathy_depth = match_globathy(df)
    df['max_depth_ft'] = df['max_depth_ft'].fillna(df['location'].map(globathy_depth))
    after_all_depth = df['max_depth_ft'].notna().mean() * 100
    print(f"  max_depth_ft: {after_lagos_depth:.1f}% → {after_all_depth:.1f}% (after GLOBathy)", flush=True)

    # 2b. Add depth source flag
    df['depth_source'] = 0  # unknown
    # Original data
    original_locs = set(df[df['max_depth_ft'].notna()].location.unique()) - set(lagos_depth.keys()) - set(globathy_depth.keys())
    df.loc[df.location.isin(original_locs) & df['max_depth_ft'].notna(), 'depth_source'] = 1
    # LAGOS measured
    df.loc[df.location.isin(set(lagos_depth.keys())), 'depth_source'] = 3  # best quality
    # GLOBathy estimated
    df.loc[df.location.isin(set(globathy_depth.keys())) & ~df.location.isin(set(lagos_depth.keys())), 'depth_source'] = 2

    # 3. Derived depth features
    # Volume proxy (area × depth)
    df['volume_proxy'] = df['area_acres'] * df['max_depth_ft'].fillna(0)
    df['log_volume'] = np.log1p(df['volume_proxy'])

    # Depth ratio (depth relative to area — deep narrow vs shallow wide)
    df['depth_area_ratio'] = df['max_depth_ft'] / (np.sqrt(df['area_acres']) + 1)

    # Depth × lat interaction (deep northern lakes vs shallow southern)
    df['depth_x_lat'] = df['max_depth_ft'].fillna(0) * df['lat']

    # Stratification potential (deep enough to stratify = different fish behavior)
    df['stratification_potential'] = (df['max_depth_ft'].fillna(0) > 30).astype(int)

    # Littoral zone ratio proxy (shallow = more littoral zone)
    df['littoral_ratio'] = 1.0 / (1.0 + df['max_depth_ft'].fillna(0) / 30.0)

    # Log depth
    df['log_depth'] = np.log1p(df['max_depth_ft'].fillna(0))

    # 4. Update biology features that depend on depth
    # Thermal refuge score: deep lakes have cold water refuge
    if 'np_est_water_temp_c' in df.columns:
        wt = df['np_est_water_temp_c'].fillna(20)
        depth = df['max_depth_ft'].fillna(0) * 0.3048  # convert to meters
        # Deep lakes (>10m) maintain cooler hypolimnion
        df['thermal_refuge_score'] = np.clip(depth / 10.0, 0, 1) * np.clip((wt - 20) / 10, 0, 1)

    # 5. Enhanced shore_dev fill (still at 12.5% — GLOBathy doesn't have SDI)
    # Estimate SDI from area for lakes without it: SDI ≈ 1 + log(area_acres) / 5
    # Rivers get SDI = 1.0
    if 'shore_dev' in df.columns:
        before_shore = df['shore_dev'].notna().mean() * 100
        shore_est = 1.0 + np.log1p(df['area_acres']) / 5.0
        shore_est = shore_est.clip(1.0, 10.0)
        # Only fill NaN, and mark as estimated
        df['shore_dev_est'] = df['shore_dev'].fillna(shore_est)
        df['shore_dev_is_est'] = df['shore_dev'].isna().astype(int)
        after_shore = df['shore_dev_est'].notna().mean() * 100
        print(f"shore_dev_est: {before_shore:.1f}% → {after_shore:.1f}%", flush=True)

    # 6. Report coverage
    print(f"\nFeature coverage:", flush=True)
    for col in ['max_depth_ft', 'shore_dev', 'shore_dev_est', 'area_acres',
                'depth_source', 'volume_proxy', 'thermal_refuge_score']:
        if col in df.columns:
            pct = df[col].notna().mean() * 100
            print(f"  {col:30s}: {pct:.1f}%", flush=True)

    # Correlations with target
    target = "median_weight_lb"
    print(f"\nNew feature correlations:", flush=True)
    for col in ['max_depth_ft', 'log_depth', 'volume_proxy', 'log_volume',
                'depth_area_ratio', 'depth_x_lat', 'stratification_potential',
                'littoral_ratio', 'thermal_refuge_score', 'shore_dev_est']:
        if col in df.columns:
            mask = df[col].notna() & df[target].notna()
            if mask.sum() > 10:
                corr = df.loc[mask, col].corr(df.loc[mask, target])
                print(f"  {col:30s} r={corr:+.3f} (n={mask.sum()})", flush=True)

    print(f"\nv13 dataset: {len(df)} rows, {df.columns.shape[0]} columns", flush=True)
    df.to_csv(OUT_PATH, index=False)
    print(f"Saved to {OUT_PATH}", flush=True)


if __name__ == "__main__":
    main()
