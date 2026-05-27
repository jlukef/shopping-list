# Shopping List Web App — Claude Instructions

## Project overview

Mobile-first shopping list PWA. GitHub Pages serves the static frontend; a Google Apps Script web app acts as the backend/API; Google Sheets is the database. No build step — plain HTML/CSS/JS.

```
docs/           → GitHub Pages frontend (index.html, style.css, app.js)
apps-script/    → Google Apps Script backend (Code.gs) — managed with clasp
```

## Key identifiers

| Thing | Value |
|---|---|
| Google Sheet ID | `1EuhJYgxXg0kd8JOt_2GiWnXOjY3-W6CNNG2XHcrs-Gw` |
| Apps Script URL | `https://script.google.com/macros/s/AKfycbxIDminbjuYCfrHqNm7vjhLQhDqwyWXrDEK1kUimmlJ9KwmJGHusRYvcU47HuD5kMr0/exec` |
| Deployment ID | `AKfycbxIDminbjuYCfrHqNm7vjhLQhDqwyWXrDEK1kUimmlJ9KwmJGHusRYvcU47HuD5kMr0` |
| Script ID | `14XSRpTWNY3HjsgYXOn3CNRAyFq9bzcDcfA6PGAfZC5fNrC9D_dCkxYXC` |
| clasp account | `jamie.feaviour@gmail.com` |

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

### Claude API key lives server-side only
The key is stored in Apps Script Script Properties (`PropertiesService.getScriptProperties()`), set via the `saveApiKey` action. It is **never** sent to the browser or stored in `localStorage`. The frontend sends `sortList` to Apps Script, which calls the Claude API server-side and returns sorted items.

### Optimistic UI
`commitAdd(shopId)` inserts the new item directly into the DOM before the API call returns. A spinning `.savingDot` replaces the delete button while saving; on success the dot swaps for the real delete button with the server-assigned ID. On failure the item is removed and an error toast shown.

### Shop ordering
`STATE.shops` is the canonical order for all three list views (create tab columns, shopping tab groups, settings list). Order is saved to `localStorage` under `shopOrder` and restored on load via `applySavedOrder()`. Dragging in any view calls `reorderShops(orderedSubsetIds)` which updates `STATE.shops` in place, persists, and calls `renderAll()`.

### View Transitions (create tab columns)
Toggling a shop chip wraps `renderCreateTab()` in `document.startViewTransition(...)`. Each `.shopSection` has `style="view-transition-name: shop-{id}"` so the browser animates columns to their new grid positions. After the transition `t.finished.then(() => initAllSortables())` re-binds the drag handles on the new DOM.

## Google Sheets schema

| Sheet | Key columns |
|---|---|
| `List` | id, item, quantity, unit, shop, bought, notes, sortOrder |
| `Items` | item, defaultShop, defaultQty, defaultUnit, useCount |
| `Shops` | id, name, emoji, color |
| `StoreLayouts` | shop, department, order, keywords |
| `History` | item, shop, boughtAt |

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
| `sortList` | ✓ | Calls Claude API server-side; falls back to keyword sort |
| `saveApiKey` | ✓ | Stores Claude key in Script Properties |
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
- `scriptUrl` — Apps Script web app URL
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
#settingsModal      bottom sheet (mobile) / centred dialog (desktop)
#sortModal          shop picker for AI sort
```

## CSS conventions

- Mobile-first; desktop overrides at `@media (min-width: 768px)` and `@media (min-width: 1080px)`
- CSS custom properties in `:root` — use them (`var(--primary)`, `var(--radius)`, etc.)
- Shop column grid: `repeat(auto-fill, minmax(300px, 1fr))` at ≥768px
- Wide desktop (≥1080px): shopping list is a 2-col grid; content max-width 1060px

## Default data seeded by `setup`

**Shops:** Tesco, Sainsbury's, Aldi, Lidl, ASDA, Waitrose, Amazon, Boots, Other

**Layouts:** Tesco (24 departments), Aldi (12 departments)

## Claude API (sorting)

Model: `claude-haiku-4-5-20251001`  
Called from Apps Script via `UrlFetchApp.fetch`. Key in Script Properties under `CLAUDE_API_KEY`. If the key is absent or the API call fails, `sortByKeywords()` is used as a fallback (matches item names against the shop's `StoreLayouts` keywords).
