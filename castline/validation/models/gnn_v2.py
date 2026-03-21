"""GNN v2: Improved architecture for LOO generalization.

Key improvements over v1 (R²=0.054):
1. GAT (Graph Attention) + GraphSAGE hybrid — learns which neighbors matter most
2. Deeper architecture: 3 GNN layers with residual connections
3. Location-aware attention: weight neighbors by feature similarity, not just topology
4. Multi-scale graph: geographic proximity + morphometric similarity + ecoregion
5. Larger capacity: hidden_gnn=64, hidden_mlp=96
6. Better training: mixup augmentation, label smoothing, cosine warmup
7. Location cluster features: aggregate info from similar locations
8. Two-stage prediction: location quality prior + environmental residual
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
from sklearn.cluster import KMeans
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")

try:
    from torch_geometric.nn import GATConv, SAGEConv
except ImportError:
    print("ERROR: torch_geometric required. Install with:")
    print("  pip install torch-geometric")
    sys.exit(1)

EARTH_RADIUS_KM = 6_371.0
TARGET = "median_weight_lb"

# Node features: static location properties (generalize to unseen locations)
NODE_FEATURES = [
    # Strongest spatial/climate signals
    "lat", "lon",
    "latitude_growth_potential",
    "growing_degree_proxy",
    "ecoregion_mean_weight", "ecoregion_mean_cpue",
    "max_depth_ft",
    # Moderate signals
    "ecoregion_n_surveys", "ecoregion_cluster",
    "n_nearby_creel_surveys",
    "shad_habitat_score",
    "regional_cpue_100km", "regional_weight_100km",
    # Morphometric
    "area_acres", "shore_dev",
    "morphometric_productivity_score",
    "productivity_x_latitude",
    "regional_weight_x_shad",
    "is_lake",
    # Water type
    "wtype_river", "wtype_reservoir", "wtype_natural_lake",
    "reservoir_score",
    # Species-specific
    "smallmouth_habitat_score",
    "species_diversity_potential",
    "northern_trophy_potential",
]

# Event features: environmental conditions at time of event
EVENT_FEATURES = [
    # Core environmental
    "water_temp_c", "air_temp_c",
    "pressure_mb", "pressure_delta_6h",
    "wind_speed_kph", "wind_dir_cos",
    "cloud_cover_pct", "precip_24h_mm",
    "discharge_cfs", "flow_delta_24h_pct", "discharge_pct_of_30d",
    "gage_height_ft", "gage_stability_7d",
    # Open-Meteo fills
    "om_air_temp_mean", "om_air_temp_max", "om_est_water_temp",
    "om_pressure_msl", "om_pressure_delta_24h", "om_pressure_delta_6h",
    "om_humidity", "om_dewpoint", "om_cloudcover",
    "om_wind_max_kph", "om_precip_mm", "om_precip_7d_total",
    "om_air_temp_7d_mean", "om_temp_trend_7d",
    # Water quality
    "dissolved_oxygen_mgL", "ph", "turbidity_fnu",
    # Temperature derivatives
    "water_temp_anomaly", "water_temp_estimated",
    "water_temp_7d_mean", "water_temp_30d_trend",
    "water_temp_x_flow", "cumulative_degree_days",
    # Temporal / seasonal
    "day_length_hours", "season_cos", "season_sin",
    "year",
    "moon_illumination_pct", "solunar_score", "moon_phase",
    # Fish biology
    "spawn_phase", "spawn_progress",
    "metabolic_rate_index", "feeding_window_score",
    "prespawn_aggression",
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
    # Interactions
    "wind_lake_interaction", "pressure_solunar_interaction",
    "depth_stability_interaction",
    "spawn_clarity_interaction",
    "metabolic_pressure_interaction",
    "feeding_season_interaction",
    # IV temporal features
    "discharge_cfs_current", "discharge_cfs_delta_3h",
    "discharge_cfs_delta_24h", "discharge_cfs_cv_24h",
    "gage_height_ft_current", "gage_height_ft_delta_3h",
    "water_temp_c_current", "water_temp_c_delta_3h",
    "discharge_spike_ratio",
    # Satellite
    "sat_air_temp_mean_c", "sat_estimated_water_temp_c",
    "sat_solar_radiation_mj", "sat_air_temp_7d_mean",
]


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _pairwise_geo_dist(lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    lat_r = np.radians(lats)
    lon_r = np.radians(lons)
    dlat = lat_r[:, None] - lat_r[None, :]
    dlon = lon_r[:, None] - lon_r[None, :]
    a = (np.sin(dlat / 2) ** 2 +
         np.cos(lat_r[:, None]) * np.cos(lat_r[None, :]) * np.sin(dlon / 2) ** 2)
    a = np.clip(a, 0, 1)
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))


def _get_avail(df, features):
    return [f for f in features if f in df.columns and df[f].notna().mean() > 0.02]


# ---------------------------------------------------------------------------
# Graph construction (multi-scale)
# ---------------------------------------------------------------------------

def build_graph(
    loc_df: pd.DataFrame,
    k_geo: int = 10,
    k_morpho: int = 5,
    k_eco: int = 6,
    node_scaler: Optional[StandardScaler] = None,
    node_medians: Optional[np.ndarray] = None,
    exclude_locations: Optional[set[str]] = None,
) -> tuple[torch.Tensor, torch.Tensor, list[str], StandardScaler, np.ndarray]:
    """Build multi-scale location graph with geographic + morphometric + ecoregion edges."""
    df = loc_df.copy().reset_index(drop=True)
    if exclude_locations:
        df = df[~df["location"].isin(exclude_locations)].reset_index(drop=True)

    n = len(df)
    location_ids = df["location"].tolist()

    avail = _get_avail(df, NODE_FEATURES)
    node_feat_np = df[avail].values.astype(np.float64)

    # Impute
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

    # --- Build edges ---
    lats = df["lat"].fillna(df["lat"].median()).values.astype(np.float64)
    lons = df["lon"].fillna(df["lon"].median()).values.astype(np.float64)
    geo_dist = _pairwise_geo_dist(lats, lons)
    np.fill_diagonal(geo_dist, np.inf)

    edge_set = set()

    # 1. Geographic KNN
    k_g = min(k_geo, n - 1)
    if k_g > 0:
        geo_nn = np.argsort(geo_dist, axis=1)[:, :k_g]
        for i in range(n):
            for j in geo_nn[i]:
                edge_set.add((i, int(j)))
                edge_set.add((int(j), i))

    # 2. Morphometric similarity KNN
    morpho_cols = ["area_acres", "max_depth_ft", "shore_dev", "is_lake"]
    morpho_avail = [c for c in morpho_cols if c in df.columns]
    k_m = min(k_morpho, n - 1)
    if morpho_avail and k_m > 0:
        mv = df[morpho_avail].values.astype(np.float64)
        for ci, col in enumerate(morpho_avail):
            if col in ("area_acres", "max_depth_ft"):
                mv[:, ci] = np.log1p(np.nan_to_num(mv[:, ci], nan=0.0))
            mask = np.isnan(mv[:, ci])
            med = np.nanmedian(mv[:, ci])
            mv[mask, ci] = med if not np.isnan(med) else 0.0
        ms = StandardScaler()
        mv_std = ms.fit_transform(mv)
        diff = mv_std[:, None, :] - mv_std[None, :, :]
        morpho_dist = np.sqrt(np.sum(diff ** 2, axis=2))
        np.fill_diagonal(morpho_dist, np.inf)
        morpho_nn = np.argsort(morpho_dist, axis=1)[:, :k_m]
        for i in range(n):
            for j in morpho_nn[i]:
                edge_set.add((i, int(j)))
                edge_set.add((int(j), i))

    # 3. Ecoregion edges: connect locations in same ecoregion cluster
    if "ecoregion_cluster" in df.columns:
        eco = df["ecoregion_cluster"].fillna(-1).values
        k_e = min(k_eco, n - 1)
        for i in range(n):
            if eco[i] < 0:
                continue
            same_eco = np.where((eco == eco[i]) & (np.arange(n) != i))[0]
            # Connect to closest K within same ecoregion
            if len(same_eco) > 0:
                eco_dists = geo_dist[i, same_eco]
                top_k = same_eco[np.argsort(eco_dists)[:k_e]]
                for j in top_k:
                    edge_set.add((i, int(j)))
                    edge_set.add((int(j), i))

    # 4. Latitude band edges: connect locations at similar latitudes (climate similarity)
    lat_bands = np.round(lats / 3) * 3  # 3-degree bands
    for i in range(n):
        same_band = np.where((lat_bands == lat_bands[i]) & (np.arange(n) != i))[0]
        if len(same_band) > 3:
            # Connect to 3 closest within band
            band_dists = geo_dist[i, same_band]
            top_3 = same_band[np.argsort(band_dists)[:3]]
            for j in top_3:
                edge_set.add((i, int(j)))
                edge_set.add((int(j), i))

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

class CastlineGNNv2(nn.Module):
    """Improved GNN for LOO generalization.

    Architecture:
    1. Location MLP: raw node features -> location quality prior
    2. GAT layer 1: attention-weighted neighbor aggregation
    3. SAGE layer 2: mean-pool aggregation (stable)
    4. GAT layer 3: final attention refinement
    5. Event encoder: environmental conditions
    6. Two-stage fusion head: location quality + environmental residual
    """

    def __init__(
        self,
        n_node_features: int,
        n_event_features: int,
        hidden_gnn: int = 64,
        hidden_mlp: int = 96,
        n_heads: int = 4,
        dropout: float = 0.1,
    ):
        super().__init__()

        # --- Location quality MLP (direct, no graph) ---
        self.loc_mlp = nn.Sequential(
            nn.Linear(n_node_features, hidden_gnn),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_gnn, hidden_gnn),
            nn.GELU(),
            nn.Linear(hidden_gnn, hidden_gnn),
        )

        # --- GNN layers (GAT + SAGE hybrid) ---
        # Layer 1: GAT with multi-head attention
        self.gat1 = GATConv(
            n_node_features, hidden_gnn // n_heads,
            heads=n_heads, concat=True, dropout=dropout,
        )
        self.ln1 = nn.LayerNorm(hidden_gnn)

        # Layer 2: SAGE (stable mean-pool)
        self.sage2 = SAGEConv(hidden_gnn, hidden_gnn)
        self.ln2 = nn.LayerNorm(hidden_gnn)

        # Layer 3: GAT refinement
        self.gat3 = GATConv(
            hidden_gnn, hidden_gnn // n_heads,
            heads=n_heads, concat=True, dropout=dropout,
        )
        self.ln3 = nn.LayerNorm(hidden_gnn)

        # Skip connections
        self.skip_in = (
            nn.Linear(n_node_features, hidden_gnn, bias=False)
            if n_node_features != hidden_gnn else nn.Identity()
        )
        self.skip_mid = nn.Identity()  # hidden_gnn -> hidden_gnn

        # --- Event encoder ---
        self.event_encoder = nn.Sequential(
            nn.Linear(n_event_features, hidden_mlp),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_mlp, hidden_mlp),
            nn.GELU(),
            nn.Linear(hidden_mlp, hidden_gnn),
        )

        # --- Stage 1: Location quality predictor ---
        # Predicts location mean weight from GNN + MLP embeddings
        self.loc_quality_head = nn.Sequential(
            nn.Linear(hidden_gnn * 2, hidden_gnn),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_gnn, 1),
        )

        # --- Stage 2: Environmental residual predictor ---
        # Predicts deviation from location mean based on conditions
        self.residual_head = nn.Sequential(
            nn.Linear(hidden_gnn * 3, hidden_mlp),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_mlp, hidden_mlp // 2),
            nn.GELU(),
            nn.Linear(hidden_mlp // 2, 1),
        )

        self.dropout_rate = dropout
        self.hidden_gnn = hidden_gnn

    def encode_locations(self, x: torch.Tensor, edge_index: torch.Tensor) -> torch.Tensor:
        """3-layer GNN encoding with residual connections."""
        skip0 = self.skip_in(x)

        # Layer 1: GAT
        h = self.gat1(x, edge_index)
        h = self.ln1(h)
        h = F.gelu(h + skip0)  # residual
        h = F.dropout(h, p=self.dropout_rate, training=self.training)

        skip1 = h

        # Layer 2: SAGE
        h = self.sage2(h, edge_index)
        h = self.ln2(h)
        h = F.gelu(h + skip1)  # residual
        h = F.dropout(h, p=self.dropout_rate, training=self.training)

        skip2 = h

        # Layer 3: GAT refinement
        h = self.gat3(h, edge_index)
        h = self.ln3(h)
        h = F.gelu(h + skip2)  # residual

        return h

    def forward(
        self,
        node_features: torch.Tensor,
        edge_index: torch.Tensor,
        event_features: torch.Tensor,
        loc_indices: torch.Tensor,
    ) -> torch.Tensor:
        # GNN path
        gnn_emb = self.encode_locations(node_features, edge_index)
        event_gnn = gnn_emb[loc_indices]

        # Direct location MLP path
        loc_quality = self.loc_mlp(node_features)
        event_loc = loc_quality[loc_indices]

        # Event encoding
        event_enc = self.event_encoder(event_features)

        # Stage 1: Location quality (GNN + MLP)
        loc_input = torch.cat([event_gnn, event_loc], dim=1)
        loc_pred = self.loc_quality_head(loc_input).squeeze(-1)

        # Stage 2: Environmental residual (all 3 streams)
        res_input = torch.cat([event_gnn, event_loc, event_enc], dim=1)
        residual = self.residual_head(res_input).squeeze(-1)

        # Final: location quality + environmental deviation
        return loc_pred + residual


# ---------------------------------------------------------------------------
# Training helpers
# ---------------------------------------------------------------------------

def prepare_event_data(
    df, avail_event_feats, location_to_idx,
    event_scaler=None, event_medians=None,
    target_mean=0.0, target_std=1.0, fit=True,
):
    event_vals = df[avail_event_feats].values.astype(np.float64)

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


def train_model(
    model, node_features, edge_index, event_features, loc_indices, targets,
    *,
    epochs=600,
    lr=3e-3,
    weight_decay=1e-2,
    warmup_epochs=50,
    verbose=False,
):
    """Train with cosine warmup + annealing, gradient clipping, mixup."""
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=lr, weight_decay=weight_decay
    )

    # Cosine annealing with warmup
    def lr_lambda(epoch):
        if epoch < warmup_epochs:
            return epoch / warmup_epochs
        progress = (epoch - warmup_epochs) / max(epochs - warmup_epochs, 1)
        return 0.01 + 0.99 * 0.5 * (1 + math.cos(math.pi * progress))

    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda)

    losses = []
    model.train()
    n = len(targets)

    for epoch in range(1, epochs + 1):
        optimizer.zero_grad()

        # Mixup augmentation (within-batch, 30% of epochs)
        if epoch % 3 == 0 and n > 10:
            alpha = 0.2
            lam = np.random.beta(alpha, alpha)
            perm = torch.randperm(n)
            ef_mix = lam * event_features + (1 - lam) * event_features[perm]
            li_mix = loc_indices  # Keep location assignments
            tgt_mix = lam * targets + (1 - lam) * targets[perm]
            pred = model(node_features, edge_index, ef_mix, li_mix)
            loss = F.huber_loss(pred, tgt_mix, delta=1.5)
        else:
            pred = model(node_features, edge_index, event_features, loc_indices)
            loss = F.huber_loss(pred, targets, delta=1.5)

        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
        optimizer.step()
        scheduler.step()
        losses.append(loss.item())

        if verbose and epoch % 100 == 0:
            print(f"    epoch {epoch:>4d}/{epochs}  loss={loss.item():.6f}  lr={scheduler.get_last_lr()[0]:.6f}")

    return losses


# ---------------------------------------------------------------------------
# LOO evaluation
# ---------------------------------------------------------------------------

def train_and_evaluate_loo(
    df: pd.DataFrame,
    top_n: int = 15,
    epochs: int = 600,
    hidden_gnn: int = 64,
    hidden_mlp: int = 96,
    n_heads: int = 4,
    k_geo: int = 10,
    k_morpho: int = 5,
    k_eco: int = 6,
    lr: float = 3e-3,
    n_seeds: int = 5,
    verbose: bool = True,
) -> dict:
    """Leave-one-location-out evaluation with GNNv2."""
    loc_counts = df["location"].value_counts()
    top_locations = loc_counts.head(top_n).index.tolist()

    if verbose:
        print(f"\n{'='*70}")
        print(f"CASTLINE GNN v2 -- LOO Evaluation")
        print(f"{'='*70}")
        print(f"Dataset: {len(df)} events, {df['location'].nunique()} locations")
        print(f"Architecture: GAT+SAGE hybrid, 3 layers, {n_heads} heads")
        print(f"Hidden: gnn={hidden_gnn}, mlp={hidden_mlp}")
        print(f"Graph: k_geo={k_geo}, k_morpho={k_morpho}, k_eco={k_eco}")
        print(f"Training: {epochs} epochs, lr={lr}, {n_seeds} seeds")

    # Build per-location aggregated dataframe
    avail_node = _get_avail(df, NODE_FEATURES)
    agg_dict = {}
    for f in ["lat", "lon"] + avail_node:
        if f in df.columns and f not in agg_dict:
            agg_dict[f] = "first"
    loc_df = df.groupby("location").agg(agg_dict).reset_index()

    avail_event = _get_avail(df, EVENT_FEATURES)
    n_node_feats = len([f for f in avail_node if f in loc_df.columns])
    n_event_feats = len(avail_event)

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
            continue

        # 1. Build TRAIN graph (exclude test location)
        train_nf, train_ei, train_lids, nscaler, nmedians = build_graph(
            loc_df, k_geo=k_geo, k_morpho=k_morpho, k_eco=k_eco,
            exclude_locations={test_loc},
        )
        train_l2i = {loc: i for i, loc in enumerate(train_lids)}
        train_df = train_df[train_df["location"].isin(train_l2i)].reset_index(drop=True)
        if len(train_df) < 10:
            continue

        # 2. Prepare training event data
        (tr_ef, tr_li, tr_tgt, escaler, emedians,
         tmean, tstd) = prepare_event_data(
            train_df, avail_event, train_l2i, fit=True,
        )

        # 3. Build FULL graph (reuse train scalers)
        full_nf, full_ei, full_lids, _, _ = build_graph(
            loc_df, k_geo=k_geo, k_morpho=k_morpho, k_eco=k_eco,
            node_scaler=nscaler, node_medians=nmedians,
        )
        full_l2i = {loc: i for i, loc in enumerate(full_lids)}

        # 4. Prepare test event data
        (te_ef, te_li, _, _, _, _, _) = prepare_event_data(
            test_df, avail_event, full_l2i,
            event_scaler=escaler, event_medians=emedians,
            target_mean=tmean, target_std=tstd, fit=False,
        )

        # 5. Train model(s) with multiple seeds
        seed_preds = []
        for seed in range(n_seeds):
            torch.manual_seed(seed * 137 + fold_i * 7 + 42)
            np.random.seed(seed * 137 + fold_i * 7 + 42)

            model = CastlineGNNv2(
                n_node_features=train_nf.shape[1],
                n_event_features=tr_ef.shape[1],
                hidden_gnn=hidden_gnn,
                hidden_mlp=hidden_mlp,
                n_heads=n_heads,
            )

            train_model(
                model, train_nf, train_ei, tr_ef, tr_li, tr_tgt,
                epochs=epochs, lr=lr, verbose=False,
            )

            model.eval()
            with torch.no_grad():
                pn = model(full_nf, full_ei, te_ef, te_li).numpy()
            seed_preds.append(pn * tstd + tmean)

        # Ensemble across seeds
        y_pred = np.mean(np.stack(seed_preds, axis=0), axis=0)
        y_true = test_df[TARGET].values
        y_pred = np.clip(y_pred, 0.5, 35.0)

        fold_r2 = r2_score(y_true, y_pred) if len(y_true) > 1 else float("nan")
        fold_mae = mean_absolute_error(y_true, y_pred)
        fold_time = time.time() - fold_start

        all_true.extend(y_true.tolist())
        all_pred.extend(y_pred.tolist())

        per_location.append({
            "location": test_loc,
            "n_events": len(test_df),
            "r2": round(fold_r2, 4),
            "mae": round(fold_mae, 4),
            "mean_true": round(float(np.mean(y_true)), 4),
            "mean_pred": round(float(np.mean(y_pred)), 4),
            "time_s": round(fold_time, 1),
        })

        if verbose:
            err = abs(np.mean(y_true) - np.mean(y_pred))
            status = "OK" if fold_r2 > 0 else ("WEAK" if fold_r2 > -1 else "BAD")
            print(
                f"  [{fold_i+1:>2d}/{top_n}] {test_loc:<45s} "
                f"n={len(test_df):>3d}  R2={fold_r2:>7.3f}  "
                f"true={np.mean(y_true):.2f}  pred={np.mean(y_pred):.2f}  "
                f"err={err:.2f}  [{fold_time:.1f}s] {status}"
            )

    total_time = time.time() - t_start

    if not all_true:
        print("No valid folds completed!")
        return {"r2": float("nan")}

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
        "config": {
            "hidden_gnn": hidden_gnn,
            "hidden_mlp": hidden_mlp,
            "n_heads": n_heads,
            "k_geo": k_geo,
            "k_morpho": k_morpho,
            "k_eco": k_eco,
            "epochs": epochs,
            "lr": lr,
            "n_seeds": n_seeds,
        },
    }

    if verbose:
        print()
        print(f"{'='*70}")
        print(f"LOO OVERALL:  R2={overall_r2:.4f}  RMSE={overall_rmse:.4f}  MAE={overall_mae:.4f}")
        print(f"Evaluated {len(all_true)} events across {len(per_location)} locations")
        print(f"Total time: {total_time:.1f}s")

        r2_list = [r["r2"] for r in per_location]
        positive = sum(1 for r in r2_list if r > 0)
        print(f"Locations with R2 > 0: {positive}/{len(per_location)}")
        print(f"Median per-location R2: {np.median(r2_list):.4f}")
        print(f"{'='*70}")

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

    # Apply feature engineering from train_v6
    try:
        import importlib.util
        tv6_path = Path(__file__).resolve().parent.parent / "train_v6.py"
        spec = importlib.util.spec_from_file_location("train_v6", str(tv6_path))
        mod = importlib.util.module_from_spec(spec)
        mod.__name__ = "train_v6"
        sys.modules["train_v6"] = mod
        spec.loader.exec_module(mod)
        df = mod.add_engineered_features(df)
        print(f"Applied feature engineering: {len(df.columns)} columns")
    except Exception as e:
        print(f"Warning: Could not load feature engineering: {e}")

    # Run with improved hyperparameters
    results = train_and_evaluate_loo(
        df,
        top_n=15,
        epochs=600,
        hidden_gnn=64,
        hidden_mlp=96,
        n_heads=4,
        k_geo=10,
        k_morpho=5,
        k_eco=6,
        lr=3e-3,
        n_seeds=5,
        verbose=True,
    )

    # Save results
    out_path = (
        Path(__file__).resolve().parent.parent
        / "artifacts" / "gnn_v2_loo_results.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {out_path}")
