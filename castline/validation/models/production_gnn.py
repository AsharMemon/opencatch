"""Production GNN for CASTLINE catch-rate prediction.

Solves the LOO (leave-one-location-out) generalization problem by learning
spatial relationships between fishing locations via a Graph Neural Network.

Architecture
------------
1. **Location Graph**: Nodes = locations, edges = geographic proximity (KNN)
   + morphometric similarity (similar area/depth).
2. **Node Features**: Creel survey data, morphometry, ecoregion — things that
   generalize to unseen locations.
3. **Event Features**: Environmental conditions at time of tournament.
4. **GraphSAGE**: 2-layer inductive GNN that can generalize to unseen nodes.
5. **Prediction**: GNN-encoded location embedding + event features -> MLP head.

Key insight: GraphSAGE learns aggregation *functions* (mean-pool of neighbor
features), not per-node embeddings. So at test time, a new location can be
inserted into the graph and get a meaningful embedding from its neighbors.

Designed for CPU training on 1275 rows / 258 locations in < 5 minutes.
"""
from __future__ import annotations

import json
import math
import sys
import time
import warnings
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.preprocessing import StandardScaler
from torch_geometric.nn import SAGEConv

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EARTH_RADIUS_KM = 6_371.0
TARGET = "median_weight_lb"

# Node features: properties of the LOCATION (generalize to unseen locations).
# Ordered by correlation with target to front-load signal.
NODE_FEATURES = [
    # Strongest spatial/climate signals (|corr| > 0.3)
    "lat", "lon",
    "latitude_growth_potential",
    "growing_degree_proxy",
    "ecoregion_mean_weight", "ecoregion_mean_cpue",
    "max_depth_ft",
    # Moderate signals
    "ecoregion_n_surveys", "n_nearby_creel_surveys",
    "shad_habitat_score",
    "regional_cpue_100km", "regional_weight_100km",
    # Morphometric
    "area_acres", "shore_dev",
    "morphometric_productivity_score",
    "productivity_x_latitude",
    "is_lake",
    # Water type
    "wtype_river", "wtype_reservoir", "wtype_natural_lake",
    "reservoir_score",
]

# Event features: conditions at time of tournament (vary per event)
EVENT_FEATURES = [
    # Core environmental
    "water_temp_c", "air_temp_c",
    "pressure_mb", "pressure_delta_6h",
    "wind_speed_kph", "wind_dir_cos",
    "cloud_cover_pct", "precip_24h_mm",
    "discharge_cfs", "flow_delta_24h_pct", "discharge_pct_of_30d",
    "gage_height_ft", "gage_stability_7d",
    # Water quality
    "dissolved_oxygen_mgL", "ph", "turbidity_fnu",
    # Temperature derivatives
    "water_temp_anomaly", "water_temp_estimated",
    "water_temp_7d_mean", "water_temp_30d_trend",
    "water_temp_x_flow", "cumulative_degree_days",
    # Temporal / seasonal
    "day_length_hours", "season_cos", "season_sin",
    "year",
    "moon_illumination_pct", "solunar_score",
    "moon_phase",
    # Fish biology
    "spawn_phase", "spawn_progress",
    "metabolic_rate_index", "feeding_window_score",
    "do_comfort_index", "do_estimated_mgL",
    "pressure_phase", "pressure_fishing_quality",
    "photoperiod_change_rate", "light_penetration_index",
    "season_quality_index", "seasonal_pattern_phase",
    "conditions_stability_index", "wind_mixing_index",
    # Lake features
    "turnover_proximity", "thermal_stability",
    "wind_fetch_score", "windblown_quality",
    "pressure_fishing_score", "lake_solunar_boost",
    "level_trend_ft_per_day", "level_anomaly_ft",
    # Interaction
    "wind_lake_interaction", "pressure_solunar_interaction",
    "depth_stability_interaction",
    # IV features
    "discharge_cfs_current", "discharge_cfs_delta_3h",
    "discharge_cfs_delta_24h", "discharge_cfs_cv_24h",
    "gage_height_ft_current", "gage_height_ft_delta_3h",
    "water_temp_c_current", "water_temp_c_delta_3h",
    "discharge_spike_ratio",
    # Satellite
    "sat_air_temp_mean_c", "sat_estimated_water_temp_c",
    "sat_solar_radiation_mj",
]


# ---------------------------------------------------------------------------
# Vectorized pairwise haversine
# ---------------------------------------------------------------------------

def _pairwise_geo_dist(lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    """Vectorized pairwise haversine distance matrix (km)."""
    lat_r = np.radians(lats)
    lon_r = np.radians(lons)
    dlat = lat_r[:, None] - lat_r[None, :]
    dlon = lon_r[:, None] - lon_r[None, :]
    a = (np.sin(dlat / 2) ** 2 +
         np.cos(lat_r[:, None]) * np.cos(lat_r[None, :]) * np.sin(dlon / 2) ** 2)
    a = np.clip(a, 0, 1)
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_graph(
    loc_df: pd.DataFrame,
    k_geo: int = 8,
    k_morpho: int = 4,
    node_scaler: Optional[StandardScaler] = None,
    node_medians: Optional[np.ndarray] = None,
    exclude_locations: Optional[set[str]] = None,
) -> tuple[torch.Tensor, torch.Tensor, list[str], StandardScaler, np.ndarray]:
    """Build a location graph with KNN edges (geographic + morphometric).

    Returns
    -------
    node_features : Tensor (N, F_node)
    edge_index : LongTensor (2, E)
    location_ids : list[str]
    node_scaler : StandardScaler (fitted)
    node_medians : ndarray — column medians for imputation
    """
    df = loc_df.copy().reset_index(drop=True)
    if exclude_locations:
        df = df[~df["location"].isin(exclude_locations)].reset_index(drop=True)

    n = len(df)
    location_ids = df["location"].tolist()

    # Extract node features
    avail_node_feats = [f for f in NODE_FEATURES if f in df.columns]
    node_feat_np = df[avail_node_feats].values.astype(np.float64)

    # Impute NaN
    if node_medians is None:
        node_medians = np.nanmedian(node_feat_np, axis=0)
    for c in range(node_feat_np.shape[1]):
        mask = np.isnan(node_feat_np[:, c])
        med = node_medians[c] if not np.isnan(node_medians[c]) else 0.0
        node_feat_np[mask, c] = med

    # Standardize
    if node_scaler is None:
        node_scaler = StandardScaler()
        node_feat_np = node_scaler.fit_transform(node_feat_np)
    else:
        node_feat_np = node_scaler.transform(node_feat_np)

    node_features = torch.tensor(node_feat_np, dtype=torch.float32)

    # Lat/lon for distance calculation
    lats = df["lat"].values.astype(np.float64)
    lons = df["lon"].values.astype(np.float64)
    lat_med = np.nanmedian(lats)
    lon_med = np.nanmedian(lons)
    lats = np.where(np.isnan(lats), lat_med, lats)
    lons = np.where(np.isnan(lons), lon_med, lons)

    geo_dist = _pairwise_geo_dist(lats, lons)
    np.fill_diagonal(geo_dist, np.inf)

    src_list, dst_list = [], []

    # Geographic KNN
    effective_k_geo = min(k_geo, n - 1)
    if effective_k_geo > 0:
        geo_neighbors = np.argsort(geo_dist, axis=1)[:, :effective_k_geo]
        for i in range(n):
            for j in geo_neighbors[i]:
                src_list.append(i)
                dst_list.append(int(j))

    # Morphometric KNN
    morpho_cols = ["area_acres", "max_depth_ft", "shore_dev"]
    morpho_avail = [c for c in morpho_cols if c in df.columns]
    effective_k_morpho = min(k_morpho, n - 1)
    if morpho_avail and effective_k_morpho > 0:
        morpho_vals = df[morpho_avail].values.astype(np.float64)
        for ci, col in enumerate(morpho_avail):
            if col in ("area_acres", "max_depth_ft"):
                morpho_vals[:, ci] = np.log1p(np.nan_to_num(morpho_vals[:, ci], nan=0.0))
            mask = np.isnan(morpho_vals[:, ci])
            med = np.nanmedian(morpho_vals[:, ci])
            morpho_vals[mask, ci] = med if not np.isnan(med) else 0.0

        ms = StandardScaler()
        morpho_std = ms.fit_transform(morpho_vals)
        diff = morpho_std[:, None, :] - morpho_std[None, :, :]
        morpho_dist = np.sqrt(np.sum(diff ** 2, axis=2))
        np.fill_diagonal(morpho_dist, np.inf)

        morpho_neighbors = np.argsort(morpho_dist, axis=1)[:, :effective_k_morpho]
        for i in range(n):
            for j in morpho_neighbors[i]:
                src_list.append(i)
                dst_list.append(int(j))

    # Make undirected and deduplicate
    edge_set = set()
    for s, d in zip(src_list, dst_list):
        edge_set.add((s, d))
        edge_set.add((d, s))

    if edge_set:
        edges = sorted(edge_set)
        edge_index = torch.tensor(
            [[e[0] for e in edges], [e[1] for e in edges]], dtype=torch.long
        )
    else:
        edge_index = torch.zeros((2, 0), dtype=torch.long)

    return node_features, edge_index, location_ids, node_scaler, node_medians


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class CastlineGNN(nn.Module):
    """GraphSAGE-based model for fishing catch-rate prediction.

    Dual-path architecture:
    1. **Location MLP path**: Raw node features -> "location quality prior"
       (big deep northern lakes catch more). Works even without graph info.
    2. **GNN path**: GraphSAGE refines location representation via neighbors.
    3. **Event path**: Environmental conditions encoder.
    4. **Fusion**: Combines all three to predict catch rate.

    GraphSAGE is critical for LOO: it learns aggregation *functions*
    (mean-pool of neighbor features), not per-node embeddings.
    """

    def __init__(
        self,
        n_node_features: int,
        n_event_features: int,
        hidden_gnn: int = 32,
        hidden_mlp: int = 48,
        dropout: float = 0.05,
    ):
        super().__init__()

        # --- Direct location MLP (no message passing, always works) ---
        self.loc_mlp = nn.Sequential(
            nn.Linear(n_node_features, hidden_gnn),
            nn.ReLU(),
            nn.Linear(hidden_gnn, hidden_gnn),
        )

        # --- GraphSAGE layers ---
        self.sage1 = SAGEConv(n_node_features, hidden_gnn)
        self.sage2 = SAGEConv(hidden_gnn, hidden_gnn)
        self.skip_proj = (
            nn.Linear(n_node_features, hidden_gnn, bias=False)
            if n_node_features != hidden_gnn
            else nn.Identity()
        )
        self.ln1 = nn.LayerNorm(hidden_gnn)
        self.ln2 = nn.LayerNorm(hidden_gnn)

        # --- Event encoder ---
        self.event_encoder = nn.Sequential(
            nn.Linear(n_event_features, hidden_mlp),
            nn.ReLU(),
            nn.Linear(hidden_mlp, hidden_gnn),
        )

        # --- Fusion head ---
        # GNN (hidden_gnn) + loc_mlp (hidden_gnn) + event (hidden_gnn)
        fusion_dim = hidden_gnn * 3
        self.head = nn.Sequential(
            nn.Linear(fusion_dim, hidden_mlp),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_mlp, 1),
        )

        self.dropout = dropout

    def encode_locations(
        self, node_features: torch.Tensor, edge_index: torch.Tensor
    ) -> torch.Tensor:
        """Run GraphSAGE to produce location embeddings."""
        identity = self.skip_proj(node_features)

        h = self.sage1(node_features, edge_index)
        h = self.ln1(h)
        h = F.relu(h)
        h = F.dropout(h, p=self.dropout, training=self.training)

        h = self.sage2(h, edge_index)
        h = self.ln2(h)
        h = F.relu(h + identity)

        return h

    def forward(
        self,
        node_features: torch.Tensor,
        edge_index: torch.Tensor,
        event_features: torch.Tensor,
        loc_indices: torch.Tensor,
    ) -> torch.Tensor:
        """Forward pass -> predicted catch rate (normalized)."""
        # GNN path
        gnn_emb = self.encode_locations(node_features, edge_index)
        event_gnn_emb = gnn_emb[loc_indices]

        # Direct location quality path
        loc_quality = self.loc_mlp(node_features)
        event_loc_quality = loc_quality[loc_indices]

        # Event encoding
        event_enc = self.event_encoder(event_features)

        # Fuse
        combined = torch.cat([event_gnn_emb, event_loc_quality, event_enc], dim=1)
        return self.head(combined).squeeze(-1)


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _get_available_features(df: pd.DataFrame, feature_list: list[str]) -> list[str]:
    return [f for f in feature_list if f in df.columns]


def prepare_event_data(
    df: pd.DataFrame,
    avail_event_feats: list[str],
    location_to_idx: dict[str, int],
    event_scaler: Optional[StandardScaler] = None,
    event_medians: Optional[np.ndarray] = None,
    target_mean: float = 0.0,
    target_std: float = 1.0,
    fit: bool = True,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, StandardScaler, np.ndarray, float, float]:
    """Prepare event features, location indices, and normalized targets."""
    event_vals = df[avail_event_feats].values.astype(np.float64)

    # Impute NaN with training medians
    if event_medians is None:
        event_medians = np.nanmedian(event_vals, axis=0)
    for c in range(event_vals.shape[1]):
        mask = np.isnan(event_vals[:, c])
        med = event_medians[c] if not np.isnan(event_medians[c]) else 0.0
        event_vals[mask, c] = med

    if fit or event_scaler is None:
        event_scaler = StandardScaler()
        event_vals = event_scaler.fit_transform(event_vals)
    else:
        event_vals = event_scaler.transform(event_vals)

    event_features = torch.tensor(event_vals, dtype=torch.float32)

    loc_indices = torch.tensor(
        [location_to_idx.get(loc, 0) for loc in df["location"].values],
        dtype=torch.long,
    )

    raw_targets = df[TARGET].values.astype(np.float64)
    if fit:
        target_mean = float(np.nanmean(raw_targets))
        target_std = float(np.nanstd(raw_targets))
        if target_std < 1e-6:
            target_std = 1.0

    targets_norm = torch.tensor(
        (raw_targets - target_mean) / target_std, dtype=torch.float32
    )

    return (event_features, loc_indices, targets_norm,
            event_scaler, event_medians, target_mean, target_std)


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def train_model(
    model: CastlineGNN,
    node_features: torch.Tensor,
    edge_index: torch.Tensor,
    event_features: torch.Tensor,
    loc_indices: torch.Tensor,
    targets: torch.Tensor,
    *,
    epochs: int = 400,
    lr: float = 5e-3,
    weight_decay: float = 5e-3,
    verbose: bool = False,
) -> list[float]:
    """Train the GNN model. Returns list of training losses."""
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=lr, weight_decay=weight_decay
    )
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=epochs, eta_min=lr * 0.01
    )

    losses = []
    model.train()
    for epoch in range(1, epochs + 1):
        optimizer.zero_grad()
        pred = model(node_features, edge_index, event_features, loc_indices)
        loss = F.huber_loss(pred, targets, delta=1.5)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
        optimizer.step()
        scheduler.step()
        losses.append(loss.item())

        if verbose and epoch % 100 == 0:
            print(f"    epoch {epoch:>4d}/{epochs}  loss={loss.item():.6f}")

    return losses


# ---------------------------------------------------------------------------
# LOO evaluation
# ---------------------------------------------------------------------------

def train_and_evaluate_loo(
    df: pd.DataFrame,
    top_n: int = 15,
    epochs: int = 400,
    hidden_gnn: int = 32,
    hidden_mlp: int = 48,
    k_geo: int = 8,
    k_morpho: int = 4,
    lr: float = 5e-3,
    n_seeds: int = 3,
    verbose: bool = True,
) -> dict:
    """Run leave-one-location-out evaluation on top N locations.

    For each held-out location:
    1. Build graph WITHOUT the test location (truly unseen).
    2. Train GNN on all events at remaining locations.
    3. At test time, re-insert the test location into the graph
       (with its node features but NO training events) and predict.

    The node feature scaler is fit on the TRAINING graph and reused for
    the full graph at test time, preventing data leakage.
    """
    loc_counts = df["location"].value_counts()
    top_locations = loc_counts.head(top_n).index.tolist()

    if verbose:
        print(f"\n{'='*70}")
        print(f"CASTLINE Production GNN -- LOO Evaluation")
        print(f"{'='*70}")
        print(f"Dataset: {len(df)} events, {df['location'].nunique()} locations")
        print(f"Top {top_n} locations, {n_seeds} seed(s)")
        print(f"GraphSAGE: hidden_gnn={hidden_gnn}, hidden_mlp={hidden_mlp}")
        print(f"Graph: k_geo={k_geo}, k_morpho={k_morpho}")
        print(f"Training: {epochs} epochs, lr={lr}")

    # Build per-location aggregated dataframe
    avail_node_feats = [f for f in NODE_FEATURES if f in df.columns]
    agg_dict = {}
    for f in ["lat", "lon"] + avail_node_feats:
        if f in df.columns and f not in agg_dict:
            agg_dict[f] = "first"
    loc_df = df.groupby("location").agg(agg_dict).reset_index()

    avail_event_feats = _get_available_features(df, EVENT_FEATURES)
    n_node_feats = len([f for f in avail_node_feats if f in loc_df.columns])
    n_event_feats = len(avail_event_feats)

    if verbose:
        print(f"Node features: {n_node_feats}, Event features: {n_event_feats}")
        print()

    all_true, all_pred = [], []
    per_location = []
    t_start = time.time()

    for fold_i, test_loc in enumerate(top_locations):
        fold_start = time.time()

        test_mask = df["location"] == test_loc
        train_df = df[~test_mask].reset_index(drop=True)
        test_df = df[test_mask].reset_index(drop=True)

        if len(test_df) < 2:
            if verbose:
                print(f"  [{fold_i+1}/{top_n}] {test_loc}: skipped (<2 events)")
            continue

        # 1. Build TRAIN graph
        train_nf, train_ei, train_lids, nscaler, nmedians = build_graph(
            loc_df, k_geo=k_geo, k_morpho=k_morpho,
            exclude_locations={test_loc},
        )
        train_l2i = {loc: i for i, loc in enumerate(train_lids)}
        train_df = train_df[train_df["location"].isin(train_l2i)].reset_index(drop=True)
        if len(train_df) < 10:
            continue

        # 2. Prepare training event data
        (tr_ef, tr_li, tr_tgt, escaler, emedians,
         tmean, tstd) = prepare_event_data(
            train_df, avail_event_feats, train_l2i, fit=True,
        )

        # 3. Build FULL graph for test-time (reuse train scalers)
        full_nf, full_ei, full_lids, _, _ = build_graph(
            loc_df, k_geo=k_geo, k_morpho=k_morpho,
            node_scaler=nscaler, node_medians=nmedians,
        )
        full_l2i = {loc: i for i, loc in enumerate(full_lids)}

        # 4. Prepare test event data (reuse train scalers)
        (te_ef, te_li, _, _, _, _, _) = prepare_event_data(
            test_df, avail_event_feats, full_l2i,
            event_scaler=escaler, event_medians=emedians,
            target_mean=tmean, target_std=tstd, fit=False,
        )

        # 5. Train model(s) and predict
        seed_preds = []
        for seed in range(n_seeds):
            torch.manual_seed(seed * 137 + fold_i * 7)

            model = CastlineGNN(
                n_node_features=train_nf.shape[1],
                n_event_features=tr_ef.shape[1],
                hidden_gnn=hidden_gnn,
                hidden_mlp=hidden_mlp,
            )

            train_model(
                model, train_nf, train_ei, tr_ef, tr_li, tr_tgt,
                epochs=epochs, lr=lr, verbose=False,
            )

            model.eval()
            with torch.no_grad():
                pn = model(full_nf, full_ei, te_ef, te_li).numpy()
            seed_preds.append(pn * tstd + tmean)

        # Ensemble: mean of seeds
        y_pred = np.mean(np.stack(seed_preds, axis=0), axis=0)
        y_true = test_df[TARGET].values
        y_pred = np.clip(y_pred, 0.5, 35.0)

        fold_r2 = r2_score(y_true, y_pred) if len(y_true) > 1 else float("nan")
        fold_rmse = math.sqrt(mean_squared_error(y_true, y_pred))
        fold_mae = mean_absolute_error(y_true, y_pred)
        fold_time = time.time() - fold_start

        all_true.extend(y_true.tolist())
        all_pred.extend(y_pred.tolist())

        per_location.append({
            "location": test_loc,
            "n_events": len(test_df),
            "r2": round(fold_r2, 4),
            "rmse": round(fold_rmse, 4),
            "mae": round(fold_mae, 4),
            "mean_true": round(float(np.mean(y_true)), 4),
            "mean_pred": round(float(np.mean(y_pred)), 4),
            "time_s": round(fold_time, 1),
        })

        if verbose:
            status = "OK" if fold_r2 > -0.5 else "WEAK"
            print(
                f"  [{fold_i+1:>2d}/{top_n}] {test_loc:<45s} "
                f"n={len(test_df):>3d}  R2={fold_r2:>7.3f}  "
                f"RMSE={fold_rmse:.3f}  "
                f"true={np.mean(y_true):.2f}  pred={np.mean(y_pred):.2f}  "
                f"[{fold_time:.1f}s] {status}"
            )

    total_time = time.time() - t_start

    if not all_true:
        print("No valid folds completed!")
        return {"r2": float("nan"), "rmse": float("nan"), "mae": float("nan")}

    all_true_arr = np.array(all_true)
    all_pred_arr = np.array(all_pred)

    overall_r2 = r2_score(all_true_arr, all_pred_arr)
    overall_rmse = math.sqrt(mean_squared_error(all_true_arr, all_pred_arr))
    overall_mae = mean_absolute_error(all_true_arr, all_pred_arr)

    results = {
        "r2": round(overall_r2, 4),
        "rmse": round(overall_rmse, 4),
        "mae": round(overall_mae, 4),
        "n_events_evaluated": len(all_true),
        "n_locations_evaluated": len(per_location),
        "total_time_s": round(total_time, 1),
        "per_location": per_location,
        "model_config": {
            "hidden_gnn": hidden_gnn,
            "hidden_mlp": hidden_mlp,
            "k_geo": k_geo,
            "k_morpho": k_morpho,
            "epochs": epochs,
            "lr": lr,
            "n_seeds": n_seeds,
            "n_node_features": n_node_feats,
            "n_event_features": n_event_feats,
        },
    }

    if verbose:
        print()
        print(f"{'='*70}")
        print(f"LOO OVERALL:  R2={overall_r2:.4f}  RMSE={overall_rmse:.4f}  MAE={overall_mae:.4f}")
        print(f"Evaluated {len(all_true)} events across {len(per_location)} locations")
        print(f"Total time: {total_time:.1f}s")
        print(f"{'='*70}")

        baseline_pred = np.full_like(all_true_arr, np.mean(all_true_arr))
        baseline_rmse = math.sqrt(mean_squared_error(all_true_arr, baseline_pred))
        print(f"\nBaseline (predict mean): RMSE={baseline_rmse:.4f}")
        improvement = baseline_rmse - overall_rmse
        print(f"GNN improvement:        RMSE reduction = {improvement:+.4f}")

        if overall_r2 > 0:
            print(f"\nGNN R2={overall_r2:.4f} > 0 : BETTER than predicting the mean")
        else:
            print(f"\nGNN R2={overall_r2:.4f} <= 0 : worse than predicting the mean")

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    data_path = (
        Path(__file__).resolve().parent.parent
        / "data" / "assembled" / "validation_dataset_v6.csv"
    )

    if not data_path.exists():
        print(f"Dataset not found: {data_path}")
        sys.exit(1)

    print(f"Loading dataset: {data_path}")
    df = pd.read_csv(data_path)
    print(f"Loaded {len(df)} events, {df['location'].nunique()} locations")
    print(f"Target range: {df[TARGET].min():.2f} - {df[TARGET].max():.2f}")

    results = train_and_evaluate_loo(
        df,
        top_n=15,
        epochs=400,
        hidden_gnn=32,
        hidden_mlp=48,
        k_geo=8,
        k_morpho=4,
        lr=5e-3,
        n_seeds=3,
        verbose=True,
    )

    # Save results
    out_path = (
        Path(__file__).resolve().parent.parent
        / "artifacts" / "gnn_loo_results.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {out_path}")
