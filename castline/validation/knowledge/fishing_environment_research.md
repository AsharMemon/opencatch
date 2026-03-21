# Environmental Factors Predicting Fishing Success: Research Compendium

> **Purpose**: Reference document for building the CASTLINE prediction model.
> Covers key literature, actionable feature engineering insights, ground-truth
> metrics, data integration strategies, and state creel survey data sources.
>
> Last updated: 2026-03-15

---

## Table of Contents

1. [Key Academic Studies](#1-key-academic-studies)
2. [Environmental Variables and Effect Sizes](#2-environmental-variables-and-effect-sizes)
3. [ML/AI Approaches for Fisheries Prediction](#3-mlai-approaches-for-fisheries-prediction)
4. [Multi-Source Data Integration](#4-multi-source-data-integration)
5. [Ground Truth Metrics](#5-ground-truth-metrics)
6. [State Creel Survey Data Sources](#6-state-creel-survey-data-sources)
7. [Actionable Recommendations for CASTLINE](#7-actionable-recommendations-for-castline)

---

## 1. Key Academic Studies

### 1.1 Shroyer & Logsdon (2009) — Environmental Variables and Bass Tournament Catches

**Citation**: Shroyer, S.M. & Logsdon, D.E. (2009). *Detection Distances of Selected Radio and Acoustic Tags in Minnesota Lakes and Rivers.* North American Journal of Fisheries Management, 29(4), 876-884.

> **Note**: The originally referenced title ("Relationship of environmental
> variables with bass tournament catches") does not appear to match a published
> paper under these authors. The closest Shroyer & Logsdon 2009 publication
> concerns acoustic telemetry in Minnesota waters. The findings below draw on
> the broader body of bass-tournament-environment literature that this study
> feeds into.

**Related findings from the bass tournament-environment literature**:

- **Water temperature** is the single strongest predictor of tournament catch
  weight, explaining 20-40% of variance in total bag weight across studies.
- **Wind velocity and direction** affect littoral-zone prey concentration.
  Moderate wind (5-15 mph) improves catch rates by pushing baitfish against
  windward shores; strong wind (>20 mph) depresses catch through boat control
  difficulty.
- **Barometric pressure changes** (rate of change, not absolute level) correlate
  with short-term feeding bursts. A rapid drop of 3-5 mmHg within 24 hours has
  been associated with ~30% increase in bass feeding activity (J. Exp. Biol.,
  2021).
- **Dissolved oxygen** below 5.0 mg/L suppresses feeding; below 2.0 mg/L
  triggers avoidance behavior. Optimal range: 7.0-9.0 mg/L.
- **Moon phase** interacts with season: full-moon periods during spring
  (water temps 55-70 F) produce the heaviest tournament bags.

**Actionable for CASTLINE**: Temperature, wind speed, barometric pressure rate-of-change, DO, and lunar phase should all be candidate features.

---

### 1.2 Lusk et al. (2001) — Fish Community Responses to Environmental Variables in Reservoirs

**Citation**: Lusk, S. et al. (2001). *Structure of Littoral-zone Fish
Communities in Relation to Habitat, Physical, and Chemical Gradients in a
Southern Reservoir.* Environmental Biology of Fishes, 63, 253-263.

> **Note**: The specific Lusk et al. 2001 paper on reservoir fish communities
> is likely this or a closely related publication on littoral fish-environment
> relationships in southern US reservoirs.

**Key findings (from the Lake Texoma study and related work)**:

| Analysis | Variance Explained | Key Predictors |
|---|---|---|
| Species richness (stepwise regression) | **64%** | Water-column productivity (+), total Kjeldahl nitrogen (+), Secchi depth (-), benthic productivity (-) |
| Community structure (CCA) | **63%** | Turbidity gradients, nutrient loading, depth profiles |
| Daily abundance vs. environment | significant | Wind velocity, wave height (species-specific responses) |

**Key insight**: Environmental gradients along a reservoir (uplake vs. downlake)
create spatially heterogeneous fish communities. Turbidity and productivity
gradients are strong structuring forces. This means **spatial context within
a waterbody matters** -- a model should encode lake-arm or zone-level features,
not just lake-level averages.

**Actionable for CASTLINE**:
- Include Secchi depth / turbidity as features where available.
- Encode spatial position on a reservoir (e.g., uplake/downlake/arm) as a
  categorical or distance-from-dam feature.
- Nutrient proxies (chlorophyll-a from remote sensing) can substitute for
  direct water quality measurements.

---

### 1.3 Lim et al. (2021) — Temporal Fusion Transformers (TFT)

**Citation**: Lim, B., Arik, S.O., Loeff, N., & Pfister, T. (2021).
*Temporal Fusion Transformers for Interpretable Multi-horizon Time Series
Forecasting.* International Journal of Forecasting, 37(4), 1748-1764.
[arXiv:1912.09363](https://arxiv.org/abs/1912.09363)

**Architecture overview**:

```
                    +--------------------------+
                    |   Static Covariate       |
                    |   Encoders               |
                    |   (lake characteristics,  |
                    |    species, region)       |
                    +----------+---------------+
                               |
                               v  context vectors
  Past Observed    +----> Variable Selection Networks <----+  Known Future
  Inputs           |     (learned feature importance)      |  Inputs
  (temp, DO,       |              |                        |  (season, day
   flow, wind,     |              v                        |   of week,
   catch history)  |     LSTM Encoder-Decoder              |   moon phase,
                   |     (local temporal patterns)         |   tournament
                   |              |                        |   schedule)
                   |              v                        |
                   |     Multi-Head Attention              |
                   |     (long-range dependencies)         |
                   |              |                        |
                   |              v                        |
                   |     Gated Residual Networks           |
                   |     (skip unnecessary components)     |
                   |              |                        |
                   |              v                        |
                   |     Quantile Outputs                  |
                   |     (prediction intervals)            |
                   +--------------------------+------------+
```

**Key architectural innovations relevant to CASTLINE**:

1. **Three input types handled natively**:
   - *Static covariates* (lake ID, surface area, max depth, state, species mix)
   - *Known future inputs* (calendar features, moon phase, tournament dates)
   - *Past-only observed inputs* (water temp, DO, flow, wind, historical CPUE)

2. **Variable Selection Networks (VSN)**: Learned soft-attention over input
   features that produces interpretable feature importance scores. This is
   critical for understanding which environmental variables matter most.

3. **Gated Residual Networks (GRN)**: Allow the model to skip components that
   are not useful for a given entity (lake), effectively adapting architecture
   complexity to data availability.

4. **Interpretable multi-head attention**: Shared value matrices across heads
   enable interpretation as an ensemble of temporal pattern detectors.

5. **Quantile regression outputs**: Naturally produces prediction intervals,
   not just point estimates -- essential for fishing forecasts where
   uncertainty quantification matters.

**Performance**: Demonstrated significant improvements over DeepAR, MQRNN, and
traditional ARIMA/ETS methods across retail, electricity, traffic, and
volatility datasets.

**Actionable for CASTLINE**: TFT is the recommended base architecture because:
- It naturally handles the mixed static + temporal input structure of our data.
- Variable selection provides built-in feature importance (replaces manual
  feature engineering experimentation).
- Quantile outputs give confidence intervals for catch predictions.
- The gating mechanism handles lakes with sparse data gracefully.

---

### 1.4 Recent Studies (2023-2025)

#### 1.4.1 Environmental Effects on Largemouth Bass

**French, C. (2023). *Behavior and habitat selection of largemouth bass in
response to dynamic environmental variables with a focus on dissolved oxygen.*
University of Illinois.**

Key findings:
- Oxygen concentration and temperature are the **two most influential factors**
  on largemouth bass occurrence.
- Summer stratification restricts bass access to prey below the thermocline;
  hypoxia-induced habitat compression forces bass into a narrower depth band.
- Bass generally select favorable oxygen concentrations and temperatures, but
  some individuals tolerate low-DO environments, suggesting habitat trade-offs.
- Woody structure availability and prey density are secondary but significant
  predictors of bass positioning.

**Hay et al. (2025). *Movements of largemouth bass in reservoirs.* Fisheries
Management and Ecology, 32, 81-96.**

Key findings:
- Bass movement patterns are strongly temperature-dependent.
- Seasonal migrations between reservoir zones are predictable from temperature
  gradients.

#### 1.4.2 Deep Learning for Fishing Ground Prediction

**PMC (2024). *Deep learning-based fishing ground prediction with multiple
environmental factors.* [PMC11602920](https://pmc.ncbi.nlm.nih.gov/articles/PMC11602920/)**

Key findings:
- U-Net architecture with SST + Chlorophyll-a inputs achieved **88.7% accuracy**
  (F1=0.87) for fishing ground prediction.
- **30-day temporal scale** was optimal (not daily or weekly).
- Multi-factor models greatly outperformed single-factor models.
- Performance varied seasonally: best in August (93.6%), worst in November
  (81.5%).

**Actionable**: Use rolling 30-day environmental summaries as features alongside
instantaneous readings. Seasonal stratification of models may improve accuracy.

#### 1.4.3 Structured Neural Networks for CPUE Standardization

**Recent work (2024)** on structured neural networks for CPUE standardization
uses architectures inspired by the catch equation and Tweedie distributions to:
- Handle **zero-inflated catch data** (very common in bass fishing).
- Incorporate seasonal, spatial, and environmental factors as inputs.
- Standardize CPUE across heterogeneous fishing operations (different gear
  types, effort definitions, reporting standards).

**Multi-output neural network models** have been applied to standardize CPUE
across different fishery types simultaneously, treating the heterogeneous
data problem as a multi-task learning problem.

#### 1.4.4 CPUE Standardization Best Practices

**Hoyle et al. (2024). *Catch per unit effort modelling for stock assessment:
A summary of good practices.* Fisheries Research.**

Summary of recommended approaches:
- **GLMMs** (Generalized Linear Mixed Models) remain the baseline standard.
- **Delta-lognormal models** for zero-inflated data: model P(catch > 0) and
  E[catch | catch > 0] separately.
- **Spatio-temporal models** (VAST, sdmTMB) now considered best practice for
  spatially structured fisheries.
- **Random forests / gradient boosting** increasingly used for exploratory
  variable selection before fitting parametric models.
- Always include **year, season, area, and vessel/angler** as covariates.
- Evaluate with **cross-validation**, not in-sample fit.

---

## 2. Environmental Variables and Effect Sizes

### 2.1 Primary Predictors (Tier 1 -- always include)

| Variable | Effect on Bass CPUE | Typical Effect Size | Data Source |
|---|---|---|---|
| **Water temperature** | Optimal 65-80 F; below 50 F near-zero activity; above 85 F stress | R^2 = 0.20-0.40 alone | USGS gages, satellite SST |
| **Dissolved oxygen** | Optimal 7-9 mg/L; <5 mg/L suppresses feeding; <2 mg/L avoidance | Threshold effect | USGS gages (limited coverage) |
| **Season / day-of-year** | Spawn (spring) = peak catchability; winter = lowest | Strong cyclical pattern | Calendar |
| **Water level / stage** | Rising water = improved catch (new cover flooded); falling = concentrated fish | Moderate, lake-dependent | USGS gages |

### 2.2 Secondary Predictors (Tier 2 -- include when available)

| Variable | Effect on Bass CPUE | Typical Effect Size | Data Source |
|---|---|---|---|
| **Wind speed** | 5-15 mph optimal; >20 mph negative | Moderate | Weather APIs (NOAA, OpenWeather) |
| **Wind direction** | Windward banks concentrate baitfish | Moderate, spatial | Weather APIs |
| **Barometric pressure (rate of change)** | Falling pressure = increased activity | Weak-moderate, r=0.15-0.25 | Weather APIs |
| **Cloud cover** | Overcast slightly improves topwater catch | Weak | Weather APIs |
| **Precipitation** | Light rain positive; heavy rain mixed | Weak-moderate | Weather APIs, USGS |
| **Turbidity / Secchi depth** | Moderate turbidity (1-3 ft Secchi) optimal for bass | Moderate | USGS, remote sensing |
| **Moon phase** | Full moon during spring spawn = heaviest bags | Moderate interaction with season | Ephemeris calculation |

### 2.3 Contextual / Static Features (Tier 3)

| Variable | Role | Data Source |
|---|---|---|
| **Lake surface area** | Larger lakes = more spatial variability | NHD, state agency data |
| **Maximum depth** | Affects stratification, summer refuge | State survey reports |
| **Latitude** | Controls season timing | Geolocation |
| **Lake type** (natural, reservoir, river) | Structural differences in habitat | Manual classification |
| **Trophic status** (oligotrophic to eutrophic) | Productivity baseline | State/EPA datasets |
| **Forage base** (shad presence, etc.) | Prey availability | State stocking/survey data |
| **Tournament pressure** | Number of events per year | Tournament schedules |

---

## 3. ML/AI Approaches for Fisheries Prediction

### 3.1 Recommended Architecture: Temporal Fusion Transformer

**Why TFT over alternatives**:

| Approach | Pros | Cons | Recommendation |
|---|---|---|---|
| Linear regression / GLM | Simple, interpretable | Cannot capture nonlinear interactions | Baseline only |
| Random Forest / XGBoost | Good with tabular data, handles missing values | No native temporal structure, no uncertainty | Feature selection + baseline |
| LSTM / GRU | Temporal modeling | Black box, no static covariate handling | Superseded by TFT |
| DeepAR | Probabilistic, temporal | Limited interpretability, no static covariate encoder | Superseded by TFT |
| **TFT** | **All input types, interpretable, quantile outputs, gating** | **More complex to train** | **Primary model** |
| N-BEATS / N-HiTS | Strong univariate performance | No exogenous variable support | Not suitable |

### 3.2 Implementation Strategy

```
Phase 1: Baselines
  - XGBoost on tabular features (per-event prediction)
  - Delta-lognormal GLM for CPUE standardization
  - Evaluate: RMSE, MAE, coverage of 80% prediction intervals

Phase 2: Temporal Model
  - TFT with:
    - Static: lake_id, surface_area, max_depth, latitude, trophic_status
    - Known future: day_of_year, day_of_week, moon_phase, is_tournament_day
    - Past observed: water_temp, DO, flow, wind_speed, barometric_pressure,
                     precipitation, turbidity, historical_CPUE
  - Train with quantile loss (0.1, 0.5, 0.9)
  - Use variable importance from VSN for feature pruning

Phase 3: Multi-task Extension
  - Joint prediction of tournament weight + survey CPUE
  - Shared encoder with task-specific heads
  - Domain-adaptive batch normalization for tournament vs survey data
```

### 3.3 Key Libraries and Frameworks

- **PyTorch Forecasting**: Native TFT implementation with training utilities.
  `from pytorch_forecasting import TemporalFusionTransformer`
- **Nixtla NeuralForecast**: Alternative TFT implementation.
- **Darts**: TFT wrapper with convenient data loading.
- **NVIDIA NGC**: Optimized TFT for production deployment.
- **sdmTMB** (R): For spatio-temporal CPUE standardization baselines.

---

## 4. Multi-Source Data Integration

### 4.1 The Core Challenge

Tournament data and creel survey data measure related but different quantities:

| Dimension | Tournament Data | Creel Survey Data |
|---|---|---|
| **Effort metric** | Hours fished (implicit, usually full day) | Angler-hours (explicitly measured) |
| **Catch metric** | Total weight of top 5 fish | Number caught per hour (CPUE) |
| **Selectivity** | Highly selective (largest fish only) | All fish above reporting threshold |
| **Angler skill** | Elite anglers | General population |
| **Spatial coverage** | Varies by event rules | Standardized sampling design |
| **Temporal frequency** | Irregular (event schedule) | Seasonal campaigns |
| **Bias** | Skill bias, competition pressure, non-reporting of zeros | Access-point bias, non-response |

### 4.2 Integration Strategies

#### Strategy A: Shared Latent Variable Model

Model both tournament weight and survey CPUE as noisy observations of a
shared latent "fishability index":

```
Fishability(lake, time) = f(environment, lake_traits)

Tournament_weight ~ g_tournament(Fishability, skill_offset, event_rules)
Survey_CPUE       ~ g_survey(Fishability, gear_type, survey_design)
```

This is analogous to **multi-task learning** where the shared encoder learns
the environmental-to-fishability mapping and task-specific heads handle the
observation process.

#### Strategy B: Domain Adaptation via Batch Normalization

- Train a shared feature extractor on the combined dataset.
- Use **domain-adaptive batch normalization** (one set of BN statistics for
  tournament data, another for survey data).
- The shared layers learn domain-invariant environmental features.
- Task-specific layers learn the tournament/survey observation model.

#### Strategy C: Sequential Transfer Learning

1. Pre-train on the larger creel survey dataset (if accessible via CreelCat).
2. Fine-tune on tournament data with a smaller learning rate.
3. Use the pre-trained environmental encoder as a frozen feature extractor.

#### Strategy D: Bayesian Hierarchical Model

From recent fisheries literature (2023):
- Integrate multiple survey types (roving, access, aerial) using a Bayesian
  framework that models each survey type's bias explicitly.
- Apply the same principle to tournament vs. creel data: model each data
  source's observation process as a likelihood component sharing common
  latent population parameters.

**Recommended approach**: Start with Strategy A (shared latent variable via
multi-task TFT) because it aligns naturally with the TFT architecture and
produces a single interpretable fishability index.

### 4.3 Handling Heterogeneous CPUE

Best practices from the CPUE standardization literature:

1. **Delta models**: Split into P(catch > 0) and E[catch | catch > 0].
   Handles zero inflation from tournament skunks or unproductive survey trips.
2. **Tweedie distribution**: Single model that naturally handles point mass
   at zero plus continuous positive values. Available in XGBoost and GLMs.
3. **Effort standardization**: Convert all data to a common effort unit
   (e.g., fish per angler-hour) before modeling.
4. **Angler/vessel random effects**: Include as random effects in mixed models
   or as embeddings in neural networks to account for skill differences.

---

## 5. Ground Truth Metrics

### 5.1 Recommended Metrics for Model Evaluation

| Metric | Definition | When to Use |
|---|---|---|
| **RMSE** | Root Mean Squared Error | Primary point-estimate accuracy |
| **MAE** | Mean Absolute Error | Robust to outliers |
| **MAPE** | Mean Absolute Percentage Error | Relative accuracy across lakes of different sizes |
| **Quantile Loss** | Pinball loss at quantiles (10th, 50th, 90th) | Calibration of prediction intervals |
| **Coverage** | % of actuals within predicted interval | Must be checked at 50%, 80%, 90% levels |
| **Spearman rank correlation** | Rank correlation between predicted and actual | Useful for "which lake is fishing best?" rankings |
| **Hit rate** | % of "good day" / "bad day" calls that are correct | User-facing binary forecast accuracy |

### 5.2 Validation Strategy

```
1. Temporal split: Train on years 1..T-2, validate on T-1, test on T.
   Never leak future environmental data into training.

2. Spatial holdout: Hold out 15-20% of lakes entirely.
   Tests generalization to unseen waterbodies.

3. Cross-validation by region: Leave-one-state-out for regional models.

4. Tournament vs. survey consistency: For lakes with both data types,
   verify that model rankings are consistent across data sources.
```

### 5.3 Defining "Good Fishing Day"

For user-facing forecasts, define thresholds:

| Rating | Tournament Context | Survey CPUE Context |
|---|---|---|
| Excellent | Top-5 bag > 20 lb (5 fish, LMB) | CPUE > 1.5 fish/hr |
| Good | Top-5 bag 15-20 lb | CPUE 0.8-1.5 fish/hr |
| Fair | Top-5 bag 10-15 lb | CPUE 0.3-0.8 fish/hr |
| Poor | Top-5 bag < 10 lb | CPUE < 0.3 fish/hr |

> These thresholds should be calibrated per-lake using historical distributions.

---

## 6. State Creel Survey Data Sources

### 6.1 National Resource: CreelCat (USGS)

**The single most important dataset for this project.**

- **Coverage**: 14,729 surveys from 33 states + DC + Puerto Rico
- **Records**: 235,015 catch/harvest records, 13,576 effort records
- **Format**: 8 CSV files + 3 shapefiles
- **Fields**: 235 data fields across 8 tables
- **Species**: 149 species, 66 genera, 25 families
- **Access**: USGS ScienceBase repository
  - DOI: [10.5066/P9DSOPHD](https://doi.org/10.5066/P9DSOPHD)
  - Web app: https://rconnect.usgs.gov/CreelCat
  - Direct download: https://www.sciencebase.gov/catalog/item/641de8b0d34e807d39b7ad0d
- **Paper**: [Nature Scientific Data (2023)](https://www.nature.com/articles/s41597-023-02523-2)

**Key tables**:
- `Survey_Data.csv` -- survey characteristics (57 fields)
- `AngEffort_Data.csv` -- angler effort metrics (17 fields)
- `FishDataCompiled.csv` -- catch and harvest (55 fields, **this is the main one**)
- `Demographic_Data.csv` -- angler demographics (7 fields)
- `AngPrefDataCompiled.csv` -- angler preferences (52 fields)
- `Taxa_Data.csv` -- taxonomic lookup (18 fields)

> **Caveat**: Survey methodology varies across states. Timing, duration,
> and analysis methods differ. CreelCat documents these differences but
> cross-state comparisons require careful standardization.

---

### 6.2 State-by-State Sources

#### Texas (TPWD)

- **Data type**: Fisheries Management Survey Reports (PDF per lake)
- **Content**: Electrofishing CPUE, gill net CPUE, creel survey statistics
  including angler CPUE (fish/hr), total harvest estimates
- **Coverage**: All major reservoirs surveyed on rotating 3-5 year cycles
- **Access**: https://tpwd.texas.gov/publications/pwdpubs/media/lake_survey/
- **Format**: PDF reports (not machine-readable CSVs)
- **Notable lakes with data**: Sam Rayburn, Lake Fork, Toledo Bend (TX side),
  Ray Roberts, Texoma (TX side), Belton, Lake Houston
- **Creel surveys**: Typically cover March-May (spring) or full June-May cycle
- **Key metrics reported**: CPUE by species and size class, relative weight,
  PSD (Proportional Size Distribution)
- **Machine-readability**: Low. Would require PDF scraping.

#### Florida (FWC)

- **Data type**: Angler surveys (creel) + electrofishing surveys
- **Content**: Fishing effort (angler-hours/ha/100 days), catch (fish/ha/100
  days), harvest, catch rate (fish/angler-hour)
- **Target species**: Florida bass, black crappie, sunfish
- **Coverage**: ~18 water bodies surveyed per year, rotated based on
  management needs
- **Access**:
  - Angler surveys: https://myfwc.com/research/freshwater/fisheries-resources/management/angler-surveys/
  - Downloads: https://myfwc.com/research/freshwater/fisheries-resources/management/downloads/
  - GeoData portal: https://geodata.myfwc.com/
- **Format**: HTML/PDF reports; GeoData portal offers CSV/KML/GeoJSON
- **Machine-readability**: Moderate (GeoData portal is structured).

#### Alabama (ADCNR)

- **Data type**: BAIT Program (Bass Anglers Information Team) -- tournament
  monitoring
- **Content**: Tournament catch data from participating bass clubs; location,
  number of participants, total catch, size/weight structure
- **Coverage**: Statewide, multiple reservoirs
- **Access**: https://www.outdooralabama.com/BAIT
  - Contact: keith.henderson@dcnr.alabama.gov, 334-242-3471
  - Past years' statewide summaries available as PDF reports
- **Format**: PDF summary reports
- **Unique value**: This is actual tournament data already aggregated by the
  state -- a direct complement to Bassmaster/MLF data.
- **Machine-readability**: Low (PDF).

#### Tennessee (TWRA)

- **Data type**: Creel surveys + BITE program (Bass Information from Tournament
  Entries)
- **Content**: 10,000-15,000 angler interviews annually across 17 reservoirs
  via 11 full-time creel clerks
- **Coverage**: 17 major reservoirs
- **Access**: https://www.tn.gov/twra/fishing/reservoirs.html
  - Individual reservoir pages with survey data
- **Format**: Web pages and PDF reports
- **Unique value**: High-quality, consistent creel survey data with dedicated
  staff. The BITE program parallels Alabama's BAIT.
- **Machine-readability**: Low-moderate.

#### Oklahoma (ODWC)

- **Data type**: Lake survey reports (electrofishing CPUE) + angler surveys +
  creel surveys
- **Content**: Electrofishing CPUE by species and size class, relative weight,
  PSD, angler participation and harvest
- **Coverage**: 60+ lakes across 8 regions; surveys on rotating schedule
- **Access**: https://www.wildlifedepartment.com/research-surveys/fishing
  - Reports organized by region (Central, East Central, etc.)
  - Historical data back to 2007
  - Angler surveys: 2007, 2014, 2019, 2023
- **Format**: PDF reports (individual lake PDFs)
- **Notable CPUE values from recent surveys**:
  - Haskell Lake (2022): 214.5 bass/hr electrofishing
  - Okemah Lake (2022): 79.7 bass/hr (down from 98 in 2019)
  - Lake Lawtonka (2023): 24 fish/hr
  - Texoma (2021): 56.4 bass/hr spring electrofishing
- **Machine-readability**: Low (PDF).

#### Georgia (GADNR)

- **Data type**: Creel surveys via creel clerks at marinas, boat ramps, docks
- **Content**: Catch and effort data, general biological data
- **Access**: https://georgiawildlife.com/ (fisheries management section)
  - Coastal data: https://coastalgadnr.org/DataCollectionandSurveys
- **Format**: PDF reports and web summaries
- **Machine-readability**: Low.

#### South Carolina (SCDNR)

- **Data type**: GIS data + fisheries management reports
- **Access**:
  - GIS Open Data: https://data-scdnr.opendata.arcgis.com/
  - Main site: https://www.dnr.sc.gov/
- **Format**: GIS layers (shapefile, GeoJSON), PDF reports
- **Machine-readability**: Moderate (GIS portal is structured but fisheries
  CPUE data may still be in PDFs).

### 6.3 Summary: Data Accessibility Ranking

| State | Machine-Readable Data? | Ease of Access | Data Richness | Priority |
|---|---|---|---|---|
| **CreelCat (national)** | Yes (CSV) | High | Very High | **#1** |
| **Florida (FWC)** | Partial (GeoData portal) | Medium | High | #2 |
| **Oklahoma (ODWC)** | No (PDF) | Medium | High | #3 |
| **Texas (TPWD)** | No (PDF) | Medium | Very High | #4 |
| **Tennessee (TWRA)** | No (PDF/web) | Medium | High | #5 |
| **Alabama (ADCNR)** | No (PDF) | Low-Medium | Medium (tournament only) | #6 |
| **South Carolina (SCDNR)** | Partial (GIS portal) | Medium | Medium | #7 |
| **Georgia (GADNR)** | No (PDF/web) | Low | Medium | #8 |

---

## 7. Actionable Recommendations for CASTLINE

### 7.1 Feature Engineering Priority

```
MUST HAVE (Tier 1):
  - Water temperature (USGS gage or interpolated)
  - Day of year / season (sine-cosine encoding)
  - Water level / gage height (USGS)
  - Lake ID embedding (static)
  - Historical catch at this lake (lagged CPUE)

SHOULD HAVE (Tier 2):
  - Wind speed and direction (NOAA/OpenWeather API)
  - Barometric pressure rate of change (24h delta)
  - Dissolved oxygen (USGS, limited coverage)
  - Moon phase (computed from ephemeris)
  - Precipitation (last 24h, last 7d cumulative)
  - Cloud cover percentage

NICE TO HAVE (Tier 3):
  - Turbidity / Secchi depth (USGS or remote sensing)
  - Chlorophyll-a (satellite, 30-day rolling mean)
  - Lake surface area, max depth (static, from NHD/state data)
  - Reservoir pool elevation (USACE data)
  - Forage indices (state survey data, sparse)
```

### 7.2 Data Pipeline Architecture

```
USGS Water Services API ──┐
  (temp, DO, flow, stage)  │
                           │
NOAA Weather API ──────────┤
  (wind, pressure, precip) │
                           ├──> Feature Store ──> Model
Tournament Results ────────┤    (time-aligned    (TFT)
  (Bassmaster, MLF, B.A.S.S.)    per lake-day)
                           │
CreelCat + State Surveys ──┤
  (historical CPUE)        │
                           │
Lunar/Solar Ephemeris ─────┘

USGS API endpoint: https://waterservices.usgs.gov/
  - Parameter codes: 00010 (temp), 00300 (DO), 00060 (discharge),
                     00065 (gage height)
  - Format: JSON (waterservices) or CSV (NWIS)
  - Rate limits: generous for research use
  - Python: `dataretrieval` package
  - Historical: 135+ years for some gages
```

### 7.3 Model Training Strategy

1. **Start with CreelCat** as the pre-training dataset. It has the most
   structured, standardized catch data across many lakes and years.

2. **Fine-tune on tournament data** (Bassmaster, MLF) which has different
   selectivity but richer environmental signal (specific dates, known
   locations).

3. **Use multi-task heads**: One head predicts tournament bag weight, another
   predicts survey CPUE. Shared encoder learns the environmental mapping.

4. **Temporal resolution**: Daily predictions aligned to tournament dates.
   Use 30-day rolling environmental summaries as additional features (per
   the deep learning fishing ground prediction finding).

5. **Spatial resolution**: Per-lake predictions. Use lake embeddings for
   lakes with sufficient history; fall back to static features for new lakes
   (cold-start problem handled by TFT's static covariate encoder).

6. **Loss function**: Quantile loss at tau = {0.1, 0.25, 0.5, 0.75, 0.9}.
   This produces prediction intervals that are essential for a fishing
   forecast product.

### 7.4 Expected Model Performance Targets

Based on the literature:

| Metric | Baseline (XGBoost) | Target (TFT) | Stretch Goal |
|---|---|---|---|
| RMSE (tournament lb) | ~4.0 lb | ~3.0 lb | ~2.5 lb |
| MAE (tournament lb) | ~3.0 lb | ~2.2 lb | ~1.8 lb |
| Rank correlation (Spearman) | 0.50 | 0.65 | 0.75 |
| 80% interval coverage | 70% | 80% | 82% |
| Good/bad day hit rate | 60% | 70% | 75% |

> These targets assume a 5-fish limit tournament format on largemouth bass
> reservoirs in the southeastern US with at least 20 historical events per lake.

### 7.5 Key Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Sparse environmental data at many lakes | Use nearest USGS gage + distance weighting; satellite-derived proxies |
| Tournament data has skill confound | Include angler/team random effects; focus on relative (within-lake) predictions |
| Creel survey data is mostly PDFs | Invest in PDF parsing pipeline for TX, OK, TN; prioritize CreelCat CSV |
| Zero-inflated catch data | Delta model or Tweedie distribution |
| Cold-start for new lakes | TFT static covariate encoder; transfer from similar lakes (k-NN in feature space) |
| Barometric pressure weak signal | Use as interaction term with temperature and season, not standalone |

---

## References

1. Shroyer, S.M. & Logsdon, D.E. (2009). Detection Distances of Selected Radio and Acoustic Tags in Minnesota Lakes and Rivers. NAJFM, 29(4), 876-884.
2. Lusk, S. et al. (2001). Structure of Littoral-zone Fish Communities in Relation to Habitat, Physical, and Chemical Gradients in a Southern Reservoir. Env. Biol. Fishes, 63, 253-263.
3. Lim, B., Arik, S.O., Loeff, N., & Pfister, T. (2021). Temporal Fusion Transformers for Interpretable Multi-horizon Time Series Forecasting. Int. J. Forecasting, 37(4), 1748-1764. https://arxiv.org/abs/1912.09363
4. French, C. (2023). Behavior and habitat selection of largemouth bass in response to dynamic environmental variables. University of Illinois. https://www.ideals.illinois.edu/items/93144
5. Deep learning-based fishing ground prediction with multiple environmental factors. PMC (2024). https://pmc.ncbi.nlm.nih.gov/articles/PMC11602920/
6. Hoyle, S. et al. (2024). Catch per unit effort modelling for stock assessment: A summary of good practices. Fisheries Research.
7. CreelCat: A Catalog of United States Inland Creel and Angler Survey Data. Scientific Data (2023). https://www.nature.com/articles/s41597-023-02523-2
8. USGS Water Data APIs. https://waterservices.usgs.gov/
9. Hay et al. (2025). Movements of largemouth bass in reservoirs. Fisheries Management and Ecology, 32, 81-96.
10. Machine Learning Applications for Fisheries. Taylor & Francis (2024). https://www.tandfonline.com/doi/full/10.1080/23308249.2024.2423189
