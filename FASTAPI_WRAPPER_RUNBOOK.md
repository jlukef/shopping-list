# FastAPI Shopping List Runbook

This is the local/server runbook for the shopping-list FastAPI wrapper.

Status: the authenticated wrapper and clean-start SQLite action backend are live at
`sharedlist.co.uk`; Apps Script remains an explicit configuration fallback. The initial VPS
deployment completed on 2026-06-30. Multi-provider receipt AI and transient camera upload were
deployed on 2026-07-01 at commit `1407c12`.

The live app uses `/srv/shopping-list`, its own `.venv`, `.env`, data directory, systemd service,
and port `127.0.0.1:8770`. Caddy routes the public HTTPS domain to that port.

## Separation from the tax app

The shopping-list app and tax app share the VPS, but they must remain separate projects and separate runtime environments.

Expected VPS layout:

```text
/srv/tax-app
  .venv/
  .env
  tax app code/data
  listens on 127.0.0.1:8767

/srv/shopping-list
  .venv/
  .env
  shopping app code/data
  listens on 127.0.0.1:8770
```

Caddy is the shared front door:

```text
viour.co.uk       -> 127.0.0.1:8767
sharedlist.co.uk  -> 127.0.0.1:8770
```

Do not reuse the tax app `.venv` in production. It was only used locally as a convenient way to run tests because it already had FastAPI installed.

## Local setup

From `C:\Users\jamie\Desktop\Documents\Claude\ShoppingListWebApp`:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Generate password hashes:

```powershell
.\.venv\Scripts\python.exe -m src.shopping_list.auth hash-password
```

Put generated hashes in `.env`:

```text
SHOPPING_LIST_USERS=jamie:pbkdf2_sha256$...;wife:pbkdf2_sha256$...
```

Use the clean SQLite backend and separate application/session files:

```text
SHOPPING_LIST_DATA_BACKEND=sqlite
SHOPPING_LIST_DB=data/shopping_list.sqlite
SHOPPING_LIST_SESSION_DB=data/sessions.sqlite
```

Set `SHOPPING_LIST_DATA_BACKEND=apps_script` only for explicit fallback comparison.

For local HTTP testing, set:

```text
SHOPPING_LIST_COOKIE_SECURE=false
```

For production behind HTTPS/Caddy, set:

```text
SHOPPING_LIST_COOKIE_SECURE=true
```

## Run locally

```powershell
.\.venv\Scripts\python.exe -m uvicorn src.shopping_list.app:app --host 127.0.0.1 --port 8770
```

Then open:

```text
http://127.0.0.1:8770/
```

Health check:

```text
http://127.0.0.1:8770/healthz
```

Crawler fallback check:

```text
http://127.0.0.1:8770/robots.txt
```

Expected body:

```text
User-agent: *
Disallow: /
```

The app also sends `X-Robots-Tag: noindex, nofollow, noarchive` as a fallback.
Caddy should still send the same header in production. This discourages indexing
but is not security; login is the security boundary.

## What the wrapper currently does

- Requires login before serving `/`.
- Requires login before serving static frontend assets such as `/app.js` and `/style.css`.
- Stores sessions server-side in a dedicated SQLite session file.
- Implements authenticated `/api?action=...` list/shop/layout/history actions against a separate
  SQLite application database.
- Creates a clean database with seven editable seeded shops/layouts on first start.
- Can proxy `/api?...` to Apps Script only when `SHOPPING_LIST_DATA_BACKEND=apps_script` is selected.
- Serves public `/healthz` for service checks.
- Serves public `/robots.txt` and adds `X-Robots-Tag` headers as a fallback crawler discouragement layer.
- Preserves quantity, unit, shop and UTC purchase time when an item becomes bought.

## Test commands

The repo now has its own dedicated `.venv` (created from `requirements.txt`). Use it for all
local verification — do **not** reuse the tax app venv:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m compileall src tests
node --check docs\app.js
```

The exact test count changes as parallel work lands; record the command result in `COLLAB-LOG.md`.

## Production deployment shape (live)

The repo now contains example deployment files:

```text
deploy/shopping-list.service.example
deploy/Caddyfile.sharedlist.example
```

The systemd example was used for the live `shopping-list.service`; the Caddy configuration already
contained the matching `sharedlist.co.uk` route when the initial deployment was performed.

For a normal approved upgrade:

```bash
cd /srv/shopping-list
git pull --ff-only origin master
./.venv/bin/python -m pip install -r requirements.txt
sudo systemctl restart shopping-list.service
curl -fsS http://127.0.0.1:8770/healthz
```

Do not put secrets in Git. Do not commit `.env`, SQLite session DBs, uploaded receipts, or private keys.

## First server deploy checklist (completed 2026-06-30)

Retained as the audit/checklist for rebuilding the service if ever needed:

1. Confirm the repo is committed/pushed and the server should use that revision.
2. SSH to `ledgerhouse` using Jamie's documented key path; do not copy or print key contents.
3. Ensure `/srv/shopping-list` exists and is separate from `/srv/tax-app`.
4. Create `/srv/shopping-list/.venv` and install `requirements.txt`.
5. Create `/srv/shopping-list/.env` from `.env.example`; fill real values on the server only.
6. Generate password hashes with `python -m src.shopping_list.auth hash-password`.
7. Set `SHOPPING_LIST_COOKIE_SECURE=true` for production HTTPS behind Caddy.
8. Set `SHOPPING_LIST_DATA_BACKEND=sqlite` and keep application/session DB paths separate.
9. Install a reviewed systemd unit based on `deploy/shopping-list.service.example`.
10. Merge a reviewed Caddy block based on `deploy/Caddyfile.sharedlist.example`.
11. Start/restart only the shopping-list service and reload Caddy.
12. Verify:
    - `curl http://127.0.0.1:8770/healthz` returns `{"ok":true}` locally on the VPS.
    - `https://sharedlist.co.uk/` redirects/shows login before the app.
    - Login works for both household users.
    - `/api?action=getList` is not accessible logged out.
    - Logged in, `/api?action=getShops` returns the seven clean-start shops and `getList` starts empty.
    - `/robots.txt` returns `Disallow: /`.
    - Response headers include `X-Robots-Tag: noindex, nofollow, noarchive`.
