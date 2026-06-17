import psycopg2
from matcher import calculate_rule_based_score


DB_CONFIG = {
    "host": "localhost",
    "port": 5435,
    "database": "HB_Hotel_Mapping",
    "user": "postgres",
    "password": "postgres"
}


def get_connection():
    return psycopg2.connect(**DB_CONFIG)


def get_supplier_hotel(cursor, supplier_hotel_record_id):
    cursor.execute(
        """
        SELECT
            id,
            supplier_name,
            supplier_hotel_id,
            hotel_name,
            normalized_name,
            normalized_address,
            star_rating,
            city,
            country
        FROM supplier_hotels
        WHERE id = %s;
        """,
        (supplier_hotel_record_id,)
    )

    return cursor.fetchone()


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


def main():
    supplier_hotel_record_id = 62571

    conn = get_connection()
    cursor = conn.cursor()

    supplier_hotel = get_supplier_hotel(
        cursor,
        supplier_hotel_record_id
    )

    print("\nSupplier hotel:")
    print(supplier_hotel)

    candidates = find_candidate_master_hotels(
        cursor,
        supplier_hotel_record_id
    )

    print("\nCandidates found:", len(candidates))

    if not candidates:
        print("\nDecision: CREATE_NEW_MASTER")
        print("Reason: No candidate master hotel found within 300m.")
        cursor.close()
        conn.close()
        return

    scored_candidates = score_candidates(candidates)

    best_candidate = max(
        scored_candidates,
        key=lambda item: item["score"]["final_score"]
    )

    print("\nBest candidate:")
    print(best_candidate)

    cursor.close()
    conn.close()


if __name__ == "__main__":
    main()
