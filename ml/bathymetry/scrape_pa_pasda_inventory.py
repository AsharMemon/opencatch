#!/usr/bin/env python3
"""
Download the official PASDA / PFBC statewide lakes dataset bundle.

This is not bathymetry-grade contour geometry, but it is a strong statewide
official lake inventory we can use to improve Pennsylvania beyond generic
fallback routing and to join future contour/depth assets.
"""

from __future__ import annotations

from pathlib import Path
from urllib.request import Request, urlopen
import zipfile

URL = "https://www.pasda.psu.edu/download/pafish/Lakes_PFBCDatabase202411.zip"
OUT_DIR = Path("/Users/Ashar/Documents/fish/data/bathymetry/pa")
OUT_ZIP = OUT_DIR / "Lakes_PFBCDatabase202411.zip"
OUT_MANIFEST = OUT_DIR / "pa_pasda_inventory.txt"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    req = Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    raw = urlopen(req, timeout=120).read()
    OUT_ZIP.write_bytes(raw)

    with zipfile.ZipFile(OUT_ZIP) as zf:
        names = zf.namelist()

    OUT_MANIFEST.write_text("\n".join(names) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_ZIP} ({OUT_ZIP.stat().st_size} bytes)")
    print(f"Wrote {OUT_MANIFEST} ({len(names)} files listed)")


if __name__ == "__main__":
    main()
