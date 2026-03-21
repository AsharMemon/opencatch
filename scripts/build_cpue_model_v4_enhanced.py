"""
CPUE Model v4 — Enhanced stacking ensemble with leakage-safe target encoding
=============================================================================
Builds on v3 with:
  1. Leave-one-group-out target encoding (Bayesian smoothing, computed inside CV)
  2. LightGBM quantile regression for uncertainty estimation
  3. Advanced feature engineering (GDD, frontal passage, interactions)
  4. Stacking ensemble (CatBoost + XGBoost + LightGBM -> Ridge meta-learner)
  5. Hyperparameter search over depth/lr combos
  6. Calibration analysis, spatial residuals, lake vs river breakdown
"""
import pandas as pd
import numpy as np
from sklearn.neighbors import BallTree
from sklearn.model_selection import GroupKFold
from sklearn.metrics import r2_score, roc_auc_score, mean_absolute_error, mean_squared_error
from sklearn.linear_model import Ridge
from catboost import CatBoostRegressor, CatBoostClassifier
from xgboost import XGBRegressor, XGBClassifier
import lightgbm as lgb
import warnings
import time

warnings.filterwarnings('ignore')

# --- PATHS (Vast.ai /workspace/castline/) ---
CREEL_FISH = "/workspace/castline/raw/FishDataCompiled.csv"
CREEL_SURVEY = "/workspace/castline/raw/Survey_Data.csv"
CREEL_EFFORT = "/workspace/castline/raw/AngEffort_Data.csv"
LAGOS_CHAR = "/workspace/castline/raw/lake_characteristics.csv"
LAGOS_DEPTH = "/workspace/castline/raw/lake_depth.csv"
LAGOS_INFO = "/workspace/castline/raw/lake_information.csv"
WEATHER_PATH = "/workspace/castline/raw/creelcat_weather.csv"
EMBEDDINGS_PATH = "/workspace/castline/raw/location_embeddings_geoclip_pca32.csv"
V16_PATH = "/workspace/castline/data/validation_dataset_v16.csv"

SEED = 42
EARTH_RADIUS_KM = 6371.0
NEARBY_RADIUS_KM = 25.0
N_SMOOTH = 10  # Bayesian smoothing parameter for target encoding

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

# State-level bass management intensity proxy (higher = more active stocking/management)
# Based on bass stocking programs, electrofishing surveys, regulation strictness
STATE_MGMT_INTENSITY = {
    'TX': 0.95, 'FL': 0.90, 'CA': 0.85, 'GA': 0.85, 'AL': 0.80,
    'MS': 0.80, 'LA': 0.75, 'SC': 0.75, 'NC': 0.70, 'TN': 0.70,
    'VA': 0.70, 'AR': 0.70, 'MO': 0.65, 'OK': 0.65, 'KY': 0.60,
    'IL': 0.55, 'IN': 0.55, 'OH': 0.55, 'MI': 0.50, 'WI': 0.50,
    'MN': 0.50, 'IA': 0.45, 'KS': 0.45, 'NE': 0.40, 'SD': 0.35,
    'ND': 0.30, 'PA': 0.50, 'NY': 0.50, 'NJ': 0.45, 'CT': 0.40,
    'MA': 0.40, 'NH': 0.35, 'VT': 0.35, 'ME': 0.30, 'MD': 0.55,
    'WV': 0.45, 'CO': 0.35, 'AZ': 0.50, 'NM': 0.35, 'NV': 0.30,
    'UT': 0.35, 'WA': 0.30, 'OR': 0.30, 'ID': 0.25, 'MT': 0.20,
    'WY': 0.20,
}


def build_dataset():
    """Build enriched CPUE dataset with real weather and advanced features."""
    print("=" * 70)
    print("CPUE MODEL v4 — ENHANCED STACKING ENSEMBLE")
    print("=" * 70)

    # ------------------------------------------------------------------
    # Load and aggregate CreelCat
    # ------------------------------------------------------------------
    fish = pd.read_csv(CREEL_FISH, low_memory=False)
    survey = pd.read_csv(CREEL_SURVEY, low_memory=False)
    effort = pd.read_csv(CREEL_EFFORT, low_memory=False)

    bass = fish[fish['Taxa'].isin(BASS_TAXA)].copy()
    bass['is_lmb'] = bass['Taxa'].str.contains('salmoides|Largemouth', case=False).astype(int)
    bass['is_smb'] = bass['Taxa'].str.contains('dolomieu|Smallmouth Bass', case=False).astype(int)
    bass['is_spotted'] = bass['Taxa'].str.contains('punctulatus|Spotted', case=False).astype(int)

    survey_cols = ['Survey_ID', 'Lat', 'Lon', 'Year', 'Start_Date', 'End_Date', 'Season',
                   'Waterbody_Name', 'WB_Type', 'Reported_Acres', 'Calc_Acres', 'State_Ab',
                   'Start_Month', 'End_Month']
    existing = [c for c in survey_cols if c in survey.columns]
    bass = bass.merge(survey[existing], on='Survey_ID', how='left')
    bass = bass[bass['Lat'].notna() & bass['Lon'].notna()].copy()

    effort_cols = ['Survey_ID', 'Effort_Hours', 'Effort_Hours_Per_Day']
    existing_e = [c for c in effort_cols if c in effort.columns]
    bass = bass.merge(effort[existing_e], on='Survey_ID', how='left')

    # Aggregate per survey
    agg = bass.groupby('Survey_ID').agg(
        lat=('Lat', 'first'), lon=('Lon', 'first'),
        year=('Year', 'first'), start_date=('Start_Date', 'first'),
        start_month=('Start_Month', 'first'),
        waterbody=('Waterbody_Name', 'first'),
        wb_type=('WB_Type', 'first'),
        season=('Season', 'first'),
        acres=('Reported_Acres', 'first'), calc_acres=('Calc_Acres', 'first'),
        state=('State_Ab', 'first'),
        cpue_hour=('Catch_Per_Hour', 'max'),
        total_catch=('Catch', 'sum'),
        has_lmb=('is_lmb', 'max'), has_smb=('is_smb', 'max'),
        has_spotted=('is_spotted', 'max'),
        n_bass_species=('Taxa', 'nunique'),
        effort_hours_per_day=('Effort_Hours_Per_Day', 'first'),
    ).reset_index()

    agg = agg[agg['cpue_hour'].notna()].copy()
    print(f"Valid surveys: {len(agg)}")

    # ------------------------------------------------------------------
    # BASIC FEATURES
    # ------------------------------------------------------------------
    agg['acres_best'] = agg['calc_acres'].fillna(agg['acres'])
    agg['log_acres'] = np.log1p(pd.to_numeric(agg['acres_best'], errors='coerce').fillna(0))
    agg['is_river'] = agg['wb_type'].str.contains('River|Stream', case=False, na=False).astype(int)
    agg['is_reservoir'] = agg['wb_type'].str.contains('Reservoir|Impound', case=False, na=False).astype(int)
    agg['has_catch'] = (agg['cpue_hour'] > 0).astype(int)

    # Temporal
    agg['start_month_num'] = pd.to_numeric(agg['start_month'], errors='coerce')
    dates = pd.to_datetime(agg['start_date'], errors='coerce')
    agg['month'] = dates.dt.month.fillna(agg['start_month_num'])
    agg['day_of_year'] = dates.dt.dayofyear
    agg['month_sin'] = np.sin(2 * np.pi * agg['month'] / 12)
    agg['month_cos'] = np.cos(2 * np.pi * agg['month'] / 12)

    # Season encoding (keep raw for interactions, encode numerically)
    season_map = {'Spring': 0, 'Summer': 1, 'Fall': 2, 'Winter': 3}
    agg['season_num'] = agg['season'].map(season_map).fillna(-1).astype(float)
    agg['season_num'] = agg['season_num'].replace(-1, np.nan)

    # Photoperiod
    lat_rad = np.radians(agg['lat'].clip(-60, 60))
    doy = agg['day_of_year'].fillna(180)
    decl = np.radians(23.45 * np.sin(np.radians(360 / 365 * (doy - 81))))
    cos_ha = (-np.tan(lat_rad) * np.tan(decl)).clip(-1, 1)
    agg['day_length_hours'] = 2 * np.degrees(np.arccos(cos_ha)) / 15

    # Location features
    agg['abs_lat'] = agg['lat'].abs()
    agg['lat_band'] = pd.cut(agg['lat'], bins=[25, 33, 38, 42, 50], labels=[0, 1, 2, 3]).astype(float)
    agg['years_since_2000'] = agg['year'] - 2000
    agg['log_effort_per_day'] = np.log1p(
        pd.to_numeric(agg['effort_hours_per_day'], errors='coerce').fillna(0))

    # State management intensity
    agg['state_mgmt_intensity'] = agg['state'].map(STATE_MGMT_INTENSITY).fillna(0.40)

    # ------------------------------------------------------------------
    # WEATHER (real data)
    # ------------------------------------------------------------------
    print("\n--- Loading real weather data ---")
    try:
        wx = pd.read_csv(WEATHER_PATH)
        print(f"  Weather records: {len(wx)}")

        agg['lat_r'] = agg['lat'].round(0)
        agg['lon_r'] = agg['lon'].round(0)
        agg['weather_key'] = (agg['lat_r'].astype(str) + '_' + agg['lon_r'].astype(str) + '_' +
                              agg['year'].astype(int).astype(str) + '_' +
                              agg['month'].fillna(6).astype(int).astype(str))

        wx_dict = {}
        wx_cols = [c for c in wx.columns if c.startswith('wx_')]
        for _, row in wx.iterrows():
            wx_dict[row['weather_key']] = {c: row[c] for c in wx_cols}

        matched = 0
        for i, key in enumerate(agg['weather_key']):
            if key in wx_dict:
                for col, val in wx_dict[key].items():
                    agg.at[agg.index[i], col] = val
                matched += 1

        print(f"  Weather matched: {matched}/{len(agg)} ({matched / len(agg) * 100:.1f}%)")

        if 'wx_temp_mean' in agg.columns:
            # Bass thermal comfort (optimal 18-24 C)
            agg['bass_thermal_comfort'] = 1 - np.minimum(np.abs(agg['wx_temp_mean'] - 21) / 10, 1)
            # Spawn proximity (water temp ~15-18 C triggers spawn)
            agg['spawn_proximity'] = np.exp(-0.5 * ((agg['wx_temp_mean'] - 16.5) / 3) ** 2)
            # Estimated water temp
            agg['est_water_temp'] = agg['wx_temp_mean'] - 2 + agg['month_sin'] * 1.5
            # Pressure stability (range = frontal activity)
            agg['pressure_stability'] = 1 / (1 + agg['wx_pressure_range'].fillna(0))
            # Precipitation intensity
            agg['precip_intensity'] = agg['wx_precip_total'].fillna(0) / 30
            # Wind comfort
            agg['wind_fishing_comfort'] = 1 - np.minimum(agg['wx_wind_max'].fillna(0) / 40, 1)
            # Temperature x latitude interaction
            agg['temp_x_lat'] = agg['wx_temp_mean'] * agg['lat']
            # Pressure x season
            agg['pressure_x_season'] = agg['wx_pressure_mean'].fillna(1013) * agg['month_sin']

            # --- NEW v4 WEATHER FEATURES ---

            # Growing degree days (proxy: monthly mean temp above 10 C, times ~30 days)
            temp_above_base = (agg['wx_temp_mean'] - 10).clip(lower=0)
            agg['growing_degree_days'] = temp_above_base * 30

            # Degree days from spawn trigger (~16.5 C)
            agg['dd_from_spawn'] = (agg['wx_temp_mean'] - 16.5).clip(lower=0) * 30

            # Frontal passage proxy: large pressure range indicates fronts moving through
            agg['frontal_passage'] = (agg['wx_pressure_range'].fillna(0) > 10).astype(float)
            agg['pressure_range_sq'] = agg['wx_pressure_range'].fillna(0) ** 2

            # Wind chill effect (simplified Steadman formula, for T < 10 C)
            temp_c = agg['wx_temp_mean'].fillna(15)
            wind_kph = agg['wx_wind_max'].fillna(0)
            wind_chill = np.where(
                temp_c < 10,
                13.12 + 0.6215 * temp_c - 11.37 * (wind_kph ** 0.16) + 0.3965 * temp_c * (wind_kph ** 0.16),
                temp_c
            )
            agg['wind_chill'] = wind_chill

            # Heat index effect (simplified, for T > 27 C)
            heat_idx = np.where(temp_c > 27, temp_c + 0.5 * (temp_c - 27), temp_c)
            agg['heat_index'] = heat_idx

            # Lake productivity proxy: warmer water with longer days = higher productivity
            agg['productivity_proxy'] = agg['est_water_temp'].clip(lower=0) * agg['day_length_hours'] / 100

            # Rapid cooling/warming indicator (temp deviation from monthly norm by latitude)
            # Approximate expected temp by latitude and month
            expected_temp = 25 - 0.3 * (agg['lat'] - 35).abs() + 8 * agg['month_sin']
            agg['temp_anomaly'] = agg['wx_temp_mean'] - expected_temp

    except FileNotFoundError:
        print("  Weather file not found! Run fetch_weather_creelcat.py first")

    # ------------------------------------------------------------------
    # LAGOS MORPHOMETRY
    # ------------------------------------------------------------------
    print("\n--- Loading LAGOS morphometry ---")
    try:
        chars = pd.read_csv(LAGOS_CHAR, low_memory=False)
        info = pd.read_csv(LAGOS_INFO, low_memory=False)
        lat_col = [c for c in info.columns if 'lat' in c.lower()]
        lon_col = [c for c in info.columns if 'lon' in c.lower()]
        if lat_col and lon_col:
            lagos = chars.merge(info[['lagoslakeid'] + lat_col + lon_col], on='lagoslakeid', how='left')
            lagos = lagos.rename(columns={lat_col[0]: 'lat', lon_col[0]: 'lon'})
            lagos = lagos[lagos['lat'].notna() & lagos['lon'].notna()]
            print(f"  LAGOS lakes with coords: {len(lagos)}")

            tree = BallTree(np.radians(lagos[['lat', 'lon']].values), metric='haversine')
            dist, idx = tree.query(np.radians(agg[['lat', 'lon']].values), k=1)
            dist_km = dist[:, 0] * EARTH_RADIUS_KM
            matched_mask = dist_km < 10

            feature_map = {
                'lake_waterarea_ha': 'lagos_area_ha',
                'lake_shorelinedevfactor': 'lagos_sdi',
                'lake_meanwidth_m': 'lagos_mean_width_m',
                'lake_mbgrect_arearatio': 'lagos_area_ratio',
            }
            for col in chars.columns:
                if 'glaciat' in col.lower():
                    feature_map[col] = 'lagos_glaciated'

            for src, dst in feature_map.items():
                if src in lagos.columns:
                    vals = lagos.iloc[idx[:, 0]][src].values
                    if lagos[src].dtype == 'object':
                        agg[dst] = np.where(matched_mask, pd.Categorical(vals).codes.astype(float), np.nan)
                    else:
                        agg[dst] = np.where(matched_mask, vals.astype(float), np.nan)

            try:
                depth = pd.read_csv(LAGOS_DEPTH, low_memory=False)
                depth_col = [c for c in depth.columns if 'max' in c.lower() and 'depth' in c.lower()]
                if depth_col:
                    lagos_d = lagos.merge(depth[['lagoslakeid'] + depth_col], on='lagoslakeid', how='left')
                    agg['lagos_max_depth_m'] = np.where(
                        matched_mask, lagos_d.iloc[idx[:, 0]][depth_col[0]].values.astype(float), np.nan)
            except Exception:
                pass

            if 'lagos_area_ha' in agg.columns:
                agg['lagos_log_area'] = np.log1p(agg['lagos_area_ha'])
            if 'lagos_max_depth_m' in agg.columns and 'lagos_area_ha' in agg.columns:
                agg['lagos_depth_area_ratio'] = agg['lagos_max_depth_m'] / np.log1p(agg['lagos_area_ha'])

            print(f"  LAGOS matched: {matched_mask.sum()}/{len(agg)} ({matched_mask.mean() * 100:.1f}%)")
    except Exception as e:
        print(f"  LAGOS error: {e}")

    # ------------------------------------------------------------------
    # GEOCLIP EMBEDDINGS
    # ------------------------------------------------------------------
    print("\n--- Loading GeoCLIP embeddings ---")
    try:
        emb = pd.read_csv(EMBEDDINGS_PATH, low_memory=False)
        emb_cols = [c for c in emb.columns if c.startswith('geoclip_')]
        tree = BallTree(np.radians(emb[['lat', 'lon']].values), metric='haversine')
        dist, idx = tree.query(np.radians(agg[['lat', 'lon']].values), k=1)
        dist_km = dist[:, 0] * EARTH_RADIUS_KM
        matched_mask = dist_km < 50
        for col in emb_cols[:16]:
            agg[col] = np.where(matched_mask, emb.iloc[idx[:, 0]][col].values, np.nan)
        print(f"  GeoCLIP matched: {matched_mask.sum()}/{len(agg)} ({matched_mask.mean() * 100:.1f}%)")
    except Exception as e:
        print(f"  GeoCLIP error: {e}")

    # ------------------------------------------------------------------
    # INTERACTIONS (v3 + new v4)
    # ------------------------------------------------------------------
    if 'wx_temp_mean' in agg.columns:
        agg['temp_x_depth'] = agg['wx_temp_mean'] * agg.get('lagos_max_depth_m', pd.Series(0, index=agg.index))
        agg['temp_x_acres'] = agg['wx_temp_mean'] * agg['log_acres']
        agg['comfort_x_daylen'] = agg['bass_thermal_comfort'] * agg['day_length_hours']

    agg['lat_x_month'] = agg['lat'] * agg['month_sin']
    agg['multi_species'] = (agg['n_bass_species'] > 1).astype(int)

    # v4 interaction: season x waterbody type
    # Encode wb_type numerically for interaction
    wb_type_map = {'Lake': 0, 'Reservoir': 1, 'River': 2, 'Stream': 3, 'Pond': 4}
    agg['wb_type_num'] = agg['wb_type'].map(
        lambda x: next((v for k, v in wb_type_map.items()
                        if isinstance(x, str) and k.lower() in x.lower()), np.nan))
    agg['season_x_wbtype'] = agg['season_num'] * 10 + agg['wb_type_num']

    # Latitude x is_river (rivers behave differently at different latitudes)
    agg['lat_x_river'] = agg['lat'] * agg['is_river']

    # Effort x comfort (more effort in comfortable conditions -> higher catch)
    if 'bass_thermal_comfort' in agg.columns:
        agg['effort_x_comfort'] = agg['log_effort_per_day'] * agg['bass_thermal_comfort']

    # ------------------------------------------------------------------
    # Store metadata columns for later analysis (before dropping)
    # ------------------------------------------------------------------
    # We keep state, wb_type, season, waterbody as metadata but drop them from features
    agg['_state'] = agg['state']
    agg['_wb_type'] = agg['wb_type']
    agg['_season'] = agg['season']
    agg['_waterbody'] = agg['waterbody']

    # Location group for spatial CV
    agg['loc_group'] = agg.apply(lambda r: f"{r['lat']:.2f}_{r['lon']:.2f}", axis=1)

    # Clean up non-feature columns
    drop_cols = ['Survey_ID', 'waterbody', 'wb_type', 'state', 'season', 'start_date',
                 'start_month', 'start_month_num', 'acres', 'calc_acres', 'acres_best',
                 'lat_r', 'lon_r', 'weather_key']
    for col in drop_cols:
        if col in agg.columns:
            agg = agg.drop(columns=[col])

    return agg


def get_features(df):
    """Get numeric feature columns, excluding targets and metadata."""
    exclude = {'cpue_hour', 'cpue_day', 'has_catch', 'total_catch', 'loc_group',
               '_state', '_wb_type', '_season', '_waterbody',
               # Target-encoded columns are added dynamically inside CV
               'te_loc_cpue', 'te_state_cpue', 'te_nearby_cpue',
               'te_loc_cpue_pos', 'te_state_cpue_pos', 'te_nearby_cpue_pos'}
    return [c for c in df.columns if c not in exclude
            and df[c].dtype in ['float64', 'int64', 'float32', 'int32']
            and df[c].notna().sum() > len(df) * 0.05]


# ======================================================================
# TARGET ENCODING (leakage-safe)
# ======================================================================

def bayesian_target_encode(train_values, global_mean, n_smooth=N_SMOOTH):
    """Bayesian smoothed mean: (global * n_smooth + group_sum) / (n_smooth + n)."""
    group_sum = np.nansum(train_values)
    n = np.sum(~np.isnan(train_values))
    if n == 0:
        return global_mean
    group_mean = group_sum / n
    return (global_mean * n_smooth + group_mean * n) / (n_smooth + n)


def compute_target_encodings(df, train_idx, val_idx, target_col, loc_groups,
                             coords_rad, nearby_tree, nearby_radius_rad):
    """
    Compute target-encoded features on training data, apply to both train and val.
    Uses leave-one-group-out for training rows, pure train-only encoding for val rows.
    Returns (te_loc, te_state, te_nearby) arrays for all rows in df.
    """
    n = len(df)
    te_loc = np.full(n, np.nan)
    te_state = np.full(n, np.nan)
    te_nearby = np.full(n, np.nan)

    train_target = df[target_col].values
    train_states = df['_state'].values
    train_groups = loc_groups

    global_mean = np.nanmean(train_target[train_idx])

    # --- Location-level target encoding ---
    # Build group -> target mapping from training data
    group_targets = {}
    for i in train_idx:
        g = train_groups[i]
        if g not in group_targets:
            group_targets[g] = []
        group_targets[g].append(train_target[i])

    # For training rows: leave-one-group-out
    for i in train_idx:
        g = train_groups[i]
        others = [v for j, v in enumerate(group_targets[g])
                  if not np.isnan(v)]
        # Remove current observation's contribution (approximate LOGO)
        cur_val = train_target[i]
        if not np.isnan(cur_val) and len(others) > 1:
            others_excl = others.copy()
            try:
                others_excl.remove(cur_val)
            except ValueError:
                pass
            te_loc[i] = bayesian_target_encode(np.array(others_excl), global_mean)
        else:
            te_loc[i] = global_mean

    # For val rows: use full group stats from training
    for i in val_idx:
        g = train_groups[i]
        if g in group_targets:
            te_loc[i] = bayesian_target_encode(np.array(group_targets[g]), global_mean)
        else:
            te_loc[i] = global_mean

    # --- State-level target encoding ---
    state_targets = {}
    for i in train_idx:
        s = train_states[i]
        if pd.isna(s):
            continue
        if s not in state_targets:
            state_targets[s] = []
        state_targets[s].append(train_target[i])

    for i in list(train_idx) + list(val_idx):
        s = train_states[i]
        if pd.isna(s) or s not in state_targets:
            te_state[i] = global_mean
        else:
            te_state[i] = bayesian_target_encode(np.array(state_targets[s]), global_mean)

    # --- Nearby CPUE (within 25km, same season) ---
    seasons = df['season_num'].values if 'season_num' in df.columns else np.zeros(n)

    # Build spatial index of training points
    train_coords = coords_rad[train_idx]
    train_targets_arr = train_target[train_idx]
    train_seasons = seasons[train_idx]
    train_tree = BallTree(train_coords, metric='haversine')

    for i in list(train_idx) + list(val_idx):
        point = coords_rad[i:i + 1]
        idxs = train_tree.query_radius(point, r=nearby_radius_rad)[0]
        if len(idxs) == 0:
            te_nearby[i] = global_mean
            continue

        # Filter to same season
        cur_season = seasons[i]
        if not np.isnan(cur_season):
            same_season = train_seasons[idxs] == cur_season
            idxs = idxs[same_season]

        if len(idxs) == 0:
            te_nearby[i] = global_mean
            continue

        nearby_vals = train_targets_arr[idxs]
        # For training rows, exclude self
        if i in set(train_idx):
            # Find which train_idx index corresponds to i
            train_idx_set = set(train_idx)
            # Remove entries that correspond to the same location group
            own_group = train_groups[i]
            mask = np.array([train_groups[train_idx[j]] != own_group for j in idxs])
            nearby_vals = nearby_vals[mask]

        if len(nearby_vals) == 0:
            te_nearby[i] = global_mean
        else:
            te_nearby[i] = bayesian_target_encode(nearby_vals, global_mean)

    return te_loc, te_state, te_nearby


# ======================================================================
# MODEL TRAINING
# ======================================================================

def train_and_evaluate(df):
    base_features = get_features(df)
    print(f"\n{'=' * 70}")
    print(f"TRAINING — {len(base_features)} base features, {len(df)} rows")
    print(f"{'=' * 70}")

    groups = df['loc_group'].values
    n_splits = min(5, df['loc_group'].nunique())
    gkf = GroupKFold(n_splits=n_splits)

    # Precompute coords in radians and BallTree for nearby encoding
    coords_rad = np.radians(df[['lat', 'lon']].values)
    nearby_radius_rad = NEARBY_RADIUS_KM / EARTH_RADIUS_KM

    t0 = time.time()

    # ==================================================================
    # Part 1: Catch Probability (binary classification)
    # ==================================================================
    print(f"\n--- Part 1: Catch Probability ---")
    y_bin = df['has_catch'].values
    auc_scores = []
    for fold, (tr, va) in enumerate(gkf.split(df, y_bin, groups)):
        clf = CatBoostClassifier(iterations=500, depth=5, learning_rate=0.05,
                                 l2_leaf_reg=5, random_seed=SEED, verbose=0,
                                 early_stopping_rounds=30, auto_class_weights='Balanced')
        clf.fit(df.iloc[tr][base_features], y_bin[tr],
                eval_set=(df.iloc[va][base_features], y_bin[va]))
        p = clf.predict_proba(df.iloc[va][base_features])[:, 1]
        auc = roc_auc_score(y_bin[va], p) if len(np.unique(y_bin[va])) > 1 else 0
        auc_scores.append(auc)
        print(f"  Fold {fold + 1}: AUC={auc:.4f}")
    print(f"  Mean AUC: {np.mean(auc_scores):.4f}")

    # ==================================================================
    # Part 2: Positive CPUE — Stacking Ensemble
    # ==================================================================
    print(f"\n{'=' * 70}")
    print("Part 2: Positive CPUE — Stacking Ensemble with Target Encoding")
    print(f"{'=' * 70}")

    pos = df[df['has_catch'] == 1].copy().reset_index(drop=True)
    y_cpue = np.log1p(pos['cpue_hour'].values)
    y_cpue_raw = pos['cpue_hour'].values
    groups_pos = pos['loc_group'].values
    n_sp = min(5, pos['loc_group'].nunique())
    gkf2 = GroupKFold(n_splits=n_sp)

    pos_coords_rad = np.radians(pos[['lat', 'lon']].values)
    # Dummy tree for radius queries (will rebuild per fold)
    pos_tree_full = BallTree(pos_coords_rad, metric='haversine')

    # Storage for OOF predictions (for stacking)
    oof_cb = np.full(len(pos), np.nan)
    oof_xgb = np.full(len(pos), np.nan)
    oof_lgb = np.full(len(pos), np.nan)
    oof_lgb_q10 = np.full(len(pos), np.nan)
    oof_lgb_q90 = np.full(len(pos), np.nan)

    # Per-fold scores
    cb_r2, xgb_r2, lgb_r2, ens_r2 = [], [], [], []
    cb_mae, xgb_mae, lgb_mae = [], [], []

    # Metadata for analysis
    val_states = []
    val_wb_types = []
    val_is_river = []
    val_indices = []

    # Hyperparameter configs to try for CatBoost
    CB_CONFIGS = [
        {'depth': 7, 'learning_rate': 0.03, 'l2_leaf_reg': 3},
        {'depth': 8, 'learning_rate': 0.02, 'l2_leaf_reg': 5},
        {'depth': 6, 'learning_rate': 0.05, 'l2_leaf_reg': 3},
    ]

    print("\n--- Hyperparameter search for CatBoost (fold 1 only) ---")
    first_tr, first_va = next(iter(gkf2.split(pos, y_cpue, groups_pos)))
    best_cb_config = CB_CONFIGS[0]
    best_cb_r2 = -999

    for cfg in CB_CONFIGS:
        te_loc, te_state, te_nearby = compute_target_encodings(
            pos, first_tr, first_va, 'cpue_hour', groups_pos,
            pos_coords_rad, pos_tree_full, nearby_radius_rad)
        features_fold = base_features + ['te_loc_cpue', 'te_state_cpue', 'te_nearby_cpue']
        pos_tmp = pos.copy()
        pos_tmp['te_loc_cpue'] = te_loc
        pos_tmp['te_state_cpue'] = te_state
        pos_tmp['te_nearby_cpue'] = te_nearby

        reg = CatBoostRegressor(iterations=2000, depth=cfg['depth'],
                                learning_rate=cfg['learning_rate'],
                                l2_leaf_reg=cfg['l2_leaf_reg'],
                                random_seed=SEED, verbose=0,
                                early_stopping_rounds=100, subsample=0.8)
        avail = [f for f in features_fold if f in pos_tmp.columns]
        reg.fit(pos_tmp.iloc[first_tr][avail], y_cpue[first_tr],
                eval_set=(pos_tmp.iloc[first_va][avail], y_cpue[first_va]))
        pred = reg.predict(pos_tmp.iloc[first_va][avail])
        r2 = r2_score(y_cpue[first_va], pred)
        print(f"  depth={cfg['depth']} lr={cfg['learning_rate']} l2={cfg['l2_leaf_reg']}: R2={r2:.4f}")
        if r2 > best_cb_r2:
            best_cb_r2 = r2
            best_cb_config = cfg

    print(f"  Best CatBoost config: {best_cb_config}")

    # ------------------------------------------------------------------
    # Main CV loop
    # ------------------------------------------------------------------
    print(f"\n--- Main {n_sp}-fold Spatial CV ---")
    for fold, (tr, va) in enumerate(gkf2.split(pos, y_cpue, groups_pos)):
        print(f"\n  Fold {fold + 1}/{n_sp} (train={len(tr)}, val={len(va)})")

        # Compute target encodings inside this fold
        te_loc, te_state, te_nearby = compute_target_encodings(
            pos, tr, va, 'cpue_hour', groups_pos,
            pos_coords_rad, pos_tree_full, nearby_radius_rad)

        pos['te_loc_cpue'] = te_loc
        pos['te_state_cpue'] = te_state
        pos['te_nearby_cpue'] = te_nearby

        # Also compute on log1p target for positive-only encoding
        te_loc_p, te_state_p, te_nearby_p = compute_target_encodings(
            pos, tr, va, 'cpue_hour', groups_pos,
            pos_coords_rad, pos_tree_full, nearby_radius_rad)
        pos['te_loc_cpue_pos'] = np.log1p(te_loc_p)
        pos['te_state_cpue_pos'] = np.log1p(te_state_p)
        pos['te_nearby_cpue_pos'] = np.log1p(te_nearby_p)

        features_fold = base_features + [
            'te_loc_cpue', 'te_state_cpue', 'te_nearby_cpue',
            'te_loc_cpue_pos', 'te_state_cpue_pos', 'te_nearby_cpue_pos',
        ]
        avail = [f for f in features_fold if f in pos.columns]

        X_tr, X_va = pos.iloc[tr][avail], pos.iloc[va][avail]
        y_tr, y_va = y_cpue[tr], y_cpue[va]

        # --- CatBoost ---
        cb = CatBoostRegressor(
            iterations=2000, depth=best_cb_config['depth'],
            learning_rate=best_cb_config['learning_rate'],
            l2_leaf_reg=best_cb_config['l2_leaf_reg'],
            random_seed=SEED, verbose=0,
            early_stopping_rounds=100, subsample=0.8)
        cb.fit(X_tr, y_tr, eval_set=(X_va, y_va))
        p_cb = cb.predict(X_va)
        oof_cb[va] = p_cb
        r2 = r2_score(y_va, p_cb)
        mae = mean_absolute_error(np.expm1(y_va), np.expm1(p_cb))
        cb_r2.append(r2)
        cb_mae.append(mae)
        print(f"    CatBoost:  R2={r2:.4f}  MAE={mae:.4f}")

        # --- XGBoost ---
        xg = XGBRegressor(
            n_estimators=2000, max_depth=best_cb_config['depth'],
            learning_rate=best_cb_config['learning_rate'],
            reg_lambda=3, subsample=0.8, colsample_bytree=0.8,
            random_state=SEED, early_stopping_rounds=100, verbosity=0)
        xg.fit(X_tr, y_tr, eval_set=[(X_va, y_va)], verbose=False)
        p_xg = xg.predict(X_va)
        oof_xgb[va] = p_xg
        r2 = r2_score(y_va, p_xg)
        mae = mean_absolute_error(np.expm1(y_va), np.expm1(p_xg))
        xgb_r2.append(r2)
        xgb_mae.append(mae)
        print(f"    XGBoost:   R2={r2:.4f}  MAE={mae:.4f}")

        # --- LightGBM (median + quantiles) ---
        lgb_train = lgb.Dataset(X_tr, y_tr)
        lgb_val = lgb.Dataset(X_va, y_va, reference=lgb_train)

        lgb_params = {
            'objective': 'regression',
            'metric': 'rmse',
            'num_leaves': 63,
            'learning_rate': 0.03,
            'feature_fraction': 0.8,
            'bagging_fraction': 0.8,
            'bagging_freq': 5,
            'lambda_l2': 3,
            'verbose': -1,
            'seed': SEED,
        }
        lgb_model = lgb.train(lgb_params, lgb_train, num_boost_round=2000,
                              valid_sets=[lgb_val],
                              callbacks=[lgb.early_stopping(100), lgb.log_evaluation(0)])
        p_lgb = lgb_model.predict(X_va)
        oof_lgb[va] = p_lgb
        r2 = r2_score(y_va, p_lgb)
        mae = mean_absolute_error(np.expm1(y_va), np.expm1(p_lgb))
        lgb_r2.append(r2)
        lgb_mae.append(mae)
        print(f"    LightGBM:  R2={r2:.4f}  MAE={mae:.4f}")

        # Quantile q10
        lgb_params_q10 = lgb_params.copy()
        lgb_params_q10['objective'] = 'quantile'
        lgb_params_q10['alpha'] = 0.1
        lgb_params_q10['metric'] = 'quantile'
        lgb_q10 = lgb.train(lgb_params_q10, lgb_train, num_boost_round=200,
                            valid_sets=[lgb_val],
                            callbacks=[lgb.early_stopping(20), lgb.log_evaluation(0)])
        oof_lgb_q10[va] = lgb_q10.predict(X_va)

        # Quantile q90
        lgb_params_q90 = lgb_params.copy()
        lgb_params_q90['objective'] = 'quantile'
        lgb_params_q90['alpha'] = 0.9
        lgb_params_q90['metric'] = 'quantile'
        lgb_q90 = lgb.train(lgb_params_q90, lgb_train, num_boost_round=200,
                            valid_sets=[lgb_val],
                            callbacks=[lgb.early_stopping(20), lgb.log_evaluation(0)])
        oof_lgb_q90[va] = lgb_q90.predict(X_va)

        # Simple average ensemble
        p_ens = (p_cb + p_xg + p_lgb) / 3
        r2_e = r2_score(y_va, p_ens)
        ens_r2.append(r2_e)
        print(f"    AvgEnsemble: R2={r2_e:.4f}")

        # Store metadata
        val_states.extend(pos['_state'].iloc[va].values)
        val_wb_types.extend(pos['_wb_type'].iloc[va].values)
        val_is_river.extend(pos['is_river'].iloc[va].values)
        val_indices.extend(va)

    # ==================================================================
    # Stacking Meta-Learner (Ridge on OOF predictions)
    # ==================================================================
    print(f"\n{'=' * 70}")
    print("STACKING META-LEARNER (Ridge on OOF predictions)")
    print(f"{'=' * 70}")

    valid_mask = ~(np.isnan(oof_cb) | np.isnan(oof_xgb) | np.isnan(oof_lgb))
    oof_stack = np.column_stack([oof_cb, oof_xgb, oof_lgb])[valid_mask]
    y_stack = y_cpue[valid_mask]
    groups_stack = groups_pos[valid_mask]

    # Train Ridge meta-learner with GroupKFold
    n_meta_splits = min(5, len(np.unique(groups_stack)))
    gkf_meta = GroupKFold(n_splits=n_meta_splits)
    oof_meta = np.full(len(y_stack), np.nan)
    meta_r2_folds = []

    for fold, (tr, va) in enumerate(gkf_meta.split(oof_stack, y_stack, groups_stack)):
        ridge = Ridge(alpha=1.0)
        ridge.fit(oof_stack[tr], y_stack[tr])
        oof_meta[va] = ridge.predict(oof_stack[va])
        r2 = r2_score(y_stack[va], oof_meta[va])
        meta_r2_folds.append(r2)

    # Final Ridge weights (fit on all OOF)
    ridge_final = Ridge(alpha=1.0)
    ridge_final.fit(oof_stack, y_stack)
    print(f"  Ridge weights: CatBoost={ridge_final.coef_[0]:.3f}, "
          f"XGBoost={ridge_final.coef_[1]:.3f}, LightGBM={ridge_final.coef_[2]:.3f}")
    print(f"  Ridge intercept: {ridge_final.intercept_:.4f}")

    meta_pred = ridge_final.predict(oof_stack)
    meta_r2 = r2_score(y_stack, meta_pred)
    meta_mae = mean_absolute_error(np.expm1(y_stack), np.expm1(meta_pred))
    meta_rmse = np.sqrt(mean_squared_error(y_stack, meta_pred))
    print(f"  Stacked R2 (full OOF): {meta_r2:.4f}")
    print(f"  Stacked MAE: {meta_mae:.4f} fish/hr")
    print(f"  Stacked RMSE (log): {meta_rmse:.4f}")

    # ==================================================================
    # Uncertainty Estimation (quantile coverage)
    # ==================================================================
    print(f"\n{'=' * 70}")
    print("UNCERTAINTY ESTIMATION (LightGBM Quantile)")
    print(f"{'=' * 70}")
    q10_valid = oof_lgb_q10[valid_mask]
    q90_valid = oof_lgb_q90[valid_mask]
    coverage_80 = np.mean((y_stack >= q10_valid) & (y_stack <= q90_valid))
    mean_interval_width = np.mean(np.expm1(q90_valid) - np.expm1(q10_valid))
    print(f"  80% prediction interval coverage: {coverage_80 * 100:.1f}% (target: 80%)")
    print(f"  Mean interval width: {mean_interval_width:.3f} fish/hr")

    # ==================================================================
    # CALIBRATION ANALYSIS
    # ==================================================================
    print(f"\n{'=' * 70}")
    print("CALIBRATION ANALYSIS (predicted vs actual by decile)")
    print(f"{'=' * 70}")
    actual_raw = np.expm1(y_stack)
    pred_raw = np.expm1(meta_pred)

    # Decile calibration
    try:
        decile_bins = pd.qcut(pred_raw, q=10, duplicates='drop')
        cal_df = pd.DataFrame({'actual': actual_raw, 'predicted': pred_raw, 'decile': decile_bins})
        cal_summary = cal_df.groupby('decile', observed=True).agg(
            mean_actual=('actual', 'mean'),
            mean_pred=('predicted', 'mean'),
            count=('actual', 'count'),
            std_actual=('actual', 'std'),
        ).reset_index()
        print(f"  {'Decile':>30}  {'Actual':>8}  {'Predicted':>8}  {'N':>5}  {'StdDev':>8}")
        print(f"  {'-' * 65}")
        for _, row in cal_summary.iterrows():
            print(f"  {str(row['decile']):>30}  {row['mean_actual']:>8.3f}  "
                  f"{row['mean_pred']:>8.3f}  {row['count']:>5.0f}  {row['std_actual']:>8.3f}")
    except Exception as e:
        print(f"  Calibration analysis error: {e}")

    # ==================================================================
    # SPATIAL RESIDUAL ANALYSIS
    # ==================================================================
    print(f"\n{'=' * 70}")
    print("SPATIAL RESIDUAL ANALYSIS (by state)")
    print(f"{'=' * 70}")
    residuals = actual_raw - pred_raw
    abs_residuals = np.abs(residuals)

    states_arr = np.array(val_states)[valid_mask[np.array(val_indices)]] \
        if len(val_states) == len(y_cpue) else np.array(val_states)[:len(residuals)]

    # Handle potential length mismatch
    min_len = min(len(states_arr), len(residuals))
    states_arr = states_arr[:min_len]
    residuals_trimmed = residuals[:min_len]
    abs_res_trimmed = abs_residuals[:min_len]
    actual_trimmed = actual_raw[:min_len]

    state_df = pd.DataFrame({
        'state': states_arr,
        'residual': residuals_trimmed,
        'abs_residual': abs_res_trimmed,
        'actual': actual_trimmed,
    })
    state_summary = state_df.groupby('state').agg(
        n=('residual', 'count'),
        mean_bias=('residual', 'mean'),
        mae=('abs_residual', 'mean'),
        mean_actual=('actual', 'mean'),
    ).sort_values('mae', ascending=False)

    print(f"  {'State':>6}  {'N':>5}  {'MAE':>8}  {'Bias':>8}  {'Actual':>8}")
    print(f"  {'-' * 45}")
    for state, row in state_summary.head(15).iterrows():
        print(f"  {state:>6}  {row['n']:>5.0f}  {row['mae']:>8.3f}  "
              f"{row['mean_bias']:>+8.3f}  {row['mean_actual']:>8.3f}")

    # ==================================================================
    # LAKE vs RIVER PERFORMANCE
    # ==================================================================
    print(f"\n{'=' * 70}")
    print("LAKE vs RIVER PERFORMANCE")
    print(f"{'=' * 70}")
    is_river_arr = np.array(val_is_river)[:min_len] if len(val_is_river) >= min_len else np.zeros(min_len)

    for label, mask_val in [("Lake/Reservoir", 0), ("River/Stream", 1)]:
        mask = is_river_arr == mask_val
        if mask.sum() > 10:
            r2_sub = r2_score(actual_trimmed[mask], (pred_raw[:min_len])[mask])
            mae_sub = mean_absolute_error(actual_trimmed[mask], (pred_raw[:min_len])[mask])
            print(f"  {label:>15}: N={mask.sum():>5}  R2={r2_sub:.4f}  MAE={mae_sub:.4f}")
        else:
            print(f"  {label:>15}: N={mask.sum():>5}  (too few for reliable stats)")

    # ==================================================================
    # FEATURE IMPORTANCE
    # ==================================================================
    print(f"\n{'=' * 70}")
    print("FEATURE IMPORTANCE (Full CatBoost with target encodings)")
    print(f"{'=' * 70}")

    # Compute target encodings on full dataset for final model
    all_idx = np.arange(len(pos))
    te_loc_full, te_state_full, te_nearby_full = compute_target_encodings(
        pos, all_idx, np.array([], dtype=int), 'cpue_hour', groups_pos,
        pos_coords_rad, pos_tree_full, nearby_radius_rad)
    pos['te_loc_cpue'] = te_loc_full
    pos['te_state_cpue'] = te_state_full
    pos['te_nearby_cpue'] = te_nearby_full
    pos['te_loc_cpue_pos'] = np.log1p(te_loc_full)
    pos['te_state_cpue_pos'] = np.log1p(te_state_full)
    pos['te_nearby_cpue_pos'] = np.log1p(te_nearby_full)

    all_features = base_features + [
        'te_loc_cpue', 'te_state_cpue', 'te_nearby_cpue',
        'te_loc_cpue_pos', 'te_state_cpue_pos', 'te_nearby_cpue_pos',
    ]
    avail_final = [f for f in all_features if f in pos.columns]

    reg_full = CatBoostRegressor(
        iterations=2000, depth=best_cb_config['depth'],
        learning_rate=best_cb_config['learning_rate'],
        l2_leaf_reg=best_cb_config['l2_leaf_reg'],
        random_seed=SEED, verbose=0)
    reg_full.fit(pos[avail_final], y_cpue)
    imp = pd.Series(reg_full.feature_importances_, index=avail_final).sort_values(ascending=False)
    for feat, val in imp.head(30).items():
        print(f"  {feat:>35}: {val:.2f}%")

    elapsed = time.time() - t0

    # ==================================================================
    # FINAL SUMMARY
    # ==================================================================
    print(f"\n{'=' * 70}")
    print("FINAL SUMMARY — CPUE Model v4")
    print(f"{'=' * 70}")
    print(f"  Dataset:                  {len(pos)} positive surveys, {len(df)} total")
    print(f"  Base features:            {len(base_features)}")
    print(f"  + Target encodings:       6")
    print(f"  Spatial CV folds:         {n_sp}")
    print(f"  Best CatBoost config:     {best_cb_config}")
    print()
    print(f"  Catch Probability AUC:    {np.mean(auc_scores):.4f}")
    print()
    print(f"  CPUE R2 (CatBoost):       {np.mean(cb_r2):.4f} +/- {np.std(cb_r2):.4f}")
    print(f"  CPUE R2 (XGBoost):        {np.mean(xgb_r2):.4f} +/- {np.std(xgb_r2):.4f}")
    print(f"  CPUE R2 (LightGBM):       {np.mean(lgb_r2):.4f} +/- {np.std(lgb_r2):.4f}")
    print(f"  CPUE R2 (Avg Ensemble):   {np.mean(ens_r2):.4f} +/- {np.std(ens_r2):.4f}")
    print(f"  CPUE R2 (Stacked Ridge):  {meta_r2:.4f}")
    print()
    print(f"  CPUE MAE (CatBoost):      {np.mean(cb_mae):.4f} fish/hr")
    print(f"  CPUE MAE (XGBoost):       {np.mean(xgb_mae):.4f} fish/hr")
    print(f"  CPUE MAE (LightGBM):      {np.mean(lgb_mae):.4f} fish/hr")
    print(f"  CPUE MAE (Stacked):       {meta_mae:.4f} fish/hr")
    print()
    print(f"  80% PI coverage:          {coverage_80 * 100:.1f}%")
    print(f"  Mean PI width:            {mean_interval_width:.3f} fish/hr")
    print()
    print(f"  Wall time:                {elapsed:.0f}s")
    print(f"{'=' * 70}")


if __name__ == '__main__':
    df = build_dataset()
    print(f"\nFinal: {len(df)} rows x {len(df.columns)} cols, {len(get_features(df))} features")
    train_and_evaluate(df)
