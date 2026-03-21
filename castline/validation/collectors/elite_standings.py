"""Bassmaster Elite Series standings collector via REST API.

Discovers Elite Series tournaments via the Bassmaster WordPress API and
fetches per-day results from the ``/wp-json/data/v1/tournament/daily-results``
endpoint to produce event-day outcome rows compatible with ``outcomes.py``.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from castline.validation.collectors.outcomes import (
    DEFAULT_BASSMASTER_SPECIES,
    DEFAULT_HEADERS,
    KNOWN_LAKE_GAUGES,
    REQUIRED_OUTCOME_COLUMNS,
    _auto_resolve_usgs_site,
    _clean_text,
    _seasonal_baseline_signal,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASSMASTER_WP_API = "https://www.bassmaster.com/wp-json/wp/v2/tournament"
BASSMASTER_DATA_API = "https://www.bassmaster.com/wp-json/data/v1"
BASSMASTER_TOURNAMENTS_API = "https://www.bassmaster.com/wp-json/bassmaster/v1/tournaments"
BASSMASTER_BASE = "https://www.bassmaster.com"

MIN_WEIGHTS_FOR_MEDIAN = 5
WP_PER_PAGE = 100

# Polite delay between API requests (seconds).
REQUEST_DELAY = 0.5

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class EliteTournament:
    """Metadata for a single Bassmaster Elite Series event."""

    wp_id: int
    slug: str
    title: str
    water_body: str
    city: str
    state: str
    start_date: pd.Timestamp
    end_date: pd.Timestamp | None = None
    num_days: int = 4
    tms_id: int | None = None  # basstms tournament ID for data API
    species: str = DEFAULT_BASSMASTER_SPECIES
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def location(self) -> str:
        parts = [self.water_body, self.city, self.state]
        return ", ".join(p for p in parts if p)

    @property
    def event_id_prefix(self) -> str:
        return f"elite-{self.slug}"


# ---------------------------------------------------------------------------
# Tournament discovery
# ---------------------------------------------------------------------------


def _fetch_json(
    url: str,
    *,
    session: requests.Session,
    params: dict[str, Any] | None = None,
) -> Any:
    """Fetch JSON from a Bassmaster API endpoint."""
    resp = session.get(url, params=params, timeout=30, headers=DEFAULT_HEADERS)
    resp.raise_for_status()
    return resp.json()


def _is_elite_tournament(item: dict[str, Any]) -> bool:
    slug = str(item.get("slug", "")).lower()
    title_rendered = str((item.get("title") or {}).get("rendered", "")).lower()
    meta = item.get("meta", {}) or {}
    tour_type = str(meta.get("bassmaster_tournament_type", "")).lower()
    trail = str(meta.get("bassmaster_tournament_trail", "")).lower()
    for text in [slug, title_rendered, tour_type, trail]:
        if "elite" in text:
            return True
    return False


def _tournament_year(item: dict[str, Any]) -> int | None:
    meta = item.get("meta", {}) or {}
    start_raw = meta.get("bassmaster_tournament_start_date", "")
    if start_raw:
        ts = pd.to_datetime(start_raw, errors="coerce")
        if not pd.isna(ts):
            return int(ts.year)
    slug = str(item.get("slug", ""))
    match = re.match(r"(\d{4})-", slug)
    if match:
        return int(match.group(1))
    return None


def _extract_tms_id(html: str) -> int | None:
    """Extract the basstms tournament ID from the results page HTML."""
    m = re.search(r'"tms"\s*:\s*(\d+)', html)
    if m:
        return int(m.group(1))
    return None


def _extract_num_days_from_html(html: str) -> int | None:
    """Extract number of competition days from the all_dates array in bassConfig."""
    m = re.search(r'"all_dates"\s*:\s*\[([^\]]+)\]', html)
    if m:
        dates = re.findall(r'"(\d{8})"', m.group(1))
        if dates:
            return len(dates)
    return None


def discover_elite_tournaments(
    session: requests.Session,
    start_year: int = 2014,
    end_year: int = 2025,
) -> list[EliteTournament]:
    """Query the Bassmaster WordPress API for Elite Series events."""
    tournaments: list[EliteTournament] = []
    seen_ids: set[int] = set()
    page = 1

    while True:
        try:
            payload = _fetch_json(
                BASSMASTER_WP_API,
                session=session,
                params={
                    "page": page,
                    "per_page": WP_PER_PAGE,
                    "_fields": "id,slug,link,title,content,meta",
                },
            )
        except requests.HTTPError as exc:
            resp = getattr(exc, "response", None)
            if resp is not None and resp.status_code in {400, 404} and page > 1:
                break
            raise
        if not payload:
            break

        for item in payload:
            wp_id = int(item.get("id", 0))
            if wp_id in seen_ids:
                continue
            seen_ids.add(wp_id)

            if not _is_elite_tournament(item):
                continue

            year = _tournament_year(item)
            if year is not None and not (start_year <= year <= end_year):
                continue

            meta = item.get("meta", {}) or {}
            start_raw = meta.get("bassmaster_tournament_start_date", "")
            start_date = pd.to_datetime(start_raw, errors="coerce")
            if pd.isna(start_date):
                continue

            end_raw = meta.get("bassmaster_tournament_end_date", "")
            end_date = pd.to_datetime(end_raw, errors="coerce")
            if pd.isna(end_date):
                end_date = None

            water_body = _clean_text(meta.get("bassmaster_tournament_body_of_water", ""))
            city = _clean_text(meta.get("bassmaster_tournament_city", ""))
            state = _clean_text(meta.get("bassmaster_tournament_state", ""))
            slug = str(item.get("slug", "")).strip()
            title = _clean_text((item.get("title") or {}).get("rendered", slug))

            num_days = 4
            if end_date is not None:
                delta = (end_date - start_date).days + 1
                if 1 <= delta <= 6:
                    num_days = delta

            tournaments.append(
                EliteTournament(
                    wp_id=wp_id,
                    slug=slug,
                    title=title,
                    water_body=water_body,
                    city=city,
                    state=state,
                    start_date=start_date,
                    end_date=end_date,
                    num_days=num_days,
                    meta=meta,
                )
            )

        if len(payload) < WP_PER_PAGE:
            break
        page += 1

    tournaments.sort(key=lambda t: (t.start_date, t.slug))
    print(
        f"elite_standings: discovered {len(tournaments)} Elite tournaments "
        f"({start_year}-{end_year})",
        file=sys.stderr,
    )
    return tournaments


# ---------------------------------------------------------------------------
# TMS ID resolution
# ---------------------------------------------------------------------------


def _resolve_tms_id(
    session: requests.Session,
    tournament: EliteTournament,
) -> int | None:
    """Fetch the results page to extract the basstms tournament_id."""
    url = f"{BASSMASTER_BASE}/tournament/{tournament.slug}/results/?day=0"
    try:
        resp = session.get(url, timeout=30, headers=DEFAULT_HEADERS)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(
            f"elite_standings: error fetching results page for {tournament.slug}: {exc}",
            file=sys.stderr,
        )
        return None

    tms_id = _extract_tms_id(resp.text)
    if tms_id:
        # Also try to refine num_days from the page
        num_days = _extract_num_days_from_html(resp.text)
        if num_days and 1 <= num_days <= 6:
            tournament.num_days = num_days
    return tms_id


# ---------------------------------------------------------------------------
# Data API — daily results
# ---------------------------------------------------------------------------


_WEIGHT_RE = re.compile(r"(\d{1,3})\s*-\s*(\d{1,2})")


def _weight_str_to_lb(text: str) -> float | None:
    """Convert a 'LB-OZ' string to decimal pounds."""
    m = _WEIGHT_RE.search(str(text))
    if not m:
        return None
    lbs = int(m.group(1))
    ozs = int(m.group(2))
    if ozs > 15:
        return None
    return lbs + ozs / 16.0


def _ounces_to_lb(ounces: Any) -> float | None:
    """Convert ounces (int) to decimal pounds."""
    try:
        oz = int(ounces)
        if oz <= 0:
            return None
        return oz / 16.0
    except (ValueError, TypeError):
        return None


def fetch_daily_results(
    session: requests.Session,
    tms_id: int,
    day: int,
) -> list[dict[str, Any]]:
    """Fetch daily results from the Bassmaster data API."""
    url = f"{BASSMASTER_DATA_API}/tournament/daily-results"
    try:
        resp = session.get(
            url,
            params={
                "tournament_id": tms_id,
                "day": day,
                "class": "anglers",
            },
            timeout=30,
            headers=DEFAULT_HEADERS,
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        return []
    except (requests.RequestException, json.JSONDecodeError) as exc:
        print(
            f"elite_standings: API error for tms={tms_id} day={day}: {exc}",
            file=sys.stderr,
        )
        return []


def extract_day_weights(
    results: list[dict[str, Any]],
    num_days: int,
) -> dict[int, list[float]]:
    """Extract per-day weight lists from daily-results API response.

    The API returns fields like:
      - TotalOuncesAfterPenalty_dayN  (ounces, integer)
      - TotalPdsOzAfterPenalty_dayN   (string like "31-3")
      - FishCount_dayN
    """
    day_weights: dict[int, list[float]] = {}

    for day_num in range(1, num_days + 1):
        weights: list[float] = []
        oz_key = f"TotalOuncesAfterPenalty_day{day_num}"
        str_key = f"TotalPdsOzAfterPenalty_day{day_num}"

        for angler in results:
            # Prefer ounces (more precise)
            w = _ounces_to_lb(angler.get(oz_key))
            if w is None:
                w = _weight_str_to_lb(str(angler.get(str_key, "")))
            if w is not None and w > 0:
                weights.append(w)

        if len(weights) >= MIN_WEIGHTS_FOR_MEDIAN:
            day_weights[day_num] = weights

    return day_weights


# ---------------------------------------------------------------------------
# Outcome collection
# ---------------------------------------------------------------------------


def _median_weight(weights: list[float]) -> float:
    return round(statistics.median(weights), 4)


def collect_elite_outcomes(
    output_path: Path | str,
    start_year: int = 2014,
    end_year: int = 2025,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Collect Elite Series per-day outcomes via the Bassmaster data API.

    Each tournament day with sufficient angler data becomes one row.
    """
    output_path = Path(output_path)
    owned_session = session is None
    session = session or requests.Session()
    rows: list[dict[str, Any]] = []

    try:
        tournaments = discover_elite_tournaments(
            session, start_year=start_year, end_year=end_year
        )

        for tournament in tournaments:
            time.sleep(REQUEST_DELAY)

            # Step 1: resolve TMS ID from the results page
            tms_id = _resolve_tms_id(session, tournament)
            if not tms_id:
                print(
                    f"elite_standings: no TMS ID for {tournament.slug}, skipping",
                    file=sys.stderr,
                )
                continue
            tournament.tms_id = tms_id

            time.sleep(REQUEST_DELAY)

            # Step 2: fetch daily results (request the last day to get all per-day breakdowns)
            results = fetch_daily_results(session, tms_id, tournament.num_days)
            if not results:
                # Try final-results as fallback
                print(
                    f"elite_standings: no daily-results for {tournament.slug} "
                    f"(tms={tms_id}), trying earlier days",
                    file=sys.stderr,
                )
                # Try each day from last to first
                for try_day in range(tournament.num_days - 1, 0, -1):
                    time.sleep(REQUEST_DELAY)
                    results = fetch_daily_results(session, tms_id, try_day)
                    if results:
                        tournament.num_days = try_day
                        break

            if not results:
                print(
                    f"elite_standings: no results data for {tournament.slug}",
                    file=sys.stderr,
                )
                continue

            # Step 3: extract per-day weights
            day_weights = extract_day_weights(results, tournament.num_days)
            if not day_weights:
                print(
                    f"elite_standings: no parseable weights for {tournament.slug}",
                    file=sys.stderr,
                )
                continue

            # Step 4: resolve USGS gauge
            usgs_site_id = _auto_resolve_usgs_site(tournament.water_body)
            if not usgs_site_id:
                for part in tournament.location.split(","):
                    usgs_site_id = _auto_resolve_usgs_site(part.strip())
                    if usgs_site_id:
                        break

            # Step 5: build outcome rows
            for day_num in sorted(day_weights.keys()):
                weights = day_weights[day_num]
                if len(weights) < MIN_WEIGHTS_FOR_MEDIAN:
                    continue

                event_date = (
                    tournament.start_date + timedelta(days=day_num - 1)
                ).strftime("%Y-%m-%d")

                rows.append(
                    {
                        "event_id": f"{tournament.event_id_prefix}-day-{day_num}",
                        "tournament_slug": tournament.slug,
                        "event_name": tournament.title,
                        "date": event_date,
                        "location": tournament.location,
                        "species": tournament.species,
                        "median_weight_lb": _median_weight(weights),
                        "baseline_signal": _seasonal_baseline_signal(event_date),
                        "usgs_site_id": usgs_site_id,
                        "results_source": "elite_api",
                        "num_anglers": len(weights),
                        "day_number": day_num,
                        "tms_id": tms_id,
                    }
                )

            print(
                f"elite_standings: {tournament.slug} → "
                f"{len(day_weights)} day(s), {len(results)} anglers, "
                f"gauge={usgs_site_id or 'UNMAPPED'}",
                file=sys.stderr,
            )

    finally:
        if owned_session:
            session.close()

    df = pd.DataFrame(rows)
    if df.empty:
        print(
            f"elite_standings: WARNING — no outcome rows produced for "
            f"{start_year}-{end_year}",
            file=sys.stderr,
        )
        df = pd.DataFrame(
            columns=[
                *REQUIRED_OUTCOME_COLUMNS,
                "tournament_slug",
                "results_source",
                "num_anglers",
                "day_number",
                "tms_id",
            ]
        )
    else:
        mapped = df["usgs_site_id"].astype(str).str.strip().ne("")
        unmapped_count = (~mapped).sum()
        if unmapped_count:
            unmapped_slugs = df.loc[~mapped, "tournament_slug"].unique().tolist()
            print(
                f"elite_standings: {unmapped_count} rows from "
                f"{len(unmapped_slugs)} unmapped tournaments kept (no gauge): "
                + ", ".join(sorted(unmapped_slugs)[:10]),
                file=sys.stderr,
            )
        # Keep unmapped rows too — they still have outcome data for later gauge resolution

    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    print(
        f"elite_standings: wrote {len(df)} rows to {output_path}",
        file=sys.stderr,
    )
    return df


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Collect Bassmaster Elite Series standings via data API"
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("castline/validation/data/raw/elite_outcomes.csv"),
    )
    parser.add_argument("--start-year", type=int, default=2014)
    parser.add_argument("--end-year", type=int, default=2025)
    args = parser.parse_args()

    result = collect_elite_outcomes(
        output_path=args.output,
        start_year=args.start_year,
        end_year=args.end_year,
    )
    print(f"\nDone. {len(result)} outcome rows.", file=sys.stderr)
