# Shopping List Web App

A mobile-first shopping list PWA backed by Google Sheets, hosted on GitHub Pages.

## Architecture

```
GitHub Pages (docs/)  ←→  Apps Script Web App  ←→  Google Sheets
     HTML/CSS/JS               Code.gs              (5 sheets)
        ↕
   Gemini API (client-side, key in localStorage)
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
clasp deploy --description "v2" # creates a new deployment
```

Or redeploy over the existing version:
```bash
clasp deploy --deploymentId AKfycbxIDminbjuYCfrHqNm7vjhLQhDqwyWXrDEK1kUimmlJ9KwmJGHusRYvcU47HuD5kMr0 --description "v2"
```

## GitHub Pages setup

1. Create a new GitHub repo (e.g. `jamie-feaviour/shopping-list`)
2. Push this folder: `git init && git add . && git commit -m "Initial" && git remote add origin <url> && git push -u origin main`
3. In GitHub repo Settings → Pages → Source: **Deploy from branch** → branch `main` → folder `/docs`
4. Your app will be at `https://jamie-feaviour.github.io/shopping-list`

## First-time app setup

1. Open the app URL
2. Tap ⚙ Settings
3. Paste the Apps Script URL above into **Apps Script URL**
4. (Optional) Paste your Gemini API key for AI aisle sorting
5. Tap **Run setup** — this creates all Sheets tabs with headers + seeds default shops & Tesco/Aldi layouts
6. Tap **Save settings**

## Features

- **List tab** — add items with qty, unit, shop; full autocomplete from past items
- **Shopping tab** — items grouped by shop; tap to mark bought; progress bar
- **AI sort** — tap 🤖 Sort → pick shop → Gemini sorts items into aisle order
- **Keyword fallback** — works without AI key using built-in aisle keyword matching
- **Store layout editor** — Settings → Store Layout Editor — customise any shop's aisle order
- **Shop management** — add/remove shops with colour + emoji

## Gemini API key

Stored in browser `localStorage` only — never sent to GitHub or the server.  
Get a free key at https://aistudio.google.com/app/apikey
