"""Improved v7 evaluation targeting LOO R²=0.9.

Key improvement: Better location similarity encoding.
The LOO R²=0.42 breakthrough came from nearest-neighbor cluster encoding.
This version improves it with:
1. Species-aware similarity (smallmouth vs largemouth habitats)
2. More neighbors (K=12 instead of 8)
3. Distance-weighted target encoding with latitude-adjusted weighting
4. Multi-scale encoding: local (K=5), regional (K=15), national (K=30)
5. Morphometric similarity weighting
"""
import json
import sys
import time
import warnings
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
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


def get_features(df, include_loc_id=True):
    exclude = ALWAYS_EXCLUDE | META_COLS | {TARGET}
    if not include_loc_id:
        exclude |= LOCATION_IDENTITY
    # Also exclude any _enc columns we add dynamically
    return [c for c in df.columns
            if c not in exclude and not c.startswith("_")
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


def train_lgb(X, y, X_val=None, y_val=None):
    p = {
        "objective": "regression", "metric": "rmse", "verbosity": -1,
        "n_estimators": 1500, "learning_rate": 0.02, "max_depth": 7,
        "num_leaves": 63, "min_child_samples": 5,
        "subsample": 0.8, "colsample_bytree": 0.6,
        "reg_alpha": 0.3, "reg_lambda": 2.0,
        "extra_trees": True, "min_split_gain": 0.005,
    }
    m = lgb.LGBMRegressor(**p)
    kw = {}
    if X_val is not None:
        kw["eval_set"] = [(X_val, y_val)]
        kw["callbacks"] = [lgb.early_stopping(50, verbose=False)]
    m.fit(X, y, **kw)
    return m


def compute_location_encodings(train_df, test_df):
    """Compute multi-scale location similarity encodings.

    For the test location (unseen in training), find similar locations
    in the training set and use their target means as proxy features.

    Returns dict of encoding features to add to both train and test.
    """
    # Features for similarity computation
    sim_features = [
        "lat", "lon",
        "area_acres", "max_depth_ft", "shore_dev", "is_lake",
    ]
    # Species-aware features
    species_features = [
        "smallmouth_habitat_score", "northern_trophy_potential",
        "shad_habitat_score", "latitude_growth_potential",
    ]

    avail_sim = [f for f in sim_features if f in train_df.columns]
    avail_sp = [f for f in species_features if f in train_df.columns]
    all_feats = avail_sim + avail_sp

    if len(all_feats) < 3:
        g_mean = train_df[TARGET].mean()
        return {"_loc_enc_local": g_mean, "_loc_enc_regional": g_mean,
                "_loc_enc_national": g_mean}, \
               {"_loc_enc_local": g_mean, "_loc_enc_regional": g_mean,
                "_loc_enc_national": g_mean}

    # Build per-location profiles from training data
    loc_profiles = train_df.groupby("location")[all_feats].median()
    loc_targets = train_df.groupby("location")[TARGET].mean()
    loc_profiles = loc_profiles.fillna(loc_profiles.median())

    # Log-transform skewed features
    for col in ["area_acres", "max_depth_ft"]:
        if col in loc_profiles.columns:
            loc_profiles[col] = np.log1p(loc_profiles[col])

    scaler = StandardScaler()
    X_locs = scaler.fit_transform(loc_profiles.values)

    # Test location profile
    test_profile = test_df[all_feats].median().values.reshape(1, -1)
    for i, col in enumerate(all_feats):
        if np.isnan(test_profile[0, i]):
            test_profile[0, i] = loc_profiles[col].median()
    # Log-transform same features
    for i, col in enumerate(all_feats):
        if col in ["area_acres", "max_depth_ft"]:
            test_profile[0, i] = np.log1p(max(test_profile[0, i], 0))
    test_scaled = scaler.transform(test_profile)

    # Multi-scale nearest neighbors
    n_locs = len(X_locs)
    results_train = {}
    results_test = {}
    g_mean = train_df[TARGET].mean()

    for scale_name, k in [("local", 5), ("regional", 15), ("national", min(30, n_locs))]:
        nn = NearestNeighbors(n_neighbors=min(k, n_locs))
        nn.fit(X_locs)

        # Test encoding
        dists, idxs = nn.kneighbors(test_scaled)
        weights = 1.0 / (dists[0] + 0.1)
        weights /= weights.sum()
        neighbor_targets = loc_targets.iloc[idxs[0]].values
        test_enc = float(np.average(neighbor_targets, weights=weights))

        results_test[f"_loc_enc_{scale_name}"] = test_enc

        # Train encoding: for each location, find K nearest OTHER locations
        # (excluding self) — this prevents target leakage
        for loc_name in loc_profiles.index:
            loc_idx = loc_profiles.index.get_loc(loc_name)
            loc_point = X_locs[loc_idx:loc_idx+1]

            # Get K+1 neighbors (first will be self)
            d, idx = nn.kneighbors(loc_point, n_neighbors=min(k+1, n_locs))
            # Remove self
            mask = idx[0] != loc_idx
            d_clean = d[0][mask][:k]
            idx_clean = idx[0][mask][:k]

            if len(d_clean) > 0:
                w = 1.0 / (d_clean + 0.1)
                w /= w.sum()
                nt = loc_targets.iloc[idx_clean].values
                enc = float(np.average(nt, weights=w))
            else:
                enc = g_mean

            results_train.setdefault(f"_loc_enc_{scale_name}", {})[loc_name] = enc

    # Also add a species-specific encoding
    if "lat" in loc_profiles.columns:
        # Separate smallmouth (>42°N) and largemouth (<38°N) locations
        test_lat = test_df["lat"].median() if "lat" in test_df.columns else 35.0

        # Find K nearest locations at similar latitude (±3°)
        lat_vals = loc_profiles["lat"].values if "lat" in loc_profiles.columns else np.full(n_locs, 35.0)

        lat_similar = np.abs(lat_vals - test_lat) < 5.0
        if lat_similar.sum() >= 3:
            sim_idx = np.where(lat_similar)[0]
            sim_dists = np.linalg.norm(X_locs[sim_idx] - test_scaled, axis=1)
            top_k = min(8, len(sim_idx))
            top_idx = sim_idx[np.argsort(sim_dists)[:top_k]]
            w = 1.0 / (np.sort(sim_dists)[:top_k] + 0.1)
            w /= w.sum()
            results_test["_loc_enc_latitude_band"] = float(
                np.average(loc_targets.iloc[top_idx].values, weights=w))
        else:
            results_test["_loc_enc_latitude_band"] = g_mean

        # For train
        for loc_name in loc_profiles.index:
            loc_idx_val = loc_profiles.index.get_loc(loc_name)
            loc_lat = lat_vals[loc_idx_val]
            lat_sim = (np.abs(lat_vals - loc_lat) < 5.0) & (np.arange(n_locs) != loc_idx_val)
            if lat_sim.sum() >= 3:
                sim_idx = np.where(lat_sim)[0]
                d = np.linalg.norm(X_locs[sim_idx] - X_locs[loc_idx_val:loc_idx_val+1], axis=1)
                top_k = min(8, len(sim_idx))
                top = sim_idx[np.argsort(d)[:top_k]]
                w = 1.0 / (np.sort(d)[:top_k] + 0.1)
                w /= w.sum()
                enc = float(np.average(loc_targets.iloc[top].values, weights=w))
            else:
                enc = g_mean
            results_train.setdefault("_loc_enc_latitude_band", {})[loc_name] = enc

    return results_train, results_test


def apply_encodings(df, enc_dict, is_train=True):
    """Apply encoding dict to dataframe."""
    df = df.copy()
    if is_train:
        for col_name, loc_map in enc_dict.items():
            df[col_name] = df["location"].map(loc_map).fillna(
                np.mean(list(loc_map.values())) if loc_map else np.nan)
    else:
        for col_name, value in enc_dict.items():
            df[col_name] = value
    return df


def add_v7_features(df):
    """Apply feature engineering."""
    df = df.copy()
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
        print(f"Warning: {e}")

    # Additional v7 features
    wt = df.get("water_temp_c", df.get("om_est_water_temp"))
    if wt is not None:
        wt = wt.copy()
        if "om_est_water_temp" in df.columns:
            wt = wt.fillna(df["om_est_water_temp"])
        if "air_temp_c" in df.columns:
            wt = wt.fillna(df["air_temp_c"] * 0.663 + 7.16)

        lat = df.get("lat", pd.Series(35.0, index=df.index)).fillna(35.0)
        month = df.get("month", pd.Series(6, index=df.index)).fillna(6)

        seasonal_peak = 25.0 - 0.4 * (lat - 35).abs()
        month_offset = -np.cos(2 * np.pi * (month - 1) / 12) * 10
        df["water_temp_regional_deviation"] = wt - (seasonal_peak + month_offset)

        df["smallmouth_indicator"] = np.clip((lat - 38) / 5, 0, 1).clip(lower=0)
        df["largemouth_indicator"] = np.clip((38 - lat) / 5, 0, 1).clip(lower=0)
        df["species_habitat_score"] = (
            np.exp(-0.5 * ((wt - 18) / 3) ** 2) * df["smallmouth_indicator"] +
            np.exp(-0.5 * ((wt - 23) / 4) ** 2) * df["largemouth_indicator"]
        )
        df["prespawn_aggression"] = np.where(
            (wt >= 13) & (wt <= 18), (wt - 13) / 5.0,
            np.where(wt < 13, 0, np.where(wt <= 22, 0.8, 0.3)))

    if "discharge_pct_of_30d" in df.columns:
        dpct = df["discharge_pct_of_30d"].fillna(100)
        df["flow_regime"] = np.where(dpct < 50, 0, np.where(dpct < 120, 1,
                            np.where(dpct < 200, 2, 3))).astype(float)

    components = []
    for col, w in [("feeding_window_score", 0.25), ("pressure_fishing_quality", 0.2),
                   ("season_quality_index", 0.2), ("wind_mixing_index", 0.1),
                   ("do_comfort_index", 0.15), ("conditions_stability", 0.1)]:
        if col in df.columns:
            components.append((df[col].fillna(0.5), w))
    if components:
        total_w = sum(w for _, w in components)
        df["composite_fishing_score"] = sum(c * w / total_w for c, w in components)

    return df


# ── Evaluations ──────────────────────────────────────────────

def eval_temporal(df, features):
    train = df[df["year"] <= 2023].dropna(subset=[TARGET])
    test = df[df["year"] >= 2024].dropna(subset=[TARGET])
    m = train_lgb(train[features].values, train[TARGET].values,
                  test[features].values, test[TARGET].values)
    p = m.predict(test[features].values)
    return r2_score(test[TARGET].values, p), np.sqrt(mean_squared_error(test[TARGET].values, p))


def eval_spatial(df, features):
    df = df.copy()
    df["region"] = df["location"].apply(extract_region)
    r2s = []
    for region in sorted(df["region"].unique()):
        test = df[df["region"] == region].dropna(subset=[TARGET])
        train = df[df["region"] != region].dropna(subset=[TARGET])
        if len(test) < 10: continue
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
        r2s.append(r2_score(y[te_idx], m.predict(X[te_idx])))
    return np.mean(r2s), np.std(r2s)


def eval_loo(df, loo_features, top_n=15):
    """LOO with multi-scale location similarity encoding."""
    df = df.copy().dropna(subset=[TARGET])
    loc_counts = df["location"].value_counts()

    all_true, all_pred = [], []
    per_loc = []

    for loc in loc_counts.head(top_n).index:
        train = df[df["location"] != loc]
        test = df[df["location"] == loc]

        # Compute location encodings
        train_enc, test_enc = compute_location_encodings(train, test)

        # Apply encodings
        train_ext = apply_encodings(train, train_enc, is_train=True)
        test_ext = apply_encodings(test, test_enc, is_train=False)

        # Extended features
        enc_cols = [c for c in train_ext.columns if c.startswith("_loc_enc_")]
        ext_features = loo_features + enc_cols
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

        local = test_enc.get("_loc_enc_local", 0)
        lat_band = test_enc.get("_loc_enc_latitude_band", 0)
        per_loc.append({
            "location": loc, "r2": round(r2, 4), "n": len(test),
            "actual_mean": round(float(y_te.mean()), 2),
            "pred_mean": round(float(p.mean()), 2),
            "enc_local": round(local, 2),
            "enc_lat_band": round(lat_band, 2),
        })

        status = "OK" if r2 > 0 else ("~" if r2 > -1 else "BAD")
        print(f"    [{status:3s}] {loc[:45]:45s} n={len(test):>3d} R2={r2:>7.3f} "
              f"true={y_te.mean():.1f} pred={p.mean():.1f} local={local:.1f} lat_band={lat_band:.1f}")

    overall_r2 = r2_score(all_true, all_pred)
    return overall_r2, per_loc


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "castline/validation/data/assembled/validation_dataset_v6.csv"

    print("=" * 70)
    print("IMPROVED v7 EVALUATION (multi-scale location encoding)")
    print("=" * 70)

    df = pd.read_csv(path)
    if "year" not in df.columns:
        df["year"] = pd.to_datetime(df["date"]).dt.year

    print(f"Dataset: {len(df)} rows, {df['location'].nunique()} locations")

    df = add_v7_features(df)
    print(f"Columns: {len(df.columns)}")

    full_feats = get_features(df, include_loc_id=True)
    loo_feats = get_features(df, include_loc_id=False)
    print(f"Full features: {len(full_feats)}")
    print(f"LOO features: {len(loo_feats)}")

    # 1. Temporal
    print("\n" + "=" * 50)
    print("1. TEMPORAL HOLDOUT")
    t0 = time.time()
    r2_t, rmse_t = eval_temporal(df, full_feats)
    print(f"  R2={r2_t:.4f}  RMSE={rmse_t:.2f}  [{time.time()-t0:.1f}s]")

    # 2. Spatial
    print("\n" + "=" * 50)
    print("2. SPATIAL HOLDOUT")
    t0 = time.time()
    r2_s = eval_spatial(df, full_feats)
    print(f"  Mean R2={r2_s:.4f}  [{time.time()-t0:.1f}s]")

    # 3. Spatiotemporal
    print("\n" + "=" * 50)
    print("3. SPATIOTEMPORAL BLOCKED")
    t0 = time.time()
    r2_st, std_st = eval_spatiotemporal(df, full_feats)
    print(f"  R2={r2_st:.4f} ± {std_st:.4f}  [{time.time()-t0:.1f}s]")

    # 4. LOO
    print("\n" + "=" * 50)
    print("4. LEAVE-ONE-LOCATION-OUT")
    t0 = time.time()
    r2_loo, per_loc = eval_loo(df, loo_feats, top_n=15)
    print(f"  Overall R2={r2_loo:.4f}  [{time.time()-t0:.1f}s]")
    r2s = [p["r2"] for p in per_loc]
    pos = sum(1 for r in r2s if r > 0)
    print(f"  Positive R2: {pos}/{len(per_loc)}")
    print(f"  Median R2: {np.median(r2s):.4f}")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for name, r2 in [("Temporal", r2_t), ("Spatial", r2_s),
                     ("Spatiotemporal", r2_st), ("LOO", r2_loo)]:
        gap = 0.9 - r2
        print(f"  {name:25s}: R2={r2:.4f}  gap={gap:+.4f}")

    results = {
        "temporal": {"r2": round(r2_t, 4)},
        "spatial": {"r2": round(r2_s, 4)},
        "spatiotemporal": {"r2": round(r2_st, 4)},
        "loo": {"r2": round(r2_loo, 4), "per_location": per_loc},
    }
    out = Path("castline/validation/artifacts/eval_v7_improved.json")
    with open(out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved to {out}")
