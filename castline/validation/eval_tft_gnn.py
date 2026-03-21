"""TFT and GNN evaluation on v7 dataset (GPU).

Temporal Fusion Transformer + GraphSAGE GNN comparison.
"""
import json
import sys
import time
import warnings

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from sklearn.metrics import r2_score
from sklearn.model_selection import GroupKFold
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")

TARGET = "median_weight_lb"
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Device: {DEVICE}")

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
         "W": "CA OR WA ID MT WY NV HI AK".split()}
    for region, states in r.items():
        if st in states:
            return region
    return "OT"


def loo_target_encode(train_df, test_df):
    g_mean = train_df[TARGET].mean()
    cluster_feats = ["lat", "lon", "area_acres", "max_depth_ft", "is_lake",
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
        cluster_enc = float(np.average(loc_targets.iloc[idxs[0]].values, weights=weights))

    test_region = extract_region(test_df["location"].iloc[0])
    train_copy = train_df.copy()
    train_copy["_region"] = train_copy["location"].apply(extract_region)
    region_means = train_copy.groupby("_region")[TARGET].mean()
    region_enc = region_means.get(test_region, g_mean)

    return g_mean, cluster_enc, region_enc


# ═══════════════════════════════════════════════════════════
# TFT Model (Simplified for tabular regression)
# ═══════════════════════════════════════════════════════════

class GatedResidualNetwork(nn.Module):
    def __init__(self, input_dim, hidden_dim, output_dim, dropout=0.1):
        super().__init__()
        self.fc1 = nn.Linear(input_dim, hidden_dim)
        self.fc2 = nn.Linear(hidden_dim, output_dim)
        self.gate = nn.Sequential(
            nn.Linear(hidden_dim, output_dim),
            nn.Sigmoid()
        )
        self.layernorm = nn.LayerNorm(output_dim)
        self.dropout = nn.Dropout(dropout)
        self.skip = nn.Linear(input_dim, output_dim) if input_dim != output_dim else nn.Identity()

    def forward(self, x):
        h = F.elu(self.fc1(x))
        h = self.dropout(h)
        out = self.fc2(h)
        gate = self.gate(h)
        return self.layernorm(self.skip(x) + gate * out)


class SimplifiedTFT(nn.Module):
    """Simplified TFT for tabular regression (no sequence dimension).

    Uses variable selection, GRN processing, and multi-head attention
    on static features grouped by category.
    """
    def __init__(self, input_dim, hidden_dim=64, n_heads=4, dropout=0.1):
        super().__init__()
        self.hidden_dim = hidden_dim

        # Variable selection
        self.var_selector = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, input_dim),
            nn.Softmax(dim=-1)
        )

        # Feature embedding
        self.feature_embed = nn.Linear(input_dim, hidden_dim)

        # GRN layers
        self.grn1 = GatedResidualNetwork(hidden_dim, hidden_dim * 2, hidden_dim, dropout)
        self.grn2 = GatedResidualNetwork(hidden_dim, hidden_dim * 2, hidden_dim, dropout)

        # Self-attention (treat features as "time steps" via reshaping)
        self.attention = nn.MultiheadAttention(hidden_dim, n_heads, dropout=dropout, batch_first=True)
        self.attn_norm = nn.LayerNorm(hidden_dim)

        # Output head
        self.output_head = nn.Sequential(
            GatedResidualNetwork(hidden_dim, hidden_dim * 2, hidden_dim, dropout),
            nn.Linear(hidden_dim, 1)
        )

    def forward(self, x):
        # Variable selection
        var_weights = self.var_selector(x)
        x_selected = x * var_weights

        # Embed
        h = self.feature_embed(x_selected)

        # GRN processing
        h = self.grn1(h)

        # Self-attention (add fake sequence dim)
        h_seq = h.unsqueeze(1)  # (batch, 1, hidden)
        attn_out, _ = self.attention(h_seq, h_seq, h_seq)
        h = self.attn_norm(h + attn_out.squeeze(1))

        # More GRN
        h = self.grn2(h)

        return self.output_head(h).squeeze(-1)


def train_tft(X_train, y_train, X_val=None, y_val=None, epochs=200, lr=1e-3):
    in_dim = X_train.shape[1]
    model = SimplifiedTFT(in_dim, hidden_dim=64, n_heads=4, dropout=0.15).to(DEVICE)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)

    X_t = torch.tensor(X_train, dtype=torch.float32).to(DEVICE)
    y_t = torch.tensor(y_train, dtype=torch.float32).to(DEVICE)

    if X_val is not None:
        X_v = torch.tensor(X_val, dtype=torch.float32).to(DEVICE)
        y_v = torch.tensor(y_val, dtype=torch.float32).to(DEVICE)

    best_val_loss = float("inf")
    patience = 30
    patience_counter = 0
    best_state = None

    for epoch in range(epochs):
        model.train()
        # Mini-batch training
        batch_size = 256
        perm = torch.randperm(len(X_t))
        epoch_loss = 0
        n_batches = 0
        for i in range(0, len(X_t), batch_size):
            idx = perm[i:i+batch_size]
            pred = model(X_t[idx])
            loss = nn.MSELoss()(pred, y_t[idx])
            optimizer.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            epoch_loss += loss.item()
            n_batches += 1

        scheduler.step()

        # Validate
        if X_val is not None and epoch % 5 == 0:
            model.eval()
            with torch.no_grad():
                val_pred = model(X_v)
                val_loss = nn.MSELoss()(val_pred, y_v).item()

            if val_loss < best_val_loss:
                best_val_loss = val_loss
                patience_counter = 0
                best_state = {k: v.clone() for k, v in model.state_dict().items()}
            else:
                patience_counter += 1
                if patience_counter >= patience // 5:
                    break

    if best_state is not None:
        model.load_state_dict(best_state)

    class TFTPredictor:
        def __init__(self, model):
            self.model = model
        def predict(self, X):
            self.model.eval()
            with torch.no_grad():
                X_t = torch.tensor(X, dtype=torch.float32).to(DEVICE)
                return self.model(X_t).cpu().numpy()

    return TFTPredictor(model)


# ═══════════════════════════════════════════════════════════
# GNN Model
# ═══════════════════════════════════════════════════════════

def train_gnn_model(X_train, y_train, X_val, y_val, train_locs, val_locs, all_df, features):
    try:
        from torch_geometric.nn import SAGEConv
    except ImportError:
        print("  [GNN] PyG not available — skipping")
        return None

    # Build location graph
    unique_locs = list(all_df.location.unique())
    loc_to_idx = {l: i for i, l in enumerate(unique_locs)}

    # Node features: median per location
    node_features = np.zeros((len(unique_locs), len(features)))
    for i, loc in enumerate(unique_locs):
        mask = all_df.location == loc
        if mask.any():
            vals = all_df.loc[mask, features].values
            node_features[i] = np.nanmedian(vals, axis=0)
    node_features = np.nan_to_num(node_features)

    # K-nearest neighbor edges
    nn_model = NearestNeighbors(n_neighbors=min(8, len(unique_locs)))
    nn_model.fit(node_features)
    _, indices = nn_model.kneighbors(node_features)

    edge_src, edge_dst = [], []
    for i, neighbors in enumerate(indices):
        for j in neighbors[1:]:
            edge_src.extend([i, j])
            edge_dst.extend([j, i])

    edge_index = torch.tensor([edge_src, edge_dst], dtype=torch.long).to(DEVICE)
    x_nodes = torch.tensor(node_features, dtype=torch.float32).to(DEVICE)

    class GNN(nn.Module):
        def __init__(self, in_dim, hidden=32):
            super().__init__()
            self.conv1 = SAGEConv(in_dim, hidden)
            self.conv2 = SAGEConv(hidden, hidden)
            self.dropout = nn.Dropout(0.3)
            self.head = nn.Sequential(
                nn.Linear(hidden + in_dim, hidden),
                nn.ReLU(), nn.Dropout(0.3),
                nn.Linear(hidden, 1)
            )

        def forward(self, x_nodes, edge_index, event_feats, loc_indices):
            h = torch.relu(self.conv1(x_nodes, edge_index))
            h = self.dropout(h)
            h = torch.relu(self.conv2(h, edge_index))
            loc_emb = h[loc_indices]
            combined = torch.cat([loc_emb, event_feats], dim=1)
            return self.head(combined).squeeze(-1)

    model = GNN(len(features), hidden=32).to(DEVICE)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001, weight_decay=1e-4)

    X_t = torch.tensor(X_train, dtype=torch.float32).to(DEVICE)
    y_t = torch.tensor(y_train, dtype=torch.float32).to(DEVICE)
    train_loc_idx = torch.tensor([loc_to_idx.get(l, 0) for l in train_locs], dtype=torch.long).to(DEVICE)

    X_v = torch.tensor(X_val, dtype=torch.float32).to(DEVICE)
    y_v = torch.tensor(y_val, dtype=torch.float32).to(DEVICE)
    val_loc_idx = torch.tensor([loc_to_idx.get(l, 0) for l in val_locs], dtype=torch.long).to(DEVICE)

    best_val_loss = float("inf")
    patience_counter = 0

    for epoch in range(300):
        model.train()
        pred = model(x_nodes, edge_index, X_t, train_loc_idx)
        loss = nn.MSELoss()(pred, y_t)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        if epoch % 10 == 0:
            model.eval()
            with torch.no_grad():
                val_pred = model(x_nodes, edge_index, X_v, val_loc_idx)
                val_loss = nn.MSELoss()(val_pred, y_v).item()
            if val_loss < best_val_loss:
                best_val_loss = val_loss
                patience_counter = 0
            else:
                patience_counter += 1
                if patience_counter > 5:
                    break

    class GNNPredictor:
        def __init__(self, model, x_nodes, edge_index, loc_to_idx):
            self.model = model
            self.x_nodes = x_nodes
            self.edge_index = edge_index
            self.loc_to_idx = loc_to_idx

        def predict_with_locs(self, X, locations):
            self.model.eval()
            with torch.no_grad():
                X_t = torch.tensor(X, dtype=torch.float32).to(DEVICE)
                loc_idx = torch.tensor([self.loc_to_idx.get(l, 0) for l in locations], dtype=torch.long).to(DEVICE)
                return self.model(self.x_nodes, self.edge_index, X_t, loc_idx).cpu().numpy()

    return GNNPredictor(model, x_nodes, edge_index, loc_to_idx)


# ═══════════════════════════════════════════════════════════
# Evaluation
# ═══════════════════════════════════════════════════════════

def eval_model(df, features, model_name, trainer_fn, use_locs=False):
    results = {}

    # 1. Temporal
    print(f"  [{model_name}] Temporal holdout...", flush=True)
    train = df[df["year"] <= 2023].dropna(subset=[TARGET])
    test = df[df["year"] >= 2024].dropna(subset=[TARGET])
    if len(test) < 10:
        n_test = len(df) // 5
        train = df.iloc[:-n_test].dropna(subset=[TARGET])
        test = df.iloc[-n_test:].dropna(subset=[TARGET])

    X_tr = np.nan_to_num(train[features].values)
    y_tr = train[TARGET].values
    X_te = np.nan_to_num(test[features].values)
    y_te = test[TARGET].values

    t0 = time.time()
    if use_locs:
        m = trainer_fn(X_tr, y_tr, X_te, y_te, train.location.values, test.location.values, df, features)
    else:
        m = trainer_fn(X_tr, y_tr, X_te, y_te)
    if m is None:
        return None

    p = m.predict_with_locs(X_te, test.location.values) if use_locs else m.predict(X_te)
    r2 = r2_score(y_te, p)
    results["temporal"] = round(r2, 4)
    print(f"    R²={r2:.4f}  [{time.time()-t0:.1f}s]", flush=True)

    # 2. Spatial
    print(f"  [{model_name}] Spatial holdout...", flush=True)
    df_c = df.copy()
    df_c["region"] = df_c.location.apply(extract_region)
    r2s = []
    for region in sorted(df_c.region.unique()):
        test = df_c[df_c.region == region].dropna(subset=[TARGET])
        train = df_c[df_c.region != region].dropna(subset=[TARGET])
        if len(test) < 10:
            continue
        X_tr = np.nan_to_num(train[features].values)
        X_te = np.nan_to_num(test[features].values)
        if use_locs:
            m = trainer_fn(X_tr, train[TARGET].values, X_te, test[TARGET].values,
                          train.location.values, test.location.values, df, features)
        else:
            m = trainer_fn(X_tr, train[TARGET].values, X_te, test[TARGET].values)
        if m is None:
            continue
        p = m.predict_with_locs(X_te, test.location.values) if use_locs else m.predict(X_te)
        r2s.append(r2_score(test[TARGET].values, p))
    if r2s:
        results["spatial"] = round(np.mean(r2s), 4)
        print(f"    R²={np.mean(r2s):.4f}", flush=True)

    # 3. Spatiotemporal
    print(f"  [{model_name}] Spatiotemporal...", flush=True)
    df_c = df.copy().dropna(subset=[TARGET])
    df_c["region"] = df_c.location.apply(extract_region)
    df_c["block"] = df_c.region + "_" + df_c.year.astype(str)
    X = np.nan_to_num(df_c[features].values)
    y = df_c[TARGET].values
    gkf = GroupKFold(n_splits=5)
    r2s = []
    for tr_idx, te_idx in gkf.split(X, y, df_c.block.values):
        if use_locs:
            m = trainer_fn(X[tr_idx], y[tr_idx], X[te_idx], y[te_idx],
                          df_c.iloc[tr_idx].location.values, df_c.iloc[te_idx].location.values, df, features)
        else:
            m = trainer_fn(X[tr_idx], y[tr_idx], X[te_idx], y[te_idx])
        if m is None:
            continue
        p = m.predict_with_locs(X[te_idx], df_c.iloc[te_idx].location.values) if use_locs else m.predict(X[te_idx])
        r2s.append(r2_score(y[te_idx], p))
    if r2s:
        results["spatiotemporal"] = round(np.mean(r2s), 4)
        print(f"    R²={np.mean(r2s):.4f}", flush=True)

    # 4. LOO
    print(f"  [{model_name}] LOO...", flush=True)
    loc_counts = df.dropna(subset=[TARGET]).location.value_counts()
    all_true, all_pred = [], []
    loo_feats = [f for f in features if f not in LOCATION_IDENTITY]

    for loc in loc_counts.head(15).index:
        train = df[df.location != loc].dropna(subset=[TARGET])
        test = df[df.location == loc].dropna(subset=[TARGET])
        g_mean, cluster_enc, region_enc = loo_target_encode(train, test)

        train_ext = train.copy()
        test_ext = test.copy()
        train_ext["_cluster_enc"] = g_mean
        test_ext["_cluster_enc"] = cluster_enc
        train_ext["_region_enc"] = g_mean
        test_ext["_region_enc"] = region_enc

        ext_feats = loo_feats + ["_cluster_enc", "_region_enc"]
        ext_feats = [f for f in ext_feats if f in train_ext.columns]

        X_tr = np.nan_to_num(train_ext[ext_feats].values)
        X_te = np.nan_to_num(test_ext[ext_feats].values)

        if use_locs:
            m = trainer_fn(X_tr, train[TARGET].values, X_te, test[TARGET].values,
                          train.location.values, test.location.values, df, ext_feats)
        else:
            m = trainer_fn(X_tr, train[TARGET].values, X_te, test[TARGET].values)
        if m is None:
            continue
        p = m.predict_with_locs(X_te, test.location.values) if use_locs else m.predict(X_te)

        r2 = r2_score(test[TARGET].values, p) if len(test) > 1 else float("nan")
        all_true.extend(test[TARGET].values.tolist())
        all_pred.extend(p.tolist())

    if all_true:
        overall_r2 = r2_score(all_true, all_pred)
        results["loo"] = round(overall_r2, 4)
        pos = sum(1 for t, p in zip(all_true, all_pred) if abs(t - p) < abs(t - np.mean(all_true)))
        print(f"    R²={overall_r2:.4f}", flush=True)

    return results


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "validation_dataset_v7.csv"
    df = pd.read_csv(path, low_memory=False)
    if "year" not in df.columns:
        df["year"] = pd.to_datetime(df["date"], errors="coerce").dt.year

    print(f"Dataset: {len(df)} rows, {df.location.nunique()} locations", flush=True)
    features = get_features(df, include_location_id=True)
    print(f"Features: {len(features)}", flush=True)

    all_results = {}

    # TFT
    print("\n" + "=" * 60, flush=True)
    print("  MODEL: TFT (Temporal Fusion Transformer)", flush=True)
    print("=" * 60, flush=True)
    tft_results = eval_model(df, features, "TFT", train_tft, use_locs=False)
    if tft_results:
        all_results["TFT"] = tft_results

    # GNN
    print("\n" + "=" * 60, flush=True)
    print("  MODEL: GNN (GraphSAGE)", flush=True)
    print("=" * 60, flush=True)
    gnn_results = eval_model(df, features, "GNN", train_gnn_model, use_locs=True)
    if gnn_results:
        all_results["GNN"] = gnn_results

    # Summary
    print("\n" + "=" * 60, flush=True)
    print("RESULTS", flush=True)
    print("=" * 60, flush=True)
    print(f"{'Model':<15s} {'Temporal':>10s} {'Spatial':>10s} {'SpatioTemp':>10s} {'LOO':>10s}", flush=True)
    for name, r in all_results.items():
        t = f"{r.get('temporal', '—')}" if isinstance(r.get('temporal'), float) else "—"
        s = f"{r.get('spatial', '—')}" if isinstance(r.get('spatial'), float) else "—"
        st = f"{r.get('spatiotemporal', '—')}" if isinstance(r.get('spatiotemporal'), float) else "—"
        l = f"{r.get('loo', '—')}" if isinstance(r.get('loo'), float) else "—"
        print(f"{name:<15s} {t:>10s} {s:>10s} {st:>10s} {l:>10s}", flush=True)

    with open("tft_gnn_results.json", "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nSaved to tft_gnn_results.json", flush=True)
