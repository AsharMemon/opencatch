# OpenCatch International Expansion Roadmap

**Version:** 1.0
**Date:** 2026-03-21
**Status:** Research & Planning Phase

---

## 1. Executive Summary

OpenCatch currently serves the US market with weather, bathymetry, fishing conditions ML, and species data built on NOAA, NHDPlus, USGS, and Sentinel-2 infrastructure. This document outlines a phased international expansion across four target markets:

| Market | Anglers | Priority | Timeline | Key Advantage |
|--------|---------|----------|----------|---------------|
| Caribbean / Central America | ~2M sport fishing tourists/yr | P0 | Q2-Q3 2026 | Charter booking revenue, reef overlay, US-adjacent |
| Australia / New Zealand | ~5.4M | P1 | Q3-Q4 2026 | English-speaking, strong digital infrastructure, state apps fragmented |
| Europe (UK, Scandinavia, Med) | ~25M across EU | P2 | Q1-Q2 2027 | Massive market, ADMIRALTY APIs exist, EMODnet free bathymetry |
| Japan | ~8.7M | P3 | Q3 2027 | High ARPU, gear culture, underserved by Western apps |

**Key finding:** Most international data is available but fragmented. GEBCO provides global low-res bathymetry, Open-Meteo covers global weather, Sentinel-2 is global for satellite composites, and FishBase covers global species. The main gaps are tide/current APIs (need Stormglass or WorldTides), country-specific regulations (manual curation required), and high-res bathymetry (varies by region).

**Estimated total investment:** 12-18 months engineering, ~$50K in API costs/year, and significant regulation data curation effort.

---

## 2. Market 1: Caribbean / Central America

### 2A. Nautical Charts / Bathymetry

| Source | Coverage | Format | Cost | Notes |
|--------|----------|--------|------|-------|
| **NOAA (US territories)** | USVI, Puerto Rico | S-57 ENC | Free | Already integrated for US |
| **GEBCO** | Global (incl. Caribbean) | NetCDF, GeoTIFF | Free | Low resolution (~450m) |
| **IBCCA** (Intl Bathymetric Chart of Caribbean Sea) | Caribbean basin | Grid data | Free | UNESCO/IOC/IHO project, medium resolution |
| **EMODnet Caribbean DTM** | Parts of Caribbean | NetCDF | Free | 2024 DTM release, includes satellite-derived bathymetry |
| **The Nature Conservancy Caribbean Habitat Maps** | All shallow Caribbean waters | GIS shapefiles | Free | Published 2020, includes coral reef, seagrass mapping |
| **National hydrographic offices** | Per-country | Varies | Varies | Most Caribbean nations lack independent HOs; rely on UKHO or NGA |

**Key gap:** High-resolution coastal bathymetry is sparse outside US territories. Our satellite-derived bathymetry ML pipeline (Sentinel-2 + XGBoost) could be a differentiator here for shallow reef areas with clear water.

**Action items:**
- Integrate IBCCA grid data for baseline Caribbean bathymetry
- Extend Sentinel-2 bathymetry pipeline to Caribbean reef zones (clear tropical water = ideal for SDB)
- Incorporate TNC coral reef / seagrass habitat maps as overlay layers

### 2B. Weather & Marine Data

| Source | Data | Coverage | Cost |
|--------|------|----------|------|
| **Open-Meteo** | Wind, temp, pressure, precipitation | Global (Caribbean confirmed) | Free (non-commercial) / $30+/mo commercial |
| **Open-Meteo Marine API** | Wave height, period, direction, swell | Caribbean covered via ICON Wave | Free / paid tiers |
| **Stormglass.io** | Tides, waves, weather | Global including Caribbean | Free tier (50 req/day) / $19-199/mo |
| **WorldTides API** | Tide predictions | Global | Credit-based (~$0.01/prediction) |
| **Copernicus Marine (CMEMS)** | Ocean currents, SST, waves | Global | Free (registration required) |
| **NOAA GFS/GEFS** | Weather forecasts | Global | Free | Already used |

**Tide gap:** NOAA Tides & Currents covers only US territories. For independent Caribbean nations (Bahamas, Costa Rica, Belize, etc.), must use Stormglass or WorldTides.

**Action items:**
- Integrate Stormglass or WorldTides for non-US tide predictions
- Add Copernicus Marine ocean current data for offshore fishing
- Verify Open-Meteo Marine API coverage quality in Caribbean

### 2C. Fishing Regulations

| Country | License Required? | Digital System? | Complexity |
|---------|-------------------|-----------------|------------|
| **Costa Rica** | Yes ($15/8-day, $30/30-day, $50/year) | Online via INCOPESCA | Low |
| **Mexico** | Yes (CONAPESCA permit) | Online system exists | Medium (federal + state) |
| **Bahamas** | Included with cruising permit (boats) | Minimal | Low |
| **Belize** | Yes | Manual process | Low |
| **Panama** | Yes (ARAP license) | Limited digital | Low |
| **US Caribbean** (USVI, PR) | NOAA regulations apply | fisheries.noaa.gov API exists | Already covered |

**Key species regulations (Caribbean):**
- Billfish: Mostly catch-and-release (Costa Rica mandates C&R for all billfish)
- Lobster: Closed seasons vary by country (typically Feb-Jun)
- Conch: Heavily regulated, closed seasons common
- Reef fish: Size/bag limits vary widely

**Action items:**
- Manually curate regulation data for top 6 Caribbean countries
- Build regulation data schema that supports per-country, per-species rules
- Partner with charter operators who maintain current regulation knowledge

### 2D. Species Data

**Top 20 Caribbean Recreational Species:**
1. Blue Marlin
2. Sailfish
3. Yellowfin Tuna
4. Mahi-Mahi (Dorado)
5. Wahoo
6. Roosterfish
7. Tarpon
8. Bonefish
9. Permit
10. Snook
11. Red Snapper
12. Grouper (Nassau, Black)
13. Barracuda
14. King Mackerel
15. Cobia
16. Jack Crevalle
17. Spotted Seatrout
18. Triggerfish
19. Spiny Lobster
20. Queen Snapper

| Source | Data | Coverage | Cost |
|--------|------|----------|------|
| **FishBase** | Species info, distribution, biology | Global | Free API |
| **GBIF** | Species occurrence records | Global | Free API |
| **iNaturalist** | Citizen science observations | Global | Free API |
| **IGFA** | World records, species ID | Global | Proprietary |

### 2E. Water Body Catalogs

| Source | Data | Notes |
|--------|------|-------|
| **TNC Caribbean Habitat Maps** | Coral reefs, seagrass, mangroves | GIS polygon data |
| **Data Basin - Caribbean MPAs** | Marine Protected Areas | Point and polygon datasets |
| **WDPA** (World Database on Protected Areas) | MPAs globally | Free via protectedplanet.net |
| **OpenStreetMap** | Water bodies, coastlines | Variable quality in Caribbean |

### 2F. Fishing Spots / Community

| Platform | Type | Reach |
|----------|------|-------|
| **FishingBooker** | Charter booking platform | 10,800+ boats in 109 countries, strong Caribbean presence |
| **FECOP** (Costa Rica) | Sport fishing federation | Costa Rica authority |
| **The Hull Truth** (forum) | Boating/fishing forum | Strong US/Caribbean community |
| **iOutdoor** | Charter booking | Florida/Caribbean focus |
| **Local Facebook groups** | Community | Per-country, high engagement |

**Monetization opportunity:** Charter boat booking integration. FishingBooker charges 10-15% commission. OpenCatch could partner or build direct charter booking.

### 2G. Safety & Navigation

| Service | Details |
|---------|---------|
| **Coast Guard equivalent** | Varies: US Coast Guard (USVI/PR), Costa Rica Guardacostas, Mexico SEMAR |
| **Emergency number** | 911 (most countries), VHF Ch. 16 |
| **AIS coverage** | MarineTraffic provides Caribbean coverage |
| **Marine VHF** | Channel 16 (distress), Channel 68 (common working) |

### 2H. Market Size & Competition

- **Sport fishing tourism:** Costa Rica alone generates $600M+ annually
- **Charter boat market:** Thousands of active charter operations
- **Competition:** FishingBooker dominates charter booking; no dominant conditions/forecasting app
- **Opportunity:** Weather + fishing conditions forecasting for charter captains and sport fishers

---

## 3. Market 2: Australia / New Zealand

### 3A. Nautical Charts / Bathymetry

| Source | Coverage | Format | Cost | Notes |
|--------|----------|--------|------|-------|
| **Australian Hydrographic Office (AHO)** | Australia, PNG, Solomon Is., Timor-Leste | S-57 ENC | Subscription via AusENC | Not free; requires license per ENC cell |
| **LINZ (NZ)** | New Zealand, parts of Antarctica, SW Pacific | S-57 ENC | **FREE** | Register at linz.govt.nz for full ENC access |
| **GEBCO** | Global | NetCDF | Free | Low resolution baseline |
| **Geoscience Australia** | Australian waters | Various | Some datasets free via data.gov.au | Bathymetry compilations available |
| **AusSeabed** | Australian waters | Various | Free | National bathymetry compilation initiative |
| **NIWA** (NZ) | NZ coastal/marine | Various | Some free via open data portal | High-quality NZ marine data |

**Key advantage:** LINZ provides free ENCs for all NZ waters -- one of the best free nautical chart offerings globally. Australia's AHO requires paid subscriptions.

**Action items:**
- Integrate LINZ free ENCs for NZ launch (low-hanging fruit)
- Evaluate AusSeabed data for Australian coastal bathymetry
- Extend Sentinel-2 bathymetry pipeline to Australian coastal waters (Great Barrier Reef)

### 3B. Weather & Marine Data

| Source | Data | Coverage | Cost |
|--------|------|----------|------|
| **Bureau of Meteorology (BOM)** | Weather forecasts, marine forecasts | Australia | Some APIs free, some paid |
| **MetService** (NZ) | Weather, marine forecasts | New Zealand | API access available |
| **Open-Meteo** | Weather, marine waves | Global (AU/NZ confirmed) | Free / paid tiers |
| **Stormglass.io** | Tides, waves, weather | Global | $19-199/mo |
| **WorldTides** | Tide predictions | Global (AU/NZ stations) | Credit-based |
| **BOM Tides** | Tide predictions | Australia | Published tables (no API) |
| **LINZ Tides** | Tide predictions | New Zealand | Published data |

**Tide gap:** Neither BOM nor LINZ offers a modern tide prediction API. Must rely on Stormglass or WorldTides for programmatic access.

### 3C. Fishing Regulations

Australia has **state-based regulation** -- each state/territory manages its own fisheries independently:

| State/Territory | App/Digital Resource | License Required? |
|-----------------|---------------------|-------------------|
| **NSW** | FishSmart app (free) | Recreational fishing fee |
| **VIC** | VicFish app | Recreational fishing license |
| **QLD** | Qfish database (online) | Some fisheries require license |
| **WA** | Recfishwest app (290K+ downloads) | Varies by fishery |
| **SA** | PIRSA online species limits checker | Recreational fishing license |
| **TAS** | Online rules | License required |
| **NT** | Online rules | License for some species |

**New Zealand:** Managed nationally by Ministry for Primary Industries (MPI). Bag limits set per species. Digital rules available on MPI website.

**Key challenge:** 7+ different regulation systems in Australia alone. Each has different species names, bag limits, size limits, and seasonal closures.

**Action items:**
- Build state-aware regulation engine for Australia
- Scrape/curate regulation data from each state fisheries authority
- NZ is simpler (national system) -- start there

### 3D. Species Data

**Top 20 Recreational Species (AU/NZ combined):**
1. Barramundi
2. Murray Cod
3. Snapper (AU/NZ)
4. Flathead (Dusky, Tiger)
5. Australian Bass
6. Whiting (Sand, King George)
7. Bream (Yellowfin, Black)
8. Kingfish (Yellowtail)
9. Trevally (Giant, Silver)
10. Coral Trout
11. Red Emperor
12. Tailor (Bluefish)
13. Jewfish / Mulloway
14. Mangrove Jack
15. Mackerel (Spanish, Spotted)
16. Trout (Brown, Rainbow -- NZ especially)
17. Kahawai (NZ)
18. Blue Cod (NZ)
19. Hapuka (NZ)
20. Marlin (Black, Blue, Striped)

| Source | Coverage | Notes |
|--------|----------|-------|
| **FishBase** | Global | Free API, good AU/NZ coverage |
| **Atlas of Living Australia (ALA)** | Australia | Free, extensive species occurrence data |
| **NIWA** | NZ marine/freshwater species | Research datasets |
| **GBIF** | Global | Includes AU/NZ records |
| **State fisheries databases** | Per-state | Species identification guides |

### 3E. Water Body Catalogs

| Source | Data | Coverage |
|--------|------|----------|
| **Geoscience Australia - Australian Hydrological Geospatial Fabric (AHGF)** | Rivers, lakes, catchments | National Australia |
| **BOM Water Data Online** | River/lake levels, flow data | National Australia |
| **NIWA NZ River Maps** | 100+ freshwater variables for all NZ rivers | National NZ |
| **NIWA River Environment Classification (REC2)** | River network with attributes | National NZ, ArcGIS geodatabase |
| **DOC Estuaries Database** (NZ) | Estuarine habitats | National NZ |
| **GBRMPA** | Great Barrier Reef marine park zones | GBR region |
| **NZ Marine Reserves** | DOC managed marine reserves | National NZ |
| **CAPAD** (Collaborative Australian Protected Areas Database) | All protected areas including MPAs | National AU |

### 3F. Fishing Spots / Community

| Platform | Region | Notes |
|----------|--------|-------|
| **Recfishwest** | WA | 290K+ app downloads, strong community |
| **FishSmart** (NSW) | NSW | Government app |
| **Fishbrain** | AU/NZ | Growing presence, community features |
| **FishAngler** | AU/NZ | Navionics integration for AU/NZ VIP |
| **Fishing Points** | AU/NZ | Popular mapping/log app |
| **OzFish** | Australia | Conservation + fishing community |
| **Fishing.net.nz** | NZ | Major NZ fishing community website |
| **Ausfish** (forum) | Australia | Long-standing fishing forum |

**Key insight:** Australian market is fragmented across state-specific apps. No single app dominates nationally. This is an opportunity for OpenCatch to provide a unified national experience.

### 3G. Safety & Navigation

| Service | Details |
|---------|---------|
| **AMSA** (Australian Maritime Safety Authority) | Federal maritime regulator |
| **State marine safety** | Each state has marine safety authority |
| **Emergency number** | 000 (AU), 111 (NZ) |
| **Maritime emergency** | VHF Ch. 16, AMSA JRCC: +61 2 6230 6811 |
| **AIS coverage** | AMSA manages coastal AIS network, ~20nm VHF range |
| **Marine VHF** | Ch. 16 (distress), Ch. 67 (small craft safety) |
| **Volunteer Marine Rescue** | VMR / Coastguard organizations in each state |

### 3H. Market Size & Competition

| Metric | Australia | New Zealand |
|--------|-----------|-------------|
| **Recreational anglers** | 4.2 million (21.4% of adults) | 1.2 million (31% of population) |
| **Economic contribution** | $11 billion/year, 100K jobs | $1 billion NZD/year |
| **Licenses sold** | ~1.2 million/year (states that require them) | National marine free, freshwater varies |
| **Dominant apps** | Fragmented (Recfishwest WA, FishSmart NSW, Fishbrain) | fishing.net.nz, Fishbrain |
| **Opportunity** | Unified national app with conditions forecasting | Strong outdoor culture, free LINZ charts |

---

## 4. Market 3: Europe (UK, Scandinavia, Mediterranean)

### 4A. Nautical Charts / Bathymetry

| Source | Coverage | Format | Cost | Notes |
|--------|----------|--------|------|-------|
| **UKHO ADMIRALTY** | UK + global (96% of int'l shipping routes) | S-57, S-63 ENC | AVCS subscription (paid) | Free trial API for south coast England |
| **ADMIRALTY APIs** | UK waters (tides, bathymetry, MPAs, wrecks) | REST API | Tidal API free tier available | Developer portal on Azure |
| **EMODnet Bathymetry** | All European seas | NetCDF, WMS, OGC APIs | **FREE** | Best free bathymetry source for Europe |
| **SHOM** (France) | French waters | S-57 ENC | Paid | Service Hydrographique et Oceanographique de la Marine |
| **BSH** (Germany) | German/Baltic waters | S-57 ENC | Paid | Bundesamt fur Seeschifffahrt und Hydrographie |
| **Kartverket** (Norway) | Norwegian waters | S-57 ENC | Some free data via geonorge.no | Norwegian Mapping Authority |
| **Sjofartsverket** (Sweden) | Swedish waters | S-57 ENC | Paid | Swedish Maritime Administration |
| **IIM** (Italy) | Italian waters | S-57 ENC | Paid | Istituto Idrografico della Marina |
| **IHM** (Spain) | Spanish waters | S-57 ENC | Paid | Instituto Hidrografico de la Marina |
| **GEBCO** | Global | NetCDF | Free | Managed from UK (UKHO + BODC) |

**Key advantage:** EMODnet provides free, harmonized DTM bathymetry for ALL European seas via OGC web services and REST API. This is the best free bathymetry data source outside the US.

**Action items:**
- Integrate EMODnet WMS/REST API for European bathymetry (high priority)
- Use ADMIRALTY Tidal API for UK waters (free tier)
- Norwegian charts via Kartverket geonorge.no (some free data available)

### 4B. Weather & Marine Data

| Source | Data | Coverage | Cost |
|--------|------|----------|------|
| **Open-Meteo** | Weather + marine waves | All of Europe | Free / paid |
| **ADMIRALTY Tidal API** | UK tide predictions | UK (600+ stations) | Free tier available |
| **Stormglass** | Tides, waves, weather | Global | $19-199/mo |
| **WorldTides** | Tide predictions | European stations | Credit-based |
| **Copernicus Marine (CMEMS)** | Ocean currents, SST, waves | European seas | Free |
| **Met Office** (UK) | Weather forecasts, marine | UK | DataPoint API (free, limited) |
| **SMHI** (Sweden) | Weather, marine | Sweden, Baltic | Open data API (free) |
| **MET Norway** | Weather, marine | Norway, Nordic seas | Free API (api.met.no) |
| **Meteo-France** | Weather, marine | France, W. Mediterranean | API available |
| **DWD** (Germany) | Weather | Germany, Europe | Open data (free) |
| **Yr.no** | Weather forecasts | Global (Norwegian) | Free API |

**Key advantage:** MET Norway (api.met.no / yr.no) provides free, high-quality weather API with global coverage. SMHI provides free Swedish marine data. ADMIRALTY Tidal API covers UK tides with a free tier.

### 4C. Fishing Regulations

#### United Kingdom
- **Rod License:** Required for freshwater fishing in England & Wales. Purchased via GOV.UK (digital). 934,000 licenses sold in 2024-25.
- **Cost:** 1-day, 8-day, or 12-month options. Digital license delivery via text/email.
- **Sea fishing:** No license required in England (license required in some regions)
- **Scotland:** Separate system, salmon/trout permits through local estates
- **Digital integration:** Environment Agency has digitized licensing but no public API

#### Scandinavia
| Country | Saltwater | Freshwater | Digital System |
|---------|-----------|------------|----------------|
| **Norway** | FREE (no license needed) | Fiskeravgift (salmon fee) + local fiskekort | Some online sales |
| **Sweden** | Free on coast + 5 big lakes | License required (local fiskekort) | **iFiske app** (leading digital platform) |
| **Finland** | Free (some restrictions) | Fishing management fee + local permits | eräluvat.fi (digital) |
| **Denmark** | Fishing license required | Same license covers both | Online via fisketegn.dk |

**Key insight:** iFiske.se is the dominant digital fishing permit platform in Sweden with a well-established user base. Potential partnership or data integration opportunity.

#### Mediterranean
| Country | License | Bag Limits | Digital? |
|---------|---------|------------|----------|
| **Spain** | Required (regional) | 4kg/person/day (shore), 25kg (boat) | Online purchase in some regions |
| **Italy** | Registration required | Various by species | Some online |
| **France** | Required for freshwater; saltwater varies | Various | Some online via federations |
| **Greece** | Required | Various | Limited digital |
| **Croatia** | Required | Various | Online available |

### 4D. Species Data

**Top 20 European Recreational Species:**
1. Atlantic Salmon
2. Brown Trout / Sea Trout
3. European Sea Bass (Dicentrarchus labrax)
4. Atlantic Cod
5. European Plaice
6. Atlantic Mackerel
7. Pollack
8. Pike
9. Carp (Common, Mirror)
10. Zander (Pike-perch)
11. Perch
12. Tench
13. Bluefin Tuna (Med)
14. Gilt-head Sea Bream (Med)
15. Dentex (Med)
16. Red Mullet (Med)
17. Halibut (Nordic)
18. Arctic Char (Nordic)
19. Coalfish / Saithe (Nordic)
20. Wrasse (UK)

### 4E. Water Body Catalogs

| Source | Coverage | Notes |
|--------|----------|-------|
| **EMODnet** | All European seas | Free, comprehensive marine data portal |
| **EU Water Framework Directive datasets** | EU freshwater bodies | Per-country, standardized reporting |
| **Ordnance Survey** (UK) | UK water features | OS Open Data (free) |
| **Environment Agency** (UK) | UK rivers, lakes | Open data portal |
| **EEA** (European Environment Agency) | EU-wide | Natura 2000 network, MPAs |
| **HELCOM** | Baltic Sea | Marine protected areas, habitats |
| **OSPAR** | NE Atlantic | MPAs, marine data |
| **OpenStreetMap** | Global | Good European coverage for water bodies |
| **Kartverket** (Norway) | Norwegian lakes/rivers | Open data via geonorge.no |

### 4F. Fishing Spots / Community

| Platform | Region | Notes |
|----------|--------|-------|
| **iFiske** | Sweden | Digital permits, fishing area maps |
| **Fishbrain** | Sweden (HQ), global | 20M+ users, strong EU presence |
| **Fishing Booker** | Mediterranean | Charter booking |
| **Angling Trust** | UK | National governing body |
| **World Sea Fishing** (forum) | UK | Sea fishing community |
| **Carp.com** | UK/Europe | Carp fishing dominant in UK/France |
| **Fiskado** | Germany | Fishing permit platform |
| **Haldorado** | Hungary/E. Europe | Fishing community |

**Key insight:** Fishbrain is headquartered in Sweden and has strongest market penetration in Scandinavia and Europe. They are the primary competitor. However, they are weak on fishing conditions forecasting and ML-based predictions -- OpenCatch's core differentiator.

### 4G. Safety & Navigation

| Region | Coast Guard | Emergency | VHF | AIS |
|--------|-------------|-----------|-----|-----|
| **UK** | HM Coastguard (MCA) | 999 / VHF Ch. 16 | Ch. 16 (distress), Ch. 67 (small craft safety) | Full UK coastal AIS |
| **Norway** | Kystvakten (Coast Guard) | 110 (emergency) / VHF Ch. 16 | Full coastal coverage | Extensive AIS network |
| **Sweden** | Kustbevakningen | 112 / VHF Ch. 16 | Coastal coverage | AIS network |
| **France** | CROSS (Centres Regionaux Operationnels de Surveillance et de Sauvetage) | 196 / VHF Ch. 16 | Coastal coverage | AIS network |
| **Spain** | SASEMAR (Salvamento Maritimo) | 900 202 202 / VHF Ch. 16 | Coastal coverage | AIS network |
| **Italy** | Guardia Costiera | 1530 / VHF Ch. 16 | Coastal coverage | AIS network |

### 4H. Market Size & Competition

| Metric | UK | Scandinavia | Mediterranean |
|--------|-----|-------------|---------------|
| **Anglers** | ~2.9M (England/Wales) | ~3-5M (NO+SE+FI+DK) | ~10-15M (ES+IT+FR+GR) |
| **Rod licenses sold** | 934K (2024-25) | Varies by country | Varies |
| **Sea angling economic value** | GBP 1.5-2B/year | Significant tourism revenue | Large charter/tourism market |
| **Primary competitor** | Fishbrain, Navionics | Fishbrain (HQ in Sweden), iFiske | Navionics, FishingBooker |
| **Opportunity** | Conditions forecasting, unified experience | Partnership with iFiske, conditions ML | Charter + conditions for tourism |

---

## 5. Market 4: Japan

### 5A. Nautical Charts / Bathymetry

| Source | Coverage | Format | Cost | Notes |
|--------|----------|--------|------|-------|
| **JHOD** (Japan Hydrographic and Oceanographic Dept.) | Japanese waters, Pacific, Indian Ocean | S-57/S-63 ENC | Paid (via JHA) | Official ENCs encrypted per IHO S-63 |
| **JHA** (Japan Hydrographic Association) | Distribution of JHOD charts | CD-ROM ENC | Paid subscription | Distribution agent for official charts |
| **JODC** (Japan Oceanographic Data Center) | Japanese waters | Various | Some free datasets | Oceanographic data portal |
| **GEBCO** | Global | NetCDF | Free | Low resolution baseline |
| **J-SHIS** (Japan Seismic Hazard Information Station) | Coastal Japan | Various | Free | Seabed/coastal data |

**Key challenge:** Japanese nautical chart data is tightly controlled and sold through JHA. No free ENC equivalent exists. GEBCO + Sentinel-2 SDB pipeline may be best approach for coastal areas.

### 5B. Weather & Marine Data

| Source | Data | Coverage | Cost |
|--------|------|----------|------|
| **JMA** (Japan Meteorological Agency) | Weather, marine forecasts, tsunami | Japan | Some open data, API limited |
| **Open-Meteo** | Weather + marine waves | Japan covered | Free / paid |
| **Stormglass** | Tides, waves, weather | Japan included | $19-199/mo |
| **WorldTides** | Tide predictions | Japanese tide stations | Credit-based |
| **Copernicus Marine** | Ocean currents, SST | NW Pacific included | Free |
| **JODC** | Ocean observations | Japanese waters | Some free |

**Tide data:** JMA publishes official tide tables but has limited API access. Stormglass or WorldTides needed for programmatic access.

### 5C. Fishing Regulations

| Category | Details |
|----------|---------|
| **Saltwater** | Generally **no license required** for recreational saltwater fishing |
| **Freshwater** | Requires "Gyogyo-ken" (fishing permit), JPY 500-3,000 |
| **Permit purchase** | Convenience stores (7-Eleven, Lawson), tackle shops, or **Fish Pass app** |
| **Protected species** | Abalone, sea cucumbers, glass eels prohibited for public |
| **Prefectural rules** | Each of 47 prefectures may have additional regulations |
| **Penalties** | Up to 3 years imprisonment or JPY 30M fine for violations |

**Digital system:** Fish Pass app (fishpass.co.jp) is the leading digital fishing permit platform in Japan -- similar to iFiske in Sweden. Potential integration partner.

### 5D. Species Data

**Top 20 Japanese Recreational Species:**
1. Madai (Red Sea Bream / True Tai)
2. Suzuki (Japanese Sea Perch / Sea Bass)
3. Hirame (Japanese Flounder / Halibut)
4. Kurodai (Black Sea Bream)
5. Aji (Horse Mackerel)
6. Saba (Mackerel)
7. Kisu (Japanese Whiting)
8. Kawahagi (Filefish)
9. Aori-ika (Bigfin Reef Squid)
10. Yamame (Cherry Trout)
11. Iwana (Japanese Char)
12. Ayu (Sweetfish)
13. Black Bass (Largemouth, Smallmouth -- invasive but popular)
14. Buri (Yellowtail)
15. Katsuo (Skipjack Tuna)
16. Maguro (Bluefin Tuna)
17. Isaki (Chicken Grunt)
18. Mebaru (Rockfish)
19. Karei (Flatfish / Righteye Flounder)
20. Tako (Octopus)

**Note:** Japanese species names (and the romanized versions) differ significantly from Western names. Localization is critical -- must support both Japanese and English species names.

### 5E. Water Body Catalogs

| Source | Data | Notes |
|--------|------|-------|
| **GSI** (Geospatial Information Authority of Japan) | Rivers, lakes, coastline | National mapping authority |
| **MLIT** (Ministry of Land, Infrastructure, Transport) | River management data | Water level, flow data |
| **National Land Information Division** | GIS datasets | Free downloads from nlftp.mlit.go.jp |
| **JODC** | Marine data catalog | Oceanographic stations, bathymetry |
| **Biodiversity Center of Japan** | MPAs, natural parks, wildlife | Ministry of Environment |

### 5F. Fishing Spots / Community

| Platform | Type | Notes |
|----------|------|-------|
| **Anglers (anglers.jp)** | Catch logging, heat maps, community | Japan's largest fishing app |
| **Tsurihack (tsurihack.com)** | #1 fishing website in Japan | Content/community |
| **Luremaga (luremaga.jp)** | Lure fishing content | #3 fishing website |
| **Fish Pass** | Digital fishing permits | Leading permit platform |
| **Daiwa / Shimano communities** | Manufacturer communities | Brand-loyal anglers |
| **FishingBooker** | Charter booking | Some Japan coverage |
| **Tsuriba Camera** | Catch sharing app | Fukuoka-based |

**Key insight:** Anglers.jp is the dominant fishing app in Japan with heat maps and catch analytics. Tsurihack.com is the top fishing content website. The market is sophisticated and brand-conscious. Japanese anglers spend heavily on gear and are tech-savvy.

### 5G. Safety & Navigation

| Service | Details |
|---------|---------|
| **Japan Coast Guard (JCG)** | Under Ministry of Land, Infrastructure, Transport and Tourism |
| **Emergency number** | **118** (maritime emergencies, introduced 2000) |
| **General emergency** | 110 (police), 119 (fire/ambulance) |
| **VHF** | Channel 16 (distress) |
| **AIS** | 2 dedicated AIS channels, JCG-managed network |
| **VTS Centers** | 24/7 Vessel Traffic Service monitoring |
| **Small vessel focus** | JCG specifically targets small boat safety (80% of accidents) |

### 5H. Market Size & Competition

| Metric | Value |
|--------|-------|
| **Recreational anglers** | ~8.7 million (2021 survey) |
| **Fishing equipment market** | $425M+ (2022) |
| **Participation rate** | 7.8% of population |
| **Angler education program** | 500K new participants enrolled (2023) |
| **Primary apps** | Anglers.jp, Fish Pass, Tsuriba Camera |
| **Primary websites** | tsurihack.com, anglers.jp, luremaga.jp |
| **ARPU potential** | Very high -- Japanese anglers spend heavily on gear/tech |
| **Localization requirement** | Full Japanese language support mandatory |

**Key challenge:** Japan requires full localization (Japanese language, species names, UI conventions). The existing app ecosystem (Anglers.jp) is well-established. Differentiation must come from ML-based conditions forecasting and satellite-derived bathymetry that local apps lack.

---

## 6. Data Availability Matrix

### What We Have (Global/Existing)

| Data Layer | Source | Global? | Notes |
|-----------|--------|---------|-------|
| Weather forecasts | Open-Meteo, GFS | Yes | Already integrated |
| Marine waves/swell | Open-Meteo Marine API | Yes | Need to verify quality per region |
| Satellite imagery | Sentinel-2 | Yes | Already in bathymetry pipeline |
| Species database | FishBase | Yes | Free API, comprehensive |
| Species occurrences | GBIF, iNaturalist | Yes | Free APIs |
| Low-res bathymetry | GEBCO | Yes | ~450m resolution |
| Protected areas | WDPA | Yes | Free via protectedplanet.net |
| Ocean currents/SST | Copernicus Marine | Yes | Free with registration |

### What We Need Per Market

| Data Layer | Caribbean | AU/NZ | Europe | Japan | Effort |
|-----------|-----------|-------|--------|-------|--------|
| **Tide predictions** | Stormglass/WorldTides | Stormglass/WorldTides | ADMIRALTY (UK) + Stormglass | Stormglass/WorldTides | Medium -- API integration |
| **High-res bathymetry** | SDB pipeline + IBCCA | LINZ (NZ free), AusSeabed | EMODnet (free) | SDB pipeline + JODC | Medium-High |
| **Fishing regulations** | Manual curation (6 countries) | Manual curation (7 states + NZ) | Manual curation (10+ countries) | Manual curation (47 prefectures) | **HIGH** -- ongoing maintenance |
| **Nautical charts** | NOAA (US terr.) only | LINZ free, AHO paid | ADMIRALTY paid, EMODnet free | JHA paid | Cost varies |
| **Water body catalog** | TNC + OSM | AHGF + NIWA REC2 | EMODnet + EU WFD + national | GSI + MLIT | Medium |
| **Species localization** | English OK | English OK | Multi-language needed | Japanese required | High for JP |
| **Local communities** | Partner with charters | Engage state fishing orgs | Partner with iFiske, angling clubs | Partner with anglers.jp | Ongoing |
| **Safety data** | Per-country coast guard | AMSA + state agencies | Per-country | JCG | Low -- static data |

---

## 7. Priority API Integrations Per Market

### Phase 0: Global Infrastructure (Before Any Market Launch)

| Priority | Integration | Purpose | Cost | Effort |
|----------|------------|---------|------|--------|
| P0 | **Stormglass.io** or **WorldTides** | Global tide predictions | $19-199/mo (Stormglass) or credit-based (WorldTides) | 2-3 weeks |
| P0 | **GEBCO** integration | Global baseline bathymetry | Free | 1-2 weeks |
| P0 | **WDPA / Protected Planet** | Global marine protected areas | Free | 1 week |
| P0 | **FishBase API** enhancement | International species data | Free | 1-2 weeks |

### Phase 1: Caribbean (Q2-Q3 2026)

| Priority | Integration | Purpose | Cost | Effort |
|----------|------------|---------|------|--------|
| P0 | **IBCCA bathymetry** | Caribbean baseline depth data | Free | 1 week |
| P0 | **TNC Caribbean habitat maps** | Reef/seagrass overlay | Free | 2 weeks |
| P1 | **SDB pipeline extension** | Satellite-derived Caribbean bathymetry | Compute costs | 3-4 weeks |
| P1 | **FishingBooker API/partnership** | Charter boat integration | Revenue share | 4-6 weeks |
| P2 | **Regulation curation** | 6 Caribbean countries | Staff time | Ongoing |

### Phase 2: Australia / New Zealand (Q3-Q4 2026)

| Priority | Integration | Purpose | Cost | Effort |
|----------|------------|---------|------|--------|
| P0 | **LINZ ENC integration** | Free NZ nautical charts | Free | 3-4 weeks |
| P0 | **NIWA REC2 + NZ River Maps** | NZ freshwater catalog | Free | 2 weeks |
| P1 | **AusSeabed** | Australian bathymetry | Free | 2-3 weeks |
| P1 | **AHGF** (Geoscience Australia) | Australian water body catalog | Free | 2 weeks |
| P1 | **State regulation engine** | 7 AU states + NZ national | Staff time | 6-8 weeks |
| P2 | **Atlas of Living Australia** | AU species data | Free | 1-2 weeks |

### Phase 3: Europe (Q1-Q2 2027)

| Priority | Integration | Purpose | Cost | Effort |
|----------|------------|---------|------|--------|
| P0 | **EMODnet Bathymetry WMS/API** | European seas bathymetry | Free | 2-3 weeks |
| P0 | **ADMIRALTY Tidal API** | UK tide predictions | Free tier | 2 weeks |
| P0 | **MET Norway API (yr.no)** | Nordic weather data | Free | 1-2 weeks |
| P1 | **SMHI Open Data** | Swedish marine data | Free | 1-2 weeks |
| P1 | **iFiske partnership** | Swedish fishing permits/spots | Revenue share | 4-6 weeks |
| P2 | **EU regulation curation** | UK + Scandinavia + Med | Staff time | 8-12 weeks |
| P2 | **Multi-language support** | UI localization | Engineering | 4-6 weeks |

### Phase 4: Japan (Q3 2027)

| Priority | Integration | Purpose | Cost | Effort |
|----------|------------|---------|------|--------|
| P0 | **Japanese localization** | Full UI + species names in Japanese | Engineering + translation | 6-8 weeks |
| P0 | **JODC data** | Japanese marine/ocean data | Some free | 2-3 weeks |
| P1 | **Fish Pass partnership** | Digital permit integration | Revenue share | 4-6 weeks |
| P1 | **SDB pipeline for Japan coast** | Satellite-derived bathymetry | Compute costs | 3-4 weeks |
| P2 | **Prefectural regulation curation** | 47 prefectures | Staff time + translator | 8-12 weeks |
| P2 | **JMA weather data** | Official Japanese forecasts | Varies | 2-3 weeks |

---

## 8. Estimated Effort & Timeline

```
2026 Q2  [====== Caribbean Launch ======]
         - Global infrastructure (tides, GEBCO, species)
         - Caribbean bathymetry (IBCCA + SDB)
         - Charter booking integration
         - 6-country regulation data

2026 Q3  [===== AU/NZ Preparation =====][== Caribbean Iterate ==]
         - LINZ ENC integration (NZ)
         - NIWA water body data
         - AusSeabed integration
         - State regulation engine design

2026 Q4  [======= AU/NZ Launch ========]
         - Australia state regulations (7 states)
         - NZ national regulations
         - AU/NZ species localization
         - Community partnerships

2027 Q1  [===== Europe Preparation ====][== AU/NZ Iterate ==]
         - EMODnet bathymetry
         - ADMIRALTY Tidal API
         - MET Norway / yr.no integration
         - iFiske partnership discussions

2027 Q2  [======= Europe Launch =======]
         - UK + Scandinavia first
         - Regulation curation (UK, Norway, Sweden)
         - Multi-language support (EN, NO, SV, DA, FI)
         - Mediterranean expansion (ES, IT, FR)

2027 Q3  [====== Japan Preparation ====][== Europe Iterate ==]
         - Full Japanese localization
         - JODC data integration
         - Fish Pass partnership
         - Prefectural regulation research

2027 Q4  [======== Japan Launch =======]
         - Japanese species database
         - 47-prefecture regulations
         - Local community engagement
         - Anglers.jp competitive positioning
```

### Resource Estimate

| Category | One-Time | Ongoing Annual |
|----------|----------|----------------|
| **API costs** (Stormglass, WorldTides, etc.) | $500 setup | $5,000-15,000/yr |
| **Chart licensing** (ADMIRALTY, AHO, JHA) | $2,000-5,000 | $5,000-10,000/yr |
| **Engineering** (integrations, localization) | 12-18 months FTE | Maintenance |
| **Regulation curation** | 6-8 months effort | Ongoing updates required |
| **Translation / Localization** | $10,000-20,000 | $5,000/yr updates |
| **Compute** (SDB pipeline expansion) | $2,000-5,000 | $1,000-3,000/yr |
| **Total estimated** | **~$50,000-80,000** | **~$20,000-35,000/yr** |

---

## 9. Go-to-Market Strategy Per Region

### Caribbean / Central America
- **Entry point:** Charter captains and sport fishing lodges
- **Hook:** Free weather/conditions forecasting for charter operations
- **Revenue:** Charter booking commission (10-15%), premium subscriptions
- **Channel:** Partner with FECOP (Costa Rica), sport fishing tournaments, marina partnerships
- **Differentiator:** ML-based fishing conditions + satellite-derived reef bathymetry (clear tropical water = best SDB accuracy)

### Australia / New Zealand
- **Entry point:** New Zealand first (free LINZ charts, simpler regulation system, English-speaking, strong fishing culture)
- **Hook:** Unified national fishing app replacing fragmented state apps
- **Revenue:** Premium subscriptions, gear partnerships (BCF, Anaconda retail chains)
- **Channel:** Fishing shows (IFISH, Fishing Australia TV), Recfishwest partnership, OzFish conservation tie-in
- **Differentiator:** Single app covering all AU states with regulations + conditions ML that state apps lack

### Europe
- **Entry point:** UK and Scandinavia first (English/high-English-proficiency, strong digital infrastructure)
- **Hook:** Conditions forecasting for sea angling (UK) and freshwater/salmon fishing (Scandinavia)
- **Revenue:** Premium subscriptions, iFiske permit integration revenue share, charter booking (Med)
- **Channel:** Angling Trust (UK), fishing media partnerships, Fishbrain competitor positioning
- **Differentiator:** ML fishing conditions forecasting (Fishbrain is social-first, OpenCatch is science/conditions-first)

### Japan
- **Entry point:** English-speaking expat anglers and fishing tourism first, then Japanese market
- **Hook:** Satellite-derived bathymetry and conditions forecasting unavailable in local apps
- **Revenue:** Premium subscriptions (high ARPU market), Fish Pass permit integration
- **Channel:** Fishing media (tsurihack.com ads), tackle shop partnerships (Daiwa, Shimano), tourism boards
- **Differentiator:** Science-based conditions ML and satellite bathymetry -- Japanese apps are primarily social/catch-logging

---

## 10. Risk Assessment

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Regulation data becomes stale | High | High | Automated scraping + community reporting + annual audit |
| Paid chart licensing too expensive | Medium | Medium | Use free alternatives (LINZ, EMODnet, GEBCO, SDB pipeline) |
| Fishbrain dominates European market | High | Medium | Differentiate on conditions ML (not social features) |
| Japanese localization quality issues | High | Medium | Hire native Japanese translator, beta test with JP anglers |
| Tide API costs scale with users | Medium | High | Cache aggressively, pre-compute common locations |
| Country-specific data privacy laws (GDPR, APPI) | High | High | Legal review per market, data residency compliance |
| Caribbean internet connectivity issues | Medium | Medium | Offline-first architecture, pre-download capability |

---

## Appendix A: Key URLs and API Endpoints

### Global
- GEBCO: https://www.gebco.net/data_and_products/gridded_bathymetry_data/
- FishBase API: https://fishbase.ropensci.org/
- Open-Meteo: https://open-meteo.com/en/docs
- Open-Meteo Marine: https://open-meteo.com/en/docs/marine-weather-api
- Stormglass: https://stormglass.io/
- WorldTides: https://www.worldtides.info/developer
- WDPA: https://www.protectedplanet.net/
- Copernicus Marine: https://marine.copernicus.eu/
- GBIF: https://www.gbif.org/developer/summary
- iNaturalist: https://www.inaturalist.org/pages/api+reference

### Caribbean
- IBCCA: https://www.ngdc.noaa.gov/mgg/ibcca/
- TNC Caribbean Maps: Available via TNC data portal
- NOAA US Caribbean regulations: https://www.fisheries.noaa.gov/southeast/rules-and-regulations/current-fishing-regulations-us-caribbean
- FECOP Costa Rica: https://fishcostarica.org/
- INCOPESCA (Costa Rica licenses): Online portal

### Australia / New Zealand
- AHO: https://www.hydro.gov.au/
- AusENC: https://www.hydro.gov.au/prodserv/digital/ausENC/enc.htm
- AusSeabed: https://www.ausseabed.gov.au/
- LINZ Charts: https://charts.linz.govt.nz/
- LINZ Data Service: https://data.linz.govt.nz/
- NIWA River Maps: https://shiny.niwa.co.nz/nzrivermaps/
- NIWA Open Data: https://data-niwa.opendata.arcgis.com/
- Geoscience Australia: https://www.ga.gov.au/
- BOM: http://www.bom.gov.au/
- Atlas of Living Australia: https://www.ala.org.au/
- GBRMPA: https://www.gbrmpa.gov.au/
- AMSA: https://www.amsa.gov.au/

### Europe
- ADMIRALTY APIs: https://admiraltyapi.portal.azure-api.net/
- ADMIRALTY Tidal API: https://www.admiralty.co.uk/access-data/apis
- EMODnet Bathymetry: https://www.emodnet-bathymetry.eu/
- EMODnet Web Services: https://emodnet.ec.europa.eu/en/emodnet-web-service-documentation
- MET Norway API: https://api.met.no/
- Yr.no: https://www.yr.no/
- SMHI Open Data: https://opendata.smhi.se/
- iFiske: https://www.ifiske.se/
- UK Rod License: https://www.gov.uk/fishing-licences
- Kartverket (Norway): https://www.geonorge.no/
- EEA Natura 2000: https://www.eea.europa.eu/

### Japan
- JHOD: https://www1.kaiho.mlit.go.jp/jhd-E.html
- JHA (chart distribution): https://www.jha.or.jp/en/
- JODC: https://www.jodc.go.jp/
- Fish Pass: https://fishpass.co.jp/
- Anglers.jp: https://anglers.jp/
- JCG (Coast Guard): https://www.kaiho.mlit.go.jp/e/
- GSI (maps): https://www.gsi.go.jp/ENGLISH/
- National Land Information: https://nlftp.mlit.go.jp/

---

*Document generated 2026-03-21. Data sources verified via web research. Pricing and availability subject to change.*
