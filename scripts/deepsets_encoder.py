"""DeepSets location encoder for LOO improvement.

Treats each location as a SET of events (not a sequence).
Learns a permutation-invariant location embedding from:
  - Event features (weather, temporal, etc.)
  - Missingness masks
  - Time delta features

The embedding is then used as a feature in CatBoost for LOO prediction.
"""
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.metrics import r2_score
from sklearn.preprocessing import StandardScaler
from sklearn.neighbors import NearestNeighbors
from catboost import CatBoostRegressor
import warnings
warnings.filterwarnings("ignore")

TARGET = "median_weight_lb"
ALWAYS_EXCLUDE = {"target_success_score", "tms_id", "event_id"}
LOCATION_IDENTITY = {"loc_enc", "trail_mean_weight", "location_mean_weight",
                     "loc_rolling_3", "baseline_signal"}
META_COLS = {"date", "location", "region", "block", "sat_source",
             "usgs_site_id", "event_name", "tournament_slug",
             "trail", "results_source", "source", "species", "spawn_phase"}

DEVICE = torch.device("cpu")  # Small enough for CPU


class DeepSetEncoder(nn.Module):
    """Permutation-invariant set encoder.

    phi: element-wise transformation
    rho: aggregation → embedding
    """

    def __init__(self, input_dim, hidden_dim=64, embed_dim=16, dropout=0.2):
        super().__init__()
        # phi: per-element encoder
        self.phi = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
        )
        # rho: aggregated → embedding
        # Input is 3*hidden_dim because we concat mean, max, attention-weighted
        self.attention = nn.Linear(hidden_dim, 1)
        self.rho = nn.Sequential(
            nn.Linear(hidden_dim * 3, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, embed_dim),
        )

    def forward(self, x, mask=None):
        """
        x: (batch, max_set_size, input_dim) - padded event features
        mask: (batch, max_set_size) - 1 for real events, 0 for padding
        Returns: (batch, embed_dim)
        """
        # Per-element encoding
        h = self.phi(x)  # (batch, max_set_size, hidden_dim)

        if mask is not None:
            mask_expanded = mask.unsqueeze(-1)  # (batch, max_set_size, 1)
            h = h * mask_expanded  # Zero out padding

            # Attention weights
            attn_logits = self.attention(h).squeeze(-1)  # (batch, max_set_size)
            attn_logits = attn_logits.masked_fill(mask == 0, -1e9)
            attn_weights = torch.softmax(attn_logits, dim=1).unsqueeze(-1)

            # Aggregation: mean, max, attention-weighted
            count = mask_expanded.sum(dim=1, keepdim=True).clamp(min=1)
            mean_pool = (h * mask_expanded).sum(dim=1) / count.squeeze(1)

            h_masked = h.clone()
            h_masked[mask == 0] = -1e9
            max_pool = h_masked.max(dim=1).values

            attn_pool = (h * attn_weights).sum(dim=1)
        else:
            mean_pool = h.mean(dim=1)
            max_pool = h.max(dim=1).values
            attn_pool = h.mean(dim=1)

        # Concat all pooling methods
        pooled = torch.cat([mean_pool, max_pool, attn_pool], dim=-1)
        return self.rho(pooled)


class DeepSetPredictor(nn.Module):
    """Full model: DeepSet encoder + prediction head."""

    def __init__(self, input_dim, hidden_dim=64, embed_dim=16, dropout=0.2):
        super().__init__()
        self.encoder = DeepSetEncoder(input_dim, hidden_dim, embed_dim, dropout)
        self.head = nn.Sequential(
            nn.Linear(embed_dim, 32),
            nn.ReLU(),
            nn.Linear(32, 1),
        )

    def forward(self, x, mask=None):
        emb = self.encoder(x, mask)
        return self.head(emb).squeeze(-1), emb


def prepare_location_sets(df, features, max_events=50):
    """Prepare set-format data: each location → padded set of events."""
    locations = df.location.unique()
    n_feats = len(features)

    # Include missingness mask and time features
    # Input per event: [features, missingness_mask, time_deltas]
    input_dim = n_feats * 2 + 3  # features + mask + (year, season, time_since_first)

    X_sets = []
    masks = []
    targets = []
    loc_names = []

    scaler = StandardScaler()
    all_feats = df[features].values
    scaler.fit(all_feats[~np.isnan(all_feats).all(axis=1)])

    for loc in locations:
        loc_df = df[df.location == loc].sort_values("date")
        n = min(len(loc_df), max_events)
        loc_df = loc_df.tail(n)  # Use most recent events

        # Feature values
        feat_vals = loc_df[features].values
        # Missingness mask
        miss_mask = (~np.isnan(feat_vals)).astype(np.float32)
        # Fill NaN with 0 for the model
        feat_vals = np.nan_to_num(feat_vals, nan=0.0)

        # Scale features
        feat_scaled = scaler.transform(feat_vals)

        # Time features
        years = loc_df.year.fillna(2000).values
        seasons = loc_df.season_sin.fillna(0).values if "season_sin" in loc_df.columns else np.zeros(n)
        first_year = years.min()
        time_since = (years - first_year) / max(years.max() - first_year, 1)

        time_feats = np.column_stack([
            (years - 2000) / 25,  # Normalized year
            seasons,
            time_since,
        ])

        # Combine: [scaled_features, miss_mask, time_feats]
        event_data = np.hstack([feat_scaled, miss_mask, time_feats])

        # Pad to max_events
        padded = np.zeros((max_events, input_dim))
        padded[:n] = event_data
        event_mask = np.zeros(max_events)
        event_mask[:n] = 1

        X_sets.append(padded)
        masks.append(event_mask)
        targets.append(loc_df[TARGET].mean())
        loc_names.append(loc)

    return (
        torch.FloatTensor(np.array(X_sets)),
        torch.FloatTensor(np.array(masks)),
        torch.FloatTensor(np.array(targets)),
        loc_names,
        input_dim,
        scaler,
    )


def train_deepset(X_sets, masks, targets, loc_names, input_dim,
                  epochs=200, lr=0.001, hold_out_loc=None):
    """Train DeepSet model, optionally holding out one location."""
    if hold_out_loc:
        hold_idx = loc_names.index(hold_out_loc)
        train_mask = torch.ones(len(loc_names), dtype=torch.bool)
        train_mask[hold_idx] = False

        X_train = X_sets[train_mask]
        m_train = masks[train_mask]
        y_train = targets[train_mask]
    else:
        X_train = X_sets
        m_train = masks
        y_train = targets

    model = DeepSetPredictor(input_dim, hidden_dim=64, embed_dim=16, dropout=0.2)
    model.to(DEVICE)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)

    model.train()
    for epoch in range(epochs):
        optimizer.zero_grad()
        pred, _ = model(X_train.to(DEVICE), m_train.to(DEVICE))
        loss = nn.MSELoss()(pred, y_train.to(DEVICE))
        loss.backward()
        optimizer.step()
        scheduler.step()

    model.eval()
    with torch.no_grad():
        _, all_emb = model(X_sets.to(DEVICE), masks.to(DEVICE))

    return model, all_emb.cpu().numpy()


def main():
    print("=" * 60)
    print("DeepSets Location Encoder + CatBoost LOO Evaluation")
    print("=" * 60)

    df = pd.read_csv("castline/validation/data/assembled/validation_dataset_v8.csv",
                     low_memory=False)
    df = df[df[TARGET].notna()].copy()
    print(f"Dataset: {len(df)} rows, {df.location.nunique()} locations")

    # Features for DeepSet encoding
    all_cols = set(df.columns)
    exclude = ALWAYS_EXCLUDE | LOCATION_IDENTITY | META_COLS | {TARGET}
    features = sorted(all_cols - exclude)
    features = [f for f in features if df[f].dtype in ["float64", "int64", "float32", "int32"]]
    loo_features = [f for f in features if f not in LOCATION_IDENTITY]

    print(f"Features: {len(features)}")

    # Prepare set data
    print("\nPreparing location sets...")
    X_sets, masks, targets, loc_names, input_dim, scaler = prepare_location_sets(
        df, features, max_events=30
    )
    print(f"Location sets: {len(loc_names)}, input_dim: {input_dim}")

    # Train global DeepSet model (for non-LOO metrics)
    print("\nTraining global DeepSet encoder...")
    model, global_embeddings = train_deepset(
        X_sets, masks, targets, loc_names, input_dim, epochs=300
    )

    # Create embedding lookup
    emb_dict = {loc: global_embeddings[i] for i, loc in enumerate(loc_names)}
    embed_dim = global_embeddings.shape[1]

    # Add embeddings to dataframe
    for d in range(embed_dim):
        df[f"ds_emb_{d}"] = df.location.map(
            {loc: emb[d] for loc, emb in emb_dict.items()}
        )

    emb_cols = [f"ds_emb_{d}" for d in range(embed_dim)]
    full_with_emb = loo_features + emb_cols

    # ═════════════════════════════════════════
    # LOO Evaluation with DeepSet embeddings
    # ═════════════════════════════════════════
    print("\n" + "=" * 50)
    print("LOO with DeepSet Location Embeddings")
    print("=" * 50)

    loc_counts = df.location.value_counts()
    big_locs = loc_counts[loc_counts >= 15].index.tolist()
    # Filter to locations with real variance
    loc_var = df.groupby("location")[TARGET].std()
    big_locs = [l for l in big_locs if loc_var.get(l, 0) > 0.1]
    print(f"Testing {len(big_locs)} locations with n>=15 and variance")

    all_true, all_pred = [], []
    per_loc_r2 = {}

    for i, loc in enumerate(big_locs):
        mask = df.location == loc
        train = df[~mask].copy()
        test = df[mask].copy()

        # Re-train DeepSet holding out this location
        _, loo_embeddings = train_deepset(
            X_sets, masks, targets, loc_names, input_dim,
            epochs=200, hold_out_loc=loc,
        )

        # Update embeddings for this LOO
        loo_emb_dict = {l: loo_embeddings[j] for j, l in enumerate(loc_names)}
        for d in range(embed_dim):
            train[f"ds_emb_{d}"] = train.location.map(
                {l: e[d] for l, e in loo_emb_dict.items()}
            )
            test[f"ds_emb_{d}"] = loo_emb_dict.get(loc, np.zeros(embed_dim))[d]

        # Also add KNN cluster encoding
        loc_agg = train.groupby("location").agg(
            mean_target=(TARGET, "mean"),
            lat=("lat", "first"),
            lon=("lon", "first"),
            area_acres=("area_acres", "first"),
        ).reset_index()

        morph_cols = ["lat", "lon", "area_acres"]
        X_morph = loc_agg[morph_cols].fillna(loc_agg[morph_cols].median()).values
        sc = StandardScaler().fit(X_morph)
        nn = NearestNeighbors(n_neighbors=min(9, len(loc_agg)))
        nn.fit(sc.transform(X_morph))

        # Cluster encoding for training locations
        cluster_map = {}
        for idx, row in loc_agg.iterrows():
            q = sc.transform(loc_agg.iloc[idx:idx+1][morph_cols].fillna(loc_agg[morph_cols].median()).values)
            dists, idxs = nn.kneighbors(q)
            nbrs = [(d, j) for d, j in zip(dists[0], idxs[0])
                    if loc_agg.iloc[j]["location"] != row["location"]][:8]
            if nbrs:
                w = [1/(d+0.01) for d,_ in nbrs]
                t = sum(w)
                cluster_map[row["location"]] = sum(wi*loc_agg.iloc[j]["mean_target"] for wi,(_,j) in zip(w,nbrs))/t
            else:
                cluster_map[row["location"]] = loc_agg["mean_target"].mean()

        # Cluster for test location
        test_morph = test[morph_cols].fillna(loc_agg[morph_cols].median().to_dict()).iloc[0:1].values
        test_q = sc.transform(test_morph)
        dists, idxs = nn.kneighbors(test_q)
        nbrs = [(d,int(j)) for d,j in zip(dists[0], idxs[0])][:8]
        w = [1/(d+0.01) for d,_ in nbrs]
        t = sum(w)
        test_cluster = sum(wi*loc_agg.iloc[j]["mean_target"] for wi,(_,j) in zip(w,nbrs))/t

        train["cluster_enc"] = train.location.map(cluster_map).fillna(train[TARGET].mean())
        test["cluster_enc"] = test_cluster

        use_feats = full_with_emb + ["cluster_enc"]

        model_cb = CatBoostRegressor(
            iterations=500, depth=6, learning_rate=0.05,
            l2_leaf_reg=3, verbose=0, random_seed=42,
        )
        model_cb.fit(train[use_feats], train[TARGET])
        pred = np.clip(model_cb.predict(test[use_feats]), 0, 30)

        r2 = r2_score(test[TARGET], pred) if len(test) > 1 and np.std(test[TARGET]) > 0.01 else 0.0
        per_loc_r2[loc] = r2

        if (i + 1) % 10 == 0:
            interim = r2_score(all_true + list(test[TARGET]), all_pred + list(pred))
            print(f"  {i+1}/{len(big_locs)} interim R²={interim:.4f}")

        all_true.extend(test[TARGET].tolist())
        all_pred.extend(pred.tolist())

    overall_r2 = r2_score(all_true, all_pred)
    positive = sum(1 for r in per_loc_r2.values() if r > 0)
    print(f"\nOverall LOO R²={overall_r2:.4f}")
    print(f"Positive R²: {positive}/{len(big_locs)}")
    print(f"Median: {np.median(list(per_loc_r2.values())):.4f}")

    # Top/bottom locations
    sorted_locs = sorted(per_loc_r2.items(), key=lambda x: x[1])
    print("\nWorst 5:")
    for loc, r2 in sorted_locs[:5]:
        print(f"  {loc:50s} R²={r2:.3f}")
    print("Best 5:")
    for loc, r2 in sorted_locs[-5:]:
        print(f"  {loc:50s} R²={r2:.3f}")


if __name__ == "__main__":
    main()
