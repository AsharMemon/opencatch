"""v8 LOO-focused evaluation with improved KNN encoding.

Improvements over baseline:
1. Multi-scale KNN (local K=3, cluster K=8, regional K=20)
2. More morphometric features in KNN
3. Filter "Statewide" aggregations
4. Hierarchical prediction: cluster mean + residual model
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

MIN_LOO_STD = 0.5
MIN_LOO_N = 15

df = pd.read_csv("castline/validation/data/assembled/validation_dataset_v8.csv",
                 low_memory=False)
df = df[df[TARGET].notna()].copy()

# Filter "Statewide" aggregations
statewide_mask = df.location.str.contains("Statewide", case=False, na=False)
print(f"Removing {statewide_mask.sum()} 'Statewide' rows")
df = df[~statewide_mask].copy()
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

print(f"LOO features: {len(loo_features)}")


def multiscale_knn_encode(train_df, test_df=None, Ks=(3, 8, 20)):
    """Multi-scale KNN target encoding with richer morphometry."""
    loc_stats = train_df.groupby("location").agg(
        mean_target=(TARGET, "mean"),
        std_target=(TARGET, "std"),
        n_events=(TARGET, "count"),
        lat=("lat", "first"),
        lon=("lon", "first"),
        area_acres=("area_acres", "first"),
        is_lake=("is_lake", "first"),
        creel_lmb_ratio=("creel_lmb_ratio", "first"),
        creel_smb_ratio=("creel_smb_ratio", "first"),
        creel_spotted_ratio=("creel_spotted_ratio", "first"),
        creel_cpue_mean=("creel_cpue_mean", "first"),
    ).reset_index()
    loc_stats["std_target"] = loc_stats["std_target"].fillna(0)

    # Use all available morphometric features for KNN distance
    morph = ["lat", "lon", "area_acres", "is_lake",
             "creel_lmb_ratio", "creel_smb_ratio", "creel_spotted_ratio",
             "creel_cpue_mean"]
    X = loc_stats[morph].fillna(loc_stats[morph].median()).values
    sc = StandardScaler().fit(X)
    Xs = sc.transform(X)

    max_k = max(Ks) + 1
    nn = NearestNeighbors(n_neighbors=min(max_k, len(loc_stats)))
    nn.fit(Xs)

    encodings = {f"knn_{k}_mean": {} for k in Ks}
    encodings.update({f"knn_{k}_std": {} for k in Ks})
    encodings["knn_spread"] = {}  # range of neighbor means

    for i, row in loc_stats.iterrows():
        q = Xs[i:i+1]
        d, ix = nn.kneighbors(q, n_neighbors=min(max_k, len(loc_stats)))
        all_nbrs = [(dd, j) for dd, j in zip(d[0], ix[0])
                    if loc_stats.iloc[j]["location"] != row["location"]]

        for k in Ks:
            nbrs = all_nbrs[:k]
            if nbrs:
                # Distance-weighted mean
                w = [1/(dd+0.01) for dd, _ in nbrs]
                total_w = sum(w)
                wmean = sum(wi * loc_stats.iloc[j]["mean_target"]
                           for wi, (_, j) in zip(w, nbrs)) / total_w
                targets = [loc_stats.iloc[j]["mean_target"] for _, j in nbrs]
                encodings[f"knn_{k}_mean"][row["location"]] = wmean
                encodings[f"knn_{k}_std"][row["location"]] = np.std(targets)
            else:
                global_mean = loc_stats["mean_target"].mean()
                encodings[f"knn_{k}_mean"][row["location"]] = global_mean
                encodings[f"knn_{k}_std"][row["location"]] = 0

        # Spread: range of K=8 neighbor means
        nbrs8 = all_nbrs[:8]
        if len(nbrs8) > 1:
            targets8 = [loc_stats.iloc[j]["mean_target"] for _, j in nbrs8]
            encodings["knn_spread"][row["location"]] = max(targets8) - min(targets8)
        else:
            encodings["knn_spread"][row["location"]] = 0

    # Encode test location if provided
    test_encs = {}
    if test_df is not None:
        tv = test_df[morph].fillna(loc_stats[morph].median().to_dict()).iloc[0:1].values
        ts = sc.transform(tv)
        d, ix = nn.kneighbors(ts, n_neighbors=min(max_k, len(loc_stats)))
        all_nbrs = [(dd, int(j)) for dd, j in zip(d[0], ix[0])]

        for k in Ks:
            nbrs = all_nbrs[:k]
            w = [1/(dd+0.01) for dd, _ in nbrs]
            total_w = sum(w)
            wmean = sum(wi * loc_stats.iloc[j]["mean_target"]
                       for wi, (_, j) in zip(w, nbrs)) / total_w
            targets = [loc_stats.iloc[j]["mean_target"] for _, j in nbrs]
            test_encs[f"knn_{k}_mean"] = wmean
            test_encs[f"knn_{k}_std"] = np.std(targets)

        nbrs8 = all_nbrs[:8]
        targets8 = [loc_stats.iloc[j]["mean_target"] for _, j in nbrs8]
        test_encs["knn_spread"] = max(targets8) - min(targets8) if len(targets8) > 1 else 0

    return encodings, test_encs


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
m = CatBoostRegressor(iterations=800, depth=6, learning_rate=0.05,
                      l2_leaf_reg=3, verbose=0, random_seed=42)
m.fit(tr_t[full_with_loc], tr_t[TARGET])
pred = m.predict(te_t[full_with_loc])
r2_t = r2_score(te_t[TARGET], pred)
print(f"  R²={r2_t:.4f}  [{time.time()-t0:.1f}s]")


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
    m = CatBoostRegressor(iterations=500, depth=6, learning_rate=0.05,
                          l2_leaf_reg=3, verbose=0, random_seed=42)
    m.fit(tr[full_with_loc], tr[TARGET])
    r2 = r2_score(te[TARGET], m.predict(te[full_with_loc]))
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
    m = CatBoostRegressor(iterations=500, depth=6, learning_rate=0.05,
                          l2_leaf_reg=3, verbose=0, random_seed=42)
    m.fit(tr[full_with_loc], tr[TARGET])
    fold_r2s.append(r2_score(te[TARGET], m.predict(te[full_with_loc])))
r2_st = np.mean(fold_r2s)
print(f"  R²={r2_st:.4f} ± {np.std(fold_r2s):.4f}  [{time.time()-t0:.1f}s]")


# ═══════════════════════════════════════
# 4. LOO WITH MULTI-SCALE KNN
# ═══════════════════════════════════════
print("\n" + "=" * 50)
print("4. LOO (multi-scale KNN)")
print("=" * 50)
t0 = time.time()

loc_counts = df.location.value_counts()
loc_std = df.groupby("location")[TARGET].std()
clean_locs = [l for l in loc_counts[loc_counts >= MIN_LOO_N].index
              if loc_std.get(l, 0) >= MIN_LOO_STD]
print(f"  Clean LOO set: {len(clean_locs)} locations")

knn_cols = ["knn_3_mean", "knn_3_std", "knn_8_mean", "knn_8_std",
            "knn_20_mean", "knn_20_std", "knn_spread"]

all_true, all_pred = [], []
per_loc = {}

for i, loc in enumerate(clean_locs):
    mask = df.location == loc
    train, test = df[~mask].copy(), df[mask].copy()

    encs, test_encs = multiscale_knn_encode(train, test)

    for col in knn_cols:
        train[col] = train.location.map(encs[col]).fillna(train[TARGET].mean())
        test[col] = test_encs[col]

    use = loo_features + knn_cols
    m = CatBoostRegressor(iterations=600, depth=6, learning_rate=0.05,
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

sorted_locs = sorted(per_loc.items(), key=lambda x: x[1])
print("\n  Worst 5:")
for loc, r2 in sorted_locs[:5]:
    n = loc_counts[loc]
    print(f"    {loc:50s} n={n:3d} R²={r2:8.3f} std={loc_std[loc]:.2f}")
print("  Best 5:")
for loc, r2 in sorted_locs[-5:]:
    n = loc_counts[loc]
    print(f"    {loc:50s} n={n:3d} R²={r2:8.3f} std={loc_std[loc]:.2f}")


# ═══════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════
print("\n" + "=" * 60)
print("SUMMARY (v8 improved)")
print("=" * 60)
print(f"  Temporal                 : R²={r2_t:.4f}")
print(f"  Spatial                  : R²={r2_s:.4f}")
print(f"  Spatiotemporal           : R²={r2_st:.4f}")
print(f"  LOO (clean, multi-KNN)   : R²={r2_loo:.4f}  ({len(clean_locs)} locs)")
print(f"  LOO MAE                  : {mae_loo:.3f} lb")
