"""Bassmaster ALL-series tournament collector.

Extends the Elite collector to pull every Bassmaster trail:
  - Elite Series
  - Bassmaster Open
  - B.A.S.S. Nation
  - Strike King College Series
  - High School Series
  - Kayak Series
  - Junior Series
  - Bassmaster Classic

Uses the same WordPress API discovery + TMS data API approach as
elite_standings.py but removes the Elite-only filter.
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
BASSMASTER_BASE = "https://www.bassmaster.com"

MIN_WEIGHTS_FOR_MEDIAN = 3  # Lower threshold for smaller series (Kayak, Junior)
WP_PER_PAGE = 100
REQUEST_DELAY = 0.4

# Trail classification keywords
TRAIL_KEYWORDS = {
    "elite": ["elite"],
    "open": ["open"],
    "nation": ["nation"],
    "college": ["college"],
    "high_school": ["high school", "high-school"],
    "kayak": ["kayak"],
    "junior": ["junior"],
    "classic": ["classic"],
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BassmasterTournament:
    """Metadata for any Bassmaster tournament."""

    wp_id: int
    slug: str
    title: str
    water_body: str
    city: str
    state: str
    start_date: pd.Timestamp
    end_date: pd.Timestamp | None = None
    num_days: int = 2
    tms_id: int | None = None
    trail: str = "unknown"
    species: str = DEFAULT_BASSMASTER_SPECIES
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def location(self) -> str:
        parts = [self.water_body, self.city, self.state]
        return ", ".join(p for p in parts if p)

    @property
    def event_id_prefix(self) -> str:
        return self.slug


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fetch_json(
    url: str,
    *,
    session: requests.Session,
    params: dict[str, Any] | None = None,
) -> Any:
    resp = session.get(url, params=params, timeout=30, headers=DEFAULT_HEADERS)
    resp.raise_for_status()
    return resp.json()


def _classify_trail(item: dict[str, Any]) -> str:
    """Classify tournament trail from WP metadata."""
    slug = str(item.get("slug", "")).lower()
    title_rendered = str((item.get("title") or {}).get("rendered", "")).lower()
    meta = item.get("meta", {}) or {}
    tour_type = str(meta.get("bassmaster_tournament_type", "")).lower()
    trail_field = str(meta.get("bassmaster_tournament_trail", "")).lower()

    texts = [slug, title_rendered, tour_type, trail_field]
    combined = " ".join(texts)

    # Check in priority order (more specific first)
    for trail_name, keywords in TRAIL_KEYWORDS.items():
        for kw in keywords:
            if kw in combined:
                return trail_name

    return "other"


def _extract_tms_id(html: str) -> int | None:
    m = re.search(r'"tms"\s*:\s*(\d+)', html)
    return int(m.group(1)) if m else None


def _extract_num_days_from_html(html: str) -> int | None:
    m = re.search(r'"all_dates"\s*:\s*\[([^\]]+)\]', html)
    if m:
        dates = re.findall(r'"(\d{8})"', m.group(1))
        if dates:
            return len(dates)
    return None


# ---------------------------------------------------------------------------
# Weight parsing
# ---------------------------------------------------------------------------

_WEIGHT_RE = re.compile(r"(\d{1,3})\s*-\s*(\d{1,2})")


def _weight_str_to_lb(text: str) -> float | None:
    m = _WEIGHT_RE.search(str(text))
    if not m:
        return None
    lbs = int(m.group(1))
    ozs = int(m.group(2))
    if ozs > 15:
        return None
    return lbs + ozs / 16.0


def _ounces_to_lb(ounces: Any) -> float | None:
    try:
        oz = int(ounces)
        if oz <= 0:
            return None
        return oz / 16.0
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Tournament discovery (ALL trails)
# ---------------------------------------------------------------------------


def discover_all_tournaments(
    session: requests.Session,
    start_year: int = 2014,
    end_year: int = 2025,
    trails: set[str] | None = None,
) -> list[BassmasterTournament]:
    """Query the Bassmaster WordPress API for ALL tournament trails.

    Parameters
    ----------
    trails : set of str, optional
        Filter to specific trails (e.g. {"college", "nation", "open"}).
        None means all trails.
    """
    tournaments: list[BassmasterTournament] = []
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

            trail = _classify_trail(item)
            if trails and trail not in trails:
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

            num_days = 2  # default for non-Elite
            if trail == "elite":
                num_days = 4
            elif trail == "classic":
                num_days = 3
            elif trail in ("kayak", "junior"):
                num_days = 1

            if end_date is not None:
                delta = (end_date - start_date).days + 1
                if 1 <= delta <= 6:
                    num_days = delta

            tournaments.append(
                BassmasterTournament(
                    wp_id=wp_id,
                    slug=slug,
                    title=title,
                    water_body=water_body,
                    city=city,
                    state=state,
                    start_date=start_date,
                    end_date=end_date,
                    num_days=num_days,
                    trail=trail,
                    meta=meta,
                )
            )

        if len(payload) < WP_PER_PAGE:
            break
        page += 1
        time.sleep(REQUEST_DELAY)

    tournaments.sort(key=lambda t: (t.start_date, t.slug))

    # Print summary by trail
    trail_counts: dict[str, int] = {}
    for t in tournaments:
        trail_counts[t.trail] = trail_counts.get(t.trail, 0) + 1
    print(
        f"all_bassmaster: discovered {len(tournaments)} tournaments "
        f"({start_year}-{end_year}): {trail_counts}",
        file=sys.stderr,
    )
    return tournaments


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


# ---------------------------------------------------------------------------
# TMS ID resolution
# ---------------------------------------------------------------------------


def _resolve_tms_id(
    session: requests.Session,
    tournament: BassmasterTournament,
) -> int | None:
    url = f"{BASSMASTER_BASE}/tournament/{tournament.slug}/results/?day=0"
    try:
        resp = session.get(url, timeout=30, headers=DEFAULT_HEADERS)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(
            f"all_bassmaster: error fetching results for {tournament.slug}: {exc}",
            file=sys.stderr,
        )
        return None

    tms_id = _extract_tms_id(resp.text)
    if tms_id:
        num_days = _extract_num_days_from_html(resp.text)
        if num_days and 1 <= num_days <= 6:
            tournament.num_days = num_days
    return tms_id


# ---------------------------------------------------------------------------
# Data API — daily results
# ---------------------------------------------------------------------------


def fetch_daily_results(
    session: requests.Session,
    tms_id: int,
    day: int,
) -> list[dict[str, Any]]:
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
        return data if isinstance(data, list) else []
    except (requests.RequestException, json.JSONDecodeError) as exc:
        print(
            f"all_bassmaster: API error tms={tms_id} day={day}: {exc}",
            file=sys.stderr,
        )
        return []


def extract_day_weights(
    results: list[dict[str, Any]],
    num_days: int,
) -> dict[int, list[float]]:
    day_weights: dict[int, list[float]] = {}

    for day_num in range(1, num_days + 1):
        weights: list[float] = []
        oz_key = f"TotalOuncesAfterPenalty_day{day_num}"
        str_key = f"TotalPdsOzAfterPenalty_day{day_num}"

        for angler in results:
            w = _ounces_to_lb(angler.get(oz_key))
            if w is None:
                w = _weight_str_to_lb(str(angler.get(str_key, "")))
            if w is not None and w > 0:
                weights.append(w)

        if len(weights) >= MIN_WEIGHTS_FOR_MEDIAN:
            day_weights[day_num] = weights

    return day_weights


# ---------------------------------------------------------------------------
# Main collection
# ---------------------------------------------------------------------------


def collect_all_bassmaster_outcomes(
    output_path: Path | str,
    start_year: int = 2014,
    end_year: int = 2025,
    trails: set[str] | None = None,
    session: requests.Session | None = None,
    checkpoint_every: int = 25,
) -> pd.DataFrame:
    """Collect per-day outcomes for ALL Bassmaster tournament trails.

    Checkpoints progress every N tournaments to avoid losing work.
    """
    output_path = Path(output_path)
    checkpoint_path = output_path.with_suffix(".checkpoint.csv")
    owned_session = session is None
    session = session or requests.Session()
    rows: list[dict[str, Any]] = []

    # Resume from checkpoint if exists
    processed_slugs: set[str] = set()
    if checkpoint_path.exists():
        try:
            existing = pd.read_csv(checkpoint_path)
            rows = existing.to_dict("records")
            processed_slugs = set(existing["tournament_slug"].unique())
            print(
                f"all_bassmaster: resuming from checkpoint — "
                f"{len(rows)} rows, {len(processed_slugs)} tournaments",
                file=sys.stderr,
            )
        except Exception:
            pass

    try:
        tournaments = discover_all_tournaments(
            session, start_year=start_year, end_year=end_year, trails=trails
        )

        total = len(tournaments)
        skipped = 0
        no_tms = 0
        no_results = 0

        for idx, tournament in enumerate(tournaments, 1):
            if tournament.slug in processed_slugs:
                skipped += 1
                continue

            time.sleep(REQUEST_DELAY)

            # Step 1: resolve TMS ID
            tms_id = _resolve_tms_id(session, tournament)
            if not tms_id:
                no_tms += 1
                continue
            tournament.tms_id = tms_id

            time.sleep(REQUEST_DELAY)

            # Step 2: fetch daily results
            results = fetch_daily_results(session, tms_id, tournament.num_days)
            if not results:
                for try_day in range(tournament.num_days - 1, 0, -1):
                    time.sleep(REQUEST_DELAY)
                    results = fetch_daily_results(session, tms_id, try_day)
                    if results:
                        tournament.num_days = try_day
                        break

            if not results:
                no_results += 1
                continue

            # Step 3: extract per-day weights
            day_weights = extract_day_weights(results, tournament.num_days)
            if not day_weights:
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
                        "median_weight_lb": round(statistics.median(weights), 4),
                        "baseline_signal": _seasonal_baseline_signal(event_date),
                        "usgs_site_id": usgs_site_id or "",
                        "results_source": f"bassmaster_{tournament.trail}",
                        "num_anglers": len(weights),
                        "day_number": day_num,
                        "tms_id": tms_id,
                        "trail": tournament.trail,
                    }
                )

            processed_slugs.add(tournament.slug)

            print(
                f"all_bassmaster: [{idx}/{total}] {tournament.trail}: "
                f"{tournament.slug} → {len(day_weights)} day(s), "
                f"{len(results)} anglers, gauge={usgs_site_id or 'UNMAPPED'}",
                file=sys.stderr,
            )

            # Checkpoint
            if idx % checkpoint_every == 0:
                _save_checkpoint(rows, checkpoint_path)

    finally:
        if owned_session:
            session.close()

    # Final save
    df = pd.DataFrame(rows)
    if df.empty:
        df = pd.DataFrame(
            columns=[
                *REQUIRED_OUTCOME_COLUMNS,
                "tournament_slug",
                "results_source",
                "num_anglers",
                "day_number",
                "tms_id",
                "trail",
            ]
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)

    # Clean up checkpoint
    if checkpoint_path.exists():
        checkpoint_path.unlink()

    # Summary
    if not df.empty:
        trail_summary = df.groupby("trail").size().to_dict()
        mapped = df["usgs_site_id"].astype(str).str.strip().ne("").sum()
        print(
            f"\nall_bassmaster: DONE — {len(df)} total rows\n"
            f"  By trail: {trail_summary}\n"
            f"  USGS mapped: {mapped}/{len(df)} ({100*mapped/len(df):.0f}%)\n"
            f"  Unique tournaments: {df['tournament_slug'].nunique()}\n"
            f"  Skipped (already done): {skipped}\n"
            f"  No TMS ID: {no_tms}\n"
            f"  No results data: {no_results}\n"
            f"  Wrote to: {output_path}",
            file=sys.stderr,
        )

    return df


def _save_checkpoint(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)
    print(f"all_bassmaster: checkpoint saved ({len(rows)} rows)", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Collect ALL Bassmaster tournament standings"
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("castline/validation/data/raw/all_bassmaster_outcomes.csv"),
    )
    parser.add_argument("--start-year", type=int, default=2014)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument(
        "--trails",
        nargs="*",
        help="Filter to specific trails (e.g. college nation open)",
    )
    args = parser.parse_args()

    trail_filter = set(args.trails) if args.trails else None
    result = collect_all_bassmaster_outcomes(
        output_path=args.output,
        start_year=args.start_year,
        end_year=args.end_year,
        trails=trail_filter,
    )
    print(f"\nDone. {len(result)} outcome rows.", file=sys.stderr)
