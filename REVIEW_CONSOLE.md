# Review Console — User Guide

**For:** the team who will operate the hotel mapping day to day
**URL:** `http://localhost:8001/ui/` (redirects from `/`)

---

## A note on the design

The old React frontend has been replaced. The console is now served directly by
the API — no build step, no `npm install`, no separate deployment. It starts with
the existing Docker stack.

**On matching the Vervotech UI:** it is built to the conventions this class of
tool uses — fixed left navigation, dense data tables, status chips, detail panes,
confirmation dialogues for anything destructive. It is **not a pixel copy**,
because I have not seen their interface. If your colleagues share screenshots of
the screens they use most, the layout can be matched more closely; the structure
here is deliberately conventional so that adjusting it is straightforward.

---

## The nine screens

### 1. Dashboard

Where the day starts. Six counters, a confidence breakdown, and a per-supplier
table.

**The banner at the top is the important part.** If any hotels were left out of
mapping and nobody has looked at them yet, an amber banner appears with a count
and a **Review now** button. When everything has been reviewed it turns green.
A red banner appears separately if records *failed* — that means an error, not a
deliberate exclusion.

The confidence bar shows how much of the mapping is safe to publish unattended:

| Tier | Meaning | Safe to publish? |
|---|---|---|
| TIER1 | Near-identical name, under 100 m | Yes |
| TIER2 | Strong name match, under 200 m | Yes |
| TIER3 | Good name match, under 500 m | Spot-check |
| TIER4 | Weaker name or further apart | Review first |

### 2. Discarded Records — **the notification screen**

Every hotel the system refused to map, and why.

Nothing is ever deleted. A discarded record stays in the database with its
reason, and can be re-processed once the underlying data is corrected.

The top table explains each reason in plain language, with a suggested action —
a reviewer never has to ask an engineer what a flag means:

| Reason | Plain meaning | Action |
|---|---|---|
| Supplier reused one ID for two different hotels | The supplier gave the same hotel ID to two properties, so a booking on that ID is ambiguous | Report to supplier |
| Duplicate row in the supplier file | Same hotel twice with the same ID; the first was mapped | None needed |
| No latitude/longitude | Cannot confirm which building it is | Request from supplier |
| Coordinates are 0,0 | Placeholder, not a real location | Request from supplier |
| Coordinates outside the stated country | Location contradicts the country field | Request correction |
| No hotel name / city / country | Record incomplete | Request correction |
| Name carries no identifying information | Nothing distinguishing left after removing the city | Request a fuller name |

Records marked **NEW** have not been looked at. Tick them and click *Mark
selected as reviewed*, or use *Mark all reviewed*. The sidebar badge and the
dashboard banner both clear as you work through them.

Filter by reason with **View**, or use **Unread only** to see just what is new
since last time.

### 3. Master Hotels — search, inspect, and correct

#### Multi-field search

A free-text hotel name box, plus eight filters that combine with AND:

| Field | Notes |
|---|---|
| Master Hotel Id | Our permanent ID, e.g. `HBM-00000087` |
| Provider Hotel Id | The supplier's own ID |
| Provider Name | Dropdown of the loaded suppliers |
| Hotel Chain Name | **No data in the current feeds** — labelled as such |
| Property Type | **No data in the current feeds** — labelled as such |
| Country | Type-ahead from loaded countries |
| City Name | Free text |
| Star | Slider, 0–5, filters to that rating and above |

**Results appear as soon as any field has a value** — no need to press Search,
though the button is there. Typing pauses briefly before searching so it does not
fire on every keystroke.

**Get Property Count** returns totals across the whole match, not just the page:
unique hotels, provider records, and how many providers are involved.

#### Results

One row per **provider record**, grouped under its master ID — so searching one
ID shows every provider's version of that hotel side by side, which is what you
need to judge whether they really are the same property:

```
#  Master ID       Provider     Provider Hotel Id  Hotel Name              Star  Lat        Long
1  HBM-00000087    ClearTrip    794174             Ginger Surat            —     21.159871  72.768415
2  HBM-00000087    GRN          1428882            Ginger Surat            —     21.159758  72.768360
3  HBM-00000087    Hummingbird  962                Ginger Surat (Piplod)   —     21.159710  72.768184
```

**View** (👁) opens the master. **Find Duplicate** (⧉) looks for hotels within
1 km with a similar name that sit under a *different* master — the tool for
catching two records that should have been one. Previous/Next page through
results.

#### The master detail page

Opening a master shows every supplier record grouped under it, with the evidence
for each: name match percentage, distance, and confidence tier.

**If one of those records is a different hotel**, tick it and press
**Split selected out**. See below.

If a hotel has closed, press **Mark closed**.

Opening a retired ID works: you get a blue banner saying which ID it became, and
you are shown the live record. Nothing 404s.

### 4. Manual Review

Hotels the engine could not decide on, with a **search bar covering every field
at once** — hotel name, provider, provider hotel ID, city, state, country,
address, postcode, the suggested match, its ID, or the reason it was queued.
Results filter as you type.

Currently empty: borderline cases the AI rejects now become their own hotel
rather than queuing for a person.

### 5. Integrity Checks

The system checking its own work, on **every** mapping rather than a sample.

The main check: *a supplier never lists one hotel twice under different names.*
If one master holds two records from the same supplier, they are almost certainly
different hotels wrongly merged. Green means none found.

The second table lists masters worth a look — records spread unusually far apart
or with weak name agreement. **A wide spread with identical names is normally
just suppliers disagreeing on coordinates, not an error.**

### 6. Supplier Quality

Discard rate per supplier. A high rate means that supplier is sending records
that cannot be mapped safely. This is a supplier conversation, not a mapping
problem — and it is the screen to open before a supplier call.

### 7. Import Data

Two sources, both two-step: **check first, import second.** Nothing is written
until you have seen what will happen.

**From a file** — CSV, XLSX or XLS. Enter a supplier name, choose the file, press
*Check the file*.

**From a database** — a PostgreSQL or MySQL connection and a `SELECT`. Useful
when a supplier gives you database access rather than a file.

#### Column matching

Supplier files never agree on column names — one calls it `HotelName`, another
`Hotel_Name`, a third `mapping_hotel_name`. The importer recognises the common
variants automatically and **shows you what it matched** before importing:

```
Hotel ID     ←  SupplierId
Hotel name   ←  HotelName
Address      ←  Address
Coordinates  ←  LatitudeLongitude     (a combined "19.06,72.86" column)
Postal code  ←  Postal
```

Combined latitude/longitude columns are handled. Postal codes are cleaned to
digits (an earlier bug stored `"110001.0"` for some suppliers and `"110001"` for
others, which broke every comparison between them).

Only three columns are truly required: **hotel ID, hotel name, country.** If any
is missing the import is blocked with a clear message rather than silently
importing blank records.

You also see a data-quality count over the first 400 rows — how many are missing
coordinates, city or postal code — so a bad file is obvious before it lands.

If the file has its own supplier column, you are told so. **The name you type
wins**, so rows always land where you expect.

Tick *Queue them for mapping straight away* to send them to the pipeline
immediately, or leave it unticked to import now and map later.

### 8. Run Pipeline

Shows how many hotels are waiting, in progress, mapped, set aside or failed, with
a progress bar. Press **Run pipeline** and it processes in the background —
you can leave the page. The screen refreshes itself while work is running.

If anything failed (an error, as opposed to being deliberately set aside),
a **Retry failed** button appears.

### 9. Exports

Every dataset as an Excel file, with filters already switched on:

| Export | Contents |
|---|---|
| Master Hotels | One row per unique hotel with its permanent ID |
| Supplier to Master Mappings | Every match with its evidence — name match %, distance, confidence |
| Discarded Records | Everything left out, with reasons |
| Manual Review Queue | Hotels awaiting a decision |
| Supplier Hotels | Everything imported, as supplied |
| Supplier Data Quality | Discard rate and missing fields per supplier |
| Integrity | Masters worth a second look |
| Reviewer Decisions | Splits your team has made |
| Supplier ID Conflicts | Reused supplier IDs — send straight to the supplier |

Each section also has its own **Export to Excel** button in the header, so you
can download what you are looking at without leaving the page.

---

## Splitting a wrongly merged hotel

The most important thing the console does.

**When:** you open a master and one of the supplier records is plainly a
different property — the integrity screen flagged it, a customer complained, or
you spotted it while checking something else.

**How:**

1. Open the master (Master Hotels → search → Open).
2. Tick the record(s) that are a **different** hotel.
3. Press **Split selected out**.
4. A dialogue shows exactly what moves and what stays. Type why.
5. Confirm.

**What happens:**

- A brand-new master hotel is created with its own new ID
- The ticked records move to it
- The original ID keeps everything else
- A permanent note is recorded: **these hotels must never be merged again**

**That last point is what makes reviewing worthwhile.** Without it, the next
mapping run would look at the two hotels, see they are close together with
similar names, and merge them straight back — silently undoing the work. The
matching engine now consults reviewer decisions and obeys them, permanently.

*Verified:* a test split was performed, the entire 8,432-record mapping was then
rebuilt from scratch, and the split held — 0 violations.

**One limitation to understand.** If a system somewhere saved the old ID while it
still covered both hotels, nobody can now tell which of the two it meant — that
information was never recorded, because at the time the system did not know there
were two. After a split, downstream systems should re-check that ID.

---

## Marking a hotel closed

Press **Mark closed** on a master and choose:

- **Closed** — the property has shut down.
- **Dormant** — it still exists, but no supplier currently lists it. Use this
  when a hotel drops out of the feeds; it is often a feed problem rather than a
  closure.

The ID keeps working either way. Anything asking about it is told the hotel is
closed rather than getting an error, which is important because an error usually
gets retried. Both are reversible with **Reactivate**.

---

## API reference

Everything the console does is available directly.

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/v1/dashboard` | Counters, confidence breakdown, supplier table |
| GET | `/api/v1/discarded/summary` | Discard reasons with unread counts |
| GET | `/api/v1/discarded` | Discarded records (`reason`, `unread_only`, `limit`, `offset`) |
| GET | `/api/v1/discarded/by-supplier` | Discard rate per supplier |
| POST | `/api/v1/discarded/acknowledge` | Mark reviewed (`record_ids`, or `all: true`) |
| GET | `/api/v1/search/facets` | Dropdown values; flags filters with no data |
| GET | `/api/v1/masters/search` | Multi-field search (`q`, `master_id`, `provider_hotel_id`, `provider_name`, `chain_name`, `property_type`, `country`, `city`, `star_min`, `limit`, `offset`) |
| GET | `/api/v1/masters/count` | Totals for the same filters |
| GET | `/api/v1/records/{row_id}/duplicates` | Possible duplicates of one record |
| GET | `/api/v1/review-queue/search?q=` | Search the review queue across all fields |
| GET | `/api/v1/masters/{public_id}` | Full detail, members, history |
| POST | `/api/v1/masters/{public_id}/split` | Split (`supplier_row_ids`, `reason`) |
| POST | `/api/v1/masters/{public_id}/deprecate` | Close (`reason`, `case`) |
| POST | `/api/v1/masters/{public_id}/reactivate` | Undo a closure |
| GET | `/api/v1/masters/{public_id}/resolve` | Resolve any ID, retired or current |
| GET | `/api/v1/integrity` | Self-audit report |
| GET | `/api/v1/export` | List available datasets |
| GET | `/api/v1/export/{dataset}` | Download an Excel file |
| POST | `/api/v1/import/file/analyse` | Upload and detect columns (writes nothing) |
| POST | `/api/v1/import/file/commit` | Import the analysed file |
| POST | `/api/v1/import/database/analyse` | Test a connection and preview |
| POST | `/api/v1/import/database/commit` | Import from a database |
| POST | `/api/v1/pipeline/run` | Start mapping |
| GET | `/api/v1/pipeline/status` | Queue counts and progress |
| POST | `/api/v1/pipeline/requeue-failed` | Retry failed records |

**Always publish `public_id` (`HBM-00000114`), never `master_hotel_id`.** The
internal key is reassigned by every rebuild; the public ID is stable and
resolves forward through merges.

---

## Not built yet

Stated plainly so nobody is surprised:

- **No login.** Anyone who can reach the URL can split hotels, close them,
  import data and start the pipeline. Every action records an actor field, but it
  defaults to `reviewer` rather than a real user. **Do not expose this outside
  your network until authentication is added.**
- **The database import is the sharpest edge of that.** It makes the server open
  a connection to whatever address is typed in. Reads are restricted to a single
  `SELECT` and only `postgresql://` or `mysql://` are accepted, but without login
  anyone reachable could point it at an internal host. Treat authentication as a
  prerequisite for this screen specifically.
- **No duplicate detection on import.** Importing the same file twice imports it
  twice. The mapping stage catches the duplicates and sets them aside, but the
  raw records are stored twice.
- **No undo for a split.** Deprecation is reversible; a split is not, other than
  by manually merging.
- **No bulk actions** on masters — splits are one at a time by design.
- **No change-event feed.** Downstream systems are not automatically told when a
  master is split or closed; they must poll `/resolve`.
- **No pagination controls** in the discarded list UI (the API supports
  `offset`); it shows the most recent 200.
- **Hotel Chain Name and Property Type have no data.** The fields, columns and
  indexes exist and will work the moment a supplier provides those values, but
  today `chain_name` is empty in all five feeds and `property_type` was not in
  the schema at all. The search panel labels both so nobody wonders why a search
  returns nothing.
- **Star filters only 1,554 of 8,432 records** — only Booking.com supplies a star
  rating. A star filter therefore excludes most of the catalogue.
