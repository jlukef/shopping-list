# Phase 2 — SQLite Data Model & Import Plan

Author: Claude (product architecture / data-semantics lane).
Status: **implemented foundation; corrected and reviewed by Codex on 2026-06-30.** Companion to `BACKEND_MIGRATION_PLAN.md`
and `UX_FLOWS.md`.

> **Fresh-start decision (Jamie, 2026-06-30):** current Google Sheets data does not need to be
> retained. The importer and mapping notes below remain as optional tooling/reference, but are no
> longer on the critical path. Phase 3 now creates a clean database with seven editable seeded shops;
> list, item dictionary, and history start empty.

> **Implementation status (2026-06-30, Claude).** Jamie asked me to implement this while Codex is out.
> Built the Phase 2 **foundation** — *not* wired into the live app (Phase 4 stays for Codex):
> - `src/shopping_list/models.py` — all tables below as SQLModel models, with CHECK constraints and
>   `ON DELETE CASCADE`/`SET NULL` rules.
> - `src/shopping_list/db.py` — engine + PRAGMAs (`foreign_keys=ON`, WAL), `init_db`, idempotent
>   default-shop seed, `bootstrap()`.
> - `src/shopping_list/importer.py` — idempotent Sheets→SQLite import + verification.
> - `scripts/import_from_sheets.py` — CLI (live API subset + JSON exports).
> - Import regression tests cover raw Sheet headers, duplicate history rows, rollback safety, and CLI input.
>   The full suite is **55/55** after Codex's review fixes; compileall
>   + `node --check` clean.
> - `requirements.txt` — added `sqlmodel`.
>
> **Verified against the live Apps Script subset:** import pulled 8 shops, 6 list items, 44
> departments / 459 keywords; ran twice → idempotent; verification `ok: true`. That live API does
> not expose Items or History, so the result was not a full migration rehearsal. Raw export-shaped
> fixtures now cover those two sheets. Two refinements made during
> implementation: (a) the import script creates **schema only, no default-shop seeding** (a migration
> must mirror the Sheet; seeding is greenfield-only via `db.bootstrap`); (b) the row-count check treats
> shops/list as exact but **items as `>=`** (importing list/history legitimately auto-creates canonical
> items). Untouched: `auth.py`, app routes, the running app.
>
> **Superseded note (Codex/Claude, batch 4, 2026-06-30):** the line above originally said not to wire
> Phase 4 until a full raw-export reconciliation passed. That no longer applies — Jamie's fresh-start
> decision (top of this file) means Phase 4 was wired up *without* any Sheets import at all. The
> importer/reconciliation checks remain available as optional tooling, not a Phase 4 prerequisite.
> Phase 4 (`src/shopping_list/sqlite_api.py`) is implemented and is the default data backend; see
> `BACKEND_MIGRATION_PLAN.md` Phase 3/4 and `FASTAPI_WRAPPER_RUNBOOK.md` for the current status.

**Decision (Jamie, 2026-06-30):** use **SQLite** (not PostgreSQL) for the database, built through an
ORM (**SQLModel / SQLAlchemy**) so a later move to Postgres stays a low-cost swap. Rationale: two
users, single VPS, simplest possible ops, and Phase 1 already uses SQLite for sessions. This is a
deliberate change from the plan's earlier Postgres lean; `BACKEND_MIGRATION_PLAN.md` is reconciled.

The schema below is the source of truth for Phase 2. It is written as SQLite DDL for precision; the
team may implement it via SQLModel models that generate equivalent tables.

---

## 1. Conventions (read first — these are deliberate data-semantics choices)

- **Money is stored as INTEGER pennies, never floats.** A float `0.84` will eventually corrupt a
  total; `84` pennies will not. Every money column ends in `_pennies`. Display layer divides by 100.
  Each money-bearing row also carries a `currency` (default `'GBP'`) so totals are never summed
  across currencies by accident.
- **Quantities are REAL** (`1`, `1.5`, `0.4`) with a separate `unit` TEXT (`'kg'`, `'L'`, `'pack'`,
  `''`). Spend on a line = `line_total_pennies` (authoritative); `unit_price_pennies` is informational
  and may be null (receipts don't always print it).
- **Timestamps are TEXT, ISO-8601, UTC** (e.g. `2026-06-30T19:05:00Z`) — matches the existing
  `auth.py` (`to_db_datetime`). **Dates** (no time) are TEXT `YYYY-MM-DD` (e.g. a purchase date).
- **Booleans are INTEGER 0/1** (SQLite has no bool type).
- **PRAGMAs at connection:** `foreign_keys = ON` (SQLite defaults OFF — must be set every connection),
  and `journal_mode = WAL` (lets the two of you read/write concurrently without "database is locked").
- **IDs:** surrogate `INTEGER PRIMARY KEY` (rowid) everywhere **except `shops`**, whose id stays a
  TEXT slug (`'tesco'`, `'aldi'`) to match the existing frontend, localStorage `shopOrder`, and
  Apps Script data we're importing.
- **Status/enum columns are TEXT** with a `CHECK (... IN (...))` constraint — readable in a DB browser,
  and SQLite has no native enum.
- **The app sets timestamps in UTC** (consistent with Phase 1); DDL `DEFAULT` clauses below are a
  fallback only.
- **JSON blobs** (raw OCR/AI output) are TEXT holding JSON; query with SQLite's json1 functions.
  Maps cleanly to Postgres `JSONB` later.

---

## 2. Schema

### 2.1 Auth (folds Phase 1 sessions into a real users table)

```sql
CREATE TABLE users (
    id            INTEGER PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,          -- lowercased, matches auth.UserStore
    password_hash TEXT NOT NULL,                 -- pbkdf2_sha256$... (see auth.py)
    display_name  TEXT,                          -- "Jamie", "Beth" — for history attribution UI
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

-- Phase 1 already has a sessions table; align it to reference users.id.
CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY,                 -- sha256 of the cookie token
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_sessions_expires ON sessions(expires_at);   -- for expiry sweeps
```

Note: Phase 1 currently stores `username` directly on the session row. Migrating to `user_id` is
cleaner but is auth-adjacent — **Codex owns** whether to do that now or keep username for one more
phase. No household table is needed yet (single shared household; per-user attribution is captured by
`*_user_id` columns).

### 2.2 Catalog: shops, items, aliases

```sql
CREATE TABLE shops (
    id         TEXT PRIMARY KEY,                 -- slug: 'tesco', 'aldi', 'morrisons'
    name       TEXT NOT NULL,
    emoji      TEXT,
    color      TEXT,
    active     INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,       -- canonical order (replaces localStorage shopOrder)
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Master item dictionary (the canonical identity of a product).
CREATE TABLE items (
    id               INTEGER PRIMARY KEY,
    canonical_name   TEXT NOT NULL UNIQUE,       -- 'semi skimmed milk' (normalised, lowercased)
    display_name     TEXT,                       -- 'Semi-skimmed milk' (nice-cased for UI)
    category         TEXT,                       -- legacy Items category, if present
    default_shop_id  TEXT REFERENCES shops(id) ON DELETE SET NULL,
    default_quantity REAL,
    default_unit     TEXT,
    use_count        INTEGER NOT NULL DEFAULT 0,
    last_used_at     TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

-- Learned aliases → canonical item. This is how messy receipt text resolves to identity.
-- Server owns the matching rules (per UX_FLOWS §4.1); this table stores the learned results.
CREATE TABLE item_aliases (
    id         INTEGER PRIMARY KEY,
    item_id    INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    alias_text TEXT NOT NULL,                    -- 'TESCO SEMI SKIMMED MILK 2.27L' (raw, lowercased)
    source     TEXT NOT NULL DEFAULT 'user'      -- 'user' | 'receipt' | 'seed'
        CHECK (source IN ('user','receipt','seed')),
    created_at TEXT NOT NULL,
    UNIQUE (alias_text)
);
CREATE INDEX idx_item_aliases_item ON item_aliases(item_id);
```

Canonicalisation policy (data-semantics recommendation, **server enforces**): normalise
case/whitespace/punctuation; treat `milk` / `semi skimmed milk` / `whole milk` as **distinct** items
(under-merge, never over-merge); keep the original raw text on the receipt row for audit.

### 2.3 Active shopping list

```sql
CREATE TABLE shopping_list_items (
    id                INTEGER PRIMARY KEY,
    source_ref        TEXT UNIQUE,               -- legacy List UUID during migration
    item_id           INTEGER REFERENCES items(id) ON DELETE SET NULL,  -- nullable while unmatched
    name              TEXT NOT NULL,             -- what the user typed/sees
    quantity          REAL NOT NULL DEFAULT 1,
    unit              TEXT NOT NULL DEFAULT '',
    shop_id           TEXT REFERENCES shops(id) ON DELETE SET NULL,
    bought            INTEGER NOT NULL DEFAULT 0,
    notes             TEXT,
    sort_order        INTEGER NOT NULL DEFAULT 0,
    added_by_user_id  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    bought_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at        TEXT NOT NULL,
    bought_at         TEXT,
    updated_at        TEXT NOT NULL
);
CREATE INDEX idx_list_shop   ON shopping_list_items(shop_id);
CREATE INDEX idx_list_bought ON shopping_list_items(bought);
```

### 2.4 Store layout (aisle ordering for AI/keyword sort)

```sql
CREATE TABLE store_layout_departments (
    id         INTEGER PRIMARY KEY,
    shop_id    TEXT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_dept_shop ON store_layout_departments(shop_id);

-- Sheets stored keywords comma-joined in one cell; here they are one row each.
CREATE TABLE store_layout_keywords (
    id            INTEGER PRIMARY KEY,
    department_id INTEGER NOT NULL REFERENCES store_layout_departments(id) ON DELETE CASCADE,
    keyword       TEXT NOT NULL
);
CREATE INDEX idx_kw_dept ON store_layout_keywords(department_id);
```

### 2.5 History — trips & trip items (the analytics backbone)

This is where "calculate value" lives. Every bought thing eventually becomes a `shopping_trip_item`
with a price, so spend can be summed by shop, by item, by month.

```sql
CREATE TABLE shopping_trips (
    id                 INTEGER PRIMARY KEY,
    shop_id            TEXT REFERENCES shops(id) ON DELETE SET NULL,
    trip_date          TEXT NOT NULL,            -- 'YYYY-MM-DD' (the shop date)
    source             TEXT NOT NULL             -- where the trip came from
        CHECK (source IN ('receipt','manual','clear_bought','import')),
    total_pennies      INTEGER,                  -- receipt grand total (authoritative when present)
    currency           TEXT NOT NULL DEFAULT 'GBP',
    notes              TEXT,
    created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    started_at         TEXT,
    completed_at       TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);
CREATE INDEX idx_trips_date ON shopping_trips(trip_date);
CREATE INDEX idx_trips_shop ON shopping_trips(shop_id);

CREATE TABLE shopping_trip_items (
    id                 INTEGER PRIMARY KEY,
    trip_id            INTEGER NOT NULL REFERENCES shopping_trips(id) ON DELETE CASCADE,
    item_id            INTEGER REFERENCES items(id) ON DELETE SET NULL,
    name               TEXT NOT NULL,            -- snapshot of name at purchase time
    quantity           REAL NOT NULL DEFAULT 1,
    unit               TEXT NOT NULL DEFAULT '',
    shop_id            TEXT REFERENCES shops(id) ON DELETE SET NULL,  -- usually = trip's shop
    unit_price_pennies INTEGER,                  -- price each (nullable)
    line_total_pennies INTEGER,                  -- qty * unit price, or receipt's line total
    currency           TEXT NOT NULL DEFAULT 'GBP',
    bought_at          TEXT NOT NULL,            -- ISO timestamp; powers cadence prediction
    source_ref         TEXT UNIQUE,              -- deterministic legacy-import row occurrence key
    source_receipt_item_id INTEGER REFERENCES receipt_items(id) ON DELETE SET NULL,
    created_at         TEXT NOT NULL
);
CREATE INDEX idx_tripitems_trip ON shopping_trip_items(trip_id);
CREATE INDEX idx_tripitems_item ON shopping_trip_items(item_id);
CREATE INDEX idx_tripitems_shop ON shopping_trip_items(shop_id);
CREATE INDEX idx_tripitems_bought_at ON shopping_trip_items(bought_at);
CREATE UNIQUE INDEX idx_tripitems_source_ref ON shopping_trip_items(source_ref);
```

Trip sources:
- `receipt` — created when a reviewed receipt is saved (the rich case: prices, totals).
- `clear_bought` / `manual` — created when bought items are cleared from the list (per Codex's
  documented nuance, clear-bought must **write** history, not silently delete). Prices usually null.
- `import` — synthetic trips built from the legacy Sheets `History` (see §3).

### 2.6 Receipts & extracted lines

```sql
CREATE TABLE receipts (
    id                  INTEGER PRIMARY KEY,
    shopping_trip_id    INTEGER REFERENCES shopping_trips(id) ON DELETE SET NULL,  -- set on accept
    uploaded_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    shop_id             TEXT REFERENCES shops(id) ON DELETE SET NULL,
    purchase_date       TEXT,                    -- 'YYYY-MM-DD' parsed from the receipt
    original_filename   TEXT,
    stored_path         TEXT NOT NULL,           -- file on disk under data/uploads/ (NOT in DB)
    mime_type           TEXT,
    file_size_bytes     INTEGER,
    status              TEXT NOT NULL DEFAULT 'uploaded'
        CHECK (status IN ('uploaded','processing','ready','reviewed','saved','failed')),
    ocr_engine          TEXT,                    -- 'tesseract' | 'ai:claude-...' | 'hybrid'
    ocr_text            TEXT,                    -- raw extracted text
    raw_extraction_json TEXT,                    -- structured candidate items as JSON (audit)
    subtotal_pennies    INTEGER,
    total_pennies       INTEGER,
    currency            TEXT NOT NULL DEFAULT 'GBP',
    extracted_at        TEXT,
    reviewed_at         TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX idx_receipts_status ON receipts(status);
CREATE INDEX idx_receipts_user   ON receipts(uploaded_by_user_id);

CREATE TABLE receipt_items (
    id                 INTEGER PRIMARY KEY,
    receipt_id         INTEGER NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
    item_id            INTEGER REFERENCES items(id) ON DELETE SET NULL,  -- proposed/confirmed match
    line_no            INTEGER,                  -- order on the receipt
    raw_text           TEXT NOT NULL,            -- exactly what OCR read (immutable audit)
    name               TEXT,                     -- user-corrected name
    quantity           REAL,
    unit               TEXT,
    unit_price_pennies INTEGER,
    line_total_pennies INTEGER,
    confidence         REAL,                     -- 0..1, drives the amber/green dot in review UI
    category           TEXT NOT NULL DEFAULT 'item'   -- separates products from noise
        CHECK (category IN ('item','discount','loyalty','subtotal','total','tax','other')),
    excluded           INTEGER NOT NULL DEFAULT 0,    -- offers/totals hidden from "Save N items"
    accepted           INTEGER NOT NULL DEFAULT 0,    -- promoted into a trip item
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);
CREATE INDEX idx_receipt_items_receipt ON receipt_items(receipt_id);
```

`category` + `excluded` directly back the UX_FLOWS review screen ("N lines hidden as offers/totals/
points"). Accepting a row sets `accepted=1` and creates a `shopping_trip_item` whose
`source_receipt_item_id` points back here.

### 2.7 Prediction — cadence stats, suggestions, AI runs

```sql
-- Materialised per-item purchase stats (recomputed after each trip/import; household-global).
CREATE TABLE item_purchase_stats (
    item_id                  INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    total_purchases          INTEGER NOT NULL DEFAULT 0,
    first_bought_at          TEXT,
    last_bought_at           TEXT,
    avg_interval_days        REAL,               -- e.g. milk ~5; drives "due" detection
    stddev_interval_days     REAL,
    avg_quantity             REAL,
    avg_unit_price_pennies   INTEGER,            -- for value/forecast ("milk usually ~£1.45")
    preferred_shop_id        TEXT REFERENCES shops(id) ON DELETE SET NULL,
    updated_at               TEXT NOT NULL
);

-- Generated suggestions + the user's accept/dismiss feedback (for decay/weighting).
CREATE TABLE suggestions (
    id                 INTEGER PRIMARY KEY,
    item_id            INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    source             TEXT NOT NULL             -- how it was generated
        CHECK (source IN ('cadence','ai')),
    model              TEXT,                     -- 'claude-haiku-4-5-...' when source='ai'
    ai_run_id          INTEGER REFERENCES ai_prediction_runs(id) ON DELETE SET NULL,
    reason             TEXT,                     -- plain-English "why" shown in the chip
    score              REAL,                     -- 0..1 confidence/priority
    predicted_shop_id  TEXT REFERENCES shops(id) ON DELETE SET NULL,
    due_date           TEXT,                     -- 'YYYY-MM-DD' when it's likely needed
    status             TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','accepted','dismissed','expired')),
    generated_at       TEXT NOT NULL,
    responded_at       TEXT,
    responded_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX idx_suggestions_status ON suggestions(status);
CREATE INDEX idx_suggestions_item   ON suggestions(item_id);

-- Audit + cost trail for AI/LLM prediction calls (separate from cheap cadence math).
CREATE TABLE ai_prediction_runs (
    id              INTEGER PRIMARY KEY,
    model           TEXT NOT NULL,               -- e.g. 'claude-haiku-4-5-20251001'
    prompt_version  TEXT,
    input_summary   TEXT,                        -- what we sent (no secrets)
    raw_response_json TEXT,                       -- full structured response (audit)
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    cost_pennies    INTEGER,                     -- so AI spend is itself trackable
    created_at      TEXT NOT NULL
);
```

Prediction approach (per the plan's "boring first"): the `cadence` source is pure SQL/arithmetic over
`item_purchase_stats` (frequently bought + overdue vs `avg_interval_days`, preferring the usual shop).
The `ai` source is optional and additive — an LLM can re-rank or explain, with every call logged in
`ai_prediction_runs` (including `cost_pennies`, since you asked to track value — AI spend is value too).

---

## 3. Optional legacy mapping (not required for the clean start)

| Sheets sheet | → Table(s) | Notes |
|---|---|---|
| `Shops` (id,name,emoji,color) | `shops` | Direct. Preserve slug ids; set `sort_order` from current `shopOrder`. |
| `Items` (item,count,lastUsed,category,defaultShop,defaultQty,defaultUnit) | `items` | `item`→`canonical_name` (normalised); preserve category/count/defaults/last-used. |
| `List` (id,item,quantity,unit,shop,bought,dateAdded,notes,sortOrder) | `shopping_list_items` | Preserve legacy UUID as `source_ref` and `dateAdded` as `created_at`; `item`→`name`; resolve `item_id` by matching to `items`. |
| `StoreLayouts` (shop,department,order,keywords) | `store_layout_departments` + `store_layout_keywords` | Split comma-joined `keywords` into one row each. |
| `History` (item,quantity,unit,shop,dateBought) | `shopping_trips` + `shopping_trip_items` | Preserve quantity/unit/date for every row. Group by (shop, date(dateBought)); prices remain null. `boughtAt` is accepted only as a compatibility alias for transformed exports. |

Legacy `History` has quantities and units but **no prices** — so historical spend starts accruing from the first *receipt* or
*clear-bought* after go-live. That's expected; the import preserves *what* was bought and *when* (so
cadence prediction works immediately), just not historical spend.

**Safety:** export/snapshot the live Sheet (and `getList`/`getShops`/`getLayouts` JSON) to a dated
backup file **before** the first import. Import must be **idempotent** (re-runnable without
duplicating). History uses a deterministic hash of item, quantity, unit, shop, parsed purchase time,
and duplicate-occurrence number. This preserves genuinely identical duplicate rows while preventing
reruns from duplicating them. Active-list and layout replacements commit atomically.

---

## 4. Import-verification checklist (for Codex to run / Gemini to expand)

Each check has an exact expected outcome, not "looks right":

1. **Row counts match.** `Shops` rows == `COUNT(*) FROM shops`; same for `Items`, `List`. Record the
   numbers in the import log.
2. **History fan-out reconciles.** `COUNT(*) FROM shopping_trip_items WHERE source-trip is 'import'`
   == number of `History` rows imported.
3. **Required shops exist.** Every shop slug referenced by any `List`/`History` row exists in `shops`
   (no orphan `shop_id`). Default set + live extras seen on 2026-06-30: tesco, aldi, lidl, amazon,
   boots, other, **morrisons, butcher**.
4. **No blank names.** `SELECT COUNT(*) FROM items WHERE canonical_name=''` → 0; same for list/trip item names.
5. **Dates parse.** Every `History.dateBought` produced a valid ISO `bought_at`; count of unparseable → 0
   (log any rejects rather than silently dropping).
6. **FK integrity.** `PRAGMA foreign_key_check;` returns no rows.
7. **Money sanity.** No negative `*_pennies` except known discount lines; no money column holds a
   non-integer.
8. **Idempotency.** Run the import twice into a temp DB → identical row counts after the second run.
9. **Spot-check.** Pull 5 random `Items`, 5 `List`, 5 `History` rows and eyeball them against the Sheet.
10. **Boundary.** App still passes the existing 37 tests against the new data layer once endpoints flip.

---

## 5. Analytics — example "value" queries (what the schema unlocks)

```sql
-- Total spend per shop in June 2026 (pennies → pounds in the app layer)
SELECT shop_id, SUM(line_total_pennies) AS spend_pennies
FROM shopping_trip_items
WHERE bought_at >= '2026-06-01' AND bought_at < '2026-07-01'
GROUP BY shop_id ORDER BY spend_pennies DESC;

-- Monthly grocery spend trend
SELECT substr(trip_date,1,7) AS month, SUM(total_pennies) AS spend_pennies
FROM shopping_trips GROUP BY month ORDER BY month;

-- Price history of one item across shops (spot price changes / cheapest shop)
SELECT s.name, ti.bought_at, ti.unit_price_pennies
FROM shopping_trip_items ti JOIN shops s ON s.id = ti.shop_id
WHERE ti.item_id = :item_id AND ti.unit_price_pennies IS NOT NULL
ORDER BY ti.bought_at;

-- Most expensive items by total lifetime spend
SELECT i.display_name, SUM(ti.line_total_pennies) AS spend_pennies
FROM shopping_trip_items ti JOIN items i ON i.id = ti.item_id
GROUP BY i.id ORDER BY spend_pennies DESC LIMIT 20;

-- "Due soon" (cadence): bought regularly, overdue vs its own average
SELECT i.display_name, st.avg_interval_days,
       julianday('now') - julianday(st.last_bought_at) AS days_since
FROM item_purchase_stats st JOIN items i ON i.id = st.item_id
WHERE st.total_purchases >= 3
  AND julianday('now') - julianday(st.last_bought_at) >= st.avg_interval_days
ORDER BY days_since DESC;
```

---

## 6. Implementation sequencing (suggested for Codex)

1. Add `sqlmodel` (pulls SQLAlchemy) to `requirements.txt`; pick a migration tool (**Alembic**
   recommended) — Codex's call.
2. Define models in e.g. `src/shopping_list/models.py`; connection/PRAGMA setup in `db.py`
   (`foreign_keys=ON`, `journal_mode=WAL`). Reuse Phase 1's `data/` dir; new DB file e.g.
   `data/shopping_list.sqlite` (keep sessions DB separate or fold in — Codex decides).
3. First migration creates the schema; a seed step inserts default shops + a starter layout for
   all seven shops (superseded: not Tesco/Aldi — see the fresh-start decision at the top of this file).
4. Write the importer (`scripts/import_from_sheets.py`) that reads the live Apps Script JSON (or a
   saved export) and populates tables idempotently; run §4 checks. (Superseded: not required for the
   clean-start cutover — kept as optional tooling only, see §3.)
5. Phase 4: dispatch the existing `/api?action=...` actions against SQLite (`sqlite_api.py`), keeping
   the legacy GET action contract rather than the REST shape originally sketched in
   `BACKEND_MIGRATION_PLAN.md` — see that file's "API shape" status note. Apps Script remains an
   explicit fallback (`SHOPPING_LIST_DATA_BACKEND=apps_script`), not a required parity gate.

---

## 7. Open questions for Codex / Jamie

1. **One DB file or two?** Fold Phase 1 sessions into this DB, or keep `sessions.sqlite` separate?
   (Leaning: one file `shopping_list.sqlite`, simpler backups.)
2. **Migration tool:** Alembic vs hand-rolled numbered SQL scripts? (Leaning Alembic for safety.)
3. **Sessions `username` → `user_id`** now (clean) or defer one phase (less churn)? Auth-adjacent —
   Codex's call.
4. **Receipt image retention** — **decided** in `BACKEND_MIGRATION_PLAN.md` (Codex review notes): keep
   uploaded receipt images for audit/review initially, stored locally under the app data directory;
   add configurable cleanup later if storage/privacy becomes a concern. No longer open.
5. **Quantity vs weight semantics:** is `quantity=0.4, unit='kg'` enough, or do we need a separate
   weight field for loose goods? (Leaning: `quantity`+`unit` is enough.)
6. **AI prediction scope for v1:** ship cadence-only first and add the `ai` source later? (Leaning yes
   — matches "boring predictor first". Tables are ready either way.)

Status update (batch 4, 2026-06-30): Phase 4 is no longer pending — `src/shopping_list/sqlite_api.py`
dispatches the live authenticated `/api?action=...` route against this schema, and SQLite is the
local default backend (`SHOPPING_LIST_DATA_BACKEND=sqlite`). What remains outstanding is **deployment
to the VPS only** (not local wiring), which still requires Jamie's explicit approval — see
`FASTAPI_WRAPPER_RUNBOOK.md`'s deploy checklist.
