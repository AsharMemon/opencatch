"""Quick LOO eval with enriched morphometry data."""
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
print(f"Dataset: {len(df)} rows, {df.location.nunique()} locations")
print(f"area_acres coverage: {df.area_acres.notna().mean()*100:.1f}%")

# Features
all_cols = set(df.columns)
exclude = ALWAYS_EXCLUDE | LOCATION_IDENTITY | META_COLS | {TARGET}
features = sorted(all_cols - exclude)
features = [f for f in features if df[f].dtype in ["float64", "int64", "float32", "int32"]]
print(f"Features: {len(features)}")

loo_features = [f for f in features if f not in LOCATION_IDENTITY]

# LOO evaluation
loc_counts = df.location.value_counts()
big_locs = loc_counts[loc_counts >= 15].index.tolist()
print(f"\n=== LOO EVALUATION ({len(big_locs)} locations with n>=15) ===")

all_true, all_pred = [], []
per_loc_r2 = {}

for loc in big_locs:
    mask = df.location == loc
    train = df[~mask].copy()
    test = df[mask].copy()

    # Build KNN target encoding from training set only
    loc_stats = train.groupby("location").agg(
        mean_target=(TARGET, "mean"),
        lat=("lat", "first"),
        lon=("lon", "first"),
        area_acres=("area_acres", "first"),
        creel_lmb_ratio=("creel_lmb_ratio", "first"),
        creel_smb_ratio=("creel_smb_ratio", "first"),
    ).reset_index()

    morph_cols = ["lat", "lon", "area_acres", "creel_lmb_ratio", "creel_smb_ratio"]
    X_morph = loc_stats[morph_cols].fillna(loc_stats[morph_cols].median()).values
    scaler = StandardScaler().fit(X_morph)
    X_scaled = scaler.transform(X_morph)

    K = 8
    nn = NearestNeighbors(n_neighbors=min(K + 1, len(loc_stats)), metric="euclidean")
    nn.fit(X_scaled)

    # Encode all training locations
    cluster_map = {}
    region_map = {}
    for i, row in loc_stats.iterrows():
        q = X_scaled[i:i + 1]
        dists, idxs = nn.kneighbors(q)
        neighbors = [(d, j) for d, j in zip(dists[0], idxs[0])
                     if loc_stats.iloc[j]["location"] != row["location"]][:K]
        if neighbors:
            weights = [1 / (d + 0.01) for d, _ in neighbors]
            total = sum(weights)
            cluster_map[row["location"]] = sum(
                w * loc_stats.iloc[j]["mean_target"]
                for w, (_, j) in zip(weights, neighbors)
            ) / total
        else:
            cluster_map[row["location"]] = loc_stats["mean_target"].mean()

        # Region (broader K=20)
        dists2, idxs2 = nn.kneighbors(q, n_neighbors=min(21, len(loc_stats)))
        neighbors2 = [(d, j) for d, j in zip(dists2[0], idxs2[0])
                      if loc_stats.iloc[j]["location"] != row["location"]][:20]
        if neighbors2:
            region_map[row["location"]] = np.mean(
                [loc_stats.iloc[j]["mean_target"] for _, j in neighbors2]
            )
        else:
            region_map[row["location"]] = loc_stats["mean_target"].mean()

    # Encode held-out location
    test_morph_vals = test[morph_cols].fillna(
        loc_stats[morph_cols].median().to_dict()
    ).iloc[0:1].values
    test_scaled = scaler.transform(test_morph_vals)
    dists, idxs = nn.kneighbors(test_scaled, n_neighbors=min(K + 1, len(loc_stats)))

    neighbors = [(d, int(j)) for d, j in zip(dists[0], idxs[0])][:K]
    weights = [1 / (d + 0.01) for d, _ in neighbors]
    total = sum(weights)
    test_cluster = sum(
        w * loc_stats.iloc[j]["mean_target"] for w, (_, j) in zip(weights, neighbors)
    ) / total
    test_region = np.mean(
        [loc_stats.iloc[j]["mean_target"] for _, j in neighbors[:20]]
    )

    # Add encodings
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
    pred = model.predict(test[use_feats])

    true_mean = test[TARGET].mean()
    pred_mean = np.mean(pred)
    n = len(test)
    r2 = r2_score(test[TARGET], pred) if n > 1 else 0.0
    per_loc_r2[loc] = r2

    tag = "OK " if r2 > 0.3 else ("~  " if r2 > -0.1 else "BAD")
    print(f"  [{tag}] {loc:50s} n={n:3d} R2={r2:7.3f} true={true_mean:.1f} "
          f"pred={pred_mean:.1f} cluster={test_cluster:.1f}")

    all_true.extend(test[TARGET].tolist())
    all_pred.extend(pred.tolist())

overall_r2 = r2_score(all_true, all_pred)
positive = sum(1 for r in per_loc_r2.values() if r > 0)
print(f"\nOverall LOO R2: {overall_r2:.4f}")
print(f"Positive R2: {positive}/{len(big_locs)}")
print(f"Median R2: {np.median(list(per_loc_r2.values())):.4f}")
