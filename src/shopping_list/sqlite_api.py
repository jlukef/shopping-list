"""SQLite implementation of the legacy ``GET /api?action=...`` contract.

The browser contract stays stable during the clean-start cutover. This module owns
the compatibility translation between legacy field names (``item``, ``shop``,
``sortOrder``) and the Phase 2 SQLModel schema.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import Engine
from sqlmodel import Session, select
from starlette.datastructures import QueryParams

from . import models
from .db import bootstrap, now_iso, seed_default_layouts, seed_default_shops, today_iso


def _normalise_name(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "y", "bought"}


def _as_float(value: Any, *, default: float = 1.0) -> float:
    if value is None or str(value).strip() == "":
        return default
    return float(value)


def _item_pk(value: Any) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid item id") from exc


def _shop_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if not slug:
        raise ValueError("Shop name must contain a letter or number")
    return slug


def ensure_catalog_item(
    session: Session,
    name: str,
    *,
    shop_id: str | None,
    quantity: float,
    unit: str,
    increment_use: bool,
) -> models.Item:
    """Find-or-create the ``items`` catalog row for ``name``.

    Shared canonicalisation used by both the legacy list actions here and the
    receipt-accept flow in ``receipts_service.py`` — deliberately the same
    exact-match-on-normalised-name logic in both places, not a second scheme.
    """
    canonical = _normalise_name(name)
    item = session.exec(
        select(models.Item).where(models.Item.canonical_name == canonical)
    ).first()
    ts = now_iso()
    if item is None:
        item = models.Item(
            canonical_name=canonical,
            display_name=name,
            default_shop_id=shop_id,
            default_quantity=quantity,
            default_unit=unit or None,
            use_count=1 if increment_use else 0,
            last_used_at=ts if increment_use else None,
            created_at=ts,
            updated_at=ts,
        )
        session.add(item)
        session.flush()
    else:
        item.display_name = name
        item.default_shop_id = shop_id
        item.default_quantity = quantity
        item.default_unit = unit or None
        if increment_use:
            item.use_count += 1
            item.last_used_at = ts
        item.updated_at = ts
    return item


class SQLiteActionService:
    """Dispatch legacy API actions against one clean-start SQLite database."""

    def __init__(self, db_path: Path | str):
        self.engine: Engine = bootstrap(db_path)

    def close(self) -> None:
        self.engine.dispose()

    def dispatch(self, query_params: QueryParams, *, username: str | None = None) -> dict[str, Any]:
        action = (query_params.get("action") or "").strip()
        try:
            raw_data = query_params.get("data")
            data = json.loads(raw_data) if raw_data else {}
            if not isinstance(data, dict):
                raise ValueError("data must be a JSON object")

            handlers = {
                "setup": lambda: self._setup(),
                "getList": lambda: self._get_list(),
                "getShops": lambda: self._get_shops(),
                "addItem": lambda: self._add_item(data),
                "updateItem": lambda: self._update_item(data),
                "deleteItem": lambda: self._delete_item(data),
                "clearBought": lambda: self._clear_bought(),
                "clearList": lambda: self._clear_list(),
                "getAutocomplete": lambda: self._get_autocomplete(query_params.get("q", "")),
                "addShop": lambda: self._add_shop(data),
                "deleteShop": lambda: self._delete_shop(data),
                "getLayouts": lambda: self._get_layouts(query_params.get("shop", "")),
                "saveLayout": lambda: self._save_layout(data),
                "sortList": lambda: self._sort_list(data),
                "getHistory": lambda: self._get_history(),
                "getApiKeySet": lambda: {"set": False, "preview": "", "supported": False},
                "saveApiKey": lambda: {"error": "AI key settings are unavailable with the SQLite backend"},
            }
            handler = handlers.get(action)
            if handler is None:
                return {"error": f"Unknown action: {action}"}
            return handler()
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return {"error": str(exc)}
        except IntegrityError:
            return {"error": "That change conflicts with existing data"}

    def _setup(self) -> dict[str, Any]:
        with Session(self.engine) as session:
            shops = seed_default_shops(session)
            layouts = seed_default_layouts(session)
        return {"success": True, "message": "Setup complete", "shopsAdded": shops, "layoutsAdded": layouts}

    @staticmethod
    def _list_json(item: models.ShoppingListItem) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "item": item.name,
            "quantity": item.quantity,
            "unit": item.unit,
            "shop": item.shop_id or "other",
            "bought": item.bought,
            "dateAdded": item.created_at,
            "notes": item.notes or "",
            "sortOrder": item.sort_order,
        }

    def _get_list(self) -> dict[str, Any]:
        with Session(self.engine) as session:
            rows = session.exec(
                select(models.ShoppingListItem).order_by(
                    models.ShoppingListItem.sort_order,
                    models.ShoppingListItem.id,
                )
            ).all()
            return {"items": [self._list_json(row) for row in rows]}

    def _get_shops(self) -> dict[str, Any]:
        with Session(self.engine) as session:
            shops = session.exec(
                select(models.Shop).where(models.Shop.active == True).order_by(  # noqa: E712
                    models.Shop.sort_order,
                    models.Shop.id,
                )
            ).all()
            return {"shops": [
                {"id": shop.id, "name": shop.name, "color": shop.color, "emoji": shop.emoji}
                for shop in shops
            ]}

    @staticmethod
    def _require_shop(session: Session, shop_id: str | None) -> str:
        resolved = shop_id or "other"
        shop = session.get(models.Shop, resolved)
        if not shop or not shop.active:
            raise ValueError(f"Unknown shop: {resolved}")
        return resolved

    def _add_item(self, data: dict[str, Any]) -> dict[str, Any]:
        name = str(data.get("item") or "").strip()
        if not name:
            raise ValueError("Item name is required")
        quantity = _as_float(data.get("quantity"), default=1.0)
        unit = str(data.get("unit") or "").strip()
        ts = now_iso()
        with Session(self.engine) as session:
            shop_id = self._require_shop(session, data.get("shop"))
            catalog = ensure_catalog_item(
                session,
                name,
                shop_id=shop_id,
                quantity=quantity,
                unit=unit,
                increment_use=True,
            )
            row = models.ShoppingListItem(
                item_id=catalog.id,
                name=name,
                quantity=quantity,
                unit=unit,
                shop_id=shop_id,
                bought=False,
                notes=str(data.get("notes") or "").strip() or None,
                sort_order=int(data.get("sortOrder") if data.get("sortOrder") is not None else 999),
                created_at=ts,
                updated_at=ts,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return {"success": True, "id": str(row.id)}

    @staticmethod
    def _history_trip(
        session: Session,
        *,
        shop_id: str | None,
        bought_at: str,
        source: str,
    ) -> models.ShoppingTrip:
        trip_date = bought_at[:10]
        trip = session.exec(
            select(models.ShoppingTrip).where(
                models.ShoppingTrip.shop_id == shop_id,
                models.ShoppingTrip.trip_date == trip_date,
                models.ShoppingTrip.source == source,
            )
        ).first()
        if trip is None:
            trip = models.ShoppingTrip(
                shop_id=shop_id,
                trip_date=trip_date,
                source=source,
                created_at=bought_at,
                updated_at=bought_at,
            )
            session.add(trip)
            session.flush()
        return trip

    def _archive_list_item(
        self,
        session: Session,
        row: models.ShoppingListItem,
        *,
        source: str,
        bought_at: str,
    ) -> None:
        # shopping_list_items.id is a plain SQLite INTEGER PRIMARY KEY (no
        # AUTOINCREMENT), so once the table empties, SQLite is free to reuse a
        # deleted row's id for the next insert. Anchoring de-duplication to
        # row.id alone would then make a brand-new purchase look like an
        # already-archived one and silently skip recording it. row.created_at is
        # set once at insert time and is never reused, so pairing it with the id
        # keeps the key unique across the row's whole lifetime even if the id is.
        source_ref = f"list-item:{row.id}:{row.created_at}:bought"
        existing = session.exec(
            select(models.ShoppingTripItem).where(models.ShoppingTripItem.source_ref == source_ref)
        ).first()
        if existing:
            return
        trip = self._history_trip(
            session,
            shop_id=row.shop_id,
            bought_at=bought_at,
            source=source,
        )
        session.add(models.ShoppingTripItem(
            trip_id=trip.id,
            item_id=row.item_id,
            name=row.name,
            quantity=row.quantity,
            unit=row.unit,
            shop_id=row.shop_id,
            bought_at=bought_at,
            source_ref=source_ref,
            created_at=bought_at,
        ))

    def _update_item(self, data: dict[str, Any]) -> dict[str, Any]:
        with Session(self.engine) as session:
            row = session.get(models.ShoppingListItem, _item_pk(data.get("id")))
            if row is None:
                return {"error": "Item not found"}

            if "item" in data:
                name = str(data.get("item") or "").strip()
                if not name:
                    raise ValueError("Item name is required")
                row.name = name
            if "quantity" in data:
                row.quantity = _as_float(data.get("quantity"), default=1.0)
            if "unit" in data:
                row.unit = str(data.get("unit") or "").strip()
            if "shop" in data:
                row.shop_id = self._require_shop(session, data.get("shop"))
            if "notes" in data:
                row.notes = str(data.get("notes") or "").strip() or None
            if "sortOrder" in data:
                row.sort_order = int(data.get("sortOrder") or 0)

            catalog = ensure_catalog_item(
                session,
                row.name,
                shop_id=row.shop_id,
                quantity=row.quantity,
                unit=row.unit,
                increment_use=False,
            )
            row.item_id = catalog.id

            if "bought" in data:
                new_bought = _as_bool(data.get("bought"))
                if new_bought and row.bought_at is None:
                    bought_at = now_iso()
                    self._archive_list_item(session, row, source="manual", bought_at=bought_at)
                    row.bought_at = bought_at
                row.bought = new_bought
            row.updated_at = now_iso()
            session.add(row)
            session.commit()
            return {"success": True}

    def _delete_item(self, data: dict[str, Any]) -> dict[str, Any]:
        with Session(self.engine) as session:
            row = session.get(models.ShoppingListItem, _item_pk(data.get("id")))
            if row is None:
                return {"error": "Item not found"}
            session.delete(row)
            session.commit()
            return {"success": True}

    def _clear_bought(self) -> dict[str, Any]:
        removed = 0
        with Session(self.engine) as session:
            rows = session.exec(
                select(models.ShoppingListItem).where(models.ShoppingListItem.bought == True)  # noqa: E712
            ).all()
            for row in rows:
                if row.bought_at is None:
                    bought_at = now_iso()
                    self._archive_list_item(session, row, source="clear_bought", bought_at=bought_at)
                    row.bought_at = bought_at
                session.delete(row)
                removed += 1
            session.commit()
        return {"success": True, "removed": removed}

    def _clear_list(self) -> dict[str, Any]:
        removed = 0
        with Session(self.engine) as session:
            rows = session.exec(select(models.ShoppingListItem)).all()
            for row in rows:
                if row.bought and row.bought_at is None:
                    bought_at = now_iso()
                    self._archive_list_item(session, row, source="clear_bought", bought_at=bought_at)
                session.delete(row)
                removed += 1
            session.commit()
        return {"success": True, "removed": removed}

    def _get_autocomplete(self, query: str) -> dict[str, Any]:
        q = _normalise_name(query)
        with Session(self.engine) as session:
            rows = session.exec(
                select(models.Item).order_by(models.Item.use_count.desc(), models.Item.canonical_name)
            ).all()
            matches = [row for row in rows if q in row.canonical_name][:20]
            return {"items": [
                {
                    "item": row.display_name or row.canonical_name,
                    "count": row.use_count,
                    "category": row.category or "",
                    "defaultShop": row.default_shop_id or "",
                    "defaultQty": row.default_quantity,
                    "defaultUnit": row.default_unit or "",
                }
                for row in matches
            ]}

    def _add_shop(self, data: dict[str, Any]) -> dict[str, Any]:
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValueError("Shop name is required")
        slug = _shop_slug(name)
        with Session(self.engine) as session:
            if session.get(models.Shop, slug):
                return {"error": "Shop already exists"}
            existing = session.exec(select(models.Shop)).all()
            ts = now_iso()
            session.add(models.Shop(
                id=slug,
                name=name,
                emoji=str(data.get("emoji") or "🏪"),
                color=str(data.get("color") or "#888888"),
                active=True,
                sort_order=max((shop.sort_order for shop in existing), default=-1) + 1,
                created_at=ts,
                updated_at=ts,
            ))
            session.commit()
        return {"success": True, "id": slug}

    def _delete_shop(self, data: dict[str, Any]) -> dict[str, Any]:
        shop_id = str(data.get("id") or "")
        if shop_id == "other":
            return {"error": "The Other shop cannot be removed"}
        with Session(self.engine) as session:
            shop = session.get(models.Shop, shop_id)
            if shop is None:
                return {"error": "Shop not found"}
            for row in session.exec(
                select(models.ShoppingListItem).where(models.ShoppingListItem.shop_id == shop_id)
            ).all():
                row.shop_id = "other"
            session.delete(shop)
            session.commit()
        return {"success": True}

    def _get_layouts(self, shop_id: str) -> dict[str, Any]:
        with Session(self.engine) as session:
            statement = select(models.StoreLayoutDepartment)
            if shop_id:
                statement = statement.where(models.StoreLayoutDepartment.shop_id == shop_id)
            departments = session.exec(statement.order_by(
                models.StoreLayoutDepartment.shop_id,
                models.StoreLayoutDepartment.sort_order,
            )).all()
            result = []
            for department in departments:
                keywords = session.exec(
                    select(models.StoreLayoutKeyword).where(
                        models.StoreLayoutKeyword.department_id == department.id
                    ).order_by(models.StoreLayoutKeyword.id)
                ).all()
                result.append({
                    "shop": department.shop_id,
                    "department": department.name,
                    "order": department.sort_order,
                    "keywords": ",".join(keyword.keyword for keyword in keywords),
                })
            return {"layouts": result}

    def _save_layout(self, data: dict[str, Any]) -> dict[str, Any]:
        shop_id = str(data.get("shop") or "")
        departments = data.get("departments")
        if not isinstance(departments, list):
            raise ValueError("departments must be a list")
        with Session(self.engine) as session:
            self._require_shop(session, shop_id)
            existing = session.exec(
                select(models.StoreLayoutDepartment).where(
                    models.StoreLayoutDepartment.shop_id == shop_id
                )
            ).all()
            for department in existing:
                session.delete(department)
            for index, raw in enumerate(departments, start=1):
                if not isinstance(raw, dict):
                    raise ValueError("Each department must be an object")
                name = str(raw.get("name") or raw.get("department") or "").strip()
                if not name:
                    raise ValueError("Department name is required")
                department = models.StoreLayoutDepartment(
                    shop_id=shop_id,
                    name=name,
                    sort_order=int(raw.get("order") or index),
                )
                session.add(department)
                session.flush()
                keywords = raw.get("keywords") or ""
                if isinstance(keywords, str):
                    keywords = keywords.split(",")
                for keyword in keywords:
                    cleaned = str(keyword).strip().lower()
                    if cleaned:
                        session.add(models.StoreLayoutKeyword(
                            department_id=department.id,
                            keyword=cleaned,
                        ))
            session.commit()
        return {"success": True}

    def _sort_list(self, data: dict[str, Any]) -> dict[str, Any]:
        shop_id = str(data.get("shop") or "")
        items = data.get("items")
        if not isinstance(items, list):
            raise ValueError("items must be a list")
        layouts = self._get_layouts(shop_id)["layouts"]
        if not layouts:
            return {"items": items, "method": "unchanged", "message": "No store layout found"}

        keyword_order: list[tuple[str, int]] = []
        for department in layouts:
            for keyword in str(department["keywords"]).split(","):
                cleaned = keyword.strip().lower()
                if cleaned:
                    keyword_order.append((cleaned, int(department["order"])))

        def rank(raw: dict[str, Any]) -> int:
            name = _normalise_name(raw.get("item"))
            exact = [order for keyword, order in keyword_order if keyword == name]
            if exact:
                return min(exact)
            partial = [order for keyword, order in keyword_order if keyword in name or name in keyword]
            return min(partial, default=999)

        ordered = sorted(enumerate(items), key=lambda pair: (rank(pair[1]), pair[0]))
        result = []
        for sort_order, (_, item) in enumerate(ordered):
            result.append({**item, "sortOrder": sort_order})
        return {"items": result, "method": "keywords"}

    def _get_history(self) -> dict[str, Any]:
        with Session(self.engine) as session:
            trips = session.exec(
                select(models.ShoppingTrip).order_by(models.ShoppingTrip.trip_date.desc(), models.ShoppingTrip.id.desc())
            ).all()
            result = []
            for trip in trips:
                rows = session.exec(
                    select(models.ShoppingTripItem).where(
                        models.ShoppingTripItem.trip_id == trip.id
                    ).order_by(models.ShoppingTripItem.id)
                ).all()
                result.append({
                    "id": str(trip.id),
                    "shop": trip.shop_id,
                    "date": trip.trip_date,
                    "source": trip.source,
                    "items": [{
                        "id": str(row.id),
                        "item": row.name,
                        "quantity": row.quantity,
                        "unit": row.unit,
                        "shop": row.shop_id,
                        "boughtAt": row.bought_at,
                    } for row in rows],
                })
            return {"trips": result}

