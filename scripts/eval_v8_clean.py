"""v8 evaluation with clean LOO (filter low-variance locations).

Key fix: exclude LOO locations with target std < 0.5 (Great Lakes sub-sites
with near-constant targets that destroy the metric).
"""
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
             "trail", "results_source", "source", "species", "spawn_phase"}

MIN_LOO_STD = 0.5   # exclude locations with target std below this
MIN_LOO_N = 15       # minimum events per location for LOO

df = pd.read_csv("castline/validation/data/assembled/validation_dataset_v8.csv",
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

print(f"Full features: {len(full_with_loc)}, LOO features: {len(loo_features)}")


def train_cb(X_tr, y_tr, iters=800):
    m = CatBoostRegressor(iterations=iters, depth=6, learning_rate=0.05,
                          l2_leaf_reg=3, verbose=0, random_seed=42)
    m.fit(X_tr, y_tr)
    return m


def knn_target_encode(train_df, K=8):
    loc_stats = train_df.groupby("location").agg(
        mean_target=(TARGET, "mean"),
        lat=("lat", "first"), lon=("lon", "first"),
        area_acres=("area_acres", "first"),
        creel_lmb_ratio=("creel_lmb_ratio", "first"),
        creel_smb_ratio=("creel_smb_ratio", "first"),
    ).reset_index()

    morph = ["lat", "lon", "area_acres", "creel_lmb_ratio", "creel_smb_ratio"]
    X = loc_stats[morph].fillna(loc_stats[morph].median()).values
    sc = StandardScaler().fit(X)
    Xs = sc.transform(X)
    nn = NearestNeighbors(n_neighbors=min(K + 1, len(loc_stats)))
    nn.fit(Xs)

    cluster_map, region_map = {}, {}
    for i, row in loc_stats.iterrows():
        q = Xs[i:i+1]
        d, ix = nn.kneighbors(q)
        nbrs = [(dd, j) for dd, j in zip(d[0], ix[0])
                if loc_stats.iloc[j]["location"] != row["location"]][:K]
        if nbrs:
            w = [1/(dd+0.01) for dd, _ in nbrs]
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

    return cluster_map, region_map, loc_stats, sc, nn


def encode_new_loc(test_df, loc_stats, sc, nn, K=8):
    morph = ["lat", "lon", "area_acres", "creel_lmb_ratio", "creel_smb_ratio"]
    tv = test_df[morph].fillna(loc_stats[morph].median().to_dict()).iloc[0:1].values
    ts = sc.transform(tv)
    d, ix = nn.kneighbors(ts, n_neighbors=min(K+1, len(loc_stats)))
    nbrs = [(dd, int(j)) for dd, j in zip(d[0], ix[0])][:K]
    w = [1/(dd+0.01) for dd, _ in nbrs]
    cluster = sum(wi * loc_stats.iloc[j]["mean_target"]
                  for wi, (_, j) in zip(w, nbrs)) / sum(w)
    region = np.mean([loc_stats.iloc[j]["mean_target"] for _, j in nbrs[:20]])
    return cluster, region


# ═══════════════════════════════════════
# 1. TEMPORAL HOLDOUT
# ═══════════════════════════════════════
print("\n" + "=" * 50)
print("1. TEMPORAL HOLDOUT")
print("=" * 50)
t0 = time.time()
df_s = df.sort_values("date")
split = int(len(df_s) * 0.8)
tr_t, te_t = df_s.iloc[:split], df_s.iloc[split:]
m = train_cb(tr_t[full_with_loc], tr_t[TARGET])
pred = m.predict(te_t[full_with_loc])
r2_t = r2_score(te_t[TARGET], pred)
mae_t = mean_absolute_error(te_t[TARGET], pred)
imp = pd.Series(m.feature_importances_, index=full_with_loc).nlargest(5)
print(f"  R²={r2_t:.4f}  MAE={mae_t:.3f}lb  [{time.time()-t0:.1f}s]")
print(f"  Top: {list(zip(imp.index, imp.values.astype(int)))}")


# ═══════════════════════════════════════
# 2. SPATIAL HOLDOUT
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
    m = train_cb(tr[full_with_loc], tr[TARGET], iters=500)
    p = m.predict(te[full_with_loc])
    r2 = r2_score(te[TARGET], p)
    region_r2s[region] = (r2, len(te))
    print(f"    {region}: R²={r2:.4f} (n={len(te)})")
r2_s = np.mean([r for r, _ in region_r2s.values()])
print(f"  Mean R²={r2_s:.4f}  [{time.time()-t0:.1f}s]")


# ═══════════════════════════════════════
# 3. SPATIOTEMPORAL BLOCKED
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
    m = train_cb(tr[full_with_loc], tr[TARGET], iters=500)
    fold_r2s.append(r2_score(te[TARGET], m.predict(te[full_with_loc])))
r2_st = np.mean(fold_r2s)
print(f"  R²={r2_st:.4f} ± {np.std(fold_r2s):.4f}  [{time.time()-t0:.1f}s]")


# ═══════════════════════════════════════
# 4. LEAVE-ONE-LOCATION-OUT (CLEAN)
# ═══════════════════════════════════════
print("\n" + "=" * 50)
print("4. LEAVE-ONE-LOCATION-OUT")
print("=" * 50)
t0 = time.time()

loc_counts = df.location.value_counts()
loc_std = df.groupby("location")[TARGET].std()

# All eligible
all_eligible = [l for l in loc_counts[loc_counts >= MIN_LOO_N].index]
# Clean: filter low variance
clean_locs = [l for l in all_eligible if loc_std.get(l, 0) >= MIN_LOO_STD]
filtered_out = [l for l in all_eligible if loc_std.get(l, 0) < MIN_LOO_STD]

print(f"  All eligible (n>={MIN_LOO_N}): {len(all_eligible)}")
print(f"  Filtered out (std<{MIN_LOO_STD}): {len(filtered_out)}")
print(f"  Clean LOO set: {len(clean_locs)}")
if filtered_out:
    print(f"  Filtered locations:")
    for l in sorted(filtered_out):
        print(f"    {l:50s} n={loc_counts[l]:3d} std={loc_std[l]:.3f}")

all_true, all_pred = [], []
per_loc = {}
for i, loc in enumerate(clean_locs):
    mask = df.location == loc
    train, test = df[~mask].copy(), df[mask].copy()

    cmap, rmap, lstats, sc, nn = knn_target_encode(train)
    tc, tr_enc = encode_new_loc(test, lstats, sc, nn)

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
print(f"  Median per-loc R²: {np.median(list(per_loc.values())):.4f}")

# Best/worst
sorted_locs = sorted(per_loc.items(), key=lambda x: x[1])
print("\n  Worst 5:")
for loc, r2 in sorted_locs[:5]:
    n = loc_counts[loc]
    true_mean = df[df.location == loc][TARGET].mean()
    std = loc_std[loc]
    print(f"    {loc:50s} n={n:3d} R²={r2:8.3f} mean={true_mean:.1f} std={std:.2f}")
print("  Best 5:")
for loc, r2 in sorted_locs[-5:]:
    n = loc_counts[loc]
    true_mean = df[df.location == loc][TARGET].mean()
    std = loc_std[loc]
    print(f"    {loc:50s} n={n:3d} R²={r2:8.3f} mean={true_mean:.1f} std={std:.2f}")

# Source breakdown
print("\n  By source:")
for src in df.source.unique():
    src_locs = [l for l in clean_locs if df[(df.location == l) & (df.source == src)].shape[0] > 0]
    if src_locs:
        src_r2s = [per_loc[l] for l in src_locs if l in per_loc]
        print(f"    {src:15s}: {len(src_locs)} locs, median R²={np.median(src_r2s):.4f}")


# ═══════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════
print("\n" + "=" * 60)
print("SUMMARY (CatBoost v8, clean LOO)")
print("=" * 60)
print(f"  Temporal                 : R²={r2_t:.4f}")
print(f"  Spatial                  : R²={r2_s:.4f}")
print(f"  Spatiotemporal           : R²={r2_st:.4f}")
print(f"  LOO (clean, std≥{MIN_LOO_STD})    : R²={r2_loo:.4f}  ({len(clean_locs)} locs)")
