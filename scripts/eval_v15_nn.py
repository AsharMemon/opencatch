"""v15 neural network evaluation: site encoder + temporal encoder fusion model.

Architecture:
  1. Site Encoder: morphometric features → 16-dim learned embedding
  2. Temporal Encoder: weather/lag/GDD/photoperiod → 32-dim encoding
  3. Fusion Network: concat(16 + 32) → prediction

Key design choice: the site encoder uses ONLY morphometric features that are
available for unseen locations, making LOO evaluation fair — the network learns
a location quality representation from physical lake characteristics rather
than memorizing location identity.

Compared against CatBoost baseline on the same splits.
"""
import pandas as pd
import numpy as np
from sklearn.metrics import r2_score, mean_absolute_error
from sklearn.preprocessing import StandardScaler
from sklearn.neighbors import NearestNeighbors
from catboost import CatBoostRegressor
from scipy.stats import spearmanr
from itertools import combinations
import warnings
import time

import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader, TensorDataset

warnings.filterwarnings("ignore")

# ═══════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════
TARGET = "median_weight_lb"
ALWAYS_EXCLUDE = {"target_success_score", "tms_id", "event_id"}
LOCATION_IDENTITY = {"loc_enc", "trail_mean_weight", "location_mean_weight",
                     "loc_rolling_3", "baseline_signal"}
META_COLS = {"date", "location", "region", "block", "sat_source",
             "usgs_site_id", "event_name", "tournament_slug",
             "trail", "results_source", "species", "spawn_phase",
             "source"}
LEAKY_COLS = {"loc_mean_enc", "source_enc"}

MIN_LOO_STD = 0.5
MIN_LOO_N = 15

SITE_FEATURES = ["lat", "lon", "area_acres", "is_lake", "max_depth_ft",
                 "shore_dev", "lagos_sdi", "creel_lmb_ratio", "creel_smb_ratio",
                 "creel_cpue_mean", "log_depth", "depth_x_lat", "littoral_ratio",
                 "volume_proxy"]

LOO_EXCLUDE_FEATURES = {"loc_rolling_mean", "loc_rolling_std", "loc_n_prior",
                        "loc_encoding_confidence"}

# KNN morphometric features (subset used for neighbor matching)
LOC_MORPH = ["lat", "lon", "area_acres", "is_lake", "max_depth_ft", "shore_dev",
             "creel_lmb_ratio", "creel_smb_ratio", "creel_cpue_mean"]

PRED_MIN = 1.0
PRED_MAX = 25.0

DEVICE = torch.device("cpu")


def p(msg=""):
    print(msg, flush=True)


# ═══════════════════════════════════════
# METRICS
# ═══════════════════════════════════════
def ndcg_at_k(true_scores, pred_scores, k):
    t, p_ = np.array(true_scores), np.array(pred_scores)
    if len(t) < 2:
        return 1.0
    pred_order = np.argsort(-p_)
    dcg = np.sum(t[pred_order[:k]] / np.log2(np.arange(2, min(k, len(t)) + 2)))
    ideal_order = np.argsort(-t)
    idcg = np.sum(t[ideal_order[:k]] / np.log2(np.arange(2, min(k, len(t)) + 2)))
    return dcg / idcg if idcg > 0 else 1.0


def pairwise_accuracy(true_scores, pred_scores):
    if len(true_scores) < 2:
        return 1.0
    correct = total = 0
    for i, j in combinations(range(len(true_scores)), 2):
        if true_scores[i] == true_scores[j]:
            continue
        total += 1
        if (true_scores[i] > true_scores[j]) == (pred_scores[i] > pred_scores[j]):
            correct += 1
    return correct / total if total > 0 else 1.0


def report_metrics(y_true, y_pred, label=""):
    r2 = r2_score(y_true, y_pred)
    mae = mean_absolute_error(y_true, y_pred)
    rho, _ = spearmanr(y_true, y_pred)
    p(f"  {label}R²={r2:.4f}  MAE={mae:.3f}  Spearman={rho:.3f}  n={len(y_true)}")
    return r2, mae, rho


# ═══════════════════════════════════════
# DATA HELPERS
# ═══════════════════════════════════════
def fill_rolling_mean_gaps(train_df, test_df=None, K=8):
    """Fill loc_rolling_mean NaN gaps using KNN prior from TRAINING data only."""
    morph = [c for c in LOC_MORPH if c in train_df.columns]

    loc_stats = train_df.groupby("location").agg(
        mean_target=(TARGET, "mean"),
        n_events=(TARGET, "count"),
        **{f: (f, "first") for f in morph}
    ).reset_index()

    X = loc_stats[morph].fillna(loc_stats[morph].median()).values
    sc = StandardScaler().fit(X)
    Xs = sc.transform(X)
    nbrs_model = NearestNeighbors(n_neighbors=min(K + 1, len(loc_stats)))
    nbrs_model.fit(Xs)

    knn_prior = {}
    for i, row in loc_stats.iterrows():
        q = Xs[i:i + 1]
        d, ix = nbrs_model.kneighbors(q)
        nbrs = [(dd, j) for dd, j in zip(d[0], ix[0])
                if loc_stats.iloc[j]["location"] != row["location"]][:K]
        if nbrs:
            w = [loc_stats.iloc[j]["n_events"] / (dd + 0.01) for dd, j in nbrs]
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
                        d, ix = nbrs_model.kneighbors(ts, n_neighbors=min(K + 1, len(loc_stats)))
                        nbs = [(dd, int(j)) for dd, j in zip(d[0], ix[0])][:K]
                        w = [loc_stats.iloc[j]["n_events"] / (dd + 0.01) for dd, j in nbs]
                        val = sum(wi * loc_stats.iloc[j]["mean_target"]
                                  for wi, (_, j) in zip(w, nbs)) / sum(w)
                        test_out.loc[loc_mask & test_out["loc_rolling_mean"].isna(),
                                     "loc_rolling_mean"] = val
            test_out["loc_rolling_mean"].fillna(train_df[TARGET].mean(), inplace=True)

    return train_out, test_out


# ═══════════════════════════════════════
# NEURAL NETWORK MODEL
# ═══════════════════════════════════════
class SiteEncoder(nn.Module):
    """Learns a 16-dim embedding from morphometric lake characteristics."""
    def __init__(self, n_site_features, dropout=0.2):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(n_site_features, 64),
            nn.BatchNorm1d(64),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(64, 32),
            nn.BatchNorm1d(32),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(32, 16),
            nn.BatchNorm1d(16),
            nn.ReLU(),
            nn.Dropout(dropout),
        )

    def forward(self, x):
        return self.net(x)


class TemporalEncoder(nn.Module):
    """Encodes weather/lag/GDD/photoperiod features into a 32-dim vector."""
    def __init__(self, n_temporal_features, dropout=0.2):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(n_temporal_features, 128),
            nn.BatchNorm1d(128),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(128, 64),
            nn.BatchNorm1d(64),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(64, 32),
            nn.BatchNorm1d(32),
            nn.ReLU(),
            nn.Dropout(dropout),
        )

    def forward(self, x):
        return self.net(x)


class FusionModel(nn.Module):
    """Site embedding + temporal encoding → prediction."""
    def __init__(self, n_site_features, n_temporal_features, dropout=0.2):
        super().__init__()
        self.site_encoder = SiteEncoder(n_site_features, dropout)
        self.temporal_encoder = TemporalEncoder(n_temporal_features, dropout)
        self.head = nn.Sequential(
            nn.Linear(48, 32),
            nn.BatchNorm1d(32),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(32, 1),
        )

    def forward(self, x_site, x_temporal):
        site_emb = self.site_encoder(x_site)         # (B, 16)
        temp_enc = self.temporal_encoder(x_temporal)   # (B, 32)
        fused = torch.cat([site_emb, temp_enc], dim=1) # (B, 48)
        return self.head(fused).squeeze(-1)            # (B,)


# ═══════════════════════════════════════
# TRAINING LOOP
# ═══════════════════════════════════════
def prepare_tensors(df, site_cols, temporal_cols, site_scaler, temporal_scaler,
                    target_col=TARGET, fit=False):
    """Scale features and produce torch tensors. NaN filled with column median."""
    # Site features
    X_site = df[site_cols].copy()
    if fit:
        site_medians = X_site.median()
        X_site = X_site.fillna(site_medians)
        site_scaler.fit(X_site)
    else:
        site_medians = None
        X_site = X_site.fillna(X_site.median())
    X_site_s = site_scaler.transform(X_site)
    # Replace any remaining NaN (all-NaN columns) with 0
    X_site_s = np.nan_to_num(X_site_s, nan=0.0)

    # Temporal features
    X_temp = df[temporal_cols].copy()
    if fit:
        temp_medians = X_temp.median()
        X_temp = X_temp.fillna(temp_medians)
        temporal_scaler.fit(X_temp)
    else:
        temp_medians = None
        X_temp = X_temp.fillna(X_temp.median())
    X_temp_s = temporal_scaler.transform(X_temp)
    X_temp_s = np.nan_to_num(X_temp_s, nan=0.0)

    t_site = torch.tensor(X_site_s, dtype=torch.float32)
    t_temp = torch.tensor(X_temp_s, dtype=torch.float32)

    y = None
    if target_col in df.columns:
        y = torch.tensor(df[target_col].values, dtype=torch.float32)

    medians = (site_medians, temp_medians) if fit else None
    return t_site, t_temp, y, medians


def train_fusion_model(train_site, train_temp, train_y,
                       val_site, val_temp, val_y,
                       n_site_features, n_temporal_features,
                       epochs=300, lr=1e-3, batch_size=256,
                       patience=30, dropout=0.2, weight_decay=1e-4,
                       verbose=True):
    """Train the fusion model with early stopping on validation loss."""
    model = FusionModel(n_site_features, n_temporal_features, dropout).to(DEVICE)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)
    criterion = nn.MSELoss()

    # DataLoader for training
    train_ds = TensorDataset(train_site, train_temp, train_y)
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True,
                              drop_last=False)

    best_val_loss = float("inf")
    best_state = None
    no_improve = 0

    for epoch in range(epochs):
        # Train
        model.train()
        train_loss_sum = 0.0
        n_batches = 0
        for b_site, b_temp, b_y in train_loader:
            b_site, b_temp, b_y = b_site.to(DEVICE), b_temp.to(DEVICE), b_y.to(DEVICE)
            optimizer.zero_grad()
            pred = model(b_site, b_temp)
            loss = criterion(pred, b_y)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            train_loss_sum += loss.item()
            n_batches += 1
        scheduler.step()

        # Validate
        model.eval()
        with torch.no_grad():
            val_pred = model(val_site.to(DEVICE), val_temp.to(DEVICE))
            val_loss = criterion(val_pred, val_y.to(DEVICE)).item()

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            no_improve = 0
        else:
            no_improve += 1

        if verbose and (epoch + 1) % 50 == 0:
            p(f"    epoch {epoch+1:3d}  train_loss={train_loss_sum/n_batches:.4f}  "
              f"val_loss={val_loss:.4f}  lr={scheduler.get_last_lr()[0]:.6f}")

        if no_improve >= patience:
            if verbose:
                p(f"    early stop at epoch {epoch+1} (best val_loss={best_val_loss:.4f})")
            break

    model.load_state_dict(best_state)
    model.eval()
    return model


def predict_fusion(model, site_t, temp_t):
    """Run inference, return numpy array clipped to valid range."""
    model.eval()
    with torch.no_grad():
        pred = model(site_t.to(DEVICE), temp_t.to(DEVICE)).cpu().numpy()
    return np.clip(pred, PRED_MIN, PRED_MAX)


# ═══════════════════════════════════════
# LOAD DATA
# ═══════════════════════════════════════
p("=" * 60)
p("CASTLINE v15 Neural Network Evaluation")
p("=" * 60)

df = pd.read_csv("castline/validation/data/assembled/validation_dataset_v15.csv",
                 low_memory=False)
df = df[df[TARGET].notna()].copy()
p(f"Dataset: {len(df)} rows, {df.location.nunique()} locations, {len(df.columns)} columns")

# Identify feature sets
all_cols = set(df.columns)
exclude = ALWAYS_EXCLUDE | LOCATION_IDENTITY | META_COLS | LEAKY_COLS | {TARGET}
base_features = sorted(all_cols - exclude)
base_features = [f for f in base_features
                 if df[f].dtype in ["float64", "int64", "float32", "int32"]]

# Site features: morphometric only (available for unseen locations)
site_cols = [f for f in SITE_FEATURES if f in df.columns]

# Temporal features: everything that is NOT a site feature and NOT excluded
temporal_features_all = [f for f in base_features if f not in set(SITE_FEATURES)]

# LOO temporal features: additionally remove rolling location stats
loo_temporal_features = [f for f in temporal_features_all if f not in LOO_EXCLUDE_FEATURES]

# For CatBoost baselines, use the full feature set
catboost_temporal_features = base_features.copy()
catboost_loo_features = [f for f in base_features if f not in LOO_EXCLUDE_FEATURES]

p(f"Site features ({len(site_cols)}): {site_cols}")
p(f"Temporal features: {len(temporal_features_all)}")
p(f"LOO temporal features: {len(loo_temporal_features)}")
p(f"CatBoost temporal features: {len(catboost_temporal_features)}")
p(f"CatBoost LOO features: {len(catboost_loo_features)}")


# ═══════════════════════════════════════
# 1. TEMPORAL HOLDOUT
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
p(f"  Train: {len(tr)} rows, Test: {len(te)} rows")
p(f"  Seen locations in test: {len(te_seen)} ({len(te_seen)/len(te)*100:.0f}%)")
p(f"  Unseen locations in test: {len(te_unseen)} ({len(te_unseen)/len(te)*100:.0f}%)")

# --- CatBoost baseline ---
p("\n  --- CatBoost Baseline ---")
cb_configs = [
    dict(iterations=1500, depth=6, learning_rate=0.03, l2_leaf_reg=3,
         random_seed=42, subsample=0.85, colsample_bylevel=0.8),
    dict(iterations=1200, depth=5, learning_rate=0.03, l2_leaf_reg=5,
         random_seed=123, subsample=0.9, colsample_bylevel=0.85),
    dict(iterations=1000, depth=7, learning_rate=0.05, l2_leaf_reg=2,
         random_seed=456, subsample=0.8, colsample_bylevel=0.75),
]
cb_preds = []
for cfg in cb_configs:
    m = CatBoostRegressor(**cfg, verbose=0)
    m.fit(tr[catboost_temporal_features], tr[TARGET])
    cb_preds.append(m.predict(te[catboost_temporal_features]))
cb_pred = np.clip(np.median(cb_preds, axis=0), PRED_MIN, PRED_MAX)
p("  CatBoost ensemble:")
cb_r2, cb_mae, cb_rho = report_metrics(te[TARGET].values, cb_pred, "Overall  ")
if len(te_seen) > 0:
    report_metrics(te_seen[TARGET].values,
                   cb_pred[te.location.isin(seen_locs).values], "Seen     ")
if len(te_unseen) > 0:
    report_metrics(te_unseen[TARGET].values,
                   cb_pred[~te.location.isin(seen_locs).values], "Unseen   ")

# --- Neural network ---
p("\n  --- Neural Network (Site+Temporal Fusion) ---")

# Use 10% of training as validation for early stopping
tr_sorted = tr.sort_values("date")
val_split = int(len(tr_sorted) * 0.9)
tr_train = tr_sorted.iloc[:val_split].copy()
tr_val = tr_sorted.iloc[val_split:].copy()
p(f"  NN train: {len(tr_train)}, NN val: {len(tr_val)}, NN test: {len(te)}")

# Prepare scalers and tensors
site_scaler = StandardScaler()
temp_scaler = StandardScaler()

train_site_t, train_temp_t, train_y_t, medians = prepare_tensors(
    tr_train, site_cols, temporal_features_all, site_scaler, temp_scaler, fit=True)
site_medians, temp_medians = medians

val_site_t, val_temp_t, val_y_t, _ = prepare_tensors(
    tr_val, site_cols, temporal_features_all, site_scaler, temp_scaler, fit=False)
test_site_t, test_temp_t, _, _ = prepare_tensors(
    te, site_cols, temporal_features_all, site_scaler, temp_scaler, fit=False)

# Train ensemble of 3 models with different seeds
nn_preds = []
for seed in [42, 123, 456]:
    torch.manual_seed(seed)
    np.random.seed(seed)
    p(f"\n  Training model (seed={seed})...")
    model = train_fusion_model(
        train_site_t, train_temp_t, train_y_t,
        val_site_t, val_temp_t, val_y_t,
        n_site_features=len(site_cols),
        n_temporal_features=len(temporal_features_all),
        epochs=300, lr=1e-3, batch_size=256,
        patience=30, dropout=0.2, weight_decay=1e-4,
        verbose=True,
    )
    nn_preds.append(predict_fusion(model, test_site_t, test_temp_t))

nn_pred = np.median(nn_preds, axis=0)
p("\n  Neural Network ensemble (3 seeds):")
nn_r2, nn_mae, nn_rho = report_metrics(te[TARGET].values, nn_pred, "Overall  ")
if len(te_seen) > 0:
    report_metrics(te_seen[TARGET].values,
                   nn_pred[te.location.isin(seen_locs).values], "Seen     ")
if len(te_unseen) > 0:
    report_metrics(te_unseen[TARGET].values,
                   nn_pred[~te.location.isin(seen_locs).values], "Unseen   ")

# --- Blend ---
blend_pred = np.clip(0.5 * cb_pred + 0.5 * nn_pred, PRED_MIN, PRED_MAX)
p("\n  50/50 CatBoost+NN Blend:")
blend_r2, blend_mae, blend_rho = report_metrics(te[TARGET].values, blend_pred, "Overall  ")
if len(te_seen) > 0:
    report_metrics(te_seen[TARGET].values,
                   blend_pred[te.location.isin(seen_locs).values], "Seen     ")
if len(te_unseen) > 0:
    report_metrics(te_unseen[TARGET].values,
                   blend_pred[~te.location.isin(seen_locs).values], "Unseen   ")

p(f"\n  Temporal eval took {time.time()-t0:.0f}s")


# ═══════════════════════════════════════
# 2. LEAVE-ONE-LOCATION-OUT (LOO)
# ═══════════════════════════════════════
p("\n" + "=" * 60)
p("2. LEAVE-ONE-LOCATION-OUT (LOO)")
p("=" * 60)
t0 = time.time()

loc_stats = df.groupby("location").agg(
    n=(TARGET, "count"),
    std=(TARGET, "std"),
    mean=(TARGET, "mean"),
).reset_index()
loc_stats["std"] = loc_stats["std"].fillna(0)

qualifying = loc_stats[(loc_stats["n"] >= MIN_LOO_N) &
                       (loc_stats["std"] >= MIN_LOO_STD)]["location"].tolist()
p(f"  Qualifying locations: {len(qualifying)} (n>={MIN_LOO_N}, std>={MIN_LOO_STD})")

loo_results = []

for i, loc in enumerate(qualifying):
    test_mask = df["location"] == loc
    train_df = df[~test_mask].copy()
    test_df = df[test_mask].copy()
    n_test = len(test_df)

    # --- KNN encoding for loc_rolling_mean in LOO setting ---
    # Build KNN from training locations to fill loc_rolling_mean for held-out loc
    morph = [c for c in LOC_MORPH if c in train_df.columns]
    train_loc_stats = train_df.groupby("location").agg(
        mean_target=(TARGET, "mean"),
        n_events=(TARGET, "count"),
        **{f: (f, "first") for f in morph}
    ).reset_index()

    X_knn = train_loc_stats[morph].fillna(train_loc_stats[morph].median()).values
    sc_knn = StandardScaler().fit(X_knn)
    Xs_knn = sc_knn.transform(X_knn)
    K = 8
    nn_model = NearestNeighbors(n_neighbors=min(K, len(train_loc_stats)))
    nn_model.fit(Xs_knn)

    # KNN prior for held-out location
    test_morph = test_df[morph].fillna(train_loc_stats[morph].median().to_dict()).iloc[0:1].values
    ts_knn = sc_knn.transform(test_morph)
    d_knn, ix_knn = nn_model.kneighbors(ts_knn)
    nbrs = [(dd, int(j)) for dd, j in zip(d_knn[0], ix_knn[0])][:K]
    w_knn = [train_loc_stats.iloc[j]["n_events"] / (dd + 0.01) for dd, j in nbrs]
    knn_mean = sum(wi * train_loc_stats.iloc[j]["mean_target"]
                   for wi, (_, j) in zip(w_knn, nbrs)) / sum(w_knn)

    # Fill loc_rolling_mean for train and test
    train_filled = train_df.copy()
    if "loc_rolling_mean" in train_filled.columns:
        # Fill training NaN gaps with per-location KNN
        for tloc in train_filled.location.unique():
            lmask = (train_filled.location == tloc) & train_filled["loc_rolling_mean"].isna()
            if lmask.any():
                tloc_row = train_filled[train_filled.location == tloc].iloc[0]
                tv = pd.DataFrame([tloc_row])[morph].fillna(
                    train_loc_stats[morph].median().to_dict()).values
                ts2 = sc_knn.transform(tv)
                d2, ix2 = nn_model.kneighbors(ts2)
                n2 = [(dd, int(j)) for dd, j in zip(d2[0], ix2[0])
                       if train_loc_stats.iloc[int(j)]["location"] != tloc][:K]
                if n2:
                    w2 = [train_loc_stats.iloc[j]["n_events"] / (dd + 0.01) for dd, j in n2]
                    val = sum(wi * train_loc_stats.iloc[j]["mean_target"]
                              for wi, (_, j) in zip(w2, n2)) / sum(w2)
                else:
                    val = train_df[TARGET].mean()
                train_filled.loc[lmask, "loc_rolling_mean"] = val
        train_filled["loc_rolling_mean"].fillna(train_df[TARGET].mean(), inplace=True)

    test_filled = test_df.copy()
    if "loc_rolling_mean" in test_filled.columns:
        test_filled["loc_rolling_mean"] = knn_mean

    # LOO does NOT use loc_rolling_mean/std/n_prior/encoding_confidence
    # in the neural network temporal features (already excluded from loo_temporal_features)

    # --- CatBoost LOO ---
    cb_loo = CatBoostRegressor(
        iterations=1000, depth=6, learning_rate=0.03, l2_leaf_reg=3,
        subsample=0.85, colsample_bylevel=0.8, verbose=0, random_seed=42)
    cb_loo.fit(train_filled[catboost_loo_features], train_filled[TARGET])
    cb_loo_pred = np.clip(cb_loo.predict(test_filled[catboost_loo_features]),
                          PRED_MIN, PRED_MAX)
    cb_loo_r2 = r2_score(test_filled[TARGET], cb_loo_pred)

    # --- Neural Network LOO ---
    # Split train into train/val (90/10 by date)
    train_s = train_filled.sort_values("date")
    val_cut = int(len(train_s) * 0.9)
    tr_nn = train_s.iloc[:val_cut]
    va_nn = train_s.iloc[val_cut:]

    site_sc = StandardScaler()
    temp_sc = StandardScaler()

    tr_site, tr_temp, tr_y, _ = prepare_tensors(
        tr_nn, site_cols, loo_temporal_features, site_sc, temp_sc, fit=True)
    va_site, va_temp, va_y, _ = prepare_tensors(
        va_nn, site_cols, loo_temporal_features, site_sc, temp_sc, fit=False)
    te_site, te_temp, _, _ = prepare_tensors(
        test_filled, site_cols, loo_temporal_features, site_sc, temp_sc, fit=False)

    torch.manual_seed(42)
    np.random.seed(42)
    nn_loo_model = train_fusion_model(
        tr_site, tr_temp, tr_y,
        va_site, va_temp, va_y,
        n_site_features=len(site_cols),
        n_temporal_features=len(loo_temporal_features),
        epochs=200, lr=1e-3, batch_size=256,
        patience=20, dropout=0.25, weight_decay=1e-4,
        verbose=False,
    )
    nn_loo_pred = predict_fusion(nn_loo_model, te_site, te_temp)
    nn_loo_r2 = r2_score(test_filled[TARGET], nn_loo_pred)

    # Blend
    blend_loo_pred = np.clip(0.5 * cb_loo_pred + 0.5 * nn_loo_pred, PRED_MIN, PRED_MAX)
    blend_loo_r2 = r2_score(test_filled[TARGET], blend_loo_pred)

    loo_results.append({
        "location": loc,
        "n": n_test,
        "std": test_df[TARGET].std(),
        "mean": test_df[TARGET].mean(),
        "cb_r2": cb_loo_r2,
        "nn_r2": nn_loo_r2,
        "blend_r2": blend_loo_r2,
        "cb_mae": mean_absolute_error(test_filled[TARGET], cb_loo_pred),
        "nn_mae": mean_absolute_error(test_filled[TARGET], nn_loo_pred),
        "blend_mae": mean_absolute_error(test_filled[TARGET], blend_loo_pred),
    })

    if (i + 1) % 5 == 0 or (i + 1) == len(qualifying):
        running = pd.DataFrame(loo_results)
        p(f"  [{i+1}/{len(qualifying)}]  "
          f"CB mean R²={running['cb_r2'].mean():.4f}  "
          f"NN mean R²={running['nn_r2'].mean():.4f}  "
          f"Blend mean R²={running['blend_r2'].mean():.4f}")

# Final LOO summary
loo_df = pd.DataFrame(loo_results)

p(f"\n  LOO eval took {time.time()-t0:.0f}s")
p(f"\n  LOO Summary ({len(loo_df)} locations):")
p(f"  {'Metric':<20s} {'CatBoost':>10s} {'NeuralNet':>10s} {'Blend':>10s}")
p(f"  {'-'*50}")
p(f"  {'Mean R²':<20s} {loo_df['cb_r2'].mean():>10.4f} {loo_df['nn_r2'].mean():>10.4f} {loo_df['blend_r2'].mean():>10.4f}")
p(f"  {'Median R²':<20s} {loo_df['cb_r2'].median():>10.4f} {loo_df['nn_r2'].median():>10.4f} {loo_df['blend_r2'].median():>10.4f}")
p(f"  {'Mean MAE':<20s} {loo_df['cb_mae'].mean():>10.3f} {loo_df['nn_mae'].mean():>10.3f} {loo_df['blend_mae'].mean():>10.3f}")
p(f"  {'R²>0 count':<20s} {(loo_df['cb_r2']>0).sum():>10d} {(loo_df['nn_r2']>0).sum():>10d} {(loo_df['blend_r2']>0).sum():>10d}")
p(f"  {'R²>0.3 count':<20s} {(loo_df['cb_r2']>0.3).sum():>10d} {(loo_df['nn_r2']>0.3).sum():>10d} {(loo_df['blend_r2']>0.3).sum():>10d}")

# Which model wins more often?
nn_wins = (loo_df["nn_r2"] > loo_df["cb_r2"]).sum()
cb_wins = (loo_df["cb_r2"] > loo_df["nn_r2"]).sum()
blend_best = ((loo_df["blend_r2"] >= loo_df["cb_r2"]) &
              (loo_df["blend_r2"] >= loo_df["nn_r2"])).sum()
p(f"\n  Head-to-head: NN wins {nn_wins}, CatBoost wins {cb_wins}, Blend best {blend_best}")

# Weighted mean R² (weighted by location n)
w = loo_df["n"].values
p(f"  Weighted mean R²: CB={np.average(loo_df['cb_r2'], weights=w):.4f}  "
  f"NN={np.average(loo_df['nn_r2'], weights=w):.4f}  "
  f"Blend={np.average(loo_df['blend_r2'], weights=w):.4f}")

p("\n" + "=" * 60)
p("DONE")
p("=" * 60)
