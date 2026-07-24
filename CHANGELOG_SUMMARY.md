# Hotel Mapping Engine — Summary of Work

**Date:** 22 July 2026
**Prepared for:** HummingBird Digital leadership
**Project:** in-house hotel mapping engine (candidate replacement for Vervotech
property mapping)

---

## One-paragraph summary

The mapping engine built by our interns was audited and found to be producing
**roughly half its output incorrectly** — a third of all automatic matches joined
two genuinely different hotels into one record. It also could not physically
process 10 lakh records. Both problems have been fixed and verified. Accuracy
is now **96.3% end-to-end with no confirmed false matches**, the system handles
10 lakh records, and a review console has been built so the team can inspect and
correct the mapping. Two things remain before production: validating against our
existing Vervotech mappings at real volume, and adding login to the console.

---

## Why this mattered

A **false match** means two different hotels merged into one record. In a unified
booking API that means a guest books Hotel A and is sent to Hotel B. At 10 lakh
records nobody can check the mapping by hand, so the error rate has to be driven
down and, more importantly, made **detectable**.

---

## What was wrong

The audit measured the original system against 8,432 real hotel records from our
five suppliers.

| Finding | Measured |
|---|---|
| Automatic matches joining **different** hotels | **1,863 — 33.5%** |
| Overall correct outcomes | **~54%** |
| Master hotel records that were clean | 48% |
| Duplicate master records (same hotel stored twice) | 473 |
| Hotels stuck in review with no result at all | 670 |

**Root cause.** The scoring gave 85 of the 90 points needed for a match to
*geographic proximity alone*. Hotel name contributed at most 10 points and could
be **zero** while the match still succeeded. Any two hotels within 1 km of each
other could merge regardless of name — which is why one record had absorbed 34
supplier entries covering roughly 11 distinct Mumbai airport hotels: Hilton,
Novotel, ITC Maratha, JW Marriott, Leela and others, all treated as one hotel.

The system also could not run at scale: a missing database index made processing
slow down quadratically, and a memory defect meant a 10-lakh run needed **15 GB
of RAM** and would have been killed part-way.

---

## What was done

### Accuracy

The matching logic was rebuilt around one principle: **a hotel's identity is its
name; geography only confirms it.** Weighting is now name 45, address 20,
geography 25, and a match cannot happen at all unless the names genuinely agree.

Three specific defects were found by hand-checking output rather than by reading
code:

1. Supplier names embed the city, so "Vivanta **Coimbatore**" scored 71% against
   "WelcomHotel **Coimbatore**" on the shared city alone. The city is now removed
   before names are compared.
2. The text-matching library scored "The Residency" as a 100% match for "The
   Residency **Towers**" — two different Chennai hotels.
3. Shared locality names did the same: "Namah Resort **Jim Corbett**" against
   "Voco **Jim Corbett**". The system now compares only the *distinguishing*
   part of each name.

A **structural safeguard** was also added, and it is the strongest protection in
the system: *one supplier never lists the same hotel twice under different
names.* If a master ever holds two records from the same supplier, they must be
different hotels — so that merge is now blocked outright. This is checked against
**every** mapping, not a sample.

### Scale

| Fix | Effect |
|---|---|
| Added the missing database index | Per-record update **40 ms → 0.4 ms** |
| Stopped holding every result in memory | **15.3 GB → flat at ~890 MB** |
| Made parallel workers safe | 4 workers, zero duplicate records |
| Connection pooling, batching, index tuning | Removes the remaining scale limits |

Tested against a purpose-built **1,000,000-record** dataset.

### Stable hotel IDs

Previously, rebuilding the mapping **reassigned every hotel ID**. Any system that
had saved an ID would have silently pointed at the wrong hotel. Hotels now carry
a permanent public ID (`HBM-00000114`) that survives rebuilds and redirects
correctly if two records are later merged.

*Verified:* two complete rebuilds reproduced **all 8,133 IDs identically**.

### Review console

The old frontend was replaced. The new console runs inside the existing setup —
no separate deployment.

- **Dashboard** — health at a glance, with a prominent alert when hotels have
  been left out of mapping
- **Discarded Records** — every excluded hotel with a plain-English reason and a
  suggested action; nothing is deleted, and items stay marked NEW until someone
  reviews them
- **Master Hotels** — search, inspect, and **split apart** hotels that were
  wrongly merged
- **Integrity Checks** — the system auditing its own output
- **Supplier Quality** — discard rate per supplier, for supplier conversations
- **Import Data** — load supplier hotels from a CSV/Excel file or straight from a
  supplier's database. Column names are matched automatically and shown for
  confirmation before anything is saved
- **Run Pipeline** — start the mapping and watch progress
- **Exports** — download any section as Excel, filters already applied

**Reviewer decisions are permanent.** When someone splits two hotels apart, the
engine records that decision and will never re-merge them. Without this the next
run would silently undo the correction — the original code had exactly this bug
elsewhere, where rejecting a match simply put it back in the queue.

*Verified:* a hotel was split, the entire mapping rebuilt from scratch, and the
split held.

---

## Results

| Measure | Before | After |
|---|---|---|
| **Correct outcomes overall** | ~54% | **96.3%** |
| **Matches joining different hotels** | 1,863 (33.5%) | **0 confirmed** |
| High-confidence matches | 38.5% | **77%** |
| Duplicate master records | 473 | 106 |
| Hotels stuck with no result | 670 | **0** |
| Failed records | — | **0** |
| Can it process 10 lakh records? | No | **Yes** |
| Hotel IDs stable across rebuilds? | No | **Yes** |

**91% of matches are now high-confidence** and can be published without review.
The remaining 9% is where any residual risk sits — a bounded set someone can
actually work through, rather than the whole catalogue.

---

## A supplier problem we found

Independent of our software, and worth raising with Sabre this week.

**Sabre uses the same hotel ID for different hotels.** 89 IDs are attached to 212
records covering more than one property. Examples:

- ID `100240826` is both **Aalia Resort** and **Amatra By the Ganges** — 13 km apart
- ID `100041328` is both **Hotel Madhuban** and **Wh Dehradun** — 7.4 km apart

A booking on those IDs cannot be resolved to one hotel. Separately, 57 IDs carry
contradictory coordinates for the same property — one is 24 km out — and two
records ship with the literal name `Zz To Be Deleted-`.

**This exists today on Vervotech too.** It is a defect in the supplier feed that
no mapping engine can repair. Full evidence, ready to send:
`sabre_id_collisions.csv` (126 cases with names, coordinates and distances).

Our system now detects and quarantines these rather than guessing.

---

## Honest position on replacing Vervotech

**What I can state:** on our 8,432-record sample, accuracy is high and no false
matches were found in 66 hand-checked pairs plus a structural check across all
5,366 matches.

**What I cannot state:** that this is safe at 10 lakh records. 66 hand-checked
pairs bounds the error rate below 4.5% statistically — not zero. That is not
evidence for the risk we are carrying.

**The way to close that gap costs nothing.** We already run Vervotech. Their
mappings for our live inventory are a ready-made answer key, far larger than any
we could build by hand. Running both over the same inventory and comparing gives
us a true error rate at real volume, every disagreement as a case to examine,
and a defensible go/no-go decision.

### Recommended sequence

| Stage | Action | Move on when |
|---|---|---|
| 1 | Run both engines in parallel and compare | Error rate measured at real volume |
| 2 | Examine every disagreement, retune | Error rate within agreed tolerance |
| 3 | Publish only high-confidence matches (91%) | Integrity checks clean each run |
| 4 | Extend to the remaining 9% | Its measured rate is acceptable |
| 5 | Retire Vervotech | Sustained clean runs |

**Keep Vervotech live through stages 1–4.** Running both for a period costs
little against a single wrong hotel reaching a booking.

Note that room mapping is already live as our own product, so the remaining gap
against Vervotech is narrower than it first appeared — it is now mainly
operational: continuous supplier feeds, and someone owning the review queue as a
standing responsibility.

---

## Before production

| # | Item | Size |
|---|---|---|
| 1 | Compare against Vervotech at real volume | Days, mostly waiting |
| 2 | **Add login to the review console** — anyone who can reach it can change mappings | ~1 day |
| 3 | Retune thresholds against the comparison results | 2–3 days |
| 4 | Full 10-lakh run end to end | 1 day |
| 5 | Decide who owns the review queue | A staffing decision |

Items 2 and 5 are not engineering problems and need a decision from the business.

---

## Supporting documents

| Document | Contents |
|---|---|
| `AUDIT_REPORT.md` | Full technical audit, all findings with evidence |
| `CHANGES.md` | Every code change with the reason for it |
| `REVIEW_CONSOLE.md` | User guide for the team |
| `ARCHITECTURE_COMPARISON.md` | Honest comparison with Vervotech, and what is still missing |
| `sabre_id_collisions.csv` | Supplier defect evidence, ready to send |

---

## What the team can now do without engineering help

| Task | Before | Now |
|---|---|---|
| Load a new supplier file | Engineer ran a script | Upload it in the console |
| Load from a supplier's database | Not possible | Connection details and a query |
| Run the mapping | Engineer ran a command | One button, with progress |
| Get data into Excel | Engineer wrote a query | Download button in every section |
| Correct a wrongly merged hotel | Not possible | Split it; the decision is permanent |
| See what was left out and why | Not visible at all | Dedicated screen with plain-English reasons |
