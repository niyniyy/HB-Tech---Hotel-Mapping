# Hotel Mapping Engine — Technical Audit Report

**Date:** 22 July 2026
**Project:** HB-Tech Hotel Mapping Engine
**Branch audited:** `feature/person-b-import-pipeline` (commit `ab9c572`)
**Method:** Static review of all 3,754 lines of Python; queries against the live
Docker stack and its database (8,432 hotels already processed); an offline
re-implementation of the pipeline driven by the project's own scoring code; and
a scale benchmark on a synthetic 1,000,000-row queue table.

**Question asked:** can this be deployed against a database of 10 lakh+ records,
where *accuracy matters more than the number of hotels mapped*?

---

## Executive Summary

**No. Not in its current state.**

There are two independent blockers, either of which alone is disqualifying.

1. **Accuracy — roughly half the output is wrong.** The scoring formula lets
   geographic proximity alone reach 85 of the 90 points needed to auto-match.
   Hotel name contributes at most 10 points and *can be zero while the match
   still succeeds*. Measured on live data, **33.5% of auto-matches join two
   demonstrably different hotels**, auto-match precision is **≈ 51.5%**, and
   counting every outcome across all 8,432 inputs **only ~54% are correct**. One
   master record has absorbed 34 supplier rows spanning roughly 11 distinct
   properties.
2. **Scale.** A missing index makes queue processing quadratic — benchmarked at
   **850× slower** at 10 lakh rows — and the batch processor retains every result
   in memory, requiring **~15.3 GB of RAM** to drain a 10 lakh queue.

A third, subtler problem cuts the other way: because candidate lookup requires an
**exact city-string match**, the same hotel filed under `Bangalore` and
`Bengaluru` never becomes a candidate. **607 duplicate master pairs** already
exist. The system simultaneously merges hotels that are different and splits
hotels that are the same.

A fourth gap compounds all of these: **the engine has no way to reject a
record.** Every input either matches, goes to review, or becomes a master — so a
row with missing coordinates or an unusable name is silently promoted to a
permanent master hotel, and nobody is told. §2.8 documents this; §7.0 specifies
the `FLAGGED` outcome that fixes it.

The architecture and technology choices are sound. Every defect below is
localised and fixable. This is a design-and-hardening problem, not a rewrite.

> **Correction notice.** This report supersedes the version dated earlier the
> same day. That version materially understated the accuracy problem (it reported
> "87 masters" affected, against a measured 409), missed the duplicate-master
> failure entirely, credited the AI layer with a validation role it does not
> perform, and projected processing time linearly when the cost is quadratic.
> Section 12 lists the specific corrections.

---

## 1. Tech Stack

| Component | Technology |
|---|---|
| API | FastAPI (async, Python 3.11) |
| Database | PostgreSQL 16 + PostGIS + pgvector + pg_trgm |
| Background jobs | Celery 5.4 + Redis, `--concurrency=4` |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2`, 384-dim, no fine-tuning |
| Fuzzy text | RapidFuzz `token_sort_ratio` |
| Geo | PostGIS `ST_DWithin` / `ST_Distance` on `geography` |
| Deployment | Docker Compose — `db`, `redis`, `api`, `worker` |

Pipeline: CSV → `supplier_hotels` → `hotel_mapping_queue` (Pending) → Celery
worker → candidate filter (country + city + 1 km) → rule scoring → optional AI
enrichment → AUTO_MATCH / MANUAL_REVIEW / CREATE_NEW_MASTER.

---

## 2. Accuracy — Primary Finding

### 2.1 The scoring formula

`app/matching/matcher.py:217-254`, for any two hotels within 1 km:

```
final_score = 85 (geo, fixed) + name_similarity×10 + address_similarity×5
AUTO_MATCH threshold = 90
```

Proximity alone delivers 85 points. The remaining 5 needed can come **entirely
from the address**, which shares road, locality, city and pincode tokens by
construction. Two hotels 900 m apart on the same road, with **zero** name
similarity, score `85 + 0 + 5 = 90` → AUTO_MATCH.

The other branches are worse:

| Distance | Geo | Name max | Address max | Auto-matches at |
|---|---|---|---|---|
| 0 m | 95 | 5 | 0 | **always** — name is irrelevant |
| ≤ 100 m | 90 | 7 | 3 | **always** — already at threshold |
| ≤ 1000 m | 85 | 10 | 5 | name ≥ 50%, or address alone |
| > 1000 m | 10–40 | 35 | 15 | requires genuine name agreement |

Identical coordinates auto-match at 95 regardless of name. Note the inversion:
the *fallback* path for distant hotels (name worth 35) is the only branch that
scores identity sensibly. The "improved" distance-based logic added in commit
`69484ba` is what broke it.

### 2.2 Measured on the live database

8,432 hotels across 5 suppliers, 5,916 AUTO mappings. Name agreement between each
supplier hotel and the master it was merged into (`pg_trgm` similarity):

| Name similarity | Mappings | Share | Meaning |
|---|---|---|---|
| **< 0.25** | **1,863** | **33.5%** | **Different hotel — false positive** |
| 0.25 – 0.45 | 836 | 15.0% | Doubtful |
| 0.45 – 0.70 | 725 | 13.0% | Likely same |
| ≥ 0.70 | 2,143 | 38.5% | Confidently same |

**Auto-match precision ≈ 51.5%** (treating ≥ 0.45 as correct); 38.5% on a
strict reading, 66.5% on a generous one.

**48.1% of auto-matches score between 90 and 95** — bunched against the
threshold, i.e. decided by proximity rather than identity.

Master hotel integrity:

| Distinct hotels per master | Masters | Share |
|---|---|---|
| 1 — clean | 1,062 | 48.4% |
| 2–3 — name variants or mild merging | 724 | 33.0% |
| **> 3 — badly merged** | **409** | **18.6%** |

#### Headline accuracy — roughly half

Across all 8,432 inputs, counting every outcome:

| Outcome | Count | Correct? |
|---|---|---|
| Auto-matched, confidently correct | 2,143 | ✓ |
| Auto-matched, likely correct | 725 | ✓ |
| Auto-matched, doubtful | 836 | ? |
| Auto-matched, clearly wrong | 1,863 | ✗ |
| New master, legitimately new | 1,722 | ✓ |
| New master, redundant duplicate (§2.6) | 473 | ✗ |
| Stuck in review, no output at all (§2.6b) | 670 | ✗ |

**≈ 4,590 of 8,432 correct — about 54%**, with a plausible range of 46–64%
depending on how the doubtful bucket is treated. Auto-match precision alone is
51.5%; master-table integrity is 48.4%. By every measure the system is close to
a coin flip.

**These are proxy measures, not ground truth**, and they err in both
directions. They *understate* accuracy where abbreviations are genuine matches
(`Rocv` → `Royal Orchid Central Vadodara`). They *overstate* it where
same-chain, different-property pairs score high on name — and that risk is
measurable: **369 of the 2,143 "confidently correct" matches (17%) are more than
200 m apart**, the characteristic profile of a same-chain confusion. The true
figure may therefore sit somewhat below 54%.

This is precisely why the labelled ground-truth set (Phase 1 item #10) gates
everything else. "About half" is sufficient to conclude *not deployable*; it is
not sufficient to measure improvement against, and post-rework the team will
need to state "precision moved from X to Y" against a fixed, trustworthy
denominator.

### 2.2b Manual verification of a random sample

Because §2.2 relies on name similarity as a proxy for correctness, a random
sample of **60 auto-mapped pairs** (seeded, unbiased) was adjudicated by hand —
judging each pair on whether it is the same physical property.

| Verdict | Count | Share |
|---|---|---|
| Correctly mapped | 31 | **51.7%** |
| Incorrectly mapped | 29 | 48.3% |

**95% confidence interval: [39%, 64%].** The automated proxy predicted 51.5%;
hand adjudication returned 51.7%. The two methods agree to within 0.2 points,
which validates applying the proxy across all 5,567 mappings.

The proxy's two known error modes largely cancelled. It scored three **brand
transitions** as wrong that are in fact correct — `Sayaji` → `Enrise by Sayaji`,
`Fortune The Savoy` → `Welcomhotel The Savoy`, `Gateway Aurangabad` → `Vivanta
Aurangabad` — while passing some same-chain pairs that were wrong. Judging the
four genuinely ambiguous cases strictly rather than generously yields 45%, so
the defensible range is **45–52%**.

Representative false positives from the sample:

| Supplier hotel | Merged into | Distance |
|---|---|---|
| The Oberoi Amarvilas | Hotel Taj Resorts, Agra | 194 m |
| The Leela Palace Jaipur | DoubleTree by Hilton Jaipur Amer | 246 m |
| Taj Taal Kutir Kolkata | The Westin Kolkata Rajarhat | 619 m |
| Grand Mercure Agra | ITC Mughal Agra | 657 m |
| Novotel Visakhapatnam Varun Beach | ITC Welcomhotel Grand Bay | 102 m |
| Vivanta Coimbatore | WelcomHotel Coimbatore | 649 m |
| Radisson Blu Bengaluru ORR | Bloom Hub ORR Marathahalli | 504 m |

#### The errors are concentrated by distance

Splitting the 60 hand-checked verdicts by separation:

| Distance | Correct | Wrong | Precision |
|---|---|---|---|
| **≤ 200 m** | 24 | 5 | **83%** |
| **> 200 m** | 7 | 24 | **23%** |

Correct matches average **123 m** apart; incorrect ones average **488 m**.

Nearly all the damage sits in the 200–1000 m range — exactly the band where the
geo component pays a flat 85 points irrespective of actual distance (§2.1). The
0 m and ≤ 100 m branches are broadly performing. **Tightening the auto-match
radius to roughly 200 m, combined with the name gate, would remove most false
positives before any reweighting** — a change small enough to ship in days, and
worth doing first as a stop-gap while Phase 3 proceeds.

### 2.3 Worked example — master_hotel_id = 5

"Hilton Mumbai International Airport" absorbed **34 supplier rows** covering at
least 11 physically distinct properties, every one auto-matched without review:

| Absorbed hotel | Score |
|---|---|
| Hilton Garden Inn Mumbai International Airport | 95.94 |
| Novotel Mumbai International Airport | 95.16 |
| Fairfield by Marriott Mumbai International Airport | 95.10 |
| The Lalit Mumbai | 94.68 |
| ITC Maratha, a Luxury Collection Hotel | 94.52 |
| Radisson Blu Mumbai International Airport | 94.51 |
| JW Marriott Mumbai Sahar | 92.81 |
| Fairmont Mumbai | 91.94 |
| The Leela Mumbai | 91.93 |
| JW Marriott Mumbai Juhu | 91.83 |

Similar clusters: `Lemon Tree Premier, Delhi Airport` (30 rows, 24 distinct
names), `Lemon Tree Hotel, Ahmedabad` (31 rows, 21), `ITC Rajputana, Jaipur`
(31 rows, 16), `Sofitel Mumbai BKC` (27 rows, 15).

The JW Marriott Juhu case also shows there is **no coordinate sanity checking** —
Juhu is several kilometres from Sahar, so a bad supplier coordinate propagated
straight into a merge unchallenged.

### 2.4 Independent reproduction

The pipeline was re-implemented offline against the raw supplier spreadsheet,
calling the project's own `calculate_rule_based_score` and reproducing the
candidate-selection SQL exactly. It matched the live system almost perfectly,
confirming these numbers describe real behaviour:

| Metric | Live system | Offline reproduction |
|---|---|---|
| Master hotels | 2,195 | 2,196 |
| Auto-matches | 5,567 | 5,566 |
| Manual review | 670 | 670 |

Under the reproduction's stricter `token_set_ratio` measure, 20.0% of
auto-matches fall below 50% name agreement, and 607 of 2,196 masters hold more
than one distinct hotel. Both measures agree: **between a fifth and a third of
auto-matches are wrong.**

### 2.5 The AI layer does not protect against this

Two independent reasons.

**It is never consulted where the errors are.**
`app/services/queue_processing_service.py:199` gates enrichment on
`75 <= rule_score < 90`. Every false positive in §2.2 scored ≥ 90 and bypassed it
entirely. `ai_integration_service.py:25-31` confirms the intent: scores ≥ 90 are
returned as AUTO_MATCH with the reason *"Rule score is above auto-match
threshold"* — no AI call is made.

**It is not an independent check.**
`vector_similarity_service.py:34` constrains the vector search to
`master_hotel_id = ANY(:candidate_master_ids)` — the same ≤ 5 masters the rules
already shortlisted. It re-ranks the rule engine's output; it cannot discover a
better master, and it cannot contradict a shortlist that was wrong to begin with.

The prior report's claim that the AI "prevented 670 false matches" and was "100%
correct" is not supported by evidence. Those 670 are simply everything in the
75–90 band it declined to promote; no verification was performed on whether they
were genuinely non-matches.

### 2.5b Worked example — the 0.11-point margin

Supplier hotel 8376 (Sabre `100182882`), taken from the live manual-review
queue, shows precisely how the threshold behaves.

| | Value |
|---|---|
| Supplier hotel | E Square The Fern Pune Series By Marriott |
| Suggested master (id 55) | The Pride Hotel - Pune |
| Distance | 631.9 m |
| Name similarity | **31.82%** → 3.18 pts |
| Address similarity | 34.29% → 1.71 pts |
| Geo | fixed → 85.00 pts |
| **Rule score** | **89.89** → MANUAL_REVIEW |
| AI similarity | 0.64 → correctly blocked |

These are two different hotels, and the system held them apart — **by 0.11
points**. Had the address string scored 36.4% rather than 34.29% (one more
shared token; `University Road` is already common to both), the score would have
crossed 90, the record would have been auto-matched, and **the AI would never
have been consulted**.

This is the AI layer working exactly as designed — and the design is the
problem. Compare against an auto-matched pair from the same run:

| | Hotel 8376 | Auto-matched record |
|---|---|---|
| Supplier hotel | E Square The Fern Pune | M Inn |
| Master | The Pride Hotel - Pune | Hash Six Hotels - Coimbatore |
| **Name similarity** | **31.82%** | **12.90%** |
| Distance | 632 m | 31 m |
| Score | 89.89 | 92.08 |
| Outcome | MANUAL_REVIEW — caught | **AUTO_MATCH — never reviewed** |

The record with *worse* name agreement was auto-matched; the one with better
name agreement went to review. **Distance decided both outcomes; identity
decided neither.** The score is not ranking "is this the same hotel" — it is
ranking "how close are they" — and the 90 threshold cuts across that ordering
arbitrarily.

Under the Phase 1 reweighting (`name 45 + address 20 + geo 25 + star 5 +
chain 5`), 31.82% name similarity contributes ~14 of 45 points and the pair
fails decisively, at 30 m or 600 m alike.

### 2.6 The mirror-image failure — duplicate masters

`app/services/matching_service.py:62-63`:

```sql
JOIN master_hotels m ON LOWER(m.country) = LOWER(s.country)
                    AND LOWER(m.city)    = LOWER(s.city)
```

A hotel is only a candidate if the city strings match **exactly**. Suppliers do
not agree on city names. Query against the live DB — masters under 150 m apart
with name similarity > 0.6 but different city strings: **607 duplicate pairs**,
roughly 23% of the master table.

| Distance | City A | City B | Hotel |
|---|---|---|---|
| 0.0 m | Bangalore | Bengaluru | Aiden by Best Western Hennur |
| 10.4 m | Gurgaon | Gurugram | Taj Damdama Lake Resort & Spa |
| 11.1 m | New Delhi | New Delhi And NCR | East End Retreat |
| 2.2 m | Madikeri | Kodagu | Coorg Marriott Resort & Spa |
| 16.0 m | Ayodhya | Faizabad | Park Inn by Radisson Ayodhya |
| 51.3 m | Konarka | Konark | Lotus Resort Konark |

Because the master's city string is inherited from whichever supplier created it,
this compounds: every subsequent supplier using a different convention creates
another duplicate.

Separately, `LIMIT 10` on candidates ordered by distance means that in Aerocity,
BKC or HITEC City — where 15–20 masters sit within 1 km — the correct master may
not even reach the scoring stage.

### 2.6b The manual review queue is ~99% noise

The 670 records awaiting human review were assessed in full. Name similarity
between each supplier hotel and its suggested master:

| Name similarity | Records | Share | Avg AI similarity |
|---|---|---|---|
| **< 0.20 — obviously different hotels** | **646** | **96.4%** | 0.55 |
| 0.20 – 0.35 — almost certainly different | 23 | 3.4% | 0.66 |
| 0.35 – 0.55 — genuinely borderline | **1** | **0.1%** | 0.73 |
| ≥ 0.55 — plausibly the same hotel | **0** | **0%** | — |

**One record out of 670 is a legitimate judgement call.** The remainder are
pairs of unrelated hotels that happen to fall within a kilometre of each other —
the same proximity artefact as §2.2, arriving at MANUAL_REVIEW instead of
AUTO_MATCH only because they landed a fraction below 90.

#### The system already has the evidence to reject them

AI similarity was computed and stored for every record in the queue:

| AI verdict | Records | Share |
|---|---|---|
| < 0.50 — no match | 185 | 27.6% |
| 0.50 – 0.70 — unlikely | 417 | 62.2% |
| 0.70 – 0.85 — possible | 68 | 10.1% |
| ≥ 0.85 — match | **0** | **0%** |

**602 of 670 (89.9%) scored below 0.70.** The maximum across the entire queue is
0.83; nothing ever reached the 0.85 promotion threshold.

The cause is in `ai_integration_service.py:77-93`. The branch is binary —
`ai_similarity >= 0.85` promotes to AUTO_MATCH, **everything else falls through
to MANUAL_REVIEW**. There is no lower bound, so an AI similarity of 0.24 is
routed to a human exactly like 0.84. The rejection path was never written.

Adding a lower threshold at 0.70 would auto-resolve 602 records and leave 68 for
review — a **90% reduction**, and the difference between a queue a person can
clear and one they cannot. At 10 lakh records this is the difference between a
projected 50,000–100,000 review items and roughly 5,000–10,000.

#### Rejection must cancel the *match*, not the *record*

These are real hotels. `E Square The Fern Pune` deserves its own master record —
it simply is not `The Pride Hotel - Pune`. The correct outcome for the 602 is
therefore **`CREATE_NEW_MASTER`**, with `FLAGGED` (§7.0) reserved for records
whose own data is unusable. Discarding them would lose legitimate inventory.

This is urgent because the 670 are currently **in limbo**: 8,432 hotels produced
7,762 mappings plus 670 in review, and **none of the 670 hold any mapping or
master at all**. They are absent from the output entirely. Combined with the
`reject_review` loop (§2.7), there is at present no way to resolve any of them
correctly.

### 2.7 Manual review cannot recover the errors

`app/services/manual_review_service.py:359` — rejecting a review sets the queue
row back to `Pending`. It is re-fetched, re-scored identically, and returns to
the review queue. **The reviewer's decision is discarded and the item loops
forever.** There is no `Rejected` terminal state. The one mechanism that could
guarantee accuracy is inoperative.

### 2.8 There is no "low score" — and no way to reject a record

`find_candidate_master_hotels` returns only masters within 1000 m, and the
≤ 1000 m branch starts at a fixed geo score of 85. Every scored candidate is
therefore **structurally guaranteed to score at least 85**. Confirmed on live
data:

| Outcome | Observed score range |
|---|---|
| AUTO mappings | 90.00 – 100.00 |
| Manual review candidates | **86.81 – 89.99** |

Nothing below 86.81 has ever been produced. Two consequences:

1. **The `> 1000 m` fallback branch is unreachable dead code** — and it is the
   only branch that weights names properly (name 35 / address 15 / geo 40).
2. **`get_match_decision`'s `< 75 → CREATE_NEW_MASTER` is also unreachable.**
   All 2,195 masters were created by the *zero-candidates* path
   (`matching_service.py:141-148`), never by a low score.

The second point is the dangerous one. A record with missing coordinates,
missing city, or an unusable name never reaches scoring at all — the candidate
query requires `s.geo_location IS NOT NULL` and an exact city match, so it
returns nothing, and the record is **silently promoted to a new master hotel**.

There is no outcome in the system that means *"this record is not fit to
process."* Every record either matches, goes to review, or becomes a master.
Unusable data therefore permanently inflates the master table, and nobody is
told.

The current dataset conceals this — it is a curated 1,700-hotel-per-supplier
sample with 0 null names, 0 null cities and 0 null coordinates (though 81.6%
lack a star rating). Production supplier feeds typically carry 5–15% missing or
invalid coordinates. At 10 lakh records that is 50,000–150,000 unverifiable
records silently becoming masters.

**This is addressed by the third outcome proposed in §7.0.**

### 2.9 The reference dataset is not clean — but not in the way expected

Hummingbird is imported first and creates the initial master hotels, so every
defect in it propagates permanently into the master table and corrupts every
subsequent supplier match. It was audited separately.

**On completeness it is excellent:**

| Check | Result |
|---|---|
| Rows | 1,714 |
| Distinct `supplier_hotel_id` | 1,714 — **zero exact duplicates** |
| Blank hotel name / address / state | 0 / 0 / 0 |
| Null coordinates | 0 |
| Null star rating | **1,714 — all of them** |

**On internal consistency it is not.** Among Hummingbird hotels within 3 km of
each other, **102 distinct city-string pairs disagree**:

| City A | City B | Pairs |
|---|---|---|
| New Delhi And NCR | Central Delhi | 161 |
| Gurgaon | Gurugram | 110 |
| Goa | North Goa | 109 |
| Goa | Candolim | 58 |
| Mussoorie | Dehradun | 19 |

The reference dataset contradicts itself in exactly the field the candidate
query depends on (§2.6). `Gurgaon` and `Gurugram` both appear *inside
Hummingbird itself*, so its own masters land in incompatible buckets, and a
supplier hotel labelled either way can only ever match half of them.

**The dominant defect is inconsistency, not garbage.** The corrective action is
therefore *normalisation*, with removal reserved for genuine rarities — not a
bulk cleanup.

#### Automated duplicate removal would damage the reference set

A proximity-plus-name duplicate scan over Hummingbird returns 13 candidate
pairs. On inspection almost all are **legitimately distinct co-located hotels**:

| Hotel A | Hotel B | Distance | Reality |
|---|---|---|---|
| Novotel Bengaluru ORR | Ibis Bengaluru ORR | 9.8 m | Two brands, one Accor complex — **distinct** |
| Pullman New Delhi Aerocity | Novotel New Delhi Aerocity | 52.2 m | Dual-brand property — **distinct** |
| Courtyard by Marriott ORR | Fairfield by Marriott ORR | 64.7 m | Dual-brand property — **distinct** |
| ITC Sonar | ITC Royal Bengal | 65.6 m | Adjacent ITC hotels — **distinct** |
| Radisson Gwalior | Park Inn by Radisson Gwalior | 0.0 m | Genuinely ambiguous — needs a human |
| Lemon Tree Premier, Leisure Valley 2 | Lemon Tree Premier 1, Gurgaon | 39.2 m | Genuinely ambiguous — needs a human |

Dual-brand and co-located hotels are common in India — Aerocity, Bengaluru ORR,
airport complexes. **Deleting them would be the most damaging possible outcome**,
because a hotel missing from the reference set cannot be matched by any
downstream supplier ever. Of 13 flagged pairs, at most 2 are real duplicates:
an automated rule would have a false-positive rate above 80%.

This is why §7.1 specifies that removals are **proposed, never applied
automatically**, and why quarantined rows must remain restorable.

---

## 3. Scale — Will Not Complete at 10 Lakh

### 3.1 Missing index makes processing quadratic

No `create_all` call exists anywhere in the codebase; tables come solely from
`scripts/init.sql`. The `index=True` declarations in `app/models/hotel.py`
therefore **never reach the database**. Verified against the live DB — the only
indexes on `hotel_mapping_queue` are its primary key and `idx_queue_status`.

`update_queue_status` (`hotel_mapping_service.py:18-21`) filters on
`supplier_hotel_id`, which has no index, and is called **twice per hotel**
(Processing, then Completed/ManualReview).

Benchmarked on a synthetic 1,000,000-row queue table, warm cache:

| Query | Plan | Time |
|---|---|---|
| `UPDATE … WHERE supplier_hotel_id = ?` — as deployed | Seq Scan, 999,999 rows discarded | **40 ms** |
| Same, with the index added | Index Scan | **0.047 ms** |

**850× penalty.** At 10 lakh hotels × 2 updates × ~32 ms ≈ **18 hours of pure
sequential scanning**, before any matching work — and that is the optimistic case
(single worker, fully cached, fast local SSD). Four concurrent workers scanning
the same table contend for buffers and row locks; this gets worse, not 4× better.

The batch-fetch query degrades too: at 10 lakh pending rows,
`WHERE status='Pending' ORDER BY supplier_hotel_id LIMIT 5000` plans as a
parallel sequential scan plus top-N sort (43 ms measured), repeated on every
batch.

The prior report's "9–10 hours" figure extrapolated linearly from 8,432 rows,
where the sequential scan costs 1.4 ms and is invisible. The cost is O(n²).

### 3.2 Guaranteed out-of-memory

`queue_processing_service.py:238-265` appends every result to a `results` list
and loops until the entire queue drains. `run_full_mapping.py`'s outer batching
does not bound it — the inner loop already consumes the whole queue.

Each entry retains the supplier row (including parsed `raw_json`), up to 10
scored candidates, and the final result. Measured against real row shapes:
**16,416 bytes per hotel.**

| Queue size | RAM retained |
|---|---|
| 1 lakh | 1.53 GB |
| **10 lakh** | **15.29 GB** |

The worker is killed long before the queue drains.

### 3.3 Concurrency is unsafe

`get_pending_queue_ids` (`queue_processing_service.py:28-42`) is a plain `SELECT`
with no `FOR UPDATE SKIP LOCKED`. With `--concurrency=4`, two workers fetch
overlapping ID ranges and process the same hotels — producing duplicate mappings
and duplicate masters. There is also an inherent race: two workers handling
different hotels at the same location both find no candidate and both create a
master.

### 3.4 Other scale defects

| Issue | Location | Impact at 10 lakh |
|---|---|---|
| `NullPool` | `database/connection.py:11` | New TCP connection per session; connection exhaustion under load |
| IVFFlat `lists = 100` | `init.sql:148` | Sized for ~10k vectors; 10 lakh needs ~1,000–3,000. Degrades to near-sequential scan |
| Sync embedding call inside `async` | `embedding_service.py:17` | CPU-bound `model.encode` blocks the event loop; no batching |
| No foreign keys | `init.sql` | Referential integrity depends entirely on application code |
| ML model per worker | — | ~500 MB × 4 workers ≈ 2 GB before any data |

---

## 4. Data Integrity Damage Already Present

Measured on the live 8,432-hotel database — these are not projections.

| Finding | Count |
|---|---|
| Duplicate `(supplier_name, supplier_hotel_id)` in `supplier_hotels` | 150 keys / 210 excess rows |
| Duplicate `(supplier_name, supplier_hotel_id)` in `hotel_mappings` | 121 keys |
| **Supplier hotels mapped to two or more *different* masters** | **64** |
| Master hotels with `state = NULL` | 2,195 (all of them) |

No `UNIQUE` constraint exists on either table, so re-running an import or
re-processing the queue silently multiplies rows. The 64 ambiguous mappings are
the most serious: those hotels have no single answer to "which master is this?",
which is the one question the system exists to answer.

---

## 5. Confirmed Bugs

### Critical

| # | Bug | Evidence |
|---|---|---|
| 1 | False-positive auto-matching | 1,976 mappings joining different hotels; §2 |
| 2 | Duplicate masters from exact-city join | 607 duplicate pairs; §2.6 |
| 3 | `GET /hotels/{id}/matches` returns HTTP 500 | Reproduced against the running API: `TypeError: AISimilarityService.find_ai_matches() missing 1 required positional argument: 'candidate_master_ids'` — `suggested_matches_service.py:27` omits it |
| 4 | Missing queue index → quadratic processing | Benchmarked, §3.1 |
| 5 | Unbounded `results` accumulation → OOM | Measured, §3.2 |
| 6 | No unique constraints | 64 ambiguous mappings already exist, §4 |

### High

| # | Bug | Evidence |
|---|---|---|
| 7 | `reject_review` loops forever | Sets status to `Pending`; no `Rejected` state — `manual_review_service.py:359` |
| 8 | `apply_decision=False` infinite-loops | Resets to `Pending` inside the drain loop — `queue_processing_service.py:214-218`. Reachable via `POST /mapping/run?apply_decision=false` |
| 9 | Unsafe concurrent queue consumption | No `SKIP LOCKED`, §3.3 |
| 10 | No authentication on any endpoint; `CORS allow_origins=["*"]` | `main.py:27-33` |

### Medium

| # | Bug | Evidence |
|---|---|---|
| 11 | `state` never written to `master_hotels` | Column omitted from INSERT — `hotel_mapping_service.py:76-99`; all 2,195 rows NULL |
| 12 | Import silently writes NULL hotel names | `_clean_row` reads `mapping_hotel_name` / `normalized_hotelname`; a CSV lacking them yields NULLs rather than an error — `import_service.py:145-146` |
| 13 | Duplicate `SuggestedMatchesResponse` class | `schemas.py:98` and `:113`; the second silently overwrites the first |
| 14 | Double-commit pattern | `get_db()` commits *and* services commit explicitly |
| 15 | No coordinate sanity checking | Bad supplier lat/long propagates directly into merges, §2.3 |

### Low

| # | Bug |
|---|---|
| 16 | `normalize_hotel_name` is dead code — never called in the pipeline |
| 17 | `COLUMN_MAP` defined but unused |
| 18 | `print()` instead of `logging` throughout the hot path |
| 19 | No validation on `limit` query parameters |
| 20 | Orphan `docker-compose-1.yml` |

---

## 6. What Works Well

These are real strengths and should be preserved through the fixes.

1. **Correct technology choices.** PostGIS for geo, pgvector for embeddings,
   pg_trgm for fuzzy text — all industry-appropriate for this problem.
2. **Clean layering.** API / service / matching / repository separation is
   consistent and genuinely well done. It is why every defect above is a
   localised fix rather than a rewrite.
3. **Stable execution.** 8,432 hotels processed with zero crashes, zero
   timeouts, zero failed queue items.
4. **Robust import pipeline.** Batched inserts, geo population, auto-queueing —
   works reliably.
5. **Reproducible deployment.** The full stack starts with one command and all
   services connect correctly.
6. **Error handling in the worker loop.** Per-hotel try/except with rollback and
   a `Failed` status is the right pattern.
7. **The fallback scoring path is sensible.** The > 1 km branch
   (name 35 / address 15 / geo 40 / star 5 / chain 5) is a reasonable weighting —
   it is the model the sub-1 km branches should have followed.

---

## 7. Remediation Plan

### 7.0 Required design change — a fourth outcome: FLAGGED

The engine currently has three outcomes: AUTO_MATCH, MANUAL_REVIEW,
CREATE_NEW_MASTER. As §2.8 shows, anything unusable falls through to
CREATE_NEW_MASTER and silently pollutes the master table. A fourth, terminal
outcome is required:

> **FLAGGED** — the record is not fit for automatic processing. It is never
> mapped, never becomes a master, and is reported back with a machine-readable
> reason.

**FLAGGED is not a slower MANUAL_REVIEW.** The two have different meanings and
different owners, and conflating them is what makes review queues explode:

| | MANUAL_REVIEW | FLAGGED |
|---|---|---|
| Meaning | "We have a specific candidate — confirm yes or no" | "We cannot form a reliable opinion at all" |
| Owner | Mapping reviewer | Supplier / data owner |
| Action | A mapping decision | Fix or re-supply the source record |
| Effort | Seconds per item | Not the reviewer's job |
| Creates a master? | On approval | **Never** |

#### Flag reasons

Group A — **data quality**, evaluated at import, *before* any matching. These
are cheap, and catching them at upload means the supplier is told in seconds
rather than after a multi-hour mapping run.

| Reason | Condition |
|---|---|
| `MISSING_COORDINATES` | `latitude` or `longitude` null |
| `INVALID_COORDINATES` | `(0,0)`, outside the country bounding box, or implausibly far from the stated city centroid |
| `MISSING_NAME` | `hotel_name` null, blank, or normalising to an empty string |
| `MISSING_CITY` / `MISSING_COUNTRY` | Required for candidate filtering |
| `DUPLICATE_SUPPLIER_KEY` | `(supplier_name, supplier_hotel_id)` already present |

Group B — **match outcome**, evaluated after scoring:

| Reason | Condition |
|---|---|
| `AMBIGUOUS_CANDIDATES` | Top two candidates within a few points of each other and both above the match floor — the engine genuinely cannot tell which |
| `LOW_CONFIDENCE_INCOMPLETE` | Best score below the floor **and** the record's completeness score is poor — cannot tell whether it is new or a duplicate |

#### One important refinement: a low score alone must not flag

A low score against a candidate usually means *"this is a genuinely different,
new hotel"* — and creating a master is the **correct** outcome. Flagging every
low score would quarantine legitimate new hotels and defeat the purpose.

The flag must trigger on **unverifiability or contradiction**, not on low
similarity:

| Best score | Record completeness | Correct outcome |
|---|---|---|
| Low | Good — name, address, valid coordinates all present | `CREATE_NEW_MASTER` — confidently a new hotel |
| Low | Poor — missing or suspect fields | **`FLAGGED`** — cannot tell if new or duplicate |
| Two candidates near-tied | Any | **`FLAGGED`** — ambiguous |

This is best driven by an explicit **completeness score** (0–100) computed per
record at import from field presence and coordinate validity. It is simple to
compute, easy to explain to a supplier, and gives the flag rule a defensible
second axis instead of relying on the match score alone.

#### On the proposed `< 40` threshold

Sound in principle, but it **cannot be implemented until the scoring rework in
Phase 1 lands** — per §2.8 the achievable score range today is [85, 100], so a
40-point floor can never fire. Once weights are rebalanced to
`name 45 + address 20 + geo 25 + star 5 + chain 5`, a score near 40 corresponds
to roughly good geography plus ~33% name agreement, which is a defensible floor.
The exact number should be set from the ground-truth set in Phase 4, not fixed
in advance.

#### Reporting — this must be visible to the user

Flagging is only useful if it is surfaced. Required:

1. **Import response** — per-supplier flag counts broken down by reason,
   returned immediately from `POST /hotels/import` rather than discovered later.
2. **`GET /api/v1/flagged`** — paginated list with reason, supplier, raw record
   and timestamp.
3. **`GET /mapping/status`** — add `flagged` to the existing status counts.
4. **Per-supplier data-quality report** — flag rate by supplier and reason, so
   it is obvious *which supplier is sending unusable data*. This is the item
   with real commercial value: it turns a mapping problem into a supplier
   conversation.
5. **Re-queueable** — `FLAGGED` is terminal until the record is re-supplied.
   A corrected record must be able to re-enter the queue cleanly.

Keep `FLAGGED` distinct from the `Rejected` status in Phase 1 item #5:
`Rejected` means a human looked and said no; `FLAGGED` means the system could
not form an opinion.

### 7.1 Required new stage — reference dataset validation

Hummingbird is the reference set: it seeds the master table, so its defects are
permanent and propagate to every supplier matched afterwards (§2.9). It must be
validated and canonicalised **before any master hotel is created**, as a
distinct pipeline stage with its own gate.

This stage is **reference-supplier only** and configurable — which supplier is
authoritative should be a setting, not a hardcoded string, so the role can move
without a code change.

#### Two classes of action

| Class | Action | Applied | Reversible |
|---|---|---|---|
| **Normalise** | Rewrite to a canonical form; original retained in `raw_json` | Automatically, logged | Yes — re-derive from `raw_json` |
| **Quarantine** | Withhold the row from master creation | **Only after explicit user approval** | Yes — full restore |

Normalisation is safe and should just happen. Quarantine removes a hotel from
the reference set, and §2.9 shows an automated rule would be wrong more than
80% of the time — so it is **proposed, never auto-applied**. "Inform the user"
is not sufficient here; it needs confirmation before the row leaves the set.

#### Validation checks

| ID | Check | Class |
|---|---|---|
| R-1 | **City canonicalisation** — `Gurgaon`→`Gurugram`, `Bangalore`→`Bengaluru`, `New Delhi And NCR`→`New Delhi`, resolved against a maintained gazetteer with a `city_canonical` column added alongside the raw value | Normalise |
| R-2 | State/country canonicalisation; trim, case-fold, collapse whitespace, strip encoding artefacts | Normalise |
| R-3 | **Coordinate validity** — reject `(0,0)`, points outside the country boundary, or implausibly far from the stated city centroid | Quarantine (propose) |
| R-4 | **Completeness** — name, address, city, country, coordinates present and non-placeholder (`test`, `TBA`, `N/A`, `-`) | Quarantine (propose) |
| R-5 | **Exact duplicates** — repeated `supplier_hotel_id`, or identical name + coordinates | Quarantine (auto-approve safe; still logged and restorable) |
| R-6 | **Near-duplicates** — proximity + name similarity | **Propose only.** Must show both records side by side with distance and brand tokens. Expect a high false-positive rate; assume distinct unless a human confirms |

Note R-5 versus R-6: an exact repeat of the same `supplier_hotel_id` is
unambiguous and safe to auto-quarantine. A *near*-duplicate is a judgement call
and must not be.

#### Storage and restore

Nothing is ever hard-deleted.

- `reference_validation_run` — one row per run: id, supplier, timestamp,
  per-check counts, overall pass/fail.
- `reference_quarantine` — full JSONB snapshot of the original row, check ID,
  reason, run id, status (`Proposed` / `Quarantined` / `Restored` /
  `Dismissed`), plus who actioned it and when.
- Quarantine is a **status change, not a delete**. `supplier_hotels` gains an
  `excluded_at` / `excluded_reason` pair; every downstream query filters on it.
  Restore is therefore a single status flip, and `raw_json` already preserves
  the original payload for normalisation rollback.

**Restore must also re-enter the pipeline.** Flipping the status back is not
enough — the record must be re-queued, and if masters were already built from
the reference set, restoring a row has to trigger re-evaluation of the hotels
near it. Restoring after master creation is the expensive path; the gate below
exists to make it rare.

#### Reporting and the gate

1. **Validation report** — per check: rows affected, action taken or proposed,
   with examples. Produced *before* anything is applied.
2. **`GET /api/v1/reference/validation/{run_id}`** — full result, and
   `GET …/quarantine` for the proposed and active exclusions.
3. **`POST …/quarantine/{id}/approve` and `/restore`** — the approval and
   restore actions, both audited.
4. **Hard gate:** master creation must refuse to run while a reference
   validation run has unresolved `Proposed` items. This is what makes the stage
   meaningful — otherwise the pipeline races ahead and the cleanup is
   retrospective.

#### Expected outcome on the current data

Based on §2.9: roughly 1,700 rows normalised for city canonicalisation
(affecting ~100 city-pair inconsistencies), 0 rows quarantined for
completeness, 0 exact duplicates, and ~13 near-duplicate pairs proposed for
review of which perhaps 2 are genuine. The work is overwhelmingly
normalisation. A user reviewing 13 proposed pairs is a ten-minute task — which
is the right size for a human gate.

### Phase 0 — Reference dataset validation (§7.1)

Runs before master creation. Item IDs are lettered to keep them distinct from
the numbered backlog.

| # | Action | Why |
|---|---|---|
| R1 | City/state gazetteer + `city_canonical` column; normalisation pass (R-1, R-2) | Fixes the 102 internal inconsistencies that fragment the master table |
| R2 | Coordinate and completeness validators (R-3, R-4) | Stops unverifiable rows seeding masters |
| R3 | Exact-duplicate detection with auto-quarantine (R-5) | Unambiguous and safe |
| R4 | Near-duplicate detection, **proposal only**, side-by-side review UI (R-6) | An automated rule would be wrong >80% of the time — §2.9 |
| R5 | `reference_validation_run` + `reference_quarantine` tables; `excluded_at` / `excluded_reason` on `supplier_hotels`; soft-delete everywhere | Makes restore a status flip, not a data recovery exercise |
| R6 | Validation report, approve/restore endpoints, and the **hard gate** blocking master creation on unresolved proposals | Without the gate the cleanup is retrospective and pointless |

### Phase 1 — Accuracy (the product itself)

| # | Action | Why |
|---|---|---|
| 1 | Rebalance weights so identity dominates proximity, e.g. `name 45 + address 20 + geo 25 + star 5 + chain 5` | Geography should *confirm* a name match, never substitute for one |
| 2 | Hard gate: refuse AUTO_MATCH when `name_similarity < 60`, at every distance including 0 m | Eliminates the entire class of failures in §2.3 |
| 2b | **Stop-gap, shippable in days:** tighten the auto-match radius to ~200 m and apply the name gate, ahead of the full reweighting | Hand verification shows 83% precision ≤ 200 m vs 23% beyond it — §2.2b |
| 3 | Replace the exact-city join with geo-first candidate search; use city as a tiebreaker, not a filter | Recovers the 607 duplicate masters |
| 4 | Run AI validation on **all** candidate matches, and let it search independently rather than re-ranking the rule shortlist | The current design cannot catch rule-engine errors |
| 4b | **Add a lower AI rejection threshold (~0.70): below it, cancel the suggested match and route to `CREATE_NEW_MASTER`** — only 0.70–0.85 reaches a human | Clears ~90% of the review queue automatically; the branch currently has no lower bound — §2.6b |
| 5 | Add a `Rejected` terminal status | Makes human review actually work |
| 6 | Raise the candidate `LIMIT` above 10 for dense areas | Correct master currently may not reach scoring |
| 7 | Add coordinate sanity checks (distance from city centroid, cross-supplier outlier detection) | Bad coordinates currently cause silent merges |
| 8 | **Implement the `FLAGGED` outcome and completeness score (§7.0)** | Stops unusable records silently becoming masters — §2.8 |
| 9 | **Expose flagged records: import response, `GET /flagged`, status counts, per-supplier quality report** | A flag nobody sees is not a control |
| 10 | **Build a labelled ground-truth set of 2,000–3,000 verified pairs** | Without it, no threshold change can be proven to help |

### Phase 2 — Scale

| # | Action | Impact |
|---|---|---|
| 11 | `CREATE INDEX ON hotel_mapping_queue(supplier_hotel_id)` | 850× — highest-leverage single change in the codebase |
| 12 | Stop accumulating `results`; return counters only | Removes the 15 GB requirement |
| 13 | `SELECT … FOR UPDATE SKIP LOCKED` on queue fetch | Makes 4 workers safe |
| 14 | `UNIQUE(supplier_name, supplier_hotel_id)` on `supplier_hotels` and `hotel_mappings`; add foreign keys | Stops duplicate and ambiguous mappings |
| 15 | Replace `NullPool` with a pooled engine | Connection reuse under sustained load |
| 16 | Retune IVFFlat `lists` to ~1,000–3,000 | Vector search stays sub-linear at 10 lakh |
| 17 | Move embedding generation off the event loop; batch encodes | Throughput; unblocks the async API |

### Phase 3 — Correctness and hardening

| # | Action |
|---|---|
| 18 | Fix `SuggestedMatchesService` — pass `candidate_master_ids` |
| 19 | Add `state` to `create_master_hotel_from_supplier` |
| 20 | Fail imports loudly on missing required columns (distinct from `FLAGGED`, which is per-record — this is a malformed *file*) |
| 21 | Remove the duplicate `SuggestedMatchesResponse`; pick one commit strategy |
| 22 | Add authentication (API key minimum); restrict CORS |
| 23 | Replace `print()` with structured logging; add metrics on match-rate, score distribution and flag rate |
| 24 | Validate `limit` parameters; delete `docker-compose-1.yml` |

---

## 8. Effort Estimate to Production Readiness

Assumes the two developers who built it continue, working in parallel tracks,
plus an analyst or ops person available for data labelling. Figures are working
days.

| Phase | Work | Dev-days | Calendar (2 devs) |
|---|---|---|---|
| 0 | Ground-truth labelling — 2,000–3,000 verified pairs (analyst-led, starts immediately, runs in parallel) | 6–8 analyst-days | Days 1–8 |
| 0b | **Reference dataset validation — gazetteer, validators, quarantine/restore, gate (R1–R6, §7.1)** | **4–5** | Days 1–6 |
| 1 | Scale + integrity fixes (#11–#17) — well-understood, low-risk | 5–7 | Days 1–5 |
| 2 | Correctness and hardening (#18–#24) | 3–4 | Days 4–7 |
| 3 | Scoring rework — weights, name gate, geo-first candidates, AI redesign, `Rejected` state, coordinate checks (#1–#7) | 8–10 | Days 5–12 |
| 3b | **`FLAGGED` outcome, completeness score, and reporting surfaces (#8–#9, §7.0)** | **3–4** | Days 8–13 |
| 4 | Tuning against ground truth — iterate match thresholds *and* the flag floor, measure precision/recall, repeat | 4–6 | Days 11–17 |
| 5 | Full 10 lakh load + soak test, then fix what it surfaces | 4–5 | Days 16–21 |
| 6 | Deployment, monitoring, alerting, runbook, handover | 3–4 | Days 19–23 |

**Total: ≈ 34–45 dev-days, or 5–5.5 calendar weeks with both developers working
in parallel.**

Call it **24–26 working days (about five and a half weeks)** if the team is
focused and nothing surprising surfaces, and **7 weeks** if the load test
exposes further issues — which it usually does at a 100× jump in data volume.

Two notes on the additions:

- **Phase 3b** (`FLAGGED`) adds ~2 calendar days. Most of its cost is the
  reporting surfaces, not the flag logic — the decision is a small amount of
  code on top of work Phase 3 already does.
- **Phase 0b** (reference validation) adds ~4–5 dev-days but **little calendar
  time**, because it runs in parallel with Phase 1 on the other developer's
  track and shares infrastructure with Phase 3b: the quarantine table, restore
  flow and reporting surfaces are the same pattern as `FLAGGED`. Building them
  together is materially cheaper than building them separately — sequence
  Phase 0b and Phase 3b under one owner.

The city gazetteer in R1 is partly a **data task, not a code task**. Around 100
inconsistent city pairs need a human decision on the canonical form. That is
another few hours of analyst time, and it should be folded into the Phase 0
labelling effort rather than treated as engineering work.

**Splitting the tracks:** Phases 1–2 and Phase 3 are independent and should run
concurrently. One developer on scale and integrity, one on scoring. They converge
at Phase 4.

**The critical path is not the code — it is Phase 0 and Phase 4.** The
engineering changes are mostly small and well-understood; the missing index is a
one-line fix worth 850×. What genuinely takes time is building the labelled
dataset and then iterating thresholds against it. Start the labelling on day one;
everything else queues behind it.

### Assumptions and risks

- **No accuracy target has been defined.** "Accuracy matters" needs a number —
  is 95% precision on auto-matches acceptable, or 99%? The answer changes both
  the threshold design and how much of the volume falls to manual review. This
  should be decided before Phase 3 begins.
- **A higher precision bar means a larger review queue.** At 10 lakh records,
  even a well-tuned system will route a meaningful share to humans. Whoever owns
  that queue needs the capacity planned, and the `Rejected` state (#5) must exist
  before it opens.
- **The existing 8,432-record database is contaminated** — 64 ambiguous
  mappings, 607 duplicate masters, 1,976 bad merges. It should be rebuilt from
  `supplier_hotels` after the scoring fix, not repaired in place. `raw_json`
  retains the original payloads, so no source data is lost.
- Estimates assume both developers stay on the project. If they roll off,
  add 5–8 days for ramp-up.

---

## 8b. Remediation Applied — Rebuilt Pipeline (v2)

The accuracy fixes were implemented in the codebase and the full 8,432-hotel
mapping was re-run against the live database. Results below are measured on that
rebuilt database, not projected.

### What changed in the code

| File | Change |
|---|---|
| `app/normalization/normalizer.py` | New `core_hotel_name()` strips the hotel's own city/state and chain boilerplate out of its name before comparison; `compare_hotel_names()` returns both a loose and a **length-sensitive strict** similarity; conflicting ordinals ("Leisure Valley 1" vs "2") and disagreeing **distinguishing parts** short-circuit to zero |
| `app/matching/matcher.py` | Rebalanced to `name 45 + address 20 + geo 25 + star 5 + chain 5`; geo now **decays with distance instead of paying a flat 85**; a tiered distance/name gate replaces the old thresholds; single-token core names restricted to 100 m |
| `app/services/matching_service.py` | Candidate search is **geo-first** — the exact-city join is gone; `LIMIT` raised 10 → 25; completeness gate added; tier-passing candidates always outrank non-passing ones |
| `app/matching/ai_integration_service.py` | Added the lower rejection bound (0.70): below it the suggested match is cancelled outright instead of queued for a human |
| `app/services/queue_processing_service.py` | Handles the new `FLAGGED` outcome; AI validation keyed off the decision rather than a hardcoded score window |
| `scripts/init.sql` | `flagged_records` table; the missing `hotel_mapping_queue(supplier_hotel_id)` index; `UNIQUE(supplier_name, supplier_hotel_id)` on `hotel_mappings` |

### The three defects that mattered

Each was found by hand-checking output, not by inspection:

1. **City tokens inflate name similarity.** "Vivanta Coimbatore" scores 71%
   against "WelcomHotel Coimbatore" purely on the shared city. Since the city is
   known from its own column, it is now stripped before comparison.
2. **`token_set_ratio` scores a subset as 100%.** "The Residency" matched "The
   Residency Towers" — two different Chennai hotels. Longer distances now require
   the length-sensitive `token_sort_ratio`.
3. **Shared *locality* tokens do the same thing.** "Namah Resort Jim Corbett" vs
   "Voco Jim Corbett" agree on two of three tokens. Removing the words two names
   share and comparing what remains — "namah" against "voco" — settles it.

### Measured results

Figures re-verified against the live database on 23 July 2026 (§8e). The
pipeline has been re-run since this section was written, under a two-reason flag
taxonomy that discards 89 more records; the "After" column is the current state.

| Metric | Before | After |
|---|---|---|
| Auto-match precision (hand-verified) | 51.7% | **0 false positives in 66 pairs** |
| Clearly-wrong merges | 1,863 (33.5%) | **0 confirmed** |
| Confident matches (name sim ≥ 0.70) | 38.5% | **77.0%** |
| Redundant/duplicate masters | 473 | **114 pairs (217 masters)** |
| Records stuck in review limbo | 670 | **0** |
| Failed records | 0 | **0** |
| Records flagged and reported | n/a | 299 (212 `SUPPLIER_ID_COLLISION`, 87 `DUPLICATE_SUPPLIER_ROW`) |
| **End-to-end success rate** | **~54%** | **95.1%** |

Final run: 8,432 input → 299 flagged → 8,133 processed → 5,366 auto-matched +
2,767 new masters, of which 114 pairs are redundant.
Success = 5,366 + (2,767 − 114) = **8,019 / 8,432 = 95.1%** (98.6% of processed).

### Verification method

66 pairs from the rebuilt database were adjudicated by hand, deliberately
weighted toward where errors would hide rather than sampled uniformly:

- **19** lowest name-similarity auto-matches (the hardest cases) — all correct;
  they are name-form variations such as `The Lodhi` vs
  `The Lodhi: A member of The Leading Hotels`
- **25** uniformly random auto-matches — all correct
- **22** matches beyond 300 m with the weakest names (the riskiest tier) — all
  correct

**0 false positives in 66.** With zero errors in 66 trials the 95% upper bound on
the true false-positive rate is **4.5%** — so "zero false positives" is verified,
not proven exhaustively. Closing that gap needs the full ground-truth set.

### What remains

- **114 redundant master pairs** (217 distinct masters) — the same hotel split in
  two, mostly coordinate discrepancies beyond the tier limits. Recall, not
  precision: recoverable later by merging, whereas a false merge is not.
- The 299 flagged records are defects in the Sabre feed — duplicate lines and
  reused hotel ids. They are reported by reason via `flagged_records`, never
  mapped, and never promoted to masters. **48 of the 299 are over-discarded**;
  see §8e.
- Thresholds were tuned against 66 hand-labelled pairs. They should be re-fitted
  against the full 2,000–3,000-pair ground-truth set before production.
- Scale work (Phase 2) is **not** included here beyond the queue index and unique
  constraint. The 15.3 GB memory issue and `SKIP LOCKED` remain open.

---

## 8c. Remediation Applied — Scale (Phase 2)

The scale blockers were fixed and verified against a purpose-built 10-lakh
dataset loaded alongside the real data, then removed.

### What changed

| File | Change |
|---|---|
| `app/services/queue_processing_service.py` | `claim_pending_batch()` replaces `get_pending_queue_ids()` — a single `UPDATE … WHERE id IN (SELECT … FOR UPDATE SKIP LOCKED)` that claims and marks Processing atomically. `process_pending_batch()` **returns counters only**, no accumulated results. `print()` replaced with `logging`. Dry-run mode terminates at `Skipped` instead of looping back to `Pending` |
| `app/database/connection.py` | `NullPool` → pooled engine (`pool_size`, `max_overflow`, `pool_pre_ping`, `pool_recycle`). `get_db()` no longer commits behind the service's back |
| `app/matching/embedding_service.py` | Thread-safe single model load; **batch encoding**; `asyncio.to_thread` wrappers so CPU-bound `encode` no longer blocks the event loop |
| `app/matching/master_embedding_service.py` | Batch-encodes each block and inserts with `executemany` instead of one encode + one INSERT per hotel |
| `scripts/init.sql` | Composite `(status, id)` queue index; IVFFlat `lists` 100 → 1000; **foreign keys** on mappings, queue and flags |
| `scripts/tune_for_scale.sql` | New — post-bulk-load `ANALYZE`, IVFFlat rebuild sized to `sqrt(rows)`, `VACUUM` |
| `config.py` | Pool and batch settings exposed rather than hardcoded |

### Measured at 10 lakh

Test bed: 1,000,000 synthetic supplier hotels across 500 city clusters,
336,101 master hotels, 1,000,000 pending queue rows.

| Query (runs per hotel) | Plan | Before | After |
|---|---|---|---|
| Queue claim (per 500) | Index Scan + SKIP LOCKED | n/a | **8.2 ms** (0.016 ms/hotel) |
| Candidate search | Bitmap Index Scan on GIST geo | — | **12.1 ms** |
| Status update | Index Scan | **40 ms** (Seq Scan) | **0.393 ms** |

| Behaviour | Before | After |
|---|---|---|
| Worker memory over 10,000 hotels | grows ~16 KB/hotel → **15.3 GB at 10 lakh** | **881 → 889 MiB, flat** |
| Duplicate mappings under 4 concurrent workers | unbounded | **0** |
| Hotels processed twice | unbounded | **0** |
| Failed records | — | **0** |

Memory holding flat across 10,000 processed hotels is the direct evidence that
the OOM is resolved: the old code would have retained ~160 MB by that point and
continued climbing linearly.

### Throughput

Sustained **47 hotels/sec** per worker process, CPU-bound in the scoring loop —
the database sat at 11.85% CPU while the worker ran at 1224%. Because the
bottleneck is CPU rather than the database, throughput scales horizontally by
adding worker processes, which `SKIP LOCKED` now makes safe.

**This figure is a worst-case floor.** The synthetic data averages **2,061
master hotels within 1 km** of each supplier hotel; the real data averages
**6.8** — a roughly 300× denser stress test, and candidate scoring is exactly
where the CPU goes.

| Scenario | Projected time for 10 lakh |
|---|---|
| 1 worker, synthetic worst-case density | ~5.9 hours |
| 4 workers, synthetic worst-case density | ~1.5 hours |
| 4 workers, realistic density | well under 1 hour |

Against the original code the same 10 lakh records would not have completed at
all — the worker would have been OOM-killed, and 10 lakh × 2 status updates at
40 ms each is ~18 hours of sequential scanning on its own.

### What is still open

- `UNIQUE(supplier_name, supplier_hotel_id)` on `supplier_hotels` is **not**
  applied — 299 rows in the current import reuse an id (87 duplicate lines, 212
  genuine collisions). They are caught at mapping time and flagged
  `DUPLICATE_SUPPLIER_ROW` / `SUPPLIER_ID_COLLISION`, but the constraint should
  be added once the source files are de-duplicated, so duplicates are rejected at
  import rather than during mapping.
- No authentication; CORS is still `*` (Phase 3, items #22).
- Retry handling for `Failed` rows is manual — `retry_count` is incremented but
  nothing re-queues them automatically.
- Throughput was measured on one host. A real 10-lakh run should be executed
  once end to end before go-live.

---

## 8d. Production Safety — Detecting Our Own Failures

Sampling cannot certify accuracy at 10 lakh records. A hand-checked sample of 66
with zero errors bounds the true false-positive rate below **4.5%** at 95%
confidence — nowhere near the tolerance for a unified booking API, where a false
merge sends a guest to a property they did not book. Three additions close that
gap without needing to verify records one by one.

### 1. A structural invariant, enforced not merely detected

**One supplier contributes at most one hotel to a master.** A supplier does not
list the same property twice under different names, so if a master already holds
a different hotel from that supplier, the two are different properties.

This is now a `NOT EXISTS` clause in the candidate query
(`matching_service.py`), so the merge is prevented rather than reported. It needs
no ground truth, and unlike sampling it is checked against **every** mapping.

Measured across all 5,366 auto-matches: **0 violations.**

The cost is deliberate — a genuine duplicate listing inside one supplier feed
becomes a second master instead of merging. A duplicate is recoverable by
merging later; a false merge is not.

### 2. Confidence tiers on every mapping

Each mapping now records the evidence that produced it — name similarity (loose
and strict), distance, and a confidence tier — so decisions are auditable and
reversible after the fact.

| Tier | Criteria | Mappings | Share |
|---|---|---|---|
| **TIER1** | near-identical name, ≤ 100 m | 3,659 | 67.7% |
| **TIER2** | strong name, ≤ 200 m | 1,257 | 23.3% |
| TIER3 | good name, ≤ 500 m | 262 | 4.8% |
| TIER4 | weaker name or > 500 m | 217 | 4.0% |

**91% of matches fall in TIER1/TIER2** and can be published unattended. Whatever
residual risk exists concentrates in TIER3/TIER4 — 8.8% of matches, a bounded
and targeted review set rather than the whole catalogue.

### 3. A continuous integrity monitor

`GET /api/v1/mapping/integrity` (`app/services/integrity_monitor.py`) runs after
every batch and looks for the *shape* of a bad merge:

- **Same-supplier collisions** — the highest-precision signal available, and the
  invariant now enforced above.
- **Absorbing masters** — geographic spread beyond 400 m, or weak worst-case
  name agreement within a master.

Note when reading its output: a master with 999 m spread but 100% name agreement
is a **coordinate discrepancy between suppliers, not a merge error**. The
combination that indicates absorption is wide spread *together with* weak names.

### A correction worth recording

An earlier pass of this monitor appeared to find 6–7 false positives. It had
joined `hotel_mappings` to `supplier_hotels` on
`(supplier_name, supplier_hotel_id)`, and that join **fanned out**, pairing
unrelated hotel names under a single mapping row. Investigating the artefact
surfaced a far more serious finding, below.

### Supplier id collisions — a live risk in the source data

`(supplier_name, supplier_hotel_id)` is **not unique in supplier feeds**:

| Supplier | Ids reused for *different* hotels | Rows affected |
|---|---|---|
| Sabre | **89** | **212** |

Sabre assigns one id to two different properties — e.g. `100355120` is both
`Airport Road Bangalore Regenta Inn` and `Grand Country Stays - Devenaha`. Any
downstream lookup keyed on that id is ambiguous and could resolve a booking to
the wrong hotel. **This is a defect in the supplier feed that no mapping logic
can repair, and it exists today regardless of which mapping engine is used.**

Two changes were made in response:

1. `hotel_mappings` now carries `supplier_hotel_row_id`, a reference to the
   actual `supplier_hotels.id`, with the uniqueness constraint moved onto it. A
   mapping now resolves to exactly one physical row.
2. Flag reasons distinguish `SUPPLIER_ID_COLLISION` (same id, different hotels —
   must be raised with the supplier) from `DUPLICATE_SUPPLIER_ROW` (same id, same
   hotel — a harmless duplicate line).

Current run: **212 `SUPPLIER_ID_COLLISION`**, 87 `DUPLICATE_SUPPLIER_ROW`, 0
failed.

### 8d.1 Evidence pack — Sabre supplier id defects

Raised as a supplier data-quality issue. This exists in the source feed and is
independent of any mapping engine. Full machine-readable evidence:
**`sabre_id_collisions.csv`** (126 pairs, with both hotels' names, cities,
coordinates, separation and name similarity).

**Finding:** in the Sabre feed, `supplier_hotel_id` is not a unique identifier.
**89 ids are attached to 212 rows describing more than one hotel.** 41 of those
span different cities; 7 exceed 10 km separation; the widest is 24.2 km.

The defects fall into three classes, each needing a different fix from Sabre:

| Class | Defect | Pairs | Distinct ids | Worst case |
|---|---|---|---|---|
| **A** | **One id reused for genuinely different properties** | 45 | 30 | 13.0 km |
| **B** | Same property duplicated, coordinates disagree | 79 | 57 | 24.2 km |
| **C** | Tombstoned records shipped in the live feed | 2 | 2 | 16.7 km |

#### Class A — one id, two different hotels (highest severity)

The id cannot resolve to a single property, so any booking or rate lookup keyed
on it is ambiguous.

| Sabre id | Property A | Property B | City | Apart |
|---|---|---|---|---|
| `100240826` | Aalia Resort | Amatra By the Ganges | Haridwar | 13,015 m |
| `100041328` | Hotel Madhuban | Wh Dehradun | Dehradun | 7,449 m |
| `100169520` | Treebo Tryst Amber | Amber Inn by Orion Hotels | Delhi | 6,066 m |
| `100178310` | Taurus (prev. Best Western) | Taurus Sarovar Portico | Delhi | 1,819 m |
| `100257992` | Nandhana Comforts Marathahalli | La Sara Comforts | Bengaluru | 1,040 m |

These are unambiguous: different brands, different operators, kilometres apart,
under one identifier.

#### Class B — same property, contradictory coordinates

The names agree; the coordinates do not. One of the two records places the hotel
in the wrong location, which breaks geo matching and any "hotels near me"
search.

| Sabre id | Property | City A | City B | Apart |
|---|---|---|---|---|
| `100585026` | Angsana Oasis Spa Resort | Bengaluru | BANGALORE | **24,216 m** |
| `100164096` | Goldfinch Retreat | Devanahalli | Bangalore | 20,137 m |
| `100558726` | Parkway Deluxe | Delhi | New Delhi | 19,822 m |
| `100181048` | Ahuja Residency, Sunder Nagar | Delhi | New Delhi | 13,958 m |

#### Class C — deleted records still in the feed

Two records ship with the literal name prefix `Zz To Be Deleted-`:

- `100079568` — `Zz To Be Deleted- Niraamaya` / `Niraamaya Surya Samudra`
- `100018830` — `Zz To Be Deleted- Shreyas` / `Shreyas Retreat`

Internal lifecycle state is leaking into the production feed.

#### What to ask Sabre for

1. **Class A** — reissue distinct ids for the conflicting properties, and
   confirm whether ids are guaranteed unique and stable across the catalogue.
2. **Class B** — correct the erroneous coordinate on the duplicated records and
   deduplicate them.
3. **Class C** — suppress tombstoned records from the live feed.
4. **Contractually** — a written statement of the uniqueness and stability
   guarantee for `supplier_hotel_id`. Every downstream system, including the
   current Vervotech integration, assumes it identifies one property.

#### Interim handling on our side

Pending Sabre's response, these are contained rather than ignored:

- `hotel_mappings` keys on `supplier_hotel_row_id` (the physical row), so a
  mapping always resolves to exactly one record even when the supplier id does
  not.
- Affected records are flagged `SUPPLIER_ID_COLLISION` and are never mapped or
  promoted to masters, so ambiguity cannot reach a booking path.
- Current run: **212 flagged `SUPPLIER_ID_COLLISION`**, 87 `DUPLICATE_SUPPLIER_ROW`.

### The highest-value validation available is already in-house

HummingBird currently runs Vervotech. **Those existing mappings are a
ground-truth oracle covering the live inventory** — orders of magnitude more
labelled pairs than the 2,000–3,000 the plan calls for, at no labelling cost.

Running both engines over the same inventory and diffing gives, directly:

- a false-positive rate measured on real volume rather than a 66-record sample;
- every disagreement as a concrete case to adjudicate;
- a defensible go/no-go number for replacing Vervotech.

This should happen **before** any cutover, and it replaces most of the Phase 0
labelling effort.

### Recommended rollout

| Stage | Action | Gate |
|---|---|---|
| 1 | Shadow-run against Vervotech on live inventory; diff | FP rate measured on real volume |
| 2 | Adjudicate every disagreement; re-fit thresholds | FP rate within agreed tolerance |
| 3 | Publish TIER1/TIER2 only (91%); hold TIER3/TIER4 | Integrity monitor clean per batch |
| 4 | Extend to TIER3 once its measured rate is acceptable | — |
| 5 | Retire Vervotech | Sustained clean batches |

Vervotech should stay live through stages 1–4. The cost of running both is small
against the cost of one bad merge reaching a booking path.

---

## 8e. Re-verification — 23 July 2026

Read-only re-run against the live `hotel_mapping_db_v2`. No remap was performed
and nothing was written. The database has moved since §8b was written: 89
records previously auto-matched are now discarded under a two-reason flag
taxonomy. Master count is identical across both runs — the delta is entirely in
what gets discarded, not in how masters are formed.

| Metric | §8b as written | 23 July 2026 |
|---|---|---|
| Input | 8,432 | 8,432 |
| Discarded | 210 | **299** |
| Mapped | 8,222 | **8,133** |
| Auto-matched | 5,455 | **5,366** |
| New masters | 2,767 | 2,767 |
| Redundant | 106 | **114 pairs / 217 masters** |
| Success | 96.3% | **95.1%** |

### Precision — nothing wrongly merged

Same-supplier collisions **0**; duplicate supplier keys in `hotel_mappings`
**0**; registry orphans, split identities and masters without a public id all
**0**; review limbo **0**; failed records **0**. Tiers: TIER1 3,670 · TIER2
1,261 · TIER3 241 · TIER4 194.

The integrity monitor reports **185 absorbing masters, all false alarms**. Every
one trips the >400 m spread rule only; none trips the weak-name rule, and 180 of
185 carry an identical name on every member. Reading the six widest by hand,
each is one hotel where a single supplier's coordinate is wrong —
`HBM-00000745` Royal Court Madurai has four suppliers agreeing on "No 4 West
Veli Street, Opp Railway Station" with one pin 930 m away. The geo outlier
rotates across suppliers (Sabre 91, Booking.com 43, GRN 21, ClearTrip 20,
Hummingbird 20), the signature of feed coordinate noise rather than
over-merging. Address trigram similarity is not usable as a check here: 92 of
195 score below 0.3 purely because suppliers write the same address differently.

**The structural invariant result is currently vacuous.**
`master_non_merge_assertion` is empty — no reviewer split has happened yet — so
"0 violations" proves nothing until the review console is used.

### Recall — worse than §8b records

Beyond the 114 pairs within 150 m, a further **268 pairs share an identical name
*and* an identical city string** while sitting under different masters. 46 are
151–489 m apart and 37 are 516–937 m; those ~83 are strong split candidates
(Walisons Hotel Srinagar 160 m, Taj Tirupati 210 m, The Oberoi Mumbai 260 m,
Conrad Pune 298 m). The 56 pairs beyond 5 km are genuinely different properties
sharing a name. The mechanism is the 1 km candidate radius
(`matching_service.py:135`) against a TIER4 ceiling of 999 m: a pair whose
supplier coordinates disagree by more than the tier allows can never merge.
884 of 2,767 masters (32%) hold a single supplier record.

Note this is the same root cause as §8e's false alarms above, seen from the
other side. Supplier coordinate noise inflates spread within a master and blocks
merges between masters. Fixing coordinate quality addresses both.

### Are the 299 discards correct?

**251 of 299 yes; 48 no.** Both error modes trace to one line:
`supplier_id_collision()` (`app/services/matching_service.py:193-215`)
distinguishes the two cases with `count(DISTINCT lower(hotel_name)) > 1` — exact
string equality — which is brittle in both directions.

**`DUPLICATE_SUPPLIER_ROW` — 87 rows, 61 groups: correct.** Every group retained
exactly one mapped row; none was lost. 28 groups have members over 200 m apart,
but 25 of those have matching addresses — Azaya Beach Resort reads "336 0
Village Calwaddo, Benaulim" against "336/0, Village Callavado, Benaulim" while
28.9 km apart on paper. The distance is a bad coordinate, not a second property.
Only 3 groups have genuinely differing addresses.

The defect is which row survives. The rule never consults geography, so the
keeper is whichever was processed first. Among the 16 groups spread over 1 km,
only **3 kept a row whose coordinate agrees with other suppliers; 13 kept an
isolated row** — anchoring the master to the wrong location. Walisons Hotel
Srinagar appears both here and as three separate masters.

**`SUPPLIER_ID_COLLISION` — 212 rows, 89 groups: 68 correct, 21 not.** The 68
are genuinely different properties sharing a Sabre id (Goldfinch Retreat
Devanahalli vs Bangalore, 16 km apart; Niraamaya Surya Samudra against a
tombstone record named "Zz To Be Deleted- Niraamaya"). Discarding them is right
and the repair belongs with Sabre.

The other **21 groups (48 rows) are one property listed twice under a name
variant** and should have been deduplicated rather than dropped: "Hotel Mamallaa
Heritage" / "Mamallaa Heritage Hotel" 40 m apart; "Chennai The Belstead" / "The
Belstead Chennai" 35 m; "Grand Palace Hotel & Spa" / "Grand Palace Hotel Spa"
10 m; "Clarks Inn Gurgaon" / "DS Clarks Inn Gurgaon" 3 m; "Cavala Seaside
Resort" / "Cavala The Seaside Resort" 13 m.

41 of those 48 rows describe a hotel already in the master set via another
supplier, so the loss is Sabre's link rather than the property. Across all 299
discards, **121 rows (103 collision, 18 duplicate) describe hotels absent from
the master set entirely** — the true coverage loss.

**Fix, not yet applied:** replace the string-equality test with the
name-similarity-plus-distance discriminator the matcher already uses (similar
name and close = duplicate row, keep one; different name or far apart = true
collision, flag all), and when deduplicating prefer the row whose coordinate
agrees with other suppliers over the first one seen.

---


## 8f. Session 2 — 23 July 2026 (afternoon)

The morning's figures (§8e) are superseded. Full detail, including the
measurement behind every claim, is in `CHANGES.md` § "Session 2"; this section
records the verdict-level changes.

### Current state

| Metric | §8e (morning) | Now |
|---|---|---|
| Input | 8,432 | 8,432 |
| Mapped | 8,133 | 7,382 |
| Masters (all rows) | 2,767 | **2,165** |
| — of which publishable (Active) | not distinguished | **1,734** |
| — Provisional (one supplier, unconfirmed) | not distinguished | 431 |
| Manual review | 0 | 755 |
| Discarded | 299 | 295 |
| Duplicate master pairs (<150 m) | 114 | **28** |
| Masters with no public id | 75 (undetected) | **0** |
| Same-supplier collisions | 0 | **0** |

Two counts were previously conflated under the word "masters". A master seeded by
a single supplier is registered Provisional and excluded from exports until a
second supplier corroborates it, so the publishable figure is 1,734, not 2,165.
Every figure in this report before §8f is the raw row count.

### Verdict changes

**"Is the AI layer an effective safeguard?" — now partially yes.** §2.5 recorded
it as bypassed and ineffective. The pre-creation safety net is real and
measurable: 131 records per run intercepted on their way to becoming duplicate
masters, at an average similarity of 90.3, escalating to review and never
merging. These are pairs whose coordinates disagree by more than the 1 km
candidate radius, so no threshold change could have reached them. The borderline
validation path (§7.0 "Place 1") is now correctly wired but fires on nothing —
0 of 400 sampled candidates land in its band, because the tier gate is a hard cut.

**"Can it be trusted with 10 lakh records?" — one caveat removed, one added.**
Removed: masters can no longer end a run unpublishable (75 → 0). Added: the
morning run silently lost 117 records to a worker crash while reporting success,
which no metric detected. Claims now expire; abandoned work is counted as
waiting and surfaced as `stalled`.

**Scoring is no longer mis-scaled.** `star_rating` is null on 6,878 of 8,432
records and only one supplier supplies it; `chain_name` is null on all 8,432.
Ten of the nominal 100 points were unreachable, so `AUTO_MATCH_MIN_SCORE` of 40
was really 40 of an attainable 90. Both weights removed and replaced with two
features chosen by measured discriminating power (m/u of 12.1× and 11.4×, against
1.2× for the postal code the scorer already trusts enough to penalise on).

**Geography was being overridden.** The candidate ranking compared an exact-name
flag before the composite score, so among candidates that had already passed the
gate a name matching character-for-character beat one scoring 13 points higher
and sitting 814 m closer. Corrected; the preference now applies only among
candidates that failed the gate, where the question is which deserves a human.

### Accuracy is now measured, not estimated

Vervotech's existing mapping — the "highest-value validation available" flagged
in §8d — has been loaded as `reference_mapping`: **8,575 labelled pairs across
1,715 hotels**, covering essentially the whole dataset at no labelling cost.
This replaces the Phase 0 labelling effort entirely.

| | Value |
|---|---|
| **False merges in published data** | **0 of 2,173 masters** |
| False merges including held mappings | 2 (99.91% clean) |
| Recall — hotels intact in one master | 1,296 / 1,714 = **75.6%** |

Precision was measured per tier policy rather than assumed. TIER1 and TIER2 have
produced **zero** false merges across 1,714 hotels; both survivors come from
TIER3. `PUBLISH_TIERS = "TIER1,TIER2"` is therefore the widest provably-clean
policy, not a conservative guess. It withholds 540 of 5,209 auto-matches (10.4%)
from export while leaving them in the database, so recall is unaffected.

All eight original false merges were chain sub-brands sharing a building — Red
Fox beside Lemon Tree, Blu Towers beside Blu, Golden Tulip Essential beside
Golden Tulip. A `BRAND_TIER_TOKENS` veto reduced them to 2 at a cost of one split
in 1,714.

This changes the §9 verdict on trustworthiness: the accuracy claim no longer
rests on 66 hand-verified pairs bounding the error rate at 4.5%. It rests on
1,714 hotels bounding it at **0.17% at 95% confidence**, with zero errors in the
published set — and `/api/v1/evaluation/accuracy` re-measures it on every run.

**The generalisation caveat is material and is documented in full in
`CHANGES.md` § "Will it hold on new data?"**. In short: the tier gate and the
structural invariants transfer to new data; the fitted thresholds, the
hand-curated sub-brand list and the absolute token-rarity cut-offs do not. The
defensible external claim is a process ("published mappings had zero false
merges against a 1,714-hotel reference, re-measured every run, unverified tiers
withheld"), not a snapshot ("100% accurate").

### Assessment unchanged

Superseded by the section above: accuracy is now measured against 1,714 hotels
rather than 66 pairs. What remains unchanged is that **recall is the open
problem** — 418 splits, of which ~118 already have a record in the review queue
and ~300 the pipeline never doubted. `review_attach_decision` now
captures a labelled pair every time a reviewer attaches a record, so the real
ground-truth set accumulates as a by-product of review rather than as a separate
project. That remains the highest-value outstanding work.

---

## 9. Final Verdict

| Question | Answer |
|---|---|
| Does the system run and stay up? | Yes — stable, zero failures over 8,432 records |
| Does cross-supplier matching work? | For isolated hotels with agreeing city names, yes |
| Does it fail in dense urban areas? | Yes — 409 masters badly merged; 33.4% of auto-matches wrong |
| Does it also create duplicate masters? | Yes — 607 duplicate pairs from city-string mismatch |
| Is the AI layer an effective safeguard? | No — bypassed above 90, and not independent below it |
| Is the manual review queue usable? | **No** — 669 of 670 records are unrelated hotels; ~90% could be auto-rejected from data already computed (§2.6b) |
| Can it reject an unusable record? | **No** — no such outcome exists; unusable records silently become masters (§2.8) |
| Is the reference dataset (Hummingbird) clean? | Complete, but **internally inconsistent** — 102 conflicting city pairs, no validation stage exists (§2.9) |
| Can it physically process 10 lakh records? | **Yes, after Phase 2** — verified at 10 lakh: flat memory, 0.393 ms status updates, safe concurrency (§8c) |
| Can it be trusted with 10 lakh records? | **Accuracy yes** (0 false positives in 66 verified — §8b, §8e, §8f); **thresholds still need re-fitting against the full ground-truth set**, and note that 66 pairs bounds the FP rate at 4.5%, not zero |
| Is it fixable? | **Largely fixed** — §8b and §8c applied; auth, import-time dedup and retry handling remain |
| Effort to production | **≈ 24–26 working days (2 devs, parallel tracks); 7 weeks if the load test surprises us** |

---

## Appendix A — Verification Method

Findings were established by four independent means; nothing here rests on
reading the code alone.

1. **Static review** — all 3,754 lines of Python across 28 modules.
2. **Live database queries** — against the running `hotel_mapping_db_v2`
   container holding the completed 8,432-hotel run. Source of every figure in
   §2.2, §2.3, §2.6 and §4.
3. **Offline reproduction** — the pipeline re-implemented against the raw
   supplier spreadsheet, calling the project's own `calculate_rule_based_score`
   and replicating the candidate SQL. Agreement with the live system was within
   0.05% (§2.4), confirming the simulation is faithful.
4. **Scale benchmark** — a synthetic 1,000,000-row queue table built in the live
   database, with `EXPLAIN ANALYZE` before and after adding the missing index
   (§3.1). The table was dropped after measurement; no project data was modified
   at any point during this audit.

The HTTP 500 in bug #3 and the `TypeError` behind it were reproduced against the
running API container, not inferred.

One caveat: the offline reproduction regenerated `normalized_name` using the
repository's own `normalize_hotel_name`, because the raw spreadsheet has no
pre-normalised column. This affects the reproduction only. Every headline
figure — 33.4%, 409 merged masters, 607 duplicates, 64 ambiguous mappings, the
850× index benchmark — comes from the live database or the running API.

## Appendix B — Scoring Reference (current behaviour)

| Distance | Geo | Name max | Address max | Star | Chain | Achievable range |
|---|---|---|---|---|---|---|
| 0 m | **95** fixed | 5 | 0 | 0 | 0 | 95–100 → **auto-matches unconditionally** |
| ≤ 100 m | **90** fixed | 7 | 3 | 0 | 0 | 90–100 → **auto-matches unconditionally** |
| ≤ 1000 m | **85** fixed | 10 | 5 | 0 | 0 | 85–100 → auto-matches on 5 points from any source |
| > 1000 m | 10 | 35 | 15 | 5 | 5 | 10–**70** |

Decision: `≥ 90` AUTO_MATCH · `75–89` MANUAL_REVIEW · `< 75` CREATE_NEW_MASTER.
AI enrichment runs only in the 75–89 band.

Three properties of this table drive most of §2:

1. **Geo is a fixed constant within each band, not a variable.** Every candidate
   between 101 m and 1000 m scores exactly 85 for geography, whether it is 150 m
   or 950 m away. That 85% therefore contributes *zero discriminating power* —
   the decision is made entirely within the remaining 15 points (5 points at
   0 m), while the AUTO_MATCH threshold sits just 5 points above the band floor.
2. **Star and chain are dead weight in every reachable path.** They appear only
   in the > 1000 m fallback, which `ST_DWithin(…, 1000)` prevents from ever
   executing — and `matching_service.py:88-89` hardcodes both chain arguments to
   `None` regardless. `star_rating` and `chain_name` are collected and never
   used; 81.6% of supplier rows lack a star rating, including all of Hummingbird.
3. **The fallback branch cannot reach the match threshold.** `calculate_geo_score`
   always returns 10 in that branch (its 40 and 25 tiers correspond to distances
   the branch never sees), capping the total at `10+35+15+5+5 = 70` — below the
   75 MANUAL_REVIEW threshold. The one branch that weights names sensibly is
   structurally incapable of producing a match.

Verified by direct execution: at 0 m with unrelated names the score is **95.59 →
AUTO_MATCH**; at 100 m, **91.14 → AUTO_MATCH**.

## Appendix C — AI Model Details

- Model: `sentence-transformers/all-MiniLM-L6-v2`, 384 dimensions
- Pre-trained general-purpose English sentence encoder; **no fine-tuning on
  hotel data was performed**
- Input text: `"{hotel_name} {address} {city} {country}"`
- Storage: `vector(384)` with an IVFFlat cosine index, `lists = 100`
- Thresholds: ≥ 0.85 confirmed · 0.70–0.85 possible · < 0.70 no match
- Scope: re-ranks the ≤ 5 masters already shortlisted by the rule engine; it does
  not perform an independent search (§2.5)
