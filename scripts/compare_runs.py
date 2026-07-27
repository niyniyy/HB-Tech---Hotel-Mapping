"""
Compare two pipeline runs hotel-by-hotel, on the SAME reference hotels.

Aggregate recall from two runs is not directly comparable, because the
denominator moves. A hotel is only "evaluated" if at least one of its supplier
records got mapped; when a run sends more records to manual review instead, those
hotels leave the evaluation entirely — and if the ones that leave were splits,
recall rises without a single hotel actually being fixed.

This joins the two runs per reference hotel and reports:

  * the transition matrix (intact/split/unevaluated -> intact/split/unevaluated),
    which shows what actually changed rather than what the totals imply;
  * recall for both runs restricted to the hotels EVALUATED IN BOTH, which is the
    only apples-to-apples recall comparison available.

Both databases must hold the same `reference_mapping` and `supplier_hotels`; only
the mapping OUTPUT should differ. Comparing runs over different input measures
nothing.

Usage — BASE_URL and NEW_URL are required, and are sync SQLAlchemy URLs:

    docker exec \\
      -e BASE_URL=postgresql+psycopg2://postgres:postgres@db:5432/<before_db> \\
      -e NEW_URL=postgresql+psycopg2://postgres:postgres@db:5432/<after_db> \\
      hotel_mapping_api_v2 python -m scripts.compare_runs
"""

from __future__ import annotations

import json
import os
from collections import Counter

from sqlalchemy import create_engine, text

# Both are required rather than defaulted. Which two databases to compare is the
# entire input to this script, and a default would silently point it at whichever
# scratch database the last person happened to create — producing either a
# confusing "database does not exist" or, worse, a plausible-looking comparison
# of the wrong pair.
BASE_URL = os.environ.get("BASE_URL")
NEW_URL = os.environ.get("NEW_URL")

# reference hotel -> number of distinct masters its records landed in
PER_HOTEL = text(
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

ALL_REFS = text("SELECT DISTINCT reference_id FROM reference_mapping")


def state_of(url: str) -> tuple[dict[str, str], dict[str, int]]:
    engine = create_engine(url)
    with engine.connect() as conn:
        refs = [str(r[0]) for r in conn.execute(ALL_REFS).all()]
        counts = {str(r[0]): int(r[1]) for r in conn.execute(PER_HOTEL).all()}
    # A hotel with no mapped record at all is "unevaluated" — not intact, and not
    # a split. Collapsing it into either is what makes the two runs incomparable.
    state = {
        r: ("intact" if counts.get(r) == 1
            else "split" if counts.get(r, 0) > 1
            else "unevaluated")
        for r in refs
    }
    return state, counts


def recall(states: dict[str, str], universe: set[str]) -> dict:
    sub = [states[r] for r in universe]
    intact = sum(1 for s in sub if s == "intact")
    split = sum(1 for s in sub if s == "split")
    total = intact + split
    return {
        "hotels_evaluated": total,
        "intact": intact,
        "split": split,
        "pct_intact": round(100.0 * intact / total, 2) if total else 0.0,
    }


def main() -> None:
    missing = [n for n, v in (("BASE_URL", BASE_URL), ("NEW_URL", NEW_URL)) if not v]
    if missing:
        raise SystemExit(
            f"Set {' and '.join(missing)} to the database(s) to compare — see the "
            "module docstring for the exact form."
        )

    base, _ = state_of(BASE_URL)
    new, _ = state_of(NEW_URL)

    refs = sorted(set(base) & set(new))

    matrix = Counter((base[r], new[r]) for r in refs)
    order = ("intact", "split", "unevaluated")

    print("transition matrix  (rows = baseline, cols = new run)")
    print(f"{'':14s}" + "".join(f"{c:>14s}" for c in order))
    for row in order:
        cells = "".join(f"{matrix[(row, c)]:>14d}" for c in order)
        print(f"{row:14s}{cells}")

    print("\nwhat actually moved")
    print(f"  split      -> intact      : {matrix[('split', 'intact')]:5d}   (genuinely fixed)")
    print(f"  intact     -> split       : {matrix[('intact', 'split')]:5d}   (regressed)")
    print(f"  split      -> unevaluated : {matrix[('split', 'unevaluated')]:5d}   "
          "(left the denominator, NOT fixed)")
    print(f"  intact     -> unevaluated : {matrix[('intact', 'unevaluated')]:5d}   "
          "(lost coverage)")
    print(f"  unevaluated-> intact      : {matrix[('unevaluated', 'intact')]:5d}")
    print(f"  unevaluated-> split       : {matrix[('unevaluated', 'split')]:5d}")

    both = {r for r in refs if base[r] != "unevaluated" and new[r] != "unevaluated"}

    print("\nrecall, each run on its own evaluated set (what the API reports)")
    print(f"  baseline : {json.dumps(recall(base, {r for r in refs if base[r] != 'unevaluated'}))}")
    print(f"  new run  : {json.dumps(recall(new, {r for r in refs if new[r] != 'unevaluated'}))}")

    print(f"\nrecall on the {len(both)} hotels EVALUATED IN BOTH (apples to apples)")
    b, n = recall(base, both), recall(new, both)
    print(f"  baseline : {json.dumps(b)}")
    print(f"  new run  : {json.dumps(n)}")
    print(f"  delta    : {n['pct_intact'] - b['pct_intact']:+.2f} pp   "
          f"({n['intact'] - b['intact']:+d} intact, {n['split'] - b['split']:+d} split)")


if __name__ == "__main__":
    main()
