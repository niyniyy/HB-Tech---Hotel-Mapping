# Incremental Runs — Design Notes

**Status:** Not built. Agreed to build after the current production-readiness
push. Captured 24 July 2026.

**Why:** A full `reset` + run rebuilds ~500k records in ~45 min (measured
components: rule processing ~44 min, embeddings ~1.5 min batched, token refresh
0.2 s, candidate retrieval sub-ms). That is a batch job, not a daily operation.
Incremental turns it into minutes-per-delta.

---

## What already works (the system is half-incremental)

- Import **dedupes** on `(supplier_name, supplier_hotel_id, hotel_name)` — a
  re-imported identical row is skipped (`data_source_service.py` ~line 514).
- Only newly-inserted rows are **enqueued** as Pending.
- `pipeline/run` **without reset** processes only Pending records.

So importing a new supplier and running already processes just the new records
against the existing master set (proven in the Agoda import dry-run). The 45-min
figure is only for `reset` + run, which is a deliberate full rebuild.

Incremental is therefore not a rebuild-from-scratch. It is closing two gaps.

---

## Gap 1 — change detection (mechanical, ~1 day, low risk)

The dedup key includes `hotel_name`, which mishandles updates:

| Supplier re-sends… | Current behavior | Bug |
|---|---|---|
| Same id, same name, new coordinates | skipped as "already present" | corrected coordinate ignored |
| Same id, new name | inserted as a NEW row | two rows for one hotel |

**Fix — content fingerprint (watermark by content, not timestamp; supplier
feeds don't reliably timestamp):**

Store a hash of the meaningful fields (`name + address + city + coords +
postcode`) per record. On import, for each `(supplier, id)`:

- fingerprint matches existing row → **unchanged**, skip
- fingerprint differs → **changed**: update row in place, delete its old
  mapping, re-enqueue
- no such `(supplier, id)` → **new**: insert + enqueue

Exception preserved: the genuine Sabre collision case (same id, different hotel)
must still insert so it gets flagged — do not turn this into a unique constraint.

---

## Gap 2 — mapping drift (the hard decision, ~2–3 days)

Pure "new records only" has a subtle failure: a new record can create a master
that is a better home for an ALREADY-mapped record, but the old record is
`Completed` and never re-evaluated. Over months of incremental runs, splits
accumulate — the ClearTrip-823m case in slow motion.

Three strategies:

- **A. Append-only** — never touch existing mappings. Fastest (minutes), but
  drift accumulates and recall silently decays between full rebuilds.
- **B. Affected-neighbourhood re-scoring (RECOMMENDED)** — when a run creates a
  new master, re-queue already-mapped records within its 1 km radius to check
  whether they now prefer it. Bounds drift to exactly where new masters appear.
  Cost scales with change, not corpus size.
- **C. Incremental + periodic full rebuild** — append-only daily, full 45-min
  rebuild weekly/monthly to reset drift. Safe (anchors hold identity across a
  rebuild) but coarser than B.

**Recommendation: B, optionally with C as a monthly safety net.**

---

## What makes this safe here (already built)

- **Identity survives it.** `master_hotel_anchor` means a re-processed or
  re-queued record reclaims its own public id. Incremental cannot scramble HBM
  ids — held across every reset this session.
- **Reviewer decisions survive it.** Approvals, merges, rejections are persisted
  (`review_attach_decision`, `master_merge_decision`,
  `master_non_merge_assertion`) and replayed at end of run. Incremental only
  touches changed records, so a decision on an untouched hotel is undisturbed.

---

## Open decisions (product, not engineering — decide before building)

1. **Drift strategy: A, B, or C?** The real accuracy-vs-cost call. B recommended.
2. **Re-review on change** — when a changed record had a reviewer decision,
   re-queue it or keep the decision? Recommendation: re-queue — the thing they
   judged has changed.
3. **What counts as a "change" worth reprocessing** — any field, or only
   name/address/coordinates? Star rating changing should NOT trigger a re-map.

---

## Effort

- Gap 1 (change detection): ~1 day, mechanical, low risk.
- Gap 2B (neighbourhood re-scoring): ~2–3 days, touches queue/matcher flow;
  keep the accuracy gate (`/evaluation/accuracy`) watching precision throughout.
- Total: ~1 week. Result: 45-min rebuild → minutes-per-delta for daily ops.

**Next step when picked up:** turn this into a full spec with the table changes
(fingerprint column, changed-record handling) and the neighbourhood re-scoring
algorithm, once the drift strategy is chosen.
