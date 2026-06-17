import psycopg2
from psycopg2 import sql
from matcher import calculate_rule_based_score


DB_CONFIG = {
    "host": "localhost",
    "port": 5435,
    "database": "HB_Hotel_Mapping",
    "user": "postgres",
    "password": "postgres"
}


TEST_SUPPLIER_HOTEL_RECORD_ID = 2


def get_connection():
    return psycopg2.connect(**DB_CONFIG)


def get_table_columns(cursor, table_name):
    cursor.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = %s;
        """,
        (table_name,)
    )
    return {row[0] for row in cursor.fetchall()}


def get_supplier_hotel(cursor, supplier_hotel_record_id):
    cursor.execute(
        """
        SELECT *
        FROM supplier_hotels
        WHERE id = %s;
        """,
        (supplier_hotel_record_id,)
    )

    row = cursor.fetchone()
    if row is None:
        return None

    columns = [desc[0] for desc in cursor.description]
    return dict(zip(columns, row))


def find_candidate_master_hotels(cursor, supplier_hotel_record_id):
    cursor.execute(
        """
        SELECT
            s.id AS supplier_hotel_record_id,
            s.supplier_name,
            s.supplier_hotel_id,
            s.hotel_name AS supplier_hotel_name,
            s.normalized_name AS supplier_normalized_name,
            s.normalized_address AS supplier_normalized_address,
            s.star_rating AS supplier_star_rating,

            m.master_hotel_id,
            m.hotel_name AS master_hotel_name,
            m.normalized_name AS master_normalized_name,
            m.normalized_address AS master_normalized_address,
            m.star_rating AS master_star_rating,

            s.city,
            s.country,

            ST_Distance(m.geo_location, s.geo_location) AS distance_meters

        FROM supplier_hotels s
        JOIN master_hotels m
          ON LOWER(m.country) = LOWER(s.country)
         AND LOWER(m.city) = LOWER(s.city)

        WHERE s.id = %s
          AND s.geo_location IS NOT NULL
          AND m.geo_location IS NOT NULL
          AND ST_DWithin(m.geo_location, s.geo_location, 300)

        ORDER BY distance_meters ASC
        LIMIT 10;
        """,
        (supplier_hotel_record_id,)
    )

    return cursor.fetchall()


def score_candidates(candidates):
    scored_candidates = []

    for row in candidates:
        (
            supplier_hotel_record_id,
            supplier_name,
            supplier_hotel_id,
            supplier_hotel_name,
            supplier_normalized_name,
            supplier_normalized_address,
            supplier_star_rating,
            master_hotel_id,
            master_hotel_name,
            master_normalized_name,
            master_normalized_address,
            master_star_rating,
            city,
            country,
            distance_meters
        ) = row

        score_result = calculate_rule_based_score(
            distance_meters=distance_meters,
            master_normalized_name=master_normalized_name,
            supplier_normalized_name=supplier_normalized_name,
            master_address=master_normalized_address,
            supplier_address=supplier_normalized_address,
            master_star_rating=master_star_rating,
            supplier_star_rating=supplier_star_rating,
            master_chain_name=None,
            supplier_chain_name=None
        )

        scored_candidates.append({
            "supplier_hotel_record_id": supplier_hotel_record_id,
            "supplier_name": supplier_name,
            "supplier_hotel_id": supplier_hotel_id,
            "supplier_hotel_name": supplier_hotel_name,
            "master_hotel_id": master_hotel_id,
            "master_hotel_name": master_hotel_name,
            "city": city,
            "country": country,
            "distance_meters": float(distance_meters),
            "score": score_result
        })

    return scored_candidates


def mark_queue_status(cursor, supplier_hotel_record_id, status):
    cursor.execute(
        """
        UPDATE hotel_mapping_queue
        SET status = %s
        WHERE supplier_hotel_record_id = %s;
        """,
        (status, supplier_hotel_record_id)
    )


def insert_hotel_mapping(
    cursor,
    master_hotel_id,
    supplier_hotel,
    match_score,
    mapping_type,
    is_manual_verified=False
):
    mapping_columns = get_table_columns(cursor, "hotel_mappings")

    values = {}

    if "master_hotel_id" in mapping_columns:
        values["master_hotel_id"] = master_hotel_id

    if "supplier_hotel_record_id" in mapping_columns:
        values["supplier_hotel_record_id"] = supplier_hotel["id"]

    if "supplier_name" in mapping_columns:
        values["supplier_name"] = supplier_hotel.get("supplier_name")

    if "supplier_hotel_id" in mapping_columns:
        values["supplier_hotel_id"] = supplier_hotel.get("supplier_hotel_id")

    if "match_score" in mapping_columns:
        values["match_score"] = match_score

    if "mapping_type" in mapping_columns:
        values["mapping_type"] = mapping_type

    if "is_manual_verified" in mapping_columns:
        values["is_manual_verified"] = is_manual_verified

    insert_columns = list(values.keys())
    insert_values = list(values.values())

    query = sql.SQL("""
        INSERT INTO hotel_mappings ({columns})
        VALUES ({placeholders})
        RETURNING *;
    """).format(
        columns=sql.SQL(", ").join(map(sql.Identifier, insert_columns)),
        placeholders=sql.SQL(", ").join(sql.Placeholder() * len(insert_columns))
    )

    cursor.execute(query, insert_values)
    return cursor.fetchone()


def create_master_hotel_from_supplier(cursor, supplier_hotel):
    master_columns = get_table_columns(cursor, "master_hotels")

    values = {}

    simple_column_map = {
        "hotel_name": "hotel_name",
        "normalized_name": "normalized_name",
        "address": "address",
        "normalized_address": "normalized_address",
        "city": "city",
        "country": "country",
        "postal_code": "postal_code",
        "star_rating": "star_rating",
        "latitude": "latitude",
        "longitude": "longitude"
    }

    for master_col, supplier_col in simple_column_map.items():
        if master_col in master_columns and supplier_col in supplier_hotel:
            values[master_col] = supplier_hotel.get(supplier_col)

    if "source_supplier" in master_columns:
        values["source_supplier"] = supplier_hotel.get("supplier_name")

    if "source_supplier_hotel_id" in master_columns:
        values["source_supplier_hotel_id"] = supplier_hotel.get("supplier_hotel_id")

    insert_columns = list(values.keys())
    insert_values = list(values.values())

    geo_sql = sql.SQL("")
    if (
        "geo_location" in master_columns
        and supplier_hotel.get("latitude") is not None
        and supplier_hotel.get("longitude") is not None
    ):
        insert_columns.append("geo_location")
        geo_sql = sql.SQL(
            "ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography"
        )
        insert_values.extend([
            supplier_hotel.get("longitude"),
            supplier_hotel.get("latitude")
        ])

    placeholders = []
    normal_value_count = len(insert_columns)

    if "geo_location" in insert_columns:
        normal_value_count -= 1

    placeholders.extend([sql.Placeholder()] * normal_value_count)

    if "geo_location" in insert_columns:
        placeholders.append(geo_sql)

    query = sql.SQL("""
        INSERT INTO master_hotels ({columns})
        VALUES ({placeholders})
        RETURNING master_hotel_id;
    """).format(
        columns=sql.SQL(", ").join(map(sql.Identifier, insert_columns)),
        placeholders=sql.SQL(", ").join(placeholders)
    )

    cursor.execute(query, insert_values)
    return cursor.fetchone()[0]


def process_one_supplier_hotel(cursor, supplier_hotel_record_id):
    supplier_hotel = get_supplier_hotel(cursor, supplier_hotel_record_id)

    if supplier_hotel is None:
        print("Supplier hotel not found.")
        return

    print("\nSupplier hotel:")
    print({
        "id": supplier_hotel.get("id"),
        "supplier_name": supplier_hotel.get("supplier_name"),
        "supplier_hotel_id": supplier_hotel.get("supplier_hotel_id"),
        "hotel_name": supplier_hotel.get("hotel_name"),
        "city": supplier_hotel.get("city"),
        "country": supplier_hotel.get("country")
    })

    candidates = find_candidate_master_hotels(cursor, supplier_hotel_record_id)

    print("\nCandidates found:", len(candidates))

    if not candidates:
        print("\nNo candidates found.")
        print("Decision: CREATE_NEW_MASTER")

        new_master_hotel_id = create_master_hotel_from_supplier(
            cursor,
            supplier_hotel
        )

        insert_hotel_mapping(
            cursor=cursor,
            master_hotel_id=new_master_hotel_id,
            supplier_hotel=supplier_hotel,
            match_score=100.0,
            mapping_type="NEW_MASTER",
            is_manual_verified=True
        )

        mark_queue_status(cursor, supplier_hotel_record_id, "Completed")

        print("Created new master_hotel_id:", new_master_hotel_id)
        print("Queue updated to Completed.")
        return

    scored_candidates = score_candidates(candidates)

    best_candidate = max(
        scored_candidates,
        key=lambda item: item["score"]["final_score"]
    )

    print("\nBest candidate:")
    print(best_candidate)

    decision = best_candidate["score"]["decision"]
    final_score = best_candidate["score"]["final_score"]

    print("\nFinal decision:", decision)

    if decision == "AUTO_MATCH":
        insert_hotel_mapping(
            cursor=cursor,
            master_hotel_id=best_candidate["master_hotel_id"],
            supplier_hotel=supplier_hotel,
            match_score=final_score,
            mapping_type="AUTO",
            is_manual_verified=False
        )

        mark_queue_status(cursor, supplier_hotel_record_id, "Completed")

        print("Inserted AUTO mapping.")
        print("Queue updated to Completed.")
        return

    if decision == "MANUAL_REVIEW":
        mark_queue_status(cursor, supplier_hotel_record_id, "ManualReview")

        print("Queue updated to ManualReview.")
        return

    if decision == "CREATE_NEW_MASTER":
        new_master_hotel_id = create_master_hotel_from_supplier(
            cursor,
            supplier_hotel
        )

        insert_hotel_mapping(
            cursor=cursor,
            master_hotel_id=new_master_hotel_id,
            supplier_hotel=supplier_hotel,
            match_score=100.0,
            mapping_type="NEW_MASTER",
            is_manual_verified=True
        )

        mark_queue_status(cursor, supplier_hotel_record_id, "Completed")

        print("Created new master_hotel_id:", new_master_hotel_id)
        print("Inserted NEW_MASTER mapping.")
        print("Queue updated to Completed.")
        return


def main():
    conn = get_connection()
    cursor = conn.cursor()

    try:
        process_one_supplier_hotel(
            cursor,
            TEST_SUPPLIER_HOTEL_RECORD_ID
        )

        conn.commit()
        print("\nDatabase changes committed.")

    except Exception as error:
        conn.rollback()
        print("\nERROR. Rolled back database changes.")
        print(error)

    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()