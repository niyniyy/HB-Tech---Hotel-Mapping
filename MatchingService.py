from psycopg2 import sql
from matcher import calculate_rule_based_score


class MatchingService:
    """
    Production matching service for one supplier hotel record.

    This service:
    1. Fetches supplier hotel
    2. Finds candidate master hotels
    3. Applies rule-based scoring
    4. Selects best candidate
    5. Returns scored candidate object
    6. Applies rule-based decision if apply_db_actions=True
    """

    def __init__(self, cursor):
        self.cursor = cursor

    def get_table_columns(self, table_name):
        self.cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = %s;
            """,
            (table_name,)
        )
        return {row[0] for row in self.cursor.fetchall()}

    def get_supplier_hotel(self, supplier_hotel_record_id):
        self.cursor.execute(
            """
            SELECT *
            FROM supplier_hotels
            WHERE id = %s;
            """,
            (supplier_hotel_record_id,)
        )

        row = self.cursor.fetchone()

        if row is None:
            return None

        columns = [desc[0] for desc in self.cursor.description]
        return dict(zip(columns, row))

    def find_candidate_master_hotels(self, supplier_hotel_record_id):
        self.cursor.execute(
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

        return self.cursor.fetchall()

    def score_candidates(self, candidates):
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

            candidate_object = {
                "supplier_hotel_record_id": supplier_hotel_record_id,
                "supplier_name": supplier_name,
                "supplier_hotel_id": supplier_hotel_id,
                "supplier_hotel_name": supplier_hotel_name,

                "master_hotel_id": master_hotel_id,
                "master_hotel_name": master_hotel_name,

                "city": city,
                "country": country,
                "distance_meters": float(distance_meters),

                "score": {
                    "geo_score": score_result["geo_score"],
                    "name_similarity": score_result["name_similarity"],
                    "name_score": score_result["name_score"],
                    "address_similarity": score_result["address_similarity"],
                    "address_score": score_result["address_score"],
                    "star_score": score_result["star_score"],
                    "chain_score": score_result["chain_score"],
                    "rule_score": score_result["final_score"],
                    "rule_decision": score_result["decision"],

                    # AI layer can append these later
                    "ai_similarity": None,
                    "final_decision": score_result["decision"],
                    "decision_reason": "Rule-based decision"
                }
            }

            scored_candidates.append(candidate_object)

        return scored_candidates

    def select_best_candidate(self, scored_candidates):
        if not scored_candidates:
            return None

        return max(
            scored_candidates,
            key=lambda item: item["score"]["rule_score"]
        )

    def mark_queue_status(self, supplier_hotel_record_id, status):
        self.cursor.execute(
            """
            UPDATE hotel_mapping_queue
            SET status = %s
            WHERE supplier_hotel_record_id = %s;
            """,
            (status, supplier_hotel_record_id)
        )

    def insert_hotel_mapping(
        self,
        master_hotel_id,
        supplier_hotel,
        match_score,
        mapping_type,
        is_manual_verified=False
    ):
        mapping_columns = self.get_table_columns("hotel_mappings")

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

        self.cursor.execute(query, insert_values)
        return self.cursor.fetchone()

    def create_master_hotel_from_supplier(self, supplier_hotel):
        master_columns = self.get_table_columns("master_hotels")

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

        geo_sql = None

        if (
            "geo_location" in master_columns
            and supplier_hotel.get("latitude") is not None
            and supplier_hotel.get("longitude") is not None
        ):
            insert_columns.append("geo_location")
            geo_sql = sql.SQL("ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography")
            insert_values.extend([
                supplier_hotel.get("longitude"),
                supplier_hotel.get("latitude")
            ])

        normal_value_count = len(insert_columns)

        if geo_sql is not None:
            normal_value_count -= 1

        placeholders = [sql.Placeholder()] * normal_value_count

        if geo_sql is not None:
            placeholders.append(geo_sql)

        query = sql.SQL("""
            INSERT INTO master_hotels ({columns})
            VALUES ({placeholders})
            RETURNING master_hotel_id;
        """).format(
            columns=sql.SQL(", ").join(map(sql.Identifier, insert_columns)),
            placeholders=sql.SQL(", ").join(placeholders)
        )

        self.cursor.execute(query, insert_values)
        return self.cursor.fetchone()[0]

    def apply_rule_based_decision(self, supplier_hotel, best_candidate):
        decision = best_candidate["score"]["final_decision"]
        rule_score = best_candidate["score"]["rule_score"]
        supplier_hotel_record_id = supplier_hotel["id"]

        if decision == "AUTO_MATCH":
            self.insert_hotel_mapping(
                master_hotel_id=best_candidate["master_hotel_id"],
                supplier_hotel=supplier_hotel,
                match_score=rule_score,
                mapping_type="AUTO",
                is_manual_verified=False
            )

            self.mark_queue_status(supplier_hotel_record_id, "Completed")
            return "AUTO mapping inserted and queue marked Completed"

        if decision == "MANUAL_REVIEW":
            self.mark_queue_status(supplier_hotel_record_id, "ManualReview")
            return "Queue marked ManualReview"

        if decision == "CREATE_NEW_MASTER":
            new_master_hotel_id = self.create_master_hotel_from_supplier(
                supplier_hotel
            )

            self.insert_hotel_mapping(
                master_hotel_id=new_master_hotel_id,
                supplier_hotel=supplier_hotel,
                match_score=100.0,
                mapping_type="NEW_MASTER",
                is_manual_verified=True
            )

            self.mark_queue_status(supplier_hotel_record_id, "Completed")
            return f"Created new master_hotel_id {new_master_hotel_id} and queue marked Completed"

        return "No action applied"

    def process_supplier_hotel(self, supplier_hotel_record_id, apply_db_actions=True):
        supplier_hotel = self.get_supplier_hotel(supplier_hotel_record_id)

        if supplier_hotel is None:
            return {
                "supplier_hotel_record_id": supplier_hotel_record_id,
                "status": "FAILED",
                "reason": "Supplier hotel not found",
                "best_candidate": None
            }

        candidates = self.find_candidate_master_hotels(supplier_hotel_record_id)

        if not candidates:
            if apply_db_actions:
                new_master_hotel_id = self.create_master_hotel_from_supplier(
                    supplier_hotel
                )

                self.insert_hotel_mapping(
                    master_hotel_id=new_master_hotel_id,
                    supplier_hotel=supplier_hotel,
                    match_score=100.0,
                    mapping_type="NEW_MASTER",
                    is_manual_verified=True
                )

                self.mark_queue_status(supplier_hotel_record_id, "Completed")

            return {
                "supplier_hotel_record_id": supplier_hotel_record_id,
                "status": "PROCESSED",
                "candidate_count": 0,
                "best_candidate": None,
                "final_decision": "CREATE_NEW_MASTER",
                "decision_reason": "No candidate master hotel found"
            }

        scored_candidates = self.score_candidates(candidates)
        best_candidate = self.select_best_candidate(scored_candidates)

        action_result = None

        if apply_db_actions:
            action_result = self.apply_rule_based_decision(
                supplier_hotel,
                best_candidate
            )

        return {
            "supplier_hotel_record_id": supplier_hotel_record_id,
            "status": "PROCESSED",
            "candidate_count": len(scored_candidates),
            "best_candidate": best_candidate,
            "final_decision": best_candidate["score"]["final_decision"],
            "decision_reason": best_candidate["score"]["decision_reason"],
            "action_result": action_result
        }