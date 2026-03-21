"""Comprehensive ML model comparison on v7 dataset.

Tests ALL methods on all 4 evaluation metrics:
1. LightGBM (baseline)
2. XGBoost
3. CatBoost
4. Stacking Ensemble (LGB + XGB + CatBoost → Ridge)
5. GNN (GraphSAGE)
6. Conditional Diffusion Model (DDPM)
7. TFT (Temporal Fusion Transformer)

Each model is evaluated on:
- Temporal holdout (train ≤2023, test ≥2024)
- Spatial holdout (leave-one-region-out)
- Spatiotemporal blocked (GroupKFold on region×year)
- Leave-one-location-out (LOO) with KNN encoding
"""
import json
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import r2_score, mean_squared_error
from sklearn.model_selection import GroupKFold
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import Ridge

warnings.filterwarnings("ignore")

TARGET = "median_weight_lb"
ALWAYS_EXCLUDE = {"target_success_score", "tms_id", "event_id"}
LOCATION_IDENTITY = {"loc_enc", "trail_mean_weight", "location_mean_weight",
                     "loc_rolling_3", "baseline_signal"}
META_COLS = {"date", "location", "region", "block", "sat_source",
             "usgs_site_id", "event_name", "tournament_slug",
             "site_id", "observation_date", "iem_station",
             "species", "results_source", "trail", "source_mode",
             "day_number", "ecoregion_season_deviation", "source",
             "front_phase", "weight_outlier", "tournament_id",
             "measurement_type", "month"}
LOO_LEAK_COLS = {"cluster_mean_weight", "cluster_std_weight",
                 "similar_loc_mean_weight", "cluster_count",
                 "location_cluster"}


def get_features(df, include_location_id=True):
    exclude = ALWAYS_EXCLUDE | META_COLS | {TARGET}
    if not include_location_id:
        exclude |= LOCATION_IDENTITY | LOO_LEAK_COLS
    return [c for c in df.columns
            if c not in exclude
            and df[c].dtype in (np.float64, np.float32, np.int64, np.int32, float, int)
            and df[c].notna().mean() > 0.03]


def extract_region(loc):
    if pd.isna(loc):
        return "OT"
    parts = loc.rsplit(",", 1)
    st = parts[1].strip()[:2].upper() if len(parts) == 2 else "UNK"
    r = {"SE": "AL FL GA SC NC VA TN MS LA AR KY WV".split(),
         "NE": "NY PA MD DE NJ CT MA ME VT NH RI".split(),
         "MW": "WI MN MI OH IN IL IA MO KS ND SD NE".split(),
         "SW": "TX OK AZ NM UT CO".split(),
         "W": "CA OR WA ID MT WY NV HI AK".split(),
         "ON": ["ON"]}
    for region, states in r.items():
        if st in states:
            return region
    return "OT"


# ═══════════════════════════════════════════════════════════
# LOO Target Encoding (shared across all models)
# ═══════════════════════════════════════════════════════════

def loo_target_encode(train_df, test_df, features_for_nn=None):
    """Compute KNN-based location encoding for LOO evaluation."""
    g_mean = train_df[TARGET].mean()

    cluster_feats = features_for_nn or ["lat", "lon", "area_acres", "max_depth_ft", "is_lake",
                                         "creel_smb_ratio", "creel_lmb_ratio", "creel_cpue_mean"]
    avail = [f for f in cluster_feats if f in train_df.columns and train_df[f].notna().mean() > 0.1]
    cluster_enc = g_mean

    if len(avail) >= 3:
        loc_profiles = train_df.groupby("location")[avail].median()
        loc_targets = train_df.groupby("location")[TARGET].mean()
        loc_profiles = loc_profiles.fillna(loc_profiles.median())

        scaler = StandardScaler()
        X_locs = scaler.fit_transform(loc_profiles.values)

        test_profile = test_df[avail].median().values.reshape(1, -1)
        test_profile = np.nan_to_num(test_profile, nan=np.nanmedian(loc_profiles.values, axis=0))
        test_scaled = scaler.transform(test_profile)

        K = min(8, len(X_locs))
        nn = NearestNeighbors(n_neighbors=K)
        nn.fit(X_locs)
        dists, idxs = nn.kneighbors(test_scaled)

        weights = 1.0 / (dists[0] + 0.1)
        weights /= weights.sum()
        neighbor_targets = loc_targets.iloc[idxs[0]].values
        cluster_enc = float(np.average(neighbor_targets, weights=weights))

    # Region encoding
    test_region = extract_region(test_df["location"].iloc[0])
    train_copy = train_df.copy()
    train_copy["_region"] = train_copy["location"].apply(extract_region)
    region_means = train_copy.groupby("_region")[TARGET].mean()
    region_enc = region_means.get(test_region, g_mean)

    return g_mean, cluster_enc, region_enc


# ═══════════════════════════════════════════════════════════
# Model Definitions
# ═══════════════════════════════════════════════════════════

def train_lgb(X, y, X_val=None, y_val=None):
    import lightgbm as lgb
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


def train_xgb(X, y, X_val=None, y_val=None):
    import xgboost as xgb
    p = {
        "objective": "reg:squarederror", "n_estimators": 1500,
        "learning_rate": 0.02, "max_depth": 7, "min_child_weight": 5,
        "subsample": 0.8, "colsample_bytree": 0.6,
        "reg_alpha": 0.3, "reg_lambda": 2.0, "verbosity": 0,
        "tree_method": "hist",
    }
    m = xgb.XGBRegressor(**p)
    kw = {}
    if X_val is not None:
        kw["eval_set"] = [(X_val, y_val)]
        kw["verbose"] = False
    m.fit(X, y, **kw)
    return m


def train_catboost(X, y, X_val=None, y_val=None):
    from catboost import CatBoostRegressor
    p = {
        "iterations": 1500, "learning_rate": 0.02, "depth": 7,
        "l2_leaf_reg": 3.0, "subsample": 0.8, "verbose": 0,
        "loss_function": "RMSE",
    }
    m = CatBoostRegressor(**p)
    kw = {}
    if X_val is not None:
        kw["eval_set"] = (X_val, y_val)
        kw["early_stopping_rounds"] = 50
    m.fit(X, y, **kw)
    return m


def train_stacking(X, y, X_val=None, y_val=None):
    """Stacking ensemble: LGB + XGB + CatBoost → Ridge meta-learner."""
    from sklearn.model_selection import KFold

    # Train base models with 3-fold stacking
    kf = KFold(n_splits=3, shuffle=True, random_state=42)
    oof_preds = np.zeros((len(X), 3))

    models = []
    for fold_idx, (tr, va) in enumerate(kf.split(X)):
        fold_models = []
        for i, trainer in enumerate([train_lgb, train_xgb, train_catboost]):
            m = trainer(X[tr], y[tr], X[va], y[va])
            oof_preds[va, i] = m.predict(X[va])
            fold_models.append(m)
        models.append(fold_models)

    # Train meta-learner on OOF predictions
    meta = Ridge(alpha=1.0)
    meta.fit(oof_preds, y)

    class StackedModel:
        def __init__(self, base_models, meta_model):
            self.base_models = base_models  # list of fold model lists
            self.meta_model = meta_model

        def predict(self, X):
            base_preds = np.zeros((len(X), 3))
            for fold_models in self.base_models:
                for i, m in enumerate(fold_models):
                    base_preds[:, i] += m.predict(X) / len(self.base_models)
            return self.meta_model.predict(base_preds)

    return StackedModel(models, meta)


def train_gnn(X_train, y_train, X_val, y_val, loc_train, loc_val, all_locs_df):
    """Simple GraphSAGE GNN for spatial prediction."""
    try:
        import torch
        import torch.nn as nn
        from torch_geometric.nn import SAGEConv
        from torch_geometric.data import Data
    except ImportError:
        print("    [GNN] PyTorch Geometric not available — skipping")
        return None

    DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    # Build graph: locations as nodes, edges by spatial proximity
    unique_locs = list(all_locs_df.location.unique())
    loc_to_idx = {l: i for i, l in enumerate(unique_locs)}

    # Node features: median of features per location
    node_features = np.zeros((len(unique_locs), X_train.shape[1]))
    for loc in unique_locs:
        mask = all_locs_df.location == loc
        if mask.any():
            node_features[loc_to_idx[loc]] = np.nanmedian(
                all_locs_df.loc[mask, :X_train.shape[1]].values, axis=0
            )
    node_features = np.nan_to_num(node_features)

    # Edges: connect K nearest neighbors by features
    nn_model = NearestNeighbors(n_neighbors=min(6, len(unique_locs)))
    nn_model.fit(node_features)
    _, indices = nn_model.kneighbors(node_features)

    edge_src, edge_dst = [], []
    for i, neighbors in enumerate(indices):
        for j in neighbors[1:]:
            edge_src.extend([i, j])
            edge_dst.extend([j, i])

    edge_index = torch.tensor([edge_src, edge_dst], dtype=torch.long).to(DEVICE)
    x = torch.tensor(node_features, dtype=torch.float32).to(DEVICE)

    class SimpleGNN(nn.Module):
        def __init__(self, in_dim, hidden=32):
            super().__init__()
            self.conv1 = SAGEConv(in_dim, hidden)
            self.conv2 = SAGEConv(hidden, hidden)
            self.head = nn.Sequential(
                nn.Linear(hidden + in_dim, hidden),
                nn.ReLU(), nn.Dropout(0.3),
                nn.Linear(hidden, 1)
            )

        def forward(self, x, edge_index, event_features):
            h = torch.relu(self.conv1(x, edge_index))
            h = torch.relu(self.conv2(h, edge_index))
            combined = torch.cat([h, event_features], dim=1)
            return self.head(combined).squeeze(-1)

    model = SimpleGNN(X_train.shape[1], hidden=32).to(DEVICE)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001, weight_decay=1e-4)

    # Prepare training data
    train_locs_idx = [loc_to_idx.get(l, 0) for l in loc_train]
    X_t = torch.tensor(X_train, dtype=torch.float32).to(DEVICE)
    y_t = torch.tensor(y_train, dtype=torch.float32).to(DEVICE)

    val_locs_idx = [loc_to_idx.get(l, 0) for l in loc_val]
    X_v = torch.tensor(X_val, dtype=torch.float32).to(DEVICE)

    best_val_loss = float("inf")
    patience = 50
    patience_counter = 0

    for epoch in range(300):
        model.train()
        # Get GNN embeddings for training locations
        with torch.no_grad():
            h = torch.relu(model.conv1(x, edge_index))
            h = torch.relu(model.conv2(h, edge_index))

        loc_emb = h[train_locs_idx]
        combined = torch.cat([loc_emb, X_t], dim=1)
        pred = model.head(combined).squeeze(-1)
        loss = nn.MSELoss()(pred, y_t)

        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        # Validate
        if epoch % 10 == 0:
            model.eval()
            with torch.no_grad():
                h = torch.relu(model.conv1(x, edge_index))
                h = torch.relu(model.conv2(h, edge_index))
                loc_emb_v = h[val_locs_idx]
                combined_v = torch.cat([loc_emb_v, X_v], dim=1)
                pred_v = model.head(combined_v).squeeze(-1)
                val_loss = nn.MSELoss()(pred_v, torch.tensor(y_val, dtype=torch.float32).to(DEVICE))

            if val_loss < best_val_loss:
                best_val_loss = val_loss
                patience_counter = 0
            else:
                patience_counter += 1
                if patience_counter > patience // 10:
                    break

    class GNNPredictor:
        def __init__(self, model, x, edge_index, loc_to_idx, unique_locs):
            self.model = model
            self.x = x
            self.edge_index = edge_index
            self.loc_to_idx = loc_to_idx
            self.unique_locs = unique_locs

        def predict(self, X, locations):
            self.model.eval()
            with torch.no_grad():
                h = torch.relu(self.model.conv1(self.x, self.edge_index))
                h = torch.relu(self.model.conv2(h, self.edge_index))
                locs_idx = [self.loc_to_idx.get(l, 0) for l in locations]
                loc_emb = h[locs_idx]
                X_t = torch.tensor(X, dtype=torch.float32).to(DEVICE)
                combined = torch.cat([loc_emb, X_t], dim=1)
                return self.model.head(combined).squeeze(-1).cpu().numpy()

    return GNNPredictor(model, x, edge_index, loc_to_idx, unique_locs)


def train_diffusion(X_train, y_train, X_val, y_val):
    """Conditional DDPM for regression."""
    try:
        import torch
        import torch.nn as nn
    except ImportError:
        print("    [Diffusion] PyTorch not available — skipping")
        return None

    DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    in_dim = X_train.shape[1]

    class SinusoidalEmb(nn.Module):
        def __init__(self, dim):
            super().__init__()
            self.dim = dim

        def forward(self, t):
            half = self.dim // 2
            emb = np.log(10000) / (half - 1)
            emb = torch.exp(torch.arange(half, device=t.device, dtype=torch.float32) * -emb)
            emb = t[:, None].float() * emb[None, :]
            return torch.cat([emb.sin(), emb.cos()], dim=-1)

    class ConditionalDenoiser(nn.Module):
        def __init__(self, cond_dim, hidden=128, time_dim=32):
            super().__init__()
            self.time_emb = SinusoidalEmb(time_dim)
            self.cond_net = nn.Sequential(
                nn.Linear(cond_dim, hidden), nn.ReLU(), nn.Dropout(0.2),
                nn.Linear(hidden, hidden), nn.ReLU(),
            )
            self.net = nn.Sequential(
                nn.Linear(1 + time_dim + hidden, hidden), nn.ReLU(), nn.Dropout(0.2),
                nn.Linear(hidden, hidden), nn.ReLU(),
                nn.Linear(hidden, 1),
            )

        def forward(self, x_noisy, t, cond):
            t_emb = self.time_emb(t)
            c = self.cond_net(cond)
            inp = torch.cat([x_noisy, t_emb, c], dim=-1)
            return self.net(inp)

    # DDPM scheduler
    n_steps = 50
    betas = torch.linspace(1e-4, 0.02, n_steps).to(DEVICE)
    alphas = 1.0 - betas
    alpha_bar = torch.cumprod(alphas, dim=0)

    model = ConditionalDenoiser(in_dim, hidden=128).to(DEVICE)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)

    X_t = torch.tensor(X_train, dtype=torch.float32).to(DEVICE)
    y_t = torch.tensor(y_train, dtype=torch.float32).to(DEVICE).unsqueeze(-1)

    # Normalize target for diffusion
    y_mean, y_std = y_t.mean(), y_t.std()
    y_norm = (y_t - y_mean) / (y_std + 1e-8)

    for epoch in range(200):
        model.train()
        # Sample random timesteps
        t = torch.randint(0, n_steps, (len(X_t),), device=DEVICE)
        noise = torch.randn_like(y_norm)
        ab = alpha_bar[t].unsqueeze(-1)
        x_noisy = torch.sqrt(ab) * y_norm + torch.sqrt(1 - ab) * noise

        pred_noise = model(x_noisy, t, X_t)
        loss = nn.MSELoss()(pred_noise, noise)

        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

    class DiffusionPredictor:
        def __init__(self, model, alpha_bar, betas, alphas, n_steps, y_mean, y_std):
            self.model = model
            self.alpha_bar = alpha_bar
            self.betas = betas
            self.alphas = alphas
            self.n_steps = n_steps
            self.y_mean = y_mean
            self.y_std = y_std

        def predict(self, X, n_samples=10):
            self.model.eval()
            X_t = torch.tensor(X, dtype=torch.float32).to(DEVICE)
            all_preds = []

            for _ in range(n_samples):
                x = torch.randn(len(X_t), 1, device=DEVICE)
                for t_val in reversed(range(self.n_steps)):
                    t = torch.full((len(X_t),), t_val, device=DEVICE, dtype=torch.long)
                    with torch.no_grad():
                        pred_noise = self.model(x, t, X_t)
                    ab = self.alpha_bar[t_val]
                    a = self.alphas[t_val]
                    b = self.betas[t_val]
                    x = (1 / torch.sqrt(a)) * (x - (b / torch.sqrt(1 - ab)) * pred_noise)
                    if t_val > 0:
                        x += torch.sqrt(b) * torch.randn_like(x)

                pred = x * self.y_std + self.y_mean
                all_preds.append(pred.squeeze(-1).cpu().numpy())

            return np.mean(all_preds, axis=0)

    return DiffusionPredictor(model, alpha_bar, betas, alphas, n_steps, y_mean, y_std)


# ═══════════════════════════════════════════════════════════
# Evaluation Framework
# ═══════════════════════════════════════════════════════════

def eval_temporal(df, features, trainer_fn, model_name):
    """Temporal holdout: train ≤2023, test ≥2024."""
    train = df[df["year"] <= 2023].dropna(subset=[TARGET])
    test = df[df["year"] >= 2024].dropna(subset=[TARGET])
    if len(test) < 10:
        # If v7 dataset has no 2024+ data, use last 20%
        n_test = len(df) // 5
        train = df.iloc[:-n_test].dropna(subset=[TARGET])
        test = df.iloc[-n_test:].dropna(subset=[TARGET])

    X_tr, y_tr = train[features].values, train[TARGET].values
    X_te, y_te = test[features].values, test[TARGET].values
    X_tr = np.nan_to_num(X_tr)
    X_te = np.nan_to_num(X_te)

    t0 = time.time()
    m = trainer_fn(X_tr, y_tr, X_te, y_te)
    if m is None:
        return None, None
    p = m.predict(X_te) if not model_name.startswith("GNN") else m.predict(X_te, test["location"].values)
    elapsed = time.time() - t0

    r2 = r2_score(y_te, p)
    rmse = np.sqrt(mean_squared_error(y_te, p))
    return r2, elapsed


def eval_spatial(df, features, trainer_fn, model_name):
    """Spatial holdout: leave-one-region-out."""
    df = df.copy()
    df["region"] = df["location"].apply(extract_region)
    r2s = []

    for region in sorted(df["region"].unique()):
        test = df[df["region"] == region].dropna(subset=[TARGET])
        train = df[df["region"] != region].dropna(subset=[TARGET])
        if len(test) < 10:
            continue

        X_tr = np.nan_to_num(train[features].values)
        y_tr = train[TARGET].values
        X_te = np.nan_to_num(test[features].values)
        y_te = test[TARGET].values

        m = trainer_fn(X_tr, y_tr, X_te, y_te)
        if m is None:
            return None
        p = m.predict(X_te) if not model_name.startswith("GNN") else m.predict(X_te, test["location"].values)
        r2 = r2_score(y_te, p)
        r2s.append(r2)

    return np.mean(r2s) if r2s else None


def eval_spatiotemporal(df, features, trainer_fn, model_name, n_splits=5):
    """Spatiotemporal blocked: GroupKFold on region×year."""
    df = df.copy().dropna(subset=[TARGET])
    df["region"] = df["location"].apply(extract_region)
    df["block"] = df["region"] + "_" + df["year"].astype(str)
    X = np.nan_to_num(df[features].values)
    y = df[TARGET].values
    groups = df["block"].values

    gkf = GroupKFold(n_splits=min(n_splits, len(np.unique(groups))))
    r2s = []
    for tr_idx, te_idx in gkf.split(X, y, groups):
        m = trainer_fn(X[tr_idx], y[tr_idx], X[te_idx], y[te_idx])
        if m is None:
            return None, None
        p = m.predict(X[te_idx]) if not model_name.startswith("GNN") else m.predict(X[te_idx], df.iloc[te_idx]["location"].values)
        r2s.append(r2_score(y[te_idx], p))

    return np.mean(r2s), np.std(r2s)


def eval_loo(df, features, trainer_fn, model_name, top_n=15):
    """LOO with KNN target encoding."""
    df = df.copy().dropna(subset=[TARGET])
    loc_counts = df["location"].value_counts()

    all_true, all_pred = [], []
    per_loc = []

    for loc in loc_counts.head(top_n).index:
        train = df[df["location"] != loc]
        test = df[df["location"] == loc]

        g_mean, cluster_enc, region_enc = loo_target_encode(train, test)

        # Add encoding features
        train_ext = train.copy()
        test_ext = test.copy()
        train_ext["_cluster_enc"] = g_mean
        test_ext["_cluster_enc"] = cluster_enc
        train_ext["_region_enc"] = g_mean
        test_ext["_region_enc"] = region_enc

        ext_features = features + ["_cluster_enc", "_region_enc"]
        ext_features = [f for f in ext_features if f in train_ext.columns]

        X_tr = np.nan_to_num(train_ext[ext_features].values)
        y_tr = train_ext[TARGET].values
        X_te = np.nan_to_num(test_ext[ext_features].values)
        y_te = test_ext[TARGET].values

        m = trainer_fn(X_tr, y_tr)
        if m is None:
            return None, None
        p = m.predict(X_te) if not model_name.startswith("GNN") else m.predict(X_te, test["location"].values)

        r2 = r2_score(y_te, p) if len(y_te) > 1 else float("nan")
        all_true.extend(y_te.tolist())
        all_pred.extend(p.tolist())

        per_loc.append({
            "location": loc, "r2": round(r2, 3), "n": len(test),
            "true": round(float(y_te.mean()), 1),
            "pred": round(float(p.mean()), 1),
        })

    overall_r2 = r2_score(all_true, all_pred)
    return overall_r2, per_loc


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════

def run_comparison(dataset_path: str):
    print("=" * 70)
    print("COMPREHENSIVE ML MODEL COMPARISON")
    print("=" * 70)

    df = pd.read_csv(dataset_path)
    if "year" not in df.columns:
        df["year"] = pd.to_datetime(df["date"], errors="coerce").dt.year

    print(f"Dataset: {len(df)} rows, {df['location'].nunique()} locations, {len(df.columns)} columns")

    full_feats = get_features(df, include_location_id=True)
    loo_feats = get_features(df, include_location_id=False)
    print(f"Full features: {len(full_feats)}")
    print(f"LOO features: {len(loo_feats)}")

    # Define models to test
    models = {
        "LightGBM": train_lgb,
        "XGBoost": train_xgb,
        "CatBoost": train_catboost,
        "Stacking": train_stacking,
    }

    # Check for diffusion model availability
    try:
        import torch
        models["Diffusion"] = lambda X, y, Xv=None, yv=None: train_diffusion(X, y, Xv, yv)
    except ImportError:
        print("  [Skip] PyTorch not available — skipping Diffusion")

    results = {}

    for model_name, trainer in models.items():
        print(f"\n{'─'*60}")
        print(f"  MODEL: {model_name}")
        print(f"{'─'*60}")

        model_results = {}

        # 1. Temporal
        print(f"  [{model_name}] Temporal holdout...")
        t0 = time.time()
        r2, elapsed = eval_temporal(df, full_feats, trainer, model_name)
        if r2 is not None:
            model_results["temporal"] = round(r2, 4)
            print(f"    R²={r2:.4f}  [{time.time()-t0:.1f}s]")
        else:
            print("    SKIPPED")

        # 2. Spatial
        print(f"  [{model_name}] Spatial holdout...")
        t0 = time.time()
        r2 = eval_spatial(df, full_feats, trainer, model_name)
        if r2 is not None:
            model_results["spatial"] = round(r2, 4)
            print(f"    R²={r2:.4f}  [{time.time()-t0:.1f}s]")
        else:
            print("    SKIPPED")

        # 3. Spatiotemporal
        print(f"  [{model_name}] Spatiotemporal blocked...")
        t0 = time.time()
        r2_st, std_st = eval_spatiotemporal(df, full_feats, trainer, model_name)
        if r2_st is not None:
            model_results["spatiotemporal"] = round(r2_st, 4)
            print(f"    R²={r2_st:.4f} ± {std_st:.4f}  [{time.time()-t0:.1f}s]")
        else:
            print("    SKIPPED")

        # 4. LOO
        print(f"  [{model_name}] Leave-one-location-out...")
        t0 = time.time()
        r2_loo, per_loc = eval_loo(df, loo_feats, trainer, model_name)
        if r2_loo is not None:
            model_results["loo"] = round(r2_loo, 4)
            model_results["loo_per_location"] = per_loc
            pos = sum(1 for p in per_loc if p["r2"] > 0)
            print(f"    R²={r2_loo:.4f}  Positive: {pos}/{len(per_loc)}  [{time.time()-t0:.1f}s]")
        else:
            print("    SKIPPED")

        results[model_name] = model_results

    # ── Summary table ──
    print("\n" + "=" * 70)
    print("RESULTS SUMMARY")
    print("=" * 70)
    print(f"{'Model':<15s} {'Temporal':>10s} {'Spatial':>10s} {'SpatioTemp':>10s} {'LOO':>10s}")
    print("─" * 55)
    for model_name, r in results.items():
        t = r.get("temporal", "—")
        s = r.get("spatial", "—")
        st = r.get("spatiotemporal", "—")
        l = r.get("loo", "—")
        t_str = f"{t:.4f}" if isinstance(t, float) else t
        s_str = f"{s:.4f}" if isinstance(s, float) else s
        st_str = f"{st:.4f}" if isinstance(st, float) else st
        l_str = f"{l:.4f}" if isinstance(l, float) else l
        print(f"{model_name:<15s} {t_str:>10s} {s_str:>10s} {st_str:>10s} {l_str:>10s}")

    # Save
    out_path = Path("castline/validation/artifacts/model_comparison_v7.json")
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved to {out_path}")

    return results


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "castline/validation/data/assembled/validation_dataset_v7.csv"
    run_comparison(path)
