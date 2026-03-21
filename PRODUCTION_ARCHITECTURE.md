# CASTLINE Production Architecture
## "Will I catch fish here today?" — A 4-Layer Decision System

### Core Insight
The model is NOT one predictor. It's **four cooperating layers** that each answer a different sub-question:

| Layer | Question | Data Source | Update Freq | Model |
|-------|----------|-------------|-------------|-------|
| **1. Site Prior** | "Is this a good fishery?" | CreelCat, LAGOS, USGS fish community | Static (quarterly) | CatBoost spatial |
| **2. Conditions** | "Are fish biting today?" | USGS real-time, Open-Meteo, solunar | Hourly | CatBoost temporal |
| **3. Catch Rate** | "How many will I catch per hour?" | CreelCat CPUE, enriched w/ weather | Daily forecast | Hurdle model |
| **4. Confidence** | "How much should I trust this?" | All above + coverage metrics | Per-prediction | Conformal/OOD |

### Composite Score (what the user sees)
```
fishing_score = 0-100 scale combining:
  - catch_probability (0-1) × 30 points
  - expected_cpue_percentile (0-1) × 30 points
  - conditions_vs_historical (0-1) × 25 points
  - trophy_potential (0-1) × 15 points

confidence_level = "high" | "medium" | "low" | "extrapolating"
```

---

## Data Pipeline (Production)

### Real-Time (every 1-6 hours)
| Source | Data | API | Cost | Latency |
|--------|------|-----|------|---------|
| **USGS NWIS** | Water temp, discharge, gage height, DO | REST, free | $0 | ~15min delay |
| **Open-Meteo** | Air temp, pressure, wind, precip, cloud | REST, free | $0 | Real-time |
| **Solunar** | Moon phase, major/minor periods | Computed locally | $0 | Instant |

### Daily (forecast layer)
| Source | Data | API | Cost |
|--------|------|-----|------|
| **Open-Meteo Forecast** | 7-day weather forecast | REST, free | $0 |
| **USGS** | 24h discharge/temp history for trends | REST, free | $0 |

### Static (quarterly refresh)
| Source | Data | Size | Notes |
|--------|------|------|-------|
| **CreelCat** | Bass CPUE per water body | 1,540 water bodies | Spatial prior |
| **LAGOS** | Lake morphometry (depth, area, SDI) | 50K+ US lakes | Physical characteristics |
| **USGS Fish Community** | Species composition per reach | 35K reaches | Biodiversity context |
| **GeoCLIP/SatCLIP** | Location embeddings (32-dim) | Per location | Learned spatial features |

### User-Generated (Phase 2)
| Source | Data | Flow |
|--------|------|------|
| **Catch Reports** | Location, date, species, count, size, effort_hours | User → API → DB |
| **Trip Ratings** | 1-5 star rating + conditions at time | User → API → DB |

---

## Model Architecture

### Current (v16 — CatBoost ensemble)
```
Features (366 cols) → CatBoost/XGB/LGBM ensemble → median_weight_lb
                                                   → R² = 0.66 LOO
```

### Target (v2 — 4-Layer System)
```
┌─────────────────────────────────────────────────────────┐
│                    INFERENCE REQUEST                      │
│   Input: (lat, lon, date, optional: usgs_site_id)        │
└──────────────┬───────────────────────────────────────────┘
               │
    ┌──────────▼──────────┐
    │   Feature Collector  │ ← USGS API, Open-Meteo API, solunar calc
    │   (real-time fetch)  │ ← LAGOS lookup, CreelCat lookup (cached)
    └──────────┬──────────┘
               │
    ┌──────────▼──────────┐
    │   Layer 1: Site Prior │ ← CreelCat CPUE, LAGOS morphometry,
    │   (static spatial)    │   species composition, embeddings
    └──────────┬──────────┘
               │
    ┌──────────▼──────────┐
    │   Layer 2: Conditions │ ← Water temp, pressure, wind, moon,
    │   (dynamic temporal)  │   discharge, frontal activity, lag features
    └──────────┬──────────┘
               │
    ┌──────────▼──────────┐
    │   Layer 3: CPUE Model │ ← Hurdle: P(catch) × E(CPUE|catch)
    │   (catch rate pred)   │   Combines spatial + temporal
    └──────────┬──────────┘
               │
    ┌──────────▼──────────┐
    │   Layer 4: Confidence │ ← Coverage flags, OOD detection,
    │   (uncertainty)       │   conformal bounds
    └──────────┬──────────┘
               │
    ┌──────────▼──────────┐
    │   Composite Scorer    │ → fishing_score (0-100)
    │                       │ → confidence_level
    │                       │ → breakdown (catch_prob, cpue, trophy, conditions)
    │                       │ → explanation text
    └───────────────────────┘
```

### Future (v3 — with user feedback loop)
```
User catch reports ──► Online learning layer
                       │
                       ▼
                   Bayesian update to site prior
                   (shrink toward user data as n grows)

When n_user_reports > 20 for a location:
  site_prior = α × creelcat_prior + (1-α) × user_posterior
  where α decays as user data accumulates
```

---

## User Feedback Integration Plan

### Phase 1: Passive Collection
- After each trip, prompt: "How was fishing?" (1-5 stars)
- Optional: species, count, size, hours fished
- Store: (user_id, lat, lon, datetime, rating, catch_count, effort_hours, conditions_snapshot)

### Phase 2: Active Learning
- For locations with low confidence, offer incentives to report
- "Help improve predictions for Lake X — log your next trip"

### Phase 3: Bayesian Site Prior Update
```python
# Pseudo-code for updating site prior with user data
def update_site_prior(location, user_reports, creelcat_prior):
    n = len(user_reports)
    if n < 5:
        return creelcat_prior  # not enough data

    user_cpue = mean(r.catch_count / r.effort_hours for r in user_reports)

    # Bayesian shrinkage: more user data → more weight on user signal
    alpha = max(0.2, 1.0 - n / 50)  # floor at 20% CreelCat weight
    updated_cpue = alpha * creelcat_prior + (1 - alpha) * user_cpue

    return updated_cpue
```

### Data Schema for User Reports
```sql
CREATE TABLE catch_reports (
    id SERIAL PRIMARY KEY,
    user_id UUID NOT NULL,
    lat DECIMAL(9,6) NOT NULL,
    lon DECIMAL(9,6) NOT NULL,
    reported_at TIMESTAMP NOT NULL,
    trip_start TIMESTAMP,
    trip_end TIMESTAMP,
    effort_hours DECIMAL(4,1),
    species VARCHAR(50),
    catch_count INTEGER,
    kept_count INTEGER,
    largest_weight_lb DECIMAL(5,2),
    rating INTEGER CHECK (rating BETWEEN 1 AND 5),
    conditions_snapshot JSONB,  -- frozen model features at time of report
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_catch_reports_location ON catch_reports USING GIST (
    ST_SetSRID(ST_MakePoint(lon, lat), 4326)
);
CREATE INDEX idx_catch_reports_time ON catch_reports (reported_at);
```

---

## API Endpoints (Production)

### Core Prediction
```
POST /api/v2/predict
{
  "lat": 34.7,
  "lon": -86.5,
  "date": "2026-03-20",
  "species": "largemouth_bass"
}

Response:
{
  "fishing_score": 78,
  "confidence": "high",
  "breakdown": {
    "catch_probability": 0.92,
    "expected_cpue": 0.45,  // fish per hour
    "cpue_percentile": 0.72,
    "conditions_score": 0.81,
    "trophy_potential": 0.35
  },
  "conditions": {
    "water_temp_c": 18.2,
    "pressure_trend": "rising",
    "wind_kph": 12,
    "moon_phase": "waxing_gibbous",
    "solunar_rating": "good"
  },
  "explanation": "Good catch conditions. Water temp ideal for bass activity. Rising barometric pressure favors feeding. Moderate wind creating shore breaks.",
  "confidence_details": {
    "site_data_quality": "high",  // CreelCat + LAGOS matched
    "conditions_freshness": "live",  // USGS data < 1hr old
    "model_coverage": "in_distribution"
  }
}
```

### User Catch Report
```
POST /api/v2/catch-report
{
  "lat": 34.7,
  "lon": -86.5,
  "trip_date": "2026-03-20",
  "effort_hours": 4.5,
  "catch_count": 3,
  "species": "largemouth_bass",
  "rating": 4
}
```

### 7-Day Forecast
```
GET /api/v2/forecast?lat=34.7&lon=-86.5&species=largemouth_bass

Response:
{
  "location": "Lake Guntersville, AL",
  "forecast": [
    {"date": "2026-03-20", "score": 78, "confidence": "high"},
    {"date": "2026-03-21", "score": 65, "confidence": "high"},
    ...
  ],
  "best_day": "2026-03-20",
  "best_window": "6:00 AM - 10:00 AM (solunar major)"
}
```

---

## Deployment Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  Mobile App  │────▶│   API Server  │────▶│  Model Server│
│  (Expo/RN)   │◀────│  (Django/DRF) │◀────│  (inference) │
└─────────────┘     └──────┬───────┘     └──────┬──────┘
                           │                     │
                    ┌──────▼───────┐     ┌───────▼──────┐
                    │  PostgreSQL   │     │  Redis Cache  │
                    │  + PostGIS    │     │  (features,   │
                    │  (user data,  │     │   predictions) │
                    │   locations)  │     └──────────────┘
                    └──────────────┘

Data Refresh:
  - Cron job (hourly): fetch USGS + Open-Meteo → Redis
  - Cron job (daily): recompute lag features, solunar
  - Cron job (quarterly): refresh CreelCat, LAGOS, embeddings
  - On user report: async update to site prior
```

### Hosting Options (MVP)
| Option | Cost | Pros | Cons |
|--------|------|------|------|
| **Railway** | ~$5-20/mo | Easy deploy, free tier | Cold starts |
| **Fly.io** | ~$5-15/mo | Global edge, good free tier | Setup complexity |
| **Render** | ~$7-25/mo | Simple, auto-deploy | Cold starts on free |
| **DigitalOcean App Platform** | ~$12/mo | Reliable, managed DB | More expensive |

Model file size: ~50MB (CatBoost ensemble) — fits in any container.

---

## What Would Make Users Say "I Need This"

1. **"It told me to fish Lake X on Thursday instead of Saturday — caught my PB"**
   → Accurate day-to-day differentiation within a location

2. **"It found me a lake I'd never heard of that was on fire"**
   → Discovery of underrated locations via high CPUE + good conditions

3. **"The confidence meter said 'low' and sure enough, it was dead"**
   → Honest uncertainty builds trust

4. **"The 7-day forecast let me plan my vacation around the best window"**
   → Forecast capability (weather + solunar + seasonal)

5. **"After logging 20 trips, it learned my spots perfectly"**
   → Personalization via user feedback loop

---

## t-PatchGNN / Advanced Architecture Notes

**Where it fits:** Layer 2 (Conditions) — forecasting environmental state from irregular USGS sensor streams.

**Why it matters:** USGS data arrives irregularly (some sensors report every 15 min, others daily, some have gaps). t-PatchGNN handles this natively without imputation.

**Implementation priority:** AFTER the 4-layer system works with CatBoost. The gain from t-PatchGNN is in better environmental state forecasting, not in the fish prediction itself. Estimated lift: +5-10% on temporal R².

**Practical alternative:** For MVP, use simple lag feature engineering (which we already do) — 3d/7d rolling means, trends, and change rates from the most recent USGS readings. This captures 80% of the value of a proper irregular time series model.
