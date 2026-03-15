# CASTLINE — Conditions & Alerts for Sport fishing, Tides, Limnology, and INshore Environments

## Complete Build Architecture & Plan

> A conditions-change alerting product for anglers, built on real-time public environmental data and temporal deep learning. Not a social network. Not a bite score. An event-level intelligence system that tells you **what's about to change and why.**

---

## Table of Contents

1. [Product Vision & Positioning](#1-product-vision--positioning)
2. [Data Sources & Ingestion Architecture](#2-data-sources--ingestion-architecture)
3. [ML/AI Architecture](#3-mlai-architecture)
4. [Backend Infrastructure](#4-backend-infrastructure)
5. [Frontend & Mobile App](#5-frontend--mobile-app)
6. [Authentication & User System](#6-authentication--user-system)
7. [Validation & Research Protocol](#7-validation--research-protocol)
8. [Deployment & DevOps](#8-deployment--devops)
9. [Cost Estimates](#9-cost-estimates)
10. [Build Phases & Timeline](#10-build-phases--timeline)

---

## 1. Product Vision & Positioning

### What We Are
A **conditions-change alerting product** for anglers. We answer one question better than anyone:

> "What are the environmental conditions doing right now at my fishing spots, what's about to change, and what does that mean for the fish?"

### What We Are NOT
- Not a social network (Fishbrain owns that)
- Not a spot-sharing platform (ANGLR, Fishbrain own that)
- Not a generic bite score (BassForecast does that)
- Not a gear shop

### Core Differentiator
**Event-level alerts grounded in real-time environmental physics.** Not "today is 7/10" but:

- "Flow at this reach drops into the fishable band at 6:40 AM"
- "Upstream rain will dirty this stretch in ~3.5 hours — fish now or wait until tomorrow"
- "Water temp just crossed 62°F at [your spot] — optimal smallmouth window opening, pressure falling, expect 4-6 hour window"
- "Lake turnover detected at [your lake] — fish are resettling, target 15-20ft near structure"

### Target Users
Serious recreational anglers who fish 20+ days/year, guides, tournament anglers. People who already check weather, water levels, and moon phase before going out — but do it across 5 different apps and websites. We consolidate the intelligence into one predictive system.

### Monetization (v1)
- **Free tier**: Current conditions dashboard for up to 3 saved spots, basic weather overlay
- **Premium ($8-10/month, $70-80/year)**: Unlimited spots, event-level alerts, predictive timelines, upstream precipitation tracking, historical pattern analysis, species-specific forecasts, SHAP-based explanations

### Coexistence Strategy
Users will likely **keep Fishbrain** for spots and community, and pay us separately for conditions intelligence. We are a complement, not a replacement. This is a much easier wedge than trying to replace their whole workflow.

---

## 2. Data Sources & Ingestion Architecture

### Overview
All data sources are **free and public**. No paid data dependencies.

### 2.1 Primary Data Sources

#### USGS Water Data (Freshwater — THE core data source)
- **What**: Water temperature, discharge (flow rate), gage height, turbidity, specific conductance
- **Coverage**: 10,000+ monitoring locations across the US
- **Update frequency**: Every 15 minutes (instantaneous values)
- **API**: REST API at `https://waterservices.usgs.gov/` (legacy) and new OGC-compliant endpoints at `https://api.waterdata.usgs.gov/`
- **Python library**: `dataretrieval` (official USGS package, `pip install dataretrieval`)
  - New `waterdata` module (Jan 2025) provides access to modernized APIs
  - Supports instantaneous values, daily values, site metadata
  - Free API key available for higher rate limits
- **Key parameter codes**:
  - `00010` — Water temperature (°C)
  - `00060` — Discharge (ft³/s)
  - `00065` — Gage height (ft)
  - `63680` — Turbidity (FNU)
  - `00095` — Specific conductance (µS/cm)
  - `00300` — Dissolved oxygen (mg/L)
- **Historical data**: Available from October 2007 to present via instantaneous values API; daily values go back decades
- **Rate limits**: Default 1,000 requests/hour per API key; contributing users get higher limits

```python
# Example: Get real-time water temp and discharge
from dataretrieval import waterdata

df, metadata = waterdata.get_continuous(
    monitoring_location_id='USGS-01646500',
    parameter_code=['00010', '00060'],
    time='2025-01-01/..'
)
```

#### NEXRAD Dual-Polarization Radar (Precipitation — critical for upstream rain alerts)
- **What**: Reflectivity, velocity, spectrum width, differential reflectivity (ZDR), correlation coefficient (CC), specific differential phase (KDP)
- **Coverage**: 160 radar sites across CONUS
- **Update frequency**: ~5-minute volume scans
- **Access**: Free on AWS S3 (no AWS account required for reads)
  - **Real-time Level II**: `s3://unidata-nexrad-level2/` (new bucket as of 2025)
  - **Level III (processed products)**: `s3://unidata-nexrad-level3/`
  - SNS notifications available for new data: `arn:aws:sns:us-east-1:684042711724:NewNEXRADLevel2Archive`
- **Python libraries**:
  - `Py-ART` (ARM Radar Toolkit) — the standard for reading and processing NEXRAD data
  - `nexradaws` — query and download from S3
  - `MetPy` — read Level II files, meteorological calculations
- **Key products for fishing**:
  - Reflectivity → precipitation detection and intensity
  - Hydrometeor classification (Level III N0H) → rain vs. hail vs. snow
  - Precipitation totals (Level III OHA, DTA) → accumulated rainfall
  - We DON'T need velocity/rotation products — that's severe weather, not fishing

```python
# Example: Access real-time NEXRAD from AWS S3
import boto3, botocore
from botocore.client import Config
from metpy.io import Level2File

s3 = boto3.resource('s3', config=Config(signature_version=botocore.UNSIGNED))
bucket = s3.Bucket('unidata-nexrad-level2')
# Files organized as: year/month/date/radarsite/SITE_YYYYMMDD_HHMMSS_V06
```

#### NOAA Tides & Currents (Saltwater/Inshore)
- **What**: Water level, tidal predictions, current speed/direction, water temperature, salinity, barometric pressure
- **Coverage**: 200+ stations, primarily coastal
- **Update frequency**: 6-minute intervals
- **API**: `https://api.tidesandcurrents.noaa.gov/api/prod/datagetter`
- **Format**: JSON, CSV, XML
- **Free, no auth required**
- **Key products**: 
  - Real-time water levels (datum: MLLW, NAVD)
  - Harmonic tide predictions (future tide times/heights)
  - Currents (speed and direction at stations with current meters)

```
# Example API call
https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?
  date=latest&station=8518750&product=water_level&datum=MLLW
  &units=english&time_zone=lst_lte&application=castline&format=json
```

#### NOAA National Data Buoy Center (NDBC) — Offshore/Nearshore
- **What**: Wind speed/direction, wave height/period/direction, water temperature, air temperature, barometric pressure
- **Coverage**: 300+ ocean buoys
- **Update frequency**: 10-60 minute intervals depending on buoy
- **API**: REST at `https://www.ndbc.noaa.gov/data/realtime2/`
- **Format**: Text files, one per station per data type
- **Historical data**: Decades of observations

#### Weather Forecast Models (NWS/NOAA)
- **GFS** (Global Forecast System): 0.25° resolution, 16-day forecast, 6-hourly updates
  - Access via NOMADS: `https://nomads.ncep.noaa.gov/`
  - GRIB2 format, readable with `cfgrib` + `xarray`
- **HRRR** (High-Resolution Rapid Refresh): 3km resolution, 18-hour forecast, hourly updates
  - Best for short-range precipitation and wind at fishing-spot scale
  - Available on AWS: `s3://noaa-hrrr-bdp-pds/`
  - Python: `herbie` library for easy HRRR access
- **Key variables**: 2m temperature, 10m wind speed/direction, precipitation rate, total cloud cover, MSLP (barometric pressure)

```python
# Example: Get HRRR forecast data
from herbie import Herbie

H = Herbie("2025-03-15", model="hrrr", fxx=6)  # 6-hour forecast
ds = H.xarray("TMP:2 m")  # 2m temperature
```

#### Astronomical Data (Moon/Sun — computed, no API needed)
- **What**: Moon phase, moonrise/set, sunrise/set, solar/lunar positions
- **Library**: `astropy` or `ephem` (Python) — compute locally, no API calls
- **Solunar theory**: Major/minor feeding periods computed from moon transit times
- This is the baseline that every other app uses — we include it for completeness but our value-add is everything ELSE

#### Phone Barometric Pressure (Crowdsourced from users)
- **What**: Atmospheric pressure from device barometer sensors
- **How**: 
  - iOS: `CMAltimeter` API provides relative altitude and pressure
  - Android: `SensorManager.getDefaultSensor(Sensor.TYPE_PRESSURE)`
- **Collection**: Background sampling every 5-15 minutes when user has app installed
- **Value**: Creates hyperlocal barometric pressure network denser than NWS stations
- **Privacy**: No location transmitted with pressure readings unless user opts in

#### Satellite Sea Surface Temperature (Offshore)
- **What**: Sea surface temperature at ~1km resolution
- **Source**: NOAA CoastWatch ERDDAP servers
- **API**: `https://coastwatch.pfeg.noaa.gov/erddap/`
- **Products**: 
  - Multi-scale Ultra-high Resolution SST (MUR) — 1km, daily
  - GOES-16 SST — near-real-time but lower resolution
- **Python**: `erddapy` library for ERDDAP access
- **Key use**: Identifying temperature breaks, upwelling zones, warm/cold eddies for offshore fishing

```python
from erddapy import ERDDAP

e = ERDDAP(server="https://coastwatch.pfeg.noaa.gov/erddap", protocol="tabledap")
e.dataset_id = "jplMURSST41"
e.constraints = {
    "time>=": "2025-03-14T00:00:00Z",
    "latitude>=": 25.0, "latitude<=": 30.0,
    "longitude>=": -90.0, "longitude<=": -80.0,
}
ds = e.to_xarray()
```

### 2.2 Ingestion Architecture

```
                        ┌─────────────────────────┐
                        │   Ingestion Scheduler    │
                        │   (Python + APScheduler) │
                        └──────────┬──────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                     │
     ┌────────▼────────┐ ┌────────▼────────┐ ┌─────────▼────────┐
     │  USGS Poller    │ │ NEXRAD Poller   │ │  NOAA Poller     │
     │  (every 5 min)  │ │ (every 5 min)   │ │  (every 6 min)   │
     │                 │ │ S3 notification  │ │  Tides/NDBC/NWS  │
     └────────┬────────┘ └────────┬────────┘ └─────────┬────────┘
              │                    │                     │
              └────────────────────┼────────────────────┘
                                   │
                          ┌────────▼────────┐
                          │   Message Queue  │
                          │   (Redis Streams │
                          │    or Valkey)    │
                          └────────┬────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    │              │               │
           ┌────────▼──────┐ ┌────▼─────┐ ┌──────▼──────┐
           │ Feature Store │ │ TimescaleDB│ │ Alert Engine│
           │  (computed    │ │ (raw time │ │ (condition  │
           │   features)   │ │  series)  │ │  evaluator) │
           └───────────────┘ └──────────┘ └─────────────┘
```

#### Message Queue: Redis (or Valkey)
- **Why Redis over Kafka**: At our scale (thousands of fishing spots, not millions of IoT devices), Redis Streams provides ordered, persistent message queuing with consumer groups — all the functionality we need without Kafka's operational complexity
- **Valkey** is the open-source Redis fork (post-license change) — fully compatible, truly free
- Use Redis Streams for ingestion pipeline, Redis pub/sub for real-time alert delivery to connected clients

#### Time-Series Storage: TimescaleDB
- **Why**: PostgreSQL extension for time-series data. We get full SQL, JSONB support, continuous aggregates, compression, AND time-series optimizations — all in one database
- **Free and open source** (Apache 2.0 for Community Edition)
- Compression ratios of 90-95% on time-series data
- Continuous aggregates for pre-computing hourly/daily rollups
- Hyperfunctions for time-weighted averages, interpolation, gap-filling
- We store ALL raw sensor readings here — this becomes our training data corpus

```sql
-- Hypertable for USGS observations
CREATE TABLE usgs_observations (
    time        TIMESTAMPTZ NOT NULL,
    site_id     TEXT NOT NULL,
    param_code  TEXT NOT NULL,
    value       DOUBLE PRECISION,
    qualifiers  TEXT
);
SELECT create_hypertable('usgs_observations', by_range('time'));

-- Continuous aggregate for hourly rollups
CREATE MATERIALIZED VIEW usgs_hourly
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time) AS bucket,
    site_id, param_code,
    avg(value) AS avg_value,
    min(value) AS min_value,
    max(value) AS max_value,
    last(value, time) AS latest_value
FROM usgs_observations
GROUP BY bucket, site_id, param_code;
```

#### Feature Store: Redis + PostgreSQL
- Computed features (rate-of-change, moving averages, derived metrics) stored in Redis for low-latency serving to the ML model
- Periodic snapshots persisted to PostgreSQL for training data assembly
- Features are computed by worker processes consuming from Redis Streams

### 2.3 Derived Features (computed from raw data)

These are the **engineered features** that go into the ML model. This is where domain knowledge lives:

**Water temperature features:**
- Current absolute temp (°C)
- Rate of change over last 1h, 3h, 6h, 12h, 24h
- Delta from 7-day rolling mean
- Position relative to species-specific optimal range (e.g., smallmouth: 60-72°F)
- Daily min/max and timing of each
- Days since last significant temp change (>2°C/24h)

**Discharge/flow features:**
- Current flow (cfs)
- Rate of change over 1h, 3h, 6h, 12h, 24h
- Percentile rank vs. historical distribution for this date (using USGS statistics API)
- Binary: rising/stable/falling classification
- Hours since last significant rise (>20% increase)
- Upstream precipitation lag estimate (requires watershed topology — see GNN section)

**Barometric pressure features:**
- Current pressure (mb)
- Rate of change over 1h, 3h, 6h
- Classification: rapidly falling (<-2mb/3h), falling, stable, rising, rapidly rising
- Hours since last front passage (detected by pressure reversal)
- Pressure relative to 30-day mean

**Precipitation features (from NEXRAD):**
- Current precipitation rate at fishing spot
- Accumulated precipitation in last 1h, 3h, 6h, 12h, 24h
- Upstream accumulated precipitation (weighted by watershed proximity and flow time)
- Hours since last rainfall event ended
- Predicted precipitation from HRRR model for next 6-18 hours

**Tidal features (saltwater only):**
- Current tide state (hours since/until high/low)
- Current direction and rate (flooding/ebbing/slack)
- Tidal range for current cycle (spring vs. neap)
- Current speed at nearest current station

**Astronomical features:**
- Moon phase (0-1 continuous)
- Moon altitude (above/below horizon)
- Solar altitude
- Minutes until/since sunrise/sunset
- Solunar major/minor period indicators

**Temporal features:**
- Hour of day (cyclical encoding: sin/cos)
- Day of year (cyclical encoding)
- Weekend/weekday flag
- Season

---

## 3. ML/AI Architecture

### 3.1 Architecture Overview

```
┌──────────────────────────────────────────────────────────┐
│                    ML PREDICTION STACK                     │
│                                                           │
│  ┌─────────────────────────────────────────────────────┐ │
│  │  Layer 5: Calibration Layer (per-spot adjustment)   │ │
│  │  Updated incrementally with each user catch report  │ │
│  └──────────────────────────┬──────────────────────────┘ │
│  ┌──────────────────────────▼──────────────────────────┐ │
│  │  Layer 4: Event Detection & Alert Generation        │ │
│  │  Rule-based + anomaly detection on streaming data   │ │
│  └──────────────────────────┬──────────────────────────┘ │
│  ┌──────────────────────────▼──────────────────────────┐ │
│  │  Layer 3: Temporal Fusion Transformer               │ │
│  │  Multi-horizon prediction (1h to 48h)               │ │
│  │  Per-species, interpretable attention weights       │ │
│  └──────────────────────────┬──────────────────────────┘ │
│  ┌──────────────────────────▼──────────────────────────┐ │
│  │  Layer 2: Spatial Interpolation (GNN)               │ │
│  │  Fills gaps for spots without direct sensor         │ │
│  │  Uses watershed topology as graph structure         │ │
│  └──────────────────────────┬──────────────────────────┘ │
│  ┌──────────────────────────▼──────────────────────────┐ │
│  │  Layer 1: Feature Engineering Pipeline              │ │
│  │  Raw sensor data → computed features                │ │
│  └─────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────┘
```

### 3.2 Layer 1: Feature Engineering Pipeline

**Framework**: Pure Python with NumPy/Pandas
**Runs**: As streaming workers consuming from Redis Streams

This layer transforms raw sensor readings into the derived features listed in Section 2.3. It runs continuously, producing updated feature vectors every time new data arrives (every 5-15 minutes depending on source).

Key implementation detail: features must be computed **causally** — only using data available at the time of prediction. No future leakage. This sounds obvious but is the #1 source of bugs in time-series ML.

```python
# Feature computation worker (simplified)
import redis
import pandas as pd
from dataclasses import dataclass

@dataclass
class SpotFeatures:
    """Feature vector for one fishing spot at one point in time"""
    water_temp_c: float
    water_temp_1h_delta: float
    water_temp_6h_delta: float
    water_temp_species_distance: float  # distance from optimal range
    discharge_cfs: float
    discharge_1h_pct_change: float
    discharge_6h_pct_change: float
    discharge_historical_percentile: float
    discharge_trend: str  # 'rising', 'stable', 'falling'
    pressure_mb: float
    pressure_3h_delta: float
    pressure_trend: str
    precip_1h_mm: float
    precip_upstream_6h_mm: float
    hours_since_rain: float
    moon_phase: float
    moon_altitude: float
    solar_altitude: float
    hour_sin: float
    hour_cos: float
    day_of_year_sin: float
    day_of_year_cos: float
    # ... ~40-60 total features
```

### 3.3 Layer 2: Spatial Interpolation via Graph Neural Network

**Purpose**: Most fishing spots don't have a USGS gauge directly on them. We need to interpolate environmental conditions from the sensor network to arbitrary fishing locations.

**Graph structure**: Nodes are USGS monitoring stations + user fishing spots. Edges represent hydrological connectivity (upstream/downstream relationships, same lake, coastal proximity). Edge weights encode distance and estimated flow propagation time.

**Why a GNN and not simple IDW interpolation**: 
- Water doesn't flow in straight lines — a station 5km upstream on the same river is more informative than a station 2km away on a different watershed
- The GNN learns that upstream precipitation at station A propagates to fishing spot B with a ~4 hour delay — this is the "upstream rain alert" feature
- For lakes, the GNN learns that a gauge at the inlet affects the lake differently than a gauge at the outlet

**Framework**: PyTorch Geometric (`torch_geometric`)
- Open source, MIT license
- Excellent support for heterogeneous graphs, message passing, and temporal graph learning

**Architecture**: 
- Graph Attention Network (GAT) variant
- Node features: current environmental conditions at each station
- Edge features: distance, flow direction, estimated propagation time
- Output: predicted environmental conditions at all nodes (including fishing spots without direct sensors)

```python
import torch
from torch_geometric.nn import GATConv

class WatershedGNN(torch.nn.Module):
    def __init__(self, in_channels, hidden_channels, out_channels, heads=4):
        super().__init__()
        self.conv1 = GATConv(in_channels, hidden_channels, heads=heads, 
                             edge_dim=3)  # edge features: distance, flow_dir, prop_time
        self.conv2 = GATConv(hidden_channels * heads, hidden_channels, heads=heads,
                             edge_dim=3)
        self.conv3 = GATConv(hidden_channels * heads, out_channels, heads=1,
                             edge_dim=3, concat=False)
    
    def forward(self, x, edge_index, edge_attr):
        x = self.conv1(x, edge_index, edge_attr).relu()
        x = self.conv2(x, edge_index, edge_attr).relu()
        x = self.conv3(x, edge_index, edge_attr)
        return x
```

**Building the graph**: 
- Use the USGS National Hydrography Dataset (NHDPlus) for watershed topology — it's free, provides stream connectivity, flow direction, and catchment boundaries
- Python library: `pynhd` for programmatic access to NHDPlus data
- Initially build graphs for the top 100 most popular fishing waters, expand as users add spots

### 3.4 Layer 3: Temporal Fusion Transformer

**Purpose**: The core prediction model. Takes the full environmental feature history and produces multi-horizon forecasts of fishing conditions.

**Why TFT specifically**:
1. Handles mixed input types natively: static covariates (lake type, species, watershed ID), known future inputs (tide predictions, moon phase, weather forecasts), and observed past inputs (water temp history, discharge trends)
2. Built-in interpretable attention — tells you WHICH variables drove each prediction (critical for the "here's why" explanations in the UI)
3. Quantile output — produces prediction intervals, not just point estimates ("70-85% bite probability" is more honest and useful than "78%")
4. Proven performance on multi-horizon time-series tasks

**Framework**: `pytorch-forecasting` library
- Open source, MIT license
- Built on PyTorch Lightning
- Provides `TemporalFusionTransformer` class with `from_dataset()` convenience method
- Integrated hyperparameter optimization via Optuna
- SHAP-compatible for feature importance

**Configuration**:
```python
from pytorch_forecasting import TemporalFusionTransformer, TimeSeriesDataSet

# Define the time series dataset
training = TimeSeriesDataSet(
    data=training_df,
    time_idx="time_idx",
    target="catch_rate",  # or binary: "fish_caught"
    group_ids=["spot_id", "species_id"],
    
    # Look back 48 hours of history
    max_encoder_length=192,  # 48h at 15-min intervals
    
    # Predict next 48 hours
    max_prediction_length=192,
    
    # Static covariates (don't change over time)
    static_categoricals=["water_body_type", "species_group", "huc8_watershed"],
    static_reals=["latitude", "longitude", "elevation", "avg_depth"],
    
    # Known future inputs (we know these ahead of time)
    time_varying_known_categoricals=["tide_state", "is_weekend"],
    time_varying_known_reals=[
        "moon_phase", "moon_altitude", "solar_altitude",
        "hour_sin", "hour_cos", "day_of_year_sin", "day_of_year_cos",
        "predicted_tide_height", "predicted_precip_rate",
        "predicted_cloud_cover", "predicted_wind_speed",
        "predicted_pressure"
    ],
    
    # Observed past inputs (only available historically, not in future)
    time_varying_unknown_reals=[
        "water_temp_c", "water_temp_1h_delta", "water_temp_6h_delta",
        "discharge_cfs", "discharge_1h_pct_change", "discharge_historical_percentile",
        "pressure_mb", "pressure_3h_delta",
        "precip_1h_mm", "precip_upstream_6h_mm",
        "hours_since_rain", "turbidity_fnu",
        "dissolved_oxygen_mgL"
    ],
    
    target_normalizer=GroupNormalizer(groups=["spot_id"]),
    add_relative_time_idx=True,
    add_encoder_length=True,
    allow_missing_timesteps=True,
)

# Build the model
tft = TemporalFusionTransformer.from_dataset(
    training,
    hidden_size=64,
    attention_head_size=4,
    dropout=0.1,
    hidden_continuous_size=32,
    loss=QuantileLoss(),  # Produces prediction intervals
    learning_rate=0.01,
    reduce_on_plateau_patience=4,
    log_interval=10,
)
```

**Training data assembly**:
- Phase 1 (pre-launch): Historical USGS data + B.A.S.S./MLF tournament results + state creel surveys. Backfill environmental features for each tournament day/site.
- Phase 2 (post-launch): User catch reports provide ongoing labeled data. Each report = environmental conditions at that spot/time + binary catch/no-catch or catch count.
- Training runs on GPU (see infrastructure section). Retrain weekly with new data.

**Attention-based explanations**:
```python
# Extract feature importances from trained model
interpretation = tft.interpret_output(output, reduction="sum")
# Returns: attention weights per feature, temporal attention patterns
# These power the "here's why" explanations in the UI:
# "Water temperature (42% importance) crossed into optimal range 3 hours ago,
#  combined with falling barometric pressure (28% importance) and 
#  rising discharge from overnight rain (18% importance)"
```

### 3.5 Layer 4: Event Detection & Alert Generation

**Purpose**: Detect significant changes in conditions and generate actionable alerts.

**Architecture**: Hybrid rule-based + learned anomaly detection running on streaming data.

**Rule-based triggers** (domain knowledge encoded directly):
```python
ALERT_RULES = {
    "temp_optimal_entry": {
        "condition": lambda f: (
            f.water_temp_species_distance < 1.0 and 
            f.water_temp_1h_delta > 0 and
            prev.water_temp_species_distance >= 1.0
        ),
        "message": "Water temp at {spot} just entered optimal range for {species}",
        "priority": "high"
    },
    "upstream_rain_incoming": {
        "condition": lambda f: (
            f.precip_upstream_6h_mm > 10 and
            f.discharge_trend == "stable"  # hasn't arrived yet
        ),
        "message": "Heavy rain upstream — expect turbidity increase at {spot} in ~{eta}h",
        "priority": "medium"
    },
    "pressure_drop_feeding_window": {
        "condition": lambda f: (
            f.pressure_3h_delta < -3.0 and
            f.water_temp_species_distance < 3.0
        ),
        "message": "Rapid pressure drop + good water temp — feeding window opening at {spot}",
        "priority": "high"
    },
    "flow_entering_fishable": {
        "condition": lambda f: (
            f.discharge_historical_percentile < 75 and
            prev.discharge_historical_percentile >= 75
        ),
        "message": "Flow at {spot} dropping into fishable range",
        "priority": "high"
    },
}
```

**Learned anomaly detection**: 
- The TFT produces prediction intervals. When actual conditions deviate significantly from prediction, flag as an anomaly worth alerting on.
- Example: model predicted stable discharge, but actual discharge is rising rapidly → likely unexpected upstream event → alert user.

**Alert delivery**:
- Push notifications via Firebase Cloud Messaging (FCM) — free tier: unlimited messages
- In-app notification feed
- Optional SMS via Twilio (paid, but low volume — only for high-priority alerts)

### 3.6 Layer 5: Calibration Layer

**Purpose**: Adjust predictions for each specific fishing spot based on accumulated user catch reports.

**Architecture**: Lightweight per-spot bias correction model.

Initially, predictions come entirely from the general TFT model. As users log catches at a spot, we accumulate (conditions, outcome) pairs. A simple logistic regression or small neural network learns a spot-specific adjustment: "at this particular spot, the general model overpredicts by 10% when discharge is high, and underpredicts by 15% during evening hours."

This is the **flywheel**: more users → more catch reports → better calibration → better predictions → more users.

```python
# Per-spot calibration (simplified)
class SpotCalibrator:
    def __init__(self, spot_id: str):
        self.spot_id = spot_id
        self.model = LogisticRegression()  # upgrades to neural net with more data
        self.samples = []
    
    def add_observation(self, features: SpotFeatures, caught_fish: bool):
        self.samples.append((features, caught_fish))
        if len(self.samples) >= 20:  # minimum for meaningful calibration
            self.retrain()
    
    def adjust(self, base_prediction: float, features: SpotFeatures) -> float:
        if len(self.samples) < 20:
            return base_prediction  # not enough data, return unadjusted
        adjustment = self.model.predict_proba([features.to_array()])[0][1]
        # Blend base prediction with local calibration
        alpha = min(len(self.samples) / 200, 0.5)  # max 50% local weight
        return (1 - alpha) * base_prediction + alpha * adjustment
```

### 3.7 Training Infrastructure

**Training hardware**: 
- GPU required for TFT training. Options:
  - **Lambda Cloud**: A100 instances at ~$1.10/hr (pay as you go, no commitment)
  - **Vast.ai**: Consumer GPUs at $0.20-0.50/hr (cheapest option)
  - **Google Colab Pro+**: $50/month, A100 access (good for prototyping)
- Estimated training time: ~2-4 hours per full retrain on A100 with initial dataset
- Weekly retrains as new data accumulates

**ML workflow**:
- **Experiment tracking**: MLflow (open source, self-hosted)
- **Hyperparameter optimization**: Optuna (integrated with pytorch-forecasting)
- **Model registry**: MLflow Model Registry
- **Model serving**: TorchServe or custom FastAPI endpoint with ONNX export for inference

---

## 4. Backend Infrastructure

### 4.1 Technology Stack

| Component | Technology | License | Why |
|-----------|-----------|---------|-----|
| **API Server** | FastAPI (Python) | MIT | Async, fast, auto-generates OpenAPI docs, great for ML serving |
| **Database** | PostgreSQL 16 + TimescaleDB | Apache 2.0 / Timescale License | Time-series optimized, full SQL, compression |
| **Cache / Queue** | Valkey (Redis fork) | BSD-3 | Message queue for ingestion, feature cache, pub/sub for real-time |
| **Task Queue** | Celery + Valkey broker | BSD-3 | Scheduled ingestion jobs, ML inference tasks, alert processing |
| **Search** | PostgreSQL full-text + PostGIS | PostgreSQL License | Geospatial queries for finding nearby spots/gauges, no need for Elasticsearch |
| **Object Storage** | MinIO (self-hosted) | AGPL-3.0 | Model artifacts, NEXRAD data cache, user uploads. S3-compatible |
| **Reverse Proxy** | Caddy | Apache 2.0 | Automatic HTTPS, simpler than nginx, great for small teams |

### 4.2 API Design

```
/api/v1/
├── /auth/
│   ├── POST /register
│   ├── POST /login
│   ├── POST /refresh
│   └── POST /logout
│
├── /spots/
│   ├── GET /              # list user's saved spots
│   ├── POST /             # add a fishing spot
│   ├── GET /:id           # get spot details + current conditions
│   ├── GET /:id/conditions # real-time conditions dashboard
│   ├── GET /:id/forecast  # 48-hour prediction timeline
│   ├── GET /:id/alerts    # active alerts for this spot
│   └── GET /:id/history   # historical conditions + catches
│
├── /catches/
│   ├── POST /             # log a catch (auto-fills conditions)
│   ├── GET /              # user's catch history
│   └── GET /:id           # catch detail with conditions snapshot
│
├── /alerts/
│   ├── GET /              # all active alerts across user's spots
│   ├── PUT /:id/settings  # alert preferences per spot
│   └── POST /device-token # register for push notifications
│
├── /species/
│   ├── GET /              # list species with optimal condition ranges
│   └── GET /:id/ranges    # species-specific environmental preferences
│
├── /gauges/
│   ├── GET /nearby        # find USGS gauges near a location
│   └── GET /:id/data      # raw gauge data (for power users)
│
└── /ws/
    └── /conditions/:spot_id  # WebSocket for real-time condition updates
```

### 4.3 Real-Time Updates

**WebSocket architecture**:
- FastAPI with `websockets` library
- When a user is viewing a spot's conditions page, the client opens a WebSocket to `/ws/conditions/:spot_id`
- Backend publishes condition updates to Valkey pub/sub channel `spot:{spot_id}:conditions`
- WebSocket handler subscribes to the channel and forwards to client
- Graceful degradation: if WebSocket fails, client falls back to polling every 30 seconds

---

## 5. Frontend & Mobile App

### 5.1 Technology Stack

| Component | Technology | License | Why |
|-----------|-----------|---------|-----|
| **Mobile Framework** | React Native | MIT | Cross-platform (iOS + Android), strong ecosystem, Dribbble-quality UI achievable |
| **State Management** | Zustand | MIT | Simpler than Redux, great for real-time data |
| **Maps** | MapLibre GL JS / react-native-maplibre-gl | BSD-3 | Free, open-source Mapbox GL fork. No API key costs. Self-host tiles with OpenMapTiles or use free tile providers |
| **Charts / Graphs** | Victory Native (for RN) or react-native-svg + d3 | MIT | Beautiful, customizable time-series charts |
| **Animations** | React Native Reanimated + Moti | MIT | Smooth 60fps animations for condition transitions |
| **Icons** | Lucide or Phosphor | MIT / MIT | Clean, consistent icon set |
| **Push Notifications** | Firebase Cloud Messaging | Free | Industry standard, free unlimited messages |
| **Background Barometer** | expo-sensors or react-native-sensors | MIT | Access device barometer for crowdsourced pressure data |

### 5.2 Key Screens

**1. My Spots (Home Screen)**
- List/grid of user's saved fishing spots
- Each card shows: spot name, current bite prediction (color-coded), key condition summary ("62°F, rising flow, pressure falling"), next alert preview
- Pull to refresh
- FAB to add new spot

**2. Spot Detail / Conditions Dashboard**
- Hero: large bite prediction gauge with confidence interval
- Below: real-time conditions cards (water temp, flow, pressure, tide, weather) each showing current value + trend arrow + sparkline
- Each card tappable → shows full time-series chart with 7-day history

**3. Prediction Timeline**
- 48-hour scrollable horizontal timeline
- Each hour block shows: predicted bite rating (color-coded bar), key condition icons, weather summary
- Tappable → shows detailed explanation ("Predicted: Good. Water temp in optimal range, stable flow, low wind. Declining at 2 PM due to rising pressure from incoming high.")
- This is the **"when should I go"** screen

**4. Alert Feed**
- Chronological list of alerts across all saved spots
- Each alert shows: time, spot name, alert type (color-coded by priority), explanation, and "Go Fish" CTA with optimal window
- Swipe to dismiss or snooze

**5. Upstream Rain Tracker**
- Map view centered on a fishing spot
- Shows NEXRAD precipitation overlaid upstream
- Animated precipitation propagation timeline: "This rain will reach your spot in approximately X hours"
- Real-time discharge graph showing current level and predicted rise

**6. Catch Logger**
- Minimalist: one tap to log "I caught a fish"
- Auto-fills: location (GPS), time, species (from user's target species setting), ALL environmental conditions at that moment
- Optional: photo, estimated size, bait/lure used
- Designed for speed — under 5 seconds to log a basic catch
- The photo triggers an optional AI fish species identifier (see below)

**7. Fish ID (bonus feature — the lightweight gamification)**
- Take a photo of your catch
- On-device ML model identifies species (using a fine-tuned MobileNet or EfficientNet on iNaturalist fish images — model runs on-device, no API call)
- Auto-suggests species for catch log
- Long-term: builds a personal "species collection" (this is the light gamification — no leaderboards yet, just personal collection)

### 5.3 Design Principles

- **Dark mode first**: Most anglers fish at dawn/dusk. Bright screens are annoying and spook fish.
- **Glanceable**: Key information visible without tapping. Spot cards show everything you need in a glance.
- **Opinionated**: Don't just show data — tell the user what to DO. "Go now" or "wait until Thursday."
- **Explanation over decoration**: Every prediction has a "why" button that shows which environmental factors drove it, with attention weights visualized as a simple bar chart.
- **Offline capable**: Cache last-known conditions and predictions for when users are on the water without signal. Use React Native's AsyncStorage or MMKV for local cache.

### 5.4 Design Inspiration
Look at these Dribbble/design references for the aesthetic:
- Weather apps: **Mercury Weather**, **Today Weather**, **Windy** (for the map overlays)
- Data dashboards: **Linear** (clean, dark, information-dense), **Monzo** (beautiful data cards)
- Outdoor apps: **Strava** (activity logging UX), **AllTrails** (spot discovery)
- Avoid: the cluttered, icon-heavy, 2010s-era fishing app aesthetic that every competitor uses

---

## 6. Authentication & User System

### Custom auth — no paid dependencies.

**Technology**: 
- `PyJWT` for token generation/validation (MIT license)
- `passlib` + `bcrypt` for password hashing (BSD license)
- PostgreSQL for user storage

**Architecture**: JWT-based auth with access + refresh token pattern.

```python
# auth/tokens.py
import jwt
from datetime import datetime, timedelta
from passlib.hash import bcrypt

SECRET_KEY = os.environ["JWT_SECRET"]  # Generate with: openssl rand -hex 32
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE = timedelta(minutes=30)
REFRESH_TOKEN_EXPIRE = timedelta(days=30)

def create_access_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "type": "access",
        "exp": datetime.utcnow() + ACCESS_TOKEN_EXPIRE,
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def create_refresh_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "type": "refresh",
        "exp": datetime.utcnow() + REFRESH_TOKEN_EXPIRE,
        "iat": datetime.utcnow(),
        "jti": str(uuid4()),  # unique token ID for revocation
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def hash_password(password: str) -> str:
    return bcrypt.hash(password)

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.verify(password, hashed)
```

**User schema**:
```sql
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    display_name TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    premium_until TIMESTAMPTZ,  -- NULL = free tier
    settings JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE refresh_tokens (
    jti UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT now()
);
```

**OAuth (optional, for convenience)**:
- "Sign in with Apple" and "Sign in with Google" using their free SDKs
- Implement as additional auth providers alongside email/password
- No third-party auth service (no Auth0, no Firebase Auth)

**Rate limiting**:
- Use Valkey for rate limit counters
- Free tier: 100 API calls/hour
- Premium: 1000 API calls/hour
- Implement as FastAPI middleware

---

## 7. Validation & Research Protocol

### Phase 1: Historical Backtesting (Week 1-3)

**Objective**: Determine if environmental variables predict fishing success better than baseline (moon + weather).

**Data**:
1. **B.A.S.S. Elite Series + MLF Bass Pro Tour results** (2014-2025)
   - ~50-70 tournament events per year
   - Public data: date, lake, daily weights per competitor
   - Scrape from bassmaster.com and majorleaguefishing.com

2. **USGS historical data** for each tournament lake
   - Use `dataretrieval` library to pull historical instantaneous values
   - Parameters: water temp (00010), discharge (00060), gage height (00065)
   - Pull 7 days of history before each tournament day

3. **Historical weather** from Iowa Environmental Mesonet (IEM)
   - Free archive of ASOS/AWOS station data
   - Barometric pressure, wind, cloud cover, precipitation

**Methodology**:
1. For each tournament day, assemble feature vector: environmental conditions + astronomical/weather baseline
2. Target variable: median weight caught per competitor (controls for lake-specific fish sizes)
3. Train TFT on 2014-2022 data, evaluate on 2023-2025
4. **Key comparison**: Model with full environmental features vs. model with only astronomical/weather features (the baseline that existing apps use)
5. Report: RMSE, MAE, R², and — critically — **decile analysis**: do the top-decile predicted days actually produce better fishing?

**Go/No-Go criteria**:
- If full model improves R² by <5% over baseline → thesis is weak, reconsider
- If full model improves R² by 5-15% → thesis is viable, proceed with caution
- If full model improves R² by >15% → strong signal, proceed aggressively
- If decile spread (top-predicted vs bottom-predicted) is <1.5x → predictions won't feel meaningful to users
- If decile spread is >2x → predictions will feel magical

### Phase 2: Prospective Pilot (Month 2-4)

**Objective**: Validate real-time predictions against actual fishing outcomes.

**Method**:
1. Recruit 50-100 serious anglers (guides preferred — they fish nearly every day)
2. Each day, the system pre-registers predictions for their primary fishing spots
3. Anglers log trips with standardized effort data: hours fished, species targeted, number caught, location
4. After 60-90 days, analyze: "When the system predicted top-quintile conditions, what was the actual CPUE? Bottom quintile?"

**Recruitment channels**:
- Local fishing guides (direct outreach — they're easy to find and motivated to try new tools)
- Bass fishing forums (TournamentFishingForum, BassFishingHQ)
- Reddit (r/bassfishing, r/flyfishing, r/kayakfishing)
- Offer: free lifetime premium in exchange for 3 months of standardized logging

### Phase 3: Head-to-Head (Month 4-6)

**Objective**: Demonstrate superiority over existing forecasts.

**Method**: Same pilot group checks both Castline forecast and Fishbrain BiteTime forecast. Log both ratings alongside actual fishing outcomes. On days where the two forecasts disagree, which one was right more often?

This produces the marketing headline: "On days where our forecast disagreed with competitors, anglers who followed our recommendation caught X% more fish per hour."

---

## 8. Deployment & DevOps

### 8.1 Infrastructure

**Self-hosted on a single powerful VPS to start.** No Kubernetes, no microservices, no AWS at launch.

Recommended: **Hetzner dedicated server** (best price/performance for compute-heavy workloads)
- AX102: AMD Ryzen 9 7950X, 128GB RAM, 2x 1.92TB NVMe — ~$90/month
- This runs: PostgreSQL + TimescaleDB, Valkey, FastAPI, Celery workers, Caddy, MinIO, MLflow
- For an early-stage product with <10,000 users, this is more than sufficient

**Why not cloud**: At this stage, a single powerful dedicated server is 5-10x cheaper than equivalent cloud resources, simpler to manage, and sufficient for the workload. Move to cloud when you need horizontal scaling.

### 8.2 Deployment Pipeline

```
GitHub Repo
    │
    ├── Push to main → GitHub Actions CI
    │   ├── Run tests (pytest)
    │   ├── Lint (ruff)
    │   ├── Type check (mypy)
    │   └── Build Docker images
    │
    └── Deploy to VPS via SSH
        ├── docker compose pull
        ├── docker compose up -d
        └── Run migrations (alembic upgrade head)
```

**Docker Compose for all services**:
```yaml
# docker-compose.yml (simplified)
services:
  api:
    build: ./api
    ports: ["8000:8000"]
    depends_on: [db, valkey]
    environment:
      DATABASE_URL: postgresql://...
      REDIS_URL: redis://valkey:6379
  
  db:
    image: timescale/timescaledb:latest-pg16
    volumes: ["pgdata:/var/lib/postgresql/data"]
    ports: ["5432:5432"]
  
  valkey:
    image: valkey/valkey:8
    volumes: ["valkeydata:/data"]
  
  celery-worker:
    build: ./api
    command: celery -A tasks worker -l info -c 4
    depends_on: [db, valkey]
  
  celery-beat:
    build: ./api
    command: celery -A tasks beat -l info
    depends_on: [valkey]
  
  ingestion:
    build: ./ingestion
    depends_on: [db, valkey]
    # Runs data ingestion workers
  
  caddy:
    image: caddy:2
    ports: ["80:80", "443:443"]
    volumes: ["./Caddyfile:/etc/caddy/Caddyfile"]
```

### 8.3 Monitoring

- **Application metrics**: Prometheus + Grafana (both open source)
- **Log aggregation**: Loki (open source, integrates with Grafana)
- **Uptime monitoring**: `uptime-kuma` (self-hosted, open source)
- **Error tracking**: Sentry (free tier: 5K errors/month, sufficient for launch)
- **Data pipeline monitoring**: Custom dashboard showing ingestion lag per data source, missing data gaps, feature freshness

---

## 9. Cost Estimates

### Monthly Costs (Pre-Revenue / Launch Phase)

| Item | Cost | Notes |
|------|------|-------|
| Hetzner dedicated server | $90 | Primary infrastructure |
| Domain + DNS | $15 | Cloudflare (free plan) for DNS/CDN |
| Apple Developer Program | $8.25 | $99/year for App Store |
| Google Play Developer | $2.08 | $25 one-time |
| GPU training (Lambda/Vast.ai) | $50-100 | ~4h/week of A100 time for retraining |
| Sentry error tracking | $0 | Free tier |
| Email (transactional) | $0 | Resend free tier: 3,000 emails/month |
| Push notifications (FCM) | $0 | Free unlimited |
| **TOTAL** | **~$170-220/month** | |

### Data Source Costs
| Source | Cost |
|--------|------|
| USGS Water Data API | **Free** (API key recommended) |
| NEXRAD on AWS S3 | **Free** (no auth needed for reads) |
| NOAA Tides & Currents | **Free** |
| NOAA NDBC Buoys | **Free** |
| HRRR Weather Model on AWS | **Free** |
| GFS Weather Model | **Free** |
| NOAA CoastWatch SST | **Free** |
| NHDPlus Watershed Data | **Free** |
| Moon/Sun calculations | **Free** (computed locally) |

**Total data cost: $0**

---

## 10. Build Phases & Timeline

### Phase 0: Validation (Weeks 1-3)
**Goal**: Determine if the thesis holds before building anything.

- [ ] Scrape B.A.S.S./MLF tournament results (2014-2025)
- [ ] Pull matching USGS historical data via `dataretrieval`
- [ ] Pull matching weather data from IEM
- [ ] Train TFT on assembled dataset
- [ ] Run backtesting analysis — compute decile spread
- [ ] **GO/NO-GO DECISION**: Does the environmental data predict fishing success meaningfully better than baseline?

**Deliverable**: Internal research report with quantified improvement over baseline. If positive, this becomes the foundation of all future marketing.

### Phase 1: Data Pipeline + Core ML (Weeks 4-8)
**Goal**: Streaming data ingestion and working prediction model.

- [ ] Set up Hetzner server + Docker Compose stack
- [ ] Implement USGS data poller (instantaneous values, 5-min polling)
- [ ] Implement NOAA tides/NDBC poller
- [ ] Implement NEXRAD precipitation extractor (upstream rainfall for key watersheds)
- [ ] Implement weather model (HRRR) downloader
- [ ] Build feature engineering pipeline
- [ ] Set up TimescaleDB schema + continuous aggregates
- [ ] Build watershed graph from NHDPlus data (top 100 fishing waters)
- [ ] Train GNN for spatial interpolation
- [ ] Train production TFT model on historical + backtesting data
- [ ] Build inference API endpoint
- [ ] Set up MLflow for experiment tracking

**Deliverable**: API that returns real-time conditions and 48-hour predictions for any supported fishing spot.

### Phase 2: Alert Engine + Backend API (Weeks 9-12)
**Goal**: Complete backend with event detection and user management.

- [ ] Implement alert rule engine
- [ ] Build upstream rain propagation estimator
- [ ] Implement user auth (JWT + refresh tokens)
- [ ] Build spot management CRUD
- [ ] Build catch logging endpoint (with auto-filled conditions)
- [ ] Implement WebSocket real-time conditions feed
- [ ] Set up FCM push notification delivery
- [ ] Build per-spot calibration layer (initially empty, populates with catches)
- [ ] API documentation (auto-generated by FastAPI)

**Deliverable**: Complete backend API ready for frontend integration.

### Phase 3: Mobile App v1 (Weeks 13-18)
**Goal**: Ship a beautiful, functional mobile app.

- [ ] React Native project setup (Expo or bare workflow)
- [ ] Design system: dark theme, typography, color palette, component library
- [ ] My Spots home screen
- [ ] Spot detail / conditions dashboard
- [ ] Prediction timeline screen
- [ ] Alert feed screen
- [ ] Catch logger (minimal, fast)
- [ ] Upstream rain tracker map view (MapLibre + NEXRAD overlay)
- [ ] Settings + spot management
- [ ] Offline caching
- [ ] Background barometer collection
- [ ] On-device fish species identifier (MobileNet fine-tuned on iNaturalist fish images)
- [ ] App Store / Play Store submission

**Deliverable**: Shipped app on both platforms.

### Phase 4: Prospective Pilot (Weeks 19-26)
**Goal**: Validate predictions against real fishing outcomes.

- [ ] Recruit 50-100 beta users (guides + serious anglers)
- [ ] Run 60-90 day prediction validation study
- [ ] Analyze decile performance
- [ ] Iterate model based on findings
- [ ] Collect testimonials and case studies
- [ ] Fix bugs and polish UX based on feedback

**Deliverable**: Quantified validation results + polished app ready for public launch.

### Phase 5: Public Launch (Week 27+)
**Goal**: Growth.

- [ ] Public launch on App Store + Play Store
- [ ] Content marketing: publish validation study as blog post / white paper
- [ ] Fishing forum seeding (with real results, not spam)
- [ ] YouTube partnerships with fishing content creators
- [ ] PR: pitch to outdoor media (Field & Stream, Outdoor Life, In-Fisherman)
- [ ] Iterate on model with growing user data
- [ ] Begin saltwater / offshore expansion (SST overlays, current data)

---

## Appendix A: Key Python Libraries

| Library | Purpose | License |
|---------|---------|---------|
| `fastapi` | API framework | MIT |
| `uvicorn` | ASGI server | BSD-3 |
| `sqlalchemy` + `asyncpg` | Database ORM + async PostgreSQL driver | MIT / Apache-2.0 |
| `alembic` | Database migrations | MIT |
| `celery` | Task queue | BSD-3 |
| `pytorch-forecasting` | TFT implementation | MIT |
| `torch` + `lightning` | Deep learning framework | BSD-3 |
| `torch_geometric` | GNN framework | MIT |
| `dataretrieval` | USGS water data access | CC0 (public domain) |
| `herbie` | HRRR/GFS model data access | MIT |
| `pyart` | NEXRAD radar data processing | BSD-3 |
| `nexradaws` | NEXRAD S3 access | MIT |
| `erddapy` | NOAA satellite data (SST) access | BSD-3 |
| `pynhd` | NHDPlus watershed data | MIT |
| `astropy` | Moon/sun calculations | BSD-3 |
| `xarray` | Multi-dimensional array data | Apache-2.0 |
| `geopandas` + `shapely` | Geospatial data | BSD-3 |
| `pyjwt` | JWT tokens | MIT |
| `passlib` | Password hashing | BSD |
| `mlflow` | Experiment tracking | Apache-2.0 |
| `optuna` | Hyperparameter optimization | MIT |
| `ruff` | Linting | MIT |
| `pytest` | Testing | MIT |

## Appendix B: Key React Native Libraries

| Library | Purpose | License |
|---------|---------|---------|
| `react-native` | Mobile framework | MIT |
| `expo` | Development platform (optional) | MIT |
| `@maplibre/maplibre-react-native` | Maps (free Mapbox GL fork) | BSD-3 |
| `zustand` | State management | MIT |
| `react-native-reanimated` | Animations | MIT |
| `react-native-mmkv` | Fast local storage | MIT |
| `victory-native` | Charts | MIT |
| `react-native-svg` | SVG rendering | MIT |
| `@react-native-firebase/messaging` | Push notifications | Apache-2.0 |
| `react-native-sensors` | Barometer access | MIT |

## Appendix C: Repo Structure

```
castline/
├── api/                          # FastAPI backend
│   ├── app/
│   │   ├── main.py               # FastAPI app entry
│   │   ├── auth/                 # JWT auth, user management
│   │   ├── spots/                # Spot CRUD, conditions API
│   │   ├── catches/              # Catch logging
│   │   ├── alerts/               # Alert management
│   │   ├── predictions/          # ML inference endpoint
│   │   ├── websocket/            # Real-time conditions feed
│   │   └── middleware/           # Rate limiting, auth middleware
│   ├── Dockerfile
│   └── requirements.txt
│
├── ingestion/                    # Data ingestion workers
│   ├── pollers/
│   │   ├── usgs.py               # USGS water data poller
│   │   ├── nexrad.py             # NEXRAD precipitation extractor
│   │   ├── noaa_tides.py         # Tides & currents poller
│   │   ├── noaa_ndbc.py          # Buoy data poller
│   │   └── weather_models.py     # HRRR/GFS downloader
│   ├── features/
│   │   ├── compute.py            # Feature engineering pipeline
│   │   └── definitions.py        # Feature definitions & configs
│   ├── Dockerfile
│   └── requirements.txt
│
├── ml/                           # ML training & experiments
│   ├── data/
│   │   ├── assemble_training.py  # Build training dataset from TimescaleDB
│   │   ├── tournament_scraper.py # B.A.S.S./MLF results scraper
│   │   └── creel_surveys.py      # State creel survey data processing
│   ├── models/
│   │   ├── tft.py                # TFT training & evaluation
│   │   ├── gnn.py                # Watershed GNN
│   │   ├── calibration.py        # Per-spot calibration layer
│   │   └── fish_id.py            # Fish species classifier (MobileNet)
│   ├── evaluation/
│   │   ├── backtesting.py        # Historical backtesting suite
│   │   ├── decile_analysis.py    # Decile spread computation
│   │   └── shap_analysis.py      # Feature importance analysis
│   ├── notebooks/                # Jupyter notebooks for exploration
│   └── requirements.txt
│
├── mobile/                       # React Native app
│   ├── src/
│   │   ├── screens/
│   │   │   ├── HomeScreen.tsx
│   │   │   ├── SpotDetailScreen.tsx
│   │   │   ├── PredictionTimelineScreen.tsx
│   │   │   ├── AlertFeedScreen.tsx
│   │   │   ├── CatchLoggerScreen.tsx
│   │   │   ├── UpstreamRainScreen.tsx
│   │   │   └── SettingsScreen.tsx
│   │   ├── components/
│   │   ├── hooks/
│   │   ├── services/              # API client, WebSocket client
│   │   ├── store/                 # Zustand stores
│   │   ├── theme/                 # Dark theme, typography, colors
│   │   └── utils/
│   └── package.json
│
├── infrastructure/
│   ├── docker-compose.yml
│   ├── docker-compose.prod.yml
│   ├── Caddyfile
│   ├── prometheus.yml
│   └── grafana/
│
├── migrations/                   # Alembic database migrations
│   ├── alembic.ini
│   └── versions/
│
├── scripts/
│   ├── setup_db.sh
│   ├── seed_gauges.py            # Load USGS gauge locations
│   ├── build_watershed_graph.py  # Generate NHDPlus graph
│   └── deploy.sh
│
├── tests/
│   ├── api/
│   ├── ingestion/
│   └── ml/
│
├── .github/
│   └── workflows/
│       ├── ci.yml
│       └── deploy.yml
│
└── README.md
```

---

## Appendix D: Species-Specific Environmental Ranges

Initial species configuration (expandable via admin or community contribution):

| Species | Optimal Water Temp (°F) | Preferred Conditions | Key Triggers |
|---------|------------------------|---------------------|--------------|
| Largemouth Bass | 65-80 | Clear to moderate clarity, moderate flow | Pre-frontal pressure drops, dawn/dusk, rising water |
| Smallmouth Bass | 60-72 | Clear water, moderate current | Rising water temp into range, falling pressure, post-rain flow events |
| Rainbow Trout | 50-62 | Clear, well-oxygenated, moderate flow | Stable flow after high water, overcast, emerging insect hatches |
| Brown Trout | 54-65 | Clear to slight stain, varied flow | Low light, falling pressure, evening/night, stable conditions |
| Walleye | 55-70 | Moderate turbidity, current | Low light conditions, wind-driven waves, post-frontal stability |
| Channel Catfish | 75-85 | Any clarity, moderate to high flow | Rising water, post-rain, night, high discharge events |
| Redfish (Red Drum) | 68-80 | Moderate clarity, tidal current | Moving tide (esp. outgoing), low pressure, wind from favorable direction |
| Speckled Trout | 58-75 | Clear to moderate, over grass | Incoming tide, dawn/dusk, stable pressure |
| Striped Bass | 55-68 | Any clarity, current | Tide changes, baitfish presence (correlate with SST breaks), falling pressure |
| Steelhead | 38-52 | Moderate flow, clearing water | Rising water temps in early spring, dropping flow after high water events |

These are starting values derived from fisheries literature and guide consensus. The model LEARNS the actual relationships from data — these ranges serve as priors and for the rule-based alert system.

---

## Appendix E: Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Environmental data doesn't predict fishing success significantly better than baseline | Medium | Fatal | Phase 0 validation before any engineering investment |
| USGS API rate limits or downtime | Medium | High | Cache aggressively, maintain 24h local buffer, fall back to daily values if instantaneous unavailable |
| NEXRAD bucket migration (Sept 2025) breaks ingestion | High | Medium | Already accounted for — use new `unidata-nexrad-level2` bucket |
| Fishbrain copies the event-alerting approach | Medium | High | Our moat is the ML model trained on accumulated data, not any single feature. First-mover advantage on the data flywheel. |
| Users don't log enough catches for calibration | High | Medium | Design catch logging to be <5 seconds. Incentivize with "your predictions improve when you log." Start with tournament data as ground truth. |
| Anglers don't want to pay for a forecast tool alongside Fishbrain | Medium | High | Price at $8-10/month — low enough to be impulse. Focus marketing on specific success stories. Offer 30-day free trial. |
| Model overfits to tournament conditions (artificial lake management, competitive dynamics) | Medium | Medium | Supplement with creel survey data and user reports. Use spatial/temporal cross-validation rigorously. |

---

*This document is the complete technical specification for Castline v1. Begin with Phase 0 validation. If the data supports the thesis, execute Phases 1-5 in sequence. Total time to public launch: approximately 6-7 months with a team of 4.*
