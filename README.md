# Shopping List Web App

A mobile-first shopping list PWA backed by Google Sheets, hosted on GitHub Pages.

## Architecture

```
GitHub Pages (docs/)  ←→  Apps Script Web App  ←→  Google Sheets
     HTML/CSS/JS               Code.gs              (5 sheets)
                                  ↕
                          Claude API (server-side,
                          key in Script Properties)
```

## Sheets layout

| Sheet | Purpose |
|---|---|
| `List` | Current shopping list |
| `Items` | Autocomplete master dictionary |
| `Shops` | Configured shops |
| `StoreLayouts` | Aisle order per shop for AI sorting |
| `History` | Past bought items |

**Sheet ID:** `1EuhJYgxXg0kd8JOt_2GiWnXOjY3-W6CNNG2XHcrs-Gw`

## Apps Script

**Script URL:**
```
https://script.google.com/macros/s/AKfycbxIDminbjuYCfrHqNm7vjhLQhDqwyWXrDEK1kUimmlJ9KwmJGHusRYvcU47HuD5kMr0/exec
```

### Deploying changes

```bash
cd apps-script
clasp push
clasp deploy --deploymentId AKfycbxIDminbjuYCfrHqNm7vjhLQhDqwyWXrDEK1kUimmlJ9KwmJGHusRYvcU47HuD5kMr0 --description "vN"
```

## GitHub Pages setup

1. Create a new GitHub repo (e.g. `jamie-feaviour/shopping-list`)
2. Push this folder: `git remote add origin <url> && git push -u origin master`
3. In GitHub repo Settings → Pages → Source: **Deploy from branch** → branch `master` → folder `/docs`
4. Your app will be at `https://jamie-feaviour.github.io/shopping-list`

## First-time app setup

1. Open the app URL
2. Tap ⚙ Settings
3. Paste the Apps Script URL above into **Apps Script URL**
4. Tap **Run setup** — creates all Sheets tabs + seeds default shops & Tesco/Aldi layouts
5. (Optional) Paste your Claude API key for AI aisle sorting, tap **Save settings**

## Features

- **List tab** — per-shop columns; enable shops with toggle chips; click to add inline
- **Shopping tab** — items grouped by shop; tap to mark bought; progress bar
- **AI sort** — tap 🤖 Sort → Claude sorts items into aisle order (server-side)
- **Keyword fallback** — works without a Claude key using built-in keyword matching
- **Store layout editor** — Settings → Store Layout Editor — customise any shop's aisle order
- **Shop management** — add/remove shops with colour + emoji; drag to reorder
- **Drag-and-drop** — reorder shops in any view; order persists across sessions

## Claude API key

Used for AI aisle sorting. Stored securely in Google Apps Script Script Properties — **never** in the browser or localStorage. Enter it in Settings → Claude API Key.

Get a key at https://console.anthropic.com/settings/keys
