"""Idempotent import of Google Sheets data into the Phase 2 SQLite store.

Works on the plain dict/list shapes the Apps Script backend returns (or a saved
JSON export). Pure functions take a Session so they are easy to unit-test without
a network or a real Sheet. Nothing here mutates Google Sheets — it only reads the
provided data and writes SQLite.

Data shapes (as returned by the Apps Script actions):
- shops:   [{"id","name","emoji","color"}]
- list:    [{"id","item","quantity","unit","shop","bought","dateAdded","notes","sortOrder"}]
- items:   [{"item","count","lastUsed","category","defaultShop","defaultQty","defaultUnit"}]
- layouts: [{"shop","department","order","keywords"}]   (per shop)
- history: [{"item","quantity","unit","shop","dateBought"}]
"""

from __future__ import annotations

import re
import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import text
from sqlmodel import Session, select

from . import models
from .db import now_iso


# ── helpers ──────────────────────────────────────────────────────────────────
def normalize_name(raw: str) -> str:
    """Canonical form for matching: lowercased, trimmed, collapsed whitespace."""
    return re.sub(r"\s+", " ", (raw or "").strip().lower())


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "y", "bought"}


def _optional_float(value) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    return float(value)


def _first_present(row: dict, *keys: str):
    for key in keys:
        if key in row:
            return row[key]
    return None


def parse_timestamp(value) -> str | None:
    """Parse a Sheets date/datetime into an ISO-8601 UTC string, or None if unparseable."""
    if value is None or str(value).strip() == "":
        return None
    raw = str(value).strip()
    # Try ISO first (handles trailing Z).
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        except ValueError:
            continue
    return None


# ── shops ────────────────────────────────────────────────────────────────────
def import_shops(session: Session, shops: list[dict]) -> int:
    """Upsert shops by slug id. Returns rows touched."""
    touched = 0
    for order, row in enumerate(shops):
        slug = str(row.get("id") or normalize_name(row.get("name", ""))).strip()
        if not slug:
            continue
        existing = session.get(models.Shop, slug)
        ts = now_iso()
        if existing:
            existing.name = row.get("name", existing.name)
            existing.emoji = row.get("emoji", existing.emoji)
            existing.color = row.get("color", existing.color)
            existing.sort_order = order
            existing.updated_at = ts
        else:
            session.add(models.Shop(
                id=slug, name=row.get("name", slug),
                emoji=row.get("emoji"), color=row.get("color"),
                active=True, sort_order=order, created_at=ts, updated_at=ts,
            ))
        touched += 1
    session.commit()
    return touched


# ── items ────────────────────────────────────────────────────────────────────
def import_items(session: Session, items: list[dict]) -> int:
    """Upsert master items by canonical_name. Returns rows touched."""
    touched = 0
    for row in items:
        name = row.get("item", "")
        canonical = normalize_name(name)
        if not canonical:
            continue
        existing = session.exec(
            select(models.Item).where(models.Item.canonical_name == canonical)
        ).first()
        ts = now_iso()
        if existing:
            existing.display_name = name or existing.display_name
            if "category" in row:
                existing.category = row.get("category") or None
            if "defaultShop" in row:
                existing.default_shop_id = row.get("defaultShop") or None
            if "defaultQty" in row:
                existing.default_quantity = _optional_float(row.get("defaultQty"))
            if "defaultUnit" in row:
                existing.default_unit = row.get("defaultUnit") or None
            source_count = _first_present(row, "useCount", "count")
            if source_count is not None and str(source_count).strip() != "":
                existing.use_count = int(source_count)
            if "lastUsed" in row:
                existing.last_used_at = parse_timestamp(row.get("lastUsed"))
            existing.updated_at = ts
        else:
            source_count = _first_present(row, "useCount", "count")
            session.add(models.Item(
                canonical_name=canonical, display_name=name or None,
                category=row.get("category") or None,
                default_shop_id=row.get("defaultShop") or None,
                default_quantity=_optional_float(row.get("defaultQty")),
                default_unit=row.get("defaultUnit") or None,
                use_count=int(source_count or 0),
                last_used_at=parse_timestamp(row.get("lastUsed")),
                created_at=ts, updated_at=ts,
            ))
        touched += 1
    session.commit()
    return touched


def ensure_item(session: Session, name: str, *, shop_id: str | None = None) -> int | None:
    """Return the item_id for a name, creating a minimal item if needed."""
    canonical = normalize_name(name)
    if not canonical:
        return None
    existing = session.exec(
        select(models.Item).where(models.Item.canonical_name == canonical)
    ).first()
    if existing:
        return existing.id
    ts = now_iso()
    item = models.Item(
        canonical_name=canonical, display_name=name or None,
        default_shop_id=shop_id, created_at=ts, updated_at=ts,
    )
    session.add(item)
    session.flush()
    session.refresh(item)
    return item.id


# ── active list (transient: replace on import) ───────────────────────────────
def import_list(session: Session, rows: list[dict]) -> int:
    """Replace the active shopping list with the imported rows. Idempotent by design."""
    for existing in session.exec(select(models.ShoppingListItem)).all():
        session.delete(existing)

    count = 0
    for row in rows:
        name = row.get("item", "")
        if not normalize_name(name):
            continue
        shop_id = (row.get("shop") or None)
        ts = now_iso()
        created_at = parse_timestamp(row.get("dateAdded")) or ts
        session.add(models.ShoppingListItem(
            source_ref=str(row.get("id")) if row.get("id") else None,
            item_id=ensure_item(session, name, shop_id=shop_id),
            name=name,
            quantity=float(row.get("quantity") or 1),
            unit=row.get("unit") or "",
            shop_id=shop_id,
            bought=_to_bool(row.get("bought")),
            notes=row.get("notes") or None,
            sort_order=int(row.get("sortOrder") or 0),
            created_at=created_at, updated_at=ts,
        ))
        count += 1
    session.commit()
    return count


# ── store layout (per shop: replace) ─────────────────────────────────────────
def import_layouts(session: Session, shop_id: str, rows: list[dict]) -> int:
    """Replace one shop's departments + keywords. Splits comma-joined keywords.
    Deleting a department cascades to its keywords (FK ON DELETE CASCADE)."""
    depts = session.exec(
        select(models.StoreLayoutDepartment).where(
            models.StoreLayoutDepartment.shop_id == shop_id
        )
    ).all()
    for dept in depts:
        session.delete(dept)

    count = 0
    for order, row in enumerate(rows):
        dept = models.StoreLayoutDepartment(
            shop_id=shop_id, name=row.get("department", ""),
            sort_order=int(row.get("order") or order),
        )
        session.add(dept)
        session.flush()
        session.refresh(dept)
        keywords = row.get("keywords", "")
        if isinstance(keywords, str):
            keywords = [k.strip() for k in keywords.split(",")]
        for kw in keywords:
            if kw:
                session.add(models.StoreLayoutKeyword(department_id=dept.id, keyword=kw))
        count += 1
    session.commit()
    return count


# ── history → import trips + trip items ──────────────────────────────────────
def import_history(session: Session, rows: list[dict]) -> dict:
    """Group legacy History rows into one 'import' trip per (shop, date).

    Raw History exports use ``dateBought`` and include quantity/unit. ``boughtAt``
    is also accepted for compatibility with earlier transformed exports. Every
    row gets a deterministic source key, including an occurrence number, so even
    genuinely duplicated products are preserved while reruns remain idempotent.
    """
    imported, skipped, unparseable = 0, 0, 0

    occurrences: dict[str, int] = {}

    for row in rows:
        name = row.get("item", "")
        if not normalize_name(name):
            continue
        shop_id = row.get("shop") or None
        bought_at = parse_timestamp(_first_present(row, "dateBought", "boughtAt"))
        if bought_at is None:
            unparseable += 1
            continue
        trip_date = bought_at[:10]
        quantity = _optional_float(row.get("quantity"))
        if quantity is None:
            quantity = 1.0
        unit = row.get("unit") or ""

        signature = json.dumps(
            [normalize_name(name), quantity, unit, shop_id, bought_at],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        occurrence = occurrences.get(signature, 0)
        occurrences[signature] = occurrence + 1
        digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()
        source_ref = f"legacy-history:{digest}:{occurrence}"

        # Skip only this exact source occurrence. Identical duplicate Sheet rows
        # receive different occurrence suffixes and are therefore all retained.
        dup = session.exec(
            select(models.ShoppingTripItem).where(
                models.ShoppingTripItem.source_ref == source_ref,
            )
        ).first()
        if dup:
            skipped += 1
            continue

        # Find or create the import trip for this (shop, date).
        trip = session.exec(
            select(models.ShoppingTrip).where(
                models.ShoppingTrip.shop_id == shop_id,
                models.ShoppingTrip.trip_date == trip_date,
                models.ShoppingTrip.source == "import",
            )
        ).first()
        if not trip:
            ts = now_iso()
            trip = models.ShoppingTrip(
                shop_id=shop_id, trip_date=trip_date, source="import",
                created_at=ts, updated_at=ts,
            )
            session.add(trip)
            session.flush()
            session.refresh(trip)

        session.add(models.ShoppingTripItem(
            trip_id=trip.id,
            item_id=ensure_item(session, name, shop_id=shop_id),
            name=name, quantity=quantity, unit=unit, shop_id=shop_id,
            bought_at=bought_at, source_ref=source_ref, created_at=now_iso(),
        ))
        imported += 1

    session.commit()
    return {"imported": imported, "skipped": skipped, "unparseable": unparseable}


# ── verification (the §4 checklist, as code) ─────────────────────────────────
def verify_import(session: Session, source_counts: dict | None = None) -> dict:
    """Return a report of integrity checks. ``source_counts`` may carry the Sheet
    row counts (e.g. {"shops": 8, "items": 120, "list": 6, "history": 340})."""
    source_counts = source_counts or {}

    def count(model) -> int:
        return len(session.exec(select(model)).all())

    db_counts = {
        "shops": count(models.Shop),
        "items": count(models.Item),
        "list": count(models.ShoppingListItem),
        "trips": count(models.ShoppingTrip),
        "trip_items": count(models.ShoppingTripItem),
        "departments": count(models.StoreLayoutDepartment),
        "keywords": count(models.StoreLayoutKeyword),
    }

    import_trip_ids = {
        trip.id for trip in session.exec(
            select(models.ShoppingTrip).where(models.ShoppingTrip.source == "import")
        ).all()
    }
    imported_history = [
        item for item in session.exec(select(models.ShoppingTripItem)).all()
        if item.trip_id in import_trip_ids
    ]

    blank_items = session.exec(
        select(models.Item).where(models.Item.canonical_name == "")
    ).all()
    blank_list = session.exec(
        select(models.ShoppingListItem).where(models.ShoppingListItem.name == "")
    ).all()
    blank_history = [item.id for item in imported_history if not normalize_name(item.name)]
    invalid_history_dates = [item.id for item in imported_history if parse_timestamp(item.bought_at) is None]

    # FK integrity + orphan shop refs.
    fk_violations = session.exec(text("PRAGMA foreign_key_check")).all()
    shop_ids = {s.id for s in session.exec(select(models.Shop)).all()}
    orphan_list_shops = [
        li.id for li in session.exec(select(models.ShoppingListItem)).all()
        if li.shop_id and li.shop_id not in shop_ids
    ]

    # Count semantics: shops and the active list should match the source exactly.
    # Items may legitimately *exceed* the source dictionary, because importing the
    # list/history auto-creates canonical items that weren't in the Items sheet.
    counts_ok = True
    if source_counts.get("shops") is not None:
        counts_ok = counts_ok and db_counts["shops"] == source_counts["shops"]
    if source_counts.get("list") is not None:
        counts_ok = counts_ok and db_counts["list"] == source_counts["list"]
    if source_counts.get("items") is not None:
        counts_ok = counts_ok and db_counts["items"] >= source_counts["items"]
    if source_counts.get("history") is not None:
        counts_ok = counts_ok and len(imported_history) == source_counts["history"]

    checks = {
        "row_counts_match": counts_ok,
        "no_blank_item_names": len(blank_items) == 0 and len(blank_list) == 0 and len(blank_history) == 0,
        "history_dates_valid": len(invalid_history_dates) == 0,
        "fk_integrity_ok": len(fk_violations) == 0,
        "no_orphan_shop_refs": len(orphan_list_shops) == 0,
    }
    return {
        "source_counts": source_counts,
        "db_counts": db_counts,
        "checks": checks,
        "fk_violations": [tuple(r) for r in fk_violations],
        "orphan_list_shops": orphan_list_shops,
        "invalid_history_dates": invalid_history_dates,
        "ok": all(checks.values()),
    }
