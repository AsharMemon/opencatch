"""Water Survey of Canada (WSC/HYDAT) collector.

Fetches real-time and historical hydrometric data from Environment and
Climate Change Canada's Water Office. This is the Canadian equivalent
of the USGS water data service.

Data source: https://wateroffice.ec.gc.ca
API: https://wateroffice.ec.gc.ca/services/real_time/csv/inline

WSC station IDs use a format like "02GA010" (two-digit province prefix,
two-letter region code, three-digit station number).

The output schema matches castline.validation.collectors.usgs so the
data can be merged into the same pipeline.
"""
from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# WSC real-time data endpoint
# ---------------------------------------------------------------------------

WSC_REALTIME_URL = "https://wateroffice.ec.gc.ca/services/real_time/csv/inline"
WSC_HISTORICAL_URL = "https://wateroffice.ec.gc.ca/services/historical/csv/inline"

# WSC parameter IDs mapped to our normalized column names
WSC_PARAMETER_MAP = {
    "Water temperature / Température de l'eau": "water_temp_c",
    "Water level / Niveau d'eau": "gage_height_ft",  # Note: WSC uses metres
    "Discharge / Débit": "discharge_cfs",  # Note: WSC uses m³/s
    "Electrical conductivity / Conductivité électrique": "specific_conductance_us_cm",
    "pH": "ph",
    "Dissolved oxygen / Oxygène dissous": "dissolved_oxygen_mgL",
    "Turbidity / Turbidité": "turbidity_fnu",
}

# Unit conversion factors
M3S_TO_CFS = 35.3147  # 1 m³/s = 35.3147 ft³/s
METRES_TO_FEET = 3.28084

# Known Canadian bass tournament lakes and their WSC station IDs
KNOWN_CANADIAN_GAUGES: dict[str, str] = {
    # Ontario
    "Lake Erie": "02GG002",
    "Lake Ontario": "02HB001",
    "Lake Simcoe": "02EC009",
    "Bay of Quinte": "02HK001",
    "Lake St. Clair": "02GE007",  # Canadian side
    "Rice Lake": "02HJ003",
    "Kawartha Lakes": "02HF001",
    "St. Lawrence River": "02OA016",
    "Ottawa River": "02KF005",
    "Lake Nipissing": "02DD014",
    # Quebec
    "St. Lawrence River QC": "02OJ024",
    "Lake Memphremagog": "02OE005",
    "Lac St-Louis": "02OA031",
    # Manitoba
    "Lake Winnipeg": "05PF069",
    "Red River": "05OC012",
    # British Columbia
    "Okanagan Lake": "08NM050",
    "Thompson River": "08LF002",
    # Saskatchewan
    "Last Mountain Lake": "05JK007",
}


def fetch_wsc_realtime(
    station_id: str,
    start_date: str,
    end_date: str,
    *,
    session: requests.Session | None = None,
    timeout: int = 30,
) -> pd.DataFrame:
    """Fetch real-time hydrometric data from WSC.

    Parameters
    ----------
    station_id : str
        WSC station ID (e.g., "02GA010").
    start_date, end_date : str
        Date range in YYYY-MM-DD format. Real-time data typically
        covers the last 30 days.

    Returns
    -------
    pd.DataFrame
        Columns: date, water_temp_c, discharge_cfs, gage_height_ft, etc.
        Values are converted to US units to match USGS schema.
    """
    requester = session or requests.Session()

    params = {
        "stations[]": station_id,
        "start_date": start_date,
        "end_date": end_date,
    }

    try:
        response = requester.get(
            WSC_REALTIME_URL,
            params=params,
            timeout=timeout,
            headers={"Accept": "text/csv"},
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"wsc: error fetching {station_id}: {exc}", file=sys.stderr)
        return pd.DataFrame()

    return _parse_wsc_csv(response.text, station_id=station_id)


def fetch_wsc_historical(
    station_id: str,
    start_date: str,
    end_date: str,
    *,
    session: requests.Session | None = None,
    timeout: int = 30,
) -> pd.DataFrame:
    """Fetch historical daily mean data from WSC.

    Historical data goes back decades but is aggregated to daily means.
    """
    requester = session or requests.Session()

    params = {
        "stations[]": station_id,
        "start_date": start_date,
        "end_date": end_date,
        "type[]": "daily",
    }

    try:
        response = requester.get(
            WSC_HISTORICAL_URL,
            params=params,
            timeout=timeout,
            headers={"Accept": "text/csv"},
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"wsc: error fetching historical {station_id}: {exc}", file=sys.stderr)
        return pd.DataFrame()

    return _parse_wsc_csv(response.text, station_id=station_id)


def _parse_wsc_csv(csv_text: str, *, station_id: str) -> pd.DataFrame:
    """Parse WSC CSV response into a normalized DataFrame.

    WSC CSV format has columns:
    ID, Date, Parameter, Value, Grade, Symbol, Approval, ...
    """
    if not csv_text.strip():
        return pd.DataFrame()

    try:
        df = pd.read_csv(StringIO(csv_text), low_memory=False)
    except Exception as exc:
        print(f"wsc: CSV parse error for {station_id}: {exc}", file=sys.stderr)
        return pd.DataFrame()

    if df.empty:
        return pd.DataFrame()

    # Identify columns (WSC CSV format varies)
    date_col = None
    param_col = None
    value_col = None

    for col in df.columns:
        col_lower = col.strip().lower()
        if "date" in col_lower and date_col is None:
            date_col = col
        elif "param" in col_lower and param_col is None:
            param_col = col
        elif "value" in col_lower or "discharge" in col_lower or "level" in col_lower:
            if value_col is None:
                value_col = col

    if date_col is None or value_col is None:
        # Try alternative: WSC sometimes returns wide format (one column per parameter)
        return _parse_wsc_wide_format(df, station_id=station_id)

    # Long format: pivot parameters into columns
    rows: dict[str, dict[str, float]] = {}

    for _, row in df.iterrows():
        try:
            date_str = str(row[date_col]).strip()
            date = pd.to_datetime(date_str, errors="coerce")
            if pd.isna(date):
                continue
            date_key = date.strftime("%Y-%m-%d")

            value = row[value_col]
            if pd.isna(value):
                continue
            value = float(value)

            param = str(row.get(param_col, "")).strip() if param_col else "discharge"
            col_name = WSC_PARAMETER_MAP.get(param)
            if col_name is None:
                # Try partial match
                param_lower = param.lower()
                if "discharge" in param_lower or "débit" in param_lower:
                    col_name = "discharge_cfs"
                elif "level" in param_lower or "niveau" in param_lower:
                    col_name = "gage_height_ft"
                elif "temp" in param_lower:
                    col_name = "water_temp_c"
                else:
                    continue

            entry = rows.setdefault(date_key, {"date": pd.Timestamp(date_key)})

            # Convert units
            if col_name == "discharge_cfs":
                value *= M3S_TO_CFS  # m³/s → cfs
            elif col_name == "gage_height_ft":
                value *= METRES_TO_FEET  # metres → feet

            entry[col_name] = value

        except (ValueError, TypeError):
            continue

    if not rows:
        return pd.DataFrame()

    result = pd.DataFrame(rows.values()).sort_values("date").reset_index(drop=True)
    result["site_id"] = f"WSC-{station_id}"
    return result


def _parse_wsc_wide_format(df: pd.DataFrame, *, station_id: str) -> pd.DataFrame:
    """Parse wide-format WSC CSV where each parameter is a column."""
    date_col = None
    for col in df.columns:
        if "date" in col.lower():
            date_col = col
            break

    if date_col is None:
        return pd.DataFrame()

    result_rows = []
    for _, row in df.iterrows():
        try:
            date = pd.to_datetime(row[date_col], errors="coerce")
            if pd.isna(date):
                continue
            entry: dict[str, object] = {"date": date}

            for col in df.columns:
                if col == date_col:
                    continue
                col_lower = col.lower()
                value = row[col]
                if pd.isna(value):
                    continue
                value = float(value)

                if "discharge" in col_lower or "débit" in col_lower:
                    entry["discharge_cfs"] = value * M3S_TO_CFS
                elif "level" in col_lower or "niveau" in col_lower:
                    entry["gage_height_ft"] = value * METRES_TO_FEET
                elif "temp" in col_lower:
                    entry["water_temp_c"] = value

            result_rows.append(entry)
        except (ValueError, TypeError):
            continue

    if not result_rows:
        return pd.DataFrame()

    result = pd.DataFrame(result_rows).sort_values("date").reset_index(drop=True)
    result["site_id"] = f"WSC-{station_id}"
    return result


def collect_wsc_history(
    output_path: Path,
    events: list[dict[str, str]],
    lookback_days: int = 30,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Collect WSC hydrometric data for a list of events.

    Parameters
    ----------
    events : list of dict
        Each dict must have: event_id, site_id (WSC format), date
    lookback_days : int
        Days of history to fetch before each event date.

    Returns
    -------
    pd.DataFrame
        Feature rows matching the USGS schema for pipeline compatibility.
    """
    sess = session or requests.Session()
    rows: list[dict[str, object]] = []

    for event in events:
        event_id = event["event_id"]
        station_id = str(event["site_id"]).replace("WSC-", "").strip()
        event_date = pd.to_datetime(event["date"])

        start = (event_date - pd.Timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        end = event_date.strftime("%Y-%m-%d")

        # Try real-time first, fall back to historical
        history = fetch_wsc_realtime(station_id, start, end, session=sess)
        if history.empty:
            history = fetch_wsc_historical(station_id, start, end, session=sess)

        if history.empty:
            print(f"wsc: no data for {station_id} ({event_id})", file=sys.stderr)
            continue

        # Build feature row (same logic as USGS _build_event_feature_row)
        history = history.sort_values("date").reset_index(drop=True)
        current = history.iloc[-1]

        def _get(col):
            val = current.get(col)
            if val is None or (isinstance(val, float) and pd.isna(val)):
                return float("nan")
            return float(val)

        previous = history.iloc[-2] if len(history) > 1 else current

        def _delta(col):
            c = _get(col)
            p_val = previous.get(col) if previous is not None else None
            if pd.isna(c) or p_val is None or (isinstance(p_val, float) and pd.isna(p_val)):
                return float("nan")
            return c - float(p_val)

        # 7-day means
        prior_7d = history.tail(7)
        temp_7d = float(prior_7d["water_temp_c"].mean()) if "water_temp_c" in prior_7d.columns and prior_7d["water_temp_c"].notna().any() else float("nan")
        discharge_7d = float(prior_7d["discharge_cfs"].mean()) if "discharge_cfs" in prior_7d.columns and prior_7d["discharge_cfs"].notna().any() else float("nan")

        rows.append({
            "event_id": event_id,
            "site_id": f"WSC-{station_id}",
            "observation_date": event_date.strftime("%Y-%m-%d"),
            "water_temp_c": _get("water_temp_c"),
            "discharge_cfs": _get("discharge_cfs"),
            "gage_height_ft": _get("gage_height_ft"),
            "dissolved_oxygen_mgL": _get("dissolved_oxygen_mgL"),
            "turbidity_fnu": _get("turbidity_fnu"),
            "specific_conductance_us_cm": _get("specific_conductance_us_cm"),
            "ph": _get("ph"),
            "temp_delta_24h_c": _delta("water_temp_c"),
            "water_temp_7d_mean": temp_7d,
            "discharge_7d_mean": discharge_7d,
            "source_mode": "wsc_realtime",
        })

    df = pd.DataFrame(rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    print(f"wsc: wrote {len(df)} rows to {output_path}", file=sys.stderr)
    return df


def resolve_wsc_station(water_body: str) -> str:
    """Resolve a Canadian water body name to a WSC station ID."""
    name_lower = water_body.lower().strip()
    for lake, station in KNOWN_CANADIAN_GAUGES.items():
        if lake.lower() in name_lower or name_lower in lake.lower():
            return station
    return ""
