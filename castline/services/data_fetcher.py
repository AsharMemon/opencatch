"""Real-time environmental data fetcher for the CASTLINE production pipeline.

Fetches live water conditions (USGS), weather (Open-Meteo), and solunar data
to build feature vectors for model inference.  All HTTP calls use stdlib/requests
with timeouts and graceful fallbacks — missing data is represented as NaN, never
as 0.0.

Classes
-------
USGSWaterFetcher   — USGS NWIS instantaneous-values API
OpenMeteoFetcher   — Open-Meteo free forecast API
SolunarCalculator  — Pure-Python moon phase & feeding windows
EnvironmentCollector — Unified collector with 1-hour result caching
"""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

try:
    import requests
except ImportError:  # fall back to urllib if requests is missing
    requests = None  # type: ignore[assignment]

import json
import urllib.request
import urllib.error
import urllib.parse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HTTP helper (works with or without `requests`)
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT = 30  # seconds


def _http_get(url: str, params: Optional[dict] = None,
              timeout: int = _DEFAULT_TIMEOUT) -> dict | list | str:
    """Issue a GET request and return parsed JSON (or raw text on failure).

    Uses ``requests`` when available, otherwise falls back to ``urllib``.
    """
    if requests is not None:
        resp = requests.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "")
        if "json" in content_type:
            return resp.json()
        return resp.text
    # urllib fallback
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


# ╔═════════════════════════════════════════════════════════════════════════╗
# ║  USGS Water Data                                                      ║
# ╚═════════════════════════════════════════════════════════════════════════╝

USGS_IV_URL = "https://waterservices.usgs.gov/nwis/iv/"

PARAMETER_CODES: Dict[str, str] = {
    "00010": "water_temp_c",
    "00060": "discharge_cfs",
    "00065": "gage_height_ft",
    "00300": "dissolved_oxygen_mgL",
}


class USGSWaterFetcher:
    """Fetch instantaneous water-quality values from the USGS NWIS API.

    Parameters
    ----------
    timeout : int
        HTTP request timeout in seconds (default 30).
    """

    def __init__(self, timeout: int = _DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout

    # ---- public API -------------------------------------------------------

    def fetch_current(self, site_id: str) -> dict:
        """Return the most recent instantaneous values for *site_id*.

        Returns a dict with keys matching ``PARAMETER_CODES`` values (e.g.
        ``water_temp_c``).  Missing parameters are ``float('nan')``.
        """
        params = {
            "format": "json",
            "sites": self._normalize_site(site_id),
            "parameterCd": ",".join(PARAMETER_CODES.keys()),
            "siteStatus": "all",
        }
        try:
            data = _http_get(USGS_IV_URL, params=params, timeout=self.timeout)
        except Exception:
            logger.warning("USGS fetch_current failed for site %s", site_id, exc_info=True)
            return self._empty_current(site_id)

        return self._parse_latest(data, site_id)

    def fetch_flow_features(self, site_id: str) -> dict:
        """Compute flow delta features for model inference.

        Returns a dict with keys matching the training features:
        - discharge_cfs: current discharge
        - discharge_delta_1d: change in discharge over 1 day
        - discharge_delta_3d: change in discharge over 3 days
        - gage_height_ft: current gage height
        - flow_regime: 'rising', 'falling', or 'stable'
        - discharge_zscore: z-score relative to 7-day window
        - water_temp_usgs: water temperature (if available)
        - log_discharge: log(1 + discharge)
        - flow_rising / flow_falling / flow_stable: binary indicators
        """
        history = self.fetch_history(site_id, days=7)
        current = self.fetch_current(site_id)

        result = {
            "discharge_cfs": current.get("discharge_cfs", float("nan")),
            "gage_height_ft": current.get("gage_height_ft", float("nan")),
            "water_temp_usgs": current.get("water_temp_c", float("nan")),
        }

        # Compute deltas from history
        if not history.empty and "discharge_cfs" in history.columns:
            discharges = history["discharge_cfs"].dropna()
            if len(discharges) >= 2:
                current_q = discharges.iloc[-1]
                result["discharge_cfs"] = current_q

                # 1-day delta (last ~96 readings at 15-min intervals)
                if len(discharges) > 96:
                    result["discharge_delta_1d"] = current_q - discharges.iloc[-96]
                elif len(discharges) > 1:
                    result["discharge_delta_1d"] = current_q - discharges.iloc[0]
                else:
                    result["discharge_delta_1d"] = float("nan")

                # 3-day delta
                if len(discharges) > 288:
                    result["discharge_delta_3d"] = current_q - discharges.iloc[-288]
                elif len(discharges) > 1:
                    result["discharge_delta_3d"] = current_q - discharges.iloc[0]
                else:
                    result["discharge_delta_3d"] = float("nan")

                # Z-score
                q_mean = discharges.mean()
                q_std = discharges.std()
                if q_std > 0:
                    result["discharge_zscore"] = (current_q - q_mean) / q_std
                else:
                    result["discharge_zscore"] = 0.0

                # Flow regime classification
                delta_1d = result.get("discharge_delta_1d", 0)
                if not _is_nan(delta_1d):
                    pct_change = delta_1d / max(current_q, 1.0)
                    if pct_change > 0.10:
                        result["flow_regime"] = "rising"
                    elif pct_change < -0.10:
                        result["flow_regime"] = "falling"
                    else:
                        result["flow_regime"] = "stable"
                else:
                    result["flow_regime"] = "stable"
            else:
                result.update({
                    "discharge_delta_1d": float("nan"),
                    "discharge_delta_3d": float("nan"),
                    "discharge_zscore": float("nan"),
                    "flow_regime": "stable",
                })
        else:
            result.update({
                "discharge_delta_1d": float("nan"),
                "discharge_delta_3d": float("nan"),
                "discharge_zscore": float("nan"),
                "flow_regime": "stable",
            })

        # Derived features
        import math as _math
        q = result.get("discharge_cfs", 0)
        result["log_discharge"] = _math.log1p(q) if not _is_nan(q) else float("nan")

        regime = result.get("flow_regime", "stable")
        result["flow_rising"] = 1 if regime == "rising" else 0
        result["flow_falling"] = 1 if regime == "falling" else 0
        result["flow_stable"] = 1 if regime == "stable" else 0

        return result

    def fetch_history(self, site_id: str, days: int = 7) -> pd.DataFrame:
        """Return a DataFrame of instantaneous values over the last *days*.

        Columns: ``datetime``, plus one column per available parameter.
        Rows are in chronological order.  Missing parameters are NaN.
        """
        params = {
            "format": "json",
            "sites": self._normalize_site(site_id),
            "parameterCd": ",".join(PARAMETER_CODES.keys()),
            "period": f"P{days}D",
            "siteStatus": "all",
        }
        try:
            data = _http_get(USGS_IV_URL, params=params, timeout=self.timeout)
        except Exception:
            logger.warning("USGS fetch_history failed for site %s", site_id, exc_info=True)
            return self._empty_history()

        return self._parse_timeseries(data)

    # ---- parsing helpers --------------------------------------------------

    @staticmethod
    def _normalize_site(site_id: str) -> str:
        """Strip common prefixes like 'USGS-'."""
        return site_id.replace("USGS-", "").strip()

    def _empty_current(self, site_id: str) -> dict:
        result: dict = {"site_id": site_id, "datetime": None}
        for name in PARAMETER_CODES.values():
            result[name] = float("nan")
        return result

    @staticmethod
    def _empty_history() -> pd.DataFrame:
        cols = ["datetime"] + list(PARAMETER_CODES.values())
        return pd.DataFrame(columns=cols)

    def _parse_latest(self, payload: Any, site_id: str) -> dict:
        """Extract the most recent value for each parameter from NWIS JSON."""
        result = self._empty_current(site_id)
        try:
            series_list = payload["value"]["timeSeries"]
        except (KeyError, TypeError):
            return result

        for series in series_list:
            code = self._extract_param_code(series)
            if code not in PARAMETER_CODES:
                continue
            col_name = PARAMETER_CODES[code]
            values = series.get("values", [{}])[0].get("value", [])
            if not values:
                continue
            latest = values[-1]
            try:
                result[col_name] = float(latest["value"])
            except (ValueError, TypeError, KeyError):
                result[col_name] = float("nan")
            # Capture the most recent timestamp
            ts = latest.get("dateTime")
            if ts and result["datetime"] is None:
                result["datetime"] = ts
        return result

    def _parse_timeseries(self, payload: Any) -> pd.DataFrame:
        """Parse NWIS JSON into a long-form DataFrame."""
        try:
            series_list = payload["value"]["timeSeries"]
        except (KeyError, TypeError):
            return self._empty_history()

        merged: Dict[str, Dict[str, Any]] = {}  # keyed by ISO timestamp

        for series in series_list:
            code = self._extract_param_code(series)
            if code not in PARAMETER_CODES:
                continue
            col_name = PARAMETER_CODES[code]
            for val in series.get("values", [{}])[0].get("value", []):
                ts = val.get("dateTime", "")
                if ts not in merged:
                    merged[ts] = {"datetime": ts}
                try:
                    merged[ts][col_name] = float(val["value"])
                except (ValueError, TypeError, KeyError):
                    merged[ts][col_name] = float("nan")

        if not merged:
            return self._empty_history()

        df = pd.DataFrame(list(merged.values()))
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True, errors="coerce")
        df.sort_values("datetime", inplace=True)
        df.reset_index(drop=True, inplace=True)
        # Ensure all parameter columns exist
        for col in PARAMETER_CODES.values():
            if col not in df.columns:
                df[col] = float("nan")
        return df

    @staticmethod
    def _extract_param_code(series: dict) -> Optional[str]:
        for code_item in series.get("variable", {}).get("variableCode", []):
            return code_item.get("value")
        return None


# ╔═════════════════════════════════════════════════════════════════════════╗
# ║  Open-Meteo Weather                                                   ║
# ╚═════════════════════════════════════════════════════════════════════════╝

OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"

# Hourly variables requested from Open-Meteo
_HOURLY_VARS = [
    "temperature_2m",
    "surface_pressure",
    "windspeed_10m",
    "winddirection_10m",
    "precipitation",
    "cloudcover",
    "shortwave_radiation",
]


class OpenMeteoFetcher:
    """Fetch current and forecast weather from the Open-Meteo free API.

    Parameters
    ----------
    timeout : int
        HTTP request timeout in seconds (default 30).
    """

    def __init__(self, timeout: int = _DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout

    def fetch_current(self, lat: float, lon: float) -> dict:
        """Return the latest weather conditions at *(lat, lon)*.

        Keys: ``air_temp_c``, ``pressure_mb``, ``wind_speed_kph``,
        ``wind_direction_deg``, ``precip_mm``, ``cloud_cover_pct``,
        ``solar_radiation_wm2``, ``datetime``.
        """
        params = {
            "latitude": round(lat, 4),
            "longitude": round(lon, 4),
            "current_weather": "true",
            "hourly": ",".join(_HOURLY_VARS),
            "forecast_days": 1,
            "timezone": "UTC",
        }
        try:
            data = _http_get(OPENMETEO_URL, params=params, timeout=self.timeout)
        except Exception:
            logger.warning("Open-Meteo fetch_current failed for %.4f, %.4f",
                           lat, lon, exc_info=True)
            return self._empty_current()

        return self._parse_current(data)

    def fetch_forecast(self, lat: float, lon: float, days: int = 7) -> pd.DataFrame:
        """Return an hourly forecast DataFrame for the next *days*.

        Columns: ``datetime``, ``air_temp_c``, ``pressure_mb``,
        ``wind_speed_kph``, ``wind_direction_deg``, ``precip_mm``,
        ``cloud_cover_pct``, ``solar_radiation_wm2``.
        """
        params = {
            "latitude": round(lat, 4),
            "longitude": round(lon, 4),
            "hourly": ",".join(_HOURLY_VARS),
            "forecast_days": min(days, 16),
            "timezone": "UTC",
        }
        try:
            data = _http_get(OPENMETEO_URL, params=params, timeout=self.timeout)
        except Exception:
            logger.warning("Open-Meteo fetch_forecast failed for %.4f, %.4f",
                           lat, lon, exc_info=True)
            return self._empty_forecast()

        return self._parse_forecast(data)

    # ---- parsing helpers --------------------------------------------------

    _COLUMN_MAP = {
        "temperature_2m": "air_temp_c",
        "surface_pressure": "pressure_mb",
        "windspeed_10m": "wind_speed_kph",
        "winddirection_10m": "wind_direction_deg",
        "precipitation": "precip_mm",
        "cloudcover": "cloud_cover_pct",
        "shortwave_radiation": "solar_radiation_wm2",
    }

    @classmethod
    def _empty_current(cls) -> dict:
        out: dict = {"datetime": None}
        for col in cls._COLUMN_MAP.values():
            out[col] = float("nan")
        return out

    @classmethod
    def _empty_forecast(cls) -> pd.DataFrame:
        cols = ["datetime"] + list(cls._COLUMN_MAP.values())
        return pd.DataFrame(columns=cols)

    @classmethod
    def _parse_current(cls, data: Any) -> dict:
        """Extract current weather from the response.

        Prefers ``current_weather`` block; falls back to the latest hourly
        values when individual fields are missing.
        """
        cw = data.get("current_weather", {}) if isinstance(data, dict) else {}
        hourly = data.get("hourly", {}) if isinstance(data, dict) else {}

        result: dict = {
            "datetime": cw.get("time"),
            "air_temp_c": cw.get("temperature", float("nan")),
            "wind_speed_kph": cw.get("windspeed", float("nan")),
            "wind_direction_deg": cw.get("winddirection", float("nan")),
        }

        # Fill remaining fields from hourly data (last available value)
        for api_key, col in cls._COLUMN_MAP.items():
            if col in result and not _is_nan(result[col]):
                continue
            vals = hourly.get(api_key, [])
            if vals:
                # Use most recent non-null value
                for v in reversed(vals):
                    if v is not None:
                        result[col] = v
                        break
                else:
                    result.setdefault(col, float("nan"))
            else:
                result.setdefault(col, float("nan"))

        return result

    @classmethod
    def _parse_forecast(cls, data: Any) -> pd.DataFrame:
        hourly = data.get("hourly", {}) if isinstance(data, dict) else {}
        times = hourly.get("time", [])
        if not times:
            return cls._empty_forecast()

        rows: Dict[str, list] = {"datetime": times}
        for api_key, col in cls._COLUMN_MAP.items():
            values = hourly.get(api_key, [None] * len(times))
            rows[col] = [v if v is not None else float("nan") for v in values]

        df = pd.DataFrame(rows)
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True, errors="coerce")
        return df


def _is_nan(v: Any) -> bool:
    try:
        return math.isnan(v)
    except (TypeError, ValueError):
        return False


# ╔═════════════════════════════════════════════════════════════════════════╗
# ║  Solunar Calculator                                                   ║
# ╚═════════════════════════════════════════════════════════════════════════╝

class SolunarCalculator:
    """Pure-Python solunar calculator for moon phase & feeding windows.

    All heavy astronomy is approximated with the standard simplified
    algorithms (Meeus / NOAA spreadsheet).  Accuracy is within a few
    minutes for sunrise/sunset and ~1 day for lunar phase — sufficient
    for fishing-conditions modelling.
    """

    def calculate(self, lat: float, lon: float, date: str) -> dict:
        """Compute solunar features for a location and date.

        Parameters
        ----------
        lat, lon : float
            Location in decimal degrees.
        date : str
            ISO-format date string (``YYYY-MM-DD``).

        Returns
        -------
        dict
            Keys: ``moon_phase`` (0-1, 0=new), ``moon_illumination_pct``,
            ``moon_phase_name``, ``major_periods``, ``minor_periods``,
            ``day_length_hours``, ``sunrise``, ``sunset``,
            ``moonrise_approx``, ``moonset_approx``.
        """
        dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        jd = self._julian_day(dt)

        phase = self._moon_phase(jd)
        illumination = self._moon_illumination(phase)
        phase_name = self._phase_name(phase)

        sunrise, sunset = self._sun_times(lat, lon, jd)
        day_length = max(0.0, (sunset - sunrise) * 24.0) if (
            sunrise is not None and sunset is not None
        ) else float("nan")

        moonrise, moonset = self._approx_moon_times(lat, lon, jd, phase)

        major, minor = self._solunar_periods(moonrise, moonset)

        return {
            "moon_phase": round(phase, 4),
            "moon_illumination_pct": round(illumination, 1),
            "moon_phase_name": phase_name,
            "major_periods": major,
            "minor_periods": minor,
            "day_length_hours": round(day_length, 2) if not math.isnan(day_length) else float("nan"),
            "sunrise": self._jd_to_timestr(sunrise),
            "sunset": self._jd_to_timestr(sunset),
            "moonrise_approx": self._jd_to_timestr(moonrise),
            "moonset_approx": self._jd_to_timestr(moonset),
        }

    # ---- Julian-day utilities -------------------------------------------

    @staticmethod
    def _julian_day(dt: datetime) -> float:
        """Convert a datetime to Julian Day Number."""
        y = dt.year
        m = dt.month
        d = dt.day + (dt.hour + dt.minute / 60 + dt.second / 3600) / 24.0
        if m <= 2:
            y -= 1
            m += 12
        A = int(y / 100)
        B = 2 - A + int(A / 4)
        return int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + d + B - 1524.5

    @staticmethod
    def _jd_to_timestr(jd: Optional[float]) -> Optional[str]:
        """Convert fractional JD to ``HH:MM`` UTC string."""
        if jd is None:
            return None
        frac = (jd + 0.5) % 1.0
        total_minutes = int(round(frac * 1440))
        h = total_minutes // 60
        m = total_minutes % 60
        return f"{h:02d}:{m:02d}"

    # ---- Moon phase (simplified Meeus) -----------------------------------

    @staticmethod
    def _moon_phase(jd: float) -> float:
        """Return lunar phase as a fraction 0-1 (0 = new moon)."""
        # Synodic month = 29.53058868 days
        # Known new moon: 2000-01-06 18:14 UTC => JD 2451550.26
        SYNODIC = 29.53058868
        REF_NEW = 2451550.26
        age = (jd - REF_NEW) % SYNODIC
        return age / SYNODIC

    @staticmethod
    def _moon_illumination(phase: float) -> float:
        """Approximate illumination percentage from phase fraction."""
        # Simple cosine model
        return round((1 - math.cos(2 * math.pi * phase)) / 2 * 100, 1)

    @staticmethod
    def _phase_name(phase: float) -> str:
        """Return a human-readable phase name."""
        if phase < 0.0625 or phase >= 0.9375:
            return "New Moon"
        if phase < 0.1875:
            return "Waxing Crescent"
        if phase < 0.3125:
            return "First Quarter"
        if phase < 0.4375:
            return "Waxing Gibbous"
        if phase < 0.5625:
            return "Full Moon"
        if phase < 0.6875:
            return "Waning Gibbous"
        if phase < 0.8125:
            return "Last Quarter"
        return "Waning Crescent"

    # ---- Sunrise / Sunset (NOAA simplified) ------------------------------

    @staticmethod
    def _sun_times(lat: float, lon: float, jd: float) -> Tuple[Optional[float], Optional[float]]:
        """Return (sunrise_jd, sunset_jd) or (None, None) for polar day/night."""
        # NOAA solar calculator, simplified
        n = jd - 2451545.0  # days since J2000.0
        # Mean solar noon
        J_star = n - lon / 360.0
        # Solar mean anomaly
        M = (357.5291 + 0.98560028 * J_star) % 360
        M_rad = math.radians(M)
        # Equation of center
        C = 1.9148 * math.sin(M_rad) + 0.0200 * math.sin(2 * M_rad) + 0.0003 * math.sin(3 * M_rad)
        # Ecliptic longitude
        lam = (M + C + 180 + 102.9372) % 360
        lam_rad = math.radians(lam)
        # Solar transit
        J_transit = 2451545.0 + J_star + 0.0053 * math.sin(M_rad) - 0.0069 * math.sin(2 * lam_rad)
        # Declination
        sin_dec = math.sin(lam_rad) * math.sin(math.radians(23.4397))
        cos_dec = math.cos(math.asin(sin_dec))
        # Hour angle
        lat_rad = math.radians(lat)
        cos_omega = (math.sin(math.radians(-0.833)) - math.sin(lat_rad) * sin_dec) / (
            math.cos(lat_rad) * cos_dec
        )
        if cos_omega < -1 or cos_omega > 1:
            return None, None  # polar day or night
        omega = math.degrees(math.acos(cos_omega))
        sunrise = J_transit - omega / 360
        sunset = J_transit + omega / 360
        return sunrise, sunset

    # ---- Approximate moonrise / moonset ----------------------------------

    @staticmethod
    def _approx_moon_times(
        lat: float, lon: float, jd: float, phase: float
    ) -> Tuple[Optional[float], Optional[float]]:
        """Very rough moonrise/moonset estimate.

        Uses the approximation that the moon transits ~50 minutes later each
        day and that at new moon it roughly follows the sun.
        """
        SYNODIC = 29.53058868
        # Moon hour angle offset from sun (in days)
        moon_offset = phase * SYNODIC * (12.37 / 24.0) / SYNODIC  # ~0-12.37h shift
        # Rough noon JD for this date at this longitude
        noon = round(jd) + 0.5 - lon / 360.0
        moonrise = noon - 0.25 + moon_offset  # ~6h before transit + phase offset
        moonset = noon + 0.25 + moon_offset
        # Clamp within the day
        return moonrise, moonset

    # ---- Solunar feeding periods -----------------------------------------

    @staticmethod
    def _solunar_periods(
        moonrise: Optional[float], moonset: Optional[float]
    ) -> Tuple[List[str], List[str]]:
        """Compute major (2h) and minor (1h) solunar feeding windows.

        Major periods centre on moonrise and moonset.
        Minor periods centre on moon overhead and moon underfoot
        (approximated as midpoints between rise/set).
        """
        if moonrise is None or moonset is None:
            return [], []

        def _window(centre_jd: float, half_hours: float) -> str:
            start = centre_jd - half_hours / 24.0
            end = centre_jd + half_hours / 24.0
            return f"{SolunarCalculator._jd_to_timestr(start)}-{SolunarCalculator._jd_to_timestr(end)}"

        major = [
            _window(moonrise, 1.0),   # 2h centred on moonrise
            _window(moonset, 1.0),     # 2h centred on moonset
        ]
        # Moon overhead ~ midpoint between rise and set; underfoot ~ 12h later
        overhead = (moonrise + moonset) / 2.0
        underfoot = overhead + 0.5  # ~12 hours later
        minor = [
            _window(overhead, 0.5),    # 1h centred on overhead
            _window(underfoot, 0.5),   # 1h centred on underfoot
        ]
        return major, minor


# ╔═════════════════════════════════════════════════════════════════════════╗
# ║  Unified Environment Collector (with caching)                         ║
# ╚═════════════════════════════════════════════════════════════════════════╝

class EnvironmentCollector:
    """Combine USGS, Open-Meteo, and solunar data into a single feature dict.

    Results are cached for 1 hour per unique (lat, lon, date, usgs_site_id)
    tuple to avoid redundant API calls during rapid successive requests.

    Parameters
    ----------
    cache_ttl : int
        Cache time-to-live in seconds (default 3600 = 1 hour).
    timeout : int
        HTTP timeout passed to sub-fetchers (default 30s).
    """

    def __init__(self, cache_ttl: int = 3600, timeout: int = _DEFAULT_TIMEOUT) -> None:
        self.cache_ttl = cache_ttl
        self._cache: Dict[str, Tuple[float, dict]] = {}
        self._usgs = USGSWaterFetcher(timeout=timeout)
        self._meteo = OpenMeteoFetcher(timeout=timeout)
        self._solunar = SolunarCalculator()

    def collect(
        self,
        lat: float,
        lon: float,
        date: str,
        usgs_site_id: Optional[str] = None,
    ) -> dict:
        """Collect all environmental features for a location and date.

        Parameters
        ----------
        lat, lon : float
            Location in decimal degrees.
        date : str
            ISO date string (``YYYY-MM-DD``).
        usgs_site_id : str, optional
            USGS site to pull water data from.  If ``None``, water fields
            are returned as NaN.

        Returns
        -------
        dict
            Flat dictionary of environmental features ready for model input.
        """
        cache_key = f"{lat:.4f}_{lon:.4f}_{date}_{usgs_site_id or 'none'}"
        now = time.monotonic()
        if cache_key in self._cache:
            ts, cached = self._cache[cache_key]
            if now - ts < self.cache_ttl:
                logger.debug("Cache hit for %s", cache_key)
                return cached

        result: dict = {"lat": lat, "lon": lon, "date": date}

        # --- USGS water data (with flow deltas for model features) ---
        if usgs_site_id:
            water = self._usgs.fetch_current(usgs_site_id)
            for key in PARAMETER_CODES.values():
                result[key] = water.get(key, float("nan"))
            result["usgs_datetime"] = water.get("datetime")

            # Flow delta features (discharge_delta_1d, discharge_zscore, etc.)
            try:
                flow_features = self._usgs.fetch_flow_features(usgs_site_id)
                for key, value in flow_features.items():
                    if key not in result:  # Don't overwrite direct readings
                        result[key] = value
            except Exception:
                logger.warning(
                    "Flow feature computation failed for %s", usgs_site_id,
                    exc_info=True,
                )
        else:
            for key in PARAMETER_CODES.values():
                result[key] = float("nan")
            result["usgs_datetime"] = None

        # --- Weather ---
        weather = self._meteo.fetch_current(lat, lon)
        for key in OpenMeteoFetcher._COLUMN_MAP.values():
            result[key] = weather.get(key, float("nan"))
        result["weather_datetime"] = weather.get("datetime")

        # --- Solunar ---
        try:
            solunar = self._solunar.calculate(lat, lon, date)
        except Exception:
            logger.warning("Solunar calculation failed for %s", date, exc_info=True)
            solunar = {
                "moon_phase": float("nan"),
                "moon_illumination_pct": float("nan"),
                "moon_phase_name": None,
                "major_periods": [],
                "minor_periods": [],
                "day_length_hours": float("nan"),
                "sunrise": None,
                "sunset": None,
                "moonrise_approx": None,
                "moonset_approx": None,
            }
        result.update(solunar)

        # Prune cache if it grows too large (simple LRU-like eviction)
        if len(self._cache) > 500:
            cutoff = now - self.cache_ttl
            self._cache = {
                k: (t, v) for k, (t, v) in self._cache.items() if t > cutoff
            }

        self._cache[cache_key] = (now, result)
        return result

    def clear_cache(self) -> None:
        """Manually clear the result cache."""
        self._cache.clear()
