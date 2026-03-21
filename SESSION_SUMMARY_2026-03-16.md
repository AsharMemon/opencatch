# CASTLINE Session Summary — March 16, 2026

## TL;DR
Massive data collection + SOTA location encoder integration session. GeoCLIP location embeddings show promising LOO improvement (interim 0.623 vs baseline 0.603). FLW scraper fixed and collecting data (340+ rows). Downloaded 3 major USGS datasets. Built fish community spatial features.

---

## What Was Done

### 1. FLW Historical Tournament Scraper — FIXED & RUNNING
**Problem**: Scraper used wrong domain (`flwoutdoors.com` → 0 results).
**Fix**: Changed to `flwfishing.com/tournaments/*` (1,000 tournament URLs found). Added slider/carousel HTML parser since FLW uses `<span class="anglerProfileFinalweight">12 - 2</span>` format, not tables.
- **Status**: 350/1000 tournaments scraped, **340 rows collected** (1997-2004 so far)
- **File**: `castline/validation/data/raw/flw_outcomes.csv`
- **Script**: `scripts/fetch_flw_historical.py`
- **Note**: Weights are TOTAL tournament weights (top-9 finishers), not per-day. Need normalization before integration. Some weights >30 lbs = multi-day totals.
- **⚠️ STILL RUNNING** — will complete ~700-900 rows total. Has checkpoint every 20 events.

### 2. GeoCLIP Location Embeddings — GENERATED & EVALUATED
**What**: Pretrained location encoder (trained on street-level imagery) generates 512-dim dense vectors encoding geographic characteristics of each location.
- **Installed**: `pip install geoclip`
- **Generated**: 512-dim embeddings for all 1,966 unique locations → PCA-reduced to 32d and 64d
- **Files**:
  - `castline/validation/data/raw/location_embeddings_geoclip.csv` (full 512d)
  - `castline/validation/data/raw/location_embeddings_geoclip_pca32.csv`
  - `castline/validation/data/raw/location_embeddings_geoclip_pca64.csv`
- **Script**: `scripts/generate_location_embeddings.py`

**Evaluation Results (PCA-32):**
| Metric | Baseline | GeoCLIP PCA-32 | Delta |
|---|---|---|---|
| Temporal | 0.438 | 0.428 | -0.010 |
| Spatial | 0.483 | 0.474 | -0.009 |
| Spatiotemporal | 0.588 | **0.593** | +0.005 ✅ |
| LOO | 0.603 | **~0.62** (interim at 20/40) | +0.02 ✅ |

- All 32 GeoCLIP PCA dims survived feature importance pruning
- `geoclip_7` had 1.75% importance (top GeoCLIP feature)
- Optimal feature count: 140 (vs 170 without GeoCLIP)
- **⚠️ LOO STILL COMPUTING** — interim R²=0.623 at 20/40 locations. PCA-64 eval not started yet.
- **Script**: `scripts/eval_v15_geoclip.py`

### 3. USGS Fish Occurrence Database — DOWNLOADED
- **35,918 stream reaches**, 419 fish species, lat/lon coordinates, 1990-2019
- Largemouth bass: 9,435 reaches (26.3%), Smallmouth: 5,491 (15.3%), Spotted: 2,266 (6.3%)
- Plus walleye, pike, muskellunge, bluegill, crappie, etc.
- **File**: `castline/validation/data/raw/usgs_fish_occurrence/agap_fish_dataset_v2_0.csv`
- **Source**: USGS ScienceBase

### 4. USGS Fish Community Features — COMPUTED
Built 14 spatial features for all 1,966 tournament locations using BallTree 50km radius search:
- `usgs_largemouth_bass_presence` (mean=0.336, fraction of nearby reaches with LMB)
- `usgs_smallmouth_bass_presence`, `usgs_spotted_bass_presence`
- `usgs_bass_richness` (mean bass species per reach)
- `usgs_predator_richness` (walleye, pike, muskie, sauger, pickerel, bowfin)
- `usgs_predator_competition` (predator/bass ratio)
- `usgs_habitat_richness` (bluegill, sunfish, crappie = productive bass habitat)
- `usgs_forage_index` (bluegill + green sunfish presence)
- `usgs_total_species_richness` (mean=11.7 species per reach)
- `usgs_community_diversity` (Shannon diversity index)
- **95.5% match rate** (1,877/1,966 locations)
- **File**: `castline/validation/data/raw/usgs_fish_community_features.csv`
- **Script**: `scripts/enrich_usgs_fish_features.py`

### 5. CreelCat & FiCli — DOWNLOADED
- **CreelCat**: 249,194 fish records from U.S. creel surveys. **4,206 Largemouth Bass records** with Catch_Per_Hour, Catch_Per_Day. Strong state overlap: MN (920 bass records), MI (872), WI (669), TX (463), FL (273). Survey_Data has Lat, Lon, Waterbody_Name, State, Year.
- **FiCli**: Fish-climate database (2,870 records) with geographic locations.
- **Files**: `castline/validation/data/raw/state_dnr/creelcat/`, `castline/validation/data/raw/state_dnr/ficli/`

### 6. Minnesota DNR Collector — BUILT & TESTED
- Working collector script at `castline/validation/collectors/mn_dnr.py`
- Uses free JSON API at lakefinder.dnr.state.mn.us
- Sample: 14 lakes → 3,681 catch records, 520 bass records, 72 electrofishing CPUE measurements
- API returns species, gear type, CPUE, catch totals, weight. Coverage since 1976.
- **File**: `castline/validation/data/raw/state_dnr/mn_dnr/mn_dnr_fish_surveys.csv`

### 7. State DNR Database Survey — COMPLETED
| State | Data Available | Format | Status |
|---|---|---|---|
| **Minnesota** | Full lake survey data | JSON API | ✅ Collector built, working |
| **Texas TPWD** | ~180 reservoirs electrofishing CPUE | PDF reports only | ⚠️ Needs PDF scraper or data request |
| **Florida FWC** | 50+ waterbodies since 2006 | Not public | ⚠️ Needs formal data request |
| **Wisconsin** | Full data behind FMIS login | Requires WAMS account | ⚠️ Needs account creation |
| **Iowa** | BioNet REST API (30 endpoints) | API (stream-focused) | ⚠️ Lake data in separate portal |

### 6. SatCLIP Research — COMPLETED
- Microsoft research, GitHub-only install (`pip install git+https://github.com/microsoft/satclip.git`)
- Trained on Sentinel-2 satellite imagery (better than GeoCLIP for our use case)
- 256-dim embeddings, (lon, lat) order, float64 required
- Pretrained models on HuggingFace: `microsoft/SatCLIP-ResNet18-L40` recommended
- Not yet installed/tested — GeoCLIP was tried first as faster path

---

## What's Still Running (may be interrupted by WiFi loss)

1. **FLW Scraper** (`scripts/fetch_flw_historical.py`) — PID active, 350/1000 tournaments
   - Has checkpoint every 20 events → data is saved
   - Will resume from checkpoint on next run
2. **GeoCLIP LOO Evaluation** (`scripts/eval_v15_geoclip.py`) — ~25/40 locations done
   - No checkpoint — needs to restart if interrupted
   - But PCA-32 results for T/S/ST are final
3. **State DNR Scout Agent** — may still be searching state websites

---

## What Still Needs To Be Done

### Immediate (next session)
1. **Get final GeoCLIP LOO result** — rerun `scripts/eval_v15_geoclip.py` if interrupted
2. **Run GeoCLIP PCA-64 evaluation** — more dimensions may capture more spatial info
3. **Integrate USGS fish community features into v16 dataset** — 14 new biology-based spatial features
4. **Integrate GeoCLIP embeddings into v16 dataset** — merge PCA-32 or PCA-64
5. **Evaluate combined: GeoCLIP + USGS fish features + v15** — this is the big test
6. **FLW data normalization** — convert total tournament weights to per-day estimates

### Medium-term
7. **Install and test SatCLIP** — may give better embeddings than GeoCLIP (satellite imagery vs street-level)
8. **CreelCat feature engineering** — extract historical bass CPUE per water body as spatial features
9. **Integrate FLW data into training set** — need geocoding (lat/lon for each water body), weather enrichment
10. **FiCli integration** — fish-climate features as spatial context
11. **State DNR survey databases** — Texas PWD, Florida FWC, Minnesota DNR
12. **Build v16 dataset** with all new data sources

### Longer-term
13. **t-PatchGNN** — irregular time series graph model (once we have >15K rows)
14. **SatCLIP + t-PatchGNN hybrid** — the reviewer's recommended SOTA stack
15. **Time-of-day features** — user wants intraday forecasting
16. **Open-Meteo weather** — retry after IP rate limit cools down

---

## Session Part 2: v16 Evaluation + CPUE Model + Production Architecture

### 8. v16 Combined Evaluation — COMPLETED (Vast.ai)
**v16 = v15 + USGS fish community + GeoCLIP PCA-32 + CreelCat spatial features (366 cols)**

| Metric | v15 Baseline | v16 GeoCLIP | v16b SatCLIP | Best |
|---|---|---|---|---|
| Temporal | 0.438 | 0.422 | **0.432** | SatCLIP |
| Spatial | 0.483 | 0.548 | **0.555** | SatCLIP |
| Spatiotemporal | 0.588 | 0.671 | **0.674** | SatCLIP |
| LOO | 0.603 | **0.660** | 0.626 | GeoCLIP |

- **LOO R²=0.660 is our new all-time best** (+9.4% over v15)
- CreelCat CPUE is #1 feature at 18.5% importance
- SatCLIP wins 3/4 metrics, GeoCLIP wins LOO (the most important one)
- USGS fish community features: 0/15 survived pruning in both runs
- Ran on Vast.ai (72 cores, 128GB RAM) — both evals completed in ~15 min

### 9. CreelCat Diagnostic Analysis — COMPLETED
Reviewer-requested diagnostics for CreelCat feature validity:

**Leakage Check:**
- CreelCat CPUE ↔ target: r=0.55 (meaningful but not leaky)
- CreelCat is static spatial feature (within-loc std≈0.01, same across years)
- NOT leakage — acts as location quality prior

**Match Quality:**
- 79.2% of locations have CreelCat match
- 78.5% of matches are <5km (likely same water body)
- Median distance: 0 km (exact match)
- River match rate only 36% (expected — CreelCat is lake-focused)

**Ablation (WITH vs WITHOUT CreelCat on unseen locations):**
- Overall delta: **+0.37 R²** (massive benefit)
- With high analog density (≥17 nearby): **+0.61**
- With low analog density: +0.21
- Without any CreelCat data: -0.03 (neutral)

### 10. CPUE Hurdle Model (Option B) — BUILT & EVALUATED
**Two-part model trained on CreelCat bass surveys (4,097 surveys, 21 states):**

| Component | Metric | v1 (clean) | v2 (LAGOS+proxies) |
|---|---|---|---|
| Catch Probability | AUC | 0.902 | 0.906 |
| Positive CPUE | R² (spatial CV) | 0.379 | 0.402 |
| Positive CPUE | MAE | 0.151 | 0.146 fish/hr |

- GeoCLIP embeddings are 25-35% of CPUE model importance (vs ~5% in weight model)
- Tournament weight ↔ CreelCat CPUE: r=0.79 at location level
- Weather enrichment in progress (6,158 Open-Meteo archive API calls)
- **Scripts**: `scripts/build_cpue_model.py`, `scripts/build_cpue_model_v2.py`

### 11. Production Architecture Plan — DOCUMENTED
**4-Layer Decision System documented in `PRODUCTION_ARCHITECTURE.md`:**
1. Site Prior (CreelCat, LAGOS, USGS fish community) — static quarterly
2. Conditions (USGS real-time, Open-Meteo, solunar) — hourly
3. Catch Rate (hurdle model: P(catch) × E(CPUE)) — daily forecast
4. Confidence (OOD detection, coverage metrics) — per-prediction

**All production data sources are FREE and accessible.**

**User feedback integration plan:** Bayesian update to site prior as user reports accumulate (α-weighted blend of CreelCat prior + user posterior).

### 12. Vast.ai Cloud Compute — OPERATIONAL
- Instance: RTX A4000, 72 cores, 128GB RAM ($0.09/hr)
- Used for: parallel v16/v16b evaluation, CPUE model training, weather fetching
- SSH works from home network (blocked on work/campus network)
- All scripts and data uploaded to `/workspace/castline/`

---

## Key Files Created/Modified This Session

| File | Action | Description |
|---|---|---|
| `scripts/fetch_flw_historical.py` | MODIFIED | Fixed domain, added slider parser |
| `scripts/generate_location_embeddings.py` | CREATED | GeoCLIP/SatCLIP embedding generator |
| `scripts/eval_v15_geoclip.py` | CREATED | Evaluation with location embeddings |
| `scripts/enrich_usgs_fish_features.py` | CREATED | USGS fish community spatial features |
| `castline/validation/data/raw/flw_outcomes.csv` | CREATED | 340+ FLW tournament rows |
| `castline/validation/data/raw/location_embeddings_geoclip*.csv` | CREATED | Embeddings |
| `castline/validation/data/raw/usgs_fish_community_features.csv` | CREATED | Fish features |
| `castline/validation/data/raw/usgs_fish_occurrence/` | CREATED | USGS raw data |
| `castline/validation/data/raw/state_dnr/` | CREATED | CreelCat + FiCli |

---

## Reviewer Insights Applied
1. **SatCLIP/GeoCLIP location encoders** → GeoCLIP tested, showing LOO improvement
2. **LightGBM/CatBoost still best for fisheries** → confirmed, keeping ensemble approach
3. **t-PatchGNN for irregular time series** → deferred until more data
4. **GraphCast/Aurora** → not directly applicable (weather forecasting, not fish)

---

## Strategic Conclusions from Session Discussions

### Product Direction
- **Primary feature**: "Will I catch a fish at this lake on this day at this time?" (forecast + ranking)
- **Ranking (spatial)** = the easier win. 76.4% of target variance is between-location. Current Spatial R²=0.483 already discriminates lakes. **V1 could ship ranking immediately.**
- **Forecast (temporal)** = harder. Only 23.6% of variance is within-location temporal signal. Requires weather/conditions to improve. Current Temporal R²=0.438.
- **Time-of-day** = user's desired intraday feature. Not yet modeled — needs sub-daily data (solunar tables, hourly weather, light levels).

### The Core Bottleneck: Unseen Locations
- **62% of temporal test rows** come from locations never seen in training → model falls back to population mean for those.
- **Seen-location R²=0.57, Unseen-location R²=0.26** — massive gap.
- This is why LOO (leave-one-location-out) is our most important metric: it directly measures unseen-location performance.
- **Location encoders (GeoCLIP/SatCLIP)** are the highest-impact SOTA direction for this problem — they give the model a dense representation of what a location "looks like" even if it's never been seen in training.
- GeoCLIP PCA-32 already improved LOO from 0.603 → ~0.62 (interim). SatCLIP (satellite imagery) may do even better since it captures water body shape, land cover, vegetation — more relevant than street-level photos.

### SOTA Model Architecture Strategy
- **Reviewer's recommended stack**: Location encoder + irregular-time graph model (t-PatchGNN) + boosted-tree baseline.
- **Our decision**: Adopt incrementally, not all at once.
  1. **Now**: GeoCLIP/SatCLIP location encoders as features into existing CatBoost/XGB/LGBM ensemble. Low risk, immediate LOO improvement.
  2. **At 15K+ rows**: Try t-PatchGNN for temporal modeling. Neural nets failed at 6K rows (R²=-0.63), need more data.
  3. **At 50K+ rows**: Full graph-based architecture with location priors baked in.
- **GraphCast/Aurora**: Not applicable — they're weather foundation models, not fish models. Useful only if we wanted to build our own weather forecasts (we use NASA POWER and Open-Meteo instead).
- **LightGBM/CatBoost confirmed as SOTA for fisheries CPUE** by the literature (Tanaka 2021, multiple fish ecology papers). No need to switch to neural nets until data scale justifies it.

### Fishery-Independent Data Strategy
Key insight: external fisheries data can serve as **FEATURES** (spatial context about a location), not just training targets.

| Data Source | Use As | Value | Feasibility |
|---|---|---|---|
| **USGS fish occurrence** | Spatial features (bass presence, community diversity) | ⭐⭐⭐ High — 14 biology-based features computed | ✅ Done |
| **CreelCat creel surveys** | Spatial features (historical CPUE per water body) + potential training data | ⭐⭐⭐⭐ Very high — actual angler catch rates | ✅ Downloaded, needs feature engineering |
| **GeoCLIP embeddings** | Spatial features (512d location representation) | ⭐⭐⭐ High — encodes geography/land cover | ✅ Done |
| **FLW tournaments** | Training data (tournament outcomes) | ⭐⭐ Medium — needs geocoding + normalization | 🔄 Scraping in progress |
| **MN DNR surveys** | Spatial features (electrofishing CPUE) + potential training data | ⭐⭐⭐ High — direct fish abundance | ✅ Collector built |
| **TX TPWD** | Spatial features (reservoir-level bass CPUE) | ⭐⭐⭐⭐ Very high — richest US bass database | ⚠️ PDF-only, needs data request |
| **FL FWC** | Spatial features | ⭐⭐⭐ High — trophy bass lakes | ⚠️ Formal request required |
| **SatCLIP** | Spatial features (satellite-derived location encoding) | ⭐⭐⭐⭐ Very high — better than GeoCLIP for water bodies | 🔜 Next to try |
| **FiCli** | Spatial features (fish-climate relationships) | ⭐⭐ Medium | ✅ Downloaded |

### Feature Strategy: What Matters Most
1. **Spatial features dominate** — 76.4% of variance. Every new spatial feature that helps discriminate locations is high-value.
2. **Biology-based spatial features** (USGS fish community, CreelCat CPUE) encode "is this a good bass lake?" directly. More interpretable and likely more predictive than pure geography.
3. **Location encoders** (GeoCLIP/SatCLIP) capture what geography/satellite can't name explicitly — water body shape, surrounding vegetation density, urbanization, terrain.
4. **Weather lag features** (NASA POWER 7-day) capture temporal signal but have diminishing returns — most temporal variance is noise (angler skill, tackle, etc.).
5. **Feature pruning is essential** — going from 283 → 170 features improved all metrics. Curse of dimensionality is real at 6K rows.
6. **Combined test is critical** — USGS fish features + GeoCLIP + v15 together may show synergy (biology captures "what fish live here", GeoCLIP captures "what does this place look like").

### Target: Path from R²≈0.6 → R²=0.9
| R² Range | Primary Lever | Estimated Gain |
|---|---|---|
| 0.60 → 0.70 | More + better spatial features (USGS, CreelCat, SatCLIP, combined) | +0.05–0.10 |
| 0.65 → 0.75 | 3–5× more training data (FLW, state DNR, expanded BASS) | +0.05–0.10 |
| 0.75 → 0.85 | Deep learning on large dataset (t-PatchGNN, graph models) | +0.05–0.10 |
| 0.85 → 0.90 | Time-of-day features, fine-grained weather, solunar | +0.03–0.05 |

**Honest assessment**: R²=0.9 across all four metrics is extremely ambitious for ecological prediction. Published fisheries models rarely exceed R²=0.7 on out-of-sample data. But with enough data (50K+ rows), SOTA location encoders, and proper temporal modeling, R²=0.8+ on LOO/Spatial and R²=0.7+ on Temporal may be achievable. The ranking product can ship well before that threshold.
