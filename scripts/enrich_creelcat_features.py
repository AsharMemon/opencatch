"""
CreelCat Feature Engineering — Extract historical bass CPUE features per location.

Matches tournament locations to nearby CreelCat survey water bodies using BallTree
spatial search, then computes per-location features from historical creel surveys.
"""
import pandas as pd
import numpy as np
from sklearn.neighbors import BallTree
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')

ROOT = Path(__file__).resolve().parent.parent
CREEL_FISH = ROOT / "castline/validation/data/raw/state_dnr/creelcat/FishDataCompiled.csv"
CREEL_SURVEY = ROOT / "castline/validation/data/raw/state_dnr/creelcat/Survey_Data.csv"
V15_PATH = ROOT / "castline/validation/data/assembled/validation_dataset_v15.csv"
OUT_PATH = ROOT / "castline/validation/data/raw/creelcat_spatial_features.csv"

SEARCH_RADIUS_KM = 75  # wider than USGS (50km) since creel surveys are sparser
EARTH_RADIUS_KM = 6371.0

# Bass species (true bass only, exclude buffalo/redhorse)
LMB_TAXA = ['Micropterus salmoides (Largemouth Bass)', 'Micropterus salmoides (Tournament)', 'Largemouth Bass']
SMB_TAXA = ['Micropterus dolomieu (Smallmouth Bass)']
SPOTTED_TAXA = ['Micropterus punctulatus (Spotted Bass)']


def load_creel_data():
    """Load and merge CreelCat fish + survey data."""
    print("Loading CreelCat data...")
    fish = pd.read_csv(CREEL_FISH, low_memory=False)
    survey = pd.read_csv(CREEL_SURVEY, low_memory=False)
    
    # Filter to bass
    all_bass = LMB_TAXA + SMB_TAXA + SPOTTED_TAXA
    bass = fish[fish['Taxa'].isin(all_bass)].copy()
    
    # Tag species
    bass['is_lmb'] = bass['Taxa'].isin(LMB_TAXA).astype(int)
    bass['is_smb'] = bass['Taxa'].isin(SMB_TAXA).astype(int)
    bass['is_spotted'] = bass['Taxa'].isin(SPOTTED_TAXA).astype(int)
    
    # Merge with survey for lat/lon
    survey_cols = ['Survey_ID', 'Lat', 'Lon', 'State_Ab', 'Waterbody_Name', 'Year', 
                   'Reported_Acres', 'WB_Type']
    bass = bass.merge(survey[survey_cols], on='Survey_ID', how='left')
    bass = bass[bass['Lat'].notna() & bass['Lon'].notna()].copy()
    
    print(f"  Bass records with location: {len(bass):,}")
    print(f"  Unique water bodies: {bass.groupby(['Lat','Lon']).ngroups}")
    print(f"  LMB: {bass['is_lmb'].sum()}, SMB: {bass['is_smb'].sum()}, Spotted: {bass['is_spotted'].sum()}")
    
    return bass


def compute_waterbody_summaries(bass: pd.DataFrame) -> pd.DataFrame:
    """Aggregate bass data per water body (lat/lon)."""
    print("Computing per-waterbody summaries...")
    
    # Group by location
    grouped = bass.groupby(['Lat', 'Lon'])
    
    summaries = []
    for (lat, lon), grp in grouped:
        rec = {
            'lat': lat,
            'lon': lon,
            'creel2_n_surveys': grp['Survey_ID'].nunique(),
            'creel2_n_bass_records': len(grp),
            # Species presence
            'creel2_has_lmb': int(grp['is_lmb'].any()),
            'creel2_has_smb': int(grp['is_smb'].any()),
            'creel2_has_spotted': int(grp['is_spotted'].any()),
            'creel2_bass_species_count': int(grp['is_lmb'].any()) + int(grp['is_smb'].any()) + int(grp['is_spotted'].any()),
            # CPUE metrics (use median — robust to outliers)
            'creel2_cpue_hour_median': grp['Catch_Per_Hour'].median(),
            'creel2_cpue_hour_max': grp['Catch_Per_Hour'].max(),
            'creel2_cpue_day_median': grp['Catch_Per_Day'].median(),
            # Per-species CPUE
            'creel2_lmb_cpue_hour': grp.loc[grp['is_lmb']==1, 'Catch_Per_Hour'].median(),
            'creel2_smb_cpue_hour': grp.loc[grp['is_smb']==1, 'Catch_Per_Hour'].median(),
            # Catch totals
            'creel2_total_catch': grp['Catch'].sum() if 'Catch' in grp.columns else np.nan,
            'creel2_total_harvest': grp['Harvest'].sum() if 'Harvest' in grp.columns else np.nan,
            # Release rate (conservation indicator)
            'creel2_release_pct': grp['Released_Percent'].median() if 'Released_Percent' in grp.columns else np.nan,
            # Year range
            'creel2_year_min': grp['Year'].min(),
            'creel2_year_max': grp['Year'].max(),
            'creel2_year_span': grp['Year'].max() - grp['Year'].min(),
        }
        summaries.append(rec)
    
    wb = pd.DataFrame(summaries)
    print(f"  Water bodies summarized: {len(wb)}")
    return wb


def spatial_match_features(tournament_locs: pd.DataFrame, wb_summaries: pd.DataFrame) -> pd.DataFrame:
    """Match tournament locations to nearby CreelCat water bodies via BallTree."""
    print(f"\nSpatial matching {len(tournament_locs)} tournament locations to {len(wb_summaries)} CreelCat water bodies...")
    
    # Build BallTree from CreelCat water bodies
    wb_rads = np.radians(wb_summaries[['lat', 'lon']].values)
    tree = BallTree(wb_rads, metric='haversine')
    
    tour_rads = np.radians(tournament_locs[['lat', 'lon']].values)
    radius_rad = SEARCH_RADIUS_KM / EARTH_RADIUS_KM
    
    results = []
    matched = 0
    
    for i in range(len(tournament_locs)):
        lat = tournament_locs.iloc[i]['lat']
        lon = tournament_locs.iloc[i]['lon']
        
        # Find all CreelCat water bodies within radius
        indices, distances = tree.query_radius(tour_rads[i:i+1], r=radius_rad, 
                                                return_distance=True)
        idx = indices[0]
        dist_km = distances[0] * EARTH_RADIUS_KM
        
        rec = {'lat': lat, 'lon': lon}
        
        if len(idx) == 0:
            # No matches — all NaN
            for col in wb_summaries.columns:
                if col.startswith('creel2_'):
                    rec[col] = np.nan
            rec['creel2_nearby_waterbodies'] = 0
            rec['creel2_nearest_km'] = np.nan
        else:
            matched += 1
            nearby = wb_summaries.iloc[idx].copy()
            nearby['dist_km'] = dist_km
            
            # Distance-weighted averages (inverse distance weighting)
            weights = 1.0 / (dist_km + 1.0)  # +1 to avoid div by zero
            weights = weights / weights.sum()
            
            rec['creel2_nearby_waterbodies'] = len(idx)
            rec['creel2_nearest_km'] = dist_km.min()
            
            # Weighted averages for numeric features
            for col in ['creel2_cpue_hour_median', 'creel2_cpue_day_median', 
                        'creel2_cpue_hour_max', 'creel2_release_pct']:
                valid = nearby[col].notna()
                if valid.any():
                    rec[col] = np.average(nearby.loc[valid, col], weights=weights[valid])
                else:
                    rec[col] = np.nan
            
            # Max/sum for presence features
            rec['creel2_has_lmb'] = int(nearby['creel2_has_lmb'].max())
            rec['creel2_has_smb'] = int(nearby['creel2_has_smb'].max())
            rec['creel2_has_spotted'] = int(nearby['creel2_has_spotted'].max())
            rec['creel2_bass_species_count'] = int(nearby['creel2_bass_species_count'].max())
            
            # Best nearby CPUE (max of medians from each water body)
            rec['creel2_best_lmb_cpue'] = nearby['creel2_lmb_cpue_hour'].max()
            rec['creel2_best_smb_cpue'] = nearby['creel2_smb_cpue_hour'].max()
            
            # Survey density (more surveys = more confidence)
            rec['creel2_total_surveys_nearby'] = int(nearby['creel2_n_surveys'].sum())
            rec['creel2_n_bass_records'] = int(nearby['creel2_n_bass_records'].sum())
            
            # Year coverage
            rec['creel2_year_min'] = int(nearby['creel2_year_min'].min())
            rec['creel2_year_max'] = int(nearby['creel2_year_max'].max())
            rec['creel2_year_span'] = rec['creel2_year_max'] - rec['creel2_year_min']
        
        results.append(rec)
    
    out = pd.DataFrame(results)
    print(f"  Matched: {matched}/{len(tournament_locs)} ({matched/len(tournament_locs)*100:.1f}%)")
    print(f"  Output columns: {len(out.columns)}")
    return out


def main():
    # Load CreelCat
    bass = load_creel_data()
    wb_summaries = compute_waterbody_summaries(bass)
    
    # Load tournament locations
    print("\nLoading tournament locations...")
    v15 = pd.read_csv(V15_PATH, usecols=['lat', 'lon'])
    locs = v15.drop_duplicates(subset=['lat', 'lon']).reset_index(drop=True)
    print(f"  Unique tournament locations: {len(locs)}")
    
    # Spatial matching
    features = spatial_match_features(locs, wb_summaries)
    
    # Save
    features.to_csv(OUT_PATH, index=False)
    print(f"\nSaved to {OUT_PATH}")
    print(f"  Shape: {features.shape}")
    
    # Summary stats
    creel_cols = [c for c in features.columns if c.startswith('creel2_')]
    print(f"\n{'Feature':<35} {'Non-null':>8} {'Mean':>10} {'Median':>10}")
    print("-" * 70)
    for col in creel_cols:
        nn = features[col].notna().sum()
        if features[col].dtype in ['float64', 'int64']:
            mn = features[col].mean()
            md = features[col].median()
            print(f"  {col:<33} {nn:>8} {mn:>10.3f} {md:>10.3f}")
        else:
            print(f"  {col:<33} {nn:>8}")


if __name__ == '__main__':
    main()
