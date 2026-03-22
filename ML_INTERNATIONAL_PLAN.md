# OpenCatch ML International Expansion Plan

**Date:** 2026-03-21
**Status:** Planning
**Current Models:** CPUE Fishing Predictions (XGBoost/Stacked Ensemble, V15) | Satellite Bathymetry (Depth Anything V2 + Spectral KAN)

---

## 1. Executive Summary

OpenCatch's two ML pipelines — CPUE-based fishing predictions and satellite-derived bathymetry — have different international portability profiles. The bathymetry pipeline is substantially more transferable because its core inputs (ICESat-2, Sentinel-2) are global sensors with physics-based relationships. The CPUE prediction model is more region-locked because it depends on species-specific catch data, local water body characteristics, and angler behavior patterns that vary by country.

**Bottom line:** Bathymetry can expand to most regions with minimal retraining (ICESat-2 + Sentinel-2 are global). CPUE predictions require per-region fine-tuning with local catch data, but universal weather/solunar features provide a transferable foundation. Target 6-12 months for first international market (Australia or Europe), 18-24 months for full multi-region coverage.

---

## 2. Model-by-Model Transfer Analysis

### 2.1 CPUE Fishing Prediction Model

**Current architecture:** Two-model stacked ensemble (seen lakes / unseen lakes), XGBoost base learners, 130 pruned features, V15 best metrics: CV R²=0.723, Walk-forward R²=0.447.

#### What Transfers (Universal Features)

| Feature Category | Transferability | Notes |
|---|---|---|
| Barometric pressure trends | HIGH | Fish physiology is universal — lateral line pressure sensitivity applies to all species |
| Temperature (air + water) | HIGH | Thermal metabolism is species-dependent but the relationship direction is universal |
| Moon phase / solunar periods | MEDIUM | Effects documented globally but magnitude varies; stronger in saltwater/tidal environments |
| Wind speed / direction | HIGH | Surface disturbance affects feeding behavior universally |
| Precipitation | HIGH | Runoff, turbidity changes affect fish universally |
| Day length / photoperiod | HIGH | Circadian feeding patterns are biologically universal |
| Seasonal lag features | HIGH | Tanaka-style lag features (7/14/30 day) transfer — the concept of recent-trend-matters is universal |

#### What Does NOT Transfer

| Feature Category | Why It Fails | Mitigation |
|---|---|---|
| Species composition | Different species per region | Build species-group embeddings (warm-water predators, cold-water salmonids, etc.) |
| Water body type encoding | US-specific lake/river/reservoir categories | Remap to universal morphometry features (surface area, max depth, elevation) |
| Location-based features | US lat/lon, state encodings | Replace with climate zone + ecoregion features |
| Stocking schedules | US state agency specific | Source local stocking data or drop feature |
| Angler pressure / num_anglers | US creel survey specific | Need local recreational survey data |
| Regulatory seasons | US state game laws | Source local open/closed seasons per species |

#### Retraining Verdict: YES, per-region fine-tuning required

The weather/solunar/temporal feature backbone (~50% of model signal) transfers. Species-specific, location-specific, and regulatory features (~50%) must be retrained. Estimated minimum viable dataset: **500-1,000 catch records per region** spanning at least 12 months (to capture seasonality), with 50+ unique water bodies.

#### Recommended Transfer Learning Strategy

1. **Phase 1 — Foundation Model:** Retrain on combined US + international data using only universal features (weather, solunar, morphometry, temporal lags). This becomes the "global backbone."
2. **Phase 2 — Regional Heads:** Add region-specific feature layers (species, local water body types, regulations) as XGBoost sub-models that stack on top of the global backbone predictions.
3. **Phase 3 — Continuous Learning:** As local data accumulates, progressively weight the regional head higher vs. the global backbone.

For XGBoost specifically, transfer learning means:
- Train a global model on universal features
- For each region, train a secondary model on residuals (global_prediction - actual) using local features
- Stack: final_prediction = global_prediction + regional_correction

Minimum samples for fine-tuning: **200-500 records** can produce a usable regional correction model if the global backbone is strong. Below 200 records, rely on the global model alone.

---

### 2.2 Satellite Bathymetry Model

**Current architecture:** Two-stage pipeline:
- Stage 1: XGBoost max-depth predictor (lake morphometry features)
- Stage 2: Spectral KAN (Sentinel-2 bands to depth) + Depth Anything V2 fine-tuned on MN DNR sonar DEMs
- Training data: MN DNR 4,500 lake sonar DEMs + ICESat-2 photon depths

#### What Transfers

| Component | Transferability | Notes |
|---|---|---|
| ICESat-2 photon bathymetry | GLOBAL | ATL24 dataset covers 88°N to 88°S; SlideRule API works for any coordinate on Earth |
| Sentinel-2 spectral bands | GLOBAL | Same sensor everywhere; band ratios (B2/B3, B1/B3) have physics-based depth relationship |
| 3D-LAKES dataset | GLOBAL | 510,530 lakes/reservoirs worldwide with ICESat-2-derived bathymetry already computed |
| Depth Anything V2 backbone | HIGH | Foundation model trained on diverse scenes; fine-tuning shown to work across domains |
| Spectral KAN architecture | HIGH | Kolmogorov-Arnold network learns spectral-to-depth mapping; architecture is sensor-dependent, not location-dependent |

#### What Does NOT Transfer

| Component | Limitation | Mitigation |
|---|---|---|
| Water clarity assumptions | MN DNR training data is temperate clear-to-moderate lakes. Tropical turbid waters cause SDB underestimation. Sentinel-2 SDB effective to ~20m in clear water, but only a few meters in turbid conditions | Multi-temporal compositing to minimize turbidity; add turbidity index as input feature; use 704nm band as turbidity proxy |
| Substrate reflectance | Dark volcanic substrates (e.g., NZ, Japan) vs. sandy bottoms (Caribbean) affect spectral response | Include substrate type as a feature; train on diverse substrate samples |
| Depth range | MN lakes are mostly 0-30m. Tropical reef systems can be 0-50m+ | Cap predictions at validated depth range; use ICESat-2 max-depth as constraint |
| Vegetation/coral | Submerged aquatic vegetation and coral confound spectral depth signal | Add NDVI-like water vegetation index; train separate coral reef model |

#### Retraining Verdict: PARTIAL — architecture transfers, weights need regional calibration

The Spectral KAN and Depth Anything V2 architectures transfer directly. Weights need calibration per water-type regime (not per country — group by optical water type):

- **Type 1 (Clear temperate):** Direct transfer from MN DNR model. Covers: Northern Europe, NZ South Island, Canada, northern Japan.
- **Type 2 (Clear tropical):** Fine-tune on Caribbean/Pacific reef bathymetry. ICESat-2 ATL24 has excellent coral reef coverage. Sentinel-2 penetrates well in clear tropical water.
- **Type 3 (Turbid/humic):** Requires dedicated turbidity-aware model. Add multi-temporal compositing, turbidity features. Covers: SE Asia, estuaries, tropical rivers.
- **Type 4 (Glacial/alpine):** Fine-tune on Tibetan Plateau-style data (existing research). Covers: NZ Southern Alps, Patagonia, Scandinavia.

**Key advantage:** The 3D-LAKES dataset already provides bathymetry for 510,530 global lakes. For many international lakes, we can use this as ground truth instead of needing local sonar surveys.

---

## 3. Per-Region Data Sources & Effort Estimates

### Overview Table

| Region | CPUE Data Source | CPUE Samples (est. available) | Min Needed | Bathy Transfer | Species DB Needed | Effort (person-months) | Timeline |
|---|---|---|---|---|---|---|---|
| **Australia** | State RecFish surveys (SA, WA, QLD), ABARES, tournament records | 5,000-10,000 | 1,000 | Type 1+2 (clear coastal + temperate lakes) | ~150 key species | 3-4 | Months 1-6 |
| **New Zealand** | MPI recreational surveys, NZ Sport Fishing Council, club records | 2,000-5,000 | 500 | Type 1+4 (clear temperate + glacial alpine) | ~60 species | 2-3 | Months 3-8 |
| **Europe (Nordic)** | Finland/Sweden catch stats, ICES WGRFS | 3,000-8,000 | 1,000 | Type 1 (clear temperate — direct transfer) | ~80 species | 3-4 | Months 4-10 |
| **Europe (Central)** | EU RecFishing system (2025), national surveys (DE, NL, PL) | 2,000-5,000 | 1,000 | Type 1+3 (temperate + some turbid) | ~100 species | 4-5 | Months 6-14 |
| **Europe (Mediterranean)** | Limited rec surveys, charter logs | 500-1,000 | 500 | Type 2 (clear coastal) | ~120 species | 4-5 | Months 8-16 |
| **Caribbean/Central Am.** | NOAA MRIP Caribbean, charter boat logs, tournament data | 500-2,000 | 500 | Type 2 (clear tropical reef) | ~200 game species | 3-4 | Months 6-12 |
| **Japan** | JFA white papers, e-Stat portal, prefectural surveys | 1,000-3,000 | 500 | Type 1 (temperate) | ~100 species | 5-6 | Months 10-18 |
| **Canada** | DFO recreational surveys, provincial data | 3,000-8,000 | 1,000 | Type 1+4 (temperate + subarctic) | ~80 species | 2-3 | Months 1-4 |

### 3.1 Australia / New Zealand

**CPUE Data Sources:**
- **Australia:** National Recreational Fishing Survey (every 5 years, funded by FRDC), state-level surveys (SA 2021-22 survey with detailed CPUE by species), ABARES fisheries data portal (data.gov.au), tournament catch records, club logbooks. Western Australia has documented CPUE spatial shifts linked to marine heatwaves — useful for validating weather-catch models.
- **New Zealand:** MPI (Ministry for Primary Industries) recreational fishing surveys, NZ Sport Fishing Council records, regional council catch data. Trout fisheries managed by Fish & Game NZ with detailed angler diary programs.

**Bathymetry Sources:**
- **AusSeabed:** National marine bathymetry portal (ausseabed.gov.au/data). Includes multibeam/singlebeam surveys at 30m-250m resolution. Covers marine/coastal — limited inland lake coverage.
- **LINZ:** NZ hydrographic data service (data.linz.govt.nz). Bathymetric data available as georeferenced TIFFs + digital files (post-2000 surveys). Good coastal coverage.
- **3D-LAKES:** Covers major AU/NZ reservoirs and lakes globally.
- **ICESat-2:** Excellent coverage of Great Barrier Reef, NZ coastal waters. SlideRule API works directly.

**Key Species:** Barramundi, Murray cod, golden perch, Australian bass, snapper, kingfish (AU). Rainbow/brown trout, chinook salmon, snapper, kingfish, kahawai (NZ).

**Language/Localization:** English — no translation barrier. Data formats compatible.

**Estimated Effort:** 3-4 months (AU), 2-3 months (NZ). Can run in parallel.

---

### 3.2 Europe

**CPUE Data Sources:**
- **EU-wide:** The EU adopted Commission Implementing Regulation (EU) 2025/274 in February 2025, establishing for the first time a harmonized system to collect catch data from recreational fisheries across all EU member states. The forthcoming "RecFishing" electronic system will standardize reporting — this is a massive opportunity for OpenCatch.
- **ICES WGRFS:** Working Group on Recreational Fisheries Surveys provides Baltic/North Sea recreational catch estimates. Coverage of pike, perch, pikeperch (zander), cod, sea bass.
- **Nordic countries:** Finland publishes detailed recreational catch statistics (pike: 8.2M kg, zander: 4.3M kg in 2022). Sweden, Norway have similar programs.
- **UK:** Sea Angling surveys, Environment Agency coarse fishing data, Scottish Fisheries data.

**Bathymetry Sources:**
- **EMODnet:** Harmonized European marine DTM at ~100m resolution (emodnet-bathymetry.eu). Includes lake bathymetry at 30m resolution worldwide. Free and open access.
- **National surveys:** Many European countries have detailed lake bathymetry from national hydrographic agencies.
- **ICESat-2 + 3D-LAKES:** Covers European lakes/reservoirs.

**Key Species:** Pike (Esox lucius), perch (Perca fluviatilis), zander/pikeperch (Sander lucioperca), carp (Cyprinus carpio), brown trout, Atlantic salmon, sea bass (Dicentrarchus labrax), cod.

**Language/Localization:** Multi-language required (EN, DE, FR, ES, IT, FI, SV, NO, etc.). Species names need localization. Data formats vary by country — the new EU RecFishing system will help standardize.

**Estimated Effort:** 3-4 months (Nordic first wave), 4-5 months (Central Europe), 4-5 months (Mediterranean).

---

### 3.3 Caribbean / Central America

**CPUE Data Sources:**
- **US territories:** NOAA MRIP covers Puerto Rico, USVI — but USVI recreational data (excluding tournaments) is noted as unavailable. NOAA's Caribbean Regional Implementation Plan is being finalized.
- **Independent nations:** Very limited formal CPUE data. Charter boat operators are the primary source — but logs are not centralized.
- **Tournaments:** Billfish tournaments (e.g., Bisbee's, IGFA events) provide high-quality catch records for pelagic species.
- **Strategy:** Partner with charter booking platforms (FishingBooker, etc.) to aggregate trip-level catch data. This is likely the only scalable approach.

**Bathymetry Sources:**
- **ICESat-2 ATL24:** Excellent coverage of Caribbean coral reefs and clear shallow waters. Sentinel-2 SDB works well in clear Caribbean water (visibility often 20m+).
- **NOAA NCEI:** Existing bathymetric surveys for US Caribbean waters.
- **Coral reef studies:** Significant academic literature on satellite-derived bathymetry for Caribbean reefs — useful for training data.

**Key Species:** Mahi-mahi, yellowfin tuna, wahoo, blue/white marlin, sailfish, snapper (mutton, yellowtail, red), grouper (Nassau, black), bonefish, tarpon, permit.

**Challenge:** Pelagic (offshore) species have very different CPUE dynamics than freshwater — current model is freshwater-optimized. Need a dedicated saltwater/offshore CPUE model.

**Estimated Effort:** 3-4 months. Data acquisition is the bottleneck, not modeling.

---

### 3.4 Japan

**CPUE Data Sources:**
- **Japan Fisheries Agency (JFA):** Publishes annual White Papers on Fisheries with national statistics. Recreational fishing population is ~8.7 million anglers.
- **e-Stat portal (e-stat.go.jp):** Official statistics database with sport fishing data by type and guide. However, recreational catches are tracked separately from commercial data and may have gaps.
- **Prefectural data:** Individual prefectures publish local fishing statistics — must be aggregated manually.
- **Challenge:** Data is primarily in Japanese. Need translation pipeline. Inland recreational CPUE data appears sparse compared to commercial marine data.

**Bathymetry Sources:**
- **JODC:** 500m gridded bathymetry (J-EGG500) covering waters around Japan. Primarily marine/coastal.
- **3D-LAKES + ICESat-2:** Covers Japanese lakes and reservoirs.
- **Challenge:** Japan has many small mountain streams and rivers for ayu/iwana/yamame fishing — these are too narrow for satellite bathymetry.

**Key Species:** Sea bream (madai), yellowtail (hamachi/buri), squid (ika), ayu, iwana (char), yamame (cherry trout), rainbow trout, largemouth bass (invasive but heavily fished), black porgy (kurodai).

**Language/Localization:** Full Japanese localization required. Species names, UI, data pipelines all need JP support.

**Estimated Effort:** 5-6 months. Language barrier and fragmented data are the main costs.

---

### 3.5 Canada (Bonus — Low-Hanging Fruit)

**CPUE Data Sources:**
- **DFO (Fisheries and Oceans Canada):** National recreational fishing survey data. Provincial surveys from Ontario, BC, Alberta, Quebec.
- **NHN (National Hydro Network):** Already integrated in the existing pipeline (`fetch_nhn_canada.py` exists).
- **Provincial creel surveys:** Ontario MNRF, BC recreational fishing surveys.

**Bathymetry Sources:**
- **3D-LAKES:** Full coverage of Canadian lakes.
- **Provincial sonar data:** Ontario has lake-specific depth maps. BC, Alberta have reservoir data.
- **ICESat-2:** Excellent northern lake coverage.

**Key Species:** Walleye, northern pike, smallmouth/largemouth bass, lake trout, brook trout, rainbow trout, chinook/coho/sockeye salmon, steelhead, musky, perch.

**Advantage:** Many species overlap with US. Weather features transfer directly. Similar regulatory frameworks. English language. NHN pipeline already built.

**Estimated Effort:** 2-3 months. Easiest international expansion.

---

## 4. Transfer Learning Strategy: Foundation-to-Fine-Tune Pipeline

### 4.1 CPUE Model Pipeline

```
┌─────────────────────────────────────────────────────┐
│              GLOBAL FOUNDATION MODEL                 │
│  Features: weather, solunar, moon, photoperiod,     │
│  temperature, pressure, wind, precipitation,         │
│  morphometry (area, depth, elevation), temporal lags │
│  Training: Combined US + all international data      │
│  Output: base_cpue_prediction                        │
└──────────────────────┬──────────────────────────────┘
                       │
        ┌──────────────┼──────────────────┐
        ▼              ▼                  ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│  US Regional │ │  AU Regional │ │  EU Regional │  ... per region
│  Head Model  │ │  Head Model  │ │  Head Model  │
│              │ │              │ │              │
│ +species     │ │ +species     │ │ +species     │
│ +stocking    │ │ +rec_survey  │ │ +ICES_data   │
│ +regulations │ │ +regulations │ │ +regulations │
│ +local_lakes │ │ +local_lakes │ │ +local_lakes │
└──────┬───────┘ └──────┬───────┘ └──────┬───────┘
       ▼                ▼                ▼
  final_pred =    final_pred =     final_pred =
  base + regional base + regional  base + regional
```

**Implementation steps:**

1. **Extract universal features** from current V15 model — isolate the weather/solunar/temporal feature set
2. **Train global backbone** on US data using only universal features — establish baseline R²
3. **Validate zero-shot transfer** — apply global backbone to first international dataset (Australia or Canada) using only universal features
4. **Train regional correction model** — XGBoost on residuals using local features
5. **Stack and validate** — measure lift from regional correction over global-only
6. **Iterate** — add more regions, monitor whether global backbone improves with more diverse data

**Minimum viable dataset per region:**

| Scenario | Records Needed | Coverage | Expected R² |
|---|---|---|---|
| Global backbone only (zero-shot) | 0 (no local data) | Weather-driven predictions only | 0.15-0.25 |
| Minimal fine-tune | 200-500 records, 20+ water bodies, 6+ months | Rough regional correction | 0.25-0.40 |
| Solid fine-tune | 1,000-2,000 records, 50+ water bodies, 12+ months | Seasonal patterns captured | 0.40-0.55 |
| Full regional model | 5,000+ records, 100+ water bodies, 24+ months | Approaches US model quality | 0.55-0.70 |

### 4.2 Bathymetry Model Pipeline

```
┌──────────────────────────────────────────────────────────┐
│                   GLOBAL BATHYMETRY PIPELINE              │
│                                                          │
│  ┌─────────────────┐  ┌────────────────────────────────┐ │
│  │ ICESat-2 (ATL24) │  │ Sentinel-2 Composites          │ │
│  │ via SlideRule API │  │ (existing build_s2_composites) │ │
│  └────────┬─────────┘  └──────────┬─────────────────────┘ │
│           │                       │                       │
│           ▼                       ▼                       │
│  ┌─────────────────┐  ┌─────────────────────────────────┐ │
│  │ 3D-LAKES lookup │  │ Spectral KAN (per water type)   │ │
│  │ (510K lakes)    │  │  - Type 1: Clear temperate      │ │
│  └────────┬────────┘  │  - Type 2: Clear tropical       │ │
│           │           │  - Type 3: Turbid               │ │
│           │           │  - Type 4: Glacial/alpine        │ │
│           │           └──────────┬──────────────────────┘ │
│           │                      │                        │
│           ▼                      ▼                        │
│  ┌────────────────────────────────────────────────────┐   │
│  │ Depth Anything V2 (fine-tuned per water type)      │   │
│  │ Ensemble: spectral_kan + depth_anything + icesat2  │   │
│  └────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
```

**Key insight:** The bathymetry pipeline's biggest advantage is that ICESat-2 and Sentinel-2 are already global sensors. The existing `fetch_icesat2.py` and `fetch_sentinel2.py` scripts work for any lat/lon. The main work is:

1. **Water type classification** — Build a simple classifier (turbidity from S2 bands) to route each lake to the correct Spectral KAN variant
2. **Fine-tune per water type** — Collect ~100-500 lakes with known bathymetry per water type category
3. **Validate with 3D-LAKES** — Use the 510K-lake dataset as independent validation for international predictions
4. **Handle edge cases** — Mountain streams (too narrow), very deep lakes (beyond S2 penetration), volcanic substrates

**Minimum calibration data per water type:**

| Water Type | Calibration Lakes Needed | Source for Ground Truth |
|---|---|---|
| Clear temperate | 0 (current MN DNR model) | Already trained |
| Clear tropical | 50-100 lakes/reefs | ICESat-2 ATL24 + coral reef bathymetry studies |
| Turbid | 100-200 water bodies | ICESat-2 + multi-temporal S2 compositing |
| Glacial/alpine | 30-50 lakes | ICESat-2 + Tibetan Plateau research data |

---

## 5. Data Acquisition Roadmap

### Phase 1: Months 1-6 (Canada + Australia)

**Canada (Months 1-4):**
- [ ] Extend existing NHN pipeline to include provincial CPUE data from DFO
- [ ] Pull Ontario MNRF creel survey data (walleye, bass, pike — overlaps with US species)
- [ ] Query ICESat-2/SlideRule for top 500 Canadian fishing lakes
- [ ] Validate bathymetry pipeline on Canadian lakes using 3D-LAKES as ground truth
- [ ] Fine-tune CPUE regional head on Canadian data
- [ ] Target: Functional Canadian model by month 4

**Australia (Months 1-6):**
- [ ] Contact ABARES / FRDC for recreational fishing survey data access
- [ ] Scrape state-level survey reports (SA, WA, QLD published PDFs)
- [ ] Download AusSeabed coastal bathymetry for key fishing areas
- [ ] Query ICESat-2 for GBR and major recreational fishing coastal zones
- [ ] Build tropical clear-water Spectral KAN variant using GBR data
- [ ] Fine-tune CPUE regional head — focus on barramundi/bass/snapper
- [ ] Target: Beta Australian model by month 6

### Phase 2: Months 4-12 (New Zealand + Nordic Europe)

**New Zealand (Months 4-8):**
- [ ] Access LINZ hydrographic data (free since 2016)
- [ ] Contact Fish & Game NZ for trout angler diary data
- [ ] MPI recreational fishing survey data request
- [ ] Validate glacial/alpine water type on NZ South Island lakes
- [ ] Target: NZ model by month 8

**Nordic Europe (Months 4-10):**
- [ ] Access Finland/Sweden public recreational catch statistics
- [ ] ICES WGRFS data request for Baltic recreational fisheries
- [ ] EMODnet bathymetry download for Nordic lakes and coastal areas
- [ ] Train European freshwater species group (pike, perch, zander)
- [ ] Target: Nordic model by month 10

### Phase 3: Months 6-16 (Caribbean + Central Europe)

**Caribbean (Months 6-12):**
- [ ] Partner with charter platforms for trip-level catch data
- [ ] NOAA MRIP Puerto Rico data download
- [ ] Build tropical reef bathymetry variant (ICESat-2 ATL24 reef data)
- [ ] Design offshore/pelagic CPUE model variant (different feature set than freshwater)
- [ ] Target: Caribbean saltwater model by month 12

**Central Europe (Months 8-14):**
- [ ] Monitor EU RecFishing system rollout — integrate when available
- [ ] German/Dutch/Polish recreational fishing survey data
- [ ] Carp-specific model variant (dominant EU recreational species)
- [ ] Target: Central European model by month 14

### Phase 4: Months 10-24 (Japan + Mediterranean + Expansion)

**Japan (Months 10-18):**
- [ ] Japanese data translation pipeline (species names, location names, units)
- [ ] JFA White Paper data extraction
- [ ] e-Stat portal data download and parsing
- [ ] Prefectural data aggregation
- [ ] JODC bathymetry integration
- [ ] Target: Japan model by month 18

**Mediterranean (Months 12-18):**
- [ ] Greek/Italian/Spanish recreational survey data
- [ ] Charter boat partnership for Mediterranean catch data
- [ ] Target: Mediterranean model by month 18

---

## 6. Cost Estimates

### Compute Costs (Vast.ai)

| Task | GPU Hours | Cost @ $0.016/hr | Notes |
|---|---|---|---|
| Global CPUE backbone training | 20 hrs | $0.32 | XGBoost is CPU-bound, minimal GPU |
| Per-region CPUE fine-tune (x8 regions) | 40 hrs total | $0.64 | Small datasets, fast training |
| Spectral KAN per water type (x4 types) | 80 hrs total | $1.28 | KAN training on S2 composites |
| Depth Anything V2 fine-tune (x4 types) | 200 hrs total | $3.20 | GPU-intensive, ViT backbone |
| ICESat-2 processing (global queries) | 40 hrs | $0.64 | SlideRule cloud processing |
| S2 composite building (international) | 100 hrs | $1.60 | GEE + local processing |
| **Total compute** | **~480 hrs** | **~$7.68** | Vast.ai RTX A5000 |

### Data Acquisition Costs

| Item | Cost | Notes |
|---|---|---|
| Most government data | $0 | Open data (AusSeabed, LINZ, EMODnet, ICESat-2, 3D-LAKES, ICES, Finland stats) |
| ABARES special data requests | $0-500 | May require formal data agreement |
| Charter platform partnerships | $0-2,000 | Data sharing agreements; may need revenue share |
| Japanese data translation | $500-1,000 | Automated + manual spot-check |
| EU RecFishing API integration | $0 | Public system when launched |
| **Total data acquisition** | **$1,000-3,500** | |

### Personnel Time

| Phase | Duration | Effort |
|---|---|---|
| Phase 1 (Canada + Australia) | 6 months | ~0.5 FTE |
| Phase 2 (NZ + Nordic) | 6 months | ~0.5 FTE |
| Phase 3 (Caribbean + Central EU) | 6 months | ~0.5 FTE |
| Phase 4 (Japan + Med) | 6 months | ~0.5 FTE |
| **Total** | **24 months** | **~1 FTE equivalent** |

---

## 7. Priority Ranking (Easiest to Hardest)

| Rank | Region | Rationale |
|---|---|---|
| 1 | **Canada** | NHN pipeline exists, species overlap with US, English, similar regulations, DFO data accessible. Lowest marginal effort. |
| 2 | **Australia** | Excellent open data (ABARES, AusSeabed), English, strong recreational fishing culture (5M+ anglers), clear government data infrastructure. |
| 3 | **New Zealand** | Free LINZ bathymetry, Fish & Game trout data, English, smaller market but very data-accessible. |
| 4 | **Nordic Europe (FI, SE, NO)** | Excellent public statistics (Finland especially), clear temperate lakes (direct bathy transfer), manageable species set. |
| 5 | **Caribbean** | Clear water = great for bathymetry. Challenge is CPUE data scarcity — need charter partnerships. Offshore species need dedicated model. |
| 6 | **Central Europe (DE, NL, PL)** | EU RecFishing system is a game-changer when ready. Carp/pike/zander are well-studied. Some turbidity challenges for bathymetry. |
| 7 | **Mediterranean** | Limited recreational data infrastructure. Diverse species. Mixed water clarity. |
| 8 | **Japan** | Language barrier, fragmented data across prefectures, separate data formats. Large market potential but highest integration cost. |

---

## 8. Technical Implementation Checklist

### Codebase Changes Required

**CPUE Pipeline:**
- [ ] Refactor feature engineering to separate universal vs. regional features
- [ ] Add species-group embedding system (species -> ecological niche vector)
- [ ] Add climate zone / ecoregion feature (replace US state encoding)
- [ ] Build global backbone training script
- [ ] Build regional head training script (residual stacking)
- [ ] Add multi-region evaluation framework (per-region R², global R²)
- [ ] Data ingestion adapters per region (format normalization)

**Bathymetry Pipeline:**
- [ ] Add water type classifier (turbidity from S2 bands)
- [ ] Parameterize `fetch_icesat2.py` for arbitrary global coordinates (verify it already works)
- [ ] Parameterize `build_s2_composites.py` for arbitrary global coordinates
- [ ] Train 3 additional Spectral KAN variants (tropical, turbid, glacial)
- [ ] Add 3D-LAKES integration for global lake validation
- [ ] Build multi-temporal compositing for turbidity mitigation

**Infrastructure:**
- [ ] Multi-language species database (common name, scientific name, local names per language)
- [ ] Country/region configuration system (data sources, species lists, regulations, seasons)
- [ ] Evaluation dashboard comparing model performance across regions

---

## 9. Key Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Insufficient CPUE data in some regions | HIGH | HIGH | Start with charter partnerships; use citizen science apps (iNaturalist, Fishbrain) as supplementary data |
| Turbid water bathymetry fails | MEDIUM | MEDIUM | Multi-temporal compositing; fall back to 3D-LAKES precomputed bathymetry; ICESat-2-only depth profiles |
| Weather features don't transfer | LOW | HIGH | Weather-to-fish physiology is well-established across species; validate with zero-shot test on AU/CA data early |
| Language/localization blockers | MEDIUM | MEDIUM | Start with English-speaking markets; defer Japan until clear ROI from earlier expansions |
| Data licensing restrictions | MEDIUM | LOW | Most target datasets are open access; negotiate early for restricted datasets |
| EU RecFishing system delayed | MEDIUM | LOW | Fall back to individual country data; Nordic countries already have good data independently |

---

## 10. Success Metrics

| Metric | Target | Measurement |
|---|---|---|
| Per-region CPUE R² (walk-forward) | > 0.30 within 6 months of launch | Walk-forward temporal validation |
| Per-region CPUE R² (mature) | > 0.45 within 18 months | Matching current US best |
| Bathymetry RMSE (new water types) | < 2.0m | Validated against 3D-LAKES or local sonar |
| Countries with functional predictions | 5+ by month 12 | US, CA, AU, NZ, FI/SE |
| Total international water bodies covered | 10,000+ by month 12 | Bathymetry predictions available |
| International user-validated accuracy | > 70% "useful" rating | In-app feedback on prediction quality |
