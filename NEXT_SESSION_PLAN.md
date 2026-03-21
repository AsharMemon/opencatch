# CASTLINE Next Session Plan — March 18, 2026

## Current State
- **V13 model trained** (March 18): CV R²=0.705 (target hit!), Walk-forward=0.429, Spatial=0.539
- **Temporal R²=0.329** — regression from V12 (0.420) due to unseen location problem
  - Seen locations: R²=0.541 (great)
  - Unseen locations: R²=0.117 (the bottleneck)
- V13 artifacts downloaded locally: `castline/models/cpue_v13_*`
- V13 training script: `scripts/build_cpue_model_v13.py`
- Vast.ai: A4000 stopped, 5070 Ti stopped, Instance 2 (skyview) running

---

## Track 1: Temporal R² Improvement (GPU / Vast.ai)

### The core problem
V13 excels at spatial prediction (WHERE is good for fishing) but temporal prediction (WHEN conditions change at a location) is weaker. The temporal holdout has 550/967 rows at unseen locations — spatial features can't help there.

### Strategy A: More & better temporal features
High-impact data sources not yet in the model:

1. **USACE Reservoir Data** (HANDOFF item 15)
   - Lake levels, scheduled releases, inflows
   - Many tournament lakes are managed reservoirs where water level changes dominate catch
   - API: https://water.usace.army.mil/a2w/
   - Script needed: `scripts/fetch_usace_reservoir.py`
   - Expected impact: HIGH — water level is the #1 signal anglers use

2. **NOAA Tidal Data** (for coastal/estuarine locations)
   - Tidal stage, current predictions
   - API: https://tidesandcurrents.noaa.gov/api/
   - Lower priority — most tournament data is freshwater

3. **Richer temporal lag features**
   - Currently have 3/7/14-day means — add 1-day and 30-day
   - Add rate-of-change features (e.g., "water temp rising 2°C over 3 days" → spawn trigger)
   - Add interaction terms: pressure_change × water_temp_change
   - Cross-location temporal features: regional fishing index (how are nearby locations doing?)

4. **Solunar/moon overhead data**
   - Currently have lunar phase — add specific major/minor feeding periods
   - Moon overhead/underfoot times correlate with feeding activity

5. **More weather granularity**
   - Hourly weather for the 24h before tournament (not just daily means)
   - Cloud cover changes, precipitation timing
   - Cold front passage detection (pressure drop rate)

### Strategy B: Two-model architecture
- **Seen-location model**: Uses spatial + temporal + historical features (should achieve R²>0.5)
- **Unseen-location model**: Uses only generalizable features (weather, season, lat/lon embeddings)
- At inference: if location has prior data → seen model, else → unseen model
- This splits the problem instead of forcing one model to handle both cases

### Recommended order
1. Fetch USACE reservoir data → add to V17 dataset (biggest bang for buck)
2. Add richer lag features (1d/30d, rate-of-change, interactions)
3. Train V14 on enriched dataset
4. If temporal R² still < 0.45, implement two-model architecture

---

## Track 2: Docker Deployment (Vast.ai)

### Steps
1. Start A4000 or 5070 Ti instance
2. Install Docker + Docker Compose
3. Copy `infra/docker-compose.yml` and related configs
4. `docker compose up -d` — PostGIS 16, Redis 7, FastAPI, Martin, worker
5. Copy V13 model artifacts into the container
6. Test endpoints: `/api/v2/predict`, `/api/v2/forecast`, `/api/v2/conditions`
7. Expose port for mobile app testing

### Files involved
- `infra/docker-compose.yml`
- `castline/api/main.py` (FastAPI app)
- `castline/api/config.py` (env settings)
- `castline/api/routers/` (all endpoint routers)

---

## Track 3: Mobile App (Local Mac)

### Priority items from HANDOFF
- Wire mobile app to live API (HANDOFF item 10)
- MapLibre GL Native migration (HANDOFF item 13)
- Bathymetric contour styling (HANDOFF item 12)

### Current state
- Expo 54 app with bottom tab nav
- Mock data in `mobile/src/data/mockData.ts`
- API client exists at `mobile/src/services/api.ts`
- Screens: Splash, Auth, Explore, Map, LocationDetail, CatchReport, Profile, etc.

---

## Parallel execution plan
- **Vast.ai**: USACE data fetch + V14 training + Docker deployment
- **Local Mac**: Mobile app wiring, MapLibre migration
- These are fully independent and can run in parallel
