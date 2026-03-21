"""Spatial GNN for interpolating missing USGS gauge sensor values.

Many tournament lakes lack a nearby USGS gauge with full sensor coverage
(water temp, dissolved oxygen, pH, etc.).  This module learns to predict
missing sensor readings at a gauge by propagating information from
spatially-nearby gauges that *do* report those parameters.

Architecture
------------
* Nodes  – USGS gauge sites, each carrying its available sensor readings.
* Edges  – connect sites within a configurable radius (default 100 km),
           weighted by haversine distance and shared-watershed flags.
* Model  – two-layer GraphSAGE with skip (residual) connections, trained
           to reconstruct masked sensor values from their spatial context.

A lightweight inverse-distance-weighting (IDW) fallback is included so that
callers can always obtain an estimate, even when ``torch_geometric`` is not
installed.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Sensor columns that the graph operates over.  Order matters: it defines the
# position of each value in the node-feature vector.
# ---------------------------------------------------------------------------
SENSOR_COLUMNS: list[str] = [
    "water_temp_c",
    "discharge_cfs",
    "gage_height_ft",
    "specific_conductance_us_cm",
    "dissolved_oxygen_mgL",
    "ph",
    "turbidity_fnu",
    "reservoir_elevation_ft",
]

EARTH_RADIUS_KM = 6_371.0


# ---------------------------------------------------------------------------
# Haversine distance
# ---------------------------------------------------------------------------

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance in kilometres between two points."""
    lat1_r, lon1_r = math.radians(lat1), math.radians(lon1)
    lat2_r, lon2_r = math.radians(lat2), math.radians(lon2)
    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Graph data container (framework-agnostic)
# ---------------------------------------------------------------------------

@dataclass
class GaugeGraph:
    """Lightweight representation of the spatial gauge graph.

    Fields
    ------
    site_ids : list[str]
        Ordered site identifiers; index *i* corresponds to node *i*.
    node_features : np.ndarray
        Shape ``(N, F)`` where *F* = ``len(SENSOR_COLUMNS)``.
        NaN indicates a missing sensor at that node.
    node_mask : np.ndarray
        Boolean array ``(N, F)``; True where the sensor value is *known*.
    edge_index : np.ndarray
        Shape ``(2, E)`` — COO source/target pairs.
    edge_attr : np.ndarray
        Shape ``(E, 2)`` — ``[distance_km, same_watershed]`` per edge.
    lats : np.ndarray
        Latitude per node.
    lons : np.ndarray
        Longitude per node.
    huc_codes : list[str]
        HUC code (or empty string) per node, used for watershed matching.
    """

    site_ids: list[str]
    node_features: np.ndarray
    node_mask: np.ndarray
    edge_index: np.ndarray
    edge_attr: np.ndarray
    lats: np.ndarray
    lons: np.ndarray
    huc_codes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_gauge_graph(
    sites_df: pd.DataFrame,
    *,
    radius_km: float = 100.0,
    huc_prefix_len: int = 4,
) -> GaugeGraph:
    """Build a spatial graph from a DataFrame of USGS gauge sites.

    Parameters
    ----------
    sites_df : pd.DataFrame
        Must contain columns ``site_id``, ``latitude``, ``longitude``.
        May contain any of the ``SENSOR_COLUMNS`` and an optional ``huc_code``
        column (string HUC identifier for watershed matching).
    radius_km : float
        Maximum distance (km) for connecting two sites with an edge.
    huc_prefix_len : int
        Number of leading HUC digits used to determine whether two gauges
        share the same watershed.  4 digits = HUC-4 sub-basin level.

    Returns
    -------
    GaugeGraph
    """
    required = {"site_id", "latitude", "longitude"}
    missing = required - set(sites_df.columns)
    if missing:
        raise ValueError(f"sites_df missing required columns: {sorted(missing)}")

    sites_df = sites_df.reset_index(drop=True)
    n = len(sites_df)

    site_ids: list[str] = sites_df["site_id"].astype(str).tolist()
    lats = sites_df["latitude"].to_numpy(dtype=np.float64)
    lons = sites_df["longitude"].to_numpy(dtype=np.float64)

    # HUC codes (optional)
    if "huc_code" in sites_df.columns:
        huc_codes = sites_df["huc_code"].fillna("").astype(str).tolist()
    else:
        huc_codes = [""] * n

    # Node features: fill present sensors, leave NaN for missing ones.
    node_features = np.full((n, len(SENSOR_COLUMNS)), np.nan, dtype=np.float64)
    for col_idx, col_name in enumerate(SENSOR_COLUMNS):
        if col_name in sites_df.columns:
            vals = pd.to_numeric(sites_df[col_name], errors="coerce").to_numpy(dtype=np.float64)
            node_features[:, col_idx] = vals

    node_mask = ~np.isnan(node_features)

    # Build edges within *radius_km*.
    src_list: list[int] = []
    dst_list: list[int] = []
    dist_list: list[float] = []
    same_ws_list: list[float] = []

    for i in range(n):
        for j in range(i + 1, n):
            d = haversine_km(lats[i], lons[i], lats[j], lons[j])
            if d <= radius_km:
                # Undirected: add both directions.
                src_list.extend([i, j])
                dst_list.extend([j, i])

                huc_i = huc_codes[i][:huc_prefix_len]
                huc_j = huc_codes[j][:huc_prefix_len]
                same_ws = 1.0 if (huc_i and huc_j and huc_i == huc_j) else 0.0

                dist_list.extend([d, d])
                same_ws_list.extend([same_ws, same_ws])

    if src_list:
        edge_index = np.array([src_list, dst_list], dtype=np.int64)
        edge_attr = np.column_stack([dist_list, same_ws_list]).astype(np.float64)
    else:
        edge_index = np.zeros((2, 0), dtype=np.int64)
        edge_attr = np.zeros((0, 2), dtype=np.float64)

    return GaugeGraph(
        site_ids=site_ids,
        node_features=node_features,
        node_mask=node_mask,
        edge_index=edge_index,
        edge_attr=edge_attr,
        lats=lats,
        lons=lons,
        huc_codes=huc_codes,
    )


# ===================================================================
# PyTorch Geometric GNN model
# ===================================================================

_HAS_PYG = False
try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    from torch_geometric.data import Data
    from torch_geometric.nn import SAGEConv

    _HAS_PYG = True
except ImportError:
    pass


if _HAS_PYG:

    class SpatialInterpolationGNN(nn.Module):
        """Two-layer GraphSAGE with skip connections for sensor interpolation.

        The model takes a node-feature matrix where missing values have been
        zero-filled (with a parallel mask indicating which entries are known)
        and predicts the full sensor vector for every node.  The training
        objective masks out a random subset of *known* values and asks the
        network to reconstruct them from their spatial neighbours.

        Parameters
        ----------
        in_channels : int
            Number of input features per node.  Defaults to
            ``2 * len(SENSOR_COLUMNS)`` (sensor values + known-mask).
        hidden_channels : int
            Width of hidden GraphSAGE layers.
        out_channels : int
            Number of outputs per node (one per sensor column).
        dropout : float
            Dropout probability applied between layers.
        """

        def __init__(
            self,
            in_channels: int | None = None,
            hidden_channels: int = 64,
            out_channels: int | None = None,
            dropout: float = 0.1,
        ) -> None:
            super().__init__()
            _in = in_channels or 2 * len(SENSOR_COLUMNS)
            _out = out_channels or len(SENSOR_COLUMNS)

            self.conv1 = SAGEConv(_in, hidden_channels)
            self.conv2 = SAGEConv(hidden_channels, hidden_channels)
            self.head = nn.Linear(hidden_channels, _out)
            self.dropout = dropout

            # Skip-connection projection when input dim != hidden dim.
            if _in != hidden_channels:
                self.skip_proj = nn.Linear(_in, hidden_channels, bias=False)
            else:
                self.skip_proj = nn.Identity()

        def forward(self, x: torch.Tensor, edge_index: torch.Tensor) -> torch.Tensor:
            """Forward pass.

            Parameters
            ----------
            x : Tensor (N, in_channels)
            edge_index : LongTensor (2, E)

            Returns
            -------
            Tensor (N, out_channels) — predicted sensor values for every node.
            """
            identity = self.skip_proj(x)

            h = self.conv1(x, edge_index)
            h = F.relu(h)
            h = F.dropout(h, p=self.dropout, training=self.training)

            h = self.conv2(h, edge_index)
            h = F.relu(h + identity)  # skip connection
            h = F.dropout(h, p=self.dropout, training=self.training)

            return self.head(h)

    # ------------------------------------------------------------------
    # Helpers: convert GaugeGraph -> PyG Data
    # ------------------------------------------------------------------

    def _graph_to_pyg(graph: GaugeGraph) -> Data:
        """Convert a ``GaugeGraph`` into a ``torch_geometric.data.Data`` object."""
        features = np.nan_to_num(graph.node_features, nan=0.0)
        mask_float = graph.node_mask.astype(np.float64)
        x_np = np.concatenate([features, mask_float], axis=1)

        x = torch.tensor(x_np, dtype=torch.float32)
        y = torch.tensor(np.nan_to_num(graph.node_features, nan=0.0), dtype=torch.float32)
        known_mask = torch.tensor(graph.node_mask, dtype=torch.bool)
        edge_index = torch.tensor(graph.edge_index, dtype=torch.long)

        return Data(x=x, y=y, edge_index=edge_index, known_mask=known_mask)

    # ------------------------------------------------------------------
    # Training loop
    # ------------------------------------------------------------------

    def train_spatial_gnn(
        graph: GaugeGraph,
        *,
        epochs: int = 100,
        lr: float = 1e-3,
        mask_ratio: float = 0.3,
        hidden_channels: int = 64,
        dropout: float = 0.1,
        verbose: bool = True,
    ) -> SpatialInterpolationGNN:
        """Train the GNN on a single graph snapshot.

        During each epoch a random ``mask_ratio`` fraction of *known* sensor
        values is hidden and the model is trained to reconstruct them.

        Parameters
        ----------
        graph : GaugeGraph
            The spatial gauge graph built by ``build_gauge_graph``.
        epochs : int
            Number of training epochs.
        lr : float
            Learning rate for the Adam optimiser.
        mask_ratio : float
            Fraction of known values to mask during training (0-1).
        hidden_channels : int
            Hidden layer width passed to ``SpatialInterpolationGNN``.
        dropout : float
            Dropout probability.
        verbose : bool
            If True, print loss every 10 epochs.

        Returns
        -------
        SpatialInterpolationGNN
            The trained model (in eval mode).
        """
        data = _graph_to_pyg(graph)
        model = SpatialInterpolationGNN(
            hidden_channels=hidden_channels,
            dropout=dropout,
        )
        optimiser = torch.optim.Adam(model.parameters(), lr=lr)

        model.train()
        for epoch in range(1, epochs + 1):
            optimiser.zero_grad()

            # Random masking: hide a subset of known values.
            train_mask = data.known_mask.clone()
            drop = torch.rand_like(train_mask.float()) < mask_ratio
            train_mask = train_mask & ~drop

            # Rebuild input with the reduced mask.
            features_full = data.y.clone()
            features_full[~train_mask] = 0.0
            mask_input = train_mask.float()
            x_masked = torch.cat([features_full, mask_input], dim=1)

            pred = model(x_masked, data.edge_index)

            # Loss only on the values that were masked out but originally known.
            target_mask = data.known_mask & drop
            if target_mask.any():
                loss = F.mse_loss(pred[target_mask], data.y[target_mask])
            else:
                loss = torch.tensor(0.0, requires_grad=True)

            loss.backward()
            optimiser.step()

            if verbose and epoch % 10 == 0:
                print(f"  epoch {epoch:>4d}/{epochs}  loss={loss.item():.6f}")

        model.eval()
        return model

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    @torch.no_grad()
    def interpolate_missing_values(
        model: SpatialInterpolationGNN,
        graph: GaugeGraph,
        target_site_id: str,
    ) -> dict[str, float]:
        """Predict missing sensor values at a specific gauge site.

        Parameters
        ----------
        model : SpatialInterpolationGNN
            A trained model (should be in eval mode).
        graph : GaugeGraph
            The spatial graph that includes ``target_site_id``.
        target_site_id : str
            The site whose missing sensors should be filled.

        Returns
        -------
        dict[str, float]
            Mapping from sensor name to predicted value for every sensor
            that was *missing* at the target site.  Already-known values
            are not overwritten and are excluded from the result.

        Raises
        ------
        ValueError
            If ``target_site_id`` is not present in the graph.
        """
        if target_site_id not in graph.site_ids:
            raise ValueError(
                f"Site '{target_site_id}' not found in graph. "
                f"Available: {graph.site_ids[:10]}{'...' if len(graph.site_ids) > 10 else ''}"
            )

        data = _graph_to_pyg(graph)
        pred = model(data.x, data.edge_index)

        node_idx = graph.site_ids.index(target_site_id)
        predictions: dict[str, float] = {}
        for col_idx, col_name in enumerate(SENSOR_COLUMNS):
            if not graph.node_mask[node_idx, col_idx]:
                predictions[col_name] = float(pred[node_idx, col_idx].item())

        return predictions


# ===================================================================
# Fallback: inverse-distance weighting (no PyTorch required)
# ===================================================================

def idw_interpolate(
    graph: GaugeGraph,
    target_site_id: str,
    *,
    power: float = 2.0,
    max_neighbours: int | None = None,
) -> dict[str, float]:
    """Inverse-distance-weighted interpolation of missing sensors.

    This is a simple, dependency-free fallback that does not require
    PyTorch or torch_geometric.  For each missing sensor at the target
    site, it computes a weighted average from neighbours that *do*
    report that sensor, with weights proportional to ``1 / d^power``.

    Parameters
    ----------
    graph : GaugeGraph
        The spatial gauge graph.
    target_site_id : str
        Site whose missing sensors should be filled.
    power : float
        Exponent for the inverse-distance weights (2.0 = standard IDW).
    max_neighbours : int | None
        If set, only the closest *max_neighbours* sites contribute.

    Returns
    -------
    dict[str, float]
        Predicted values for each sensor that was missing at the target.

    Raises
    ------
    ValueError
        If ``target_site_id`` is not found in the graph.
    """
    if target_site_id not in graph.site_ids:
        raise ValueError(
            f"Site '{target_site_id}' not found in graph. "
            f"Available: {graph.site_ids[:10]}{'...' if len(graph.site_ids) > 10 else ''}"
        )

    node_idx = graph.site_ids.index(target_site_id)
    target_lat = graph.lats[node_idx]
    target_lon = graph.lons[node_idx]

    # Compute distances to all other nodes.
    n = len(graph.site_ids)
    distances: list[tuple[int, float]] = []
    for j in range(n):
        if j == node_idx:
            continue
        d = haversine_km(target_lat, target_lon, graph.lats[j], graph.lons[j])
        if d > 0:
            distances.append((j, d))

    distances.sort(key=lambda t: t[1])
    if max_neighbours is not None:
        distances = distances[:max_neighbours]

    predictions: dict[str, float] = {}
    for col_idx, col_name in enumerate(SENSOR_COLUMNS):
        if graph.node_mask[node_idx, col_idx]:
            # Value already known — skip.
            continue

        weighted_sum = 0.0
        weight_total = 0.0
        for j, d in distances:
            if not graph.node_mask[j, col_idx]:
                continue
            w = 1.0 / (d ** power)
            weighted_sum += w * graph.node_features[j, col_idx]
            weight_total += w

        if weight_total > 0:
            predictions[col_name] = weighted_sum / weight_total

    return predictions


# ===================================================================
# Convenience: unified interface
# ===================================================================

def interpolate(
    graph: GaugeGraph,
    target_site_id: str,
    *,
    model: Any | None = None,
    idw_power: float = 2.0,
    max_neighbours: int | None = None,
) -> dict[str, float]:
    """Predict missing sensor values, using the GNN when available.

    If a trained ``SpatialInterpolationGNN`` model is supplied *and*
    torch_geometric is installed, the GNN prediction is used.  Otherwise
    the function transparently falls back to inverse-distance weighting.

    Parameters
    ----------
    graph : GaugeGraph
        Spatial gauge graph.
    target_site_id : str
        Site to interpolate.
    model : SpatialInterpolationGNN | None
        Optional trained GNN model.
    idw_power : float
        Power parameter for the IDW fallback.
    max_neighbours : int | None
        Neighbour cap for the IDW fallback.

    Returns
    -------
    dict[str, float]
        Predicted values for each missing sensor at the target site.
    """
    if model is not None and _HAS_PYG:
        return interpolate_missing_values(model, graph, target_site_id)

    if model is not None and not _HAS_PYG:
        warnings.warn(
            "torch_geometric is not installed; falling back to IDW interpolation.",
            stacklevel=2,
        )

    return idw_interpolate(
        graph,
        target_site_id,
        power=idw_power,
        max_neighbours=max_neighbours,
    )


# ===================================================================
# Fishing Location Graph (prototype)
# ===================================================================
#
# While the GaugeGraph above models USGS sensors, this section builds
# a graph where **fishing locations** (tournament lakes) are nodes.
# Edges connect locations that:
#   (a) share a watershed (HUC code prefix), or
#   (b) are geographically close (< radius_km).
#
# This graph will be used to transfer-learn environmental patterns:
# if Lake A and Lake B are in the same watershed, conditions affecting
# fishing at A likely also affect B.  This is the first step toward
# enabling predictions at locations without direct USGS coverage.


@dataclass
class FishingLocationGraph:
    """Graph of tournament fishing locations for spatial transfer learning.

    Attributes
    ----------
    location_ids : list[str]
        Canonical location names (e.g. "Lake Guntersville, Guntersville, AL").
    node_features : np.ndarray
        Shape ``(N, F)`` -- morphometry + historical performance features.
    edge_index : np.ndarray
        Shape ``(2, E)`` -- COO source/target pairs.
    edge_attr : np.ndarray
        Shape ``(E, 3)`` -- ``[distance_km, same_huc4, same_huc8]`` per edge.
    lats : np.ndarray
    lons : np.ndarray
    huc_codes : list[str]
    usgs_site_ids : list[str]
        USGS gauge linked to each location (empty string if none).
    """

    location_ids: list[str]
    node_features: np.ndarray
    edge_index: np.ndarray
    edge_attr: np.ndarray
    lats: np.ndarray
    lons: np.ndarray
    huc_codes: list[str] = field(default_factory=list)
    usgs_site_ids: list[str] = field(default_factory=list)


LOCATION_FEATURE_COLS: list[str] = [
    "area_acres",
    "max_depth_ft",
    "shore_dev",
    "location_mean_weight",
    "loc_rolling_3",
]


def build_fishing_location_graph(
    locations_df: pd.DataFrame,
    *,
    radius_km: float = 150.0,
    huc4_prefix_len: int = 4,
    huc8_prefix_len: int = 8,
) -> FishingLocationGraph:
    """Build a spatial graph from a DataFrame of fishing locations.

    Parameters
    ----------
    locations_df : pd.DataFrame
        Must contain ``location``, ``lat``, ``lon``.  May contain
        ``huc_code``, ``usgs_site_id``, and any of ``LOCATION_FEATURE_COLS``.
    radius_km : float
        Maximum edge distance.
    huc4_prefix_len, huc8_prefix_len : int
        HUC prefix lengths for watershed matching granularity.

    Returns
    -------
    FishingLocationGraph
    """
    required = {"location", "lat", "lon"}
    missing = required - set(locations_df.columns)
    if missing:
        raise ValueError(f"locations_df missing columns: {sorted(missing)}")

    # Deduplicate by location
    df = locations_df.drop_duplicates(subset="location").reset_index(drop=True)
    n = len(df)

    location_ids = df["location"].tolist()
    lats = df["lat"].to_numpy(dtype=np.float64)
    lons = df["lon"].to_numpy(dtype=np.float64)

    huc_codes = (
        df["huc_code"].fillna("").astype(str).tolist()
        if "huc_code" in df.columns
        else [""] * n
    )
    usgs_ids = (
        df["usgs_site_id"].fillna("").astype(str).tolist()
        if "usgs_site_id" in df.columns
        else [""] * n
    )

    # Node features
    node_features = np.full((n, len(LOCATION_FEATURE_COLS)), np.nan, dtype=np.float64)
    for col_idx, col_name in enumerate(LOCATION_FEATURE_COLS):
        if col_name in df.columns:
            node_features[:, col_idx] = pd.to_numeric(
                df[col_name], errors="coerce",
            ).to_numpy(dtype=np.float64)

    # Build edges
    src_list: list[int] = []
    dst_list: list[int] = []
    dist_list: list[float] = []
    same_huc4_list: list[float] = []
    same_huc8_list: list[float] = []

    for i in range(n):
        lat_i, lon_i = lats[i], lons[i]
        if np.isnan(lat_i) or np.isnan(lon_i):
            continue
        for j in range(i + 1, n):
            lat_j, lon_j = lats[j], lons[j]
            if np.isnan(lat_j) or np.isnan(lon_j):
                continue

            d = haversine_km(lat_i, lon_i, lat_j, lon_j)

            huc_i = huc_codes[i]
            huc_j = huc_codes[j]
            same_h4 = 1.0 if (huc_i[:huc4_prefix_len] and huc_j[:huc4_prefix_len]
                              and huc_i[:huc4_prefix_len] == huc_j[:huc4_prefix_len]) else 0.0
            same_h8 = 1.0 if (huc_i[:huc8_prefix_len] and huc_j[:huc8_prefix_len]
                              and huc_i[:huc8_prefix_len] == huc_j[:huc8_prefix_len]) else 0.0

            # Connect if within radius OR in same HUC-4 watershed
            if d <= radius_km or same_h4 > 0:
                src_list.extend([i, j])
                dst_list.extend([j, i])
                dist_list.extend([d, d])
                same_huc4_list.extend([same_h4, same_h4])
                same_huc8_list.extend([same_h8, same_h8])

    if src_list:
        edge_index = np.array([src_list, dst_list], dtype=np.int64)
        edge_attr = np.column_stack(
            [dist_list, same_huc4_list, same_huc8_list],
        ).astype(np.float64)
    else:
        edge_index = np.zeros((2, 0), dtype=np.int64)
        edge_attr = np.zeros((0, 3), dtype=np.float64)

    return FishingLocationGraph(
        location_ids=location_ids,
        node_features=node_features,
        edge_index=edge_index,
        edge_attr=edge_attr,
        lats=lats,
        lons=lons,
        huc_codes=huc_codes,
        usgs_site_ids=usgs_ids,
    )


def build_location_graph_from_dataset(
    dataset_path: str | Path,
    radius_km: float = 150.0,
) -> FishingLocationGraph:
    """Convenience: build a fishing location graph from the v4 dataset CSV.

    Aggregates per-location stats from the dataset and constructs the graph.
    """
    df = pd.read_csv(dataset_path)

    # Aggregate per location
    agg = df.groupby("location").agg({
        "lat": "first",
        "lon": "first",
        **{c: "mean" for c in LOCATION_FEATURE_COLS if c in df.columns},
        **({"usgs_site_id": "first"} if "usgs_site_id" in df.columns else {}),
    }).reset_index()

    # Compute location_mean_weight if not already present
    if "location_mean_weight" not in agg.columns and "target_success_score" in df.columns:
        loc_means = df.groupby("location")["target_success_score"].mean()
        agg["location_mean_weight"] = agg["location"].map(loc_means)

    return build_fishing_location_graph(agg, radius_km=radius_km)


def summarise_location_graph(graph: FishingLocationGraph) -> dict:
    """Return summary statistics for a fishing location graph."""
    n_nodes = len(graph.location_ids)
    n_edges = graph.edge_index.shape[1] if graph.edge_index.size > 0 else 0

    # Degree distribution
    if n_edges > 0:
        degrees = np.bincount(graph.edge_index[0], minlength=n_nodes)
        avg_degree = float(degrees.mean())
        max_degree = int(degrees.max())
        isolated = int((degrees == 0).sum())
    else:
        avg_degree = 0.0
        max_degree = 0
        isolated = n_nodes

    # USGS coverage
    has_usgs = sum(1 for s in graph.usgs_site_ids if s)

    return {
        "n_locations": n_nodes,
        "n_edges": n_edges,
        "avg_degree": round(avg_degree, 1),
        "max_degree": max_degree,
        "isolated_nodes": isolated,
        "usgs_coverage": f"{has_usgs}/{n_nodes} ({100*has_usgs/max(n_nodes,1):.0f}%)",
    }
