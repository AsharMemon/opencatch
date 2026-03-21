"""v10 production evaluation: freshness features + as-of safe + ranking metrics.

Uses loc_rolling_mean (as-of safe) instead of loc_mean_enc (leaky) for temporal.
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

TARGET = "median_weight_lb"
ALWAYS_EXCLUDE = {"target_success_score", "tms_id", "event_id"}
LOCATION_IDENTITY = {"loc_enc", "trail_mean_weight", "location_mean_weight",
                     "loc_rolling_3", "baseline_signal"}
META_COLS = {"date", "location", "region", "block", "sat_source",
             "usgs_site_id", "event_name", "tournament_slug",
             "trail", "results_source", "species", "spawn_phase",
             "source"}  # source is now represented by is_bassmaster etc.

# As-of safe: use loc_rolling_mean instead of loc_mean_enc
# loc_rolling_mean only uses prior events at that location
LEAKY_COLS = {"loc_mean_enc", "source_enc"}

MIN_LOO_STD = 0.5
MIN_LOO_N = 15


def ndcg_at_k(true_scores, pred_scores, k):
    t, p = np.array(true_scores), np.array(pred_scores)
    if len(t) < 2: return 1.0
    pred_order = np.argsort(-p)
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
    ).reset_index()

    morph = ["lat", "lon", "area_acres", "is_lake",
             "creel_lmb_ratio", "creel_smb_ratio", "creel_cpue_mean"]
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

    return cluster_map, test_cluster


# ═══════════════════════════════════════
# LOAD DATA
# ═══════════════════════════════════════
df = pd.read_csv("castline/validation/data/assembled/validation_dataset_v10.csv",
                 low_memory=False)
df = df[df[TARGET].notna()].copy()
print(f"Dataset: {len(df)} rows, {df.location.nunique()} locations")

all_cols = set(df.columns)
exclude = ALWAYS_EXCLUDE | LOCATION_IDENTITY | META_COLS | LEAKY_COLS | {TARGET}
full_features = sorted(all_cols - exclude)
full_features = [f for f in full_features if df[f].dtype in ["float64", "int64", "float32", "int32"]]

# For temporal/spatial: use as-of safe features (loc_rolling_mean replaces loc_mean_enc)
temporal_features = full_features.copy()

# For LOO: exclude location identity features
loo_features = [f for f in full_features
                if f not in LOCATION_IDENTITY
                and f not in {"loc_rolling_mean", "loc_rolling_std", "loc_n_prior",
                              "loc_encoding_confidence"}]

LOC_FEATURES = ["lat", "lon", "area_acres", "is_lake",
                "creel_lmb_ratio", "creel_smb_ratio", "creel_spotted_ratio",
                "creel_cpue_mean", "max_depth_ft",
                "is_bassmaster", "is_creel", "is_tourneyx"]
LOC_FEATURES = [f for f in LOC_FEATURES if f in df.columns]

print(f"Temporal features: {len(temporal_features)} (as-of safe)")
print(f"LOO features: {len(loo_features)}")
print(f"loc_rolling_mean coverage: {df['loc_rolling_mean'].notna().mean()*100:.1f}%")


# ═══════════════════════════════════════
# 1. TEMPORAL (AS-OF SAFE)
# ═══════════════════════════════════════
print("\n" + "=" * 60)
print("1. TEMPORAL HOLDOUT (as-of safe)")
print("=" * 60)
t0 = time.time()
df_s = df.sort_values("date")
split = int(len(df_s) * 0.8)
tr, te = df_s.iloc[:split], df_s.iloc[split:]
m = CatBoostRegressor(iterations=1000, depth=6, learning_rate=0.05,
                      l2_leaf_reg=3, verbose=0, random_seed=42)
m.fit(tr[temporal_features], tr[TARGET])
pred_t = m.predict(te[temporal_features])
r2_t = r2_score(te[TARGET], pred_t)
mae_t = mean_absolute_error(te[TARGET], pred_t)

# Ranking
te_c = te.copy()
te_c["pred"] = pred_t
ndcgs, pws = [], []
for date, g in te_c.groupby("date"):
    if len(g) < 3: continue
    lt = g.groupby("location")[TARGET].mean().values
    lp = g.groupby("location")["pred"].mean().values
    if len(lt) < 2: continue
    ndcgs.append(ndcg_at_k(lt, lp, 3))
    pws.append(pairwise_accuracy(lt, lp))

imp = pd.Series(m.feature_importances_, index=temporal_features).nlargest(10)
print(f"  R²={r2_t:.4f}  MAE={mae_t:.3f}lb  [{time.time()-t0:.1f}s]")
if ndcgs:
    print(f"  NDCG@3={np.mean(ndcgs):.4f}  Pairwise={np.mean(pws):.4f}")
print(f"  Top features: {list(zip(imp.index[:5], imp.values[:5].astype(int)))}")


# ═══════════════════════════════════════
# 2. SPATIAL
# ═══════════════════════════════════════
print("\n" + "=" * 60)
print("2. SPATIAL HOLDOUT")
print("=" * 60)
t0 = time.time()
df["_region"] = pd.cut(df.lat, bins=5, labels=["S", "SM", "M", "MN", "N"])
rr = {}
for region in df["_region"].unique():
    te_r = df[df._region == region]
    tr_r = df[df._region != region]
    if len(te_r) < 10: continue
    m = CatBoostRegressor(iterations=500, depth=6, learning_rate=0.05,
                          l2_leaf_reg=3, verbose=0, random_seed=42)
    m.fit(tr_r[temporal_features], tr_r[TARGET])
    r2 = r2_score(te_r[TARGET], m.predict(te_r[temporal_features]))
    rr[region] = (r2, len(te_r))
    print(f"    {region}: R²={r2:.4f} (n={len(te_r)})")
r2_s = np.mean([r for r, _ in rr.values()])
print(f"  Mean R²={r2_s:.4f}  [{time.time()-t0:.1f}s]")


# ═══════════════════════════════════════
# 3. SPATIOTEMPORAL
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
    m.fit(tr[temporal_features], tr[TARGET])
    fold_r2s.append(r2_score(te[TARGET], m.predict(te[temporal_features])))
r2_st = np.mean(fold_r2s)
print(f"  R²={r2_st:.4f} ± {np.std(fold_r2s):.4f}  [{time.time()-t0:.1f}s]")


# ═══════════════════════════════════════
# 4. LOO
# ═══════════════════════════════════════
print("\n" + "=" * 60)
print("4. LOO (with ranking)")
print("=" * 60)
t0 = time.time()

loc_counts = df.location.value_counts()
loc_std = df.groupby("location")[TARGET].std()
clean_locs = [l for l in loc_counts[loc_counts >= MIN_LOO_N].index
              if loc_std.get(l, 0) >= MIN_LOO_STD]
print(f"  Clean LOO set: {len(clean_locs)} locations")

all_true, all_pred = [], []
per_loc = {}
loc_mean_true, loc_mean_pred = {}, {}

for i, loc in enumerate(clean_locs):
    mask = df.location == loc
    train, test = df[~mask].copy(), df[mask].copy()

    cmap, tc = knn_encode(train, test)
    train["knn_enc"] = train.location.map(cmap).fillna(train[TARGET].mean())
    test["knn_enc"] = tc

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

    extra = ["knn_enc", "loc_quality"]
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

r2_loo = r2_score(all_true, all_pred)
mae_loo = mean_absolute_error(all_true, all_pred)
positive = sum(1 for r in per_loc.values() if r > 0)

# Ranking
true_means = [loc_mean_true[l] for l in clean_locs]
pred_means = [loc_mean_pred[l] for l in clean_locs]
ndcg5 = ndcg_at_k(true_means, pred_means, 5)
pw = pairwise_accuracy(true_means, pred_means)
spear_r, _ = spearmanr(true_means, pred_means)
tau, _ = kendalltau(true_means, pred_means)

print(f"\n  REGRESSION: R²={r2_loo:.4f}  MAE={mae_loo:.3f}lb")
print(f"  Positive R²: {positive}/{len(clean_locs)}")
print(f"  RANKING: NDCG@5={ndcg5:.4f}  Pairwise={pw:.4f}  Spearman={spear_r:.4f}  Kendall={tau:.4f}")

sorted_locs = sorted(per_loc.items(), key=lambda x: x[1])
print("\n  Worst 5:")
for loc, r2 in sorted_locs[:5]:
    print(f"    {loc:50s} n={loc_counts[loc]:3d} R²={r2:8.3f}")
print("  Best 5:")
for loc, r2 in sorted_locs[-5:]:
    print(f"    {loc:50s} n={loc_counts[loc]:3d} R²={r2:8.3f}")


# ═══════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════
print("\n" + "=" * 60)
print("PRODUCTION v10 SUMMARY")
print("=" * 60)
print(f"  Temporal (as-of safe)  : R²={r2_t:.4f}  MAE={mae_t:.3f}")
print(f"  Spatial                : R²={r2_s:.4f}")
print(f"  Spatiotemporal         : R²={r2_st:.4f}")
print(f"  LOO Regression         : R²={r2_loo:.4f}  MAE={mae_loo:.3f}")
print(f"  LOO Ranking            : NDCG@5={ndcg5:.4f}  Pairwise={pw:.4f}")
print(f"  LOO Spearman           : ρ={spear_r:.4f}")
print()
print("Comparison (v9 → v10):")
print("  v9:  T=0.723 S=0.749 ST=0.776 LOO=0.586 NDCG@5=0.963 Pairwise=0.863")
print(f"  v10: T={r2_t:.3f} S={r2_s:.3f} ST={r2_st:.3f} LOO={r2_loo:.3f} NDCG@5={ndcg5:.3f} Pairwise={pw:.3f}")
print()
print("Note: v10 temporal R² may be lower because loc_mean_enc (leaky) is")
print("replaced with loc_rolling_mean (as-of safe). This is the HONEST metric.")
