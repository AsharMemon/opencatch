"""
CPUE Hurdle Model v3 — With real historical weather from Open-Meteo
===================================================================
Uses pre-fetched weather data (creelcat_weather.csv) for actual temperature,
precipitation, wind, pressure, and solar radiation at survey locations.
"""
import pandas as pd
import numpy as np
from sklearn.neighbors import BallTree
from catboost import CatBoostRegressor, CatBoostClassifier
from xgboost import XGBRegressor, XGBClassifier
from sklearn.model_selection import GroupKFold
from sklearn.metrics import r2_score, roc_auc_score, f1_score, mean_absolute_error
import warnings
warnings.filterwarnings('ignore')

# --- PATHS ---
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


def build_dataset():
    """Build enriched CPUE dataset with real weather."""
    print("="*70)
    print("CPUE MODEL v3 — WITH REAL WEATHER")
    print("="*70)

    # Load and aggregate CreelCat
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

    # --- BASIC FEATURES ---
    agg['acres_best'] = agg['calc_acres'].fillna(agg['acres'])
    agg['log_acres'] = np.log1p(pd.to_numeric(agg['acres_best'], errors='coerce').fillna(0))
    agg['is_river'] = agg['wb_type'].str.contains('River|Stream', case=False, na=False).astype(int)
    agg['has_catch'] = (agg['cpue_hour'] > 0).astype(int)

    # Temporal
    agg['start_month_num'] = pd.to_numeric(agg['start_month'], errors='coerce')
    dates = pd.to_datetime(agg['start_date'], errors='coerce')
    agg['month'] = dates.dt.month.fillna(agg['start_month_num'])
    agg['day_of_year'] = dates.dt.dayofyear
    agg['month_sin'] = np.sin(2 * np.pi * agg['month'] / 12)
    agg['month_cos'] = np.cos(2 * np.pi * agg['month'] / 12)

    # Photoperiod
    lat_rad = np.radians(agg['lat'].clip(-60, 60))
    doy = agg['day_of_year'].fillna(180)
    decl = np.radians(23.45 * np.sin(np.radians(360 / 365 * (doy - 81))))
    cos_ha = (-np.tan(lat_rad) * np.tan(decl)).clip(-1, 1)
    agg['day_length_hours'] = 2 * np.degrees(np.arccos(cos_ha)) / 15

    # Location features
    agg['abs_lat'] = agg['lat'].abs()
    agg['lat_band'] = pd.cut(agg['lat'], bins=[25, 33, 38, 42, 50], labels=[0,1,2,3]).astype(float)
    agg['years_since_2000'] = agg['year'] - 2000
    agg['log_effort_per_day'] = np.log1p(pd.to_numeric(agg['effort_hours_per_day'], errors='coerce').fillna(0))

    # --- WEATHER (real data) ---
    print("\n--- Loading real weather data ---")
    try:
        wx = pd.read_csv(WEATHER_PATH)
        print(f"  Weather records: {len(wx)}")

        # Create matching key
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

        print(f"  Weather matched: {matched}/{len(agg)} ({matched/len(agg)*100:.1f}%)")

        # Derived weather features
        if 'wx_temp_mean' in agg.columns:
            # Bass thermal comfort (optimal 18-24°C)
            agg['bass_thermal_comfort'] = 1 - np.minimum(np.abs(agg['wx_temp_mean'] - 21) / 10, 1)
            # Spawn proximity (water temp ~15-18°C triggers spawn)
            agg['spawn_proximity'] = np.exp(-0.5 * ((agg['wx_temp_mean'] - 16.5) / 3) ** 2)
            # Estimated water temp (lags air by ~2-4°C depending on season)
            agg['est_water_temp'] = agg['wx_temp_mean'] - 2 + agg['month_sin'] * 1.5
            # Pressure stability (range = frontal activity)
            agg['pressure_stability'] = 1 / (1 + agg['wx_pressure_range'].fillna(0))
            # Precipitation intensity
            agg['precip_intensity'] = agg['wx_precip_total'].fillna(0) / 30  # mm per day
            # Wind comfort
            agg['wind_fishing_comfort'] = 1 - np.minimum(agg['wx_wind_max'].fillna(0) / 40, 1)

            # Temperature × latitude interaction
            agg['temp_x_lat'] = agg['wx_temp_mean'] * agg['lat']
            # Pressure × season
            agg['pressure_x_season'] = agg['wx_pressure_mean'].fillna(1013) * agg['month_sin']
    except FileNotFoundError:
        print("  Weather file not found! Run fetch_weather_creelcat.py first")

    # --- LAGOS MORPHOMETRY ---
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
            matched = dist_km < 10

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
                        agg[dst] = np.where(matched, pd.Categorical(vals).codes.astype(float), np.nan)
                    else:
                        agg[dst] = np.where(matched, vals.astype(float), np.nan)

            # Depth
            try:
                depth = pd.read_csv(LAGOS_DEPTH, low_memory=False)
                depth_col = [c for c in depth.columns if 'max' in c.lower() and 'depth' in c.lower()]
                if depth_col:
                    lagos_d = lagos.merge(depth[['lagoslakeid'] + depth_col], on='lagoslakeid', how='left')
                    agg['lagos_max_depth_m'] = np.where(matched, lagos_d.iloc[idx[:,0]][depth_col[0]].values.astype(float), np.nan)
            except: pass

            if 'lagos_area_ha' in agg.columns:
                agg['lagos_log_area'] = np.log1p(agg['lagos_area_ha'])
            if 'lagos_max_depth_m' in agg.columns and 'lagos_area_ha' in agg.columns:
                agg['lagos_depth_area_ratio'] = agg['lagos_max_depth_m'] / np.log1p(agg['lagos_area_ha'])

            print(f"  LAGOS matched: {matched.sum()}/{len(agg)} ({matched.mean()*100:.1f}%)")
    except Exception as e:
        print(f"  LAGOS error: {e}")

    # --- GEOCLIP EMBEDDINGS ---
    print("\n--- Loading GeoCLIP embeddings ---")
    try:
        emb = pd.read_csv(EMBEDDINGS_PATH, low_memory=False)
        emb_cols = [c for c in emb.columns if c.startswith('geoclip_')]
        tree = BallTree(np.radians(emb[['lat', 'lon']].values), metric='haversine')
        dist, idx = tree.query(np.radians(agg[['lat', 'lon']].values), k=1)
        dist_km = dist[:, 0] * EARTH_RADIUS_KM
        matched = dist_km < 50
        for col in emb_cols[:16]:
            agg[col] = np.where(matched, emb.iloc[idx[:,0]][col].values, np.nan)
        print(f"  GeoCLIP matched: {matched.sum()}/{len(agg)} ({matched.mean()*100:.1f}%)")
    except Exception as e:
        print(f"  GeoCLIP error: {e}")

    # --- INTERACTIONS ---
    if 'wx_temp_mean' in agg.columns:
        agg['temp_x_depth'] = agg['wx_temp_mean'] * agg.get('lagos_max_depth_m', 0)
        agg['temp_x_acres'] = agg['wx_temp_mean'] * agg['log_acres']
        agg['comfort_x_daylen'] = agg['bass_thermal_comfort'] * agg['day_length_hours']

    agg['lat_x_month'] = agg['lat'] * agg['month_sin']
    agg['multi_species'] = (agg['n_bass_species'] > 1).astype(int)

    # Clean up
    drop_cols = ['Survey_ID', 'waterbody', 'wb_type', 'state', 'start_date',
                 'start_month', 'start_month_num', 'acres', 'calc_acres', 'acres_best',
                 'lat_r', 'lon_r', 'weather_key']
    for col in drop_cols:
        if col in agg.columns:
            agg = agg.drop(columns=[col])

    return agg


def get_features(df):
    exclude = {'cpue_hour', 'cpue_day', 'has_catch', 'total_catch', 'loc_group'}
    return [c for c in df.columns if c not in exclude
            and df[c].dtype in ['float64', 'int64', 'float32', 'int32']
            and df[c].notna().sum() > len(df) * 0.05]


def train_and_evaluate(df):
    features = get_features(df)
    print(f"\n{'='*70}")
    print(f"TRAINING — {len(features)} features, {len(df)} rows")
    print(f"{'='*70}")

    df['loc_group'] = df.apply(lambda r: f"{r['lat']:.2f}_{r['lon']:.2f}", axis=1)
    groups = df['loc_group'].values
    n_splits = min(5, df['loc_group'].nunique())
    gkf = GroupKFold(n_splits=n_splits)

    # === Part 1: Catch Probability ===
    print(f"\n--- Part 1: Catch Probability ---")
    y_bin = df['has_catch'].values
    auc_scores = []
    for fold, (tr, va) in enumerate(gkf.split(df, y_bin, groups)):
        clf = CatBoostClassifier(iterations=500, depth=5, learning_rate=0.05,
                                  l2_leaf_reg=5, random_seed=SEED, verbose=0,
                                  early_stopping_rounds=30, auto_class_weights='Balanced')
        clf.fit(df.iloc[tr][features], y_bin[tr], eval_set=(df.iloc[va][features], y_bin[va]))
        p = clf.predict_proba(df.iloc[va][features])[:, 1]
        auc = roc_auc_score(y_bin[va], p) if len(np.unique(y_bin[va])) > 1 else 0
        auc_scores.append(auc)
        print(f"  Fold {fold+1}: AUC={auc:.4f}")
    print(f"  Mean AUC: {np.mean(auc_scores):.4f}")

    # === Part 2: Positive CPUE (CatBoost) ===
    print(f"\n--- Part 2: Positive CPUE (CatBoost) ---")
    pos = df[df['has_catch'] == 1].copy()
    y_cpue = np.log1p(pos['cpue_hour'].values)
    groups_pos = pos['loc_group'].values
    n_sp = min(5, pos['loc_group'].nunique())
    gkf2 = GroupKFold(n_splits=n_sp)

    cb_r2, cb_mae = [], []
    for fold, (tr, va) in enumerate(gkf2.split(pos, y_cpue, groups_pos)):
        reg = CatBoostRegressor(iterations=2000, depth=7, learning_rate=0.03,
                                 l2_leaf_reg=3, random_seed=SEED, verbose=0,
                                 early_stopping_rounds=100, subsample=0.8)
        reg.fit(pos.iloc[tr][features], y_cpue[tr],
                eval_set=(pos.iloc[va][features], y_cpue[va]))
        pred = reg.predict(pos.iloc[va][features])
        r2 = r2_score(y_cpue[va], pred)
        mae = mean_absolute_error(np.expm1(y_cpue[va]), np.expm1(pred))
        cb_r2.append(r2)
        cb_mae.append(mae)
        print(f"  Fold {fold+1}: R²={r2:.4f}  MAE={mae:.4f}")
    print(f"  CatBoost Mean R²: {np.mean(cb_r2):.4f} ± {np.std(cb_r2):.4f}")

    # === Part 2b: Positive CPUE (XGBoost) ===
    print(f"\n--- Part 2b: Positive CPUE (XGBoost) ---")
    xgb_r2 = []
    for fold, (tr, va) in enumerate(gkf2.split(pos, y_cpue, groups_pos)):
        xreg = XGBRegressor(n_estimators=2000, max_depth=7, learning_rate=0.03,
                             reg_lambda=3, subsample=0.8, colsample_bytree=0.8,
                             random_state=SEED, early_stopping_rounds=100, verbosity=0)
        xreg.fit(pos.iloc[tr][features], y_cpue[tr],
                 eval_set=[(pos.iloc[va][features], y_cpue[va])], verbose=False)
        pred = xreg.predict(pos.iloc[va][features])
        r2 = r2_score(y_cpue[va], pred)
        xgb_r2.append(r2)
        print(f"  Fold {fold+1}: R²={r2:.4f}")
    print(f"  XGBoost Mean R²: {np.mean(xgb_r2):.4f}")

    # === Part 2c: Ensemble ===
    print(f"\n--- Part 2c: CatBoost + XGBoost Ensemble ---")
    ens_r2 = []
    for fold, (tr, va) in enumerate(gkf2.split(pos, y_cpue, groups_pos)):
        cb = CatBoostRegressor(iterations=2000, depth=7, learning_rate=0.03,
                                l2_leaf_reg=3, random_seed=SEED, verbose=0,
                                early_stopping_rounds=100, subsample=0.8)
        cb.fit(pos.iloc[tr][features], y_cpue[tr],
               eval_set=(pos.iloc[va][features], y_cpue[va]))
        xg = XGBRegressor(n_estimators=2000, max_depth=7, learning_rate=0.03,
                           reg_lambda=3, subsample=0.8, colsample_bytree=0.8,
                           random_state=SEED, early_stopping_rounds=100, verbosity=0)
        xg.fit(pos.iloc[tr][features], y_cpue[tr],
               eval_set=[(pos.iloc[va][features], y_cpue[va])], verbose=False)

        p_cb = cb.predict(pos.iloc[va][features])
        p_xg = xg.predict(pos.iloc[va][features])
        p_ens = 0.5 * p_cb + 0.5 * p_xg
        r2 = r2_score(y_cpue[va], p_ens)
        ens_r2.append(r2)
        print(f"  Fold {fold+1}: R²={r2:.4f}")
    print(f"  Ensemble Mean R²: {np.mean(ens_r2):.4f}")

    # Feature importance
    print(f"\n--- Feature Importance (Full CatBoost) ---")
    reg_full = CatBoostRegressor(iterations=2000, depth=7, learning_rate=0.03,
                                  l2_leaf_reg=3, random_seed=SEED, verbose=0)
    reg_full.fit(pos[features], y_cpue)
    imp = pd.Series(reg_full.feature_importances_, index=features).sort_values(ascending=False)
    for feat, val in imp.head(25).items():
        print(f"  {feat:>30}: {val:.2f}%")

    print(f"\n{'='*70}")
    print(f"SUMMARY")
    print(f"  Catch Probability AUC:    {np.mean(auc_scores):.4f}")
    print(f"  CPUE R² (CatBoost):       {np.mean(cb_r2):.4f}")
    print(f"  CPUE R² (XGBoost):        {np.mean(xgb_r2):.4f}")
    print(f"  CPUE R² (Ensemble):       {np.mean(ens_r2):.4f}")
    print(f"  CPUE MAE:                 {np.mean(cb_mae):.4f} fish/hr")
    print(f"{'='*70}")


if __name__ == '__main__':
    df = build_dataset()
    print(f"\nFinal: {len(df)} rows × {len(df.columns)} cols, {len(get_features(df))} features")
    train_and_evaluate(df)
