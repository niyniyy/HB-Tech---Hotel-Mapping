"""
Print the accuracy evaluation for whichever database DATABASE_URL points at.

The same measurement as GET /api/v1/evaluation/accuracy, minus the web app. That
endpoint always reads the API container's configured database, which makes it
useless for the one thing a benchmark needs: scoring a *copy* of production
after re-running the pipeline on it, without touching production.

Set EVAL_OUT to also write the JSON to a file.

    docker exec \
      -e DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/<db> \
      hotel_mapping_api_v2 python -m scripts.eval_accuracy_cli
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from app.database.connection import AsyncSessionLocal
from app.services.accuracy_evaluation_service import AccuracyEvaluationService


async def main() -> None:
    async with AsyncSessionLocal() as session:
        report = await AccuracyEvaluationService(session).evaluate()

        # Master count is not part of the accuracy report — that report is scoped
        # to the reference-mapped subset, while this is the whole table, which is
        # the number the duplicate-master question is actually about.
        from sqlalchemy import text

        totals = await session.execute(
            text(
                """
                SELECT (SELECT count(*) FROM master_hotels)   AS master_hotels,
                       (SELECT count(*) FROM hotel_mappings)  AS mappings,
                       (SELECT count(*) FROM hotel_embeddings) AS embeddings,
                       (SELECT count(*) FROM manual_review_candidates)
                                                              AS review_candidates
                """
            )
        )
        report["totals"] = dict(totals.mappings().first())

    out = json.dumps(report, indent=2, default=str)
    print(out)

    path = os.environ.get("EVAL_OUT")
    if path:
        with open(path, "w") as handle:
            handle.write(out)
        print(f"\nwritten to {path}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
