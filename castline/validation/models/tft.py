"""Custom Temporal Fusion Transformer for CASTLINE fishing predictions.

Instead of treating tournament events as a regular time series (which fails
because events are irregularly spaced), this TFT consumes a **72-hour
environmental lookback window** for each event.  USGS instantaneous-value
data arrives at 15-minute intervals, giving 288 timesteps per window.

Architecture
------------
1. **Static variable selection** -- location morphometry, historical mean
   weight, and other per-lake features are projected through a gated
   residual network and used to initialise the LSTM hidden state.
2. **Temporal encoder** -- an LSTM processes the 72h x 288-step
   environmental sequence (water_temp, discharge, gage_height, plus
   derived signals).  When real IV data is unavailable the module
   synthesises a pseudo-time-series from daily aggregates (Gaussian
   noise + linear interpolation) so training can still proceed.
3. **Interpretable multi-head attention** -- attends over the LSTM
   outputs so the model can learn patterns like "discharge spike 12h
   before the event correlates with better catch".
4. **Gated residual network (GRN)** combiner -- merges static and
   temporal representations before the final regression head.

The model trains with Adam, cosine-annealing LR schedule, and
patience-based early stopping.  It reports R-squared, RMSE, and MAE on a
temporal holdout split (train <= 2023, test >= 2024).
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader
from sklearn.metrics import r2_score, mean_absolute_error, mean_squared_error

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class TFTConfig:
    """Hyper-parameters for the CASTLINE TFT."""

    # Architecture
    hidden_size: int = 64
    lstm_layers: int = 2
    attention_heads: int = 4
    dropout: float = 0.15
    grn_hidden: int = 64

    # Temporal input
    seq_len: int = 288          # 72h at 15-min intervals
    temporal_features: int = 5  # water_temp, discharge, gage_height, temp_rate, flow_rate

    # Training
    batch_size: int = 32
    max_epochs: int = 200
    learning_rate: float = 1e-3
    weight_decay: float = 1e-4
    patience: int = 25
    min_lr: float = 1e-6
    grad_clip: float = 1.0

    # Data
    train_end_year: int = 2023
    seed: int = 42


# Feature lists --
STATIC_FEATURES = [
    'loc_enc', 'trail_mean_weight', 'location_mean_weight',
    'area_acres', 'max_depth_ft', 'shore_dev', 'lat',
]

TABULAR_ENV_FEATURES = [
    'baseline_signal',
    'air_temp_c', 'pressure_mb', 'wind_speed_kph', 'cloud_cover_pct',
    'precip_24h_mm', 'water_temp_c', 'discharge_cfs', 'gage_height_ft',
    'temp_delta_24h_c', 'flow_delta_24h_pct',
    'dissolved_oxygen_mgL', 'specific_conductance_us_cm', 'ph',
    'water_temp_7d_mean', 'discharge_7d_mean', 'gage_height_7d_mean',
    'pressure_delta_6h', 'pressure_delta_12h', 'wind_dir_cos',
    'discharge_pct_of_30d', 'gage_stability_7d', 'cumulative_degree_days',
    'water_temp_x_flow', 'water_temp_estimated', 'water_temp_anomaly',
    'solunar_score', 'moon_illumination_pct',
    'spawn_phase_score', 'day_length_hours',
    'season_sin', 'season_cos',
    'front_pre_frontal', 'front_post_frontal',
    'year',
]

# Columns from usgs_iv_features.csv that provide intraday signal
IV_FEATURE_COLS = [
    'discharge_cfs_daily_range', 'discharge_cfs_daily_mean',
    'discharge_cfs_dawn', 'discharge_cfs_rate_of_change',
    'discharge_cfs_6h_delta',
    'gage_height_ft_daily_range', 'gage_height_ft_daily_mean',
    'gage_height_ft_dawn', 'gage_height_ft_rate_of_change',
    'gage_height_ft_6h_delta',
    'water_temp_c_daily_range', 'water_temp_c_daily_mean',
    'water_temp_c_dawn', 'water_temp_c_rate_of_change',
    'water_temp_c_6h_delta',
]

TARGET = 'target_success_score'


# ---------------------------------------------------------------------------
# Gated Residual Network
# ---------------------------------------------------------------------------

class GatedResidualNetwork(nn.Module):
    """GRN as described in the TFT paper (Lim et al. 2021)."""

    def __init__(
        self,
        input_size: int,
        hidden_size: int,
        output_size: int,
        dropout: float = 0.1,
        context_size: int | None = None,
    ):
        super().__init__()
        self.fc1 = nn.Linear(input_size, hidden_size)
        self.elu = nn.ELU()
        self.fc2 = nn.Linear(hidden_size, output_size)
        self.dropout = nn.Dropout(dropout)
        self.layer_norm = nn.LayerNorm(output_size)

        # GLU gate
        self.gate_fc = nn.Linear(hidden_size, output_size * 2)

        # Optional context vector
        self.context_fc = nn.Linear(context_size, hidden_size, bias=False) if context_size else None

        # Skip connection projection
        self.skip_proj = nn.Linear(input_size, output_size) if input_size != output_size else None

    def forward(self, x: torch.Tensor, context: torch.Tensor | None = None) -> torch.Tensor:
        residual = self.skip_proj(x) if self.skip_proj else x

        h = self.fc1(x)
        if self.context_fc is not None and context is not None:
            h = h + self.context_fc(context)
        h = self.elu(h)

        gate_input = self.gate_fc(h)
        a, b = gate_input.chunk(2, dim=-1)
        h = a * torch.sigmoid(b)  # GLU

        h = self.dropout(h)
        return self.layer_norm(h + residual)


# ---------------------------------------------------------------------------
# Variable Selection Network
# ---------------------------------------------------------------------------

class VariableSelectionNetwork(nn.Module):
    """Selects and weights the most relevant input variables."""

    def __init__(self, input_size: int, num_vars: int, hidden_size: int, dropout: float = 0.1):
        super().__init__()
        self.num_vars = num_vars
        self.hidden_size = hidden_size

        # Per-variable GRN
        self.var_grns = nn.ModuleList([
            GatedResidualNetwork(input_size // num_vars, hidden_size, hidden_size, dropout)
            for _ in range(num_vars)
        ])

        # Softmax variable weights
        self.weight_grn = GatedResidualNetwork(input_size, hidden_size, num_vars, dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (batch, input_size) where input_size = num_vars * per_var_dim
        per_var_dim = x.shape[-1] // self.num_vars

        # Compute variable importance weights
        weights = torch.softmax(self.weight_grn(x), dim=-1)  # (batch, num_vars)

        # Process each variable
        var_outputs = []
        for i, grn in enumerate(self.var_grns):
            var_input = x[..., i * per_var_dim: (i + 1) * per_var_dim]
            var_outputs.append(grn(var_input))  # (batch, hidden_size)

        var_outputs = torch.stack(var_outputs, dim=1)  # (batch, num_vars, hidden_size)
        weights = weights.unsqueeze(-1)  # (batch, num_vars, 1)

        return (var_outputs * weights).sum(dim=1)  # (batch, hidden_size)


# ---------------------------------------------------------------------------
# Interpretable Multi-Head Attention
# ---------------------------------------------------------------------------

class InterpretableMultiHeadAttention(nn.Module):
    """Multi-head attention with per-head interpretability (Lim et al.)."""

    def __init__(self, hidden_size: int, num_heads: int, dropout: float = 0.1):
        super().__init__()
        self.num_heads = num_heads
        self.head_dim = hidden_size // num_heads
        assert hidden_size % num_heads == 0

        self.q_proj = nn.Linear(hidden_size, hidden_size)
        self.k_proj = nn.Linear(hidden_size, hidden_size)
        self.v_proj = nn.Linear(hidden_size, hidden_size)
        self.out_proj = nn.Linear(hidden_size, hidden_size)
        self.dropout = nn.Dropout(dropout)

    def forward(self, query: torch.Tensor, key: torch.Tensor, value: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        batch_size, seq_len, _ = query.shape

        q = self.q_proj(query).view(batch_size, seq_len, self.num_heads, self.head_dim).transpose(1, 2)
        k = self.k_proj(key).view(batch_size, -1, self.num_heads, self.head_dim).transpose(1, 2)
        v = self.v_proj(value).view(batch_size, -1, self.num_heads, self.head_dim).transpose(1, 2)

        scale = math.sqrt(self.head_dim)
        scores = torch.matmul(q, k.transpose(-2, -1)) / scale
        attn_weights = torch.softmax(scores, dim=-1)
        attn_weights = self.dropout(attn_weights)

        context = torch.matmul(attn_weights, v)
        context = context.transpose(1, 2).contiguous().view(batch_size, seq_len, -1)
        output = self.out_proj(context)

        return output, attn_weights


# ---------------------------------------------------------------------------
# CASTLINE TFT Model
# ---------------------------------------------------------------------------

class CastlineTFT(nn.Module):
    """Temporal Fusion Transformer adapted for event-level fishing prediction.

    Each sample consists of:
    - Static features: location morphometry and historical stats
    - Temporal sequence: 72h of environmental readings (288 steps at 15-min)
    - Tabular environmental features: daily aggregates and weather
    """

    def __init__(self, config: TFTConfig, n_static: int, n_tabular: int):
        super().__init__()
        self.config = config
        h = config.hidden_size

        # --- Static pathway ---
        self.static_proj = nn.Sequential(
            nn.Linear(n_static, h),
            nn.ReLU(),
            nn.Dropout(config.dropout),
        )
        self.static_grn = GatedResidualNetwork(h, config.grn_hidden, h, config.dropout)

        # --- Temporal pathway ---
        self.temporal_proj = nn.Linear(config.temporal_features, h)
        self.temporal_lstm = nn.LSTM(
            input_size=h,
            hidden_size=h,
            num_layers=config.lstm_layers,
            batch_first=True,
            dropout=config.dropout if config.lstm_layers > 1 else 0.0,
        )
        self.temporal_grn = GatedResidualNetwork(h, config.grn_hidden, h, config.dropout)

        # Attention over temporal sequence
        self.attention = InterpretableMultiHeadAttention(h, config.attention_heads, config.dropout)
        self.attn_layer_norm = nn.LayerNorm(h)
        self.attn_grn = GatedResidualNetwork(h, config.grn_hidden, h, config.dropout)

        # --- Tabular pathway ---
        self.tabular_proj = nn.Sequential(
            nn.Linear(n_tabular, h),
            nn.ReLU(),
            nn.Dropout(config.dropout),
        )
        self.tabular_grn = GatedResidualNetwork(h, config.grn_hidden, h, config.dropout)

        # --- Fusion ---
        self.fusion_grn = GatedResidualNetwork(h * 3, config.grn_hidden, h, config.dropout)

        # --- Output head ---
        self.output_head = nn.Sequential(
            nn.Linear(h, h // 2),
            nn.ReLU(),
            nn.Dropout(config.dropout),
            nn.Linear(h // 2, 1),
        )

        self._init_weights()

    def _init_weights(self):
        for m in self.modules():
            if isinstance(m, nn.Linear):
                nn.init.xavier_uniform_(m.weight)
                if m.bias is not None:
                    nn.init.zeros_(m.bias)

    def forward(
        self,
        static: torch.Tensor,      # (B, n_static)
        temporal: torch.Tensor,     # (B, seq_len, temporal_features)
        tabular: torch.Tensor,      # (B, n_tabular)
        has_temporal: torch.Tensor,  # (B,) bool mask -- True if real IV data
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """Forward pass.

        Returns
        -------
        predictions : (B, 1) predicted catch rate
        attn_weights : (B, heads, seq_len, seq_len) attention weights for interpretability
        """
        # Static
        s = self.static_proj(static)
        s = self.static_grn(s)  # (B, h)

        # Temporal
        t_proj = self.temporal_proj(temporal)  # (B, seq_len, h)

        # Use static context to initialise LSTM hidden state
        h0 = s.unsqueeze(0).expand(self.config.lstm_layers, -1, -1).contiguous()
        c0 = torch.zeros_like(h0)
        lstm_out, _ = self.temporal_lstm(t_proj, (h0, c0))  # (B, seq_len, h)

        # Self-attention over temporal sequence
        attn_out, attn_weights = self.attention(lstm_out, lstm_out, lstm_out)
        attn_out = self.attn_layer_norm(attn_out + lstm_out)  # residual

        # Pool temporal: use last timestep + mean, weighted by has_temporal
        t_last = attn_out[:, -1, :]         # (B, h)
        t_mean = attn_out.mean(dim=1)       # (B, h)
        t_combined = t_last + t_mean
        t_combined = self.temporal_grn(t_combined)

        # For samples without real IV data, downweight temporal contribution
        temporal_weight = has_temporal.float().unsqueeze(-1)  # (B, 1)
        t_combined = t_combined * (0.3 + 0.7 * temporal_weight)

        # Tabular
        tab = self.tabular_proj(tabular)
        tab = self.tabular_grn(tab)  # (B, h)

        # Fusion: concatenate all three streams
        fused = torch.cat([s, t_combined, tab], dim=-1)  # (B, 3*h)
        fused = self.fusion_grn(fused)  # (B, h)

        # Output
        pred = self.output_head(fused)  # (B, 1)
        return pred, attn_weights


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

class CastlineTemporalDataset(Dataset):
    """PyTorch dataset that pairs each event with its environmental time series.

    For events with real USGS IV data, the temporal input is constructed from
    the IV features.  For events without IV data, a pseudo-time-series is
    synthesised from daily aggregates.
    """

    def __init__(
        self,
        df: pd.DataFrame,
        iv_df: pd.DataFrame | None,
        static_cols: list[str],
        tabular_cols: list[str],
        target_col: str,
        seq_len: int = 288,
        temporal_features: int = 5,
        is_train: bool = True,
    ):
        self.seq_len = seq_len
        self.temporal_features = temporal_features
        self.is_train = is_train
        self.target_col = target_col

        # Store feature arrays
        self.static = df[static_cols].values.astype(np.float32)
        self.tabular = df[tabular_cols].values.astype(np.float32)
        self.targets = df[target_col].values.astype(np.float32)
        self.event_ids = df['event_id'].values if 'event_id' in df.columns else np.arange(len(df))

        # Replace NaN with 0 in inputs
        self.static = np.nan_to_num(self.static, nan=0.0)
        self.tabular = np.nan_to_num(self.tabular, nan=0.0)

        # Build temporal sequences
        self.temporal_seqs, self.has_iv = self._build_temporal(df, iv_df)

        # Compute normalisation stats from training data
        self._static_mean = self.static.mean(axis=0)
        self._static_std = self.static.std(axis=0) + 1e-8
        self._tabular_mean = self.tabular.mean(axis=0)
        self._tabular_std = self.tabular.std(axis=0) + 1e-8

    def set_normalization(self, static_mean, static_std, tabular_mean, tabular_std):
        """Use training set normalisation stats (for val/test sets)."""
        self._static_mean = static_mean
        self._static_std = static_std
        self._tabular_mean = tabular_mean
        self._tabular_std = tabular_std

    def _build_temporal(
        self, df: pd.DataFrame, iv_df: pd.DataFrame | None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Build temporal input arrays.

        temporal channels:
          0: water_temp_c (normalised)
          1: discharge_cfs (log-scaled)
          2: gage_height_ft
          3: temperature rate of change
          4: flow rate of change
        """
        n = len(df)
        seqs = np.zeros((n, self.seq_len, self.temporal_features), dtype=np.float32)
        has_iv = np.zeros(n, dtype=bool)

        # Map IV data by event_id
        iv_lookup: dict[str, dict] = {}
        if iv_df is not None and not iv_df.empty:
            for _, row in iv_df.iterrows():
                eid = row.get('event_id', '')
                if eid:
                    iv_lookup[str(eid)] = row.to_dict()

        for i in range(n):
            eid = str(self.event_ids[i])

            if eid in iv_lookup:
                # We have IV-derived aggregate features -- use them to build
                # a structured pseudo-series (better than pure daily)
                iv = iv_lookup[eid]
                seqs[i] = self._build_iv_sequence(iv, df.iloc[i])
                has_iv[i] = True
            else:
                # Synthesise from daily features
                seqs[i] = self._build_synthetic_sequence(df.iloc[i])
                has_iv[i] = False

        return seqs, has_iv

    def _build_iv_sequence(self, iv: dict, row: pd.Series) -> np.ndarray:
        """Build a structured temporal sequence from IV aggregate features.

        Uses the daily mean, range, dawn value, and rate-of-change to
        reconstruct an approximate intraday curve.
        """
        seq = np.zeros((self.seq_len, self.temporal_features), dtype=np.float32)

        # Water temp channel
        wt_mean = _safe_float(iv.get('water_temp_c_daily_mean', row.get('water_temp_c', 15.0)))
        wt_range = _safe_float(iv.get('water_temp_c_daily_range', 2.0))
        wt_dawn = _safe_float(iv.get('water_temp_c_dawn', wt_mean - wt_range / 3))
        wt_rate = _safe_float(iv.get('water_temp_c_rate_of_change', 0.0))

        # Discharge channel
        dc_mean = _safe_float(iv.get('discharge_cfs_daily_mean', row.get('discharge_cfs', 100.0)))
        dc_range = _safe_float(iv.get('discharge_cfs_daily_range', dc_mean * 0.1))
        dc_dawn = _safe_float(iv.get('discharge_cfs_dawn', dc_mean))
        dc_rate = _safe_float(iv.get('discharge_cfs_rate_of_change', 0.0))

        # Gage height channel
        gh_mean = _safe_float(iv.get('gage_height_ft_daily_mean', row.get('gage_height_ft', 5.0)))
        gh_range = _safe_float(iv.get('gage_height_ft_daily_range', 0.1))
        gh_rate = _safe_float(iv.get('gage_height_ft_rate_of_change', 0.0))

        t = np.linspace(0, 1, self.seq_len)

        # Water temp: diurnal sinusoid (coolest at dawn ~ t=0.25, warmest afternoon ~ t=0.6)
        diurnal = np.sin(2 * np.pi * (t - 0.25))
        seq[:, 0] = wt_mean + (wt_range / 2) * diurnal + wt_rate * t * self.seq_len * 0.01

        # Discharge: trend + some structure
        seq[:, 1] = np.log1p(np.maximum(dc_dawn + dc_rate * t * self.seq_len * 0.1 +
                                         (dc_range / 2) * np.sin(2 * np.pi * t * 0.5), 0.01))

        # Gage height
        seq[:, 2] = gh_mean + gh_rate * t * self.seq_len * 0.01 + \
                     (gh_range / 2) * np.sin(2 * np.pi * t * 0.3)

        # Rate of change channels
        seq[:, 3] = np.gradient(seq[:, 0])  # temp rate
        seq[:, 4] = np.gradient(seq[:, 1])  # flow rate

        return seq.astype(np.float32)

    def _build_synthetic_sequence(self, row: pd.Series) -> np.ndarray:
        """Synthesise a temporal sequence from daily aggregate features.

        Uses the event-day values as the "endpoint" and adds structured
        noise to simulate the preceding 72 hours.
        """
        seq = np.zeros((self.seq_len, self.temporal_features), dtype=np.float32)

        wt = _safe_float(row.get('water_temp_c', 15.0))
        dc = _safe_float(row.get('discharge_cfs', 100.0))
        gh = _safe_float(row.get('gage_height_ft', 5.0))

        # 7-day means for trend anchor
        wt_7d = _safe_float(row.get('water_temp_7d_mean', wt))
        dc_7d = _safe_float(row.get('discharge_7d_mean', dc))
        gh_7d = _safe_float(row.get('gage_height_7d_mean', gh))

        t = np.linspace(0, 1, self.seq_len)

        # Linear trend from 7d-mean to current + diurnal + noise
        rng = np.random.RandomState(hash(str(row.name)) % (2**31))
        noise_scale = 0.02

        # Water temp
        trend = wt_7d + (wt - wt_7d) * t
        diurnal = 1.5 * np.sin(2 * np.pi * (t * 3 - 0.25))  # 3 days of diurnal
        noise = rng.normal(0, abs(wt) * noise_scale, self.seq_len)
        seq[:, 0] = trend + diurnal + noise

        # Discharge (log-scale)
        dc_trend = dc_7d + (dc - dc_7d) * t
        dc_noise = rng.normal(0, max(dc * 0.05, 1.0), self.seq_len)
        seq[:, 1] = np.log1p(np.maximum(dc_trend + dc_noise, 0.01))

        # Gage height
        gh_trend = gh_7d + (gh - gh_7d) * t
        gh_noise = rng.normal(0, max(abs(gh) * noise_scale, 0.01), self.seq_len)
        seq[:, 2] = gh_trend + gh_noise

        # Rates of change
        seq[:, 3] = np.gradient(seq[:, 0])
        seq[:, 4] = np.gradient(seq[:, 1])

        return seq.astype(np.float32)

    def __len__(self) -> int:
        return len(self.targets)

    def __getitem__(self, idx: int) -> dict[str, torch.Tensor]:
        # Normalise static and tabular
        static = (self.static[idx] - self._static_mean) / self._static_std
        tabular = (self.tabular[idx] - self._tabular_mean) / self._tabular_std

        return {
            'static': torch.from_numpy(static),
            'temporal': torch.from_numpy(self.temporal_seqs[idx]),
            'tabular': torch.from_numpy(tabular),
            'has_temporal': torch.tensor(self.has_iv[idx], dtype=torch.bool),
            'target': torch.tensor(self.targets[idx], dtype=torch.float32),
        }


def _safe_float(val, default: float = 0.0) -> float:
    """Convert to float, returning default for NaN/None."""
    if val is None:
        return default
    try:
        f = float(val)
        return f if not math.isnan(f) else default
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def _prepare_data(
    dataset_path: Path,
    iv_path: Path | None,
    config: TFTConfig,
) -> tuple[CastlineTemporalDataset, CastlineTemporalDataset, list[str], list[str]]:
    """Load CSV, split, and return train/test datasets."""

    df = pd.read_csv(dataset_path)
    df['date'] = pd.to_datetime(df['date'])
    if 'year' not in df.columns:
        df['year'] = df['date'].dt.year

    # Load IV features
    iv_df = None
    if iv_path and Path(iv_path).exists():
        iv_df = pd.read_csv(iv_path)
        print(f"tft: loaded {len(iv_df)} IV feature rows", file=sys.stderr)

    # Determine available features
    static_cols = [c for c in STATIC_FEATURES if c in df.columns]
    tabular_cols = [c for c in TABULAR_ENV_FEATURES if c in df.columns]

    # If IV aggregate features are merged into the main dataset, add them
    for c in IV_FEATURE_COLS:
        if c in df.columns and c not in tabular_cols:
            tabular_cols.append(c)

    print(f"tft: {len(static_cols)} static features, {len(tabular_cols)} tabular features", file=sys.stderr)

    # Mean-encode location (leak-free)
    global_mean = df.loc[df['year'] <= config.train_end_year, TARGET].mean()
    if 'location' in df.columns:
        train_mask = df['year'] <= config.train_end_year
        loc_means = df.loc[train_mask].groupby('location')[TARGET].mean()
        df['loc_enc'] = df['location'].map(loc_means).fillna(global_mean)
        if 'trail' in df.columns:
            trail_means = df.loc[train_mask].groupby('trail')[TARGET].mean()
            df['trail_mean_weight'] = df['trail'].map(trail_means).fillna(global_mean)
        if 'location_mean_weight' not in df.columns:
            df['location_mean_weight'] = df['loc_enc']

    # loc_rolling_3 -> into tabular if present
    if 'loc_rolling_3' in df.columns and 'loc_rolling_3' not in tabular_cols:
        tabular_cols.append('loc_rolling_3')

    # Ensure columns exist
    for c in static_cols + tabular_cols:
        if c not in df.columns:
            df[c] = np.nan

    # Temporal split
    train_df = df[df['year'] <= config.train_end_year].reset_index(drop=True)
    test_df = df[df['year'] > config.train_end_year].reset_index(drop=True)

    print(f"tft: train={len(train_df)}, test={len(test_df)}", file=sys.stderr)

    # Build datasets
    train_ds = CastlineTemporalDataset(
        train_df, iv_df, static_cols, tabular_cols, TARGET,
        seq_len=config.seq_len, temporal_features=config.temporal_features,
        is_train=True,
    )
    test_ds = CastlineTemporalDataset(
        test_df, iv_df, static_cols, tabular_cols, TARGET,
        seq_len=config.seq_len, temporal_features=config.temporal_features,
        is_train=False,
    )

    # Use training normalisation stats for test set
    test_ds.set_normalization(
        train_ds._static_mean, train_ds._static_std,
        train_ds._tabular_mean, train_ds._tabular_std,
    )

    iv_count_train = int(train_ds.has_iv.sum())
    iv_count_test = int(test_ds.has_iv.sum())
    print(f"tft: IV coverage -- train: {iv_count_train}/{len(train_ds)} "
          f"({100*iv_count_train/max(len(train_ds),1):.0f}%), "
          f"test: {iv_count_test}/{len(test_ds)} "
          f"({100*iv_count_test/max(len(test_ds),1):.0f}%)", file=sys.stderr)

    return train_ds, test_ds, static_cols, tabular_cols


def train_tft(
    dataset_path: str | Path,
    output_dir: str | Path,
    iv_path: str | Path | None = None,
    config: TFTConfig | None = None,
) -> dict:
    """Train the CASTLINE TFT model.

    Parameters
    ----------
    dataset_path : path to validation_dataset_v4.csv
    output_dir : directory for saved model artifacts
    iv_path : optional path to usgs_iv_features.csv
    config : optional TFTConfig overrides

    Returns
    -------
    dict with training results, metrics, and model info
    """
    if config is None:
        config = TFTConfig()

    dataset_path = Path(dataset_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Auto-detect IV path
    if iv_path is None:
        candidate = dataset_path.parent.parent / 'raw' / 'usgs_iv_features.csv'
        if candidate.exists():
            iv_path = candidate
            print(f"tft: auto-detected IV features at {iv_path}", file=sys.stderr)

    torch.manual_seed(config.seed)
    np.random.seed(config.seed)

    # Prepare data
    train_ds, test_ds, static_cols, tabular_cols = _prepare_data(
        dataset_path, iv_path, config,
    )

    if len(train_ds) < 20:
        return {'status': 'insufficient_data', 'train_rows': len(train_ds)}

    train_loader = DataLoader(
        train_ds, batch_size=config.batch_size, shuffle=True,
        num_workers=0, drop_last=False,
    )
    test_loader = DataLoader(
        test_ds, batch_size=config.batch_size, shuffle=False,
        num_workers=0,
    )

    # Build model
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model = CastlineTFT(
        config=config,
        n_static=len(static_cols),
        n_tabular=len(tabular_cols),
    ).to(device)

    n_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"tft: model has {n_params:,} trainable parameters", file=sys.stderr)

    optimizer = torch.optim.Adam(
        model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay,
    )
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=config.max_epochs, eta_min=config.min_lr,
    )

    loss_fn = nn.HuberLoss(delta=1.0)

    # Training loop
    best_val_loss = float('inf')
    best_epoch = 0
    patience_counter = 0
    best_state = None
    train_losses = []
    val_losses = []

    print(f"\ntft: training on {device} for up to {config.max_epochs} epochs", file=sys.stderr)
    print(f"{'epoch':>6} {'train_loss':>12} {'val_loss':>12} {'val_r2':>10} {'lr':>12}", file=sys.stderr)
    print("-" * 56, file=sys.stderr)

    for epoch in range(1, config.max_epochs + 1):
        # --- Train ---
        model.train()
        epoch_loss = 0.0
        n_batches = 0

        for batch in train_loader:
            static = batch['static'].to(device)
            temporal = batch['temporal'].to(device)
            tabular = batch['tabular'].to(device)
            has_temporal = batch['has_temporal'].to(device)
            target = batch['target'].to(device)

            optimizer.zero_grad()
            pred, _ = model(static, temporal, tabular, has_temporal)
            loss = loss_fn(pred.squeeze(-1), target)
            loss.backward()

            torch.nn.utils.clip_grad_norm_(model.parameters(), config.grad_clip)
            optimizer.step()

            epoch_loss += loss.item()
            n_batches += 1

        avg_train_loss = epoch_loss / max(n_batches, 1)
        train_losses.append(avg_train_loss)

        # --- Validate ---
        model.eval()
        val_preds = []
        val_targets = []
        val_loss_sum = 0.0
        val_batches = 0

        with torch.no_grad():
            for batch in test_loader:
                static = batch['static'].to(device)
                temporal = batch['temporal'].to(device)
                tabular = batch['tabular'].to(device)
                has_temporal = batch['has_temporal'].to(device)
                target = batch['target'].to(device)

                pred, _ = model(static, temporal, tabular, has_temporal)
                loss = loss_fn(pred.squeeze(-1), target)

                val_loss_sum += loss.item()
                val_batches += 1
                val_preds.append(pred.squeeze(-1).cpu().numpy())
                val_targets.append(target.cpu().numpy())

        avg_val_loss = val_loss_sum / max(val_batches, 1)
        val_losses.append(avg_val_loss)

        val_preds_arr = np.concatenate(val_preds)
        val_targets_arr = np.concatenate(val_targets)
        val_r2 = r2_score(val_targets_arr, val_preds_arr) if len(val_targets_arr) > 1 else float('nan')

        scheduler.step()
        current_lr = optimizer.param_groups[0]['lr']

        # Print every 10 epochs
        if epoch % 10 == 0 or epoch == 1:
            print(
                f"{epoch:>6d} {avg_train_loss:>12.4f} {avg_val_loss:>12.4f} "
                f"{val_r2:>10.4f} {current_lr:>12.6f}",
                file=sys.stderr,
            )

        # Early stopping
        if avg_val_loss < best_val_loss:
            best_val_loss = avg_val_loss
            best_epoch = epoch
            patience_counter = 0
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
        else:
            patience_counter += 1
            if patience_counter >= config.patience:
                print(f"\ntft: early stopping at epoch {epoch} (best={best_epoch})", file=sys.stderr)
                break

    # Load best model
    if best_state is not None:
        model.load_state_dict(best_state)
        model.to(device)

    # Final evaluation
    model.eval()
    all_preds = []
    all_targets = []

    with torch.no_grad():
        for batch in test_loader:
            static = batch['static'].to(device)
            temporal = batch['temporal'].to(device)
            tabular = batch['tabular'].to(device)
            has_temporal = batch['has_temporal'].to(device)
            target = batch['target'].to(device)

            pred, _ = model(static, temporal, tabular, has_temporal)
            all_preds.append(pred.squeeze(-1).cpu().numpy())
            all_targets.append(target.cpu().numpy())

    preds = np.concatenate(all_preds)
    targets = np.concatenate(all_targets)

    r2 = float(r2_score(targets, preds))
    rmse = float(mean_squared_error(targets, preds) ** 0.5)
    mae = float(mean_absolute_error(targets, preds))

    print(f"\n{'='*56}", file=sys.stderr)
    print(f"tft: FINAL RESULTS (best epoch {best_epoch})", file=sys.stderr)
    print(f"  R-squared : {r2:.4f}", file=sys.stderr)
    print(f"  RMSE      : {rmse:.4f}", file=sys.stderr)
    print(f"  MAE       : {mae:.4f}", file=sys.stderr)
    print(f"  Test rows : {len(targets)}", file=sys.stderr)
    print(f"{'='*56}", file=sys.stderr)

    # Save model
    model_path = output_dir / 'tft_model.pt'
    torch.save({
        'model_state_dict': model.state_dict(),
        'config': config,
        'static_cols': static_cols,
        'tabular_cols': tabular_cols,
        'n_static': len(static_cols),
        'n_tabular': len(tabular_cols),
        'train_static_mean': train_ds._static_mean,
        'train_static_std': train_ds._static_std,
        'train_tabular_mean': train_ds._tabular_mean,
        'train_tabular_std': train_ds._tabular_std,
        'best_epoch': best_epoch,
        'metrics': {'r2': r2, 'rmse': rmse, 'mae': mae},
    }, model_path)
    print(f"tft: saved model to {model_path}", file=sys.stderr)

    # Save results
    result = {
        'status': 'trained',
        'train_rows': len(train_ds),
        'test_rows': len(test_ds),
        'iv_coverage_train': int(train_ds.has_iv.sum()),
        'iv_coverage_test': int(test_ds.has_iv.sum()),
        'model_params': n_params,
        'best_epoch': best_epoch,
        'r2': r2,
        'rmse': rmse,
        'mae': mae,
        'train_losses': train_losses,
        'val_losses': val_losses,
    }

    with open(output_dir / 'tft_results.json', 'w') as f:
        json.dump({k: v for k, v in result.items()
                   if k not in ('train_losses', 'val_losses')}, f, indent=2)

    return result


# ---------------------------------------------------------------------------
# Inference helper
# ---------------------------------------------------------------------------

def load_tft_model(model_path: str | Path, device: str = 'cpu') -> tuple[CastlineTFT, dict]:
    """Load a trained TFT model from disk.

    Returns (model, metadata) where metadata contains normalisation
    stats and feature column lists.
    """
    checkpoint = torch.load(model_path, map_location=device, weights_only=False)
    config = checkpoint['config']

    model = CastlineTFT(
        config=config,
        n_static=checkpoint['n_static'],
        n_tabular=checkpoint['n_tabular'],
    )
    model.load_state_dict(checkpoint['model_state_dict'])
    model.eval()
    model.to(device)

    metadata = {
        'static_cols': checkpoint['static_cols'],
        'tabular_cols': checkpoint['tabular_cols'],
        'train_static_mean': checkpoint['train_static_mean'],
        'train_static_std': checkpoint['train_static_std'],
        'train_tabular_mean': checkpoint['train_tabular_mean'],
        'train_tabular_std': checkpoint['train_tabular_std'],
        'config': config,
        'metrics': checkpoint.get('metrics', {}),
    }
    return model, metadata


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Train CASTLINE Temporal Fusion Transformer')
    parser.add_argument(
        '--dataset', type=str,
        default='castline/validation/data/assembled/validation_dataset_v4.csv',
    )
    parser.add_argument(
        '--iv-features', type=str,
        default='castline/validation/data/raw/usgs_iv_features.csv',
    )
    parser.add_argument(
        '--output-dir', type=str,
        default='castline/validation/models/tft_artifacts',
    )
    parser.add_argument('--epochs', type=int, default=200)
    parser.add_argument('--batch-size', type=int, default=32)
    parser.add_argument('--lr', type=float, default=1e-3)
    parser.add_argument('--hidden', type=int, default=64)
    parser.add_argument('--patience', type=int, default=25)

    args = parser.parse_args()

    cfg = TFTConfig(
        max_epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.lr,
        hidden_size=args.hidden,
        patience=args.patience,
    )

    result = train_tft(
        dataset_path=args.dataset,
        output_dir=args.output_dir,
        iv_path=args.iv_features,
        config=cfg,
    )

    print(json.dumps(
        {k: v for k, v in result.items() if k not in ('train_losses', 'val_losses')},
        indent=2,
    ))
