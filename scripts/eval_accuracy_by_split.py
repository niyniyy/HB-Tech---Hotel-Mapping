"""
Accuracy broken down by the fine-tune's train/val/test split.

Why this exists: the model was fine-tuned on pairs mined from `reference_mapping`,
and `reference_mapping` is also what the accuracy evaluation scores against. About
70% of the reference hotels were in the model's TRAINING set, so a single
end-to-end recall number is measured partly on hotels the model was taught. That
number is real — those hotels are real records in the pipeline — but it is not
evidence of generalisation, and quoting it alone would overstate the gain.

The honest number is the **test** row: reference hotels the fine-tune never saw
in any form. Compare train vs test to see how much of the improvement is memory.

The split is reproduced exactly as scripts/build_finetune_data.py made it —
same seed, same sorted-then-shuffled reference id order, same 70/15/15 — so the
partition here is the partition the model was trained under. If that script's
seed or ordering changes, this one must change with it.

    docker exec -e DATABASE_URL=... hotel_mapping_api_v2 \
        python -m scripts.eval_accuracy_by_split
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys

from sqlalchemy import text

from app.database.connection import AsyncSessionLocal

SEED = 42
TRAIN_FRAC, VAL_FRAC = 0.70, 0.85


async def split_of(session) -> dict[str, str]:
    """Rebuild build_finetune_data.py's by-hotel split, reference id -> split."""
    result = await session.execute(
        text(
            """
            SELECT DISTINCT r.reference_id
            FROM supplier_hotels s
            JOIN reference_mapping r
              ON r.supplier_name = s.supplier_name
             AND r.supplier_hotel_id = s.supplier_hotel_id
            WHERE s.hotel_name IS NOT NULL
            """
        )
    )
    # Sort on the raw values, exactly as build_finetune_data.py does — sorting
    # the stringified ids would order 10 before 9 and produce a different, silently
    # wrong partition. Only the returned keys are stringified, to match the
    # lookup side.
    refs = sorted(row[0] for row in result.all())

    rng = random.Random(SEED)
    rng.shuffle(refs)
    n = len(refs)
    train = set(refs[: int(TRAIN_FRAC * n)])
    val = set(refs[int(TRAIN_FRAC * n): int(VAL_FRAC * n)])

    return {
        str(r): ("train" if r in train else "val" if r in val else "test")
        for r in refs
    }


async def main() -> None:
    async with AsyncSessionLocal() as session:
        splits = await split_of(session)

        # reference hotel -> how many of our masters its records landed in
        result = await session.execute(
            text(
                """
                SELECT reference_id, count(DISTINCT master_hotel_id) AS masters
                FROM (
                    SELECT DISTINCT r.reference_id, hm.master_hotel_id
                    FROM reference_mapping r
                    JOIN supplier_hotels s
                      ON s.supplier_name = r.supplier_name
                     AND s.supplier_hotel_id = r.supplier_hotel_id
                    JOIN hotel_mappings hm ON hm.supplier_hotel_row_id = s.id
                ) em
                GROUP BY 1
                """
            )
        )
        rows = [(str(r[0]), int(r[1])) for r in result.all()]

        # false merges are a property of a master, not of a hotel, so they are
        # attributed to a split only when every reference hotel in the offending
        # master belongs to that split; mixed ones are reported separately.
        fm = await session.execute(
            text(
                """
                SELECT master_hotel_id, array_agg(DISTINCT reference_id) AS refs
                FROM (
                    SELECT DISTINCT r.reference_id, hm.master_hotel_id
                    FROM reference_mapping r
                    JOIN supplier_hotels s
                      ON s.supplier_name = r.supplier_name
                     AND s.supplier_hotel_id = r.supplier_hotel_id
                    JOIN hotel_mappings hm ON hm.supplier_hotel_row_id = s.id
                ) em
                GROUP BY 1 HAVING count(DISTINCT reference_id) > 1
                """
            )
        )
        false_merges = [[str(x) for x in row[1]] for row in fm.all()]

    report: dict = {}
    for split in ("train", "val", "test", "ALL"):
        sub = [(r, m) for r, m in rows if split == "ALL" or splits.get(r) == split]
        if not sub:
            continue
        intact = sum(1 for _, m in sub if m == 1)
        total = len(sub)
        fm_n = sum(
            1
            for refs in false_merges
            if split == "ALL" or all(splits.get(r) == split for r in refs)
        )
        report[split] = {
            "hotels_evaluated": total,
            "intact": intact,
            "split": total - intact,
            "pct_intact": round(100.0 * intact / total, 2),
            "false_merges": fm_n,
        }

    print(json.dumps(report, indent=2))

    path = os.environ.get("EVAL_OUT")
    if path:
        with open(path, "w") as handle:
            handle.write(json.dumps(report, indent=2))
        print(f"\nwritten to {path}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
