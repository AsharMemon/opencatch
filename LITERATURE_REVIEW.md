# Literature Review: Machine Learning for Fish Catch and CPUE Prediction

**Compiled: March 2026**
**Scope:** Academic papers and studies on predicting fish catch, CPUE, and recreational fishing outcomes using ML/statistical models, with emphasis on freshwater fisheries and environmental feature engineering.

---

## Table of Contents

1. [ML Models for Catch Prediction](#1-ml-models-for-catch-prediction)
2. [Feature Engineering for Fisheries](#2-feature-engineering-for-fisheries)
3. [Recreational Fishing and Angler Behavior Prediction](#3-recreational-fishing-and-angler-behavior-prediction)
4. [Spatial and Temporal Modeling](#4-spatial-and-temporal-modeling)
5. [Data Sources and Integration](#5-data-sources-and-integration)
6. [Transfer Learning and Cross-Location Generalization](#6-transfer-learning-and-cross-location-generalization)
7. [Ranking vs Regression Approaches](#7-ranking-vs-regression-approaches)
8. [Synthesis and Implications for CASTLINE](#8-synthesis-and-implications-for-castline)
9. [Full Reference List](#9-full-reference-list)

---

## 1. ML Models for Catch Prediction

### 1.1 Ensemble Tree Methods (XGBoost, LightGBM, Random Forest)

#### Rawat et al. (2025) — Random Forest for Estuarine CPUE Prediction

- **Title:** "Prediction of fish (Coilia nasus) catch using spatiotemporal environmental variables and random forest model in a highly turbid macrotidal estuary"
- **Journal:** Ecological Informatics
- **Model:** Random Forest (RF)
- **Features:** Salinity, suspended sediment concentration (SSC), water temperature, river discharge, mean tidal range
- **Performance:** R² = 0.89 (best model M19, using salinity + SSC + discharge)
- **Dataset:** Hourly fish catch data from Chikugo River estuary, Japan, 2009-2020 (spawning seasons)
- **Key Findings:**
  - Salinity (0.04-0.2 optimal range) and SSC (16-118 mg/L) were the most influential variables
  - The freshwater-saltwater interface showed highest catch rates
  - Hourly temporal resolution with environmental covariates was critical for high R²
- **CASTLINE Relevance:** Demonstrates that R² > 0.85 is achievable with RF when environmental variables directly relevant to fish physiology are included. The use of water chemistry (salinity, turbidity) rather than just weather is instructive — analogous to our need for USGS gage data beyond simple weather.

#### Dhapodkar et al. (2025) — Stacking Ensemble for Fish Abundance

- **Title:** "Ensemble Machine Learning for Fish Abundance Prediction: A Multi-Model Stacking Approach with Environmental and Fisheries Data"
- **Journal:** International Journal on Advanced Computer Engineering and Communication Technology
- **Model:** Two-tier stacking — base learners (Random Forest, XGBoost, KNN, MLP), meta-learner (Ridge Regression)
- **Features:** 16 total — 11 environmental (water temperature, pH, dissolved oxygen, nutrients) + 5 fisheries (fishing area, gear type, species ID)
- **Performance:** R² = 0.9922, MAE = 37.35, RMSE = 49.37
- **Dataset:** 1,000 observations, Manila Bay, Philippines, 2010-2024
- **Key Findings:**
  - Environment-only model yielded R² = -0.0045 (useless alone)
  - Fisheries-only model achieved R² = 0.9968
  - Combined model yielded optimal balance (R² = 0.9922)
  - Stacking consistently outperformed individual base models
- **CASTLINE Relevance:** The extremely high R² is partly due to the inclusion of fisheries metadata (gear type, area) which implicitly encode location/method identity. For CASTLINE, this suggests that location-level features (lake characteristics, tournament format) are essential — pure environmental features alone are insufficient. However, the small dataset (1,000 rows) and inclusion of gear/area features raises concerns about information leakage.

#### LightGBM-SHAP for Interpretable CPUE Prediction (2025)

- **Title:** "Interpretable fish abundance index prediction in tuna longline fisheries: A LightGBM-SHAP case study in the tropical Atlantic Ocean"
- **Journal:** Fisheries Research
- **Model:** LightGBM with SHAP interpretation
- **Features:** Raw environmental variables without dimensionality reduction — SST, SSH, SSS, dissolved oxygen at depth, chlorophyll-a, latitude, longitude, month
- **Performance:** R² > 0.84 across all species (bigeye tuna, yellowfin tuna, swordfish)
- **Key Findings:**
  - Raw environmental variables outperformed PCA-reduced features
  - Most influential factors were spatiotemporal (month, latitude, longitude) rather than purely environmental
  - Species-specific environmental determinants identified via SHAP: depth at 250m for yellowfin, depth at 450m for swordfish
  - Spatial validation confirmed SHAP predictions matched observed CPUE distributions
- **CASTLINE Relevance:** Validates the use of gradient boosting methods and highlights that spatiotemporal features (location, time of year) often dominate over environmental variables. The success of raw features over engineered/reduced features is relevant to our pipeline design.

#### Comparative ML Study — LightGBM for Yellowfin Tuna Distribution (2024)

- **Title:** "A Comparative Machine Learning Study Identifies Light Gradient Boosting Machine (LightGBM) as the Optimal Model for Unveiling the Environmental Drivers of Yellowfin Tuna Distribution Using SHAP Analysis"
- **Journal:** Biology (MDPI)
- **Model:** 16 regression models compared; LightGBM selected as optimal
- **Features:** SST, SSS, SSH, dissolved oxygen, chlorophyll-a, and additional oceanographic variables
- **Key Findings:**
  - LightGBM demonstrated superior nonlinear fitting and generalization
  - SHAP provided global and local interpretability
  - Environmental features contributed meaningfully only when spatiotemporal context was preserved
- **CASTLINE Relevance:** Confirms LightGBM/XGBoost as top-tier methods for environmental fisheries prediction. The SHAP workflow is directly applicable to our feature importance analysis.

### 1.2 Deep Learning Models

#### Agmata & Gudmundsson (2025) — CATCH ConvLSTM Model

- **Title:** "Convolutional-LSTM approach for temporal catch hotspots (CATCH): an AI-driven model for spatiotemporal forecasting of fisheries catch probability densities"
- **Journal:** Biology Methods and Protocols (Oxford Academic)
- **Model:** ConvLSTM (Convolutional Long Short-Term Memory) neural network
  - 2-3 ConvLSTM layers with 3-5 filters
  - 9x9 kernel size, ReLU activation
  - Weighted binary cross-entropy loss (0.3 weight on CPUE, 0.175 each on environmental variables)
- **Features:** 5 input variables — CPUE (kg/min), bottom temperature, depth, dissolved oxygen, salinity
- **Performance:**
  - Atlantic cod: RMSE = 4.71e-3, MAE = 1.16e-3, Structural Similarity Index = 0.955
  - Other species mean: RMSE = 6.13e-3, SSI = 0.949
  - Syrjala's test: P > 0.05 (predicted and observed distributions statistically indistinguishable)
- **Dataset:** 274,521 spatiotemporal observations, 5 species in Icelandic waters, 2008-2022+
  - Spatial binning: 0.5 deg longitude x 0.25 deg latitude (~24km x 28km)
  - Temporal binning: monthly
- **Key Findings:**
  - Optimal lag window: 18 months (balancing short-term dynamics with long-term patterns)
  - Model successfully generalized across 5 species despite behavioral differences
  - Recursive forecasting degraded after 4+ months due to compounding errors
  - Predictions generated continuous probability density maps from sparse observed data
- **CASTLINE Relevance:** The 18-month lag window is directly relevant — our lag feature engineering should consider multi-month windows. The spatial probability density approach could inform a "where to fish" product. The degradation with recursive forecasting warns against long-horizon forecasts.

#### Xie et al. (2024) — U-Net for Fishing Ground Prediction

- **Title:** "Deep learning-based fishing ground prediction with multiple environmental factors"
- **Journal:** Marine Life Science & Technology
- **Model:** Modified U-Net (fully convolutional) with SpatialDropout2D (0.75), skip connections, sigmoid output
- **Features:** SST (0.05 deg resolution), SSH (0.25 deg), SSS (0.25 deg), Chlorophyll-a (4km resolution)
- **Performance:**
  - Best month (August): Accuracy = 93.59%, F1 = 0.9407
  - Worst month (November): Accuracy = 81.48%, F1 = 0.7375
  - Overall optimal: Accuracy = 88.74%, F1 = 0.8732
- **Dataset:** Neon flying squid in Northwest Pacific, July-November 2002-2019 (training), 2020 (test)
- **Key Findings:**
  - Optimal temporal scale: 30 days (longer scales = better performance)
  - Best environmental factor combination: SST + Chlorophyll-a
  - Multi-factor models reduced predicted area by 11.82% vs single-factor (more concentrated predictions)
  - Introduced "Application Effect Index of Fishing Ground" (AEIFG) metric
- **CASTLINE Relevance:** SST + chlorophyll-a as the optimal minimal combination is significant for our satellite data collection. The 30-day optimal temporal scale supports our use of multi-week lag features. The finding that more factors is not always better argues for careful feature selection.

#### Hu et al. (2025) — ANN Comparison for Aquatic Production

- **Title:** "Intelligent forecasting model for aquatic production based on artificial neural network"
- **Journal:** Frontiers in Marine Science
- **Models Compared:** BP neural network, GA-BP, LSTM, RBF
- **Performance:**
  - RBF: R² = 0.96 (best)
  - LSTM: R² = 0.94
  - GA-BP: R² = 0.93
  - BP: R² = 0.73
- **Features:** GDP per capita (most important via Grey Relational Analysis), sunshine duration, temperature, economic indicators
- **Dataset:** Guangdong Province aquaculture data (regional production, not individual waterbody)
- **CASTLINE Relevance:** While the R² values are high, this study predicts aggregate regional production (driven by economic factors), not individual waterbody catch rates. The dominance of GDP as a feature suggests this is really an economics model, not an ecological one. Not directly applicable but illustrates the R² inflation that can occur with aggregate targets.

#### Yoon et al. (2020) — DNN for Satellite-Based Catch Prediction

- **Title:** "An Artificial Intelligence Method for the Prediction of Near- and Off-Shore Fish Catch Using Satellite and Numerical Model Data"
- **Journal:** Korean Journal of Remote Sensing
- **Models:** SVM (Gaussian RBF kernel), Random Forest (500 trees), DNN (200-200-200 hidden layers, AdaDelta optimizer, ReLU activation)
- **Features:** SST (OSTIA satellite, 6km), salinity, atmospheric pressure, relative humidity, rainfall, sea surface wind velocity, significant wave height
- **Performance (10-fold cross-validation, correlation coefficient):**
  - Mackerel (DNN): 0.745
  - Anchovies (DNN): 0.864
  - Squid (DNN): 0.842
- **Dataset:** 4,113 spatiotemporal matchups, 2014-2016, South Korean waters, 15km grid
- **Key Findings:**
  - DNN substantially outperformed SVM and RF
  - Daily predictions at 15km resolution (improved over previous 50km monthly baselines)
  - Larger, higher-quality datasets expected to further improve accuracy
- **CASTLINE Relevance:** Correlation of 0.745-0.864 corresponds roughly to R² of 0.55-0.75 — in the range of our current model. The use of 7 environmental variables from satellite and weather models is similar to our approach. DNN outperformance of tree methods here contrasts with other studies where XGBoost wins.

### 1.3 Time Series and ARIMA Models

#### Tanaka et al. (2025) — Lag Features for Fish Catch Prediction

- **Title:** "Fish Catch Prediction by Combining Fishing, Weather and Tidal Data"
- **Conference:** SciTePress (ICAART 2025)
- **Model:** XGBoost with engineered lag and moving average features
- **Features:** Catch history (1-7 day lags), weather variables (temperature, wind, pressure, humidity), tidal data, 3-day moving averages
- **Performance:**
  - Proposed method: RMSE = 4.36, MAE = 3.02, R² = 0.20
  - Baseline: RMSE = 5.47, MAE = 4.16, R² = -0.27
- **Key Findings:**
  - Lag features (1-7 days) captured temporal dependencies
  - 3-day moving averages captured short-term trends
  - Combined features improved over baseline but R² remained low (0.20)
  - Individual day-level catch prediction is inherently very noisy
- **CASTLINE Relevance:** The low R² (0.20) despite good methodology is the most honest result in this review. Daily catch prediction for individual anglers is fundamentally hard — the signal-to-noise ratio is low. This validates our approach of predicting tournament-averaged CPUE (which aggregates across many anglers and multiple days) rather than individual trip outcomes.

---

## 2. Feature Engineering for Fisheries

### 2.1 Variable Importance Rankings Across Studies

The following table synthesizes feature importance findings across multiple studies:

| Variable Category | Specific Features | Importance Level | Studies |
|---|---|---|---|
| **Spatiotemporal** | Latitude, longitude, month, season | Very High | LightGBM-SHAP (2025), Yoon (2020), most studies |
| **Water Temperature** | SST, water temp, bottom temp | High | Rawat (2025), Chen (2022), CATCH (2025), Yoon (2020) |
| **Chlorophyll-a** | Satellite chl-a, phytoplankton | High | Xie (2024), LightGBM-SHAP (2025), Chen (2022) |
| **Water Chemistry** | Salinity, dissolved oxygen, pH | High | Rawat (2025), CATCH (2025), Dhapodkar (2025) |
| **Hydrological** | Discharge, water level, flow | Medium-High | Rawat (2025), Chen (2022) |
| **Turbidity/Clarity** | SSC, Secchi depth, suspended matter | Medium | Rawat (2025), Chen (2022) |
| **Atmospheric** | Pressure, humidity, wind speed | Medium | Yoon (2020), Tanaka (2025), Kuparinen (2010) |
| **Depth/Bathymetry** | Depth, SSH, morphometry | Medium | CATCH (2025), LightGBM-SHAP (2025) |
| **Lunar/Solunar** | Moon phase, solunar period | Low-Mixed | Kuparinen (2010), Allen (2010), Cooke (disputed) |
| **Barometric Pressure** | Direct pressure effects | Very Low | Multiple studies find no isolated effect |

### 2.2 Water Temperature

Water temperature is consistently identified as one of the most important environmental variables across studies. Key findings:

- Largemouth bass exhibit strong temperature-dependent behavior, with survival sharply declining above 25 deg C (Keretz et al. 2018)
- Pike catch rates increased at low temperatures (Kuparinen et al. 2010)
- Temperature explained significant variation in European perch population dynamics over 62 years of angler diary records
- For freshwater species, water temperature has stronger predictive power than air temperature, highlighting the value of USGS water temperature data over weather station data

### 2.3 Dissolved Oxygen

- Largemouth bass avoid water with dissolved oxygen < 27% air saturation (Burleson & Smith 2001)
- Smaller fish tolerate lower oxygen levels than larger fish
- DO interacts strongly with temperature (warm water holds less oxygen)
- Both the CATCH model and Dhapodkar's ensemble identify DO as a key predictor

### 2.4 Lunar Phase and Barometric Pressure

The scientific evidence for these popular angling factors is weak:

- **Lunar phase:** Allen (2010) studied trophy bass angler Porter Hall's detailed records — 21% of fish landed at new moon, 28% at full moon, 49% at first/last quarters. The effect exists but is modest. Dr. Steven Cooke (Carleton University) argues the negligible tides in freshwater make lunar effects minimal.
- **Barometric pressure:** "Every scientific report in which barometric pressure was studied reached a similar conclusion: no direct relationship is evident" (In-Fisherman). Pressure effects cannot be isolated from simultaneous weather phenomena (fronts, storms).
- **Recommendation for CASTLINE:** Include lunar phase as a feature (low cost, may capture weak signal) but do not expect it to be a strong predictor. Do not rely on barometric pressure as a primary feature.

### 2.5 Temporal Lag Features

Multiple studies validate the use of lag features:

- **Tanaka (2025):** 1-7 day lags + 3-day moving averages improved RMSE by 20%
- **CATCH (2025):** 18-month lag window was optimal for monthly CPUE prediction
- **Xie (2024):** 30-day temporal scale outperformed 3, 6, 10, and 15-day scales
- **General principle:** Lag features capture autoregressive patterns (fish were biting yesterday, likely biting today) and cumulative environmental effects (prolonged warm spell vs single hot day)
- **CASTLINE implication:** Our current 7-day and 14-day lag features align with best practices. Consider adding 30-day aggregate features for seasonal-scale patterns.

### 2.6 Cyclical Encoding

Fourier features (sine/cosine encoding) for temporal variables (month, day of year) are standard practice in fisheries time-series modeling. They ensure models interpret time cyclically (December is close to January) rather than linearly, and can represent multiple overlapping seasonal patterns (daily, weekly, monthly, annual).

### 2.7 Optimal Variable Selection

Miao et al. (2024) in the Canadian Journal of Fisheries and Aquatic Sciences proposed Recursive Feature Elimination with Cross-Validation (RFECV) for determining optimal variable combinations. Using four tree-based models (RF, XGBoost, LightGBM, CatBoost), they found that sea temperature, dissolved oxygen, chlorophyll-a, salinity, and SSH were universally identified as significant across all models for yellowfin tuna distribution.

---

## 3. Recreational Fishing and Angler Behavior Prediction

### 3.1 Schmid et al. (2024) — Predicting Citizen-Reported Angler Behavior

- **Title:** "Can machine learning predict citizen-reported angler behavior?"
- **Source:** arXiv (physics.soc-ph)
- **Models:** 9 algorithms — linear regression, SVR, KNN, RF, gradient boosted trees, neural networks, Bayesian networks, naive Bayes, tree-augmented naive Bayes
- **Features:**
  - Environmental (9): temperature, precipitation, humidity, solar radiation, pressure, wind speed, degree days, water body type, surface area
  - Socioeconomic (8): population, income, distance to urban areas, COVID metrics
  - Fisheries management (6): bag limits, size restrictions, stocking events
- **Performance:**
  - Monthly at single waterbody: 88% accuracy (catch rate), 87% (duration)
  - Daily regional: 86% accuracy
  - Daily provincial: 75% accuracy
  - Daily multi-province: 67% accuracy
- **Dataset:** ~56,000 fishing trips, 4,147 water bodies, 3 Canadian provinces (Ontario, BC, Alberta), 2018-2022
- **Key Findings:**
  - Performance inversely correlated with spatial extent and temporal granularity
  - Adding Julian day/week/month variables produced negligible improvements (max 1.6%)
  - Feature-based models substantially outperformed temporal-mean baselines
  - Crowdsourced app data contains novel information not captured by auxiliary variables
- **CASTLINE Relevance:** The 88% accuracy at single-waterbody monthly scale is encouraging but not directly comparable to our regression task. The finding that broader spatial scope degrades performance is critical — our cross-lake generalization challenge is fundamentally harder than single-lake prediction. The negligible impact of temporal features confirms that environmental variables carry the seasonal signal.

### 3.2 Kuparinen, Klefoth & Arlinghaus (2010) — Abiotic Correlates of Pike Catch Rates

- **Title:** "Abiotic and fishing-related correlates of angling rates in pike (Esox lucius)"
- **Journal:** Fisheries Research
- **Method:** Statistical regression (not ML)
- **Study Site:** Kleiner Dollnsee, Germany (25 ha mesotrophic lake)
- **Key Findings:**
  - Catch rates significantly affected by: past 2 days' fishing effort, time of day, water temperature, wind speed, moon phase
  - Catch rates increased at: dusk, high wind speeds, full/new moon
  - Catch rates decreased with: increasing water temperature
  - Cumulative fishing pressure reduced future catch rates even in catch-and-release
- **CASTLINE Relevance:** The finding that recent fishing pressure affects catch rates has implications for tournament prediction — heavily fished tournament lakes may show depressed CPUE. This is a variable we don't currently capture. The temperature-inverse relationship for pike contrasts with bass (which prefers moderate warmth), highlighting species-specific modeling needs.

### 3.3 Fishbrain / Modulai — Commercial Fishing Forecast Application

- **Developer:** Fishbrain (world's largest fishing app)
- **Model Partner:** Modulai (ML consultancy)
- **Dataset:** 2.5 million catches (from 10M+ registered catches), global coverage
- **Features:**
  - Weather: air temperature, air pressure, wind speed, cloud cover, precipitation
  - Astronomical: moon phase, solar irradiation, azimuth angle
  - Climate: historical weather patterns, vegetation, geology
  - Catch metadata: date, time, location, species
- **Product:** BiteTime fishing forecast, spot prediction algorithm
- **Performance:** Not publicly disclosed
- **CASTLINE Relevance:** Fishbrain's approach validates the commercial viability of ML-based fishing predictions. Their use of global climate models and vegetation data is interesting — we have not explored vegetation/land cover features. Their scale (2.5M catches globally) dwarfs our dataset but they predict for all species worldwide, which is a much harder problem.

### 3.4 Proportional Angling Success (PAS)

Pollock et al. (2007) in Fisheries proposed an alternative to mean CPUE: Proportional Angling Success (PAS), defined as the proportion of anglers with catch rates >= x fish per hour. This metric is more robust to outliers and may better represent the recreational angler experience.

**CASTLINE Relevance:** Consider offering PAS-like metrics alongside raw CPUE predictions — "70% chance of catching 1+ bass per hour" may be more actionable than "predicted CPUE = 0.45 fish/hour."

---

## 4. Spatial and Temporal Modeling

### 4.1 Spatiotemporal Neural Networks

#### Spatial-Temporal Neural Networks for CPUE Standardization (2024)

- **Title:** "Spatial-temporal neural networks for catch rate standardization and fish distribution modeling"
- **Journal:** Fisheries Research
- **Key Concept:** Neural networks that jointly model spatial and temporal patterns in CPUE data, accounting for the fact that fish distributions shift seasonally and interannually
- **Finding:** Spatial-temporal models outperformed traditional GLM/GAM approaches for CPUE standardization, particularly when fish distributions were non-stationary

#### TransFish — Transformer for Fishing Effort (2025)

- **Title:** "TransFish: day-level forecasting of fishing effort distribution via transformer on multi-source data"
- **Journal:** Reviews in Fish Biology and Fisheries
- **Model:** Transformer architecture
- **Key Innovation:** Day-level spatial forecasting of fishing effort using multi-source data including hydrological factors and chlorophyll concentration
- **CASTLINE Relevance:** Transformers are an emerging architecture for fisheries spatiotemporal prediction. Their self-attention mechanism may capture long-range spatial dependencies between lakes better than our current GNN approach.

### 4.2 Handling Location Identity

A critical challenge across all studies is how to represent "location":

1. **Latitude/longitude as features:** Used by LightGBM-SHAP (2025), Yoon (2020). Simple but learns location-specific patterns that don't transfer.
2. **Spatial blocking/stratification:** Used in CPUE standardization (Hoyle et al. 2024). Divides area into strata with separate intercepts.
3. **Location embeddings:** Not widely used in fisheries literature but standard in recommendation systems. Would allow learning lake-specific biases while sharing environmental response functions.
4. **Location-as-graph-node:** GNN approaches (emerging). Lakes as nodes, geographic proximity as edges. Our approach.
5. **Location mean encoding:** Common but causes severe leakage (our v10 experience: 0.30 R² leakage).

### 4.3 Spatial Cross-Validation

Multiple sources emphasize that random cross-validation overestimates model generalizability when data has spatial autocorrelation:

- Random splitting allows training and testing data from the same locations, learning location-specific patterns that fail at new locations
- **Blocked cross-validation** (spatial blocks left out one at a time) provides more honest estimates of extrapolation performance
- Leave-one-location-out (LOO) is the gold standard for evaluating new-location prediction
- Our LOO cross-validation approach is consistent with best practices in the literature

### 4.4 Chen et al. (2022) — Remote Sensing for Lake Fish Resources

- **Title:** "Remote sensing modeling of environmental influences on lake fish resources by machine learning"
- **Journal:** Frontiers in Environmental Science
- **Study Site:** Poyang Lake, China (162,200 km² watershed, largest freshwater lake in China)
- **Data:** 1960-2017 (historical), 2000-2017 (remote sensing)
- **Models:** XGBoost (best performer), DNN, RF
- **Performance:**
  - XGBoost: NSE = 0.97 (Nash-Sutcliffe Efficiency)
  - Adding water ecology variables: R increased 17%, RMSE decreased 3,200 tons
  - Adding water quality variables: R increased 11%, RMSE decreased 2,600 tons
  - Combined multi-dimensional: R increased 21%, RMSE decreased 4,300 tons
- **Most Important Variables:** Hydrometeorological factors (temperature, precipitation, water level) > water ecological variables > water quality parameters (chlorophyll-a, Secchi depth, SPM)
- **Key Finding:** Fish catches were more susceptible to water ecological variables than water quality variables
- **CASTLINE Relevance:** Demonstrates that multi-dimensional environmental data (hydro + ecology + quality) substantially outperforms any single category. Our use of USGS (hydro) + weather + satellite (quality) covers most of these dimensions. Consider adding ecological variables (vegetation indices, trophic status indicators).

---

## 5. Data Sources and Integration

### 5.1 USGS Water Data

USGS operates 10,000+ streamgages providing real-time streamflow, gage height, water temperature, and hundreds of other parameters. The Conte fish ecology team has tracked fish populations in relation to streamflow since 1997, establishing direct links between flow variation and fish population dynamics (Atlantic salmon, brook trout, brown trout).

**For CASTLINE:** USGS instantaneous value (IV) data at 15-minute intervals provides the highest-resolution hydrological data available. Our current use of daily summary statistics (mean, min, max, range) from USGS IV data is appropriate. Consider also extracting rate-of-change features (rising vs falling water levels/flow).

### 5.2 NASA/NOAA Satellite Data

Key satellite data products used across studies:

| Data Product | Source | Resolution | Variables | Studies Using |
|---|---|---|---|---|
| MODIS SST/Chl-a | NASA Aqua/Terra | 1-4 km, daily | Water temp, chlorophyll | Xie (2024), Chen (2022), multiple |
| OSTIA SST | UK Met Office/NOAA | 6 km, daily | Sea/lake surface temp | Yoon (2020) |
| Sentinel-2 MSI | ESA | 10-60 m, 5-day | Chlorophyll, turbidity, clarity | Multiple remote sensing studies |
| Landsat 8/9 OLI | NASA/USGS | 30 m, 16-day | Chlorophyll, water quality | Multiple remote sensing studies |
| NASA POWER | NASA | 0.5 deg, daily | Solar, temp, humidity, wind | Our current pipeline |

**For CASTLINE:** We currently use NASA POWER for weather data. Adding MODIS-derived chlorophyll-a and surface water temperature could improve predictions, as these are consistently identified as high-importance features. The 16-day revisit cycle of Landsat limits temporal resolution but Sentinel-2's 5-day cycle is sufficient for weekly predictions.

### 5.3 Crowdsourced and Creel Survey Data

- **CreelCat (USGS):** Catalog of US inland creel and angler survey data — a potential source of standardized CPUE observations across many lakes
- **Fishbrain:** 10M+ catches with date, time, location, species
- **Angler's Atlas / MyCatch (Canada):** Used by Schmid et al. (2024) — 56,000 trips across 4,147 water bodies
- **iAngler:** Data comparable to MRIP creel surveys for marine species
- **Tournament data (Bassmaster, MLF):** Our primary data source — standardized effort, known dates/locations, reported weights

**For CASTLINE:** Tournament data remains our strongest data source due to standardized effort. Crowdsourced app data could supplement but has selection biases (anglers report good trips more than bad ones). Creel survey data from state agencies could provide additional ground truth.

### 5.4 Lake Morphometry and Trophic Status

Lake physical characteristics affect fish communities through multiple pathways:

- Lake area and mean depth are primary predictors of fish species richness
- Depth determines thermal stratification (polymictic vs monomictic vs dimictic)
- Shoreline development ratio affects littoral habitat availability
- Trophic status (oligotrophic/mesotrophic/eutrophic) predicts overall productivity

The Carlson Trophic State Index (TSI) uses Secchi depth, chlorophyll-a, and total phosphorus to classify lakes. Bayesian models can predict trophic state from Secchi depth, elevation, N, and P concentrations (Rethinking the lake trophic state index, PMC 2019).

**For CASTLINE:** Our morphometry features (lake area, depth) are appropriate. Consider adding estimated TSI or a productivity proxy derived from satellite chlorophyll-a.

---

## 6. Transfer Learning and Cross-Location Generalization

### 6.1 The Fundamental Challenge

The "new location" problem — predicting catch at a lake with no historical data — is arguably the hardest challenge in fisheries ML. Key findings from the literature:

1. **Spatial autocorrelation causes overestimation:** Random cross-validation without spatial blocking "can greatly overestimate model generalizability" (multiple sources). Models learn location-specific patterns (e.g., Lake X always produces well) that fail at new sites.

2. **Blocked cross-validation reveals true performance:** When spatial blocks are left out for testing, performance typically drops substantially. Our LOO R² of 0.42-0.575 vs random-split R² of 0.97+ illustrates this gap.

3. **Environmental features must transfer:** For a model to generalize, it must learn response functions (e.g., "bass CPUE increases from 50-65 deg F water temp") rather than location identities. This requires that the environmental features adequately explain the between-location variance.

### 6.2 Strategies for Generalization

From the literature, successful approaches include:

- **Feature selection via RFECV:** Eliminate location-proxy features that don't transfer (Miao et al. 2024)
- **Spatial block cross-validation:** Honest evaluation of extrapolation performance
- **Domain adaptation:** Fine-tune on small amounts of target-location data (not widely used in fisheries yet)
- **Lake characteristic features:** Encode lake properties (area, depth, trophic status) to give the model context about the type of waterbody
- **Regional/climate grouping:** Group similar lakes and train region-specific models
- **Graph neural networks:** Learn spatial relationships between lakes (our approach, limited literature in fisheries specifically)

### 6.3 Schmid et al. (2024) Performance Degradation

The most relevant finding: at the single-waterbody monthly scale, 88% accuracy was achieved. When predicting across multiple provinces (akin to new locations), accuracy dropped to 67%. This ~20% degradation is consistent with our experience of R² dropping from 0.55 (random split) to 0.42 (LOO).

### 6.4 Implications for CASTLINE R² = 0.9 Target

Based on this literature review, achieving R² = 0.9 on truly unseen locations appears extremely ambitious. The highest R² values reported in the literature fall into two categories:

1. **R² > 0.9 achieved with:** location-specific metadata, fisheries operational data (gear, area), aggregate regional targets, or small datasets with potential overfit (Dhapodkar 2025, Hu 2025)
2. **R² = 0.5-0.85 achieved with:** pure environmental features, honest spatial validation, individual species at moderate spatial resolution (Rawat 2025, LightGBM-SHAP 2025, CATCH 2025)

**Realistic targets for CASTLINE:**
- R² = 0.6-0.7 with current approach (environmental features, LOO validation) may be achievable with better features and more data
- R² = 0.8+ likely requires location-specific calibration data (a few historical tournaments per lake)
- R² = 0.9 may require fundamentally different framing (ranking rather than regression, or per-lake models with transfer learning)

---

## 7. Ranking vs Regression Approaches

### 7.1 Habitat Suitability Index (HSI) Models

HSI models answer "is this a good habitat?" rather than "how much will be caught?" Key findings:

- HSI models based on CPUE tend to incorrectly estimate suitable habitat areas compared to models based on fishing effort
- GAM approaches outperformed multiplicative HSI models (Frontiers in Marine Science)
- Boosted Regression Tree (BRT) models for HSI provide variable importance via relative contribution percentages
- Weighted HSI models based on BRT yield more reliable predictions than unweighted models

### 7.2 Classification vs Regression

Several studies frame catch prediction as classification ("good fishing" vs "poor fishing") rather than regression (predicting exact CPUE):

- **Xie (2024):** Binary classification (fishing ground vs not), accuracy 88%
- **Watson et al. (2023):** Binary classification (federal vs state waters), accuracy 97%
- **Schmid (2024):** Classification of catch rate categories, accuracy 86-88%

Classification generally achieves higher apparent performance because it reduces the prediction target to a simpler problem. For CASTLINE, a "Top 5 lakes to fish this weekend" ranking product may be more achievable (and more commercially useful) than precise CPUE regression.

### 7.3 Probability Density Forecasting

The CATCH model (Agmata & Gudmundsson 2025) forecasts probability density maps rather than point estimates. This approach:
- Naturally handles uncertainty
- Answers "where is the best fishing?" spatially
- Avoids the precision trap of CPUE regression
- Could be adapted for CASTLINE as a "fishing conditions map" product

---

## 8. Synthesis and Implications for CASTLINE

### 8.1 What the Literature Tells Us

1. **R² = 0.5-0.7 is typical for honest environmental-only prediction** at the individual waterbody level. Our current R² = 0.547 is within this range and not far from the state of the art.

2. **The most impactful features are:** water temperature (directly measured, not air temp), chlorophyll-a/productivity, hydrological variables (flow, water level), dissolved oxygen, and spatiotemporal context (location, season). Barometric pressure and lunar phase are weak predictors.

3. **Lag features are essential.** Studies consistently find that 7-30 day lag features improve predictions. The optimal lag varies by application but multi-week windows are common.

4. **XGBoost/LightGBM consistently match or beat deep learning** for tabular environmental data. Deep learning excels only with spatial image data (satellite imagery) or very long time series.

5. **Cross-location generalization is the hardest problem.** Random cross-validation massively overestimates performance. LOO validation is the honest test.

6. **Multi-source data integration improves results.** Studies combining hydro + weather + satellite + ecological variables consistently outperform single-source approaches.

7. **Tournament/aggregated data is more predictable than individual trips.** Daily individual catch prediction has R² ~ 0.20 (Tanaka 2025), while aggregated metrics can reach R² = 0.89 (Rawat 2025).

### 8.2 Recommended Actions for CASTLINE

Based on this review:

| Priority | Action | Expected Impact | Evidence |
|---|---|---|---|
| High | Add satellite chlorophyll-a / water clarity features | +0.05-0.10 R² | Xie (2024), Chen (2022), multiple studies |
| High | Add directly measured water temperature (not air temp proxy) | +0.03-0.08 R² | Rawat (2025), Kuparinen (2010), bass physiology literature |
| High | Extend lag features to 30 days | +0.02-0.05 R² | CATCH (2025), Xie (2024) |
| Medium | Add lake trophic status / productivity proxy | +0.02-0.05 R² | Chen (2022), morphometry literature |
| Medium | Add rate-of-change features (rising/falling water, warming/cooling) | +0.02-0.04 R² | Rawat (2025), hydrological studies |
| Medium | Implement SHAP-based feature selection | +0.01-0.03 R² | LightGBM-SHAP (2025), Miao (2024) |
| Low | Add dissolved oxygen estimates | +0.01-0.03 R² | CATCH (2025), bass physiology |
| Low | Include lunar phase features | +0.00-0.01 R² | Mixed evidence, low cost |
| Strategic | Consider ranking/classification product instead of pure CPUE regression | N/A (product pivot) | Xie (2024), HSI literature |
| Strategic | Evaluate per-lake fine-tuning with transfer learning | Potentially +0.10-0.15 R² | Underexplored in literature |

### 8.3 Feature Engineering Priorities (Ordered by Evidence Strength)

1. **Water temperature** (direct measurement via USGS or satellite) — universally identified as top predictor
2. **Chlorophyll-a / water productivity** — second most consistently important environmental variable
3. **Hydrological variables** (streamflow, water level, discharge rate-of-change) — high importance in freshwater studies
4. **Multi-week lag aggregates** (7, 14, 30-day windows for temperature trends, cumulative precipitation)
5. **Dissolved oxygen** (estimated or measured) — strong physiological basis for bass
6. **Seasonal Fourier features** (sine/cosine encoding of day-of-year) — standard best practice
7. **Lake morphometry** (area, max depth, mean depth, shoreline development) — explains between-lake variance
8. **Wind speed and direction** — moderate importance, primarily for surface-feeding species and wave action
9. **Lunar phase** — weak but non-zero signal, essentially free to include
10. **Barometric pressure** — minimal isolated effect, likely a proxy for frontal passage

### 8.4 Model Architecture Recommendations

Based on the literature:

- **Primary model:** XGBoost or LightGBM for tabular features (consistent top performer)
- **Supplement with:** Location embeddings or GNN for spatial relationships (emerging but promising)
- **Consider:** Stacking ensemble (RF + XGBoost + MLP + Ridge meta-learner) for marginal gains
- **Avoid:** ConvLSTM/U-Net unless working with gridded satellite imagery directly
- **Validation:** LOO spatial cross-validation (gold standard for our use case)

---

## 9. Full Reference List

### Core Papers (Directly Applicable)

1. Rawat, V. et al. (2025). "Prediction of fish (Coilia nasus) catch using spatiotemporal environmental variables and random forest model in a highly turbid macrotidal estuary." *Ecological Informatics*. [ScienceDirect](https://www.sciencedirect.com/science/article/pii/S1574954125000573)

2. Agmata, A. & Gudmundsson, S. (2025). "Convolutional-LSTM approach for temporal catch hotspots (CATCH): an AI-driven model for spatiotemporal forecasting of fisheries catch probability densities." *Biology Methods and Protocols*. [PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC12203189/)

3. Tanaka, T. et al. (2025). "Fish Catch Prediction by Combining Fishing, Weather and Tidal Data." *SciTePress/ICAART 2025*. [PDF](https://www.scitepress.org/Papers/2025/132632/132632.pdf)

4. Schmid, J.S. et al. (2024). "Can machine learning predict citizen-reported angler behavior?" *arXiv:2402.06678*. [arXiv](https://arxiv.org/abs/2402.06678)

5. Xie, M. et al. (2024). "Deep learning-based fishing ground prediction with multiple environmental factors." *Marine Life Science & Technology*. [PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC11602920/)

6. Chen, T. et al. (2022). "Remote sensing modeling of environmental influences on lake fish resources by machine learning." *Frontiers in Environmental Science*. [Frontiers](https://www.frontiersin.org/journals/environmental-science/articles/10.3389/fenvs.2022.944319/full)

7. Yoon, Y.-J. et al. (2020). "An Artificial Intelligence Method for the Prediction of Near- and Off-Shore Fish Catch Using Satellite and Numerical Model Data." *Korean Journal of Remote Sensing*. [Korea Science](https://koreascience.or.kr/article/JAKO202012758284748.page)

8. Kuparinen, A., Klefoth, T. & Arlinghaus, R. (2010). "Abiotic and fishing-related correlates of angling rates in pike (Esox lucius)." *Fisheries Research*. [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0165783610000755)

### CPUE Standardization and Methods

9. Hoyle, S.D. et al. (2024). "Catch per unit effort modelling for stock assessment: A summary of good practices." *Fisheries Research*. [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0165783623002539)

10. LightGBM-SHAP Study (2025). "Interpretable fish abundance index prediction in tuna longline fisheries: A LightGBM-SHAP case study in the tropical Atlantic Ocean." *Fisheries Research*. [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S016578362500205X)

11. Comparative ML Study (2024). "A Comparative Machine Learning Study Identifies LightGBM as the Optimal Model for Unveiling Environmental Drivers of Yellowfin Tuna Distribution Using SHAP Analysis." *Biology (MDPI)*. [MDPI](https://www.mdpi.com/2079-7737/14/11/1567)

12. Miao, Z. et al. (2024). "Identifying optimal variables for machine-learning-based fish distribution modeling." *Canadian Journal of Fisheries and Aquatic Sciences*. [CJFAS](https://cdnsciencepub.com/doi/10.1139/cjfas-2023-0197)

### Deep Learning and Spatial Models

13. Hu, J. et al. (2025). "Intelligent forecasting model for aquatic production based on artificial neural network." *Frontiers in Marine Science*. [Frontiers](https://www.frontiersin.org/journals/marine-science/articles/10.3389/fmars.2025.1556294/full)

14. Dhapodkar, A. et al. (2025). "Ensemble Machine Learning for Fish Abundance Prediction: A Multi-Model Stacking Approach." *IJACECT*. [IJACECT](https://journals.mriindia.com/index.php/ijacect/article/view/1605)

15. TransFish (2025). "TransFish: day-level forecasting of fishing effort distribution via transformer on multi-source data." *Reviews in Fish Biology and Fisheries*. [Springer](https://link.springer.com/article/10.1007/s11160-025-09951-w)

### Recreational Fishing and Angler Studies

16. Watson, J.T. et al. (2023). "Fishery catch records support machine learning-based prediction of illegal fishing off US West Coast." *PeerJ*. [PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC10590572/)

17. Pollock, K.H. et al. (2007). "Proportional Angling Success: An Alternative Approach to Representing Angling Success." *Fisheries*. [Taylor & Francis](https://tandfonline.com/doi/abs/10.1577/1548-8446(2007)32[129:PASAAT]2.0.CO;2)

18. Website visits predict angler presence (2024). *arXiv:2409.17425*. [arXiv](https://arxiv.org/abs/2409.17425)

### Environmental Variables and Bass Biology

19. Keretz, K.R. et al. (2018). "Effect of Water Temperature, Angling Time, and Dissolved Oxygen on Survival of Largemouth Bass." *North American Journal of Fisheries Management*. [Oxford Academic](https://academic.oup.com/najfm/article/38/3/606/7817609)

20. Burleson, M.L. & Smith, R.L. (2001). "The influence of fish size on the avoidance of hypoxia and oxygen selection by largemouth bass." *Journal of Fish Biology*. [Wiley](https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1095-8649.2001.tb00196.x)

### Reviews and Frameworks

21. Machine Learning Applications for Fisheries (2024). "Machine Learning Applications for Fisheries—At Scales from Genomics to Ecosystems." *Reviews in Fisheries Science & Aquaculture*. [Taylor & Francis](https://www.tandfonline.com/doi/full/10.1080/23308249.2024.2423189)

22. NOAA AFSC (2014). "A Comparison of Statistical Methods to Standardize Catch-Per-Unit-Effort." *NOAA Technical Memorandum NMFS-AFSC-269*. [PDF](https://apps-afsc.fisheries.noaa.gov/Publications/AFSC-TM/NOAA-TM-AFSC-269.pdf)

### Data Sources

23. USGS CreelCat. "U.S. Inland Creel and Angler Survey Catalog." [USGS](https://www.usgs.gov/tools/us-inland-creel-and-angler-survey-catalog-creelcat)

24. NOAA Fisheries. "Satellite Data for Fisheries." [NOAA](https://www.fisheries.noaa.gov/national/science-data/satellite-data)

25. Modulai / Fishbrain. "Forecasting the success of fishing trips, for millions of Fishbrain users." [Modulai](https://modulai.io/case/global-forecasting-model-for-worlds-largest-fishing-app/)

---

*End of Literature Review*
