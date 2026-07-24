"""
Shadow evaluation for the LLM adjudicator — measure it against reviewers'
ground truth BEFORE it is allowed to decide anything in the pipeline.

This is the gate agreed in LLM_INTEGRATION_PLAN.md: run the LLM over pairs a
human already labelled, gate its verdict exactly as the pipeline would
(same_hotel AND confidence >= LLM_MIN_CONFIDENCE), and compare to the human.
Read-only — it never writes to the database and never changes a mapping.

Ground truth comes from review_attach_decision:

  * Positive pair (same_hotel = TRUE):  supplier row  ->  chosen master.
    A reviewer attaching a row to a master is a statement that they are one
    property.
  * Hard negative (same_hotel = FALSE): supplier row  ->  suggested master,
    but only when the reviewer REJECTED that suggestion
    (agreed_with_suggestion = FALSE). The system proposed it, the human said
    no — exactly the trap the LLM must not fall for.

The headline number is FALSE MERGES: a confident "same" on a truly-different
pair. In a booking path that sends a guest to the wrong hotel, so it must be
~0 before the LLM is trusted to publish. Recall on positives tells you how many
extra auto-matches you would gain; correct rejects on negatives tell you how
much duplicate-master risk it removes.

Usage (from the repo root, in an environment that can reach the database —
e.g. inside the api container):

    docker compose exec api python -m scripts.llm_shadow_eval --limit 200

    # sweep the confidence gate without re-deciding:
    docker compose exec api python -m scripts.llm_shadow_eval --limit 200 --min-confidence 0.95

Note on ids: review_attach_decision also stores stable public ids, but this
harness joins on the internal master ids for simplicity. Run it against a
database whose masters have not been rebuilt since the labels were recorded
(or after replay_reviewer_decisions), so the internal ids still resolve; pairs
that don't resolve are simply skipped and reported.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text

from app.database.connection import AsyncSessionLocal, engine
from app.matching.llm_service import LLMService
from config import settings


# Both label sets in one pass. Distance is computed with PostGIS so it is
# correct for every pair, not just the ones whose distance happened to be
# recorded at review time.
_LABELLED_SQL = """
WITH labelled AS (
    SELECT d.id AS decision_id, d.created_at, TRUE AS same_hotel,
           s.hotel_name AS s_name, s.address AS s_address, s.city AS s_city,
           s.state AS s_state, s.postal_code AS s_postal,
           s.latitude AS s_lat, s.longitude AS s_lng, s.star_rating AS s_star,
           m.hotel_name AS m_name, m.address AS m_address, m.city AS m_city,
           m.state AS m_state, m.postal_code AS m_postal,
           m.latitude AS m_lat, m.longitude AS m_lng, m.star_rating AS m_star,
           ST_Distance(s.geo_location::geography, m.geo_location::geography) AS distance_m
    FROM review_attach_decision d
    JOIN supplier_hotels s ON s.id = d.supplier_hotel_row_id
    JOIN master_hotels   m ON m.master_hotel_id = d.chosen_master_hotel_id

    UNION ALL

    SELECT d.id, d.created_at, FALSE AS same_hotel,
           s.hotel_name, s.address, s.city, s.state, s.postal_code,
           s.latitude, s.longitude, s.star_rating,
           m.hotel_name, m.address, m.city, m.state, m.postal_code,
           m.latitude, m.longitude, m.star_rating,
           ST_Distance(s.geo_location::geography, m.geo_location::geography)
    FROM review_attach_decision d
    JOIN supplier_hotels s ON s.id = d.supplier_hotel_row_id
    JOIN master_hotels   m ON m.master_hotel_id = d.suggested_master_hotel_id
    WHERE d.agreed_with_suggestion = FALSE
      AND d.suggested_master_hotel_id IS NOT NULL
      AND d.suggested_master_hotel_id <> d.chosen_master_hotel_id
)
SELECT * FROM labelled
ORDER BY created_at DESC
LIMIT :limit
"""

_COUNT_SQL = """
SELECT
  (SELECT COUNT(*) FROM review_attach_decision d
     JOIN master_hotels m ON m.master_hotel_id = d.chosen_master_hotel_id)          AS positives,
  (SELECT COUNT(*) FROM review_attach_decision d
     JOIN master_hotels m ON m.master_hotel_id = d.suggested_master_hotel_id
    WHERE d.agreed_with_suggestion = FALSE
      AND d.suggested_master_hotel_id IS NOT NULL
      AND d.suggested_master_hotel_id <> d.chosen_master_hotel_id)                  AS negatives
"""


def _record(row, side: str) -> dict:
    """Pull a supplier ('s') or master ('m') record out of a result row."""
    return {
        "hotel_name": row[f"{side}_name"],
        "address": row[f"{side}_address"],
        "city": row[f"{side}_city"],
        "state": row[f"{side}_state"],
        "postal_code": row[f"{side}_postal"],
        "latitude": row[f"{side}_lat"],
        "longitude": row[f"{side}_lng"],
        "star_rating": row[f"{side}_star"],
    }


def _confidence_bucket(confidence: float) -> str:
    if confidence >= 1.0:
        return "1.00 (max)"
    if confidence >= 0.91:
        return "0.91-0.99"
    if round(confidence, 2) == 0.90:
        return "0.90 (exact)"
    if confidence >= 0.85:
        return "0.85-0.89"
    if confidence >= 0.70:
        return "0.70-0.84"
    return "<0.70"


_BUCKET_ORDER = [
    "1.00 (max)",
    "0.91-0.99",
    "0.90 (exact)",
    "0.85-0.89",
    "0.70-0.84",
    "<0.70",
]


async def main() -> None:
    parser = argparse.ArgumentParser(description="Shadow-eval the LLM against reviewer labels.")
    parser.add_argument("--limit", type=int, default=200, help="Max labelled pairs to score (caps cost).")
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=settings.LLM_MIN_CONFIDENCE,
        help="Gate: a verdict only acts at or above this confidence (default LLM_MIN_CONFIDENCE).",
    )
    args = parser.parse_args()

    service = LLMService()
    if not service.enabled:
        print("LLM_ENABLED is False — set it True (in .env) before running the shadow eval.")
        return

    async with AsyncSessionLocal() as session:
        counts = (await session.execute(text(_COUNT_SQL))).mappings().one()
        available = counts["positives"] + counts["negatives"]
        if available == 0:
            print(
                "No resolvable labelled pairs in review_attach_decision.\n"
                "Either no reviewer decisions exist yet, or the masters were rebuilt "
                "since (internal ids no longer resolve). Run after some review activity."
            )
            return

        print(
            f"Ground truth available: {counts['positives']} positive + "
            f"{counts['negatives']} hard-negative pairs.\n"
            f"Scoring up to {args.limit} with {service.usage_summary()['model']} "
            f"at gate >= {args.min_confidence:.2f}...\n"
        )

        rows = (
            await session.execute(text(_LABELLED_SQL), {"limit": args.limit})
        ).mappings().all()

    gate = args.min_confidence
    # Outcomes, split by ground truth.
    tp = fp = tn = fn = 0            # confident decisions
    review_pos = review_neg = 0     # abstentions (below gate or unavailable)
    unavailable = 0
    buckets: dict[str, int] = {name: 0 for name in _BUCKET_ORDER}

    for row in rows:
        supplier = _record(row, "s")
        master = _record(row, "m")
        distance = row["distance_m"]
        truth_same = bool(row["same_hotel"])

        verdict = await service.verify_hotel_match(supplier, master, distance)

        if not verdict.ok:
            unavailable += 1
            if truth_same:
                review_pos += 1
            else:
                review_neg += 1
            continue

        buckets[_confidence_bucket(verdict.confidence)] += 1
        acts = verdict.confidence >= gate

        if not acts:                       # below the gate → pipeline sends to review
            if truth_same:
                review_pos += 1
            else:
                review_neg += 1
        elif verdict.same_hotel and truth_same:
            tp += 1                        # confident, correct auto-match
        elif verdict.same_hotel and not truth_same:
            fp += 1                        # confident "same" on a different hotel = FALSE MERGE
        elif (not verdict.same_hotel) and (not truth_same):
            tn += 1                        # confident, correct reject
        else:
            fn += 1                        # confident "different" on the same hotel = missed match

    scored = len(rows)
    positives = sum(1 for r in rows if r["same_hotel"])
    negatives = scored - positives

    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / positives if positives else None
    reject_rate = tn / negatives if negatives else None
    confident = tp + fp + tn + fn
    accuracy = (tp + tn) / confident if confident else None
    abstention = (review_pos + review_neg) / scored if scored else 0.0

    def pct(x):
        return "n/a" if x is None else f"{x * 100:.1f}%"

    print(f"Scored {scored} pairs ({positives} positive, {negatives} negative).")
    print(f"Gate: verdict acts only at confidence >= {gate:.2f}\n")

    print("On the pairs it acted on:")
    print(f"  correct auto-matches (TP): {tp}")
    print(f"  correct rejects      (TN): {tn}")
    print(f"  missed matches       (FN): {fn}   (would wrongly create a new master)")
    print(f"  \033[1mFALSE MERGES         (FP): {fp}\033[0m   (confident 'same' on a different hotel)\n")

    print("Sent to human review (abstained — no pipeline change vs today):")
    print(f"  on positive pairs: {review_pos}")
    print(f"  on negative pairs: {review_neg}")
    if unavailable:
        print(f"  (of which {unavailable} were LLM-unavailable / errored)")
    print()

    print("Metrics on confident decisions:")
    print(f"  precision (merges that were right): {pct(precision)}")
    print(f"  recall    (positives auto-matched): {pct(recall)}")
    print(f"  reject rate (negatives caught)    : {pct(reject_rate)}")
    print(f"  accuracy                          : {pct(accuracy)}")
    print(f"  abstention rate                   : {pct(abstention)}\n")

    print("Confidence distribution (calibration — watch for clustering at 0.90):")
    for name in _BUCKET_ORDER:
        count = buckets[name]
        if count:
            bar = "#" * min(40, count)
            print(f"  {name:>13}: {count:4d}  {bar}")
    print()

    print("Usage:", service.usage_summary())

    if fp == 0:
        print("\n\033[32mZero false merges at this gate — safe to proceed to a wired shadow/canary.\033[0m")
    else:
        print(
            f"\n\033[31m{fp} false merge(s) at gate {gate:.2f}. Raise --min-confidence "
            "and re-run before wiring this into any publishing path.\033[0m"
        )

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
