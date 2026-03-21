# CASTLINE Feature List — Comprehensive Work Queue

_Created: 2026-03-19 | Priority: top-to-bottom within each section_

## Status Key
- [ ] Not started
- [~] In progress / partially done
- [x] Complete

---

## 1. ML Model & Data (Vast.ai)

### 1.1 Temporal R² Improvement (Current: 0.329, Target: 0.50+)

- [x] **V14 two-model architecture** — Separate seen-location (spatial+temporal) and unseen-location (weather/season only) models. Script: `scripts/build_cpue_model_v14.py`
- [x] **Enhanced rate-of-change features** — Cold front detection, pressure/temp change rates, stability indices, interaction terms (pressure×temp, wind×pressure). Built into V14.
- [~] **USACE reservoir data** — Pool elevation, tailwater, inflow/outflow for managed reservoirs. Script: `scripts/fetch_usace_reservoir.py` (written, needs to run). Water level changes are the #1 bass angling signal.
- [ ] **Train V14 on Vast.ai** — Run two-model training, evaluate temporal R² improvement
- [ ] **NOAA temperature departure** — Compute deviation from 1991-2020 monthly climate normals. Fish respond to anomalies, not absolutes. (HANDOFF item 16)
- [ ] **Hourly weather pre-tournament** — Fetch 24h hourly weather before each event (not just daily means). Captures frontal passage timing.
- [ ] **Solunar major/minor feeding windows** — Currently have moon phase; add computed major/minor overhead/underfoot feeding periods with duration.
- [ ] **30-day lag features** — Add 30-day rolling means and trends to capture longer-term seasonal transitions.
- [ ] **1-day delta features** — Sharp 1-day changes in temp, pressure, discharge as distinct features.
- [ ] **Cold front passage detector** — Binary feature: pressure drop >4mb in 12h + temp drop + wind shift. The single most impactful short-term fishing event.
- [ ] **Spawn timing model** — GDD-based spawn phase prediction per species per latitude. Spawn is the biggest seasonal transition for bass.
- [ ] **V15: Hurdle model architecture** — P(catch > 0) × E(weight | catch > 0). Addresses zero-inflated nature of fishing data. (PRODUCTION_ARCHITECTURE target v2)
- [ ] **V16: ConvLSTM/t-PatchGNN** — Deep learning for spatiotemporal patterns the tree ensemble misses. CATCH paper approach. Requires significant GPU time. (HANDOFF item 4)

### 1.2 Spatial Prediction Improvement (Current: 0.539)

- [ ] **More tournament data sources** — Scrape additional circuits: B.A.S.S. Nation, college bass, state federation tournaments. More locations = better spatial coverage.
- [ ] **State DNR creel survey integration** — Expand beyond CreelCat to state-specific survey databases (FL FWC, TX Parks, MN DNR). Script stubs exist.
- [ ] **NHD+ stream connectivity features** — Upstream/downstream relationships between tournament locations. Water quality propagates downstream.
- [ ] **Satellite chlorophyll-a time series** — MODIS/Sentinel-2 chlorophyll as productivity proxy. Script exists: `scripts/fetch_modis_chlorophyll.py`
- [ ] **Water quality portal (WQP) expansion** — Currently have chlorophyll/secchi/phosphorus for some locations. Expand coverage.

### 1.3 Model Infrastructure

- [ ] **Conformal prediction intervals** — Layer 4 confidence with calibrated uncertainty bounds. (PRODUCTION_ARCHITECTURE)
- [ ] **OOD (out-of-distribution) detection** — Flag predictions for locations/conditions far from training data.
- [ ] **Online learning from catch reports** — Bayesian update to site prior as user data accumulates. (PRODUCTION_ARCHITECTURE Phase 3)
- [ ] **Model monitoring dashboard** — Track prediction drift, feature distribution shifts over time.
- [ ] **A/B test framework** — Compare V13 vs V14 predictions on live traffic.

---

## 2. Backend & Infrastructure

### 2.1 Docker Deployment (Vast.ai)

- [x] **Docker Compose stack** — PostGIS 16, Redis 7, FastAPI, Martin, worker. `infra/docker-compose.yml`
- [x] **Deployment script** — `scripts/deploy_vast.sh` for one-command deployment to Vast.ai
- [x] **Nginx reverse proxy config** — `infra/nginx/nginx.conf`
- [ ] **Deploy Docker stack on Vast.ai** — Actually run it on a live instance
- [ ] **Run PostGIS ingestion scripts** — Load PAD-US, NHDPlus, bathymetry into PostGIS (HANDOFF item 9)
- [ ] **Smoke test all endpoints** — POST /predict, GET /forecast, GET /best-fishing, POST /catch-report, GET /conditions
- [ ] **SSL/HTTPS setup** — Let's Encrypt or Cloudflare tunnel for secure mobile connections
- [ ] **Environment variable management** — Move secrets (DB passwords, API keys) to .env files, not hardcoded

### 2.2 API Enhancements

- [x] **Port Django views to FastAPI** — predict_v2, forecast_v2, catch_report, batch_predict, model_info. (HANDOFF item 14)
- [x] **V14 inference module** — Two-model routing in `castline/models/inference_v14.py`
- [ ] **Real-time feature collection** — Wire predictions to actually call USGS + Open-Meteo + solunar at request time (currently uses precomputed features)
- [ ] **Catch report persistence** — Store reports in PostGIS (currently JSONL fallback)
- [ ] **Signal dashboard endpoint** — `/api/v2/signals` for aggregating recent catch reports into fishing activity signals
- [ ] **Location search endpoint** — Fuzzy text search with PostGIS `pg_trgm` for finding lakes/rivers by name
- [ ] **Nearby locations endpoint** — PostGIS spatial query for "best fishing near me"
- [ ] **Background worker** — Periodic refresh of conditions cache for popular locations (every 1-6 hours)
- [ ] **Rate limiting** — Protect API from abuse, per-user quotas

### 2.3 Data Pipeline

- [ ] **Automated USGS data refresh** — Cron job to pull latest water data for all tracked locations
- [ ] **Weather forecast cache** — Pre-fetch 7-day forecasts for top locations
- [ ] **Stocking report ingestion** — State fish stocking data (FL FWC, Great Lakes). Scripts exist.
- [ ] **Tournament result auto-scraper** — Periodic scraping of new tournament results to expand training data

---

## 3. Mobile App (Local Mac)

### 3.1 Core Wiring

- [~] **Wire screens to live API** — Replace mock data with real API calls. Agent working on: ExploreScreen, LocationDetailScreen, CatchReportScreen. (HANDOFF item 10)
- [ ] **API base URL configuration** — Dev/staging/prod URL switching with environment detection
- [ ] **Offline mode** — Cache last-fetched conditions and show stale data indicator when offline
- [ ] **Error handling UX** — Network errors, 503 model not loaded, timeout handling with retry
- [ ] **Pull-to-refresh** — RefreshControl on ExploreScreen and LocationDetailScreen

### 3.2 Map Experience

- [ ] **MapLibre GL Native migration** — Replace react-native-maps for vector tile support. Required for custom contour styling. (HANDOFF item 13)
- [ ] **Bathymetric contour styling** — Depth gradient fills (shallow→deep blue), line-width interpolation by zoom, depth labels. Requires MapLibre. (HANDOFF item 12)
- [ ] **Heat map layer** — Fishing score overlay on the map showing hot/cold zones
- [ ] **Location search on map** — Search bar that geocodes and flies to a location
- [ ] **Save waypoint from map** — Long-press to drop a pin and save as personal waypoint
- [ ] **Cluster markers** — Group nearby location pins when zoomed out

### 3.3 Prediction & Forecast UX

- [ ] **Fishing score gauge** — Animated 0-100 gauge on LocationDetailScreen (component exists: `ScoreGauge.tsx`)
- [ ] **4-layer breakdown visualization** — Stacked bar or radar chart showing hydrology/weather/biology/history contributions
- [ ] **7-day forecast chart** — Score trend line with weather icons (component exists: `ForecastChart.tsx`)
- [ ] **Explanation cards** — Human-readable "why is fishing good/bad today?" cards (component exists: `ExplanationCard.tsx`)
- [ ] **Best fishing times** — Show optimal times of day based on solunar + weather
- [ ] **Trophy alert** — Notification when conditions align for above-average catch potential

### 3.4 Catch Reports & Social

- [ ] **Catch report flow** — Full form: species picker, weight, photo, location auto-detect, conditions snapshot
- [ ] **Trip log** — History of submitted catch reports with stats
- [ ] **Activity feed** — Recent activity from nearby anglers (ActivityScreen exists)
- [ ] **Star ratings** — Post-trip rating with sentiment analysis (StarRating component exists)

### 3.5 Settings & Personalization

- [ ] **Species preference** — Select target species to customize predictions
- [ ] **Unit preferences** — Fahrenheit/Celsius, lbs/kg, miles/km
- [ ] **Notification preferences** — Push alerts for good conditions, weather changes
- [ ] **Home lake** — Default location for quick dashboard view

### 3.6 Authentication

- [ ] **Auth flow** — Sign up / sign in (AuthScreen exists, needs backend)
- [ ] **JWT token management** — Store and refresh tokens
- [ ] **User profile sync** — Settings, waypoints, catch reports tied to account

---

## 4. Data Quality & Expansion

- [ ] **More USGS gauge coverage** — Map additional tournament locations to nearest gauges
- [ ] **Canadian water data** — Environment Canada real-time hydro API (international expansion Tier 1)
- [ ] **Lake level historical trends** — Long-term pool elevation trends for reservoir management cycles
- [ ] **Vegetation index** — Satellite-derived aquatic vegetation coverage (bass habitat)
- [ ] **Boat ramp / access point data** — Public access points for each water body
- [ ] **Fishing regulations** — Season dates, limits, special regulations per state/water body

---

## 5. Future / Research

- [ ] **65-species catalog** — Expand beyond bass to panfish, catfish, trout, walleye, coastal species. (Service exists: `castline/services/site_prior.py`)
- [ ] **International expansion** — Canada, South Africa, Japan. (Memory: `project_international.md`)
- [ ] **Photo AI** — Species identification from catch photos (on-device model)
- [ ] **Voice assistant** — "Hey Castline, should I go fishing today?"
- [ ] **Apple Watch complication** — Quick glance at fishing score for saved locations
- [ ] **Social fishing spots** — Community-contributed locations with privacy controls
- [ ] **Tournament mode** — Practice planning tools using historical conditions analysis

---

## Execution Order (Recommended)

### This week:
1. Train V14 on Vast.ai (2-3 hours)
2. Deploy Docker stack on Vast.ai
3. Finish mobile app API wiring
4. Run USACE reservoir data fetch

### Next week:
5. MapLibre GL Native migration
6. Bathymetric contour styling
7. NOAA temperature departure features
8. Train V15 with USACE + temp departure data

### Following weeks:
9. Hurdle model (V15/V16)
10. Catch report persistence + signal dashboard
11. Auth flow + user profiles
12. ConvLSTM experimentation
