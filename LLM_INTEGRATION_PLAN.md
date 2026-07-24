# Hotel Mapping Engine — Improvement Plan

## Objective

Improve the hotel mapping pipeline through multiple strategies:

1. **Increase mapping accuracy** — resolve borderline cases the embedding-only
   layer cannot reason about (brand distinctions, transliterations, rebranding).
2. **Reduce master hotel duplication** — catch duplicate masters the rule engine
   and cosine similarity miss, especially when coordinates disagree by >1 km.
3. **Improve data quality** — validate and enrich incoming data before it enters
   the matching pipeline.
4. **Reduce manual review burden** — make reviewers faster and more effective,
   auto-resolve clear-cut cases.
5. **Build a self-improving system** — use reviewer decisions as training data
   to continuously tighten thresholds and retrain models.

---

## Current Pipeline (as-is)

```
Supplier Hotel
    │
    ▼
┌─────────────────────────┐
│ Record Completeness     │  ← discard if missing coords/name/city/country
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│ Geo Candidate Search    │  ← find masters within 1 km (+ exact-name up to 5 km)
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│ Rule-Based Scorer       │  ← name (45), address (20), geo (25), building (5),
│ (matcher.py)            │     rarity (5) = 100 max.  Tier gate + composite.
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────┐
│ AI Enrichment (embeddings only)                     │
│ (ai_integration_service.py)                         │
│                                                     │
│  • Runs when rule_decision == MANUAL_REVIEW         │
│  • Generates embedding (all-MiniLM-L6-v2)          │
│  • Cosine similarity against top-5 candidate masters│
│  • >= 0.85 → AUTO_MATCH, < 0.70 → CREATE_MASTER    │
│  • 0.70–0.85 → MANUAL_REVIEW                       │
└────────────┬────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────┐
│ Pre-Creation Safety Net                             │
│ (check_semantic_duplicate_before_creating)          │
│                                                     │
│  • Runs when decision == CREATE_NEW_MASTER          │
│  • Address-duplicate check (postcode + building)    │
│  • Semantic-duplicate check (embedding, 5 km radius)│
│  • Escalates to MANUAL_REVIEW if duplicate found    │
└────────────┬────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────┐
│ Apply Decision          │
│  AUTO_MATCH             │
│  MANUAL_REVIEW          │
│  CREATE_NEW_MASTER      │
│  FLAGGED                │
└─────────────────────────┘
```

### Limitations of current AI layer

| Problem | Why embeddings alone fail |
|---------|--------------------------|
| "The Park Chennai" vs "Park Hotel Chennai" | Similar embeddings, different chains |
| "Lemon Tree Premier" appearing in 4 cities | Core name matches, embeddings can't distinguish branches |
| "Clarks Inn Suites" vs "DS Clarks Inn" (rebrand) | Different strings, same property |
| Coordinates off by 2+ km | Outside 1 km search radius; semantic search only goes 5 km |
| "Hotel Mamallaa Heritage" vs "Mamallaa Heritage Hotel" | High similarity by accident of word order |

An LLM reasons about hotel identity using context (city, brand hierarchies,
address structure) rather than just computing distance between vectors.

---

## Proposed Pipeline (to-be)

```
Supplier Hotel
    │
    ▼
┌─────────────────────────┐
│ Record Completeness     │  (unchanged)
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│ Geo + Name Candidate    │  (unchanged)
│ Search                  │
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│ Rule-Based Scorer       │  (unchanged)
└────────────┬────────────┘
             │
             ▼
┌──────────────────────────────────────────────────────────┐
│ AI Enrichment (embedding + LLM verification) ★ CHANGED   │
│                                                          │
│  STEP 1: Embedding similarity (existing, fast, cheap)    │
│  STEP 2: LLM verification (NEW — only when uncertain)    │
│                                                          │
│  Triggers:                                               │
│  • Borderline band (30 ≤ score < 40)                     │
│  • Embedding similarity in uncertain zone (0.70–0.85)    │
│                                                          │
│  LLM receives structured hotel pair and reasons about    │
│  whether they are the same physical property.            │
└────────────┬─────────────────────────────────────────────┘
             │
             ▼
┌──────────────────────────────────────────────────────────┐
│ Pre-Creation Safety Net (with LLM) ★ CHANGED             │
│                                                          │
│  • Address-duplicate check (unchanged)                   │
│  • Semantic-duplicate check (unchanged)                  │
│  • LLM duplicate verification (NEW)                      │
│    — when embedding finds a near-duplicate (>= 0.85),    │
│      ask LLM to confirm before escalating to review      │
└────────────┬─────────────────────────────────────────────┘
             │
             ▼
┌──────────────────────────────────────────────────────────┐
│ Batch Deduplication Job (NEW)                            │
│                                                          │
│  Periodic scan of existing masters:                      │
│  • Provisional masters with suspicious neighbours        │
│  • Active masters sharing core_name within 5 km          │
│  • LLM evaluates each pair → auto-merge or escalate     │
└──────────────────────────────────────────────────────────┘
```

---

## Implementation Plan

### Phase 1: LLM Service Foundation

**Goal:** Build a reusable LLM client that the rest of the system can call.

#### 1.1 Configuration (`config.py`)

Add LLM provider settings:

```python
# ── LLM integration ────────────────────────────────────────────────────
LLM_PROVIDER: str = "openai"          # "openai", "anthropic", "bedrock"
LLM_MODEL: str = "gpt-4o-mini"        # cheap, fast, structured output
LLM_API_KEY: str = ""
LLM_BASE_URL: str = ""                # for self-hosted / proxy endpoints
LLM_TEMPERATURE: float = 0.0          # deterministic for matching
LLM_MAX_TOKENS: int = 500
LLM_ENABLED: bool = False             # feature flag — disabled by default
LLM_MAX_RETRIES: int = 2
LLM_TIMEOUT_SECONDS: int = 30
LLM_BATCH_CONCURRENCY: int = 5        # max parallel LLM calls in batch jobs
```

#### 1.2 New file: `app/matching/llm_service.py`

Responsibilities:
- Abstract the LLM provider (OpenAI, Anthropic, Bedrock)
- Structured prompt construction for hotel pairs
- Response parsing with validation
- Rate limiting and retry logic
- Cost tracking (token counts per call)
- Feature flag check (no-op when disabled)

Key methods:
```python
class LLMService:
    async def verify_hotel_match(
        self,
        supplier_hotel: dict,
        master_hotel: dict,
    ) -> LLMVerdict:
        """
        Ask the LLM: are these two records the same physical property?
        Returns: LLMVerdict(same_hotel: bool, confidence: float, reasoning: str)
        """

    async def verify_hotel_match_batch(
        self,
        pairs: list[tuple[dict, dict]],
    ) -> list[LLMVerdict]:
        """Batch verification with concurrency control."""

    async def classify_duplicate_masters(
        self,
        master_a: dict,
        master_b: dict,
    ) -> LLMVerdict:
        """Are these two master hotel records the same physical property?"""
```

#### 1.3 Prompt design

The prompt provides:
- Both hotel records as structured fields (name, address, city, postal code,
  coordinates, star rating)
- Distance in meters between the two
- Domain knowledge injected as system context:
  - Hotels rebrand (ITC → Mementos by ITC)
  - City names differ across suppliers (Bangalore/Bengaluru)
  - Coordinates from different geocoders can be 1–3 km apart
  - Chain branches exist in the same city (Lemon Tree Premier has 3 in Delhi)
  - Building/plot numbers are strong identity signals
  - Same postcode + same building = strong evidence of same property

Response format: JSON with `same_hotel`, `confidence`, `reasoning`.

#### 1.4 New dependency

Add to `requirements.txt`:
```
openai==1.35.0        # or anthropic / boto3 depending on provider
```

---

### Phase 2: LLM Verification in Borderline Matching

**Goal:** Replace blind embedding-based decisions in the uncertain band with
LLM reasoning.

#### 2.1 Modify `app/matching/ai_integration_service.py`

**Where:** Inside `enrich_candidate`, when embedding similarity is between
0.70 and 0.85 (the band that currently goes to MANUAL_REVIEW).

**Logic change:**
```
Before (current):
  0.70–0.85 similarity → always MANUAL_REVIEW

After (with LLM):
  0.70–0.85 similarity →
    if LLM_ENABLED:
      call llm_service.verify_hotel_match(supplier, master)
      if LLM says same_hotel with confidence >= 0.90 → AUTO_MATCH
      if LLM says different_hotel with confidence >= 0.90 → CREATE_NEW_MASTER
      else → MANUAL_REVIEW (genuinely ambiguous)
    else:
      MANUAL_REVIEW (current behavior preserved)
```

**Safety guardrail:** The LLM can only promote to AUTO_MATCH when the rule
engine already placed the candidate in the borderline band (score 30–40) AND
embedding similarity is >= 0.70. It cannot override a rule-engine rejection.

**Fallback:** If the LLM call fails (timeout, API error), fall through to
MANUAL_REVIEW. The system degrades to current behavior, never to a wrong merge.

#### 2.2 Fetch master hotel details for prompt

The current `enrich_candidate` receives a `candidate` dict that has the supplier
fields but only `master_hotel_id` for the master side. Add a helper to load the
master's full record (name, address, city, postal code, coordinates) so the LLM
prompt is complete.

---

### Phase 3: LLM in Pre-Creation Duplicate Detection

**Goal:** Before creating a new master, use the LLM to verify whether the
semantic-duplicate candidate is truly the same property.

#### 3.1 Modify `check_semantic_duplicate_before_creating` in
`app/services/queue_processing_service.py`

**Where:** After the existing `semantic_duplicate_for` call returns a candidate
with similarity >= 0.85.

**Logic change:**
```
Before (current):
  embedding similarity >= 0.85 → escalate to MANUAL_REVIEW

After (with LLM):
  embedding similarity >= 0.85 →
    if LLM_ENABLED:
      call llm_service.classify_duplicate_masters(supplier, existing_master)
      if LLM says same_hotel with confidence >= 0.90 → AUTO_MATCH to existing
      if LLM says different_hotel with confidence >= 0.85 → allow CREATE_NEW_MASTER
      else → MANUAL_REVIEW
    else:
      MANUAL_REVIEW (current behavior preserved)
```

**Impact:** Reduces the provisional master queue by auto-resolving cases where
the embedding AND the LLM agree. Only genuinely ambiguous cases reach a human.

---

### Phase 4: Batch Deduplication Job

**Goal:** Periodically scan existing masters for duplicates the real-time
pipeline missed.

#### 4.1 New file: `app/jobs/deduplication_job.py`

**Triggers:**
- Run after every full queue drain (end of `process_pending_batch`)
- Also available as a standalone API endpoint / Celery task

**Algorithm:**
1. Query pairs of masters that share a `core_name` or `strict_name` within 5 km
   (reuse the `ProvisionalMasterService.queue(suspicious_only=True)` logic)
2. For each pair, compute embedding similarity
3. If similarity >= 0.70, call `llm_service.classify_duplicate_masters`
4. If LLM says same_hotel with high confidence:
   - If both are Provisional → auto-merge (younger into older)
   - If one is Active → escalate to human (higher stakes)
5. Log all decisions for audit

**Concurrency:** Process pairs with `LLM_BATCH_CONCURRENCY` parallel calls
to stay within rate limits.

#### 4.2 New API endpoint: `POST /api/admin/run-deduplication`

Allows manual trigger from the review console.

#### 4.3 New table: `llm_dedup_decisions`

Audit trail for every LLM-based deduplication decision:
```sql
CREATE TABLE llm_dedup_decisions (
    id SERIAL PRIMARY KEY,
    master_a_id INTEGER NOT NULL,
    master_b_id INTEGER NOT NULL,
    llm_verdict BOOLEAN,         -- same_hotel?
    llm_confidence FLOAT,
    llm_reasoning TEXT,
    action_taken TEXT,           -- 'merged', 'escalated', 'no_action'
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

### Phase 5: Exact-Name Escalation Enhancement

**Goal:** Reduce false-positive manual reviews for the exact-name-but-far-apart
case.

#### 5.1 Modify `enrich_candidate` — the `exact_name_class is not None` branch

**Currently:** Always MANUAL_REVIEW, no further analysis.

**With LLM:**
```
if LLM_ENABLED and distance_meters > 2000:
  Ask LLM: "Two hotels share the exact name X and are Y km apart.
            One is at address A, the other at address B.
            Is this one hotel with a wrong coordinate, or two branches?"
  if LLM says different_hotel with confidence >= 0.90 → CREATE_NEW_MASTER
  else → MANUAL_REVIEW (preserve safety for uncertain cases)
```

**Rationale:** Chains like Lemon Tree Premier have 3 locations in Delhi, all
with the same core_name, 2–8 km apart. These are currently all queued for human
review. The LLM can resolve the obvious ones (different cities, different
addresses) and only escalate the truly ambiguous.

---

### Phase 6: Provisional Master Auto-Resolution

**Goal:** Drain the provisional master queue faster by LLM-assisted confirmation
or merge.

#### 6.1 Modify `app/services/provisional_master_service.py`

Add method:
```python
async def auto_resolve_suspicious(self, limit: int = 50) -> dict:
    """
    For each provisional master with suspicion > 0:
    1. Get its top suggestion (nearest name-alike)
    2. Ask LLM if they are the same property
    3. Auto-merge if LLM is confident, else leave for human
    """
```

#### 6.2 Integrate with queue drain

Call at the end of `process_pending_batch`, after `replay_reviewer_decisions`,
gated by `LLM_ENABLED`.

---

## File Changes Summary

| File | Change Type | Description |
|------|-------------|-------------|
| `config.py` | Modify | Add LLM settings |
| `requirements.txt` | Modify | Add `openai` (or provider SDK) |
| `.env.example` | Modify | Document new env vars |
| `app/matching/llm_service.py` | **New** | LLM client, prompt builder, response parser |
| `app/matching/ai_integration_service.py` | Modify | Add LLM verification in uncertain band + exact-name |
| `app/services/queue_processing_service.py` | Modify | LLM in pre-creation check, invoke batch dedup |
| `app/services/provisional_master_service.py` | Modify | Add `auto_resolve_suspicious` |
| `app/jobs/deduplication_job.py` | **New** | Batch dedup job using LLM |
| `app/api/data_routes.py` | Modify | Add `/admin/run-deduplication` endpoint |
| `scripts/migration_llm_audit.sql` | **New** | Create `llm_dedup_decisions` table |

---

## Safety Principles

1. **LLM never merges alone.** The LLM can promote a candidate to AUTO_MATCH
   only when the rule engine already placed it in the borderline band. It cannot
   override a rule-engine rejection or bypass the tier gate.

2. **Feature flag.** All LLM paths are gated by `LLM_ENABLED`. When disabled,
   behavior is identical to today.

3. **Graceful degradation.** If the LLM call fails (timeout, rate limit, API
   error), the system falls through to the existing decision (MANUAL_REVIEW or
   CREATE_NEW_MASTER). A broken LLM never produces a wrong merge.

4. **Audit trail.** Every LLM decision is logged with the full prompt, response,
   confidence, and reasoning. Reviewable in the admin console.

5. **Conservative thresholds.** LLM confidence must be >= 0.90 to act. Anything
   below that stays MANUAL_REVIEW. Better to send one extra case to a human than
   to merge two different hotels.

6. **No LLM for high-confidence cases.** When the rule engine already says
   AUTO_MATCH (score >= 40 + tier gate passed), no LLM call is made. It would
   add latency and cost for no benefit.

7. **Cost control.** LLM is called only for the ~10% of records that are
   borderline. At 8,000 supplier records and ~800 borderline cases, that's
   ~800 LLM calls per run. At gpt-4o-mini pricing (~$0.15/1M input tokens),
   a full run costs well under $1.

---

## Expected Impact

| Metric | Current | Expected with LLM |
|--------|---------|-------------------|
| Manual review queue size | ~540 per run (10.4%) | ~150 per run (~3%) |
| Provisional masters with suspicious neighbours | growing | auto-resolved within 1 run |
| False merges in published set | 0 (gate holds TIER3/4) | 0 (maintained) |
| Duplicate masters created per run | ~5-15 (estimated from comments) | ~1-3 |
| Time to drain provisional queue | weeks (human-only) | hours (LLM + human for edge cases) |

---

## Implementation Order

```
Phase 1 (Foundation)      → 1–2 days
Phase 2 (Borderline)      → 1 day
Phase 3 (Pre-creation)    → 1 day
Phase 4 (Batch dedup)     → 2 days
Phase 5 (Exact-name)      → 0.5 day
Phase 6 (Provisional)     → 1 day
Testing + tuning          → 2–3 days
─────────────────────────────────────
Total                     → ~9–11 days
```

---

## Decision: Which LLM Provider?

| Provider | Model | Cost (1M tokens) | Latency | Structured Output |
|----------|-------|-------------------|---------|-------------------|
| OpenAI | gpt-4o-mini | ~$0.15 in / $0.60 out | ~500ms | Yes (JSON mode) |
| Anthropic | claude-3.5-haiku | ~$0.25 in / $1.25 out | ~600ms | Yes |
| AWS Bedrock | various | varies | ~800ms | Depends on model |

**Recommendation:** Start with OpenAI `gpt-4o-mini` — cheapest, fastest, has
native JSON mode for reliable structured output. The abstraction layer makes
switching providers a config change, not a code change.

---

## Next Steps

1. Confirm LLM provider choice and obtain API key
2. Implement Phase 1 (LLM service + config)
3. Test with a sample of known borderline pairs from the manual review queue
4. Measure accuracy improvement before enabling in production
5. Roll out incrementally: Phase 2 → 3 → 4 → 5 → 6

Ready to start implementation on your go.


---
---

# Part 2: Beyond LLM — Full Improvement Roadmap

The LLM integration above (Phases 1–6) is one piece. Below is the complete set
of improvements that can be made across the entire pipeline.

---

## A. Smarter Matching & Scoring

### A1. Fine-Tuned Embedding Model

**Problem:** `all-MiniLM-L6-v2` is a general-purpose sentence embedder. It
treats "The Park Chennai" and "Park Hotel Chennai" as very similar because the
words overlap, but they are different chains.

**Solution:** Fine-tune the embedding model on hotel-pair data. Your
`review_attach_decision` table already contains reviewer-labelled positive and
negative pairs — that is a perfect training set.

**Alternative:** Use a cross-encoder (re-ranker) like
`cross-encoder/ms-marco-MiniLM-L-6-v2` on the top-K candidates returned by the
bi-encoder. Cross-encoders are much more accurate because they see both texts
together rather than comparing independent vectors.

**Integration point:** `app/matching/ai_similarity_service.py` — add a reranking
step after `find_ai_matches` returns candidates.

**Effort:** Medium (2–3 days for cross-encoder, 1 week for fine-tuning pipeline)
**Impact:** High on accuracy, medium on duplication.

---

### A2. Learned Scoring Model (Replace Linear Weights)

**Problem:** The rule engine adds signals linearly:
`name(45) + address(20) + geo(25) + building(5) + rarity(5) = 100`. But
interactions matter: "rare name + close = certain match" is stronger than the
sum of its parts, while "common name + close = need more evidence."

**Solution:** Train a gradient-boosted tree (XGBoost/LightGBM) or logistic
regression on the features the rule engine already computes:
- `name_similarity_loose`
- `name_similarity_strict`
- `address_score`
- `geo_score`
- `building_score`
- `name_rarity_score`
- `distance_meters`
- `postal_conflict`
- `min_core_tokens`

Your training data: 11,395 positive pairs (records the pipeline put under one
master) + 36,028 hard negatives (different masters within 1 km). This is
excellent for a binary classifier.

**Integration point:** `app/matching/matcher.py` — replace
`calculate_rule_based_score` with a model prediction, or use the model as a
second opinion alongside the linear score.

**Effort:** Medium (3–5 days including evaluation)
**Impact:** High on accuracy, medium on duplication.

---

### A3. Phonetic / Transliteration Matching

**Problem:** Indian hotel names get transliterated inconsistently across
suppliers:
- Mamallaa / Mamallapuram / Mahabalipuram
- Kozhikode / Calicut
- Varanasi / Benaras / Kashi
- Thiruvananthapuram / Trivandrum

The current string similarity cannot recognize these as equivalent.

**Solution:** Add a phonetic matching layer:
- Apply Metaphone or Soundex to name tokens
- Use a transliteration library (e.g., `indic-transliteration` or
  `ai4bharat-transliteration`) to normalize Hindi/Tamil romanizations
- Add phonetic agreement as an additional signal to the scorer

**Integration point:** `app/normalization/normalizer.py` — new function
`phonetic_hotel_name()` that returns a phonetic representation. Used as an
additional blocking key in candidate search and as a scoring signal.

**Effort:** Low (1–2 days)
**Impact:** Medium on accuracy, low on duplication.

---

### A4. Dynamic Candidate Search Radius

**Problem:** The flat 1 km search radius works for most cities, but metro areas
(Mumbai, Delhi, Bangalore) have higher coordinate scatter from geocoders. A
hotel in a congested urban area might have coordinates off by 1.5–2 km between
suppliers, putting it outside the search window.

**Solution:** Scale the search radius by city density or geocoding confidence:
- Tier 1 cities (Mumbai, Delhi, Bangalore, Chennai): 2 km radius
- Tier 2 cities (Pune, Hyderabad, Kolkata): 1.5 km radius
- Smaller towns: 1 km (current)
- Alternatively: use the spread of existing coordinates for that city's masters
  as a dynamic indicator of geocoding reliability.

**Integration point:** `app/services/matching_service.py` —
`find_candidate_master_hotels` query's `ST_DWithin` radius becomes a function
of the city.

**Effort:** Low (0.5–1 day)
**Impact:** Low on accuracy, medium on duplication.

---

### A5. Chain / Brand Knowledge Base

**Problem:** Chains operate multiple brands at the same address, and the current
`BRAND_TIER_TOKENS` set in the normalizer catches only a handful. Meanwhile,
rebrands look like different hotels when they are the same property.

**Solution:** Maintain a structured knowledge base of hotel chains:

```
ITC Hotels:
  - ITC Grand, Mementos by ITC, WelcomHotel, Fortune, Fortune Select, Fortune Park
  
Lemon Tree Hotels:
  - Lemon Tree Premier, Lemon Tree, Red Fox, Keys Prima, Keys Select, Keys Lite

Radisson Hotel Group:
  - Radisson Blu, Radisson, Radisson RED, Park Inn by Radisson, Country Inn

Accor:
  - Sofitel, Pullman, Novotel, ibis, ibis Styles, ibis budget

Marriott:
  - JW Marriott, Marriott, Sheraton, Westin, Le Méridien, Courtyard, Fairfield
```

**Usage in scoring:**
- Two names from DIFFERENT brands of the SAME parent at the same location →
  likely different hotels (Red Fox vs Lemon Tree Premier — co-located but
  different properties)
- A name that changed from one brand to another within the same group → likely a
  rebrand of the same property (score boost)
- Two names from completely unrelated chains → strong negative signal regardless
  of string similarity

**Integration point:**
- New file: `app/matching/chain_knowledge.py`
- Used in: `app/matching/matcher.py` (scoring) and
  `app/normalization/normalizer.py` (normalization)

**Effort:** Low (1–2 days)
**Impact:** Medium on accuracy, high on duplication prevention.

---

## B. Data Quality & Enrichment

### B1. Geocoding Validation / Correction

**Problem:** The single biggest source of duplicate masters is bad coordinates.
When two records for the same hotel have coordinates >1 km apart, they are never
compared and silently become two masters. Your codebase comments mention this
repeatedly (Mementos at 1608m, Zone by The Park at 3177m).

**Solution:** Run suspect coordinates through a geocoding service:
- At import time, geocode `hotel_name + address + city + postal_code`
- If the geocoded result is >2 km from the supplied coordinate, flag the record
- Optionally, use the geocoded coordinate for matching (with a confidence
  downgrade)
- Providers: Google Maps Geocoding API, MapMyIndia (for India), Nominatim (free)

**Cost estimate:** Google Maps Geocoding: $5 per 1,000 requests. For 8,000
hotels/run, that's ~$40/run. MapMyIndia is cheaper for India-specific data.

**Integration point:** `app/services/import_service.py` — add a geocoding
validation step in `_clean_row` or as a post-import batch job.

**Effort:** Medium (2–3 days)
**Impact:** Medium on accuracy, HIGH on duplication (attacks root cause).

---

### B2. Address Parsing & Structuring

**Problem:** Indian addresses are unstructured text with high variability:
- "Plot 47, Sector 10, Gurugram, Haryana"
- "47, Sec-10, Gurgaon"
- "No. 47 Sector Ten Gurugram HR"

The current `calculate_address_score` uses fuzzy matching, which struggles with
these variations.

**Solution:** Parse addresses into structured components:
- Building/plot number (you already extract this)
- Street/sector
- Locality/area
- City
- State
- Postal code

Use rule-based parsing for common Indian patterns (Sector/Phase/Block) or an LLM
for harder cases. Compare structured components independently: exact match on
building number + fuzzy on locality is much more reliable than fuzzy on the
whole string.

**Integration point:** `app/matching/matcher.py` —
`calculate_address_score` and `building_number` functions.

**Effort:** Medium (3–4 days)
**Impact:** Medium on accuracy, medium on duplication.

---

### B3. Supplier Quality Scoring

**Problem:** Not all suppliers are equal. Some have GPS coordinates from
professional surveys; others have geocoded city centroids. Some have
hand-verified hotel names; others have automated scrapes with typos. Treating
all data equally means bad data from one supplier corrupts the master.

**Solution:** Track per-supplier quality metrics:
- % of records with valid coordinates (not city centroid)
- Average coordinate deviation from consensus (where multiple suppliers map the
  same hotel)
- % of records that end up in manual review
- % of records flagged for data quality
- Name consistency (do they use full names or abbreviations?)

Use these scores to:
- Weight supplier data in master hotel consolidation (trusted supplier's
  coordinates win)
- Prioritize quality improvements with specific suppliers
- Surface in the review console

**Integration point:** New file: `app/services/supplier_quality_service.py`.
Surface in the dashboard under the "Supplier Quality" page (already in nav).

**Effort:** Medium (2–3 days)
**Impact:** Medium across the board — improves everything indirectly.

---

### B4. External Data Enrichment

**Problem:** The system only knows what suppliers tell it. If a supplier says a
hotel exists at coordinates X with name Y, the system trusts that or flags it —
but never validates against ground truth.

**Solution:** Cross-reference against external sources:
- Google Places API — search for hotel near coordinates, confirm name and
  existence
- TripAdvisor / MakeMyTrip — if the hotel has a listing, it exists
- OTA mapping databases (if accessible)
- Government tourism board listings

Match a supplier record to an external ID → validated existence + validated
location. Could be done as a batch enrichment step after import.

**Integration point:** New service: `app/services/external_enrichment_service.py`
Run as a post-import batch job. Store external IDs in a new column on
`supplier_hotels` or a separate linking table.

**Effort:** High (5–7 days including API integration)
**Impact:** High on accuracy, high on duplication.

---

### B5. Duplicate Detection at Import Time

**Problem:** The same hotel can appear multiple times in a single supplier file
(a duplicate row), and the current `supplier_id_collision` logic runs during
matching, not during import. By then, both rows are in the queue competing for
masters.

**Solution:** At import time, before enqueueing:
- Same supplier + same hotel_id + same name + coordinates within 100m → duplicate
  row, keep one, discard the rest
- Same supplier + same hotel_id + DIFFERENT name/location → collision, flag for
  supplier correction (already handled, but surface it better)
- Same supplier + different hotel_id + same name + same city + coordinates within
  200m → possible duplicate listing, flag

**Integration point:** `app/services/import_service.py` — add dedup logic after
batch insert, before enqueueing.

**Effort:** Low (1–2 days)
**Impact:** Low on accuracy, medium on reducing queue noise.

---

## C. Review Console Improvements

### C1. AI-Assisted Review Explanations

**Problem:** When a reviewer opens a case, they see two hotel records and some
scores. They must mentally reconstruct why the system is uncertain and what
evidence to look for.

**Solution:** Use the LLM to generate a one-line explanation for each review
case:
- "These are likely the same hotel — both at Plot 47, Sector 10, Gurgaon.
  The name difference (Clarks Inn → DS Clarks Inn) suggests a rebrand."
- "Probably different hotels — same chain name (Lemon Tree Premier) but
  addresses are in different sectors and 3.2 km apart."

Show this in the review UI as a "AI Assessment" field. The reviewer still
decides, but they have a head start.

**Integration point:** `app/services/manual_review_service.py` — generate
explanation when a review candidate is created (or lazily on first view).
Surface in `app/static/app.js` review page.

**Effort:** Low (1 day — reuses the LLM service from Phase 1)
**Impact:** High on reviewer throughput.

---

### C2. Confidence-Ranked Review Queue

**Problem:** The current queue is ordered by `created_at DESC`. A near-certain
yes (exact name, 50m apart, just need a click) sits next to a genuinely hard
case (different name, 1.5 km apart, needs investigation). Reviewers context-switch
constantly.

**Solution:** Rank the queue by:
1. **Easy approvals first** — exact name + close distance + high AI similarity.
   These take 2 seconds each and clear volume.
2. **High-value decisions next** — cases where the LLM is genuinely uncertain,
   so a human decision is maximally informative.
3. **Likely rejects last** — cases the system is fairly sure are wrong but
   couldn't quite auto-reject.

**Integration point:** `app/services/manual_review_service.py` —
`get_manual_reviews` query gets an `ORDER BY` based on a computed priority
score rather than `created_at DESC`.

**Effort:** Low (0.5–1 day)
**Impact:** High on reviewer throughput.

---

### C3. Cluster-Based Bulk Review

**Problem:** 583 of 1,295 queued items are the same pattern — an exact name at a
distance. A reviewer who understands the pattern ("these are all the Lemon Tree
Premier cases") should decide the whole cluster at once, not click through 583
individual items.

**Solution:** Group review items by:
- Same `review_type` + same chain name
- Same supplier + same city (supplier has systematic data issue)
- Same master hotel (multiple suppliers disagreeing about one property)

Present clusters as collapsible groups. One decision applies to the whole group.
The existing `decide_batch` endpoint already supports this on the backend.

**Integration point:** `app/static/app.js` — modify the review page rendering.
Backend already supports batch via `decide_batch`.

**Effort:** Medium (2–3 days, mostly frontend)
**Impact:** High on reviewer throughput.

---

### C4. Map / Street View Integration

**Problem:** For genuinely hard cases (same name, 2 km apart), the reviewer needs
to see the physical locations. Currently they must open Google Maps in a separate
tab and search manually.

**Solution:** Embed a map view directly in the review console:
- Show both hotel pins on a map with distance line
- Link to Google Street View for each location
- Show satellite view to identify if it's the same building

**Integration point:** `app/static/app.js` and `app/static/index.html` — add a
Leaflet/Google Maps embed to the review detail view.

**Effort:** Low-Medium (1–2 days)
**Impact:** Medium on reviewer accuracy and speed for hard cases.

---

### C5. Active Learning Feedback Loop

**Problem:** Reviewer decisions are recorded but never used to improve the
pipeline's thresholds or models. A reviewer who approves 95% of TIER3 matches
is telling you that TIER3 should be in the publish gate, but nobody is
listening.

**Solution:** Build a feedback loop:
1. **Threshold tuning** — After every N reviewer decisions, recompute optimal
   thresholds for `AUTO_MATCH_MIN_SCORE`, `MANUAL_REVIEW_MIN_SCORE`, and
   `AI_CONFIRM_AT` against the growing labelled dataset.
2. **Model retraining** — Periodically retrain the embedding model or learned
   scorer on the latest reviewer decisions.
3. **Gate adjustment** — If false merge rate stays at 0% for 1,000+ decisions,
   consider expanding `PUBLISH_TIERS` to include TIER3.

**Integration point:** New service: `app/services/feedback_service.py`. Runs as
a periodic job (weekly/monthly). Outputs recommendations that a human confirms
before applying.

**Effort:** High (5–7 days)
**Impact:** High — makes the system self-improving over time.

---

## D. Pipeline Architecture

### D1. Incremental / Real-Time Processing

**Problem:** Currently the pipeline runs as a batch job. New supplier data waits
until the next manual run or scheduled drain. In a production booking system,
stale mappings mean lost revenue.

**Solution:** Process new records as they arrive:
- File upload → immediate enqueue → processed within minutes
- Webhook/API endpoint for real-time single-hotel matching
- Keep batch mode as a full-rebuild capability, but default to incremental

**Integration point:** `app/services/queue_processing_service.py` already
supports claim-and-process semantics. Add a lightweight trigger that processes
immediately after import rather than waiting for a manual "Run Pipeline" click.

**Effort:** Low (1 day — most infrastructure already exists)
**Impact:** Operational improvement, no accuracy change.

---

### D2. Parallel Worker Scaling

**Problem:** The current Celery workers process the queue sequentially within a
claimed batch. With 8,000+ records, a single run takes significant time.

**Solution:** The `SKIP LOCKED` claim mechanism already supports concurrent
workers. Deploy 4–8 workers processing independent batches in parallel:
- Each worker claims its own batch
- No coordination needed (the lock is in the database)
- Linear speedup up to the database connection limit

**Integration point:** `docker-compose.yml` — scale the worker service.
`config.py` — ensure `DB_POOL_SIZE × worker_count < max_connections`.

**Effort:** Low (0.5 day — just deployment config)
**Impact:** 4–8x throughput improvement.

---

### D3. Warm-Start Rebuilds

**Problem:** A full pipeline reset re-scores every record from scratch, even
records that haven't changed and whose master neighbourhood hasn't changed.
This wastes time and can produce different results due to ordering effects.

**Solution:** Track what has changed since the last run:
- New supplier records → must be scored
- Modified supplier records (coordinate correction, name fix) → re-score
- New masters created → re-score nearby unmatched records against them
- Unchanged records with unchanged surroundings → skip

**Integration point:** Add a `last_processed_at` or version column to
`supplier_hotels`. `claim_pending_batch` only picks records where inputs
changed.

**Effort:** Medium (3–4 days)
**Impact:** 10x reduction in rebuild time for incremental changes.

---

### D4. Embedding Index Maintenance

**Problem:** As the master table grows, pgvector's brute-force search slows.
When masters merge, their old embeddings become stale and waste index space.

**Solution:**
- Create an HNSW index on `hotel_embeddings` for faster approximate NN search
- Add a cleanup job that removes embeddings for deprecated/merged masters
- Re-embed masters whose representative data changed (e.g., after a merge
  consolidates address info)

**Integration point:** `scripts/` — migration to add HNSW index.
`app/matching/master_embedding_service.py` — cleanup method.

**Effort:** Low (1 day)
**Impact:** Performance improvement at scale, no accuracy change.

---

## E. Analytics & Monitoring

### E1. Matching Quality Dashboard

**Problem:** You can't improve what you can't measure. Currently, accuracy is
measured by a gate check at the end of each run, but there's no trend view.

**Solution:** Track and plot over time:
- Auto-match rate per run
- Manual review queue size trend
- False merge count (should always be 0)
- Duplicate masters created vs detected
- Reviewer agreement rate with AI suggestions
- Average time to drain review queue

**Integration point:** New table `pipeline_run_metrics`. Dashboard page in
`app/static/app.js`.

**Effort:** Medium (2–3 days)
**Impact:** Visibility — lets you know if things are improving or regressing.

---

### E2. Supplier Comparison Matrix

**Problem:** When two suppliers disagree about a hotel (different name, different
coordinates), you don't know which to trust without per-supplier quality data.

**Solution:** Build a heatmap/matrix showing:
- Supplier A vs Supplier B: % of shared hotels where they agree on name
- Supplier A vs B: average coordinate deviation for shared hotels
- Supplier A vs B: % of their shared hotels that needed manual review

Surface in the "Supplier Quality" page (already in nav).

**Integration point:** `app/services/supplier_quality_service.py` (new) +
API endpoint + frontend visualization.

**Effort:** Medium (2–3 days)
**Impact:** Informs which supplier data to trust/deprioritize.

---

### E3. Master Hotel Health Score

**Problem:** Not all masters are equally trustworthy. A master with 4 suppliers
all agreeing is reliable. A master with 1 supplier and coordinates that don't
match the address is suspect.

**Solution:** Compute per-master health based on:
- Number of contributing suppliers (more = healthier)
- Coordinate agreement across suppliers (low variance = healthier)
- Name agreement across suppliers (high similarity = healthier)
- Has been manually verified or not
- Star rating agreement

Flag unhealthy masters for periodic review.

**Integration point:** New computed column or materialized view on
`master_hotels`. Surfaced in the Master Hotels detail page.

**Effort:** Low-Medium (1–2 days)
**Impact:** Identifies masters most likely to be wrong.

---

### E4. Drift Detection

**Problem:** If a supplier re-submits a hotel with the same ID but wildly
different name or location, this likely indicates data corruption, not a
legitimate change. Currently it would be imported and re-processed normally.

**Solution:** At import time, compare against the existing record for that
supplier + hotel_id:
- Name changed >50% → flag as potential corruption
- Coordinates moved >5 km → flag as potential corruption
- City changed → flag for review
- Normal minor updates (address formatting, star rating change) → proceed

**Integration point:** `app/services/import_service.py` — add comparison
against existing row before update/insert.

**Effort:** Low (1 day)
**Impact:** Prevents bad data from corrupting established mappings.

---

## F. Export & Consumer Features

### F1. Confidence-Scored Exports

**Problem:** Currently exports are binary: published or withheld. Consumers
with different risk tolerances get the same data.

**Solution:** Export mappings with a confidence tier and score:
- Booking consumers (zero tolerance for wrong hotels) → TIER1 + TIER2 only
- Search/discovery consumers (need completeness) → all tiers with visible
  confidence labels
- Analytics consumers → everything including provisional

**Integration point:** `app/services/export_service.py` — add confidence
fields to export format. Let consumers filter by tier.

**Effort:** Low (0.5–1 day)
**Impact:** Better consumer experience, no accuracy change.

---

### F2. Multi-Region Support

**Problem:** The current system is India-focused (bounding box, city name
transliterations, Indian address patterns, postal code format).

**Solution:** Abstract region-specific logic:
- Country-specific bounding boxes (already partially there)
- Region-aware city name normalization (Munich/München, Bangkok/Krung Thep)
- Different postal code formats per country
- Different geocoding providers per region
- Language-specific transliteration

**Integration point:** `app/normalization/normalizer.py` — make geo-stripping
and transliteration region-aware. `config.py` — add region settings.

**Effort:** High (1–2 weeks)
**Impact:** Enables expansion beyond India.

---

### F3. Change Feed / Webhook for Consumers

**Problem:** Consumers currently poll exports to detect changes. When a master
is created, merged, or deprecated, they learn about it on the next export
generation — which might be hours or days later.

**Solution:** Emit events on master lifecycle changes:
- `master.created` — new master published
- `master.merged` — two masters became one (include redirect info)
- `master.deprecated` — master no longer valid
- `mapping.added` — new supplier attached to a master
- `mapping.removed` — supplier detached from a master

Consumers subscribe via webhook or message queue (Redis Streams, SQS, etc).

**Integration point:** `app/services/master_identity_service.py` — emit events
in `register_master`, `attach_to_master`, `merge`. New
`app/services/event_service.py` for dispatch.

**Effort:** Medium (3–4 days)
**Impact:** Real-time consumer sync, better operational visibility.

---

## G. Cost / Impact / Priority Matrix

| # | Improvement | Effort | Accuracy | Deduplication | Reviewer Speed | Priority |
|---|---|---|---|---|---|---|
| P1–6 | LLM Integration (all phases) | 9–11 days | High | High | High | **1** |
| A5 | Chain/Brand Knowledge Base | 1–2 days | Medium | High | — | **2** |
| B1 | Geocoding Validation | 2–3 days | Medium | **Very High** | — | **3** |
| A2 | Learned Scoring Model | 3–5 days | High | Medium | — | **4** |
| A1 | Fine-Tuned Embedding | 3–7 days | High | Medium | — | **5** |
| C2 | Confidence-Ranked Queue | 0.5–1 day | — | — | High | **6** |
| C1 | AI-Assisted Review Explanations | 1 day | — | — | High | **7** |
| C3 | Cluster-Based Bulk Review | 2–3 days | — | — | High | **8** |
| A3 | Phonetic/Transliteration Match | 1–2 days | Medium | Low | — | **9** |
| B5 | Import-Time Dedup | 1–2 days | Low | Medium | — | **10** |
| D2 | Parallel Worker Scaling | 0.5 day | — | — | — | **11** |
| D4 | Embedding Index Maintenance | 1 day | — | — | — | **12** |
| E4 | Drift Detection | 1 day | Low | Low | — | **13** |
| A4 | Dynamic Candidate Radius | 0.5–1 day | Low | Medium | — | **14** |
| C4 | Map/Street View in Console | 1–2 days | — | — | Medium | **15** |
| D1 | Incremental/Real-Time Processing | 1 day | — | — | — | **16** |
| E1 | Matching Quality Dashboard | 2–3 days | — | — | — | **17** |
| B2 | Address Parsing | 3–4 days | Medium | Medium | — | **18** |
| B3 | Supplier Quality Scoring | 2–3 days | Medium | Medium | — | **19** |
| E2 | Supplier Comparison Matrix | 2–3 days | — | — | — | **20** |
| E3 | Master Hotel Health Score | 1–2 days | — | Low | — | **21** |
| F1 | Confidence-Scored Exports | 0.5–1 day | — | — | — | **22** |
| F3 | Change Feed / Webhook | 3–4 days | — | — | — | **23** |
| C5 | Active Learning Feedback Loop | 5–7 days | High | Medium | High | **24** |
| D3 | Warm-Start Rebuilds | 3–4 days | — | — | — | **25** |
| B4 | External Data Enrichment | 5–7 days | High | High | — | **26** |
| F2 | Multi-Region Support | 1–2 weeks | — | — | — | **27** |

---

## Recommended Implementation Waves

### Wave 1: Quick Wins (1–2 weeks)
- LLM Phase 1–2 (foundation + borderline verification)
- Chain/brand knowledge base (A5)
- Confidence-ranked review queue (C2)
- Parallel worker scaling (D2)

### Wave 2: Duplication Kill (2–3 weeks)
- LLM Phase 3–4 (pre-creation check + batch dedup)
- Geocoding validation (B1)
- Import-time dedup (B5)
- Dynamic candidate radius (A4)

### Wave 3: Intelligence Layer (3–4 weeks)
- Learned scoring model (A2)
- Fine-tuned embedding (A1)
- AI-assisted review explanations (C1)
- LLM Phase 5–6 (exact-name + provisional)

### Wave 4: Reviewer Productivity (2 weeks)
- Cluster-based bulk review (C3)
- Map/Street View integration (C4)
- Phonetic matching (A3)
- Drift detection (E4)

### Wave 5: Self-Improving System (3–4 weeks)
- Active learning feedback loop (C5)
- Matching quality dashboard (E1)
- Supplier quality scoring (B3)
- Supplier comparison matrix (E2)

### Wave 6: Scale & Expand (4+ weeks)
- External data enrichment (B4)
- Address parsing (B2)
- Warm-start rebuilds (D3)
- Multi-region support (F2)
- Change feed/webhook (F3)
- Confidence-scored exports (F1)

---

## Total Estimated Timeline

| Wave | Duration | Cumulative |
|------|----------|------------|
| Wave 1 | 1–2 weeks | 2 weeks |
| Wave 2 | 2–3 weeks | 5 weeks |
| Wave 3 | 3–4 weeks | 9 weeks |
| Wave 4 | 2 weeks | 11 weeks |
| Wave 5 | 3–4 weeks | 15 weeks |
| Wave 6 | 4+ weeks | 19+ weeks |

Each wave is independently valuable and can ship on its own. The ordering
maximizes ROI: Wave 1 alone cuts the review queue by ~70% and prevents most new
duplicates, delivering the majority of the improvement in just 2 weeks.
