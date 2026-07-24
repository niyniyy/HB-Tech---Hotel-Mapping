-- ============================================================
-- Geocode correction columns on supplier_hotels
-- ============================================================
-- Raw latitude/longitude are NEVER modified — the original supplied coordinate
-- stays recoverable. Geocoding results and any correction live in dedicated
-- columns so a pipeline rebuild cannot silently lose them.
--
-- match_geo_location is the coordinate candidate search should use. It is NULL
-- for OK/unchecked records, so COALESCE(match_geo_location, geo_location) is a
-- no-op until a correction is written by scripts/geocode_correct.py.

ALTER TABLE supplier_hotels ADD COLUMN IF NOT EXISTS geocoded_latitude       NUMERIC(10, 7);
ALTER TABLE supplier_hotels ADD COLUMN IF NOT EXISTS geocoded_longitude      NUMERIC(10, 7);
ALTER TABLE supplier_hotels ADD COLUMN IF NOT EXISTS geocode_status          VARCHAR(20);   -- OK | SUSPECT | UNGEOCODABLE
ALTER TABLE supplier_hotels ADD COLUMN IF NOT EXISTS geocode_mismatch_meters NUMERIC(10, 2);
ALTER TABLE supplier_hotels ADD COLUMN IF NOT EXISTS geocode_checked_at      TIMESTAMP;
ALTER TABLE supplier_hotels ADD COLUMN IF NOT EXISTS match_geo_location      geography(Point, 4326);

-- The enrichment script filters on geocode_status (e.g. re-check only unchecked
-- rows); reporting queries filter on 'SUSPECT'.
CREATE INDEX IF NOT EXISTS idx_supplier_geocode_status
    ON supplier_hotels (geocode_status);
