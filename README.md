# CASTLINE

Validation-first scaffold for **Phase 0**.

This repo is intentionally biased toward answering one question before broad product work:

> Do environmental features materially outperform a simpler baseline when predicting fishing outcomes?

## What's here

- **Django backend scaffold** that is PostgreSQL-ready but defaults to SQLite for fast local startup
- **Python validation package** for Phase 0 data collection, dataset assembly, and baseline-vs-full-feature comparison
- **CLI scripts** that produce local CSV artifacts so work can start before full infrastructure exists

## Validation lane quickstart

### 1. Create an environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Initialize the thin backend scaffold

```bash
python manage.py migrate
python manage.py runserver
```

### 3. Run the Phase 0 scaffold pipeline

```bash
python scripts/collect_historical_outcomes.py --sample
python scripts/collect_usgs_history.py --sample
python scripts/collect_weather_history.py --sample
python scripts/assemble_validation_dataset.py
python scripts/run_baseline_comparison.py
```

Artifacts land under:

- `castline/validation/data/raw/`
- `castline/validation/data/processed/`
- `castline/validation/artifacts/`

## Real-data collection path

The validation lane now supports a first non-sample ingestion route.

1. Build or scrape a normalized outcomes CSV with these columns:
   - `event_id`
   - `event_name`
   - `date`
   - `location`
   - `species`
   - `median_weight_lb`
   - `baseline_signal`
   - `usgs_site_id`
2. Normalize it into the working raw-data location:

```bash
python scripts/collect_historical_outcomes.py --source path/to/outcomes.csv
```

3. Generate a first-pass USGS mapping suggestion sheet for Bassmaster tournaments so manual curation starts from candidates instead of a blank file:

```bash
python scripts/suggest_bassmaster_mappings.py \
  --bassmaster-start-year 2024 \
  --bassmaster-end-year 2024 \
  --top-n 3
```

This writes `castline/validation/data/raw/bassmaster_usgs_mapping_suggestions.csv` as a review sheet with the top ranked USGS candidates per tournament plus station metadata, match scores, `review_status`, `selected_usgs_site_id`, and `review_notes` columns so curation can happen in-place instead of starting from a blank mapping file.

4. Build the outcomes manifest directly from official Bassmaster result pages plus a small gauge mapping file:

```bash
python scripts/collect_historical_outcomes.py \
  --bassmaster-start-year 2024 \
  --bassmaster-end-year 2024 \
  --mapping path/to/bassmaster_usgs_mapping.csv
```

Bassmaster mapping CSV columns:
- `tournament_slug`
- `usgs_site_id`
- `species` (optional; defaults to `black_bass`)

The Bassmaster adapter uses the official Bassmaster WordPress API to discover `/results/` posts, downloads linked standings PDFs, extracts per-day competitor weights, and emits one normalized row per tournament day with median daily weight.

5. Pull matching USGS daily-value history and derive event features:

```bash
python scripts/collect_usgs_history.py --outcomes castline/validation/data/raw/historical_outcomes.csv --lookback-days 7
```

6. Pull matching historical weather from IEM ASOS directly from the outcomes manifest:

```bash
python scripts/collect_weather_history.py --outcomes castline/validation/data/raw/historical_outcomes.csv
```

The collector will:
- reuse `iem_station` / `weather_station` if your outcomes manifest already has one
- otherwise infer the state from event metadata and search the corresponding `STATE_ASOS` IEM network
- pick the best-matching station using city/location token overlap
- summarize event-day weather into:
  - `air_temp_c`
  - `pressure_mb`
  - `wind_speed_kph`
  - `cloud_cover_pct`
  - `precip_24h_mm`

7. Or, if you already have a normalized weather CSV, you can still load it directly:

```bash
python scripts/collect_weather_history.py --source path/to/weather_history.csv
```

Required weather columns:
- `event_id`
- `air_temp_c`
- `pressure_mb`
- `wind_speed_kph`
- `cloud_cover_pct`
- `precip_24h_mm`

This adds baseline weather features into dataset assembly so the comparison workflow can score a richer environmental model against the simpler baseline signal.

## Notes

- The collector scripts still support `--sample` mode so the lane is runnable immediately.
- Real-source connectors for tournament/creel scraping still need source-specific implementation, but the pipeline can now ingest a real normalized outcomes manifest and fetch real USGS and IEM weather features from it.
- Decision rubric from the plan:
  - `<5%` improvement => weak thesis
  - `5-15%` improvement => viable thesis
  - `>15%` improvement => strong thesis
