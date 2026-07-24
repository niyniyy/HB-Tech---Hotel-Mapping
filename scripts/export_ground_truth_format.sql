SELECT
    hm.master_hotel_id,

    STRING_AGG(DISTINCT hm.supplier_hotel_id::text, ', ')
        FILTER (WHERE hm.supplier_name ILIKE '%Humming%') AS "HummingbirdId",

    STRING_AGG(DISTINCT hm.supplier_hotel_id::text, ', ')
        FILTER (WHERE hm.supplier_name ILIKE '%Sabre%') AS "Sabre",

    STRING_AGG(DISTINCT hm.supplier_hotel_id::text, ', ')
        FILTER (WHERE hm.supplier_name ILIKE '%Cleartrip%') AS "ClearTrip_HotelId",

    STRING_AGG(DISTINCT hm.supplier_hotel_id::text, ', ')
        FILTER (WHERE hm.supplier_name ILIKE '%GRN%') AS "GRN_HotelId",

    STRING_AGG(DISTINCT hm.supplier_hotel_id::text, ', ')
        FILTER (WHERE hm.supplier_name ILIKE '%Booking%') AS "BOK_HotelId",

    COUNT(*) AS mapped_count

FROM hotel_mappings hm
GROUP BY hm.master_hotel_id
ORDER BY hm.master_hotel_id;
