"""
CPUE Hurdle Model v2 — Enriched with weather + LAGOS morphometry
================================================================
Improvements over v1:
  - LAGOS lake morphometry (depth, area, shoreline development, glaciation)
  - Historical weather from Open-Meteo archive (temp, precip, pressure, wind)
  - Photoperiod / day length
  - More interaction features
  - Effort normalization
"""
import pandas as pd
import numpy as np
from sklearn.neighbors import BallTree
from catboost import CatBoostRegressor, CatBoostClassifier
from sklearn.model_selection import GroupKFold
from sklearn.metrics import r2_score, roc_auc_score, f1_score, mean_absolute_error
import json
import time
import warnings
warnings.filterwarnings('ignore')

# --- PATHS (Vast.ai) ---
CREEL_FISH = "/workspace/castline/raw/FishDataCompiled.csv"
CREEL_SURVEY = "/workspace/castline/raw/Survey_Data.csv"
CREEL_EFFORT = "/workspace/castline/raw/AngEffort_Data.csv"
LAGOS_CHAR = "/workspace/castline/raw/lake_characteristics.csv"
LAGOS_DEPTH = "/workspace/castline/raw/lake_depth.csv"
LAGOS_INFO = "/workspace/castline/raw/lake_information.csv"
EMBEDDINGS_PATH = "/workspace/castline/raw/location_embeddings_geoclip_pca32.csv"
V16_PATH = "/workspace/castline/data/validation_dataset_v16.csv"

SEED = 42
EARTH_RADIUS_KM = 6371.0

BASS_TAXA = [
    'Micropterus salmoides (Largemouth Bass)',
    'Micropterus salmoides (Tournament)',
    'Largemouth Bass',
    'Micropterus dolomieu (Smallmouth Bass)',
    'Micropterus punctulatus (Spotted Bass)',
    'Micropterus (Black Basses)',
    'Micropterus henshalli (Alabama Bass)',
    'Micropterus coosae (Coosa Bass)',
    'Micropterus treculii (Guadalupe Bass)',
    'Micropterus notius (Suwannee Bass)',
]


def build_cpue_dataset():
    """Build survey-level CPUE training dataset from CreelCat."""
    print("="*70)
    print("BUILDING CPUE TRAINING DATASET v2")
    print("="*70)

    fish = pd.read_csv(CREEL_FISH, low_memory=False)
    survey = pd.read_csv(CREEL_SURVEY, low_memory=False)
    effort = pd.read_csv(CREEL_EFFORT, low_memory=False)

    bass = fish[fish['Taxa'].isin(BASS_TAXA)].copy()
    bass['is_lmb'] = bass['Taxa'].str.contains('salmoides|Largemouth', case=False).astype(int)
    bass['is_smb'] = bass['Taxa'].str.contains('dolomieu|Smallmouth Bass', case=False).astype(int)
    bass['is_spotted'] = bass['Taxa'].str.contains('punctulatus|Spotted', case=False).astype(int)

    survey_cols = ['Survey_ID', 'Lat', 'Lon', 'Year', 'Start_Date', 'End_Date', 'Season',
                   'Waterbody_Name', 'WB_Type', 'Reported_Acres', 'Calc_Acres', 'State_Ab',
                   'Survey_Type', 'Duration', 'Start_Month', 'End_Month', 'County']
    existing_cols = [c for c in survey_cols if c in survey.columns]
    bass = bass.merge(survey[existing_cols], on='Survey_ID', how='left')
    bass = bass[bass['Lat'].notna() & bass['Lon'].notna()].copy()

    effort_cols = ['Survey_ID', 'Effort_Hours', 'Effort_Outings', 'Effort_Anglers',
                   'Effort_Hours_Per_Day', 'Effort_Hours_Per_Acre']
    existing_effort = [c for c in effort_cols if c in effort.columns]
    bass = bass.merge(effort[existing_effort], on='Survey_ID', how='left')

    print(f"Bass with location: {len(bass)}")

    # Aggregate to survey level
    survey_agg = bass.groupby('Survey_ID').agg(
        lat=('Lat', 'first'),
        lon=('Lon', 'first'),
        year=('Year', 'first'),
        start_date=('Start_Date', 'first'),
        end_date=('End_Date', 'first'),
        season=('Season', 'first'),
        start_month=('Start_Month', 'first'),
        end_month=('End_Month', 'first'),
        waterbody=('Waterbody_Name', 'first'),
        wb_type=('WB_Type', 'first'),
        acres=('Reported_Acres', 'first'),
        calc_acres=('Calc_Acres', 'first'),
        state=('State_Ab', 'first'),
        county=('County', 'first'),
        cpue_hour=('Catch_Per_Hour', 'max'),
        cpue_day=('Catch_Per_Day', 'max'),
        total_catch=('Catch', 'sum'),
        total_harvest=('Harvest', 'sum'),
        total_release=('Release', 'sum'),
        has_lmb=('is_lmb', 'max'),
        has_smb=('is_smb', 'max'),
        has_spotted=('is_spotted', 'max'),
        n_bass_species=('Taxa', 'nunique'),
        effort_hours=('Effort_Hours', 'first'),
        effort_hours_per_day=('Effort_Hours_Per_Day', 'first'),
    ).reset_index()

    # Derived features
    survey_agg['acres_best'] = survey_agg['calc_acres'].fillna(survey_agg['acres'])
    survey_agg['log_acres'] = np.log1p(pd.to_numeric(survey_agg['acres_best'], errors='coerce').fillna(0))
    survey_agg['is_river'] = survey_agg['wb_type'].str.contains('River|Stream', case=False, na=False).astype(int)
    survey_agg['is_lake'] = (~survey_agg['wb_type'].str.contains('River|Stream', case=False, na=False)).astype(int)
    survey_agg['has_catch'] = (survey_agg['cpue_hour'] > 0).astype(int)

    # Temporal features
    survey_agg['start_month_num'] = pd.to_numeric(survey_agg['start_month'], errors='coerce')
    try:
        dates = pd.to_datetime(survey_agg['start_date'], errors='coerce')
        survey_agg['month'] = dates.dt.month.fillna(survey_agg['start_month_num'])
        survey_agg['day_of_year'] = dates.dt.dayofyear
    except:
        survey_agg['month'] = survey_agg['start_month_num']
        survey_agg['day_of_year'] = np.nan

    survey_agg['month_sin'] = np.sin(2 * np.pi * survey_agg['month'] / 12)
    survey_agg['month_cos'] = np.cos(2 * np.pi * survey_agg['month'] / 12)

    # Photoperiod (approximate from latitude and day of year)
    lat_rad = np.radians(survey_agg['lat'].clip(-60, 60))
    doy = survey_agg['day_of_year'].fillna(180)  # default to mid-year
    # Solar declination
    declination = 23.45 * np.sin(np.radians(360 / 365 * (doy - 81)))
    decl_rad = np.radians(declination)
    # Hour angle
    cos_ha = -np.tan(lat_rad) * np.tan(decl_rad)
    cos_ha = cos_ha.clip(-1, 1)
    ha = np.degrees(np.arccos(cos_ha))
    survey_agg['day_length_hours'] = 2 * ha / 15
    survey_agg['photoperiod_change'] = survey_agg.groupby(
        survey_agg['lat'].round(0))['day_length_hours'].diff().fillna(0)

    # Latitude features
    survey_agg['abs_lat'] = survey_agg['lat'].abs()
    survey_agg['lat_band'] = pd.cut(survey_agg['lat'], bins=[25, 33, 38, 42, 50],
                                     labels=[0, 1, 2, 3]).astype(float)

    # Effort features (safe ones — hours per day is not denominator of cpue_hour)
    survey_agg['log_effort_hours'] = np.log1p(
        pd.to_numeric(survey_agg['effort_hours'], errors='coerce').fillna(0))
    survey_agg['log_effort_per_day'] = np.log1p(
        pd.to_numeric(survey_agg['effort_hours_per_day'], errors='coerce').fillna(0))

    # Year features
    survey_agg['decade'] = (survey_agg['year'] // 10) * 10
    survey_agg['years_since_2000'] = survey_agg['year'] - 2000

    valid = survey_agg[survey_agg['cpue_hour'].notna()].copy()
    print(f"\nValid surveys with CPUE: {len(valid)}")
    print(f"  States: {valid['state'].nunique()}, Water bodies: {valid.groupby(['lat','lon']).ngroups}")

    return valid


def add_lagos_features(df):
    """Match to LAGOS lake database for morphometry."""
    print("\n--- Adding LAGOS morphometry ---")
    try:
        chars = pd.read_csv(LAGOS_CHAR, low_memory=False)
        print(f"  LAGOS characteristics: {len(chars)} lakes")

        # Get lat/lon from lake_information
        try:
            info = pd.read_csv(LAGOS_INFO, low_memory=False)
            lat_col = [c for c in info.columns if 'lat' in c.lower()]
            lon_col = [c for c in info.columns if 'lon' in c.lower()]
            if lat_col and lon_col:
                lagos = chars.merge(info[['lagoslakeid'] + lat_col + lon_col],
                                    on='lagoslakeid', how='left')
                lagos = lagos.rename(columns={lat_col[0]: 'lat', lon_col[0]: 'lon'})
            else:
                print("  No lat/lon in lake_information — trying lake_link")
                link = pd.read_csv("/workspace/castline/raw/lake_link.csv", low_memory=False)
                lat_col = [c for c in link.columns if 'lat' in c.lower()]
                lon_col = [c for c in link.columns if 'lon' in c.lower()]
                if lat_col and lon_col:
                    lagos = chars.merge(link[['lagoslakeid'] + lat_col + lon_col],
                                        on='lagoslakeid', how='left')
                    lagos = lagos.rename(columns={lat_col[0]: 'lat', lon_col[0]: 'lon'})
                else:
                    print("  No lat/lon found — skipping LAGOS")
                    return df
        except FileNotFoundError:
            print("  lake_information.csv not found — skipping LAGOS")
            return df

        lagos = lagos[lagos['lat'].notna() & lagos['lon'].notna()]
        print(f"  LAGOS with coordinates: {len(lagos)}")

        if len(lagos) == 0:
            return df

        # BallTree match
        tree = BallTree(np.radians(lagos[['lat', 'lon']].values), metric='haversine')
        query = np.radians(df[['lat', 'lon']].values)
        dist, idx = tree.query(query, k=1)
        dist_km = dist[:, 0] * EARTH_RADIUS_KM

        matched = dist_km < 10  # 10km match radius
        print(f"  Matched within 10km: {matched.sum()}/{len(df)} ({matched.mean()*100:.1f}%)")

        # Extract features
        feature_map = {
            'lake_waterarea_ha': 'lagos_area_ha',
            'lake_shorelinedevfactor': 'lagos_sdi',
            'lake_meanwidth_m': 'lagos_mean_width_m',
            'lake_mbgrect_length_m': 'lagos_length_m',
            'lake_mbgrect_arearatio': 'lagos_area_ratio',
        }

        # Check for glaciation column
        for col in chars.columns:
            if 'glaciat' in col.lower():
                feature_map[col] = 'lagos_glaciated'
            if 'connect' in col.lower() and 'class' in col.lower():
                feature_map[col] = 'lagos_connectivity'

        for src, dst in feature_map.items():
            if src in lagos.columns:
                vals = lagos.iloc[idx[:, 0]][src].values
                if lagos[src].dtype == 'object':
                    # Encode categorical
                    df[dst] = np.where(matched, pd.Categorical(vals).codes.astype(float), np.nan)
                else:
                    df[dst] = np.where(matched, vals.astype(float), np.nan)

        df['lagos_match_dist_km'] = dist_km

        # Try depth data
        try:
            depth = pd.read_csv(LAGOS_DEPTH, low_memory=False)
            depth_col = [c for c in depth.columns if 'max' in c.lower() and 'depth' in c.lower()]
            mean_depth_col = [c for c in depth.columns if 'mean' in c.lower() and 'depth' in c.lower()]

            if depth_col:
                lagos_depth = lagos.merge(depth[['lagoslakeid'] + depth_col + mean_depth_col],
                                          on='lagoslakeid', how='left')
                df['lagos_max_depth_m'] = np.where(
                    matched, lagos_depth.iloc[idx[:, 0]][depth_col[0]].values.astype(float), np.nan)
                if mean_depth_col:
                    df['lagos_mean_depth_m'] = np.where(
                        matched, lagos_depth.iloc[idx[:, 0]][mean_depth_col[0]].values.astype(float), np.nan)
                print(f"  Depth data matched: {df['lagos_max_depth_m'].notna().sum()}")
        except (FileNotFoundError, Exception) as e:
            print(f"  Depth data: {e}")

        # Derived features
        if 'lagos_area_ha' in df.columns and 'lagos_max_depth_m' in df.columns:
            df['lagos_log_area'] = np.log1p(df['lagos_area_ha'])
            df['lagos_depth_area_ratio'] = df['lagos_max_depth_m'] / np.log1p(df['lagos_area_ha'])
            df['lagos_volume_proxy'] = df['lagos_area_ha'] * df['lagos_max_depth_m'].fillna(0) / 3

    except FileNotFoundError:
        print("  LAGOS data not available — skipping")

    return df


def add_geoclip_features(df):
    """Match to GeoCLIP location embeddings."""
    print("\n--- Adding GeoCLIP embeddings ---")
    try:
        emb = pd.read_csv(EMBEDDINGS_PATH, low_memory=False)
        emb_cols = [c for c in emb.columns if c.startswith('geoclip_')]
        if len(emb_cols) > 0 and 'lat' in emb.columns:
            tree = BallTree(np.radians(emb[['lat', 'lon']].values), metric='haversine')
            query = np.radians(df[['lat', 'lon']].values)
            dist, idx = tree.query(query, k=1)
            dist_km = dist[:, 0] * EARTH_RADIUS_KM

            matched = dist_km < 50  # 50km for embeddings — broader radius for CreelCat
            for col in emb_cols[:16]:  # Top 16 PCA components
                df[col] = np.where(matched, emb.iloc[idx[:, 0]][col].values, np.nan)
            print(f"  GeoCLIP matched: {matched.sum()}/{len(df)} ({matched.mean()*100:.1f}%)")
    except FileNotFoundError:
        print("  GeoCLIP embeddings not available — skipping")
    return df


def add_weather_features(df):
    """Add historical weather from Open-Meteo Archive API."""
    print("\n--- Adding historical weather (Open-Meteo Archive) ---")

    # We'll batch-fetch weather for unique (lat, lon, month, year) combinations
    # to avoid redundant API calls
    import urllib.request

    # Group by approximate location + year + month for weather
    df['weather_key'] = (df['lat'].round(1).astype(str) + '_' +
                         df['lon'].round(1).astype(str) + '_' +
                         df['year'].astype(str) + '_' +
                         df['month'].fillna(6).astype(int).astype(str))

    unique_keys = df['weather_key'].unique()
    print(f"  Unique weather lookups needed: {len(unique_keys)}")

    if len(unique_keys) > 500:
        print(f"  Too many API calls needed ({len(unique_keys)})")
        print(f"  Using latitude-based climate proxies instead")

        # Climate proxies from latitude + month
        # These capture the major climate gradients without API calls
        lat = df['lat']
        month = df['month'].fillna(6)

        # Approximate average temperature by latitude and month
        # Using simplified sinusoidal model
        base_temp = 30 - 0.5 * lat.abs()  # warmer near equator
        seasonal = 10 * np.sin(2 * np.pi * (month - 4) / 12)  # peak in July
        lat_seasonal = seasonal * (lat.abs() / 45)  # stronger seasonality at higher latitudes
        df['est_air_temp_c'] = base_temp + lat_seasonal

        # Growing degree days proxy
        df['est_gdd'] = np.maximum(0, df['est_air_temp_c'] - 10).cumsum()  # rough GDD

        # Estimated water temperature (lags air by ~2-4 weeks)
        lag_seasonal = 10 * np.sin(2 * np.pi * (month - 5) / 12)
        lat_lag_seasonal = lag_seasonal * (lat.abs() / 45)
        df['est_water_temp_c'] = base_temp + lat_lag_seasonal - 2  # water slightly cooler

        # Thermal comfort for bass (optimal 18-24°C)
        df['bass_thermal_comfort'] = 1 - np.minimum(
            np.abs(df['est_water_temp_c'] - 21) / 10, 1)

        # Spawn proximity (based on water temp reaching 15-18°C)
        df['spawn_proximity'] = np.exp(-0.5 * ((df['est_water_temp_c'] - 16.5) / 3) ** 2)

        print(f"  Added 5 climate proxy features")
        return df

    # If manageable number of calls, use Open-Meteo
    weather_cache = {}
    fetched = 0
    errors = 0

    for key in unique_keys:
        parts = key.split('_')
        lat, lon, year, month = float(parts[0]), float(parts[1]), int(parts[2]), int(parts[3])

        # Construct date range for month
        start = f"{year}-{month:02d}-01"
        end_day = 28 if month == 2 else 30 if month in [4, 6, 9, 11] else 31
        end = f"{year}-{month:02d}-{end_day}"

        url = (f"https://archive-api.open-meteo.com/v1/archive?"
               f"latitude={lat}&longitude={lon}&start_date={start}&end_date={end}"
               f"&daily=temperature_2m_max,temperature_2m_min,temperature_2m_mean,"
               f"precipitation_sum,windspeed_10m_max,pressure_msl_mean"
               f"&timezone=auto")

        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())
                daily = data.get('daily', {})
                weather_cache[key] = {
                    'wx_temp_mean': np.nanmean(daily.get('temperature_2m_mean', [np.nan])),
                    'wx_temp_max': np.nanmax(daily.get('temperature_2m_max', [np.nan])),
                    'wx_temp_min': np.nanmin(daily.get('temperature_2m_min', [np.nan])),
                    'wx_precip_total': np.nansum(daily.get('precipitation_sum', [0])),
                    'wx_wind_max': np.nanmax(daily.get('windspeed_10m_max', [np.nan])),
                    'wx_pressure_mean': np.nanmean(daily.get('pressure_msl_mean', [np.nan])),
                }
                fetched += 1
        except Exception as e:
            errors += 1
            weather_cache[key] = {}

        if fetched % 50 == 0 and fetched > 0:
            print(f"  Fetched {fetched}/{len(unique_keys)} weather records...")
        time.sleep(0.05)  # Rate limiting

    print(f"  Weather fetched: {fetched}, errors: {errors}")

    # Map weather back to dataframe
    for col in ['wx_temp_mean', 'wx_temp_max', 'wx_temp_min', 'wx_precip_total',
                'wx_wind_max', 'wx_pressure_mean']:
        df[col] = df['weather_key'].map(lambda k: weather_cache.get(k, {}).get(col, np.nan))

    # Derived weather features
    if 'wx_temp_mean' in df.columns:
        df['wx_temp_range'] = df['wx_temp_max'] - df['wx_temp_min']
        df['bass_thermal_comfort'] = 1 - np.minimum(
            np.abs(df['wx_temp_mean'] - 21) / 10, 1)
        df['spawn_proximity'] = np.exp(-0.5 * ((df['wx_temp_mean'] - 16.5) / 3) ** 2)

    return df


def add_interaction_features(df):
    """Add interaction features between key variables."""
    print("\n--- Adding interaction features ---")
    n_before = len([c for c in df.columns if df[c].dtype in ['float64', 'int64']])

    # Lat × month interaction (seasonality varies by latitude)
    if 'month_sin' in df.columns:
        df['lat_x_month_sin'] = df['lat'] * df['month_sin']
        df['lat_x_month_cos'] = df['lat'] * df['month_cos']

    # Area × depth interactions
    if 'lagos_max_depth_m' in df.columns:
        df['depth_x_area'] = df['lagos_max_depth_m'] * df['log_acres']

    # Thermal × month (spawn timing)
    if 'bass_thermal_comfort' in df.columns:
        df['comfort_x_month'] = df['bass_thermal_comfort'] * df['month_sin']
        df['comfort_x_lat'] = df['bass_thermal_comfort'] * df['abs_lat']

    # Year × lat (changing patterns over time by region)
    df['year_x_lat'] = df['years_since_2000'] * df['lat']

    # Species composition interactions
    df['multi_species'] = (df['n_bass_species'] > 1).astype(int)
    df['lmb_only'] = ((df['has_lmb'] == 1) & (df['has_smb'] == 0) & (df['has_spotted'] == 0)).astype(int)

    n_after = len([c for c in df.columns if df[c].dtype in ['float64', 'int64']])
    print(f"  Added {n_after - n_before} interaction features")
    return df


def get_feature_cols(df):
    """Get clean modeling features."""
    exclude = {'Survey_ID', 'cpue_hour', 'cpue_day', 'has_catch', 'total_catch',
               'total_harvest', 'total_release', 'waterbody', 'wb_type', 'state',
               'start_date', 'end_date', 'season', 'start_month', 'end_month',
               'start_month_num', 'county',
               'acres', 'calc_acres', 'acres_best', 'weather_key',
               'release_pct', 'effort_hours', 'effort_outings', 'effort_anglers',
               'loc_group', 'decade'}
    features = [c for c in df.columns if c not in exclude
                and df[c].dtype in ['float64', 'int64', 'float32', 'int32']
                and df[c].notna().sum() > len(df) * 0.05]
    return features


def train_hurdle_model(df):
    """Train and evaluate the two-part hurdle model."""
    print("\n" + "="*70)
    print("TRAINING HURDLE MODEL v2")
    print("="*70)

    features = get_feature_cols(df)
    print(f"Features: {len(features)}")
    for i in range(0, len(features), 10):
        print(f"  {features[i:i+10]}")

    df['loc_group'] = df.apply(lambda r: f"{r['lat']:.2f}_{r['lon']:.2f}", axis=1)
    groups = df['loc_group'].values
    n_splits = min(5, df['loc_group'].nunique())
    gkf = GroupKFold(n_splits=n_splits)

    # Part 1: Catch Probability
    print(f"\n--- Part 1: Catch Probability ---")
    y_binary = df['has_catch'].values
    auc_scores, f1_scores = [], []

    for fold, (train_idx, val_idx) in enumerate(gkf.split(df, y_binary, groups)):
        clf = CatBoostClassifier(
            iterations=500, depth=5, learning_rate=0.05,
            l2_leaf_reg=5, random_seed=SEED, verbose=0,
            early_stopping_rounds=30, auto_class_weights='Balanced'
        )
        clf.fit(df.iloc[train_idx][features], y_binary[train_idx],
                eval_set=(df.iloc[val_idx][features], y_binary[val_idx]))

        pred_proba = clf.predict_proba(df.iloc[val_idx][features])[:, 1]
        auc = roc_auc_score(y_binary[val_idx], pred_proba) if len(np.unique(y_binary[val_idx])) > 1 else 0
        f1 = f1_score(y_binary[val_idx], (pred_proba > 0.5).astype(int))
        auc_scores.append(auc)
        f1_scores.append(f1)
        print(f"  Fold {fold+1}: AUC={auc:.4f}  F1={f1:.4f}")

    print(f"  Mean AUC: {np.mean(auc_scores):.4f} ± {np.std(auc_scores):.4f}")

    # Part 2: Positive CPUE
    print(f"\n--- Part 2: Positive CPUE Regression ---")
    positive = df[df['has_catch'] == 1].copy()
    y_cpue = np.log1p(positive['cpue_hour'].values)
    groups_pos = positive['loc_group'].values
    n_splits_pos = min(5, positive['loc_group'].nunique())
    gkf_pos = GroupKFold(n_splits=n_splits_pos)

    r2_scores, mae_scores = [], []

    for fold, (train_idx, val_idx) in enumerate(gkf_pos.split(positive, y_cpue, groups_pos)):
        reg = CatBoostRegressor(
            iterations=1500, depth=7, learning_rate=0.03,
            l2_leaf_reg=3, random_seed=SEED, verbose=0,
            early_stopping_rounds=100, subsample=0.8
        )
        reg.fit(positive.iloc[train_idx][features], y_cpue[train_idx],
                eval_set=(positive.iloc[val_idx][features], y_cpue[val_idx]))

        pred = reg.predict(positive.iloc[val_idx][features])
        r2 = r2_score(y_cpue[val_idx], pred)
        mae = mean_absolute_error(np.expm1(y_cpue[val_idx]), np.expm1(pred))
        r2_scores.append(r2)
        mae_scores.append(mae)
        print(f"  Fold {fold+1}: R²={r2:.4f}  MAE={mae:.4f} fish/hour")

    print(f"  Mean R²:  {np.mean(r2_scores):.4f} ± {np.std(r2_scores):.4f}")
    print(f"  Mean MAE: {np.mean(mae_scores):.4f} ± {np.std(mae_scores):.4f}")

    # Feature importance
    print(f"\n--- Feature Importance ---")
    reg_full = CatBoostRegressor(
        iterations=1500, depth=7, learning_rate=0.03,
        l2_leaf_reg=3, random_seed=SEED, verbose=0
    )
    reg_full.fit(positive[features], y_cpue)

    imp = pd.Series(reg_full.feature_importances_, index=features).sort_values(ascending=False)
    print(f"\n  Top 20 features (Positive CPUE):")
    for feat, val in imp.head(20).items():
        print(f"    {feat:>30}: {val:.2f}%")

    # Cross-reference with tournaments
    print("\n" + "="*70)
    print("CROSS-REFERENCE: CPUE vs Tournament Weights")
    print("="*70)
    try:
        tourn = pd.read_csv(V16_PATH, low_memory=False)
        tourn_locs = tourn.groupby(['lat', 'lon']).agg(
            mean_weight=('median_weight_lb', 'mean'),
            n_events=('median_weight_lb', 'count')
        ).reset_index()

        creel_locs = df.groupby(['lat', 'lon']).agg(
            mean_cpue=('cpue_hour', 'mean')
        ).reset_index()

        tree = BallTree(np.radians(creel_locs[['lat', 'lon']].values), metric='haversine')
        dist, idx = tree.query(np.radians(tourn_locs[['lat', 'lon']].values), k=1)
        dist_km = dist[:, 0] * EARTH_RADIUS_KM

        matched = dist_km < 25
        if matched.sum() > 10:
            tourn_locs_matched = tourn_locs[matched].copy()
            tourn_locs_matched['creel_cpue'] = creel_locs.iloc[idx[matched, 0]]['mean_cpue'].values
            corr = tourn_locs_matched['mean_weight'].corr(tourn_locs_matched['creel_cpue'])
            print(f"  weight ↔ CPUE correlation: r={corr:.4f} (n={matched.sum()})")
    except Exception as e:
        print(f"  Cross-reference failed: {e}")

    return {'auc': np.mean(auc_scores), 'r2': np.mean(r2_scores), 'mae': np.mean(mae_scores)}


def main():
    df = build_cpue_dataset()
    df = add_lagos_features(df)
    df = add_geoclip_features(df)
    df = add_weather_features(df)
    df = add_interaction_features(df)

    print(f"\nFinal dataset: {len(df)} rows × {len(df.columns)} columns")
    print(f"Numeric features: {len(get_feature_cols(df))}")

    results = train_hurdle_model(df)

    print("\n" + "="*70)
    print(f"FINAL RESULTS")
    print(f"  Catch Probability AUC: {results['auc']:.4f}")
    print(f"  Positive CPUE R²:     {results['r2']:.4f}")
    print(f"  Positive CPUE MAE:    {results['mae']:.4f} fish/hour")
    print("="*70)


if __name__ == '__main__':
    main()
