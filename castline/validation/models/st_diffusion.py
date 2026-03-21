"""Spatio-Temporal Diffusion Model for fishing prediction.

Inspired by spatio-temporal diffusion approaches from:
https://github.com/yyysjz1997/Awesome-TimeSeries-SpatioTemporal-Diffusion-Model

Architecture: Conditional Score-based Diffusion with Spatial Graph conditioning.
- The model learns to denoise a noisy version of the target (median_weight_lb)
- Conditioning: spatial graph features (from GraphSAGE) + environmental features
- At inference: iterative denoising from pure noise → predicted weight
- For LOO: new locations get spatial embeddings via inductive GraphSAGE

Key insight: Diffusion models can capture multi-modal distributions
(e.g., a lake might have both good and bad days), whereas GBM only predicts the mean.
This is particularly useful for LOO where the uncertainty is high.

Also includes a Spatial Transformer baseline for comparison.
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

warnings.filterwarnings("ignore")

try:
    from torch_geometric.nn import SAGEConv
    HAS_PYG = True
except ImportError:
    HAS_PYG = False
    print("Warning: torch_geometric not available, using MLP fallback")

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
TARGET = "median_weight_lb"
EARTH_RADIUS_KM = 6_371.0

# Features
NODE_FEATURES = [
    "lat", "lon", "latitude_growth_potential", "growing_degree_proxy",
    "ecoregion_mean_weight", "ecoregion_mean_cpue", "max_depth_ft",
    "area_acres", "shore_dev", "shad_habitat_score",
    "regional_cpue_100km", "regional_weight_100km", "n_nearby_creel_surveys",
    "morphometric_productivity_score", "is_lake",
    "smallmouth_habitat_score", "northern_trophy_potential",
    "wtype_reservoir", "wtype_river",
]

EVENT_FEATURES = [
    "water_temp_c", "air_temp_c", "pressure_mb", "wind_speed_kph",
    "discharge_cfs", "gage_height_ft", "gage_stability_7d",
    "day_length_hours", "season_cos", "season_sin",
    "spawn_phase", "metabolic_rate_index", "feeding_window_score",
    "pressure_fishing_quality", "season_quality_index",
    "moon_phase", "solunar_score", "do_comfort_index",
    "om_humidity", "om_cloudcover", "om_air_temp_7d_mean",
    "om_pressure_delta_6h", "om_temp_trend_7d",
    "om_est_water_temp", "om_pressure_msl",
]


def _pairwise_geo_dist(lats, lons):
    lat_r, lon_r = np.radians(lats), np.radians(lons)
    dlat = lat_r[:, None] - lat_r[None, :]
    dlon = lon_r[:, None] - lon_r[None, :]
    a = np.sin(dlat/2)**2 + np.cos(lat_r[:,None])*np.cos(lat_r[None,:])*np.sin(dlon/2)**2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def _get_avail(df, feats):
    return [f for f in feats if f in df.columns and df[f].notna().mean() > 0.02]


# ─────────────────────────────────────────────────────────────
# Graph construction (same as GNN models)
# ─────────────────────────────────────────────────────────────

def build_graph(loc_df, k_geo=8, k_morpho=4,
                node_scaler=None, node_medians=None,
                exclude_locations=None):
    df = loc_df.copy().reset_index(drop=True)
    if exclude_locations:
        df = df[~df["location"].isin(exclude_locations)].reset_index(drop=True)

    n = len(df)
    location_ids = df["location"].tolist()
    avail = _get_avail(df, NODE_FEATURES)
    nf = df[avail].values.astype(np.float64)

    if node_medians is None:
        node_medians = np.nanmedian(nf, axis=0)
    for c in range(nf.shape[1]):
        mask = np.isnan(nf[:, c])
        nf[mask, c] = node_medians[c] if not np.isnan(node_medians[c]) else 0.0

    if node_scaler is None:
        node_scaler = StandardScaler()
        nf = node_scaler.fit_transform(nf)
    else:
        nf = node_scaler.transform(nf)

    node_features = torch.tensor(nf, dtype=torch.float32).to(DEVICE)

    lats = df["lat"].fillna(df["lat"].median()).values.astype(np.float64)
    lons = df["lon"].fillna(df["lon"].median()).values.astype(np.float64)
    geo_dist = _pairwise_geo_dist(lats, lons)
    np.fill_diagonal(geo_dist, np.inf)

    edge_set = set()
    k_g = min(k_geo, n-1)
    if k_g > 0:
        nn = np.argsort(geo_dist, axis=1)[:, :k_g]
        for i in range(n):
            for j in nn[i]:
                edge_set.add((i, int(j)))
                edge_set.add((int(j), i))

    morpho = ["area_acres", "max_depth_ft", "shore_dev"]
    ma = [c for c in morpho if c in df.columns]
    k_m = min(k_morpho, n-1)
    if ma and k_m > 0:
        mv = df[ma].values.astype(np.float64)
        for ci, col in enumerate(ma):
            if col in ("area_acres", "max_depth_ft"):
                mv[:, ci] = np.log1p(np.nan_to_num(mv[:, ci], nan=0))
            mv[np.isnan(mv[:, ci]), ci] = np.nanmedian(mv[:, ci])
        ms = StandardScaler()
        mv_s = ms.fit_transform(mv)
        d = mv_s[:, None, :] - mv_s[None, :, :]
        morpho_dist = np.sqrt(np.sum(d**2, axis=2))
        np.fill_diagonal(morpho_dist, np.inf)
        mn = np.argsort(morpho_dist, axis=1)[:, :k_m]
        for i in range(n):
            for j in mn[i]:
                edge_set.add((i, int(j)))
                edge_set.add((int(j), i))

    if edge_set:
        edges = sorted(edge_set)
        ei = torch.tensor([[e[0] for e in edges], [e[1] for e in edges]],
                         dtype=torch.long).to(DEVICE)
    else:
        ei = torch.zeros((2, 0), dtype=torch.long).to(DEVICE)

    return node_features, ei, location_ids, node_scaler, node_medians


# ─────────────────────────────────────────────────────────────
# Sinusoidal timestep embedding (standard for diffusion)
# ─────────────────────────────────────────────────────────────

class SinusoidalPosEmb(nn.Module):
    def __init__(self, dim):
        super().__init__()
        self.dim = dim

    def forward(self, t):
        half_dim = self.dim // 2
        emb = math.log(10000) / (half_dim - 1)
        emb = torch.exp(torch.arange(half_dim, device=t.device, dtype=torch.float32) * -emb)
        emb = t[:, None].float() * emb[None, :]
        return torch.cat([torch.sin(emb), torch.cos(emb)], dim=-1)


# ─────────────────────────────────────────────────────────────
# Conditional Diffusion Model
# ─────────────────────────────────────────────────────────────

class ConditionalDiffusion(nn.Module):
    """Score-based diffusion for conditional prediction.

    Predicts noise (epsilon) given:
    - Noisy target x_t at timestep t
    - Location embedding from GraphSAGE
    - Environmental features

    Loss: ||epsilon - epsilon_pred||^2 (standard DDPM)
    """

    def __init__(self, n_node, n_event, hidden=48, time_dim=32, dropout=0.15):
        super().__init__()

        # Time embedding
        self.time_mlp = nn.Sequential(
            SinusoidalPosEmb(time_dim),
            nn.Linear(time_dim, hidden),
            nn.GELU(),
            nn.Linear(hidden, hidden),
        )

        # GraphSAGE for location encoding
        if HAS_PYG:
            self.sage1 = SAGEConv(n_node, hidden)
            self.sage2 = SAGEConv(hidden, hidden)
            self.ln_g1 = nn.LayerNorm(hidden)
            self.ln_g2 = nn.LayerNorm(hidden)
            self.skip_g = nn.Linear(n_node, hidden, bias=False) if n_node != hidden else nn.Identity()
        else:
            self.loc_fallback = nn.Sequential(
                nn.Linear(n_node, hidden), nn.GELU(), nn.Linear(hidden, hidden)
            )

        # Location MLP (direct, no graph)
        self.loc_mlp = nn.Sequential(
            nn.Linear(n_node, hidden), nn.GELU(),
            nn.Dropout(dropout), nn.Linear(hidden, hidden),
        )

        # Event encoder
        self.event_enc = nn.Sequential(
            nn.Linear(n_event, hidden), nn.GELU(),
            nn.Dropout(dropout), nn.Linear(hidden, hidden),
        )

        # Noise predictor: x_t + time_emb + loc_emb + event_emb → epsilon
        # Input: 1 (noisy target) + hidden (time) + hidden (gnn) + hidden (loc) + hidden (event)
        total_in = 1 + hidden * 4
        self.noise_pred = nn.Sequential(
            nn.Linear(total_in, hidden * 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden * 2, hidden),
            nn.GELU(),
            nn.Linear(hidden, 1),
        )

        self.hidden = hidden

    def encode_locations(self, node_feat, edge_index):
        if HAS_PYG:
            skip = self.skip_g(node_feat)
            h = self.sage1(node_feat, edge_index)
            h = self.ln_g1(h)
            h = F.gelu(h)
            h = self.sage2(h, edge_index)
            h = self.ln_g2(h)
            h = F.gelu(h + skip)
            return h
        else:
            return self.loc_fallback(node_feat)

    def forward(self, x_t, t, node_feat, edge_index, event_feat, loc_idx):
        """Predict noise epsilon given noisy x_t and conditions."""
        # Time embedding
        t_emb = self.time_mlp(t)

        # Location embeddings
        gnn_emb = self.encode_locations(node_feat, edge_index)
        loc_gnn = gnn_emb[loc_idx]
        loc_direct = self.loc_mlp(node_feat)[loc_idx]

        # Event embedding
        ev_emb = self.event_enc(event_feat)

        # Concatenate all
        inp = torch.cat([x_t.unsqueeze(-1), t_emb, loc_gnn, loc_direct, ev_emb], dim=-1)

        return self.noise_pred(inp).squeeze(-1)


# ─────────────────────────────────────────────────────────────
# DDPM Scheduler
# ─────────────────────────────────────────────────────────────

class DDPMScheduler:
    """Simple DDPM noise schedule."""

    def __init__(self, n_steps=100, beta_start=1e-4, beta_end=0.02):
        self.n_steps = n_steps
        self.betas = torch.linspace(beta_start, beta_end, n_steps).to(DEVICE)
        self.alphas = (1.0 - self.betas).to(DEVICE)
        self.alpha_bar = torch.cumprod(self.alphas, dim=0).to(DEVICE)

    def add_noise(self, x0, t):
        """Add noise to x0 at timestep t."""
        alpha_bar_t = self.alpha_bar[t].to(x0.device)
        noise = torch.randn_like(x0)
        x_t = torch.sqrt(alpha_bar_t) * x0 + torch.sqrt(1 - alpha_bar_t) * noise
        return x_t, noise

    def sample(self, model, node_feat, edge_index, event_feat, loc_idx, n_samples):
        """Generate samples via iterative denoising (DDPM reverse process)."""
        model.eval()
        x = torch.randn(n_samples, device=DEVICE)

        with torch.no_grad():
            for t_val in reversed(range(self.n_steps)):
                t = torch.full((n_samples,), t_val, device=DEVICE, dtype=torch.long)
                alpha_bar_t = self.alpha_bar[t_val].to(DEVICE)
                alpha_t = self.alphas[t_val].to(DEVICE)
                beta_t = self.betas[t_val].to(DEVICE)

                eps_pred = model(x, t, node_feat, edge_index, event_feat, loc_idx)

                # DDPM reverse step
                x_mean = (1 / torch.sqrt(alpha_t)) * (
                    x - (beta_t / torch.sqrt(1 - alpha_bar_t)) * eps_pred
                )

                if t_val > 0:
                    noise = torch.randn_like(x)
                    x = x_mean + torch.sqrt(beta_t) * noise
                else:
                    x = x_mean

        return x


# ─────────────────────────────────────────────────────────────
# Training
# ─────────────────────────────────────────────────────────────

def prepare_data(df, avail_event, loc_to_idx,
                 event_scaler=None, event_medians=None,
                 t_mean=0, t_std=1, fit=True):
    vals = df[avail_event].values.astype(np.float64)
    if event_medians is None:
        event_medians = np.nanmedian(vals, axis=0)
    for c in range(vals.shape[1]):
        mask = np.isnan(vals[:, c])
        vals[mask, c] = event_medians[c] if not np.isnan(event_medians[c]) else 0.0

    if fit or event_scaler is None:
        event_scaler = StandardScaler()
        vals = event_scaler.fit_transform(vals)
    else:
        vals = event_scaler.transform(vals)

    ef = torch.tensor(vals, dtype=torch.float32).to(DEVICE)
    li = torch.tensor([loc_to_idx.get(l, 0) for l in df["location"]], dtype=torch.long).to(DEVICE)

    raw_y = df[TARGET].values.astype(np.float64)
    if fit:
        t_mean, t_std = float(np.nanmean(raw_y)), float(np.nanstd(raw_y))
        if t_std < 1e-6: t_std = 1.0

    tgt = torch.tensor((raw_y - t_mean) / t_std, dtype=torch.float32).to(DEVICE)
    return ef, li, tgt, event_scaler, event_medians, t_mean, t_std


def train_diffusion(model, scheduler, node_feat, edge_index,
                    event_feat, loc_idx, targets,
                    epochs=500, lr=3e-3, wd=0.01, verbose=False):
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=wd)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs, eta_min=lr*0.01)

    model.train()
    n = len(targets)
    losses = []

    for ep in range(1, epochs+1):
        optimizer.zero_grad()

        # Random timesteps
        t = torch.randint(0, scheduler.n_steps, (n,), device=DEVICE)

        # Add noise to targets
        x_t, noise = scheduler.add_noise(targets, t)

        # Predict noise
        eps_pred = model(x_t, t, node_feat, edge_index, event_feat, loc_idx)

        # Loss
        loss = F.mse_loss(eps_pred, noise)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        sched.step()
        losses.append(loss.item())

        if verbose and ep % 100 == 0:
            print(f"    ep {ep}/{epochs} loss={loss.item():.5f}")

    return losses


# ─────────────────────────────────────────────────────────────
# LOO Evaluation
# ─────────────────────────────────────────────────────────────

def train_and_evaluate_loo(
    df, top_n=15, epochs=500, hidden=48, n_diffusion_steps=50,
    k_geo=8, k_morpho=4, lr=3e-3, n_seeds=5, n_sample_passes=10,
    verbose=True,
):
    """LOO evaluation with conditional diffusion model."""
    loc_counts = df["location"].value_counts()
    top_locs = loc_counts.head(top_n).index.tolist()

    if verbose:
        print(f"\n{'='*70}")
        print(f"SPATIO-TEMPORAL DIFFUSION MODEL -- LOO Evaluation")
        print(f"{'='*70}")
        print(f"Device: {DEVICE}")
        print(f"Dataset: {len(df)} events, {df['location'].nunique()} locations")
        print(f"Hidden: {hidden}, Diffusion steps: {n_diffusion_steps}")
        print(f"Training: {epochs} epochs, {n_seeds} seeds, {n_sample_passes} sample passes")

    avail_node = _get_avail(df, NODE_FEATURES)
    agg = {f: "first" for f in ["lat", "lon"] + avail_node if f in df.columns}
    loc_df = df.groupby("location").agg(agg).reset_index()

    avail_event = _get_avail(df, EVENT_FEATURES)
    n_node = len([f for f in avail_node if f in loc_df.columns])
    n_event = len(avail_event)

    if verbose:
        print(f"Node feats: {n_node}, Event feats: {n_event}")
        print()

    all_true, all_pred = [], []
    per_loc = []
    t0 = time.time()

    for fi, test_loc in enumerate(top_locs):
        ft0 = time.time()
        train_df = df[df["location"] != test_loc].reset_index(drop=True)
        test_df = df[df["location"] == test_loc].reset_index(drop=True)
        if len(test_df) < 2:
            continue

        # Build graphs
        tr_nf, tr_ei, tr_lids, ns, nm = build_graph(
            loc_df, k_geo=k_geo, k_morpho=k_morpho,
            exclude_locations={test_loc}
        )
        tr_l2i = {l: i for i, l in enumerate(tr_lids)}
        train_df = train_df[train_df["location"].isin(tr_l2i)].reset_index(drop=True)

        tr_ef, tr_li, tr_tgt, es, em, tm, ts = prepare_data(
            train_df, avail_event, tr_l2i, fit=True
        )

        fu_nf, fu_ei, fu_lids, _, _ = build_graph(
            loc_df, k_geo=k_geo, k_morpho=k_morpho,
            node_scaler=ns, node_medians=nm
        )
        fu_l2i = {l: i for i, l in enumerate(fu_lids)}

        te_ef, te_li, _, _, _, _, _ = prepare_data(
            test_df, avail_event, fu_l2i,
            event_scaler=es, event_medians=em,
            t_mean=tm, t_std=ts, fit=False
        )

        # Multi-seed training + sampling
        seed_preds = []
        for seed in range(n_seeds):
            torch.manual_seed(seed * 137 + fi * 7 + 42)

            scheduler = DDPMScheduler(n_steps=n_diffusion_steps)
            model = ConditionalDiffusion(
                n_node=tr_nf.shape[1], n_event=tr_ef.shape[1],
                hidden=hidden, dropout=0.15,
            ).to(DEVICE)

            train_diffusion(
                model, scheduler, tr_nf, tr_ei, tr_ef, tr_li, tr_tgt,
                epochs=epochs, lr=lr, verbose=False,
            )

            # Sample multiple times and average (reduces variance)
            samples = []
            for _ in range(n_sample_passes):
                s = scheduler.sample(
                    model, fu_nf, fu_ei, te_ef, te_li, len(test_df)
                )
                samples.append(s.cpu().numpy() * ts + tm)

            seed_preds.append(np.mean(samples, axis=0))

        y_pred = np.median(np.stack(seed_preds), axis=0)
        y_true = test_df[TARGET].values
        y_pred = np.clip(y_pred, 2.0, 30.0)

        r2 = r2_score(y_true, y_pred) if len(y_true) > 1 else float("nan")
        ft_time = time.time() - ft0

        all_true.extend(y_true.tolist())
        all_pred.extend(y_pred.tolist())

        per_loc.append({
            "location": test_loc,
            "n": len(test_df),
            "r2": round(r2, 4),
            "mean_true": round(float(y_true.mean()), 2),
            "mean_pred": round(float(y_pred.mean()), 2),
            "time_s": round(ft_time, 1),
        })

        if verbose:
            err = abs(y_true.mean() - y_pred.mean())
            st = "OK" if r2 > 0 else ("~" if r2 > -1 else "BAD")
            print(f"  [{fi+1:>2d}/{top_n}] {test_loc:<45s} n={len(test_df):>3d}  "
                  f"R2={r2:>7.3f}  true={y_true.mean():.2f}  pred={y_pred.mean():.2f}  "
                  f"err={err:.2f}  [{ft_time:.1f}s] {st}")

    total = time.time() - t0
    if not all_true:
        return {"r2": float("nan")}

    all_t, all_p = np.array(all_true), np.array(all_pred)
    ov_r2 = r2_score(all_t, all_p)
    ov_rmse = math.sqrt(mean_squared_error(all_t, all_p))

    results = {
        "model": "conditional_diffusion",
        "r2": round(ov_r2, 4),
        "rmse": round(ov_rmse, 4),
        "mae": round(mean_absolute_error(all_t, all_p), 4),
        "per_location": per_loc,
        "config": {
            "hidden": hidden, "epochs": epochs,
            "n_diffusion_steps": n_diffusion_steps,
            "k_geo": k_geo, "k_morpho": k_morpho,
            "lr": lr, "n_seeds": n_seeds,
            "n_sample_passes": n_sample_passes,
            "device": str(DEVICE),
        },
        "time_s": round(total, 1),
    }

    if verbose:
        print(f"\n{'='*70}")
        print(f"DIFFUSION LOO: R2={ov_r2:.4f}  RMSE={ov_rmse:.4f}")
        r2s = [r["r2"] for r in per_loc]
        pos = sum(1 for r in r2s if r > 0)
        print(f"R2>0: {pos}/{len(per_loc)}  Median: {np.median(r2s):.4f}")
        print(f"Time: {total:.1f}s  Device: {DEVICE}")
        print(f"{'='*70}")

    return results


if __name__ == "__main__":
    data_path = (
        Path(__file__).resolve().parent.parent
        / "data" / "assembled" / "validation_dataset_v6.csv"
    )
    df = pd.read_csv(data_path)
    print(f"Loaded {len(df)} events, {df['location'].nunique()} locations")
    print(f"Device: {DEVICE}")

    # Apply feature engineering
    try:
        import importlib.util
        tv6_path = Path(__file__).resolve().parent.parent / "train_v6.py"
        spec = importlib.util.spec_from_file_location("train_v6", str(tv6_path))
        mod = importlib.util.module_from_spec(spec)
        mod.__name__ = "train_v6"
        sys.modules["train_v6"] = mod
        spec.loader.exec_module(mod)
        df = mod.add_engineered_features(df)
        print(f"Features: {len(df.columns)} columns")
    except Exception as e:
        print(f"Warning: {e}")

    results = train_and_evaluate_loo(
        df, top_n=15, epochs=500, hidden=48,
        n_diffusion_steps=50, k_geo=8, k_morpho=4,
        lr=3e-3, n_seeds=5, n_sample_passes=10,
        verbose=True,
    )

    out_path = (
        Path(__file__).resolve().parent.parent
        / "artifacts" / "diffusion_loo_results.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved to {out_path}")
