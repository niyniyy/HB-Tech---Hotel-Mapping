from sqlalchemy import text

from app.matching.ai_similarity_service import AISimilarityService
from app.matching.llm_service import LLMService, LLMVerdict
from app.matching.matcher import AUTO_MATCH_MIN_SCORE, MANUAL_REVIEW_MIN_SCORE
from config import settings

# Below this the AI is confident the pair is not the same hotel; the suggested
# match is cancelled rather than queued for a human. Only the band between the
# two bounds is genuinely uncertain and worth a person's time.
#
# Both come from settings because they are calibrated to whichever model
# settings.EMBEDDING_MODEL names — see the note there. Hard-coding them is what
# makes a model swap change the pipeline's behaviour silently.
AI_REJECT_BELOW = settings.AI_REJECT_BELOW

# Above this the model is treated as agreeing that two records describe one
# hotel. It confirms a rule-based candidate; on its own it never merges.
AI_CONFIRM_AT = settings.AI_CONFIRM_AT


# Full records for the LLM prompt — the candidate dict only reliably carries the
# master id, so both sides are loaded from the database to give the model
# complete, accurate fields to reason over.
_HOTEL_FIELDS = (
    "hotel_name, address, city, state, postal_code, "
    "latitude, longitude, star_rating"
)
_MASTER_SQL = text(f"SELECT {_HOTEL_FIELDS} FROM master_hotels WHERE master_hotel_id = :id")
_SUPPLIER_SQL = text(f"SELECT {_HOTEL_FIELDS} FROM supplier_hotels WHERE id = :id")


class AIIntegrationService:

    def __init__(self, db):
        self.db = db
        self.ai_service = AISimilarityService(db)
        # No-op while settings.LLM_ENABLED is False; the client is built lazily on
        # first use, so constructing it here is free when the flag is off.
        self.llm = LLMService(db)

    async def semantic_duplicate_for(
        self,
        supplier_hotel_row_id: int,
        hotel_name: str,
        address: str,
        city: str,
        country: str,
        radius_meters: float,
    ) -> dict | None:
        """
        An existing master that means the same hotel as a record about to become
        a master of its own — or None.

        Returns a result only above the confirm bar. Everything below it is the
        model failing to distinguish neighbouring hotels rather than evidence of
        anything, and putting that in front of a reviewer costs more attention
        than it saves.
        """
        best = await self.ai_service.find_semantic_duplicate_master(
            supplier_hotel_row_id=supplier_hotel_row_id,
            hotel_name=hotel_name,
            address=address,
            city=city,
            country=country,
            radius_meters=radius_meters,
        )

        if best is None or best["ai_similarity"] < AI_CONFIRM_AT:
            return None

        return best

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

        # An exact-name conflict is not the AI's to resolve. Every branch below
        # can end at CREATE_NEW_MASTER, which is precisely the outcome this case
        # exists to prevent: two records with the same name, too far apart to be
        # sure, silently becoming two masters. The question — one hotel with a
        # bad coordinate, or two branches of a chain? — is answerable from a map
        # and a street address, which is a person's job, not a similarity score.
        if score.get("exact_name_class") is not None:

            score["ai_similarity"] = None
            score["final_decision"] = "MANUAL_REVIEW"
            score["decision_reason"] = (
                f"Names match exactly ({score['exact_name_class']}) but the "
                f"records are {score.get('distance_meters')} m apart — human "
                "decision required"
            )

            return candidate

        # Auto-match already confirmed by rule engine
        if rule_score >= AUTO_MATCH_MIN_SCORE:

            final_decision = "AUTO_MATCH"

            decision_reason = (
                "Rule score is above auto-match threshold"
            )

        # Borderline candidate — use AI for validation.
        #
        # These bounds are read from the matcher rather than written here. The
        # previous literals (75..90) were left over from the old 0-100 scoring
        # scale and no longer intersected the band this method is called for, so
        # every borderline candidate fell through to the final else and became
        # its own master without the AI ever being consulted — the AI was not
        # merely unused, it was silently manufacturing duplicate masters.
        elif MANUAL_REVIEW_MIN_SCORE <= rule_score < AUTO_MATCH_MIN_SCORE:
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
                    ai_similarity >= AI_CONFIRM_AT
                    and ai_master_hotel_id == rule_master_hotel_id
                ):

                    final_decision = "AUTO_MATCH"

                    decision_reason = (
                        f"Rule score is borderline, AI similarity is above "
                        f"{AI_CONFIRM_AT}, and AI confirms the same master hotel"
                    )

                # AI is confident this is NOT the same hotel. Previously any
                # similarity below the confirm bar fell through to MANUAL_REVIEW, which sent
                # ~90% of the queue to a human who could only ever reject it.
                # Below this bound the suggested match is cancelled outright and
                # the hotel becomes its own master.
                elif ai_similarity < AI_REJECT_BELOW:

                    final_decision = "CREATE_NEW_MASTER"

                    decision_reason = (
                        f"AI similarity {ai_similarity:.2f} is below the "
                        f"rejection bound {AI_REJECT_BELOW}; suggested match "
                        "cancelled and a new master created"
                    )

                else:

                    # Genuinely-uncertain embedding band. Before
                    # parking it with a human, let the LLM reason about identity
                    # — but only here, only to confirm or deny the rule engine's
                    # own candidate master, and never on the conflicting
                    # "strong AI match to a *different* master" case. The LLM can
                    # never override a rule rejection or bypass the tier gate: it
                    # only acts on a candidate the rules already found plausible.
                    # Any failure leaves the record where it is today:
                    # MANUAL_REVIEW.
                    llm_decided = False

                    if (
                        self.llm.enabled
                        and ai_similarity is not None
                        and ai_similarity < AI_CONFIRM_AT
                    ):
                        verdict = await self._llm_adjudicate(candidate)

                        if verdict.ok and verdict.confidence >= settings.LLM_MIN_CONFIDENCE:
                            llm_decided = True

                            if verdict.same_hotel:
                                final_decision = "AUTO_MATCH"
                                decision_reason = (
                                    "Rule score borderline; LLM confirms the "
                                    f"same property (confidence "
                                    f"{verdict.confidence:.2f}): {verdict.reasoning}"
                                )
                            else:
                                final_decision = "CREATE_NEW_MASTER"
                                decision_reason = (
                                    "Rule score borderline; LLM finds a "
                                    f"different property (confidence "
                                    f"{verdict.confidence:.2f}): {verdict.reasoning}"
                                )

                    if not llm_decided:

                        final_decision = "MANUAL_REVIEW"

                        if ai_similarity >= AI_CONFIRM_AT:

                            decision_reason = (
                                "AI found a strong match, but the AI master hotel "
                                "differs from the rule-based master candidate"
                            )

                        else:

                            decision_reason = (
                                "Rule score is borderline and AI similarity is "
                                "genuinely uncertain — human decision required"
                            )

            # No AI matches found
            else:

                final_decision = "CREATE_NEW_MASTER"

                decision_reason = (
                    "Rule score is borderline and no AI matches were found"
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

    async def _llm_adjudicate(self, candidate: dict) -> LLMVerdict:
        """Ask the LLM whether the supplier record and the rule engine's own
        candidate master are the same physical hotel.

        Loads both full records so the prompt is complete; if either cannot be
        loaded the verdict is 'unavailable', which the caller reads as "leave it
        for review". This never chooses a master — it only confirms or denies the
        one the rule engine already proposed.
        """
        master = await self._load_hotel(_MASTER_SQL, candidate.get("master_hotel_id"))
        supplier = await self._load_hotel(
            _SUPPLIER_SQL, candidate.get("supplier_hotel_record_id")
        )
        if master is None or supplier is None:
            return LLMVerdict.unavailable("candidate record could not be loaded")

        distance = candidate["score"].get("distance_meters")
        return await self.llm.verify_hotel_match(supplier, master, distance)

    async def _load_hotel(self, query, row_id) -> dict | None:
        if row_id is None:
            return None
        result = await self.db.execute(query, {"id": row_id})
        row = result.mappings().first()
        return dict(row) if row else None