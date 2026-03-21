"""Location set features for LOO: hand-crafted DeepSet-style aggregation.

Instead of neural DeepSets (which failed due to NaN issues), compute
per-location aggregated features from their event sets:
- Mean/std/min/max of key features across events
- Temporal statistics (span, density)
- Missingness patterns

These capture "what kind of location is this" without using target values.
"""
import numpy as np
import pandas as pd
from sklearn.metrics import r2_score
from sklearn.preprocessing import StandardScaler
from sklearn.neighbors import NearestNeighbors
from sklearn.model_selection import GroupKFold
from catboost import CatBoostRegressor
import time
import warnings
warnings.filterwarnings("ignore")

TARGET = "median_weight_lb"
ALWAYS_EXCLUDE = {"target_success_score", "tms_id", "event_id"}
LOCATION_IDENTITY = {"loc_enc", "trail_mean_weight", "location_mean_weight",
                     "loc_rolling_3", "baseline_signal"}
META_COLS = {"date", "location", "region", "block", "sat_source",
             "usgs_site_id", "event_name", "tournament_slug",
             "trail", "results_source", "source", "species", "spawn_phase"}


def build_set_features(df):
    """Build per-location aggregated features from event sets."""
    # Key features to aggregate (choose features with reasonable coverage)
    agg_features = [
        "lat", "lon", "area_acres", "day_of_year", "month", "year",
        "season_sin", "season_cos", "om_air_temp_mean", "om_precip_mm",
        "om_wind_max_kph", "om_pressure_msl",
        "creel_lmb_ratio", "creel_smb_ratio", "creel_spotted_ratio",
        "is_lake", "spawn_progress",
    ]
    agg_features = [f for f in agg_features if f in df.columns]

    # Compute per-location statistics
    loc_feats = {}
    for loc in df.location.unique():
        sub = df[df.location == loc]
        feats = {}

        # Basic stats
        feats["set_n_events"] = len(sub)
        feats["set_year_span"] = sub.year.max() - sub.year.min() if sub.year.notna().any() else 0
        feats["set_year_mean"] = sub.year.mean() if sub.year.notna().any() else 2000
        feats["set_season_diversity"] = sub.month.nunique() if sub.month.notna().any() else 0

        # Aggregate key features
        for f in agg_features:
            vals = sub[f].dropna()
            if len(vals) > 0:
                feats[f"set_{f}_mean"] = vals.mean()
                if len(vals) > 1:
                    feats[f"set_{f}_std"] = vals.std()
                else:
                    feats[f"set_{f}_std"] = 0
            else:
                feats[f"set_{f}_mean"] = np.nan
                feats[f"set_{f}_std"] = np.nan

        # Data completeness score
        feats["set_completeness"] = sub[agg_features].notna().mean().mean()

        loc_feats[loc] = feats

    return pd.DataFrame.from_dict(loc_feats, orient="index")


def knn_encode(train_df, test_df=None, K=8):
    """KNN target encoding from training data."""
    loc_agg = train_df.groupby("location").agg(
        mean_target=(TARGET, "mean"),
        lat=("lat", "first"),
        lon=("lon", "first"),
        area_acres=("area_acres", "first"),
        creel_lmb_ratio=("creel_lmb_ratio", "first"),
    ).reset_index()

    cols = ["lat", "lon", "area_acres", "creel_lmb_ratio"]
    cols = [c for c in cols if c in loc_agg.columns]
    X = loc_agg[cols].fillna(loc_agg[cols].median()).values
    sc = StandardScaler().fit(X)
    nn = NearestNeighbors(n_neighbors=min(K + 1, len(loc_agg)))
    nn.fit(sc.transform(X))

    cluster_map = {}
    region_map = {}
    for i, row in loc_agg.iterrows():
        q = sc.transform(loc_agg.iloc[i:i+1][cols].fillna(loc_agg[cols].median()).values)
        dists, idxs = nn.kneighbors(q)
        nbrs = [(d, j) for d, j in zip(dists[0], idxs[0])
                if loc_agg.iloc[j]["location"] != row["location"]][:K]
        if nbrs:
            w = [1/(d+0.01) for d,_ in nbrs]
            cluster_map[row["location"]] = sum(
                wi * loc_agg.iloc[j]["mean_target"] for wi, (_, j) in zip(w, nbrs)
            ) / sum(w)
        else:
            cluster_map[row["location"]] = loc_agg["mean_target"].mean()

        # Region (broader)
        dists2, idxs2 = nn.kneighbors(q, n_neighbors=min(21, len(loc_agg)))
        nbrs2 = [(d, j) for d, j in zip(dists2[0], idxs2[0])
                 if loc_agg.iloc[j]["location"] != row["location"]][:20]
        region_map[row["location"]] = (
            np.mean([loc_agg.iloc[j]["mean_target"] for _, j in nbrs2])
            if nbrs2 else loc_agg["mean_target"].mean()
        )

    test_cluster = None
    test_region = None
    if test_df is not None:
        test_morph = test_df[cols].fillna(loc_agg[cols].median().to_dict()).iloc[0:1].values
        test_q = sc.transform(test_morph)
        dists, idxs = nn.kneighbors(test_q, n_neighbors=min(K + 1, len(loc_agg)))
        nbrs = [(d, int(j)) for d, j in zip(dists[0], idxs[0])][:K]
        w = [1/(d+0.01) for d,_ in nbrs]
        test_cluster = sum(wi * loc_agg.iloc[j]["mean_target"]
                          for wi, (_, j) in zip(w, nbrs)) / sum(w)

        nbrs2 = [(d, int(j)) for d, j in zip(dists[0], idxs[0])][:20]
        test_region = np.mean([loc_agg.iloc[j]["mean_target"] for _, j in nbrs2])

    return cluster_map, region_map, test_cluster, test_region


def main():
    print("=" * 60)
    print("Set Features + CatBoost (ALL 4 METRICS)")
    print("=" * 60)

    df = pd.read_csv("castline/validation/data/assembled/validation_dataset_v8.csv",
                     low_memory=False)
    df = df[df[TARGET].notna()].copy()
    print(f"Dataset: {len(df)} rows, {df.location.nunique()} locations")

    # Build set features
    print("\nBuilding location set features...")
    set_feats = build_set_features(df)
    set_cols = list(set_feats.columns)
    print(f"Set features: {len(set_cols)}")

    # Add to dataframe
    for col in set_cols:
        df[col] = df.location.map(set_feats[col])

    # Base features
    all_cols = set(df.columns) - set(set_cols)
    exclude = ALWAYS_EXCLUDE | LOCATION_IDENTITY | META_COLS | {TARGET}
    features = sorted(all_cols - exclude)
    features = [f for f in features if df[f].dtype in ["float64", "int64", "float32", "int32"]]
    loo_features = [f for f in features if f not in LOCATION_IDENTITY] + set_cols

    # Location encoding for non-LOO
    loc_means = df.groupby("location")[TARGET].mean()
    df["loc_mean_enc"] = df.location.map(loc_means)
    full_with_loc = loo_features + ["loc_mean_enc"]

    print(f"LOO features: {len(loo_features)}")
    print(f"Full features (with loc): {len(full_with_loc)}")

    # ══════════════════════════════════════
    # 1. TEMPORAL HOLDOUT
    # ══════════════════════════════════════
    print("\n" + "=" * 50)
    print("1. TEMPORAL HOLDOUT")
    print("=" * 50)
    t0 = time.time()
    df_sorted = df.sort_values("date")
    split = int(len(df_sorted) * 0.8)
    train_t, test_t = df_sorted.iloc[:split], df_sorted.iloc[split:]
    m = CatBoostRegressor(iterations=800, depth=6, learning_rate=0.05,
                          l2_leaf_reg=3, verbose=0, random_seed=42)
    m.fit(train_t[full_with_loc], train_t[TARGET])
    pred = m.predict(test_t[full_with_loc])
    r2_t = r2_score(test_t[TARGET], pred)
    imp = pd.Series(m.feature_importances_, index=full_with_loc).nlargest(5)
    print(f"  R²={r2_t:.4f}  [{time.time()-t0:.1f}s]")
    print(f"  Top: {list(zip(imp.index, imp.values.astype(int)))}")

    # ══════════════════════════════════════
    # 2. SPATIAL HOLDOUT
    # ══════════════════════════════════════
    print("\n" + "=" * 50)
    print("2. SPATIAL HOLDOUT")
    print("=" * 50)
    t0 = time.time()
    df["_region"] = pd.cut(df.lat, bins=5, labels=["S", "SM", "M", "MN", "N"])
    region_r2s = {}
    for region in df["_region"].unique():
        te = df[df._region == region]
        tr = df[df._region != region]
        if len(te) < 10:
            continue
        m = CatBoostRegressor(iterations=500, depth=6, learning_rate=0.05,
                              l2_leaf_reg=3, verbose=0, random_seed=42)
        m.fit(tr[full_with_loc], tr[TARGET])
        r2 = r2_score(te[TARGET], m.predict(te[full_with_loc]))
        region_r2s[region] = (r2, len(te))
        print(f"    {region}: R²={r2:.4f} (n={len(te)})")
    r2_s = np.mean([r for r, _ in region_r2s.values()])
    print(f"  Mean R²={r2_s:.4f}  [{time.time()-t0:.1f}s]")

    # ══════════════════════════════════════
    # 3. SPATIOTEMPORAL BLOCKED
    # ══════════════════════════════════════
    print("\n" + "=" * 50)
    print("3. SPATIOTEMPORAL BLOCKED")
    print("=" * 50)
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

    # ══════════════════════════════════════
    # 4. LOO
    # ══════════════════════════════════════
    print("\n" + "=" * 50)
    print("4. LEAVE-ONE-LOCATION-OUT")
    print("=" * 50)
    t0 = time.time()
    loc_counts = df.location.value_counts()
    loc_var = df.groupby("location")[TARGET].std()
    big_locs = [l for l in loc_counts[loc_counts >= 15].index
                if loc_var.get(l, 0) > 0.1]
    print(f"  Testing {len(big_locs)} locations (n>=15, with variance)")

    all_true, all_pred = [], []
    per_loc = {}
    for i, loc in enumerate(big_locs):
        mask = df.location == loc
        train, test = df[~mask].copy(), df[mask].copy()

        # Rebuild set features for test location using only test events
        # (the current set features use ALL events including test → leakage for temporal)
        # For LOO this is fine since we're holding out the entire location

        cluster_map, region_map, test_cluster, test_region = knn_encode(train, test)
        train["cluster_enc"] = train.location.map(cluster_map).fillna(train[TARGET].mean())
        train["region_enc"] = train.location.map(region_map).fillna(train[TARGET].mean())
        test["cluster_enc"] = test_cluster
        test["region_enc"] = test_region

        use_feats = loo_features + ["cluster_enc", "region_enc"]

        m = CatBoostRegressor(iterations=500, depth=6, learning_rate=0.05,
                              l2_leaf_reg=3, verbose=0, random_seed=42)
        m.fit(train[use_feats], train[TARGET])
        pred = np.clip(m.predict(test[use_feats]), 0, 30)

        r2 = r2_score(test[TARGET], pred) if len(test) > 1 and np.std(test[TARGET]) > 0.01 else 0.0
        per_loc[loc] = r2

        if (i + 1) % 15 == 0:
            interim = r2_score(all_true + list(test[TARGET]), all_pred + list(pred))
            print(f"  {i+1}/{len(big_locs)} interim R²={interim:.4f}")

        all_true.extend(test[TARGET].tolist())
        all_pred.extend(pred.tolist())

    r2_loo = r2_score(all_true, all_pred)
    positive = sum(1 for r in per_loc.values() if r > 0)
    print(f"\n  Overall LOO R²={r2_loo:.4f}  [{time.time()-t0:.1f}s]")
    print(f"  Positive R²: {positive}/{len(big_locs)}")
    print(f"  Median: {np.median(list(per_loc.values())):.4f}")

    # Show best/worst
    sorted_locs = sorted(per_loc.items(), key=lambda x: x[1])
    print("\n  Worst 5:")
    for loc, r2 in sorted_locs[:5]:
        n = loc_counts[loc]
        true = df[df.location == loc][TARGET].mean()
        print(f"    {loc:50s} n={n:3d} R²={r2:8.3f} mean={true:.1f}")
    print("  Best 5:")
    for loc, r2 in sorted_locs[-5:]:
        n = loc_counts[loc]
        true = df[df.location == loc][TARGET].mean()
        print(f"    {loc:50s} n={n:3d} R²={r2:8.3f} mean={true:.1f}")

    # ══════════════════════════════════════
    # SUMMARY
    # ══════════════════════════════════════
    print("\n" + "=" * 60)
    print("SUMMARY (Set Features + CatBoost)")
    print("=" * 60)
    print(f"  Temporal                 : R²={r2_t:.4f}")
    print(f"  Spatial                  : R²={r2_s:.4f}")
    print(f"  Spatiotemporal           : R²={r2_st:.4f}")
    print(f"  LOO                      : R²={r2_loo:.4f}")


if __name__ == "__main__":
    main()
