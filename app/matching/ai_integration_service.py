from app.matching.ai_similarity_service import AISimilarityService


class AIIntegrationService:

    def __init__(self, db):
        self.db = db

        self.ai_service = AISimilarityService(db)

    async def enrich_candidate(
        self,
        candidate: dict
    ) -> dict:

        score = candidate["score"]

        rule_score = score["rule_score"]

        # Default values
        ai_similarity = None
        final_decision = score["rule_decision"]
        decision_reason = "Rule-based decision"

        # Auto-match already confirmed
        if rule_score >= 90:

            final_decision = "AUTO_MATCH"

            decision_reason = (
                "Rule score is above auto-match threshold"
            )

        # Borderline candidates
        elif 70 <= rule_score < 90:

            matches = await self.ai_service.find_ai_matches(
                supplier_hotel_id=
                    candidate["supplier_hotel_record_id"],

                hotel_name=
                    candidate["supplier_hotel_name"],

                address=
                    candidate.get(
                        "supplier_normalized_address",
                        ""
                    ),

                city=
                    candidate["city"],

                country=
                    candidate["country"]
            )

            if matches:

                best_match = matches[0]

                ai_similarity = (
                    best_match["ai_similarity_score"] / 100
                )

                if ai_similarity >= 0.85:

                    final_decision = "AUTO_MATCH"

                    decision_reason = (
                        "Rule score is borderline and "
                        "AI similarity is above 0.85"
                    )

                else:

                    final_decision = "MANUAL_REVIEW"

                    decision_reason = (
                        "Rule score is borderline but "
                        "AI similarity is below 0.85"
                    )

        else:

            final_decision = "CREATE_NEW_MASTER"

            decision_reason = (
                "Rule score below threshold"
            )

        score["ai_similarity"] = ai_similarity
        score["final_decision"] = final_decision
        score["decision_reason"] = decision_reason

        return candidate