#!/usr/bin/env python3
"""Scrape historical FLW (Fishing League Worldwide) tournament results
from the Wayback Machine.

FLW was rebranded to MLF in 2020.  Pre-2020 results lived at
``flwfishing.com`` (primary, 94+ archived tournament URLs) and
``flwoutdoors.com`` (fallback).  This script discovers archived FLW
results pages via the Wayback Machine CDX API, parses angler standings
tables, and outputs per-event-day rows in the same schema used by the
MLF and Bassmaster collectors.

Usage::

    python3 scripts/fetch_flw_historical.py

Output is written to:
    castline/validation/data/raw/flw_outcomes.csv
"""

from __future__ import annotations

import json
import math
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup, Tag

# ---------------------------------------------------------------------------
# Resolve project root so we can import from castline even when running
# the script directly.
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
# Constants
# ---------------------------------------------------------------------------

FLW_DOMAIN = "flwfishing.com"

WAYBACK_CDX_URL = "http://web.archive.org/cdx/search/cdx"
WAYBACK_WEB_PREFIX = "http://web.archive.org/web"

# Polite delay between Wayback requests (seconds).
WAYBACK_DELAY = 2.0

# Minimum angler weights needed for a meaningful median.
MIN_WEIGHTS_FOR_MEDIAN = 5

# Checkpoint interval (save every N events).
CHECKPOINT_INTERVAL = 20

# Retry settings.
MAX_RETRIES = 3

# Year range for FLW Tour / FLW Series / EverStart / MLF.
START_YEAR = 1996
END_YEAR = 2026

OUTPUT_PATH = _PROJECT_ROOT / "castline" / "validation" / "data" / "raw" / "flw_outcomes.csv"

# URL patterns to query the CDX API.  FLW used several URL structures
# across 2007-2019.
_FLW_CDX_QUERIES = [
    # Primary: flwfishing.com/tournaments/* has 3300+ archived tournament pages
    f"{FLW_DOMAIN}/tournaments/*",
    # Results / standings sub-pages
    f"{FLW_DOMAIN}/tournaments/*/results*",
    f"{FLW_DOMAIN}/tournaments/*/standings*",
    f"{FLW_DOMAIN}/tournaments/*/leaderboard*",
    # Alternate paths observed in Wayback archives
    f"{FLW_DOMAIN}/tournament/*",
    f"{FLW_DOMAIN}/events/*",
    f"{FLW_DOMAIN}/bassfishing/*results*",
    f"{FLW_DOMAIN}/bassfishing/*standings*",
    # flwoutdoors.com — old domain with tournament.cfm results pages (6700+ URLs,
    # 212 unique tournament IDs with t=results&coAngler=0 standings tables)
    "flwoutdoors.com/tournament/*",
    "flwoutdoors.com/tournament.cfm*",
    "flwoutdoors.com/bassfishing/*results*",
    "flwoutdoors.com/bassfishing/*standings*",
    # majorleaguefishing.com — FLW was rebranded to MLF; the /events/ path has
    # date-slug URLs similar to flwfishing.com (938+ unique event slugs).
    # NOTE: MLF results pages are Vue.js SPA but event landing pages sometimes
    # contain summary data in HTML.
    "majorleaguefishing.com/events/*",
]

# CDX query limit — increased from 1000 to 10000 to capture the full
# flwfishing.com/tournaments/* archive (3330 unique URLs) and the
# flwoutdoors.com/tournament.cfm pool (6700 URLs).
_CDX_QUERY_LIMIT = 10000

# Regex patterns for weight extraction (lbs-oz, decimal, etc.).
_WEIGHT_LB_OZ_RE = re.compile(
    r"(\d{1,3})\s*(?:lbs?|pounds?)?\s*[-\s,]*\s*(\d{1,2})\s*(?:oz|ounces?)?",
    re.IGNORECASE,
)
_WEIGHT_DECIMAL_RE = re.compile(
    r"(\d{1,3}\.\d{1,4})\s*(?:lbs?|pounds?)?", re.IGNORECASE
)
_WEIGHT_DASH_RE = re.compile(r"(\d{1,3})\s*-\s*(\d{1,2})")

# FLW trail classification patterns (from URL or title).
_TRAIL_PATTERNS = [
    (re.compile(r"flw[- ]?tour\b", re.IGNORECASE), "flw-tour"),
    (re.compile(r"flw[- ]?series\b", re.IGNORECASE), "flw-series"),
    (re.compile(r"everstart\b", re.IGNORECASE), "everstart"),
    (re.compile(r"stren[- ]?series\b", re.IGNORECASE), "stren-series"),
    (re.compile(r"walmart\b.*flw\b", re.IGNORECASE), "flw-tour"),
    (re.compile(r"bass[- ]?pro[- ]?tour\b", re.IGNORECASE), "mlf-bpt"),
    (re.compile(r"pro[- ]?circuit\b", re.IGNORECASE), "mlf-pro-circuit"),
    (re.compile(r"majorleaguefishing\.com", re.IGNORECASE), "mlf"),
    (re.compile(r"flw\b", re.IGNORECASE), "flw-other"),
]

# Water-body extraction patterns for FLW page titles / headings.
_WATER_BODY_TITLE_RE = re.compile(
    r"(?:on|at|@)\s+((?:lake|reservoir|river|bay|delta|chain)\s+[\w\s'.]+)",
    re.IGNORECASE,
)
_WATER_BODY_STANDALONE_RE = re.compile(
    r"\b((?:lake|reservoir|river)\s+[\w'.]+(?:\s+[\w'.]+)?)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class FLWTournament:
    """Metadata for a single FLW tournament discovered via the Wayback CDX."""

    slug: str
    title: str
    water_body: str
    date: str  # YYYY-MM-DD (approximate from archive timestamp)
    trail: str = "flw-tour"
    species: str = "black_bass"
    archived_urls: list[str] = field(default_factory=list)

    @property
    def location(self) -> str:
        return self.water_body

    @property
    def event_id_prefix(self) -> str:
        return f"flw-{self.slug}"


# ---------------------------------------------------------------------------
# Weight parsing  (mirrors mlf.py logic)
# ---------------------------------------------------------------------------


def _weight_str_to_lb(text: str) -> float | None:
    """Convert a weight string to decimal pounds.

    Handles: "15-08", "15 lbs 8 oz", "15.50", etc.
    Returns None on failure.
    """
    text = str(text).strip()
    if not text or text.upper() in ("-", "—", "DNW", "DQ", "DNS", "0", "0-00"):
        return None

    # lbs-oz dash format.
    m = _WEIGHT_DASH_RE.search(text)
    if m:
        lbs, ozs = int(m.group(1)), int(m.group(2))
        if ozs <= 15 and lbs < 200:
            return lbs + ozs / 16.0

    # "X lbs Y oz" format.
    m = _WEIGHT_LB_OZ_RE.search(text)
    if m:
        lbs, ozs = int(m.group(1)), int(m.group(2))
        if ozs <= 15 and lbs < 200:
            return lbs + ozs / 16.0

    # Decimal pounds.
    m = _WEIGHT_DECIMAL_RE.search(text)
    if m:
        val = float(m.group(1))
        if 0 < val < 200:
            return val

    # Plain integer (total lbs, rare).
    try:
        val = float(text)
        if 0 < val < 200:
            return val
    except ValueError:
        pass

    return None


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _fetch_with_retry(
    session: requests.Session,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 45,
    max_retries: int = MAX_RETRIES,
    delay: float = 2.0,
    params: dict[str, Any] | None = None,
) -> requests.Response | None:
    """Fetch a URL with retries and exponential backoff."""
    for attempt in range(max_retries + 1):
        try:
            resp = session.get(
                url,
                headers=headers or DEFAULT_HEADERS,
                timeout=timeout,
                params=params,
            )
            if resp.status_code == 429:
                wait = delay * (2 ** attempt)
                print(
                    f"flw: rate-limited on {url}, waiting {wait:.0f}s",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue
            if resp.status_code == 503:
                wait = delay * (2 ** attempt)
                print(
                    f"flw: 503 on {url}, retrying in {wait:.0f}s",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue
            return resp
        except requests.RequestException as exc:
            if attempt < max_retries:
                wait = delay * (2 ** attempt)
                print(
                    f"flw: request error (attempt {attempt + 1}/{max_retries + 1}) "
                    f"for {url}: {exc}",
                    file=sys.stderr,
                )
                time.sleep(wait)
            else:
                print(f"flw: request failed for {url}: {exc}", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# Wayback Machine CDX discovery
# ---------------------------------------------------------------------------


def _query_wayback_cdx(
    session: requests.Session,
    url_pattern: str,
    *,
    limit: int = 1000,
    from_year: int | None = None,
    to_year: int | None = None,
) -> list[dict[str, str]]:
    """Query the Wayback Machine CDX API for archived URLs.

    Returns a list of dicts with keys: timestamp, original, statuscode, mimetype.
    """
    params: dict[str, Any] = {
        "url": url_pattern,
        "output": "json",
        "limit": limit,
        "fl": "timestamp,original,statuscode,mimetype",
        "filter": "statuscode:200",
        "collapse": "urlkey",
    }
    if from_year:
        params["from"] = str(from_year)
    if to_year:
        params["to"] = str(to_year)

    resp = _fetch_with_retry(
        session,
        WAYBACK_CDX_URL,
        params=params,
        timeout=60,
    )
    if resp is None or resp.status_code != 200:
        return []

    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError):
        print("flw: CDX API returned non-JSON response", file=sys.stderr)
        return []

    if not data or len(data) < 2:
        return []

    header = data[0]
    return [dict(zip(header, row)) for row in data[1:]]


def _fetch_wayback_page(
    session: requests.Session,
    timestamp: str,
    original_url: str,
) -> str | None:
    """Fetch a page from the Wayback Machine archive."""
    # Try with id_ modifier first (raw page without Wayback toolbar).
    wayback_url = f"{WAYBACK_WEB_PREFIX}/{timestamp}id_/{original_url}"
    time.sleep(WAYBACK_DELAY)

    resp = _fetch_with_retry(session, wayback_url, timeout=45)
    if resp is not None and resp.status_code == 200:
        return resp.text

    # Fallback: without id_ modifier.
    wayback_url_raw = f"{WAYBACK_WEB_PREFIX}/{timestamp}/{original_url}"
    resp = _fetch_with_retry(session, wayback_url_raw, timeout=45)
    if resp is not None and resp.status_code == 200:
        return resp.text

    return None


# ---------------------------------------------------------------------------
# URL filtering — keep only pages likely to contain results tables
# ---------------------------------------------------------------------------

# Patterns in the URL path that indicate a results/standings page.
_RESULTS_URL_INDICATORS = re.compile(
    r"(results|standings|leaderboard|final|weigh|day-\d|round-\d)",
    re.IGNORECASE,
)

# Patterns to *exclude* (navigation / media / assets / duplicate pages).
_URL_EXCLUDES = re.compile(
    r"\.(jpg|jpeg|png|gif|css|js|xml|rss|ico|pdf|mp4|mp3)(\?|$)"
    r"|/feed/"
    r"|/page/\d+"
    r"|/comment"
    r"|/author/"
    r"|/tag/"
    r"|/category/"
    r"|/wp-content/"
    r"|/wp-admin/"
    r"|/press-release"
    r"|/photo"
    r"|/gallery"
    r"|/video"
    # MLF sub-pages that won't have results data
    r"|/pairings/?$"
    r"|/details/?$"
    r"|/anglers/?$"
    r"|/news/?$",
    re.IGNORECASE,
)

# For flwoutdoors.com/tournament.cfm URLs, prefer the page that shows all
# results (``all=1``) or the first page (``sr=1``).  Skip paginated pages
# (sr=51, sr=101, ...) to avoid duplicates.
def _is_first_page_cfm(url: str) -> bool:
    """Return True if this tournament.cfm URL is the first/all page."""
    if "tournament.cfm" not in url:
        return True
    # If it shows all results, keep it
    if "all=1" in url:
        return True
    # If sr= param is absent or sr=1, keep it
    sr_m = re.search(r"sr=(\d+)", url)
    if sr_m is None:
        return True
    return int(sr_m.group(1)) <= 1


def _is_results_url(url: str) -> bool:
    """Heuristic: does this URL look like it contains tournament results?"""
    if _URL_EXCLUDES.search(url):
        return False
    parsed = urlparse(url)
    path = parsed.path
    # Must have enough path depth to be a specific page.
    segments = [s for s in path.strip("/").split("/") if s]
    if len(segments) < 2:
        # Exception: tournament.cfm with query params is a single-segment path
        if "tournament.cfm" not in path:
            return False

    # flwfishing.com/tournaments/YYYY-MM-DD-lake-name pattern
    if re.search(r"/tournaments/\d{4}-\d{2}-\d{2}", path):
        return True

    # majorleaguefishing.com/events/YYYY-MM-DD-lake-name pattern
    if re.search(r"/events/\d{4}-\d{2}-\d{2}", path):
        return True

    # flwoutdoors.com/tournament.cfm?...t=results... — standings tables
    if "tournament.cfm" in path and "t=results" in url:
        return True

    return bool(_RESULTS_URL_INDICATORS.search(url))


# ---------------------------------------------------------------------------
# Tournament discovery
# ---------------------------------------------------------------------------


def _classify_trail(text: str) -> str:
    """Classify an FLW trail from URL or title text."""
    for pattern, trail in _TRAIL_PATTERNS:
        if pattern.search(text):
            return trail
    return "flw-tour"


def _extract_slug_from_url(url: str) -> str:
    """Build a short slug from the URL path."""
    parsed = urlparse(url)
    path = parsed.path.strip("/")

    # For flwfishing.com/tournaments/YYYY-MM-DD-lake-name, use the last segment
    m = re.search(r"/tournaments/(\d{4}-\d{2}-\d{2}-.+?)(?:/|$)", path)
    if m:
        slug = m.group(1)
        slug = re.sub(r"[^a-z0-9-]", "-", slug.lower())
        slug = re.sub(r"-+", "-", slug).strip("-")
        return slug[:80] or "unknown"

    # majorleaguefishing.com/events/YYYY-MM-DD-lake-name — same pattern
    m = re.search(r"/events/(\d{4}-\d{2}-\d{2}-.+?)(?:/|$)", path)
    if m:
        slug = "mlf-" + m.group(1)
        slug = re.sub(r"[^a-z0-9-]", "-", slug.lower())
        slug = re.sub(r"-+", "-", slug).strip("-")
        return slug[:80] or "unknown"

    # flwoutdoors.com/tournament.cfm?cid=X&tid=Y — use cid+tid as slug
    if "tournament.cfm" in path:
        query = parsed.query
        cid_m = re.search(r"cid=(\d+)", query)
        tid_m = re.search(r"tid=(\d+)", query)
        if tid_m:
            cid = cid_m.group(1) if cid_m else "0"
            tid = tid_m.group(1)
            return f"flwoutdoors-c{cid}-t{tid}"

    segments = [
        s
        for s in path.split("/")
        if s
        and not re.match(
            r"^(bassfishing|results|standings|tournaments|events|page|\d+)$",
            s,
            re.IGNORECASE,
        )
    ]
    slug = "-".join(segments[-3:]) if segments else path.replace("/", "-")
    # Clean up the slug.
    slug = re.sub(r"[^a-z0-9-]", "-", slug.lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:80] or "unknown"


def _extract_year_from_timestamp(ts: str) -> int:
    """Extract a 4-digit year from a Wayback CDX timestamp."""
    return int(ts[:4]) if len(ts) >= 4 else 2015


def discover_flw_tournaments(
    session: requests.Session,
    start_year: int = START_YEAR,
    end_year: int = END_YEAR,
) -> list[FLWTournament]:
    """Discover FLW tournaments via the Wayback Machine CDX API.

    Returns tournaments sorted by date ascending, deduplicated by slug.
    """
    all_cdx: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    for pattern in _FLW_CDX_QUERIES:
        print(f"flw: querying Wayback CDX for {pattern}...", file=sys.stderr)
        results = _query_wayback_cdx(
            session, pattern, limit=_CDX_QUERY_LIMIT, from_year=start_year, to_year=end_year
        )
        new_count = 0
        for r in results:
            original = r.get("original", "")
            if original and original not in seen_urls:
                seen_urls.add(original)
                all_cdx.append(r)
                new_count += 1
        print(
            f"flw:   -> {len(results)} returned, {new_count} new unique",
            file=sys.stderr,
        )
        time.sleep(WAYBACK_DELAY)

    print(f"flw: {len(all_cdx)} unique archived URLs found", file=sys.stderr)

    # Filter to results-like pages.
    results_cdx = [
        r
        for r in all_cdx
        if _is_results_url(r.get("original", ""))
        and ("html" in r.get("mimetype", "") or "text" in r.get("mimetype", ""))
        and _is_first_page_cfm(r.get("original", ""))
    ]
    print(
        f"flw: {len(results_cdx)} URLs pass results-page filter",
        file=sys.stderr,
    )

    # Group by slug to deduplicate.
    tournament_map: dict[str, FLWTournament] = {}
    tournaments: list[FLWTournament] = []

    for cdx_entry in results_cdx:
        original_url = cdx_entry.get("original", "")
        timestamp = cdx_entry.get("timestamp", "")

        slug = _extract_slug_from_url(original_url)
        trail = _classify_trail(original_url)

        # Normalize slug for grouping (remove long digit sequences like
        # timestamps, but keep short ones like tournament IDs and dates).
        if slug.startswith("flwoutdoors-"):
            # flwoutdoors-cX-tY slugs are already unique per tournament
            group_key = slug
        else:
            group_key = re.sub(r"\d{8,}", "", slug)
            group_key = re.sub(r"-+", "-", group_key).strip("-")
            if not group_key:
                group_key = slug

        if group_key in tournament_map:
            tournament_map[group_key].archived_urls.append(
                f"{timestamp}|{original_url}"
            )
        else:
            # Try to extract date from slug (flwfishing.com: YYYY-MM-DD-lake,
            # or mlf-YYYY-MM-DD-lake for majorleaguefishing.com)
            slug_date_match = re.search(r"(\d{4})-(\d{2})-(\d{2})", slug)
            if slug_date_match:
                year, month, day = slug_date_match.groups()
                approx_date = f"{year}-{month}-{day}"
                # Title is the part after the date — also use as water_body hint
                title_part = re.sub(r"^(?:mlf-)?\d{4}-\d{2}-\d{2}-?", "", slug)
                title = title_part.replace("-", " ").title() if title_part else slug.replace("-", " ").title()
                # Pre-populate water_body from slug (lake-okeechobee → Lake Okeechobee)
                water_body_hint = title_part.replace("-", " ").title() if title_part else ""
            else:
                year = timestamp[:4] if len(timestamp) >= 4 else "2015"
                month = timestamp[4:6] if len(timestamp) >= 6 else "06"
                day = timestamp[6:8] if len(timestamp) >= 8 else "15"
                approx_date = f"{year}-{month}-{day}"
                title = slug.replace("-", " ").title()

            t = FLWTournament(
                slug=slug,
                title=title,
                water_body=water_body_hint if slug_date_match else "",
                date=approx_date,
                trail=trail,
                archived_urls=[f"{timestamp}|{original_url}"],
            )
            tournament_map[group_key] = t
            tournaments.append(t)

    # Filter to year range.
    filtered = []
    for t in tournaments:
        try:
            year = int(t.date[:4])
            if start_year <= year <= end_year:
                filtered.append(t)
        except (ValueError, IndexError):
            filtered.append(t)

    filtered.sort(key=lambda t: t.date)
    print(
        f"flw: {len(filtered)} tournament groups discovered "
        f"({start_year}-{end_year})",
        file=sys.stderr,
    )
    return filtered


# ---------------------------------------------------------------------------
# HTML parsing for FLW standings
# ---------------------------------------------------------------------------


def _parse_standings_table(table: Tag) -> list[dict[str, Any]]:
    """Extract angler rows from an HTML <table>."""
    rows: list[dict[str, Any]] = []
    headers: list[str] = []

    # Extract headers.
    thead = table.find("thead")
    if thead:
        header_cells = thead.find_all(["th", "td"])
        headers = [_clean_text(cell.get_text()) for cell in header_cells]
    else:
        first_tr = table.find("tr")
        if first_tr:
            ths = first_tr.find_all("th")
            if ths:
                headers = [_clean_text(th.get_text()) for th in ths]

    tbody = table.find("tbody") or table
    for tr in tbody.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue
        if all(cell.name == "th" for cell in cells):
            if not headers:
                headers = [_clean_text(c.get_text()) for c in cells]
            continue

        values = [_clean_text(cell.get_text()) for cell in cells]
        if headers and len(values) >= len(headers):
            row = dict(zip(headers, values))
        elif headers and len(values) < len(headers):
            row = dict(zip(headers[: len(values)], values))
        else:
            row = {f"col_{i}": v for i, v in enumerate(values)}
        rows.append(row)

    return rows


def _find_weight_columns(headers: list[str]) -> dict[str, str]:
    """Map logical day/weight columns to actual header names."""
    mapping: dict[str, str] = {}
    for h in headers:
        hl = h.lower().strip()
        if re.match(r"day\s*1", hl):
            mapping["day1"] = h
        elif re.match(r"day\s*2", hl):
            mapping["day2"] = h
        elif re.match(r"day\s*3", hl):
            mapping["day3"] = h
        elif re.match(r"day\s*4", hl):
            mapping["day4"] = h
        elif hl in (
            "total", "total weight", "total wt", "total wt.",
            "total catch", "total lbs",
        ):
            mapping["total"] = h
        elif hl in (
            "weight", "wt", "wt.", "today", "today wt", "catch",
            "final weight", "final wt", "lbs", "pounds",
        ):
            mapping.setdefault("weight", h)
        elif "weight" in hl or "wt" in hl:
            mapping.setdefault("weight", h)
        elif "catch" in hl and "total" not in hl:
            mapping.setdefault("weight", h)
    return mapping


def _extract_day_weights_from_rows(
    rows: list[dict[str, Any]],
) -> dict[int, list[float]]:
    """Extract per-day weight lists from parsed table rows.

    Returns ``{day_number: [weight_lb, ...]}`` (1-based).
    """
    if not rows:
        return {}

    headers = list(rows[0].keys())
    col_map = _find_weight_columns(headers)
    day_weights: dict[int, list[float]] = {}

    # Strategy 1: explicit Day N columns.
    for day_num in range(1, 5):
        key = f"day{day_num}"
        if key in col_map:
            col_name = col_map[key]
            weights = []
            for row in rows:
                w = _weight_str_to_lb(row.get(col_name, ""))
                if w is not None and w > 0:
                    weights.append(w)
            if weights:
                day_weights[day_num] = weights

    # Strategy 2: single weight/total column as day 1.
    if not day_weights:
        for fallback_key in ("weight", "total"):
            if fallback_key in col_map:
                col_name = col_map[fallback_key]
                weights = []
                for row in rows:
                    w = _weight_str_to_lb(row.get(col_name, ""))
                    if w is not None and w > 0:
                        weights.append(w)
                if weights:
                    day_weights[1] = weights
                    break

    # Strategy 3: brute-force every column for weight-like values.
    if not day_weights:
        for col_name in headers:
            weights = []
            for row in rows:
                w = _weight_str_to_lb(row.get(col_name, ""))
                if w is not None and w > 0:
                    weights.append(w)
            if len(weights) >= MIN_WEIGHTS_FOR_MEDIAN:
                day_weights[1] = weights
                break

    return day_weights


def _extract_metadata_from_html(html: str) -> dict[str, str]:
    """Extract tournament metadata (water body, date, title) from HTML."""
    soup = BeautifulSoup(html, "html.parser")
    meta: dict[str, str] = {}

    # Remove Wayback Machine toolbar.
    for wb_div in soup.select("#wm-ipp-base, #wm-ipp, #wm-ipp-print"):
        wb_div.decompose()

    # Title from <title> tag.
    title_tag = soup.find("title")
    if title_tag:
        meta["title"] = _clean_text(title_tag.get_text())

    # Title from <h1> or <h2>.
    for tag_name in ("h1", "h2"):
        tag = soup.find(tag_name)
        if tag:
            text = _clean_text(tag.get_text())
            if text and len(text) > 5:
                meta.setdefault("title", text)
                break

    full_title = meta.get("title", "")

    # Water body from title using "on/at Lake Foo" pattern.
    m = _WATER_BODY_TITLE_RE.search(full_title)
    if m:
        meta["water_body"] = m.group(1).strip()
    else:
        # Standalone lake/reservoir/river mention.
        m = _WATER_BODY_STANDALONE_RE.search(full_title)
        if m:
            meta["water_body"] = m.group(1).strip()

    # Also search first 3000 chars of page text for water body.
    if "water_body" not in meta:
        text_content = soup.get_text(separator=" ")[:3000]
        m = _WATER_BODY_TITLE_RE.search(text_content)
        if m:
            meta["water_body"] = m.group(1).strip()
        else:
            m = _WATER_BODY_STANDALONE_RE.search(text_content)
            if m:
                meta["water_body"] = m.group(1).strip()

    # Date extraction.
    text_content = soup.get_text(separator=" ")[:3000]
    date_patterns = [
        re.compile(
            r"(\w+\s+\d{1,2}(?:\s*[-\u2013]\s*\d{1,2})?,\s*\d{4})"
        ),
        re.compile(r"(\d{1,2}/\d{1,2}/\d{4})"),
        re.compile(r"(\d{4}-\d{2}-\d{2})"),
    ]
    for pattern in date_patterns:
        m = pattern.search(text_content)
        if m:
            date_str = m.group(1)
            parsed = pd.to_datetime(date_str, errors="coerce")
            if not pd.isna(parsed):
                meta["date"] = parsed.strftime("%Y-%m-%d")
                break

    return meta


def _parse_flw_slider_weights(soup: BeautifulSoup) -> dict[int, list[float]]:
    """Extract weights from FLW's angler profile slider/carousel format.

    flwfishing.com renders top finishers in a slider with spans like:
        <span class="anglerProfileFinalweight">12 - 2</span>
    These are total tournament weights (not per-day).
    """
    weights: list[float] = []

    # Find all weight spans in the slider
    for span in soup.select(".anglerProfileFinalweight, span.anglerProfileFinalweight"):
        text = _clean_text(span.get_text())
        w = _weight_str_to_lb(text)
        if w is not None and w > 0:
            weights.append(w)

    # Also try regex on raw HTML for cases where CSS classes get mangled
    if not weights:
        html_str = str(soup)
        for m in re.finditer(
            r'anglerProfileFinalweight["\']?\s*>([^<]+)<', html_str
        ):
            w = _weight_str_to_lb(m.group(1))
            if w is not None and w > 0:
                weights.append(w)

    if len(weights) >= MIN_WEIGHTS_FOR_MEDIAN:
        return {1: weights}  # Return as "day 1" (total weight)
    return {}


def _parse_page_for_standings(html: str) -> dict[int, list[float]]:
    """Parse an HTML page for standings data from tables and sliders."""
    soup = BeautifulSoup(html, "html.parser")

    # Remove Wayback Machine toolbar.
    for wb_div in soup.select("#wm-ipp-base, #wm-ipp, #wm-ipp-print"):
        wb_div.decompose()

    best_day_weights: dict[int, list[float]] = {}

    # Strategy 0: FLW angler profile slider format (most common on flwfishing.com)
    slider_weights = _parse_flw_slider_weights(soup)
    if slider_weights:
        best_day_weights = slider_weights

    # Try tables with various selectors (FLW used different CSS classes) — only if slider didn't work.
    if not best_day_weights:
        table_selectors = [
            "table.results-table",
            "table.standings",
            "table.leaderboard",
            "table.tournament-results",
            "table.ResultsTable",
            "table.data-table",
            "div.results table",
            "div.standings table",
            "div.leaderboard table",
            "div.tournament table",
            "#results table",
            "#standings table",
            "main table",
            "article table",
            ".content table",
            "table",
        ]

        for selector in table_selectors:
            tables = soup.select(selector)
            for table in tables:
                parsed_rows = _parse_standings_table(table)
                if len(parsed_rows) < MIN_WEIGHTS_FOR_MEDIAN:
                    continue
                day_weights = _extract_day_weights_from_rows(parsed_rows)
                total_new = sum(len(ws) for ws in day_weights.values())
                total_best = sum(len(ws) for ws in best_day_weights.values())
                if total_new > total_best:
                    best_day_weights = day_weights
            if best_day_weights:
                break

    # Try inline JSON / script data if tables didn't work.
    if not best_day_weights:
        best_day_weights = _try_extract_json_standings(soup)

    return best_day_weights


def _try_extract_json_standings(soup: BeautifulSoup) -> dict[int, list[float]]:
    """Search <script> blocks for JSON data containing standings."""
    for script in soup.find_all("script"):
        text = script.string or ""
        if not text:
            continue

        weight_keywords = ('"weight"', '"totalweight"', '"day1"', '"catch"', '"angler"')
        if not any(kw in text.lower() for kw in weight_keywords):
            continue

        for json_match in re.finditer(r"(\[{.*?}\])", text, re.DOTALL):
            try:
                data = json.loads(json_match.group(1))
                result = _walk_json_for_standings(data)
                if result:
                    return result
            except (json.JSONDecodeError, KeyError):
                continue

    return {}


def _walk_json_for_standings(
    data: Any,
    depth: int = 0,
) -> dict[int, list[float]] | None:
    """Recursively search a JSON structure for standings arrays."""
    if depth > 8:
        return None

    if isinstance(data, list) and len(data) >= MIN_WEIGHTS_FOR_MEDIAN:
        if all(isinstance(item, dict) for item in data):
            day_weights: dict[int, list[float]] = {}

            for day_num in range(1, 5):
                weights: list[float] = []
                for item in data:
                    for key_pattern in [
                        f"day{day_num}",
                        f"day_{day_num}",
                        f"day{day_num}_weight",
                        f"d{day_num}",
                    ]:
                        for k, v in item.items():
                            nk = k.lower().replace(" ", "").replace("-", "_")
                            if nk == key_pattern:
                                w = _weight_str_to_lb(str(v))
                                if w is not None and w > 0:
                                    weights.append(w)
                if weights:
                    day_weights[day_num] = weights

            if not day_weights:
                weights = []
                for item in data:
                    for k in (
                        "weight", "total_weight", "totalWeight", "total",
                        "catch", "totalCatch",
                    ):
                        v = item.get(k)
                        if v is not None:
                            w = _weight_str_to_lb(str(v))
                            if w is not None and w > 0:
                                weights.append(w)
                            break
                if len(weights) >= MIN_WEIGHTS_FOR_MEDIAN:
                    day_weights[1] = weights

            if day_weights:
                return day_weights

    if isinstance(data, dict):
        for v in data.values():
            result = _walk_json_for_standings(v, depth + 1)
            if result:
                return result

    if isinstance(data, list):
        for item in data:
            result = _walk_json_for_standings(item, depth + 1)
            if result:
                return result

    return None


# ---------------------------------------------------------------------------
# Scrape standings for a tournament (tries all archived snapshots)
# ---------------------------------------------------------------------------


def scrape_flw_standings(
    session: requests.Session,
    tournament: FLWTournament,
) -> dict[int, list[float]]:
    """Scrape standings for an FLW tournament from its archived pages.

    Tries each archived snapshot, taking the one with the most weight data.
    Returns ``{day_number: [weight_lb, ...]}`` mapping.
    """
    best_day_weights: dict[int, list[float]] = {}

    for archived_entry in tournament.archived_urls:
        if "|" not in archived_entry:
            continue

        timestamp, original_url = archived_entry.split("|", 1)
        html = _fetch_wayback_page(session, timestamp, original_url)
        if not html:
            continue

        # Fill in missing metadata from page content.
        if not tournament.water_body or tournament.title == tournament.slug.replace("-", " ").title():
            page_meta = _extract_metadata_from_html(html)
            if "water_body" in page_meta and not tournament.water_body:
                tournament.water_body = page_meta["water_body"]
            if "title" in page_meta:
                tournament.title = page_meta["title"]
            if "date" in page_meta:
                tournament.date = page_meta["date"]

        day_weights = _parse_page_for_standings(html)
        if day_weights:
            total_new = sum(len(ws) for ws in day_weights.values())
            total_best = sum(len(ws) for ws in best_day_weights.values())
            if total_new > total_best:
                best_day_weights = day_weights

        # If we found good data, no need to try more snapshots.
        if sum(len(ws) for ws in best_day_weights.values()) >= MIN_WEIGHTS_FOR_MEDIAN:
            break

    return best_day_weights


# ---------------------------------------------------------------------------
# Checkpoint support
# ---------------------------------------------------------------------------


def _load_checkpoint(output_path: Path) -> tuple[pd.DataFrame, set[str]]:
    """Load existing checkpoint CSV if present.

    Returns (existing_df, set_of_processed_slugs).
    """
    if output_path.exists():
        try:
            df = pd.read_csv(output_path)
            slugs = set(df["tournament_slug"].unique()) if "tournament_slug" in df.columns else set()
            print(
                f"flw: loaded checkpoint with {len(df)} rows, "
                f"{len(slugs)} slugs",
                file=sys.stderr,
            )
            return df, slugs
        except Exception as exc:
            print(f"flw: error loading checkpoint: {exc}", file=sys.stderr)
    return pd.DataFrame(), set()


def _save_checkpoint(output_path: Path, rows: list[dict[str, Any]]) -> None:
    """Save current rows to checkpoint CSV."""
    if not rows:
        return
    df = pd.DataFrame(rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    print(f"flw: checkpoint saved -> {len(df)} rows to {output_path}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Median weight helper
# ---------------------------------------------------------------------------


def _median_weight(weights: list[float]) -> float:
    """Compute median weight in pounds, rounded to 4 decimal places."""
    return round(statistics.median(weights), 4)


# ---------------------------------------------------------------------------
# Main collection
# ---------------------------------------------------------------------------


def collect_flw_outcomes(
    output_path: Path | str = OUTPUT_PATH,
    start_year: int = START_YEAR,
    end_year: int = END_YEAR,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Collect FLW tournament outcome data from the Wayback Machine.

    Discovers tournaments, scrapes per-day standings, and writes an
    event-day outcome CSV compatible with the existing outcomes schema.

    Parameters
    ----------
    output_path:
        Destination CSV path.
    start_year, end_year:
        Inclusive year range (default 2007-2019).
    session:
        Optional ``requests.Session``; created if not provided.

    Returns
    -------
    pd.DataFrame
        With columns: event_id, tournament_slug, event_name, date,
        location, species, median_weight_lb, baseline_signal,
        usgs_site_id, results_source, num_anglers, day_number.
    """
    output_path = Path(output_path)
    owned_session = session is None
    session = session or requests.Session()

    # Load checkpoint to resume from.
    existing_df, processed_slugs = _load_checkpoint(output_path)
    rows: list[dict[str, Any]] = existing_df.to_dict("records") if not existing_df.empty else []

    events_since_checkpoint = 0

    try:
        # Phase 1: Discover tournaments.
        tournaments = discover_flw_tournaments(
            session, start_year=start_year, end_year=end_year
        )

        # Phase 2: Scrape standings for each tournament.
        for i, tournament in enumerate(tournaments):
            if tournament.slug in processed_slugs:
                print(
                    f"flw: [{i + 1}/{len(tournaments)}] skipping {tournament.slug} "
                    f"(already processed)",
                    file=sys.stderr,
                )
                continue

            print(
                f"flw: [{i + 1}/{len(tournaments)}] scraping {tournament.slug}...",
                file=sys.stderr,
            )

            try:
                day_weights = scrape_flw_standings(session, tournament)
            except Exception as exc:
                print(
                    f"flw: error scraping {tournament.slug}: {exc}",
                    file=sys.stderr,
                )
                continue

            if not day_weights:
                print(
                    f"flw: no standings data for {tournament.slug}",
                    file=sys.stderr,
                )
                continue

            # Resolve USGS gauge.
            usgs_site_id = ""
            if tournament.water_body:
                usgs_site_id = _auto_resolve_usgs_site(tournament.water_body)
            if not usgs_site_id and tournament.location:
                for part in tournament.location.split(","):
                    usgs_site_id = _auto_resolve_usgs_site(part.strip())
                    if usgs_site_id:
                        break
            if not usgs_site_id:
                usgs_site_id = _auto_resolve_usgs_site(tournament.title)

            for day_num in sorted(day_weights.keys()):
                weights = day_weights[day_num]
                if len(weights) < MIN_WEIGHTS_FOR_MEDIAN:
                    print(
                        f"flw: {tournament.slug} day {day_num}: "
                        f"only {len(weights)} weights, skipping",
                        file=sys.stderr,
                    )
                    continue

                # Compute event date offset from tournament start.
                try:
                    base_date = pd.to_datetime(tournament.date, errors="coerce")
                    if not pd.isna(base_date):
                        event_date = (
                            base_date + timedelta(days=day_num - 1)
                        ).strftime("%Y-%m-%d")
                    else:
                        event_date = tournament.date
                except Exception:
                    event_date = tournament.date

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
                        "results_source": "mlf" if tournament.slug.startswith("mlf-") else "flw",
                        "num_anglers": len(weights),
                        "day_number": day_num,
                    }
                )

            print(
                f"flw: {tournament.slug} -> "
                f"{len(day_weights)} day(s), "
                f"water={tournament.water_body or '?'}, "
                f"gauge={usgs_site_id or 'UNMAPPED'}",
                file=sys.stderr,
            )

            processed_slugs.add(tournament.slug)
            events_since_checkpoint += 1

            # Periodic checkpoint save.
            if events_since_checkpoint >= CHECKPOINT_INTERVAL:
                _save_checkpoint(output_path, rows)
                events_since_checkpoint = 0

    finally:
        if owned_session:
            session.close()

    # ------------------------------------------------------------------
    # Build and persist the final output DataFrame.
    # ------------------------------------------------------------------
    df = pd.DataFrame(rows)
    if df.empty:
        print(
            f"flw: WARNING -- no outcome rows produced for "
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
            ]
        )
    else:
        # Deduplicate by event_id (checkpoint + new may overlap).
        df = df.drop_duplicates(subset=["event_id"], keep="last").reset_index(drop=True)

        # Report mapping stats.
        mapped = df["usgs_site_id"].astype(str).str.strip().ne("")
        unmapped_slugs = df.loc[~mapped, "tournament_slug"].unique().tolist()
        if unmapped_slugs:
            print(
                f"flw: {(~mapped).sum()} rows from "
                f"{len(unmapped_slugs)} tournaments have no USGS gauge: "
                + ", ".join(sorted(unmapped_slugs)[:15]),
                file=sys.stderr,
            )
        mapped_count = mapped.sum()
        print(
            f"flw: {mapped_count}/{len(df)} rows have USGS gauge mappings",
            file=sys.stderr,
        )

    # Persist final output.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    print(f"flw: saved {len(df)} rows to {output_path}", file=sys.stderr)

    return df


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    print("=" * 60, file=sys.stderr)
    print("FLW Historical Tournament Scraper (Wayback Machine)", file=sys.stderr)
    print(f"Year range: {START_YEAR}-{END_YEAR}", file=sys.stderr)
    print(f"Output: {OUTPUT_PATH}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    df = collect_flw_outcomes()

    if not df.empty:
        print("\n--- Summary ---", file=sys.stderr)
        print(f"Total rows: {len(df)}", file=sys.stderr)
        print(
            f"Unique tournaments: {df['tournament_slug'].nunique()}",
            file=sys.stderr,
        )
        mapped = df["usgs_site_id"].astype(str).str.strip().ne("")
        print(f"Rows with USGS gauge: {mapped.sum()}", file=sys.stderr)
        if "date" in df.columns:
            print(
                f"Date range: {df['date'].min()} to {df['date'].max()}",
                file=sys.stderr,
            )
        print(
            f"Median weight stats: "
            f"mean={df['median_weight_lb'].mean():.2f}, "
            f"min={df['median_weight_lb'].min():.2f}, "
            f"max={df['median_weight_lb'].max():.2f}",
            file=sys.stderr,
        )
    else:
        print("\nNo data collected.", file=sys.stderr)


if __name__ == "__main__":
    main()
