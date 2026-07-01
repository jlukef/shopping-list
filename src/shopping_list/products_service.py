"""Products screen: the item catalog with live purchase stats, plus merging.

A "product" is a row in ``items``. Purchase stats (count, last price, spend)
are computed live from ``shopping_trip_items`` rather than stored, so merging
two products combines their history automatically once the trip items are
repointed. Merging keeps the target product's name, sums ``use_count``, turns
each source's canonical name into an ``item_aliases`` row pointing at the
target (so future receipts/list adds under the old name resolve to the merged
product — see ``ensure_catalog_item``), and deletes the source rows.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import update
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from . import models
from .db import now_iso
from .sqlite_api import _item_pk as _pk


class ProductNotFound(LookupError):
    pass


class ProductsService:
    def __init__(self, engine: Engine):
        self.engine = engine

    def list_products(self) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            items = session.exec(select(models.Item)).all()
            trip_rows = session.exec(
                select(models.ShoppingTripItem).where(
                    models.ShoppingTripItem.item_id != None  # noqa: E711
                ).order_by(models.ShoppingTripItem.bought_at, models.ShoppingTripItem.id)
            ).all()
            aliases = session.exec(select(models.ItemAlias)).all()

            stats: dict[int, dict[str, Any]] = {}
            for row in trip_rows:
                s = stats.setdefault(row.item_id, {
                    "purchaseCount": 0, "totalSpendPennies": None,
                    "lastBoughtAt": None, "lastShopId": None, "lastUnitPricePennies": None,
                })
                s["purchaseCount"] += 1
                if row.line_total_pennies is not None:
                    s["totalSpendPennies"] = (s["totalSpendPennies"] or 0) + row.line_total_pennies
                # trip_rows are ordered oldest-first, so the last write wins
                s["lastBoughtAt"] = row.bought_at
                s["lastShopId"] = row.shop_id
                if row.unit_price_pennies is not None:
                    s["lastUnitPricePennies"] = row.unit_price_pennies

            alias_map: dict[int, list[str]] = {}
            for alias in aliases:
                alias_map.setdefault(alias.item_id, []).append(alias.alias_text)

            products = []
            for item in items:
                s = stats.get(item.id, {})
                products.append({
                    "id": str(item.id),
                    "name": item.display_name or item.canonical_name,
                    "canonicalName": item.canonical_name,
                    "aliases": sorted(alias_map.get(item.id, [])),
                    "useCount": item.use_count,
                    "lastUsedAt": item.last_used_at,
                    "defaultShopId": item.default_shop_id,
                    "purchaseCount": s.get("purchaseCount", 0),
                    "totalSpendPennies": s.get("totalSpendPennies"),
                    "lastBoughtAt": s.get("lastBoughtAt"),
                    "lastShopId": s.get("lastShopId"),
                    "lastUnitPricePennies": s.get("lastUnitPricePennies"),
                })
            # Most-bought first, then most-used on the list, then A→Z.
            products.sort(key=lambda p: (-p["purchaseCount"], -p["useCount"], p["name"].lower()))
            return products

    def merge_products(self, target_id: str, source_ids: list[str]) -> dict[str, Any]:
        with Session(self.engine) as session:
            target = session.get(models.Item, _pk(target_id))
            if target is None:
                raise ProductNotFound(f"No product {target_id}")
            source_pks = []
            for raw in source_ids:
                pk = _pk(raw)
                if pk != target.id and pk not in source_pks:
                    source_pks.append(pk)
            if not source_pks:
                raise ValueError("Choose at least one other product to merge")
            sources = []
            for pk in source_pks:
                source = session.get(models.Item, pk)
                if source is None:
                    raise ProductNotFound(f"No product {pk}")
                sources.append(source)

            ts = now_iso()
            for source in sources:
                # Repoint every reference so history/stats follow the merge.
                for model in (
                    models.ShoppingTripItem,
                    models.ReceiptItem,
                    models.ShoppingListItem,
                    models.ItemAlias,
                    models.Suggestion,
                ):
                    session.exec(update(model).where(model.item_id == source.id).values(item_id=target.id))
                # Cached per-item stats (if any) are stale for both sides after
                # a merge; drop them — they are recomputable from trip items.
                for pk in (source.id, target.id):
                    cached = session.get(models.ItemPurchaseStats, pk)
                    if cached is not None:
                        session.delete(cached)

                existing_alias = session.exec(
                    select(models.ItemAlias).where(models.ItemAlias.alias_text == source.canonical_name)
                ).first()
                if existing_alias is None:
                    session.add(models.ItemAlias(
                        item_id=target.id,
                        alias_text=source.canonical_name,
                        source="user",
                        created_at=ts,
                    ))
                else:
                    existing_alias.item_id = target.id
                    session.add(existing_alias)

                target.use_count += source.use_count
                if source.last_used_at and (target.last_used_at or "") < source.last_used_at:
                    target.last_used_at = source.last_used_at
                target.default_shop_id = target.default_shop_id or source.default_shop_id
                target.default_quantity = target.default_quantity or source.default_quantity
                target.default_unit = target.default_unit or source.default_unit
                target.category = target.category or source.category
                session.delete(source)

            target.updated_at = ts
            session.add(target)
            session.commit()
            return {"success": True, "targetId": str(target.id), "merged": len(sources)}
