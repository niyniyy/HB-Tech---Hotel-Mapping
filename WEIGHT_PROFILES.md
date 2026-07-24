# Hotel Matcher — Weight Profiles

Reference for the rule-based matcher's field weights. Mix and match, or paste a
profile's values into `app/matching/matcher.py`.

---

## How weights are derived

A field's weight reflects its **discriminating power**, not how important it
feels. In record-linkage terms (Fellegi–Sunter), that is a function of two
probabilities:

- **m** = P(fields agree | it is the *same* hotel)
- **u** = P(fields agree | it is a *different* hotel)
- weight is proportional to **log(m / u)**

A field earns weight only when agreement is *informative*. Distance is the trap:
different hotels are routinely within 500 m–1 km of each other in airport and
CBD clusters, so its **u** is high and proximity-agreement is weak evidence on
its own. That is why geo is a **confirmer** here, not the primary identifier —
and why the hard distance-tier gate (`passes_distance_tier`) does most of
geography's real work regardless of the composite weight.

Discriminating power on this data, highest to lowest:

```
Name (core, city-stripped)  >>  Address  >  Geo (as score)  >  Star ~ Chain
```

---

## Quick-reference matrix

All weights sum to 100 so `AUTO_MATCH_MIN_SCORE` stays comparable across
profiles. If you change a profile so the weights no longer sum to 100, scale its
auto-match threshold by the same factor.

| Profile | Name | Address | Geo | Star | Chain | Auto thr | Manual thr | Geo decay (m) | Postal penalty |
|---------|------|---------|-----|------|-------|----------|------------|---------------|----------------|
| **balanced_default** | 45 | 20 | 25 | 5 | 5 | 40 | 30 | 1000 | 6 |
| **name_dominant** | 55 | 18 | 17 | 5 | 5 | 42 | 30 | 1500 | 6 |
| **precision_first** | 50 | 22 | 18 | 5 | 5 | 55 | 32 | 800 | 8 |
| **recall_first** | 40 | 18 | 30 | 7 | 5 | 35 | 25 | 1200 | 4 |
| **address_strong** | 45 | 28 | 15 | 7 | 5 | 45 | 30 | 700 | 7 |
| **geo_confirmer** | 50 | 12 | 28 | 5 | 5 | 42 | 30 | 900 | 6 |
| **chain_aware** | 42 | 18 | 22 | 6 | 12 | 42 | 30 | 1000 | 6 |

---

## Profiles in detail

### 1. balanced_default (current default)
```
Name 45 | Address 20 | Geo 25 | Star 5 | Chain 5    (auto-match ≥ 40)
```
Identity evidence leads (name + address = 65), geo confirms at 25, weak fields
sit at 5. The researched middle ground and a safe default.

**Use when:** general case, mixed supplier quality, no strong reason to skew.

### 2. name_dominant (identity-first)
```
Name 55 | Address 18 | Geo 17 | Star 5 | Chain 5    (auto-match ≥ 42)
```
For feeds where coordinates are mostly locality centroids and disagree by a
kilometre for the same hotel. Leans hard on the name; geography survives mainly
through the tier gate, not the composite score. Wider geo decay (1500 m)
tolerates bad coordinates.

**Use when:** supplier lat/long is known to be unreliable / centroid-heavy.

### 3. precision_first (minimize false merges)
```
Name 50 | Address 22 | Geo 18 | Star 5 | Chain 5    (auto-match ≥ 55)
```
Booking-path safety: a false merge sends a guest to the wrong property. Higher
threshold, strong identity fields, tight geo decay, harsher postal penalty.
Expect more `CREATE_NEW_MASTER` / `MANUAL_REVIEW` and fewer silent auto-matches.

**Use when:** mistakes are expensive and a human backstop exists.

### 4. recall_first (maximize matches)
```
Name 40 | Address 18 | Geo 30 | Star 7 | Chain 5    (auto-match ≥ 35)
```
Catches more true matches at the cost of more false ones. Lower threshold, more
geo credit, gentler postal penalty. Pair with heavy manual review.

**Use when:** coverage matters more than purity and reviewers absorb the risk.

### 5. address_strong (dense urban clusters)
```
Name 45 | Address 28 | Geo 15 | Star 7 | Chain 5    (auto-match ≥ 45)
```
When many distinct hotels share near-identical coordinates (airport strips, tech
parks), geo cannot separate them — so lean on address instead of distance.
Address is an independent identifier that clusters do not share.

**Use when:** data is concentrated in dense clusters and addresses are decent.

### 6. geo_confirmer (weak / dirty addresses)
```
Name 50 | Address 12 | Geo 28 | Star 5 | Chain 5    (auto-match ≥ 42)
```
Mirror image of `address_strong`: some suppliers give almost no usable address
text. Shift that weight onto name and geo instead.

**Use when:** address fields are sparse, truncated, or garbage.

### 7. chain_aware (chain field well populated)
```
Name 42 | Address 18 | Geo 22 | Star 6 | Chain 12   (auto-match ≥ 42)
```
Only worthwhile when `chain_name` has real coverage. A chain match combined with
a name match is strong; without coverage this weight is wasted.

**Use when:** most records carry a reliable chain/brand field.
**Do NOT use** if `chain_name` is mostly null (as in the tested data).

---

## AI Layer — Sentence Transformer Integration

### What the AI does

The rule engine compares **characters**. The AI compares **meaning**.

| Scenario | Rule engine sees | AI sees |
|----------|-----------------|---------|
| "Zone Connect by The Park" vs "Zone Connect Hotel" | 72% token match → FAIL | Same semantic meaning → 0.90 → MATCH |
| "Welcomhotel by ITC" vs "ITC Welcom Hotel" | Reordered + spacing → borderline | Same concept → 0.92 → MATCH |
| "Radisson Blu" vs "Radisson Collection" | 70% match → borderline | Different sub-brands → 0.72 → UNCERTAIN |

### Model used

- **Model:** `sentence-transformers/all-MiniLM-L6-v2`
- **Type:** Pre-trained BERT-based sentence encoder (not custom-trained)
- **Dimensions:** 384 floats per vector
- **Storage:** pgvector column with IVFFlat cosine index
- **Memory:** ~500MB per worker process

### Where it should sit in the pipeline

The AI adds value in **two** places — not just one:

```
Place 1: BORDERLINE VALIDATION (after rule scoring, before final decision)
─────────────────────────────────────────────────────────────────────────────
Rule engine found a candidate but isn't confident enough to auto-match.
AI confirms or denies the match.

Trigger:   MANUAL_REVIEW_MIN_SCORE <= rule_score < AUTO_MATCH_MIN_SCORE
Action:    Generate supplier embedding → compare against candidate master embeddings
Decision:
  - AI similarity >= 0.85 AND same master → promote to AUTO_MATCH
  - AI similarity 0.70-0.85 → keep as MANUAL_REVIEW
  - AI similarity < 0.70 → demote to CREATE_NEW_MASTER


Place 2: PRE-CREATION SAFETY NET (before creating a new master) ← THE MISSING PIECE
─────────────────────────────────────────────────────────────────────────────
Rule engine found no candidates (or all scored too low). About to create
a duplicate master. AI checks if an existing master means the same thing.

Trigger:   Decision is CREATE_NEW_MASTER
Action:    Generate supplier embedding → search ALL master embeddings in same city
Decision:
  - AI similarity >= 0.85 → escalate to MANUAL_REVIEW (don't create duplicate)
  - AI similarity < 0.85 → proceed with CREATE_NEW_MASTER (genuinely new)
```

### Current status (as of this audit)

The AI is **dead code** in the current pipeline:
- `enrich_candidate()` still checks `75 <= rule_score < 90` — a range the
  new matcher cannot produce for MANUAL_REVIEW candidates
- 0 of 601 manual reviews have ai_similarity populated
- 2,256 master embeddings generated but never queried
- 0 supplier embeddings ever generated

### How to fix

1. In `ai_integration_service.py`, replace:
   ```python
   elif 75 <= rule_score < 90:
   ```
   with:
   ```python
   elif MANUAL_REVIEW_MIN_SCORE <= rule_score < AUTO_MATCH_MIN_SCORE:
   ```

2. Add a pre-creation check in `queue_processing_service.py`:
   ```python
   if decision == "CREATE_NEW_MASTER":
       # Before creating, check if an existing master is semantically equivalent
       ai_matches = await self.ai_service.find_similar_masters_in_city(
           hotel_name, address, city, country
       )
       if ai_matches and ai_matches[0]["similarity"] >= 0.85:
           decision = "MANUAL_REVIEW"  # Don't create duplicate — human decides
   ```

3. Remove the early return in `enrich_candidate` for `exact_name_class` cases
   if you want AI to weigh in on those too.

### Expected impact

| Metric | Without AI | With AI (both places) |
|--------|-----------|----------------------|
| Auto-match precision | ~99% | ~99% (unchanged) |
| Fragmented masters | ~75 excess (3.3%) | ~40-50 excess (1.5-2%) |
| Manual review queue | 601 | ~650-700 (slightly more) |
| New false merges | 0 | 0 (AI only escalates to review, never auto-merges) |

### AI similarity thresholds

| Similarity | Decision | Meaning |
|-----------|----------|---------|
| >= 0.85 | Confirm match / escalate to review | Model is confident these are the same hotel |
| 0.70 - 0.85 | Uncertain — human decides | Genuinely ambiguous |
| < 0.70 | Reject / allow new master | Model says these are different hotels |

These thresholds are based on the model's calibration for English text.
For non-English hotel names, lower the confirm threshold to 0.80.

---

## Notes

- These sum-to-100 distributions are **starting points, not measured optima**.
  The truly correct values come from fitting against labeled match/no-match
  pairs (Fellegi–Sunter with EM, or logistic regression where the learned
  coefficients *are* the weights).
- Whichever profile you pick, `passes_distance_tier()` still does the heavy
  geographic lifting — so the geo **weight** matters less than the geo **gate**.
- The machine-readable version of this table lives in
  `app/matching/weight_profiles.py` (importable dicts + a validator).

---

## Needed: Pipeline Reset Feature

### Why

After changing weight profiles, matcher logic, or normalization rules, you need
to re-run the full pipeline on the same data to see the effect. Currently there
is no way to clear mapping output without also deleting the imported supplier
data.

### What it should do

A "Reset Pipeline" action that:

1. **Deletes** all mapping output:
   - `hotel_mappings` (all AUTO + NEW_MASTER mappings)
   - `master_hotels` (all generated masters)
   - `manual_review_candidates`
   - `hotel_embeddings`
   - `flagged_records`

2. **Preserves** all imported supplier data:
   - `supplier_hotels` (untouched)

3. **Resets** the queue:
   - `hotel_mapping_queue` → all statuses back to `'Pending'`, retry_count = 0

4. **Resets** ID sequences:
   - `master_hotels_master_hotel_id_seq` → restart from 1
   - `hotel_mappings_id_seq` → restart from 1

5. **Returns:** count of hotels now pending for reprocessing.

### Where to add it

| Component | Change needed |
|-----------|---------------|
| **API endpoint** | `POST /api/v1/mapping/reset` in `app/api/mapping.py` |
| **Frontend button** | "Reset Pipeline" button (red/danger style) on the Run Pipeline page (`app/static/app.js` → `render_pipeline`) |
| **Confirmation dialog** | Must require user confirmation before executing (destructive action) |
| **Response** | `{ "message": "...", "pending_for_reprocessing": <count> }` |

### API implementation outline

```python
@router.post("/mapping/reset")
async def reset_mapping_pipeline(session: AsyncSession = Depends(get_db)):
    from sqlalchemy import text

    await session.execute(text("DELETE FROM hotel_mappings"))
    await session.execute(text("DELETE FROM manual_review_candidates"))
    await session.execute(text("DELETE FROM hotel_embeddings"))
    await session.execute(text("DELETE FROM flagged_records"))
    await session.execute(text("DELETE FROM master_hotels"))
    await session.execute(text(
        "UPDATE hotel_mapping_queue SET status = 'Pending', retry_count = 0"
    ))
    await session.execute(text(
        "ALTER SEQUENCE master_hotels_master_hotel_id_seq RESTART WITH 1"
    ))
    await session.execute(text(
        "ALTER SEQUENCE hotel_mappings_id_seq RESTART WITH 1"
    ))
    await session.commit()

    result = await session.execute(text(
        "SELECT COUNT(*) FROM hotel_mapping_queue WHERE status = 'Pending'"
    ))
    pending = result.scalar()

    return {
        "message": "Pipeline reset complete. All mappings cleared, supplier data retained.",
        "pending_for_reprocessing": pending,
    }
```

### Frontend implementation outline

```javascript
// In render_pipeline toolbar, add:
<button class="btn-danger btn-sm" onclick="App.resetPipeline()">Reset Pipeline</button>

// Handler:
async resetPipeline() {
    if (!confirm(
        'Reset the entire pipeline?\n\n'
        + 'This will DELETE all mappings, masters, embeddings, and review candidates.\n'
        + 'Supplier records stay intact and are re-queued as Pending.\n\n'
        + 'Use this after changing matcher logic or weight profiles.'
    )) return;

    const r = await post(`${API}/mapping/reset`, {});
    toast(`Reset complete — ${fmt(r.pending_for_reprocessing)} hotels ready to re-process`);
    this.refresh();
}
```

### Workflow

```
1. Import data (once)
2. Run pipeline → inspect results
3. Tweak weights / matcher logic / normalizer
4. Hit "Reset Pipeline" → clears all output, re-queues everything
5. Run pipeline again → compare new results
6. Repeat 3-5 until satisfied
```
