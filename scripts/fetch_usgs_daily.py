"""Fetch USGS daily water data for all fishing event locations.

Uses USGS Water Services API to find nearest gauges and pull daily values.
Key parameters:
  00060 = Discharge (cfs)
  00065 = Gage height (ft)
  00010 = Water temperature (C)
  00300 = Dissolved oxygen (mg/L)
  63680 = Turbidity (FNU)
  62614 = Lake/reservoir elevation (ft NGVD29)
"""
import pandas as pd
import numpy as np
import requests
import time
import os
from collections import defaultdict

DATASET = "castline/validation/data/assembled/validation_dataset_v10.csv"
CACHE_FILE = "castline/validation/data/raw/usgs_daily_expanded.csv"
SITE_CACHE = "castline/validation/data/raw/usgs_site_cache.csv"

PARAMS = {
    "00060": "discharge_cfs",
    "00065": "gage_height_ft",
    "00010": "water_temp_c",
    "00300": "dissolved_oxygen_mgL",
    "63680": "turbidity_fnu",
    "62614": "reservoir_elevation_ft",
}


def find_usgs_sites(lat, lon, radius_miles=15):
    """Find USGS sites within radius of a lat/lon point."""
    url = "https://waterservices.usgs.gov/nwis/site/"
    params = {
        "format": "rdb",
        "bBox": f"{lon-0.3},{lat-0.3},{lon+0.3},{lat+0.3}",
        "siteType": "LK,ST,SP",  # Lake, Stream, Spring
        "siteStatus": "all",
        "hasDataTypeCd": "dv",  # daily values
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            return []
        lines = [l for l in resp.text.split("\n")
                 if l and not l.startswith("#") and not l.startswith("5s")]
        if len(lines) < 2:
            return []

        header = lines[0].split("\t")
        sites = []
        for line in lines[2:]:  # skip header and format line
            cols = line.split("\t")
            if len(cols) < 4:
                continue
            try:
                site_no = cols[header.index("site_no")]
                site_lat = float(cols[header.index("dec_lat_va")])
                site_lon = float(cols[header.index("dec_long_va")])
                site_name = cols[header.index("station_nm")]
                dist_deg = np.sqrt((site_lat - lat)**2 + (site_lon - lon)**2)
                dist_km = dist_deg * 111  # rough conversion
                if dist_km <= radius_miles * 1.6:
                    sites.append({
                        "site_no": site_no,
                        "name": site_name,
                        "lat": site_lat,
                        "lon": site_lon,
                        "dist_km": dist_km,
                    })
            except (ValueError, IndexError):
                continue

        return sorted(sites, key=lambda s: s["dist_km"])
    except Exception as e:
        return []


def fetch_daily_values(site_no, param_code, start_date, end_date):
    """Fetch daily values for a site and parameter."""
    url = "https://waterservices.usgs.gov/nwis/dv/"
    params = {
        "format": "json",
        "sites": site_no,
        "parameterCd": param_code,
        "startDT": start_date,
        "endDT": end_date,
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            return {}
        data = resp.json()
        ts = data.get("value", {}).get("timeSeries", [])
        if not ts:
            return {}

        values = {}
        for series in ts:
            for val in series.get("values", [{}])[0].get("value", []):
                date = val["dateTime"][:10]
                try:
                    v = float(val["value"])
                    if v != -999999:
                        values[date] = v
                except (ValueError, TypeError):
                    continue
        return values
    except Exception:
        return {}


def main():
    print("=" * 60)
    print("USGS Daily Water Data Expansion")
    print("=" * 60)

    # Load dataset
    df = pd.read_csv(DATASET, low_memory=False)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df[df["date"].notna() & df["lat"].notna() & df["lon"].notna()].copy()
    print(f"Events: {len(df)}, Locations: {df.location.nunique()}")

    # Current USGS coverage
    for param_name in PARAMS.values():
        if param_name in df.columns:
            pct = df[param_name].notna().mean() * 100
            print(f"  Current {param_name}: {pct:.1f}%")

    # Get unique locations
    locations = df.groupby("location").agg(
        lat=("lat", "first"),
        lon=("lon", "first"),
        n_events=("date", "count"),
        min_date=("date", "min"),
        max_date=("date", "max"),
    ).reset_index()
    print(f"\nSearching USGS sites for {len(locations)} locations...")

    # Load site cache
    site_cache = {}
    if os.path.exists(SITE_CACHE):
        sc = pd.read_csv(SITE_CACHE)
        for _, r in sc.iterrows():
            key = f"{r.loc_lat:.4f},{r.loc_lon:.4f}"
            if key not in site_cache:
                site_cache[key] = []
            site_cache[key].append(r.to_dict())
        print(f"Site cache: {len(site_cache)} locations")

    # Find nearest USGS sites for each location
    sites_found = 0
    new_sites = []
    for i, row in locations.iterrows():
        key = f"{row.lat:.4f},{row.lon:.4f}"
        if key in site_cache:
            if site_cache[key]:
                sites_found += 1
            continue

        sites = find_usgs_sites(row.lat, row.lon)
        if sites:
            sites_found += 1
            for s in sites[:3]:  # keep top 3 nearest
                new_sites.append({
                    "location": row.location,
                    "loc_lat": row.lat,
                    "loc_lon": row.lon,
                    "site_no": s["site_no"],
                    "site_name": s["name"],
                    "site_lat": s["lat"],
                    "site_lon": s["lon"],
                    "dist_km": s["dist_km"],
                })
            site_cache[key] = [{"site_no": s["site_no"]} for s in sites[:3]]
        else:
            site_cache[key] = []

        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(locations)} searched, {sites_found} with USGS sites")
            time.sleep(0.5)  # rate limit

        time.sleep(0.2)  # be gentle with API

    # Save site cache
    if new_sites:
        new_df = pd.DataFrame(new_sites)
        if os.path.exists(SITE_CACHE):
            old = pd.read_csv(SITE_CACHE)
            combined = pd.concat([old, new_df], ignore_index=True)
            combined.drop_duplicates(subset=["location", "site_no"], keep="last", inplace=True)
        else:
            combined = new_df
        combined.to_csv(SITE_CACHE, index=False)
        print(f"\nSaved {len(combined)} site mappings to {SITE_CACHE}")

    print(f"\nLocations with USGS sites within 15 mi: {sites_found}/{len(locations)}")

    # Phase 2: Fetch daily values for events with nearby sites
    # Load site mappings
    if os.path.exists(SITE_CACHE):
        site_map = pd.read_csv(SITE_CACHE)
        print(f"\nFetching daily values from {site_map.site_no.nunique()} USGS sites...")

        # Group events by site
        results = []
        processed = 0
        total_sites = site_map.site_no.nunique()

        for site_no in site_map.site_no.unique():
            site_locs = site_map[site_map.site_no == site_no].location.unique()
            events = df[df.location.isin(site_locs)]
            if len(events) == 0:
                continue

            min_d = events.date.min().strftime("%Y-%m-%d")
            max_d = events.date.max().strftime("%Y-%m-%d")

            for param_code, param_name in PARAMS.items():
                values = fetch_daily_values(site_no, param_code, min_d, max_d)
                if values:
                    for _, ev in events.iterrows():
                        date_str = ev.date.strftime("%Y-%m-%d")
                        if date_str in values:
                            results.append({
                                "location": ev.location,
                                "date": date_str,
                                "site_no": site_no,
                                "param": param_name,
                                "value": values[date_str],
                            })
                time.sleep(0.1)

            processed += 1
            if processed % 20 == 0:
                print(f"  {processed}/{total_sites} sites, {len(results)} values found")

        # Pivot and save
        if results:
            res_df = pd.DataFrame(results)
            # Take the closest site's value for each location/date/param
            pivoted = res_df.pivot_table(
                index=["location", "date"],
                columns="param",
                values="value",
                aggfunc="first",
            ).reset_index()
            pivoted.to_csv(CACHE_FILE, index=False)
            print(f"\nSaved {len(pivoted)} rows to {CACHE_FILE}")

            # Coverage report
            for param_name in PARAMS.values():
                if param_name in pivoted.columns:
                    n = pivoted[param_name].notna().sum()
                    print(f"  {param_name}: {n} values")
        else:
            print("\nNo new USGS data found")

    print(f"\nDone!")


if __name__ == "__main__":
    main()
