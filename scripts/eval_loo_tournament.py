"""LOO eval on real tournament locations only (fair comparison to v6)."""
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

# Only tournament locations with n>=15, exclude Statewide
loc_stats = df.groupby("location").agg(
    n=(TARGET, "count"),
    std=(TARGET, "std"),
    source=("source", "first"),
).reset_index()

test_locs = loc_stats[
    (loc_stats.n >= 15)
    & (loc_stats.source.isin(["bassmaster", "tourneyx"]))
    & (~loc_stats.location.str.startswith("Statewide"))
].location.tolist()

print(f"Testing {len(test_locs)} real tournament locations\n")

all_true, all_pred = [], []
for loc in test_locs:
    mask = df.location == loc
    train = df[~mask].copy()
    test = df[mask].copy()

    loc_agg = train.groupby("location").agg(
        mean_target=(TARGET, "mean"),
        lat=("lat", "first"),
        lon=("lon", "first"),
        area_acres=("area_acres", "first"),
        creel_lmb_ratio=("creel_lmb_ratio", "first"),
        creel_smb_ratio=("creel_smb_ratio", "first"),
    ).reset_index()

    morph_cols = ["lat", "lon", "area_acres", "creel_lmb_ratio", "creel_smb_ratio"]
    X_morph = loc_agg[morph_cols].fillna(loc_agg[morph_cols].median()).values
    scaler = StandardScaler().fit(X_morph)
    X_scaled = scaler.transform(X_morph)
    K = 8
    nn = NearestNeighbors(n_neighbors=min(K + 1, len(loc_agg)))
    nn.fit(X_scaled)

    cluster_map = {}
    region_map = {}
    for i, row in loc_agg.iterrows():
        q = X_scaled[i : i + 1]
        dists, idxs = nn.kneighbors(q)
        nbrs = [
            (d, j) for d, j in zip(dists[0], idxs[0])
            if loc_agg.iloc[j]["location"] != row["location"]
        ][:K]
        if nbrs:
            w = [1 / (d + 0.01) for d, _ in nbrs]
            t = sum(w)
            cluster_map[row["location"]] = (
                sum(wi * loc_agg.iloc[j]["mean_target"] for wi, (_, j) in zip(w, nbrs)) / t
            )
        else:
            cluster_map[row["location"]] = loc_agg["mean_target"].mean()

        dists2, idxs2 = nn.kneighbors(q, n_neighbors=min(21, len(loc_agg)))
        nbrs2 = [
            (d, j) for d, j in zip(dists2[0], idxs2[0])
            if loc_agg.iloc[j]["location"] != row["location"]
        ][:20]
        region_map[row["location"]] = (
            np.mean([loc_agg.iloc[j]["mean_target"] for _, j in nbrs2])
            if nbrs2
            else loc_agg["mean_target"].mean()
        )

    # Encode test location
    test_morph = (
        test[morph_cols].fillna(loc_agg[morph_cols].median().to_dict()).iloc[0:1].values
    )
    test_scaled = scaler.transform(test_morph)
    dists, idxs = nn.kneighbors(test_scaled, n_neighbors=min(K + 1, len(loc_agg)))
    nbrs = [(d, int(j)) for d, j in zip(dists[0], idxs[0])][:K]
    w = [1 / (d + 0.01) for d, _ in nbrs]
    t = sum(w)
    test_cluster = (
        sum(wi * loc_agg.iloc[j]["mean_target"] for wi, (_, j) in zip(w, nbrs)) / t
    )
    test_region = np.mean([loc_agg.iloc[j]["mean_target"] for _, j in nbrs[:20]])

    train["cluster_enc"] = train.location.map(cluster_map).fillna(train[TARGET].mean())
    train["region_enc"] = train.location.map(region_map).fillna(train[TARGET].mean())
    test["cluster_enc"] = test_cluster
    test["region_enc"] = test_region

    use_feats = loo_features + ["cluster_enc", "region_enc"]
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
        f"true={test[TARGET].mean():.1f} pred={np.mean(pred):.1f} cluster={test_cluster:.1f}"
    )

    all_true.extend(test[TARGET].tolist())
    all_pred.extend(pred.tolist())

r2 = r2_score(all_true, all_pred)
print(f"\nOverall tournament LOO R2: {r2:.4f}")
print(f"(Fair comparison to v6 LOO which was 0.42)")
