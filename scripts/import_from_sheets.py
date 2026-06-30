"""CLI: import Google Sheets data into the Phase 2 SQLite store.

Read-only against the Sheet (never writes Google data). Writes a local SQLite DB.

Two data sources, mixable:
  --apps-script-url URL   Fetch the live-API subset (shops, list, layouts-per-shop).
                          Defaults to $SHOPPING_LIST_APPS_SCRIPT_URL.
  --export-dir DIR        Read JSON exports for anything the live API doesn't expose
                          (items.json, history.json, and optionally shops/list/layouts).

The Apps Script read API does not expose the full Items dictionary or History sheet,
so those must come from a manual export (or future read endpoints). Each file is a
JSON array matching the documented shapes in importer.py.

Examples:
  python -m scripts.import_from_sheets --apps-script-url https://... --db data/shopping_list.sqlite
  python -m scripts.import_from_sheets --export-dir export/ --db data/shopping_list.sqlite
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx
from sqlmodel import Session

from src.shopping_list import importer
from src.shopping_list.db import DEFAULT_DB_PATH, get_engine, init_db

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
except ImportError:
    pass


def _fetch(url: str, action: str, **params) -> object | None:
    try:
        resp = httpx.get(url, params={"action": action, **params}, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001 — best-effort fetch, report and move on
        print(f"  ! fetch {action} failed: {exc}", file=sys.stderr)
        return None


def _load_export(export_dir: Path, name: str) -> list | None:
    path = export_dir / f"{name}.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    # Accept either a bare array or {"<name>": [...]} / {"items": [...]} envelopes.
    if isinstance(data, dict):
        for key in (name, "items", "rows", "data"):
            if isinstance(data.get(key), list):
                return data[key]
        return None
    return data


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Import Google Sheets data into SQLite.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--apps-script-url", default=os.environ.get("SHOPPING_LIST_APPS_SCRIPT_URL", ""))
    parser.add_argument("--export-dir", default="")
    parser.add_argument("--no-fetch", action="store_true", help="Skip the live API; use --export-dir only.")
    args = parser.parse_args(argv)

    if not args.apps_script_url and not args.export_dir:
        parser.error(
            "no import source configured; pass --apps-script-url, set "
            "SHOPPING_LIST_APPS_SCRIPT_URL, or pass --export-dir"
        )

    export_dir = Path(args.export_dir) if args.export_dir else None
    source_counts: dict[str, int] = {}

    # Schema only — do NOT seed default shops here. A migration must mirror the
    # Sheet exactly; default-shop seeding is for a greenfield install (db.bootstrap).
    print(f"Creating schema at {args.db} (importing real shops, no default seeding) …")
    engine = get_engine(args.db)
    init_db(engine)

    shops = layouts_by_shop = list_rows = None
    items = history = None

    # 1) Live API subset (best-effort).
    if args.apps_script_url and not args.no_fetch:
        print(f"Fetching live subset from {args.apps_script_url} …")
        shops_res = _fetch(args.apps_script_url, "getShops")
        shops = (shops_res or {}).get("shops") if isinstance(shops_res, dict) else None
        list_res = _fetch(args.apps_script_url, "getList")
        list_rows = (list_res or {}).get("items") if isinstance(list_res, dict) else None
        layouts_by_shop = {}
        for sh in (shops or []):
            lr = _fetch(args.apps_script_url, "getLayouts", shop=sh["id"])
            rows = (lr or {}).get("layouts") if isinstance(lr, dict) else None
            if rows:
                layouts_by_shop[sh["id"]] = rows

    # 2) Export files override / fill gaps (items + history are export-only today).
    if export_dir:
        print(f"Reading exports from {export_dir} …")
        shops = _load_export(export_dir, "shops") or shops
        list_rows = _load_export(export_dir, "list") or list_rows
        items = _load_export(export_dir, "items")
        history = _load_export(export_dir, "history")
        layout_rows = _load_export(export_dir, "layouts")
        if layout_rows is not None:
            layouts_by_shop = {}
            for row in layout_rows:
                shop_id = row.get("shop")
                if shop_id:
                    layouts_by_shop.setdefault(shop_id, []).append(row)

    try:
        with Session(engine) as session:
            if shops:
                n = importer.import_shops(session, shops)
                source_counts["shops"] = len(shops)
                print(f"  shops: imported {n}")
            if items:
                n = importer.import_items(session, items)
                source_counts["items"] = len(items)
                print(f"  items: imported {n}")
            if list_rows is not None:
                n = importer.import_list(session, list_rows)
                source_counts["list"] = len(list_rows)
                print(f"  list: imported {n}")
            if layouts_by_shop:
                total = sum(importer.import_layouts(session, sid, rows)
                            for sid, rows in layouts_by_shop.items())
                print(f"  layouts: imported {total} departments across {len(layouts_by_shop)} shops")
            if history:
                res = importer.import_history(session, history)
                source_counts["history"] = len(history)
                print(f"  history: {res}")

            report = importer.verify_import(session, source_counts)
    finally:
        engine.dispose()

    print("\nVerification report:")
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
