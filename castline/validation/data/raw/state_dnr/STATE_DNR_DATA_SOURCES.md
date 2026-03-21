# State DNR Fish Survey Data Sources

Research conducted 2026-03-16. Summary of available bass-focused fish survey
databases from state agencies and USGS.

---

## 1. USGS CreelCat (DOWNLOADED)

**Status: Downloaded and ready to use**
**Location:** `state_dnr/creelcat/`

- **URL:** https://www.sciencebase.gov/catalog/item/66183787d34e7eb9eb7d7b1c
- **DOI:** https://doi.org/10.5066/P1WOQBRN
- **Format:** CSV (8 files + shapefiles + geopackage)
- **Coverage:** 14,766 creel/angler surveys, 1937-2023, all 50 states
- **Key files downloaded:**
  - `FishDataCompiled.csv` (57 MB, 249k records) - catch/harvest by species
  - `Survey_Data.csv` (8.8 MB, 15.5k surveys) - survey metadata with locations
  - `AngEffort_Data.csv` (1.6 MB, 15k records) - angler effort data
  - `Taxa_Data.csv` (44 KB) - species taxonomy
- **Bass data:** 4,206 Largemouth Bass records with catch rates (Catch_Per_Hour, Catch_Per_Day)
- **State overlap with tournaments:** MN (920), MI (872), WI (669), TX (463), FL (273)
- **Metrics:** Catch, Harvest, Release, Catch_Per_Hour, Catch_Per_Day, Catch_Per_Outing
- **Quality:** Excellent - standardized CPUE, multi-decade temporal coverage, geo-referenced

---

## 2. Minnesota DNR LakeFinder (DOWNLOADED - collector built)

**Status: Collector script operational, sample data downloaded**
**Location:** `state_dnr/mn_dnr/` and collector at `collectors/mn_dnr.py`

- **API:** `https://maps.dnr.state.mn.us/cgi-bin/lakefinder/detail.cgi?type=lake_survey&id={DOW_ID}`
- **Metadata API:** `https://services.dnr.state.mn.us/api/lakefinder/by_id/v1?id={DOW_ID}`
- **Format:** JSON API (no auth required)
- **Coverage:** 4,500+ lakes, surveys dating back to 1976, ~650 surveys/year
- **Data fields:** species code, gear type, CPUE, totalCatch, gearCount, totalWeight, averageWeight, length distributions, quartile ranges
- **Gear types:** Standard electrofishing, Fall electrofishing, gill nets, trap nets, seining, trawling
- **Sample collection:** 14 lakes -> 3,681 catch records, 520 bass records, 72 electrofishing bass records
- **Bass species:** LMB (Largemouth Bass), SMB (Smallmouth Bass), RKB (Rock Bass)
- **Quality:** Excellent - structured CPUE data by gear type with quartile comparisons
- **Next step:** Need a comprehensive DOW ID list to collect all bass lakes (can scrape from MN GIS data or contact DNR)

---

## 3. USGS FiCli - Fish and Climate Change Database (DOWNLOADED)

**Status: Downloaded**
**Location:** `state_dnr/ficli/`

- **URL:** https://www.sciencebase.gov/catalog/item/63eff8edd34efa0476b039b7
- **Format:** CSV
- **Coverage:** 2,870 records of climate change effects on fish
- **Key files:**
  - `Main_Database.csv` (2.4 MB) - climate-fish response data
  - `Geographic_Locations.csv` (252 KB) - study locations
- **Relevance:** Background research on how climate affects bass; not direct CPUE data but useful for feature engineering (thermal guild, management recommendations)
- **Quality:** Good - academic-sourced, peer-reviewed studies

---

## 4. Texas Parks & Wildlife (TPWD)

**Status: Not downloadable in bulk**

- **URL:** https://tpwd.texas.gov/publications/pwdpubs/lake_survey/index.phtml
- **Open Data Portal:** https://gis-tpwd.opendata.arcgis.com/
- **Format:** PDF reports only (individual lake PDFs)
- **Coverage:** ~180+ reservoirs, surveyed every 4 years
- **Data available:** Electrofishing CPUE (fish/hour), gill net catch rates (fish/net-night), species composition, length distributions
- **Species:** Largemouth Bass extensively covered
- **Limitation:** No CSV/API for survey data. Each lake is a separate PDF report. Would require PDF scraping.
- **Open data portal:** Has GIS layers but no fisheries survey data as downloadable CSV
- **Recommendation:** Contact TPWD Inland Fisheries Division directly for bulk data access, or build a PDF scraper for the ~180 lake reports

---

## 5. Florida FWC

**Status: Not directly downloadable**

- **Long-Term Monitoring:** https://myfwc.com/research/freshwater/freshwater-projects/long-term-monitoring/project/
- **GeoData Portal:** https://geodata.myfwc.com/
- **Format:** Portal supports CSV/KML/GeoJSON downloads, but actual fish survey data is not readily findable on the portal
- **Coverage:** 50+ lakes/rivers surveyed annually since 2006
- **Data collection:** Electrofishing at 25 locations per lake each fall
- **Species:** Florida Bass (Micropterus salmoides) collected in every lake sampled
- **Limitation:** Survey data appears internal to FWC; public-facing portal has GIS layers (habitats, species locations) but not the actual CPUE/catch data
- **GBIF:** Historical data (1956-2000) available: https://www.gbif.org/dataset/c5126b60-6dc3-428b-b479-15a921199da1
- **Recommendation:** Contact FWC FWRI Freshwater Fisheries Research section for data request

---

## 6. Wisconsin DNR

**Status: Requires login for detailed data**

- **FMIS:** https://dnr.wisconsin.gov/topic/Fishing/data/infosystem.html
- **Open Data Portal:** https://data-wi-dnr.opendata.arcgis.com/
- **Format:** FMIS requires WAMS ID login; Open Data Portal has fishing regulations GIS data but no fish survey results
- **Coverage:** Comprehensive statewide fish and habitat survey database (FHDB)
- **Limitation:** Fish survey data is behind the FMIS login. Open data portal has regulations and trout data but not electrofishing survey results
- **Recommendation:** Create WAMS account and access FMIS, or contact WI DNR Fisheries

---

## 7. Iowa DNR

**Status: API available but focused on streams, not lakes**

- **BioNet/FishNet API:** https://programs.iowadnr.gov/bionet/api/v1/
- **Swagger docs:** https://programs.iowadnr.gov/bionet/swagger/docs/V1
- **Fish Iowa Portal:** https://programs.iowadnr.gov/lakemanagement/fishiowa/LakeSearch
- **Format:** JSON API (30 endpoints), GeoJSON spatial data
- **Coverage:** 3,377 surveys, 1,698,067 fish collected, 133 species
- **Bass species available:** Largemouth Bass (id=79), Smallmouth Bass (id=78), Spotted Bass (id=129)
- **Limitation:** BioNet is stream-focused (wadeable bioassessment). Lake survey data is in the separate Fish Iowa portal which doesn't appear to have an API
- **Key endpoints:**
  - `GET /api/v1/fish/species` - all species
  - `GET /api/v1/fish/spatial_temporal_distribution/{id}` - occurrence locations (GeoJSON)
  - `GET /api/v1/fish/at_sites/{id}` - fish data at a site
- **Recommendation:** Useful for stream bass data. For lake data, contact Iowa DNR Fisheries Bureau

---

## Priority Actions

1. **Integrate CreelCat** into the validation pipeline - 4,206 LMB catch rate records with temporal coverage matching our tournament data (especially MN, MI, WI, TX, FL)

2. **Expand MN DNR collection** - run the `mn_dnr.py` collector with a full list of bass-productive DOW IDs (need to source the DOW list from MN GIS commons or the LakeFinder search)

3. **Contact state agencies directly:**
   - TX TPWD - request bulk electrofishing CPUE data
   - FL FWC - request Long-Term Monitoring database
   - WI DNR - request FMIS data extract or create account

4. **Build PDF scraper for TPWD** - 180+ lake reports with standardized tables containing electrofishing CPUE data

5. **Investigate additional states:**
   - Michigan DNR has similar programs
   - Illinois, Tennessee, Georgia also have tournament overlap

---

## Data File Inventory

```
state_dnr/
  creelcat/
    FishDataCompiled.csv     (57 MB, 249k records)
    Survey_Data.csv          (8.8 MB, 15.5k surveys)
    AngEffort_Data.csv       (1.6 MB, 15k records)
    Taxa_Data.csv            (44 KB, 181 taxa)
  mn_dnr/
    mn_dnr_fish_surveys.csv  (sample: 3,681 records from 14 lakes)
  ficli/
    Main_Database.csv        (2.4 MB, 2,870 records)
    Geographic_Locations.csv (252 KB, 1,698 locations)
```
