#!/usr/bin/env python3
"""Generate location embeddings using GeoCLIP and/or SatCLIP.

Creates dense vector representations of geographic locations from
pretrained models trained on satellite/street imagery. These embeddings
capture land cover, terrain, water body characteristics, vegetation,
urbanization, and climate patterns — all relevant to fish habitat.

Usage::

    python3 scripts/generate_location_embeddings.py

Output:
    castline/validation/data/raw/location_embeddings_geoclip.csv
    castline/validation/data/raw/location_embeddings_satclip.csv  (if available)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

DATASET_PATH = (
    _PROJECT_ROOT
    / "castline"
    / "validation"
    / "data"
    / "assembled"
    / "validation_dataset_v15.csv"
)

OUTPUT_DIR = _PROJECT_ROOT / "castline" / "validation" / "data" / "raw"


# ---------------------------------------------------------------------------
# GeoCLIP embeddings
# ---------------------------------------------------------------------------


def generate_geoclip_embeddings(
    lats: np.ndarray,
    lons: np.ndarray,
    batch_size: int = 128,
) -> np.ndarray:
    """Generate GeoCLIP embeddings for an array of (lat, lon) pairs.

    GeoCLIP uses (lat, lon) order.
    Returns array of shape (N, 512).
    """
    from geoclip import LocationEncoder

    print("geoclip: loading pretrained LocationEncoder...", file=sys.stderr)
    encoder = LocationEncoder()
    encoder.eval()

    n = len(lats)
    all_embeddings = []

    for start in range(0, n, batch_size):
        end = min(start + batch_size, n)
        batch_coords = torch.tensor(
            np.column_stack([lats[start:end], lons[start:end]]),
            dtype=torch.float32,
        )

        with torch.no_grad():
            emb = encoder(batch_coords)  # (batch, 512)

        all_embeddings.append(emb.cpu().numpy())

        if (start // batch_size) % 5 == 0:
            print(
                f"geoclip: {end}/{n} locations embedded",
                file=sys.stderr,
            )

    embeddings = np.vstack(all_embeddings)
    print(
        f"geoclip: done — {embeddings.shape[0]} locations × "
        f"{embeddings.shape[1]} dims",
        file=sys.stderr,
    )
    return embeddings


# ---------------------------------------------------------------------------
# SatCLIP embeddings (optional, requires separate install)
# ---------------------------------------------------------------------------


def generate_satclip_embeddings(
    lats: np.ndarray,
    lons: np.ndarray,
    batch_size: int = 128,
    model_name: str = "microsoft/SatCLIP-ResNet18-L40",
) -> np.ndarray | None:
    """Generate SatCLIP embeddings for an array of (lat, lon) pairs.

    SatCLIP uses (lon, lat) order.
    Returns array of shape (N, 256) or None if SatCLIP is not installed.
    """
    try:
        from huggingface_hub import hf_hub_download
        from satclip.load import get_satclip
    except ImportError:
        print(
            "satclip: not installed — skipping. Install with: "
            "pip install git+https://github.com/microsoft/satclip.git",
            file=sys.stderr,
        )
        return None

    ckpt_name = model_name.split("/")[-1].lower() + ".ckpt"
    print(f"satclip: downloading {model_name}...", file=sys.stderr)
    ckpt_path = hf_hub_download(model_name, ckpt_name)

    print("satclip: loading location encoder...", file=sys.stderr)
    model = get_satclip(ckpt_path, device="cpu")
    model.eval()

    n = len(lats)
    all_embeddings = []

    for start in range(0, n, batch_size):
        end = min(start + batch_size, n)
        # SatCLIP expects (lon, lat) order with float64
        batch_coords = torch.tensor(
            np.column_stack([lons[start:end], lats[start:end]]),
            dtype=torch.float64,
        )

        with torch.no_grad():
            emb = model(batch_coords)  # (batch, 256)

        all_embeddings.append(emb.cpu().float().numpy())

        if (start // batch_size) % 5 == 0:
            print(
                f"satclip: {end}/{n} locations embedded",
                file=sys.stderr,
            )

    embeddings = np.vstack(all_embeddings)
    print(
        f"satclip: done — {embeddings.shape[0]} locations × "
        f"{embeddings.shape[1]} dims",
        file=sys.stderr,
    )
    return embeddings


# ---------------------------------------------------------------------------
# PCA reduction
# ---------------------------------------------------------------------------


def reduce_embeddings_pca(
    embeddings: np.ndarray,
    n_components: int = 32,
    prefix: str = "emb",
) -> pd.DataFrame:
    """Reduce embedding dimensions via PCA.

    Returns DataFrame with columns like emb_0, emb_1, ..., emb_N
    plus variance_explained column.
    """
    from sklearn.decomposition import PCA

    pca = PCA(n_components=n_components)
    reduced = pca.fit_transform(embeddings)

    total_var = pca.explained_variance_ratio_.sum()
    print(
        f"pca: {embeddings.shape[1]}d → {n_components}d, "
        f"variance explained: {total_var:.3f}",
        file=sys.stderr,
    )

    cols = {f"{prefix}_{i}": reduced[:, i] for i in range(n_components)}
    return pd.DataFrame(cols)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    print("=" * 60, file=sys.stderr)
    print("Location Embedding Generator", file=sys.stderr)
    print(f"Dataset: {DATASET_PATH}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    # Load dataset and get unique locations
    df = pd.read_csv(DATASET_PATH)
    print(f"Dataset: {len(df)} rows", file=sys.stderr)

    # Get unique locations
    loc_df = df[["lat", "lon"]].drop_duplicates().reset_index(drop=True)
    print(f"Unique locations: {len(loc_df)}", file=sys.stderr)

    lats = loc_df["lat"].values
    lons = loc_df["lon"].values

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # --- GeoCLIP ---
    print("\n--- GeoCLIP (512d) ---", file=sys.stderr)
    geoclip_emb = generate_geoclip_embeddings(lats, lons)

    # Save full embeddings
    geoclip_full_df = loc_df.copy()
    for i in range(geoclip_emb.shape[1]):
        geoclip_full_df[f"geoclip_{i}"] = geoclip_emb[:, i]
    full_path = OUTPUT_DIR / "location_embeddings_geoclip.csv"
    geoclip_full_df.to_csv(full_path, index=False)
    print(f"geoclip: saved full embeddings → {full_path}", file=sys.stderr)

    # PCA reduce to 32 dims for CatBoost
    geoclip_pca = reduce_embeddings_pca(geoclip_emb, n_components=32, prefix="geoclip")
    geoclip_pca_df = pd.concat([loc_df, geoclip_pca], axis=1)
    pca_path = OUTPUT_DIR / "location_embeddings_geoclip_pca32.csv"
    geoclip_pca_df.to_csv(pca_path, index=False)
    print(f"geoclip: saved PCA-32 embeddings → {pca_path}", file=sys.stderr)

    # Also try PCA-64 for more expressiveness
    geoclip_pca64 = reduce_embeddings_pca(geoclip_emb, n_components=64, prefix="geoclip")
    geoclip_pca64_df = pd.concat([loc_df, geoclip_pca64], axis=1)
    pca64_path = OUTPUT_DIR / "location_embeddings_geoclip_pca64.csv"
    geoclip_pca64_df.to_csv(pca64_path, index=False)
    print(f"geoclip: saved PCA-64 embeddings → {pca64_path}", file=sys.stderr)

    # --- SatCLIP (optional) ---
    print("\n--- SatCLIP (256d) ---", file=sys.stderr)
    satclip_emb = generate_satclip_embeddings(lats, lons)

    if satclip_emb is not None:
        satclip_full_df = loc_df.copy()
        for i in range(satclip_emb.shape[1]):
            satclip_full_df[f"satclip_{i}"] = satclip_emb[:, i]
        sat_path = OUTPUT_DIR / "location_embeddings_satclip.csv"
        satclip_full_df.to_csv(sat_path, index=False)
        print(f"satclip: saved full embeddings → {sat_path}", file=sys.stderr)

        satclip_pca = reduce_embeddings_pca(satclip_emb, n_components=32, prefix="satclip")
        satclip_pca_df = pd.concat([loc_df, satclip_pca], axis=1)
        sat_pca_path = OUTPUT_DIR / "location_embeddings_satclip_pca32.csv"
        satclip_pca_df.to_csv(sat_pca_path, index=False)
        print(f"satclip: saved PCA-32 embeddings → {sat_pca_path}", file=sys.stderr)

    # --- Summary ---
    print("\n" + "=" * 60, file=sys.stderr)
    print("Summary", file=sys.stderr)
    print(f"  Locations embedded: {len(loc_df)}", file=sys.stderr)
    print(f"  GeoCLIP: 512d → PCA-32, PCA-64", file=sys.stderr)
    if satclip_emb is not None:
        print(f"  SatCLIP: 256d → PCA-32", file=sys.stderr)
    print("=" * 60, file=sys.stderr)


if __name__ == "__main__":
    main()
