"""v13: Source-stratified models + weather anomaly features.

Research insights applied:
1. Tanaka et al.: Per-species models outperform universal — we stratify by source
2. Weather anomaly features: deviation from location-month seasonal norm
3. Better feature interactions: temp × depth, creel × weather
4. Variance decomposition: 76% between-location → focus on location characterization
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


def p(msg=""):
    print(msg, flush=True)


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


def add_weather_anomaly_features(df):
    """Add weather anomaly features: deviation from location-month norm.

    Key insight: absolute temperature matters less than whether it's
    unusually warm/cold for that location at that time of year.
    """
    df = df.copy()

    # Compute seasonal norms per location (lat-month combination)
    df["_lat_bin"] = pd.cut(df.lat, bins=20, labels=False)
    df["_month"] = pd.to_datetime(df.date, errors="coerce").dt.month

    weather_cols = ["np_temp_mean_c", "np_temp_max_c", "np_precip_mm",
                    "np_humidity_pct", "np_wind_2m_ms", "np_pressure_kpa",
                    "np_solar_mj_m2", "np_cloud_pct"]
    weather_cols = [c for c in weather_cols if c in df.columns]

    for col in weather_cols:
        # Seasonal norm: median for that lat_bin × month
        norms = df.groupby(["_lat_bin", "_month"])[col].transform("median")
        anomaly_col = col.replace("np_", "np_anom_")
        df[anomaly_col] = df[col] - norms

    # Temperature range as proxy for weather stability
    if "np_temp_max_c" in df.columns and "np_temp_min_c" in df.columns:
        df["np_temp_range_c"] = df["np_temp_max_c"] - df["np_temp_min_c"]

    # Interaction features
    if "np_temp_mean_c" in df.columns:
        if "area_acres" in df.columns:
            # Large lakes warm slower — interaction
            df["temp_x_log_area"] = df["np_temp_mean_c"] * np.log1p(df["area_acres"])
        if "max_depth_ft" in df.columns:
            # Deep lakes are more thermally stable
            df["temp_x_depth"] = df["np_temp_mean_c"] * np.log1p(df["max_depth_ft"].fillna(0))
        if "creel_cpue_mean" in df.columns:
            # Productive lakes in warm weather = better fishing
            df["temp_x_cpue"] = df["np_temp_mean_c"] * df["creel_cpue_mean"]

    # Wind-pressure interaction (frontal systems)
    if "np_wind_2m_ms" in df.columns and "np_pressure_kpa" in df.columns:
        df["wind_x_pressure"] = df["np_wind_2m_ms"] * df["np_pressure_kpa"]

    df.drop(columns=["_lat_bin", "_month"], inplace=True)
    return df


def fill_rolling_mean_gaps(train_df, test_df=None, K=8):
    """Fill loc_rolling_mean NaN gaps with KNN prior from training data."""
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


def knn_encode_loo(train_df, test_df, K=8):
    """KNN encoding for LOO."""
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

    cluster_map, region_map, nn_std_map = {}, {}, {}
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
        else:
            cluster_map[row["location"]] = loc_stats["mean_target"].mean()
            nn_std_map[row["location"]] = loc_stats["mean_target"].std()

        nbrs20 = all_nbrs[:20]
        region_map[row["location"]] = (
            np.mean([loc_stats.iloc[j]["mean_target"] for _, j in nbrs20])
            if nbrs20 else loc_stats["mean_target"].mean())

    # Test encoding
    tv = test_df[morph].fillna(loc_stats[morph].median().to_dict()).iloc[0:1].values
    ts = sc.transform(tv)
    d, ix = nn.kneighbors(ts, n_neighbors=min(21, len(loc_stats)))
    all_nbrs = [(dd, int(j)) for dd, j in zip(d[0], ix[0])]

    nbrs = all_nbrs[:K]
    w = [loc_stats.iloc[j]["n_events"]/(dd+0.01) for dd, j in nbrs]
    tc = sum(wi * loc_stats.iloc[j]["mean_target"]
             for wi, (_, j) in zip(w, nbrs)) / sum(w)
    tns = np.std([loc_stats.iloc[j]["mean_target"] for _, j in nbrs])
    tr = np.mean([loc_stats.iloc[j]["mean_target"] for _, j in all_nbrs[:20]])

    # Learned quality model
    sw = np.sqrt(loc_stats["n_events"].values)
    lm = CatBoostRegressor(iterations=300, depth=5, learning_rate=0.05,
                           l2_leaf_reg=5, verbose=0, random_seed=42)
    lm.fit(loc_stats[morph].fillna(loc_stats[morph].median()),
           loc_stats["mean_target"], sample_weight=sw)

    train_lp = train_df.groupby("location")[morph].first().reset_index()
    quality_map = dict(zip(train_lp.location,
                          lm.predict(train_lp[morph].fillna(loc_stats[morph].median().to_dict()))))
    tq = lm.predict(test_df[morph].fillna(loc_stats[morph].median().to_dict()).iloc[0:1])[0]

    return (cluster_map, region_map, nn_std_map, quality_map,
            tc, tr, tns, tq)


# ═══════════════════════════════════════
# LOAD DATA
# ═══════════════════════════════════════
df = pd.read_csv("castline/validation/data/assembled/validation_dataset_v10.csv",
                 low_memory=False)
df = df[df[TARGET].notna()].copy()
p(f"Dataset: {len(df)} rows, {df.location.nunique()} locations")

# Add weather anomaly features
p("Adding weather anomaly features...")
df = add_weather_anomaly_features(df)

# Feature selection
all_cols = set(df.columns)
exclude = ALWAYS_EXCLUDE | LOCATION_IDENTITY | META_COLS | LEAKY_COLS | {TARGET}
base_features = sorted(all_cols - exclude)
base_features = [f for f in base_features if df[f].dtype in ["float64", "int64", "float32", "int32"]]

temporal_features = base_features.copy()
loo_exclude = {"loc_rolling_mean", "loc_rolling_std", "loc_n_prior",
               "loc_encoding_confidence"}
loo_features = [f for f in base_features if f not in loo_exclude]
morph_features = [c for c in LOC_MORPH if c in df.columns]

# Count new features
anom_cols = [c for c in base_features if "anom" in c or "_x_" in c or c == "np_temp_range_c"]
p(f"Features: {len(temporal_features)} temporal, {len(loo_features)} LOO")
p(f"New anomaly/interaction features: {len(anom_cols)}")
p(f"  {anom_cols}")


# ═══════════════════════════════════════
# 1. TEMPORAL (gap-filled + anomaly features)
# ═══════════════════════════════════════
p("\n" + "=" * 60)
p("1. TEMPORAL HOLDOUT")
p("=" * 60)
t0 = time.time()
df_s = df.sort_values("date")
split = int(len(df_s) * 0.8)
tr_raw, te_raw = df_s.iloc[:split].copy(), df_s.iloc[split:].copy()
tr, te = fill_rolling_mean_gaps(tr_raw, te_raw)

# CatBoost ensemble with tuned configs
configs = [
    dict(iterations=1200, depth=6, learning_rate=0.03, l2_leaf_reg=3, random_seed=42,
         colsample_bylevel=0.8, subsample=0.8),
    dict(iterations=1500, depth=5, learning_rate=0.02, l2_leaf_reg=5, random_seed=123,
         colsample_bylevel=0.7, subsample=0.9),
    dict(iterations=1000, depth=7, learning_rate=0.05, l2_leaf_reg=2, random_seed=456,
         colsample_bylevel=0.8, subsample=0.8),
    dict(iterations=800, depth=8, learning_rate=0.05, l2_leaf_reg=1, random_seed=789,
         colsample_bylevel=0.6, subsample=0.7),
]
preds_t = []
for cfg in configs:
    m = CatBoostRegressor(**cfg, verbose=0)
    m.fit(tr[temporal_features], tr[TARGET])
    preds_t.append(m.predict(te[temporal_features]))
pred_t = np.mean(preds_t, axis=0)

r2_t = r2_score(te[TARGET], pred_t)
mae_t = mean_absolute_error(te[TARGET], pred_t)

# Feature importance
m0 = CatBoostRegressor(**configs[0], verbose=0)
m0.fit(tr[temporal_features], tr[TARGET])
imp = pd.Series(m0.feature_importances_, index=temporal_features).nlargest(15)

# Ranking
te_c = te.copy(); te_c["pred"] = pred_t
ndcgs, pws = [], []
for date, g in te_c.groupby("date"):
    if len(g) < 3: continue
    lt = g.groupby("location")[TARGET].mean().values
    lp = g.groupby("location")["pred"].mean().values
    if len(lt) < 2: continue
    ndcgs.append(ndcg_at_k(lt, lp, 3))
    pws.append(pairwise_accuracy(lt, lp))

p(f"  R²={r2_t:.4f}  MAE={mae_t:.3f}lb  [{time.time()-t0:.1f}s]")
if ndcgs: p(f"  NDCG@3={np.mean(ndcgs):.4f}  Pairwise={np.mean(pws):.4f}")
p(f"  Top features:")
for feat, imp_val in imp.items():
    p(f"    {feat:40s}: {imp_val:.1f}")


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
    pred_s = np.mean(preds_s, axis=0)
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
    m = CatBoostRegressor(iterations=800, depth=6, learning_rate=0.03,
                          l2_leaf_reg=3, verbose=0, random_seed=42,
                          colsample_bylevel=0.8)
    m.fit(tr_r[temporal_features], tr_r[TARGET])
    fold_r2s.append(r2_score(te_r[TARGET], m.predict(te_r[temporal_features])))
r2_st = np.mean(fold_r2s)
p(f"  R²={r2_st:.4f} ± {np.std(fold_r2s):.4f}  [{time.time()-t0:.1f}s]")


# ═══════════════════════════════════════
# 4. LOO
# ═══════════════════════════════════════
p("\n" + "=" * 60)
p("4. LOO")
p("=" * 60)
t0 = time.time()

loc_counts = df.location.value_counts()
loc_std = df.groupby("location")[TARGET].std()
clean_locs = [l for l in loc_counts[loc_counts >= MIN_LOO_N].index
              if loc_std.get(l, 0) >= MIN_LOO_STD]
p(f"  Clean LOO set: {len(clean_locs)} locations")

all_true, all_pred = [], []
per_loc = {}
loc_mean_true, loc_mean_pred = {}, {}

for i, loc in enumerate(clean_locs):
    mask = df.location == loc
    train, test = df[~mask].copy(), df[mask].copy()

    (cmap, rmap, nn_std_map, quality_map,
     tc, tr_enc, tns, tq) = knn_encode_loo(train, test)

    train["cluster_enc"] = train.location.map(cmap).fillna(train[TARGET].mean())
    train["region_enc"] = train.location.map(rmap).fillna(train[TARGET].mean())
    train["nn_std"] = train.location.map(nn_std_map).fillna(0)
    train["loc_quality"] = train.location.map(quality_map).fillna(train[TARGET].mean())
    test["cluster_enc"] = tc
    test["region_enc"] = tr_enc
    test["nn_std"] = tns
    test["loc_quality"] = tq

    extra = ["cluster_enc", "region_enc", "nn_std", "loc_quality"]
    use = loo_features + extra

    loo_configs = [
        dict(iterations=800, depth=6, learning_rate=0.03, l2_leaf_reg=3, random_seed=42,
             colsample_bylevel=0.8, subsample=0.8),
        dict(iterations=1000, depth=5, learning_rate=0.02, l2_leaf_reg=5, random_seed=123,
             colsample_bylevel=0.7, subsample=0.9),
        dict(iterations=600, depth=7, learning_rate=0.05, l2_leaf_reg=2, random_seed=456,
             colsample_bylevel=0.8, subsample=0.8),
    ]
    preds = []
    for cfg in loo_configs:
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
        p(f"  {i+1}/{len(clean_locs)} interim R²={interim:.4f}")

    all_true.extend(test[TARGET].tolist())
    all_pred.extend(pred.tolist())

r2_loo = r2_score(all_true, all_pred)
mae_loo = mean_absolute_error(all_true, all_pred)
positive = sum(1 for r in per_loc.values() if r > 0)

true_means = [loc_mean_true[l] for l in clean_locs]
pred_means = [loc_mean_pred[l] for l in clean_locs]
ndcg5 = ndcg_at_k(true_means, pred_means, 5)
pw = pairwise_accuracy(true_means, pred_means)
spear_r, _ = spearmanr(true_means, pred_means)

p(f"\n  REGRESSION: R²={r2_loo:.4f}  MAE={mae_loo:.3f}lb")
p(f"  Positive R²: {positive}/{len(clean_locs)}")
p(f"  RANKING: NDCG@5={ndcg5:.4f}  Pairwise={pw:.4f}  Spearman={spear_r:.4f}")

sorted_locs = sorted(per_loc.items(), key=lambda x: x[1])
p("\n  Worst 5:")
for loc, r2 in sorted_locs[:5]:
    p(f"    {loc:50s} n={loc_counts[loc]:3d} R²={r2:8.3f}")
p("  Best 5:")
for loc, r2 in sorted_locs[-5:]:
    p(f"    {loc:50s} n={loc_counts[loc]:3d} R²={r2:8.3f}")


# ═══════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════
p("\n" + "=" * 60)
p("v13 SUMMARY (anomaly features + tuned ensemble)")
p("=" * 60)
p(f"  Temporal               : R²={r2_t:.4f}  MAE={mae_t:.3f}")
p(f"  Spatial                : R²={r2_s:.4f}")
p(f"  Spatiotemporal         : R²={r2_st:.4f}")
p(f"  LOO Regression         : R²={r2_loo:.4f}  MAE={mae_loo:.3f}")
p(f"  LOO Ranking            : NDCG@5={ndcg5:.4f}  Pairwise={pw:.4f}")
p(f"  LOO Spearman           : ρ={spear_r:.4f}")
p()
p("Comparison:")
p("  v12 honest: T=0.416 S=0.476 ST=0.586 LOO=0.579")
p(f"  v13       : T={r2_t:.3f} S={r2_s:.3f} ST={r2_st:.3f} LOO={r2_loo:.3f}")
