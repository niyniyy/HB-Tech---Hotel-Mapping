from app.matching.ai_similarity_service import AISimilarityService


class AIIntegrationService:

    def __init__(self, db):
        self.db = db
        self.ai_service = AISimilarityService(db)

    async def enrich_candidate(
    self,
    candidate: dict,
    rule_candidates: list[dict]
) -> dict:

        score = candidate["score"]
        rule_score = score["rule_score"]

        # Default values
        ai_similarity = None
        final_decision = score["rule_decision"]
        decision_reason = "Rule-based decision"

        # Auto-match already confirmed by rule engine
        if rule_score >= 90:

            final_decision = "AUTO_MATCH"

            decision_reason = (
                "Rule score is above auto-match threshold"
            )

        # Borderline candidate — use AI for validation
        elif 75 <= rule_score < 90:
            top_rule_candidates = rule_candidates[:5]

            candidate_master_ids = [
                item["master_hotel_id"]
                for item in top_rule_candidates
]
            matches = await self.ai_service.find_ai_matches(
    supplier_hotel_id=candidate["supplier_hotel_record_id"],
    hotel_name=candidate["supplier_hotel_name"],
    address=candidate.get(
        "supplier_normalized_address",
        ""
    ),
    city=candidate["city"],
    country=candidate["country"],
    candidate_master_ids=candidate_master_ids
)

            if matches:

                best_match = matches[0]

                ai_similarity = (
                    best_match["ai_similarity_score"] / 100
                )

                rule_master_hotel_id = candidate["master_hotel_id"]
                ai_master_hotel_id = best_match["master_hotel_id"]

                # AI strongly supports the SAME master
                if (
                    ai_similarity >= 0.85
                    and ai_master_hotel_id == rule_master_hotel_id
                ):

                    final_decision = "AUTO_MATCH"

                    decision_reason = (
                        "Rule score is borderline, AI similarity is above "
                        "0.85, and AI confirms the same master hotel"
                    )

                else:

                    final_decision = "MANUAL_REVIEW"

                    if ai_similarity >= 0.85:

                        decision_reason = (
                            "AI found a strong match, but the AI master hotel "
                            "differs from the rule-based master candidate"
                        )

                    else:

                        decision_reason = (
                            "Rule score is borderline but AI similarity "
                            "is below 0.85"
                        )

            # No AI matches found
            else:

                final_decision = "MANUAL_REVIEW"

                decision_reason = (
                    "Rule score is borderline but no AI matches were found"
                )

        # Rule score below threshold
        else:

            final_decision = "CREATE_NEW_MASTER"

            decision_reason = (
                "Rule score below threshold"
            )

        score["ai_similarity"] = ai_similarity
        score["final_decision"] = final_decision
        score["decision_reason"] = decision_reason

        return candidate