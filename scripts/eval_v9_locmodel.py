"""v9 LOO with learned location quality model.

Instead of KNN target encoding, train a CatBoost model that predicts
location mean target from morphometric features. This can learn
non-linear relationships that KNN misses.
"""
import pandas as pd
import numpy as np
from sklearn.metrics import r2_score, mean_absolute_error
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
source_means = df.groupby("source")[TARGET].mean()
df["source_enc"] = df.source.map(source_means)
print(f"Dataset: {len(df)} rows, {df.location.nunique()} locations")

all_cols = set(df.columns)
exclude = ALWAYS_EXCLUDE | LOCATION_IDENTITY | META_COLS | {TARGET}
full_features = sorted(all_cols - exclude)
full_features = [f for f in full_features if df[f].dtype in ["float64", "int64", "float32", "int32"]]
loo_features = [f for f in full_features if f not in LOCATION_IDENTITY]

# Location-level features (static per location — for quality model)
LOC_FEATURES = ["lat", "lon", "area_acres", "is_lake",
                "creel_lmb_ratio", "creel_smb_ratio", "creel_spotted_ratio",
                "creel_cpue_mean", "max_depth_ft", "shore_dev",
                "latitude_growth_potential", "source_enc"]
LOC_FEATURES = [f for f in LOC_FEATURES if f in df.columns]

print(f"LOO features: {len(loo_features)}")
print(f"Location quality features: {len(LOC_FEATURES)}")

loc_counts = df.location.value_counts()
loc_std = df.groupby("location")[TARGET].std()
clean_locs = [l for l in loc_counts[loc_counts >= MIN_LOO_N].index
              if loc_std.get(l, 0) >= MIN_LOO_STD]

print(f"\n" + "=" * 50)
print(f"LOO: LEARNED LOCATION QUALITY MODEL")
print(f"=" * 50)
print(f"Clean LOO set: {len(clean_locs)} locations")
t0 = time.time()

all_true, all_pred = [], []
per_loc = {}

for i, loc in enumerate(clean_locs):
    mask = df.location == loc
    train, test = df[~mask].copy(), df[mask].copy()

    # ── Step 1: Train location quality model ──
    # Build location-level dataset from training data
    loc_data = train.groupby("location").agg(
        loc_target=(TARGET, "mean"),
        n_events=(TARGET, "count"),
        **{f: (f, "first") for f in LOC_FEATURES}
    ).reset_index()
    # Weight by number of events (more data = more reliable mean)
    sample_weight = np.sqrt(loc_data["n_events"].values)

    loc_model = CatBoostRegressor(
        iterations=300, depth=5, learning_rate=0.05,
        l2_leaf_reg=5, verbose=0, random_seed=42,
    )
    loc_model.fit(loc_data[LOC_FEATURES], loc_data["loc_target"],
                  sample_weight=sample_weight)

    # Predict quality for all locations (train + test)
    train_loc_pred = train.groupby("location")[LOC_FEATURES].first().reset_index()
    train_loc_pred["loc_quality"] = loc_model.predict(train_loc_pred[LOC_FEATURES])
    train["loc_quality"] = train.location.map(
        dict(zip(train_loc_pred.location, train_loc_pred.loc_quality))
    )

    test_quality = loc_model.predict(test[LOC_FEATURES].iloc[0:1])[0]
    test["loc_quality"] = test_quality

    # Also predict std
    loc_data_std = train.groupby("location").agg(
        loc_std=(TARGET, "std"),
        **{f: (f, "first") for f in LOC_FEATURES}
    ).reset_index()
    loc_data_std["loc_std"] = loc_data_std["loc_std"].fillna(0)

    loc_std_model = CatBoostRegressor(
        iterations=200, depth=4, learning_rate=0.05,
        l2_leaf_reg=5, verbose=0, random_seed=42,
    )
    loc_std_model.fit(loc_data_std[LOC_FEATURES], loc_data_std["loc_std"])
    train_std_pred = train.groupby("location")[LOC_FEATURES].first().reset_index()
    train_std_pred["loc_pred_std"] = loc_std_model.predict(train_std_pred[LOC_FEATURES])
    train["loc_pred_std"] = train.location.map(
        dict(zip(train_std_pred.location, train_std_pred.loc_pred_std))
    )
    test["loc_pred_std"] = loc_std_model.predict(test[LOC_FEATURES].iloc[0:1])[0]

    # ── Step 2: Train event-level model ──
    use = loo_features + ["loc_quality", "loc_pred_std"]
    m = CatBoostRegressor(iterations=600, depth=6, learning_rate=0.05,
                          l2_leaf_reg=3, verbose=0, random_seed=42)
    m.fit(train[use], train[TARGET])
    pred = np.clip(m.predict(test[use]), 0, 30)

    r2 = r2_score(test[TARGET], pred) if len(test) > 1 and np.std(test[TARGET]) > 0.01 else 0.0
    per_loc[loc] = r2

    if (i + 1) % 10 == 0:
        interim = r2_score(all_true + list(test[TARGET]), all_pred + list(pred))
        print(f"  {i+1}/{len(clean_locs)} interim R²={interim:.4f}  loc_quality={test_quality:.1f} actual_mean={test[TARGET].mean():.1f}")

    all_true.extend(test[TARGET].tolist())
    all_pred.extend(pred.tolist())

r2_loo = r2_score(all_true, all_pred)
mae_loo = mean_absolute_error(all_true, all_pred)
positive = sum(1 for r in per_loc.values() if r > 0)
print(f"\n  Overall LOO R²={r2_loo:.4f}  MAE={mae_loo:.3f}lb  [{time.time()-t0:.1f}s]")
print(f"  Positive R²: {positive}/{len(clean_locs)}")
print(f"  Median: {np.median(list(per_loc.values())):.4f}")

sorted_locs = sorted(per_loc.items(), key=lambda x: x[1])
print("\n  Worst 5:")
for loc, r2 in sorted_locs[:5]:
    n = loc_counts[loc]
    mn = df[df.location == loc][TARGET].mean()
    print(f"    {loc:50s} n={n:3d} R²={r2:8.3f} mean={mn:.1f}")
print("  Best 5:")
for loc, r2 in sorted_locs[-5:]:
    n = loc_counts[loc]
    mn = df[df.location == loc][TARGET].mean()
    print(f"    {loc:50s} n={n:3d} R²={r2:8.3f} mean={mn:.1f}")

# Feature importance
fi = pd.Series(m.feature_importances_, index=use).nlargest(15)
print("\n  Feature importance (last fold):")
for f, v in fi.items():
    print(f"    {f:40s} {v:.1f}")

print(f"\nProgress: v8=0.493 → v9=0.535 → v9-best=0.551 → v9-locmodel={r2_loo:.3f}")
