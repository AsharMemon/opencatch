#!/usr/bin/env python3
"""
t-PatchGNN Prototype for CASTLINE CPUE Prediction
==================================================

Adapts the t-PatchGNN architecture (ICML 2024) for irregular multivariate
time series forecasting of fishing CPUE across a spatial graph of 2001 lakes.

This script is designed to run on Vast.ai with a GPU.

Status: PROTOTYPE -- see inline comments for what is production-ready vs placeholder.
"""

import math
import warnings
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader
from scipy.spatial.distance import cdist

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# PRODUCTION-READY: these match the V17 dataset schema
ENV_CHANNELS = [
    "water_temp_c",
    "discharge_cfs",
    "gage_height_ft",
    "air_temp_c",
    "pressure_mb",
    "wind_speed_kph",
    "precip_mm",
    "dissolved_oxygen_mgL",
]

STATIC_FEATURES = [
    "lat", "lon",
    "area_acres", "max_depth_ft", "shore_dev",
    "reservoir_score", "is_lake",
    "is_largemouth_water", "is_smallmouth_water", "species_diversity",
    "latitude_growth_potential", "northern_trophy_potential",
    "shad_habitat_score", "smallmouth_habitat_score",
] + [f"geoclip_{i}" for i in range(32)]

EARTH_RADIUS_KM = 6371.0


# ===========================================================================
# 1. DATA PIPELINE
# ===========================================================================

def haversine_matrix(coords: np.ndarray) -> np.ndarray:
    """Compute pairwise haversine distances in km. coords: (N, 2) with (lat, lon) in degrees.

    PRODUCTION-READY.
    """
    lat = np.radians(coords[:, 0])
    lon = np.radians(coords[:, 1])
    dlat = lat[:, None] - lat[None, :]
    dlon = lon[:, None] - lon[None, :]
    a = np.sin(dlat / 2) ** 2 + np.cos(lat[:, None]) * np.cos(lat[None, :]) * np.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def build_adjacency(coords: np.ndarray, threshold_km: float = 100.0, sigma_km: float = 50.0) -> np.ndarray:
    """Build geographic adjacency matrix with exponential distance weighting.

    PRODUCTION-READY.
    Returns: (N, N) float32 adjacency matrix.
    """
    dist = haversine_matrix(coords)
    adj = np.exp(-dist / sigma_km) * (dist < threshold_km).astype(np.float32)
    np.fill_diagonal(adj, 1.0)
    # Row-normalize for GCN
    row_sum = adj.sum(axis=1, keepdims=True)
    row_sum[row_sum == 0] = 1.0
    return (adj / row_sum).astype(np.float32)


def build_location_registry(df: pd.DataFrame) -> pd.DataFrame:
    """Extract unique locations with static features.

    PRODUCTION-READY.
    """
    available_static = [c for c in STATIC_FEATURES if c in df.columns]
    loc_cols = ["location"] + available_static
    # Take first non-null row per location for static features
    registry = df[loc_cols].groupby("location").first().reset_index()
    # Fill NaN static features with 0 (safe default for normalized features)
    for c in available_static:
        registry[c] = registry[c].fillna(0.0)
    return registry


class CastlineGraphDataset(Dataset):
    """PyTorch Dataset that converts the V17 tabular data into graph-structured
    samples for t-PatchGNN.

    Each sample corresponds to one prediction event (location, date) and includes:
    - Environmental time series patches for the target location and its neighbors
    - Static features for each node in the subgraph
    - The target CPUE value

    PROTOTYPE -- uses snapshot features from the event row as a simplified proxy
    for true time series. Production version should pull from raw USGS/weather CSVs.
    """

    def __init__(
        self,
        df: pd.DataFrame,
        location_registry: pd.DataFrame,
        adj_matrix: np.ndarray,
        n_patches: int = 8,
        max_obs_per_patch: int = 20,
        n_channels: int = 8,
        max_neighbors: int = 30,
        target_col: str = "baseline_signal",
    ):
        self.target_col = target_col
        self.n_patches = n_patches
        self.max_obs = max_obs_per_patch
        self.n_channels = n_channels
        self.max_neighbors = max_neighbors

        # Filter to rows with valid target
        self.df = df.dropna(subset=[target_col]).reset_index(drop=True)
        self.registry = location_registry
        self.adj = adj_matrix

        # Build location -> index mapping
        self.loc_to_idx = {loc: i for i, loc in enumerate(self.registry["location"])}

        # Precompute static feature tensor
        static_cols = [c for c in STATIC_FEATURES if c in self.registry.columns]
        self.static_tensor = torch.tensor(
            self.registry[static_cols].values, dtype=torch.float32
        )
        self.static_dim = len(static_cols)

        # Precompute neighbor lists from adjacency
        self.neighbors = {}
        for i in range(len(self.registry)):
            # Get top-k neighbors by adjacency weight (excluding self)
            weights = self.adj[i].copy()
            weights[i] = 0  # exclude self
            top_k = np.argsort(weights)[-self.max_neighbors:]
            top_k = top_k[weights[top_k] > 0]
            self.neighbors[i] = top_k.tolist()

        # Precompute per-location event data for time series construction
        self._build_location_timeseries()

    def _build_location_timeseries(self):
        """Build simplified time series per location from event snapshots.

        PLACEHOLDER: In production, this should load from raw USGS/weather CSVs
        to get daily environmental data (not just tournament-date snapshots).
        """
        self.loc_timeseries = {}
        available_channels = [c for c in ENV_CHANNELS if c in self.df.columns]
        self.actual_channels = len(available_channels)

        for loc, group in self.df.groupby("location"):
            if loc not in self.loc_to_idx:
                continue
            # Sort by date
            group = group.sort_values("date")
            timestamps = pd.to_datetime(group["date"]).values.astype(np.int64) / 1e9  # unix seconds
            values = group[available_channels].fillna(0.0).values  # (T, C)
            masks = group[available_channels].notna().values.astype(np.float32)
            self.loc_timeseries[loc] = {
                "timestamps": timestamps.astype(np.float64),
                "values": values.astype(np.float32),
                "masks": masks,
            }

    def _get_patches(self, location: str, target_timestamp: float):
        """Extract patches for a location leading up to target_timestamp.

        Returns:
            values: (M, L, C) tensor of observations
            times:  (M, L) tensor of relative timestamps
            masks:  (M, L, C) tensor of observation masks

        PLACEHOLDER: With real daily data this would slice true time windows.
        Currently uses available event snapshots as sparse observations.
        """
        M = self.n_patches
        L = self.max_obs
        C = self.actual_channels

        values = torch.zeros(M, L, C)
        times = torch.zeros(M, L)
        masks = torch.zeros(M, L, C)

        ts_data = self.loc_timeseries.get(location)
        if ts_data is None:
            return values, times, masks

        all_ts = ts_data["timestamps"]
        all_vals = ts_data["values"]
        all_masks = ts_data["masks"]

        # Define patch windows: each 7 days, going back n_patches * 7 days
        patch_days = 7
        for p in range(M):
            t_end = target_timestamp - (M - 1 - p) * patch_days * 86400
            t_start = t_end - patch_days * 86400
            # Find observations in this window
            in_window = (all_ts >= t_start) & (all_ts < t_end)
            idx = np.where(in_window)[0]
            if len(idx) == 0:
                continue
            # Take up to L observations
            idx = idx[:L]
            n_obs = len(idx)
            # Relative time within patch (0 to 1)
            if t_end > t_start:
                rel_time = (all_ts[idx] - t_start) / (t_end - t_start)
            else:
                rel_time = np.zeros(n_obs)
            values[p, :n_obs, :] = torch.from_numpy(all_vals[idx])
            times[p, :n_obs] = torch.from_numpy(rel_time.astype(np.float32))
            masks[p, :n_obs, :] = torch.from_numpy(all_masks[idx])

        return values, times, masks

    def __len__(self):
        return len(self.df)

    def __getitem__(self, idx):
        row = self.df.iloc[idx]
        loc = row["location"]
        target = float(row[self.target_col])
        target_date = pd.Timestamp(row["date"])
        target_ts = target_date.timestamp()

        loc_idx = self.loc_to_idx.get(loc, 0)

        # Subgraph: target node + neighbors
        neighbor_idxs = self.neighbors.get(loc_idx, [])
        subgraph_idxs = [loc_idx] + neighbor_idxs
        n_nodes = len(subgraph_idxs)

        # Get patches for each node in subgraph
        all_values = []
        all_times = []
        all_masks = []

        for ni in subgraph_idxs:
            node_loc = self.registry.iloc[ni]["location"]
            v, t, m = self._get_patches(node_loc, target_ts)
            all_values.append(v)
            all_times.append(t)
            all_masks.append(m)

        # Stack: (N_sub, M, L, C)
        values = torch.stack(all_values)
        times = torch.stack(all_times)
        masks = torch.stack(all_masks)

        # Static features for subgraph nodes
        static = self.static_tensor[subgraph_idxs]  # (N_sub, D_static)

        # Sub-adjacency matrix
        sg_idx = torch.tensor(subgraph_idxs, dtype=torch.long)
        sub_adj = torch.tensor(
            self.adj[np.ix_(subgraph_idxs, subgraph_idxs)],
            dtype=torch.float32,
        )

        return {
            "values": values,        # (N_sub, M, L, C)
            "times": times,          # (N_sub, M, L)
            "masks": masks,          # (N_sub, M, L, C)
            "static": static,        # (N_sub, D_static)
            "adj": sub_adj,          # (N_sub, N_sub)
            "target": torch.tensor(target, dtype=torch.float32),
            "target_node_idx": 0,    # target is always first node in subgraph
            "n_nodes": n_nodes,
        }


def collate_graph_batch(batch):
    """Custom collate for variable-size subgraphs.

    Pads all subgraphs to the maximum size in the batch.

    PRODUCTION-READY (simple padding strategy).
    """
    max_nodes = max(b["n_nodes"] for b in batch)
    B = len(batch)
    M = batch[0]["values"].shape[1]
    L = batch[0]["values"].shape[2]
    C = batch[0]["values"].shape[3]
    D_static = batch[0]["static"].shape[1]

    values = torch.zeros(B, max_nodes, M, L, C)
    times = torch.zeros(B, max_nodes, M, L)
    masks = torch.zeros(B, max_nodes, M, L, C)
    static = torch.zeros(B, max_nodes, D_static)
    adj = torch.zeros(B, max_nodes, max_nodes)
    targets = torch.zeros(B)
    node_mask = torch.zeros(B, max_nodes)  # which nodes are real

    for i, b in enumerate(batch):
        n = b["n_nodes"]
        values[i, :n] = b["values"]
        times[i, :n] = b["times"]
        masks[i, :n] = b["masks"]
        static[i, :n] = b["static"]
        adj[i, :n, :n] = b["adj"]
        targets[i] = b["target"]
        node_mask[i, :n] = 1.0

    return {
        "values": values,
        "times": times,
        "masks": masks,
        "static": static,
        "adj": adj,
        "targets": targets,
        "node_mask": node_mask,
    }


# ===========================================================================
# 2. MODEL ARCHITECTURE
# ===========================================================================

class TimeEncoding(nn.Module):
    """Continuous time encoding: linear trend + periodic sinusoidal components.

    Following t-PatchGNN: encodes irregular timestamps into a fixed-dim vector
    without discretizing time.

    PRODUCTION-READY.
    """

    def __init__(self, te_dim: int = 16):
        super().__init__()
        self.te_dim = te_dim
        self.trend = nn.Linear(1, 1)
        self.periodic = nn.Linear(1, te_dim - 1)

    def forward(self, t: torch.Tensor) -> torch.Tensor:
        """
        Args:
            t: (...,) tensor of timestamps (normalized to [0, 1])
        Returns:
            (..., te_dim) time encoding
        """
        t = t.unsqueeze(-1)  # (..., 1)
        trend = self.trend(t)  # (..., 1)
        periodic = torch.sin(self.periodic(t))  # (..., te_dim-1)
        return torch.cat([trend, periodic], dim=-1)  # (..., te_dim)


class TTCN(nn.Module):
    """Transformable Temporal Convolution Network.

    Generates adaptive convolution filters from time encodings and applies
    masked softmax attention over irregular observations within each patch.

    Following the t-PatchGNN paper, this replaces standard fixed-stride
    convolutions with data-adaptive filters that handle variable observation
    counts per patch.

    PRODUCTION-READY (core algorithm matches paper).
    """

    def __init__(self, input_dim: int, ttcn_dim: int, te_dim: int):
        super().__init__()
        self.ttcn_dim = ttcn_dim
        # Filter generator: from time encoding to adaptive filter weights
        self.filter_gen = nn.Sequential(
            nn.Linear(te_dim, 64),
            nn.ReLU(),
            nn.Linear(64, input_dim * ttcn_dim),
        )

    def forward(self, x: torch.Tensor, te: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: (B*N*M, L, input_dim) observations
            te: (B*N*M, L, te_dim) time encodings
            mask: (B*N*M, L, 1) observation mask

        Returns:
            (B*N*M, ttcn_dim) patch-level representation
        """
        BNM, L, D = x.shape
        # Generate filters from time encoding: (BNM, L, D * ttcn_dim)
        filters = self.filter_gen(te)
        # Reshape to (BNM, L, D, ttcn_dim)
        filters = filters.view(BNM, L, D, self.ttcn_dim)

        # Masked softmax attention over observations
        # Compute attention scores: (BNM, L, ttcn_dim)
        scores = torch.einsum("bld,bldt->blt", x, filters)  # (BNM, L, ttcn_dim)

        # Apply mask: set non-observed positions to -inf
        mask_expanded = mask.expand_as(scores)
        scores = scores.masked_fill(mask_expanded == 0, -1e8)

        # Softmax over L dimension
        attn = F.softmax(scores, dim=1)  # (BNM, L, ttcn_dim)
        attn = attn * mask_expanded  # zero out padded positions

        # Apply attention: weighted sum of input features mapped through filters
        # x: (BNM, L, D), filters: (BNM, L, D, ttcn_dim)
        mapped = torch.einsum("bld,bldt->blt", x, filters)  # (BNM, L, ttcn_dim)
        # Weighted sum over L
        output = (attn * mapped).sum(dim=1)  # (BNM, ttcn_dim)

        return output


class PositionalEncoding(nn.Module):
    """Standard sinusoidal positional encoding for transformer.

    PRODUCTION-READY.
    """

    def __init__(self, d_model: int, max_len: int = 500, dropout: float = 0.1):
        super().__init__()
        self.dropout = nn.Dropout(dropout)
        pe = torch.zeros(max_len, d_model)
        position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(position * div_term)
        if d_model > 1:
            pe[:, 1::2] = torch.cos(position * div_term[:d_model // 2])
        pe = pe.unsqueeze(0)  # (1, max_len, d_model)
        self.register_buffer("pe", pe)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = x + self.pe[:, :x.size(1)]
        return self.dropout(x)


class AdaptiveGraphLearner(nn.Module):
    """Learns an adaptive graph structure that combines geographic prior
    with data-driven node relationships.

    Following t-PatchGNN: learnable node embeddings produce a soft adjacency
    matrix via softmax(ReLU(E1 @ E2)).

    PRODUCTION-READY.
    """

    def __init__(self, max_nodes: int, node_dim: int, hid_dim: int):
        super().__init__()
        self.node_emb1 = nn.Parameter(torch.randn(max_nodes, node_dim) * 0.1)
        self.node_emb2 = nn.Parameter(torch.randn(node_dim, max_nodes) * 0.1)
        self.alpha = nn.Parameter(torch.tensor(0.5))  # geographic vs learned blend

        # Gate to incorporate temporal context
        self.gate = nn.Sequential(
            nn.Linear(hid_dim + node_dim, 1),
            nn.Tanh(),
        )

    def forward(self, geo_adj: torch.Tensor, n_nodes: int) -> torch.Tensor:
        """
        Args:
            geo_adj: (B, N, N) geographic adjacency
            n_nodes: actual number of nodes (may be < max_nodes)
        Returns:
            (B, N, N) combined adjacency
        """
        # Learned adjacency from node embeddings
        e1 = self.node_emb1[:n_nodes]  # (N, node_dim)
        e2 = self.node_emb2[:, :n_nodes]  # (node_dim, N)
        learned_adj = F.softmax(F.relu(e1 @ e2), dim=-1)  # (N, N)

        # Blend geographic and learned
        alpha = torch.sigmoid(self.alpha)
        combined = alpha * geo_adj + (1 - alpha) * learned_adj.unsqueeze(0)

        return combined


class GraphConv(nn.Module):
    """Chebyshev spectral graph convolution.

    Implements K-order polynomial approximation for efficient spectral filtering.

    PRODUCTION-READY.
    """

    def __init__(self, in_dim: int, out_dim: int, order: int = 2):
        super().__init__()
        self.order = order
        self.linear = nn.Linear(in_dim * (order + 1), out_dim)

    def forward(self, x: torch.Tensor, adj: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: (B, N, D) node features
            adj: (B, N, N) adjacency matrix
        Returns:
            (B, N, out_dim) convolved features
        """
        supports = [x]  # order 0: identity
        if self.order >= 1:
            supports.append(torch.bmm(adj, x))  # order 1: A @ x
        for k in range(2, self.order + 1):
            # Chebyshev recurrence: T_k(x) = 2*x*T_{k-1}(x) - T_{k-2}(x)
            supports.append(
                2 * torch.bmm(adj, supports[-1]) - supports[-2]
            )

        out = torch.cat(supports, dim=-1)  # (B, N, D * (order+1))
        return self.linear(out)


class tPatchGNN(nn.Module):
    """t-PatchGNN adapted for CASTLINE CPUE prediction.

    Architecture:
    1. Time encoding for irregular timestamps
    2. TTCN for adaptive temporal convolution within patches
    3. Transformer for temporal patterns across patches
    4. Adaptive graph learning (geographic + learned)
    5. Chebyshev GCN for spatial message passing
    6. Static feature fusion + prediction head

    PROTOTYPE -- architecture is faithful to the paper but hyperparameters
    and some design choices need tuning on actual data.
    """

    def __init__(
        self,
        n_channels: int = 8,
        te_dim: int = 16,
        ttcn_dim: int = 32,
        hid_dim: int = 64,
        node_dim: int = 16,
        static_dim: int = 46,
        n_patches: int = 8,
        max_nodes: int = 31,  # 1 target + 30 neighbors
        n_tf_layers: int = 2,
        n_tf_heads: int = 4,
        n_gnn_layers: int = 2,
        gcn_order: int = 2,
        dropout: float = 0.1,
    ):
        super().__init__()
        self.n_patches = n_patches
        self.hid_dim = hid_dim

        # --- Time encoding ---
        self.time_enc = TimeEncoding(te_dim)

        # --- Input projection ---
        self.input_proj = nn.Linear(n_channels, hid_dim)

        # --- TTCN: temporal conv within patches ---
        self.ttcn = TTCN(input_dim=n_channels, ttcn_dim=ttcn_dim, te_dim=te_dim)

        # --- Patch-level projection ---
        self.patch_proj = nn.Linear(ttcn_dim, hid_dim)

        # --- Transformer: temporal patterns across patches ---
        self.pos_enc = PositionalEncoding(hid_dim, max_len=n_patches + 10, dropout=dropout)
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=hid_dim,
            nhead=n_tf_heads,
            dim_feedforward=hid_dim * 4,
            dropout=dropout,
            batch_first=True,
        )
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=n_tf_layers)

        # --- Adaptive graph learning ---
        self.graph_learner = AdaptiveGraphLearner(max_nodes, node_dim, hid_dim)

        # --- GCN layers ---
        self.gcn_layers = nn.ModuleList()
        self.gcn_norms = nn.ModuleList()
        for _ in range(n_gnn_layers):
            self.gcn_layers.append(GraphConv(hid_dim, hid_dim, order=gcn_order))
            self.gcn_norms.append(nn.LayerNorm(hid_dim))

        # --- Temporal aggregation (across patches) ---
        self.temporal_agg = nn.Linear(n_patches * hid_dim, hid_dim)

        # --- Static feature fusion ---
        self.static_proj = nn.Linear(static_dim, hid_dim)

        # --- Prediction head ---
        self.decoder = nn.Sequential(
            nn.Linear(hid_dim * 2, hid_dim),  # temporal + static
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hid_dim, hid_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hid_dim, 1),
        )

    def forward(
        self,
        values: torch.Tensor,    # (B, N, M, L, C)
        times: torch.Tensor,     # (B, N, M, L)
        masks: torch.Tensor,     # (B, N, M, L, C)
        static: torch.Tensor,    # (B, N, D_static)
        adj: torch.Tensor,       # (B, N, N)
        node_mask: torch.Tensor, # (B, N) -- which nodes are real
    ) -> torch.Tensor:
        """
        Returns: (B,) predicted CPUE for target node (index 0) in each batch element.
        """
        B, N, M, L, C = values.shape

        # --- 1. Time encoding ---
        te = self.time_enc(times)  # (B, N, M, L, te_dim)

        # --- 2. TTCN: process each patch ---
        # Reshape to (B*N*M, L, C) for TTCN
        x_flat = values.reshape(B * N * M, L, C)
        te_flat = te.reshape(B * N * M, L, -1)
        # For mask: collapse channel dim -> any channel observed
        mask_any = masks.any(dim=-1, keepdim=True).float()  # (B, N, M, L, 1)
        mask_flat = mask_any.reshape(B * N * M, L, 1)

        patch_repr = self.ttcn(x_flat, te_flat, mask_flat)  # (B*N*M, ttcn_dim)
        patch_repr = self.patch_proj(patch_repr)  # (B*N*M, hid_dim)
        patch_repr = patch_repr.reshape(B, N, M, self.hid_dim)

        # --- 3. Transformer: temporal patterns across patches per node ---
        # Reshape to (B*N, M, hid_dim)
        x_tf = patch_repr.reshape(B * N, M, self.hid_dim)
        x_tf = self.pos_enc(x_tf)
        x_tf = self.transformer(x_tf)  # (B*N, M, hid_dim)
        x_tf = x_tf.reshape(B, N, M, self.hid_dim)

        # --- 4. GCN: spatial message passing (per patch) ---
        # Learn adaptive adjacency
        combined_adj = self.graph_learner(adj, N)  # (B, N, N)

        # Apply GCN per patch step
        for gcn, norm in zip(self.gcn_layers, self.gcn_norms):
            x_gcn_out = []
            for m in range(M):
                h = x_tf[:, :, m, :]  # (B, N, hid_dim)
                h_new = gcn(h, combined_adj)  # (B, N, hid_dim)
                h_new = norm(h_new)
                h_new = F.relu(h_new)
                x_gcn_out.append(h_new)
            x_tf = torch.stack(x_gcn_out, dim=2)  # (B, N, M, hid_dim)
            # Residual connection
            x_tf = x_tf + patch_repr

        # --- 5. Temporal aggregation ---
        # Flatten patches: (B, N, M*hid_dim)
        x_agg = x_tf.reshape(B, N, M * self.hid_dim)
        x_agg = self.temporal_agg(x_agg)  # (B, N, hid_dim)
        x_agg = F.relu(x_agg)

        # --- 6. Static feature fusion ---
        static_h = self.static_proj(static)  # (B, N, hid_dim)
        static_h = F.relu(static_h)

        combined = torch.cat([x_agg, static_h], dim=-1)  # (B, N, 2*hid_dim)

        # --- 7. Prediction ---
        pred = self.decoder(combined)  # (B, N, 1)
        pred = pred.squeeze(-1)  # (B, N)

        # Return prediction for target node (index 0)
        return pred[:, 0]  # (B,)


# ===========================================================================
# 3. TRAINING LOOP
# ===========================================================================

def train_epoch(model, loader, optimizer, device):
    """Single training epoch. PRODUCTION-READY."""
    model.train()
    total_loss = 0.0
    n_batches = 0

    for batch in loader:
        values = batch["values"].to(device)
        times = batch["times"].to(device)
        masks = batch["masks"].to(device)
        static = batch["static"].to(device)
        adj = batch["adj"].to(device)
        targets = batch["targets"].to(device)
        node_mask = batch["node_mask"].to(device)

        optimizer.zero_grad()
        preds = model(values, times, masks, static, adj, node_mask)
        loss = F.mse_loss(preds, targets)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()

        total_loss += loss.item()
        n_batches += 1

    return total_loss / max(n_batches, 1)


@torch.no_grad()
def evaluate(model, loader, device):
    """Evaluate model and return metrics. PRODUCTION-READY."""
    model.eval()
    all_preds = []
    all_targets = []

    for batch in loader:
        values = batch["values"].to(device)
        times = batch["times"].to(device)
        masks = batch["masks"].to(device)
        static = batch["static"].to(device)
        adj = batch["adj"].to(device)
        targets = batch["targets"].to(device)
        node_mask = batch["node_mask"].to(device)

        preds = model(values, times, masks, static, adj, node_mask)
        all_preds.append(preds.cpu())
        all_targets.append(targets.cpu())

    preds = torch.cat(all_preds).numpy()
    targets = torch.cat(all_targets).numpy()

    mse = np.mean((preds - targets) ** 2)
    mae = np.mean(np.abs(preds - targets))

    # R-squared
    ss_res = np.sum((targets - preds) ** 2)
    ss_tot = np.sum((targets - np.mean(targets)) ** 2)
    r2 = 1 - ss_res / max(ss_tot, 1e-8)

    return {"mse": mse, "mae": mae, "r2": r2, "preds": preds, "targets": targets}


def cosine_warmup_scheduler(optimizer, warmup_epochs, total_epochs):
    """Cosine annealing with linear warmup. PRODUCTION-READY."""
    def lr_lambda(epoch):
        if epoch < warmup_epochs:
            return epoch / max(warmup_epochs, 1)
        progress = (epoch - warmup_epochs) / max(total_epochs - warmup_epochs, 1)
        return 0.5 * (1 + math.cos(math.pi * progress))
    return torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda)


# ===========================================================================
# 4. MAIN: DATA LOADING + TRAINING
# ===========================================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(description="t-PatchGNN prototype for CASTLINE")
    parser.add_argument("--data", type=str,
                        default="castline/validation/data/assembled/validation_dataset_v17.csv",
                        help="Path to V17 dataset CSV")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--batch_size", type=int, default=16)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--hid_dim", type=int, default=64)
    parser.add_argument("--n_patches", type=int, default=8)
    parser.add_argument("--patience", type=int, default=15)
    parser.add_argument("--gpu", type=int, default=0)
    parser.add_argument("--distance_km", type=float, default=100.0)
    parser.add_argument("--max_neighbors", type=int, default=30)
    parser.add_argument("--val_cutoff", type=str, default="2024-01-01")
    parser.add_argument("--test_cutoff", type=str, default="2025-01-01")
    parser.add_argument("--target", type=str, default="baseline_signal")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    # --- Seed ---
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    # --- Device ---
    if args.gpu < 0:
        device = torch.device("cpu")
    else:
        device = torch.device(f"cuda:{args.gpu}" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    # --- Load data ---
    print(f"Loading data from {args.data}")
    df = pd.read_csv(args.data)
    df["date"] = pd.to_datetime(df["date"])
    print(f"  Total rows: {len(df)}, Locations: {df['location'].nunique()}")

    # Filter to rows with valid target
    df_valid = df.dropna(subset=[args.target]).copy()
    print(f"  Valid target rows: {len(df_valid)}")

    # --- Build location registry ---
    registry = build_location_registry(df)
    print(f"  Location registry: {len(registry)} locations")

    # --- Build adjacency ---
    coords = registry[["lat", "lon"]].values
    print(f"  Building adjacency matrix (threshold={args.distance_km} km)...")
    adj = build_adjacency(coords, threshold_km=args.distance_km)
    avg_neighbors = (adj > 0).sum(axis=1).mean() - 1  # subtract self-loop
    print(f"  Average neighbors per node: {avg_neighbors:.1f}")

    # --- Walk-forward split ---
    train_mask = df_valid["date"] < args.val_cutoff
    val_mask = (df_valid["date"] >= args.val_cutoff) & (df_valid["date"] < args.test_cutoff)
    test_mask = df_valid["date"] >= args.test_cutoff

    df_train = df_valid[train_mask].reset_index(drop=True)
    df_val = df_valid[val_mask].reset_index(drop=True)
    df_test = df_valid[test_mask].reset_index(drop=True)
    print(f"  Train: {len(df_train)}, Val: {len(df_val)}, Test: {len(df_test)}")

    if len(df_val) == 0:
        warnings.warn("No validation data! Adjust --val_cutoff.")
    if len(df_test) == 0:
        warnings.warn("No test data! Adjust --test_cutoff.")

    # --- Datasets ---
    n_channels = len([c for c in ENV_CHANNELS if c in df.columns])
    static_dim = len([c for c in STATIC_FEATURES if c in registry.columns])

    train_ds = CastlineGraphDataset(
        df_train, registry, adj,
        n_patches=args.n_patches,
        max_neighbors=args.max_neighbors,
        target_col=args.target,
    )
    val_ds = CastlineGraphDataset(
        df_val, registry, adj,
        n_patches=args.n_patches,
        max_neighbors=args.max_neighbors,
        target_col=args.target,
    )
    test_ds = CastlineGraphDataset(
        df_test, registry, adj,
        n_patches=args.n_patches,
        max_neighbors=args.max_neighbors,
        target_col=args.target,
    )

    train_loader = DataLoader(
        train_ds, batch_size=args.batch_size, shuffle=True,
        collate_fn=collate_graph_batch, num_workers=0,
    )
    val_loader = DataLoader(
        val_ds, batch_size=args.batch_size, shuffle=False,
        collate_fn=collate_graph_batch, num_workers=0,
    )
    test_loader = DataLoader(
        test_ds, batch_size=args.batch_size, shuffle=False,
        collate_fn=collate_graph_batch, num_workers=0,
    )

    # --- Model ---
    max_subgraph_nodes = args.max_neighbors + 1
    model = tPatchGNN(
        n_channels=n_channels,
        te_dim=16,
        ttcn_dim=32,
        hid_dim=args.hid_dim,
        node_dim=16,
        static_dim=static_dim,
        n_patches=args.n_patches,
        max_nodes=max_subgraph_nodes,
        n_tf_layers=2,
        n_tf_heads=4,
        n_gnn_layers=2,
        gcn_order=2,
        dropout=0.1,
    ).to(device)

    n_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"  Model parameters: {n_params:,}")

    # --- Optimizer ---
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    scheduler = cosine_warmup_scheduler(optimizer, warmup_epochs=5, total_epochs=args.epochs)

    # --- Training ---
    best_val_loss = float("inf")
    best_epoch = 0
    patience_counter = 0
    save_path = Path("castline/models/tpatchgnn_best.pt")
    save_path.parent.mkdir(parents=True, exist_ok=True)

    print("\n--- Training ---")
    for epoch in range(1, args.epochs + 1):
        train_loss = train_epoch(model, train_loader, optimizer, device)
        scheduler.step()

        # Validate
        val_metrics = evaluate(model, val_loader, device) if len(df_val) > 0 else {"mse": train_loss, "r2": 0}

        lr = optimizer.param_groups[0]["lr"]
        print(
            f"Epoch {epoch:3d} | "
            f"Train Loss: {train_loss:.4f} | "
            f"Val MSE: {val_metrics['mse']:.4f} | "
            f"Val R²: {val_metrics['r2']:.4f} | "
            f"LR: {lr:.6f}"
        )

        # Early stopping
        if val_metrics["mse"] < best_val_loss:
            best_val_loss = val_metrics["mse"]
            best_epoch = epoch
            patience_counter = 0
            torch.save(model.state_dict(), save_path)
        else:
            patience_counter += 1
            if patience_counter >= args.patience:
                print(f"\nEarly stopping at epoch {epoch} (best: {best_epoch})")
                break

    # --- Test evaluation ---
    if len(df_test) > 0 and save_path.exists():
        print("\n--- Test Evaluation ---")
        model.load_state_dict(torch.load(save_path, map_location=device))
        test_metrics = evaluate(model, test_loader, device)
        print(f"Test MSE:  {test_metrics['mse']:.4f}")
        print(f"Test MAE:  {test_metrics['mae']:.4f}")
        print(f"Test R²:   {test_metrics['r2']:.4f}")

        # Save predictions
        pred_path = Path("castline/models/tpatchgnn_predictions.csv")
        pd.DataFrame({
            "predicted": test_metrics["preds"],
            "actual": test_metrics["targets"],
        }).to_csv(pred_path, index=False)
        print(f"Predictions saved to {pred_path}")

    print("\n--- Comparison with V15 Ensemble ---")
    print(f"V15 Walk-forward R²: 0.447")
    if len(df_test) > 0 and save_path.exists():
        print(f"t-PatchGNN Test R²:  {test_metrics['r2']:.4f}")
        delta = test_metrics["r2"] - 0.447
        print(f"Delta: {delta:+.4f} ({'BETTER' if delta > 0 else 'WORSE'})")
    else:
        print("(no test results available)")

    print("\nDone!")


if __name__ == "__main__":
    main()
