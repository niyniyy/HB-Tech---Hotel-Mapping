import psycopg2
from MatchingService import MatchingService


DB_CONFIG = {
    "host": "localhost",
    "port": 5435,
    "database": "HB_Hotel_Mapping",
    "user": "postgres",
    "password": "postgres"
}


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()

    try:
        service = MatchingService(cursor)

        result = service.process_supplier_hotel(
            supplier_hotel_record_id=62571,
            apply_db_actions=False
        )

        print(result)

        conn.rollback()

    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()