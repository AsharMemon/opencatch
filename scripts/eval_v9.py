"""Full 4-metric evaluation on v9 dataset (real weather + clean data)."""
import pandas as pd
import numpy as np
from sklearn.neighbors import NearestNeighbors
from sklearn.metrics import r2_score, mean_absolute_error
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import GroupKFold
from catboost import CatBoostRegressor
import warnings
import time

warnings.filterwarnings("ignore")

TARGET = "median_weight_lb"
ALWAYS_EXCLUDE = {"target_success_score", "tms_id", "event_id"}
LOCATION_IDENTITY = {"loc_enc", "trail_mean_weight", "location_mean_weight",
                     "loc_rolling_3", "baseline_signal"}
META_COLS = {"date", "location", "region", "block", "sat_source",
             "usgs_site_id", "event_name", "tournament_slug",
             "trail", "results_source", "species", "spawn_phase"}

MIN_LOO_STD = 0.5
MIN_LOO_N = 15

df = pd.read_csv("castline/validation/data/assembled/validation_dataset_v9.csv",
                 low_memory=False)
df = df[df[TARGET].notna()].copy()
print(f"Dataset: {len(df)} rows, {df.location.nunique()} locations")

# Features
all_cols = set(df.columns)
exclude = ALWAYS_EXCLUDE | LOCATION_IDENTITY | META_COLS | {TARGET}
full_features = sorted(all_cols - exclude)
full_features = [f for f in full_features if df[f].dtype in ["float64", "int64", "float32", "int32"]]
loo_features = [f for f in full_features if f not in LOCATION_IDENTITY]

loc_means = df.groupby("location")[TARGET].mean()
df["loc_mean_enc"] = df.location.map(loc_means)
full_with_loc = full_features + ["loc_mean_enc"]

# Count np_ features
np_cols = [f for f in full_features if f.startswith("np_")]
np_coverage = df[np_cols].notna().mean().mean() * 100 if np_cols else 0
print(f"Features: {len(full_with_loc)} full, {len(loo_features)} LOO")
print(f"NASA POWER features: {len(np_cols)}, avg coverage: {np_coverage:.1f}%")


def knn_encode(train_df, test_df=None, K=8):
    loc_stats = train_df.groupby("location").agg(
        mean_target=(TARGET, "mean"),
        n_events=(TARGET, "count"),
        lat=("lat", "first"), lon=("lon", "first"),
        area_acres=("area_acres", "first"),
        is_lake=("is_lake", "first"),
        creel_lmb_ratio=("creel_lmb_ratio", "first"),
        creel_smb_ratio=("creel_smb_ratio", "first"),
        creel_cpue_mean=("creel_cpue_mean", "first"),
    ).reset_index()

    morph = ["lat", "lon", "area_acres", "is_lake",
             "creel_lmb_ratio", "creel_smb_ratio", "creel_cpue_mean"]
    X = loc_stats[morph].fillna(loc_stats[morph].median()).values
    sc = StandardScaler().fit(X)
    Xs = sc.transform(X)
    nn = NearestNeighbors(n_neighbors=min(K+1, len(loc_stats)))
    nn.fit(Xs)

    cluster_map, region_map = {}, {}
    for i, row in loc_stats.iterrows():
        q = Xs[i:i+1]
        d, ix = nn.kneighbors(q)
        nbrs = [(dd, j) for dd, j in zip(d[0], ix[0])
                if loc_stats.iloc[j]["location"] != row["location"]][:K]
        if nbrs:
            w = [loc_stats.iloc[j]["n_events"]/(dd+0.01) for dd, j in nbrs]
            cluster_map[row["location"]] = sum(
                wi * loc_stats.iloc[j]["mean_target"]
                for wi, (_, j) in zip(w, nbrs)) / sum(w)
        else:
            cluster_map[row["location"]] = loc_stats["mean_target"].mean()

        d2, ix2 = nn.kneighbors(q, n_neighbors=min(21, len(loc_stats)))
        nbrs2 = [(dd, j) for dd, j in zip(d2[0], ix2[0])
                 if loc_stats.iloc[j]["location"] != row["location"]][:20]
        region_map[row["location"]] = (
            np.mean([loc_stats.iloc[j]["mean_target"] for _, j in nbrs2])
            if nbrs2 else loc_stats["mean_target"].mean())

    test_cluster, test_region = None, None
    if test_df is not None:
        tv = test_df[morph].fillna(loc_stats[morph].median().to_dict()).iloc[0:1].values
        ts = sc.transform(tv)
        d, ix = nn.kneighbors(ts, n_neighbors=min(K+1, len(loc_stats)))
        nbrs = [(dd, int(j)) for dd, j in zip(d[0], ix[0])][:K]
        w = [loc_stats.iloc[j]["n_events"]/(dd+0.01) for dd, j in nbrs]
        test_cluster = sum(wi * loc_stats.iloc[j]["mean_target"]
                          for wi, (_, j) in zip(w, nbrs)) / sum(w)
        test_region = np.mean([loc_stats.iloc[j]["mean_target"] for _, j in nbrs[:20]])

    return cluster_map, region_map, test_cluster, test_region


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
m = CatBoostRegressor(iterations=1000, depth=6, learning_rate=0.05,
                      l2_leaf_reg=3, verbose=0, random_seed=42)
m.fit(tr[full_with_loc], tr[TARGET])
pred = m.predict(te[full_with_loc])
r2_t = r2_score(te[TARGET], pred)
mae_t = mean_absolute_error(te[TARGET], pred)
imp = pd.Series(m.feature_importances_, index=full_with_loc).nlargest(10)
print(f"  R²={r2_t:.4f}  MAE={mae_t:.3f}lb  [{time.time()-t0:.1f}s]")
print(f"  Top 10: {list(zip(imp.index, imp.values.astype(int)))}")


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
    te_r = df[df._region == region]
    tr_r = df[df._region != region]
    if len(te_r) < 10:
        continue
    m = CatBoostRegressor(iterations=500, depth=6, learning_rate=0.05,
                          l2_leaf_reg=3, verbose=0, random_seed=42)
    m.fit(tr_r[full_with_loc], tr_r[TARGET])
    r2 = r2_score(te_r[TARGET], m.predict(te_r[full_with_loc]))
    region_r2s[region] = (r2, len(te_r))
    print(f"    {region}: R²={r2:.4f} (n={len(te_r)})")
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
    m = CatBoostRegressor(iterations=500, depth=6, learning_rate=0.05,
                          l2_leaf_reg=3, verbose=0, random_seed=42)
    m.fit(tr[full_with_loc], tr[TARGET])
    fold_r2s.append(r2_score(te[TARGET], m.predict(te[full_with_loc])))
r2_st = np.mean(fold_r2s)
print(f"  R²={r2_st:.4f} ± {np.std(fold_r2s):.4f}  [{time.time()-t0:.1f}s]")


# ═══════════════════════════════════════
# 4. LOO
# ═══════════════════════════════════════
print("\n" + "=" * 50)
print("4. LEAVE-ONE-LOCATION-OUT")
print("=" * 50)
t0 = time.time()

loc_counts = df.location.value_counts()
loc_std = df.groupby("location")[TARGET].std()
clean_locs = [l for l in loc_counts[loc_counts >= MIN_LOO_N].index
              if loc_std.get(l, 0) >= MIN_LOO_STD]
print(f"  Clean LOO set: {len(clean_locs)} locations (n>={MIN_LOO_N}, std>={MIN_LOO_STD})")

all_true, all_pred = [], []
per_loc = {}

for i, loc in enumerate(clean_locs):
    mask = df.location == loc
    train, test = df[~mask].copy(), df[mask].copy()

    cmap, rmap, tc, tr_enc = knn_encode(train, test)
    train["cluster_enc"] = train.location.map(cmap).fillna(train[TARGET].mean())
    train["region_enc"] = train.location.map(rmap).fillna(train[TARGET].mean())
    test["cluster_enc"] = tc
    test["region_enc"] = tr_enc

    use = loo_features + ["cluster_enc", "region_enc"]
    m = CatBoostRegressor(iterations=500, depth=6, learning_rate=0.05,
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
print("SUMMARY (v9: real weather + clean data)")
print("=" * 60)
print(f"  Temporal                 : R²={r2_t:.4f}  MAE={mae_t:.3f}")
print(f"  Spatial                  : R²={r2_s:.4f}")
print(f"  Spatiotemporal           : R²={r2_st:.4f}")
print(f"  LOO (clean)              : R²={r2_loo:.4f}  MAE={mae_loo:.3f}")
print(f"  LOO positive             : {positive}/{len(clean_locs)}")
print()
print("v8 baseline for comparison:")
print("  Temporal: 0.6678  Spatial: 0.7209  ST: 0.7519  LOO: 0.4925")
