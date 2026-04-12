# Survey-Grade Promotion Playbook

Updated: 2026-04-09

This is the execution bridge for promoting remaining jurisdictions from:

- `Supporting live`
- `PDF upgrade`
- `Reservoir subset`

into real survey-grade geometry and tiles.

It complements:

- [/Users/Ashar/Documents/fish/docs/jurisdiction-survey-grade-tracker.md](/Users/Ashar/Documents/fish/docs/jurisdiction-survey-grade-tracker.md)
- [/Users/Ashar/Documents/fish/docs/systematic-survey-grade-source-audit-2026-04-09.md](/Users/Ashar/Documents/fish/docs/systematic-survey-grade-source-audit-2026-04-09.md)

## Promotion Programs

### 1. Official PDF jurisdictions

Use this when a jurisdiction has an official inventory of lake-depth PDFs but no clean vector geometry yet.

Pipeline:

1. Inventory scrape
2. Bulk PDF download
3. PDF digitization
4. GeoJSON normalization
5. PMTiles build

Core tools:

- [/Users/Ashar/Documents/fish/ml/bathymetry/download_pdf_inventory_batch.py](/Users/Ashar/Documents/fish/ml/bathymetry/download_pdf_inventory_batch.py)
- [/Users/Ashar/Documents/fish/ml/bathymetry/digitize_pdf_bathymetry.py](/Users/Ashar/Documents/fish/ml/bathymetry/digitize_pdf_bathymetry.py)
- [/Users/Ashar/Documents/fish/ml/bathymetry/run_pdf_promotion_vast.sh](/Users/Ashar/Documents/fish/ml/bathymetry/run_pdf_promotion_vast.sh)
- [/Users/Ashar/Documents/fish/ml/bathymetry/deploy_pdf_promotion_vast.sh](/Users/Ashar/Documents/fish/ml/bathymetry/deploy_pdf_promotion_vast.sh)
- [/Users/Ashar/Documents/fish/ml/bathymetry/normalize_survey_geojson.py](/Users/Ashar/Documents/fish/ml/bathymetry/normalize_survey_geojson.py)
- [/Users/Ashar/Documents/fish/ml/bathymetry/build_survey_pmtiles.py](/Users/Ashar/Documents/fish/ml/bathymetry/build_survey_pmtiles.py)

Validated jurisdictions so far:

- `Mississippi`
  - inventory: [/Users/Ashar/Documents/fish/data/bathymetry/ms/ms_lake_depth_inventory.csv](/Users/Ashar/Documents/fish/data/bathymetry/ms/ms_lake_depth_inventory.csv)
  - verified sample downloads: [/Users/Ashar/Documents/fish/data/bathymetry/ms/pdfs](/Users/Ashar/Documents/fish/data/bathymetry/ms/pdfs)
- `South Dakota`
  - inventory: [/Users/Ashar/Documents/fish/data/bathymetry/sd/sd_gfp_bathymetry_inventory.csv](/Users/Ashar/Documents/fish/data/bathymetry/sd/sd_gfp_bathymetry_inventory.csv)
  - verified sample downloads: [/Users/Ashar/Documents/fish/data/bathymetry/sd/lake_maps](/Users/Ashar/Documents/fish/data/bathymetry/sd/lake_maps)
- `New Brunswick`
  - inventory: [/Users/Ashar/Documents/fish/data/bathymetry/nb/nb_lake_depth_inventory.csv](/Users/Ashar/Documents/fish/data/bathymetry/nb/nb_lake_depth_inventory.csv)
  - verified sample downloads: [/Users/Ashar/Documents/fish/data/bathymetry/nb/pdfs](/Users/Ashar/Documents/fish/data/bathymetry/nb/pdfs)
- `Nova Scotia`
  - inventory: [/Users/Ashar/Documents/fish/data/bathymetry/ns/ns_lake_inventory.csv](/Users/Ashar/Documents/fish/data/bathymetry/ns/ns_lake_inventory.csv)
  - verified sample downloads: [/Users/Ashar/Documents/fish/data/bathymetry/ns/cb_pdfs](/Users/Ashar/Documents/fish/data/bathymetry/ns/cb_pdfs)

### 2. Reservoir subset jurisdictions

Use this when the best official path is a federal or state reservoir survey program instead of a statewide lake contour service.

Pipeline:

1. Filter official reservoir survey catalog by state set
2. Download survey PDFs
3. Extract A-E or contour content
4. Merge into reservoir geometry program

Core tools:

- [/Users/Ashar/Documents/fish/ml/bathymetry/reservoir/fetch_usbr_surveys.py](/Users/Ashar/Documents/fish/ml/bathymetry/reservoir/fetch_usbr_surveys.py)

Validated western subset catalog:

- [/Users/Ashar/Documents/fish/data/bathymetry/reservoir/usbr_west/usbr_survey_catalog.csv](/Users/Ashar/Documents/fish/data/bathymetry/reservoir/usbr_west/usbr_survey_catalog.csv)
- [/Users/Ashar/Documents/fish/data/bathymetry/reservoir/usbr_west/usbr_survey_catalog.json](/Users/Ashar/Documents/fish/data/bathymetry/reservoir/usbr_west/usbr_survey_catalog.json)

### 3. Supporting live jurisdictions

Use this when the jurisdiction already has an official index, viewer, or footprint lane but not full contour geometry.

Typical pattern:

1. Build or refresh the official index
2. Harvest direct map/report URLs
3. Use the PDF program above for real documents
4. Normalize and tile the promoted outputs

Current examples already in the repo:

- [/Users/Ashar/Documents/fish/ml/bathymetry/build_ny_dec_contour_index.py](/Users/Ashar/Documents/fish/ml/bathymetry/build_ny_dec_contour_index.py)
- [/Users/Ashar/Documents/fish/ml/bathymetry/build_nj_lake_plan_index.py](/Users/Ashar/Documents/fish/ml/bathymetry/build_nj_lake_plan_index.py)
- [/Users/Ashar/Documents/fish/ml/bathymetry/build_ri_lake_management_index.py](/Users/Ashar/Documents/fish/ml/bathymetry/build_ri_lake_management_index.py)
- [/Users/Ashar/Documents/fish/ml/bathymetry/scrape_va_dwr_inventory.py](/Users/Ashar/Documents/fish/ml/bathymetry/scrape_va_dwr_inventory.py)
- [/Users/Ashar/Documents/fish/ml/bathymetry/scrape_wv_lake_map_index.py](/Users/Ashar/Documents/fish/ml/bathymetry/scrape_wv_lake_map_index.py)

## Recommended Execution Commands

### Mississippi

```bash
python3 /Users/Ashar/Documents/fish/ml/bathymetry/download_pdf_inventory_batch.py \
  --inventory /Users/Ashar/Documents/fish/data/bathymetry/ms/ms_lake_depth_inventory.csv \
  --output-dir /Users/Ashar/Documents/fish/data/bathymetry/ms/pdfs \
  --download --insecure
```

Remote full download + conversion:

```bash
INSECURE=1 bash /Users/Ashar/Documents/fish/ml/bathymetry/deploy_pdf_promotion_vast.sh \
  ms \
  /Users/Ashar/Documents/fish/data/bathymetry/ms/ms_lake_depth_inventory.csv \
  33296547
```

### South Dakota

Start with `Lake Maps` before the longer `Lake Survey Report` tail:

```bash
python3 /Users/Ashar/Documents/fish/ml/bathymetry/download_pdf_inventory_batch.py \
  --inventory /Users/Ashar/Documents/fish/data/bathymetry/sd/sd_gfp_bathymetry_inventory.csv \
  --output-dir /Users/Ashar/Documents/fish/data/bathymetry/sd/lake_maps \
  --where-column report_type \
  --where-value "Lake Maps" \
  --download
```

Remote full download + conversion:

```bash
WHERE_COLUMN=report_type WHERE_VALUE="Lake Maps" \
  bash /Users/Ashar/Documents/fish/ml/bathymetry/deploy_pdf_promotion_vast.sh \
  sd_lake_maps \
  /Users/Ashar/Documents/fish/data/bathymetry/sd/sd_gfp_bathymetry_inventory.csv \
  33296547
```

### New Brunswick

```bash
python3 /Users/Ashar/Documents/fish/ml/bathymetry/download_pdf_inventory_batch.py \
  --inventory /Users/Ashar/Documents/fish/data/bathymetry/nb/nb_lake_depth_inventory.csv \
  --output-dir /Users/Ashar/Documents/fish/data/bathymetry/nb/pdfs \
  --download
```

Remote full download + conversion:

```bash
bash /Users/Ashar/Documents/fish/ml/bathymetry/deploy_pdf_promotion_vast.sh \
  nb \
  /Users/Ashar/Documents/fish/data/bathymetry/nb/nb_lake_depth_inventory.csv \
  33296547
```

### Nova Scotia

Run in region batches to keep storage and failures manageable:

```bash
python3 /Users/Ashar/Documents/fish/ml/bathymetry/download_pdf_inventory_batch.py \
  --inventory /Users/Ashar/Documents/fish/data/bathymetry/ns/ns_lake_inventory.csv \
  --output-dir /Users/Ashar/Documents/fish/data/bathymetry/ns/cb_pdfs \
  --region-code CB \
  --download
```

Remote full download + conversion:

```bash
REGION_CODE=CB bash /Users/Ashar/Documents/fish/ml/bathymetry/deploy_pdf_promotion_vast.sh \
  ns_cb \
  /Users/Ashar/Documents/fish/data/bathymetry/ns/ns_lake_inventory.csv \
  33296547
```

### Western USBR reservoir subset

```bash
python3 /Users/Ashar/Documents/fish/ml/bathymetry/reservoir/fetch_usbr_surveys.py \
  --states AZ,CA,CO,ID,NV,NM,OR,UT,WY \
  --output /Users/Ashar/Documents/fish/data/bathymetry/reservoir/usbr_west
```

Download PDFs for the same filtered subset:

```bash
python3 /Users/Ashar/Documents/fish/ml/bathymetry/reservoir/fetch_usbr_surveys.py \
  --states AZ,CA,CO,ID,NV,NM,OR,UT,WY \
  --download-pdfs \
  --output /Users/Ashar/Documents/fish/data/bathymetry/reservoir/usbr_west
```

## Practical Interpretation

- `PDF upgrade` jurisdictions are now executable.
- `Mississippi`, `South Dakota`, `New Brunswick`, and `Nova Scotia` all have validated sample download paths on disk now.
- `Reservoir subset` jurisdictions now have a state-filtered acquisition path.
- `Supporting live` jurisdictions still need more per-source promotion work, but they now fit into the same playbook once direct PDF/report URLs are harvested.
- The next real bottleneck is no longer discovery; it is bulk download, digitization quality, and storage/compute management.

## Active Vast Runs

Using instance `33296547`:

- Mississippi:
  - `/data/pdf_promotion_ms_20260410T055455Z`
- South Dakota Lake Maps:
  - `/data/pdf_promotion_sd_lake_maps_20260410T055803Z`
- New Brunswick:
  - `/data/pdf_promotion_nb_20260410T055827Z`
- Nova Scotia Cape Breton:
  - failed first run due apt lock: `/data/pdf_promotion_ns_cb_20260410T055827Z`
  - relaunched with apt-lock wait logic: `/data/pdf_promotion_ns_cb_20260410T060002Z`
