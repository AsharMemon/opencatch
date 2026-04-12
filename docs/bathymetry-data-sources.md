# North American Lake Bathymetry Data Sources

**Generated: 2026-03-29**
**Purpose:** Complete coverage of US state + Canadian province lake depth/bathymetry GIS data for OpenCatch.

## Already Have
Minnesota, Montana, Washington, Florida, Ontario, Quebec, Alberta, Manitoba

---

## TIER 1 - Confirmed ArcGIS REST Services (Direct Query/Download)

### Alaska - ADF&G Lake Bathymetry
- **Service:** `https://gis.adfg.alaska.gov/ags/rest/services/sf_public/Lake_Bathymetry/MapServer`
- **Layers:** 0=Surveyed Lakes (52 polygons), 1=Lake_Bathymetry (998 features), 2=JBER
- **Fields:** Lake, Depth, Shape_Area
- **Format:** Query as GeoJSON via REST API
```bash
# Get all bathymetry polygons (paginate by 1000)
curl -o ak_bathy_0.geojson "https://gis.adfg.alaska.gov/ags/rest/services/sf_public/Lake_Bathymetry/MapServer/1/query?where=1%3D1&outFields=*&resultOffset=0&resultRecordCount=1000&f=geojson"
```

### New Hampshire - GRANIT Bathymetry Lakes
- **Service:** `https://nhgeodata.unh.edu/nhgeodata/rest/services/EDP/Bathymetry_Lakes/MapServer`
- **Layers:** 0=bathymetry_lakes_lines, 1=bathymetry_lakes_polygons (7,351 features)
- **FTP Download:** `https://ftp.granit.unh.edu/GRANIT_Data/Vector_Data/Elevation_and_Derived_Products/d-bathymetry/`
```bash
# Query polygons as GeoJSON
curl -o nh_bathy_poly.geojson "https://nhgeodata.unh.edu/nhgeodata/rest/services/EDP/Bathymetry_Lakes/MapServer/1/query?where=1%3D1&outFields=*&resultOffset=0&resultRecordCount=2000&f=geojson"
# Or download from FTP
curl -o nh_bathy.zip "https://ftp.granit.unh.edu/GRANIT_Data/Vector_Data/Elevation_and_Derived_Products/d-bathymetry/bathymetry_lakes_polygons/nh/bathymetry_lakes_polygons.zip"
```

### Iowa - DNR Bathymetry (Selected Lakes)
- **Service:** `https://programs.iowadnr.gov/geospatial/rest/services/Recreation/fishing/MapServer/1`
- **Features:** 6,489 polylines
- **Fields:** Lake_Name, County, CONTOUR, lakeCode
```bash
curl -o ia_bathy.geojson "https://programs.iowadnr.gov/geospatial/rest/services/Recreation/fishing/MapServer/1/query?where=1%3D1&outFields=*&resultOffset=0&resultRecordCount=2000&f=geojson"
```

### Illinois - IDNR Lake Depth & Capacity
- **Service:** `https://maps.dnr.illinois.gov/geoservices/rest/services/WaterResources/LakeDepthAndCapacity/MapServer`
- **Layers:** 0=Lake Depth (feet) - 4,828 polylines, 1=Lakes Surveyed (polygons)
```bash
curl -o il_depth.geojson "https://maps.dnr.illinois.gov/geoservices/rest/services/WaterResources/LakeDepthAndCapacity/MapServer/0/query?where=1%3D1&outFields=*&resultOffset=0&resultRecordCount=2000&f=geojson"
```

### Ohio - ODNR DOW Lakes Bathymetry
- **Service:** `https://gis.ohiodnr.gov/arcgis/rest/services/DOW_Services/DOW_Lakes_Bathymetry/MapServer`
- **Layers:** 0=Lake Bathymetry (2,809 polylines), 1=Lakes (polygons)
- **Fields:** LAKE_NAME, DEPTH, DIV_NUM
```bash
curl -o oh_bathy.geojson "https://gis.ohiodnr.gov/arcgis/rest/services/DOW_Services/DOW_Lakes_Bathymetry/MapServer/0/query?where=1%3D1&outFields=*&resultOffset=0&resultRecordCount=2000&f=geojson"
```

### Connecticut - DEEP Lake Bathymetry Contours
- **Service:** `https://services1.arcgis.com/FjPcSmEFuDYlIdKC/arcgis/rest/services/Lake_Bathymetry_Contours/FeatureServer`
- **Layers:** 1=Lines (7,495 features), 2=Polygons
- **Coverage:** 125 waterbodies, depth in feet (DEPTH_FT)
```bash
curl -o ct_bathy_lines.geojson "https://services1.arcgis.com/FjPcSmEFuDYlIdKC/arcgis/rest/services/Lake_Bathymetry_Contours/FeatureServer/1/query?where=1%3D1&outFields=*&resultOffset=0&resultRecordCount=2000&f=geojson"
curl -o ct_bathy_poly.geojson "https://services1.arcgis.com/FjPcSmEFuDYlIdKC/arcgis/rest/services/Lake_Bathymetry_Contours/FeatureServer/2/query?where=1%3D1&outFields=*&resultOffset=0&resultRecordCount=2000&f=geojson"
```

### Saskatchewan - Bathymetric Index (Point Data)
- **Service:** `https://gis.saskatchewan.ca/arcgis/rest/services/bathymetric/mapserver`
- **Layer 0:** 945 points (index of surveyed lakes, with SCAN_LINK to PDF maps)
- **Fields:** MAP_NAME, CONTOUR_INT, QUALITY, SCAN_LINK
- **Note:** This is a map index, not the actual contours. SCAN_LINK has PDFs.
```bash
curl -o sk_bathy_index.geojson "https://gis.saskatchewan.ca/arcgis/rest/services/bathymetric/mapserver/0/query?where=1%3D1&outFields=*&f=geojson"
```

---

## TIER 2 - Open Data Hub Downloads (Shapefile/GeoJSON via portal)

### Michigan - DNR Inland Lake Contours
- **Portal:** `https://gis-michigan.opendata.arcgis.com/datasets/midnr::inland-lake-contours`
- **Also at:** `https://gis-midnr.opendata.arcgis.com/datasets/midnr::inland-lake-contours`
- **Coverage:** 2000+ inland lakes (hand-drawn contours digitized)
```bash
# Download GeoJSON from ArcGIS Hub API
curl -L -o mi_inland_contours.geojson "https://opendata.arcgis.com/api/v3/datasets/65085a22e09e432c92d722d85f467f7a_0/downloads/data?format=geojson&spatialRefId=4326"
```

### Indiana - DNR Lake Bathymetry
- **Shapefile:** `https://maps.indiana.edu/download/Hydrology/Water_Bodies_Lakes_Bathymetry.zip`
- **Coverage:** 164 lakes, 5-ft contours (some 2-ft)
- **Shapefile name:** LAKE_BATHYMETRY_IDNR_IN.SHP
```bash
curl -L -o in_bathy.zip "https://maps.indiana.edu/download/Hydrology/Water_Bodies_Lakes_Bathymetry.zip"
```

### Massachusetts - MassWildlife Inland Water Bathymetry
- **Portal:** `https://www.mass.gov/info-details/massgis-data-masswildlife-inland-water-bathymetry`
- **Feature Service:** `https://gis.data.mass.gov/datasets/masswildlife-inland-bathymetry-feature-service`
- **Coverage:** 104+ water bodies (updated Dec 2024), ~970 MB download
```bash
# Download shapefile from MassGIS (check portal for current link)
curl -L -o ma_bathy.zip "https://s3.us-east-1.amazonaws.com/download.massgis.digital.mass.gov/shapefiles/state/dfwbathy.zip"
```

### North Dakota - NDGF Lake Contours
- **Portal:** `https://gishubdata-ndgov.hub.arcgis.com/datasets/ndgf::ndgf-lake-contours`
- **Also:** `https://gishubdata.nd.gov/dataset/lake-contours`
```bash
curl -L -o nd_lake_contours.geojson "https://opendata.arcgis.com/api/v3/datasets/2790def30e2b4f41b5faba6e325f7525_0/downloads/data?format=geojson&spatialRefId=4326"
```

### Nebraska - NGPC Lake Contours
- **Portal:** `https://data-outdoornebraska.opendata.arcgis.com/datasets/lake-contours`
- **MapServer:** `https://maps.outdoornebraska.gov/arcgis/rest/services/Programs/LakeMapping/MapServer`
```bash
# Try the open data portal GeoJSON download
curl -L -o ne_lake_contours.geojson "https://data-outdoornebraska.opendata.arcgis.com/datasets/lake-contours.geojson"
```

### Texas - TWDB Completed Lake Surveys
- **Survey list:** `https://www.twdb.texas.gov/surfacewater/surveys/completed/list/index.asp`
- **Coverage:** 80+ reservoirs with shapefiles
- **Format:** Individual .zip per lake (shapefiles + contour PDFs + EAC tables)
```bash
# Example for a specific lake (check survey list for all available)
# Each lake has its own download page - scrape the list page for all links
curl -o tx_surveys_page.html "https://www.twdb.texas.gov/surfacewater/surveys/completed/list/index.asp"
```

### New Brunswick - Lake Depth Bathymetry Points
- **Service:** `https://gis-erd-der.gnb.ca/server/rest/services/OpenData/Lake_Depth_Bathymetry_Points/FeatureServer`
- **Note:** Server was down during testing - retry later
```bash
curl -o nb_bathy.geojson "https://gis-erd-der.gnb.ca/server/rest/services/OpenData/Lake_Depth_Bathymetry_Points/FeatureServer/0/query?where=1%3D1&outFields=*&f=geojson"
```

---

## TIER 3 - Known Data Exists, Requires Portal Navigation

### Wisconsin - DNR Lake Depth Maps
- **Open Data Portal:** `https://data-wi-dnr.opendata.arcgis.com/`
- **SCO Lake Maps:** `https://www.sco.wisc.edu/maps/lake/`
- **Commercial GIS:** `https://wiscartography.com/digital-data` (160 lakes, shapefile + geodatabase, paid)
- **REST services root:** `https://dnrmaps.wi.gov/arcgis/rest/services`
- **Status:** No free statewide bathymetry layer found. DNR has scanned paper maps. Check WY_Lakes_AIS folder.

### New York - DEC Lake Contour Maps
- **GIS Clearinghouse:** `https://data.gis.ny.gov/`
- **Contour Index:** `https://elevation.its.ny.gov/arcgis/rest/services/indexes/contour_index/FeatureServer`
- **DEC fishing/maps hub:** `https://dec.ny.gov/outdoor/7749.html`
- **DEC lake contour search:** `https://dec.ny.gov/places-to-go/maps?text=&f%5B0%5D=map_type%3A6256`
- **DEC Maps viewer:** `https://nysdec.maps.arcgis.com/apps/webappviewer/index.html?id=ae91142c812a4ab997ba739ed9723e6e`
- **Sitemap:** `https://dec.ny.gov/sitemap.xml`
- **USGS East of Hudson:** `https://data.usgs.gov/datacatalog/data/USGS:5f7c85a082ce1d74e7db5363` (shapefiles for reservoirs)
- **Status:** promoted into a live official statewide contour-page index lane built from the public sitemap and page URLs. Direct automated fetch of the DEC landing pages is still bot-protected from this machine, so the live lane uses public geocodes plus official contour-page links rather than pretending direct PDF extraction is solved.
- **Local inventory artifact:** `/Users/Ashar/Documents/fish/data/bathymetry/ny/ny_dec_map_inventory.csv`
- **Local refined contour inventory:** `/Users/Ashar/Documents/fish/data/bathymetry/ny/ny_dec_contour_pages.csv` (`421` official contour-map landing pages)
- **Local statewide index artifacts:** `/Users/Ashar/Documents/fish/data/bathymetry/ny/ny_dec_contour_inventory.csv`, `/Users/Ashar/Documents/fish/data/bathymetry/ny/ny_waterbody_index.geojson`

### Maine - IF&W Lake Depth Points
- **KML index:** `https://www.maine.gov/ifw/fishing/kml/Lake_Depths.kml` (68 regional KMZ files)
- **DEP service (token required):** `https://gis.maine.gov/arcgis/rest/services/dep/MaineDEP_Lakes_Data/MapServer`
- **MEGIS catalog:** `https://www.maine.gov/megis/catalog/`
```bash
# Download KML index file
curl -o me_lake_depths.kml "https://www.maine.gov/ifw/fishing/kml/Lake_Depths.kml"
```

### Oregon - Atlas of Oregon Lakes
- **Atlas website:** `https://oregonlakesatlas.org/bathymetry`
- **ArcGIS item:** `https://www.arcgis.com/home/item.html?id=7ed7eeb4c1594abeb8dc977678e47366`
- **ODFW Data Clearinghouse:** `https://nrimp.dfw.state.or.us/nrimp/default.aspx?p=259`
- **GEOHub:** `https://geohub.oregon.gov/`

### Oklahoma - OWRB Bathymetric Mapping
- **OWRB portal:** `https://oklahoma.gov/owrb/data-and-maps/bathymetric-mapping.html`
- **GIS data:** `https://oklahoma.gov/owrb/data-and-maps/gis-data.html`
- **Open Data Hub:** `https://home-owrb.opendata.arcgis.com/`
- **Official statewide lake workbook:** `https://oklahoma.gov/content/dam/ok/en/owrb/documents/maps-and-data/lakes-of-oklahoma-data.xlsx`
- **Format:** Shapefiles, geodatabases, or KMZ
- **Status:** promoted into a live official statewide depth-point lane using OWRB coordinates + max-depth values.
- **Local inventory artifacts:** `/Users/Ashar/Documents/fish/data/bathymetry/ok/ok_lakes_inventory.xlsx`, `/Users/Ashar/Documents/fish/data/bathymetry/ok/ok_lakes_inventory.csv`, `/Users/Ashar/Documents/fish/data/bathymetry/ok/ok_depth_points.geojson`

### Pennsylvania - PFBC / PASDA
- **PASDA portal:** `https://www.pasda.psu.edu/`
- **Dataset:** `https://www.pasda.psu.edu/uci/DataSummary.aspx?dataset=1103`
- **Direct official ZIP:** `https://www.pasda.psu.edu/download/pafish/Lakes_PFBCDatabase202411.zip`
- **Status:** promoted into a live official statewide lake-footprint lane; still not bathymetry contours, but now usable as a Pennsylvania-specific coverage layer.
- **Local artifacts:** `/Users/Ashar/Documents/fish/data/bathymetry/pa/Lakes_PFBCDatabase202411.zip`, `/Users/Ashar/Documents/fish/data/bathymetry/pa/pa_pasda_inventory.txt`, `/Users/Ashar/Documents/fish/data/bathymetry/pa/pa_lake_footprints.geojson`

### Vermont - ANR Lake Champlain + Inland
- **Lake Champlain Bathymetry:** `https://geodata.vermont.gov/datasets/vt-lake-champlain-bathymetry`
- **ANR GIS Hub:** `https://gis-vtanr.hub.arcgis.com/`
- **Depth charts (PDF):** `https://dec.vermont.gov/watershed/lakes-ponds/data-maps/charts`

### Virginia - DWR GIS Data
- **DWR GIS Download:** `https://dwr.virginia.gov/gis/data/download/`
- **GIS Clearinghouse:** `https://vgin.vdem.virginia.gov/pages/cl-data-download`
- **Waterbody sitemap:** `https://dwr.virginia.gov/wp-sitemap-posts-waterbody-1.xml`
- **Status:** promoted into a live official statewide survey-index lane built from DWR waterbody pages with embedded map coordinates and official map/report PDF links.
- **Local artifacts:** `/Users/Ashar/Documents/fish/data/bathymetry/va/va_dwr_waterbody_inventory.csv`, `/Users/Ashar/Documents/fish/data/bathymetry/va/va_waterbody_index.geojson`

### Kentucky - KyGovMaps / KDFWR
- **Open Data Portal:** `https://opengisdata.ky.gov/`
- **Fish Attractor GPX:** `https://fw.ky.gov/Fish/Pages/fish_attractor_lakes.aspx`
- **USGS Buckhorn Lake (2023):** `https://catalog.data.gov/dataset/bathymetry-of-buckhorn-lake-kentucky-may-2023`

### Missouri - USGS Water Supply Lakes
- **USGS SIR 2024-5114:** `https://pubs.usgs.gov/publication/sir20245114` (13 lakes, 2022-23)
- **USGS SIR 2023-5046:** NW + WC Missouri lakes (2020)
- **USGS SIR 2023-5108:** NE Missouri lakes (2021)

### Georgia - DNR Wildlife Resources
- **Open Data Hub:** `https://gis-gadnrwrd.opendata.arcgis.com/`
- **Contact:** Jan.McKinnon@dnr.ga.gov (DEM files too large for download)
- **Partial official subset:** Georgia DNR PFAs expose fishing-guide / contour-depth PDF leads for some lakes, so Georgia should be treated as a `PDF / partial official subset`, not a pure no-data state.
- **Local inventory artifact:** `/Users/Ashar/Documents/fish/data/bathymetry/ga/ga_pfa_pdf_inventory.csv` (`21` candidate PFAs, `7` verified PDFs)
- **Downloaded subset manifest:** `/Users/Ashar/Documents/fish/data/bathymetry/ga/pdfs/download_manifest.json`

### South Carolina - SCDNR
- **Open Data:** `https://data-scdnr.opendata.arcgis.com/`
- **Format:** Shapefiles, File GeoDatabase, KMZ

### Tennessee - TWRA
- **GIS Maps:** `https://www.tn.gov/twra/gis-maps.html`
- **State GIS Portal:** `https://tn-tnmap.opendata.arcgis.com/search?tags=water`
- **Note:** Reservoir maps from TVA/USACE, not TWRA directly

### North Carolina - NCWRC
- **DEQ Open Data:** `https://data-ncdenr.opendata.arcgis.com/`
- **NC OneMap:** `https://www.nconemap.gov/`
- **Status:** Treat North Carolina as a `reservoir subset`, not a blank state. Official NCDEQ reservoir assessment reports exist, and Jordan Lake reservoir modeling explicitly cites a recent UNC bathymetry survey for below-normal-pool bathymetry.
- **Local lead artifact:** `/Users/Ashar/Documents/fish/data/bathymetry/nc/nc_reservoir_bathymetry_leads.csv`
- **Inventory artifact:** `/Users/Ashar/Documents/fish/data/bathymetry/nc/nc_reservoir_report_inventory.csv`
- **Downloaded subset manifest:** `/Users/Ashar/Documents/fish/data/bathymetry/nc/pdfs/download_manifest.json`
- **Extracted calibration signals:** `/Users/Ashar/Documents/fish/data/bathymetry/nc/extracted/nc_reservoir_report_signals.csv`
- **Calibration metadata:** `/Users/Ashar/Documents/fish/data/bathymetry/nc/calibration/nc_reservoir_calibration_metadata.csv`

### Utah - DWR
- **DWR Data Hub:** `https://dwr-data-utahdnr.hub.arcgis.com/`
- **Open Water Data:** `https://dwre-utahdnr.opendata.arcgis.com/`

### Wyoming - WGFD
- **Open Data:** `https://wyoming-wgfd.opendata.arcgis.com/`
- **Geospatial Hub:** `https://data.geospatialhub.org/`

### Idaho - IDFG
- **Hydrography MapServer:** `https://gis.idfg.idaho.gov/server/rest/services/Hydrography/MapServer`
- **Open Data:** `https://data-idfggis.opendata.arcgis.com/`
- **Note:** Layer 0 = Lakes and Reservoirs (1:100K), no dedicated bathymetry layer found

### South Dakota - GFP
- **DANR Data:** `https://danr.sd.gov/Press/DataAndMapping.aspx`
- **Open Data:** `https://opendata2017-09-18t192802468z-sdbit.opendata.arcgis.com/`

---

## TIER 4 - PDF/Image Only or Very Limited Data

### Arkansas
- **AGFC Lake Maps (PDF):** `https://www.agfc.com/resources/maps/arkansas-lake-maps/`
- **USGS individual lakes:** Blue Mountain, Dierks Lake (geodatabases at USGS data catalog)

### California
- **CDFW California Lakes (polygons only, no depth):** `https://gis.data.ca.gov/datasets/CDFW::california-lakes`
- **Status:** No statewide bathymetry. Individual reservoir data may exist through DWR.

### Colorado
- **CPW Maps:** `https://cpw.state.co.us/maps-and-gis`
- **Status:** Mostly PDFs. No ArcGIS REST bathymetry service found.

### Delaware
- **DNREC Open Data:** `https://dnrec.delaware.gov/dnrec-open-data/`
- **Confirmed ArcGIS service:** `https://enterprise.firstmaptest.delaware.gov/arcgis/rest/services/Hydrology/DE_Public_Ponds/MapServer`
- **Layers:** `0=Depths` (point soundings), `5=Bathemetry` (polyline contours)
- **Fields:** layer `5` exposes `POND` + `LABEL`, where `LABEL` is the contour depth in feet
- **Status:** acquired, normalized, tiled, and published live as `de_contours`
```bash
curl -o de_bathy.geojson "https://enterprise.firstmaptest.delaware.gov/arcgis/rest/services/Hydrology/DE_Public_Ponds/MapServer/5/query?where=1%3D1&outFields=*&resultOffset=0&resultRecordCount=1000&f=geojson"
```

### Hawaii
- **State GIS:** `https://geoportal.hawaii.gov/`
- **Status:** Ocean bathymetry available; inland lake bathymetry not found (very few lakes).

### Kansas
- **KDWP Maps (PDF):** `https://ksoutdoors.gov/Fishing/Where-to-Fish-in-Kansas/Bathymetric-Lake-Maps`
- **Geoportal:** `https://hub.kansasgis.org/`

### Louisiana
- **LDWF:** `https://www.wlf.louisiana.gov/page/wma-gis-data-download`
- **Inland plan trail:** `https://www.wlf.louisiana.gov/resources/category/freshwater-inland-fish/inland-waterbody-management-plans`
- **Vegetation plan trail:** `https://www.wlf.louisiana.gov/resources/category/freshwater-inland-fish/aquatic-vegetation-control-plans`
- **Status:** Promote Louisiana into `PDF upgrade`. LDWF has a real inland lake-by-lake plan trail even though the category pages are bot-protected to automated fetches.
- **Local lead artifact:** `/Users/Ashar/Documents/fish/data/bathymetry/la/la_official_bathymetry_leads.csv`
- **Inventory artifact:** `/Users/Ashar/Documents/fish/data/bathymetry/la/la_ldwf_plan_inventory.csv`
- **Planned manifest:** `/Users/Ashar/Documents/fish/data/bathymetry/la/pdfs/download_manifest.json`
- **Protected fetch workflow:** `/Users/Ashar/Documents/fish/ml/bathymetry/download_pdf_inventory_playwright.py`, `/Users/Ashar/Documents/fish/ml/bathymetry/deploy_protected_pdf_fetch_vast.sh`
- **Reality check:** even a full Playwright/Chromium browser session on Vast still returned `403` for all seeded LDWF asset URLs, so this lane currently needs search-index or manual-browser-assisted retrieval rather than ordinary automation.
- **Saved retry manifest:** `/Users/Ashar/Documents/fish/data/bathymetry/la/remote_manifests/la_ldwf_retry_download_manifest.json`
- **Manual retrieval queue:** `/Users/Ashar/Documents/fish/data/bathymetry/la/manual_queue/la_ldwf_manual_retrieval_queue.csv`
- **Search-index hits:** `/Users/Ashar/Documents/fish/data/bathymetry/la/manual_queue/la_ldwf_search_index_hits.csv`

### Maryland
- **iMap (deprecated, migrating):** Chesapeake Bay + Ocean contours only
- **Status:** No inland lake bathymetry layer found

### Mississippi
- **MDWFP Lake Depth Maps (PDF):** `https://www.mdwfp.com/fishing-boating/lake-depth-maps`

### Nevada
- **NDOW Open Data:** `https://gis-ndow.opendata.arcgis.com/`
- **Status:** No bathymetry layer found in open data portal.

### New Jersey
- **NJDEP Fish & Wildlife (PDF maps):** `https://dep.nj.gov/njfw/fishing/freshwater/lake-survey-maps/`
- **NJ Geological Survey lake map index:** `https://www.nj.gov/dep/njgs/pricelst/njlakes.pdf`
- **NJDEP lake management plans:** `https://www.nj.gov/dep/fgw/fshresmgt_lakeplans.htm`
- **Offshore contours only:** `https://gisdata-njdep.opendata.arcgis.com/datasets/njdep::bathymetric-contours-of-new-jersey`
- **Status:** promoted into a live official survey-index lane from the accessible lake-plan PDFs, while the primary Fish & Wildlife page remains Incapsula-protected.
- **Local inventory artifacts:** `/Users/Ashar/Documents/fish/data/bathymetry/nj/nj_lake_plan_inventory.csv`, `/Users/Ashar/Documents/fish/data/bathymetry/nj/pdfs/`, `/Users/Ashar/Documents/fish/data/bathymetry/nj/nj_waterbody_index.geojson`

### New Mexico
- **RGIS:** `https://rgis.unm.edu/`
- **Status:** No lake bathymetry found.

### Rhode Island
- **DEM maps (PDF):** `https://dem.ri.gov/sites/g/files/xkgbur861/files/maps/mapfile/pondbath.pdf`
- **Freshwater lakes page:** `https://dem.ri.gov/natural-resources-bureau/fish-wildlife/reports-publications/freshwater-lakes-ponds-and-reservoirs`
- **Lake management planning projects:** `https://dem.ri.gov/node/28786`
- **Bowdish Lake plan (PDF):** `https://dem.ri.gov/sites/g/files/xkgbur861/files/2025-12/bowdish-lake-mgnt-plan.pdf`
- **Smith and Sayles Reservoir plan (PDF):** `https://dem.ri.gov/sites/g/files/xkgbur861/files/2025-12/smith-sayles-lake-mgnt-plan.pdf`
- **RIGIS:** `https://www.rigis.org/`
- **Status:** promoted into a live official lake-management index lane built from DEM project pages and plan PDFs for `8` Rhode Island waterbodies. The direct automated fetch blocker on the broader DEM bathymetry PDF path still exists, but Rhode Island no longer depends only on the national coarse fallback.
- **Local inventory artifacts:** `/Users/Ashar/Documents/fish/data/bathymetry/ri/ri_lake_management_inventory.csv`, `/Users/Ashar/Documents/fish/data/bathymetry/ri/ri_waterbody_index.geojson`

### West Virginia
- **DNR GIS:** `https://wvdnr.gov/gis-mapping/`
- **Lake map links:** `https://wvdnr.gov/gis-mapping/lake-map-links/`
- **Status:** promoted into a live official survey-index lane from the WVDNR lake-map directory. No statewide GIS bathymetry contours found yet.
- **Local inventory artifacts:** `/Users/Ashar/Documents/fish/data/bathymetry/wv/wv_lake_map_inventory.csv`, `/Users/Ashar/Documents/fish/data/bathymetry/wv/wv_waterbody_index.geojson`

---

## TIER 5 - Canadian Provinces (Beyond Already-Have)

### British Columbia
- **FIDQ Bathymetric Maps (PDF only):** `https://a100.gov.bc.ca/pub/fidq/viewBathymetricMaps.do`
- **BC Data Catalogue:** `https://catalogue.data.gov.bc.ca/dataset/bathymetric-maps-of-surveyed-lakes`
- **Status:** Scanned maps only, NOT vector/GIS. Would need OCR/georeferencing pipeline.

### Saskatchewan
- **MapServer (index only):** `https://gis.saskatchewan.ca/arcgis/rest/services/bathymetric/mapserver` (945 points)
- **Viewer:** `https://gisappl.saskatchewan.ca/Html5Ext/?viewer=bathy`
- **Status:** Index of surveyed lakes with links to scanned PDF maps. No vector contours.
```bash
curl -o sk_bathy_index.geojson "https://gis.saskatchewan.ca/arcgis/rest/services/bathymetric/mapserver/0/query?where=1%3D1&outFields=*&f=geojson"
```

### New Brunswick
- **FeatureServer (was down):** `https://gis-erd-der.gnb.ca/server/rest/services/OpenData/Lake_Depth_Bathymetry_Points/FeatureServer`
- **Status:** Digital GIS points exist (50+ years of surveys). Server intermittently available.

### Nova Scotia
- **Lake Inventory Maps (PDF/scanned):** `https://novascotia.ca/fish/sportfishing/our-lakes/lake-inventory/`
- **Lake Survey Points:** `https://open.canada.ca/data/en/dataset/e852a640-8deb-9a75-d086-ccd1c20d12b9`
- **Status:** 1000+ lakes inventoried but maps are scanned, not vector.

### Prince Edward Island
- **Open Data:** `https://data.princeedwardisland.ca/`
- **GIS Catalog:** `https://gov.pe.ca/gis/`
- **Angling hub:** `https://www.princeedwardisland.ca/en/information/environment-energy-and-climate-action/angling-resources-and-information-centre`
- **Status:** Treat PEI as `project / index-supported`, not fully blank. Official angling/fishing-location and GIS/project resources exist, even though a direct inland bathymetry dataset has not surfaced.
- **Local index artifact:** `/Users/Ashar/Documents/fish/data/bathymetry/pe/pei_project_index.csv`
- **Direct publication inventory:** `/Users/Ashar/Documents/fish/data/bathymetry/pe/pei_publication_inventory.csv`
- **Downloaded subset manifest:** `/Users/Ashar/Documents/fish/data/bathymetry/pe/pdfs/download_manifest.json`

### Newfoundland & Labrador
- **Open Data:** `http://opendata.gov.nl.ca/`
- **Status:** No direct province-wide inland bathymetry dataset found, but this is stronger than a blank state: official water-resources atlas, hydrology reporting, and project/report mapping exist. Treat as `project / index-supported`.
- **Local index artifact:** `/Users/Ashar/Documents/fish/data/bathymetry/nl/nl_water_resources_index.csv` (`152` structured links)

### Northwest Territories
- **NT GoMap:** `https://www.maps.geomatics.gov.nt.ca/`
- **Status:** No lake bathymetry GIS data found. Some scanned maps may exist.

### Yukon
- **Geomatics Yukon:** `https://geomaticsyukon.ca/`
- **Status:** Distinct `withdrawn official data` case. The old Yukon bathymetry maps/data record was removed from distribution for poor accuracy, while separate Yukon boating e-charts still exist for contextual navigation use.

### Nunavut
- **Canada-Nunavut Geoscience Office**
- **Status:** Treat Nunavut as `project / index-supported`, not fully blank. Official procurement and coastal-resource inventory documents confirm real bathymetry project activity, but not a territory-wide inland dataset.
- **Local index artifact:** `/Users/Ashar/Documents/fish/data/bathymetry/nu/nunavut_bathymetry_project_index.csv`

---

## Global Fallback Dataset

### GLOBathy - Global Lakes Bathymetry
- **Paper:** `https://www.nature.com/articles/s41597-022-01132-9`
- **Coverage:** 1.4 million lakes worldwide, synthetic max-depth estimates
- **Resolution:** Lake-level (single max depth value per lake, not contours)
- **Use:** Fill gaps for states/provinces without survey data

---

## FALLBACK DOWNLOAD METHODS (for states/provinces where primary download failed)

### 1. Alaska (AK) - Primary ArcGIS server unreachable
**Alternative 1:** ADF&G GIS Data Downloads page (may block automated access)
```bash
# The primary server at gis.adfg.alaska.gov may block curl user-agents. Try with browser headers:
curl -H "User-Agent: Mozilla/5.0" -o ak_bathy_0.geojson \
  "https://gis.adfg.alaska.gov/ags/rest/services/sf_public/Lake_Bathymetry/MapServer/1/query?where=1%3D1&outFields=*&resultOffset=0&resultRecordCount=1000&f=geojson"
```
**Alternative 2:** ADF&G Morphometric Atlas (PDF, 23 lakes with full bathymetry)
```bash
curl -o ak_morphometric_atlas.pdf "https://www.adfg.alaska.gov/fedaidpdfs/RIR.2A.2000.23.pdf"
```
**Alternative 3:** Alaska DGGS individual lake studies (check for specific lakes)
- Mother Goose Lake: https://dggs.alaska.gov/webpubs/metadata/RDF2008-3.faq.html
- Valdez Glacier Lake: https://dggs.alaska.gov/webpubs/metadata/RDF2015-1.faq.html

### 2. Connecticut (CT) - FeatureServer returned 400
**Alternative 1:** CTECO Data Download (authoritative source, pre-packaged shapefiles)
```bash
# CT ECO is the canonical download site - hosted at UConn
# Navigate to: https://maps.cteco.uconn.edu/download/ → select "Lake Bathymetry"
# Direct metadata: https://cteco.uconn.edu/guides/Elevation_Contour_Lake_Bathymetry.htm
```
**Alternative 2:** Retry the ArcGIS FeatureServer with explicit layer IDs
```bash
# Lines layer (layer 1), 125 waterbodies
curl -o ct_bathy_lines.geojson \
  "https://services1.arcgis.com/FjPcSmEFuDYlIdKC/arcgis/rest/services/Lake_Bathymetry_Contours/FeatureServer/1/query?where=1%3D1&outFields=*&resultOffset=0&resultRecordCount=2000&f=geojson"
# Polygons layer (layer 2)
curl -o ct_bathy_poly.geojson \
  "https://services1.arcgis.com/FjPcSmEFuDYlIdKC/arcgis/rest/services/Lake_Bathymetry_Contours/FeatureServer/2/query?where=1%3D1&outFields=*&resultOffset=0&resultRecordCount=2000&f=geojson"
```
**Alternative 3:** New deepmaps.ct.gov portal
```bash
# The CT DEEP data has moved to deepmaps.ct.gov
# Dataset page: https://deepmaps.ct.gov/maps/bdb59e03b6b14b0f9c7a0f65114dc2b0
```

### 3. Illinois (IL) - MapServer returned 500
**Alternative 1:** Query individual layers directly (the 500 may be on the root service)
```bash
# Layer 0 = Lake Depth (feet) polylines, Layer 1 = Lakes Surveyed polygons, Layer 2 = Lake polygons
# Try layer 0 directly:
curl -o il_depth.geojson \
  "https://maps.dnr.illinois.gov/geoservices/rest/services/WaterResources/LakeDepthAndCapacity/MapServer/0/query?where=1%3D1&outFields=*&resultOffset=0&resultRecordCount=1000&f=geojson"
```
**Alternative 2:** ISGS Clearinghouse
```bash
# Illinois State Geological Survey has some bathymetry data
# Browse: https://clearinghouse.isgs.illinois.edu/data
# Look for "Chicago Beaches" singlebeam bathymetry
```
**Alternative 3:** USGS Cook County 2023 Bathymetry (partial coverage)
```bash
# USGS survey of surface water bodies in Cook County (2023, ver 2.0 March 2025)
# https://data.usgs.gov/datacatalog/data/USGS:65a92f5ed34ebad3f34ce977
```

### 4. Indiana (IN) - Server timed out
**Alternative 1:** Direct shapefile download (most reliable)
```bash
curl -L -o in_bathy.zip "https://maps.indiana.edu/download/Hydrology/Water_Bodies_Lakes_Bathymetry.zip"
# Contains LAKE_BATHYMETRY_IDNR_IN.SHP - 85 lakes, 5-ft contours (some 2-ft)
```
**Alternative 2:** IndianaMap portal
```bash
# IDNR Lake Bathymetry 2019 layer:
# https://www.indianamap.org/maps/bea7752105a840afbbee1256f9b7a19b
# Big Ten Academic Alliance Geoportal mirror:
# https://geo.btaa.org/catalog/055b9a57-4eb3-4d64-af8c-f6cd76370187
```

### 5. Massachusetts (MA) - S3 URL returned 404
**Alternative 1:** Updated S3 URL (confirmed working as of Dec 2024 update)
```bash
curl -L -o ma_bathy.zip \
  "https://s3.us-east-1.amazonaws.com/download.massgis.digital.mass.gov/shapefiles/state/dfwbathy.zip"
# ~970 MB, contains shapefile + TIFF + layer files
# Updated Dec 2024 with 104+ additional water bodies
```
**Alternative 2:** MassGIS Data Hub Feature Service (REST API)
```bash
# Query the feature service directly:
curl -o ma_bathy.geojson \
  "https://gis.data.mass.gov/datasets/masswildlife-inland-bathymetry-feature-service.geojson"
```
**Alternative 3:** Quabbin Reservoir (separate dataset)
```bash
# https://www.mass.gov/info-details/massgis-data-quabbin-reservoir-bathymetry
# 10-foot interval bathymetry for Quabbin
```

### 6. Michigan (MI) - Only got 1K of 15.5K features (pagination issue)
**Alternative 1:** ArcGIS Hub bulk download (bypasses pagination)
```bash
# Full GeoJSON download via Hub API:
curl -L -o mi_inland_contours.geojson \
  "https://opendata.arcgis.com/api/v3/datasets/65085a22e09e432c92d722d85f467f7a_4/downloads/data?format=geojson&spatialRefId=4326"
# Or shapefile:
curl -L -o mi_inland_contours.zip \
  "https://opendata.arcgis.com/api/v3/datasets/65085a22e09e432c92d722d85f467f7a_4/downloads/data?format=shp&spatialRefId=4326"
```
**Alternative 2:** Michigan DNR Maps portal
```bash
# https://gis-midnr.opendata.arcgis.com/datasets/midnr::inland-lake-contours/about
# Click "Download" → select format (Shapefile, CSV, GeoJSON, KML, File GDB)
```
**Alternative 3:** MCGI Michigan Geographic Data Library
```bash
# http://www.mcgi.state.mi.us/mgdl/ → search "Inland Lake Contours"
```
**Alternative 4:** Paginated query (if bulk fails)
```bash
# Loop with resultOffset to get all 15.5K features in chunks of 2000:
for offset in $(seq 0 2000 16000); do
  curl -o "mi_bathy_${offset}.geojson" \
    "https://gis-midnr.opendata.arcgis.com/datasets/midnr::inland-lake-contours/query?where=1%3D1&outFields=*&resultOffset=${offset}&resultRecordCount=2000&f=geojson"
done
```

### 7. Nebraska (NE) - Hub returned 400/403
**Alternative 1:** Direct MapServer query
```bash
# Nebraska Game & Parks MapServer (bypasses Hub):
curl -o ne_contours.geojson \
  "https://maps.outdoornebraska.gov/arcgis/rest/services/Programs/LakeMapping/MapServer/0/query?where=1%3D1&outFields=*&resultOffset=0&resultRecordCount=2000&f=geojson"
```
**Alternative 2:** ArcGIS Online FeatureServer
```bash
# Lake Bathymetry Contours Lines (layer 1):
curl -o ne_bathy_lines.geojson \
  "https://services1.arcgis.com/FjPcSmEFuDYlIdKC/arcgis/rest/services/Lake_Bathymetry_Contours/FeatureServer/1/query?where=1%3D1&outFields=*&resultOffset=0&resultRecordCount=2000&f=geojson"
```
**Alternative 3:** Open Data Hub with explicit API
```bash
curl -L -o ne_lake_contours.geojson \
  "https://data-outdoornebraska.opendata.arcgis.com/api/v3/datasets/0f0e603f248d4a4bb79f350db3780d72_0/downloads/data?format=geojson&spatialRefId=4326"
```

### 8. New Hampshire (NH) - Pagination not supported
**Alternative 1:** FTP shapefile download (bypasses pagination entirely)
```bash
# Lines (51 MB zip):
curl -o nh_bathy_lines.zip \
  "https://ftp.granit.unh.edu/GRANIT_Data/Vector_Data/Elevation_and_Derived_Products/d-bathymetry/Bathymetry_Lakes_lines/nh/Bathymetry_Lakes_lines.zip"
# Polygons (98 MB zip):
curl -o nh_bathy_poly.zip \
  "https://ftp.granit.unh.edu/GRANIT_Data/Vector_Data/Elevation_and_Derived_Products/d-bathymetry/Bathymetry_Lakes_polygons/nh/Bathymetry_Lakes_polygons.zip"
```
**Alternative 2:** NH Geodata Portal Hub
```bash
# Lines: https://new-hampshire-geodata-portal-1-nhgranit.hub.arcgis.com/datasets/NHGRANIT::nh-bathymetry-lakes-lines/about
# Polygons: https://new-hampshire-geodata-portal-1-nhgranit.hub.arcgis.com/datasets/NHGRANIT::nh-bathymetry-lakes-polygons/about
```
**Alternative 3:** Query without pagination (single request, may work for smaller subsets)
```bash
curl -o nh_bathy_all.geojson \
  "https://nhgeodata.unh.edu/nhgeodata/rest/services/EDP/Bathymetry_Lakes/MapServer/1/query?where=1%3D1&outFields=*&f=geojson&returnGeometry=true"
```

### 9. North Dakota (ND) - Server returned 500
**Alternative 1:** ArcGIS Hub download API
```bash
# NDGF Lake Contours via Hub API:
curl -L -o nd_lake_contours.geojson \
  "https://opendata.arcgis.com/api/v3/datasets/8e14ea5445404b9191b36e1dc5c2e5cd_0/downloads/data?format=geojson&spatialRefId=4326"
# Or shapefile:
curl -L -o nd_lake_contours.zip \
  "https://opendata.arcgis.com/api/v3/datasets/8e14ea5445404b9191b36e1dc5c2e5cd_0/downloads/data?format=shp&spatialRefId=4326"
```
**Alternative 2:** ND GIS Hub Data Portal direct
```bash
# https://gishubdata.nd.gov/dataset/lake-contours (may need browser)
# https://gishubdata-ndgov.hub.arcgis.com/datasets/ndgf::ndgf-lake-contours/about
```
**Alternative 3:** ND Game & Fish fishing waters page
```bash
# Lake statistics and bathymetric maps: http://gf.nd.gov/fishing/fishing-waters
```

### 10. Ohio (OH) - Service 404
**Alternative 1:** Correct ODNR MapServer endpoint (confirmed working)
```bash
# DOW Lakes Bathymetry MapServer:
# Layer 0 = Lake Bathymetry (polylines), Layer 1 = Lakes (polygons)
curl -o oh_bathy.geojson \
  "https://gis.ohiodnr.gov/arcgis/rest/services/DOW_Services/DOW_Lakes_Bathymetry/MapServer/0/query?where=1%3D1&outFields=*&resultOffset=0&resultRecordCount=1000&f=geojson"
# Paginate for all features:
for offset in $(seq 0 1000 3000); do
  curl -o "oh_bathy_${offset}.geojson" \
    "https://gis.ohiodnr.gov/arcgis/rest/services/DOW_Services/DOW_Lakes_Bathymetry/MapServer/0/query?where=1%3D1&outFields=*&resultOffset=${offset}&resultRecordCount=1000&f=geojson"
done
```
**Alternative 2:** ODNR GIS Open Data Portal
```bash
# Search for bathymetry: https://gis-odnr.opendata.arcgis.com/
# Division of Wildlife lake fishing maps: https://ohiodnr.gov/discover-and-learn/land-water/inland-lakes/fishing-lake-maps
```

### 11. Wisconsin (WI) - No digital contours found (critical - 15K+ lakes)
**Alternative 1:** UMN DRUM Hypsography Dataset (FREE - 750+ WI lakes digitized!)
```bash
# This is the best free source - area-at-depth data for 750+ WI lakes
curl -L -o wi_hypsography.zip \
  "https://conservancy.umn.edu/bitstreams/28cef343-a31c-4cb1-967a-3865f2b95a1c/download"
# DOI: https://doi.org/10.13020/f3xa-5h34
# Contains CSV files with area-at-depth for each lake
```
**Alternative 2:** WI SCO Lake and Depth Maps (browse interface)
```bash
# https://www.sco.wisc.edu/maps/lake/
# Individual lake maps with depth info, need to scrape per-lake
```
**Alternative 3:** WI DNR Surface Water Data Viewer
```bash
# https://dnr.wisconsin.gov/topic/SurfaceWater/swdv
# May have bathymetry layers accessible via REST:
# https://dnrmaps.wi.gov/arcgis/rest/services
```
**Alternative 4:** WisCartography (commercial, $5-15/lake, 160 lakes)
```bash
# https://wiscartography.com/digital-data
# Shapefile + geodatabase format, per-lake purchase
```

### 12. New Brunswick (NB) - FeatureServer intermittently down
**Alternative 1:** Retry the FeatureServer (intermittent availability)
```bash
curl -o nb_bathy.geojson \
  "https://gis-erd-der.gnb.ca/server/rest/services/OpenData/Lake_Depth_Bathymetry_Points/FeatureServer/0/query?where=1%3D1&outFields=*&resultOffset=0&resultRecordCount=2000&f=geojson"
```
**Alternative 2:** GeoNB Data Catalogue
```bash
# Browse: https://www2.gnb.ca/content/gnb/en/departments/erd/open-data/fish-wildlife.html
# Also: https://www.snb.ca/geonb1/e/DC/catalogue-E.asp
# WMS/WFS services may be available even when FeatureServer is down
```

### 13. Nova Scotia (NS) - No GIS data found
**Alternative 1:** Contact inland@novascotia.ca for bulk data
- NS Fisheries has 1000+ lakes inventoried with depth contour maps
- Individual PDF lake maps available online organized by 6 Recreational Fishing Areas
- Portal: https://novascotia.ca/fish/sportfishing/our-lakes/lake-inventory/
**Alternative 2:** NS Environment Lake Survey (ArcGIS layer)
```bash
# ArcGIS Hub layer: https://hub.arcgis.com/maps/1936e489870343cd8a6e79d312f6d0f5
```
**Alternative 3:** Open Canada portal
```bash
# NS Lake Survey Points: https://open.canada.ca/data/en/dataset/e852a640-8deb-9a75-d086-ccd1c20d12b9
```

### 14. Prince Edward Island (PE) - Project/index-supported
**Status:** No direct inland bathymetry dataset surfaced, but official angling/fishing-location and GIS/project resources are real enough to treat PEI as project/index-supported instead of blank.
- Open Data: https://data.princeedwardisland.ca/
- GIS Catalog: https://gov.pe.ca/gis/index.php3?amp=&lang=E&number=77543
- **Recommendation:** Build a PEI waterbody/project index first, then use GLOBathy only as geometry fallback.

### 15. Newfoundland & Labrador (NL) - Project/index-supported, not blank
**Status:** No direct inland lake bathymetry GIS dataset found, but official atlas/reporting and project-style water-resources mapping are available.
- Open Data: http://opendata.gov.nl.ca/
- CHS ocean bathymetry available but not relevant for inland lakes
- **Recommendation:** Build an NL project/index-supported lane first, then use GLOBathy only as fallback.

### 16. Northwest Territories (NT) - No data found
**Status:** No public inland lake bathymetry data.
- NT GoMap: https://www.maps.geomatics.gov.nt.ca/
- GNWT open data: limited to administrative/mining datasets
- **Recommendation:** Use GLOBathy or NONNA-10 (ocean/coastal only).

### 17. Yukon (YT) - Withdrawn official data
**Status:** Yukon had an official bathymetry record, but it was withdrawn for poor accuracy. Separate Yukon boating e-charts still exist for contextual water features.
- Geomatics Yukon: https://geomaticsyukon.ca/
- Yukon Open Data: https://yukon.ca/en/open-data
- **Recommendation:** Track Yukon separately from ordinary weak-source hunting and use fallback/boating e-chart context unless a newer bathymetry program appears.

### 18. Nunavut (NU) - Project/index-supported
**Status:** No territory-wide inland bathymetry dataset surfaced, but official procurement and coastal-resource inventory documents confirm real bathymetry project activity.
- Very remote territory with project-scale rather than territory-scale public bathymetry
- **Recommendation:** Build a Nunavut project index first, then use GLOBathy as geometry fallback.

---

## Summary Priority Matrix

| Priority | State/Province | Est. Features | Status | Fallback |
|----------|---------------|---------------|--------|----------|
| HIGH | Alaska | 998 | ArcGIS REST - retry w/ headers | Add User-Agent header |
| HIGH | New Hampshire | 7,351 | FTP download - BEST | FTP shapefiles bypass pagination |
| HIGH | Iowa | 6,489 | ArcGIS REST - ready | - |
| HIGH | Illinois | 4,828 | MapServer query layers | Query layer 0 directly |
| HIGH | Ohio | 2,809 | MapServer confirmed | DOW_Lakes_Bathymetry endpoint works |
| HIGH | Connecticut | 7,495 | FeatureServer + CTECO | CTECO shapefile or deepmaps.ct.gov |
| HIGH | Michigan | 15,500 | Hub bulk download | Hub API bypasses 1K pagination |
| HIGH | Indiana | 85 lakes | Direct shapefile ZIP | maps.indiana.edu reliable |
| HIGH | Massachusetts | 104+ lakes | S3 download (Dec 2024) | Updated URL confirmed |
| HIGH | North Dakota | varies | Hub API download | Hub API bypasses 500 error |
| HIGH | Nebraska | varies | Direct MapServer query | Bypass Hub, query MapServer |
| HIGH | Wisconsin | 750+ lakes | UMN DRUM hypsography | FREE CSV area-at-depth data |
| MED | Texas | 80+ lakes | Individual lake shapefiles | - |
| MED | Oklahoma | varies | OWRB portal | - |
| MED | Pennsylvania | varies | PASDA portal | - |
| MED | New York | varies | USGS reservoirs + DEC PDFs | - |
| MED | Maine | 68 regions | KML/KMZ depth points | - |
| MED | Oregon | varies | Atlas of Oregon Lakes | - |
| MED | New Brunswick | varies | FeatureServer retry + GeoNB | WMS/WFS alternative |
| MED | Nova Scotia | 1000+ maps | Contact inland@novascotia.ca | PDF maps + ArcGIS Hub layer |
| MED | Saskatchewan | 945 index | Index only, PDFs behind | - |
| LOW | Prince Edward Island | limited | Project/index-supported | Build waterbody/project index + GLOBathy fallback |
| LOW | Newfoundland | limited | Project/index-supported | Atlas/project lane + GLOBathy fallback |
| LOW | Nunavut | limited | Project/index-supported | Project index + GLOBathy fallback |
| LOW | Yukon | limited | Withdrawn official data | E-chart context + fallback |
