"""v9 LOO with dual KNN (geographic + morphometric) + source encoding.

Key insight: geographic proximity doesn't predict fishing quality well.
Sam Rayburn (mean=24) is near Sabine River (mean=7) — totally different.
So use BOTH geographic KNN and morphometric KNN as separate features.
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
             "trail", "results_source", "species", "spawn_phase"}

MIN_LOO_STD = 0.5
MIN_LOO_N = 15

df = pd.read_csv("castline/validation/data/assembled/validation_dataset_v9.csv",
                 low_memory=False)
df = df[df[TARGET].notna()].copy()

# Source encoding (kept for LOO — tells model what kind of data source)
source_means = df.groupby("source")[TARGET].mean()
df["source_enc"] = df.source.map(source_means)

print(f"Dataset: {len(df)} rows, {df.location.nunique()} locations")

all_cols = set(df.columns)
exclude = ALWAYS_EXCLUDE | LOCATION_IDENTITY | META_COLS | {TARGET}
full_features = sorted(all_cols - exclude)
full_features = [f for f in full_features if df[f].dtype in ["float64", "int64", "float32", "int32"]]
loo_features = [f for f in full_features if f not in LOCATION_IDENTITY]

loc_means = df.groupby("location")[TARGET].mean()
df["loc_mean_enc"] = df.location.map(loc_means)
full_with_loc = full_features + ["loc_mean_enc"]

print(f"Features: {len(full_with_loc)} full, {len(loo_features)} LOO")
print(f"source_enc in LOO: {'source_enc' in loo_features}")


def dual_knn_encode(train_df, test_df=None, K=8):
    """Two independent KNN encoders: geographic and morphometric."""
    loc_stats = train_df.groupby("location").agg(
        mean_target=(TARGET, "mean"),
        std_target=(TARGET, "std"),
        n_events=(TARGET, "count"),
        lat=("lat", "first"), lon=("lon", "first"),
        area_acres=("area_acres", "first"),
        is_lake=("is_lake", "first"),
        creel_lmb_ratio=("creel_lmb_ratio", "first"),
        creel_smb_ratio=("creel_smb_ratio", "first"),
        creel_cpue_mean=("creel_cpue_mean", "first"),
        source_enc=("source_enc", "first"),
    ).reset_index()
    loc_stats["std_target"] = loc_stats["std_target"].fillna(0)

    # ── Geographic KNN ──
    geo_cols = ["lat", "lon"]
    Xg = loc_stats[geo_cols].values
    scg = StandardScaler().fit(Xg)
    Xgs = scg.transform(Xg)
    nn_g = NearestNeighbors(n_neighbors=min(K+1, len(loc_stats)))
    nn_g.fit(Xgs)

    # ── Morphometric KNN (no geography!) ──
    morph_cols = ["area_acres", "is_lake", "creel_lmb_ratio",
                  "creel_smb_ratio", "creel_cpue_mean", "source_enc"]
    Xm = loc_stats[morph_cols].fillna(loc_stats[morph_cols].median()).values
    scm = StandardScaler().fit(Xm)
    Xms = scm.transform(Xm)
    nn_m = NearestNeighbors(n_neighbors=min(K+1, len(loc_stats)))
    nn_m.fit(Xms)

    # ── Combined KNN (both) ──
    comb_cols = ["lat", "lon", "area_acres", "is_lake",
                 "creel_lmb_ratio", "creel_smb_ratio", "creel_cpue_mean"]
    Xc = loc_stats[comb_cols].fillna(loc_stats[comb_cols].median()).values
    scc = StandardScaler().fit(Xc)
    Xcs = scc.transform(Xc)
    nn_c = NearestNeighbors(n_neighbors=min(K+1, len(loc_stats)))
    nn_c.fit(Xcs)

    def _encode(nn, Xs_all, i, loc_name, k):
        q = Xs_all[i:i+1]
        d, ix = nn.kneighbors(q, n_neighbors=min(k+1, len(loc_stats)))
        nbrs = [(dd, j) for dd, j in zip(d[0], ix[0])
                if loc_stats.iloc[j]["location"] != loc_name][:k]
        if nbrs:
            w = [loc_stats.iloc[j]["n_events"]/(dd+0.01) for dd, j in nbrs]
            wmean = sum(wi * loc_stats.iloc[j]["mean_target"]
                       for wi, (_, j) in zip(w, nbrs)) / sum(w)
            targets = [loc_stats.iloc[j]["mean_target"] for _, j in nbrs]
            return wmean, np.std(targets)
        return loc_stats["mean_target"].mean(), loc_stats["mean_target"].std()

    def _encode_new(nn, scaler, cols, test_row, k):
        tv = test_row[cols].fillna(loc_stats[cols].median().to_dict()).iloc[0:1].values
        ts = scaler.transform(tv)
        d, ix = nn.kneighbors(ts, n_neighbors=min(k+1, len(loc_stats)))
        nbrs = [(dd, int(j)) for dd, j in zip(d[0], ix[0])][:k]
        w = [loc_stats.iloc[j]["n_events"]/(dd+0.01) for dd, j in nbrs]
        wmean = sum(wi * loc_stats.iloc[j]["mean_target"]
                   for wi, (_, j) in zip(w, nbrs)) / sum(w)
        targets = [loc_stats.iloc[j]["mean_target"] for _, j in nbrs]
        return wmean, np.std(targets)

    enc_cols = {}
    for prefix, nn, Xs_all in [("geo", nn_g, Xgs), ("morph", nn_m, Xms), ("comb", nn_c, Xcs)]:
        enc_cols[f"{prefix}_knn_mean"] = {}
        enc_cols[f"{prefix}_knn_std"] = {}
        for i, row in loc_stats.iterrows():
            mean, std = _encode(nn, Xs_all, i, row["location"], K)
            enc_cols[f"{prefix}_knn_mean"][row["location"]] = mean
            enc_cols[f"{prefix}_knn_std"][row["location"]] = std

    test_encs = {}
    if test_df is not None:
        for prefix, nn, sc, cols in [
            ("geo", nn_g, scg, geo_cols),
            ("morph", nn_m, scm, morph_cols),
            ("comb", nn_c, scc, comb_cols),
        ]:
            mean, std = _encode_new(nn, sc, cols, test_df, K)
            test_encs[f"{prefix}_knn_mean"] = mean
            test_encs[f"{prefix}_knn_std"] = std

    return enc_cols, test_encs


knn_feat_names = ["geo_knn_mean", "geo_knn_std",
                  "morph_knn_mean", "morph_knn_std",
                  "comb_knn_mean", "comb_knn_std"]


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
rr = {}
for region in df["_region"].unique():
    te_r = df[df._region == region]
    tr_r = df[df._region != region]
    if len(te_r) < 10: continue
    m = CatBoostRegressor(iterations=500, depth=6, learning_rate=0.05,
                          l2_leaf_reg=3, verbose=0, random_seed=42)
    m.fit(tr_r[full_with_loc], tr_r[TARGET])
    r2 = r2_score(te_r[TARGET], m.predict(te_r[full_with_loc]))
    rr[region] = (r2, len(te_r))
    print(f"    {region}: R²={r2:.4f} (n={len(te_r)})")
r2_s = np.mean([r for r, _ in rr.values()])
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
# 4. LOO WITH DUAL KNN
# ═══════════════════════════════════════
print("\n" + "=" * 50)
print("4. LOO (dual KNN + source)")
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

    encs, test_encs = dual_knn_encode(train, test)
    for col in knn_feat_names:
        train[col] = train.location.map(encs[col]).fillna(train[TARGET].mean())
        test[col] = test_encs[col]

    use = loo_features + knn_feat_names
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

# Feature importance from last model
fi = pd.Series(m.feature_importances_, index=use).nlargest(15)
print("\n  Feature importance (last LOO fold):")
for f, v in fi.items():
    print(f"    {f:40s} {v:.1f}")

# ═══════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════
print("\n" + "=" * 60)
print("SUMMARY (v9-best: dual KNN + source + real weather)")
print("=" * 60)
print(f"  Temporal                 : R²={r2_t:.4f}")
print(f"  Spatial                  : R²={r2_s:.4f}")
print(f"  Spatiotemporal           : R²={r2_st:.4f}")
print(f"  LOO (clean)              : R²={r2_loo:.4f}  ({len(clean_locs)} locs)")
print()
print("Progress tracker:")
print("  v6 (15 locs)     : T=0.547 S=?     ST=?     LOO=0.42")
print("  v8 (48 locs)     : T=0.668 S=0.721 ST=0.752 LOO=0.493")
print("  v9 basic (40)    : T=0.717 S=0.749 ST=0.778 LOO=0.535")
print(f"  v9 best (40)     : T={r2_t:.3f} S={r2_s:.3f} ST={r2_st:.3f} LOO={r2_loo:.3f}")
