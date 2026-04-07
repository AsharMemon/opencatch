"""
OpenCatch Reservoir Bathymetry Pipeline

Separate from the natural lakes pipeline, this sub-package implements
terrain-extrapolation-based bathymetry for reservoirs. Reservoirs are
flooded valleys whose surrounding DEM terrain directly constrains
underwater topography via geometric extrapolation.

Modules:
    fetch_nid               - National Inventory of Dams data fetcher
    fetch_nhdplus_flowlines - River channel extraction for thalweg tracing
    fetch_usbr_surveys      - USBR validation survey ground truth
    cross_section_extractor - Valley cross-section extraction + extrapolation
    reservoir_features      - Reservoir-specific feature engineering
    terrain_extrapolation_model - Physics-based terrain extrapolation + bias correction
    reservoir_ensemble      - Multi-model fusion (terrain + A-E + optical)
    storage_nowcast         - Real-time storage estimation from A-E + pool level
    evaluate_reservoir      - Validation metrics and benchmarking
    run_reservoir_pipeline  - Master pipeline orchestration
"""
