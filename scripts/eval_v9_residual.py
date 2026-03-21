"""v9 LOO with residual prediction: predict deviation from cluster mean.

Instead of: model → predict target directly
Do: model → predict (target - cluster_mean), then add cluster_mean back

This bounds the maximum error since even a bad model will at least
return the cluster mean (i.e., what nearby similar lakes produce).
"""
import pandas as pd
import numpy as np
from sklearn.neighbors import NearestNeighbors
from sklearn.metrics import r2_score, mean_absolute_error
from sklearn.preprocessing import StandardScaler
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
print(f"Dataset: {len(df)} rows, {df.location.nunique()} locations")

all_cols = set(df.columns)
exclude = ALWAYS_EXCLUDE | LOCATION_IDENTITY | META_COLS | {TARGET}
full_features = sorted(all_cols - exclude)
full_features = [f for f in full_features if df[f].dtype in ["float64", "int64", "float32", "int32"]]
loo_features = [f for f in full_features if f not in LOCATION_IDENTITY]
print(f"LOO features: {len(loo_features)}")


def knn_encode_full(train_df, test_df=None, K=8):
    """Multi-scale KNN encoding returning cluster/region means."""
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
    ).reset_index()
    loc_stats["std_target"] = loc_stats["std_target"].fillna(0)

    morph = ["lat", "lon", "area_acres", "is_lake",
             "creel_lmb_ratio", "creel_smb_ratio", "creel_cpue_mean"]
    X = loc_stats[morph].fillna(loc_stats[morph].median()).values
    sc = StandardScaler().fit(X)
    Xs = sc.transform(X)
    nn = NearestNeighbors(n_neighbors=min(K+1, len(loc_stats)))
    nn.fit(Xs)

    loc_cluster = {}
    loc_region = {}
    loc_nn_std = {}  # uncertainty estimate
    for i, row in loc_stats.iterrows():
        q = Xs[i:i+1]
        d, ix = nn.kneighbors(q, n_neighbors=min(21, len(loc_stats)))
        all_nbrs = [(dd, j) for dd, j in zip(d[0], ix[0])
                    if loc_stats.iloc[j]["location"] != row["location"]]

        # Local cluster (K neighbors)
        nbrs = all_nbrs[:K]
        if nbrs:
            w = [loc_stats.iloc[j]["n_events"]/(dd+0.01) for dd, j in nbrs]
            loc_cluster[row["location"]] = sum(
                wi * loc_stats.iloc[j]["mean_target"]
                for wi, (_, j) in zip(w, nbrs)) / sum(w)
            targets = [loc_stats.iloc[j]["mean_target"] for _, j in nbrs]
            loc_nn_std[row["location"]] = np.std(targets)
        else:
            loc_cluster[row["location"]] = loc_stats["mean_target"].mean()
            loc_nn_std[row["location"]] = loc_stats["mean_target"].std()

        # Regional (20 neighbors)
        nbrs20 = all_nbrs[:20]
        if nbrs20:
            loc_region[row["location"]] = np.mean(
                [loc_stats.iloc[j]["mean_target"] for _, j in nbrs20])
        else:
            loc_region[row["location"]] = loc_stats["mean_target"].mean()

    test_encs = {}
    if test_df is not None:
        tv = test_df[morph].fillna(loc_stats[morph].median().to_dict()).iloc[0:1].values
        ts = sc.transform(tv)
        d, ix = nn.kneighbors(ts, n_neighbors=min(21, len(loc_stats)))
        all_nbrs = [(dd, int(j)) for dd, j in zip(d[0], ix[0])]

        nbrs = all_nbrs[:K]
        w = [loc_stats.iloc[j]["n_events"]/(dd+0.01) for dd, j in nbrs]
        test_encs["cluster"] = sum(
            wi * loc_stats.iloc[j]["mean_target"]
            for wi, (_, j) in zip(w, nbrs)) / sum(w)
        targets = [loc_stats.iloc[j]["mean_target"] for _, j in nbrs]
        test_encs["nn_std"] = np.std(targets)

        nbrs20 = all_nbrs[:20]
        test_encs["region"] = np.mean(
            [loc_stats.iloc[j]["mean_target"] for _, j in nbrs20])

    return loc_cluster, loc_region, loc_nn_std, test_encs


print("\n" + "=" * 50)
print("LOO: RESIDUAL PREDICTION")
print("=" * 50)
t0 = time.time()

loc_counts = df.location.value_counts()
loc_std = df.groupby("location")[TARGET].std()
clean_locs = [l for l in loc_counts[loc_counts >= MIN_LOO_N].index
              if loc_std.get(l, 0) >= MIN_LOO_STD]
print(f"Clean LOO set: {len(clean_locs)} locations")

all_true, all_pred = [], []
all_true_direct, all_pred_direct = [], []  # for comparison
per_loc_residual = {}
per_loc_direct = {}

for i, loc in enumerate(clean_locs):
    mask = df.location == loc
    train, test = df[~mask].copy(), df[mask].copy()

    # KNN encoding
    cmap, rmap, nn_std_map, test_encs = knn_encode_full(train, test)

    # Add KNN features to train
    train["cluster_enc"] = train.location.map(cmap).fillna(train[TARGET].mean())
    train["region_enc"] = train.location.map(rmap).fillna(train[TARGET].mean())
    train["nn_std"] = train.location.map(nn_std_map).fillna(0)

    # Add KNN features to test
    test["cluster_enc"] = test_encs["cluster"]
    test["region_enc"] = test_encs["region"]
    test["nn_std"] = test_encs["nn_std"]

    use = loo_features + ["cluster_enc", "region_enc", "nn_std"]

    # ── METHOD A: RESIDUAL ──
    # Train target = actual - cluster_mean
    train_cluster_means = train.location.map(cmap).fillna(train[TARGET].mean())
    train["_residual"] = train[TARGET] - train_cluster_means

    m_res = CatBoostRegressor(iterations=500, depth=6, learning_rate=0.05,
                               l2_leaf_reg=3, verbose=0, random_seed=42)
    m_res.fit(train[use], train["_residual"])
    pred_residual = m_res.predict(test[use])
    pred_a = test_encs["cluster"] + pred_residual
    pred_a = np.clip(pred_a, 0, 30)

    # ── METHOD B: DIRECT (baseline comparison) ──
    m_dir = CatBoostRegressor(iterations=500, depth=6, learning_rate=0.05,
                               l2_leaf_reg=3, verbose=0, random_seed=42)
    m_dir.fit(train[use], train[TARGET])
    pred_b = np.clip(m_dir.predict(test[use]), 0, 30)

    true_vals = test[TARGET].values
    r2_res = r2_score(true_vals, pred_a) if len(test) > 1 and np.std(true_vals) > 0.01 else 0.0
    r2_dir = r2_score(true_vals, pred_b) if len(test) > 1 and np.std(true_vals) > 0.01 else 0.0

    per_loc_residual[loc] = r2_res
    per_loc_direct[loc] = r2_dir

    all_true.extend(true_vals.tolist())
    all_pred.extend(pred_a.tolist())
    all_true_direct.extend(true_vals.tolist())
    all_pred_direct.extend(pred_b.tolist())

    if (i + 1) % 10 == 0:
        int_res = r2_score(all_true, all_pred)
        int_dir = r2_score(all_true_direct, all_pred_direct)
        print(f"  {i+1}/{len(clean_locs)} residual R²={int_res:.4f}  direct R²={int_dir:.4f}")

r2_res = r2_score(all_true, all_pred)
r2_dir = r2_score(all_true_direct, all_pred_direct)
mae_res = mean_absolute_error(all_true, all_pred)
mae_dir = mean_absolute_error(all_true_direct, all_pred_direct)
pos_res = sum(1 for r in per_loc_residual.values() if r > 0)
pos_dir = sum(1 for r in per_loc_direct.values() if r > 0)

print(f"\n{'Method':<20} {'R²':>8} {'MAE':>8} {'Positive':>10} {'Median':>8}")
print("-" * 56)
print(f"{'Residual':<20} {r2_res:8.4f} {mae_res:8.3f} {pos_res:>10}/{len(clean_locs)} {np.median(list(per_loc_residual.values())):8.4f}")
print(f"{'Direct':<20} {r2_dir:8.4f} {mae_dir:8.3f} {pos_dir:>10}/{len(clean_locs)} {np.median(list(per_loc_direct.values())):8.4f}")

print(f"\n  [{time.time()-t0:.1f}s]")

# Per-location comparison
print("\n  Biggest improvements (residual vs direct):")
improvements = [(loc, per_loc_residual[loc] - per_loc_direct[loc], per_loc_residual[loc], per_loc_direct[loc])
                for loc in clean_locs]
improvements.sort(key=lambda x: x[1], reverse=True)
for loc, delta, r_res, r_dir in improvements[:5]:
    n = loc_counts[loc]
    print(f"    {loc:50s} n={n:3d}  Δ={delta:+.3f} (res={r_res:.3f} dir={r_dir:.3f})")

print("\n  Biggest regressions:")
for loc, delta, r_res, r_dir in improvements[-5:]:
    n = loc_counts[loc]
    print(f"    {loc:50s} n={n:3d}  Δ={delta:+.3f} (res={r_res:.3f} dir={r_dir:.3f})")

# Best/worst residual
sorted_res = sorted(per_loc_residual.items(), key=lambda x: x[1])
print("\n  Worst 5 (residual):")
for loc, r2 in sorted_res[:5]:
    n = loc_counts[loc]
    mn = df[df.location == loc][TARGET].mean()
    print(f"    {loc:50s} n={n:3d} R²={r2:8.3f} mean={mn:.1f}")
print("  Best 5 (residual):")
for loc, r2 in sorted_res[-5:]:
    n = loc_counts[loc]
    mn = df[df.location == loc][TARGET].mean()
    print(f"    {loc:50s} n={n:3d} R²={r2:8.3f} mean={mn:.1f}")
