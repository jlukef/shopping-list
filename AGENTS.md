# Shopping List Web App — Codex Instructions

## Project overview

Mobile-first shopping list PWA migrating from GitHub Pages + Apps Script to a VPS-hosted
Python/FastAPI app. The app listens on `127.0.0.1:8770`; local batch-4 work now includes a clean-start
SQLite action backend, with Apps Script retained only as an explicit fallback.

```
docs/           → GitHub Pages frontend (index.html, style.css, app.js)
apps-script/    → Google Apps Script backend (Code.gs) — managed with clasp
```

## Planning / collaboration docs

- `BACKEND_MIGRATION_PLAN.md` — plan for adding login, replacing Google Sheets / Apps Script with a VPS backend/database, receipt OCR, shopping history, and prediction.
- `COLLAB-LOG.md` — shared newest-first log and multi-agent coordination instructions for Claude / Gemini / GPT-5 collaboration. Update it after meaningful work, especially when handing off tasks or tests to another agent.
- `FASTAPI_WRAPPER_RUNBOOK.md` — local/server runbook for the Phase 1 FastAPI wrapper. It explicitly keeps the shopping-list app separate from the tax app environment.

## FastAPI wrapper migration status

The repo now includes a Python/FastAPI app under `src/shopping_list/` that can:

- require login before serving the existing `docs/` frontend;
- store sessions server-side in SQLite for Phase 1;
- serve authenticated `/api?action=...` requests from SQLite, or proxy to Apps Script when explicitly configured;
- bootstrap the seven clean-start shops and editable starter layouts;
- expose public `/healthz` for service checks;
- expose public `/robots.txt` and send `X-Robots-Tag: noindex, nofollow, noarchive` as an app-level fallback;
- run locally on `127.0.0.1:8770` behind Caddy.

Jamie decided not to import the old Sheet data. SQLite starts with an empty list/history. The local
cutover code is not deployed; deployment still requires Jamie's explicit approval.

The shopping-list app must have its own `.venv`, `.env`, service, data directory, and eventual database. The tax app's `.venv` may be used only as a local testing convenience in this workspace, never as the production environment for `sharedlist.co.uk`.

## Key identifiers

| Thing | Value |
|---|---|
| Google Sheet ID | `1EuhJYgxXg0kd8JOt_2GiWnXOjY3-W6CNNG2XHcrs-Gw` |
| Apps Script URL | `https://script.google.com/macros/s/AKfycbxIDminbjuYCfrHqNm7vjhLQhDqwyWXrDEK1kUimmlJ9KwmJGHusRYvcU47HuD5kMr0/exec` |
| Deployment ID | `AKfycbxIDminbjuYCfrHqNm7vjhLQhDqwyWXrDEK1kUimmlJ9KwmJGHusRYvcU47HuD5kMr0` |
| Script ID | `14XSRpTWNY3HjsgYXOn3CNRAyFq9bzcDcfA6PGAfZC5fNrC9D_dCkxYXC` |
| clasp account | `jamie.feaviour@gmail.com` |

## Deployment / VPS access

### Server

| Thing | Value |
|---|---|
| Provider | Hetzner |
| Server name | `ledgerhouse` |
| Public IPv4 | `49.12.212.235` |
| OS | Ubuntu 26.04 LTS |
| SSH user | `jamie` |
| SSH key path on Jamie's Windows machine | `C:\Users\jamie\.ssh\hetzner_taxapp_ed25519` |
| SSH command | `ssh -i C:\Users\jamie\.ssh\hetzner_taxapp_ed25519 jamie@49.12.212.235` |
| Expected project location on server | `/srv/shopping-list` |
| Python environment | Isolated venv (do not reuse `/srv/tax-app` venv) |

Security note: do **not** copy, print, upload, or commit the private key contents. It is okay to store the key path and SSH command in project memory.

### Domain / DNS

- Shopping list domain: `sharedlist.co.uk`
- `www.sharedlist.co.uk` also points to the same server.
- DNS A records point to `49.12.212.235`.

### Caddy / reverse proxy

- Caddy is installed and running on `ledgerhouse`.
- `/etc/caddy/Caddyfile` currently routes `sharedlist.co.uk` and `www.sharedlist.co.uk` to `127.0.0.1:8770`.
- Therefore the shopping list app should listen locally on `127.0.0.1:8770`.
- Do not bind the app directly to public `0.0.0.0` unless there is a specific reason.
- Example deployment files live in `deploy/`; they are review material only and have not been installed on the VPS.

### Crawler / indexing controls

`sharedlist.co.uk/robots.txt` returns:

```txt
User-agent: *
Disallow: /
```

Caddy also sends:

```txt
X-Robots-Tag: noindex, nofollow, noarchive
```

The FastAPI wrapper also serves the same `robots.txt` body and sends the same `X-Robots-Tag` header as a fallback. This discourages indexing but is not security.

### Related app on the same VPS

- `viour.co.uk` is for the tax app.
- `viour.co.uk` routes to `127.0.0.1:8767`.
- Keep the shopping list app separate from `/srv/tax-app`.

## Deploying changes

### Frontend only (docs/)
Just commit and push to the `master` branch. GitHub Pages serves from `/docs` automatically.

### Backend (apps-script/)
```powershell
cd apps-script
clasp push
clasp deploy --deploymentId AKfycbxIDminbjuYCfrHqNm7vjhLQhDqwyWXrDEK1kUimmlJ9KwmJGHusRYvcU47HuD5kMr0 --description "vN"
```
Always push **and** redeploy to the same deployment ID — this keeps the URL stable.

## Architecture decisions

### All API calls are GET, never POST
Apps Script redirects POST requests in a way that breaks CORS. Every mutation (add, delete, update) goes via GET with the payload JSON-encoded in a `?data=` query parameter. The two helpers in `app.js`:
- `api(action, data)` — encodes `data` as `?data=JSON`
- `apiQ(action, queryExtra)` — passes extra params directly in the query string (used for reads that need simple params like `?shop=tesco`)

### Codex API key lives server-side only
The key is stored in Apps Script Script Properties (`PropertiesService.getScriptProperties()`), set via the `saveApiKey` action. It is **never** sent to the browser or stored in `localStorage`. The frontend sends `sortList` to Apps Script, which calls the Codex API server-side and returns sorted items.

### Optimistic UI
`commitAdd(shopId)` inserts the new item directly into the DOM before the API call returns. A spinning `.savingDot` replaces the delete button while saving; on success the dot swaps for the real delete button with the server-assigned ID. On failure the item is removed and an error toast shown.

### Shop ordering
`STATE.shops` is the canonical order for all three list views (create tab columns, shopping tab groups, settings list). Order is saved to `localStorage` under `shopOrder` and restored on load via `applySavedOrder()`. Dragging in any view calls `reorderShops(orderedSubsetIds)` which updates `STATE.shops` in place, persists, and calls `renderAll()`.

### View Transitions (create tab columns)
Toggling a shop chip wraps `renderCreateTab()` in `document.startViewTransition(...)`. Each `.shopSection` has `style="view-transition-name: shop-{id}"` so the browser animates columns to their new grid positions. After the transition `t.finished.then(() => initAllSortables())` re-binds the drag handles on the new DOM.

## Google Sheets schema

These three sections (Google Sheets schema, Apps Script actions, Default data seeded by `setup`)
describe the **legacy Apps Script / Sheets path only** (`SHOPPING_LIST_DATA_BACKEND=apps_script`,
explicit fallback). They are historical/reference material for that code path, not the live default.
The default backend is now SQLite (`SHOPPING_LIST_DATA_BACKEND=sqlite`), which seeds seven different
shops — see "FastAPI wrapper migration status" above and `PHASE2_DATA_MODEL.md` / `src/shopping_list/db.py`
for the current seed (Morrisons, Aldi, Lidl, Butcher, Fruit and Veg Shop, Boots/Superdrug, Other).

| Sheet | Key columns |
|---|---|
| `List` | id, item, quantity, unit, shop, bought, dateAdded, notes, sortOrder |
| `Items` | item, count, lastUsed, category, defaultShop, defaultQty, defaultUnit |
| `Shops` | id, name, emoji, color |
| `StoreLayouts` | shop, department, order, keywords |
| `History` | item, quantity, unit, shop, dateBought |

## Apps Script actions (all via GET `?action=…`)

| Action | Mutates? | Notes |
|---|---|---|
| `setup` | ✓ | Seeds sheets, shops, Tesco/Aldi layouts |
| `getList` | — | Returns all List rows |
| `getShops` | — | Returns all Shops rows |
| `addItem` | ✓ | Adds to List, updates Items dictionary |
| `updateItem` | ✓ | Patches any field (bought, sortOrder, etc.) |
| `deleteItem` | ✓ | Removes from List |
| `clearBought` | ✓ | Removes all bought items |
| `clearList` | ✓ | Empties List sheet |
| `getAutocomplete` | — | `?q=query` — fuzzy match on Items |
| `addShop` / `deleteShop` | ✓ | Shops management |
| `getLayouts` | — | `?shop=id` |
| `saveLayout` | ✓ | Saves aisle order for a shop |
| `sortList` | ✓ | Calls Codex API server-side; falls back to keyword sort |
| `saveApiKey` | ✓ | Stores Codex key in Script Properties |
| `getApiKeySet` | — | Returns `{set: bool, preview: "sk-ant-…xx"}` |

## Frontend state (`STATE` object in app.js)

```javascript
STATE = {
  items:              [],    // all list items from API
  shops:              [],    // shop objects {id, name, color, emoji} — canonical order
  layouts:            {},    // { shopId: [{shop, department, order, keywords}] }
  enabledShops:       [],    // shop IDs currently shown in create tab
  activeShopFilter:   null,  // shopping tab filter (null = All)
  activeAddShop:      null,  // which shop's inline add row is open
  activeAddInputValue:'',    // preserved across re-renders
  acTimeout, acSelected, acShop, loading
}
```

`localStorage` keys:
- `scriptUrl` — Apps Script web app URL (Legacy mode only. Hosted mode defaults to `/api`)
- `defaultShop` — selected default shop ID
- `createEnabledShops` — JSON array of enabled shop IDs
- `shopOrder` — JSON array of all shop IDs in user's drag order

## UI structure

```
#appHeader          fixed top bar (title + refresh + settings)
#tabBar             fixed tab bar (mobile: bottom; desktop ≥768px: below header)
  [List tab]        #createTab
    .createHeader   shop toggle chips + item count + clear-all
    #createSections CSS grid of .shopSection columns (one per enabled shop)
      .shopSection  has drag handle ⠿, items, inline add row
  [Shopping tab]    #shopTab
    .shopToolbar    filter chips + Sort + Clear bought
    #shoppingList   .shopGroup cards (has drag handle ⠿ per group)
  [Receipts tab]    #receiptsTab
    .segmentedControl [Receipts | History]
    #receiptsView   placeholder for future receipt upload/review
    #historyView    placeholder for future shopping history
#settingsModal      bottom sheet (mobile) / centred dialog (desktop)
#sortModal          shop picker for AI sort
```

## CSS conventions

- Mobile-first; desktop overrides at `@media (min-width: 768px)` and `@media (min-width: 1080px)`
- CSS custom properties in `:root` — use them (`var(--primary)`, `var(--radius)`, etc.)
- Shop column grid: `repeat(auto-fill, minmax(300px, 1fr))` at ≥768px
- Wide desktop (≥1080px): shopping list is a 2-col grid; content max-width 1060px

## Default data seeded by `setup` (legacy Apps Script path only — see note above)

**Shops:** Tesco, Sainsbury's, Aldi, Lidl, ASDA, Waitrose, Amazon, Boots, Other

**Layouts:** Tesco (24 departments), Aldi (12 departments)

## Codex API (sorting)

Model: `Codex-haiku-4-5-20251001`  
Called from Apps Script via `UrlFetchApp.fetch`. Key in Script Properties under `CLAUDE_API_KEY`. If the key is absent or the API call fails, `sortByKeywords()` is used as a fallback (matches item names against the shop's `StoreLayouts` keywords).
