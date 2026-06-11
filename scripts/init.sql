-- ============================================================
-- Hotel Mapping Engine — PostgreSQL Init Script
-- Runs automatically when Docker container starts
-- ============================================================

-- Extensions
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;

-- ─────────────────────────────────────────────────────────────
-- Table 1: supplier_hotels
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS supplier_hotels (
    id                  BIGSERIAL PRIMARY KEY,
    supplier_name       VARCHAR(50)     NOT NULL,
    supplier_hotel_id   VARCHAR(100)    NOT NULL,
    hotel_name          TEXT,
    normalized_name     TEXT,
    address             TEXT,
    city                VARCHAR(100),
    state               VARCHAR(100),
    country             VARCHAR(100),
    postal_code         VARCHAR(20),
    latitude            DECIMAL(10, 7),
    longitude           DECIMAL(10, 7),
    geo_location        geography(Point, 4326),
    star_rating         NUMERIC(2, 1),
    chain_name          VARCHAR(100),
    raw_json            JSONB,
    created_at          TIMESTAMP DEFAULT NOW()
);

-- ─────────────────────────────────────────────────────────────
-- Table 2: master_hotels
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS master_hotels (
    master_hotel_id     BIGSERIAL PRIMARY KEY,
    hotel_name          TEXT,
    normalized_name     TEXT,
    address             TEXT,
    city                VARCHAR(100),
    state               VARCHAR(100),
    country             VARCHAR(100),
    postal_code         VARCHAR(20),
    latitude            DECIMAL(10, 7),
    longitude           DECIMAL(10, 7),
    geo_location        geography(Point, 4326),
    star_rating         NUMERIC(2, 1),
    created_at          TIMESTAMP DEFAULT NOW()
);

-- ─────────────────────────────────────────────────────────────
-- Table 3: hotel_mappings
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS hotel_mappings (
    id                  BIGSERIAL PRIMARY KEY,
    master_hotel_id     BIGINT          NOT NULL,
    supplier_name       VARCHAR(50)     NOT NULL,
    supplier_hotel_id   VARCHAR(100)    NOT NULL,
    match_score         DECIMAL(5, 2),
    mapping_type        VARCHAR(50),
    is_manual_verified  BOOLEAN         DEFAULT FALSE,
    created_at          TIMESTAMP DEFAULT NOW()
);

-- ─────────────────────────────────────────────────────────────
-- Table 4: hotel_mapping_queue
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS hotel_mapping_queue (
    id                  BIGSERIAL PRIMARY KEY,
    supplier_hotel_id   BIGINT          NOT NULL,
    status              VARCHAR(20)     DEFAULT 'Pending',
    retry_count         INT             DEFAULT 0,
    created_at          TIMESTAMP DEFAULT NOW()
);

-- ─────────────────────────────────────────────────────────────
-- Table 5: hotel_embeddings (pgvector)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS hotel_embeddings (
    id                  BIGSERIAL PRIMARY KEY,
    master_hotel_id     BIGINT,
    supplier_hotel_id   BIGINT,
    supplier_name       VARCHAR(50),
    embedding           vector(384),
    created_at          TIMESTAMP DEFAULT NOW()
);

-- ─────────────────────────────────────────────────────────────
-- Indexes
-- ─────────────────────────────────────────────────────────────

-- Geo index for distance matching (Stage 3)
CREATE INDEX IF NOT EXISTS idx_supplier_geo
ON supplier_hotels USING GIST(geo_location);

CREATE INDEX IF NOT EXISTS idx_master_geo
ON master_hotels USING GIST(geo_location);

-- City index for city filtering (Stage 2)
CREATE INDEX IF NOT EXISTS idx_supplier_city
ON supplier_hotels(city);

CREATE INDEX IF NOT EXISTS idx_master_city
ON master_hotels(city);

-- Country index for country filtering (Stage 1)
CREATE INDEX IF NOT EXISTS idx_supplier_country
ON supplier_hotels(country);

CREATE INDEX IF NOT EXISTS idx_master_country
ON master_hotels(country);

-- Trigram index for name similarity (Stage 4)
CREATE INDEX IF NOT EXISTS idx_supplier_name_trgm
ON supplier_hotels USING GIN(normalized_name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_master_name_trgm
ON master_hotels USING GIN(normalized_name gin_trgm_ops);

-- Queue status index
CREATE INDEX IF NOT EXISTS idx_queue_status
ON hotel_mapping_queue(status);

-- Embedding vector index (ivfflat for cosine similarity)
CREATE INDEX IF NOT EXISTS idx_embedding
ON hotel_embeddings USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

-- Supplier name + hotel id index
CREATE INDEX IF NOT EXISTS idx_supplier_name
ON supplier_hotels(supplier_name);

CREATE INDEX IF NOT EXISTS idx_mapping_master
ON hotel_mappings(master_hotel_id);
