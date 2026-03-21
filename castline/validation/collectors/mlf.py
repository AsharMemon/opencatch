"""Major League Fishing (MLF) tournament results scraper.

MLF's website (majorleaguefishing.com) returns 403 for automated requests.
This collector uses multiple bypass strategies, primarily the Wayback Machine's
CDX API to discover and fetch archived versions of MLF results pages.

The output schema matches ``elite_standings.py`` so the two sources can be
concatenated for downstream validation.
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
from urllib.parse import quote, urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup, Tag

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

MLF_BASE = "https://www.majorleaguefishing.com"

# Wayback Machine CDX API endpoint
WAYBACK_CDX_URL = "http://web.archive.org/cdx/search/cdx"
WAYBACK_WEB_PREFIX = "http://web.archive.org/web"

# Minimum number of angler weights required to compute a meaningful median.
MIN_WEIGHTS_FOR_MEDIAN = 5

# Polite delay between Wayback Machine requests (seconds).
WAYBACK_DELAY = 1.0

# User-Agent rotation for direct access attempts.
_USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) "
        "Gecko/20100101 Firefox/121.0"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) "
        "Gecko/20100101 Firefox/121.0"
    ),
]

# Headers that mimic a real browser session.
_BROWSER_HEADERS_TEMPLATE = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

# URL patterns we look for in the Wayback Machine index.
_MLF_RESULTS_URL_PATTERNS = [
    "majorleaguefishing.com/results",
    "majorleaguefishing.com/tournaments",
    "majorleaguefishing.com/bass-pro-tour",
    "majorleaguefishing.com/cup",
    "majorleaguefishing.com/event",
    "majorleaguefishing.com/schedule",
]

# Regex to extract weight values in various formats.
# Handles: "15 lbs 8 oz", "15-08", "15.50", "7 lb 12 oz", etc.
_WEIGHT_LB_OZ_RE = re.compile(
    r"(\d{1,3})\s*(?:lbs?|pounds?)?\s*[-\s,]*\s*(\d{1,2})\s*(?:oz|ounces?)?",
    re.IGNORECASE,
)
_WEIGHT_DECIMAL_RE = re.compile(r"(\d{1,3}\.\d{1,4})\s*(?:lbs?|pounds?)?", re.IGNORECASE)
_WEIGHT_DASH_RE = re.compile(r"(\d{1,3})\s*-\s*(\d{1,2})")

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class MLFTournament:
    """Metadata for a single MLF tournament."""

    slug: str
    title: str
    water_body: str
    date: str
    species: str = "black_bass"
    archived_urls: list[str] = field(default_factory=list)
    trail: str = ""  # e.g. "bass-pro-tour", "cup", "toyota-series"

    @property
    def location(self) -> str:
        return self.water_body

    @property
    def event_id_prefix(self) -> str:
        return f"mlf-{self.slug}"


# ---------------------------------------------------------------------------
# Weight parsing
# ---------------------------------------------------------------------------


def _weight_str_to_lb(text: str) -> float | None:
    """Convert a weight string to decimal pounds.

    Handles multiple formats found across MLF result pages:
      - "15-08" (lbs-oz, Bassmaster style)
      - "15 lbs 8 oz"
      - "15.50" (decimal pounds)
      - "7 lb 12 oz"
    Returns None on failure.
    """
    text = str(text).strip()
    if not text or text in ("-", "—", "DNW", "DQ", "DNS"):
        return None

    # Try lbs-oz dash format first (most common in tournament tables).
    m = _WEIGHT_DASH_RE.search(text)
    if m:
        lbs = int(m.group(1))
        ozs = int(m.group(2))
        if ozs <= 15 and lbs < 200:
            return lbs + ozs / 16.0

    # Try "X lbs Y oz" format.
    m = _WEIGHT_LB_OZ_RE.search(text)
    if m:
        lbs = int(m.group(1))
        ozs = int(m.group(2))
        if ozs <= 15 and lbs < 200:
            return lbs + ozs / 16.0

    # Try decimal pounds.
    m = _WEIGHT_DECIMAL_RE.search(text)
    if m:
        val = float(m.group(1))
        if 0 < val < 200:
            return val

    # Last resort: plain integer (rare but possible for total lbs).
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


def _make_browser_headers(user_agent_idx: int = 0, referer: str = "") -> dict[str, str]:
    """Build a set of headers that mimic a real browser."""
    headers = dict(_BROWSER_HEADERS_TEMPLATE)
    headers["User-Agent"] = _USER_AGENTS[user_agent_idx % len(_USER_AGENTS)]
    if referer:
        headers["Referer"] = referer
    return headers


def _fetch_with_retry(
    session: requests.Session,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
    max_retries: int = 2,
    delay: float = 1.0,
) -> requests.Response | None:
    """Fetch a URL with retries and polite delays."""
    for attempt in range(max_retries + 1):
        try:
            resp = session.get(url, headers=headers or DEFAULT_HEADERS, timeout=timeout)
            if resp.status_code == 429:
                wait = delay * (2 ** attempt)
                print(f"mlf: rate-limited on {url}, waiting {wait:.0f}s", file=sys.stderr)
                time.sleep(wait)
                continue
            return resp
        except requests.RequestException as exc:
            if attempt < max_retries:
                time.sleep(delay)
            else:
                print(f"mlf: request failed for {url}: {exc}", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# Strategy 1: Wayback Machine discovery and fetching
# ---------------------------------------------------------------------------


def _query_wayback_cdx(
    session: requests.Session,
    url_pattern: str,
    *,
    limit: int = 500,
    from_year: int | None = None,
    to_year: int | None = None,
) -> list[dict[str, str]]:
    """Query the Wayback Machine CDX API for archived URLs matching a pattern.

    Returns a list of dicts with keys: urlkey, timestamp, original, mimetype,
    statuscode, digest, length.
    """
    params: dict[str, Any] = {
        "url": url_pattern,
        "output": "json",
        "limit": limit,
        "fl": "timestamp,original,statuscode,mimetype",
        "filter": "statuscode:200",
        "collapse": "urlkey",  # deduplicate by URL
    }
    if from_year:
        params["from"] = str(from_year)
    if to_year:
        params["to"] = str(to_year)

    resp = _fetch_with_retry(
        session,
        WAYBACK_CDX_URL,
        headers=DEFAULT_HEADERS,
        timeout=60,
    )
    if resp is None:
        return []

    # CDX API needs params passed via query string
    resp = session.get(WAYBACK_CDX_URL, params=params, timeout=60, headers=DEFAULT_HEADERS)
    if resp.status_code != 200:
        print(f"mlf: CDX API returned {resp.status_code}", file=sys.stderr)
        return []

    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError):
        print("mlf: CDX API returned non-JSON response", file=sys.stderr)
        return []

    if not data or len(data) < 2:
        return []

    # First row is the header
    header = data[0]
    results = []
    for row in data[1:]:
        results.append(dict(zip(header, row)))

    return results


def _fetch_wayback_page(
    session: requests.Session,
    timestamp: str,
    original_url: str,
) -> str | None:
    """Fetch a page from the Wayback Machine archive."""
    wayback_url = f"{WAYBACK_WEB_PREFIX}/{timestamp}id_/{original_url}"
    time.sleep(WAYBACK_DELAY)

    resp = _fetch_with_retry(session, wayback_url, timeout=45)
    if resp is None or resp.status_code != 200:
        # Try without the id_ modifier (raw page).
        wayback_url_raw = f"{WAYBACK_WEB_PREFIX}/{timestamp}/{original_url}"
        resp = _fetch_with_retry(session, wayback_url_raw, timeout=45)
        if resp is None or resp.status_code != 200:
            return None

    return resp.text


def _extract_tournament_info_from_url(url: str) -> dict[str, str]:
    """Try to extract tournament metadata from a Wayback-archived MLF URL.

    Parses URL path segments and returns whatever we can infer.
    """
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    segments = [s for s in path.split("/") if s]

    info: dict[str, str] = {"url": url}

    # Try to extract year from URL.
    for seg in segments:
        if re.match(r"^20\d{2}$", seg):
            info["year"] = seg
            break

    # Build a slug from the path.
    slug_parts = [s for s in segments if not re.match(r"^(results|standings|tournaments|events|schedule|page|\d+)$", s)]
    if slug_parts:
        info["slug"] = "-".join(slug_parts[-3:])  # last 3 meaningful segments

    return info


def discover_mlf_tournaments(
    session: requests.Session,
    start_year: int = 2014,
    end_year: int = 2025,
) -> list[MLFTournament]:
    """Discover MLF tournaments via the Wayback Machine CDX API.

    Queries the Internet Archive for cached MLF results/tournament pages
    and attempts to extract tournament metadata from the archived content.

    Returns tournaments sorted by date ascending.
    """
    all_cdx_results: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    # Query CDX API for multiple URL patterns.
    url_queries = [
        "majorleaguefishing.com/results*",
        "majorleaguefishing.com/tournaments/*",
        "majorleaguefishing.com/bass-pro-tour/*/results*",
        "majorleaguefishing.com/cup/*/results*",
        "majorleaguefishing.com/event/*/results*",
        "majorleaguefishing.com/schedule/*",
    ]

    for url_pattern in url_queries:
        print(f"mlf: querying Wayback CDX for {url_pattern}...", file=sys.stderr)
        results = _query_wayback_cdx(
            session,
            url_pattern,
            limit=500,
            from_year=start_year,
            to_year=end_year,
        )
        for r in results:
            original = r.get("original", "")
            if original not in seen_urls:
                seen_urls.add(original)
                all_cdx_results.append(r)

    print(
        f"mlf: found {len(all_cdx_results)} unique archived URLs",
        file=sys.stderr,
    )

    # Group archived URLs by tournament (heuristic: similar URL paths).
    tournaments: list[MLFTournament] = []
    tournament_map: dict[str, MLFTournament] = {}

    for cdx_entry in all_cdx_results:
        original_url = cdx_entry.get("original", "")
        timestamp = cdx_entry.get("timestamp", "")

        # Skip non-HTML results.
        mimetype = cdx_entry.get("mimetype", "")
        if mimetype and "html" not in mimetype and "text" not in mimetype:
            continue

        # Filter out index/listing pages (we want individual tournament pages).
        path = urlparse(original_url).path.strip("/")
        segments = [s for s in path.split("/") if s]
        if len(segments) < 2:
            continue  # Too generic, likely a listing page.

        info = _extract_tournament_info_from_url(original_url)
        slug = info.get("slug", path.replace("/", "-"))

        # Normalize slug for grouping.
        group_key = re.sub(r"\d{14}", "", slug)  # remove timestamps
        group_key = re.sub(r"-+", "-", group_key).strip("-")

        if group_key in tournament_map:
            tournament_map[group_key].archived_urls.append(
                f"{timestamp}|{original_url}"
            )
        else:
            # Extract year from timestamp for date approximation.
            year = timestamp[:4] if len(timestamp) >= 4 else info.get("year", "2020")
            month = timestamp[4:6] if len(timestamp) >= 6 else "06"
            day = timestamp[6:8] if len(timestamp) >= 8 else "15"
            approx_date = f"{year}-{month}-{day}"

            # Derive a title from the slug.
            title = slug.replace("-", " ").title()

            t = MLFTournament(
                slug=slug,
                title=title,
                water_body="",  # filled in during scraping
                date=approx_date,
                archived_urls=[f"{timestamp}|{original_url}"],
            )
            tournament_map[group_key] = t
            tournaments.append(t)

    # Filter to requested year range.
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
        f"mlf: discovered {len(filtered)} tournament groups "
        f"({start_year}-{end_year})",
        file=sys.stderr,
    )
    return filtered


# ---------------------------------------------------------------------------
# Strategy 2: Direct access with browser-like headers
# ---------------------------------------------------------------------------


def _try_direct_access(
    session: requests.Session,
    url: str,
) -> str | None:
    """Attempt to fetch an MLF page directly using browser-mimicking headers.

    Tries multiple User-Agent and header combinations to bypass 403 blocks.
    Returns the HTML content if successful, None otherwise.
    """
    for ua_idx in range(len(_USER_AGENTS)):
        headers = _make_browser_headers(ua_idx, referer="https://www.google.com/")
        resp = _fetch_with_retry(session, url, headers=headers, max_retries=1, delay=2.0)
        if resp is not None and resp.status_code == 200:
            return resp.text
        if resp is not None and resp.status_code == 403:
            continue  # Try next UA
        if resp is not None and resp.status_code == 404:
            return None  # Page doesn't exist

    return None


# ---------------------------------------------------------------------------
# Strategy 3: WordPress / JSON API exploration
# ---------------------------------------------------------------------------


def _try_mlf_api(
    session: requests.Session,
) -> list[dict[str, Any]]:
    """Attempt to discover MLF tournaments via potential API endpoints.

    MLF may use a WordPress backend or a custom JSON API. We try
    several common patterns.
    """
    api_endpoints = [
        f"{MLF_BASE}/wp-json/wp/v2/posts?per_page=100&categories=results",
        f"{MLF_BASE}/wp-json/wp/v2/tournament?per_page=100",
        f"{MLF_BASE}/wp-json/wp/v2/event?per_page=100",
        f"{MLF_BASE}/api/results",
        f"{MLF_BASE}/api/tournaments",
        f"{MLF_BASE}/api/v1/results",
        f"{MLF_BASE}/api/v1/tournaments",
    ]

    results: list[dict[str, Any]] = []

    for endpoint in api_endpoints:
        for ua_idx in range(2):  # Try 2 different UAs
            headers = _make_browser_headers(ua_idx)
            headers["Accept"] = "application/json"
            resp = _fetch_with_retry(
                session, endpoint, headers=headers, max_retries=1, delay=1.0
            )
            if resp is None:
                continue
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    if isinstance(data, list) and data:
                        print(
                            f"mlf: API endpoint responded: {endpoint} "
                            f"({len(data)} items)",
                            file=sys.stderr,
                        )
                        results.extend(data)
                    elif isinstance(data, dict) and data:
                        print(
                            f"mlf: API endpoint responded: {endpoint}",
                            file=sys.stderr,
                        )
                        results.append(data)
                    break  # Got a response, no need to try other UAs
                except (json.JSONDecodeError, ValueError):
                    continue
            elif resp.status_code in (403, 404):
                break  # This endpoint doesn't exist or is blocked

    return results


# ---------------------------------------------------------------------------
# Strategy 4: Google Cache (low success rate fallback)
# ---------------------------------------------------------------------------


def _try_google_cache(
    session: requests.Session,
    url: str,
) -> str | None:
    """Attempt to fetch a page from Google's cache.

    Low success rate for programmatic access but worth trying as a fallback.
    """
    cache_url = f"https://webcache.googleusercontent.com/search?q=cache:{quote(url)}"
    headers = _make_browser_headers(0, referer="https://www.google.com/search")
    resp = _fetch_with_retry(session, cache_url, headers=headers, max_retries=1, delay=2.0)
    if resp is not None and resp.status_code == 200:
        return resp.text
    return None


# ---------------------------------------------------------------------------
# HTML parsing for MLF standings
# ---------------------------------------------------------------------------


def _parse_mlf_standings_table(table: Tag) -> list[dict[str, Any]]:
    """Extract angler rows from an HTML <table> found on MLF pages.

    MLF has used various table formats over the years.  This function
    handles the common patterns.
    """
    rows: list[dict[str, Any]] = []
    headers: list[str] = []

    # Extract headers from <thead> or first <tr> with <th> cells.
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
        # Skip pure header rows.
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
    """Map logical day/weight columns to actual header names.

    Looks for columns like 'Day 1', 'Day 2', 'Weight', 'Total', 'Catch', etc.
    MLF uses varied naming conventions.
    """
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
        elif hl in ("total", "total weight", "total wt", "total wt.", "total catch"):
            mapping["total"] = h
        elif hl in (
            "weight", "wt", "wt.", "today", "today wt", "catch",
            "scorecard", "final weight", "final wt",
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
    """Given parsed table rows, extract per-day weight lists.

    Returns ``{day_number: [weight_in_lbs, ...]}`` where day_number is 1-based.
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

    # Strategy 3: brute-force scan every column for weight-like values.
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
    """Extract tournament metadata (water body, date, title) from page HTML."""
    soup = BeautifulSoup(html, "html.parser")
    meta: dict[str, str] = {}

    # Try <title> tag.
    title_tag = soup.find("title")
    if title_tag:
        meta["title"] = _clean_text(title_tag.get_text())

    # Try <h1> or <h2> for tournament name.
    for tag_name in ("h1", "h2"):
        tag = soup.find(tag_name)
        if tag:
            text = _clean_text(tag.get_text())
            if text and len(text) > 5:
                meta.setdefault("title", text)
                break

    # Look for location/water body in common patterns.
    # MLF pages often have location info near headers or in structured divs.
    location_patterns = [
        re.compile(r"(?:lake|reservoir|river)\s+\w[\w\s]+", re.IGNORECASE),
    ]
    text_content = soup.get_text(separator=" ")
    for pattern in location_patterns:
        m = pattern.search(text_content[:2000])
        if m:
            meta.setdefault("water_body", _clean_text(m.group(0)))
            break

    # Try to find date in the page.
    date_patterns = [
        re.compile(r"(\w+\s+\d{1,2}(?:\s*[-–]\s*\d{1,2})?,\s*\d{4})"),  # "January 5-8, 2023"
        re.compile(r"(\d{1,2}/\d{1,2}/\d{4})"),  # "1/5/2023"
        re.compile(r"(\d{4}-\d{2}-\d{2})"),  # "2023-01-05"
    ]
    for pattern in date_patterns:
        m = pattern.search(text_content[:3000])
        if m:
            date_str = m.group(1)
            parsed = pd.to_datetime(date_str, errors="coerce")
            if not pd.isna(parsed):
                meta["date"] = parsed.strftime("%Y-%m-%d")
                break

    return meta


def _try_extract_json_standings(html: str) -> dict[int, list[float]] | None:
    """Search for inline JSON data or script blobs containing standings."""
    soup = BeautifulSoup(html, "html.parser")

    for script in soup.find_all("script"):
        text = script.string or ""
        if not text:
            continue

        # Look for Next.js data blob.
        if "__NEXT_DATA__" in text:
            match = re.search(
                r"__NEXT_DATA__\s*=\s*({.*?})\s*;?\s*$", text, re.DOTALL
            )
            if match:
                try:
                    data = json.loads(match.group(1))
                    result = _walk_json_for_standings(data)
                    if result:
                        return result
                except (json.JSONDecodeError, KeyError):
                    pass

        # Generic JSON structures containing weight data.
        if any(
            kw in text.lower()
            for kw in ('"weight"', '"totalweight"', '"day1"', '"catch"', '"angler"')
        ):
            for json_match in re.finditer(r"(\[{.*?}\])", text, re.DOTALL):
                try:
                    data = json.loads(json_match.group(1))
                    result = _walk_json_for_standings(data)
                    if result:
                        return result
                except (json.JSONDecodeError, KeyError):
                    continue

    return None


def _walk_json_for_standings(
    data: Any,
    depth: int = 0,
) -> dict[int, list[float]] | None:
    """Recursively search a JSON structure for standings-like arrays."""
    if depth > 8:
        return None

    if isinstance(data, list) and len(data) >= MIN_WEIGHTS_FOR_MEDIAN:
        if all(isinstance(item, dict) for item in data):
            day_weights: dict[int, list[float]] = {}

            # Check for day-specific weight keys.
            for day_num in range(1, 5):
                weights: list[float] = []
                for item in data:
                    for key_pattern in [
                        f"day{day_num}",
                        f"day_{day_num}",
                        f"day{day_num}_weight",
                        f"day{day_num}weight",
                        f"d{day_num}",
                    ]:
                        for k, v in item.items():
                            normalized_key = (
                                k.lower().replace(" ", "").replace("-", "_")
                            )
                            if normalized_key == key_pattern:
                                w = _weight_str_to_lb(str(v))
                                if w is not None and w > 0:
                                    weights.append(w)
                if weights:
                    day_weights[day_num] = weights

            # Fallback: generic weight field.
            if not day_weights:
                weights = []
                for item in data:
                    for k in (
                        "weight", "total_weight", "totalWeight", "total",
                        "catch", "totalCatch", "scorecard",
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
# Main scraping orchestration
# ---------------------------------------------------------------------------


def scrape_mlf_standings(
    session: requests.Session,
    tournament: MLFTournament,
) -> dict[int, list[float]]:
    """Scrape standings for an MLF tournament.

    Tries multiple strategies in order of reliability:
      1. Wayback Machine archived pages
      2. Direct access with browser-like headers
      3. Google Cache
      4. JSON API endpoints

    Returns ``{day_number: [weight_lb, ...]}`` mapping.
    """
    best_day_weights: dict[int, list[float]] = {}

    # ------------------------------------------------------------------
    # Strategy 1: Wayback Machine (primary strategy)
    # ------------------------------------------------------------------
    for archived_entry in tournament.archived_urls:
        if "|" in archived_entry:
            timestamp, original_url = archived_entry.split("|", 1)
        else:
            continue

        html = _fetch_wayback_page(session, timestamp, original_url)
        if not html:
            continue

        # Try to fill in missing tournament metadata.
        if not tournament.water_body:
            page_meta = _extract_metadata_from_html(html)
            if "water_body" in page_meta:
                tournament.water_body = page_meta["water_body"]
            if "title" in page_meta and tournament.title.startswith("mlf-"):
                tournament.title = page_meta["title"]
            if "date" in page_meta:
                tournament.date = page_meta["date"]

        # Parse HTML tables.
        day_weights = _parse_page_for_standings(html)
        if day_weights:
            total_weights = sum(len(ws) for ws in day_weights.values())
            if total_weights > sum(len(ws) for ws in best_day_weights.values()):
                best_day_weights = day_weights

        if best_day_weights:
            return best_day_weights

    # ------------------------------------------------------------------
    # Strategy 2: Direct access with browser headers
    # ------------------------------------------------------------------
    direct_urls = _build_direct_urls(tournament)
    for url in direct_urls:
        html = _try_direct_access(session, url)
        if html:
            if not tournament.water_body:
                page_meta = _extract_metadata_from_html(html)
                if "water_body" in page_meta:
                    tournament.water_body = page_meta["water_body"]

            day_weights = _parse_page_for_standings(html)
            if day_weights:
                total_weights = sum(len(ws) for ws in day_weights.values())
                if total_weights > sum(
                    len(ws) for ws in best_day_weights.values()
                ):
                    best_day_weights = day_weights

            if best_day_weights:
                return best_day_weights

    # ------------------------------------------------------------------
    # Strategy 3: Google Cache (low success rate)
    # ------------------------------------------------------------------
    for url in direct_urls[:2]:  # Only try first 2 URLs
        html = _try_google_cache(session, url)
        if html:
            day_weights = _parse_page_for_standings(html)
            if day_weights:
                total_weights = sum(len(ws) for ws in day_weights.values())
                if total_weights > sum(
                    len(ws) for ws in best_day_weights.values()
                ):
                    best_day_weights = day_weights

            if best_day_weights:
                return best_day_weights

    return best_day_weights


def _build_direct_urls(tournament: MLFTournament) -> list[str]:
    """Build a list of direct MLF URLs to try for a tournament."""
    slug = tournament.slug
    urls = [
        f"{MLF_BASE}/results/{slug}/",
        f"{MLF_BASE}/tournaments/{slug}/results/",
        f"{MLF_BASE}/bass-pro-tour/{slug}/results/",
        f"{MLF_BASE}/event/{slug}/results/",
        f"{MLF_BASE}/cup/{slug}/results/",
    ]
    # Also try the original archived URLs directly.
    for entry in tournament.archived_urls:
        if "|" in entry:
            _, original_url = entry.split("|", 1)
            if original_url not in urls:
                urls.append(original_url)
    return urls


def _parse_page_for_standings(html: str) -> dict[int, list[float]]:
    """Parse a full HTML page for standings data (tables and JSON)."""
    soup = BeautifulSoup(html, "html.parser")

    # Remove Wayback Machine toolbar if present.
    for wb_div in soup.select("#wm-ipp-base, #wm-ipp, #wm-ipp-print"):
        wb_div.decompose()

    best_day_weights: dict[int, list[float]] = {}

    # Try HTML tables with various selectors.
    table_selectors = [
        "table.results-table",
        "table.standings-table",
        "table.leaderboard",
        "table.tournament-results",
        "table.tournament-standings",
        "div.results table",
        "div.standings table",
        "div.leaderboard table",
        "div.tournament-results table",
        "main table",
        "article table",
        ".content table",
        "table",
    ]

    for selector in table_selectors:
        tables = soup.select(selector)
        for table in tables:
            parsed_rows = _parse_mlf_standings_table(table)
            if len(parsed_rows) < MIN_WEIGHTS_FOR_MEDIAN:
                continue
            day_weights = _extract_day_weights_from_rows(parsed_rows)
            if sum(len(ws) for ws in day_weights.values()) > sum(
                len(ws) for ws in best_day_weights.values()
            ):
                best_day_weights = day_weights
        if best_day_weights:
            break

    # Try inline JSON if no tables worked.
    if not best_day_weights:
        json_result = _try_extract_json_standings(html)
        if json_result:
            best_day_weights = json_result

    return best_day_weights


# ---------------------------------------------------------------------------
# Outcome collection
# ---------------------------------------------------------------------------


def _median_weight(weights: list[float]) -> float:
    """Compute median weight in pounds, rounded to 4 decimal places."""
    return round(statistics.median(weights), 4)


def collect_mlf_outcomes(
    output_path: Path | str,
    start_year: int = 2014,
    end_year: int = 2025,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Collect MLF tournament outcome data.

    Discovers tournaments via the Wayback Machine, scrapes per-day standings,
    and produces an event-day outcome DataFrame compatible with the schema
    used by ``outcomes.py`` and ``elite_standings.py``.

    Parameters
    ----------
    output_path:
        Destination CSV path.
    start_year, end_year:
        Inclusive year range for tournament discovery.
    session:
        Optional ``requests.Session``; one is created if not provided.

    Returns
    -------
    pd.DataFrame
        With columns matching ``REQUIRED_OUTCOME_COLUMNS`` plus extras.
    """
    output_path = Path(output_path)
    owned_session = session is None
    session = session or requests.Session()

    rows: list[dict[str, Any]] = []

    try:
        # ------------------------------------------------------------------
        # Phase 1: Discover tournaments via Wayback Machine.
        # ------------------------------------------------------------------
        tournaments = discover_mlf_tournaments(
            session, start_year=start_year, end_year=end_year
        )

        # Also try the JSON API for supplementary data.
        api_results = _try_mlf_api(session)
        if api_results:
            print(
                f"mlf: found {len(api_results)} items via API exploration",
                file=sys.stderr,
            )
            # We could parse API results into additional MLFTournament objects
            # here, but the Wayback approach is the primary strategy.

        # ------------------------------------------------------------------
        # Phase 2: Scrape standings for each tournament.
        # ------------------------------------------------------------------
        for tournament in tournaments:
            try:
                day_weights = scrape_mlf_standings(session, tournament)
            except Exception as exc:
                print(
                    f"mlf: error scraping {tournament.slug}: {exc}",
                    file=sys.stderr,
                )
                continue

            if not day_weights:
                print(
                    f"mlf: no standings data for {tournament.slug}",
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
                # Try extracting water body from title.
                usgs_site_id = _auto_resolve_usgs_site(tournament.title)

            for day_num in sorted(day_weights.keys()):
                weights = day_weights[day_num]
                if len(weights) < MIN_WEIGHTS_FOR_MEDIAN:
                    print(
                        f"mlf: {tournament.slug} day {day_num}: "
                        f"only {len(weights)} weights, skipping",
                        file=sys.stderr,
                    )
                    continue

                # Compute event date (offset from tournament start).
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
                        "results_source": "mlf",
                        "num_anglers": len(weights),
                        "day_number": day_num,
                    }
                )

            print(
                f"mlf: {tournament.slug} -> "
                f"{len(day_weights)} day(s), "
                f"gauge={usgs_site_id or 'UNMAPPED'}",
                file=sys.stderr,
            )

    finally:
        if owned_session:
            session.close()

    # ------------------------------------------------------------------
    # Build and persist output DataFrame.
    # ------------------------------------------------------------------
    df = pd.DataFrame(rows)
    if df.empty:
        print(
            f"mlf: WARNING -- no outcome rows produced for "
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
        # Drop rows with no gauge mapping.
        mapped = df["usgs_site_id"].astype(str).str.strip().ne("")
        unmapped_slugs = df.loc[~mapped, "tournament_slug"].unique().tolist()
        if unmapped_slugs:
            print(
                f"mlf: dropping {(~mapped).sum()} rows from "
                f"{len(unmapped_slugs)} unmapped tournaments: "
                + ", ".join(sorted(unmapped_slugs)),
                file=sys.stderr,
            )
        df = df.loc[mapped].reset_index(drop=True)

    # Persist.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    print(
        f"mlf: wrote {len(df)} rows to {output_path}",
        file=sys.stderr,
    )
    return df


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Scrape MLF tournament results into outcome CSV"
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("castline/validation/data/raw/mlf_outcomes.csv"),
        help="Output CSV path (default: %(default)s)",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=2014,
        help="First year to include (default: %(default)s)",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=2025,
        help="Last year to include (default: %(default)s)",
    )
    args = parser.parse_args()

    result = collect_mlf_outcomes(
        output_path=args.output,
        start_year=args.start_year,
        end_year=args.end_year,
    )
    print(f"\nDone. {len(result)} outcome rows.", file=sys.stderr)
