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

3. Pull matching USGS daily-value history and derive event features:

```bash
python scripts/collect_usgs_history.py --outcomes castline/validation/data/raw/historical_outcomes.csv --lookback-days 7
```

This produces per-event historical environmental features keyed to the outcome rows, which can then flow into dataset assembly and the baseline-vs-full-feature comparison.

## Notes

- The collector scripts still support `--sample` mode so the lane is runnable immediately.
- Real-source connectors for tournament/creel scraping still need source-specific implementation, but the pipeline can now ingest a real normalized outcomes manifest and fetch real USGS features from it.
- Decision rubric from the plan:
  - `<5%` improvement => weak thesis
  - `5-15%` improvement => viable thesis
  - `>15%` improvement => strong thesis
