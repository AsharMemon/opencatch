#!/usr/bin/env python3
"""
Validate that the U.S. / Canada bathymetry coverage matrix explicitly tracks
all 50 U.S. states and all 13 Canadian provinces / territories.

Usage:
  python3 ml/bathymetry/audit_coverage_matrix.py
"""

from __future__ import annotations

from pathlib import Path
import sys


MATRIX_PATH = Path("/Users/Ashar/Documents/fish/docs/us-canada-bathymetry-coverage-matrix.md")

US_STATES = {
    "Alabama",
    "Alaska",
    "Arizona",
    "Arkansas",
    "California",
    "Colorado",
    "Connecticut",
    "Delaware",
    "Florida",
    "Georgia",
    "Hawaii",
    "Idaho",
    "Illinois",
    "Indiana",
    "Iowa",
    "Kansas",
    "Kentucky",
    "Louisiana",
    "Maine",
    "Maryland",
    "Massachusetts",
    "Michigan",
    "Minnesota",
    "Mississippi",
    "Missouri",
    "Montana",
    "Nebraska",
    "Nevada",
    "New Hampshire",
    "New Jersey",
    "New Mexico",
    "New York",
    "North Carolina",
    "North Dakota",
    "Ohio",
    "Oklahoma",
    "Oregon",
    "Pennsylvania",
    "Rhode Island",
    "South Carolina",
    "South Dakota",
    "Tennessee",
    "Texas",
    "Utah",
    "Vermont",
    "Virginia",
    "Washington",
    "West Virginia",
    "Wisconsin",
    "Wyoming",
}

CANADA_JURISDICTIONS = {
    "Alberta",
    "British Columbia",
    "Manitoba",
    "New Brunswick",
    "Newfoundland & Labrador",
    "Northwest Territories",
    "Nova Scotia",
    "Nunavut",
    "Ontario",
    "Prince Edward Island",
    "Quebec",
    "Saskatchewan",
    "Yukon",
}


def parse_matrix(path: Path) -> tuple[list[str], list[str]]:
    text = path.read_text().splitlines()
    section = None
    us: list[str] = []
    ca: list[str] = []

    for line in text:
        if line.startswith("## United States"):
            section = "us"
            continue
        if line.startswith("## Canada"):
            section = "ca"
            continue
        if not line.startswith("| ") or "---" in line:
            continue

        parts = [p.strip() for p in line.strip("|").split("|")]
        if len(parts) != 4:
            continue
        name = parts[0]
        if name in {"State", "Province / Territory"}:
            continue

        if section == "us":
            us.append(name)
        elif section == "ca":
            ca.append(name)

    return us, ca


def main() -> int:
    us, ca = parse_matrix(MATRIX_PATH)
    us_set = set(us)
    ca_set = set(ca)

    missing_us = sorted(US_STATES - us_set)
    extra_us = sorted(us_set - US_STATES)
    missing_ca = sorted(CANADA_JURISDICTIONS - ca_set)
    extra_ca = sorted(ca_set - CANADA_JURISDICTIONS)

    print(f"US rows: {len(us)}")
    print(f"Canada rows: {len(ca)}")
    print(f"Total rows: {len(us) + len(ca)}")

    if missing_us or extra_us or missing_ca or extra_ca:
        if missing_us:
            print("Missing US:", ", ".join(missing_us))
        if extra_us:
            print("Unexpected US:", ", ".join(extra_us))
        if missing_ca:
            print("Missing Canada:", ", ".join(missing_ca))
        if extra_ca:
            print("Unexpected Canada:", ", ".join(extra_ca))
        return 1

    print("Coverage matrix audit passed: all 50 states and all 13 provinces/territories are represented.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
