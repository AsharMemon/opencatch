-- CASTLINE PostGIS initialization
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- for text search

-- ── Core tables ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS locations (
    id SERIAL PRIMARY KEY,
    name VARCHAR(256) UNIQUE NOT NULL,
    geom GEOMETRY(Point, 4326) NOT NULL,
    usgs_site_id VARCHAR(32),
    state VARCHAR(2),
    lake_area_ha FLOAT,
    max_depth_m FLOAT,
    lagos_sdi FLOAT,
    historical_avg_cpue FLOAT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_locations_geom ON locations USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_locations_name ON locations USING gin(name gin_trgm_ops);

CREATE TABLE IF NOT EXISTS catch_reports (
    id SERIAL PRIMARY KEY,
    location_id INTEGER REFERENCES locations(id),
    geom GEOMETRY(Point, 4326),
    species VARCHAR(64),
    weight_lb FLOAT,
    quantity INTEGER DEFAULT 1,
    method VARCHAR(64),
    conditions_score FLOAT,
    water_temp_f FLOAT,
    notes TEXT,
    reported_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_reports_geom ON catch_reports USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_reports_time ON catch_reports (reported_at DESC);

CREATE TABLE IF NOT EXISTS prediction_log (
    id SERIAL PRIMARY KEY,
    location_id INTEGER REFERENCES locations(id),
    predicted_score FLOAT NOT NULL,
    confidence VARCHAR(16),
    features JSONB,
    model_version VARCHAR(16),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── Spatial overlay tables ─────────────────────────────────────

CREATE TABLE IF NOT EXISTS public_lands (
    id SERIAL PRIMARY KEY,
    name VARCHAR(256),
    agency VARCHAR(64),
    designation VARCHAR(128),
    access_type VARCHAR(32),
    state VARCHAR(2),
    gis_acres FLOAT,
    geom GEOMETRY(MultiPolygon, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_public_lands_geom ON public_lands USING GIST(geom);

CREATE TABLE IF NOT EXISTS bathymetry_contours (
    id SERIAL PRIMARY KEY,
    lake_id INTEGER REFERENCES locations(id),
    depth_ft FLOAT NOT NULL,
    geom GEOMETRY(MultiLineString, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bathy_geom ON bathymetry_contours USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_bathy_lake ON bathymetry_contours (lake_id);

CREATE TABLE IF NOT EXISTS nhd_flowlines (
    id SERIAL PRIMARY KEY,
    comid BIGINT UNIQUE,
    gnis_name VARCHAR(256),
    stream_order INTEGER,
    fcode INTEGER,
    geom GEOMETRY(MultiLineString, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nhd_geom ON nhd_flowlines USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_nhd_order ON nhd_flowlines (stream_order);

CREATE TABLE IF NOT EXISTS access_points (
    id SERIAL PRIMARY KEY,
    location_id INTEGER REFERENCES locations(id),
    name VARCHAR(256),
    access_type VARCHAR(32) NOT NULL,
    source VARCHAR(32) NOT NULL DEFAULT 'osm',
    source_id VARCHAR(128),
    nearest_waterbody VARCHAR(256),
    distance_to_water_m FLOAT,
    fee BOOLEAN,
    is_free BOOLEAN,
    public_access BOOLEAN,
    capacity INTEGER,
    difficulty VARCHAR(64),
    surface VARCHAR(64),
    tags JSONB,
    geom GEOMETRY(Geometry, 4326) NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_access_points_source
ON access_points (source, source_id, access_type)
WHERE source_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_access_points_geom ON access_points USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_access_points_type ON access_points (access_type);
