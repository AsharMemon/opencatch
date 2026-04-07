# MN DNR Lake Bathymetry Data — Key Legal Questions

## Status Summary

- **Data source:** Minnesota Geospatial Commons (gisdata.mn.gov) + DNR LakeFinder
- **License on gisdata.mn.gov:** "License not specified" / no formal license listed
- **Access constraints:** Listed as "None" on the Geospatial Commons page
- **DNR LakeFinder page:** States that commercial use requires review and agreement to the DNR's General Data and Software License Agreement
- **Agreement form:** http://files.dnr.state.mn.us/eco/lakemapping/agreement.pdf
- **Contact for GIS data:** gisinfo.mngeo@state.mn.us

---

## Questions Requiring DNR Clarification

### 1. Display of Contour Maps in a Mobile App

**Question:** Can we render DNR depth contour lines and lake outlines inside the OpenCatch mobile app for end users?

**Why it matters:** This is our primary use case. We need to know whether displaying the data in an interactive mobile map (as vector tiles or rendered layers) constitutes "reproduction" or "republication" under the agreement, and whether it is permitted under a commercial license.

**Risk if unanswered:** If display is not permitted, we would need to rely entirely on derivative/predicted data and could not show official contours.

---

### 2. Training ML Models on DNR Sonar Data

**Question:** Does the data use agreement cover derivative products created by training machine learning models on DNR bathymetric survey data?

**Why it matters:** Our ML pipeline ingests DNR depth contours and DEMs as ground-truth training data. The trained model then produces independent depth predictions for unmapped lakes. The model weights encode statistical patterns learned from the data, but no individual DNR measurements are stored or reproduced in the output.

**Key distinction:** We are not redistributing DNR data — we are learning from it and producing new predictions. We need to know whether this is treated differently under the agreement.

**Risk if unanswered:** If derivative works are restricted, our entire bathymetry prediction pipeline may need alternative training data.

---

### 3. Attribution Requirements

**Question:** What attribution must appear on each lake map that uses DNR data? Is a simple credit line sufficient, or is specific language/logo placement required?

**Possible approaches:**
- Credit line on every map view: "Bathymetry data: Minnesota DNR"
- Attribution in app settings / "About" screen
- Link back to DNR LakeFinder for the source lake

**Why it matters:** We want to comply fully but also need to understand how prominently the attribution must appear in a mobile UI context.

---

### 4. Charging Users for Access

**Question:** Are there restrictions on monetizing an app that displays or derives value from DNR bathymetric data? Specifically:
- Can we offer a free tier that includes DNR-sourced bathymetry maps?
- Can we include DNR-derived maps in a paid subscription tier?
- Does charging for premium features (not the data itself, but the app experience) trigger additional licensing requirements?

**Why it matters:** OpenCatch may offer both free and premium tiers. We need to know whether including DNR data in a paid product changes the licensing terms or requires revenue sharing.

---

### 5. Redistribution of Processed/Derived Data

**Question:** Can we redistribute processed versions of DNR data (e.g., vector tiles generated from contour shapefiles, simplified polygons, rasterized depth grids) through our app or API?

**Key scenarios:**
- **Tile server:** Contour shapefiles converted to Mapbox vector tiles served to the app
- **Offline downloads:** Users download bathymetry tiles for offline use within the app
- **API responses:** Depth values returned from our API that originate from DNR measurements

**Why it matters:** Technical delivery of map data inherently involves format conversion and redistribution through our infrastructure. We need to know whether this is permitted or whether we must direct users to DNR sources.

---

## Action Items

| Priority | Action | Status |
|----------|--------|--------|
| 1 | Send email to DNR Lake Mapping Program | Pending |
| 2 | Download and review agreement PDF in full | Pending |
| 3 | Complete and sign the agreement form | Blocked on #1 |
| 4 | Follow up with gisinfo.mngeo@state.mn.us if no response in 2 weeks | Pending |
| 5 | Research whether other apps (Navionics, onXmaps) have DNR agreements as precedent | Pending |

## Precedent / Comparable Products

Other commercial products that appear to use MN DNR bathymetry data (worth investigating their licensing approach):
- **Navionics (Garmin)** — displays MN lake contours in their app/charts
- **LakeMaster (Humminbird)** — sells MN lake map chips, likely has a formal agreement
- **onX Hunt/Fish** — displays lake depth contours
- **Fishidy** — community fishing maps with some contour data

Understanding how these companies licensed DNR data could inform our approach and demonstrate that commercial agreements are feasible.
