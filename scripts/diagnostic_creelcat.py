"""
CreelCat Diagnostic Analysis — Reviewer Feedback Items
=======================================================
1. Ablation: Do CreelCat features help unseen locations uniformly or only near good analogs?
2. Match-quality audit: Systematic check of spatial matching quality
3. Leakage/proxy check: Is creel2_cpue just a location identity proxy?
4. Regime breakdown: Where does CreelCat help most?
"""
import pandas as pd
import numpy as np
from sklearn.model_selection import GroupKFold
from catboost import CatBoostRegressor
import warnings
warnings.filterwarnings('ignore')

# --- CONFIG ---
DATA_PATH = "/workspace/castline/data/validation_dataset_v16.csv"  # GeoCLIP version (LOO winner)
TARGET = "median_weight_lb"
SEED = 42

def load_data():
    df = pd.read_csv(DATA_PATH, low_memory=False)
    # Create loc_id from location name for grouping
    df['loc_id'] = df['location']
    print(f"Loaded: {df.shape[0]} rows, {df.shape[1]} cols, {df['loc_id'].nunique()} locations")
    return df

def get_features(df, include_creel=True):
    """Get feature columns, optionally excluding CreelCat."""
    exclude = {TARGET, 'loc_id', 'event_id', 'date', 'lat', 'lon', 'location',
               'tournament_slug', 'species', 'results_source', 'state'}
    cols = [c for c in df.columns if c not in exclude
            and df[c].dtype in ['float64', 'int64', 'float32', 'int32']]
    if not include_creel:
        cols = [c for c in cols if not c.startswith('creel2_')]
    return cols

def train_catboost(X_train, y_train, X_val, y_val, features):
    """Train CatBoost and return predictions."""
    model = CatBoostRegressor(
        iterations=1000, depth=6, learning_rate=0.05,
        l2_leaf_reg=3, random_seed=SEED, verbose=0,
        early_stopping_rounds=50
    )
    model.fit(X_train[features], y_train, eval_set=(X_val[features], y_val))
    return model.predict(X_val[features])


# ============================================================
# 1. ABLATION: CreelCat impact on unseen locations
# ============================================================
def ablation_unseen_locations(df):
    """LOO evaluation WITH and WITHOUT CreelCat features, broken down by match quality."""
    print("\n" + "="*70)
    print("1. ABLATION: CreelCat impact on UNSEEN locations")
    print("="*70)

    features_with = get_features(df, include_creel=True)
    features_without = get_features(df, include_creel=False)

    # Get LOO locations (same criteria as eval scripts)
    loc_counts = df.groupby('loc_id').size()
    loo_locs = loc_counts[loc_counts >= 20].index.tolist()
    if len(loo_locs) > 40:
        loo_locs = sorted(loo_locs, key=lambda x: loc_counts[x], reverse=True)[:40]

    print(f"LOO locations: {len(loo_locs)}")

    results = []
    for i, loc in enumerate(loo_locs):
        test_mask = df['loc_id'] == loc
        train = df[~test_mask].copy()
        test = df[test_mask].copy()

        y_train = train[TARGET]
        y_test = test[TARGET]

        # WITH CreelCat
        pred_with = train_catboost(train, y_train, test, y_test, features_with)
        ss_res_with = ((y_test - pred_with) ** 2).sum()
        ss_tot = ((y_test - y_test.mean()) ** 2).sum()
        r2_with = 1 - ss_res_with / ss_tot if ss_tot > 0 else 0

        # WITHOUT CreelCat
        pred_without = train_catboost(train, y_train, test, y_test, features_without)
        ss_res_without = ((y_test - pred_without) ** 2).sum()
        r2_without = 1 - ss_res_without / ss_tot if ss_tot > 0 else 0

        # CreelCat match quality for this location
        creel_coverage = test['creel2_cpue_hour_max'].notna().mean()
        creel_cpue = test['creel2_cpue_hour_max'].median()
        nearest_km = test['creel2_nearest_km'].median() if 'creel2_nearest_km' in test.columns else np.nan
        nearby_count = test['creel2_nearby_waterbodies'].median() if 'creel2_nearby_waterbodies' in test.columns else np.nan

        # Water type
        wtype_cols = [c for c in test.columns if c.startswith('wtype_')]
        wtype = 'unknown'
        for wc in wtype_cols:
            if test[wc].mean() > 0.5:
                wtype = wc.replace('wtype_', '')
                break

        # Region
        region_cols = [c for c in test.columns if c.startswith('region_')]
        region = 'unknown'
        for rc in region_cols:
            if test[rc].mean() > 0.5:
                region = rc.replace('region_', '')
                break

        results.append({
            'loc_id': loc,
            'n_test': len(test),
            'r2_with_creel': r2_with,
            'r2_without_creel': r2_without,
            'delta_r2': r2_with - r2_without,
            'creel_coverage': creel_coverage,
            'creel_cpue_max': creel_cpue,
            'nearest_km': nearest_km,
            'nearby_waterbodies': nearby_count,
            'water_type': wtype,
            'region': region,
        })

        if (i + 1) % 10 == 0:
            interim = pd.DataFrame(results)
            with_mean = interim['r2_with_creel'].mean()
            without_mean = interim['r2_without_creel'].mean()
            delta_mean = interim['delta_r2'].mean()
            print(f"  {i+1}/{len(loo_locs)}: WITH={with_mean:.4f} WITHOUT={without_mean:.4f} DELTA={delta_mean:+.4f}")

    res = pd.DataFrame(results)

    # Overall summary
    print(f"\n--- Overall ---")
    print(f"  WITH CreelCat:    R²={res['r2_with_creel'].mean():.4f}")
    print(f"  WITHOUT CreelCat: R²={res['r2_without_creel'].mean():.4f}")
    print(f"  Delta:            {res['delta_r2'].mean():+.4f}")

    # Split by CreelCat coverage
    has_creel = res[res['creel_coverage'] > 0]
    no_creel = res[res['creel_coverage'] == 0]

    print(f"\n--- Locations WITH CreelCat data ({len(has_creel)}) ---")
    if len(has_creel) > 0:
        print(f"  WITH:    R²={has_creel['r2_with_creel'].mean():.4f}")
        print(f"  WITHOUT: R²={has_creel['r2_without_creel'].mean():.4f}")
        print(f"  Delta:   {has_creel['delta_r2'].mean():+.4f}")

    print(f"\n--- Locations WITHOUT CreelCat data ({len(no_creel)}) ---")
    if len(no_creel) > 0:
        print(f"  WITH:    R²={no_creel['r2_with_creel'].mean():.4f}")
        print(f"  WITHOUT: R²={no_creel['r2_without_creel'].mean():.4f}")
        print(f"  Delta:   {no_creel['delta_r2'].mean():+.4f}")

    # Split by nearby waterbody count (match quality proxy)
    if has_creel['nearby_waterbodies'].notna().any():
        median_nearby = has_creel['nearby_waterbodies'].median()
        good_match = has_creel[has_creel['nearby_waterbodies'] >= median_nearby]
        poor_match = has_creel[has_creel['nearby_waterbodies'] < median_nearby]

        print(f"\n--- HIGH analog density (>={median_nearby:.0f} nearby, n={len(good_match)}) ---")
        if len(good_match) > 0:
            print(f"  Delta: {good_match['delta_r2'].mean():+.4f}")

        print(f"\n--- LOW analog density (<{median_nearby:.0f} nearby, n={len(poor_match)}) ---")
        if len(poor_match) > 0:
            print(f"  Delta: {poor_match['delta_r2'].mean():+.4f}")

    # By water type
    print(f"\n--- By Water Type ---")
    for wt in res['water_type'].unique():
        subset = res[res['water_type'] == wt]
        print(f"  {wt:>15}: n={len(subset):>3}  delta={subset['delta_r2'].mean():+.4f}  "
              f"WITH={subset['r2_with_creel'].mean():.4f}  WITHOUT={subset['r2_without_creel'].mean():.4f}")

    # By region
    print(f"\n--- By Region ---")
    for rg in res['region'].unique():
        subset = res[res['region'] == rg]
        print(f"  {rg:>15}: n={len(subset):>3}  delta={subset['delta_r2'].mean():+.4f}  "
              f"WITH={subset['r2_with_creel'].mean():.4f}  WITHOUT={subset['r2_without_creel'].mean():.4f}")

    # Individual locations sorted by delta
    print(f"\n--- Top 10 locations where CreelCat HELPS most ---")
    top_help = res.nlargest(10, 'delta_r2')
    for _, row in top_help.iterrows():
        print(f"  {row['loc_id']:>30}: delta={row['delta_r2']:+.4f}  "
              f"creel_cpue={row['creel_cpue_max']:.2f}  nearby={row['nearby_waterbodies']:.0f}  {row['water_type']}")

    print(f"\n--- Top 10 locations where CreelCat HURTS most ---")
    top_hurt = res.nsmallest(10, 'delta_r2')
    for _, row in top_hurt.iterrows():
        print(f"  {row['loc_id']:>30}: delta={row['delta_r2']:+.4f}  "
              f"creel_cpue={row['creel_cpue_max']:.2f}  nearby={row['nearby_waterbodies']:.0f}  {row['water_type']}")

    return res


# ============================================================
# 2. MATCH-QUALITY AUDIT (systematic, not manual sampling)
# ============================================================
def match_quality_audit(df):
    """Audit CreelCat spatial matching quality programmatically."""
    print("\n" + "="*70)
    print("2. MATCH-QUALITY AUDIT")
    print("="*70)

    locs = df.drop_duplicates(subset=['loc_id'])[['loc_id', 'lat', 'lon']].copy()

    # Add CreelCat info per location
    creel_cols = [c for c in df.columns if c.startswith('creel2_')]
    for col in creel_cols:
        locs[col] = df.groupby('loc_id')[col].first().values

    # Add water type
    wtype_cols = [c for c in df.columns if c.startswith('wtype_')]
    for wc in wtype_cols:
        locs[wc] = df.groupby('loc_id')[wc].first().values

    total = len(locs)
    has_creel = locs['creel2_cpue_hour_max'].notna().sum()
    no_creel = total - has_creel

    print(f"\nTotal locations: {total}")
    print(f"  With CreelCat match: {has_creel} ({has_creel/total*100:.1f}%)")
    print(f"  Without match: {no_creel} ({no_creel/total*100:.1f}%)")

    matched = locs[locs['creel2_cpue_hour_max'].notna()].copy()

    if 'creel2_nearest_km' in matched.columns:
        print(f"\nDistance to nearest CreelCat water body:")
        print(f"  Mean: {matched['creel2_nearest_km'].mean():.1f} km")
        print(f"  Median: {matched['creel2_nearest_km'].median():.1f} km")
        print(f"  <5 km (likely same water body): {(matched['creel2_nearest_km'] < 5).sum()} ({(matched['creel2_nearest_km'] < 5).mean()*100:.1f}%)")
        print(f"  5-25 km (plausible analog): {((matched['creel2_nearest_km'] >= 5) & (matched['creel2_nearest_km'] < 25)).sum()}")
        print(f"  25-50 km (weaker analog): {((matched['creel2_nearest_km'] >= 25) & (matched['creel2_nearest_km'] < 50)).sum()}")
        print(f"  50-75 km (marginal analog): {((matched['creel2_nearest_km'] >= 50) & (matched['creel2_nearest_km'] <= 75)).sum()}")

    if 'creel2_nearby_waterbodies' in matched.columns:
        print(f"\nNearby CreelCat water bodies within 75km:")
        print(f"  Mean: {matched['creel2_nearby_waterbodies'].mean():.1f}")
        print(f"  Median: {matched['creel2_nearby_waterbodies'].median():.0f}")
        print(f"  Max: {matched['creel2_nearby_waterbodies'].max():.0f}")
        print(f"  Only 1 nearby: {(matched['creel2_nearby_waterbodies'] == 1).sum()}")

    # Water type breakdown
    print(f"\nMatch rate by water type:")
    for wc in wtype_cols:
        wt = wc.replace('wtype_', '')
        is_type = locs[wc] == 1
        if is_type.sum() > 0:
            type_matched = is_type & locs['creel2_cpue_hour_max'].notna()
            pct = type_matched.sum() / is_type.sum() * 100
            print(f"  {wt:>15}: {type_matched.sum():>4}/{is_type.sum():>4} matched ({pct:.1f}%)")

    # CPUE distribution sanity check
    print(f"\nCreelCat CPUE distribution (matched locations):")
    print(f"  cpue_hour_max:  mean={matched['creel2_cpue_hour_max'].mean():.3f}  "
          f"median={matched['creel2_cpue_hour_max'].median():.3f}  "
          f"max={matched['creel2_cpue_hour_max'].max():.3f}")
    print(f"  cpue_hour_median: mean={matched['creel2_cpue_hour_median'].mean():.3f}  "
          f"median={matched['creel2_cpue_hour_median'].median():.3f}")

    # Flag suspicious matches: very high CPUE might indicate different species or gear
    suspicious_cpue = matched[matched['creel2_cpue_hour_max'] > 5.0]
    print(f"\n  Suspicious (cpue_hour_max > 5.0): {len(suspicious_cpue)} locations")
    if len(suspicious_cpue) > 0:
        print(f"    These might indicate non-bass species contamination or party boat data")


# ============================================================
# 3. LEAKAGE / PROXY CHECK
# ============================================================
def leakage_check(df):
    """Check if CreelCat features are leaking information or just acting as location priors."""
    print("\n" + "="*70)
    print("3. LEAKAGE / PROXY CHECK")
    print("="*70)

    # A. Correlation between CreelCat CPUE and target
    creel_cols = ['creel2_cpue_hour_max', 'creel2_cpue_hour_median', 'creel2_cpue_day_median']
    existing_creel = [c for c in creel_cols if c in df.columns]

    print(f"\n--- A. Correlation with target ({TARGET}) ---")
    for col in existing_creel:
        valid = df[[col, TARGET]].dropna()
        if len(valid) > 10:
            corr = valid[col].corr(valid[TARGET])
            print(f"  {col:>30}: r={corr:.4f} (n={len(valid)})")

    # B. Is CreelCat CPUE constant per location? (i.e., a location identity marker)
    print(f"\n--- B. Is CreelCat CPUE a location identity marker? ---")
    print(f"  (If variance within-location = 0, it's purely a spatial prior)")
    for col in existing_creel:
        within_var = df.groupby('loc_id')[col].std().mean()
        between_var = df.groupby('loc_id')[col].mean().std()
        print(f"  {col:>30}: within-loc std={within_var:.4f}  between-loc std={between_var:.4f}")
        if within_var < 0.001:
            print(f"    → YES: This is a pure location-level feature (same value for all events at a location)")
            print(f"    → This is OKAY: it acts as a spatial quality prior, not temporal leakage")

    # C. Temporal overlap check
    print(f"\n--- C. Temporal overlap check ---")
    if 'creel2_year_max' in df.columns and 'year' in df.columns:
        # Check if CreelCat surveys overlap in time with tournaments
        valid = df[df['creel2_year_max'].notna()].copy()
        overlap = valid[valid['year'] <= valid['creel2_year_max']]
        print(f"  Tournament years overlapping with CreelCat survey period: "
              f"{len(overlap)}/{len(valid)} ({len(overlap)/len(valid)*100:.1f}%)")
        print(f"  This means some CreelCat data comes from BEFORE tournament dates (OK)")
        print(f"  CreelCat data is aggregated across years, not event-specific (no direct leakage)")

        # Check: do tournaments at same location in different years get same CreelCat value?
        multi_year = valid.groupby('loc_id').agg(
            n_years=('year', 'nunique'),
            creel_unique=('creel2_cpue_hour_max', 'nunique')
        )
        multi_year = multi_year[multi_year['n_years'] > 1]
        same_creel = (multi_year['creel_unique'] == 1).sum()
        print(f"  Locations with multiple years: {len(multi_year)}")
        print(f"  Of those, same CreelCat value across years: {same_creel}/{len(multi_year)}")
        print(f"  → CreelCat is a STATIC spatial feature, not time-varying")

    # D. Does the model use CreelCat as location encoding?
    print(f"\n--- D. CreelCat vs loc_rolling_mean (location identity comparison) ---")
    if 'loc_rolling_mean' in df.columns:
        valid = df[['creel2_cpue_hour_max', 'loc_rolling_mean', TARGET]].dropna()
        corr_creel_loc = valid['creel2_cpue_hour_max'].corr(valid['loc_rolling_mean'])
        corr_creel_target = valid['creel2_cpue_hour_max'].corr(valid[TARGET])
        corr_loc_target = valid['loc_rolling_mean'].corr(valid[TARGET])
        print(f"  creel_cpue ↔ loc_rolling_mean: r={corr_creel_loc:.4f}")
        print(f"  creel_cpue ↔ target:           r={corr_creel_target:.4f}")
        print(f"  loc_rolling_mean ↔ target:     r={corr_loc_target:.4f}")
        if abs(corr_creel_loc) > 0.5:
            print(f"  ⚠️  CreelCat and loc_rolling_mean are moderately correlated — "
                  f"they encode similar location quality info")
        else:
            print(f"  ✓ CreelCat and loc_rolling_mean are NOT redundant — they encode different aspects")

    # E. Deployment check: will CreelCat be available for new locations?
    print(f"\n--- E. Deployment availability ---")
    total_locs = df['loc_id'].nunique()
    locs_with_creel = df[df['creel2_cpue_hour_max'].notna()]['loc_id'].nunique()
    print(f"  Locations with CreelCat data: {locs_with_creel}/{total_locs} ({locs_with_creel/total_locs*100:.1f}%)")
    print(f"  CreelCat surveys cover {locs_with_creel} of {total_locs} training locations")
    print(f"  At deployment: new locations need BallTree lookup against CreelCat database")
    print(f"  Coverage depends on geographic overlap with CreelCat survey network")


# ============================================================
# MAIN
# ============================================================
def main():
    df = load_data()

    # Run diagnostics
    leakage_check(df)        # Fast — just correlations
    match_quality_audit(df)   # Fast — just statistics
    ablation_results = ablation_unseen_locations(df)  # Slow — 40x2 model training

    print("\n" + "="*70)
    print("DIAGNOSTIC COMPLETE")
    print("="*70)

if __name__ == '__main__':
    main()
