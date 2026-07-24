-- 1. Queue status
SELECT status, COUNT(*) AS count
FROM hotel_mapping_queue
GROUP BY status
ORDER BY status;

-- 2. Duplicate supplier hotel mapped to multiple masters
SELECT
    supplier_name,
    supplier_hotel_id,
    COUNT(DISTINCT master_hotel_id) AS master_count
FROM hotel_mappings
GROUP BY supplier_name, supplier_hotel_id
HAVING COUNT(DISTINCT master_hotel_id) > 1
ORDER BY master_count DESC;

-- 3. Possible over-merged masters
SELECT
    master_hotel_id,
    COUNT(*) AS mapped_count,
    STRING_AGG(DISTINCT supplier_name, ', ') AS suppliers
FROM hotel_mappings
GROUP BY master_hotel_id
HAVING COUNT(*) > 5
ORDER BY mapped_count DESC;

-- 4. Single-supplier final masters
SELECT
    master_hotel_id,
    COUNT(DISTINCT supplier_name) AS supplier_count,
    COUNT(*) AS mapped_count
FROM hotel_mappings
GROUP BY master_hotel_id
HAVING COUNT(DISTINCT supplier_name) = 1
ORDER BY mapped_count DESC;

-- 5. Manual review count by supplier
SELECT supplier_name, COUNT(*) AS manual_review_count
FROM manual_review_candidates
GROUP BY supplier_name
ORDER BY supplier_name;
