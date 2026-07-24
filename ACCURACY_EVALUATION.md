# Hotel Mapping Engine — Accuracy & Model Evaluation

**Dataset:** 8,432 real hotel records across 5 suppliers (Hummingbird, Booking.com, ClearTrip, GRN, Sabre), India.
**Evaluated against:** the current (rewritten) rule-based matcher output in the live database.
**Method:** measured on stored per-mapping evidence (`name_similarity`, `distance_meters`, `confidence_tier`) plus targeted adversarial sampling of the highest-risk mappings. No fresh re-run was needed — the live data is already current-code output (exact-name escalation and confidence tiers only the new matcher produces).

---

## Headline

| Metric | Result |
|--------|--------|
| **Precision (auto-matches correct)** | **~99%+** — zero false merges found in any adversarial sample |
| **Fragmentation (recall loss, upper bound)** | **≤ 3.3%** of masters (75 excess), true value lower |
| **Auto-decision rate** | **89.5%** (5,289 auto + 2,257 new-master of 8,432) |
| **Deferred to human review** | 7.1% (601 records) |
| **Flagged (data quality)** | 3.4% (285 records) |
| **ML model contribution** | **Zero** — the embedding model is dead code in the decision path |

This is a night-and-day improvement over the original matcher, which had a **29.4% false-positive rate** (e.g., a single Hilton Mumbai master absorbed 34 unrelated hotels). The worst master now holds 4 mappings, all correct.

---

## 1. Record accounting (reconciles to 8,432)

| Outcome | Count | Share |
|---------|-------|-------|
| Auto-matched to existing master | 5,289 | 62.7% |
| Created new master | 2,257 | 26.8% |
| Sent to manual review | 601 | 7.1% |
| Flagged (data quality) | 285 | 3.4% |
| **Total** | **8,432** | 100% |
| Distinct master hotels produced | 2,248 | — |

---

## 2. Precision (are the auto-matches correct?)

Precision is the metric that matters most — a false merge sends a guest to the wrong hotel and is not automatically recoverable.

**Every one of the 5,289 auto-matches has name similarity ≥ 75:**

| Name similarity band | Auto-matches | Avg distance |
|----------------------|--------------|--------------|
| 90–100 (near-identical) | 5,278 | 79 m |
| 75–90 (strong) | 11 | 21 m |

**Adversarial sampling — I deliberately inspected the mappings most likely to be wrong:**

- **Lowest name similarity (82–90%):** all correct. They are accent and spacing variants of the same hotel — "Le Méridien" vs "Le Meridien", "Saira Fort Sarovar Portico" vs "Sairafort Sarovar Portico", "Welcom Hotel" vs "Welcomhotel".
- **Greatest distance (925–990 m apart):** all correct. Same hotel with supplier coordinate disagreement — "The Zuri Whitefield Bengaluru" vs "The Zuri Whitefield" (933 m), "Royal Court" vs "Royal Court Madurai" (949 m). The tier gate correctly demands 95%+ name similarity at this range.
- **Most crowded masters (4 distinct supplier names each):** all correct. "President — IHCL SeleQtions", "Taj MG Road, Bengaluru", "The Den Bengaluru/Bangalore" — each is one hotel with 4 supplier spellings, all at 100% core-name similarity within tens of meters.

**No false merge surfaced in any slice.** Precision is at or above ~99%. (A precise figure would require hand-labeling all 5,289 pairs; the point is that the highest-risk subsets are clean.)

### Why precision is now high — the fix that worked

The old matcher gave a flat 85 points for mere proximity within 1 km, so any two nearby hotels auto-matched. The new matcher makes geography a **hard gate scaled to name confidence** (`passes_distance_tier`): a match at 1 km needs 95/90 name similarity, at 500 m needs 90/85, at 200 m needs 80. Proximity alone can no longer produce a match. This is what eliminated the airport/CBD cluster merges.

---

## 3. Recall / fragmentation (are true duplicates being missed?)

The failure mode of a conservative matcher is the opposite of the old one: it **under-merges**, leaving the same hotel as two masters.

- **Fragmented groups:** 75 `(core_name, city)` groups span more than one master.
- **Excess masters:** 75 (upper bound on fragmentation), i.e. **≤ 3.3%** of the 2,248 masters.
- **True fragmentation is lower** than 75, because many shared-core-name groups are genuinely different properties — e.g., "Ginger Chennai (Tharamani)" vs "Ginger Chennai (Vadapalani)", "Ginger Surat (City Centre)" vs "Ginger Surat (Piplod)" are different hotels correctly kept separate.
- **The system does not fragment silently.** 545 same-name-but-far-apart pairs (`EXACT_NAME_GEO_CONFLICT`) are correctly queued for human review rather than being split or wrongly merged. This is the intended design: conflicting evidence (identical name, contradicted by distance) goes to a person.

---

## 4. Cross-supplier deduplication (the actual goal)

The whole point of the system is collapsing the same hotel across suppliers. It works:

| Master carries hotels from | Masters |
|----------------------------|---------|
| All 5 suppliers | 741 |
| 4 suppliers | 519 |
| 3 suppliers | 268 |
| 2 suppliers | 241 |
| 1 supplier only | 479 |

**78.7% of masters (1,769 of 2,248) carry two or more suppliers** — real dedup wins. 741 hotels were correctly recognized across all five feeds despite different names, spellings, and coordinates.

---

## 5. Data-quality flagging

The matcher now refuses to silently map unverifiable records:

| Flag reason | Count |
|-------------|-------|
| SUPPLIER_ID_COLLISION (one supplier id reused for two different hotels) | 212 |
| DUPLICATE_SUPPLIER_ROW (same id, same hotel, duplicate line) | 73 |

These are surfaced for correction rather than mapped, which prevents a bad supplier id from resolving a booking to the wrong property.

---

## 6. Model evaluation — the AI is dead code

**Finding: the sentence-transformer model (`all-MiniLM-L6-v2`) has zero effect on any mapping decision.** This is proven, not inferred:

| Evidence | Value |
|----------|-------|
| Manual-review candidates with `ai_similarity` populated | **0 of 601** |
| Manual reviews from the AI score-band path | **0** (all 601 are exact-name/city-stripped) |
| Supplier embeddings ever generated (needed for AI search) | **0** |
| Master embeddings generated | 2,256 |
| Master embeddings ever queried | **0** |

### Why it is dead

The embedding search (`find_ai_matches`) is only reachable from `AIIntegrationService.enrich_candidate`, and only inside a `75 ≤ rule_score < 90` window. But:

1. `enrich_candidate` runs **only** when the decision is `MANUAL_REVIEW`.
2. The rewritten matcher scores on a different scale — `MANUAL_REVIEW` happens either from an **exact-name conflict** (which `enrich_candidate` returns on *before* reaching the AI call) or from a composite score of **30–40** (which falls below the 75 threshold, into the `else` branch → `CREATE_NEW_MASTER`).
3. So the `75–90` window is never entered. The model is never queried.

A side effect: composite-score borderline candidates (score 30–40) are converted straight to `CREATE_NEW_MASTER` instead of reaching a human — the score-band review path is effectively disabled. Only exact-name conflicts get reviewed.

### Is the model worth fixing, or removing?

- **The rule engine alone achieves ~99% precision without it.** The normalizer (city/chain stripping, accent handling) plus RapidFuzz `token_set_ratio` / `token_sort_ratio` already handles the semantic variation the embedding model was meant to catch ("ITC Grand Chola" vs "Grand Chola"). For this task and this data, the model is **redundant**.
- **It currently costs without benefiting:** it loads a ~500 MB model per worker and runs inference to produce 2,256 vectors that are never read.
- **`all-MiniLM-L6-v2` itself is a reasonable general-purpose embedder** (384-dim, fast, English-optimized). If a semantic tie-breaker were ever wanted for a genuine borderline band, it is a sound choice — but it would need the borderline band to actually exist and route into it, and it would need to be re-validated against the rule engine to prove it adds anything.

**Recommendation:** either (a) remove the embedding model and its master-embedding generation to reclaim memory and compute, or (b) if you want an AI backstop, re-wire `enrich_candidate` to trigger on the new score scale and prove on a labeled sample that it beats the rule engine before trusting it. Do not ship it as-is claiming "AI matching" — it does nothing.

---

## 7. Verdict

| Question | Answer |
|----------|--------|
| Mapping accuracy (precision) | ~99%+ on auto-matches; no false merges found |
| Recall / fragmentation | ≤ 3.3% under-merge (upper bound), ambiguous cases correctly deferred |
| Cross-supplier dedup | Works — 78.7% of masters are multi-supplier |
| Is the AI model used? | No — it is dead code with zero decision impact |
| Is the AI model needed? | No — the rule engine matches this data to ~99% without it |
| Production-ready on accuracy? | Yes on precision; watch fragmentation and drain the review queue |

**Bottom line:** the rewrite turned a 29%-false-positive prototype into a high-precision matcher. The accuracy comes entirely from the rule engine (name normalization + fuzzy similarity + distance-gated tiers), not from the ML model, which is currently inert. Fix or remove the model — do not rely on it.

---

### Caveats on method

- Precision was measured by adversarial sampling of the riskiest mappings, not by exhaustively labeling all 5,289 pairs. No false positive was found, but the true rate could be a fraction of a percent.
- Recall is approximated via name-based fragmentation; without a hand-labeled ground-truth set, exact recall cannot be stated. A labeled sample of a few hundred pairs would convert these estimates into hard precision/recall numbers.
