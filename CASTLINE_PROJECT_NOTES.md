# CASTLINE Project Notes — Phase 1 Production

**Last updated:** 2026-03-16
**Goal:** R²=0.9 on all four metrics (Temporal, Spatial, Spatiotemporal, LOO)

---

## Current Honest Metrics (as-of safe, no leakage)

| Metric | v12 | v13 (best) | Target | Gap |
|---|---|---|---|---|
| Temporal | 0.416 | **0.425** | 0.9 | 0.475 |
| Spatial | 0.476 | **0.486** | 0.9 | 0.414 |
| Spatiotemporal | 0.586 | **0.594** | 0.9 | 0.306 |
| LOO | 0.579 | **0.606** | 0.9 | 0.294 |
| LOO NDCG@5 | 0.983 | 0.979 | — | — |
| LOO Pairwise | 0.860 | 0.859 | — | — |
| LOO Spearman | 0.882 | 0.880 | — | — |

### v13 Key Additions
- **GLOBathy**: max_depth_ft 12.5% → 87% (1,686 locations filled with estimated depths)
- **LAGOS-US**: shore_dev 12.5% → 92% (479K lake SDI database), lagos_sdi as model feature
- **Derived depth features**: log_depth, depth_x_lat, littoral_ratio, stratification_potential, thermal_refuge_score

---

## Three Recommendations (all implemented)

### 1. Data Freshness/Staleness Features ✅
Added in v10:
- `is_bassmaster`, `is_creel`, `is_tourneyx` — source type flags
- `has_usgs_water`, `usgs_feature_count`, `has_usgs_iv` — data availability indicators
- `has_real_weather`, `has_creel_data` — completeness flags
- `data_quality_score`, `morph_completeness` — quality composites

### 2. Missingness Indicators ✅
Added in v10:
- Each USGS/weather feature absence is encoded as a separate flag
- `morph_completeness` tracks fraction of morphometric fields available
- CatBoost natively handles NaN but indicators help for explicit missingness patterns

### 3. As-Of Safe Location Encoding ✅
Added in v10:
- `loc_rolling_mean` — expanding window mean of prior events at location (67% coverage)
- `loc_rolling_std` — expanding window std of prior events
- `loc_n_prior` — count of prior events at location
- `loc_encoding_confidence` — confidence based on n_prior
- `source_rolling_mean` — per-source rolling mean
- KNN-based gap-filling for first-time locations (v12+)

---

## Reviewer Feedback

### Reviewer 1: Architecture & Pipeline
1. ✅ **Boosted trees baseline** — CatBoost implemented as primary model
2. ✅ **Point-in-time pipeline** — as-of safe, no temporal leakage
3. ✅ **Data freshness features** — source type, staleness, availability flags
4. ✅ **Narrow target definition** — median_weight_lb (regression, not classification)
5. ✅ **Source audit** — bassmaster vs creel vs tourneyx separated
6. 🔄 **Two-stage pipeline** — designed but not yet implemented
   - Stage 1: Environmental state estimation (fill missing data)
   - Stage 2: Fishing opportunity scoring

### Reviewer 2: Modeling Approach
1. 🔄 **Two-stage pipeline** — Stage 1 env state + Stage 2 scoring
2. ⬜ **Deep Sets for Stage 2** — evidence fusion from multiple data sources
3. ✅ **Data freshness as first-class feature** — implemented
4. ✅ **LightGBM/CatBoost on honest features** — CatBoost ensemble (3-4 configs)
5. ⬜ **Two-part model** — P(catch>0) × E(CPUE|catch>0) decomposition
6. ✅ **Ranking evaluation** — NDCG@k, pairwise accuracy, Spearman ρ, Kendall τ, top-k hit rate

---

## Research Papers & Insights

### Paper 1: Tanaka et al. — "Fish Catch Prediction by Combining Fishing, Weather and Tidal Data"
- **Method:** XGBoost with species-specific models
- **Result:** R² improved from -0.27 to 0.20 with lag features
- **Key Features:** Air temp (avg/max/min), wind speed, humidity, tidal data, water temp
- **Key Insight:** **Lag features (1-7 day) and 3-day moving averages** dramatically improve temporal predictions
- **Applied:** Weather anomaly features (marginal gain), NASA POWER 7-day fetch started
- **TODO:** Re-fetch 7-day weather with lower concurrency (only got 6.7% coverage due to rate limits)

### Paper 2: Agmata & Guðmundsson — "CATCH: ConvLSTM for Spatiotemporal Fisheries" (2024)
- **Method:** Convolutional LSTM predicting CPUE probability densities on spatial grids
- **Architecture:** 5D tensor (sample × time-lag × lat × lon × feature), ConvLSTM layers
- **Input Features:** CPUE probability density + bottom temperature + depth
- **Result:** SSI ~0.95+, very low RMSE/MAE/WD, generalizes across species
- **Key Insight:** Spatial grid + probability distribution framing captures spatiotemporal patterns
- **TODO:** Consider ConvLSTM for our spatial/ST metrics (needs GPU)

### Paper 3: Frontiers — "ANN for CPUE Prediction with CALIPSO Satellite Data"
- **Method:** ANN vs SVM vs GLM for CPUE prediction
- **Result:** ANN R²=0.34 for bigeye tuna, R²=0.92 for Antarctic krill
- **Key Insight:** **Satellite-derived chlorophyll-a** as lake productivity indicator
- **Applied:** Fetching WQP chlorophyll-a and Secchi depth data (224 locations, 9.3M measurements)
- **TODO:** Integrate chlorophyll-a as location-level feature; also explore MODIS/Aqua data

---

## Data Expansion Pipeline

### Completed ✅
1. **NASA POWER weather** — 90.3% event coverage, 15 daily weather parameters
2. **Fish biology features** — Recomputed from NASA POWER water temp estimate (7% → 91% coverage)
3. **Freshness/staleness features** — Source type, data quality, missingness indicators
4. **As-of safe location encoding** — loc_rolling_mean, KNN gap-filling

### In Progress 🔄
5. **USGS daily water data** — 33/2001 locations have nearby USGS sites (fetch stopped at 450/2001)
6. **WQP chlorophyll-a & Secchi** — 224 locations with chlorophyll, Secchi, phosphorus data (9.3M rows)
7. **NASA POWER 7-day windows** — Only 407/6084 events got data; need lower concurrency re-fetch

### Planned ⬜
8. **MODIS/Aqua satellite chlorophyll** — For inland lakes without WQP data
9. **FLW/MLF tournament data** — Expand tournament coverage beyond Bassmaster
10. **Fish stocking records** — State DNR data for location-level productivity
11. **USACE reservoir levels** — Lake levels, releases, inflows
12. **Tidal data** — Per Tanaka, relevant for coastal/tidal fishing locations

---

## Key Bottleneck Analysis

### Variance Decomposition
- **76.4%** of target variance is **between-location** (which lake)
- **23.6%** is **within-location** (weather, conditions, timing)
- Median events per location: 2 (972 locations have only 1 event)

### Temporal Holdout Gap
- 62% of test rows are from **new locations** (never seen in training)
- For these: loc_rolling_mean = NaN, model relies on morphometry + weather only
- area_acres (97%), creel_cpue_mean (70%) are only strong location features
- max_depth_ft (12.5%), shore_dev (12.5%) have terrible coverage

### Catastrophic LOO Locations
- Sabine River, TX (R²=-26) — unique tidal river fishery
- Lake St. Clair, MI (R²=-9) — shallow Great Lakes connected water
- St. Johns River, FL (R²=-5) — unique Florida river system
- Need specialized features or data for these unique waterbodies

### Realistic Ceilings (with current data)
- Temporal: ~0.50-0.55 (limited by 62% new locations)
- Spatial: ~0.55-0.60 (limited by cross-region transfer)
- Spatiotemporal: ~0.65-0.70 (most constrained)
- LOO: ~0.60-0.65 (limited by catastrophic locations)

### Path to R²=0.9
1. **More events per location** — reduce new-location fraction in temporal holdout
2. **Better location features** — chlorophyll-a, Secchi depth, fish stocking, lake productivity
3. **Weather lag features** — 3-7 day temperature trends (per Tanaka)
4. **Expand morphometry coverage** — max_depth_ft at 12.5% needs 80%+
5. **Two-stage pipeline** — separate env state estimation from fishing scoring
6. **Deep learning** — ConvLSTM or transformer for spatiotemporal patterns
7. **GPU training** — for deep learning models (Vast.ai available)

---

## Key Scripts
| Script | Purpose |
|---|---|
| `scripts/build_v11.py` | Build v11 dataset (biology from NASA POWER) |
| `scripts/eval_v12_honest.py` | Best evaluation framework (gap-filling, ensemble) |
| `scripts/eval_v13_stratified.py` | With weather anomaly features |
| `scripts/fetch_nasa_power.py` | NASA POWER daily weather fetch |
| `scripts/fetch_nasa_power_lags.py` | NASA POWER 7-day window fetch |
| `scripts/fetch_usgs_daily.py` | USGS Water Services API fetch |
| `scripts/fetch_wqp_chlorophyll.py` | WQP chlorophyll/Secchi/phosphorus fetch |
| `scripts/expand_morphometry.py` | Expand lake morphometry coverage |
