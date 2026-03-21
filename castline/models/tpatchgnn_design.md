# t-PatchGNN for CASTLINE: Design Document

## Background

**Paper**: "Irregular Multivariate Time Series Forecasting: A Transformable Patching Graph Neural Networks Approach"
**Venue**: ICML 2024 (Zhang, Yin, Liu, Zhou, Xiong)
**Repository**: https://github.com/usail-hkust/t-PatchGNN

### Why t-PatchGNN for CASTLINE

CASTLINE's CPUE prediction problem has three properties that make t-PatchGNN a strong candidate:

1. **Irregular sampling**: Tournament events are not evenly spaced. A location may have events on 2021-03-15, 2021-09-02, 2022-06-18. Environmental data (USGS, weather) is also irregularly available across locations.

2. **Multivariate time series**: Each location has ~92 time-varying environmental features (water temp, discharge, gage height, pressure, wind, precipitation, etc.) plus derived features (lag trends, spawn phase, solunar).

3. **Spatial graph structure**: 2001 locations with geographic coordinates. Nearby lakes share weather systems, watershed hydrology, and fish population dynamics. A GNN can propagate information from data-rich locations to sparse ones.

### Current Baseline

- V15 stacked ensemble (CatBoost + XGBoost + LightGBM + Ridge meta-learner)
- Walk-forward R^2 = 0.447 (temporal generalization)
- Seen-location CV R^2 = 0.723
- 6084 event-rows, 406 features, 2001 unique locations

---

## 1. Architecture Design for CASTLINE

### 1.1 Graph Construction

**Nodes**: Each of the 2001 unique locations is a node.

**Edges**: Connect locations that are geographically proximate or hydrologically related.
- **Primary criterion**: Haversine distance < 100 km (tunable)
- **Secondary criterion**: Same HUC-8 watershed (from `lagos_hu8_enc`)
- **Edge weighting**: `w_ij = exp(-d_ij / sigma)` where `d_ij` is haversine distance, `sigma = 50 km`
- **Edge features** (optional, for edge-conditioned GNN):
  - Geographic distance (normalized)
  - Same-watershed binary flag
  - Similar morphometry flag (both reservoirs, or both natural lakes)
  - Latitude difference (proxy for climate similarity)

**Expected graph density**: With 100 km radius, most locations will have 5-30 neighbors. Some isolated locations (e.g., Canadian or western US lakes) may have 0-2 neighbors. We add self-loops for all nodes.

**Adaptive GSL**: Following the paper, we also learn an adaptive adjacency matrix:
- Learnable node embeddings `E1` (N x d), `E2` (d x N)
- `A_adaptive = softmax(ReLU(E1 @ E2))`
- Final adjacency: `A = alpha * A_geo + (1 - alpha) * A_adaptive` (alpha is learnable)

### 1.2 Node Features (Per-Location Time Series)

For each location, we construct a multivariate time series from available data. The key environmental channels are:

| Channel | Source | Typical availability |
|---------|--------|---------------------|
| water_temp_c | USGS gauge | ~40% of locations |
| discharge_cfs | USGS gauge | ~45% of locations |
| gage_height_ft | USGS gauge | ~50% of locations |
| air_temp_c | NASA POWER / OpenMeteo | ~95% of locations |
| pressure_mb | NASA POWER / OpenMeteo | ~95% of locations |
| wind_speed_kph | NASA POWER / OpenMeteo | ~95% of locations |
| precip_mm | NASA POWER / OpenMeteo | ~95% of locations |
| dissolved_oxygen_mgL | USGS gauge | ~15% of locations |

**Static node features** (concatenated to temporal embedding):
- lat, lon (normalized)
- area_acres, max_depth_ft, shore_dev (morphometry)
- reservoir_score, is_lake
- GeoClip embeddings (32-dim PCA)
- Species composition flags (is_largemouth_water, is_smallmouth_water, etc.)
- Creel survey baselines

### 1.3 Temporal Patching Strategy

The paper's key innovation: divide irregular time series into patches with consistent time horizons but variable observation counts.

**For CASTLINE**:
- **Patch time horizon**: 7 days (one week of environmental data per patch)
- **Stride**: 7 days (non-overlapping) or 3 days (overlapping for more context)
- **History window**: 8 patches = 56 days of lookback before each prediction date
- **Within each patch**: Variable number of observations (0 to many USGS readings)
- **Masking**: Binary mask indicates which channels have valid data in each patch

**Handling sparse locations**: For locations with few observations, most patch slots will be empty (masked). The GNN propagates information from nearby data-rich locations, which is exactly the spatial interpolation we need.

### 1.4 Time Encoding

Following the paper:
- **Trend component**: Linear projection of normalized timestamp
- **Periodic component**: `sin(Linear(t))` with `te_dim - 1` dimensions
- Combined: `[trend; sin(W*t)]` gives `te_dim`-dimensional time encoding
- This naturally handles irregular timestamps without discretization

### 1.5 Model Forward Pass (CASTLINE-specific)

```
Input per batch:
  X: (B, M, L_max, N_sub)  -- B=batch, M=patches, L_max=max obs per patch, N_sub=subgraph nodes
  T: (B, M, L_max, N_sub)  -- timestamps for each observation
  Mask: (B, M, L_max, N_sub) -- 1 where data exists, 0 otherwise
  Static: (B, N_sub, D_static) -- static features per node

1. Time Encoding: T -> (B, M, L_max, N_sub, te_dim)
2. TTCN (per-patch temporal conv):
   - Adaptive filter generation from time encoding
   - Masked softmax convolution over observations within each patch
   - Output: (B, N_sub, M, ttcn_dim)
3. Transformer Encoder (intra-series):
   - Processes patch sequence per node
   - Output: (B, N_sub, M, hid_dim)
4. Adaptive Graph Learning:
   - Combine geographic adjacency with learned node relationships
   - Per-patch graph structure (allows time-varying relationships)
5. GCN (inter-series):
   - Chebyshev spectral convolution on the graph
   - Propagates information between nearby locations
   - Output: (B, N_sub, M, hid_dim)
6. Temporal Aggregation:
   - Flatten or Conv1d across patch dimension
   - Concatenate with static features
   - Output: (B, N_sub, final_dim)
7. Prediction Head:
   - MLP: final_dim -> hid_dim -> hid_dim -> 1
   - Output: predicted CPUE (baseline_signal) per location per date
```

### 1.6 Output

- Single scalar per (location, date): predicted `baseline_signal` (CPUE proxy, range 0-1)
- Optional: predict variance for uncertainty quantification

---

## 2. Data Preparation Pipeline

### 2.1 From Tabular to Graph Format

The current V17 dataset is tabular: one row per (event, date) with all features flattened. For t-PatchGNN, we need to restructure:

**Step 1: Extract location registry**
```python
locations = df[['location', 'lat', 'lon']].drop_duplicates()
# Add static features: morphometry, geoclip, species, creel baselines
# Result: DataFrame with 2001 rows, ~50 static feature columns
```

**Step 2: Build adjacency matrix**
```python
from scipy.spatial.distance import cdist
coords = locations[['lat', 'lon']].values
# Haversine distance matrix: (2001, 2001)
dist_matrix = haversine_matrix(coords)
# Threshold + exponential weighting
adj = np.exp(-dist_matrix / 50.0) * (dist_matrix < 100.0)
np.fill_diagonal(adj, 1.0)  # self-loops
```

**Step 3: Construct per-location time series**

For each location, gather all available environmental observations:
- From USGS raw data: `castline/validation/data/raw/usgs_history_full.csv`
- From weather data: `castline/validation/data/raw/weather_history_full.csv`
- From the event rows themselves (which contain snapshot features)

Each location gets a time series: `[(timestamp, channel_id, value), ...]`

**Step 4: Create patches**
```python
for each location:
    for each target_date (prediction date):
        patches = []
        for p in range(n_patches):  # e.g., 8 patches
            t_start = target_date - timedelta(days=(n_patches - p) * patch_days)
            t_end = t_start + timedelta(days=patch_days)
            obs = get_observations(location, t_start, t_end)
            # obs: list of (relative_time, channel_id, value) tuples
            patches.append(obs)
```

**Step 5: Batch construction**

For graph batching with PyG:
- Each prediction event becomes a mini-graph (or we use the full graph with masking)
- Subgraph sampling: for each target location, sample its k-hop neighborhood (k=2)
- This keeps batch sizes manageable and provides local context

### 2.2 Feature Channels

Core time-varying channels (8 primary):
```
0: water_temp_c
1: discharge_cfs
2: gage_height_ft
3: air_temp_c
4: pressure_mb
5: wind_speed_kph
6: precip_mm
7: dissolved_oxygen_mgL
```

All values are z-score normalized per channel (using training set statistics).

### 2.3 Train/Val/Test Split (Walk-Forward)

To match the V15 evaluation protocol:
- **Training**: All events before cutoff date (e.g., 2024-01-01)
- **Validation**: Events from 2024-01-01 to 2024-12-31
- **Test**: Events from 2025-01-01 onward
- Walk-forward: expanding training window, predict next temporal block

For spatial generalization test:
- Hold out 20% of locations entirely (never seen in training)
- Test if GNN can generalize to unseen locations via graph propagation

### 2.4 Practical Data Volume

- 6084 events across 2001 locations
- Median 2 events per location (sparse!)
- 292 locations with >= 5 events
- Environmental time series: potentially daily data for ~40 years (USGS)
- The richness comes from the environmental time series, not event frequency

---

## 3. Training Strategy

### 3.1 Loss Function

**Primary**: MSE on `baseline_signal` (CPUE proxy)

**Considered alternatives**:
- **Huber loss**: More robust to outlier events (tournament blowouts)
- **Quantile loss**: If we want prediction intervals
- **Hurdle loss**: `L = lambda_cls * BCE(y > 0) + lambda_reg * MSE(y | y > 0)` -- useful if many zero catches, but our baseline_signal is continuous [0, 1]

**Recommendation**: Start with MSE, switch to Huber if loss curves show outlier sensitivity.

**Auxiliary losses** (multi-task):
- Environmental reconstruction: predict masked sensor values (self-supervised pretraining)
- This pretraining phase uses all available USGS/weather data, not just tournament dates

### 3.2 Training Phases

**Phase 1: Self-supervised pretraining (optional but recommended)**
- Mask random channels/patches in the environmental time series
- Train model to reconstruct masked values
- This teaches the temporal and spatial patterns without needing CPUE labels
- Can use ALL environmental data (not just tournament dates)

**Phase 2: Supervised fine-tuning**
- Freeze or reduce LR on encoder, train prediction head
- Use tournament CPUE labels
- Walk-forward validation

### 3.3 Hyperparameters (Starting Point)

```yaml
# Patching
patch_size_days: 7
n_patches: 8           # 56-day lookback
max_obs_per_patch: 20  # max observations within a 7-day patch
stride_days: 7

# Architecture
n_channels: 8          # environmental channels
te_dim: 16             # time encoding dimension
ttcn_dim: 32           # temporal conv output dim
hid_dim: 64            # hidden dimension
node_dim: 16           # learnable node embedding dim
n_transformer_layers: 2
n_transformer_heads: 4
n_gnn_layers: 2
gcn_order: 2           # Chebyshev polynomial order
dropout: 0.1

# Static features
static_dim: 50         # lat, lon, morphometry, geoclip, etc.

# Graph
max_neighbors: 30      # max edges per node
distance_threshold_km: 100
distance_sigma_km: 50

# Training
batch_size: 32         # number of target events per batch
learning_rate: 1e-3
weight_decay: 1e-4
epochs: 100
patience: 15           # early stopping
scheduler: cosine_warmup (5 epoch warmup)

# Hardware
gpu: 1x RTX 3060 (Vast.ai)
expected_memory: ~4 GB
```

### 3.4 Hardware Requirements

- **GPU**: RTX 3060 12GB on Vast.ai ($0.06/hr) is sufficient
- **Training time estimate**:
  - 6084 events / 32 batch = 190 steps per epoch
  - ~2-3 seconds per step with subgraph sampling
  - ~100 epochs = ~5-8 hours
  - With pretraining: add 3-5 hours
- **Total cost estimate**: ~$0.50-$1.00

### 3.5 Comparison Plan vs V15 Ensemble

| Metric | V15 Baseline | t-PatchGNN Target | Notes |
|--------|-------------|-------------------|-------|
| Walk-forward R^2 | 0.447 | > 0.50 | Primary metric |
| Seen CV R^2 | 0.723 | > 0.70 | Should be competitive |
| Spatial R^2 (unseen locs) | 0.540 | > 0.60 | GNN advantage expected here |
| MAE | TBD | Lower | Secondary metric |

**Where t-PatchGNN should excel**:
- Unseen locations: GNN can propagate from neighbors (tree models cannot)
- Locations with sparse events but rich environmental data
- Capturing temporal dynamics (cold fronts, warming trends) better than snapshot features

**Where it may struggle**:
- Only 6084 labeled events (small for deep learning)
- 2001 locations with median 2 events each
- Tree ensembles are very strong on tabular data

**Mitigation**: Self-supervised pretraining on environmental data (much more data available).

---

## 4. Implementation Plan

### Step 1: Data Pipeline (1-2 days)
- [ ] Build location registry with static features
- [ ] Construct geographic adjacency matrix
- [ ] Write time series extraction from USGS/weather CSVs
- [ ] Implement patching: irregular observations -> fixed-shape tensors with masks
- [ ] Create PyTorch Dataset class with subgraph sampling
- [ ] Walk-forward train/val/test split

### Step 2: Model Architecture (1-2 days)
- [ ] Time encoding module (trend + periodic)
- [ ] TTCN: adaptive temporal convolution with masking
- [ ] Transformer encoder for intra-patch temporal patterns
- [ ] Adaptive graph structure learning (geographic + learned)
- [ ] Chebyshev GCN for inter-location message passing
- [ ] Static feature fusion
- [ ] Prediction head MLP

### Step 3: Training Loop (1 day)
- [ ] Loss function (MSE + optional Huber)
- [ ] Optimizer + cosine warmup scheduler
- [ ] Early stopping on validation loss
- [ ] Logging (tensorboard/wandb)
- [ ] Checkpoint saving

### Step 4: Self-Supervised Pretraining (1 day, optional)
- [ ] Masked reconstruction objective
- [ ] Use full environmental dataset (not just tournament dates)
- [ ] Pretrain encoder, freeze for fine-tuning

### Step 5: Evaluation (1 day)
- [ ] Walk-forward R^2 computation
- [ ] Seen vs unseen location breakdown
- [ ] Comparison with V15 ensemble
- [ ] Error analysis by location type, season, data availability

### Step 6: Integration (if promising)
- [ ] Export model for inference
- [ ] Integrate as a member in the stacked ensemble (alongside tree models)
- [ ] Or: use as standalone if it outperforms

### Dependencies
- PyTorch >= 2.0
- PyTorch Geometric (torch-geometric) >= 2.4
- NumPy, Pandas, scikit-learn
- SciPy (for distance computations)
- tqdm (progress bars)

---

## 5. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Too few labeled events for deep learning | High | High | Self-supervised pretraining on env data |
| Overfitting on small dataset | High | Medium | Dropout, weight decay, early stopping, subgraph sampling |
| Graph too sparse (isolated nodes) | Medium | Medium | Add watershed-based edges, increase distance threshold |
| Slow convergence | Medium | Low | Warmup scheduler, pretrained encoder |
| Worse than tree ensemble | Medium | Medium | Use as ensemble member rather than replacement |
| Memory issues with full graph | Low | Medium | Subgraph sampling, mini-batch training |

## 6. Key Differences from Original t-PatchGNN

| Aspect | Original Paper | CASTLINE Adaptation |
|--------|---------------|-------------------|
| Graph structure | Fully learned (no prior) | Geographic prior + learned adaptive |
| Node meaning | Variables (e.g., heart rate, BP) | Locations (lakes) |
| Channels per node | 1 (univariate per node) | 8+ (multivariate per location) |
| Task | Multi-step forecasting | Single-step regression (CPUE) |
| Static features | None | Rich morphometry, species, embeddings |
| Scale | 10-50 nodes | 2001 nodes (subgraph sampling needed) |
| Data volume | Thousands of time points | Sparse events, rich env background |
