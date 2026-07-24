# Architecture Comparison — Vervotech vs. the In-House Mapping Engine

**Date:** 22 July 2026
**Purpose:** assess what replacing Vervotech actually requires, beyond the
matching engine itself.

---

## A necessary caveat on sourcing

**I do not have inside knowledge of Vervotech's implementation.** What follows
separates two things and labels them throughout:

- **Observable** — what is visible from their public product surface,
  documentation and the shape of their API.
- **Inferred** — the reference architecture that *any* hotel-mapping product
  operating at that scale must have. This is well-understood in the domain and
  is not specific to Vervotech; a competitor would look broadly similar.

Nothing here should be quoted as a statement about Vervotech's internal design.
Where a decision depends on their actual behaviour, the answer is to test it —
and §7 explains how, using the integration already in place.

---

## 1. What Vervotech provides (observable)

| Capability | Notes |
|---|---|
| **Property mapping** | Deduplicates hotel records across many supplier feeds into one canonical property |
| **Persistent unified id** | A stable identifier per property, carried across suppliers and over time |
| **Room mapping** | Maps room types across suppliers. **HummingBird already has this live as its own product**, so it is not a gap here |
| **Broad supplier coverage** | Thousands of supplier feeds, millions of properties, global |
| **Delivery** | API plus bulk/feed delivery, with ongoing updates |
| **Managed accuracy** | Accuracy is contracted and operated as a service, not a one-off output |

The last point is the one that matters most commercially. What is bought is not
a matching algorithm — it is **a maintained, warranted mapping**, continuously
reconciled as feeds change, with somebody accountable when it is wrong.

---

## 2. Reference architecture for production hotel mapping (inferred)

Any system in this category has to solve nine problems. This is the honest
comparison frame.

| # | Stage | What it must do |
|---|---|---|
| 1 | **Ingestion** | Continuous, per-supplier feeds; schema drift; change detection |
| 2 | **Cleansing** | Normalisation, geocoding, validation, canonical city/locality gazetteer |
| 3 | **Blocking** | Reduce N×M to a tractable candidate set — geo, phonetic, trigram, postal |
| 4 | **Signal extraction** | Name, geo, address, postal, phone, email, website, chain/brand, star, room count, images, third-party ids |
| 5 | **Decision** | Score and classify pairs; usually a model trained on labelled pairs, not fixed thresholds |
| 6 | **Identity lifecycle** | Stable ids; merge, split, deprecate, rename, reopen — with history |
| 7 | **Human loop** | Ops team adjudicating low-confidence and disputed cases, continuously |
| 8 | **Feedback** | Customer-reported errors flow back into labels and retraining |
| 9 | **Delivery** | Versioned API/feed, confidence exposed, change notifications |

---

## 3. Component-by-component comparison

Legend: ● present · ◐ partial · ○ absent

| # | Component | Vervotech (observable/inferred) | In-house today | Gap |
|---|---|---|---|---|
| 1 | Continuous ingestion | ● Ongoing per-supplier feeds | ◐ One-shot CSV import | **Significant** |
| 2 | Change detection | ● Incremental re-mapping on feed change | ○ Full rebuild only | **Significant** |
| 3 | Cleansing / gazetteer | ● Canonical geography | ◐ City stripped from names; no gazetteer | Moderate |
| 4 | Blocking | ● Multi-strategy | ● Geo-first, 1 km, PostGIS GIST | **Comparable** |
| 5 | Signal richness | ● Many signals incl. phone/website/ids | ◐ Name + geo + address + postal | **Significant** |
| 6 | Decision layer | ● Trained model (inferred) | ◐ Tiered rules + gates, hand-fitted | Moderate |
| 7 | Confidence exposed | ● | ● TIER1–TIER4 per mapping | **Comparable** |
| 8 | Identity lifecycle | ● Stable ids, merge/split history | ● Stable `public_id` + merge history; split/deprecate flow pending | Minor |
| 9 | Human review loop | ● Standing ops function | ◐ Endpoints exist, no process | **Significant** |
| 10 | Feedback → retraining | ● | ○ | **Significant** |
| 11 | Self-audit / integrity | Not externally visible | ● Structural invariant + monitor | **In-house ahead** |
| 12 | Evidence per decision | Not externally visible | ● Similarity, distance, tier stored | **In-house ahead** |
| 13 | Room mapping | ● | ● Live in-house product | **Comparable** |
| 14 | Coverage | Millions, global | 8,432, India, 5 suppliers | **Critical** |
| 15 | Accountability | Contracted SLA | Internal | Commercial decision |

---

## 4. The gaps that decide this

### 4.1 Master ids have no lifecycle — **RESOLVED**

Fixed during this engagement. `master_hotel_id` remains an internal
auto-increment key and is never published; consumers receive `public_id`
(`HBM-00000001`) from a registry that survives rebuilds and carries merge
history. Verified: two consecutive full rebuilds reproduced **all 8,133 public
ids identically**. See `CHANGES.md` §13.

Still outstanding: an explicit **split** workflow (one master found to be two
properties) and **deprecation** when a hotel closes. The tables and audit trail
support both; the operator-facing flow is not built.

### 4.2 Signal poverty

The matcher uses **name, geography and address**. Star rating is present for one
supplier only; chain name is empty in all five feeds, so that scoring component
is dead.

**Postal code — now used, but it is a weak signal, not the "free win" first
reported.** Measured on this data: codes agree 87.7% of the time for the same
hotel, but also 74.8% of the time for *different* hotels within 300 m, because a
pincode covers a whole locality. Only disagreement is informative. It is applied
as a penalty and a distance cap, never a bonus (`CHANGES.md` §14). Coverage:

| Supplier | Rows | With postal code |
|---|---|---|
| Booking.com | 1,586 | 1,586 (100%) |
| ClearTrip | 1,568 | 1,513 (96.5%) |
| GRN | 1,642 | 1,617 (98.5%) |
| Hummingbird | 1,714 | 1,711 (99.8%) |
| Sabre | 1,922 | 1,891 (98.4%) |

Using it at all first required fixing an import bug: pandas read the column as a
float, so three suppliers stored `"110001.0"` and two `"110001"`, and every
cross-supplier comparison failed on formatting alone.

The signals commercial mappers rely on and these feeds do not carry
— **phone, website, email** — are near-unique per property and would settle most
remaining ambiguity. Whether suppliers can provide them is a commercial
question worth asking; it may be cheaper than any further algorithm work.

---

## 5. Where the in-house build is genuinely ahead

Not everything favours the incumbent. Three things here are stronger than what a
black-box service gives you:

1. **An enforced structural invariant.** "One supplier contributes at most one
   hotel to a master" is checked against every mapping, not a sample, and
   *prevents* the merge rather than reporting it. Current result: 0 violations
   across 5,395 matches.
2. **Evidence on every decision.** Each mapping stores the name similarity,
   distance and confidence tier that produced it. Every merge is auditable and
   reversible. A third-party service typically gives you the answer, not the
   reasoning.
3. **Self-audit.** `GET /api/v1/mapping/integrity` looks for the *shape* of a
   bad merge after every batch, needing no ground truth.

Plus the strategic argument: control over thresholds, the ability to tune to
HummingBird's own risk tolerance, and no per-record cost.

---

## 6. Honest assessment

**The matching engine is now credible. The product around it is not.**

Accuracy on the 8,432-record sample went from ~54% to 96.3% end-to-end with zero
confirmed false positives in 66 hand-verified pairs, and it processes 10 lakh
records without falling over. That is a real result and it demonstrates the
approach works.

But replacing Vervotech means owning:

| Requirement | Status |
|---|---|
| Property mapping engine | **Done, needs validation at scale** |
| Stable public ids | **Done — verified across full rebuilds** |
| Id split / deprecation workflow | Tables exist, operator flow not built |
| Room mapping | **Already live in-house** |
| Continuous ingestion + incremental re-mapping | **Not started** |
| Standing ops/review function | **Not started — a hiring question, not a coding one** |
| Feedback loop from production errors | **Not started** |
| Coverage beyond 5 Indian suppliers | **Not started** |

With room mapping already live and public ids now stable, the remaining gap is
predominantly **operational** rather than algorithmic: continuous ingestion,
incremental re-mapping, a standing review function, and a feedback loop. That is
the part of the service that is bought, and it is the part still to be built.

---

## 7. Recommended next steps

**Immediate, before anything consumes this output**

1. ~~Make master ids stable~~ — **done**; publish `public_id`, never `master_hotel_id`.
2. ~~Add postal code~~ — **done**, as a negative signal only.
3. Run the Vervotech shadow-diff (`AUDIT_REPORT.md` §8d). Their existing
   mappings are a ground-truth oracle over your live inventory, at no labelling
   cost. It is the only way to get a false-positive rate measured on real
   volume, and it doubles as a direct capability benchmark.

**Then**

4. Build the operator flow for id **split** and **deprecation** (storage and
   audit trail already exist).
5. Move ingestion from one-shot import to continuous per-supplier feeds with
   change detection.
6. Ask suppliers for phone/website/email; scope the answer commercially.
7. Decide who owns the review queue as a standing function.

**Strategically**

A staged position is available and probably right: **run the in-house engine for
property mapping where it is now measurably strong, and keep Vervotech for
long-tail supplier coverage** until continuous ingestion and the review function
are actually in place. That captures most of the cost saving without betting
bookings on capabilities not yet built.

Do not cut over on the strength of the 8,432-record result. It is a strong
signal that the approach works — it is not evidence about the other 99% of the
catalogue.
