# HB Hotel Mapping Engine

<p align="center">
  <img src="HB-logo%20BG-removed.png" alt="Hummingbird" width="180">
</p>

**Maps the same physical hotel across multiple suppliers into one deduplicated master record.**

Hotel suppliers describe the same property in incompatible ways — different names ("Taj Mahal Palace" vs "The Taj Mahal Palace, Mumbai"), differently formatted addresses, drifting coordinates, missing star ratings and chains. This engine ingests raw supplier feeds and resolves them into a single canonical list of hotels, so that one physical property maps to exactly one master regardless of how many suppliers sell it.

Built and tuned against **8,432 real hotel records across 5 suppliers** (Hummingbird, Booking.com, ClearTrip, GRN, Sabre) in India.

---

## What it does

- **Ingests** supplier feeds from Excel/CSV uploads or a source database, preserving the raw record untouched.
- **Normalizes** each record into blocking keys and a PostGIS geo-point.
- **Matches** every supplier hotel against existing masters using a measured, rule-based scorer backed by a semantic (vector) safety net.
- **Decides** automatically to attach to a master, create a new master, or **hold for a human** — with the confidence tier recorded on every mapping.
- **Publishes** only the confidence tiers that have never produced a wrong merge, holding the rest for review without hurting recall.
- **Provides a review console** (served by the API, no build step) for the operations team to work the manual-review queue, search masters, split/merge, and watch accuracy and integrity.

---

## Why it's careful

A **false merge is unrecoverable in a booking path** — it sends a guest to a property they did not book. The whole design is shaped around that asymmetry:

- Proximity alone can never produce an automatic match — a candidate must clear a **distance/name tier gate** first.
- The matcher scores only **features measured on this data** (name similarity, address, geo decay, building number, token rarity), with weights chosen from their real discriminating power — not guessed.
- The **semantic model can only escalate to review, never auto-merge.** It's a net for cases the rules miss, not an authority.
- Measured against a reference mapping on 1,714 hotels, publishing **TIER1 + TIER2 produces zero false merges**; those are the only tiers released to consumers unattended.

See [`ACCURACY_EVALUATION.md`](ACCURACY_EVALUATION.md) and [`AUDIT_REPORT.md`](AUDIT_REPORT.md) for the numbers.

---

## Architecture

```
Supplier feeds (Excel / CSV / source DB)
          │
          ▼
   ┌──────────────┐     raw record kept verbatim in supplier_hotels
   │   Import     │───▶ normalize → core_name / strict_name + geo_location
   └──────────────┘
          │  enqueue
          ▼
   ┌──────────────┐     Celery worker claims batches from hotel_mapping_queue
   │  Matching    │
   │   pipeline   │  1. Candidate search   geo-first (≤1 km) + name-first blocking
   │              │  2. Rule scorer        name 45 · address 20 · geo 25 · bldg 5 · rarity 5
   │              │  3. Semantic check     pgvector cosine, escalate-only
   │              │  4. Decision           AUTO_MATCH · MANUAL_REVIEW · CREATE_NEW_MASTER
   └──────────────┘
          │
          ▼
   ┌──────────────┐     TIER1/TIER2 → published · TIER3/TIER4 → held
   │ Publication  │───▶ export (only confirmed / safe tiers)
   │    gate      │
   └──────────────┘
          │
          ▼
   ┌──────────────┐     dashboard · manual-review queue · master search
   │Review console│     splits / merges · provisional masters · accuracy · integrity
   │    (/ui)     │
   └──────────────┘
```

### Tech stack

| Layer | Technology |
|---|---|
| API | FastAPI + Uvicorn |
| Database | PostgreSQL with **PostGIS** (geo), **pg_trgm** (fuzzy name), **pgvector** (embeddings) |
| ORM / driver | SQLAlchemy 2.0 async · asyncpg · psycopg2 |
| Background jobs | Celery + Redis |
| Name similarity | RapidFuzz · geopy |
| Semantic similarity | sentence-transformers (384-dim) · Torch |
| Data I/O | pandas · openpyxl |
| Frontend | Vanilla HTML/CSS/JS served at `/ui` (no build step) |
| Packaging | Docker + docker-compose |

---

## Data model

| Table | Purpose |
|---|---|
| `supplier_hotels` | Raw supplier records, **never modified after import**. Holds derived blocking keys `core_name` (city stripped) and `strict_name` (city retained) plus a PostGIS `geo_location`. |
| `master_hotels` | The deduplicated canonical list — one row per physical hotel. |
| `hotel_mappings` | `supplier_hotel → master_hotel` links, with `match_score`, `mapping_type` (AUTO / MANUAL) and confidence tier. |
| `hotel_mapping_queue` | Work queue read by the Celery worker (`Pending → Processing → Completed / Failed / ManualReview`). |
| `hotel_embeddings` | 384-dim vectors for semantic similarity (ivfflat cosine index). |
| `manual_review_candidates` | The suggested master + reason for records held for a human. |
| `flagged_records` | Records unfit for automatic processing — reported back to the supplier, never mapped. |

Schema and extensions are bootstrapped by [`scripts/init.sql`](scripts/init.sql) on first database start.

---

## The matching engine

The rule-based scorer ([`app/matching/matcher.py`](app/matching/matcher.py)) is a two-part decision:

**1. Tier gate (hard, must pass to auto-match).** Distance tolerance scales with name confidence — a strong, length-sensitive name match earns geographic latitude; a weak one demands near co-location:

| Max distance | Loose name sim | Strict name sim |
|---|---|---|
| 1000 m | ≥ 95 | ≥ 90 |
| 500 m | ≥ 90 | ≥ 85 |
| 200 m | ≥ 80 | — |

**2. Composite score** out of 100, screening candidates that clear the gate:

| Feature | Weight | Why |
|---|---|---|
| Name similarity | 45 | Core name compared with city stripped, so unrelated hotels don't score high just for sharing a city. |
| Address | 20 | Token-set fuzzy ratio. |
| Geo | 25 | **Decays** with distance — confirms a name match, never substitutes for one. |
| Building number | 5 | Agrees on 88% of same-hotel pairs, 7% of different hotels within 1 km. |
| Token rarity | 5 | Sharing a *rare* word ("radisson") is near-proof; sharing only "hotel" is the trap. |
| Postal mismatch | −6 | Penalty only (a pincode covers a whole locality); never blocks a match outright. |

Every mapping is stamped with a **confidence tier** (TIER1–TIER4) so downstream consumers know what is safe to trust unattended. Two extra safeguards handle the hard cases:

- **Exact-name escalation** — an identical name whose coordinates disagree by more than 1 km (bad geocoding) becomes `MANUAL_REVIEW` rather than silently splitting into two masters. It can never *lower* the bar for an automatic match.
- **Semantic duplicate check** — before creating a new master, pgvector looks for a near-duplicate the rules missed and escalates to review if found.

Field weights can be re-profiled — see [`WEIGHT_PROFILES.md`](WEIGHT_PROFILES.md).

---

## Getting started

### With Docker (recommended)

```bash
docker compose up --build
```

This starts four containers: PostgreSQL (with PostGIS/pgvector), Redis, the API, and a Celery worker. The schema is created automatically on first run.

| Service | URL / port |
|---|---|
| API + review console | http://localhost:8001 (redirects to `/ui`) |
| Interactive API docs | http://localhost:8001/docs · http://localhost:8001/redoc |
| Health check | http://localhost:8001/health |
| PostgreSQL | `localhost:5436` |
| Redis | `localhost:6380` |

### Local development

Requires Python 3.11, and a PostgreSQL (with PostGIS + pgvector) and Redis you can reach.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # then edit connection URLs

uvicorn app.main:app --reload --port 8000              # API
celery -A app.jobs.celery_app worker --loglevel=info   # worker (separate shell)
```

### Configuration

Settings load from `.env` (see [`.env.example`](.env.example) and [`config.py`](config.py)):

| Variable | Purpose |
|---|---|
| `DATABASE_URL` / `SYNC_DATABASE_URL` | Async (asyncpg) and sync (psycopg2) Postgres URLs. |
| `REDIS_URL` / `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | Redis for the queue and Celery. |
| `PUBLISH_TIERS` | Confidence tiers released without human review (default `TIER1,TIER2`). |
| `QUEUE_CLAIM_BATCH` | Rows a worker claims per batch. |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | Connection-pool sizing (keep `(size+overflow) × workers` under the server's `max_connections`). |

---

## Typical workflow

1. **Import** a supplier workbook or pull from a source database
   (`POST /api/v1/import/file/analyse` → `commit`, or `POST /api/v1/hotels/import`).
2. **Run the pipeline** to match the queued records
   (`POST /api/v1/pipeline/run`, or the `mapping/run` endpoint; `scripts/run_full_mapping.py` runs an end-to-end pass).
3. **Watch progress** on the dashboard (`GET /api/v1/dashboard`) or `GET /api/v1/pipeline/status`.
4. **Work the queue** in the review console at `/ui` — attach, approve, create-master, reject, split, or merge.
5. **Export** the mapped, publishable set (`GET /api/v1/export`).

Full API surface is browsable at **`/docs`**. Route groups: **Hotels** (import/status), **Mapping** (run, reset, statistics, manual review, master lookup), **Review** (dashboard, master search, provisional masters, duplicates, integrity, accuracy evaluation), **Data** (file/database import, export, pipeline control).

---

## Review console

Served directly by the API at `/ui` — no separate deployment. It gives the operations team the dashboard, the manual-review queue, master search, split/merge tools, provisional-master confirmation, duplicate sweeps, and live accuracy/integrity readouts. Operator guide: [`REVIEW_CONSOLE.md`](REVIEW_CONSOLE.md).

---

## Project layout

```
app/
  api/            FastAPI routers (hotels, mapping, review, data)
  matching/       rule-based scorer + AI/vector similarity services
  normalization/  name → blocking-key normalizer
  services/       import, matching, queue, review, export, accuracy, integrity, …
  repositories/   data access
  models/         SQLAlchemy models + Pydantic schemas
  jobs/           Celery app + mapping worker
  static/         the review console (HTML/CSS/JS)
  main.py         app wiring, routers, /ui mount, health
config.py         settings (env-driven)
scripts/          init.sql, migrations, and one-off pipeline / backfill scripts
docker-compose.yml
```

---

## Further reading

| Document | What it covers |
|---|---|
| [`AUDIT_REPORT.md`](AUDIT_REPORT.md) | Technical audit of the engine. |
| [`ACCURACY_EVALUATION.md`](ACCURACY_EVALUATION.md) | Accuracy and model evaluation against ground truth. |
| [`ARCHITECTURE_COMPARISON.md`](ARCHITECTURE_COMPARISON.md) | This engine vs. a third-party mapping service. |
| [`WEIGHT_PROFILES.md`](WEIGHT_PROFILES.md) | The matcher's field weights and how to re-profile them. |
| [`REVIEW_CONSOLE.md`](REVIEW_CONSOLE.md) | Operator guide for the review UI. |
| [`INCREMENTAL_RUNS_DESIGN.md`](INCREMENTAL_RUNS_DESIGN.md) | Design notes for incremental (non-full-rebuild) runs. |
| [`CHANGES.md`](CHANGES.md) · [`CHANGELOG_SUMMARY.md`](CHANGELOG_SUMMARY.md) | Change log and summary of work. |
