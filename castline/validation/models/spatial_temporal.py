"""Spatio-Temporal Graph Neural Network for CASTLINE.

Integrates:
1. GNN (Graph Neural Network) for spatial generalization across locations
2. TFT-inspired temporal encoder for environmental time series
3. Cross-modal fusion for the final prediction

The key insight: the GNN learns that "similar lakes produce similar fish"
while the temporal encoder learns "how conditions affect fishing NOW".
Together they solve both spatial generalization AND temporal dynamics.

Architecture:
    Location Graph (GNN) → Spatial Embedding
    Environmental Time Series (LSTM+Attention) → Temporal Embedding
    Static Features → Static Embedding
    [Spatial ⊕ Temporal ⊕ Static] → Fusion → Predicted Catch Rate
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader


# ──────────────────────────────────────────────────────────────────
# 1. Graph Neural Network for Spatial Generalization
# ──────────────────────────────────────────────────────────────────

class SpatialGNN(nn.Module):
    """Graph Neural Network that learns location embeddings.

    Nodes represent fishing locations with physical features.
    Edges connect locations that are:
    - Geographically close
    - In the same watershed/ecoregion
    - Morphometrically similar (same lake type)

    Message passing propagates information between similar locations,
    so a new unseen lake can get a reasonable embedding from its neighbors.
    """

    def __init__(self, node_dim: int, hidden_dim: int = 64, n_layers: int = 2):
        super().__init__()
        self.node_encoder = nn.Sequential(
            nn.Linear(node_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(0.1),
        )

        self.message_layers = nn.ModuleList()
        self.update_layers = nn.ModuleList()
        for _ in range(n_layers):
            self.message_layers.append(nn.Sequential(
                nn.Linear(hidden_dim * 2, hidden_dim),
                nn.ReLU(),
            ))
            self.update_layers.append(nn.Sequential(
                nn.Linear(hidden_dim * 2, hidden_dim),
                nn.ReLU(),
                nn.Dropout(0.1),
            ))

        self.output_dim = hidden_dim

    def forward(self, node_features: torch.Tensor, edge_index: torch.Tensor) -> torch.Tensor:
        """
        Args:
            node_features: [N, node_dim] - physical features per location
            edge_index: [2, E] - edge indices (source, target)

        Returns:
            node_embeddings: [N, hidden_dim]
        """
        h = self.node_encoder(node_features)

        for msg_layer, upd_layer in zip(self.message_layers, self.update_layers):
            # Gather neighbor features
            src, dst = edge_index[0], edge_index[1]
            src_h = h[src]  # [E, hidden_dim]
            dst_h = h[dst]  # [E, hidden_dim]

            # Compute messages
            messages = msg_layer(torch.cat([src_h, dst_h], dim=-1))  # [E, hidden_dim]

            # Aggregate messages per node (mean aggregation)
            agg = torch.zeros_like(h)
            count = torch.zeros(h.size(0), 1, device=h.device)
            agg.index_add_(0, dst, messages)
            count.index_add_(0, dst, torch.ones(len(dst), 1, device=h.device))
            count = count.clamp(min=1)
            agg = agg / count

            # Update node embeddings
            h = upd_layer(torch.cat([h, agg], dim=-1))

        return h


# ──────────────────────────────────────────────────────────────────
# 2. Temporal Encoder (TFT-inspired)
# ──────────────────────────────────────────────────────────────────

class GatedResidualNetwork(nn.Module):
    """GRN from the Temporal Fusion Transformer paper."""

    def __init__(self, input_dim: int, hidden_dim: int, output_dim: int, dropout: float = 0.1):
        super().__init__()
        self.fc1 = nn.Linear(input_dim, hidden_dim)
        self.fc2 = nn.Linear(hidden_dim, output_dim)
        self.gate = nn.Linear(hidden_dim, output_dim)
        self.layer_norm = nn.LayerNorm(output_dim)
        self.dropout = nn.Dropout(dropout)

        if input_dim != output_dim:
            self.skip = nn.Linear(input_dim, output_dim)
        else:
            self.skip = None

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        residual = self.skip(x) if self.skip else x
        h = F.elu(self.fc1(x))
        h = self.dropout(h)
        h = self.fc2(h)
        gate = torch.sigmoid(self.gate(F.elu(self.fc1(x))))
        return self.layer_norm(gate * h + residual)


class TemporalEncoder(nn.Module):
    """LSTM + Multi-Head Attention for environmental time series.

    Takes a sequence of environmental readings (e.g., 72h at 15-min = 288 steps)
    and produces a temporal embedding that captures:
    - Trends (is water temp rising?)
    - Events (discharge spike 6h ago)
    - Stability (how variable were conditions?)
    - Attention to key moments (what mattered most?)
    """

    def __init__(
        self,
        input_dim: int,   # Number of environmental variables per timestep
        hidden_dim: int = 64,
        n_heads: int = 4,
        n_lstm_layers: int = 2,
        dropout: float = 0.1,
    ):
        super().__init__()

        self.input_proj = nn.Linear(input_dim, hidden_dim)

        self.lstm = nn.LSTM(
            input_size=hidden_dim,
            hidden_size=hidden_dim,
            num_layers=n_lstm_layers,
            batch_first=True,
            dropout=dropout if n_lstm_layers > 1 else 0,
            bidirectional=False,
        )

        self.attention = nn.MultiheadAttention(
            embed_dim=hidden_dim,
            num_heads=n_heads,
            dropout=dropout,
            batch_first=True,
        )

        self.grn = GatedResidualNetwork(hidden_dim, hidden_dim * 2, hidden_dim, dropout)
        self.output_dim = hidden_dim

    def forward(self, x: torch.Tensor, mask: Optional[torch.Tensor] = None) -> torch.Tensor:
        """
        Args:
            x: [B, T, input_dim] - environmental time series
            mask: [B, T] - True for valid timesteps, False for padding

        Returns:
            temporal_embedding: [B, hidden_dim]
        """
        # Project inputs
        h = self.input_proj(x)  # [B, T, hidden_dim]

        # LSTM encoding
        lstm_out, _ = self.lstm(h)  # [B, T, hidden_dim]

        # Self-attention over the sequence
        key_padding_mask = ~mask if mask is not None else None
        attn_out, attn_weights = self.attention(
            lstm_out, lstm_out, lstm_out,
            key_padding_mask=key_padding_mask,
        )

        # Use last timestep + attention-weighted context
        last_hidden = lstm_out[:, -1, :]  # [B, hidden_dim]
        context = attn_out.mean(dim=1)    # [B, hidden_dim]

        # Fuse via GRN
        combined = last_hidden + context
        return self.grn(combined)


# ──────────────────────────────────────────────────────────────────
# 3. Full Spatio-Temporal Model
# ──────────────────────────────────────────────────────────────────

class CastlineSTGNN(nn.Module):
    """Full Spatio-Temporal Graph Neural Network.

    Combines spatial (GNN), temporal (LSTM+Attention), and static features
    for catch rate prediction.
    """

    def __init__(
        self,
        node_dim: int = 5,         # Physical features per location
        temporal_dim: int = 6,     # Environmental variables per timestep
        static_dim: int = 15,      # Static contextual features
        hidden_dim: int = 64,
        gnn_layers: int = 2,
        n_heads: int = 4,
        dropout: float = 0.1,
    ):
        super().__init__()

        # Spatial encoder
        self.gnn = SpatialGNN(node_dim, hidden_dim, gnn_layers)

        # Temporal encoder
        self.temporal = TemporalEncoder(
            temporal_dim, hidden_dim, n_heads,
            n_lstm_layers=2, dropout=dropout,
        )

        # Static feature encoder
        self.static_encoder = GatedResidualNetwork(
            static_dim, hidden_dim * 2, hidden_dim, dropout,
        )

        # Cross-modal fusion
        fusion_dim = hidden_dim * 3  # spatial + temporal + static
        self.fusion = nn.Sequential(
            nn.Linear(fusion_dim, hidden_dim * 2),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim * 2, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
        )

        # Output heads
        self.mean_head = nn.Linear(hidden_dim, 1)      # Predicted catch rate
        self.var_head = nn.Linear(hidden_dim, 1)        # Prediction uncertainty

    def forward(
        self,
        node_features: torch.Tensor,     # [N, node_dim]
        edge_index: torch.Tensor,        # [2, E]
        location_idx: torch.Tensor,      # [B] - index into node_features
        temporal_input: torch.Tensor,    # [B, T, temporal_dim]
        static_input: torch.Tensor,      # [B, static_dim]
        temporal_mask: Optional[torch.Tensor] = None,  # [B, T]
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """
        Returns:
            mean: [B, 1] - predicted catch rate
            log_var: [B, 1] - log variance (uncertainty)
        """
        # 1. Spatial embedding via GNN
        all_embeddings = self.gnn(node_features, edge_index)  # [N, hidden]
        spatial = all_embeddings[location_idx]                 # [B, hidden]

        # 2. Temporal embedding
        temporal = self.temporal(temporal_input, temporal_mask)  # [B, hidden]

        # 3. Static embedding
        static = self.static_encoder(static_input)              # [B, hidden]

        # 4. Cross-modal fusion
        fused = self.fusion(torch.cat([spatial, temporal, static], dim=-1))

        # 5. Output
        mean = self.mean_head(fused)
        log_var = self.var_head(fused)

        return mean, log_var

    def loss(self, mean, log_var, target):
        """Heteroscedastic Gaussian NLL loss.

        The model learns to output both the prediction AND its uncertainty.
        This naturally handles noisy targets (tournament data is inherently noisy).
        """
        var = torch.exp(log_var).clamp(min=1e-6)
        nll = 0.5 * (torch.log(var) + (target - mean) ** 2 / var)
        return nll.mean()


# ──────────────────────────────────────────────────────────────────
# 4. Graph Construction
# ──────────────────────────────────────────────────────────────────

def build_location_graph(
    locations: list[dict],
    distance_threshold_km: float = 200.0,
    morphometry_similarity: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """Build a graph connecting similar fishing locations.

    Edge types:
    1. Geographic proximity (< threshold km)
    2. Morphometric similarity (same type of water body)
    3. Ecoregion (same ecological zone)

    Args:
        locations: list of dicts with keys: lat, lon, area_acres, max_depth_ft, etc.
        distance_threshold_km: max distance for geographic edges
        morphometry_similarity: whether to add morphometry-based edges

    Returns:
        node_features: [N, node_dim] array
        edge_index: [2, E] array
    """
    n = len(locations)

    # Node features
    node_features = np.zeros((n, 5))  # [area, depth, shore_dev, lat, is_lake]
    for i, loc in enumerate(locations):
        node_features[i, 0] = np.log1p(loc.get("area_acres", 0))
        node_features[i, 1] = np.log1p(loc.get("max_depth_ft", 0))
        node_features[i, 2] = loc.get("shore_dev", 1.0)
        node_features[i, 3] = loc.get("lat", 35.0) / 50.0  # Normalize
        node_features[i, 4] = 1.0 if loc.get("area_acres", 0) > 0 else 0.0

    # Build edges
    edges_src, edges_dst = [], []

    for i in range(n):
        for j in range(i + 1, n):
            connect = False

            # Geographic proximity
            lat1, lon1 = locations[i].get("lat", 0), locations[i].get("lon", 0)
            lat2, lon2 = locations[j].get("lat", 0), locations[j].get("lon", 0)
            dist = _haversine_km(lat1, lon1, lat2, lon2)
            if dist < distance_threshold_km:
                connect = True

            # Morphometric similarity
            if morphometry_similarity:
                area1 = locations[i].get("area_acres", 0)
                area2 = locations[j].get("area_acres", 0)
                depth1 = locations[i].get("max_depth_ft", 0)
                depth2 = locations[j].get("max_depth_ft", 0)

                # Similar size (within 3x)
                if area1 > 0 and area2 > 0:
                    ratio = max(area1, area2) / max(min(area1, area2), 1)
                    if ratio < 3.0:
                        depth_ratio = max(depth1, depth2) / max(min(depth1, depth2), 1)
                        if depth_ratio < 3.0:
                            connect = True

            if connect:
                edges_src.extend([i, j])
                edges_dst.extend([j, i])

    edge_index = np.array([edges_src, edges_dst]) if edges_src else np.zeros((2, 0), dtype=int)

    return node_features, edge_index


def _haversine_km(lat1, lon1, lat2, lon2):
    """Haversine distance between two points in km."""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


# ──────────────────────────────────────────────────────────────────
# 5. Dataset
# ──────────────────────────────────────────────────────────────────

class FishingDataset(Dataset):
    """Dataset for the ST-GNN model."""

    def __init__(
        self,
        events: list[dict],
        location_idx_map: dict[str, int],
        temporal_dim: int = 6,
        seq_len: int = 48,  # 48 timesteps (e.g., hourly for 48h)
    ):
        self.events = events
        self.location_idx_map = location_idx_map
        self.temporal_dim = temporal_dim
        self.seq_len = seq_len

    def __len__(self):
        return len(self.events)

    def __getitem__(self, idx):
        event = self.events[idx]

        # Location index
        loc_idx = self.location_idx_map.get(event["location"], 0)

        # Temporal input (IV data or synthetic)
        if "temporal_series" in event and event["temporal_series"] is not None:
            temporal = np.array(event["temporal_series"])[:self.seq_len]
            # Pad if needed
            if len(temporal) < self.seq_len:
                pad = np.zeros((self.seq_len - len(temporal), self.temporal_dim))
                temporal = np.vstack([pad, temporal])
            mask = np.ones(self.seq_len, dtype=bool)
        else:
            # Synthetic: expand static features with noise
            temporal = np.zeros((self.seq_len, self.temporal_dim))
            static_vals = [
                event.get("water_temp_c", 15.0),
                event.get("discharge_cfs", 100.0),
                event.get("gage_height_ft", 3.0),
                event.get("pressure_mb", 1013.0),
                event.get("wind_speed_kph", 10.0),
                event.get("air_temp_c", 20.0),
            ][:self.temporal_dim]
            for t in range(self.seq_len):
                for d, val in enumerate(static_vals):
                    if np.isnan(val):
                        val = 0.0
                    noise = np.random.normal(0, abs(val) * 0.05)
                    temporal[t, d] = val + noise * (1 - t / self.seq_len)
            mask = np.ones(self.seq_len, dtype=bool)

        # Static features
        static = np.array([
            event.get("day_length_hours", 12.0),
            event.get("season_cos", 0.0),
            event.get("season_sin", 0.0),
            event.get("spawn_phase_score", 0.0),
            event.get("moon_illumination_pct", 50.0),
            event.get("solunar_score", 0.5),
            event.get("cloud_cover_pct", 50.0),
            event.get("precip_24h_mm", 0.0),
            event.get("turnover_proximity", 0.0),
            event.get("thermal_stability", 0.5),
            event.get("wind_fetch_score", 0.5),
            event.get("windblown_quality", 0.5),
            event.get("pressure_fishing_score", 0.5),
            event.get("is_lake", 1.0),
            event.get("year", 2020) / 2030.0,  # Normalize
        ], dtype=np.float32)

        # Replace NaN with 0
        static = np.nan_to_num(static, nan=0.0)
        temporal = np.nan_to_num(temporal, nan=0.0)

        # Target
        target = event.get("median_weight_lb", 0.0)

        return {
            "location_idx": torch.tensor(loc_idx, dtype=torch.long),
            "temporal": torch.tensor(temporal, dtype=torch.float32),
            "temporal_mask": torch.tensor(mask, dtype=torch.bool),
            "static": torch.tensor(static, dtype=torch.float32),
            "target": torch.tensor(target, dtype=torch.float32),
        }


# ──────────────────────────────────────────────────────────────────
# 6. Training
# ──────────────────────────────────────────────────────────────────

def train_model(
    train_events: list[dict],
    val_events: list[dict],
    locations: list[dict],
    location_names: list[str],
    epochs: int = 100,
    lr: float = 1e-3,
    batch_size: int = 32,
    hidden_dim: int = 64,
    device: str = "cpu",
) -> CastlineSTGNN:
    """Train the full ST-GNN model."""

    # Build graph
    node_features, edge_index = build_location_graph(locations)
    node_features_t = torch.tensor(node_features, dtype=torch.float32).to(device)
    edge_index_t = torch.tensor(edge_index, dtype=torch.long).to(device)

    print(f"Graph: {len(locations)} nodes, {edge_index.shape[1]} edges")
    print(f"Avg edges per node: {edge_index.shape[1] / max(len(locations), 1):.1f}")

    # Location index map
    loc_idx_map = {name: i for i, name in enumerate(location_names)}

    # Datasets
    train_ds = FishingDataset(train_events, loc_idx_map)
    val_ds = FishingDataset(val_events, loc_idx_map)
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=batch_size)

    # Model
    model = CastlineSTGNN(
        node_dim=node_features.shape[1],
        temporal_dim=6,
        static_dim=15,
        hidden_dim=hidden_dim,
        gnn_layers=2,
        n_heads=4,
        dropout=0.1,
    ).to(device)

    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)

    best_val_loss = float("inf")
    best_state = None
    patience = 15
    wait = 0

    for epoch in range(epochs):
        # Train
        model.train()
        train_loss = 0
        n_batches = 0
        for batch in train_loader:
            optimizer.zero_grad()

            mean, log_var = model(
                node_features_t, edge_index_t,
                batch["location_idx"].to(device),
                batch["temporal"].to(device),
                batch["static"].to(device),
                batch["temporal_mask"].to(device),
            )

            target = batch["target"].to(device).unsqueeze(1)
            loss = model.loss(mean, log_var, target)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()

            train_loss += loss.item()
            n_batches += 1

        scheduler.step()

        # Validate
        model.eval()
        val_preds, val_targets = [], []
        val_loss = 0
        n_val = 0
        with torch.no_grad():
            for batch in val_loader:
                mean, log_var = model(
                    node_features_t, edge_index_t,
                    batch["location_idx"].to(device),
                    batch["temporal"].to(device),
                    batch["static"].to(device),
                    batch["temporal_mask"].to(device),
                )
                target = batch["target"].to(device).unsqueeze(1)
                val_loss += model.loss(mean, log_var, target).item()
                n_val += 1

                val_preds.extend(mean.squeeze().cpu().numpy().tolist())
                val_targets.extend(target.squeeze().cpu().numpy().tolist())

        val_loss /= max(n_val, 1)
        train_loss /= max(n_batches, 1)

        # Compute R²
        val_preds_arr = np.array(val_preds)
        val_targets_arr = np.array(val_targets)
        ss_res = np.sum((val_targets_arr - val_preds_arr) ** 2)
        ss_tot = np.sum((val_targets_arr - val_targets_arr.mean()) ** 2)
        val_r2 = 1 - ss_res / max(ss_tot, 1e-8)

        if (epoch + 1) % 10 == 0 or epoch == 0:
            print(f"  Epoch {epoch+1:>3}: train_loss={train_loss:.4f} val_loss={val_loss:.4f} val_R2={val_r2:.4f} lr={scheduler.get_last_lr()[0]:.6f}")

        # Early stopping
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            wait = 0
        else:
            wait += 1
            if wait >= patience:
                print(f"  Early stopping at epoch {epoch+1}")
                break

    if best_state:
        model.load_state_dict(best_state)

    return model
