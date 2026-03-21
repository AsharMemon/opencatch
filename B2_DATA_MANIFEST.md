# B2 Data Manifest — CASTLINE Project

## Backblaze B2 Storage

- **Bucket**: `castline-data`
- **Key Name**: `fish`
- **Key ID**: `004b6da11e9f7ad0000000003`
- **Region**: us-west-004
- **S3 Endpoint**: `https://s3.us-west-004.backblazeb2.com`
- **All files prefixed with**: `castline/`

## Retrieval

Install and authorize the B2 CLI:

```bash
pip3 install b2
b2 account authorize 004b6da11e9f7ad0000000003 <APPLICATION_KEY>
```

Download a single file:

```bash
b2 file download "b2://castline-data/castline/raw/wqp/wqp_lake_productivity.csv" ./local_path.csv
```

Download an entire folder:

```bash
b2 file download --recursive "b2://castline-data/castline/raw/lagos/" ./lagos/
```

List all files:

```bash
b2 ls -r "b2://castline-data/castline/"
```

## Files Migrated (2026-03-17)

| B2 Path | Original Local Path | Size | Description |
|---------|---------------------|------|-------------|
| `castline/raw/wqp/wqp_lake_productivity.csv` | `validation/data/raw/wqp_lake_productivity.csv` | 7.3 GB | WQP lake productivity/chlorophyll data |
| `castline/raw/globathy/GLOBathy_basic_parameters.zip` | `validation/data/raw/globathy/GLOBathy_basic_parameters.zip` | ~110 MB | GLOBathy lake bathymetry (zipped) |
| `castline/raw/globathy/GLOBathy_basic_parameters/*.csv` (16 files) | `validation/data/raw/globathy/GLOBathy_basic_parameters/` | ~561 MB | GLOBathy lake bathymetry CSVs (unzipped, partitioned by lake range) |
| `castline/raw/lagos/data_dictionary_depth.csv` | `validation/data/raw/lagos/data_dictionary_depth.csv` | small | LAGOS depth data dictionary |
| `castline/raw/lagos/lake_characteristics.csv` | `validation/data/raw/lagos/lake_characteristics.csv` | ~50 MB | LAGOS lake characteristics |
| `castline/raw/lagos/lake_depth.csv` | `validation/data/raw/lagos/lake_depth.csv` | ~15 MB | LAGOS lake depth measurements |
| `castline/raw/lagos/lake_information.csv` | `validation/data/raw/lagos/lake_information.csv` | ~140 MB | LAGOS lake information |
| `castline/raw/lagos/lake_link.csv` | `validation/data/raw/lagos/lake_link.csv` | ~232 MB | LAGOS lake linkage table |
| `castline/raw/creelcat/FishDataCompiled.csv` | `validation/data/raw/state_dnr/creelcat/FishDataCompiled.csv` | 57 MB | CreelCat compiled fish survey data |
| `castline/raw/creel/FishDataCompiled.csv` | `validation/data/raw/creel/FishDataCompiled.csv` | 57 MB | Creel compiled fish survey data |
| `castline/raw/usgs_fish/agap_fish_dataset_v2_0.csv` | `validation/data/raw/usgs_fish_occurrence/agap_fish_dataset_v2_0.csv` | ~33 MB | USGS fish occurrence dataset |
| `castline/raw/usgs_fish/species_list_v2_0.csv` | `validation/data/raw/usgs_fish_occurrence/species_list_v2_0.csv` | small | USGS fish species list |

**Total migrated: ~8.5 GB**

## Notes

- Local copies have been DELETED to free disk space
- The `castline/` prefix keeps these files organized separately from other B2 projects
- Application key is stored locally at `~/.b2_account_info` after authorization
