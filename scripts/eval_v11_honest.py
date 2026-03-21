"""v11 honest evaluation: fix loc_rolling_mean gaps with KNN priors.

Key improvements over v10:
1. Fill loc_rolling_mean NaN with KNN-based location quality prior
2. Blend rolling mean with KNN prior based on confidence (n_prior events)
3. Add learned location quality model as feature for ALL metrics (not just LOO)
4. CatBoost ensemble for all metrics
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
             "source"}
LEAKY_COLS = {"loc_mean_enc", "source_enc"}

MIN_LOO_STD = 0.5
MIN_LOO_N = 15

LOC_MORPH = ["lat", "lon", "area_acres", "is_lake", "max_depth_ft", "shore_dev",
             "creel_lmb_ratio", "creel_smb_ratio", "creel_cpue_mean"]


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


def build_knn_prior(df, K=8):
    """Build KNN-based location quality prior from morphometric features.

    For each location, estimates expected target from K nearest locations
    by morphometry. This is as-of safe because it uses location averages
    weighted by similarity, not temporal ordering.
    """
    morph = [c for c in LOC_MORPH if c in df.columns]
    loc_stats = df.groupby("location").agg(
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

    return knn_prior


def build_loc_quality_model(df, morph_features):
    """Train a CatBoost model to predict location mean from morphometry."""
    loc_data = df.groupby("location").agg(
        loc_target=(TARGET, "mean"),
        n_events=(TARGET, "count"),
        **{f: (f, "first") for f in morph_features}
    ).reset_index()

    sw = np.sqrt(loc_data["n_events"].values)
    lm = CatBoostRegressor(iterations=300, depth=5, learning_rate=0.05,
                           l2_leaf_reg=5, verbose=0, random_seed=42)
    lm.fit(loc_data[morph_features], loc_data["loc_target"], sample_weight=sw)

    # Predict for all locations
    quality = dict(zip(loc_data.location, lm.predict(loc_data[morph_features])))
    return quality, lm


def add_asof_loc_features(df, train_mask=None):
    """Add as-of safe location features with KNN prior gap-filling.

    For temporal/spatial: use rolling mean where available, KNN prior for gaps.
    Blend based on confidence (how many prior events at this location).
    """
    df = df.copy()

    # Build KNN prior from ALL data (this is safe since it's cross-location)
    knn_prior = build_knn_prior(df)
    df["knn_prior"] = df.location.map(knn_prior)

    # Blended location estimate: rolling_mean when confident, KNN prior when not
    # confidence = min(loc_n_prior / 5, 1)  -- need 5+ prior events for full confidence
    if "loc_rolling_mean" in df.columns and "loc_n_prior" in df.columns:
        confidence = np.clip(df["loc_n_prior"] / 5, 0, 1)
        rolling = df["loc_rolling_mean"].values
        prior = df["knn_prior"].values

        # Where rolling is available, blend; where not, use KNN prior
        blended = np.where(
            df["loc_rolling_mean"].notna(),
            confidence * rolling + (1 - confidence) * prior,
            prior
        )
        df["loc_blended_mean"] = blended
    else:
        df["loc_blended_mean"] = df["knn_prior"]

    return df


def add_asof_loc_features_split(train_df, test_df=None):
    """As-of safe location features for train/test split (temporal holdout).

    KNN prior computed from train only. Rolling mean already in data.
    """
    morph = [c for c in LOC_MORPH if c in train_df.columns]

    # KNN prior from train locations only
    knn_prior = build_knn_prior(train_df)
    train_df = train_df.copy()
    train_df["knn_prior"] = train_df.location.map(knn_prior)

    # Loc quality model from train
    quality, lm = build_loc_quality_model(train_df, morph)
    train_df["loc_quality"] = train_df.location.map(quality)

    # Blend rolling mean with KNN prior
    if "loc_rolling_mean" in train_df.columns:
        conf = np.clip(train_df["loc_n_prior"].fillna(0) / 5, 0, 1)
        rolling = train_df["loc_rolling_mean"].values
        prior = train_df["knn_prior"].values
        train_df["loc_blended"] = np.where(
            train_df["loc_rolling_mean"].notna(),
            conf * rolling + (1 - conf) * prior,
            prior
        )
    else:
        train_df["loc_blended"] = train_df["knn_prior"]

    if test_df is not None:
        test_df = test_df.copy()

        # KNN prior for test locations (some may be new)
        loc_stats = train_df.groupby("location").agg(
            mean_target=(TARGET, "mean"),
            n_events=(TARGET, "count"),
            **{f: (f, "first") for f in morph}
        ).reset_index()

        X = loc_stats[morph].fillna(loc_stats[morph].median()).values
        sc = StandardScaler().fit(X)
        Xs = sc.transform(X)
        nn = NearestNeighbors(n_neighbors=min(9, len(loc_stats)))
        nn.fit(Xs)

        test_knn = {}
        for loc in test_df.location.unique():
            row = test_df[test_df.location == loc].iloc[0]
            tv = pd.DataFrame([row])[morph].fillna(loc_stats[morph].median().to_dict()).values
            ts = sc.transform(tv)
            d, ix = nn.kneighbors(ts, n_neighbors=min(9, len(loc_stats)))
            nbrs = [(dd, int(j)) for dd, j in zip(d[0], ix[0])][:8]
            w = [loc_stats.iloc[j]["n_events"]/(dd+0.01) for dd, j in nbrs]
            test_knn[loc] = sum(wi * loc_stats.iloc[j]["mean_target"]
                               for wi, (_, j) in zip(w, nbrs)) / sum(w)

        test_df["knn_prior"] = test_df.location.map(test_knn)

        # Loc quality for test
        test_lp = test_df.groupby("location")[morph].first().reset_index()
        test_lp["loc_quality"] = lm.predict(test_lp[morph].fillna(loc_stats[morph].median().to_dict()))
        test_df["loc_quality"] = test_df.location.map(
            dict(zip(test_lp.location, test_lp.loc_quality)))

        # Blend for test
        if "loc_rolling_mean" in test_df.columns:
            conf = np.clip(test_df["loc_n_prior"].fillna(0) / 5, 0, 1)
            rolling = test_df["loc_rolling_mean"].values
            prior = test_df["knn_prior"].values
            test_df["loc_blended"] = np.where(
                test_df["loc_rolling_mean"].notna(),
                conf * rolling + (1 - conf) * prior,
                prior
            )
        else:
            test_df["loc_blended"] = test_df["knn_prior"]

    return train_df, test_df


# ═══════════════════════════════════════
# LOAD DATA
# ═══════════════════════════════════════
df = pd.read_csv("castline/validation/data/assembled/validation_dataset_v10.csv",
                 low_memory=False)
df = df[df[TARGET].notna()].copy()
print(f"Dataset: {len(df)} rows, {df.location.nunique()} locations")

all_cols = set(df.columns)
exclude = ALWAYS_EXCLUDE | LOCATION_IDENTITY | META_COLS | LEAKY_COLS | {TARGET}
base_features = sorted(all_cols - exclude)
base_features = [f for f in base_features if df[f].dtype in ["float64", "int64", "float32", "int32"]]

# Additional engineered features to add
EXTRA_FEATURES = ["knn_prior", "loc_quality", "loc_blended"]

# For LOO: exclude location-specific rolling features
loo_exclude = {"loc_rolling_mean", "loc_rolling_std", "loc_n_prior",
               "loc_encoding_confidence", "loc_blended"}

morph_features = [c for c in LOC_MORPH if c in df.columns]

print(f"Base features: {len(base_features)}")
print(f"loc_rolling_mean coverage: {df['loc_rolling_mean'].notna().mean()*100:.1f}%")


# ═══════════════════════════════════════
# 1. TEMPORAL (AS-OF SAFE with KNN gap-fill)
# ═══════════════════════════════════════
print("\n" + "=" * 60)
print("1. TEMPORAL HOLDOUT (as-of safe + KNN prior)")
print("=" * 60)
t0 = time.time()
df_s = df.sort_values("date")
split = int(len(df_s) * 0.8)
tr_raw, te_raw = df_s.iloc[:split].copy(), df_s.iloc[split:].copy()

tr, te = add_asof_loc_features_split(tr_raw, te_raw)

temporal_features = base_features + EXTRA_FEATURES
temporal_features = [f for f in temporal_features if f in tr.columns]

# Ensemble
configs = [
    dict(iterations=1000, depth=6, learning_rate=0.05, l2_leaf_reg=3, random_seed=42),
    dict(iterations=1200, depth=5, learning_rate=0.03, l2_leaf_reg=5, random_seed=123),
    dict(iterations=800, depth=7, learning_rate=0.05, l2_leaf_reg=2, random_seed=456),
]
preds_t = []
for cfg in configs:
    m = CatBoostRegressor(**cfg, verbose=0)
    m.fit(tr[temporal_features], tr[TARGET])
    preds_t.append(m.predict(te[temporal_features]))
pred_t = np.mean(preds_t, axis=0)

r2_t = r2_score(te[TARGET], pred_t)
mae_t = mean_absolute_error(te[TARGET], pred_t)

# Feature importance from first model
m0 = CatBoostRegressor(**configs[0], verbose=0)
m0.fit(tr[temporal_features], tr[TARGET])
imp = pd.Series(m0.feature_importances_, index=temporal_features).nlargest(10)

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
    te_mask = df._region == region
    tr_r, te_r = add_asof_loc_features_split(df[~te_mask], df[te_mask])
    if len(te_r) < 10: continue

    use_feats = [f for f in temporal_features if f in tr_r.columns]
    preds_s = []
    for cfg in configs[:2]:  # 2 models for speed
        m = CatBoostRegressor(**cfg, verbose=0)
        m.fit(tr_r[use_feats], tr_r[TARGET])
        preds_s.append(m.predict(te_r[use_feats]))
    pred_s = np.mean(preds_s, axis=0)
    r2 = r2_score(te_r[TARGET], pred_s)
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
    tr_r, te_r = add_asof_loc_features_split(df.iloc[tr_idx], df.iloc[te_idx])
    use_feats = [f for f in temporal_features if f in tr_r.columns]
    m = CatBoostRegressor(iterations=600, depth=6, learning_rate=0.05,
                          l2_leaf_reg=3, verbose=0, random_seed=42)
    m.fit(tr_r[use_feats], tr_r[TARGET])
    fold_r2s.append(r2_score(te_r[TARGET], m.predict(te_r[use_feats])))
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

# LOO features: no location-specific rolling features
loo_base = [f for f in base_features if f not in loo_exclude]
loo_use = loo_base + ["knn_prior", "loc_quality"]

all_true, all_pred = [], []
per_loc = {}
loc_mean_true, loc_mean_pred = {}, {}

for i, loc in enumerate(clean_locs):
    mask = df.location == loc
    train_raw, test_raw = df[~mask].copy(), df[mask].copy()

    # KNN prior from train only (excluding held-out location)
    knn_prior = build_knn_prior(train_raw)
    train_raw["knn_prior"] = train_raw.location.map(knn_prior)

    # Loc quality model from train
    quality, lm = build_loc_quality_model(train_raw, morph_features)
    train_raw["loc_quality"] = train_raw.location.map(quality)

    # For test: compute KNN prior and loc_quality from train data
    loc_stats = train_raw.groupby("location").agg(
        mean_target=(TARGET, "mean"),
        n_events=(TARGET, "count"),
        **{f: (f, "first") for f in morph_features}
    ).reset_index()

    X = loc_stats[morph_features].fillna(loc_stats[morph_features].median()).values
    sc = StandardScaler().fit(X)
    Xs = sc.transform(X)
    nn = NearestNeighbors(n_neighbors=min(9, len(loc_stats)))
    nn.fit(Xs)

    tv = test_raw[morph_features].fillna(
        loc_stats[morph_features].median().to_dict()).iloc[0:1].values
    ts = sc.transform(tv)
    d, ix = nn.kneighbors(ts, n_neighbors=min(9, len(loc_stats)))
    nbrs = [(dd, int(j)) for dd, j in zip(d[0], ix[0])][:8]
    w = [loc_stats.iloc[j]["n_events"]/(dd+0.01) for dd, j in nbrs]
    test_knn = sum(wi * loc_stats.iloc[j]["mean_target"]
                   for wi, (_, j) in zip(w, nbrs)) / sum(w)

    test_raw = test_raw.copy()
    test_raw["knn_prior"] = test_knn
    test_raw["loc_quality"] = lm.predict(
        test_raw[morph_features].fillna(
            loc_stats[morph_features].median().to_dict()).iloc[0:1])[0]

    use = [f for f in loo_use if f in train_raw.columns]

    # Ensemble
    loo_configs = [
        dict(iterations=600, depth=6, learning_rate=0.05, l2_leaf_reg=3, random_seed=42),
        dict(iterations=800, depth=5, learning_rate=0.03, l2_leaf_reg=5, random_seed=123),
        dict(iterations=500, depth=7, learning_rate=0.05, l2_leaf_reg=2, random_seed=456),
    ]
    preds = []
    for cfg in loo_configs:
        m = CatBoostRegressor(**cfg, verbose=0)
        m.fit(train_raw[use], train_raw[TARGET])
        preds.append(m.predict(test_raw[use]))
    pred = np.clip(np.mean(preds, axis=0), 0, 30)

    r2 = r2_score(test_raw[TARGET], pred) if len(test_raw) > 1 and np.std(test_raw[TARGET]) > 0.01 else 0.0
    per_loc[loc] = r2
    loc_mean_true[loc] = test_raw[TARGET].mean()
    loc_mean_pred[loc] = pred.mean()

    if (i + 1) % 10 == 0:
        interim = r2_score(all_true + list(test_raw[TARGET]), all_pred + list(pred))
        print(f"  {i+1}/{len(clean_locs)} interim R²={interim:.4f}")

    all_true.extend(test_raw[TARGET].tolist())
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
print("v11 HONEST SUMMARY")
print("=" * 60)
print(f"  Temporal (as-of safe)  : R²={r2_t:.4f}  MAE={mae_t:.3f}")
print(f"  Spatial                : R²={r2_s:.4f}")
print(f"  Spatiotemporal         : R²={r2_st:.4f}")
print(f"  LOO Regression         : R²={r2_loo:.4f}  MAE={mae_loo:.3f}")
print(f"  LOO Ranking            : NDCG@5={ndcg5:.4f}  Pairwise={pw:.4f}")
print(f"  LOO Spearman           : ρ={spear_r:.4f}")
print()
print("Comparison:")
print("  v10 honest: T=0.424 S=0.477 ST=0.596 LOO=0.575")
print(f"  v11 honest: T={r2_t:.3f} S={r2_s:.3f} ST={r2_st:.3f} LOO={r2_loo:.3f}")
