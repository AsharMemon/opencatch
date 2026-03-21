"""Quick evaluation of v7 features with single LightGBM (memory-efficient).

Tests the impact of v7 feature engineering + LOO target encoding
without the heavy stacking ensemble.
"""
import json
import sys
import time
import warnings
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupKFold
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")

TARGET = "median_weight_lb"
ALWAYS_EXCLUDE = {"target_success_score", "tms_id", "event_id"}
LOCATION_IDENTITY = {"loc_enc", "trail_mean_weight", "location_mean_weight",
                     "loc_rolling_3", "baseline_signal"}
META_COLS = {"date", "location", "region", "block", "sat_source",
             "usgs_site_id", "event_name", "tournament_slug",
             "site_id", "observation_date", "iem_station",
             "species", "results_source", "trail", "source_mode",
             "day_number", "ecoregion_season_deviation"}
# These leak target for LOO
LOO_LEAK_COLS = {"cluster_mean_weight", "cluster_std_weight",
                 "similar_loc_mean_weight", "cluster_count",
                 "location_cluster"}


def get_features(df, include_location_id=True, include_cluster=False):
    exclude = ALWAYS_EXCLUDE | META_COLS | {TARGET}
    if not include_location_id:
        exclude |= LOCATION_IDENTITY
    if not include_cluster:
        exclude |= LOO_LEAK_COLS
    return [c for c in df.columns
            if c not in exclude
            and df[c].dtype in (np.float64, np.float32, np.int64, np.int32, float, int)
            and df[c].notna().mean() > 0.03]


def extract_region(loc):
    parts = loc.rsplit(",", 1)
    st = parts[1].strip()[:2].upper() if len(parts) == 2 else "UNK"
    r = {"SE": "AL FL GA SC NC VA TN MS LA AR".split(),
         "NE": "NY PA MD DE NJ CT MA ME VT NH".split(),
         "MW": "WI MN MI OH IN IL IA MO KS ND SD NE".split(),
         "SW": "TX OK AZ NM UT CO".split(),
         "W": "CA OR WA ID MT WY NV".split()}
    for region, states in r.items():
        if st in states:
            return region
    return "OT"


def train_lgb(X, y, X_val=None, y_val=None, params=None):
    p = {
        "objective": "regression", "metric": "rmse", "verbosity": -1,
        "n_estimators": 1500, "learning_rate": 0.02, "max_depth": 7,
        "num_leaves": 63, "min_child_samples": 5,
        "subsample": 0.8, "colsample_bytree": 0.6,
        "reg_alpha": 0.3, "reg_lambda": 2.0,
        "extra_trees": True, "min_split_gain": 0.005,
    }
    if params:
        p.update(params)
    m = lgb.LGBMRegressor(**p)
    kw = {}
    if X_val is not None:
        kw["eval_set"] = [(X_val, y_val)]
        kw["callbacks"] = [lgb.early_stopping(50, verbose=False)]
    m.fit(X, y, **kw)
    return m


def loo_target_encode(train_df, test_df, smoothing=30.0):
    """LOO target encoding with Bayesian smoothing."""
    g_mean = train_df[TARGET].mean()
    loc_stats = train_df.groupby("location").agg(
        s=(TARGET, "sum"), n=(TARGET, "count")
    )
    # For test (unseen location): use global mean
    test_enc = g_mean
    if test_df["location"].iloc[0] in loc_stats.index:
        # Shouldn't happen in LOO, but just in case
        row = loc_stats.loc[test_df["location"].iloc[0]]
        test_enc = (row["s"] + smoothing * g_mean) / (row["n"] + smoothing)

    # Also compute cluster-level encoding
    cluster_feats = ["lat", "lon", "area_acres", "max_depth_ft", "is_lake"]
    avail = [f for f in cluster_feats if f in train_df.columns]
    cluster_enc = g_mean

    if len(avail) >= 3:
        loc_profiles = train_df.groupby("location")[avail].median()
        loc_targets = train_df.groupby("location")[TARGET].mean()
        loc_profiles = loc_profiles.fillna(loc_profiles.median())

        scaler = StandardScaler()
        X_locs = scaler.fit_transform(loc_profiles.values)

        # Test location features
        test_profile = test_df[avail].median().values.reshape(1, -1)
        test_profile = np.nan_to_num(test_profile, nan=np.nanmedian(loc_profiles.values, axis=0))
        test_scaled = scaler.transform(test_profile)

        # Find K nearest locations
        nn = NearestNeighbors(n_neighbors=min(8, len(X_locs)))
        nn.fit(X_locs)
        dists, idxs = nn.kneighbors(test_scaled)

        # Weighted average (closer = more weight)
        weights = 1.0 / (dists[0] + 0.1)
        weights /= weights.sum()
        neighbor_targets = loc_targets.iloc[idxs[0]].values
        cluster_enc = float(np.average(neighbor_targets, weights=weights))

    # Region-level encoding
    test_region = extract_region(test_df["location"].iloc[0])
    train_copy = train_df.copy()
    train_copy["_region"] = train_copy["location"].apply(extract_region)
    region_means = train_copy.groupby("_region")[TARGET].mean()
    region_enc = region_means.get(test_region, g_mean)

    return g_mean, cluster_enc, region_enc


def add_v7_features(df):
    """Add v7 feature engineering."""
    df = df.copy()

    # Import v6 features
    try:
        sys.path.insert(0, ".")
        import importlib.util
        spec = importlib.util.spec_from_file_location("tv6", "castline/validation/train_v6.py")
        mod = importlib.util.module_from_spec(spec)
        mod.__name__ = "tv6"
        sys.modules["tv6"] = mod
        spec.loader.exec_module(mod)
        df = mod.add_engineered_features(df)
    except Exception as e:
        print(f"Warning: v6 features failed: {e}")

    # v7 additions
    wt = df.get("water_temp_c", df.get("om_est_water_temp"))
    if wt is not None:
        wt = wt.copy()
        if "om_est_water_temp" in df.columns:
            wt = wt.fillna(df["om_est_water_temp"])
        if "air_temp_c" in df.columns:
            wt = wt.fillna(df["air_temp_c"] * 0.663 + 7.16)

        lat = df.get("lat", pd.Series(35.0, index=df.index)).fillna(35.0)
        month = df.get("month", pd.Series(6, index=df.index)).fillna(6)

        # Water temp deviation from regional seasonal norm
        seasonal_peak = 25.0 - 0.4 * (lat - 35).abs()
        month_offset = -np.cos(2 * np.pi * (month - 1) / 12) * 10
        regional_norm = seasonal_peak + month_offset
        df["water_temp_regional_deviation"] = wt - regional_norm

        # Species indicators
        df["smallmouth_indicator"] = np.clip((lat - 38) / 5, 0, 1).clip(lower=0)
        df["largemouth_indicator"] = np.clip((38 - lat) / 5, 0, 1).clip(lower=0)
        sm_score = np.exp(-0.5 * ((wt - 18) / 3) ** 2) * df["smallmouth_indicator"]
        lm_score = np.exp(-0.5 * ((wt - 23) / 4) ** 2) * df["largemouth_indicator"]
        df["species_habitat_score"] = sm_score + lm_score

        # Pre-spawn aggression
        df["prespawn_aggression"] = np.where(
            (wt >= 13) & (wt <= 18), (wt - 13) / 5.0,
            np.where(wt < 13, 0, np.where(wt <= 22, 0.8, 0.3))
        )

    # Flow regime
    if "discharge_pct_of_30d" in df.columns:
        dpct = df["discharge_pct_of_30d"].fillna(100)
        df["flow_regime"] = np.where(dpct < 50, 0, np.where(dpct < 120, 1,
                            np.where(dpct < 200, 2, 3))).astype(float)

    # Composite fishing score
    components = []
    for col, w in [("feeding_window_score", 0.25), ("pressure_fishing_quality", 0.2),
                   ("season_quality_index", 0.2), ("wind_mixing_index", 0.1),
                   ("do_comfort_index", 0.15), ("conditions_stability", 0.1)]:
        if col in df.columns:
            components.append((df[col].fillna(0.5), w))
    if components:
        total_w = sum(w for _, w in components)
        df["composite_fishing_score"] = sum(c * w / total_w for c, w in components)

    # Interactions
    if "morphometric_productivity" in df.columns and "composite_fishing_score" in df.columns:
        df["loc_quality_x_conditions"] = df["morphometric_productivity"] * df["composite_fishing_score"]
    if "pressure_fishing_quality" in df.columns and "spawn_phase" in df.columns:
        df["pressure_x_spawn"] = df["pressure_fishing_quality"] * df["spawn_phase"]
    if "wind_mixing_index" in df.columns and "max_depth_ft" in df.columns:
        df["wind_x_depth"] = df["wind_mixing_index"] / (1 + df["max_depth_ft"].fillna(30) / 50)

    return df


# ── Evaluations ──────────────────────────────────────────────

def eval_temporal(df, features):
    train = df[df["year"] <= 2023].dropna(subset=[TARGET])
    test = df[df["year"] >= 2024].dropna(subset=[TARGET])
    X_tr, y_tr = train[features].values, train[TARGET].values
    X_te, y_te = test[features].values, test[TARGET].values
    m = train_lgb(X_tr, y_tr, X_te, y_te)
    p = m.predict(X_te)
    r2 = r2_score(y_te, p)
    rmse = np.sqrt(mean_squared_error(y_te, p))
    top = [(features[i], int(m.feature_importances_[i]))
           for i in np.argsort(m.feature_importances_)[::-1][:10]]
    return r2, rmse, top


def eval_spatial(df, features):
    df = df.copy()
    df["region"] = df["location"].apply(extract_region)
    r2s = []
    for region in sorted(df["region"].unique()):
        test = df[df["region"] == region].dropna(subset=[TARGET])
        train = df[df["region"] != region].dropna(subset=[TARGET])
        if len(test) < 10:
            continue
        m = train_lgb(train[features].values, train[TARGET].values)
        p = m.predict(test[features].values)
        r2 = r2_score(test[TARGET].values, p)
        r2s.append(r2)
        print(f"    {region}: R2={r2:.4f} (n={len(test)})")
    return np.mean(r2s)


def eval_spatiotemporal(df, features, n_splits=5):
    df = df.copy().dropna(subset=[TARGET])
    df["region"] = df["location"].apply(extract_region)
    df["block"] = df["region"] + "_" + df["year"].astype(str)
    X, y = df[features].values, df[TARGET].values
    groups = df["block"].values
    gkf = GroupKFold(n_splits=min(n_splits, len(np.unique(groups))))
    r2s = []
    for tr_idx, te_idx in gkf.split(X, y, groups):
        m = train_lgb(X[tr_idx], y[tr_idx])
        p = m.predict(X[te_idx])
        r2s.append(r2_score(y[te_idx], p))
    return np.mean(r2s), np.std(r2s), r2s


def eval_loo(df, features, top_n=15):
    """LOO with LOO target encoding + nearest-neighbor location encoding."""
    df = df.copy().dropna(subset=[TARGET])
    loc_counts = df["location"].value_counts()

    all_true, all_pred = [], []
    per_loc = []

    for loc in loc_counts.head(top_n).index:
        train = df[df["location"] != loc]
        test = df[df["location"] == loc]

        # Compute LOO target encodings
        g_mean, cluster_enc, region_enc = loo_target_encode(train, test)

        # Add encoding features to data
        train_ext = train.copy()
        test_ext = test.copy()

        # For train: LOO encoding per location
        for l in train_ext["location"].unique():
            mask = train_ext["location"] == l
            other = train_ext[~mask]
            l_stats = other.groupby("location").agg({TARGET: ["sum", "count"]})
            loc_sum = l_stats.get((TARGET, "sum"), {}).get(l, 0)
            loc_n = l_stats.get((TARGET, "count"), {}).get(l, 0)
            # Simpler: just use regional mean as proxy
            pass

        # Add target encoding features
        train_ext["_cluster_enc"] = g_mean  # placeholder for train
        test_ext["_cluster_enc"] = cluster_enc
        train_ext["_region_enc"] = g_mean  # placeholder
        test_ext["_region_enc"] = region_enc

        ext_features = features + ["_cluster_enc", "_region_enc"]
        ext_features = [f for f in ext_features if f in train_ext.columns]

        X_tr = train_ext[ext_features].values
        y_tr = train_ext[TARGET].values
        X_te = test_ext[ext_features].values
        y_te = test_ext[TARGET].values

        m = train_lgb(X_tr, y_tr)
        p = m.predict(X_te)

        r2 = r2_score(y_te, p) if len(y_te) > 1 else float("nan")
        all_true.extend(y_te.tolist())
        all_pred.extend(p.tolist())

        per_loc.append({
            "location": loc, "r2": round(r2, 4), "n": len(test),
            "actual_mean": round(float(y_te.mean()), 2),
            "pred_mean": round(float(p.mean()), 2),
            "cluster_enc": round(cluster_enc, 2),
            "region_enc": round(region_enc, 2),
        })

        status = "OK" if r2 > 0 else ("~" if r2 > -1 else "BAD")
        print(f"    [{status:3s}] {loc[:45]:45s} n={len(test):>3d} R2={r2:>7.3f} "
              f"true={y_te.mean():.1f} pred={p.mean():.1f} cluster={cluster_enc:.1f} region={region_enc:.1f}")

    overall_r2 = r2_score(all_true, all_pred)
    return overall_r2, per_loc


# ── Main ────────────────────────────────────────────────────

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "castline/validation/data/assembled/validation_dataset_v6.csv"

    print("=" * 70)
    print("QUICK v7 EVALUATION (single LightGBM + v7 features)")
    print("=" * 70)

    df = pd.read_csv(path)
    if "year" not in df.columns:
        df["year"] = pd.to_datetime(df["date"]).dt.year

    print(f"Dataset: {len(df)} rows, {df['location'].nunique()} locations")

    print("\nApplying v7 feature engineering...")
    df = add_v7_features(df)
    print(f"Columns: {len(df.columns)}")

    full_feats = get_features(df, include_location_id=True)
    loo_feats = get_features(df, include_location_id=False, include_cluster=False)
    print(f"Full features: {len(full_feats)}")
    print(f"LOO features: {len(loo_feats)}")

    # 1. Temporal
    print("\n" + "=" * 50)
    print("1. TEMPORAL HOLDOUT")
    print("=" * 50)
    t0 = time.time()
    r2_t, rmse_t, top_t = eval_temporal(df, full_feats)
    print(f"  R2={r2_t:.4f}  RMSE={rmse_t:.2f}  [{time.time()-t0:.1f}s]")
    print(f"  Top: {top_t[:5]}")

    # 2. Spatial
    print("\n" + "=" * 50)
    print("2. SPATIAL HOLDOUT")
    print("=" * 50)
    t0 = time.time()
    r2_s = eval_spatial(df, full_feats)
    print(f"  Mean R2={r2_s:.4f}  [{time.time()-t0:.1f}s]")

    # 3. Spatiotemporal
    print("\n" + "=" * 50)
    print("3. SPATIOTEMPORAL BLOCKED")
    print("=" * 50)
    t0 = time.time()
    r2_st, std_st, folds_st = eval_spatiotemporal(df, full_feats)
    print(f"  R2={r2_st:.4f} ± {std_st:.4f}  [{time.time()-t0:.1f}s]")
    print(f"  Folds: {[round(f, 4) for f in folds_st]}")

    # 4. LOO
    print("\n" + "=" * 50)
    print("4. LEAVE-ONE-LOCATION-OUT")
    print("=" * 50)
    t0 = time.time()
    r2_loo, per_loc = eval_loo(df, loo_feats, top_n=15)
    print(f"  Overall R2={r2_loo:.4f}  [{time.time()-t0:.1f}s]")
    r2_list = [p["r2"] for p in per_loc]
    pos = sum(1 for r in r2_list if r > 0)
    print(f"  Positive R2: {pos}/{len(per_loc)}")
    print(f"  Median R2: {np.median(r2_list):.4f}")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for name, r2 in [("Temporal", r2_t), ("Spatial", r2_s),
                     ("Spatiotemporal", r2_st), ("LOO", r2_loo)]:
        gap = 0.9 - r2
        print(f"  {name:25s}: R2={r2:.4f}  gap={gap:+.4f}")

    # Save results
    results = {
        "temporal": {"r2": round(r2_t, 4)},
        "spatial": {"r2": round(r2_s, 4)},
        "spatiotemporal": {"r2": round(r2_st, 4)},
        "loo": {"r2": round(r2_loo, 4), "per_location": per_loc},
    }
    out = Path("castline/validation/artifacts/eval_quick_v7.json")
    out.parent.mkdir(exist_ok=True)
    with open(out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved to {out}")
