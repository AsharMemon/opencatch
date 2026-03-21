"""
Option B: CPUE Hurdle Model from CreelCat Data
================================================
Two-part model:
  Part 1: P(catch > 0) — binary classifier
  Part 2: E(CPUE | catch > 0) — regression on positive catches

Training data: CreelCat bass surveys (~5K surveys, 29 states, 1937-2023)
Features: spatial (lat/lon/embeddings/morphometry), temporal (year/season), waterbody characteristics
"""
import pandas as pd
import numpy as np
from sklearn.neighbors import BallTree
from catboost import CatBoostRegressor, CatBoostClassifier
from sklearn.model_selection import GroupKFold
from sklearn.metrics import r2_score, roc_auc_score, f1_score, mean_absolute_error
import warnings
warnings.filterwarnings('ignore')

# --- PATHS (Vast.ai) ---
CREEL_FISH = "/workspace/castline/raw/FishDataCompiled.csv"
CREEL_SURVEY = "/workspace/castline/raw/Survey_Data.csv"
CREEL_EFFORT = "/workspace/castline/raw/AngEffort_Data.csv"
LAGOS_PATH = "/workspace/castline/raw/lagos_morphometry.csv"  # if available
EMBEDDINGS_PATH = "/workspace/castline/raw/location_embeddings_geoclip_pca32.csv"  # tournament embeddings
V16_PATH = "/workspace/castline/data/validation_dataset_v16.csv"

SEED = 42
EARTH_RADIUS_KM = 6371.0

# True bass taxa only
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
    print("BUILDING CPUE TRAINING DATASET")
    print("="*70)

    # Load raw data
    fish = pd.read_csv(CREEL_FISH, low_memory=False)
    survey = pd.read_csv(CREEL_SURVEY, low_memory=False)
    effort = pd.read_csv(CREEL_EFFORT, low_memory=False)

    # Filter to true bass
    bass = fish[fish['Taxa'].isin(BASS_TAXA)].copy()
    print(f"Bass records: {len(bass)}")

    # Tag species
    bass['is_lmb'] = bass['Taxa'].str.contains('salmoides|Largemouth', case=False).astype(int)
    bass['is_smb'] = bass['Taxa'].str.contains('dolomieu|Smallmouth Bass', case=False).astype(int)
    bass['is_spotted'] = bass['Taxa'].str.contains('punctulatus|Spotted', case=False).astype(int)

    # Merge with survey metadata
    survey_cols = ['Survey_ID', 'Lat', 'Lon', 'Year', 'Start_Date', 'End_Date', 'Season',
                   'Waterbody_Name', 'WB_Type', 'Reported_Acres', 'Calc_Acres', 'State_Ab',
                   'Survey_Type', 'Duration', 'Start_Month', 'End_Month']
    existing_cols = [c for c in survey_cols if c in survey.columns]
    bass = bass.merge(survey[existing_cols], on='Survey_ID', how='left')
    bass = bass[bass['Lat'].notna() & bass['Lon'].notna()].copy()

    # Merge with effort data
    effort_cols = ['Survey_ID', 'Effort_Hours', 'Effort_Outings', 'Effort_Anglers']
    existing_effort = [c for c in effort_cols if c in effort.columns]
    bass = bass.merge(effort[existing_effort], on='Survey_ID', how='left')

    print(f"Bass with location: {len(bass)}")
    print(f"Unique surveys: {bass['Survey_ID'].nunique()}")

    # Aggregate to survey level (sum catches across bass species per survey)
    survey_agg = bass.groupby('Survey_ID').agg(
        lat=('Lat', 'first'),
        lon=('Lon', 'first'),
        year=('Year', 'first'),
        start_date=('Start_Date', 'first'),
        season=('Season', 'first'),
        start_month=('Start_Month', 'first'),
        waterbody=('Waterbody_Name', 'first'),
        wb_type=('WB_Type', 'first'),
        acres=('Reported_Acres', 'first'),
        calc_acres=('Calc_Acres', 'first'),
        state=('State_Ab', 'first'),
        # CPUE — use max across species (target species catch rate)
        cpue_hour=('Catch_Per_Hour', 'max'),
        cpue_day=('Catch_Per_Day', 'max'),
        # Totals
        total_catch=('Catch', 'sum'),
        total_harvest=('Harvest', 'sum'),
        total_release=('Release', 'sum'),
        # Species composition
        has_lmb=('is_lmb', 'max'),
        has_smb=('is_smb', 'max'),
        has_spotted=('is_spotted', 'max'),
        n_bass_species=('Taxa', 'nunique'),
        # Effort
        effort_hours=('Effort_Hours', 'first'),
        effort_outings=('Effort_Outings', 'first'),
        effort_anglers=('Effort_Anglers', 'first'),
    ).reset_index()

    # Clean up
    survey_agg['acres_best'] = survey_agg['calc_acres'].fillna(survey_agg['acres'])
    survey_agg['log_acres'] = np.log1p(survey_agg['acres_best'].fillna(0))
    survey_agg['is_river'] = survey_agg['wb_type'].str.contains('River|Stream', case=False, na=False).astype(int)
    survey_agg['is_lake'] = (~survey_agg['wb_type'].str.contains('River|Stream', case=False, na=False)).astype(int)

    # Parse temporal features
    survey_agg['start_month_num'] = pd.to_numeric(survey_agg['start_month'], errors='coerce')
    # Season encoding
    season_map = {'Spring': 0, 'Summer': 1, 'Fall': 2, 'Winter': 3, 'Annual': np.nan}
    survey_agg['season_num'] = survey_agg['season'].map(season_map)

    # Month from start_date if available
    try:
        dates = pd.to_datetime(survey_agg['start_date'], errors='coerce')
        survey_agg['month'] = dates.dt.month.fillna(survey_agg['start_month_num'])
    except:
        survey_agg['month'] = survey_agg['start_month_num']

    survey_agg['month_sin'] = np.sin(2 * np.pi * survey_agg['month'] / 12)
    survey_agg['month_cos'] = np.cos(2 * np.pi * survey_agg['month'] / 12)

    # Hurdle target
    survey_agg['has_catch'] = (survey_agg['cpue_hour'] > 0).astype(int)

    # Release rate
    total = survey_agg['total_catch'].replace(0, np.nan)
    survey_agg['release_pct'] = survey_agg['total_release'] / total

    # Drop rows without CPUE
    valid = survey_agg[survey_agg['cpue_hour'].notna()].copy()
    print(f"\nValid surveys with CPUE: {len(valid)}")
    print(f"  With catch: {valid['has_catch'].sum()} ({valid['has_catch'].mean()*100:.1f}%)")
    print(f"  CPUE stats: mean={valid['cpue_hour'].mean():.3f} median={valid['cpue_hour'].median():.3f}")
    print(f"  States: {valid['state'].nunique()}")
    print(f"  Year range: {valid['year'].min():.0f}-{valid['year'].max():.0f}")
    print(f"  Water bodies: {valid.groupby(['lat','lon']).ngroups}")

    return valid


def add_spatial_features(df):
    """Add location-derived features via matching to LAGOS and embeddings."""
    print("\n--- Adding spatial features ---")

    # Try to load LAGOS morphometry
    try:
        lagos = pd.read_csv(LAGOS_PATH, low_memory=False)
        print(f"  LAGOS data: {len(lagos)} lakes")
        # Match CreelCat locations to LAGOS via BallTree
        lagos_locs = lagos[['lat', 'lon']].dropna()
        if len(lagos_locs) > 0:
            tree = BallTree(np.radians(lagos_locs.values), metric='haversine')
            query = np.radians(df[['lat', 'lon']].values)
            dist, idx = tree.query(query, k=1)
            dist_km = dist[:, 0] * EARTH_RADIUS_KM

            # Only match within 10km
            matched = dist_km < 10
            lagos_features = ['lagos_sdi', 'max_depth_ft', 'area_acres', 'shore_dev']
            for col in lagos_features:
                if col in lagos.columns:
                    df[f'lagos_{col}'] = np.where(matched, lagos.iloc[idx[:, 0]][col].values, np.nan)
            print(f"  LAGOS matched: {matched.sum()}/{len(df)} ({matched.mean()*100:.1f}%)")
    except FileNotFoundError:
        print("  LAGOS data not available — skipping")

    # Try to load location embeddings and match
    try:
        emb = pd.read_csv(EMBEDDINGS_PATH, low_memory=False)
        emb_cols = [c for c in emb.columns if c.startswith('geoclip_')]
        if len(emb_cols) > 0 and 'lat' in emb.columns:
            tree = BallTree(np.radians(emb[['lat', 'lon']].values), metric='haversine')
            query = np.radians(df[['lat', 'lon']].values)
            dist, idx = tree.query(query, k=1)
            dist_km = dist[:, 0] * EARTH_RADIUS_KM

            matched = dist_km < 25  # 25km for embeddings
            for col in emb_cols[:16]:  # Top 16 PCA components
                df[col] = np.where(matched, emb.iloc[idx[:, 0]][col].values, np.nan)
            print(f"  GeoCLIP matched: {matched.sum()}/{len(df)} ({matched.mean()*100:.1f}%)")
    except FileNotFoundError:
        print("  GeoCLIP embeddings not available — skipping")

    # Latitude-derived features (always available)
    df['abs_lat'] = df['lat'].abs()
    df['lat_band'] = pd.cut(df['lat'], bins=[25, 33, 38, 42, 50], labels=[0, 1, 2, 3]).astype(float)

    return df


def get_feature_cols(df):
    """Get modeling feature columns."""
    exclude = {'Survey_ID', 'cpue_hour', 'cpue_day', 'has_catch', 'total_catch',
               'total_harvest', 'total_release', 'waterbody', 'wb_type', 'state',
               'start_date', 'season', 'start_month', 'start_month_num', 'season_num',
               'acres', 'calc_acres', 'acres_best',
               'release_pct',  # LEAKY: encodes whether fish were caught
               'effort_outings', 'effort_anglers',  # effort is denominator of CPUE
               'loc_group'}
    features = [c for c in df.columns if c not in exclude
                and df[c].dtype in ['float64', 'int64', 'float32', 'int32']
                and df[c].notna().sum() > len(df) * 0.1]  # At least 10% non-null
    return features


def train_hurdle_model(df):
    """Train two-part hurdle model with spatial CV."""
    print("\n" + "="*70)
    print("TRAINING HURDLE MODEL")
    print("="*70)

    features = get_feature_cols(df)
    print(f"Features: {len(features)}")
    print(f"  {features[:20]}...")

    # Create location groups for spatial CV
    df['loc_group'] = df.apply(lambda r: f"{r['lat']:.2f}_{r['lon']:.2f}", axis=1)
    groups = df['loc_group'].values
    unique_groups = df['loc_group'].nunique()
    print(f"Location groups: {unique_groups}")

    n_splits = min(5, unique_groups)
    gkf = GroupKFold(n_splits=n_splits)

    # Part 1: P(catch > 0)
    print(f"\n--- Part 1: Catch Probability ---")
    y_binary = df['has_catch'].values
    auc_scores = []
    f1_scores = []

    for fold, (train_idx, val_idx) in enumerate(gkf.split(df, y_binary, groups)):
        X_train = df.iloc[train_idx][features]
        y_train = y_binary[train_idx]
        X_val = df.iloc[val_idx][features]
        y_val = y_binary[val_idx]

        clf = CatBoostClassifier(
            iterations=500, depth=5, learning_rate=0.05,
            l2_leaf_reg=5, random_seed=SEED, verbose=0,
            early_stopping_rounds=30, auto_class_weights='Balanced'
        )
        clf.fit(X_train, y_train, eval_set=(X_val, y_val))

        pred_proba = clf.predict_proba(X_val)[:, 1]
        pred_class = (pred_proba > 0.5).astype(int)

        auc = roc_auc_score(y_val, pred_proba) if len(np.unique(y_val)) > 1 else 0
        f1 = f1_score(y_val, pred_class)
        auc_scores.append(auc)
        f1_scores.append(f1)
        print(f"  Fold {fold+1}: AUC={auc:.4f}  F1={f1:.4f}")

    print(f"  Mean AUC: {np.mean(auc_scores):.4f} ± {np.std(auc_scores):.4f}")
    print(f"  Mean F1:  {np.mean(f1_scores):.4f} ± {np.std(f1_scores):.4f}")

    # Part 2: E(CPUE | catch > 0)
    print(f"\n--- Part 2: Positive CPUE Regression ---")
    positive = df[df['has_catch'] == 1].copy()
    y_cpue = np.log1p(positive['cpue_hour'].values)  # Log transform for skewed CPUE
    groups_pos = positive['loc_group'].values

    r2_scores = []
    mae_scores = []

    n_splits_pos = min(5, positive['loc_group'].nunique())
    gkf_pos = GroupKFold(n_splits=n_splits_pos)

    for fold, (train_idx, val_idx) in enumerate(gkf_pos.split(positive, y_cpue, groups_pos)):
        X_train = positive.iloc[train_idx][features]
        y_train = y_cpue[train_idx]
        X_val = positive.iloc[val_idx][features]
        y_val = y_cpue[val_idx]

        reg = CatBoostRegressor(
            iterations=1000, depth=6, learning_rate=0.05,
            l2_leaf_reg=3, random_seed=SEED, verbose=0,
            early_stopping_rounds=50
        )
        reg.fit(X_train, y_train, eval_set=(X_val, y_val))

        pred = reg.predict(X_val)
        r2 = r2_score(y_val, pred)
        mae = mean_absolute_error(np.expm1(y_val), np.expm1(pred))  # MAE in original scale
        r2_scores.append(r2)
        mae_scores.append(mae)
        print(f"  Fold {fold+1}: R²={r2:.4f}  MAE={mae:.4f} fish/hour")

    print(f"  Mean R²:  {np.mean(r2_scores):.4f} ± {np.std(r2_scores):.4f}")
    print(f"  Mean MAE: {np.mean(mae_scores):.4f} ± {np.std(mae_scores):.4f} fish/hour")

    # Feature importance (train final model on all data for importance)
    print(f"\n--- Feature Importance (Full Model) ---")
    clf_full = CatBoostClassifier(
        iterations=500, depth=5, learning_rate=0.05,
        l2_leaf_reg=5, random_seed=SEED, verbose=0,
        auto_class_weights='Balanced'
    )
    clf_full.fit(df[features], y_binary)

    reg_full = CatBoostRegressor(
        iterations=1000, depth=6, learning_rate=0.05,
        l2_leaf_reg=3, random_seed=SEED, verbose=0
    )
    reg_full.fit(positive[features], y_cpue)

    # Classifier importance
    clf_imp = pd.Series(clf_full.feature_importances_, index=features).sort_values(ascending=False)
    print(f"\n  Top 15 features (Catch Probability):")
    for feat, imp in clf_imp.head(15).items():
        print(f"    {feat:>25}: {imp:.2f}%")

    # Regressor importance
    reg_imp = pd.Series(reg_full.feature_importances_, index=features).sort_values(ascending=False)
    print(f"\n  Top 15 features (Positive CPUE):")
    for feat, imp in reg_imp.head(15).items():
        print(f"    {feat:>25}: {imp:.2f}%")

    return clf_full, reg_full, features


def evaluate_on_tournaments(clf, reg, features, cpue_df):
    """Cross-reference: predict CPUE for tournament locations and correlate with weight."""
    print("\n" + "="*70)
    print("CROSS-REFERENCE: CPUE predictions vs Tournament Weights")
    print("="*70)

    try:
        tourn = pd.read_csv(V16_PATH, low_memory=False)
    except FileNotFoundError:
        print("  Tournament data not available — skipping cross-reference")
        return

    # Get unique tournament locations
    tourn_locs = tourn.drop_duplicates(subset=['lat', 'lon'])[['lat', 'lon']].copy()
    print(f"Tournament locations: {len(tourn_locs)}")

    # Match tournament locations to nearest CreelCat survey
    creel_locs = cpue_df.drop_duplicates(subset=['lat', 'lon'])
    tree = BallTree(np.radians(creel_locs[['lat', 'lon']].values), metric='haversine')
    query = np.radians(tourn_locs[['lat', 'lon']].values)
    dist, idx = tree.query(query, k=1)
    dist_km = dist[:, 0] * EARTH_RADIUS_KM

    # For matched locations, get the CPUE prediction
    matched_mask = dist_km < 25  # 25km match
    print(f"Matched within 25km: {matched_mask.sum()}/{len(tourn_locs)} ({matched_mask.mean()*100:.1f}%)")

    # Get average tournament weight per location
    tourn_loc_weight = tourn.groupby(['lat', 'lon']).agg(
        mean_weight=('median_weight_lb', 'mean'),
        n_events=('median_weight_lb', 'count')
    ).reset_index()

    # Get CreelCat CPUE per location
    creel_loc_cpue = cpue_df.groupby(['lat', 'lon']).agg(
        mean_cpue=('cpue_hour', 'mean'),
        median_cpue=('cpue_hour', 'median'),
        n_surveys=('cpue_hour', 'count')
    ).reset_index()

    # Match and correlate
    tourn_loc_weight['match_idx'] = idx[:, 0]
    tourn_loc_weight['match_dist'] = dist_km
    tourn_loc_weight = tourn_loc_weight[tourn_loc_weight['match_dist'] < 25].copy()

    if len(tourn_loc_weight) > 10:
        matched_creel = creel_loc_cpue.iloc[tourn_loc_weight['match_idx'].values]
        tourn_loc_weight['creel_cpue'] = matched_creel['mean_cpue'].values

        corr = tourn_loc_weight['mean_weight'].corr(tourn_loc_weight['creel_cpue'])
        print(f"\nCorrelation: tournament weight ↔ CreelCat CPUE: r={corr:.4f} (n={len(tourn_loc_weight)})")

        if corr > 0.3:
            print("  → Moderate positive correlation: higher CPUE locations tend to produce heavier tournament catches")
        elif corr > 0:
            print("  → Weak positive correlation: CPUE and tournament weight measure somewhat different things")
        else:
            print("  → Near-zero/negative correlation: CPUE and tournament weight are independent signals")
            print("  → This SUPPORTS dual-target modeling — they capture different aspects of fishing quality")


def main():
    # Step 1: Build dataset
    cpue_df = build_cpue_dataset()

    # Step 2: Add spatial features
    cpue_df = add_spatial_features(cpue_df)

    # Step 3: Train hurdle model
    clf, reg, features = train_hurdle_model(cpue_df)

    # Step 4: Cross-reference with tournaments
    evaluate_on_tournaments(clf, reg, features, cpue_df)

    print("\n" + "="*70)
    print("CPUE HURDLE MODEL COMPLETE")
    print("="*70)
    print(f"\nModel ready for composite scoring:")
    print(f"  P(catch) model: CatBoostClassifier")
    print(f"  E(CPUE|catch) model: CatBoostRegressor")
    print(f"  Composite = P(catch) × E(CPUE|catch) × weight_model_score")


if __name__ == '__main__':
    main()
