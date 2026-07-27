"""
Re-tune the AI decision band (AI_REJECT_BELOW / AI_CONFIRM_AT) for a new
embedding model.

Those two literals — 0.70 and 0.85 — are not model-independent constants. They
are points on *vanilla* all-MiniLM-L6-v2's cosine distribution. Fine-tuning moves
the whole distribution, so carrying the same numbers over silently changes what
the pipeline does: the confirm bar stops meaning "high precision" and the reject
bar stops meaning "safe to discard".

This finds the fine-tuned model's equivalents by *operational meaning* rather
than by number, on the held-out TEST hotels:

  CONFIRM  the lowest cosine at which the HARD negatives — same-city fuzzy and
           brand collisions — are fully separated (0% false merges) and overall
           precision is >= 0.99. Above it, "the model agrees" is safe. The bound
           is set by the hard traps rather than by aggregate precision, because
           the aggregate is dominated by easy negatives and would clear a much
           lower bar than the cases that actually merge two hotels.

  REJECT   the highest cosine that still leaves >= 99% of true matches above it.
           Below this bound a suggested match is cancelled outright and the
           record becomes its own master, so every positive it drops is a split
           that no later record can recover.

Both bounds are also reported per negative kind, because the aggregate is
flattered by easy negatives — the question is whether the model separates the
hard same-city and brand-collision traps.

Run inside the api container (it has the matching sentence-transformers pin):
    docker exec hotel_mapping_api_v2 python -m scripts.tune_embedding_band
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sentence_transformers import SentenceTransformer

BASE = "sentence-transformers/all-MiniLM-L6-v2"
FT = "models/hotel-minilm-ft"

# The band the pipeline runs today, on the vanilla model.
VANILLA_REJECT = 0.70
VANILLA_CONFIRM = 0.85

GRID = np.linspace(0.20, 0.995, 160)


def cosine(model: SentenceTransformer, a: list[str], b: list[str]) -> np.ndarray:
    ea = model.encode(a, convert_to_numpy=True, normalize_embeddings=True, batch_size=256)
    eb = model.encode(b, convert_to_numpy=True, normalize_embeddings=True, batch_size=256)
    return (ea * eb).sum(axis=1)


def pr_at(scores: np.ndarray, labels: np.ndarray, t: float) -> tuple[float, float]:
    pred = scores >= t
    tp = int((pred & (labels == 1)).sum())
    fp = int((pred & (labels == 0)).sum())
    fn = int((~pred & (labels == 1)).sum())
    prec = tp / (tp + fp) if (tp + fp) else 1.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    return prec, rec


def kind_table(scores: np.ndarray, labels: np.ndarray, kinds: np.ndarray, t: float) -> str:
    """False-merge rate per negative kind at threshold t, plus recall on positives."""
    out = []
    pred = scores >= t
    pos = labels == 1
    out.append(f"      recall on true matches : {pred[pos].mean() * 100:5.1f}%")
    for kind in ("neg_city_fuzzy", "neg_core_brand", "neg_easy"):
        m = kinds == kind
        if not m.any():
            continue
        out.append(f"      FMR {kind:15s}: {pred[m].mean() * 100:5.1f}%  (n={int(m.sum())})")
    return "\n".join(out)


def main() -> None:
    df = pd.read_csv("data/finetune/test.csv").fillna("")
    a = df["text_a"].astype(str).tolist()
    b = df["text_b"].astype(str).tolist()
    labels = df["label"].to_numpy()
    kinds = df["kind"].to_numpy()

    print(f"held-out test pairs: {len(df)}  "
          f"(positives {int((labels == 1).sum())}, negatives {int((labels == 0).sum())})\n")

    van = cosine(SentenceTransformer(BASE), a, b)
    ft = cosine(SentenceTransformer(FT), a, b)

    # --- what the current band actually delivers on the vanilla model ---
    v_conf_p, v_conf_r = pr_at(van, labels, VANILLA_CONFIRM)
    v_rej_p, v_rej_r = pr_at(van, labels, VANILLA_REJECT)

    print("VANILLA (the band in production today)")
    print(f"  CONFIRM @ {VANILLA_CONFIRM:.2f}  precision {v_conf_p:.3f}  recall {v_conf_r:.3f}")
    print(kind_table(van, labels, kinds, VANILLA_CONFIRM))
    print(f"  REJECT  @ {VANILLA_REJECT:.2f}  precision {v_rej_p:.3f}  recall {v_rej_r:.3f}")
    print(kind_table(van, labels, kinds, VANILLA_REJECT))

    # --- the fine-tuned equivalents, matched on meaning ---
    # CONFIRM: lowest threshold that fully separates the hard traps and still
    # clears 0.99 aggregate precision. Aggregate precision alone is not enough —
    # 5,452 of the 5,859 negatives are easy, so a threshold can look precise
    # while still merging the same-city pairs this bound exists to stop.
    hard = np.isin(kinds, ("neg_city_fuzzy", "neg_core_brand"))
    confirm = None
    for t in GRID:
        p, r = pr_at(ft, labels, t)
        if p >= 0.99 and not (ft[hard] >= t).any():
            confirm = (t, p, r)
            break

    # REJECT: highest threshold still leaving >= 99% of true matches above it.
    # Below this bound the match is cancelled outright, so a dropped positive is
    # an unrecoverable split — this bound is bought with recall, not precision.
    reject = None
    for t in reversed(GRID):
        p, r = pr_at(ft, labels, t)
        if r >= 0.99:
            reject = (t, p, r)
            break

    # The band has to be ordered, or the "uncertain" region is empty and the
    # REJECT branch shadows the CONFIRM branch entirely.
    if confirm and reject and reject[0] > confirm[0]:
        raise SystemExit(
            f"inverted band: reject {reject[0]:.2f} > confirm {confirm[0]:.2f} — "
            "the model's positives and hard negatives overlap too little for "
            "these two rules to coexist; widen one of them deliberately."
        )

    print("\nFINE-TUNED (models/hotel-minilm-ft) — equivalents by meaning")
    if confirm:
        t, p, r = confirm
        print(f"  CONFIRM @ {t:.2f}  precision {p:.3f}  recall {r:.3f}   "
              "(hard-negative false merges: 0)")
        print(kind_table(ft, labels, kinds, t))
    else:
        print("  CONFIRM: no threshold separates the hard negatives at P >= 0.99")

    if reject:
        t, p, r = reject
        print(f"  REJECT  @ {t:.2f}  precision {p:.3f}  recall {r:.3f}   "
              "(target recall >= 0.99)")
        print(kind_table(ft, labels, kinds, t))
    else:
        print("  REJECT: no threshold retains 99% of true matches")

    # --- what the OLD numbers would do on the NEW model (the silent-change risk) ---
    print("\nIf the old literals were left in place on the fine-tuned model:")
    for name, t in (("CONFIRM", VANILLA_CONFIRM), ("REJECT", VANILLA_REJECT)):
        p, r = pr_at(ft, labels, t)
        print(f"  {name} @ {t:.2f}  precision {p:.3f}  recall {r:.3f}")

    if confirm and reject:
        print("\n" + "=" * 66)
        print(f"  AI_REJECT_BELOW : {VANILLA_REJECT:.2f}  ->  {reject[0]:.2f}")
        print(f"  AI_CONFIRM_AT   : {VANILLA_CONFIRM:.2f}  ->  {confirm[0]:.2f}")
        print("=" * 66)


if __name__ == "__main__":
    main()
