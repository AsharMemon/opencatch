"""v15 evaluation with feature selection: keep only proven features.

Strategy: Start from v13 baseline features, add ONLY the new features
that have clear predictive signal. CatBoost with 283 features on 6K rows
overfits — need to be selective.
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

# Only the NEW features that showed clear signal (r > 0.1 or domain importance)
SELECTED_NEW_FEATURES = {
    # From GDD/photoperiod (biology-backed, 100% coverage)
    "photoperiod_hrs", "photoperiod_change_min", "photoperiod_spawn_proximity",
    "gdd_calibrated", "gdd_log", "gdd_spawn_phase", "gdd_daily_rate",
    "seasonal_position", "gdd_feeding_optimality",
    # Thermal regime (biology-backed)
    "thermal_comfort", "thermal_stress", "spawn_probability",
    "stratification_likelihood",
    # Best lag features (per correlation + literature)
    "lag_temp_7d_mean", "lag_temp_3d_mean", "lag_temp_trend", "lag_temp_range",
    "lag_diurnal_mean", "lag_diurnal_std",
    "lag_pressure_range", "lag_pressure_std",
    "lag_precip_7d_sum", "lag_cloud_3d_mean",
    # LAGOS site features (clear signal for LOO)
    "lagos_epanutr_zone_enc", "lagos_glaciated", "lagos_state_enc",
    "lagos_elevation_m", "lagos_waterarea_ha", "lagos_island_pct",
    "lagos_connectivity_enc", "lagos_upstream_lakes_n",
    "lagos_elongation", "lagos_mbg_length_m",
}

# Location-derived features (exclude from LOO but OK for temporal/spatial)
LOC_DERIVED = {"loc_total_events", "loc_tournament_fraction", "loc_year_range",
               "loc_first_year", "loc_month_diversity",
               "loc_rolling_mean", "loc_rolling_std", "loc_n_prior",
               "loc_encoding_confidence", "loc_source_diversity", "loc_last_year"}


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
# LOAD DATA
# ═══════════════════════════════════════
df = pd.read_csv("castline/validation/data/assembled/validation_dataset_v15.csv", low_memory=False)
df = df[df[TARGET].notna()].copy()
p(f"Dataset: {len(df)} rows, {df.shape[1]} columns, {df.location.nunique()} locations")

all_cols = set(df.columns)
exclude = ALWAYS_EXCLUDE | LOCATION_IDENTITY | META_COLS | LEAKY_COLS | {TARGET}
base_features = sorted(all_cols - exclude)
base_features = [f for f in base_features if df[f].dtype in ["float64", "int64", "float32", "int32"]]

# Filter to v13 core + selected new features
v13_core = [f for f in base_features if f not in SELECTED_NEW_FEATURES and f not in LOC_DERIVED]
selected_new = [f for f in base_features if f in SELECTED_NEW_FEATURES]
loc_derived_avail = [f for f in base_features if f in LOC_DERIVED]

temporal_features = v13_core + selected_new + loc_derived_avail
# LOO: exclude location-derived
loo_features = v13_core + selected_new

p(f"v13 core features: {len(v13_core)}")
p(f"Selected new features: {len(selected_new)}")
p(f"  available: {selected_new}")
p(f"Loc derived (temporal only): {len(loc_derived_avail)}")
p(f"Total temporal: {len(temporal_features)}")
p(f"Total LOO: {len(loo_features)}")

# Higher iterations for better convergence
CONFIGS = [
    dict(iterations=1000, depth=6, learning_rate=0.05, l2_leaf_reg=3,
         random_seed=42, subsample=0.85, verbose=0),
    dict(iterations=1200, depth=5, learning_rate=0.03, l2_leaf_reg=5,
         random_seed=123, subsample=0.9, verbose=0),
]


# ═══════════════════════════════════════
# 1. TEMPORAL
# ═══════════════════════════════════════
p("\n" + "=" * 60)
p("1. TEMPORAL HOLDOUT")
p("=" * 60)
t0 = time.time()
df_s = df.sort_values("date")
split = int(len(df_s) * 0.8)
tr_raw, te_raw = df_s.iloc[:split].copy(), df_s.iloc[split:].copy()
tr, te = fill_rolling_mean_gaps(tr_raw, te_raw)

seen_locs = set(tr.location.unique())
te_seen = te[te.location.isin(seen_locs)]
te_unseen = te[~te.location.isin(seen_locs)]

preds_t = []
for cfg in CONFIGS:
    m = CatBoostRegressor(**cfg)
    m.fit(tr[temporal_features], tr[TARGET])
    preds_t.append(m.predict(te[temporal_features]))
pred = np.clip(np.mean(preds_t, axis=0), 1, 25)

r2_t = r2_score(te[TARGET], pred)
r2_seen = r2_score(te_seen[TARGET], pred[te.location.isin(seen_locs).values]) if len(te_seen) > 0 else float('nan')
r2_unseen = r2_score(te_unseen[TARGET], pred[~te.location.isin(seen_locs).values]) if len(te_unseen) > 0 else float('nan')

# Feature importance
m0 = CatBoostRegressor(**CONFIGS[0])
m0.fit(tr[temporal_features], tr[TARGET])
imp = pd.Series(m0.feature_importances_, index=temporal_features).nlargest(20)
p(f"  R²={r2_t:.4f}  (seen={r2_seen:.4f}, unseen={r2_unseen:.4f})  [{time.time()-t0:.0f}s]")
p("  Top features:")
for f, v in imp.items():
    marker = " ★" if f in SELECTED_NEW_FEATURES else ""
    p(f"    {f:40s} {v:.1f}%{marker}")


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
    for cfg in CONFIGS:
        m = CatBoostRegressor(**cfg)
        m.fit(tr_r[temporal_features], tr_r[TARGET])
        preds_s.append(m.predict(te_r[temporal_features]))
    pred_s = np.clip(np.mean(preds_s, axis=0), 1, 25)
    r2 = r2_score(te_r[TARGET], pred_s)
    rr[region] = (r2, len(te_r))
    p(f"    {region}: R²={r2:.4f} (n={len(te_r)})")
r2_s = np.mean([r for r, _ in rr.values()])
p(f"  Mean R²={r2_s:.4f}  [{time.time()-t0:.0f}s]")


# ═══════════════════════════════════════
# 3. SPATIOTEMPORAL
# ═══════════════════════════════════════
p("\n" + "=" * 60)
p("3. SPATIOTEMPORAL")
p("=" * 60)
t0 = time.time()
df["_block"] = df["_region"].astype(str) + "_" + (df.year // 3).astype(str)
gkf = GroupKFold(n_splits=5)
fold_r2s = []
for tr_idx, te_idx in gkf.split(df, groups=df["_block"]):
    tr_r, te_r = fill_rolling_mean_gaps(df.iloc[tr_idx], df.iloc[te_idx])
    preds = []
    for cfg in CONFIGS:
        m = CatBoostRegressor(**cfg)
        m.fit(tr_r[temporal_features], tr_r[TARGET])
        preds.append(m.predict(te_r[temporal_features]))
    pred = np.clip(np.mean(preds, axis=0), 1, 25)
    fold_r2s.append(r2_score(te_r[TARGET], pred))
r2_st = np.mean(fold_r2s)
p(f"  R²={r2_st:.4f} ± {np.std(fold_r2s):.4f}  [{time.time()-t0:.0f}s]")


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

morph = [c for c in LOC_MORPH if c in df.columns]
all_true, all_pred = [], []
per_loc = {}

for i, loc in enumerate(clean_locs):
    mask = df.location == loc
    train, test = df[~mask].copy(), df[mask].copy()

    # KNN encoding
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
    use = loo_features + extra

    preds = []
    for cfg in CONFIGS:
        m = CatBoostRegressor(**cfg)
        m.fit(train[use], train[TARGET])
        preds.append(m.predict(test[use]))
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

p(f"\n  LOO R²={r2_loo:.4f}  MAE={mae_loo:.3f}lb  Positive: {positive}/{len(clean_locs)}")

sorted_locs = sorted(per_loc.items(), key=lambda x: x[1])
p("\n  Worst 5:")
for loc, r2 in sorted_locs[:5]:
    p(f"    {loc:50s} n={loc_counts[loc]:3d} R²={r2:8.3f}")
p("  Best 5:")
for loc, r2 in sorted_locs[-5:]:
    p(f"    {loc:50s} n={loc_counts[loc]:3d} R²={r2:8.3f}")

# Catastrophic check
p("\n  Catastrophic location check:")
for cat in ["Sabine River", "Lake St. Clair", "St. Johns River", "Huntley", "Istokpoga"]:
    for loc, r2 in per_loc.items():
        if cat.lower() in loc.lower():
            p(f"    {loc:50s} R²={r2:8.3f}")

# ═══════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════
p("\n" + "=" * 60)
p("v15 SELECTED FEATURES SUMMARY")
p("=" * 60)
p(f"  Temporal       : R²={r2_t:.4f}  (seen={r2_seen:.4f}, unseen={r2_unseen:.4f})")
p(f"  Spatial        : R²={r2_s:.4f}")
p(f"  Spatiotemporal : R²={r2_st:.4f}")
p(f"  LOO            : R²={r2_loo:.4f}  MAE={mae_loo:.3f}")
p()
p("Comparison:")
p("  v13 best     : T=0.425 S=0.486 ST=0.594 LOO=0.606")
p("  v15 all feats: T=0.413 S=0.481 ST=0.578 LOO=0.607")
p(f"  v15 selected : T={r2_t:.3f} S={r2_s:.3f} ST={r2_st:.3f} LOO={r2_loo:.3f}")
