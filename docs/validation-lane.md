# Validation lane

Phase 0 only.

## Commands

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python scripts/collect_historical_outcomes.py --sample
python scripts/collect_usgs_history.py --sample
python scripts/collect_weather_history.py --sample
python scripts/assemble_validation_dataset.py
python scripts/run_baseline_comparison.py
pytest
```

## Real-data path added

The validation lane is no longer limited to bundled samples.

### 1. Prepare a normalized outcomes CSV

Required columns:

- `event_id`
- `event_name`
- `date` (`YYYY-MM-DD`)
- `location`
- `species`
- `median_weight_lb`
- `baseline_signal`
- `usgs_site_id`

### 2. Generate first-pass USGS mapping suggestions for Bassmaster tournaments

```bash
python scripts/suggest_bassmaster_mappings.py \
  --bassmaster-start-year 2024 \
  --bassmaster-end-year 2024
```

This emits `castline/validation/data/raw/bassmaster_usgs_mapping_suggestions.csv` with one suggested gauge per tournament, including station name, site type, candidate count, and a lightweight token-overlap score.

### 3. Normalize outcomes into the working raw-data location

```bash
python scripts/collect_historical_outcomes.py --source path/to/outcomes.csv
```

Or scrape official Bassmaster result pages directly:

```bash
python scripts/collect_historical_outcomes.py \
  --bassmaster-start-year 2024 \
  --bassmaster-end-year 2024 \
  --mapping path/to/bassmaster_usgs_mapping.csv
```

Bassmaster mapping CSV columns:

- `tournament_slug`
- `usgs_site_id`
- `species` (optional)

This adapter walks official Bassmaster `/results/` pages via the Bassmaster WordPress API, downloads linked standings PDFs, and emits one normalized row per tournament day with median daily weight.

### 4. Pull USGS daily values keyed off those rows

```bash
python scripts/collect_usgs_history.py --outcomes castline/validation/data/raw/historical_outcomes.csv --lookback-days 7
```

This currently uses the USGS daily-values API and derives per-event:

- `water_temp_c`
- `discharge_cfs`
- `gage_height_ft`
- `temp_delta_24h_c`
- `flow_delta_24h_pct`

## Scope currently implemented

- Django scaffold with a validation app and `ValidationRun` model
- Sample-backed historical outcomes collector scaffold
- Sample-backed USGS history collector scaffold
- Sample-backed weather collector scaffold
- Manifest-driven outcomes normalization for real historical rows
- Official Bassmaster result-page adapter that turns standings PDFs into per-day median outcome rows
- First-pass Bassmaster-to-USGS mapping suggester built from USGS site-service candidate search
- Event-to-USGS daily-value collection and per-event feature extraction
- Manifest-driven weather/IEM-style join keyed by `event_id`
- Dataset assembler with weather-aware feature engineering
- Baseline-vs-full-feature comparison report generator

## Intended next extension points

- Real tournament/creel source ingestion adapters for building the outcomes manifest itself
- Better event-to-gauge mapping automation than the current first-pass suggestion scoring
- Direct IEM/ASOS fetch adapters instead of normalized weather CSV handoff
- Out-of-sample temporal evaluation instead of same-sample scaffold scoring
