# Exhaustive Survey: ALL Techniques for Satellite-Derived & Remote-Sensing Bathymetry

**Date:** 2026-03-22
**Purpose:** Comprehensive catalog of every known approach for estimating lake/reservoir bathymetry from remote sensing and related data. Identifies what we're doing, what we're missing, and what the theoretical best ensemble looks like.

---

## Table of Contents

1. [A. Spectral/Optical Methods](#a-spectraloptical-methods)
2. [B. Geometric/Hypsometric Methods](#b-geometrichypsometric-methods)
3. [C. Terrain-Based Prediction](#c-terrain-based-prediction)
4. [D. Morphometric/Statistical Methods](#d-morphometricstatistical-methods)
5. [E. LiDAR-Direct Methods](#e-lidar-direct-methods)
6. [F. SAR-Based Methods](#f-sar-based-methods)
7. [G. Satellite Altimetry Methods](#g-satellite-altimetry-methods)
8. [H. Thermal Methods](#h-thermal-methods)
9. [I. Physics-Based Radiative Transfer](#i-physics-based-radiative-transfer)
10. [J. Physics-Informed Neural Networks](#j-physics-informed-neural-networks)
11. [K. Multi-Source Fusion](#k-multi-source-fusion)
12. [L. Acoustic/Sonar + Crowdsourced](#l-acousticsonar--crowdsourced)
13. [M. Ground-Penetrating Radar](#m-ground-penetrating-radar)
14. [N. Drone/UAV Photogrammetry](#n-droneuav-photogrammetry)
15. [O. Hyperspectral Methods](#o-hyperspectral-methods)
16. [Current SOTA & Theoretical Limits](#current-sota--theoretical-limits)
17. [Recommended Ensemble Architecture](#recommended-ensemble-architecture)
18. [Implementation Priority for OpenCatch](#implementation-priority-for-opencatch)

---

## A. Spectral/Optical Methods

### A1. Stumpf Log-Ratio Model (2003)
- **How it works:** Takes the ratio of log-transformed reflectances in two bands (typically blue/green). The ratio is approximately linear with depth because different wavelengths attenuate at different rates. Eliminates some bottom-type dependency.
- **Formula:** `depth = m1 * (ln(Rw_blue) / ln(Rw_green)) - m0`
- **Best accuracy:** R2 = 0.56-0.93, RMSE = 0.24-0.65 m (varies by site)
- **Data requirements:** Multispectral imagery (Sentinel-2, Landsat), calibration depths
- **Turbid water:** Poor -- assumes clear water with visible bottom
- **Deep water:** Limited to ~2 Secchi depths (~10-20m in clear water)
- **Can we implement:** YES -- already implemented in our pipeline
- **Complements:** Foundation of our spectral approach; improved by ML overlay

### A2. Lyzenga Log-Linear Model (1978, 1985)
- **How it works:** Multiple linear regression on log-transformed band reflectances. Accounts for varying bottom types better than single-ratio.
- **Formula:** `depth = a0 + a1*ln(Rw_b1) + a2*ln(Rw_b2) + ... + an*ln(Rw_bn)`
- **Best accuracy:** R2 = 0.54-0.94, RMSE = 0.23-0.74 m
- **Data requirements:** Same as Stumpf + more bands useful
- **Turbid water:** Poor
- **Deep water:** Same 2-Secchi-depth limit
- **Can we implement:** YES -- trivial to add as feature engineering
- **Complements:** Provides different error characteristics than Stumpf; good ensemble member

### A3. Machine Learning on Spectral Bands (XGBoost, RF, SVM, ANN)
- **How it works:** Train ML models on spectral band values/ratios/indices to predict depth. Non-linear relationships captured automatically.
- **Best accuracy:** RF R2 = 0.88 (MAE=0.12m, RMSE=0.32m); QRF R2 = 0.984; XGBoost competitive
- **Data requirements:** Multispectral imagery + training depths (ICESat-2 or sonar)
- **Turbid water:** Better than empirical -- can learn turbidity-depth interactions if features include turbidity indicators
- **Deep water:** Still limited by optical penetration
- **Can we implement:** YES -- this IS our primary approach (XGBoost stage 1 + 2)
- **Complements:** Core pipeline component

### A4. Deep Learning (CNN, U-Net, BathyNet)
- **How it works:** Convolutional neural networks process spatial patches of multispectral imagery. U-Net architecture captures both local texture and broader spatial context. BathyNet specifically designed for bathymetry.
- **Best accuracy:** MAE ~1m for depths up to 20m; specific architectures (Swin-BathyUNet) show improvements via attention mechanisms
- **Data requirements:** Large training datasets, multispectral imagery at ~10m resolution
- **Turbid water:** Can learn turbidity compensation if training data covers turbid areas
- **Deep water:** Same optical limits
- **Can we implement:** YES -- we have U-Net stage 2 planned
- **Complements:** Captures spatial patterns that point-based ML misses (e.g., depth gradients, shore proximity patterns)

### A5. Multi-Temporal Compositing
- **How it works:** Uses time-series of satellite images to reduce noise from sun glint, atmospheric variation, and transient turbidity. Generalized Linear Model (GLM) across multiple dates.
- **Best accuracy:** MAE = 0.34 m in optically complex shallow waters
- **Data requirements:** Multiple cloud-free Sentinel-2 scenes over same area
- **Turbid water:** BETTER -- temporal averaging can see through intermittent turbidity
- **Deep water:** Same optical limits but reduced noise floor
- **Can we implement:** YES -- we already do temporal compositing
- **Complements:** Essential preprocessing step for all optical methods

### A6. Fast Feature Cascade Learning (Turbid Water Specific)
- **How it works:** Cascade of increasingly complex features specifically designed for turbid coastal/inland waters. Uses spectral, spatial, and contextual features in stages.
- **Best accuracy:** Significantly better than standard approaches in turbid conditions
- **Data requirements:** Sentinel-2 or similar multispectral
- **Turbid water:** YES -- specifically designed for this
- **Deep water:** Still optically limited
- **Can we implement:** YES -- could adapt architecture
- **Complements:** Critical for our turbid midwest/southern lakes

---

## B. Geometric/Hypsometric Methods

### B1. Area-Elevation (A-E) Method (3D-LAKES, 2025)
- **How it works:** Tracks lake shoreline position at different water levels using satellite imagery (Landsat/Sentinel-2) combined with satellite altimetry (ICESat-2/SWOT) for water surface elevation. As water level drops, exposed lakebed reveals bathymetry. Builds area-elevation curve.
- **Best accuracy:** A-E relationship RMSE = 0.60 m (NRMSE = 0.14, R2 = 0.61); 3D bathymetry RMSE = 1.37 m (NRMSE = 0.26)
- **Data requirements:** Multi-temporal satellite imagery + satellite altimetry (ICESat-2, SWOT). Works best for lakes with significant water level fluctuation.
- **Coverage:** 510,530 global lakes (98.9% of global surface water storage)
- **Turbid water:** YES -- does not depend on optical penetration at all
- **Deep water:** Limited to exposed elevation range during observation period
- **Can we implement:** YES -- we have extract_3dlakes_depths.py already
- **Complements:** CRITICAL complement to spectral -- works where spectral fails (turbid, deep)

### B2. Step-Wise Water Recession Method (WRM)
- **How it works:** Simulates lake draining by progressively lowering water level on a DEM. Uses shoreline topographic gradients to extrapolate below-water terrain. Assumes geological continuity between terrestrial and subaqueous landforms.
- **Key assumptions:** (1) Bathymetry shaped by same geological processes as surrounding terrain; (2) Sediment thickness proportional to water depth
- **Best accuracy:** Depends on DEM quality and terrain uniformity
- **Data requirements:** High-resolution DEM (SRTM 30m, or better)
- **Turbid water:** YES -- purely geometric, no optical dependency
- **Deep water:** Can extrapolate to any depth using terrain continuation
- **Can we implement:** YES -- needs DEM data + GIS processing
- **Complements:** Good for lakes without water level variation (where A-E fails)

### B3. SRTM Inundation Mapping
- **How it works:** Exploits the fact that SRTM was collected at a specific water level. By comparing current water extent to SRTM-era extent, newly inundated or exposed bathymetry can be directly read from the DEM. Covers 372,986 km2 (15.95% of investigated lake area).
- **Best accuracy:** Directly reads elevation from DEM -- limited by DEM accuracy (~1-5m vertical)
- **Data requirements:** SRTM DEM + current water extent from satellite
- **Turbid water:** YES
- **Deep water:** Only works for exposed/inundated margins
- **Can we implement:** YES -- straightforward GIS operation
- **Complements:** Free depth data for lake margins where water level has changed since 2000

---

## C. Terrain-Based Prediction

### C1. Deep Learning from Surrounding Topography (Martinsen et al., 2023)
- **How it works:** U-Net trained on terrestrial DEM surrounding lakes to predict underwater bathymetry. Learns that lake basins are geological extensions of surrounding landscape. Trained on 153 Danish lakes.
- **Best accuracy:** MAE = 1.75 m (validation), 2.15 m (test) -- much better than baseline interpolation (3.12 m)
- **Data requirements:** High-resolution DEM of surrounding terrain, known bathymetry for training
- **Turbid water:** YES -- no optical dependency at all
- **Deep water:** YES -- can predict deep basins from surrounding relief
- **Can we implement:** YES -- we have terrain_depth_model.py
- **Complements:** VERY complementary -- works for ANY lake regardless of optical conditions. Only needs DEM.

### C2. Surrounding Relief as Predictor
- **How it works:** Statistical relationship: average change in relief between surrounding terrestrial landscape and lake surface is the best single predictor of lake depth and volume.
- **Best accuracy:** R2 ~0.50-0.70 depending on region
- **Data requirements:** DEM, lake outline
- **Turbid water:** YES
- **Deep water:** YES
- **Can we implement:** YES -- simple feature for XGBoost
- **Complements:** Excellent feature for max-depth prediction stage

---

## D. Morphometric/Statistical Methods

### D1. GLOBathy Power-Law Approach (Khazaei et al., 2022)
- **How it works:** Uses geometric/geophysical attributes from HydroLAKES (surface area, perimeter, shoreline development, elevation, etc.) with power-law and regression models to estimate max depth. Then generates full bathymetric map using idealized basin shapes.
- **Coverage:** 1.4+ million waterbodies globally, 30m resolution
- **Best accuracy:** Validated against 1,503 waterbodies with diverse observed data
- **Data requirements:** HydroLAKES attributes (freely available)
- **Turbid water:** YES -- no optical dependency
- **Deep water:** YES -- statistical estimation
- **Can we implement:** YES -- we have fetch_globathy.py; data available on Google Earth Engine
- **Complements:** Provides baseline estimate for every lake; useful as prior/feature

### D2. Cael & Seekell Area-Depth Scaling (2017, 2022)
- **How it works:** Theoretical framework based on fractal properties of Earth's surface. Lake depth scales with surface area via power law: `V ~ A^(1+H/2)` where H is Hurst exponent (~0.4). Maximum depth is a single random displacement on a topographic profile.
- **Key insight:** Good for predicting DISTRIBUTIONS of depths across many lakes, but high lake-to-lake variance makes individual lake prediction uncertain.
- **Best accuracy:** Statistical relationship only -- massive individual variation is inherent
- **Data requirements:** Lake surface area only
- **Turbid water:** YES
- **Deep water:** YES
- **Can we implement:** YES -- trivial formula
- **Complements:** Provides a Bayesian prior; useful as feature in ML model

### D3. European Standard EN 16039 Methods (area, perimeter, development ratio)
- **How it works:** Suite of empirical formulas relating morphometric variables (surface area, perimeter, shoreline development index, watershed area) to mean/max depth and volume.
- **Caution:** Recent analysis (2024) found these specific formulas are flawed and should not be used as-is. The methods produce biased estimates.
- **Can we implement:** Use as features in ML, not as standalone predictors

### D4. Comprehensive Morphometric Framework (Nature Water, 2025)
- **How it works:** Integrates lake hypsography (full depth-area curve) with functional properties across global scale. Provides framework for relating morphometric attributes to ecological and physical lake function.
- **Data requirements:** Combination of morphometric databases
- **Can we implement:** YES -- useful for understanding which features matter most

---

## E. LiDAR-Direct Methods

### E1. ICESat-2 / ATLAS Photon-Counting Bathymetry
- **How it works:** ICESat-2's ATLAS instrument fires green (532nm) laser photons from space. Photons that penetrate water and reflect off lake bottom are detected. Time-of-flight gives depth after refraction correction. New ATL24 bathymetry product (2024).
- **Best accuracy:** RMSE = 0.69 m (rRMSE = 10.3%) for depths < 16.5 m in clear lakes; can reach 38-40m in transparent water
- **Algorithms:** Median Filter (Ranndal 2021), CShelph (Thomas 2022), Bathy Pathfinder (Corcoran 2024) -- all being integrated into ATL24 ensemble
- **Data requirements:** ICESat-2 ATL03/ATL24 data (free from NASA)
- **Turbid water:** NO -- requires clear water for photon penetration
- **Deep water:** Up to 38m in very clear water, typically <16.5m
- **Can we implement:** YES -- we have fetch_icesat2.py and build_icesat2_training_set.py
- **Complements:** CRITICAL as training data source for spectral models. Sparse but accurate.

### E2. Airborne Bathymetric LiDAR (EAARL, Leica Chiroptera, etc.)
- **How it works:** Aircraft-mounted dual-wavelength LiDAR: IR (1064nm) reflects off water surface, green (532nm) penetrates water and reflects off bottom. Depth = difference in return times. EAARL-B can reach 3-44m.
- **Best accuracy:** cm-level in clear water (within 3 Secchi depths)
- **Data requirements:** Airborne survey -- expensive, limited geographic coverage
- **Turbid water:** Limited to ~3 Secchi depths (poor in turbid water)
- **Deep water:** Up to 44m with EAARL-B (10x laser power improvement)
- **Can we implement:** Only as data consumer -- USGS datasets for select lakes
- **Complements:** Highest accuracy available; use as ground truth where available

---

## F. SAR-Based Methods

### F1. Wave-Based SAR Bathymetry (Sentinel-1)
- **How it works:** Radar images capture ocean/lake surface wave patterns. As waves enter shallower water, wavelength shortens (shoaling effect). Depth derived from linear dispersion relation using wavelength and wave period.
- **Best accuracy:** Works to 100-200m depth for ocean (where waves are present)
- **Data requirements:** SAR imagery (Sentinel-1 C-band), presence of surface waves
- **Turbid water:** YES -- radar is independent of water clarity
- **Deep water:** YES -- up to 200m with appropriate wave conditions
- **Can we implement:** DIFFICULT for inland lakes -- requires consistent surface wave patterns which most lakes lack
- **Complements:** Not practical for most inland lakes; wave climate too variable/absent

### F2. Current-Based SAR Bathymetry
- **How it works:** SAR image intensity variations caused by currents over underwater topography. Requires current flow + small-scale surface waves for radar backscatter modulation.
- **Data requirements:** SAR imagery + current presence
- **Turbid water:** YES
- **Deep water:** YES
- **Can we implement:** NO for lakes -- requires ocean-like current patterns
- **Complements:** Not applicable to inland lakes

### F3. SAR Band Selection for Inland Water
- **How it works:** P-band is most suitable for underwater terrain detection, followed by L-band. C-band (Sentinel-1) is less ideal. But P-band satellites are very rare.
- **Can we implement:** NO -- practical SAR bathymetry for inland lakes is not yet mature
- **Complements:** Monitor for future P-band satellite missions

---

## G. Satellite Altimetry Methods

### G1. SWOT Ka-Band Radar Interferometry (2023-present)
- **How it works:** Surface Water and Ocean Topography satellite uses Ka-band radar interferometer to measure water surface elevation at ~100m resolution across 120km swaths. Combined with water extent from optical satellites, enables A-E approach.
- **Best accuracy:** Water surface elevation accuracy ~0.18m; lake/reservoir storage for areas > 1km2
- **Coverage:** Nearly all lakes > 250m x 250m globally; 21-day repeat cycle
- **Data requirements:** SWOT data products (free from NASA/CNES, Version C since March 2024)
- **Turbid water:** YES -- radar-based, cloud/turbidity independent
- **Deep water:** Only measures surface elevation, not depth directly
- **Can we implement:** YES -- use as elevation input to A-E method
- **Complements:** Key enabler for A-E approach; fills gaps where ICESat-2 has no ground tracks

### G2. Sentinel-6 / Jason-CS Radar Altimetry
- **How it works:** Conventional radar altimetry measuring water surface elevation along nadir track. SAR mode achieves ~300m along-track footprint.
- **Best accuracy:** u-RMSE = 6.4 cm for water surface elevation
- **Data requirements:** Free Copernicus data
- **Can we implement:** YES -- as elevation input
- **Complements:** Long time-series of water level history (Jason-1/2/3 back to 2001)

### G3. Multi-Mission Altimetry Time Series
- **How it works:** Combines water level from 7+ radar altimetry missions + 2 LiDAR missions over decades. Long water level history reveals more of the A-E curve.
- **Data source:** USDA G-REALM, ESA DAHITI databases
- **Can we implement:** YES -- historical water levels freely available
- **Complements:** More water level variation = more A-E data points = better bathymetry reconstruction

---

## H. Thermal Methods

### H1. Thermal Band Augmentation (Landsat-8 Band 10/11)
- **How it works:** Shallow water over dark substrate warms faster than deep water (less thermal inertia). Thermal infrared bands correlate with depth because shallow areas absorb more solar radiation and warm the overlying water.
- **Best accuracy:** Thermal bands alone: R2 = 0.52-0.53; combined with optical: R2 = 0.98, RMSE = 0.78m (vs R2 = 0.93, RMSE = 1.30m without thermal)
- **Data requirements:** Landsat-8/9 thermal bands (100m resolution, resampled to 30m)
- **Turbid water:** PARTIALLY -- thermal signal exists regardless of turbidity, but is weaker in deep turbid water
- **Deep water:** Diminishes with depth as thermal contrast decreases
- **Can we implement:** YES -- add Landsat thermal bands as features in XGBoost
- **Complements:** OVERLOOKED in our pipeline. Adding thermal bands improved RMSE by 40% in one study. Easy to add.

### H2. Diurnal Thermal Cycling
- **How it works:** Shallow areas heat/cool faster than deep areas through the diurnal cycle. Comparing morning vs afternoon thermal imagery reveals depth-correlated patterns.
- **Data requirements:** Multiple thermal images at different times of day (difficult from polar-orbiting satellites)
- **Can we implement:** DIFFICULT -- limited temporal sampling from Landsat
- **Complements:** Theoretically powerful but data-limited from satellite; more practical with geostationary thermal sensors

---

## I. Physics-Based Radiative Transfer

### I1. HydroLight / Radiative Transfer Equation Inversion
- **How it works:** Full physics simulation of photon transport through atmosphere -> water surface -> water column -> bottom -> back to sensor. Solves the scalar radiative transfer equation. Inverts observed reflectance to retrieve depth, bottom type, and water optical properties simultaneously.
- **Best accuracy:** Similar to empirical approaches in validation; more physically consistent
- **Data requirements:** Atmospheric correction, knowledge of water IOPs (inherent optical properties), bottom reflectance model
- **Turbid water:** Can model turbid water explicitly if IOPs are known
- **Deep water:** Limited by same optical penetration physics
- **Can we implement:** PARTIALLY -- complex to implement; requires IOP estimation. Could use semi-analytical approximations.
- **Complements:** Provides physically-grounded depth estimates; important for understanding why models fail

### I2. Semi-Analytical Models (Lee et al., HOPE model)
- **How it works:** Simplified radiative transfer that parameterizes water depth and bottom reflectance explicitly. Separates water column contributions from bottom contributions algebraically.
- **Best accuracy:** Moderately accurate in waters < 13m; errors increase with depth
- **Data requirements:** Multispectral imagery + basic IOP estimates
- **Can we implement:** YES -- can add as feature or parallel model
- **Complements:** Provides physics-based depth estimate as feature for ML ensemble

### I3. Copernicus Marine Physics-Based SDB
- **How it works:** Operational physics-based product using RTE inversion for clear coastal waters. Also includes wave-kinematics approach for turbid areas.
- **Coverage:** Global coastal, launched 2024
- **Can we implement:** Can use as external data source for coastal lakes
- **Complements:** Reference dataset for validation

---

## J. Physics-Informed Neural Networks (PINNs)

### J1. HybridBathNet (2025)
- **How it works:** Integrates U-Net (spatial/spectral feature extraction) with a physical bathymetry network (ensuring outputs obey physical constraints like Beer-Lambert law). Physics-guided loss function penalizes physically impossible depth estimates.
- **Best accuracy:** Improved generalizability vs pure data-driven models across diverse areas
- **Data requirements:** Sentinel-2 imagery + training depths
- **Can we implement:** YES -- modify our U-Net stage 2 to include physics constraints
- **Complements:** Addresses our generalization problem for unseen lakes

### J2. PINN for Shallow Water Equations
- **How it works:** Neural network trained with loss function that includes shallow water equation residuals. Can recover bathymetry from surface observations (water level, velocity) by inverting the physics.
- **Best accuracy:** Good for depth and velocity even over non-horizontal bottoms
- **Data requirements:** Water surface elevation/velocity observations
- **Can we implement:** RESEARCH-STAGE -- interesting but complex
- **Complements:** Theoretical interest; practical implementation challenging

---

## K. Multi-Source Fusion

### K1. ICESat-2 + Sentinel-2 Fusion (Most Common)
- **How it works:** Uses ICESat-2 photon bathymetry as training data for spectral models applied to Sentinel-2 imagery. Combines sparse-but-accurate LiDAR depths with dense-but-noisy optical reflectance.
- **Best accuracy:** MAE < 0.90m with profile depth fitting; 0.35-0.46m improvement over non-fitted
- **Data requirements:** Both ICESat-2 and Sentinel-2 data
- **Can we implement:** YES -- this is essentially our pipeline
- **Complements:** Core architecture

### K2. Active-Passive Photon Fusion (SDB-IFAP, 2025)
- **How it works:** Iteratively fuses active LiDAR photons (ICESat-2) with "pseudo-photons" derived from passive imagery. Novel approach that converts spectral information into photon-like depth observations.
- **Best accuracy:** Improved over standard ICESat-2 alone or Sentinel-2 alone
- **Data requirements:** ICESat-2 + multispectral imagery
- **Can we implement:** RESEARCH -- novel technique, would require custom implementation
- **Complements:** Interesting direction for improving fusion accuracy

### K3. Multi-Temporal Sentinel-2 + ICESat-2 Bathymetry Fusion
- **How it works:** Produces bathymetry from multiple time points using 4+ traditional methods, then fuses all results. Time-series fusion reduces uncertainty vs single-image estimates.
- **Best accuracy:** Improved reliability over single-date estimates
- **Can we implement:** YES -- extend our temporal compositing to multi-model fusion
- **Complements:** Reduces variance in predictions

### K4. MuSRFM (Multiple Scale Resolution Fusion Model)
- **How it works:** Integrates information at multiple spatial scales from temporally fused Sentinel-2 imagery. Captures both fine-grained and broad-scale depth patterns.
- **Can we implement:** YES -- architectural modification to our CNN
- **Complements:** Better for complex bathymetry with multiple depth regimes

### K5. Sonar + Satellite Fusion
- **How it works:** Uses sparse sonar transects (from boats, fish finders) as ground truth, densified by satellite-derived features. ML interpolation between sonar points using spectral, thermal, and morphometric features.
- **Best accuracy:** Dependent on sonar density; can approach sonar accuracy at scale
- **Data requirements:** Some sonar data + satellite imagery
- **Can we implement:** YES -- we have state bathymetry data (sonar surveys)
- **Complements:** Our state survey data serves this role

### K6. Spectral + Geometric + Morphometric Ensemble
- **How it works:** Stacks predictions from spectral SDB (shallow, clear), A-E geometric (fluctuating lakes), terrain-based (all lakes), and morphometric (coarse baseline). Meta-learner weights each by confidence.
- **THIS IS THE IDEAL ARCHITECTURE** (see recommendations below)
- **Can we implement:** YES -- all components available
- **Complements:** This is what we should build

---

## L. Acoustic/Sonar + Crowdsourced

### L1. Multibeam Echo Sounder (MBES)
- **How it works:** Transmits multiple acoustic beams simultaneously from boat-mounted transducer. Provides highest-resolution bathymetry possible.
- **Best accuracy:** cm-level
- **Data requirements:** Boat access, expensive equipment, trained operator
- **Can we implement:** Only as data consumer (state survey datasets)

### L2. Single-Beam Echo Sounder (SBES)
- **How it works:** Single acoustic beam from boat. Produces depth along transect lines.
- **Can we implement:** Consumer-grade fishfinders operate on this principle

### L3. Crowdsourced Bathymetry (CSB)
- **How it works:** Recreational boaters with GPS-equipped fishfinders (Lowrance, Garmin, Humminbird) contribute depth readings. Aggregated by platforms like BioBase, IHO DCDB (117+ million points).
- **Data requirements:** Fishfinder data from recreational boaters
- **Can we implement:** YES as data consumer -- Navionics, C-MAP, and other platforms aggregate recreational sonar data
- **Complements:** UNDEREXPLOITED resource. Fishbrain users with fishfinders could contribute. IHO database freely available.

### L4. Graph Neural Network / Kriging Interpolation of Sparse Sonar
- **How it works:** Takes sparse sonar transects and interpolates full bathymetric surface using graph neural networks or geostatistical kriging. GNNs learn spatial dependency structure.
- **Best accuracy:** Approaches dense sonar quality with sufficient transect coverage
- **Can we implement:** YES -- GNN or kriging on available sonar data
- **Complements:** Key technique for converting sparse crowdsourced data to full maps

---

## M. Ground-Penetrating Radar (GPR)

### M1. Drone-Borne / Boat-Borne GPR
- **How it works:** High-frequency (100-2000 MHz) pulsed radar waves transmitted through water. Reflects off bottom and subsurface sediment layers. Works best in freshwater (conductive water attenuates signal).
- **Best accuracy:** Water depth precision +/-3%, sediment thickness +/-15%
- **Depth range:** Up to ~2.5m with drone-borne; deeper with boat-borne depending on conductivity
- **Data requirements:** GPR equipment on drone/boat, low-conductivity freshwater (<340 uS/cm)
- **Turbid water:** YES -- radar penetrates turbid water
- **Deep water:** NO -- limited to very shallow water (<5m typically)
- **Can we implement:** NO -- requires in-situ equipment, not satellite-based
- **Complements:** Niche technique; better than sonar in vegetated areas

---

## N. Drone/UAV Photogrammetry

### N1. Structure-from-Motion (SfM) Bathymetry
- **How it works:** UAV captures overlapping RGB images of water body. SfM algorithms reconstruct 3D surface including visible bottom. Requires refraction correction for underwater points.
- **Best accuracy:** SD = 0.11m for depths up to 1m; up to 4-5m with correction
- **Data requirements:** UAV + camera + clear water + ground control points
- **Turbid water:** NO -- requires visible bottom
- **Deep water:** NO -- limited to ~5m maximum
- **Can we implement:** NO -- not satellite-based; requires in-situ UAV flights
- **Complements:** Ultra-high resolution for shallow clear-water validation

### N2. UAV Multispectral Bathymetry
- **How it works:** UAV-mounted multispectral cameras (like satellite but from lower altitude). Apply same Stumpf/Lyzenga/ML methods at much higher resolution.
- **Best accuracy:** Higher than satellite due to better resolution and atmospheric conditions
- **Can we implement:** NO -- requires UAV deployment
- **Complements:** High-res ground truth for satellite model validation

---

## O. Hyperspectral Methods

### O1. PRISMA Spaceborne Hyperspectral (ASI, Italy)
- **How it works:** 250+ contiguous spectral bands (400-2500nm) at 30m resolution. Narrow bands allow precise isolation of water absorption features and bottom reflectance, enabling simultaneous retrieval of depth, bottom type, and water quality.
- **Best accuracy:** Not yet extensively validated for inland lake bathymetry
- **Data requirements:** PRISMA imagery (limited coverage, tasking required)
- **Can we implement:** PARTIALLY -- data access is limited; could request specific lake scenes
- **Complements:** Future potential as hyperspectral missions expand

### O2. EnMAP Hyperspectral (DLR, Germany)
- **How it works:** Similar to PRISMA; 242 bands, 30m resolution, 400-2500nm
- **Can we implement:** Same as PRISMA -- limited coverage
- **Complements:** Future potential

### O3. DESIS Hyperspectral (DLR, on ISS)
- **How it works:** 235 bands, 400-1000nm range, 30m resolution, 2.5nm spectral sampling
- **Best accuracy:** Depth RMSE = 0.38 m, relative error 17% for inland water
- **Can we implement:** Data available but limited temporal coverage
- **Complements:** Best hyperspectral accuracy reported for inland water

### O4. AVIRIS-NG Airborne Hyperspectral
- **How it works:** Airborne hyperspectral sensor used for turbid and deep water mapping. Study in Wax Lake Delta achieved depth mapping in turbid conditions.
- **Best accuracy:** Demonstrated capability in turbid/deep conditions where multispectral fails
- **Can we implement:** Only as data consumer for specific study areas
- **Complements:** Demonstrates potential of hyperspectral for challenging conditions

---

## Current SOTA & Theoretical Limits

### What is the BEST achievable accuracy for inland lake bathymetry?

**Shallow clear water (<10m, Secchi > 3m):**
- Best: RMSE = 0.3-0.5m using ML on multispectral + ICESat-2 training data
- Theoretical limit: ~0.2m (limited by Sentinel-2 radiometric noise and 10m pixel mixing)

**Shallow turbid water (<10m, Secchi < 1m):**
- Best: RMSE = 1-2m using thermal augmentation + multi-temporal + ML
- Theoretical limit: ~0.5m (if sufficient training data from sonar available)

**Deep water (10-40m):**
- Best: RMSE = 1-3m using ICESat-2 direct measurement (clear water only)
- Spectral methods: effectively useless beyond ~2 Secchi depths

**Any lake, any depth (full bathymetric map):**
- A-E method (3D-LAKES): RMSE = 1.37m (requires water level variation)
- Terrain-based DL (Martinsen): MAE = 1.75-2.15m (works for any lake)
- GLOBathy morphometric: coarse but global

### Theoretical limit for ensemble combining all methods:
- Clear, shallow lakes: RMSE ~0.3m
- Typical inland lakes: RMSE ~1.0m
- Deep/turbid/stable lakes: RMSE ~1.5-2.0m

---

## Recommended Ensemble Architecture

### The Optimal Multi-Source Bathymetry Ensemble:

```
LAYER 1: Data Sources
  [Sentinel-2 spectral] [Landsat-8 thermal] [ICESat-2 photons] [SWOT elevation]
  [3D-LAKES A-E] [GLOBathy] [DEM terrain] [State sonar surveys] [Crowdsourced sonar]

LAYER 2: Individual Models (diverse methods)
  a) XGBoost on spectral+thermal bands (clear shallow)
  b) U-Net CNN on spectral image patches (spatial patterns)
  c) Physics-informed neural net (physical consistency)
  d) A-E geometric interpolation (fluctuating lakes)
  e) Terrain DL prediction from surrounding DEM (all lakes)
  f) GLOBathy morphometric baseline (global prior)
  g) Kriging interpolation of sparse sonar data (where available)

LAYER 3: Meta-Learner
  - Stacked ensemble (XGBoost meta-learner)
  - Confidence-weighted averaging based on data availability
  - Spectral models weighted high for clear/shallow
  - A-E/terrain models weighted high for turbid/deep
  - Sonar models weighted high where coverage exists

LAYER 4: Uncertainty Quantification
  - Quantile regression for prediction intervals
  - Model disagreement as uncertainty signal
  - Data availability mask (which sources contributed)
```

---

## Implementation Priority for OpenCatch

### What we HAVE:
1. Spectral XGBoost (stage 1 max-depth, stage 2 spatial) -- CORE
2. ICESat-2 training data pipeline -- CORE
3. 3D-LAKES A-E extraction -- NEW
4. GLOBathy baseline -- AVAILABLE
5. State sonar surveys -- PARTIAL
6. BathyFormer (ViT) -- EXPERIMENTAL

### What we're MISSING (ranked by impact/effort):

**HIGH IMPACT, LOW EFFORT:**
1. **Thermal band features** -- Add Landsat-8 Band 10/11 as features to XGBoost. Studies show 40% RMSE improvement. Trivial to implement.
2. **Surrounding terrain features** -- Extract DEM statistics (relief, slope, curvature) around each lake as features. Already partially in terrain_depth_model.py.
3. **GLOBathy as prior/feature** -- Feed GLOBathy max-depth estimate as a feature into our XGBoost. Free improvement via Bayesian prior.

**HIGH IMPACT, MEDIUM EFFORT:**
4. **Physics-informed loss function** -- Add Beer-Lambert / RTE constraints to our U-Net training loss. Improves generalization to unseen lakes.
5. **Multi-model stacking** -- Combine spectral, A-E, terrain, and morphometric models in a meta-learner.
6. **SWOT water level time-series** -- Integrate SWOT elevation data for A-E enhancement.
7. **Crowdsourced sonar integration** -- Pull IHO CSB database or Navionics data for training.

**MEDIUM IMPACT, HIGH EFFORT:**
8. **Hyperspectral (DESIS/PRISMA)** -- Worth exploring for challenging lakes but limited data availability.
9. **Multi-temporal fusion** -- Already partially implemented; formalize across methods.
10. **GNN spatial interpolation** -- For densifying sparse sonar data.

### Techniques we can SAFELY SKIP for inland lakes:
- SAR wave-based bathymetry (no consistent wave climate in lakes)
- GPR (requires in-situ equipment)
- Drone photogrammetry (not scalable)
- Gravity-based methods (ocean-scale only)

---

## Key Insight: What We Missed Before

Before this review, we were primarily focused on:
- Spectral/optical methods (A)
- ICESat-2 training data (E1)
- 3D-LAKES geometric (B1)
- GLOBathy morphometric (D1)

**What we were missing:**
1. **Thermal bands** (H1) -- Most impactful easy win. Landsat-8 thermal bands significantly improve depth estimation.
2. **Terrain-based DL** (C1) -- Predicting bathymetry purely from surrounding DEM topology. Works for ALL lakes.
3. **Physics-informed constraints** (J1) -- HybridBathNet approach for better generalization.
4. **Crowdsourced sonar** (L3) -- 117M+ depth points in IHO database, plus fishfinder data from recreational anglers.
5. **SWOT water levels** (G1) -- New satellite mission dramatically improves A-E approach.
6. **Water recession method** (B2) -- DEM-only bathymetry via simulated lake draining.
7. **Semi-analytical radiative transfer** (I2) -- Physics-based depth as feature for ML.
8. **Multi-model stacking** (K6) -- Formally combining diverse approaches in meta-learner.
