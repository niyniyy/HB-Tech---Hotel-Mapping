# Change Log — Accuracy and Scale Remediation

**Date:** 22 July 2026
**Baseline:** commit `ab9c572` (branch `feature/person-b-import-pipeline`)
**Scope:** 14 files modified, 2 added, 889 insertions / 515 deletions
**Companion documents:** `AUDIT_REPORT.md` (findings and evidence),
`sabre_id_collisions.csv` (supplier defect evidence)

---

## Headline

The table below records the morning of **23 July 2026**. The pipeline changed
substantially that afternoon — see
[Session 2](#session-2--23-july-2026-afternoon) for the current state, which
supersedes these figures. Note also that "masters" here counts every row in
`master_hotels`; the publishable figure (Active only) is smaller, and Session 2
gives both.

| Metric | Before | After |
|---|---|---|
| Auto-match precision | 51.7% (hand-verified) | 0 errors in 66 hand-verified pairs |
| Clearly-wrong merges | 1,863 (33.5%) | 0 confirmed |
| Same-supplier collisions (structural FP signal) | not checked | **0 across all 5,366 matches** |
| End-to-end success rate | ~54% | **95.1%** |
| Records stuck in review limbo | 670 | 0 |
| Public id stability across full rebuild | ids reassigned every run | **8,133/8,133 identical** |
| Worker memory at 10 lakh | ~15.3 GB (OOM) | **flat, ~890 MB** |
| Per-hotel status update | 40 ms (seq scan) | **0.393 ms** |
| Concurrency | duplicates under >1 worker | **0 duplicates, 4 workers** |

---

## 1. `app/normalization/normalizer.py` — rewritten

Previously 19 lines of dead code — `normalize_hotel_name` was never called by
the pipeline.

| Change | Reason |
|---|---|
| `core_hotel_name()` strips the hotel's **own city and state** out of its name before comparison | "Vivanta Coimbatore" scored 71% against "WelcomHotel Coimbatore" purely on the shared city token. Highest-impact single change. |
| Strips chain marketing boilerplate (`a member of …`, `an Accor brand`, `IHCL SeleQtions`, `Series by Marriott`, …) | Carries no identifying information; dilutes real signal |
| Never reduces a name to nothing — falls back to the full normalized name | Early version dropped "O Hotel Pune" and "The Residency Chennai" to empty strings |
| Narrowed the stop-word list — keeps `residency`, `grand`, `premier`, `palace` | These ARE distinguishing in Indian hotel names |
| `compare_hotel_names()` returns **both** loose (`token_set_ratio`) and strict (`token_sort_ratio`) similarity | `token_set_ratio` scores a subset as 100%: "The Residency" matched "The Residency Towers", two different Chennai hotels |
| Conflicting ordinals short-circuit to zero | "Leisure Valley 1" vs "Leisure Valley 2" are different properties |
| `_distinguishing_parts_agree()` — removes the words two names share and compares the remainder | Shared **locality** tokens inflate similarity the same way city names do: "Namah Resort Jim Corbett" vs "Voco Jim Corbett" agree on 2 of 3 tokens. Stripping the common words leaves "namah" vs "voco". |

## 2. `app/matching/matcher.py` — rewritten

| Change | Reason |
|---|---|
| Weights rebalanced to `name 45 + address 20 + geo 25 + star 5 + chain 5` | Identity now carries 65 of 100 points |
| **Geo decays with distance** instead of paying a flat 85 | Old design: any hotel within 1 km got 85 of the 90 needed to auto-match, so name similarity could be **zero** and the match still succeeded |
| `DISTANCE_TIERS` — tolerance scales with name confidence: identical name → 1 km, weak name → 200 m | Hand verification showed 83% precision ≤ 200 m vs 23% beyond. Tiering recovered the recall a flat cut cost (duplicate masters 835 → 106) |
| Longer tiers require the **strict** (length-sensitive) similarity | Prevents "X" absorbing "X Towers" at distance |
| `SINGLE_TOKEN_MAX_METERS = 100` | "Taj Agra" reduces to the bare token `taj`, which matches every Taj in the city. Caused a confirmed false positive at 469 m |
| A candidate failing the tier gate can **never** auto-match, whatever its composite score | Proximity alone must not produce a match |
| `confidence_tier()` — TIER1…TIER4 per mapping | Lets 91% publish unattended while residual risk concentrates in a bounded 8.8% |
| Removed the unreachable `> 1000 m` fallback branch | Candidate search capped at 1 km, so it never executed. It was also capped at 70 points — below the 75 review threshold — so it could never have produced a match anyway |

## 3. `app/services/matching_service.py`

| Change | Reason |
|---|---|
| **Removed the exact-city join** — candidate search is now geo-first (country + radius) | `LOWER(m.city)=LOWER(s.city)` meant Bangalore/Bengaluru, Gurgaon/Gurugram never became candidates. Caused 607 duplicate master pairs |
| `LIMIT 10` → `LIMIT 25` | In dense clusters the correct master did not reach scoring |
| **Structural invariant** — `NOT EXISTS`: a master already holding a different hotel from this supplier is excluded | A supplier does not list one property twice under different names. Highest-precision FP signal available, now *enforced* rather than detected |
| `check_record_completeness()` — coordinates present/valid/in-country, name, city, country | Previously a record with no coordinates found no candidates and was **silently promoted to a master** |
| `supplier_id_collision()` distinguishes `SUPPLIER_ID_COLLISION` from `DUPLICATE_SUPPLIER_ROW` | Same id + different hotels is a supplier defect; same id + same hotel is a harmless duplicate line |
| Tier-passing candidates always outrank non-passing ones | A merely-nearby hotel can no longer win on composite score |
| Names compared on raw `hotel_name` (with city context) rather than the supplier's `normalized_name` | The supplied `normalized_name` was inconsistent across suppliers |

## 4. `app/matching/ai_integration_service.py`

| Change | Reason |
|---|---|
| Added lower rejection bound `AI_REJECT_BELOW = 0.70` — below it the suggested match is cancelled and the hotel becomes its own master | The branch was binary (`>= 0.85` promote, everything else → human). An AI score of 0.24 went to a human exactly like 0.84. **602 of 670 (89.9%) of the review queue scored below 0.70** |
| "No AI matches found" now resolves to `CREATE_NEW_MASTER` instead of `MANUAL_REVIEW` | No evidence of a match is evidence of no match |

## 5. `app/services/queue_processing_service.py`

| Change | Reason |
|---|---|
| `claim_pending_batch()` — single `UPDATE … WHERE id IN (SELECT … FOR UPDATE SKIP LOCKED)` that claims and marks Processing atomically | Plain `SELECT` let concurrent workers claim the same rows, producing duplicate mappings and masters |
| `process_pending_batch()` returns **counters only** | It appended every result — supplier row plus up to 10 scored candidates, ~16 KB/hotel — to a list living until the queue drained: **~15.3 GB at 10 lakh**, worker OOM-killed |
| Handles the `FLAGGED` outcome; writes `flagged_records` | Unusable records are reported, never mapped or promoted |
| AI validation keyed off the decision, not a hardcoded 75–90 score window | The window no longer matched the rescaled scoring |
| Dry-run terminates at `Skipped` instead of returning to `Pending` | `apply_decision=false` looped forever, re-claiming the same rows |
| `print()` → `logging` | Observability; `print` in the hot path |
| Removed redundant per-hotel "Processing" write | The claim already sets it — halves the status writes |

## 6. `app/database/connection.py`

| Change | Reason |
|---|---|
| `NullPool` → pooled engine (`pool_size`, `max_overflow`, `pool_pre_ping`, `pool_recycle=1800`) | A fresh TCP connection per session; at 10 lakh that is millions of handshakes and connection-slot exhaustion under concurrency |
| `get_db()` no longer commits | Double-commit: the dependency committed *and* services committed, so a request could commit a half-finished unit of work |

## 7. `app/matching/embedding_service.py` / `master_embedding_service.py`

| Change | Reason |
|---|---|
| `asyncio.to_thread` wrappers on all encode calls | `model.encode` is CPU-bound and blocking; calling it from async code stalled the event loop, including database I/O |
| Batch encoding (`generate_embeddings`, batch_size 64) + `executemany` insert | Was one encode and one INSERT per hotel |
| Thread-safe double-checked model load | Four workers could each start loading the model concurrently |

## 8. `app/services/hotel_mapping_service.py`

| Change | Reason |
|---|---|
| Mappings store **evidence**: `name_similarity`, `name_similarity_strict`, `distance_meters`, `confidence_tier` | A false merge must be auditable and reversible after the fact, not merely rare |
| Added `supplier_hotel_row_id` referencing `supplier_hotels.id` | `(supplier_name, supplier_hotel_id)` is **not unique in supplier data** — Sabre reuses 89 ids across 212 rows — so a mapping keyed on it cannot resolve to one property |

## 9. `app/services/integrity_monitor.py` — **new**

Continuous self-audit, exposed at `GET /api/v1/mapping/integrity`. Detects the
*shape* of a bad merge without ground truth:

- **Same-supplier collisions** — the enforced invariant, verified across every mapping
- **Absorbing masters** — spread > 400 m, or weak worst-case name agreement

Reports only; never mutates mappings. Intended to run after every batch and
before publishing to a booking path.

> Reading its output: a master with ~1 km spread but 100% name agreement is a
> **coordinate discrepancy between suppliers, not a merge error**. Absorption is
> indicated by wide spread *together with* weak names.

## 10. `scripts/init.sql`

| Change | Reason |
|---|---|
| `idx_queue_supplier_hotel_id` | **The single highest-leverage change in the codebase.** `update_queue_status` filters on this column twice per hotel; without the index it sequentially scanned the whole queue — 40 ms vs 0.047 ms at 1M rows, an 850× penalty |
| `idx_queue_status_id` composite | The claim query filters on status and orders by id |
| `flagged_records` table | Backing store for the `FLAGGED` outcome |
| `UNIQUE(supplier_hotel_row_id)` on `hotel_mappings` | One mapping per physical supplier row |
| Foreign keys on mappings, queue, flagged records | Referential integrity previously depended entirely on application code |
| IVFFlat `lists` 100 → 1000 | 100 suits ~10k vectors; 10 lakh needs ~1000 or search degrades toward a sequential scan |

Note: tables are created **only** from this file — there is no `create_all`
anywhere — so the `index=True` declarations in `app/models/hotel.py` never
reached the database. That is why the queue index was missing.

## 11. `scripts/tune_for_scale.sql` — **new**

Post-bulk-load routine: `ANALYZE` all tables, rebuild the IVFFlat index sized to
`sqrt(row_count)`, `VACUUM ANALYZE`. Run after each large import.

## 12. `config.py`, `app/api/mapping.py`, `scripts/run_full_mapping.py`

- Pool sizing and `QUEUE_CLAIM_BATCH` exposed as settings rather than hardcoded
- `GET /api/v1/mapping/integrity` endpoint added
- `run_full_mapping.py` reads counters instead of the removed `results` list

---

## 13. `app/services/master_identity_service.py` — **new** (stable public ids)

`master_hotel_id` is an internal auto-increment key that **every full rebuild
reassigns**. It must never be published. Consumers now receive `public_id`
(`HBM-00000001`), which survives rebuilds and carries merge history.

| Table | Purpose |
|---|---|
| `master_hotel_registry` | `public_id` → current `master_hotel_id`, status (Active/Merged/Deprecated), `superseded_by` |
| `master_hotel_anchor` | Which supplier rows have ever belonged to which `public_id`. **Never truncated** — this is what makes ids survive a rebuild |
| `master_hotel_lifecycle` | Audit trail: CREATED / REUSED / MERGED / SPLIT / DEPRECATED |

**How stability works.** On a rebuild, a master seeded by a previously-seen
supplier row reclaims that row's original `public_id` rather than minting a new
one. When a row that already belongs to id A is mapped into a master carrying id
B, the two describe one property: the **older id always survives** (so the
outcome does not depend on processing order), the younger is marked `Merged`,
every anchor pointing at it moves to the survivor, and it keeps resolving
forward. A consumer holding a retired id is never broken.

**Verified:** two consecutive full rebuilds — masters and mappings truncated,
registry preserved — reproduced **all 8,133 public ids identically**.

> A first implementation retired the younger id but then anchored the row to it,
> orphaning 4 of 6 merges. `anchor_for_row()` now resolves forward before use and
> `merge()` returns the survivor.

## 14. Postal code — corrected assessment

Initially reported as a "free win". **That was wrong**, and measuring it changed
the design:

| Evidence | Same hotel | Different hotels < 300 m |
|---|---|---|
| Postal codes **agree** | 87.7% | 74.8% |
| Postal codes **differ** | 12.3% | 25.2% |

Agreement is nearly uninformative — a pincode covers a whole locality, so most
different neighbouring hotels share one. Only **disagreement** carries signal:
about twice as likely between different hotels. So it is applied as a penalty
(6 points) that also caps the distance tier at 300 m, never as a bonus and never
as a veto — 12.3% of genuinely-matching pairs disagree through supplier error.

**An import bug had to be fixed first.** pandas read numeric postal columns as
floats, so three suppliers stored `"110001.0"` and two stored `"110001"`; every
cross-supplier comparison failed on formatting alone. Agreement between records
for the same hotel measured **45.9% before the fix and 87.7% after**. One
supplier also ships state names in the postal field; those now become NULL.
Fixed in `import_service._clean_postal_code()`.

## 15. Room mapping — removed from the gap list

Confirmed by HummingBird as an existing, live product. `ARCHITECTURE_COMPARISON.md`
listed it as a critical gap; that no longer applies.

---

## Verification performed

| Check | Result |
|---|---|
| Hand-adjudicated pairs (final config) | **66 — 0 false positives** (19 lowest-similarity, 25 random, 22 riskiest long-distance) |
| Structural invariant across **all** 5,366 matches | **0 violations** |
| 10-lakh scale test (1,000,000 supplier hotels, 336k masters) | Memory flat 881 → 889 MiB over 10,000 hotels |
| 4 concurrent workers | 0 duplicate mappings, 0 hotels processed twice |
| Sustained throughput | 47 hotels/sec/worker, CPU-bound (DB at 11.85%) |
| Full pipeline rerun on real data | 0 failed records |

Synthetic test data averaged **2,061 masters within 1 km** per hotel versus
**6.8** in the real data — a ~300× denser stress test, so 47/sec is a
worst-case floor.

---

## Re-verification — 23 July 2026

Re-run read-only against the live `hotel_mapping_db_v2`. No remap was performed;
nothing was written. The database has moved since this document was first
written — 89 records that were previously auto-matched are now discarded under a
two-reason flag taxonomy.

| Metric | As first documented | Now |
|---|---|---|
| Input records | 8,432 | 8,432 |
| Discarded | 210 (`DUPLICATE_SUPPLIER_KEY`) | **299** (212 `SUPPLIER_ID_COLLISION` + 87 `DUPLICATE_SUPPLIER_ROW`) |
| Mapped | 8,222 | **8,133** |
| Auto-matched | 5,455 | **5,366** |
| New masters | 2,767 | 2,767 |
| Redundant masters | 106 | **114 pairs, 217 distinct masters** |
| End-to-end success | 96.3% | **95.1%** — 5,366 + (2,767 − 114) = 8,019 / 8,432 |

The master count is byte-identical across both runs, which is the determinism
signal that matters: the delta is entirely in what gets discarded, not in how
masters are formed.

**Precision — nothing wrongly merged.** Same-supplier collisions 0; duplicate
supplier keys in `hotel_mappings` 0; registry orphans, split identities and
masters without a public id all 0; review limbo 0; failed records 0.

The integrity monitor reports **185 absorbing masters**, all of which are false
alarms. Every one trips the >400 m spread rule only — none trips the weak-name
rule, and 180 of 185 carry an identical name on every member. Hand-reading the
six widest, each is one hotel where a single supplier's coordinate is wrong
(`HBM-00000745` Royal Court Madurai: four suppliers agree on "No 4 West Veli
Street, Opp Railway Station", one pin sits 930 m away). The geo outlier rotates
across suppliers — Sabre 91, Booking.com 43, GRN 21, ClearTrip 20, Hummingbird
20 — which is the signature of feed coordinate noise, not of the matcher
over-merging. Note that address trigram similarity is useless as a check here:
92 of 195 score below 0.3 purely because suppliers write the same address
differently.

**Recall — the split problem is larger than the 114 figure suggests.** Beyond the
114 pairs within 150 m, a further **268 pairs share an identical name *and* an
identical city string** yet sit under different masters. 46 are 151–489 m apart
and 37 are 516–937 m; those ~83 are strong split candidates (Walisons Hotel
Srinagar 160 m, Taj Tirupati 210 m, The Oberoi Mumbai 260 m, Conrad Pune 298 m).
The 56 pairs beyond 5 km are genuinely different properties sharing a name.
Cause is the 1 km candidate radius (`matching_service.py:135`) against a TIER4
ceiling of 999 m: a pair whose coordinates disagree by more than the tier allows
can never merge. 884 of 2,767 masters (32%) hold a single supplier record.

**Supplier coverage.** Sabre 84.4% (1,623/1,922); Hummingbird, GRN, Booking.com
and ClearTrip all 100%. Every one of the 299 discards is Sabre.

Confidence tiers: TIER1 3,670 · TIER2 1,261 · TIER3 241 · TIER4 194.

### Caveat on the structural invariant

`master_non_merge_assertion` is **empty**, so the "0 violations" result currently
proves nothing — no reviewer split has been performed yet. The check becomes
meaningful only once the review console has been used in anger.

---

## Are the 299 discards correct?

Audited in full on 23 July 2026. **251 of 299 are correctly discarded; 48 are
not.** Both errors trace to one line: `supplier_id_collision()`
(`app/services/matching_service.py:193-215`) separates the two cases with
`count(DISTINCT lower(hotel_name)) > 1` — exact string equality — which is
brittle in both directions.

**`DUPLICATE_SUPPLIER_ROW` — 87 rows, 61 groups. Correct.** Every group retained
exactly one mapped row; none was lost entirely. 28 groups have members more than
200 m apart, which looks alarming, but 25 of those 28 have matching addresses —
"336 0 Village Calwaddo, Benaulim" vs "336/0, Village Callavado, Benaulim" for
Azaya Beach Resort, 28.9 km apart on paper. The distance is a bad coordinate in
the feed, not a second property. Only 3 groups have genuinely differing
addresses and deserve a look.

There is a real defect here even so. The rule never consults geography, so the
surviving row is whichever was processed first. Among the 16 groups spread over
1 km, only **3 kept a row whose coordinate agrees with other suppliers — 13 kept
an isolated row**, meaning the master was anchored to the wrong location. This
feeds the split problem directly: Walisons Hotel Srinagar appears both in this
list and as three separate masters.

**`SUPPLIER_ID_COLLISION` — 212 rows, 89 groups. 68 groups correct, 21 not.**
The 68 are genuinely different properties sharing one Sabre id — Goldfinch
Retreat Devanahalli vs Bangalore 16 km apart, Niraamaya Surya Samudra against a
supplier tombstone literally named "Zz To Be Deleted- Niraamaya". Discarding
these is right and the repair belongs with Sabre, not here.

The remaining **21 groups (48 rows) are one property listed twice under a name
variant** and should have been deduplicated, not dropped:

| Sabre id | Names | Apart |
|---|---|---|
| 100100868 | "Hotel Mamallaa Heritage" / "Mamallaa Heritage Hotel" | 40 m |
| 100554552 | "Chennai The Belstead" / "The Belstead Chennai" | 35 m |
| 100625242 | "Grand Palace Hotel & Spa" / "Grand Palace Hotel Spa" | 10 m |
| 100104366 | "Clarks Inn Gurgaon" / "DS Clarks Inn Gurgaon" | 3 m |
| 100247162 | "Cavala Seaside Resort" / "Cavala The Seaside Resort" | 13 m |

41 of those 48 rows describe a hotel already present in the master set via
another supplier, so what is lost is Sabre's link to a known property rather
than the property itself. Across all 299 discards, **121 rows (103 collision, 18
duplicate) describe hotels absent from the master set entirely** — that is the
true coverage loss.

**Fix, not yet applied:** replace the string-equality test with the same
name-similarity-plus-distance discriminator the matcher already uses — similar
name and close together means duplicate row, keep one; different name or far
apart means true collision, flag all — and when deduplicating, prefer the row
whose coordinate agrees with other suppliers rather than the first one seen.

---

## Session 2 — 23 July 2026 (afternoon)

Everything below was found by pulling on something that looked wrong in the
review console. Each item is a defect the metrics of the morning run did not
show, which is the point worth carrying forward: the counts were healthy through
all of it.

### A note on "masters", because the word meant two things

Earlier sections in this document use "masters" for the row count of
`master_hotels`. The console shows a different number, and both are right:

| | Count | Meaning |
|---|---|---|
| `master_hotels` rows | **2,165** | every master that exists |
| ├ Active | **1,734** | corroborated by ≥2 suppliers — **publishable** |
| └ Provisional | **431** | one supplier's unconfirmed claim |

A master seeded by a single supplier registers as Provisional and is excluded
from exports until a second supplier attaches or a reviewer confirms it. Figures
before this section are the raw count; the publishable figure is smaller.

(The sidebar badge reads 1,822, which counts *Active registry rows* rather than
active masters — 88 of those ids belong to earlier runs whose seeding record now
sits in review. They retain identity so it can be reclaimed. Cosmetic, but the
badge and the table disagree by 88.)

### Current state

| Metric | Value |
|---|---|
| Input records | 8,432 |
| Mapped | 7,382 (5,217 auto + 2,165 new master) |
| Masters | 2,165 total · **1,734 Active** · 431 Provisional |
| Manual review | 755 |
| Discarded | 295 |
| Confidence | TIER1 3,739 · TIER2 938 · TIER3 320 · TIER4 220 |
| **Masters with no public id** | **0** |
| **Same-supplier collisions** | **0** |
| **False merges (published, vs Vervotech)** | **0 of 2,173** |
| False merges including held mappings | 2 (99.91% clean) |
| Recall vs Vervotech | 1,296 / 1,714 = 75.6% |
| Mappings published / held | 6,842 / 540 |
| Duplicate master pairs (<150 m) | 28 |
| Same name + city, different masters | 41 |

Review queue by kind:

| Kind | Rows | Avg confidence |
|---|---|---|
| `EXACT_NAME_GEO_CONFLICT` | 557 | 68.2 |
| `AI_SEMANTIC_DUPLICATE` | 131 | 90.3 |
| `CITY_STRIPPED_NAME_MATCH` | 67 | 72.4 |

### 1. The AI was not merely unused — it was manufacturing duplicates

`enrich_candidate` gated on `75 <= rule_score < 90`, literals left over from the
old 0-100 scale. It is only ever called for candidates whose decision is
MANUAL_REVIEW, whose scores live in `[30, 40)`. Every one fell past that band
into the final `else` and became `CREATE_NEW_MASTER` **without the AI ever being
consulted**. Bounds now read `MANUAL_REVIEW_MIN_SCORE` / `AUTO_MATCH_MIN_SCORE`
from the matcher so they cannot drift apart again.

Measured afterwards: the band is still empty. 0 of 400 sampled candidates score
inside it, because the tier gate is a hard cut — candidates clear it and score
high (AUTO average 86) or fail it and go straight to a new master. **Place 1
contributes no decisions on this data.** Left in place because it is now correct
and a future weight re-fit could repopulate the band, but it should not be
counted as working machinery.

### 2. Pre-creation safety net (the AI's real job)

Before a record becomes its own master, ask whether an existing master nearby
*means* the same hotel. Escalates to review, never merges.

- Scoped by **geography, not city string** — a city join is what produced 607
  duplicate masters (Bangalore/Bengaluru, Gurgaon/Gurugram) and rebuilding it
  inside the layer meant to catch those consequences would be circular.
- 5 km radius, matching the exact-name escalation.
- Wrapped so an embedding failure leaves the previous behaviour intact.

Catches **131 records per run** that the rule engine structurally cannot reach —
pairs whose coordinates disagree by more than the 1 km candidate radius, so they
were never compared at all. Verified on the case that started it: ClearTrip's
"Zone By The Park Coimbatore" sits 3,177 m from its twin and scores 0.92.

Honest caveat: at the 0.85 bar it also surfaces chain sub-brands at one location
— `Lemon Tree Premier Delhi Airport` vs `Lemon Tree Hotel, Delhi Airport` at
2.1 km are different properties. Safe because it only ever escalates. If
reviewers reject most of them, raise the bar.

### 3. Star and chain were unscoreable; two measured features replace them

`chain_name` is null on **all 8,432** records and `star_rating` on 6,878 — and
the 1,554 that have one are all from a single supplier, so a cross-supplier pair
could never have both sides populated. Ten of the nominal 100 points were
unreachable, silently rescaling every threshold: `AUTO_MATCH_MIN_SCORE` of 40 was
really 40 of an attainable 90.

Replacements were chosen by measuring 11,395 positive pairs against 36,028 **hard
negatives** (records in different masters within 1 km — the pairs the matcher
actually has to separate, not random ones):

| Feature | m | u | m/u | bits |
|---|---|---|---|---|
| Building number agrees | 0.884 | 0.073 | **12.1×** | 3.60 |
| Rarest shared token df ≤ 20 | 0.685 | 0.060 | **11.4×** | 3.51 |
| *postal code agrees (for scale)* | *0.877* | *0.748* | *1.2×* | *0.23* |

**Building number** — first digit run in the address. `33/3, Avinashi Road` and
`33, Avinashi Rd` both reduce to 33; `04` and `4` are the same door. Bonus only;
45% of pairs have no number and absence is a fact about supplier formatting, not
about the hotel.

**Shared-token rarity** — how distinctive is the rarest word two names share.
`hotel` appears in 2,138 records, `radisson` in 486, a hotel's actual
identifying word in two or three. Attacks the "lemon tree premier is several real
hotels in one city" failure directly. Backed by `name_token_df`, rebuilt on
import *and* before every run, because more than one code path writes
`supplier_hotels` and a feature correct on only some paths is worse than one
that is absent.

Effect: positives gained 5.5 points, hard negatives 0.55 — separation 61.0 → 65.9.
Outcome counts barely moved, because the tier gate was already deciding almost
everything (false positives pinned at 0.38% across thresholds 38–50). The gain is
in candidate *ranking*, and in a score that finally means something across its
full range.

### 4. Four defects fixed

**Stranded queue rows.** `asyncio.run()` builds a new event loop per Celery task
while the engine is a module global whose pooled asyncpg connections stay bound
to the loop that created them; the second task in a worker process died with
"got Future attached to a different loop". It had claimed 117 rows as
`Processing`, and because the drain loop only ever looked for `Pending`, the next
task reported **"No pending hotels remaining"** and Celery logged success — with
117 records unprocessed and uncounted. Fixed three ways: dispose the pool per
task; give claims a `claimed_at` and treat a claim older than 15 minutes as
abandoned; count abandoned claims as waiting in `/pipeline/run` and report them
as `stalled` in `/pipeline/status`.

**Orphaned masters — three attempts, and the first two were wrong.** 75 masters
ended a run with no public id: unpublishable, blank in exports. Two supplier rows
that shared an id in an earlier run get split across two masters in a later one,
both reclaim it, and an unconditional `UPDATE` handed it to whichever registered
last.

1. `SELECT` then `UPDATE` in `register_master` — closed the single-threaded case,
   75 → 34, failed under `--concurrency=4` because a read cannot see another
   transaction's uncommitted claim.
2. Made that `UPDATE` conditional so it became a row lock. Correct, fired 122
   times — but orphans only went 34 → 38: a second path was doing the same thing.
3. `attach_to_master` merged identities even when the record's previous id was
   still the live identity of *another* master. That is not a merge; the run had
   deliberately kept those properties apart and the record was simply moving
   between them.

The general bug in both places was **check-then-act across transactions**; the
fix in both is to put the condition inside the `UPDATE`. Result: **0 orphans**,
with the two guards firing 122 and 64 times respectively, so the run genuinely
exercised the paths rather than avoiding them.

**Ranking ignored geography.** The candidate sort key was
`(tier_passed, exact_name_class is not None, rule_score)`. Among candidates that
*passed* the gate, the exact-name flag was compared before the score, so it
overrode it. ClearTrip's Zone Connect record scored 87.83 against a master 9 m
away and 74.57 against one 823 m away, and was mapped to the far one purely
because that master's name matched character for character. The exact-name
preference is right — but only among candidates that **failed** the gate, where
the question is which one deserves a human. Now scoped that way. 1.2% of sampled
AUTO mappings re-target; masters fell 2,175 → 2,133 and duplicate pairs 26 → 20.

**Discard classification.** `supplier_id_collision` split its two cases on
`count(DISTINCT lower(hotel_name)) > 1` — exact string equality, brittle both
ways. "Hotel Mamallaa Heritage" and "Mamallaa Heritage Hotel" are one hotel 40 m
apart and were discarded entirely; rows with byte-identical names 28 km apart
were deduplicated as if they were the same line. Replaced with two tiers, the
same "distance buys tolerance for a weaker name" shape the matcher uses:

```
within 200 m → core-name similarity ≥ 0.60
within  50 m → core-name similarity ≥ 0.30
```

The second tier exists because co-location outweighs a name mangled by a brand
prefix: `Clarks Inn Gurgaon` vs `DS Clarks Inn Gurgaon` are 3 m apart with core
names agreeing 0.389. Which copy survives is now deterministic too — the row
corroborated by the most *other* suppliers, not whichever worker arrived first,
which previously anchored 13 masters to a pin nobody else agreed with.

**The total did not change: 295 before, 295 after.** ~21 groups were recovered
and ~28 wrongly-kept groups became correct collisions, and the two offset. The
win landed elsewhere: 63 of 150 flagged groups now keep a hotel, and mapped rose
by 92 with review falling by 92.

### 5. Reviewer tooling

`POST /manual-review/{id}/attach` — attach a queued record to a master the
reviewer chooses, not only the suggested one. The queue previously offered
accept / new master / reject and had no answer for "right idea, wrong hotel"; a
reviewer who knew the correct master could only reject and hope, which the next
run would not fix, since the run that produced the suggestion is the run that
already failed to find the better one.

The picker is seeded from the record itself and shows distance, name agreement
and provider count per candidate — a reviewer choosing from a bare list of names
is doing the matcher's job more slowly and with less information.

Every choice is recorded in `review_attach_decision` as a labelled pair, keyed on
**public id** so it survives a pipeline reset. A disagreement is simultaneously a
positive for the chosen master and a negative for the suggested one — the two
labels the thresholds most need and currently have none of. Ground truth
accumulates as a by-product of work someone is doing anyway.

### 6. Reset endpoint

`POST /mapping/reset`, needed to iterate on weights at all. Clears mapping
output; preserves supplier records, public ids, anchors and reviewer decisions;
**nulls `master_hotel_registry.master_hotel_id`**, which is the step whose
absence leaves published ids aimed at hotels they do not describe; does **not**
restart sequences, because internal ids are disposable by design and restarting
guarantees reuse. Requires `{"confirm": "RESET"}` in the body — the endpoint is
directly callable, so nothing in the UI can be the safeguard.

Identity survives it: **4,300+ `REUSED` events against ~25 `CREATED`** on a full
rebuild.

### 7. Accuracy, finally measured

HummingBird already runs Vervotech over this inventory. Those mappings are a
ground-truth oracle — **8,575 labelled pairs across 1,715 hotels**, at no
labelling cost, covering essentially the whole dataset. Loaded as
`reference_mapping` (in `init.sql`, and deliberately outside the reset's
TRUNCATE list — it is evidence, not output).

This replaces the entire Phase 0 labelling effort the plan called for, and turns
"is it accurate?" from a debate into a query.

#### Results

| | Value |
|---|---|
| **False merges in published data** | **0 of 2,173 masters** |
| False merges including held mappings | 2 (99.91% clean) |
| Recall — reference hotels intact in one master | 1,296 / 1,714 = **75.6%** |
| Split across 2 masters | 377 |
| Split across 3 | 39 |
| Split across 4 | 2 |

Precision and recall are reported together always. The system is tuned to trade
the second for the first — a false merge sends a guest to the wrong property and
is unrecoverable, a split costs coverage and a reviewer can fix it — so a change
that improves one at the other's expense is the normal case and must be visible
as such.

#### The publication gate

Precision was measured **per tier policy** rather than guessed:

| Policy | False merges | Auto-published |
|---|---|---|
| TIER1 only | 0 | 1,483 |
| **TIER1 + TIER2** | **0** | **2,051** |
| TIER1+2+3 | 2 | 2,255 |
| All tiers | 2 | 2,403 |

Both surviving false merges come from TIER3. TIER1 and TIER2 have never produced
one across 1,714 hotels, so `PUBLISH_TIERS = "TIER1,TIER2"` is the widest policy
that is provably clean rather than a conservative guess.

Enforced through a single predicate in `app/services/publication_gate.py` that
every outward-facing query composes, substituted into export SQL at build time.
A gate enforced in one export and missed in another is not a gate.

**Held is not discarded.** The 540 withheld TIER3/TIER4 mappings (10.4% of
auto-matches) stay in the database and keep attracting later records, so recall
is unchanged at 75.61% either way. They are simply not asserted to anyone until
a person confirms them.

Human decisions publish regardless of tier — `NEW_MASTER`, `MANUAL`, or anything
`is_manual_verified`. The gate holds unverified machine decisions, and a
reviewer's decision is verified by definition.

#### Sub-brand collisions — the 8 false merges

Every confirmed false merge was a chain running two brands from one building:

```
Lemon Tree Hotel        ←→  Red Fox by Lemon Tree Hotels
Lemon Tree Premier      ←→  Red Fox Hotel Delhi Airport
Radisson Blu Kaushambi  ←→  Radisson Blu Towers Kaushambi
Hyatt Regency Pune      ←→  Hyatt Pune Kalyani Nagar
Golden Tulip Jaipur     ←→  Golden Tulip Essential Jaipur
juSTa Off MG Road       ←→  juSTa MG Road
```

Same street, same coordinates, and one name a clean superset of the other —
which `_distinguishing_parts_agree` deliberately tolerated, because "Wow" and
"Wow Crest" usually *are* the same place. Geography cannot separate them and
fuzzy matching actively rewards the overlap.

`BRAND_TIER_TOKENS` names the distinguishing words explicitly — `fox, towers,
tower, regency, essential, premier, collection, select, express, signature` —
and vetoes a match when one appears on a single side. Checked *before* the
superset rule, which is the hole they fell through.

Result: **8 false merges → 2**, at a cost of **one split in 1,714** (0.05%
recall). Verified that legitimate matches are unaffected: `Zone by The Park` ↔
`Zone By The Park` 100/100, `The Lodhi` ↔ `The Lodhi: A member of The Leading
Hotels` 100/100, `Wow Hotel` ↔ `Wow Crest` 100/50.

The two survivors are the same case from both directions — juSTa Off MG Road vs
juSTa MG Road, two real properties separated only by the word "Off". `off` is a
preposition suppliers drop inconsistently, so adding it to the veto would cost
real matches elsewhere for one case. Left for the reviewer.

#### The harness

| Endpoint | Returns |
|---|---|
| `GET /api/v1/evaluation/accuracy` | precision, recall, coverage, published-vs-held, split distribution |
| `GET /api/v1/evaluation/false-merges` | every offending master with both reference hotels |
| `GET /api/v1/evaluation/splits` | worst first, flagged by whether a record is already in review |

**Run it after every pipeline change.** It would have caught the candidate-ranking
bug immediately, and it turns "did that help?" into a diff.

#### What these numbers do and do not claim

Zero errors in 1,714 hotels bounds the true false-merge rate below roughly
**0.17% at 95% confidence** (rule of three) — a far stronger claim than the 4.5%
that 66 hand-verified pairs supported, and measured on real inventory rather
than a sample. It is **not** a guarantee of zero.

It is also a statement about *this* data. See "Will it hold on new data?" below.

### Will it hold on new data?

Not automatically, and the honest answer matters more than the headline number.
Six things were fitted to *this* corpus and will drift when the data changes.

**1. `BRAND_TIER_TOKENS` is a hand-curated list of ten words** derived from eight
observed cases. It catches Red Fox beside Lemon Tree and Blu Towers beside Blu.
It knows nothing about Courtyard beside Residence Inn, Vivanta beside Ginger, or
Novotel beside ibis — all the same failure, all invisible to this list. **This is
the most fragile thing in the system**, and a new chain will reproduce the exact
class of false merge the list was written to stop.

**2. Token-rarity thresholds are absolute counts, not percentiles.** The feature
scores `df ≤ 5` as fully distinctive, `≤ 20` as strong, `≤ 100` as weak — tuned
against a 1,751-token corpus built from 8,432 records. At 10 lakh records the
distribution is entirely different and `df ≤ 20` would mean "vanishingly rare"
rather than "reasonably distinctive". The table refreshes automatically; **the
thresholds do not**. They should be percentile-based before any scale-up.

**3. Every threshold was fitted here.** `DISTANCE_TIERS`, the 0.6/200 m and
0.3/50 m duplicate-row tiers, the 0.85 AI confirm bar, the two feature weights.
They were measured on Indian urban density — 6.8 masters within 1 km in real
data. A denser or sparser market moves all of them.

**4. Geography is India-only.** `COUNTRY_BOUNDS` has one entry. Records outside
it are validated against nothing.

**5. The oracle only covers what it covers.** Vervotech maps these 1,715 hotels.
New hotels are not in the reference, so the guarantee does not extend to them —
you can measure the old inventory forever and learn nothing about the new.

**6. The same-supplier invariant assumes a supplier never lists one property
twice.** Sabre already violates a neighbouring assumption by reusing hotel ids.

#### What does transfer

The **publication gate** is structural rather than fitted: TIER1/TIER2 are
defined by distance and name agreement, and the *ordering* of risk across tiers
is a property of the design, not of this dataset. The **structural invariants**
(same-supplier collisions, orphaned masters, duplicate public ids) are logical,
not statistical — they hold on any data. And the **measurement harness** is the
durable asset here: it is what lets you answer this question again after every
import instead of assuming.

#### How to keep it true

- Run `/evaluation/accuracy` **on every run**, and treat any false merge in the
  published set as a release blocker rather than a metric.
- Keep Vervotech shadow-running so the reference grows with the inventory and
  the confidence bound tightens instead of going stale.
- Re-measure **per supplier and per region** as feeds are added — an aggregate
  number hides a bad new feed.
- When a new chain appears, expect a sub-brand collision and extend
  `BRAND_TIER_TOKENS` with a measurement, not a guess.

The correct claim to make externally is not "our mapping is 100% accurate". It
is: **"published mappings had zero false merges against a 1,714-hotel reference,
we re-measure on every run, and unverified tiers are withheld until a human
confirms them."** That one stays true as the data changes, because it describes
a process rather than a snapshot.

### Still open

1. ~~No ground truth.~~ **Resolved** — Vervotech's mapping is loaded as
   `reference_mapping` (8,575 pairs, 1,715 hotels) and precision/recall are
   measured on every run. The bound is now 0.17% at 95% confidence rather than
   4.5%. `review_attach_decision` continues to accumulate labels for hotels the
   reference does not cover.
2. **28 duplicate master pairs and 41 same-name/same-city pairs** remain.
3. **295 discards are a Sabre feed defect** — 87 groups where one id means two
   different hotels. Unfixable downstream; the export is the evidence for that
   conversation.
4. **The new discard set has not been re-audited** the way the old one was. The
   four cited cases are verified; the full 150-group classification has not been
   re-run.
5. **Master-level `star_rating` is arbitrary** — inherited from whichever
   supplier seeded the master. 1,021 masters show no star despite holding a rated
   supplier record. Recommend removing it from the product; supplier-level values
   stay in `raw_json`.
6. **No authentication; CORS `*`.** Unchanged.

---

## Known limitations

1. **66 hand-verified pairs bounds the false-positive rate below 4.5% at 95%
   confidence — not at zero.** Thresholds were fitted to those 66 labels and
   must be re-fitted against a full ground-truth set before production. The
   Vervotech shadow-diff (`AUDIT_REPORT.md` §8d) is the way to get one.
2. **114 duplicate master pairs remain** (217 distinct masters), plus a further
   ~83 same-name/same-city pairs split between 150 m and 1 km — same hotel
   divided in two, mostly coordinate discrepancies beyond the tier limits.
   Recall, not precision: recoverable by merging, whereas a false merge is not.
3. **No authentication; CORS still `*`.**
4. **No automatic retry** for `Failed` rows — `retry_count` increments but
   nothing re-queues.
5. **`UNIQUE(supplier_name, supplier_hotel_id)` on `supplier_hotels` not
   applied** — the Sabre collisions make it impossible until the feed is fixed.
6. **No room-level mapping.** Required for rate comparison in a unified API and
   not in scope here.
7. **Throughput measured on one host**; a full 10-lakh run should be executed
   end to end before go-live.

---

## Rollback

Pre-change mapping state is preserved in `bak_master_hotels`,
`bak_hotel_mappings`, `bak_queue`, `bak_manual_review`. Code changes are
uncommitted working-tree edits against `ab9c572`; `git checkout -- <file>`
reverts any individual file. `supplier_hotels.raw_json` retains every original
supplier payload, so the entire mapping output can be rebuilt from source at any
time.


---

## 16. Split, deprecation, and the review console

### `master_non_merge_assertion` — the piece everything else depends on

A reviewer splits two hotels apart. The next mapping run sees them close together
with similar names and **merges them straight back**, silently undoing the work.

This is the same class of defect as the original `reject_review` bug: a human
decision written somewhere the pipeline immediately overwrites. Without a fix,
every other review feature is erasable and reviewing is wasted effort.

`master_non_merge_assertion` records reviewer decisions as supplier-row pairs.
`find_candidate_master_hotels` consults it as a hard exclusion, permanently.
Human judgement outranks the algorithm.

**Verified:** a master was split, the entire 8,432-record mapping rebuilt from
scratch, and the split held — **0 violations**.

### `app/services/master_lifecycle_service.py` — new

| Operation | Behaviour |
|---|---|
| `split_master` | Moves selected rows to a new master with a **new** public id; original keeps the remainder; records non-merge assertions for every moved/kept pair; logs `SPLIT` on both ids |
| `deprecate_master` | `Closed` (property shut) or `Dormant` (still exists, unsupplied). Id keeps resolving — a consumer must hear "closed", not get an error |
| `reactivate_master` | Reverses a deprecation |
| `get_master_detail` | Master, member records with evidence, full history; resolves retired ids forward |

> Bug found in testing: `register_master` reclaimed the seeding row's previous id,
> so the split returned the *same* id it started with. Added `force_new=True` —
> correct for rebuilds, wrong for splits, where a distinct identity is the point.

### `app/services/discarded_records_service.py` — new

Every excluded record with a plain-language reason, severity and suggested
action, plus acknowledgement state so the UI can show "N new". Records are never
deleted and can be re-processed once the supplier corrects the data.

### `app/api/review_routes.py` — new

Dashboard, discarded records and acknowledgement, master search/detail, split,
deprecate, reactivate, id resolution, integrity.

### `app/static/` — new review console

Replaces the React/Vite frontend on `feature/frontend-updates`. Plain HTML/CSS/JS
served by FastAPI at `/ui` — no build step, no `npm`, no separate deployment.

Six screens: Dashboard, Discarded Records, Master Hotels, Manual Review,
Integrity Checks, Supplier Quality. Discarded records surface as a dashboard
banner and a sidebar badge until acknowledged, which was an explicit requirement.

Built to standard conventions for this class of tool, **not a pixel copy of
Vervotech's UI** — that has not been seen. Structure is deliberately conventional
so it can be adjusted against screenshots.

### Fixes made while building

| Issue | Fix |
|---|---|
| `asyncpg` could not infer the type of a NULL filter parameter | Explicit `CAST(:reason AS VARCHAR)` |
| Split reused the original public id | `force_new` flag on `register_master` |

### Known gaps

- **No authentication** — anyone reaching the URL can split and close hotels.
  Actor is recorded but defaults to `reviewer`. Must be added before exposure.
- No undo for a split (deprecation is reversible).
- No change-event feed; consumers must poll `/resolve`.
- Discarded list shows the most recent 200; the API supports `offset`.


---

## 17. Import, pipeline control, and Excel export

### `app/services/export_service.py` — new

Nine datasets exportable as Excel: master hotels, mappings, discarded records,
manual review, supplier hotels, supplier quality, integrity, reviewer decisions,
supplier ID conflicts.

Each dataset is one SQL statement whose column aliases are the human-readable
headers, so adding an export needs only a new entry in `DATASETS`. Files come out
with a styled header row, frozen panes, auto-filter and width-fitted columns.

### `app/services/data_source_service.py` — new

Import from a file (CSV/XLSX/XLS) or an external PostgreSQL/MySQL database.

**Two-step by design: analyse, then commit.** Nothing is written until the
detected column mapping has been seen. A silently mis-detected column produces
thousands of unusable records — which is exactly what the original importer did,
since it demanded fixed column names (`mapping_hotel_name`, `normalized_hotelname`)
and wrote NULL when a header did not match (audit bug #12).

| Capability | Detail |
|---|---|
| Column auto-detection | Synonym table covering the naming variants across all five supplier feeds |
| Combined coordinates | A single `LatitudeLongitude` column of `"19.06,72.86"` is split automatically |
| Postal normalisation | Digits only, so `"110001.0"` and `"110001"` compare equal |
| Required-field check | Blocks the import if hotel ID, name or country is absent, rather than importing blanks |
| Quality preview | Counts missing coordinates/city/postal over the first 400 rows |
| Database guards | `postgresql://` or `mysql://` only; single statement; `SELECT`/`WITH` only |

**Two-pass column detection.** Exact synonym matches claim their column first;
substring matching then runs only on unclaimed columns. Without the claim step,
`supplier_name` matched `supplier_hotel_id` on the substring "supplier" and
imported hotel IDs as supplier names.

**The typed supplier name is authoritative.** A file often carries its own
supplier column; preferring it meant a user typed "TestImport" and the rows
landed under "Hummingbird". The file's value is now reported in the preview so
the override is visible, not silent.

### `app/api/data_routes.py` — new

Export list and download, file analyse/commit, database analyse/commit, pipeline
run/status/requeue-failed.

### UI — three new screens

**Import Data**, **Run Pipeline** (with progress bar and self-refresh while
running), **Exports**. Every existing section also gained an *Export to Excel*
button in its header.

### Fixes made while building

| Issue | Fix |
|---|---|
| `RETURNING` yields no rows under `executemany`, so imported IDs could not be queued | Rewrote as a single set-based `INSERT … SELECT FROM unnest(...)` — also one round trip per batch instead of many |
| asyncpg could not deduce a type for `:latitude`, used as both a NUMERIC column and a `ST_MakePoint` argument | Explicit `CAST(… AS double precision)` |
| `supplier_name` claimed the `supplier_hotel_id` column | Two-pass detection with column claiming |
| File's supplier column silently overrode the user's input | User input wins; file value surfaced in the preview |

### Verified end to end

Imported 60 rows from a real supplier file → 11 columns auto-detected → committed
and queued → pipeline run → all 60 matched correctly → exported to Excel. Test
data removed afterwards; baseline unchanged at 8,432 records / 2,767 masters /
0 failed.

### Known gaps

- **No duplicate detection on import** — importing the same file twice stores the
  rows twice. Mapping catches and sets aside the duplicates, but the raw records
  are duplicated.
- **The database import is the sharpest edge of having no authentication.** It
  makes the server connect to whatever address is supplied. Reads are restricted
  to a single `SELECT` over `postgresql://`/`mysql://` only, but without login an
  unauthenticated caller could point it at an internal host. Authentication is a
  prerequisite for exposing this screen.
- Exports cap at 200,000 rows per file.
- Large file uploads are held in memory during analyse (120 MB limit).


---

## 18. Multi-field search

### `app/services/search_service.py` — new

Replaces the single-box master search with a filter set matching the layout the
team already uses, and changes what a result *is*.

**Results are provider records grouped under their master ID**, not master rows.
Searching one ID returns every provider's version of that hotel side by side —
the view a reviewer actually needs to judge whether they are the same property.

| Filter | Backing |
|---|---|
| Hotel name (free text) | supplier name, master name, address |
| Master Hotel Id | `master_hotel_registry.public_id` |
| Provider Hotel Id | `supplier_hotels.supplier_hotel_id` |
| Provider Name | exact match on supplier |
| Hotel Chain Name | `chain_name` on master or supplier — **no data yet** |
| Property Type | `property_type` — **column added, no data yet** |
| Country / City | supplier values |
| Star | minimum rating |

Only the filters actually supplied become `WHERE` fragments, so unused fields
cost nothing in the plan. `count_records` backs *Get Property Count* with totals
across the whole match. `find_duplicates` returns hotels within 1 km with a
similar name sitting under a **different** master — verified against a known
duplicate pair (`Le Meridien Kochi`, 11 m apart, 100% name match, different
masters).

Schema additions: `master_hotels.chain_name`, `master_hotels.property_type`,
`supplier_hotels.property_type`, plus indexes on chain, type, star and
lowercased name.

`facets()` returns dropdown values **and** an `empty_fields` list, so the UI can
label filters that currently have nothing behind them rather than letting a user
search a dead field.

### UI

**Master Hotels** — hero name box, eight filters in a three-column grid, star
slider, Get Property Count / Reset / Search. **Live search: results refresh
350 ms after any field changes**, no button press needed. Results table matches
the target columns (`#`, Master ID, Provider Name, Provider Hotel Id, Hotel Name,
Address, Star, Lat, Long, View, Find Duplicate) with Previous/Next paging.

**Manual Review** — search bar covering every field at once, filtering as you
type.

### Fixes made while building

| Issue | Fix |
|---|---|
| `/manual-review/search` collided with the existing `/manual-review/{id:int}` route and returned 422 | Moved to `/review-queue/search` |
| Leftover `if False` list comprehension in `facets()` | Removed |
| Docker daemon stopped mid-build, hanging a `docker restart` | Restarted; unrelated to the code |
