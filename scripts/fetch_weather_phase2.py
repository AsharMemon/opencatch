#!/usr/bin/env python3
"""
Phase 2: Fetch IEM ASOS weather for all geocoded tournament events
that don't already have weather data from Phase 1.

Uses the tournament_events_geocoded.csv as the source and the
tournament_events_with_weather.csv from Phase 1 as the "already done" set.

Usage:
    python3 fetch_weather_phase2.py --workspace /workspace/castline
"""

import argparse
import csv
import json
import math
import os
import time
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import numpy as np


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def load_stations(workspace: str):
    """Load IEM ASOS station list."""
    path = Path(workspace) / 'raw' / 'iem_asos_stations.csv'
    if path.exists():
        df = pd.read_csv(path)
        return df

    # Download station list
    url = "https://mesonet.agron.iastate.edu/sites/networks.php?network=US__ASOS&format=csv&nohtml=on"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'CASTLINE/1.0'})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read().decode()
        with open(path, 'w') as f:
            f.write(data)
        return pd.read_csv(path)
    except Exception as e:
        print(f"Failed to download stations: {e}")
        return None


def find_nearest_station(lat, lon, stations_df, max_km=150):
    """Find the nearest ASOS station within max_km."""
    if stations_df is None:
        return None, None

    dists = stations_df.apply(
        lambda r: haversine_km(lat, lon, r['lat'], r['lon']), axis=1
    )
    idx = dists.idxmin()
    if dists[idx] <= max_km:
        return stations_df.loc[idx, 'station_id'], dists[idx]
    return None, None


def fetch_iem_weather(station_id, date_str):
    """Fetch daily weather summary from IEM ASOS for a specific date."""
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        end_dt = dt + timedelta(days=1)

        params = urllib.parse.urlencode({
            'station': station_id,
            'data': 'tmpf,dwpf,relh,drct,sknt,alti,p01i,vsby,skyc1',
            'tz': 'UTC',
            'format': 'comma',
            'latlon': 'no',
            'elev': 'no',
            'missing': 'null',
            'year1': dt.year, 'month1': dt.month, 'day1': dt.day,
            'year2': end_dt.year, 'month2': end_dt.month, 'day2': end_dt.day,
        })

        url = f"https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?{params}"
        req = urllib.request.Request(url, headers={'User-Agent': 'CASTLINE/1.0'})

        with urllib.request.urlopen(req, timeout=15) as resp:
            lines = resp.read().decode().strip().split('\n')

        # Skip #DEBUG lines
        lines = [l for l in lines if not l.startswith('#') and l.strip()]
        if len(lines) < 2:
            return None

        header = lines[0].split(',')
        rows = []
        for line in lines[1:]:
            vals = line.split(',')
            if len(vals) == len(header):
                rows.append(dict(zip(header, vals)))

        if not rows:
            return None

        def safe_float(val):
            try:
                if val in ('null', 'M', '', 'None'):
                    return None
                return float(val)
            except:
                return None

        temps = [safe_float(r.get('tmpf')) for r in rows]
        temps = [t for t in temps if t is not None]

        winds = [safe_float(r.get('sknt')) for r in rows]
        winds = [w for w in winds if w is not None]

        pressures = [safe_float(r.get('alti')) for r in rows]
        pressures = [p for p in pressures if p is not None]

        precips = [safe_float(r.get('p01i')) for r in rows]
        precips = [p for p in precips if p is not None]

        humidity = [safe_float(r.get('relh')) for r in rows]
        humidity = [h for h in humidity if h is not None]

        visibility = [safe_float(r.get('vsby')) for r in rows]
        visibility = [v for v in visibility if v is not None]

        # Convert to metric to match phase 1 column names
        # F -> C, knots -> m/s, inHg -> hPa, inches -> mm
        temps_c = [(t - 32) * 5 / 9 for t in temps] if temps else []
        winds_ms = [w * 0.51444 for w in winds] if winds else []
        pressures_hpa = [p * 33.8639 for p in pressures] if pressures else []
        precip_mm = [p * 25.4 for p in precips] if precips else []

        # Compute dew point from temperature and humidity
        dew_points = [safe_float(r.get('dwpf')) for r in rows]
        dew_points = [d for d in dew_points if d is not None]
        dew_c = [(d - 32) * 5 / 9 for d in dew_points] if dew_points else []

        # Sky cover
        sky_map = {'CLR': 0, 'FEW': 0.25, 'SCT': 0.5, 'BKN': 0.75, 'OVC': 1.0}
        sky_vals = [sky_map.get(r.get('skyc1', ''), None) for r in rows]
        sky_vals = [s for s in sky_vals if s is not None]

        result = {
            'temp_mean': round(np.mean(temps_c), 1) if temps_c else None,
            'temp_max': round(max(temps_c), 1) if temps_c else None,
            'temp_min': round(min(temps_c), 1) if temps_c else None,
            'wind_mean': round(np.mean(winds_ms), 1) if winds_ms else None,
            'wind_max': round(max(winds_ms), 1) if winds_ms else None,
            'pressure_mean': round(np.mean(pressures_hpa), 1) if pressures_hpa else None,
            'pressure_min': round(min(pressures_hpa), 1) if pressures_hpa else None,
            'pressure_max': round(max(pressures_hpa), 1) if pressures_hpa else None,
            'precip_total': round(sum(precip_mm), 1) if precip_mm else 0.0,
            'humidity_mean': round(np.mean(humidity), 1) if humidity else None,
            'dew_point': round(np.mean(dew_c), 1) if dew_c else None,
            'cloud_cover': round(np.mean(sky_vals), 2) if sky_vals else None,
            'n_observations': len(rows),
            'station_distance_km': None,  # filled later
        }

        return result

    except Exception as e:
        return None


def fetch_pressure_history(station_id, date_str, days_back=3):
    """Fetch pressure for preceding days to compute pressure delta."""
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        start_dt = dt - timedelta(days=days_back)

        params = urllib.parse.urlencode({
            'station': station_id,
            'data': 'alti',
            'tz': 'UTC',
            'format': 'comma',
            'latlon': 'no',
            'elev': 'no',
            'missing': 'null',
            'year1': start_dt.year, 'month1': start_dt.month, 'day1': start_dt.day,
            'year2': dt.year, 'month2': dt.month, 'day2': dt.day,
        })

        url = f"https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?{params}"
        req = urllib.request.Request(url, headers={'User-Agent': 'CASTLINE/1.0'})

        with urllib.request.urlopen(req, timeout=15) as resp:
            lines = resp.read().decode().strip().split('\n')

        # Skip #DEBUG lines
        lines = [l for l in lines if not l.startswith('#') and l.strip()]
        if len(lines) < 2:
            return None, None

        header = lines[0].split(',')
        pressures_by_day = {}

        for line in lines[1:]:
            vals = line.split(',')
            if len(vals) == len(header):
                row = dict(zip(header, vals))
                try:
                    p = float(row.get('alti', 'null'))
                    day = row.get('valid', '')[:10]
                    if day not in pressures_by_day:
                        pressures_by_day[day] = []
                    pressures_by_day[day].append(p)
                except:
                    pass

        day_means = {day: np.mean(ps) for day, ps in pressures_by_day.items()}

        prev_1d = (dt - timedelta(days=1)).strftime('%Y-%m-%d')
        prev_3d = (dt - timedelta(days=3)).strftime('%Y-%m-%d')

        current_p = day_means.get(date_str)
        prev_1d_p = day_means.get(prev_1d)
        prev_3d_p = day_means.get(prev_3d)

        # Convert inHg delta to hPa (1 inHg = 33.8639 hPa) to match phase 1
        delta_1d = round((current_p - prev_1d_p) * 33.8639, 1) if current_p and prev_1d_p else None
        delta_3d = round((current_p - prev_3d_p) * 33.8639, 1) if current_p and prev_3d_p else None

        return delta_1d, delta_3d

    except:
        return None, None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', default='/workspace/castline')
    args = parser.parse_args()

    raw_dir = Path(args.workspace) / 'raw'
    log_dir = Path(args.workspace) / 'logs'
    log_dir.mkdir(exist_ok=True)

    # Load geocoded events
    geocoded = pd.read_csv(raw_dir / 'tournament_events_geocoded.csv')
    print(f"Total geocoded events: {len(geocoded)}")

    # Load existing weather data (from phase 1)
    weather_path = raw_dir / 'tournament_weather_daily.csv'
    if weather_path.exists():
        existing = pd.read_csv(weather_path)
        done_ids = set(existing['event_id'].values)
        print(f"Already have weather for: {len(done_ids)} events")
    else:
        existing = pd.DataFrame()
        done_ids = set()

    # Find events needing weather
    todo = geocoded[~geocoded['event_id'].isin(done_ids)].copy()
    print(f"Need weather for: {len(todo)} events")

    if len(todo) == 0:
        print("All events already have weather!")
        return

    # Load stations
    stations = load_stations(args.workspace)
    if stations is None:
        print("ERROR: Could not load ASOS stations")
        return
    print(f"Loaded {len(stations)} ASOS stations")

    # Checkpoint
    checkpoint_path = Path(args.workspace) / 'weather_phase2_checkpoint.json'
    results = []
    start_idx = 0

    if checkpoint_path.exists():
        with open(checkpoint_path) as f:
            cp = json.load(f)
        results = cp.get('results', [])
        start_idx = cp.get('processed', 0)
        done_cp_ids = {r['event_id'] for r in results}
        todo = todo[~todo['event_id'].isin(done_cp_ids)]
        print(f"Resuming from checkpoint: {start_idx} processed, {len(results)} results")

    total = len(todo)
    success = sum(1 for r in results if r.get('temp_mean') is not None)
    fail = len(results) - success

    for i, (_, row) in enumerate(todo.iterrows()):
        event_id = row['event_id']
        lat = row['lat']
        lon = row['lon']
        date = str(row['date'])[:10]

        # Find nearest station
        station, dist = find_nearest_station(lat, lon, stations)

        result = {'event_id': event_id}

        if station:
            weather = fetch_iem_weather(station, date)
            if weather:
                result.update(weather)
                result['station_id'] = station
                result['station_distance_km'] = round(dist, 1)

                # Get pressure deltas
                delta_1d, delta_3d = fetch_pressure_history(station, date)
                result['pressure_delta_1d'] = delta_1d
                result['pressure_delta_3d'] = delta_3d

                success += 1
            else:
                fail += 1
                result['station_id'] = station
        else:
            fail += 1

        results.append(result)

        processed = start_idx + i + 1
        if processed % 10 == 0:
            total_done = processed
            rate = success / (success + fail) * 100 if (success + fail) > 0 else 0
            ts = datetime.now().strftime('%H:%M:%S')
            print(f"[{ts}]   Progress: {total_done}/{total} (success={success}, fail={fail}, rate={rate:.0f}%)")

        if processed % 50 == 0:
            with open(checkpoint_path, 'w') as f:
                json.dump({'processed': processed, 'results': results}, f)
            ts = datetime.now().strftime('%H:%M:%S')
            print(f"[{ts}]   Checkpoint saved: {processed} records")

        time.sleep(0.3)  # Be respectful to IEM

    # Save results — append weather-only rows to the phase 1 output file
    if results:
        results_df = pd.DataFrame(results)
        # Only keep rows that actually got weather data
        results_df = results_df[results_df['temp_mean'].notna()].copy()
        print(f"\nPhase 2 events with weather: {len(results_df)}")

        # Load phase 1 output and append
        if len(existing) > 0:
            # Make columns consistent
            all_cols = set(results_df.columns) | set(existing.columns)
            for col in all_cols:
                if col not in results_df.columns:
                    results_df[col] = None
                if col not in existing.columns:
                    existing[col] = None
            combined = pd.concat([existing, results_df], ignore_index=True)
            combined = combined.drop_duplicates(subset='event_id')
        else:
            combined = results_df

        combined.to_csv(weather_path, index=False)
        print(f"Saved combined weather file: {len(combined)} total events")
        has_temp = combined['temp_mean'].notna().sum() if 'temp_mean' in combined.columns else 0
        print(f"Events with temperature data: {has_temp}")
        has_pressure = combined['pressure_mean'].notna().sum() if 'pressure_mean' in combined.columns else 0
        print(f"Events with pressure data: {has_pressure}")

    # Clean up checkpoint
    if checkpoint_path.exists():
        os.remove(checkpoint_path)

    print("\nPhase 2 weather fetch complete!")


if __name__ == '__main__':
    main()
