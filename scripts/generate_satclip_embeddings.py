#!/usr/bin/env python3
"""Generate SatCLIP location embeddings for CASTLINE tournament locations.

Uses the lightweight SatCLIP loader to avoid heavy dependencies.
Requires: torch, einops, huggingface_hub, sklearn, pandas, numpy
SatCLIP repo must be cloned at /tmp/satclip (for location_encoder module).

Usage::

    python3 scripts/generate_satclip_embeddings.py

Output:
    castline/validation/data/raw/location_embeddings_satclip.csv      (256d)
    castline/validation/data/raw/location_embeddings_satclip_pca32.csv
    castline/validation/data/raw/location_embeddings_satclip_pca64.csv
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch

# Add SatCLIP source to path for location_encoder module
_SATCLIP_SRC = Path("/tmp/satclip/satclip")
if not _SATCLIP_SRC.exists():
    print(
        "ERROR: SatCLIP repo not found at /tmp/satclip.\n"
        "Clone it with: git clone https://github.com/microsoft/satclip.git /tmp/satclip",
        file=sys.stderr,
    )
    sys.exit(1)
sys.path.insert(0, str(_SATCLIP_SRC))

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATASET_PATH = (
    _PROJECT_ROOT
    / "castline"
    / "validation"
    / "data"
    / "assembled"
    / "validation_dataset_v15.csv"
)

OUTPUT_DIR = _PROJECT_ROOT / "castline" / "validation" / "data" / "raw"


def load_satclip_model(
    model_name: str = "microsoft/SatCLIP-ResNet18-L40",
    device: str = "cpu",
):
    """Load SatCLIP location encoder using lightweight approach."""
    from huggingface_hub import hf_hub_download
    from location_encoder import (
        LocationEncoder,
        get_neural_network,
        get_positional_encoding,
    )

    ckpt_name = model_name.split("/")[-1].lower() + ".ckpt"
    print(f"satclip: downloading {model_name}...", file=sys.stderr)
    ckpt_path = hf_hub_download(model_name, ckpt_name)

    print("satclip: loading location encoder...", file=sys.stderr)
    ckpt = torch.load(ckpt_path, map_location=device, weights_only=False)
    hp = ckpt["hyper_parameters"]

    posenc = get_positional_encoding(
        hp["le_type"],
        hp["legendre_polys"],
        hp["harmonics_calculation"],
        hp["min_radius"],
        hp["max_radius"],
        hp["frequency_num"],
    )
    nnet = get_neural_network(
        hp["pe_type"],
        posenc.embedding_dim,
        hp["embed_dim"],
        hp["capacity"],
        hp["num_hidden_layers"],
    )

    state_dict = ckpt["state_dict"]
    state_dict = {
        k[k.index("nnet"):]: state_dict[k]
        for k in state_dict.keys()
        if "nnet" in k
    }

    loc_encoder = LocationEncoder(posenc, nnet).double()
    loc_encoder.load_state_dict(state_dict)
    loc_encoder.eval()

    return loc_encoder


def generate_satclip_embeddings(
    model,
    lats: np.ndarray,
    lons: np.ndarray,
    batch_size: int = 128,
) -> np.ndarray:
    """Generate SatCLIP embeddings for (lat, lon) pairs.

    SatCLIP expects (lon, lat) order with float64 precision.
    Returns array of shape (N, 256).
    """
    n = len(lats)
    all_embeddings = []

    for start in range(0, n, batch_size):
        end = min(start + batch_size, n)
        # SatCLIP expects (lon, lat) order
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
        f"satclip: done — {embeddings.shape[0]} locations x "
        f"{embeddings.shape[1]} dims",
        file=sys.stderr,
    )
    return embeddings


def reduce_embeddings_pca(
    embeddings: np.ndarray,
    n_components: int = 32,
    prefix: str = "satclip",
) -> pd.DataFrame:
    """Reduce embedding dimensions via PCA."""
    from sklearn.decomposition import PCA

    pca = PCA(n_components=n_components)
    reduced = pca.fit_transform(embeddings)

    total_var = pca.explained_variance_ratio_.sum()
    print(
        f"pca: {embeddings.shape[1]}d -> {n_components}d, "
        f"variance explained: {total_var:.3f}",
        file=sys.stderr,
    )

    cols = {f"{prefix}_{i}": reduced[:, i] for i in range(n_components)}
    return pd.DataFrame(cols)


def main() -> None:
    print("=" * 60, file=sys.stderr)
    print("SatCLIP Location Embedding Generator", file=sys.stderr)
    print(f"Dataset: {DATASET_PATH}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    # Load dataset and get unique locations
    df = pd.read_csv(DATASET_PATH)
    print(f"Dataset: {len(df)} rows", file=sys.stderr)

    loc_df = df[["lat", "lon"]].drop_duplicates().reset_index(drop=True)
    print(f"Unique locations: {len(loc_df)}", file=sys.stderr)

    lats = loc_df["lat"].values
    lons = loc_df["lon"].values

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Load model
    model = load_satclip_model()

    # Generate embeddings
    print("\n--- SatCLIP (256d) ---", file=sys.stderr)
    embeddings = generate_satclip_embeddings(model, lats, lons)

    # Save full 256d embeddings
    emb_cols = {f"satclip_{i}": embeddings[:, i] for i in range(embeddings.shape[1])}
    full_df = pd.concat([loc_df, pd.DataFrame(emb_cols)], axis=1)
    full_path = OUTPUT_DIR / "location_embeddings_satclip.csv"
    full_df.to_csv(full_path, index=False)
    print(f"satclip: saved full embeddings -> {full_path}", file=sys.stderr)

    # PCA-32
    pca32 = reduce_embeddings_pca(embeddings, n_components=32, prefix="satclip")
    pca32_df = pd.concat([loc_df, pca32], axis=1)
    pca32_path = OUTPUT_DIR / "location_embeddings_satclip_pca32.csv"
    pca32_df.to_csv(pca32_path, index=False)
    print(f"satclip: saved PCA-32 embeddings -> {pca32_path}", file=sys.stderr)

    # PCA-64
    pca64 = reduce_embeddings_pca(embeddings, n_components=64, prefix="satclip")
    pca64_df = pd.concat([loc_df, pca64], axis=1)
    pca64_path = OUTPUT_DIR / "location_embeddings_satclip_pca64.csv"
    pca64_df.to_csv(pca64_path, index=False)
    print(f"satclip: saved PCA-64 embeddings -> {pca64_path}", file=sys.stderr)

    # Summary
    print("\n" + "=" * 60, file=sys.stderr)
    print("Summary", file=sys.stderr)
    print(f"  Locations embedded: {len(loc_df)}", file=sys.stderr)
    print(f"  SatCLIP: 256d full, PCA-32, PCA-64", file=sys.stderr)
    print(f"  Output dir: {OUTPUT_DIR}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)


if __name__ == "__main__":
    main()
