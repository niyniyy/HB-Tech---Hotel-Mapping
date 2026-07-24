"""
Smoke test for the LLM adjudication service (Phase 1).

Runs a handful of known-answer hotel pairs through the service so you can
confirm the wiring — key, model, structured output, cost tracking — before any
of it touches the pipeline. Nothing here writes to the database.

Usage (from the repo root, with the key set in .env or the environment):

    LLM_ENABLED=True python -m scripts.llm_smoke_test

If .env already has LLM_ENABLED=True and LLM_API_KEY set, just:

    python -m scripts.llm_smoke_test
"""

import asyncio

from app.matching.llm_service import LLMService

# (record_a, record_b, distance_m, expected) — expected is for your eyes only;
# the service is never told the answer.
CASES = [
    (
        {
            "hotel_name": "Clarks Inn Suites",
            "address": "Plot 47, Sector 10, Gurugram, Haryana",
            "city": "Gurgaon",
            "postal_code": "122001",
            "latitude": 28.4500,
            "longitude": 77.0200,
        },
        {
            "hotel_name": "DS Clarks Inn",
            "address": "47, Sector 10, Gurgaon",
            "city": "Gurugram",
            "postal_code": "122001",
            "latitude": 28.4510,
            "longitude": 77.0210,
        },
        120.0,
        "SAME — rebrand of one property, same plot/postcode",
    ),
    (
        {
            "hotel_name": "Lemon Tree Premier",
            "address": "Sector 29",
            "city": "Gurugram",
            "latitude": 28.4600,
            "longitude": 77.0600,
        },
        {
            "hotel_name": "Lemon Tree Premier",
            "address": "City Centre, MG Road",
            "city": "Gurugram",
            "latitude": 28.4800,
            "longitude": 77.0900,
        },
        3200.0,
        "DIFFERENT — two branches of one chain in the same city",
    ),
    (
        {
            "hotel_name": "Hotel Mamallaa Heritage",
            "address": "104 East Raja Street",
            "city": "Mahabalipuram",
            "latitude": 12.6200,
            "longitude": 80.1900,
        },
        {
            "hotel_name": "Mamallaa Heritage",
            "address": "East Raja St",
            "city": "Mamallapuram",
            "latitude": 12.6210,
            "longitude": 80.1920,
        },
        150.0,
        "SAME — transliterated city + word-order difference",
    ),
]


async def main() -> None:
    service = LLMService()

    if not service.enabled:
        print(
            "LLM_ENABLED is False — the service is a no-op.\n"
            "Run with:  LLM_ENABLED=True python -m scripts.llm_smoke_test"
        )
        return

    print(f"Running {len(CASES)} cases against {service.usage_summary()['model']}...\n")

    for record_a, record_b, distance, expected in CASES:
        verdict = await service.verify_hotel_match(record_a, record_b, distance)
        print(f"{record_a['hotel_name']}  vs  {record_b['hotel_name']}")
        print(f"  expected : {expected}")
        if verdict.ok:
            print(
                f"  verdict  : same_hotel={verdict.same_hotel}  "
                f"confidence={verdict.confidence:.2f}  ({verdict.latency_ms:.0f} ms)"
            )
            print(f"  reasoning: {verdict.reasoning}")
        else:
            print(f"  UNAVAILABLE ({verdict.error}) — caller would fall back to review")
        print()

    print("Usage:", service.usage_summary())


if __name__ == "__main__":
    asyncio.run(main())
