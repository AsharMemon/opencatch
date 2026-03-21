"""v8 eval v2: fix data quality + source encoding + dedup + better KNN."""
import pandas as pd
import numpy as np
from sklearn.neighbors import NearestNeighbors
from sklearn.metrics import r2_score, mean_absolute_error
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import GroupKFold
from catboost import CatBoostRegressor
import warnings
import time
import re

warnings.filterwarnings("ignore")

TARGET = "median_weight_lb"
ALWAYS_EXCLUDE = {"target_success_score", "tms_id", "event_id"}
LOCATION_IDENTITY = {"loc_enc", "trail_mean_weight", "location_mean_weight",
                     "loc_rolling_3", "baseline_signal"}
META_COLS = {"date", "location", "region", "block", "sat_source",
             "usgs_site_id", "event_name", "tournament_slug",
             "trail", "results_source", "species", "spawn_phase"}
# NOTE: "source" removed from META_COLS — it's informative!

MIN_LOO_STD = 0.5
MIN_LOO_N = 15

df = pd.read_csv("castline/validation/data/assembled/validation_dataset_v8.csv",
                 low_memory=False)
df = df[df[TARGET].notna()].copy()

# ═══════════════════════════════════════
# DATA QUALITY FIXES
# ═══════════════════════════════════════

# 1. Remove Statewide aggregations
statewide = df.location.str.contains("Statewide", case=False, na=False)
print(f"Removing {statewide.sum()} 'Statewide' rows")
df = df[~statewide].copy()

# 2. Fix area_acres = 0 → NaN
zero_area = df.area_acres == 0
print(f"Fixing {zero_area.sum()} rows with area_acres=0 → NaN")
df.loc[zero_area, "area_acres"] = np.nan

# 3. Deduplicate near-duplicate locations (same lake, different names)
dedup_map = {}
loc_stats = df.groupby("location").agg(
    lat=("lat", "first"), lon=("lon", "first"),
    n=(TARGET, "count"), mean_t=(TARGET, "mean"),
).reset_index()

# Find locations within 0.05 degrees with similar names
for i, r1 in loc_stats.iterrows():
    for j, r2 in loc_stats.iterrows():
        if j <= i:
            continue
        dist = np.sqrt((r1.lat - r2.lat)**2 + (r1.lon - r2.lon)**2)
        if dist < 0.05:
            # Check name similarity
            n1 = re.sub(r'[^a-z]', '', r1.location.lower().split(',')[0])
            n2 = re.sub(r'[^a-z]', '', r2.location.lower().split(',')[0])
            # If one name contains the other
            if n1 in n2 or n2 in n1 or (len(n1) > 5 and len(n2) > 5 and
                    len(set(n1.split()) & set(n2.split())) / max(len(n1.split()), len(n2.split()), 1) > 0.5):
                # Keep the one with more events
                if r1.n >= r2.n:
                    dedup_map[r2.location] = r1.location
                else:
                    dedup_map[r1.location] = r2.location

if dedup_map:
    print(f"Deduplicating {len(dedup_map)} location pairs:")
    for old, new in sorted(dedup_map.items()):
        print(f"  {old} → {new}")
    df["location"] = df.location.map(lambda x: dedup_map.get(x, x))

# 4. Source encoding
source_means = df.groupby("source")[TARGET].mean()
df["source_enc"] = df.source.map(source_means)
print(f"\nSource means: {dict(source_means.round(2))}")

print(f"\nClean dataset: {len(df)} rows, {df.location.nunique()} locations")

# Features — include source_enc
all_cols = set(df.columns)
exclude = ALWAYS_EXCLUDE | LOCATION_IDENTITY | META_COLS | {TARGET}
full_features = sorted(all_cols - exclude)
full_features = [f for f in full_features if df[f].dtype in ["float64", "int64", "float32", "int32"]]
loo_features = [f for f in full_features if f not in LOCATION_IDENTITY]

loc_means = df.groupby("location")[TARGET].mean()
df["loc_mean_enc"] = df.location.map(loc_means)
full_with_loc = full_features + ["loc_mean_enc"]

print(f"Features: {len(full_with_loc)} (full), {len(loo_features)} (LOO)")
print(f"  source_enc in loo_features: {'source_enc' in loo_features}")


def knn_encode(train_df, test_df=None, Ks=(3, 8, 20)):
    """Multi-scale KNN with better morphometry."""
    loc_stats = train_df.groupby("location").agg(
        mean_target=(TARGET, "mean"),
        n_events=(TARGET, "count"),
        lat=("lat", "first"), lon=("lon", "first"),
        area_acres=("area_acres", "first"),
        is_lake=("is_lake", "first"),
        creel_lmb_ratio=("creel_lmb_ratio", "first"),
        creel_smb_ratio=("creel_smb_ratio", "first"),
        creel_cpue_mean=("creel_cpue_mean", "first"),
        source_enc=("source_enc", "first"),
    ).reset_index()

    morph = ["lat", "lon", "area_acres", "is_lake",
             "creel_lmb_ratio", "creel_smb_ratio", "creel_cpue_mean", "source_enc"]
    X = loc_stats[morph].fillna(loc_stats[morph].median()).values
    sc = StandardScaler().fit(X)
    Xs = sc.transform(X)
    max_k = max(Ks) + 1
    nn = NearestNeighbors(n_neighbors=min(max_k, len(loc_stats)))
    nn.fit(Xs)

    all_encs = {f"knn_{k}_mean": {} for k in Ks}
    all_encs.update({f"knn_{k}_wmean": {} for k in Ks})

    for i, row in loc_stats.iterrows():
        q = Xs[i:i+1]
        d, ix = nn.kneighbors(q, n_neighbors=min(max_k, len(loc_stats)))
        nbrs = [(dd, j) for dd, j in zip(d[0], ix[0])
                if loc_stats.iloc[j]["location"] != row["location"]]

        for k in Ks:
            kn = nbrs[:k]
            if kn:
                # Distance-weighted
                w = [1/(dd+0.01) for dd, _ in kn]
                all_encs[f"knn_{k}_wmean"][row["location"]] = sum(
                    wi * loc_stats.iloc[j]["mean_target"]
                    for wi, (_, j) in zip(w, kn)) / sum(w)
                # Count-weighted (trust locations with more data)
                cw = [loc_stats.iloc[j]["n_events"] / (dd + 0.1) for dd, j in kn]
                all_encs[f"knn_{k}_mean"][row["location"]] = sum(
                    wi * loc_stats.iloc[j]["mean_target"]
                    for wi, (_, j) in zip(cw, kn)) / sum(cw)
            else:
                gm = loc_stats["mean_target"].mean()
                all_encs[f"knn_{k}_wmean"][row["location"]] = gm
                all_encs[f"knn_{k}_mean"][row["location"]] = gm

    test_encs = {}
    if test_df is not None:
        tv = test_df[morph].fillna(loc_stats[morph].median().to_dict()).iloc[0:1].values
        ts = sc.transform(tv)
        d, ix = nn.kneighbors(ts, n_neighbors=min(max_k, len(loc_stats)))
        nbrs = [(dd, int(j)) for dd, j in zip(d[0], ix[0])]

        for k in Ks:
            kn = nbrs[:k]
            w = [1/(dd+0.01) for dd, _ in kn]
            test_encs[f"knn_{k}_wmean"] = sum(
                wi * loc_stats.iloc[j]["mean_target"]
                for wi, (_, j) in zip(w, kn)) / sum(w)
            cw = [loc_stats.iloc[j]["n_events"] / (dd + 0.1) for dd, j in kn]
            test_encs[f"knn_{k}_mean"] = sum(
                wi * loc_stats.iloc[j]["mean_target"]
                for wi, (_, j) in zip(cw, kn)) / sum(cw)

    return all_encs, test_encs


knn_cols = ["knn_3_mean", "knn_3_wmean", "knn_8_mean", "knn_8_wmean",
            "knn_20_mean", "knn_20_wmean"]

# ═══════════════════════════════════════
# 1. TEMPORAL
# ═══════════════════════════════════════
print("\n" + "=" * 50)
print("1. TEMPORAL HOLDOUT")
print("=" * 50)
t0 = time.time()
df_s = df.sort_values("date")
split = int(len(df_s) * 0.8)
tr, te = df_s.iloc[:split], df_s.iloc[split:]
m = CatBoostRegressor(iterations=1000, depth=7, learning_rate=0.03,
                      l2_leaf_reg=3, verbose=0, random_seed=42)
m.fit(tr[full_with_loc], tr[TARGET])
r2_t = r2_score(te[TARGET], m.predict(te[full_with_loc]))
print(f"  R²={r2_t:.4f}  [{time.time()-t0:.1f}s]")

# ═══════════════════════════════════════
# 2. SPATIAL
# ═══════════════════════════════════════
print("\n" + "=" * 50)
print("2. SPATIAL HOLDOUT")
print("=" * 50)
t0 = time.time()
df["_region"] = pd.cut(df.lat, bins=5, labels=["S", "SM", "M", "MN", "N"])
region_r2s = {}
for region in df["_region"].unique():
    te = df[df._region == region]
    tr = df[df._region != region]
    if len(te) < 10:
        continue
    m = CatBoostRegressor(iterations=600, depth=7, learning_rate=0.03,
                          l2_leaf_reg=3, verbose=0, random_seed=42)
    m.fit(tr[full_with_loc], tr[TARGET])
    r2 = r2_score(te[TARGET], m.predict(te[full_with_loc]))
    region_r2s[region] = (r2, len(te))
    print(f"    {region}: R²={r2:.4f} (n={len(te)})")
r2_s = np.mean([r for r, _ in region_r2s.values()])
print(f"  Mean R²={r2_s:.4f}  [{time.time()-t0:.1f}s]")

# ═══════════════════════════════════════
# 3. SPATIOTEMPORAL
# ═══════════════════════════════════════
print("\n" + "=" * 50)
print("3. SPATIOTEMPORAL BLOCKED")
print("=" * 50)
t0 = time.time()
df["_block"] = df["_region"].astype(str) + "_" + (df.year // 3).astype(str)
gkf = GroupKFold(n_splits=5)
fold_r2s = []
for tr_idx, te_idx in gkf.split(df, groups=df["_block"]):
    tr, te = df.iloc[tr_idx], df.iloc[te_idx]
    m = CatBoostRegressor(iterations=600, depth=7, learning_rate=0.03,
                          l2_leaf_reg=3, verbose=0, random_seed=42)
    m.fit(tr[full_with_loc], tr[TARGET])
    fold_r2s.append(r2_score(te[TARGET], m.predict(te[full_with_loc])))
r2_st = np.mean(fold_r2s)
print(f"  R²={r2_st:.4f} ± {np.std(fold_r2s):.4f}  [{time.time()-t0:.1f}s]")

# ═══════════════════════════════════════
# 4. LOO
# ═══════════════════════════════════════
print("\n" + "=" * 50)
print("4. LOO (multi-scale KNN + source)")
print("=" * 50)
t0 = time.time()

loc_counts = df.location.value_counts()
loc_std = df.groupby("location")[TARGET].std()
clean_locs = [l for l in loc_counts[loc_counts >= MIN_LOO_N].index
              if loc_std.get(l, 0) >= MIN_LOO_STD]
print(f"  Clean LOO set: {len(clean_locs)} locations")

all_true, all_pred = [], []
per_loc = {}

for i, loc in enumerate(clean_locs):
    mask = df.location == loc
    train, test = df[~mask].copy(), df[mask].copy()

    encs, tencs = knn_encode(train, test)
    for col in knn_cols:
        train[col] = train.location.map(encs[col]).fillna(train[TARGET].mean())
        test[col] = tencs[col]

    use = loo_features + knn_cols
    m = CatBoostRegressor(iterations=600, depth=7, learning_rate=0.03,
                          l2_leaf_reg=3, verbose=0, random_seed=42)
    m.fit(train[use], train[TARGET])
    pred = np.clip(m.predict(test[use]), 0, 30)

    r2 = r2_score(test[TARGET], pred) if len(test) > 1 and np.std(test[TARGET]) > 0.01 else 0.0
    per_loc[loc] = r2

    if (i + 1) % 10 == 0:
        interim = r2_score(all_true + list(test[TARGET]), all_pred + list(pred))
        print(f"  {i+1}/{len(clean_locs)} interim R²={interim:.4f}")

    all_true.extend(test[TARGET].tolist())
    all_pred.extend(pred.tolist())

r2_loo = r2_score(all_true, all_pred)
mae_loo = mean_absolute_error(all_true, all_pred)
positive = sum(1 for r in per_loc.values() if r > 0)
print(f"\n  Overall LOO R²={r2_loo:.4f}  MAE={mae_loo:.3f}lb  [{time.time()-t0:.1f}s]")
print(f"  Positive R²: {positive}/{len(clean_locs)}")
print(f"  Median: {np.median(list(per_loc.values())):.4f}")

sorted_locs = sorted(per_loc.items(), key=lambda x: x[1])
print("\n  Worst 5:")
for loc, r2 in sorted_locs[:5]:
    n = loc_counts[loc]
    mn = df[df.location == loc][TARGET].mean()
    print(f"    {loc:50s} n={n:3d} R²={r2:8.3f} mean={mn:.1f} std={loc_std[loc]:.2f}")
print("  Best 5:")
for loc, r2 in sorted_locs[-5:]:
    n = loc_counts[loc]
    mn = df[df.location == loc][TARGET].mean()
    print(f"    {loc:50s} n={n:3d} R²={r2:8.3f} mean={mn:.1f} std={loc_std[loc]:.2f}")

# ═══════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════
print("\n" + "=" * 60)
print("SUMMARY (v8-v2: source + dedup + area fix + tuned CatBoost)")
print("=" * 60)
print(f"  Temporal                 : R²={r2_t:.4f}")
print(f"  Spatial                  : R²={r2_s:.4f}")
print(f"  Spatiotemporal           : R²={r2_st:.4f}")
print(f"  LOO (clean)              : R²={r2_loo:.4f}  ({len(clean_locs)} locs)")
print(f"  LOO MAE                  : {mae_loo:.3f} lb")
print(f"  LOO positive             : {positive}/{len(clean_locs)}")
