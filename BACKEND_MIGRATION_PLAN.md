# Shopping List Backend Migration Plan

Planning notes for moving the shopping list app from Google Sheets / Apps Script to a VPS-hosted backend with login, database-backed history, receipt OCR, and prediction.

## Current state

The app is currently a mobile-first static PWA:

```text
GitHub Pages / docs/
  -> Google Apps Script API
  -> Google Sheets
```

Current data lives in a Google Drive spreadsheet:

- `List` — current shopping list rows.
- `Items` — autocomplete / master item dictionary.
- `Shops` — configured shops.
- `StoreLayouts` — aisle / department ordering and keywords.
- `History` — past bought items.

The frontend stores a few preferences in `localStorage`, including:

- Apps Script URL.
- Default shop.
- Enabled shops in the create view.
- Shop display order.

The new documented deployment target is:

```text
sharedlist.co.uk
  -> Caddy on ledgerhouse
  -> 127.0.0.1:8770
```

Do not bind the shopping list app directly to public `0.0.0.0` unless there is a specific reason.

## Why migrate

Google Sheets was a good lightweight first database, but the next features need a real backend:

- Login for Jamie and his wife.
- Server-enforced access control.
- Receipt photo uploads.
- OCR / AI extraction from receipt images.
- Review and correction of extracted receipt items.
- Durable shopping-trip history.
- Future list prediction from past shopping behaviour.

Frontend-only login is not security. Access control should be enforced by the server before serving the app or API data.

## Recommended target architecture

Recommended first target:

```text
Browser
  -> Caddy / HTTPS / noindex headers
  -> Python/FastAPI app on 127.0.0.1:8770
  -> SQLite via SQLModel/SQLAlchemy
  -> local receipt image storage under /srv/shopping-list/data/uploads
```

Codex decision, 2026-06-30: use Python/FastAPI for the backend rather than Node/Express. This repo is currently plain static JS, but Jamie's adjacent tax app already uses FastAPI, Uvicorn, Jinja templates, static mounting, receipt/camera flows, and Python tests. Matching that ecosystem should make review, testing, deployment, and later receipt/OCR work easier than introducing a second backend stack.

Jamie decided on 2026-06-30 to use SQLite for the application database. With two users and one VPS,
it keeps deployment, backups, and recovery simple. SQLModel/SQLAlchemy keeps a later PostgreSQL move
possible if concurrency or workload eventually justifies it.

The existing `docs/` frontend should initially be reused as much as possible. Avoid a full UI rewrite until the backend migration is stable.

## Authentication approach

For two users, keep auth simple:

- Email or username plus password.
- Passwords stored with a strong password hash.
- Secure HTTP-only session cookie.
- Sessions stored in the database.
- Logout endpoint that deletes/revokes the session.

Suggested tables:

```text
users
sessions
```

Avoid storing secrets in source control. Runtime secrets should live in an environment file on the server, such as `/srv/shopping-list/.env`, with restrictive permissions.

## Data model sketch

This is a planning schema, not a final migration script.

```text
users
- id
- username
- password_hash
- created_at
- updated_at

sessions
- id
- user_id
- token_hash
- expires_at
- created_at

shops
- id
- name
- emoji
- color
- active
- sort_order
- created_at
- updated_at

items
- id
- canonical_name
- default_shop_id
- default_quantity
- default_unit
- use_count
- last_used_at
- created_at
- updated_at

shopping_list_items
- id
- item_id nullable
- name
- quantity
- unit
- shop_id
- bought
- notes
- sort_order
- added_by_user_id
- bought_by_user_id nullable
- created_at
- bought_at nullable
- updated_at

store_layout_departments
- id
- shop_id
- name
- sort_order

store_layout_keywords
- id
- department_id
- keyword

shopping_trips
- id
- shop_id nullable
- started_at
- completed_at
- source
- notes

shopping_trip_items
- id
- shopping_trip_id
- item_id nullable
- name
- quantity
- unit
- shop_id nullable
- price nullable
- bought_at

receipts
- id
- shopping_trip_id nullable
- uploaded_by_user_id
- shop_id nullable
- original_filename
- stored_path
- status
- ocr_text nullable
- extracted_at nullable
- created_at
- updated_at

receipt_items
- id
- receipt_id
- item_id nullable
- raw_text
- name
- quantity nullable
- unit nullable
- price nullable
- confidence nullable
- accepted
- created_at
- updated_at
```

## API shape

Status note, 2026-06-30: the REST-style sketch below was the original aspiration. The actual
batch-4 SQLite cutover deliberately **kept the legacy `GET /api?action=...` contract** (same
action names, same JSON `data`/query-param shapes `docs/app.js` already used) so the frontend and
backend could be built independently without a protocol redesign mid-migration. See
`src/shopping_list/sqlite_api.py` and `FASTAPI_WRAPPER_RUNBOOK.md` for what is actually implemented.
A REST/CSRF cleanup remains future work, not yet started.

The frontend should eventually stop calling Apps Script directly and call the local app API:

```text
GET    /api/bootstrap
GET    /api/list
POST   /api/list/items
PATCH  /api/list/items/:id
DELETE /api/list/items/:id
POST   /api/list/clear-bought
POST   /api/list/clear

GET    /api/shops
POST   /api/shops
PATCH  /api/shops/:id
DELETE /api/shops/:id

GET    /api/autocomplete?q=
GET    /api/layouts/:shopId
PUT    /api/layouts/:shopId

POST   /api/auth/login
POST   /api/auth/logout
GET    /api/auth/me

POST   /api/receipts
GET    /api/receipts
GET    /api/receipts/:id
PATCH  /api/receipts/:id/items/:itemId
POST   /api/receipts/:id/accept

GET    /api/history
GET    /api/suggestions
```

During migration, it is acceptable to add a temporary proxy layer:

```text
Frontend -> /api/... -> Apps Script
```

That allows login and server hosting to land before the database replacement is complete.

## Receipt OCR / AI extraction flow

Target flow:

```text
User uploads or takes receipt photo
  -> server stores image
  -> OCR / vision extraction runs
  -> raw text and structured candidate items are saved
  -> user reviews and corrects extracted items
  -> accepted items become shopping history
  -> history feeds future suggestions
```

Important design point: receipt OCR must include a review screen. Receipts are messy, shop-specific, and OCR will sometimes invent, miss, or mangle items.

Possible OCR/extraction options:

- Server-side OCR library for basic text extraction.
- Vision-capable AI model for structured extraction from receipt images.
- Hybrid approach: OCR text first, AI cleanup second.

For privacy and cost control, store the original receipt image locally and only send image/text to an external AI service if Jamie explicitly chooses that route.

## Prediction strategy

Start deliberately simple:

- Track items bought by date/shop.
- Suggest frequently bought items not bought recently.
- Use item cadence, e.g. milk every 4-7 days, coffee every 20-40 days.
- Prefer the user's normal shop for each item.
- Let rejected suggestions decay in confidence.

Avoid trying to build a clever recommender before the history data is reliable. A boring cadence-based predictor will probably be useful faster.

## Migration phases

### Phase 1 — Server wrapper and login

- Add a small FastAPI backend app on port `127.0.0.1:8770`.
- Serve the existing `docs/` frontend through the backend.
- Add login page, secure sessions, and logout.
- Keep Apps Script as the data backend temporarily.
- Hide direct Apps Script URL from normal UI if possible.
- Expose `/healthz` for service checks.
- Serve `/robots.txt` and `X-Robots-Tag` as app-level indexing discouragement fallback; Caddy should still do this too.

Exit criteria:

- `sharedlist.co.uk` requires login before showing the app.
- Existing list features still work.
- App listens only on `127.0.0.1:8770`.

#### Codex Phase 1 implementation plan

Recommended Phase 1 goal: land server-enforced login and hosting without changing the data store yet.

Scope:

- Use FastAPI + Uvicorn.
- Serve the existing `docs/` frontend after authentication.
- Add a simple login page and logout route.
- Store sessions server-side in the Phase 1 SQLite session database.
- Keep current Apps Script API calls working during the first wrapper slice.
- Prefer adding a `/api/...` proxy to Apps Script before deploying publicly, so authenticated users call same-origin APIs and the frontend no longer needs the Apps Script URL in normal settings.
- Do not implement receipt OCR, prediction, or the full application-database migration in Phase 1.

Proposed files to add before implementation:

```text
.gitignore
.env.example
requirements.txt
src/shopping_list/__init__.py
src/shopping_list/app.py
src/shopping_list/auth.py
src/shopping_list/config.py
src/shopping_list/apps_script_proxy.py
templates/login.html
tests/test_auth.py
tests/test_app_routes.py
```

Possible later Phase 1 deployment files, added only when Jamie is ready to deploy:

```text
deploy/shopping-list.service.example
deploy/Caddyfile.sharedlist.example
```

These example files now exist locally as review material. They have not been installed on the VPS and should not be treated as an executed deployment.

Runtime/server-only files that must not be committed:

```text
.env
data/
*.sqlite
*.sqlite3
.venv/
```

Suggested local run command:

```powershell
.\.venv\Scripts\python.exe -m uvicorn src.shopping_list.app:app --host 127.0.0.1 --port 8770
```

Suggested production run shape:

```bash
python -m uvicorn src.shopping_list.app:app --host 127.0.0.1 --port 8770
```

Open Phase 1 decisions for Jamie/Codex before coding:

- Login usernames: Codex recommendation is separate `jamie` and wife accounts from day one, not a shared household login. This supports `added_by_user_id` / `bought_by_user_id` later without adding UX complexity.
- Password setup: Codex recommendation is a one-off CLI/admin command for creating users, not public signup and not first-startup magic.
- Session lifetime: Codex recommendation is convenience-first, around 30 days, with server-side expiry and explicit logout.
- Apps Script proxy: Codex recommendation is to include the `/api/...` proxy before public VPS deployment, so browser traffic is same-origin and authenticated even while Apps Script remains the data store.

#### Codex review notes from Claude/Gemini handoffs

Claude's `UX_FLOWS.md` is accepted as the product direction for future UX work. It stays correctly out of auth crypto/deployment decisions.

Immediate decisions from Claude's open questions:

- Use one login per person, not a shared household login, unless Jamie says otherwise.
- Keep localStorage device preferences in Phase 1; move preferences server-side after the DB foundation is in place.
- Treat OCR as potentially asynchronous from the beginning, even if the first implementation is synchronous.
- Canonicalisation should run server-side once the DB exists; the frontend can suggest/confirm but should not own identity rules.
- Suggestions should be household-global in the UI, with room for per-user signals underneath.
- Accept Claude's history-placement direction: use a future Receipts tab with a segmented `[Receipts | History]` control, plus an explicit per-item `... -> History` entry point rather than relying on long-press discovery.
- Accept Claude's clear-bought direction for the DB-backed version: clear-bought should archive/move bought rows into history before removing them from the active list. The UI copy should make that clear.
- Receipt-image retention default: keep uploaded receipt images for audit/review initially, stored locally under the app data directory; add configurable cleanup later if storage/privacy becomes a concern.

Gemini's `gemini_checklist_review.md` is accepted as a useful checklist handoff. It correctly spots the main migration issues:

- `List.item` maps to `shopping_list_items.name`, with nullable `item_id` while canonical matching is imperfect.
- `Items.item` maps to `items.canonical_name`.
- Legacy `History` rows need synthetic `shopping_trips` or a nullable/dummy trip strategy.
- `StoreLayouts.keywords` must be split into individual keyword rows.
- Add a future `suggestion_feedback` concept/table before Phase 6 so dismissed suggestions can decay rather than reappearing immediately.
- Add a future first-login/user-onboarding state if the welcome strip needs to follow the user across devices. This is not required for Phase 1.
- Legacy `History` rows have no user column. During migration, leave user attribution null for legacy history rather than assigning it arbitrarily to Jamie or his wife.
- Phase 1 sessions remain disposable. When auth is folded into the application database, existing
  sessions may simply expire/log users out rather than being migrated.

Current Apps Script history nuance:

- `updateItem(... bought: true ...)` calls `addToHistory(...)`.
- `clearBought()` deletes bought rows but does not itself add history rows.
- Therefore migration should not assume every cleared/deleted item has a history row. Future DB-backed clear-bought should explicitly write one history/trip record per bought item before removing it from the active list.

### Phase 2 — Database foundation

- Add the SQLite application database through SQLModel/SQLAlchemy.
- Add migrations.
- Add user/session tables.
- Add shopping list, shops, items, layout, and history tables.
- Add seed data for default shops/layouts.

Exit criteria:

- Backend can start with an empty database and create required schema.
- Tests cover auth and key CRUD endpoints.

### Phase 3 — Fresh database seed

- Jamie decided on 2026-06-30 not to retain the current Google Sheets data.
- Start with an empty SQLite list, item dictionary, and history.
- Seed editable defaults for Morrisons, Aldi, Lidl, Butcher, Fruit and Veg Shop,
  Boots/Superdrug, and Other, with sensible guessed layouts and keyword order.
- Keep the corrected importer only as an optional development utility; it is not a cutover prerequisite.

Exit criteria:

- A fresh database creates the full schema and exact default shop order idempotently.
- Every default shop has an editable starter layout with useful keyword coverage.
- The active list and history begin empty.

### Phase 4 — Replace Apps Script API

- Change frontend API helpers to call local `/api/...`.
- Implement equivalent backend endpoints.
- Remove settings UI that asks for an Apps Script URL, or hide it behind a legacy/admin section.
- Keep Apps Script code in the repo temporarily as migration history.
- Keep an explicit Apps Script fallback during local verification, but do not import its data.

Exit criteria:

- App works without Apps Script.
- Current list, autocomplete, shops, layouts, sorting, and history use SQLite.

### Phase 5 — Receipt upload and review

- Add receipt upload UI.
- Store images server-side.
- Run OCR/extraction job.
- Add receipt review/correction UI.
- Save accepted receipt rows into history.

Exit criteria:

- A receipt can be uploaded, reviewed, corrected, and committed to history.

### Phase 6 — Suggestions / prediction

- Add suggestions endpoint.
- Show suggested items in the UI.
- Allow accept/reject.
- Track feedback for future weighting.

Exit criteria:

- App can suggest likely needed items from history.
- User can accept suggestions into the current list.

## Future idea: native Android wrapper app + home-screen widget (not started)

Context, 2026-07-01: investigated why Chrome on Jamie's Pixel 10 Pro XL (Chrome 149) never
offers "Add to Home screen" / "Install app" for `sharedlist.co.uk` (or, he reports, for other
sites either). Along the way, found and fixed a real bug — `<link rel="manifest">` fetches omit
cookies by default, so Chrome was silently hitting the login wall instead of reading the
manifest; fixed with `crossorigin="use-credentials"` (shipped). Confirmed via server logs the
manifest now loads correctly (200, not 303), and ruled out engagement-timing, Chrome policies
(`chrome://policy` shows none), and launcher incompatibility (stock Pixel Launcher). Even so, the
menu still doesn't show the option — not even the manifest-free basic bookmark shortcut — which
points at a device/Chrome-version-specific quirk on very recent hardware, outside what's
diagnosable remotely. Jamie proposed sidestepping it entirely with a native wrapper.

### Wrapper app

A thin native Android shell: one Activity, one `WebView` pointed at `https://sharedlist.co.uk`,
nothing else. This is a well-understood, simple pattern (well under 100 lines), and completely
sidesteps the installability investigation above since it's not a PWA at all.

Required WebView settings:

- `javaScriptEnabled = true` (the app is fully JS-driven).
- `domStorageEnabled = true` — **required**, not optional: the frontend uses `localStorage` for
  `shopOrder`, `defaultShop`, `createEnabledShops`, and legacy `scriptUrl`. Without this, shop
  ordering/preferences silently break.
- `CookieManager` with `setAcceptCookie(true)`, so the login session persists across app restarts.
- `INTERNET` permission in the manifest.
- Override back-button handling: `webView.goBack()` when `webView.canGoBack()`, else default.
- Reuse the existing generated icons (`docs/icons/icon-192.png`, `docs/icons/icon-512.png`) as the
  launcher icon instead of designing new assets.
- Tint the status bar to `#1a1a2e` to match the web app's theme color for a consistent look.

Process: Jamie creates an empty "Empty Views Activity" project in Android Studio and hands over
the project folder path. From there it's a handful of file edits (manifest, one layout, one
Activity class, icon resources). The project's `gradlew` wrapper should allow building
(`gradlew assembleDebug`) and installing (`adb install`, already working from this session) from
the command line, without needing Android Studio open again.

Naming: recommended **"SharedList"** (matches the `sharedlist.co.uk` domain, distinct from any
generic "Shopping List" app on the device). Alternative: **"Shop List"** (matches the existing
PWA manifest `short_name` exactly, for branding consistency).

### Follow-on: home-screen widget for adding items

Discussed as a natural next step once the wrapper app exists, not a replacement for it.
Architectural constraint: Android home-screen widgets are built from `RemoteViews`, which
**cannot embed a WebView** — so the widget can't just "run the website" the way the wrapper app
does. It needs a small slice of native (Kotlin/Java) code that calls the existing
`GET /api?action=addItem&data=...` endpoint directly over HTTP, reusing the session cookie the
wrapper app's `CookieManager` already holds. **No backend changes needed** — the legacy GET
action contract is already widget-friendly.

Two design tiers, not yet decided which to build:

- **Simple ("quick-add" buttons):** a small number of buttons for frequently-added items (e.g.
  drawn from the autocomplete/use-count data), each tap fires a background HTTP call to add that
  item with no app-opening required. Straightforward to build.
- **Ambitious (free-text entry):** type any item name directly into the widget and tap Add.
  Meaningfully fussier — Android's support for real text input inside home-screen widgets has been
  inconsistent/limited across versions — so this needs more validation before committing to it.

Status: **not started.** Revisit once Jamie provides the empty Android Studio project. Build the
wrapper app first; treat the widget as a separate follow-on step after that's working.

## Deployment workflow

Recommended simple workflow:

```text
Edit locally
  -> test locally
  -> git commit
  -> git push
  -> SSH to ledgerhouse
  -> cd /srv/shopping-list
  -> git pull
  -> install/update dependencies if needed
  -> run migrations if needed
  -> restart shopping-list systemd service
```

Keep GitHub as the source of truth. Avoid editing application files directly on the server except for server-only config such as `.env`, Caddy config, and systemd unit files.

**Important:** The shopping list app must use its own isolated Python virtual environment. Do not reuse the `viour.co.uk` tax app venv in production.

## Agent task lanes

Useful ways to split work between agents:

- Architecture agent: refine schema, API boundaries, and migration phases.
- Backend agent: implement server, auth, migrations, database CRUD, and tests.
- Frontend agent: adapt existing UI to login, `/api` calls, receipt upload, review screen, and suggestions.
- Data migration agent: write export/import scripts and verify row counts/sample records.
- OCR agent: prototype receipt extraction and define review data structures.
- Deployment agent: write systemd/Caddy/deploy notes and ensure the app binds to `127.0.0.1:8770`.
- QA agent: test locally, verify login boundaries, and document manual test steps.

Every agent should update `COLLAB-LOG.md` after meaningful work.

## Open questions

- What measured workload or concurrency threshold would justify a future PostgreSQL move?
- Should receipt images be kept forever, deleted after extraction, or configurable?
- Should OCR/AI run locally, via an external API, or both?
- Should list suggestions be global to the household or personalised per user?
- How much of the existing Apps Script admin/settings UI should remain during transition?
