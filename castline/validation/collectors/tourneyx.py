"""TourneyX tournament results collector.

TourneyX is a tournament management platform with a public JSON API.
Endpoints:
  - Setup: https://live.tourneyx.com/php/setup.php?id={id}
  - Results: https://live.tourneyx.com/php/index.php?id={id}

Tournament IDs are sequential integers. We scan a range, filter for
bass-species tournaments, and extract angler catch data.

Most TourneyX tournaments are kayak/club events using length measurement.
We store raw measurement and convert to approximate weight using
standard length-weight relationships for largemouth bass.
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from castline.validation.collectors.outcomes import (
    _auto_resolve_usgs_site,
    _seasonal_baseline_signal,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TOURNEYX_SETUP_URL = "https://live.tourneyx.com/php/setup.php"
TOURNEYX_RESULTS_URL = "https://live.tourneyx.com/php/index.php"

REQUEST_DELAY = 0.15  # TourneyX seems tolerant of moderate request rates
BATCH_SIZE = 50  # Process IDs in batches

# Keywords that indicate a bass tournament
BASS_KEYWORDS = [
    "bass", "lmb", "smb", "largemouth", "smallmouth", "spotted bass",
    "black bass", "bassin", "hawg", "lunker",
]

# Keywords that indicate NOT a bass tournament
EXCLUDE_KEYWORDS = [
    "sea bass", "striped bass", "striper", "hybrid bass", "white bass",
    "catfish", "walleye", "pike", "musky", "trout", "salmon", "crappie",
    "panfish", "redfish", "snook", "tarpon", "inshore", "offshore",
    "saltwater", "surf",
]

# Standard largemouth bass length-weight relationship
# W(g) = a * L(cm)^b  (Anderson & Neumann 1996)
# Using metric coefficients and converting from inches input
LMB_A = 0.0126  # grams
LMB_B = 3.02    # exponent
IN_TO_CM = 2.54
G_TO_LB = 1.0 / 453.592


def length_to_weight_lb(length_inches: float) -> float:
    """Convert bass length (inches) to approximate weight (lbs)."""
    if length_inches <= 0:
        return 0.0
    length_cm = length_inches * IN_TO_CM
    weight_g = LMB_A * (length_cm ** LMB_B)
    return weight_g * G_TO_LB


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------


def fetch_tournament_setup(
    tournament_id: int,
    session: requests.Session,
    timeout: int = 15,
) -> dict[str, Any] | None:
    """Fetch tournament metadata."""
    try:
        resp = session.get(
            TOURNEYX_SETUP_URL,
            params={"id": tournament_id},
            timeout=timeout,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not data or not data.get("title"):
            return None
        return data
    except Exception:
        return None


def fetch_tournament_results(
    tournament_id: int,
    session: requests.Session,
    timeout: int = 15,
) -> list[dict[str, Any]] | dict[str, Any] | None:
    """Fetch tournament results/leaderboard."""
    try:
        resp = session.get(
            TOURNEYX_RESULTS_URL,
            params={"id": tournament_id},
            timeout=timeout,
        )
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception:
        return None


def _is_bass_tournament(title: str) -> bool:
    """Check if tournament title suggests a bass tournament."""
    title_lower = title.lower()
    # Check exclusions first
    for kw in EXCLUDE_KEYWORDS:
        if kw in title_lower:
            return False
    # Check for bass keywords
    for kw in BASS_KEYWORDS:
        if kw in title_lower:
            return True
    return False


def _extract_weights_from_results(
    results: list[dict[str, Any]] | dict[str, Any],
    measurement_type: str = "length",
) -> list[float]:
    """Extract per-angler total weights from results data.

    For length-based tournaments, converts to approximate weight using
    standard length-weight relationships.
    """
    weights: list[float] = []

    # Handle team format
    if isinstance(results, dict):
        team_lb = results.get("team_leaderboard", [])
        if isinstance(team_lb, list):
            for team in team_lb:
                anglers = team.get("angler_list", [])
                for angler in anglers:
                    total = _safe_float(angler.get("angler_total", 0))
                    if total and total > 0:
                        if measurement_type == "length":
                            weights.append(length_to_weight_lb(total))
                        else:
                            weights.append(total)
        return weights

    # Handle individual format (list of anglers)
    if not isinstance(results, list):
        return weights

    for angler in results:
        if measurement_type == "weight":
            # Weight-based: use total_fish_weight or total directly
            total = _safe_float(angler.get("total_fish_weight"))
            if total is None:
                total = _safe_float(angler.get("total_fish_length"))
            if total and total > 0:
                weights.append(total)
        else:
            # Length-based: convert total length to approximate weight
            total_length = _safe_float(angler.get("total_fish_length", 0))
            fish_caught = _safe_int(angler.get("fish_caught", 0))
            if total_length and total_length > 0 and fish_caught and fish_caught > 0:
                avg_length = total_length / fish_caught
                # Convert each fish's approximate weight and sum
                total_weight = fish_caught * length_to_weight_lb(avg_length)
                weights.append(total_weight)

    return weights


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        return f if not math.isnan(f) else None
    except (ValueError, TypeError):
        return None


def _safe_int(val: Any) -> int | None:
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Main collector
# ---------------------------------------------------------------------------


def collect_tourneyx_outcomes(
    output_path: Path | str,
    id_start: int = 1,
    id_end: int = 26000,
    session: requests.Session | None = None,
    checkpoint_every: int = 500,
    min_anglers: int = 5,
) -> pd.DataFrame:
    """Scan TourneyX tournament IDs and collect bass tournament outcomes.

    Parameters
    ----------
    id_start, id_end : int
        Range of tournament IDs to scan.
    min_anglers : int
        Minimum number of anglers with valid weights to include.
    """
    output_path = Path(output_path)
    checkpoint_path = output_path.with_suffix(".checkpoint.csv")
    sess = session or requests.Session()
    rows: list[dict[str, Any]] = []

    # Resume from checkpoint
    processed_ids: set[int] = set()
    if checkpoint_path.exists():
        try:
            existing = pd.read_csv(checkpoint_path)
            rows = existing.to_dict("records")
            # Track all IDs we've already processed (not just those with results)
            processed_ids = set(existing.get("tournament_id", pd.Series(dtype=int)).unique())
            print(
                f"tourneyx: resuming from checkpoint — {len(rows)} rows",
                file=sys.stderr,
            )
        except Exception:
            pass

    # Also track IDs we scanned but had no results (stored separately)
    scanned_ids_path = output_path.with_suffix(".scanned_ids.txt")
    if scanned_ids_path.exists():
        try:
            with open(scanned_ids_path) as f:
                for line in f:
                    line = line.strip()
                    if line.isdigit():
                        processed_ids.add(int(line))
        except Exception:
            pass

    bass_found = 0
    scanned = 0
    errors = 0

    try:
        for tid in range(id_start, id_end + 1):
            if tid in processed_ids:
                continue

            scanned += 1
            time.sleep(REQUEST_DELAY)

            # Step 1: Check setup
            setup = fetch_tournament_setup(tid, sess)
            if setup is None:
                processed_ids.add(tid)
                continue

            title = setup.get("title", "")
            if not _is_bass_tournament(title):
                processed_ids.add(tid)
                continue

            # Step 2: Get results
            time.sleep(REQUEST_DELAY)
            results = fetch_tournament_results(tid, sess)
            if results is None:
                processed_ids.add(tid)
                continue

            # Determine measurement type
            mtype = "length"  # default
            if isinstance(results, list) and results:
                mtype = results[0].get("measurement_type", "length") or "length"
            elif isinstance(results, dict):
                team_lb = results.get("team_leaderboard", [])
                if team_lb:
                    mtype = team_lb[0].get("measurement_type", "length") or "length"

            # Extract weights
            weights = _extract_weights_from_results(results, mtype)
            weights = [w for w in weights if w > 0]

            if len(weights) < min_anglers:
                processed_ids.add(tid)
                continue

            bass_found += 1
            import statistics
            median_weight = round(statistics.median(weights), 4)

            # Parse date
            start_date = setup.get("start_date", "")
            try:
                event_date = pd.to_datetime(start_date).strftime("%Y-%m-%d")
            except Exception:
                event_date = ""

            city = setup.get("city", "")
            state = setup.get("state", "")
            location = f"{city}, {state}".strip(", ")

            # Try to resolve USGS gauge from title or location
            usgs_site_id = ""
            for text in [title, location]:
                usgs_site_id = _auto_resolve_usgs_site(text) or ""
                if usgs_site_id:
                    break

            rows.append({
                "event_id": f"tourneyx-{tid}",
                "tournament_slug": f"tourneyx-{tid}",
                "event_name": title,
                "date": event_date,
                "location": location,
                "species": "black_bass",
                "median_weight_lb": median_weight,
                "baseline_signal": _seasonal_baseline_signal(event_date) if event_date else 0.5,
                "usgs_site_id": usgs_site_id,
                "results_source": "tourneyx",
                "num_anglers": len(weights),
                "day_number": 1,
                "tms_id": None,
                "trail": "tourneyx_club",
                "tournament_id": tid,
                "measurement_type": mtype,
            })

            processed_ids.add(tid)

            print(
                f"tourneyx: [{scanned}] ID {tid}: {title} → "
                f"{len(weights)} anglers, median={median_weight:.2f}lb, "
                f"type={mtype}, gauge={usgs_site_id or 'UNMAPPED'}",
                file=sys.stderr,
            )

            # Checkpoint
            if bass_found % checkpoint_every == 0 and rows:
                _save_checkpoint(rows, checkpoint_path, processed_ids, scanned_ids_path)

    except KeyboardInterrupt:
        print("\ntourneyx: interrupted, saving checkpoint...", file=sys.stderr)
    finally:
        # Save final checkpoint
        if rows:
            _save_checkpoint(rows, checkpoint_path, processed_ids, scanned_ids_path)

    # Final save
    df = pd.DataFrame(rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)

    print(
        f"\ntourneyx: DONE — scanned {scanned} IDs, "
        f"found {bass_found} bass tournaments, {len(df)} rows\n"
        f"  Wrote to: {output_path}",
        file=sys.stderr,
    )

    # Clean up
    if checkpoint_path.exists():
        checkpoint_path.unlink()
    if scanned_ids_path.exists():
        scanned_ids_path.unlink()

    return df


def _save_checkpoint(
    rows: list[dict],
    checkpoint_path: Path,
    processed_ids: set[int],
    scanned_ids_path: Path,
) -> None:
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(checkpoint_path, index=False)
    with open(scanned_ids_path, "w") as f:
        for tid in sorted(processed_ids):
            f.write(f"{tid}\n")
    print(f"tourneyx: checkpoint saved ({len(rows)} rows, {len(processed_ids)} scanned)", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Collect TourneyX bass tournament results")
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=Path("castline/validation/data/raw/tourneyx_outcomes.csv"),
    )
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int, default=26000)
    parser.add_argument("--min-anglers", type=int, default=5)
    args = parser.parse_args()

    result = collect_tourneyx_outcomes(
        output_path=args.output,
        id_start=args.start,
        id_end=args.end,
        min_anglers=args.min_anglers,
    )
    print(f"\nDone. {len(result)} outcome rows.", file=sys.stderr)
