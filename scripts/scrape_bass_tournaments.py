#!/usr/bin/env python3
"""Scrape B.A.S.S. and MLF tournament results to expand the day-level dataset.

This script combines three collection strategies:

1. **Bassmaster 2000-2013** — Uses the existing WordPress API + TMS data API
   approach from ``all_bassmaster.py``, just with an earlier date range.
2. **MLF Bass Pro Tour + Toyota Series (2019-2025)** — Uses the Wayback Machine
   CDX API to discover archived MLF results pages, then parses standings tables.
3. **FLW 2016-2019** — Uses Wayback Machine for the gap between existing FLW
   data (ends ~2015) and MLF rebrand (2020).

Output:
    castline/validation/data/raw/bass_tournament_results.csv
    castline/validation/data/raw/bass_tournament_geocoding.csv

Usage:
    python3 scripts/scrape_bass_tournaments.py [--phase 1|2|3|all]
"""

from __future__ import annotations

import csv
import json
import math
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup, Tag

# ---------------------------------------------------------------------------
# Resolve project root
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from castline.validation.collectors.outcomes import (  # noqa: E402
    DEFAULT_BASSMASTER_SPECIES,
    DEFAULT_HEADERS,
    KNOWN_LAKE_GAUGES,
    REQUIRED_OUTCOME_COLUMNS,
    _auto_resolve_usgs_site,
    _clean_text,
    _seasonal_baseline_signal,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
RAW_DIR = _PROJECT_ROOT / "castline" / "validation" / "data" / "raw"
OUTPUT_PATH = RAW_DIR / "bass_tournament_results.csv"
GEOCODING_PATH = RAW_DIR / "bass_tournament_geocoding.csv"
CHECKPOINT_DIR = RAW_DIR / "scrape_checkpoints"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BASSMASTER_WP_API = "https://www.bassmaster.com/wp-json/wp/v2/tournament"
BASSMASTER_DATA_API = "https://www.bassmaster.com/wp-json/data/v1"
BASSMASTER_BASE = "https://www.bassmaster.com"

WAYBACK_CDX_URL = "http://web.archive.org/cdx/search/cdx"
WAYBACK_WEB_PREFIX = "http://web.archive.org/web"

WP_PER_PAGE = 100
REQUEST_DELAY = 0.5
WAYBACK_DELAY = 1.5
MIN_WEIGHTS_FOR_MEDIAN = 3

# Trail classification
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

# Weight parsing
_WEIGHT_RE = re.compile(r"(\d{1,3})\s*-\s*(\d{1,2})")

# Output columns
OUTPUT_COLUMNS = [
    "event_id", "source", "tournament_name", "series", "date",
    "location", "species", "total_weight_lb", "num_fish",
    "num_anglers", "day_number", "median_weight_lb",
    "usgs_site_id", "baseline_signal",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
        return oz / 16.0 if oz > 0 else None
    except (ValueError, TypeError):
        return None


def _classify_trail(item: dict[str, Any]) -> str:
    slug = str(item.get("slug", "")).lower()
    title_rendered = str((item.get("title") or {}).get("rendered", "")).lower()
    meta = item.get("meta", {}) or {}
    tour_type = str(meta.get("bassmaster_tournament_type", "")).lower()
    trail_field = str(meta.get("bassmaster_tournament_trail", "")).lower()
    combined = " ".join([slug, title_rendered, tour_type, trail_field])
    for trail_name, keywords in TRAIL_KEYWORDS.items():
        for kw in keywords:
            if kw in combined:
                return trail_name
    return "other"


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
    m = re.search(r'"tms"\s*:\s*(\d+)', html)
    return int(m.group(1)) if m else None


def _extract_num_days_from_html(html: str) -> int | None:
    m = re.search(r'"all_dates"\s*:\s*\[([^\]]+)\]', html)
    if m:
        dates = re.findall(r'"(\d{8})"', m.group(1))
        if dates:
            return len(dates)
    return None


def _save_checkpoint(rows: list[dict], name: str) -> None:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    path = CHECKPOINT_DIR / f"{name}.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    print(f"  checkpoint: {len(rows)} rows -> {path.name}", file=sys.stderr)


def _load_checkpoint(name: str) -> tuple[list[dict], set[str]]:
    path = CHECKPOINT_DIR / f"{name}.csv"
    if path.exists():
        try:
            df = pd.read_csv(path)
            rows = df.to_dict("records")
            slugs = set(df["event_id"].str.rsplit("-day-", n=1).str[0].unique())
            print(f"  resumed checkpoint: {len(rows)} rows, {len(slugs)} events",
                  file=sys.stderr)
            return rows, slugs
        except Exception:
            pass
    return [], set()


# ---------------------------------------------------------------------------
# PHASE 1: Bassmaster pre-2014 via WordPress API
# ---------------------------------------------------------------------------

def phase1_bassmaster_all(session: requests.Session) -> list[dict]:
    """Collect ALL Bassmaster tournaments 2000-2026, deduping against existing data."""
    print("\n=== PHASE 1: Bassmaster ALL (2000-2026) ===", file=sys.stderr)

    rows, processed = _load_checkpoint("phase1_bassmaster_all")
    if rows:
        print(f"  already have {len(rows)} rows from checkpoint", file=sys.stderr)

    # Load existing slugs for dedup
    existing_slugs: set[str] = set()
    for f in [RAW_DIR / "all_bassmaster_outcomes.csv", RAW_DIR / "elite_outcomes.csv",
              RAW_DIR / "historical_outcomes.csv"]:
        if f.exists():
            try:
                df = pd.read_csv(f)
                if "tournament_slug" in df.columns:
                    existing_slugs.update(df["tournament_slug"].astype(str).unique())
            except Exception:
                pass
    print(f"  existing Bassmaster slugs to skip: {len(existing_slugs)}", file=sys.stderr)

    # Discover tournaments
    tournaments = []
    seen_ids: set[int] = set()
    page = 1

    while True:
        try:
            resp = session.get(
                BASSMASTER_WP_API,
                params={
                    "page": page,
                    "per_page": WP_PER_PAGE,
                    "_fields": "id,slug,link,title,content,meta",
                },
                timeout=(5, 15),
                headers=DEFAULT_HEADERS,
            )
            if resp.status_code in {400, 404} and page > 1:
                break
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            if page > 1:
                break
            raise

        if not payload:
            break

        for item in payload:
            wp_id = int(item.get("id", 0))
            if wp_id in seen_ids:
                continue
            seen_ids.add(wp_id)

            year = _tournament_year(item)
            if year is None or not (2000 <= year <= 2026):
                continue

            # Skip tournaments already in existing data
            slug = str(item.get("slug", "")).strip()
            if slug in existing_slugs:
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

            trail = _classify_trail(item)
            water_body = _clean_text(meta.get("bassmaster_tournament_body_of_water", ""))
            city = _clean_text(meta.get("bassmaster_tournament_city", ""))
            state = _clean_text(meta.get("bassmaster_tournament_state", ""))
            slug = str(item.get("slug", "")).strip()
            title = _clean_text((item.get("title") or {}).get("rendered", slug))

            num_days = 2
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

            tournaments.append({
                "wp_id": wp_id,
                "slug": slug,
                "title": title,
                "water_body": water_body,
                "city": city,
                "state": state,
                "start_date": start_date,
                "end_date": end_date,
                "num_days": num_days,
                "trail": trail,
            })

        if len(payload) < WP_PER_PAGE:
            break
        page += 1
        time.sleep(REQUEST_DELAY)

    tournaments.sort(key=lambda t: (t["start_date"], t["slug"]))
    print(f"  discovered {len(tournaments)} NEW tournaments (2000-2026)", file=sys.stderr)

    # Process each tournament
    total = len(tournaments)
    no_tms = 0
    no_results = 0

    for idx, t in enumerate(tournaments, 1):
        if t["slug"] in processed:
            continue

        time.sleep(REQUEST_DELAY)

        # Resolve TMS ID
        url = f"{BASSMASTER_BASE}/tournament/{t['slug']}/results/?day=0"
        tms_id = None
        sys.stderr.write(f"  [{idx}/{total}] TMS: {t['slug'][:50]}...\n")
        sys.stderr.flush()
        try:
            resp = session.get(url, timeout=(5, 15), headers=DEFAULT_HEADERS)
            if resp.status_code != 404:
                resp.raise_for_status()
                tms_id = _extract_tms_id(resp.text)
                if tms_id:
                    nd = _extract_num_days_from_html(resp.text)
                    if nd and 1 <= nd <= 6:
                        t["num_days"] = nd
        except Exception as exc:
            sys.stderr.write(f"    err: {exc}\n")
            sys.stderr.flush()

        if not tms_id:
            no_tms += 1
            continue

        time.sleep(REQUEST_DELAY)

        # Fetch daily results
        results = []
        api_url = f"{BASSMASTER_DATA_API}/tournament/daily-results"
        try:
            resp = session.get(
                api_url,
                params={"tournament_id": tms_id, "day": t["num_days"], "class": "anglers"},
                timeout=(5, 15), headers=DEFAULT_HEADERS,
            )
            if resp.status_code != 404:
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, list):
                    results = data
        except Exception:
            pass

        if not results:
            for try_day in range(t["num_days"] - 1, 0, -1):
                time.sleep(REQUEST_DELAY)
                try:
                    resp = session.get(
                        api_url,
                        params={"tournament_id": tms_id, "day": try_day, "class": "anglers"},
                        timeout=(5, 15), headers=DEFAULT_HEADERS,
                    )
                    if resp.status_code != 404:
                        resp.raise_for_status()
                        data = resp.json()
                        if isinstance(data, list) and data:
                            results = data
                            t["num_days"] = try_day
                            break
                except Exception:
                    pass

        if not results:
            no_results += 1
            continue

        # Extract per-day weights
        day_weights: dict[int, list[float]] = {}
        for day_num in range(1, t["num_days"] + 1):
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

        if not day_weights:
            continue

        # Resolve USGS gauge
        usgs_site_id = _auto_resolve_usgs_site(t["water_body"])
        if not usgs_site_id:
            location = ", ".join(p for p in [t["water_body"], t["city"], t["state"]] if p)
            for part in location.split(","):
                usgs_site_id = _auto_resolve_usgs_site(part.strip())
                if usgs_site_id:
                    break

        location = ", ".join(p for p in [t["water_body"], t["city"], t["state"]] if p)

        for day_num in sorted(day_weights.keys()):
            weights = day_weights[day_num]
            event_date = (t["start_date"] + timedelta(days=day_num - 1)).strftime("%Y-%m-%d")
            median_w = round(statistics.median(weights), 4)
            total_w = round(sum(weights), 2)

            rows.append({
                "event_id": f"bm-{t['slug']}-day-{day_num}",
                "source": "bassmaster",
                "tournament_name": t["title"],
                "series": f"bassmaster_{t['trail']}",
                "date": event_date,
                "location": location,
                "species": DEFAULT_BASSMASTER_SPECIES,
                "total_weight_lb": total_w,
                "num_fish": "",  # not always available
                "num_anglers": len(weights),
                "day_number": day_num,
                "median_weight_lb": median_w,
                "usgs_site_id": usgs_site_id or "",
                "baseline_signal": _seasonal_baseline_signal(event_date),
            })

        processed.add(t["slug"])
        print(
            f"  [{idx}/{total}] {t['trail']}: {t['slug']} -> "
            f"{len(day_weights)} day(s), {len(results)} anglers",
            file=sys.stderr,
        )

        if idx % 20 == 0:
            _save_checkpoint(rows, "phase1_bassmaster_all")

    _save_checkpoint(rows, "phase1_bassmaster_all")
    print(
        f"  Phase 1 done: {len(rows)} rows, no_tms={no_tms}, no_results={no_results}",
        file=sys.stderr,
    )
    return rows


# ---------------------------------------------------------------------------
# PHASE 2: MLF via Wayback Machine
# ---------------------------------------------------------------------------

def _wayback_discover(
    session: requests.Session,
    url_pattern: str,
    *,
    limit: int = 500,
) -> list[dict[str, str]]:
    """Discover archived URLs via Wayback CDX API."""
    params = {
        "url": url_pattern,
        "output": "json",
        "limit": limit,
        "fl": "original,timestamp,statuscode,mimetype",
        "filter": "statuscode:200",
        "collapse": "urlkey",
    }
    try:
        resp = session.get(WAYBACK_CDX_URL, params=params, timeout=(5, 30))
        resp.raise_for_status()
        data = resp.json()
        if not data or len(data) < 2:
            return []
        headers = data[0]
        return [dict(zip(headers, row)) for row in data[1:]]
    except Exception as exc:
        print(f"  wayback CDX error for {url_pattern}: {exc}", file=sys.stderr)
        return []


def _wayback_fetch(session: requests.Session, url: str, timestamp: str) -> str | None:
    """Fetch an archived page from Wayback Machine."""
    wb_url = f"{WAYBACK_WEB_PREFIX}/{timestamp}id_/{url}"
    try:
        resp = session.get(wb_url, timeout=(5, 30), headers=DEFAULT_HEADERS)
        if resp.status_code == 200:
            return resp.text
    except Exception as exc:
        print(f"  wayback fetch error: {exc}", file=sys.stderr)
    return None


def _parse_mlf_results_page(html: str, url: str) -> list[dict]:
    """Parse an MLF results page for tournament and weight data."""
    soup = BeautifulSoup(html, "html.parser")
    rows = []

    # Try to find tournament title
    title = ""
    for tag in ["h1", "h2"]:
        el = soup.find(tag)
        if el:
            title = el.get_text(strip=True)
            break

    # Try to find date
    date_str = ""
    # Look for date patterns in the page
    date_patterns = [
        re.compile(r'(\d{4}-\d{2}-\d{2})'),
        re.compile(r'(\w+ \d{1,2},?\s*\d{4})'),
        re.compile(r'(\d{1,2}/\d{1,2}/\d{4})'),
    ]

    page_text = soup.get_text()
    for pat in date_patterns:
        m = pat.search(page_text[:2000])
        if m:
            try:
                parsed = pd.to_datetime(m.group(1), errors="coerce")
                if not pd.isna(parsed):
                    date_str = parsed.strftime("%Y-%m-%d")
                    break
            except Exception:
                pass

    # Try to extract from URL
    if not date_str:
        url_date_m = re.search(r'/(\d{4})', url)
        if url_date_m:
            date_str = f"{url_date_m.group(1)}-01-01"

    # Try to find location
    location = ""
    for meta_tag in soup.find_all("meta"):
        content = meta_tag.get("content", "")
        if "lake" in content.lower() or "river" in content.lower():
            location = content[:100]
            break

    # Look for standings tables
    tables = soup.find_all("table")
    for table in tables:
        weights = _extract_weights_from_table(table)
        if len(weights) >= MIN_WEIGHTS_FOR_MEDIAN:
            median_w = round(statistics.median(weights), 4)
            total_w = round(sum(weights), 2)
            rows.append({
                "title": title,
                "date": date_str,
                "location": location,
                "weights": weights,
                "median_weight_lb": median_w,
                "total_weight_lb": total_w,
                "num_anglers": len(weights),
            })

    # Also look for structured JSON-LD data
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            ld = json.loads(script.string)
            if isinstance(ld, dict):
                if ld.get("name"):
                    title = title or ld["name"]
                if ld.get("location"):
                    loc = ld["location"]
                    if isinstance(loc, dict):
                        location = location or loc.get("name", "")
        except Exception:
            pass

    return rows


def _extract_weights_from_table(table: Tag) -> list[float]:
    """Extract weight values from a standings/results table."""
    weights: list[float] = []
    header_row = table.find("tr")
    if not header_row:
        return weights

    # Find which column contains weight data
    headers = [th.get_text(strip=True).lower() for th in header_row.find_all(["th", "td"])]
    weight_col = None
    for i, h in enumerate(headers):
        if any(kw in h for kw in ["weight", "total", "lbs", "pounds", "wt"]):
            weight_col = i
            break

    if weight_col is None:
        return weights

    for row in table.find_all("tr")[1:]:
        cells = row.find_all(["td", "th"])
        if weight_col < len(cells):
            text = cells[weight_col].get_text(strip=True)
            # Try lb-oz format
            w = _weight_str_to_lb(text)
            if w is None:
                # Try decimal pounds
                try:
                    w = float(re.sub(r'[^\d.]', '', text))
                    if w <= 0 or w > 200:
                        w = None
                except (ValueError, TypeError):
                    w = None
            if w is not None and w > 0:
                weights.append(w)

    return weights


def phase2_mlf_wayback(session: requests.Session) -> list[dict]:
    """Collect MLF tournament results via Wayback Machine."""
    print("\n=== PHASE 2: MLF via Wayback Machine ===", file=sys.stderr)

    rows, processed = _load_checkpoint("phase2_mlf_wayback")
    if rows:
        print(f"  already have {len(rows)} rows from checkpoint", file=sys.stderr)

    # Discover MLF results pages
    url_patterns = [
        "majorleaguefishing.com/bass-pro-tour/*/results*",
        "majorleaguefishing.com/toyota-series/*/results*",
        "majorleaguefishing.com/cup/*/results*",
        "majorleaguefishing.com/results/*",
        "majorleaguefishing.com/tournaments/*/results*",
    ]

    discovered: list[dict[str, str]] = []
    for pattern in url_patterns:
        time.sleep(WAYBACK_DELAY)
        results = _wayback_discover(session, pattern, limit=200)
        print(f"  CDX: {pattern} -> {len(results)} URLs", file=sys.stderr)
        discovered.extend(results)

    # Deduplicate by URL
    seen_urls: set[str] = set()
    unique: list[dict[str, str]] = []
    for entry in discovered:
        url = entry.get("original", "")
        if url not in seen_urls:
            seen_urls.add(url)
            unique.append(entry)

    print(f"  total unique MLF URLs: {len(unique)}", file=sys.stderr)

    # Fetch and parse each page
    for idx, entry in enumerate(unique, 1):
        url = entry["original"]
        timestamp = entry["timestamp"]

        # Create an ID from the URL
        url_key = re.sub(r'[^a-z0-9]', '-', urlparse(url).path.lower().strip("/"))
        if url_key in processed:
            continue

        time.sleep(WAYBACK_DELAY)

        html = _wayback_fetch(session, url, timestamp)
        if not html:
            continue

        parsed = _parse_mlf_results_page(html, url)
        if not parsed:
            continue

        for p_idx, result in enumerate(parsed):
            event_id = f"mlf-wb-{url_key}"
            if p_idx > 0:
                event_id += f"-{p_idx}"

            # Determine series from URL
            series = "mlf_other"
            if "bass-pro-tour" in url.lower():
                series = "mlf_bass_pro_tour"
            elif "toyota-series" in url.lower():
                series = "mlf_toyota_series"
            elif "cup" in url.lower():
                series = "mlf_cup"

            # Resolve USGS gauge
            location = result.get("location", "")
            usgs_site_id = ""
            if location:
                usgs_site_id = _auto_resolve_usgs_site(location)
                if not usgs_site_id:
                    for part in location.split(","):
                        usgs_site_id = _auto_resolve_usgs_site(part.strip())
                        if usgs_site_id:
                            break

            date_str = result.get("date", "")
            rows.append({
                "event_id": event_id,
                "source": "mlf_wayback",
                "tournament_name": result.get("title", ""),
                "series": series,
                "date": date_str,
                "location": location,
                "species": DEFAULT_BASSMASTER_SPECIES,
                "total_weight_lb": result.get("total_weight_lb", ""),
                "num_fish": "",
                "num_anglers": result.get("num_anglers", ""),
                "day_number": 1,
                "median_weight_lb": result.get("median_weight_lb", ""),
                "usgs_site_id": usgs_site_id or "",
                "baseline_signal": _seasonal_baseline_signal(date_str) if date_str else "",
            })

        processed.add(url_key)
        print(
            f"  [{idx}/{len(unique)}] {url[:80]} -> {len(parsed)} result(s)",
            file=sys.stderr,
        )

        if idx % 20 == 0:
            _save_checkpoint(rows, "phase2_mlf_wayback")

    _save_checkpoint(rows, "phase2_mlf_wayback")
    print(f"  Phase 2 done: {len(rows)} rows from MLF Wayback", file=sys.stderr)
    return rows


# ---------------------------------------------------------------------------
# PHASE 3: FLW 2016-2019 gap via Wayback Machine
# ---------------------------------------------------------------------------

def _parse_flw_results_page(html: str, url: str) -> list[dict]:
    """Parse an FLW results page for tournament data."""
    soup = BeautifulSoup(html, "html.parser")
    results = []

    # Find title
    title = ""
    for tag in ["h1", "h2", "title"]:
        el = soup.find(tag)
        if el:
            t = el.get_text(strip=True)
            if t and len(t) > 5:
                title = t
                break

    # Find date
    date_str = ""
    page_text = soup.get_text()[:3000]
    date_patterns = [
        re.compile(r'(\d{4}-\d{2}-\d{2})'),
        re.compile(r'(\w+\s+\d{1,2},?\s+\d{4})'),
    ]
    for pat in date_patterns:
        m = pat.search(page_text)
        if m:
            try:
                parsed = pd.to_datetime(m.group(1), errors="coerce")
                if not pd.isna(parsed) and 2015 <= parsed.year <= 2020:
                    date_str = parsed.strftime("%Y-%m-%d")
                    break
            except Exception:
                pass

    # Find location from text
    location = ""
    lake_pattern = re.compile(
        r'(?:Lake|Reservoir|River)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*',
    )
    m = lake_pattern.search(page_text)
    if m:
        location = m.group(0)

    # Parse standings tables
    tables = soup.find_all("table")
    for table in tables:
        weights = _extract_weights_from_table(table)
        if len(weights) >= MIN_WEIGHTS_FOR_MEDIAN:
            median_w = round(statistics.median(weights), 4)
            total_w = round(sum(weights), 2)
            results.append({
                "title": title,
                "date": date_str,
                "location": location,
                "weights": weights,
                "median_weight_lb": median_w,
                "total_weight_lb": total_w,
                "num_anglers": len(weights),
            })

    return results


def phase3_flw_extension(session: requests.Session) -> list[dict]:
    """Collect FLW results from 2016-2019 via Wayback Machine."""
    print("\n=== PHASE 3: FLW 2016-2019 via Wayback ===", file=sys.stderr)

    rows, processed = _load_checkpoint("phase3_flw_extension")
    if rows:
        print(f"  already have {len(rows)} rows from checkpoint", file=sys.stderr)

    # Discover FLW results pages
    url_patterns = [
        "flwfishing.com/results*",
        "flwfishing.com/tournaments/*/results*",
        "flwfishing.com/tournament/*/results*",
    ]

    discovered: list[dict[str, str]] = []
    for pattern in url_patterns:
        time.sleep(WAYBACK_DELAY)
        results = _wayback_discover(
            session, pattern, limit=500,
        )
        print(f"  CDX: {pattern} -> {len(results)} URLs", file=sys.stderr)
        discovered.extend(results)

    # Deduplicate
    seen_urls: set[str] = set()
    unique: list[dict[str, str]] = []
    for entry in discovered:
        url = entry.get("original", "")
        ts = entry.get("timestamp", "")
        # Filter to 2016-2019 timestamps
        if ts[:4] in ("2016", "2017", "2018", "2019"):
            if url not in seen_urls:
                seen_urls.add(url)
                unique.append(entry)

    print(f"  total unique FLW 2016-2019 URLs: {len(unique)}", file=sys.stderr)

    for idx, entry in enumerate(unique, 1):
        url = entry["original"]
        timestamp = entry["timestamp"]

        url_key = re.sub(r'[^a-z0-9]', '-', urlparse(url).path.lower().strip("/"))
        if url_key in processed:
            continue

        time.sleep(WAYBACK_DELAY)

        html = _wayback_fetch(session, url, timestamp)
        if not html:
            continue

        parsed = _parse_flw_results_page(html, url)
        if not parsed:
            continue

        for p_idx, result in enumerate(parsed):
            event_id = f"flw-ext-{url_key}"
            if p_idx > 0:
                event_id += f"-{p_idx}"

            location = result.get("location", "")
            usgs_site_id = ""
            if location:
                usgs_site_id = _auto_resolve_usgs_site(location)
                if not usgs_site_id:
                    for part in location.split(","):
                        usgs_site_id = _auto_resolve_usgs_site(part.strip())
                        if usgs_site_id:
                            break

            date_str = result.get("date", "")
            rows.append({
                "event_id": event_id,
                "source": "flw_wayback",
                "tournament_name": result.get("title", ""),
                "series": "flw",
                "date": date_str,
                "location": location,
                "species": DEFAULT_BASSMASTER_SPECIES,
                "total_weight_lb": result.get("total_weight_lb", ""),
                "num_fish": "",
                "num_anglers": result.get("num_anglers", ""),
                "day_number": 1,
                "median_weight_lb": result.get("median_weight_lb", ""),
                "usgs_site_id": usgs_site_id or "",
                "baseline_signal": _seasonal_baseline_signal(date_str) if date_str else "",
            })

        processed.add(url_key)
        print(
            f"  [{idx}/{len(unique)}] {url[:80]} -> {len(parsed)} result(s)",
            file=sys.stderr,
        )

        if idx % 20 == 0:
            _save_checkpoint(rows, "phase3_flw_extension")

    _save_checkpoint(rows, "phase3_flw_extension")
    print(f"  Phase 3 done: {len(rows)} rows from FLW 2016-2019", file=sys.stderr)
    return rows


# ---------------------------------------------------------------------------
# Deduplication and combination
# ---------------------------------------------------------------------------

def _load_existing_events() -> set[str]:
    """Load existing event IDs from all outcome files to avoid duplicates."""
    existing = set()
    files = [
        RAW_DIR / "all_bassmaster_outcomes.csv",
        RAW_DIR / "elite_outcomes.csv",
        RAW_DIR / "historical_outcomes.csv",
        RAW_DIR / "flw_outcomes.csv",
        RAW_DIR / "mlf_outcomes.csv",
    ]
    for f in files:
        if f.exists():
            try:
                df = pd.read_csv(f, usecols=["event_id"])
                existing.update(df["event_id"].astype(str).tolist())
            except Exception:
                pass

    # Also build slug-date keys for fuzzy dedup
    for f in files:
        if f.exists():
            try:
                df = pd.read_csv(f)
                if "date" in df.columns and "location" in df.columns:
                    for _, row in df.iterrows():
                        key = f"{row.get('date', '')}|{str(row.get('location', '')).lower()[:30]}"
                        existing.add(key)
            except Exception:
                pass

    print(f"  loaded {len(existing)} existing event keys for dedup", file=sys.stderr)
    return existing


def _build_geocoding_file(df: pd.DataFrame) -> None:
    """Extract unique locations and save a geocoding mapping file."""
    if df.empty:
        return

    locations = df[["location"]].drop_duplicates()
    locations = locations[locations["location"].str.strip().ne("")]

    # Try to extract coordinates from known lake gauges
    geo_rows = []
    for _, row in locations.iterrows():
        loc = row["location"]
        usgs_id = ""
        for part in loc.split(","):
            usgs_id = _auto_resolve_usgs_site(part.strip())
            if usgs_id:
                break
        geo_rows.append({
            "location": loc,
            "usgs_site_id": usgs_id,
            "latitude": "",
            "longitude": "",
            "needs_geocoding": "yes" if not usgs_id else "no",
        })

    geo_df = pd.DataFrame(geo_rows)
    geo_df.to_csv(GEOCODING_PATH, index=False)
    print(f"  geocoding file: {len(geo_df)} locations -> {GEOCODING_PATH.name}", file=sys.stderr)


def combine_and_save(
    phase1_rows: list[dict],
    phase2_rows: list[dict],
    phase3_rows: list[dict],
) -> pd.DataFrame:
    """Combine all phases, deduplicate, and save."""
    print("\n=== COMBINING AND DEDUPLICATING ===", file=sys.stderr)

    existing_keys = _load_existing_events()

    all_rows = phase1_rows + phase2_rows + phase3_rows
    print(f"  total raw rows: {len(all_rows)}", file=sys.stderr)

    # Filter out duplicates
    deduped = []
    for row in all_rows:
        event_id = row.get("event_id", "")
        date = row.get("date", "")
        location = str(row.get("location", "")).lower()[:30]
        date_loc_key = f"{date}|{location}"

        if event_id in existing_keys:
            continue
        if date_loc_key in existing_keys:
            continue

        # Also skip rows with missing critical data
        if not date or not row.get("median_weight_lb"):
            continue

        deduped.append(row)
        existing_keys.add(event_id)
        existing_keys.add(date_loc_key)

    df = pd.DataFrame(deduped, columns=OUTPUT_COLUMNS)
    df.to_csv(OUTPUT_PATH, index=False)

    print(f"\n  FINAL OUTPUT: {len(df)} new event-day rows", file=sys.stderr)
    if not df.empty:
        by_source = df.groupby("source").size().to_dict()
        by_series = df.groupby("series").size().to_dict()
        date_range = f"{df['date'].min()} to {df['date'].max()}"
        mapped = df["usgs_site_id"].astype(str).str.strip().ne("").sum()
        print(f"  By source: {by_source}", file=sys.stderr)
        print(f"  By series: {by_series}", file=sys.stderr)
        print(f"  Date range: {date_range}", file=sys.stderr)
        print(f"  USGS mapped: {mapped}/{len(df)}", file=sys.stderr)

    _build_geocoding_file(df)
    print(f"\n  Saved to: {OUTPUT_PATH}", file=sys.stderr)

    return df


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Scrape B.A.S.S. and MLF tournament results"
    )
    parser.add_argument(
        "--phase",
        default="all",
        choices=["1", "2", "3", "all"],
        help="Which phase to run (1=Bassmaster pre-2014, 2=MLF Wayback, 3=FLW ext, all=everything)",
    )
    args = parser.parse_args()

    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    # Retry on failures and use aggressive timeouts
    retry_strategy = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    try:
        phase1_rows: list[dict] = []
        phase2_rows: list[dict] = []
        phase3_rows: list[dict] = []

        if args.phase in ("1", "all"):
            phase1_rows = phase1_bassmaster_all(session)

        if args.phase in ("2", "all"):
            phase2_rows = phase2_mlf_wayback(session)

        if args.phase in ("3", "all"):
            phase3_rows = phase3_flw_extension(session)

        df = combine_and_save(phase1_rows, phase2_rows, phase3_rows)

        # Print summary
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"SCRAPING COMPLETE", file=sys.stderr)
        print(f"  New rows: {len(df)}", file=sys.stderr)

        # Show existing totals for context
        existing_counts = {}
        for name in ["all_bassmaster_outcomes", "elite_outcomes", "flw_outcomes",
                      "mlf_outcomes", "tourneyx_outcomes"]:
            path = RAW_DIR / f"{name}.csv"
            if path.exists():
                try:
                    existing_counts[name] = len(pd.read_csv(path)) - 1
                except Exception:
                    pass
        if existing_counts:
            total_existing = sum(existing_counts.values())
            print(f"  Existing rows: {total_existing} ({existing_counts})", file=sys.stderr)
            print(f"  Grand total: {total_existing + len(df)}", file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)

    finally:
        session.close()


if __name__ == "__main__":
    main()
