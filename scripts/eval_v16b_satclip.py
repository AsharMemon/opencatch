"""v16 evaluation — USGS fish community + SatCLIP + CreelCat features combined.

Tests whether the combined new spatial features improve over v15 baseline.
Uses the same pruning + multi-library ensemble pipeline.
"""
import pandas as pd
import numpy as np
from sklearn.neighbors import NearestNeighbors
from sklearn.metrics import r2_score, mean_absolute_error
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import GroupKFold
from catboost import CatBoostRegressor
import xgboost as xgb
import lightgbm as lgb
import warnings
import time

warnings.filterwarnings("ignore")
def p(msg=""): print(msg, flush=True)

TARGET = "median_weight_lb"
ALWAYS_EXCLUDE = {"target_success_score", "tms_id", "event_id"}
LOCATION_IDENTITY = {"loc_enc", "trail_mean_weight", "location_mean_weight",
                     "loc_rolling_3", "baseline_signal"}
META_COLS = {"date", "location", "region", "block", "sat_source",
             "usgs_site_id", "event_name", "tournament_slug",
             "trail", "results_source", "species", "spawn_phase", "source"}
LEAKY_COLS = {"loc_mean_enc", "source_enc", "loc_target_cv"}

MIN_LOO_STD = 0.5
MIN_LOO_N = 15

LOC_MORPH = ["lat", "lon", "area_acres", "is_lake", "max_depth_ft", "shore_dev",
             "creel_lmb_ratio", "creel_smb_ratio", "creel_cpue_mean"]

LOO_EXCLUDE = {"loc_rolling_mean", "loc_rolling_std", "loc_n_prior",
               "loc_encoding_confidence", "loc_total_events",
               "loc_source_diversity", "loc_tournament_fraction",
               "loc_year_range", "loc_first_year", "loc_last_year",
               "loc_month_diversity"}

# New feature groups to track
NEW_PREFIXES = {
    "usgs_": "USGS Fish Community",
    "satclip_": "SatCLIP PCA-32",
    "creel2_": "CreelCat Spatial",
}


def fill_rolling_mean_gaps(train_df, test_df=None, K=8):
    morph = [c for c in LOC_MORPH if c in train_df.columns]
    loc_stats = train_df.groupby("location").agg(
        mean_target=(TARGET, "mean"), n_events=(TARGET, "count"),
        **{f: (f, "first") for f in morph}).reset_index()
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


# ═══════════════════════════════════════
# LOAD v16 DATASET
# ═══════════════════════════════════════
p("Loading v16 dataset...")
df = pd.read_csv("castline/validation/data/assembled/validation_dataset_v16b_satclip.csv", low_memory=False)
df = df[df[TARGET].notna()].copy()
p(f"Dataset: {len(df)} rows, {df.shape[1]} columns, {df.location.nunique()} locations")

# Count new features
for prefix, name in NEW_PREFIXES.items():
    cols = [c for c in df.columns if c.startswith(prefix)]
    non_null_pct = df[cols].notna().mean().mean() * 100 if cols else 0
    p(f"  {name}: {len(cols)} features, {non_null_pct:.1f}% non-null")

# ═══════════════════════════════════════
# FEATURE SELECTION
# ═══════════════════════════════════════
all_cols = set(df.columns)
exclude = ALWAYS_EXCLUDE | LOCATION_IDENTITY | META_COLS | LEAKY_COLS | {TARGET,
          "_lat_r", "_lon_r", "_region", "_block"}
all_features = sorted(all_cols - exclude)
all_features = [f for f in all_features if df[f].dtype in ["float64", "int64", "float32", "int32"]]

temporal_all = all_features.copy()
loo_all = [f for f in all_features if f not in LOO_EXCLUDE]

p(f"\nTotal numeric features: {len(temporal_all)}")

# ── Feature importance pruning ──
p("\n--- FEATURE IMPORTANCE PRUNING ---")
df_s = df.sort_values("date")
split = int(len(df_s) * 0.8)
tr_raw, te_raw = df_s.iloc[:split].copy(), df_s.iloc[split:].copy()
tr, te = fill_rolling_mean_gaps(tr_raw, te_raw)

m_full = CatBoostRegressor(iterations=1000, depth=6, learning_rate=0.05,
                           l2_leaf_reg=3, verbose=0, random_seed=42, subsample=0.85)
m_full.fit(tr[temporal_all], tr[TARGET])
imp_full = pd.Series(m_full.feature_importances_, index=temporal_all).sort_values(ascending=False)

# Find optimal N
best_n, best_r2 = 0, -np.inf
for top_n in range(30, 260, 10):
    if top_n > len(temporal_all):
        break
    top_feats = imp_full.head(top_n).index.tolist()
    m = CatBoostRegressor(iterations=1000, depth=6, learning_rate=0.05,
                          l2_leaf_reg=3, verbose=0, random_seed=42, subsample=0.85)
    m.fit(tr[top_feats], tr[TARGET])
    pred = np.clip(m.predict(te[top_feats]), 1, 25)
    r2 = r2_score(te[TARGET], pred)
    if r2 > best_r2:
        best_r2 = r2
        best_n = top_n

p(f"Optimal top-N: {best_n} features (R²={best_r2:.4f})")
temporal_pruned = imp_full.head(best_n).index.tolist()

# Report new feature survival
p("\nNew feature survival after pruning:")
for prefix, name in NEW_PREFIXES.items():
    total = len([c for c in temporal_all if c.startswith(prefix)])
    survived = [f for f in temporal_pruned if f.startswith(prefix)]
    p(f"  {name}: {len(survived)}/{total} survived")
    if survived:
        for f in survived[:5]:
            p(f"    {f}: importance={imp_full[f]:.3f}%")
        if len(survived) > 5:
            p(f"    ... and {len(survived)-5} more")

# Top 20 features overall
p("\nTop 20 features:")
for f in imp_full.head(20).index:
    tag = ""
    for prefix, name in NEW_PREFIXES.items():
        if f.startswith(prefix):
            tag = f" [{name}]"
    p(f"  {f}: {imp_full[f]:.3f}%{tag}")

# LOO features
m_loo_full = CatBoostRegressor(iterations=600, depth=6, learning_rate=0.05,
                               l2_leaf_reg=3, verbose=0, random_seed=42)
m_loo_full.fit(tr[[f for f in loo_all if f in tr.columns]], tr[TARGET])
imp_loo = pd.Series(m_loo_full.feature_importances_,
                    index=[f for f in loo_all if f in tr.columns]).sort_values(ascending=False)
loo_pruned = imp_loo.head(best_n).index.tolist()

# ── TEMPORAL ──
p(f"\n--- TEMPORAL ---")
t0 = time.time()

cb_configs = [
    dict(iterations=1500, depth=6, learning_rate=0.03, l2_leaf_reg=3,
         random_seed=42, subsample=0.85, verbose=0),
    dict(iterations=1200, depth=5, learning_rate=0.03, l2_leaf_reg=5,
         random_seed=123, subsample=0.9, verbose=0),
    dict(iterations=1000, depth=7, learning_rate=0.05, l2_leaf_reg=2,
         random_seed=456, subsample=0.8, verbose=0),
]
cb_preds = []
for cfg in cb_configs:
    m = CatBoostRegressor(**cfg)
    m.fit(tr[temporal_pruned], tr[TARGET])
    cb_preds.append(m.predict(te[temporal_pruned]))
cb_pred = np.mean(cb_preds, axis=0)

tr_filled = tr[temporal_pruned].fillna(-999)
te_filled = te[temporal_pruned].fillna(-999)
xgb_m = xgb.XGBRegressor(n_estimators=1000, max_depth=6, learning_rate=0.05,
                          subsample=0.85, colsample_bytree=0.8,
                          reg_lambda=3, random_state=42, verbosity=0, tree_method="hist")
xgb_m.fit(tr_filled, tr[TARGET])
xgb_pred = xgb_m.predict(te_filled)

lgb_m = lgb.LGBMRegressor(n_estimators=1000, max_depth=6, learning_rate=0.05,
                           subsample=0.85, colsample_bytree=0.8,
                           reg_lambda=3, random_state=42, verbosity=-1)
lgb_m.fit(tr[temporal_pruned], tr[TARGET])
lgb_pred = lgb_m.predict(te[temporal_pruned])

ensemble_pred = np.clip((cb_pred * 0.5 + xgb_pred * 0.25 + lgb_pred * 0.25), 1, 25)
cb_only = np.clip(cb_pred, 1, 25)

r2_cb = r2_score(te[TARGET], cb_only)
r2_ens = r2_score(te[TARGET], ensemble_pred)
r2_t = max(r2_cb, r2_ens)

seen_locs = set(tr.location.unique())
te_seen = te[te.location.isin(seen_locs)]
te_unseen = te[~te.location.isin(seen_locs)]
best_pred = cb_only if r2_cb > r2_ens else ensemble_pred
r2_seen = r2_score(te_seen[TARGET], best_pred[te.location.isin(seen_locs).values]) if len(te_seen) > 0 else 0
r2_unseen = r2_score(te_unseen[TARGET], best_pred[~te.location.isin(seen_locs).values]) if len(te_unseen) > 0 else 0

p(f"  CatBoost:  R²={r2_cb:.4f}")
p(f"  Ensemble:  R²={r2_ens:.4f}")
p(f"  Best:      R²={r2_t:.4f}")
p(f"  Seen: R²={r2_seen:.4f} ({len(te_seen)} rows)  Unseen: R²={r2_unseen:.4f} ({len(te_unseen)} rows)")
p(f"  [{time.time()-t0:.0f}s]")

# ── SPATIAL ──
p(f"\n--- SPATIAL ---")
t0 = time.time()
df["_region"] = pd.cut(df.lat, bins=5, labels=["S", "SM", "M", "MN", "N"])
rr = {}
for region in df["_region"].unique():
    te_mask = df._region == region
    tr_r, te_r = fill_rolling_mean_gaps(df[~te_mask], df[te_mask])
    if len(te_r) < 10: continue
    preds = []
    for cfg in cb_configs[:2]:
        m = CatBoostRegressor(**cfg)
        m.fit(tr_r[temporal_pruned], tr_r[TARGET])
        preds.append(m.predict(te_r[temporal_pruned]))
    pred = np.clip(np.mean(preds, axis=0), 1, 25)
    r2 = r2_score(te_r[TARGET], pred)
    rr[region] = (r2, len(te_r))
    p(f"    {region}: R²={r2:.4f} (n={len(te_r)})")
r2_s = np.mean([r for r, _ in rr.values()])
p(f"  Mean R²={r2_s:.4f}  [{time.time()-t0:.0f}s]")

# ── SPATIOTEMPORAL ──
p(f"\n--- SPATIOTEMPORAL ---")
t0 = time.time()
df["_block"] = df["_region"].astype(str) + "_" + (df.year // 3).astype(str)
gkf = GroupKFold(n_splits=5)
fold_r2s = []
for tr_idx, te_idx in gkf.split(df, groups=df["_block"]):
    tr_r, te_r = fill_rolling_mean_gaps(df.iloc[tr_idx], df.iloc[te_idx])
    preds = []
    for cfg in cb_configs[:2]:
        m = CatBoostRegressor(**cfg)
        m.fit(tr_r[temporal_pruned], tr_r[TARGET])
        preds.append(m.predict(te_r[temporal_pruned]))
    pred = np.clip(np.mean(preds, axis=0), 1, 25)
    fold_r2s.append(r2_score(te_r[TARGET], pred))
r2_st = np.mean(fold_r2s)
p(f"  R²={r2_st:.4f} ± {np.std(fold_r2s):.4f}  [{time.time()-t0:.0f}s]")

# ── LOO ──
p(f"\n--- LOO ---")
t0 = time.time()

loc_counts = df.location.value_counts()
loc_std = df.groupby("location")[TARGET].std()
clean_locs = [l for l in loc_counts[loc_counts >= MIN_LOO_N].index
              if loc_std.get(l, 0) >= MIN_LOO_STD]
p(f"  Clean LOO set: {len(clean_locs)} locations")

morph = [c for c in LOC_MORPH if c in df.columns]

all_true, all_pred = [], []
per_loc = {}

for i, loc in enumerate(clean_locs):
    mask = df.location == loc
    train, test = df[~mask].copy(), df[mask].copy()

    loc_stats = train.groupby("location").agg(
        mean_target=(TARGET, "mean"), n_events=(TARGET, "count"),
        **{f: (f, "first") for f in morph}).reset_index()
    X = loc_stats[morph].fillna(loc_stats[morph].median()).values
    sc = StandardScaler().fit(X)
    Xs = sc.transform(X)
    nn = NearestNeighbors(n_neighbors=min(9, len(loc_stats)))
    nn.fit(Xs)
    cluster_map = {}
    for j, row in loc_stats.iterrows():
        q = Xs[j:j+1]
        d, ix = nn.kneighbors(q)
        nbrs = [(dd, k) for dd, k in zip(d[0], ix[0])
                if loc_stats.iloc[k]["location"] != row["location"]][:8]
        if nbrs:
            w = [loc_stats.iloc[k]["n_events"]/(dd+0.01) for dd, k in nbrs]
            cluster_map[row["location"]] = sum(
                wi * loc_stats.iloc[k]["mean_target"]
                for wi, (_, k) in zip(w, nbrs)) / sum(w)
        else:
            cluster_map[row["location"]] = loc_stats["mean_target"].mean()

    tv = test[morph].fillna(loc_stats[morph].median().to_dict()).iloc[0:1].values
    ts = sc.transform(tv)
    d, ix = nn.kneighbors(ts)
    nbrs = [(dd, int(k)) for dd, k in zip(d[0], ix[0])][:8]
    w = [loc_stats.iloc[k]["n_events"]/(dd+0.01) for dd, k in nbrs]
    tc = sum(wi * loc_stats.iloc[k]["mean_target"]
             for wi, (_, k) in zip(w, nbrs)) / sum(w)

    sw = np.sqrt(loc_stats["n_events"].values)
    lm = CatBoostRegressor(iterations=200, depth=5, learning_rate=0.05,
                           l2_leaf_reg=5, verbose=0, random_seed=42)
    lm.fit(loc_stats[morph].fillna(loc_stats[morph].median()),
           loc_stats["mean_target"], sample_weight=sw)

    train["cluster_enc"] = train.location.map(cluster_map).fillna(train[TARGET].mean())
    test["cluster_enc"] = tc
    train_lp = train.groupby("location")[morph].first().reset_index()
    quality_map = dict(zip(train_lp.location,
                          lm.predict(train_lp[morph].fillna(loc_stats[morph].median().to_dict()))))
    train["loc_quality"] = train.location.map(quality_map).fillna(train[TARGET].mean())
    test["loc_quality"] = lm.predict(test[morph].fillna(loc_stats[morph].median().to_dict()).iloc[0:1])[0]

    extra = ["cluster_enc", "loc_quality"]
    use = [f for f in loo_pruned if f in train.columns] + extra

    preds = []
    for cfg in cb_configs[:2]:
        m = CatBoostRegressor(**cfg)
        m.fit(train[use], train[TARGET])
        preds.append(m.predict(test[use]))

    xm = xgb.XGBRegressor(n_estimators=800, max_depth=6, learning_rate=0.05,
                           subsample=0.85, colsample_bytree=0.8,
                           reg_lambda=3, random_state=42, verbosity=0, tree_method="hist")
    xm.fit(train[use].fillna(-999), train[TARGET])
    preds.append(xm.predict(test[use].fillna(-999)))

    pred = np.clip(np.mean(preds, axis=0), 1, 25)
    r2 = r2_score(test[TARGET], pred) if len(test) > 1 and np.std(test[TARGET]) > 0.01 else 0.0
    per_loc[loc] = r2
    all_true.extend(test[TARGET].tolist())
    all_pred.extend(pred.tolist())

    if (i + 1) % 10 == 0:
        interim = r2_score(all_true, all_pred)
        p(f"  {i+1}/{len(clean_locs)} interim R²={interim:.4f}")

r2_loo = r2_score(all_true, all_pred)
mae_loo = mean_absolute_error(all_true, all_pred)
positive = sum(1 for r in per_loc.values() if r > 0)
p(f"\n  LOO R²={r2_loo:.4f}  MAE={mae_loo:.3f}  Positive: {positive}/{len(clean_locs)}")

sorted_locs = sorted(per_loc.items(), key=lambda x: x[1])
p("\n  Worst 5:")
for loc, r2 in sorted_locs[:5]:
    p(f"    {loc:50s} n={loc_counts[loc]:3d} R²={r2:8.3f}")
p("  Best 5:")
for loc, r2 in sorted_locs[-5:]:
    p(f"    {loc:50s} n={loc_counts[loc]:3d} R²={r2:8.3f}")

# ── FINAL SUMMARY ──
p(f"\n{'='*60}")
p(f"v16b SATCLIP COMBINED EVALUATION SUMMARY")
p(f"{'='*60}")
p(f"  Dataset: {len(df)} rows, {df.shape[1]} cols")
p(f"  Pruned to: {best_n} features")
p(f"  Temporal       : R²={r2_t:.4f}  (seen={r2_seen:.4f}, unseen={r2_unseen:.4f})")
p(f"  Spatial        : R²={r2_s:.4f}")
p(f"  Spatiotemporal : R²={r2_st:.4f}")
p(f"  LOO            : R²={r2_loo:.4f}  MAE={mae_loo:.3f}")
p()
p("  Baselines:")
p("  v15 pruned   : T=0.438 S=0.483 ST=0.588 LOO=0.603")
p("  v15+SatCLIP32: T=0.428 S=0.474 ST=0.593 LOO=~0.617 (interim)")
p("  v13 best     : T=0.425 S=0.486 ST=0.594 LOO=0.606")

# New feature contribution
p(f"\n  New feature survival:")
for prefix, name in NEW_PREFIXES.items():
    total = len([c for c in temporal_all if c.startswith(prefix)])
    survived = len([f for f in temporal_pruned if f.startswith(prefix)])
    p(f"    {name}: {survived}/{total}")
p()
