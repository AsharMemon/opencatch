#!/usr/bin/env python3
"""
Build a Georgia DNR Public Fishing Area PDF inventory.

Georgia does not currently expose one clean statewide bathymetry service, but a
repeatable subset of official PFA fishing-guide PDFs exists. These guides often
contain depth/contour context and are the right bridge from "partial official
lead" to an executable PDF-upgrade lane.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Iterable

import requests

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover - fallback is fine
    PdfReader = None


USER_AGENT = "Mozilla/5.0 (compatible; OpenCatch/1.0; +https://opencatch.app)"
REGS_URL = "https://gadnr.org/sites/default/files/wrd/pdf/regulations/GA%20Hunting%20%26%20Fishing%20Popular%20Guide%202024-25.pdf"
PFA_LANDING_URL = "https://gadnr.org/pfa"
PDF_BASE = "https://gadnr.org/sites/default/files/wrd/pdf/pfa"
OUT_DIR = Path("/Users/Ashar/Documents/fish/data/bathymetry/ga")
OUT_CSV = OUT_DIR / "ga_pfa_pdf_inventory.csv"

HARDCODED_PFAS = [
    "Dodge County PFA",
    "Evans County PFA",
    "Flat Creek PFA",
    "Hugh Gillis PFA",
    "McDuffie PFA",
    "Ocmulgee PFA",
    "Paradise PFA",
    "Rocky Mountain PFA",
]


def fetch_bytes(url: str) -> bytes:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=120)
    resp.raise_for_status()
    return resp.content


def title_case_slug(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", " ", text).strip()
    return "".join(part.capitalize() for part in cleaned.split())


def discover_pfas_from_regulations(pdf_bytes: bytes) -> list[str]:
    if PdfReader is None:
        return HARDCODED_PFAS[:]

    reader = PdfReader(io.BytesIO(pdf_bytes))
    text = "\n".join(page.extract_text() or "" for page in reader.pages[:80])

    matches = re.findall(r"\b([A-Z][A-Za-z&.' -]{2,80} PFA)\b", text)
    cleaned: list[str] = []
    seen: set[str] = set()
    for match in matches + HARDCODED_PFAS:
        name = " ".join(match.replace("McDUFFIE", "McDuffie").split())
        if not name.endswith("PFA"):
            continue
        lower = name.lower()
        if any(bad in lower for bad in ("wma or pfa", "entering a wma", "flat creek pfa q3", "mcduffie pfa q25")):
            continue
        if lower not in seen:
            seen.add(lower)
            cleaned.append(name)
    return cleaned


def candidate_urls(name: str) -> Iterable[str]:
    stem = name.replace(" PFA", "")
    variants = {
        title_case_slug(name),
        title_case_slug(stem) + "PFA",
        title_case_slug(stem.replace("&", "And")) + "PFA",
        title_case_slug(stem.replace(".", "")) + "PFA",
    }
    for variant in sorted(variants):
        yield f"{PDF_BASE}/{variant}_FishingGuide.pdf"


def probe_pdf(session: requests.Session, url: str) -> bool:
    try:
        resp = session.head(url, headers={"User-Agent": USER_AGENT}, timeout=30, allow_redirects=True)
        content_type = (resp.headers.get("content-type") or "").lower()
        return resp.status_code == 200 and "pdf" in content_type
    except Exception:
        return False


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pdf_bytes = fetch_bytes(REGS_URL)
    pfas = discover_pfas_from_regulations(pdf_bytes)

    records = []
    session = requests.Session()
    for name in pfas:
        hit_url = None
        for url in candidate_urls(name):
            if probe_pdf(session, url):
                hit_url = url
                break
        records.append(
            {
                "lake_name": name.replace(" PFA", "").strip(),
                "waterbody": name,
                "pdf_url": hit_url or "",
                "source_page": PFA_LANDING_URL,
                "evidence_url": REGS_URL,
                "report_type": "Georgia DNR PFA Fishing Guide",
                "jurisdiction": "ga",
                "status": "verified_pdf" if hit_url else "unresolved_candidate",
            }
        )

    with OUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "lake_name",
                "waterbody",
                "pdf_url",
                "source_page",
                "evidence_url",
                "report_type",
                "jurisdiction",
                "status",
            ],
        )
        writer.writeheader()
        writer.writerows(records)

    verified = sum(1 for row in records if row["status"] == "verified_pdf")
    unresolved = len(records) - verified
    print(f"Wrote {OUT_CSV} ({len(records)} candidate PFAs)")
    print(f"Verified PDFs: {verified}")
    print(f"Unresolved candidates: {unresolved}")


if __name__ == "__main__":
    main()
