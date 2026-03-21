"""USGS Instantaneous Values (IV) collector for temporal model.

Fetches 15-minute interval data from USGS streamgauges for building
temporal features. This is the key data source for the TFT model —
instead of daily averages, we get the full environmental time series.

Parameters available at 15-min resolution:
- 00010: Water temperature (°C)
- 00060: Discharge (cfs)
- 00065: Gage height (ft)
- 63680: Turbidity (FNU)
- 00300: Dissolved oxygen (mg/L)
- 00400: pH
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

USGS_IV_URL = "https://waterservices.usgs.gov/nwis/iv/"

# Key parameters for fishing predictions
PARAM_CODES = {
    "00010": "water_temp_c",
    "00060": "discharge_cfs",
    "00065": "gage_height_ft",
    "63680": "turbidity_fnu",
    "00300": "dissolved_oxygen_mgL",
    "00400": "ph",
}

PARAM_STRING = ",".join(PARAM_CODES.keys())


def fetch_iv_window(
    site_id: str,
    center_date: str,
    hours_before: int = 72,
    hours_after: int = 0,
) -> pd.DataFrame:
    """Fetch IV data for a time window around a date.

    Args:
        site_id: USGS site ID (8-digit string)
        center_date: Center date (YYYY-MM-DD)
        hours_before: Hours of data before center_date (default 72 = 3 days)
        hours_after: Hours of data after center_date

    Returns:
        DataFrame with columns: datetime, water_temp_c, discharge_cfs, etc.
        Indexed at 15-minute intervals.
    """
    site_id = str(site_id).zfill(8)
    center = datetime.strptime(center_date, "%Y-%m-%d")
    start = center - timedelta(hours=hours_before)
    end = center + timedelta(hours=hours_after)

    params = {
        "sites": site_id,
        "parameterCd": PARAM_STRING,
        "startDT": start.strftime("%Y-%m-%dT%H:%M-05:00"),
        "endDT": end.strftime("%Y-%m-%dT%H:%M-05:00"),
        "format": "json",
        "siteStatus": "all",
    }

    try:
        resp = requests.get(USGS_IV_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  USGS IV error for {site_id}: {e}")
        return pd.DataFrame()

    ts_list = data.get("value", {}).get("timeSeries", [])
    if not ts_list:
        return pd.DataFrame()

    records = {}
    for ts in ts_list:
        var_code = ts.get("variable", {}).get("variableCode", [{}])[0].get("value", "")
        col_name = PARAM_CODES.get(var_code, var_code)

        values = ts.get("values", [{}])[0].get("value", [])
        for v in values:
            dt = v.get("dateTime", "")
            val = v.get("value", "")
            qualifier = v.get("qualifiers", [""])

            if dt not in records:
                records[dt] = {"datetime": dt}

            try:
                records[dt][col_name] = float(val) if val and val != "" else float("nan")
            except (ValueError, TypeError):
                records[dt][col_name] = float("nan")

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(list(records.values()))
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.sort_values("datetime").reset_index(drop=True)

    # Resample to regular 15-min intervals
    df = df.set_index("datetime")
    df = df.resample("15min").mean()
    df = df.reset_index()

    return df


def compute_temporal_features(iv_df: pd.DataFrame) -> dict[str, float]:
    """Compute derived temporal features from IV time series.

    These capture the temporal dynamics that GBM can't learn from snapshots:
    - Rate of change (is water temp rising or falling?)
    - Recent trends (discharge increasing over last 24h?)
    - Event detection (was there a flow spike in the last 12h?)
    - Stability metrics (how stable were conditions?)

    Returns a dict of feature_name -> value.
    """
    if iv_df.empty:
        return {}

    features = {}

    for col in ["water_temp_c", "discharge_cfs", "gage_height_ft"]:
        if col not in iv_df.columns:
            continue

        series = iv_df[col].dropna()
        if len(series) < 4:
            continue

        # Current value (most recent)
        features[f"{col}_current"] = float(series.iloc[-1])

        # Rate of change over last 3 hours (12 readings)
        if len(series) >= 12:
            recent = series.iloc[-12:]
            features[f"{col}_delta_3h"] = float(recent.iloc[-1] - recent.iloc[0])

        # Rate of change over last 24 hours (96 readings)
        if len(series) >= 96:
            day = series.iloc[-96:]
            features[f"{col}_delta_24h"] = float(day.iloc[-1] - day.iloc[0])

        # Coefficient of variation over last 24h (stability)
        if len(series) >= 96:
            day = series.iloc[-96:]
            mean_val = day.mean()
            if mean_val != 0:
                features[f"{col}_cv_24h"] = float(day.std() / abs(mean_val))

        # Max spike in last 72h
        features[f"{col}_max_72h"] = float(series.max())
        features[f"{col}_min_72h"] = float(series.min())
        features[f"{col}_range_72h"] = float(series.max() - series.min())

        # Hours since max value (recency of peak)
        if len(series) >= 4:
            max_idx = series.idxmax()
            if hasattr(max_idx, 'item'):
                max_idx = max_idx.item()
            hours_since_max = (len(series) - series.index.get_loc(max_idx)) * 0.25
            features[f"{col}_hours_since_max"] = float(hours_since_max)

    # Discharge-specific: detect flow spikes
    if "discharge_cfs" in iv_df.columns:
        q = iv_df["discharge_cfs"].dropna()
        if len(q) >= 96:
            # Rolling 6h mean
            q_6h = q.rolling(24).mean().dropna()
            if len(q_6h) >= 48:
                # Spike = current 6h mean vs previous 24h mean
                current_6h = q_6h.iloc[-1]
                prev_24h = q_6h.iloc[-96:-24].mean()
                if prev_24h > 0:
                    features["discharge_spike_ratio"] = float(current_6h / prev_24h)

    return features


def collect_iv_for_events(
    events: pd.DataFrame,
    output_path: str | Path,
    hours_before: int = 72,
    rate_limit_sec: float = 0.5,
) -> pd.DataFrame:
    """Collect IV data for a batch of events.

    Args:
        events: DataFrame with columns: usgs_site_id, date
        output_path: Where to save the collected IV features
        hours_before: Hours of historical data to fetch per event
        rate_limit_sec: Seconds between API calls

    Returns:
        DataFrame with temporal features for each event.
    """
    output_path = Path(output_path)

    # Load checkpoint if exists
    if output_path.exists():
        existing = pd.read_csv(output_path)
        if "usgs_site_id" in existing.columns and "date" in existing.columns:
            existing_keys = set(zip(existing["usgs_site_id"].astype(str), existing["date"].astype(str)))
        else:
            # Old format — start fresh for new format
            existing = pd.DataFrame()
            existing_keys = set()
        print(f"Loaded {len(existing)} existing IV feature records")
    else:
        existing = pd.DataFrame()
        existing_keys = set()

    new_records = []
    total = len(events)

    for i, (_, row) in enumerate(events.iterrows()):
        raw_id = row.get("usgs_site_id", "")
        date = str(row.get("date", "")).strip()

        if not date or str(raw_id).strip() in ("", "nan", "None"):
            continue

        # Handle float-formatted site IDs (e.g., 7194500.0 -> 07194500)
        try:
            site_id = str(int(float(raw_id))).zfill(8)
        except (ValueError, TypeError):
            site_id = str(raw_id).strip().zfill(8)
        key = (site_id, date)
        if key in existing_keys:
            continue

        print(f"[{i+1}/{total}] Fetching IV: site={site_id} date={date}")

        iv_df = fetch_iv_window(site_id, date, hours_before=hours_before)
        features = compute_temporal_features(iv_df)

        if features:
            features["usgs_site_id"] = site_id
            features["date"] = date
            features["iv_readings"] = len(iv_df)
            new_records.append(features)
            existing_keys.add(key)

        time.sleep(rate_limit_sec)

        # Checkpoint every 50 events
        if len(new_records) > 0 and len(new_records) % 50 == 0:
            _save_checkpoint(existing, new_records, output_path)
            print(f"  Checkpoint: {len(new_records)} new records saved")

    # Final save
    if new_records:
        _save_checkpoint(existing, new_records, output_path)

    result = pd.read_csv(output_path) if output_path.exists() else pd.DataFrame()
    print(f"Total IV feature records: {len(result)}")
    return result


def _save_checkpoint(existing: pd.DataFrame, new_records: list[dict], path: Path):
    new_df = pd.DataFrame(new_records)
    if len(existing) > 0:
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df
    combined.to_csv(path, index=False)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python usgs_iv.py <site_id> <date>")
        print("Example: python usgs_iv.py 03572110 2024-04-15")
        sys.exit(1)

    site_id = sys.argv[1]
    date = sys.argv[2]

    print(f"Fetching 72h IV data for site {site_id} around {date}...")
    df = fetch_iv_window(site_id, date)
    print(f"Got {len(df)} readings")
    if not df.empty:
        print(f"Columns: {list(df.columns)}")
        print(f"Time range: {df['datetime'].min()} to {df['datetime'].max()}")
        print(df.describe())

        features = compute_temporal_features(df)
        print(f"\nDerived temporal features ({len(features)}):")
        for k, v in sorted(features.items()):
            print(f"  {k}: {v:.4f}")
