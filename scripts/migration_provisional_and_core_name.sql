-- ============================================================
-- Migration: provisional masters + core-name blocking
--
-- Idempotent. Safe to re-run.
--
-- NOTE: scripts/init.sql is stale — the identity tables
-- (master_hotel_registry, master_hotel_anchor, master_hotel_lifecycle,
-- master_non_merge_assertion, master_public_id_seq) and several
-- hotel_mappings columns exist in the live database but have no DDL in the
-- repository. This migration assumes the live shape and only adds what is
-- new; it does not attempt to reconstruct what init.sql is missing.
-- ============================================================

-- ─────────────────────────────────────────────────────────────
-- 1. Two computed blocking keys
--
-- `normalized_name` is NOT usable as a blocking key. On supplier_hotels it is
-- whatever the supplier shipped in `normalized_hotelname`, and on
-- master_hotels it is that value copied forward — supplier-authored text of
-- unknown provenance driving a matching decision. Both columns below are
-- computed here from hotel_name, so they are deterministic and reproducible.
--
--   strict_name  normalize_hotel_name(hotel_name)
--                Stop words removed, city RETAINED.
--
--   core_name    core_hotel_name(hotel_name, city, state)
--                City and state tokens removed as well — the same string the
--                in-memory scorer compares.
--
-- Two keys are needed because neither subsumes the other. Measured on the
-- 2767 masters currently loaded, within a 5 km radius:
--
--   both keys agree      365 pairs
--   core_name only        94 pairs   "The Residency Chennai" vs "The Residency"
--   strict_name only     122 pairs   city spellings disagree — Gurgaon/GURUGRAM,
--                                    Calicut/Kozhikode, Bangalore/Bengaluru —
--                                    so core stripping removes DIFFERENT tokens
--                                    from each side and equality breaks
--
-- Blocking is the union. The difference between them is also the confidence
-- signal: agreement on strict_name means the names match with the city still
-- in them, while a core_name-only match depended on removing it, which can
-- leave nothing but a bare chain name behind.
-- ─────────────────────────────────────────────────────────────
ALTER TABLE supplier_hotels ADD COLUMN IF NOT EXISTS core_name   TEXT;
ALTER TABLE supplier_hotels ADD COLUMN IF NOT EXISTS strict_name TEXT;
ALTER TABLE master_hotels   ADD COLUMN IF NOT EXISTS core_name   TEXT;
ALTER TABLE master_hotels   ADD COLUMN IF NOT EXISTS strict_name TEXT;

-- Equality lookups drive the exact-name pass.
CREATE INDEX IF NOT EXISTS idx_master_core_name
ON master_hotels (core_name);

CREATE INDEX IF NOT EXISTS idx_master_strict_name
ON master_hotels (strict_name);

CREATE INDEX IF NOT EXISTS idx_supplier_core_name
ON supplier_hotels (core_name);

CREATE INDEX IF NOT EXISTS idx_supplier_strict_name
ON supplier_hotels (strict_name);

-- Trigram, for the near-exact fallback and the fragmentation sweep.
CREATE INDEX IF NOT EXISTS idx_master_core_name_trgm
ON master_hotels USING GIN (core_name gin_trgm_ops);

-- ─────────────────────────────────────────────────────────────
-- 1b. Repair master_hotels.state
--
-- create_master_hotel_from_supplier() never wrote the state column, so all
-- 2767 existing masters carry NULL while their supplier rows carry a value.
-- That breaks core_name symmetry: core_hotel_name() strips the state token
-- from a supplier name and cannot strip it from the master name, so
-- "Marriott Kochi" (supplier, state Kerala) and the same master would key to
-- different strings and never block together.
--
-- Take the most common non-null state across the master's own supplier rows.
-- Run before the core_name backfill.
-- ─────────────────────────────────────────────────────────────
WITH supplier_state AS (
    SELECT hm.master_hotel_id,
           s.state,
           row_number() OVER (
               PARTITION BY hm.master_hotel_id
               ORDER BY count(*) DESC, s.state
           ) AS rank
    FROM hotel_mappings hm
    JOIN supplier_hotels s ON s.id = hm.supplier_hotel_row_id
    WHERE s.state IS NOT NULL AND btrim(s.state) <> ''
    GROUP BY hm.master_hotel_id, s.state
)
UPDATE master_hotels m
SET state = supplier_state.state
FROM supplier_state
WHERE supplier_state.master_hotel_id = m.master_hotel_id
  AND supplier_state.rank = 1
  AND m.state IS NULL;

-- ─────────────────────────────────────────────────────────────
-- 2. Provisional masters
--
-- master_hotel_registry.status gains 'Provisional' alongside
-- Active / Merged / Deprecated / Dormant. A provisional master is matchable
-- but not publishable: it exists so later supplier rows have something to
-- attach to, and it is excluded from every export until a second distinct
-- supplier corroborates it or a reviewer confirms it.
--
-- The column is a plain varchar with no CHECK constraint, so no type change
-- is needed — only the supporting index and the audit columns.
-- ─────────────────────────────────────────────────────────────
ALTER TABLE master_hotel_registry
    ADD COLUMN IF NOT EXISTS confirmed_at   TIMESTAMP;

ALTER TABLE master_hotel_registry
    ADD COLUMN IF NOT EXISTS confirmed_by   VARCHAR(120);

ALTER TABLE master_hotel_registry
    ADD COLUMN IF NOT EXISTS confirm_reason TEXT;

-- The provisional review queue is "status = 'Provisional' ordered by id".
CREATE INDEX IF NOT EXISTS idx_registry_provisional
ON master_hotel_registry (status, public_id)
WHERE status = 'Provisional';

-- ─────────────────────────────────────────────────────────────
-- 3. Review queue typing
--
-- review_type separates the reasons a record is in front of a human, so the
-- console can rank them. An exact-name geo conflict is a different question
-- from a borderline composite score and deserves a different screen.
--
--   SCORE_BAND               legacy/default — composite score in the review band
--   EXACT_NAME_GEO_CONFLICT  identical core name, distance beyond auto tolerance
--   CITY_STRIPPED_NAME_MATCH names match only after the city is removed
-- ─────────────────────────────────────────────────────────────
ALTER TABLE manual_review_candidates
    ADD COLUMN IF NOT EXISTS review_type VARCHAR(40) DEFAULT 'SCORE_BAND';

ALTER TABLE manual_review_candidates
    ADD COLUMN IF NOT EXISTS distance_meters NUMERIC(10, 2);

UPDATE manual_review_candidates
SET review_type = 'SCORE_BAND'
WHERE review_type IS NULL;

-- One open review item per supplier row. Without this, re-processing a record
-- that was rejected back to Pending inserts a second identical review item.
CREATE UNIQUE INDEX IF NOT EXISTS uq_manual_review_supplier
ON manual_review_candidates (supplier_hotel_id);

CREATE INDEX IF NOT EXISTS idx_manual_review_type
ON manual_review_candidates (review_type);

-- ─────────────────────────────────────────────────────────────
-- 4. Master merge audit
--
-- split_master already records master_non_merge_assertion. Merge is its
-- opposite and was never reachable from the API; the reviewer decision behind
-- one needs the same durability, because the next pipeline run must not undo
-- it and the next reviewer must be able to see why it happened.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS master_merge_decision (
    id                  BIGSERIAL PRIMARY KEY,
    retired_public_id   VARCHAR(24) NOT NULL,
    surviving_public_id VARCHAR(24) NOT NULL,
    reason              TEXT        NOT NULL,
    decided_by          VARCHAR(120) NOT NULL DEFAULT 'reviewer',
    records_moved       INT         NOT NULL DEFAULT 0,
    created_at          TIMESTAMP   DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_merge_decision_survivor
ON master_merge_decision (surviving_public_id);
