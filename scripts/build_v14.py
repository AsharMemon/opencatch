"""Build v14 dataset: v13 + NASA POWER 7-day weather lag features + stocking data.

Key additions:
- 7-day temperature trends and moving averages (per Tanaka et al.)
- 3-day and 7-day rolling means for all weather parameters
- Temperature trend slopes (warming/cooling)
- Fish stocking density as location feature
"""
import pandas as pd
import numpy as np
from sklearn.neighbors import BallTree

V13_PATH = "castline/validation/data/assembled/validation_dataset_v13.csv"
NASA_7DAY_PATH = "castline/validation/data/raw/nasa_power_7day.csv"
FL_STOCKING_PATH = "castline/validation/data/raw/florida_fwc_stocking.csv"
OUT_PATH = "castline/validation/data/assembled/validation_dataset_v14.csv"


def compute_lag_features(nasa_7day):
    """Compute lag features from 7-day NASA POWER windows."""
    features = []

    for (event_date, event_lat, event_lon), group in nasa_7day.groupby(
            ['event_date', 'event_lat', 'event_lon']):
        group = group.sort_values('date')
        if len(group) < 3:
            continue

        row = {
            'event_date': event_date,
            'event_lat': event_lat,
            'event_lon': event_lon,
        }

        # Temperature features
        if 'np_temp_mean_c' in group.columns:
            temps = group['np_temp_mean_c'].dropna()
            if len(temps) >= 3:
                # 3-day and 7-day means
                row['lag_temp_3d_mean'] = temps.iloc[-3:].mean()
                row['lag_temp_7d_mean'] = temps.mean()
                # Temperature trend (slope of last 7 days)
                if len(temps) >= 5:
                    x = np.arange(len(temps))
                    slope = np.polyfit(x, temps.values, 1)[0]
                    row['lag_temp_trend'] = slope  # degrees/day
                # Temperature range over 7 days
                row['lag_temp_range'] = temps.max() - temps.min()
                # Warming vs cooling
                row['lag_warming'] = max(0, temps.iloc[-1] - temps.iloc[0])
                row['lag_cooling'] = max(0, temps.iloc[0] - temps.iloc[-1])
                # Day-before temperature
                row['lag_temp_1d'] = temps.iloc[-2] if len(temps) >= 2 else np.nan
                # 3-day trend
                if len(temps) >= 4:
                    row['lag_temp_3d_trend'] = np.polyfit(np.arange(3), temps.iloc[-3:].values, 1)[0]

        # Max temperature features
        if 'np_temp_max_c' in group.columns:
            tmax = group['np_temp_max_c'].dropna()
            if len(tmax) >= 3:
                row['lag_tmax_3d_mean'] = tmax.iloc[-3:].mean()
                row['lag_tmax_7d_mean'] = tmax.mean()

        # Min temperature features
        if 'np_temp_min_c' in group.columns:
            tmin = group['np_temp_min_c'].dropna()
            if len(tmin) >= 3:
                row['lag_tmin_3d_mean'] = tmin.iloc[-3:].mean()
                row['lag_tmin_7d_mean'] = tmin.mean()
                # Diurnal range stability
                if 'np_temp_max_c' in group.columns:
                    diurnal = group['np_temp_max_c'] - group['np_temp_min_c']
                    row['lag_diurnal_mean'] = diurnal.mean()
                    row['lag_diurnal_std'] = diurnal.std()

        # Precipitation features
        if 'np_precip_mm' in group.columns:
            precip = group['np_precip_mm'].dropna()
            if len(precip) >= 3:
                row['lag_precip_3d_sum'] = precip.iloc[-3:].sum()
                row['lag_precip_7d_sum'] = precip.sum()
                row['lag_rain_days'] = (precip > 1.0).sum()

        # Wind features
        if 'np_wind_2m_ms' in group.columns:
            wind = group['np_wind_2m_ms'].dropna()
            if len(wind) >= 3:
                row['lag_wind_3d_mean'] = wind.iloc[-3:].mean()
                row['lag_wind_7d_mean'] = wind.mean()
                row['lag_wind_max'] = wind.max()

        # Pressure features
        if 'np_pressure_kpa' in group.columns:
            press = group['np_pressure_kpa'].dropna()
            if len(press) >= 3:
                row['lag_pressure_3d_mean'] = press.iloc[-3:].mean()
                row['lag_pressure_trend'] = press.iloc[-1] - press.iloc[0] if len(press) >= 2 else np.nan
                row['lag_pressure_range'] = press.max() - press.min()

        # Cloud/Solar features
        if 'np_cloud_pct' in group.columns:
            cloud = group['np_cloud_pct'].dropna()
            if len(cloud) >= 3:
                row['lag_cloud_3d_mean'] = cloud.iloc[-3:].mean()
                row['lag_cloud_7d_mean'] = cloud.mean()

        # Humidity features
        if 'np_humidity_pct' in group.columns:
            humid = group['np_humidity_pct'].dropna()
            if len(humid) >= 3:
                row['lag_humidity_3d_mean'] = humid.iloc[-3:].mean()

        features.append(row)

    return pd.DataFrame(features)


def integrate_stocking(df, stocking_path, max_dist_km=15):
    """Add fish stocking density as a location-level feature."""
    try:
        stock = pd.read_csv(stocking_path, low_memory=False)
    except FileNotFoundError:
        print(f"  Stocking file not found: {stocking_path}", flush=True)
        return df

    # Filter to bass species
    bass = stock[stock['Species'].str.contains('Bass|bass', na=False)].copy()
    if len(bass) == 0:
        return df

    # Aggregate by waterbody
    bass_agg = bass.groupby('Waterbody').agg(
        total_bass_stocked=('Fish_Stocked', 'sum'),
        stock_lat=('Latitude', 'first'),
        stock_lon=('Longitude', 'first'),
        n_stockings=('Fish_Stocked', 'count'),
    ).reset_index()

    # Match to dataset locations by proximity
    locs = df.groupby('location').agg(lat=('lat', 'first'), lon=('lon', 'first')).reset_index()

    stock_coords = np.radians(bass_agg[['stock_lat', 'stock_lon']].dropna().values)
    if len(stock_coords) == 0:
        return df

    tree = BallTree(stock_coords, metric='haversine')
    stock_map = {}
    valid_idx = bass_agg[['stock_lat', 'stock_lon']].dropna().index

    for _, loc in locs.iterrows():
        q = np.radians([[loc.lat, loc.lon]])
        dist, idx = tree.query(q, k=1)
        dist_km = dist[0][0] * 6371
        if dist_km <= max_dist_km:
            row = bass_agg.iloc[valid_idx[idx[0][0]]]
            stock_map[loc.location] = row['total_bass_stocked']

    if stock_map:
        df['bass_stocking_density'] = df['location'].map(stock_map)
        df['has_stocking_data'] = df['bass_stocking_density'].notna().astype(int)
        print(f"  Stocking: {len(stock_map)} locations matched", flush=True)

    return df


def main():
    print("=" * 60, flush=True)
    print("Building v14 Dataset (v13 + Weather Lags + Stocking)", flush=True)
    print("=" * 60, flush=True)

    df = pd.read_csv(V13_PATH, low_memory=False)
    df = df[df["median_weight_lb"].notna()].copy()
    print(f"v13 input: {len(df)} rows, {df.columns.shape[0]} columns", flush=True)

    # 1. NASA POWER 7-day lag features
    print("\nStep 1: NASA POWER 7-day lag features...", flush=True)
    try:
        nasa = pd.read_csv(NASA_7DAY_PATH, low_memory=False)
        print(f"  Raw NASA 7-day data: {len(nasa)} daily rows", flush=True)

        lag_df = compute_lag_features(nasa)
        print(f"  Computed lag features for {len(lag_df)} events", flush=True)
        print(f"  Lag feature columns: {[c for c in lag_df.columns if c.startswith('lag_')]}", flush=True)

        # Match to dataset
        df['_date_key'] = df['date']
        df['_lat_key'] = df['lat'].round(4)
        df['_lon_key'] = df['lon'].round(4)
        lag_df['_date_key'] = lag_df['event_date']
        lag_df['_lat_key'] = lag_df['event_lat'].round(4)
        lag_df['_lon_key'] = lag_df['event_lon'].round(4)

        lag_cols = [c for c in lag_df.columns if c.startswith('lag_')]
        lag_merge = lag_df[['_date_key', '_lat_key', '_lon_key'] + lag_cols].drop_duplicates(
            subset=['_date_key', '_lat_key', '_lon_key'])

        before = len(df)
        df = df.merge(lag_merge, on=['_date_key', '_lat_key', '_lon_key'], how='left')
        df.drop(columns=['_date_key', '_lat_key', '_lon_key'], inplace=True)
        assert len(df) == before, f"Merge changed row count: {before} → {len(df)}"

        coverage = df[lag_cols[0]].notna().mean() * 100 if lag_cols else 0
        print(f"  Lag feature coverage: {coverage:.1f}%", flush=True)

    except FileNotFoundError:
        print("  NASA 7-day file not found, skipping", flush=True)
    except Exception as e:
        print(f"  Error: {e}", flush=True)

    # 2. Fish stocking data
    print("\nStep 2: Fish stocking data...", flush=True)
    df = integrate_stocking(df, FL_STOCKING_PATH)

    # 3. Report
    print(f"\nv14 dataset: {len(df)} rows, {df.columns.shape[0]} columns", flush=True)

    # Correlations for new features
    target = "median_weight_lb"
    lag_cols = [c for c in df.columns if c.startswith('lag_')]
    if lag_cols:
        print(f"\nLag feature correlations:", flush=True)
        for col in lag_cols:
            mask = df[col].notna() & df[target].notna()
            if mask.sum() > 10:
                corr = df.loc[mask, col].corr(df.loc[mask, target])
                print(f"  {col:30s} r={corr:+.3f} (n={mask.sum()})", flush=True)

    df.to_csv(OUT_PATH, index=False)
    print(f"\nSaved to {OUT_PATH}", flush=True)


if __name__ == "__main__":
    main()
