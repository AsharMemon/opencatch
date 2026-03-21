"""Expand lake morphometry coverage for CASTLINE dataset.

Adds morphometry data for missing locations using:
1. Name-matching aliases (e.g. 'Smith Lake, Jasper, AL' -> 'Lewis Smith Lake')
2. Manual research data for high-impact lakes
3. River defaults for river locations
"""
import json
from pathlib import Path

MORPH_PATH = Path("castline/validation/knowledge/lake_morphometry.json")

# Load existing morphometry
with open(MORPH_PATH) as f:
    morph = json.load(f)

# ── Aliases: map location names to existing morphometry keys ──
ALIASES = {
    # Smith Lake variants
    "Smith Lake, Jasper, AL": "Lewis Smith Lake",
    "Smith Lake, Cullman, AL": "Lewis Smith Lake",
    # St. Lawrence River variants (already have it)
    "St. Lawrence River, Waddington, NY": "St. Lawrence River",
    "St. Lawrence River, Clayton, NY": "St. Lawrence River",
    # Mississippi River variants
    "Mississippi River, La Crosse, WI": "Mississippi River",
    "Upper Mississippi River, La Crosse, WI": "Mississippi River",
    "Upper Mississippi River, Clinton, IA": "Mississippi River",
    "La Crosse, WI": "Mississippi River",
    # Sabine River variants
    "Sabine River, Orange, TX": "Sabine River",
    # Sacramento River variants
    "Sacramento River, Sacramento, CA": "Sacramento River",
    # Potomac River variants
    "Potomac River, Charles County, MD": "Potomac River",
    # Delaware River variants
    "Delaware River, Philadelphia, PA": "Delaware River",
    # Santee Cooper variants
    "Santee, SC": "Santee Cooper",
    "St. Stephen, SC": "Santee Cooper",
    # Harris Chain variants
    "Leesburg, FL": "Harris Chain",
    # Toledo Bend variants
    "Zwolle, LA": "Toledo Bend",
    "Many, LA": "Toledo Bend",
    # Sam Rayburn variants
    "Jasper, TX": "Sam Rayburn",
    # Grand Lake variants
    "Grove, OK": "Grand Lake",
    # Clarks Hill / Thurmond variants
    "Clarks Hill Lake, Evans, GA": "Clarks Hill",
    "Clarks Hill Reservoir, Columbia County, GA": "Clarks Hill",
    "Clarks Hill Reservoir, Evans, GA": "Clarks Hill",
    "Clarks Hill, SC": "Clarks Hill",
    # Erie variants
    "Erie, PA": "Lake Erie",
    "Erie , PA": "Lake Erie",
    "Lakeside Marblehead, OH": "Lake Erie",
    # Buggs Island = Kerr Reservoir
    "Buggs Island Reservoir, Clarksville, VA": "Buggs Island",
    "Buggs Island, Mecklenburg County, VA": "Buggs Island",
    # Lake Guntersville variants
    "Scottsboro, AL": "Lake Guntersville",
    "Scottsboro , AL": "Lake Guntersville",
    # Kissimmee variants
    "Kissimmee, FL": "Kissimmee Chain",
    "Kissimmee Chain of Lakes, Kissimmee, FL": "Kissimmee Chain",
    # Saginaw Bay
    "Saginaw Bay, Bay City, MI": "Saginaw Bay",
    "Saginaw Bay, Bangor Charter Township, MI": "Saginaw Bay",
    "Bay City, MI": "Saginaw Bay",
    # Lake Oahe variants
    "Mobridge, SD": "Lake Oahe",
    # Anderson = Hartwell
    "Anderson, SC": "Lake Hartwell",
    "Anderson , SC": "Lake Hartwell",
    # Oakwood = Lanier
    "Oakwood, GA": "Lake Lanier",
    "Gainesville, GA": "Lake Lanier",
    # Bays de Noc
    "Bays de Noc, Escanaba, MI": "Bays de Noc",
    # Table Rock variants
    "Branson, MO": "Table Rock Lake",
    # Red River variants
    "Red River, Shreveport, LA": "Red River",
    "Red River, Shreveport-Bossier, LA": "Red River",
    "Red River, Shreveport/Bossier City, LA": "Red River",
    "Red River, Natchitoches, LA": "Red River",
    "Shreveport, LA": "Red River",
    # Lake of the Ozarks
    "Lake of the Ozarks, Osage Beach, MO": "Lake of the Ozarks",
    "Camdenton, MO": "Lake of the Ozarks",
    # Watts Bar
    "Watts Bar Reservoir, Kingston, TN": "Watts Bar",
    # Lewisville
    "Lewisville Lake, Lewisville, TX": "Lewisville Lake",
    # Milford Lake
    "Milford Lake, Junction City, KS": "Milford Lake",
    # Sturgeon Bay = Green Bay
    "Sturgeon Bay, Door County, WI": "Sturgeon Bay",
    "Sturgeon Bay, WI": "Sturgeon Bay",
    # Stockton Lake
    "Stockton, MO": "Stockton Lake",
    # Waukesha = Pewaukee Lake area
    "Waukesha, WI": "Pewaukee Lake",
    # Lake Mills = Rock Lake
    "Lake Mills, WI": "Rock Lake",
    # Perryville = Susquehanna Flats
    "Perryville, MD": "Chesapeake Bay",
    "La Plata, MD": "Chesapeake Bay",
    "Williamsport, MD": "Potomac River",
    "Brunswick, MD": "Potomac River",
}

# ── New lake morphometry data (researched) ──
NEW_LAKES = {
    "St. Johns River": {
        "area_acres": 0,  # river
        "max_depth_ft": 12,
        "shore_dev": 1.0,
        "lat": 29.65,
        "lon": -81.63,
        "is_river": True,
    },
    "James River": {
        "area_acres": 0,
        "max_depth_ft": 25,
        "shore_dev": 1.0,
        "lat": 37.53,
        "lon": -77.43,
        "is_river": True,
    },
    "Arkansas River": {
        "area_acres": 0,
        "max_depth_ft": 30,
        "shore_dev": 1.0,
        "lat": 35.75,
        "lon": -95.37,
        "is_river": True,
    },
    "Red River": {
        "area_acres": 0,
        "max_depth_ft": 40,
        "shore_dev": 1.0,
        "lat": 32.51,
        "lon": -93.75,
        "is_river": True,
    },
    "Kissimmee Chain": {
        "area_acres": 34948,
        "max_depth_ft": 12,
        "shore_dev": 2.1,
        "lat": 28.30,
        "lon": -81.42,
    },
    "Buggs Island": {  # John H. Kerr Reservoir
        "area_acres": 48900,
        "max_depth_ft": 108,
        "shore_dev": 4.5,
        "lat": 36.60,
        "lon": -78.32,
    },
    "Clarks Hill": {  # J. Strom Thurmond
        "area_acres": 71100,
        "max_depth_ft": 165,
        "shore_dev": 5.2,
        "lat": 33.66,
        "lon": -82.20,
    },
    "Lake Powell": {
        "area_acres": 161390,
        "max_depth_ft": 560,
        "shore_dev": 7.0,
        "lat": 37.07,
        "lon": -111.25,
    },
    "Lake Chatuge": {
        "area_acres": 7050,
        "max_depth_ft": 125,
        "shore_dev": 3.2,
        "lat": 34.93,
        "lon": -83.83,
    },
    "Lake of the Ozarks": {
        "area_acres": 54000,
        "max_depth_ft": 130,
        "shore_dev": 8.5,
        "lat": 38.13,
        "lon": -92.62,
    },
    "Saginaw Bay": {
        "area_acres": 700000,
        "max_depth_ft": 50,
        "shore_dev": 1.5,
        "lat": 43.83,
        "lon": -83.83,
    },
    "Bays de Noc": {
        "area_acres": 125000,
        "max_depth_ft": 80,
        "shore_dev": 2.0,
        "lat": 45.72,
        "lon": -86.95,
    },
    "Watts Bar": {
        "area_acres": 39090,
        "max_depth_ft": 72,
        "shore_dev": 4.1,
        "lat": 35.73,
        "lon": -84.78,
    },
    "Lake Ray Roberts": {
        "area_acres": 29350,
        "max_depth_ft": 110,
        "shore_dev": 3.0,
        "lat": 33.37,
        "lon": -97.03,
    },
    "Lewisville Lake": {
        "area_acres": 29592,
        "max_depth_ft": 67,
        "shore_dev": 3.8,
        "lat": 33.10,
        "lon": -96.98,
    },
    "Milford Lake": {
        "area_acres": 15700,
        "max_depth_ft": 65,
        "shore_dev": 4.0,
        "lat": 39.10,
        "lon": -96.93,
    },
    "Pokegama Lake": {
        "area_acres": 6585,
        "max_depth_ft": 105,
        "shore_dev": 3.5,
        "lat": 47.23,
        "lon": -93.53,
    },
    "Saranac Lake": {
        "area_acres": 4284,
        "max_depth_ft": 62,
        "shore_dev": 3.0,
        "lat": 44.32,
        "lon": -74.13,
    },
    "Stockton Lake": {
        "area_acres": 24900,
        "max_depth_ft": 142,
        "shore_dev": 4.5,
        "lat": 37.63,
        "lon": -93.73,
    },
    "Lake Ouachita": {
        "area_acres": 40100,
        "max_depth_ft": 200,
        "shore_dev": 5.0,
        "lat": 34.57,
        "lon": -93.22,
    },
    "Tims Ford Lake": {
        "area_acres": 10700,
        "max_depth_ft": 155,
        "shore_dev": 4.2,
        "lat": 35.18,
        "lon": -86.25,
    },
    "Sturgeon Bay": {
        "area_acres": 320000,
        "max_depth_ft": 100,
        "shore_dev": 2.5,
        "lat": 44.83,
        "lon": -87.37,
    },
    "Apalachicola Bay": {
        "area_acres": 155000,
        "max_depth_ft": 15,
        "shore_dev": 1.8,
        "lat": 29.67,
        "lon": -85.02,
    },
    "Aransas Bay": {
        "area_acres": 58000,
        "max_depth_ft": 8,
        "shore_dev": 1.5,
        "lat": 28.03,
        "lon": -96.98,
    },
    "Tombigbee River": {
        "area_acres": 0,
        "max_depth_ft": 35,
        "shore_dev": 1.0,
        "lat": 33.50,
        "lon": -88.42,
        "is_river": True,
    },
    "Pewaukee Lake": {
        "area_acres": 2498,
        "max_depth_ft": 45,
        "shore_dev": 2.0,
        "lat": 43.08,
        "lon": -88.27,
    },
    "Rock Lake": {
        "area_acres": 1371,
        "max_depth_ft": 55,
        "shore_dev": 1.8,
        "lat": 43.08,
        "lon": -88.92,
    },
    "Lake Conroe": {
        "area_acres": 20118,
        "max_depth_ft": 70,
        "shore_dev": 3.2,
        "lat": 30.42,
        "lon": -95.55,
    },
    # Louisiana small lakes/bayous — use defaults for bayou fishing
    "Atchafalaya Basin": {
        "area_acres": 595000,
        "max_depth_ft": 20,
        "shore_dev": 1.0,
        "lat": 30.28,
        "lon": -91.55,
        "is_river": True,
    },
}

# Add location-specific entries that map to existing data with city coordinates
LOCATION_OVERRIDES = {
    "St. Johns River, Palatka, FL": {"lat": 29.65, "lon": -81.63},
    "James River, Richmond, VA": {"lat": 37.53, "lon": -77.43},
    "James River, Henrico, VA": {"lat": 37.58, "lon": -77.32},
    "Arkansas River, Muskogee, OK": {"lat": 35.75, "lon": -95.37},
    "Lodi, CA": {"lat": 38.13, "lon": -121.27},  # California Delta
    "Oil City, LA": {"lat": 32.74, "lon": -93.97},  # Caddo Lake area
    "Cherryvale, KS": {"lat": 37.27, "lon": -95.55},  # Elk City Lake
    "Lacombe, LA": {"lat": 30.32, "lon": -89.94},  # Bayou Lacombe
    "Breaux Bridge, LA": {"lat": 30.27, "lon": -91.90},  # Henderson Swamp
    "Hurricane, UT": {"lat": 37.17, "lon": -113.30},  # Sand Hollow/Quail Creek
    "Ferriday, LA": {"lat": 31.63, "lon": -91.55},  # Lake Concordia
    "Hayes, LA": {"lat": 30.10, "lon": -92.78},  # Lacassine Bayou
    "New Roads, LA": {"lat": 30.70, "lon": -91.44},  # False River
    "Huntingtington Beach, CA": {"lat": 33.66, "lon": -118.00},  # Coastal
    "Huntington Beach, CA": {"lat": 33.66, "lon": -118.00},
    "Long Beach, CA": {"lat": 33.77, "lon": -118.19},
    "San Diego, CA": {"lat": 32.71, "lon": -117.16},
    "Newport Beach, CA": {"lat": 33.62, "lon": -117.93},
}

# Louisiana bayou defaults
LOUISIANA_BAYOU_DEFAULTS = {
    "area_acres": 500,
    "max_depth_ft": 15,
    "shore_dev": 1.0,
    "is_river": True,
}

# California coastal defaults
CALIFORNIA_COASTAL_DEFAULTS = {
    "area_acres": 0,
    "max_depth_ft": 30,
    "shore_dev": 1.0,
    "is_river": True,
}

# ── Apply updates ──
# 1. Add new lakes
for name, data in NEW_LAKES.items():
    if name not in morph:
        morph[name] = {k: v for k, v in data.items() if k != "is_river"}
        print(f"  Added: {name}")

# 2. Add aliases
for alias, target in ALIASES.items():
    if target not in morph:
        print(f"  WARNING: alias target '{target}' not in morphometry")

# Count coverage improvement
import pandas as pd
df = pd.read_csv("castline/validation/data/assembled/validation_dataset_v4.csv")

def resolve_morphometry(location):
    """Resolve morphometry for a location string."""
    # Direct alias
    if location in ALIASES:
        target = ALIASES[location]
        if target in morph:
            return morph[target]

    # Try matching lake name from location
    for lake_name in morph:
        if lake_name.lower() in location.lower():
            return morph[lake_name]

    return None

before_missing = df['area_acres'].isna().sum()

# Apply to dataset
for idx, row in df.iterrows():
    if pd.isna(row.get('area_acres')):
        loc = row['location']
        m = resolve_morphometry(loc)
        if m:
            df.at[idx, 'area_acres'] = m.get('area_acres', 0)
            df.at[idx, 'max_depth_ft'] = m.get('max_depth_ft', 0)
            df.at[idx, 'shore_dev'] = m.get('shore_dev', 1.0)
            if pd.isna(row.get('lat')) or row.get('lat') == 0:
                df.at[idx, 'lat'] = m.get('lat', 0)
                df.at[idx, 'lon'] = m.get('lon', 0)
        elif ', LA' in loc:
            df.at[idx, 'area_acres'] = LOUISIANA_BAYOU_DEFAULTS['area_acres']
            df.at[idx, 'max_depth_ft'] = LOUISIANA_BAYOU_DEFAULTS['max_depth_ft']
            df.at[idx, 'shore_dev'] = LOUISIANA_BAYOU_DEFAULTS['shore_dev']
        elif ', CA' in loc:
            df.at[idx, 'area_acres'] = CALIFORNIA_COASTAL_DEFAULTS['area_acres']
            df.at[idx, 'max_depth_ft'] = CALIFORNIA_COASTAL_DEFAULTS['max_depth_ft']
            df.at[idx, 'shore_dev'] = CALIFORNIA_COASTAL_DEFAULTS['shore_dev']

    # Apply coordinate overrides
    if row['location'] in LOCATION_OVERRIDES:
        override = LOCATION_OVERRIDES[row['location']]
        if pd.isna(row.get('lat')) or row.get('lat') == 0:
            df.at[idx, 'lat'] = override['lat']
            df.at[idx, 'lon'] = override['lon']

after_missing = df['area_acres'].isna().sum()

print(f"\n=== Morphometry Coverage ===")
print(f"Before: {before_missing} rows missing ({100*before_missing/len(df):.1f}%)")
print(f"After:  {after_missing} rows missing ({100*after_missing/len(df):.1f}%)")
print(f"Filled: {before_missing - after_missing} rows")

# Save updated morphometry
with open(MORPH_PATH, 'w') as f:
    json.dump(morph, f, indent=2)
print(f"\nSaved {len(morph)} lakes to {MORPH_PATH}")

# Save updated dataset
out_path = "castline/validation/data/assembled/validation_dataset_v4.csv"
df.to_csv(out_path, index=False)
print(f"Saved updated dataset to {out_path}")

# Show remaining missing locations
still_missing = df[df['area_acres'].isna()]['location'].value_counts()
if len(still_missing) > 0:
    print(f"\nStill missing ({len(still_missing)} locations, {after_missing} rows):")
    for loc, count in still_missing.head(20).items():
        print(f"  {loc}: {count}")
