"""v15 robust evaluation: improved LOO via distance-aware shrinkage + prediction clipping.

Key improvements over v12 eval:
1. Uses v13 dataset (GLOBathy depth + LAGOS SDI)
2. Distance-aware KNN shrinkage: blend prediction with regional prior when
   neighbors are far away
3. Prediction clipping to realistic range [1, 25]
4. Median ensemble (robust to outlier model predictions)
5. Adaptive K: more neighbors for unusual locations
"""
import pandas as pd
import numpy as np
from sklearn.neighbors import NearestNeighbors
from sklearn.metrics import r2_score, mean_absolute_error
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import GroupKFold
from catboost import CatBoostRegressor
from scipy.stats import spearmanr, kendalltau
from itertools import combinations
import warnings
import time

warnings.filterwarnings("ignore")

def p(msg=""):
    print(msg, flush=True)

TARGET = "median_weight_lb"
ALWAYS_EXCLUDE = {"target_success_score", "tms_id", "event_id"}
LOCATION_IDENTITY = {"loc_enc", "trail_mean_weight", "location_mean_weight",
                     "loc_rolling_3", "baseline_signal"}
META_COLS = {"date", "location", "region", "block", "sat_source",
             "usgs_site_id", "event_name", "tournament_slug",
             "trail", "results_source", "species", "spawn_phase",
             "source"}
LEAKY_COLS = {"loc_mean_enc", "source_enc"}

MIN_LOO_STD = 0.5
MIN_LOO_N = 15

# Include lagos_sdi for model features, but NOT in KNN matching
LOC_MORPH = ["lat", "lon", "area_acres", "is_lake", "max_depth_ft", "shore_dev",
             "creel_lmb_ratio", "creel_smb_ratio", "creel_cpue_mean"]

# Extended morphometry for model features (not KNN)
EXTRA_LOC_FEATURES = ["lagos_sdi", "log_depth", "depth_x_lat", "littoral_ratio",
                      "volume_proxy", "log_volume", "depth_area_ratio",
                      "stratification_potential", "thermal_refuge_score",
                      "shore_dev_est", "shore_dev_is_est", "depth_source",
                      "mean_depth_ft"]

# Prediction bounds
PRED_MIN = 1.0
PRED_MAX = 25.0


def ndcg_at_k(true_scores, pred_scores, k):
    t, p_ = np.array(true_scores), np.array(pred_scores)
    if len(t) < 2: return 1.0
    pred_order = np.argsort(-p_)
    dcg = np.sum(t[pred_order[:k]] / np.log2(np.arange(2, min(k, len(t)) + 2)))
    ideal_order = np.argsort(-t)
    idcg = np.sum(t[ideal_order[:k]] / np.log2(np.arange(2, min(k, len(t)) + 2)))
    return dcg / idcg if idcg > 0 else 1.0


def pairwise_accuracy(true_scores, pred_scores):
    if len(true_scores) < 2: return 1.0
    correct = total = 0
    for i, j in combinations(range(len(true_scores)), 2):
        if true_scores[i] == true_scores[j]: continue
        total += 1
        if (true_scores[i] > true_scores[j]) == (pred_scores[i] > pred_scores[j]):
            correct += 1
    return correct / total if total > 0 else 1.0


def fill_rolling_mean_gaps(train_df, test_df=None, K=8):
    """Fill loc_rolling_mean NaN gaps using KNN prior from TRAINING data only."""
    morph = [c for c in LOC_MORPH if c in train_df.columns]

    loc_stats = train_df.groupby("location").agg(
        mean_target=(TARGET, "mean"),
        n_events=(TARGET, "count"),
        **{f: (f, "first") for f in morph}
    ).reset_index()

    X = loc_stats[morph].fillna(loc_stats[morph].median()).values
    sc = StandardScaler().fit(X)
    Xs = sc.transform(X)
    nn = NearestNeighbors(n_neighbors=min(K+1, len(loc_stats)))
    nn.fit(Xs)

    knn_prior = {}
    for i, row in loc_stats.iterrows():
        q = Xs[i:i+1]
        d, ix = nn.kneighbors(q)
        nbrs = [(dd, j) for dd, j in zip(d[0], ix[0])
                if loc_stats.iloc[j]["location"] != row["location"]][:K]
        if nbrs:
            w = [loc_stats.iloc[j]["n_events"]/(dd+0.01) for dd, j in nbrs]
            knn_prior[row["location"]] = sum(
                wi * loc_stats.iloc[j]["mean_target"]
                for wi, (_, j) in zip(w, nbrs)) / sum(w)
        else:
            knn_prior[row["location"]] = loc_stats["mean_target"].mean()

    train_out = train_df.copy()
    if "loc_rolling_mean" in train_out.columns:
        mask = train_out["loc_rolling_mean"].isna()
        train_out.loc[mask, "loc_rolling_mean"] = train_out.loc[mask, "location"].map(knn_prior)
        train_out["loc_rolling_mean"].fillna(train_df[TARGET].mean(), inplace=True)

    test_out = None
    if test_df is not None:
        test_out = test_df.copy()
        if "loc_rolling_mean" in test_out.columns:
            for loc in test_out.location.unique():
                loc_mask = test_out.location == loc
                if test_out.loc[loc_mask, "loc_rolling_mean"].isna().any():
                    if loc in knn_prior:
                        test_out.loc[loc_mask & test_out["loc_rolling_mean"].isna(),
                                     "loc_rolling_mean"] = knn_prior[loc]
                    else:
                        row = test_out[test_out.location == loc].iloc[0]
                        tv = pd.DataFrame([row])[morph].fillna(
                            loc_stats[morph].median().to_dict()).values
                        ts = sc.transform(tv)
                        d, ix = nn.kneighbors(ts, n_neighbors=min(K+1, len(loc_stats)))
                        nbrs = [(dd, int(j)) for dd, j in zip(d[0], ix[0])][:K]
                        w = [loc_stats.iloc[j]["n_events"]/(dd+0.01) for dd, j in nbrs]
                        val = sum(wi * loc_stats.iloc[j]["mean_target"]
                                  for wi, (_, j) in zip(w, nbrs)) / sum(w)
                        test_out.loc[loc_mask & test_out["loc_rolling_mean"].isna(),
                                     "loc_rolling_mean"] = val
            test_out["loc_rolling_mean"].fillna(train_df[TARGET].mean(), inplace=True)

    return train_out, test_out


def knn_encode_loo_robust(train_df, test_df, K=8):
    """Robust KNN encoding for LOO with distance-aware shrinkage.

    Key improvement: returns knn_distance so we can shrink predictions
    toward global mean when neighbors are far away.
    """
    morph = [c for c in LOC_MORPH if c in train_df.columns]

    loc_stats = train_df.groupby("location").agg(
        mean_target=(TARGET, "mean"),
        std_target=(TARGET, "std"),
        n_events=(TARGET, "count"),
        **{f: (f, "first") for f in morph}
    ).reset_index()
    loc_stats["std_target"] = loc_stats["std_target"].fillna(0)

    X = loc_stats[morph].fillna(loc_stats[morph].median()).values
    sc = StandardScaler().fit(X)
    Xs = sc.transform(X)
    nn = NearestNeighbors(n_neighbors=min(K+1, len(loc_stats)))
    nn.fit(Xs)

    global_mean = train_df[TARGET].mean()

    # Train KNN encoding
    cluster_map = {}
    region_map = {}
    nn_std_map = {}
    knn_dist_map = {}

    for i, row in loc_stats.iterrows():
        q = Xs[i:i+1]
        d, ix = nn.kneighbors(q, n_neighbors=min(21, len(loc_stats)))
        all_nbrs = [(dd, j) for dd, j in zip(d[0], ix[0])
                    if loc_stats.iloc[j]["location"] != row["location"]]

        nbrs = all_nbrs[:K]
        if nbrs:
            w = [loc_stats.iloc[j]["n_events"]/(dd+0.01) for dd, j in nbrs]
            cluster_map[row["location"]] = sum(
                wi * loc_stats.iloc[j]["mean_target"]
                for wi, (_, j) in zip(w, nbrs)) / sum(w)
            nn_std_map[row["location"]] = np.std(
                [loc_stats.iloc[j]["mean_target"] for _, j in nbrs])
            knn_dist_map[row["location"]] = np.mean([dd for dd, _ in nbrs])
        else:
            cluster_map[row["location"]] = global_mean
            nn_std_map[row["location"]] = loc_stats["mean_target"].std()
            knn_dist_map[row["location"]] = 10.0

        nbrs20 = all_nbrs[:20]
        region_map[row["location"]] = (
            np.mean([loc_stats.iloc[j]["mean_target"] for _, j in nbrs20])
            if nbrs20 else global_mean)

    # Test KNN encoding
    tv = test_df[morph].fillna(loc_stats[morph].median().to_dict()).iloc[0:1].values
    ts = sc.transform(tv)
    d, ix = nn.kneighbors(ts, n_neighbors=min(21, len(loc_stats)))
    all_nbrs = [(dd, int(j)) for dd, j in zip(d[0], ix[0])]

    nbrs = all_nbrs[:K]
    w = [loc_stats.iloc[j]["n_events"]/(dd+0.01) for dd, j in nbrs]
    test_cluster = sum(wi * loc_stats.iloc[j]["mean_target"]
                      for wi, (_, j) in zip(w, nbrs)) / sum(w)
    test_nn_std = np.std([loc_stats.iloc[j]["mean_target"] for _, j in nbrs])
    test_region = np.mean([loc_stats.iloc[j]["mean_target"]
                          for _, j in all_nbrs[:20]])
    test_knn_dist = np.mean([dd for dd, _ in nbrs])

    # Distance-aware shrinkage: blend cluster prediction with global mean
    # based on how far away neighbors are
    dist_percentiles = np.array(list(knn_dist_map.values()))
    dist_p75 = np.percentile(dist_percentiles, 75) if len(dist_percentiles) > 0 else 2.0
    dist_p90 = np.percentile(dist_percentiles, 90) if len(dist_percentiles) > 0 else 3.0

    # Shrinkage for test location
    shrinkage = min(1.0, max(0.0, (test_knn_dist - dist_p75) / (dist_p90 - dist_p75 + 0.01)))
    test_cluster_shrunk = test_cluster * (1 - shrinkage) + global_mean * shrinkage

    # Learned location quality model
    sw = np.sqrt(loc_stats["n_events"].values)
    lm = CatBoostRegressor(iterations=300, depth=5, learning_rate=0.05,
                           l2_leaf_reg=5, verbose=0, random_seed=42)
    lm.fit(loc_stats[morph].fillna(loc_stats[morph].median()),
           loc_stats["mean_target"], sample_weight=sw)

    train_lp = train_df.groupby("location")[morph].first().reset_index()
    train_lp_filled = train_lp[morph].fillna(loc_stats[morph].median().to_dict())
    quality_map = dict(zip(train_lp.location, lm.predict(train_lp_filled)))

    test_quality = lm.predict(
        test_df[morph].fillna(loc_stats[morph].median().to_dict()).iloc[0:1])[0]

    return (cluster_map, region_map, nn_std_map, quality_map, knn_dist_map,
            test_cluster, test_cluster_shrunk, test_region, test_nn_std,
            test_quality, test_knn_dist)


# ═══════════════════════════════════════
# LOAD DATA
# ═══════════════════════════════════════
df = pd.read_csv("castline/validation/data/assembled/validation_dataset_v15.csv",
                 low_memory=False)
df = df[df[TARGET].notna()].copy()
p(f"Dataset: {len(df)} rows, {df.location.nunique()} locations")

all_cols = set(df.columns)
exclude = ALWAYS_EXCLUDE | LOCATION_IDENTITY | META_COLS | LEAKY_COLS | {TARGET}
base_features = sorted(all_cols - exclude)
base_features = [f for f in base_features if df[f].dtype in ["float64", "int64", "float32", "int32"]]

temporal_features = base_features.copy()

loo_exclude = {"loc_rolling_mean", "loc_rolling_std", "loc_n_prior",
               "loc_encoding_confidence"}
loo_features = [f for f in base_features if f not in loo_exclude]

p(f"Temporal features: {len(temporal_features)}")
p(f"LOO features: {len(loo_features)}")

# Check for v13-specific features
v13_features = [f for f in EXTRA_LOC_FEATURES if f in df.columns]
p(f"v13 extra features available: {v13_features}")


# ═══════════════════════════════════════
# 1. TEMPORAL (gap-filled loc_rolling_mean)
# ═══════════════════════════════════════
p("\n" + "=" * 60)
p("1. TEMPORAL HOLDOUT (gap-filled)")
p("=" * 60)
t0 = time.time()
df_s = df.sort_values("date")
split = int(len(df_s) * 0.8)
tr_raw, te_raw = df_s.iloc[:split].copy(), df_s.iloc[split:].copy()

tr, te = fill_rolling_mean_gaps(tr_raw, te_raw)

p(f"  Train: {len(tr)} rows, Test: {len(te)} rows")
p(f"  Test loc_rolling_mean coverage: {te['loc_rolling_mean'].notna().mean()*100:.1f}%")

seen_locs = set(tr.location.unique())
te_seen = te[te.location.isin(seen_locs)]
te_unseen = te[~te.location.isin(seen_locs)]
p(f"  Seen locations: {len(te_seen)} rows ({len(te_seen)/len(te)*100:.0f}%)")
p(f"  Unseen locations: {len(te_unseen)} rows ({len(te_unseen)/len(te)*100:.0f}%)")

# Ensemble with multiple configs
configs = [
    dict(iterations=1500, depth=6, learning_rate=0.03, l2_leaf_reg=3,
         random_seed=42, subsample=0.85, colsample_bylevel=0.8),
    dict(iterations=1200, depth=5, learning_rate=0.03, l2_leaf_reg=5,
         random_seed=123, subsample=0.9, colsample_bylevel=0.85),
    dict(iterations=1000, depth=7, learning_rate=0.05, l2_leaf_reg=2,
         random_seed=456, subsample=0.8, colsample_bylevel=0.75),
    dict(iterations=1500, depth=6, learning_rate=0.02, l2_leaf_reg=4,
         random_seed=789, subsample=0.85, colsample_bylevel=0.8),
]
preds_t = []
for cfg in configs:
    m = CatBoostRegressor(**cfg, verbose=0)
    m.fit(tr[temporal_features], tr[TARGET])
    preds_t.append(m.predict(te[temporal_features]))

# Median ensemble (more robust than mean)
pred_t_mean = np.mean(preds_t, axis=0)
pred_t_median = np.median(preds_t, axis=0)
pred_t = np.clip(pred_t_median, PRED_MIN, PRED_MAX)

r2_t_mean = r2_score(te[TARGET], np.clip(pred_t_mean, PRED_MIN, PRED_MAX))
r2_t_median = r2_score(te[TARGET], pred_t)

# Breakdown by seen/unseen
if len(te_seen) > 0:
    r2_seen = r2_score(te_seen[TARGET], pred_t[te.location.isin(seen_locs)])
else:
    r2_seen = float('nan')
if len(te_unseen) > 0:
    r2_unseen = r2_score(te_unseen[TARGET], pred_t[~te.location.isin(seen_locs)])
else:
    r2_unseen = float('nan')

p(f"  R² (mean ens):   {r2_t_mean:.4f}")
p(f"  R² (median ens): {r2_t_median:.4f}")
p(f"  R² seen:   {r2_seen:.4f}")
p(f"  R² unseen: {r2_unseen:.4f}")

# Feature importance from first model
m0 = CatBoostRegressor(**configs[0], verbose=0)
m0.fit(tr[temporal_features], tr[TARGET])
imp = pd.Series(m0.feature_importances_, index=temporal_features).nlargest(15)
p(f"  Top features:")
for f, v in imp.items():
    p(f"    {f:35s} {v:.1f}%")


# ═══════════════════════════════════════
# 2. SPATIAL
# ═══════════════════════════════════════
p("\n" + "=" * 60)
p("2. SPATIAL HOLDOUT")
p("=" * 60)
t0 = time.time()
df["_region"] = pd.cut(df.lat, bins=5, labels=["S", "SM", "M", "MN", "N"])
rr = {}
for region in df["_region"].unique():
    te_mask = df._region == region
    tr_r, te_r = fill_rolling_mean_gaps(df[~te_mask], df[te_mask])
    if len(te_r) < 10: continue

    preds_s = []
    for cfg in configs[:2]:
        m = CatBoostRegressor(**cfg, verbose=0)
        m.fit(tr_r[temporal_features], tr_r[TARGET])
        preds_s.append(m.predict(te_r[temporal_features]))
    pred_s = np.clip(np.median(preds_s, axis=0), PRED_MIN, PRED_MAX)
    r2 = r2_score(te_r[TARGET], pred_s)
    rr[region] = (r2, len(te_r))
    p(f"    {region}: R²={r2:.4f} (n={len(te_r)})")
r2_s = np.mean([r for r, _ in rr.values()])
p(f"  Mean R²={r2_s:.4f}  [{time.time()-t0:.1f}s]")


# ═══════════════════════════════════════
# 3. SPATIOTEMPORAL
# ═══════════════════════════════════════
p("\n" + "=" * 60)
p("3. SPATIOTEMPORAL BLOCKED")
p("=" * 60)
t0 = time.time()
df["_block"] = df["_region"].astype(str) + "_" + (df.year // 3).astype(str)
gkf = GroupKFold(n_splits=5)
fold_r2s = []
for tr_idx, te_idx in gkf.split(df, groups=df["_block"]):
    tr_r, te_r = fill_rolling_mean_gaps(df.iloc[tr_idx], df.iloc[te_idx])
    m = CatBoostRegressor(iterations=1000, depth=6, learning_rate=0.03,
                          l2_leaf_reg=3, verbose=0, random_seed=42, subsample=0.85)
    m.fit(tr_r[temporal_features], tr_r[TARGET])
    pred = np.clip(m.predict(te_r[temporal_features]), PRED_MIN, PRED_MAX)
    fold_r2s.append(r2_score(te_r[TARGET], pred))
r2_st = np.mean(fold_r2s)
p(f"  R²={r2_st:.4f} ± {np.std(fold_r2s):.4f}  [{time.time()-t0:.1f}s]")


# ═══════════════════════════════════════
# 4. LOO (with robust KNN + shrinkage)
# ═══════════════════════════════════════
p("\n" + "=" * 60)
p("4. LOO (robust KNN + distance shrinkage)")
p("=" * 60)
t0 = time.time()

loc_counts = df.location.value_counts()
loc_std = df.groupby("location")[TARGET].std()
clean_locs = [l for l in loc_counts[loc_counts >= MIN_LOO_N].index
              if loc_std.get(l, 0) >= MIN_LOO_STD]
p(f"  Clean LOO set: {len(clean_locs)} locations")

all_true, all_pred = [], []
all_true_baseline, all_pred_baseline = [], []
per_loc = {}
per_loc_baseline = {}
loc_mean_true, loc_mean_pred = {}, {}
loc_knn_dist = {}

for i, loc in enumerate(clean_locs):
    mask = df.location == loc
    train, test = df[~mask].copy(), df[mask].copy()

    # Robust KNN encoding with distance info
    (cmap, rmap, nn_std_map, quality_map, knn_dist_map,
     tc, tc_shrunk, tr_enc, tns, tq, tkd) = knn_encode_loo_robust(train, test)

    train["cluster_enc"] = train.location.map(cmap).fillna(train[TARGET].mean())
    train["region_enc"] = train.location.map(rmap).fillna(train[TARGET].mean())
    train["nn_std"] = train.location.map(nn_std_map).fillna(0)
    train["loc_quality"] = train.location.map(quality_map).fillna(train[TARGET].mean())
    train["knn_distance"] = train.location.map(knn_dist_map).fillna(0)

    # Use shrunk cluster for test (conservative for far-away locations)
    test["cluster_enc"] = tc_shrunk
    test["region_enc"] = tr_enc
    test["nn_std"] = tns
    test["loc_quality"] = tq
    test["knn_distance"] = tkd

    extra = ["cluster_enc", "region_enc", "nn_std", "loc_quality", "knn_distance"]
    use = loo_features + extra

    # Ensemble with more configs for robustness
    loo_configs = [
        dict(iterations=800, depth=6, learning_rate=0.05, l2_leaf_reg=3,
             random_seed=42, subsample=0.85),
        dict(iterations=1000, depth=5, learning_rate=0.03, l2_leaf_reg=5,
             random_seed=123, subsample=0.9),
        dict(iterations=600, depth=7, learning_rate=0.05, l2_leaf_reg=2,
             random_seed=456, subsample=0.8),
        dict(iterations=1200, depth=6, learning_rate=0.02, l2_leaf_reg=4,
             random_seed=789, subsample=0.85),
    ]
    preds = []
    for cfg in loo_configs:
        m = CatBoostRegressor(**cfg, verbose=0)
        m.fit(train[use], train[TARGET])
        preds.append(m.predict(test[use]))

    # Median ensemble + clipping
    pred = np.clip(np.median(preds, axis=0), PRED_MIN, PRED_MAX)

    # Also compute baseline (mean ensemble, no clipping)
    pred_baseline = np.clip(np.mean(preds, axis=0), 0, 30)

    r2 = r2_score(test[TARGET], pred) if len(test) > 1 and np.std(test[TARGET]) > 0.01 else 0.0
    r2_bl = r2_score(test[TARGET], pred_baseline) if len(test) > 1 and np.std(test[TARGET]) > 0.01 else 0.0

    per_loc[loc] = r2
    per_loc_baseline[loc] = r2_bl
    loc_mean_true[loc] = test[TARGET].mean()
    loc_mean_pred[loc] = pred.mean()
    loc_knn_dist[loc] = tkd

    if (i + 1) % 10 == 0:
        interim = r2_score(all_true + list(test[TARGET]), all_pred + list(pred))
        interim_bl = r2_score(all_true_baseline + list(test[TARGET]),
                              all_pred_baseline + list(pred_baseline))
        p(f"  {i+1}/{len(clean_locs)} robust={interim:.4f} baseline={interim_bl:.4f}")

    all_true.extend(test[TARGET].tolist())
    all_pred.extend(pred.tolist())
    all_true_baseline.extend(test[TARGET].tolist())
    all_pred_baseline.extend(pred_baseline.tolist())

r2_loo = r2_score(all_true, all_pred)
r2_loo_bl = r2_score(all_true_baseline, all_pred_baseline)
mae_loo = mean_absolute_error(all_true, all_pred)
positive = sum(1 for r in per_loc.values() if r > 0)

# Ranking
true_means = [loc_mean_true[l] for l in clean_locs]
pred_means = [loc_mean_pred[l] for l in clean_locs]
ndcg5 = ndcg_at_k(true_means, pred_means, 5)
pw = pairwise_accuracy(true_means, pred_means)
spear_r, _ = spearmanr(true_means, pred_means)
tau, _ = kendalltau(true_means, pred_means)

p(f"\n  ROBUST:   R²={r2_loo:.4f}  MAE={mae_loo:.3f}lb")
p(f"  BASELINE: R²={r2_loo_bl:.4f}")
p(f"  Positive R²: {positive}/{len(clean_locs)}")
p(f"  RANKING: NDCG@5={ndcg5:.4f}  Pairwise={pw:.4f}  Spearman={spear_r:.4f}")

sorted_locs = sorted(per_loc.items(), key=lambda x: x[1])
p("\n  Worst 5 (robust):")
for loc, r2 in sorted_locs[:5]:
    dist = loc_knn_dist.get(loc, 0)
    bl = per_loc_baseline.get(loc, 0)
    p(f"    {loc:50s} n={loc_counts[loc]:3d} R²={r2:8.3f} (bl={bl:8.3f}) dist={dist:.2f}")
p("  Best 5:")
for loc, r2 in sorted_locs[-5:]:
    p(f"    {loc:50s} n={loc_counts[loc]:3d} R²={r2:8.3f}")


# ═══════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════
p("\n" + "=" * 60)
p("v15 ROBUST SUMMARY")
p("=" * 60)
p(f"  Temporal (gap-filled)  : R²={r2_t_median:.4f}")
p(f"  Spatial                : R²={r2_s:.4f}")
p(f"  Spatiotemporal         : R²={r2_st:.4f}")
p(f"  LOO Regression         : R²={r2_loo:.4f}  MAE={mae_loo:.3f}")
p(f"  LOO Baseline (no shrink): R²={r2_loo_bl:.4f}")
p(f"  LOO Ranking            : NDCG@5={ndcg5:.4f}  Pairwise={pw:.4f}")
p(f"  LOO Spearman           : ρ={spear_r:.4f}")
p()
p("Comparison:")
p("  v13 best : T=0.425 S=0.486 ST=0.594 LOO=0.606")
p(f"  v15 robust: T={r2_t_median:.3f} S={r2_s:.3f} ST={r2_st:.3f} LOO={r2_loo:.3f}")
p()
p(f"  Total time: {time.time()-t0:.0f}s")
