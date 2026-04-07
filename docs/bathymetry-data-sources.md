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
- **DEC Maps viewer:** `https://nysdec.maps.arcgis.com/apps/webappviewer/index.html?id=ae91142c812a4ab997ba739ed9723e6e`
- **USGS East of Hudson:** `https://data.usgs.gov/datacatalog/data/USGS:5f7c85a082ce1d74e7db5363` (shapefiles for reservoirs)
- **Status:** DEC has 100s of lake maps but mostly as PDFs/images. USGS has some reservoir shapefiles.

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
- **Format:** Shapefiles, geodatabases, or KMZ

### Pennsylvania - PFBC / PASDA
- **PASDA portal:** `https://www.pasda.psu.edu/`
- **Dataset:** `https://www.pasda.psu.edu/uci/DataSummary.aspx?dataset=1103`

### Vermont - ANR Lake Champlain + Inland
- **Lake Champlain Bathymetry:** `https://geodata.vermont.gov/datasets/vt-lake-champlain-bathymetry`
- **ANR GIS Hub:** `https://gis-vtanr.hub.arcgis.com/`
- **Depth charts (PDF):** `https://dec.vermont.gov/watershed/lakes-ponds/data-maps/charts`

### Virginia - DWR GIS Data
- **DWR GIS Download:** `https://dwr.virginia.gov/gis/data/download/`
- **GIS Clearinghouse:** `https://vgin.vdem.virginia.gov/pages/cl-data-download`

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
- **Status:** Very few natural lakes. No bathymetry layer found.

### Hawaii
- **State GIS:** `https://geoportal.hawaii.gov/`
- **Status:** Ocean bathymetry available; inland lake bathymetry not found (very few lakes).

### Kansas
- **KDWP Maps (PDF):** `https://ksoutdoors.gov/Fishing/Where-to-Fish-in-Kansas/Bathymetric-Lake-Maps`
- **Geoportal:** `https://hub.kansasgis.org/`

### Louisiana
- **LDWF:** `https://www.wlf.louisiana.gov/page/wma-gis-data-download`
- **USGS individual lakes:** Lake Maurepas contours

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
- **Offshore contours only:** `https://gisdata-njdep.opendata.arcgis.com/datasets/njdep::bathymetric-contours-of-new-jersey`

### New Mexico
- **RGIS:** `https://rgis.unm.edu/`
- **Status:** No lake bathymetry found.

### Rhode Island
- **DEM maps (PDF):** `https://dem.ri.gov/sites/g/files/xkgbur861/files/maps/mapfile/pondbath.pdf`
- **RIGIS:** `https://www.rigis.org/`

### West Virginia
- **DNR GIS:** `https://wvdnr.gov/gis-mapping/`
- **Status:** Lake fishing maps (PDF). No GIS bathymetry layer found.

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
- **Status:** Very few lakes. No bathymetry data found.

### Newfoundland & Labrador
- **Open Data:** `http://opendata.gov.nl.ca/`
- **Status:** No inland lake bathymetry found. Ocean bathymetry from CHS available.

### Northwest Territories
- **NT GoMap:** `https://www.maps.geomatics.gov.nt.ca/`
- **Status:** No lake bathymetry GIS data found. Some scanned maps may exist.

### Yukon
- **Geomatics Yukon:** `https://geomaticsyukon.ca/`
- **Status:** Lake bathymetry as scanned maps only, not geospatial.

### Nunavut
- **Canada-Nunavut Geoscience Office**
- **Status:** No inland lake bathymetry data found.

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

### 14. Prince Edward Island (PE) - Very few lakes
**Status:** No bathymetry data found. Very few natural inland lakes.
- Open Data: https://data.princeedwardisland.ca/
- GIS Catalog: https://gov.pe.ca/gis/index.php3?amp=&lang=E&number=77543
- **Recommendation:** Skip or use GLOBathy synthetic estimates.

### 15. Newfoundland & Labrador (NL) - No inland data found
**Status:** No inland lake bathymetry GIS data available.
- Open Data: http://opendata.gov.nl.ca/
- CHS ocean bathymetry available but not relevant for inland lakes
- **Recommendation:** Use GLOBathy synthetic estimates or contact NL Dept of Environment.

### 16. Northwest Territories (NT) - No data found
**Status:** No public inland lake bathymetry data.
- NT GoMap: https://www.maps.geomatics.gov.nt.ca/
- GNWT open data: limited to administrative/mining datasets
- **Recommendation:** Use GLOBathy or NONNA-10 (ocean/coastal only).

### 17. Yukon (YT) - Scanned maps only
**Status:** Lake bathymetry exists as scanned maps only, not geospatial vector data.
- Geomatics Yukon: https://geomaticsyukon.ca/
- Yukon Open Data: https://yukon.ca/en/open-data
- **Recommendation:** Use GLOBathy synthetic estimates.

### 18. Nunavut (NU) - No data
**Status:** No inland lake bathymetry data found.
- Very remote territory, no public GIS bathymetry datasets
- **Recommendation:** Use GLOBathy synthetic estimates.

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
| LOW | Prince Edward Island | ~0 | No data - skip | GLOBathy |
| LOW | Newfoundland | ~0 | No inland data | GLOBathy |
| LOW | NWT/Yukon/Nunavut | ~0 | No data | GLOBathy |
