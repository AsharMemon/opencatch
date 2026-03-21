"""Improved LOO evaluation with better KNN target encoding.

Key improvements:
1. Separate KNN for tournament vs creel neighbors
2. Use more morphometric features for matching
3. Source-weighted averaging
"""
import pandas as pd
import numpy as np
from sklearn.neighbors import NearestNeighbors
from sklearn.metrics import r2_score
from sklearn.preprocessing import StandardScaler
from catboost import CatBoostRegressor
import warnings
warnings.filterwarnings("ignore")

TARGET = "median_weight_lb"
ALWAYS_EXCLUDE = {"target_success_score", "tms_id", "event_id"}
LOCATION_IDENTITY = {"loc_enc", "trail_mean_weight", "location_mean_weight",
                     "loc_rolling_3", "baseline_signal"}
META_COLS = {"date", "location", "region", "block", "sat_source",
             "usgs_site_id", "event_name", "tournament_slug",
             "trail", "results_source", "source", "species", "spawn_phase"}

df = pd.read_csv("castline/validation/data/assembled/validation_dataset_v7_enriched.csv",
                 low_memory=False)
df = df[df[TARGET].notna()].copy()

all_cols = set(df.columns)
exclude = ALWAYS_EXCLUDE | LOCATION_IDENTITY | META_COLS | {TARGET}
features = sorted(all_cols - exclude)
features = [f for f in features if df[f].dtype in ["float64", "int64", "float32", "int32"]]
loo_features = [f for f in features if f not in LOCATION_IDENTITY]

print(f"Dataset: {len(df)} rows, {df.location.nunique()} locations")
print(f"LOO features: {len(loo_features)}")


def improved_knn_encode(train_df, test_loc_df, K=8):
    """Build KNN target encoding with tournament-aware weighting."""
    loc_agg = train_df.groupby("location").agg(
        mean_target=(TARGET, "mean"),
        lat=("lat", "first"),
        lon=("lon", "first"),
        area_acres=("area_acres", "first"),
        max_depth_ft=("max_depth_ft", "first"),
        creel_lmb_ratio=("creel_lmb_ratio", "first"),
        creel_smb_ratio=("creel_smb_ratio", "first"),
        is_lake=("is_lake", "first"),
        source=("source", "first"),
        n_events=(TARGET, "count"),
    ).reset_index()

    morph_cols = ["lat", "lon", "area_acres", "max_depth_ft",
                  "creel_lmb_ratio", "creel_smb_ratio", "is_lake"]
    X = loc_agg[morph_cols].fillna(loc_agg[morph_cols].median()).values
    scaler = StandardScaler().fit(X)
    X_scaled = scaler.transform(X)

    nn = NearestNeighbors(n_neighbors=min(K * 3, len(loc_agg)))
    nn.fit(X_scaled)

    # Encode training locations
    cluster_map = {}
    region_map = {}
    for i, row in loc_agg.iterrows():
        q = X_scaled[i:i + 1]
        dists, idxs = nn.kneighbors(q)
        nbrs = [
            (d, j) for d, j in zip(dists[0], idxs[0])
            if loc_agg.iloc[j]["location"] != row["location"]
        ]

        # Weight: 1/distance * source_weight * n_events_weight
        weighted_sum = 0
        weight_total = 0
        for d, j in nbrs[:K]:
            source_w = 3.0 if loc_agg.iloc[j]["source"] in ["bassmaster", "tourneyx"] else 1.0
            n_w = min(loc_agg.iloc[j]["n_events"], 10) / 3.0  # More events = more reliable
            w = source_w * n_w / (d + 0.01)
            weighted_sum += w * loc_agg.iloc[j]["mean_target"]
            weight_total += w
        cluster_map[row["location"]] = weighted_sum / weight_total if weight_total > 0 else loc_agg["mean_target"].mean()

        # Regional: broader, tournament-weighted
        weighted_sum_r = 0
        weight_total_r = 0
        for d, j in nbrs[:30]:
            source_w = 3.0 if loc_agg.iloc[j]["source"] in ["bassmaster", "tourneyx"] else 1.0
            w = source_w / (d + 0.1)
            weighted_sum_r += w * loc_agg.iloc[j]["mean_target"]
            weight_total_r += w
        region_map[row["location"]] = weighted_sum_r / weight_total_r if weight_total_r > 0 else loc_agg["mean_target"].mean()

    # Encode test location
    test_morph = (
        test_loc_df[morph_cols].fillna(loc_agg[morph_cols].median().to_dict()).iloc[0:1].values
    )
    test_scaled = scaler.transform(test_morph)
    dists, idxs = nn.kneighbors(test_scaled, n_neighbors=min(K * 3, len(loc_agg)))
    nbrs = [(d, int(j)) for d, j in zip(dists[0], idxs[0])]

    # Tournament-weighted cluster for test
    weighted_sum = 0
    weight_total = 0
    for d, j in nbrs[:K]:
        source_w = 3.0 if loc_agg.iloc[j]["source"] in ["bassmaster", "tourneyx"] else 1.0
        n_w = min(loc_agg.iloc[j]["n_events"], 10) / 3.0
        w = source_w * n_w / (d + 0.01)
        weighted_sum += w * loc_agg.iloc[j]["mean_target"]
        weight_total += w
    test_cluster = weighted_sum / weight_total if weight_total > 0 else loc_agg["mean_target"].mean()

    weighted_sum_r = 0
    weight_total_r = 0
    for d, j in nbrs[:30]:
        source_w = 3.0 if loc_agg.iloc[j]["source"] in ["bassmaster", "tourneyx"] else 1.0
        w = source_w / (d + 0.1)
        weighted_sum_r += w * loc_agg.iloc[j]["mean_target"]
        weight_total_r += w
    test_region = weighted_sum_r / weight_total_r if weight_total_r > 0 else loc_agg["mean_target"].mean()

    # Also compute tournament-only cluster (K neighbors from tournament data only)
    tourn_locs = loc_agg[loc_agg.source.isin(["bassmaster", "tourneyx"])].copy()
    if len(tourn_locs) > 5:
        X_tourn = tourn_locs[morph_cols].fillna(loc_agg[morph_cols].median()).values
        scaler_t = StandardScaler().fit(X_tourn)
        nn_t = NearestNeighbors(n_neighbors=min(K, len(tourn_locs)))
        nn_t.fit(scaler_t.transform(X_tourn))

        test_scaled_t = scaler_t.transform(test_morph)
        dists_t, idxs_t = nn_t.kneighbors(test_scaled_t)
        w_t = [1 / (d + 0.01) for d in dists_t[0]]
        total_t = sum(w_t)
        tourn_cluster = sum(wi * tourn_locs.iloc[j]["mean_target"]
                           for wi, j in zip(w_t, idxs_t[0])) / total_t

        # Also for training
        for i, row in loc_agg.iterrows():
            q_t = scaler_t.transform(
                loc_agg.iloc[i:i+1][morph_cols].fillna(loc_agg[morph_cols].median()).values
            )
            # Find tournament neighbors excluding self
            k_search = min(K + 1, len(tourn_locs))
            dists_tr, idxs_tr = nn_t.kneighbors(q_t, n_neighbors=k_search)
            nbrs_tr = [
                (d, j) for d, j in zip(dists_tr[0], idxs_tr[0])
                if tourn_locs.iloc[j]["location"] != row["location"]
            ][:K]
            if nbrs_tr:
                w_tr = [1 / (d + 0.01) for d, _ in nbrs_tr]
                total_tr = sum(w_tr)
                cluster_map[row["location"] + "_tourn"] = sum(
                    wi * tourn_locs.iloc[j]["mean_target"]
                    for wi, (_, j) in zip(w_tr, nbrs_tr)
                ) / total_tr
            else:
                cluster_map[row["location"] + "_tourn"] = tourn_locs["mean_target"].mean()
    else:
        tourn_cluster = test_cluster
        for _, row in loc_agg.iterrows():
            cluster_map[row["location"] + "_tourn"] = cluster_map.get(row["location"], loc_agg["mean_target"].mean())

    return cluster_map, region_map, test_cluster, test_region, tourn_cluster


# Test locations
loc_stats = df.groupby("location").agg(
    n=(TARGET, "count"),
    source=("source", "first"),
).reset_index()

test_locs = loc_stats[
    (loc_stats.n >= 15)
    & (loc_stats.source.isin(["bassmaster", "tourneyx"]))
    & (~loc_stats.location.str.startswith("Statewide"))
].location.tolist()

print(f"\nTesting {len(test_locs)} tournament locations\n")

all_true, all_pred = [], []
for loc in test_locs:
    mask = df.location == loc
    train = df[~mask].copy()
    test = df[mask].copy()

    cluster_map, region_map, test_cluster, test_region, tourn_cluster = (
        improved_knn_encode(train, test)
    )

    train["cluster_enc"] = train.location.map(cluster_map).fillna(train[TARGET].mean())
    train["region_enc"] = train.location.map(region_map).fillna(train[TARGET].mean())
    train["tourn_cluster"] = train.location.map(
        {k.replace("_tourn", ""): v for k, v in cluster_map.items() if k.endswith("_tourn")}
    ).fillna(train[TARGET].mean())

    test["cluster_enc"] = test_cluster
    test["region_enc"] = test_region
    test["tourn_cluster"] = tourn_cluster

    use_feats = loo_features + ["cluster_enc", "region_enc", "tourn_cluster"]

    model = CatBoostRegressor(
        iterations=500, depth=6, learning_rate=0.05,
        l2_leaf_reg=3, verbose=0, random_seed=42,
    )
    model.fit(train[use_feats], train[TARGET])
    pred = np.clip(model.predict(test[use_feats]), 0, 30)

    r2 = r2_score(test[TARGET], pred) if len(test) > 1 else 0
    tag = "OK " if r2 > 0.3 else ("~  " if r2 > -0.1 else "BAD")
    print(
        f"[{tag}] {loc:50s} n={len(test):3d} R2={r2:7.3f} "
        f"true={test[TARGET].mean():.1f} pred={np.mean(pred):.1f} "
        f"cluster={test_cluster:.1f} tourn={tourn_cluster:.1f}"
    )

    all_true.extend(test[TARGET].tolist())
    all_pred.extend(pred.tolist())

r2 = r2_score(all_true, all_pred)
print(f"\nOverall tournament LOO R2: {r2:.4f}")
