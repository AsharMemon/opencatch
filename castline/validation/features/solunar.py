"""Solunar and astronomical features for fishing prediction.

Computes moon phase, solunar period timing, and dawn/dusk times that
are known to influence fish feeding behavior. Based on John Alden Knight's
Solunar Theory (1926) which correlates fish activity with lunar position.

Key features:
- Moon phase (0-1 continuous): new=0, full=0.5
- Moon illumination percentage
- Solunar major/minor period overlap with fishing hours
- Day length (photoperiod) — triggers spawn migration
- Dawn/dusk proximity — crepuscular feeding windows
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

import ephem
import numpy as np
import pandas as pd


def _moon_phase_continuous(date: datetime) -> float:
    """Return moon phase as continuous 0-1 value. New moon=0, Full moon=0.5."""
    observer = ephem.Observer()
    observer.date = ephem.Date(date)
    moon = ephem.Moon(observer)

    # Calculate phase angle
    # ephem.Moon.phase gives illumination percentage 0-100
    illumination = moon.phase / 100.0

    # Determine waxing vs waning to get 0→1 continuous cycle
    # Check if moon is past full
    prev_new = ephem.previous_new_moon(observer.date)
    next_new = ephem.next_new_moon(observer.date)
    cycle_length = float(next_new - prev_new)
    days_since_new = float(observer.date - prev_new)
    phase_fraction = days_since_new / cycle_length if cycle_length > 0 else 0.0

    return phase_fraction


def _moon_illumination(date: datetime) -> float:
    """Return moon illumination as percentage 0-100."""
    observer = ephem.Observer()
    observer.date = ephem.Date(date)
    moon = ephem.Moon(observer)
    return float(moon.phase)


def _is_solunar_major_period(date: datetime, lat: float, lon: float) -> bool:
    """Check if date falls within a solunar major period.

    Major periods: moon transit (overhead) and moon underfoot (opposite side).
    These are ~2 hour windows centered on when the moon crosses the meridian.
    """
    observer = ephem.Observer()
    observer.lat = str(lat)
    observer.lon = str(lon)
    observer.date = ephem.Date(date)

    moon = ephem.Moon(observer)
    try:
        transit = observer.next_transit(moon)
        # Major period is ~1 hour either side of transit
        transit_dt = ephem.Date(transit).datetime()
        date_dt = date if isinstance(date, datetime) else datetime.combine(date, datetime.min.time())
        hours_from_transit = abs((date_dt - transit_dt).total_seconds()) / 3600
        return hours_from_transit <= 1.0
    except Exception:
        return False


def _day_length_hours(date: datetime, lat: float, lon: float) -> float:
    """Calculate day length (sunrise to sunset) in hours."""
    observer = ephem.Observer()
    observer.lat = str(lat)
    observer.lon = str(lon)
    observer.date = ephem.Date(date)
    observer.horizon = '0'

    sun = ephem.Sun()
    try:
        sunrise = observer.next_rising(sun)
        observer.date = sunrise
        sunset = observer.next_setting(sun)
        return float(sunset - sunrise) * 24.0
    except (ephem.AlwaysUpError, ephem.NeverUpError):
        return 12.0  # Default for edge cases


def _solunar_score(date: datetime) -> float:
    """Compute a solunar fishing score (0-1).

    Higher score = better fishing based on:
    - Moon phase (new/full = best)
    - Solunar period overlap with typical fishing hours (6am-6pm)

    Based on Knight's Solunar Theory:
    - Major periods: moon overhead/underfoot (2 hours each)
    - Minor periods: moonrise/moonset (1 hour each)
    - Best days: new moon and full moon
    """
    phase = _moon_phase_continuous(date)

    # Score peaks at new moon (0.0) and full moon (0.5)
    # Using cosine to create peaks at 0 and 0.5
    phase_score = (math.cos(4 * math.pi * phase) + 1) / 2  # 0-1, peaks at new/full

    return phase_score


def compute_solunar_features(
    dates: pd.Series,
    latitudes: pd.Series | None = None,
    longitudes: pd.Series | None = None,
) -> pd.DataFrame:
    """Compute solunar features for a series of dates.

    Parameters
    ----------
    dates : pd.Series of datetime-like
    latitudes, longitudes : optional, for day-length calculation

    Returns
    -------
    pd.DataFrame with columns:
        moon_phase (0-1 continuous), moon_illumination_pct,
        solunar_score (0-1), day_length_hours,
        moon_phase_sin, moon_phase_cos (cyclical encoding)
    """
    results = []
    for i, date in enumerate(pd.to_datetime(dates)):
        dt = date.to_pydatetime()
        phase = _moon_phase_continuous(dt)
        illum = _moon_illumination(dt)
        score = _solunar_score(dt)

        lat = float(latitudes.iloc[i]) if latitudes is not None and pd.notna(latitudes.iloc[i]) else 35.0
        lon = float(longitudes.iloc[i]) if longitudes is not None and pd.notna(longitudes.iloc[i]) else -85.0

        day_len = _day_length_hours(dt, lat, lon)

        results.append({
            'moon_phase': phase,
            'moon_illumination_pct': illum,
            'solunar_score': score,
            'day_length_hours': day_len,
            'moon_phase_sin': math.sin(2 * math.pi * phase),
            'moon_phase_cos': math.cos(2 * math.pi * phase),
        })

    return pd.DataFrame(results)


def add_solunar_features_to_dataset(
    dataset_path: Path,
    output_path: Path | None = None,
) -> pd.DataFrame:
    """Add solunar features to an existing dataset CSV."""
    df = pd.read_csv(dataset_path)

    lat_col = None
    lon_col = None
    for col in df.columns:
        if 'lat' in col.lower():
            lat_col = col
        if 'lon' in col.lower():
            lon_col = col

    print(f"solunar: computing features for {len(df)} rows...", file=sys.stderr)
    solunar = compute_solunar_features(
        df['date'],
        latitudes=df[lat_col] if lat_col else None,
        longitudes=df[lon_col] if lon_col else None,
    )

    for col in solunar.columns:
        df[col] = solunar[col].values

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(output_path, index=False)
        print(f"solunar: saved to {output_path}", file=sys.stderr)

    return df
