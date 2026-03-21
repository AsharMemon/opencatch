"""Full 4-metric evaluation with enriched v7 dataset using CatBoost."""
import pandas as pd
import numpy as np
from sklearn.neighbors import NearestNeighbors
from sklearn.metrics import r2_score
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

# Add location encoding for non-LOO tests
loc_means = df.groupby("location")[TARGET].mean()
df["loc_mean_enc"] = df.location.map(loc_means)
full_features_with_loc = full_features + ["loc_mean_enc"]

print(f"Full features: {len(full_features_with_loc)}, LOO features: {len(loo_features)}")


def train_catboost(X_tr, y_tr, iterations=800):
    model = CatBoostRegressor(
        iterations=iterations, depth=6, learning_rate=0.05,
        l2_leaf_reg=3, verbose=0, random_seed=42,
    )
    model.fit(X_tr, y_tr)
    return model


def knn_target_encode(train_df, K=8):
    """Build KNN target encoding from training data."""
    loc_stats = train_df.groupby("location").agg(
        mean_target=(TARGET, "mean"),
        lat=("lat", "first"),
        lon=("lon", "first"),
        area_acres=("area_acres", "first"),
        creel_lmb_ratio=("creel_lmb_ratio", "first"),
        creel_smb_ratio=("creel_smb_ratio", "first"),
    ).reset_index()

    morph_cols = ["lat", "lon", "area_acres", "creel_lmb_ratio", "creel_smb_ratio"]
    X_morph = loc_stats[morph_cols].fillna(loc_stats[morph_cols].median()).values
    scaler = StandardScaler().fit(X_morph)
    X_scaled = scaler.transform(X_morph)

    nn = NearestNeighbors(n_neighbors=min(K + 1, len(loc_stats)), metric="euclidean")
    nn.fit(X_scaled)

    cluster_map = {}
    region_map = {}
    for i, row in loc_stats.iterrows():
        q = X_scaled[i:i + 1]
        dists, idxs = nn.kneighbors(q)
        neighbors = [(d, j) for d, j in zip(dists[0], idxs[0])
                     if loc_stats.iloc[j]["location"] != row["location"]][:K]
        if neighbors:
            weights = [1 / (d + 0.01) for d, _ in neighbors]
            total = sum(weights)
            cluster_map[row["location"]] = sum(
                w * loc_stats.iloc[j]["mean_target"]
                for w, (_, j) in zip(weights, neighbors)
            ) / total
        else:
            cluster_map[row["location"]] = loc_stats["mean_target"].mean()

        dists2, idxs2 = nn.kneighbors(q, n_neighbors=min(21, len(loc_stats)))
        neighbors2 = [(d, j) for d, j in zip(dists2[0], idxs2[0])
                      if loc_stats.iloc[j]["location"] != row["location"]][:20]
        if neighbors2:
            region_map[row["location"]] = np.mean(
                [loc_stats.iloc[j]["mean_target"] for _, j in neighbors2]
            )
        else:
            region_map[row["location"]] = loc_stats["mean_target"].mean()

    return cluster_map, region_map, loc_stats, scaler, nn


def encode_new_location(test_df, loc_stats, scaler, nn, K=8):
    """Encode a held-out location using its neighbors."""
    morph_cols = ["lat", "lon", "area_acres", "creel_lmb_ratio", "creel_smb_ratio"]
    test_morph = test_df[morph_cols].fillna(
        loc_stats[morph_cols].median().to_dict()
    ).iloc[0:1].values
    test_scaled = scaler.transform(test_morph)
    dists, idxs = nn.kneighbors(test_scaled, n_neighbors=min(K + 1, len(loc_stats)))

    neighbors = [(d, int(j)) for d, j in zip(dists[0], idxs[0])][:K]
    weights = [1 / (d + 0.01) for d, _ in neighbors]
    total = sum(weights)
    cluster = sum(
        w * loc_stats.iloc[j]["mean_target"]
        for w, (_, j) in zip(weights, neighbors)
    ) / total
    region = np.mean(
        [loc_stats.iloc[j]["mean_target"] for _, j in neighbors[:20]]
    )
    return cluster, region


# ═════════════════════════════════════════════
# 1. TEMPORAL HOLDOUT
# ═════════════════════════════════════════════
print("\n" + "=" * 50)
print("1. TEMPORAL HOLDOUT")
print("=" * 50)
t0 = time.time()

df_sorted = df.sort_values("date")
split = int(len(df_sorted) * 0.8)
train_t = df_sorted.iloc[:split].copy()
test_t = df_sorted.iloc[split:].copy()

model = train_catboost(train_t[full_features_with_loc], train_t[TARGET])
pred = model.predict(test_t[full_features_with_loc])
r2_temporal = r2_score(test_t[TARGET], pred)

# Feature importance
imp = pd.Series(model.feature_importances_, index=full_features_with_loc)
top5 = imp.nlargest(5)
print(f"  R²={r2_temporal:.4f}  [{time.time()-t0:.1f}s]")
print(f"  Top: {list(zip(top5.index, top5.values.astype(int)))}")


# ═════════════════════════════════════════════
# 2. SPATIAL HOLDOUT
# ═════════════════════════════════════════════
print("\n" + "=" * 50)
print("2. SPATIAL HOLDOUT")
print("=" * 50)
t0 = time.time()

# Assign regions by latitude bands
df["_region"] = pd.cut(df.lat, bins=5, labels=["S", "SM", "M", "MN", "N"])
region_r2s = {}
for region in df["_region"].unique():
    test_r = df[df._region == region].copy()
    train_r = df[df._region != region].copy()
    if len(test_r) < 10:
        continue
    model = train_catboost(train_r[full_features_with_loc], train_r[TARGET], iterations=500)
    pred = model.predict(test_r[full_features_with_loc])
    r2 = r2_score(test_r[TARGET], pred)
    region_r2s[region] = (r2, len(test_r))
    print(f"    {region}: R²={r2:.4f} (n={len(test_r)})")

spatial_r2 = np.mean([r for r, _ in region_r2s.values()])
print(f"  Mean R²={spatial_r2:.4f}  [{time.time()-t0:.1f}s]")


# ═════════════════════════════════════════════
# 3. SPATIOTEMPORAL BLOCKED
# ═════════════════════════════════════════════
print("\n" + "=" * 50)
print("3. SPATIOTEMPORAL BLOCKED")
print("=" * 50)
t0 = time.time()

if "block" not in df.columns or df["block"].isna().all():
    # Create blocks from region x year
    df["_block"] = df["_region"].astype(str) + "_" + (df.year // 3).astype(str)
else:
    df["_block"] = df["block"]

gkf = GroupKFold(n_splits=5)
fold_r2s = []
for fold, (tr_idx, te_idx) in enumerate(gkf.split(df, groups=df["_block"])):
    train_st = df.iloc[tr_idx]
    test_st = df.iloc[te_idx]
    model = train_catboost(train_st[full_features_with_loc], train_st[TARGET], iterations=500)
    pred = model.predict(test_st[full_features_with_loc])
    r2 = r2_score(test_st[TARGET], pred)
    fold_r2s.append(r2)

st_r2 = np.mean(fold_r2s)
print(f"  R²={st_r2:.4f} ± {np.std(fold_r2s):.4f}  [{time.time()-t0:.1f}s]")
print(f"  Folds: {[f'{r:.4f}' for r in fold_r2s]}")


# ═════════════════════════════════════════════
# 4. LEAVE-ONE-LOCATION-OUT
# ═════════════════════════════════════════════
print("\n" + "=" * 50)
print("4. LEAVE-ONE-LOCATION-OUT")
print("=" * 50)
t0 = time.time()

loc_counts = df.location.value_counts()
big_locs = loc_counts[loc_counts >= 15].index.tolist()
print(f"  Testing {len(big_locs)} locations with n>=15")

all_true, all_pred = [], []
per_loc = {}

for i, loc in enumerate(big_locs):
    mask = df.location == loc
    train = df[~mask].copy()
    test = df[mask].copy()

    cluster_map, region_map, loc_stats, scaler, nn = knn_target_encode(train)
    test_cluster, test_region = encode_new_location(test, loc_stats, scaler, nn)

    train["cluster_enc"] = train.location.map(cluster_map).fillna(train[TARGET].mean())
    train["region_enc"] = train.location.map(region_map).fillna(train[TARGET].mean())
    test["cluster_enc"] = test_cluster
    test["region_enc"] = test_region

    use_feats = loo_features + ["cluster_enc", "region_enc"]

    model = CatBoostRegressor(
        iterations=500, depth=6, learning_rate=0.05,
        l2_leaf_reg=3, verbose=0, random_seed=42,
    )
    model.fit(train[use_feats], train[TARGET])
    pred = model.predict(test[use_feats])

    true_vals = test[TARGET].values
    n = len(test)

    # Clip predictions to reasonable range
    pred = np.clip(pred, 0, 30)

    r2 = r2_score(true_vals, pred) if n > 1 and np.std(true_vals) > 0.01 else 0.0
    per_loc[loc] = r2

    if (i + 1) % 20 == 0:
        interim_r2 = r2_score(all_true + list(true_vals), all_pred + list(pred))
        print(f"  ... {i+1}/{len(big_locs)} interim R²={interim_r2:.4f}")

    all_true.extend(true_vals.tolist())
    all_pred.extend(pred.tolist())

overall_r2 = r2_score(all_true, all_pred)
positive = sum(1 for r in per_loc.values() if r > 0)
print(f"\n  Overall LOO R²={overall_r2:.4f}  [{time.time()-t0:.1f}s]")
print(f"  Positive R²: {positive}/{len(big_locs)}")
print(f"  Median per-loc R²: {np.median(list(per_loc.values())):.4f}")

# Show worst and best
sorted_locs = sorted(per_loc.items(), key=lambda x: x[1])
print("\n  Worst 5:")
for loc, r2 in sorted_locs[:5]:
    n = loc_counts[loc]
    true = df[df.location == loc][TARGET].mean()
    print(f"    {loc:50s} n={n:3d} R²={r2:8.3f} mean={true:.1f}")
print("  Best 5:")
for loc, r2 in sorted_locs[-5:]:
    n = loc_counts[loc]
    true = df[df.location == loc][TARGET].mean()
    print(f"    {loc:50s} n={n:3d} R²={r2:8.3f} mean={true:.1f}")

# ═════════════════════════════════════════════
# SUMMARY
# ═════════════════════════════════════════════
print("\n" + "=" * 60)
print("SUMMARY (CatBoost + enriched morphometry)")
print("=" * 60)
print(f"  Temporal                 : R²={r2_temporal:.4f}")
print(f"  Spatial                  : R²={spatial_r2:.4f}")
print(f"  Spatiotemporal           : R²={st_r2:.4f}")
print(f"  LOO                      : R²={overall_r2:.4f}")
