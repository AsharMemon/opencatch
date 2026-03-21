"""
NOAA GHCN-Daily weather lookup via Meteostat v2.

Replaces rate-limited Open-Meteo API with unlimited local lookups backed by
NOAA's Global Historical Climatology Network (GHCN-Daily) data.

Usage:
    from scripts.noaa_weather_lookup import NOAAWeatherLookup

    lookup = NOAAWeatherLookup()
    weather = lookup.get_daily(35.0, -85.0, "2023-06-15")
    df = lookup.get_range(35.0, -85.0, "2023-06-01", "2023-06-30")
    context = lookup.get_with_context(35.0, -85.0, "2023-06-15", days_before=3)
"""

from __future__ import annotations

import math
import warnings
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)

_EARTH_RADIUS_KM = 6371.0


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _safe_float(val) -> float:
    """Convert to float, returning NaN for missing/invalid. Never 0 for missing."""
    if val is None:
        return float("nan")
    try:
        result = float(val)
        if math.isnan(result):
            return float("nan")
        return result
    except (ValueError, TypeError):
        return float("nan")


class NOAAWeatherLookup:
    """Fast local weather lookup using Meteostat v2 (NOAA GHCN-Daily backend).

    First call triggers station metadata download (~2 MB). Individual station
    data is cached by Meteostat in ~/.meteostat/cache/ and reused across runs.

    Meteostat v2 API:
        meteostat.stations.nearby(Point) -> DataFrame of stations
        meteostat.daily(station_id, start, end).fetch() -> DataFrame
    """

    def __init__(self, max_station_distance_km: float = 100.0, n_candidates: int = 5):
        from meteostat import stations as _stations_mod, daily as _daily_func, Point as _Point
        self._stations_mod = _stations_mod
        self._daily_func = _daily_func
        self._Point = _Point
        self.max_station_distance_km = max_station_distance_km
        self.n_candidates = n_candidates
        self._cache: dict[str, pd.DataFrame | None] = {}

    def get_daily(self, lat: float, lon: float, date: str | datetime) -> dict[str, Any]:
        """Return a single day's weather as a dict. Missing values are NaN."""
        dt = pd.Timestamp(date)
        df = self._fetch(lat, lon, dt, dt)
        if df is None or df.empty:
            return self._empty_record(lat, lon, dt)
        row = df.iloc[0]
        return self._row_to_dict(row, lat, lon)

    def get_range(
        self, lat: float, lon: float, start_date: str | datetime, end_date: str | datetime
    ) -> pd.DataFrame:
        """Return a DataFrame of daily weather for a date range."""
        start = pd.Timestamp(start_date)
        end = pd.Timestamp(end_date)
        df = self._fetch(lat, lon, start, end)
        if df is None or df.empty:
            return pd.DataFrame()
        records = [self._row_to_dict(row, lat, lon) for _, row in df.iterrows()]
        return pd.DataFrame(records)

    def get_with_context(
        self, lat: float, lon: float, date: str | datetime, days_before: int = 3
    ) -> pd.DataFrame:
        """Return event-day weather plus preceding days for frontal detection."""
        dt = pd.Timestamp(date)
        start = dt - timedelta(days=days_before)
        df = self.get_range(lat, lon, start, dt)
        if df.empty:
            return pd.DataFrame()
        df["day_offset"] = (pd.to_datetime(df["date"]) - dt).dt.days
        return df

    def _fetch(
        self, lat: float, lon: float, start: pd.Timestamp, end: pd.Timestamp
    ) -> pd.DataFrame | None:
        """Fetch daily data, trying up to n_candidates nearby stations."""
        pt = self._Point(lat, lon)
        nearby = self._stations_mod.nearby(pt).head(self.n_candidates)

        if nearby.empty:
            return None

        start_dt = start.to_pydatetime()
        end_dt = end.to_pydatetime()

        for station_id in nearby.index:
            srow = nearby.loc[station_id]
            dist = srow.get("distance", 0) / 1000.0  # meteostat returns meters
            if dist > self.max_station_distance_km:
                continue

            cache_key = f"{station_id}|{start.date()}|{end.date()}"
            if cache_key in self._cache:
                df = self._cache[cache_key]
            else:
                try:
                    ts = self._daily_func(station_id, start_dt, end_dt)
                    df = ts.fetch()
                    if df is None:
                        df = pd.DataFrame()
                    self._cache[cache_key] = df
                except Exception:
                    self._cache[cache_key] = pd.DataFrame()
                    continue

            if df is None or df.empty:
                continue

            df = df.copy()
            df["_station_id"] = station_id
            df["_station_distance_km"] = round(dist, 1)
            return df

        return None

    def _row_to_dict(self, row: pd.Series, lat: float, lon: float) -> dict[str, Any]:
        """Convert a Meteostat daily row to our standard dict format.

        Meteostat v2 columns: temp, tmin, tmax, rhum, prcp, snwd, wspd, wpgt, pres, tsun, cldc
        """
        date_val = row.name if isinstance(row.name, (datetime, pd.Timestamp)) else None

        return {
            "date": str(date_val.date()) if date_val else None,
            "lat": lat,
            "lon": lon,
            "temp_max": _safe_float(row.get("tmax")),
            "temp_min": _safe_float(row.get("tmin")),
            "temp_mean": _safe_float(row.get("temp")),
            "precip_mm": _safe_float(row.get("prcp")),
            "wind_max_kph": _safe_float(row.get("wpgt")),
            "wind_avg_kph": _safe_float(row.get("wspd")),
            "wind_dir": float("nan"),  # v2 doesn't have wdir
            "pressure_hpa": _safe_float(row.get("pres")),
            "humidity_pct": _safe_float(row.get("rhum")),
            "cloud_cover_okta": _safe_float(row.get("cldc")),
            "snow_mm": _safe_float(row.get("snwd")),
            "sunshine_min": _safe_float(row.get("tsun")),
            "station_id": row.get("_station_id", None),
            "station_distance_km": row.get("_station_distance_km", float("nan")),
        }

    def _empty_record(self, lat: float, lon: float, dt: pd.Timestamp) -> dict[str, Any]:
        return {
            "date": str(dt.date()),
            "lat": lat,
            "lon": lon,
            "temp_max": float("nan"),
            "temp_min": float("nan"),
            "temp_mean": float("nan"),
            "precip_mm": float("nan"),
            "wind_max_kph": float("nan"),
            "wind_avg_kph": float("nan"),
            "wind_dir": float("nan"),
            "pressure_hpa": float("nan"),
            "humidity_pct": float("nan"),
            "cloud_cover_okta": float("nan"),
            "snow_mm": float("nan"),
            "sunshine_min": float("nan"),
            "station_id": None,
            "station_distance_km": float("nan"),
        }


# ---------------------------------------------------------------------------
# Bulk fetch for event DataFrames
# ---------------------------------------------------------------------------


def _both_valid(a, b) -> bool:
    return pd.notna(a) and pd.notna(b)


def bulk_fetch_for_events(
    events_df: pd.DataFrame,
    days_before: int = 0,
    progress: bool = True,
) -> pd.DataFrame:
    """Fetch weather for a DataFrame of events with lat, lon, date columns.

    Args:
        events_df: Must have columns 'lat', 'lon', 'date'. May have 'event_id'.
        days_before: If >0, also fetch preceding days and add lag features.
        progress: Print progress updates.

    Returns:
        DataFrame with weather columns. One row per event (lag features
        are flattened as temp_mean_lag1, pressure_hpa_lag1, etc.)
    """
    required = {"lat", "lon", "date"}
    missing = required - set(events_df.columns)
    if missing:
        raise ValueError(f"events_df missing required columns: {missing}")

    lookup = NOAAWeatherLookup()
    records: list[dict[str, Any]] = []
    total = len(events_df)

    events = events_df.copy()
    events["_idx"] = range(len(events))
    events["date"] = pd.to_datetime(events["date"])
    events = events.sort_values(["lat", "lon", "date"])

    for i, (_, row) in enumerate(events.iterrows()):
        lat = float(row["lat"])
        lon = float(row["lon"])
        dt = row["date"]

        if progress and (i % 100 == 0 or i == total - 1):
            print(f"  [{i+1}/{total}] Fetching weather for ({lat:.2f}, {lon:.2f}) on {dt.date()}")

        if days_before > 0:
            ctx = lookup.get_with_context(lat, lon, dt, days_before=days_before)
            if ctx.empty:
                record = lookup._empty_record(lat, lon, pd.Timestamp(dt))
            else:
                event_row = ctx.loc[ctx["day_offset"] == 0]
                if event_row.empty:
                    record = ctx.iloc[-1].to_dict()
                else:
                    record = event_row.iloc[0].to_dict()

                lag_cols = ["temp_mean", "temp_max", "temp_min", "precip_mm",
                            "pressure_hpa", "wind_avg_kph"]
                for lag_day in range(1, days_before + 1):
                    lag_row = ctx.loc[ctx["day_offset"] == -lag_day]
                    for col in lag_cols:
                        key = f"{col}_lag{lag_day}"
                        if not lag_row.empty and col in lag_row.columns:
                            record[key] = lag_row.iloc[0].get(col, float("nan"))
                        else:
                            record[key] = float("nan")

                # Pressure delta features (critical for fishing predictions)
                p0 = record.get("pressure_hpa", float("nan"))
                p1 = record.get("pressure_hpa_lag1", float("nan"))
                p2 = record.get("pressure_hpa_lag2", float("nan"))
                p3 = record.get("pressure_hpa_lag3", float("nan"))

                record["pressure_delta_1d"] = p0 - p1 if _both_valid(p0, p1) else float("nan")
                record["pressure_delta_2d"] = p0 - p2 if _both_valid(p0, p2) else float("nan")
                if days_before >= 3:
                    record["pressure_delta_3d"] = p0 - p3 if _both_valid(p0, p3) else float("nan")

                # Frontal phase classification
                delta = record.get("pressure_delta_1d", float("nan"))
                if pd.notna(delta):
                    if delta < -3.0:
                        record["front_phase"] = "pre_frontal"
                    elif delta > 3.0:
                        record["front_phase"] = "post_frontal"
                    elif delta < -1.0:
                        record["front_phase"] = "approaching"
                    elif delta > 1.0:
                        record["front_phase"] = "clearing"
                    else:
                        record["front_phase"] = "stable"
                else:
                    record["front_phase"] = None
        else:
            record = lookup.get_daily(lat, lon, dt)

        if "event_id" in row.index:
            record["event_id"] = row["event_id"]
        record["_idx"] = row["_idx"]

        records.append(record)

    result = pd.DataFrame(records).sort_values("_idx").drop(columns=["_idx"]).reset_index(drop=True)
    for col in ["day_offset"]:
        if col in result.columns:
            result = result.drop(columns=[col])

    return result


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== NOAA Weather Lookup Test ===\n")

    lookup = NOAAWeatherLookup()

    test_events = [
        {"name": "Lake Guntersville, AL", "lat": 34.37, "lon": -86.29, "date": "2023-06-15"},
        {"name": "Sam Rayburn, TX", "lat": 31.06, "lon": -94.10, "date": "2023-04-10"},
        {"name": "Lake Okeechobee, FL", "lat": 26.95, "lon": -80.80, "date": "2023-02-20"},
        {"name": "Table Rock Lake, MO", "lat": 36.60, "lon": -93.31, "date": "2023-05-05"},
        {"name": "Oneida Lake, NY", "lat": 43.20, "lon": -75.93, "date": "2023-07-01"},
        {"name": "Lake Erie, OH", "lat": 41.68, "lon": -82.84, "date": "2023-08-15"},
        {"name": "Toledo Bend, LA/TX", "lat": 31.17, "lon": -93.57, "date": "2023-03-25"},
        {"name": "Chickamauga Lake, TN", "lat": 35.16, "lon": -85.14, "date": "2023-09-10"},
        {"name": "Lake Fork, TX", "lat": 32.82, "lon": -95.55, "date": "2023-11-01"},
        {"name": "Kissimmee Chain, FL", "lat": 28.30, "lon": -81.38, "date": "2023-01-15"},
    ]

    print(f"Testing {len(test_events)} locations...\n")

    for evt in test_events:
        result = lookup.get_daily(evt["lat"], evt["lon"], evt["date"])
        print(f"  {evt['name']} ({evt['date']}):")
        print(f"    Station: {result.get('station_id')} ({result.get('station_distance_km')} km)")
        print(f"    Temp: {result.get('temp_min')}–{result.get('temp_max')}C (avg {result.get('temp_mean')})")
        print(f"    Precip: {result.get('precip_mm')} mm | Humidity: {result.get('humidity_pct')}%")
        print(f"    Wind: avg {result.get('wind_avg_kph')} kph")
        print(f"    Pressure: {result.get('pressure_hpa')} hPa")
        print(f"    Cloud cover: {result.get('cloud_cover_okta')} okta")
        print()

    # Test context (frontal passage detection)
    print("=== Context Test (3-day lookback) ===")
    ctx = lookup.get_with_context(34.37, -86.29, "2023-06-15", days_before=3)
    if not ctx.empty:
        show_cols = ["date", "day_offset", "temp_mean", "pressure_hpa", "precip_mm", "wind_avg_kph"]
        available = [c for c in show_cols if c in ctx.columns]
        print(ctx[available].to_string(index=False))
    else:
        print("  No context data returned.")

    print("\nDone.")
