"""Production evaluation with ranking metrics + regression metrics.

Metrics:
- R² (regression quality)
- MAE (prediction error in lbs)
- NDCG@k (ranking quality: can we rank locations correctly?)
- Pairwise accuracy (% of location pairs ranked correctly)
- Top-k hit rate (is the best location in our top-k recommendations?)
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
from itertools import combinations

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


# ═══════════════════════════════════════
# RANKING METRICS
# ═══════════════════════════════════════

def dcg_at_k(relevances, k):
    """Discounted Cumulative Gain at k."""
    relevances = np.array(relevances[:k])
    if len(relevances) == 0:
        return 0.0
    return np.sum(relevances / np.log2(np.arange(2, len(relevances) + 2)))


def ndcg_at_k(true_scores, pred_scores, k):
    """Normalized DCG@k. Ranks items by pred_scores, evaluates by true_scores."""
    if len(true_scores) < 2:
        return 1.0

    # Rank by predicted scores (descending)
    pred_order = np.argsort(-np.array(pred_scores))
    true_arr = np.array(true_scores)

    # DCG: relevances in predicted order
    pred_relevances = true_arr[pred_order]
    dcg = dcg_at_k(pred_relevances, k)

    # Ideal DCG: relevances in perfect order
    ideal_order = np.argsort(-true_arr)
    ideal_relevances = true_arr[ideal_order]
    idcg = dcg_at_k(ideal_relevances, k)

    return dcg / idcg if idcg > 0 else 1.0


def pairwise_accuracy(true_scores, pred_scores):
    """Fraction of pairs where predicted ranking agrees with true ranking."""
    if len(true_scores) < 2:
        return 1.0

    correct = 0
    total = 0
    for i, j in combinations(range(len(true_scores)), 2):
        if true_scores[i] == true_scores[j]:
            continue
        total += 1
        true_better = true_scores[i] > true_scores[j]
        pred_better = pred_scores[i] > pred_scores[j]
        if true_better == pred_better:
            correct += 1

    return correct / total if total > 0 else 1.0


def top_k_hit_rate(true_scores, pred_scores, k=3):
    """Is the true best location in the top-k predicted locations?"""
    if len(true_scores) <= k:
        return 1.0
    true_best = np.argmax(true_scores)
    pred_top_k = set(np.argsort(-np.array(pred_scores))[:k])
    return 1.0 if true_best in pred_top_k else 0.0


# ═══════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════

df = pd.read_csv("castline/validation/data/assembled/validation_dataset_v9.csv",
                 low_memory=False)
df = df[df[TARGET].notna()].copy()
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

LOC_FEATURES = ["lat", "lon", "area_acres", "is_lake",
                "creel_lmb_ratio", "creel_smb_ratio", "creel_spotted_ratio",
                "creel_cpue_mean", "max_depth_ft", "source_enc"]
LOC_FEATURES = [f for f in LOC_FEATURES if f in df.columns]

print(f"Features: {len(full_with_loc)} full, {len(loo_features)} LOO")


def knn_encode(train_df, test_df, K=8):
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
    nn = NearestNeighbors(n_neighbors=min(K+1, len(loc_stats)))
    nn.fit(Xs)

    cluster_map = {}
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

    tv = test_df[morph].fillna(loc_stats[morph].median().to_dict()).iloc[0:1].values
    ts = sc.transform(tv)
    d, ix = nn.kneighbors(ts, n_neighbors=min(K+1, len(loc_stats)))
    nbrs = [(dd, int(j)) for dd, j in zip(d[0], ix[0])][:K]
    w = [loc_stats.iloc[j]["n_events"]/(dd+0.01) for dd, j in nbrs]
    test_cluster = sum(wi * loc_stats.iloc[j]["mean_target"]
                      for wi, (_, j) in zip(w, nbrs)) / sum(w)
    test_nn_std = np.std([loc_stats.iloc[j]["mean_target"] for _, j in nbrs])

    return cluster_map, test_cluster, test_nn_std


# ═══════════════════════════════════════
# 1. TEMPORAL HOLDOUT
# ═══════════════════════════════════════
print("\n" + "=" * 60)
print("1. TEMPORAL HOLDOUT")
print("=" * 60)
t0 = time.time()
df_s = df.sort_values("date")
split = int(len(df_s) * 0.8)
tr, te = df_s.iloc[:split], df_s.iloc[split:]
m = CatBoostRegressor(iterations=1000, depth=6, learning_rate=0.05,
                      l2_leaf_reg=3, verbose=0, random_seed=42)
m.fit(tr[full_with_loc], tr[TARGET])
pred_t = m.predict(te[full_with_loc])
r2_t = r2_score(te[TARGET], pred_t)
mae_t = mean_absolute_error(te[TARGET], pred_t)

# Ranking metrics: group by date, rank locations within each date
te_copy = te.copy()
te_copy["pred"] = pred_t
date_groups = te_copy.groupby("date")
ndcgs, pairwises, topk_hits = [], [], []
for date, group in date_groups:
    if len(group) < 3:
        continue
    loc_true = group.groupby("location")[TARGET].mean().values
    loc_pred = group.groupby("location")["pred"].mean().values
    if len(loc_true) < 2:
        continue
    ndcgs.append(ndcg_at_k(loc_true, loc_pred, k=3))
    pairwises.append(pairwise_accuracy(loc_true, loc_pred))
    topk_hits.append(top_k_hit_rate(loc_true, loc_pred, k=3))

print(f"  R²={r2_t:.4f}  MAE={mae_t:.3f}lb  [{time.time()-t0:.1f}s]")
if ndcgs:
    print(f"  NDCG@3={np.mean(ndcgs):.4f}  Pairwise={np.mean(pairwises):.4f}  TopK@3={np.mean(topk_hits):.4f}")
    print(f"  (computed over {len(ndcgs)} date-groups with 3+ locations)")


# ═══════════════════════════════════════
# 2. SPATIAL HOLDOUT
# ═══════════════════════════════════════
print("\n" + "=" * 60)
print("2. SPATIAL HOLDOUT")
print("=" * 60)
t0 = time.time()
df["_region"] = pd.cut(df.lat, bins=5, labels=["S", "SM", "M", "MN", "N"])
region_r2s = {}
all_spatial_true, all_spatial_pred = [], []
for region in df["_region"].unique():
    te_r = df[df._region == region]
    tr_r = df[df._region != region]
    if len(te_r) < 10: continue
    m = CatBoostRegressor(iterations=500, depth=6, learning_rate=0.05,
                          l2_leaf_reg=3, verbose=0, random_seed=42)
    m.fit(tr_r[full_with_loc], tr_r[TARGET])
    pred = m.predict(te_r[full_with_loc])
    r2 = r2_score(te_r[TARGET], pred)
    region_r2s[region] = (r2, len(te_r))
    all_spatial_true.extend(te_r[TARGET].tolist())
    all_spatial_pred.extend(pred.tolist())
    print(f"    {region}: R²={r2:.4f} (n={len(te_r)})")
r2_s = np.mean([r for r, _ in region_r2s.values()])
print(f"  Mean R²={r2_s:.4f}  [{time.time()-t0:.1f}s]")


# ═══════════════════════════════════════
# 3. SPATIOTEMPORAL BLOCKED
# ═══════════════════════════════════════
print("\n" + "=" * 60)
print("3. SPATIOTEMPORAL BLOCKED")
print("=" * 60)
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
# 4. LOO WITH FULL RANKING METRICS
# ═══════════════════════════════════════
print("\n" + "=" * 60)
print("4. LOO (with ranking metrics)")
print("=" * 60)
t0 = time.time()

loc_counts = df.location.value_counts()
loc_std = df.groupby("location")[TARGET].std()
clean_locs = [l for l in loc_counts[loc_counts >= MIN_LOO_N].index
              if loc_std.get(l, 0) >= MIN_LOO_STD]
print(f"  Clean LOO set: {len(clean_locs)} locations")

all_true, all_pred = [], []
per_loc = {}
loc_mean_true = {}
loc_mean_pred = {}

for i, loc in enumerate(clean_locs):
    mask = df.location == loc
    train, test = df[~mask].copy(), df[mask].copy()

    # KNN encoding
    cmap, test_cluster, test_nn_std = knn_encode(train, test)
    train["knn_enc"] = train.location.map(cmap).fillna(train[TARGET].mean())
    test["knn_enc"] = test_cluster
    train["knn_std"] = 0
    test["knn_std"] = test_nn_std

    # Location quality model
    loc_data = train.groupby("location").agg(
        loc_target=(TARGET, "mean"),
        n_events=(TARGET, "count"),
        **{f: (f, "first") for f in LOC_FEATURES}
    ).reset_index()
    sw = np.sqrt(loc_data["n_events"].values)
    lm = CatBoostRegressor(iterations=300, depth=5, learning_rate=0.05,
                           l2_leaf_reg=5, verbose=0, random_seed=42)
    lm.fit(loc_data[LOC_FEATURES], loc_data["loc_target"], sample_weight=sw)

    train_lp = train.groupby("location")[LOC_FEATURES].first().reset_index()
    train_lp["loc_quality"] = lm.predict(train_lp[LOC_FEATURES])
    train["loc_quality"] = train.location.map(
        dict(zip(train_lp.location, train_lp.loc_quality)))
    test["loc_quality"] = lm.predict(test[LOC_FEATURES].iloc[0:1])[0]

    # Ensemble
    extra = ["knn_enc", "knn_std", "loc_quality"]
    use = loo_features + extra
    configs = [
        dict(iterations=600, depth=6, learning_rate=0.05, l2_leaf_reg=3, random_seed=42),
        dict(iterations=800, depth=5, learning_rate=0.03, l2_leaf_reg=5, random_seed=123),
        dict(iterations=500, depth=7, learning_rate=0.05, l2_leaf_reg=2, random_seed=456),
    ]
    preds = []
    for cfg in configs:
        m = CatBoostRegressor(**cfg, verbose=0)
        m.fit(train[use], train[TARGET])
        preds.append(m.predict(test[use]))
    pred = np.clip(np.mean(preds, axis=0), 0, 30)

    r2 = r2_score(test[TARGET], pred) if len(test) > 1 and np.std(test[TARGET]) > 0.01 else 0.0
    per_loc[loc] = r2
    loc_mean_true[loc] = test[TARGET].mean()
    loc_mean_pred[loc] = pred.mean()

    if (i + 1) % 10 == 0:
        interim = r2_score(all_true + list(test[TARGET]), all_pred + list(pred))
        print(f"  {i+1}/{len(clean_locs)} interim R²={interim:.4f}")

    all_true.extend(test[TARGET].tolist())
    all_pred.extend(pred.tolist())

# Regression metrics
r2_loo = r2_score(all_true, all_pred)
mae_loo = mean_absolute_error(all_true, all_pred)
positive = sum(1 for r in per_loc.values() if r > 0)

# Ranking metrics across LOO locations
true_means = [loc_mean_true[l] for l in clean_locs]
pred_means = [loc_mean_pred[l] for l in clean_locs]

ndcg3 = ndcg_at_k(true_means, pred_means, k=3)
ndcg5 = ndcg_at_k(true_means, pred_means, k=5)
ndcg10 = ndcg_at_k(true_means, pred_means, k=10)
pw_acc = pairwise_accuracy(true_means, pred_means)
topk3 = top_k_hit_rate(true_means, pred_means, k=3)
topk5 = top_k_hit_rate(true_means, pred_means, k=5)

print(f"\n  REGRESSION:")
print(f"    R²={r2_loo:.4f}  MAE={mae_loo:.3f}lb  [{time.time()-t0:.1f}s]")
print(f"    Positive R²: {positive}/{len(clean_locs)}")
print(f"    Median per-loc R²: {np.median(list(per_loc.values())):.4f}")

print(f"\n  RANKING (across {len(clean_locs)} LOO locations):")
print(f"    NDCG@3={ndcg3:.4f}  NDCG@5={ndcg5:.4f}  NDCG@10={ndcg10:.4f}")
print(f"    Pairwise accuracy={pw_acc:.4f}")
print(f"    Top-3 hit rate={topk3:.4f}  Top-5 hit rate={topk5:.4f}")

# Correlation between predicted and true location means
from scipy.stats import spearmanr, kendalltau
spear_r, spear_p = spearmanr(true_means, pred_means)
tau, tau_p = kendalltau(true_means, pred_means)
print(f"    Spearman ρ={spear_r:.4f} (p={spear_p:.4f})")
print(f"    Kendall τ={tau:.4f} (p={tau_p:.4f})")

# Show predicted vs true for top/bottom locations
print(f"\n  Location ranking (true vs predicted mean):")
ranking = sorted(zip(clean_locs, true_means, pred_means),
                 key=lambda x: x[1], reverse=True)
print(f"  {'Location':50s} {'True':>6s} {'Pred':>6s} {'Rank Δ':>7s}")
for rank, (loc, true, pred) in enumerate(ranking[:10]):
    pred_rank = sorted(range(len(pred_means)), key=lambda i: -pred_means[i]).index(
        clean_locs.index(loc))
    print(f"    {loc:48s} {true:6.1f} {pred:6.1f} {pred_rank-rank:+4d}")


# Worst/best
sorted_locs = sorted(per_loc.items(), key=lambda x: x[1])
print("\n  Worst 5:")
for loc, r2 in sorted_locs[:5]:
    n = loc_counts[loc]
    mn = df[df.location == loc][TARGET].mean()
    print(f"    {loc:50s} n={n:3d} R²={r2:8.3f} mean={mn:.1f}")
print("  Best 5:")
for loc, r2 in sorted_locs[-5:]:
    n = loc_counts[loc]
    mn = df[df.location == loc][TARGET].mean()
    print(f"    {loc:50s} n={n:3d} R²={r2:8.3f} mean={mn:.1f}")


# ═══════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════
print("\n" + "=" * 60)
print("PRODUCTION EVALUATION SUMMARY")
print("=" * 60)
print(f"  Temporal        : R²={r2_t:.4f}  MAE={mae_t:.3f}")
print(f"  Spatial         : R²={r2_s:.4f}")
print(f"  Spatiotemporal  : R²={r2_st:.4f}")
print(f"  LOO Regression  : R²={r2_loo:.4f}  MAE={mae_loo:.3f}")
print(f"  LOO Ranking     : NDCG@5={ndcg5:.4f}  Pairwise={pw_acc:.4f}  Spearman={spear_r:.4f}")
print(f"  LOO Top-5 Hit   : {topk5:.4f}")
