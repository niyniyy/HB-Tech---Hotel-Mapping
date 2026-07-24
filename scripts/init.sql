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
-- Table 5: manual_review_candidates
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS manual_review_candidates (
    id                          BIGSERIAL PRIMARY KEY,

    supplier_hotel_id           BIGINT NOT NULL,

    suggested_master_hotel_id   BIGINT NOT NULL,

    rule_score                  DECIMAL(5,2),

    ai_similarity               DECIMAL(5,2),

    decision_reason             TEXT,

    created_at                  TIMESTAMP DEFAULT NOW()
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

-- ─────────────────────────────────────────────────────────────
-- Table 7: flagged_records
-- Records not fit for automatic processing. Never mapped, never
-- promoted to a master; reported back so the supplier can correct them.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS flagged_records (
    id                  BIGSERIAL PRIMARY KEY,
    supplier_hotel_id   BIGINT          NOT NULL UNIQUE,
    flag_reason         VARCHAR(64)     NOT NULL,
    status              VARCHAR(20)     DEFAULT 'Flagged',
    created_at          TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_flagged_reason
ON flagged_records(flag_reason);

-- Queue status index
CREATE INDEX IF NOT EXISTS idx_queue_status
ON hotel_mapping_queue(status);

-- Queue lookup index. update_queue_status() filters on supplier_hotel_id and is
-- called twice per hotel; without this it sequentially scans the whole queue
-- every time — 40ms per call at 1M rows versus 0.047ms with the index.
CREATE INDEX IF NOT EXISTS idx_queue_supplier_hotel_id
ON hotel_mapping_queue(supplier_hotel_id);

-- One mapping per supplier hotel. Without this, re-processing silently creates
-- duplicate and even contradictory mappings to different masters.
CREATE UNIQUE INDEX IF NOT EXISTS uq_mapping_supplier
ON hotel_mappings(supplier_name, supplier_hotel_id);

-- Embedding vector index (ivfflat for cosine similarity).
-- lists should be about sqrt(row_count): 100 suits ~10k vectors, 1000 suits
-- ~1M. Undersized lists degrade the search toward a sequential scan.
-- Rebuild with a larger value after a bulk load: see scripts/tune_for_scale.sql
CREATE INDEX IF NOT EXISTS idx_embedding
ON hotel_embeddings USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 1000);

-- Composite index for the queue claim query, which filters on status and
-- orders by id. Without it, claiming a batch from a 10-lakh queue plans as a
-- sequential scan plus sort on every call.
CREATE INDEX IF NOT EXISTS idx_queue_status_id
ON hotel_mapping_queue(status, id);

-- ─────────────────────────────────────────────────────────────
-- Referential integrity
-- ─────────────────────────────────────────────────────────────
ALTER TABLE hotel_mappings
    DROP CONSTRAINT IF EXISTS fk_mappings_master;
ALTER TABLE hotel_mappings
    ADD CONSTRAINT fk_mappings_master
    FOREIGN KEY (master_hotel_id) REFERENCES master_hotels(master_hotel_id)
    ON DELETE CASCADE;

ALTER TABLE hotel_mapping_queue
    DROP CONSTRAINT IF EXISTS fk_queue_supplier;
ALTER TABLE hotel_mapping_queue
    ADD CONSTRAINT fk_queue_supplier
    FOREIGN KEY (supplier_hotel_id) REFERENCES supplier_hotels(id)
    ON DELETE CASCADE;

ALTER TABLE flagged_records
    DROP CONSTRAINT IF EXISTS fk_flagged_supplier;
ALTER TABLE flagged_records
    ADD CONSTRAINT fk_flagged_supplier
    FOREIGN KEY (supplier_hotel_id) REFERENCES supplier_hotels(id)
    ON DELETE CASCADE;

-- Supplier name + hotel id index
CREATE INDEX IF NOT EXISTS idx_supplier_name
ON supplier_hotels(supplier_name);

CREATE INDEX IF NOT EXISTS idx_mapping_master
ON hotel_mappings(master_hotel_id);

CREATE INDEX IF NOT EXISTS idx_manual_supplier
ON manual_review_candidates(supplier_hotel_id);

CREATE INDEX IF NOT EXISTS idx_manual_master
ON manual_review_candidates(suggested_master_hotel_id);


-- ── reviewer attach decisions (ground-truth labels) ──────────────────────

-- A reviewer attaching a record to a master is a labelled example: this
-- supplier row belongs to this property. Keyed on public_id, not
-- master_hotel_id, because internal ids are reassigned by every rebuild while
-- public ids are not — a label has to stay true across a pipeline reset or it
-- is not ground truth.
CREATE TABLE IF NOT EXISTS review_attach_decision (
    id                        BIGSERIAL PRIMARY KEY,
    supplier_hotel_row_id     BIGINT NOT NULL
                              REFERENCES supplier_hotels(id) ON DELETE CASCADE,
    chosen_public_id          VARCHAR(24) NOT NULL,
    suggested_public_id       VARCHAR(24),
    agreed_with_suggestion    BOOLEAN NOT NULL,
    chosen_master_hotel_id    BIGINT,
    suggested_master_hotel_id BIGINT,
    rule_score                NUMERIC(5,2),
    ai_similarity             NUMERIC(5,2),
    distance_meters           NUMERIC(10,2),
    review_type               VARCHAR(40),
    reason                    TEXT,
    decided_by                VARCHAR(120) NOT NULL DEFAULT 'reviewer',
    created_at                TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_attach_decision_row
    ON review_attach_decision (supplier_hotel_row_id);
CREATE INDEX IF NOT EXISTS idx_attach_decision_agreed
    ON review_attach_decision (agreed_with_suggestion);


-- ── name token frequencies (feature: shared-token rarity) ────────────────

-- How often each word appears across all supplier hotel names.
--
-- "hotel" occurs in 2,138 records and says nothing; a hotel's actual
-- identifying word occurs in two or three and says almost everything. Fuzzy
-- string similarity cannot tell the difference — both are just tokens that
-- matched — so the corpus statistic is kept here and consulted at scoring time.
--
-- Derived data: rebuilt from supplier_hotels, never edited by hand, and safe to
-- drop and regenerate after any import.
CREATE TABLE IF NOT EXISTS name_token_df (
    token VARCHAR(64) PRIMARY KEY,
    df    INTEGER NOT NULL
);

-- Band edges for the rarity feature, stored as percentiles of the live df
-- distribution rather than absolute counts. df<=5/20/100 were fitted to a
-- 1,751-token corpus; at 100x the corpus those same numbers would mean the
-- opposite, scoring a mid-size chain name as if it were unique. Recomputed with
-- the df table below so they track whatever data is loaded. p60/p88/p97
-- reproduce 5/20/100 on the fitting corpus, so behaviour there is unchanged.
CREATE TABLE IF NOT EXISTS name_rarity_cutoffs (
    band       VARCHAR(8) PRIMARY KEY,   -- 'full' | 'strong' | 'weak'
    cutoff_df  INTEGER NOT NULL
);

CREATE OR REPLACE FUNCTION refresh_name_token_df() RETURNS bigint AS $$
DECLARE
    n bigint;
BEGIN
    TRUNCATE name_token_df;

    INSERT INTO name_token_df (token, df)
    SELECT token, count(DISTINCT id)
    FROM (
        SELECT s.id,
               unnest(string_to_array(
                   regexp_replace(lower(s.hotel_name), '[^a-z0-9 ]', ' ', 'g'),
                   ' ')) AS token
        FROM supplier_hotels s
        WHERE s.hotel_name IS NOT NULL
    ) t
    WHERE length(token) > 2
    GROUP BY token;

    GET DIAGNOSTICS n = ROW_COUNT;

    -- percentile_disc returns an actual df value, so a token's raw df can still
    -- be compared against these directly.
    TRUNCATE name_rarity_cutoffs;
    INSERT INTO name_rarity_cutoffs (band, cutoff_df)
    SELECT 'full',   coalesce(percentile_disc(0.60) WITHIN GROUP (ORDER BY df), 5)   FROM name_token_df
    UNION ALL
    SELECT 'strong', coalesce(percentile_disc(0.88) WITHIN GROUP (ORDER BY df), 20)  FROM name_token_df
    UNION ALL
    SELECT 'weak',   coalesce(percentile_disc(0.97) WITHIN GROUP (ORDER BY df), 100) FROM name_token_df;

    RETURN n;
END;
$$ LANGUAGE plpgsql;


-- ── queue claim recovery ─────────────────────────────────────────────────

-- A claim needs a timestamp or it cannot be recovered. Without this, a worker
-- that dies mid-batch leaves its rows in Processing forever, and the next run
-- reports "no pending hotels remaining" — success, while records sit lost.
ALTER TABLE hotel_mapping_queue ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMP;
CREATE INDEX IF NOT EXISTS idx_queue_claimed_at
    ON hotel_mapping_queue (claimed_at) WHERE status = 'Processing';


-- ── reference mapping (accuracy evaluation) ─────────────────────────────

-- A reference mapping to score against: one row per (reference hotel, supplier
-- record). Sourced from an existing production mapping (Vervotech) or any
-- hand-built set. Deliberately outside the pipeline reset's TRUNCATE list —
-- it is evidence, not output.
CREATE TABLE IF NOT EXISTS reference_mapping (
    reference_id      INTEGER NOT NULL,
    supplier_name     VARCHAR(50) NOT NULL,
    supplier_hotel_id VARCHAR(100) NOT NULL,
    source            VARCHAR(60) NOT NULL DEFAULT 'vervotech',
    loaded_at         TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (reference_id, supplier_name, supplier_hotel_id)
);

CREATE INDEX IF NOT EXISTS idx_reference_supplier
    ON reference_mapping (supplier_name, supplier_hotel_id);

-- ── duplicate-detection indexes ──────────────────────────────────────────
-- The comprehensive duplicate sweep joins masters on name and postcode
-- equality; without these it is an O(n^2) sequential scan (4.8 s on 2k
-- masters, and quadratic beyond). With them the sweep runs in ~0.15 s.
CREATE INDEX IF NOT EXISTS idx_master_strict_name ON master_hotels (lower(strict_name)) WHERE strict_name IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_master_core_name ON master_hotels (lower(core_name)) WHERE core_name IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_master_postal ON master_hotels (postal_code) WHERE postal_code IS NOT NULL;
