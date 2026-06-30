# Gemini Checklist Review

This document contains the checklist review performed by the Gemini agent, as requested in `COLLAB-LOG.md`.

## 1. Schema Comparison & Missing Fields
Comparing current Google Sheets fields (`AGENTS.md`) with proposed database tables (`BACKEND_MIGRATION_PLAN.md`):

- **List vs shopping_list_items**: 
  - `List` sheet uses `item` (string). `shopping_list_items` uses `name` (string) and `item_id` (foreign key). We need to clarify if `name` is the canonical name fallback or user-edited name, and if `item_id` is strictly required.
  - No `added_by_user_id` or `bought_by_user_id` in Sheets; these will need default/null values during migration.
- **Items vs items**:
  - `Items` sheet has `item` (string). The table has `canonical_name`. These map directly.
  - `Items` lacks `last_used_at`; this can be inferred from the most recent `History` entry during migration, or set to null.
- **History vs shopping_trip_items**:
  - `History` sheet contains `item`, `shop`, `boughtAt`.
  - The new schema groups history into `shopping_trips`. To migrate, we will either need to group `History` rows by `boughtAt` (date/time) and `shop` to synthesize `shopping_trips`, or allow dummy/nullable trips for legacy data.
- **StoreLayouts vs store_layout_departments / store_layout_keywords**:
  - Sheets stores `keywords` as a single column (likely comma-separated). Migration will need to split this string into individual rows for `store_layout_keywords`.

## 2. Expanded Manual Test Cases
*(Expanding the original list with pass/fail specifics)*

**Auth & Access**
- [ ] Logged-out user visiting `/` sees login page or redirect.
- [ ] Bad password fails gracefully without revealing whether the username exists.
- [ ] Good login sets secure HTTP-only session cookie (verify via browser dev tools).
- [ ] Logout invalidates session on server and clears cookie.
- [ ] Direct static file access (e.g., `/index.html` or `/app.js`) does not bypass login.
- [ ] Direct API access (e.g., `GET /api/list`) returns 401 Unauthorized without session.
- [ ] Refreshing the page preserves authenticated state without prompting login again.

**Core List Features**
- [ ] Logged-in user can load the current list; counts match the database.
- [ ] Add item: Item appears in list and is saved to the backend.
- [ ] Update item: Toggling 'bought' state reflects in UI and database.
- [ ] Delete item: Item is removed from UI and database.
- [ ] Clear bought: All bought items are removed from current list and moved into history/trips.

**Migration & Mobile**
- [ ] Bought items correctly generate history records (or shopping trip records).
- [ ] App behaves sensibly on mobile devices (viewport scaling, tap targets).

**Receipt Upload & OCR (Phase 5+)**
- [ ] Receipt upload rejects unsupported file types (e.g., .exe, .pdf if not supported).
- [ ] Receipt upload rejects files larger than a defined size limit (e.g., >10MB).
- [ ] OCR review screen allows editing parsed text, deleting bad rows, and accepting valid rows.
- [ ] Accepted receipt rows are committed to history, skipping current list.

**Suggestions (Phase 6+)**
- [ ] Suggestions UI shows items based on history cadence.
- [ ] Accepted suggestions correctly add to the active shopping list.

## 3. Simple Migration Checks
Checklist for Codex / DB migration script:
- [ ] **Row Counts:** Count of `List` rows in Sheets == count of `shopping_list_items` in DB.
- [ ] **Row Counts:** Count of `History` rows in Sheets == count of migrated `shopping_trip_items` in DB.
- [ ] **Shops Validation:** Required shops (e.g., Tesco, Aldi, Sainsbury's) exist in the `shops` table.
- [ ] **Data Integrity:** No blank/empty item names migrated into the `items` dictionary.
- [ ] **Date Parsing:** `boughtAt` from `History` sheet successfully parses into PostgreSQL timestamp format.
- [ ] **Keyword Splitting:** `StoreLayouts` keywords are correctly split and inserted into `store_layout_keywords`.
- [ ] **Orphans:** Ensure all items currently in the `List` sheet have a valid corresponding `items` dictionary record.

## 4. Receipt OCR Edge Cases
Things the OCR parsing logic and review UI must handle:
- [ ] **Poor image quality:** Blurry, crumpled, or badly lit receipts leading to unreadable text.
- [ ] **Duplicate item lines:** Buying 2 of the same item recorded as two separate lines on the receipt instead of "Item x2".
- [ ] **Discounts & Vouchers:** Price deductions appearing as negative items, which shouldn't be added to shopping history.
- [ ] **Loyalty points:** Clubcard / Nectar points balances parsed mistakenly as grocery items.
- [ ] **Multi-buy offers:** "3 for £5" promos that appear as separate lines or sub-lines.
- [ ] **Unknown shop:** Header of receipt missing or unreadable, shop cannot be auto-detected.
- [ ] **Non-items:** Subtotals, VAT breakdown, and card payment details parsed as items.
- [ ] **Non-grocery:** Clothes, electronics, or homeware on the same receipt as groceries.

## 5. Documentation Consistency & Migration Risks
Review of `UX_FLOWS.md`, `BACKEND_MIGRATION_PLAN.md`, and `AGENTS.md` for consistency:

**Missing Fields Identified:**
- [ ] **Suggestion Feedback:** `UX_FLOWS.md` recommends decaying suggestion confidence when users reject them. The schema in `BACKEND_MIGRATION_PLAN.md` has no `suggestion_feedback` table or `rejected_at` column to track this.
- [ ] **First Login State:** `UX_FLOWS.md` mentions skipping the welcome strip for returning logins. We may need a `has_logged_in_before` boolean on `users` if we can't reliably infer it from an empty list.

**Stale Filenames & Inconsistencies:**
- [ ] **Stale Open Question:** `BACKEND_MIGRATION_PLAN.md` lists "Use Node/Express, Python/FastAPI, or another backend stack?" as an open question, but Codex already recorded the Python/FastAPI decision at the top of the file.
- [ ] **Docs Stating Old Stack:** `AGENTS.md` firmly states the backend is Apps Script. While true right now, it should prominently reference `BACKEND_MIGRATION_PLAN.md` to prevent other agents from assuming Apps Script is the permanent target.

**Obvious Migration Risks:**
- [ ] **SQLite to PostgreSQL Dual-Migration:** `BACKEND_MIGRATION_PLAN.md` suggests using SQLite for Phase 1 sessions, then PostgreSQL for Phase 2. This creates a risk of needing an SQLite-to-PostgreSQL migration just for sessions. It might be safer to use PostgreSQL from Day 1.
- [ ] **History Data Loss (clearBought):** `UX_FLOWS.md` correctly notes that prediction needs history data. Currently, Apps Script's `clearBought` simply deletes rows without adding to history. If this isn't patched before Phase 6, we'll lack sufficient prediction data.
- [ ] **Single vs Dual User History Allocation:** `UX_FLOWS.md` recommends two separate accounts (jamie + wife). Since the legacy Google Sheets `History` has no "user" column, all migrated history will either be unassigned (null) or assigned arbitrarily to one user, which could temporarily skew per-user prediction metrics.
