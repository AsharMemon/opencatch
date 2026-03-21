"""Fetch ICESat-2 ATL13 inland water granule metadata from NASA CMR."""
import requests, json, os, time

OUTPUT = "/data/icesat2"
os.makedirs(OUTPUT, exist_ok=True)
CMR_URL = "https://cmr.earthdata.nasa.gov/search/granules.json"

# Small bboxes around known training lake regions
regions = [
    ("mn_north", -96, 46, -93, 49),
    ("mn_south", -96, 43, -93, 46),
    ("wi_north", -92, 45, -88, 47),
    ("wi_south", -92, 43, -88, 45),
    ("mi_upper", -90, 45, -84, 47),
    ("mi_lower", -87, 42, -83, 45),
    ("ma", -73, 41, -70, 43),
    ("ny_north", -76, 43, -73, 45),
    ("ab_south", -116, 50, -112, 53),
    ("on_south", -82, 43, -78, 46),
    ("bc_south", -125, 48, -120, 52),
    ("fl_north", -86, 28, -80, 31),
    ("tx_east", -97, 29, -93, 33),
    ("ca_north", -123, 38, -119, 42),
    ("or", -123, 42, -118, 46),
    ("wa", -123, 46, -117, 49),
]

total = 0
all_granules = []
for name, w, s, e, n in regions:
    print(f"Querying ATL13 for {name} ({w},{s},{e},{n})...")
    params = {
        "short_name": "ATL13",
        "version": "006",
        "bounding_box": f"{w},{s},{e},{n}",
        "page_size": 200,
        "sort_key": "-start_date",
    }
    try:
        r = requests.get(CMR_URL, params=params, timeout=30)
        entries = r.json().get("feed", {}).get("entry", [])
        print(f"  Found {len(entries)} granules")
        total += len(entries)
        all_granules.extend(entries)
        with open(f"{OUTPUT}/{name}_granules.json", "w") as f:
            json.dump(entries, f, indent=2)
    except Exception as ex:
        print(f"  Error: {ex}")
    time.sleep(1)

# Save combined
with open(f"{OUTPUT}/all_granules.json", "w") as f:
    json.dump(all_granules, f)

print(f"\nTotal ATL13 granules found: {total}")
print(f"Saved to {OUTPUT}/")
