"""Receipt upload / review / accept, including 5b AI extraction.

Image bytes are never persisted — see PHASE5_RECEIPT_OCR_PLAN.md §5 and
``receipt_images.py``. ``uploaded_by_user_id`` / ``created_by_user_id`` stay
null: current auth is an environment-backed username with no corresponding
row in the (empty) ``users`` table, so there is no real integer id to
attribute history to yet (plan §6, "User attribution boundary").

Extraction (§3-4) is provider-neutral and optional: with no
``SHOPPING_LIST_RECEIPT_AI_OPTIONS`` configured, ``ExtractorRegistry`` has no
enabled options and every upload behaves exactly like the 5a slice — status
goes straight to ``ready`` with zero lines, manual entry only.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from . import models
from .config import ReceiptAISettings
from .db import now_iso, today_iso
from .receipt_extraction import (
    AllExtractionAttemptsFailed,
    AttemptRecord,
    ExtractorRegistry,
    extract_with_fallback,
)
from .receipt_fields import (
    as_bool,
    as_item_name,
    as_optional_date,
    as_optional_money,
    as_quantity,
    as_unit,
)
from .receipt_images import InvalidReceiptImage, process_upload
from .sqlite_api import _item_pk as _pk
from .sqlite_api import ensure_catalog_item

EDITABLE_STATUSES = {"ready", "reviewed", "failed"}
MUTABLE_STATUSES = EDITABLE_STATUSES | {"saved"}


class ReceiptNotFound(LookupError):
    pass


class ReceiptStateError(ValueError):
    """The requested action isn't valid for the receipt's current status."""


class ReceiptService:
    """Receipt upload/review/accept operations against the shared app Engine."""

    def __init__(self, engine: Engine, *, extractor_registry: ExtractorRegistry | None = None):
        self.engine = engine
        self._registry = extractor_registry or ExtractorRegistry(ReceiptAISettings())
        self._recover_stale_processing_receipts()

    def _recover_stale_processing_receipts(self) -> None:
        # A receipt left in 'processing' means the process died mid-extraction
        # (crash/restart/deploy) — without this, it would be stuck forever,
        # since normal edit/accept routes deliberately reject that status.
        ts = now_iso()
        with Session(self.engine) as session:
            stuck = session.exec(select(models.Receipt).where(models.Receipt.status == "processing")).all()
            for receipt in stuck:
                receipt.status = "failed"
                receipt.updated_at = ts
                session.add(receipt)
            if stuck:
                session.commit()

    # ── image upload ────────────────────────────────────────────────────
    async def create_receipt(
        self, raw_bytes: bytes, *, original_filename: str | None, extractor_alias: str = "auto",
    ) -> dict[str, Any]:
        try:
            processed = await asyncio.to_thread(process_upload, raw_bytes)
        except InvalidReceiptImage as exc:
            raise ValueError(str(exc)) from exc

        ts = now_iso()
        will_extract = self._registry.configured
        with Session(self.engine) as session:
            existing = session.exec(
                select(models.Receipt).where(
                    models.Receipt.content_sha256 == processed.content_sha256,
                    models.Receipt.status != "failed",
                )
            ).first()
            if existing is not None:
                # Same photo re-selected/re-submitted — return the existing
                # record rather than creating (and paying to re-extract) a duplicate.
                return self._receipt_json(session, existing)

            receipt = models.Receipt(
                original_filename=(original_filename or "").strip()[:255] or None,
                stored_path="",
                content_sha256=processed.content_sha256,
                mime_type="image/jpeg",
                file_size_bytes=len(processed.jpeg_bytes),
                status="processing" if will_extract else "ready",
                currency="GBP",
                created_at=ts,
                updated_at=ts,
            )
            session.add(receipt)
            session.commit()
            session.refresh(receipt)
            receipt_id = receipt.id
            if not will_extract:
                return self._receipt_json(session, receipt)

        await self._run_extraction(receipt_id, processed.jpeg_bytes, extractor_alias)
        with Session(self.engine) as session:
            receipt = self._require_receipt(session, str(receipt_id))
            return self._receipt_json(session, receipt)

    async def retry_receipt(
        self, receipt_id: str, raw_bytes: bytes, *, extractor_alias: str = "auto",
    ) -> dict[str, Any]:
        """Re-run extraction on a re-supplied image (no receipt image is ever stored to retry from).

        This is a full replace, not a merge: every existing line on the receipt is
        discarded and rebuilt from the new extraction. It is only permitted before
        the receipt is saved to history, and the frontend must confirm with the
        user before calling this — see PHASE5_RECEIPT_OCR_PLAN.md §3, "Retrying a
        successful-but-unsaved extraction ... must ask before replacing current rows."
        """
        if not self._registry.configured:
            raise ValueError("Receipt AI extraction is not configured")
        try:
            processed = await asyncio.to_thread(process_upload, raw_bytes)
        except InvalidReceiptImage as exc:
            raise ValueError(str(exc)) from exc

        with Session(self.engine) as session:
            receipt = self._require_receipt(session, receipt_id)
            if receipt.status == "saved":
                raise ReceiptStateError("A saved receipt can't be retried")
            receipt.status = "processing"
            receipt.content_sha256 = processed.content_sha256
            receipt.file_size_bytes = len(processed.jpeg_bytes)
            receipt.updated_at = now_iso()
            session.add(receipt)
            session.commit()
            pk = receipt.id

        await self._run_extraction(pk, processed.jpeg_bytes, extractor_alias)
        with Session(self.engine) as session:
            receipt = self._require_receipt(session, str(pk))
            return self._receipt_json(session, receipt)

    async def _run_extraction(self, receipt_id: int, jpeg_bytes: bytes, extractor_alias: str) -> None:
        """Runs the fallback chain and writes the outcome. Holds no DB session while awaiting network I/O."""
        try:
            result, attempts = await extract_with_fallback(
                self._registry, requested=extractor_alias, image_bytes=jpeg_bytes, mime_type="image/jpeg",
            )
        except AllExtractionAttemptsFailed as exc:
            result, attempts = None, exc.attempts
        except ValueError:
            # Unknown/disabled alias requested — nothing to attempt at all.
            self._mark_receipt_failed(receipt_id, [])
            raise

        self._write_extraction_outcome(receipt_id, result, attempts)

    def _mark_receipt_failed(self, receipt_id: int, attempts: list[AttemptRecord]) -> None:
        self._write_extraction_outcome(receipt_id, None, attempts)

    def _write_extraction_outcome(self, receipt_id: int, result, attempts: list[AttemptRecord]) -> None:
        ts = now_iso()
        with Session(self.engine) as session:
            receipt = session.get(models.Receipt, receipt_id)
            if receipt is None:
                return  # discarded while extraction was in flight
            for attempt in attempts:
                session.add(models.ReceiptExtractionAttempt(
                    receipt_id=receipt.id,
                    alias=attempt.alias,
                    provider=attempt.provider,
                    model=attempt.model,
                    outcome=attempt.outcome,
                    error_class=attempt.error_class,
                    duration_ms=attempt.duration_ms,
                    created_at=ts,
                ))

            if result is None:
                receipt.status = "failed"
                receipt.updated_at = ts
                session.add(receipt)
                session.commit()
                return

            for row in session.exec(
                select(models.ReceiptItem).where(models.ReceiptItem.receipt_id == receipt.id)
            ).all():
                session.delete(row)

            for line_no, line in enumerate(result.lines, start=1):
                is_item = line.category == "item"
                session.add(models.ReceiptItem(
                    receipt_id=receipt.id,
                    line_no=line_no,
                    raw_text=line.raw_text,
                    name=line.name,
                    quantity=line.quantity,
                    unit=line.unit,
                    unit_price_pennies=line.unit_price_pennies,
                    line_total_pennies=line.line_total_pennies,
                    confidence=line.confidence,
                    category=line.category,
                    excluded=not is_item,
                    accepted=is_item,
                    created_at=ts,
                    updated_at=ts,
                ))

            if receipt.shop_id is None and result.shop_name_guess:
                guess = result.shop_name_guess.strip().lower()
                active_shops = session.exec(select(models.Shop).where(models.Shop.active == True)).all()  # noqa: E712
                match = next((s for s in active_shops if s.name.strip().lower() == guess), None)
                if match:
                    receipt.shop_id = match.id

            receipt.purchase_date = result.purchase_date
            receipt.subtotal_pennies = result.subtotal_pennies
            receipt.total_pennies = result.total_pennies
            receipt.currency = result.currency
            receipt.ocr_engine = f"ai:{result.provider}:{result.model}"
            receipt.raw_extraction_json = result.raw_json
            receipt.extracted_at = ts
            receipt.status = "ready"
            receipt.updated_at = ts
            session.add(receipt)
            session.commit()

    # ── extractor options (for the "Read with" picker) ─────────────────
    def list_extractor_options(self) -> list[dict[str, str]]:
        return self._registry.list_options()

    # ── reads ───────────────────────────────────────────────────────────
    def list_receipts(self) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            receipts = session.exec(
                select(models.Receipt).order_by(models.Receipt.created_at.desc(), models.Receipt.id.desc())
            ).all()
            return [self._receipt_summary_json(session, receipt) for receipt in receipts]

    def get_receipt(self, receipt_id: str) -> dict[str, Any]:
        with Session(self.engine) as session:
            receipt = self._require_receipt(session, receipt_id)
            return self._receipt_json(session, receipt)

    # ── receipt-level edits ─────────────────────────────────────────────
    def patch_receipt(self, receipt_id: str, data: dict[str, Any]) -> dict[str, Any]:
        with Session(self.engine) as session:
            receipt = self._require_receipt(session, receipt_id)
            self._require_mutable(receipt)
            was_saved = receipt.status == "saved"
            if "shopId" in data:
                shop_id = data.get("shopId") or None
                if shop_id is not None and session.get(models.Shop, shop_id) is None:
                    raise ValueError(f"Unknown shop: {shop_id}")
                receipt.shop_id = shop_id
            if "purchaseDate" in data:
                receipt.purchase_date = as_optional_date(data.get("purchaseDate"))
            if "totalPennies" in data:
                receipt.total_pennies = as_optional_money(data.get("totalPennies"))
            if "subtotalPennies" in data:
                receipt.subtotal_pennies = as_optional_money(data.get("subtotalPennies"))
            receipt.status = "saved" if was_saved else "reviewed"
            receipt.updated_at = now_iso()
            session.add(receipt)
            if was_saved:
                self._sync_saved_receipt_history(session, receipt, receipt.updated_at)
            session.commit()
            return self._receipt_json(session, receipt)

    def discard_receipt(self, receipt_id: str) -> dict[str, Any]:
        with Session(self.engine) as session:
            receipt = self._require_receipt(session, receipt_id)
            if receipt.shopping_trip_id is not None:
                trip = session.get(models.ShoppingTrip, receipt.shopping_trip_id)
                if trip is not None:
                    session.delete(trip)
            session.delete(receipt)
            session.commit()
            return {"success": True}

    # ── line items ──────────────────────────────────────────────────────
    def add_item(self, receipt_id: str, data: dict[str, Any]) -> dict[str, Any]:
        name = as_item_name(data.get("name"))
        with Session(self.engine) as session:
            receipt = self._require_receipt(session, receipt_id)
            self._require_mutable(receipt)
            was_saved = receipt.status == "saved"
            ts = now_iso()
            row = models.ReceiptItem(
                receipt_id=receipt.id,
                raw_text=name,  # manual row — nothing was OCR'd, name is the only "raw" text there is
                name=name,
                quantity=as_quantity(data.get("quantity"), default=1.0),
                unit=as_unit(data.get("unit")),
                unit_price_pennies=as_optional_money(data.get("unitPricePennies")),
                line_total_pennies=as_optional_money(data.get("lineTotalPennies")),
                category="item",
                excluded=False,
                accepted=True,
                created_at=ts,
                updated_at=ts,
            )
            session.add(row)
            receipt.status = "saved" if was_saved else "reviewed"
            receipt.updated_at = ts
            session.add(receipt)
            session.flush()
            if was_saved:
                self._sync_saved_receipt_history(session, receipt, ts)
            session.commit()
            return self._receipt_json(session, receipt)

    def update_item(self, receipt_id: str, item_id: str, data: dict[str, Any]) -> dict[str, Any]:
        with Session(self.engine) as session:
            receipt = self._require_receipt(session, receipt_id)
            self._require_mutable(receipt)
            was_saved = receipt.status == "saved"
            row = session.get(models.ReceiptItem, _pk(item_id))
            if row is None or row.receipt_id != receipt.id:
                raise ReceiptNotFound(f"No item {item_id} on receipt {receipt_id}")

            if "name" in data:
                row.name = as_item_name(data.get("name"))
            if "quantity" in data:
                row.quantity = as_quantity(data.get("quantity"), default=1.0)
            if "unit" in data:
                row.unit = as_unit(data.get("unit"))
            if "unitPricePennies" in data:
                row.unit_price_pennies = as_optional_money(data.get("unitPricePennies"))
            if "lineTotalPennies" in data:
                row.line_total_pennies = as_optional_money(data.get("lineTotalPennies"))
            if "excluded" in data:
                row.excluded = as_bool(data.get("excluded"), "excluded")
                row.accepted = not row.excluded
            if "accepted" in data:
                row.accepted = as_bool(data.get("accepted"), "accepted")
            row.updated_at = now_iso()
            session.add(row)
            receipt.status = "saved" if was_saved else "reviewed"
            receipt.updated_at = row.updated_at
            session.add(receipt)
            if was_saved:
                self._sync_saved_receipt_history(session, receipt, row.updated_at)
            session.commit()
            return self._receipt_json(session, receipt)

    # ── accept -> history ───────────────────────────────────────────────
    def accept_receipt(self, receipt_id: str) -> dict[str, Any]:
        with Session(self.engine) as session:
            receipt = self._require_receipt(session, receipt_id)
            if receipt.status == "saved":
                # Idempotent: a second submit (double-tap, retried request)
                # must not create a second trip.
                raise ReceiptStateError("Receipt has already been saved to history")
            if receipt.status not in EDITABLE_STATUSES:
                raise ReceiptStateError(f"Receipt can't be saved while status is {receipt.status}")

            rows = session.exec(
                select(models.ReceiptItem).where(
                    models.ReceiptItem.receipt_id == receipt.id,
                    models.ReceiptItem.accepted == True,  # noqa: E712
                    models.ReceiptItem.excluded == False,  # noqa: E712
                )
            ).all()
            if not rows:
                raise ValueError("Accept at least one item before saving to history")

            ts = now_iso()
            trip_date = as_optional_date(receipt.purchase_date) or today_iso()
            total_pennies = as_optional_money(receipt.total_pennies)
            trip = models.ShoppingTrip(
                shop_id=receipt.shop_id,
                trip_date=trip_date,
                source="receipt",
                total_pennies=total_pennies,
                currency=receipt.currency,
                created_by_user_id=None,
                started_at=ts,
                completed_at=ts,
                created_at=ts,
                updated_at=ts,
            )
            session.add(trip)
            session.flush()

            for row in rows:
                name = as_item_name(row.name or row.raw_text)
                quantity = as_quantity(row.quantity, default=1.0)
                unit_price = as_optional_money(row.unit_price_pennies)
                line_total = as_optional_money(row.line_total_pennies)
                catalog = ensure_catalog_item(
                    session,
                    name,
                    shop_id=receipt.shop_id,
                    quantity=quantity,
                    unit=row.unit or "",
                    increment_use=True,
                )
                row.item_id = catalog.id
                session.add(row)
                session.add(models.ShoppingTripItem(
                    trip_id=trip.id,
                    item_id=catalog.id,
                    name=name,
                    quantity=quantity,
                    unit=row.unit or "",
                    shop_id=receipt.shop_id,
                    unit_price_pennies=unit_price,
                    line_total_pennies=line_total,
                    currency=receipt.currency,
                    bought_at=ts,
                    source_receipt_item_id=row.id,
                    created_at=ts,
                ))

            receipt.status = "saved"
            receipt.shopping_trip_id = trip.id
            receipt.reviewed_at = ts
            receipt.updated_at = ts
            session.add(receipt)
            session.commit()
            return {"success": True, "tripId": str(trip.id), "itemCount": len(rows)}

    # ── history reads / edits ──────────────────────────────────────────
    def list_history(self) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            trips = session.exec(
                select(models.ShoppingTrip).order_by(
                    models.ShoppingTrip.trip_date.desc(), models.ShoppingTrip.id.desc()
                )
            ).all()
            return [self._history_trip_json(session, trip) for trip in trips]

    def get_history_trip(self, trip_id: str) -> dict[str, Any]:
        with Session(self.engine) as session:
            return self._history_trip_json(session, self._require_trip(session, trip_id))

    def patch_history_trip(self, trip_id: str, data: dict[str, Any]) -> dict[str, Any]:
        with Session(self.engine) as session:
            trip = self._require_trip(session, trip_id)
            if "shopId" in data:
                shop_id = data.get("shopId") or None
                if shop_id is not None and session.get(models.Shop, shop_id) is None:
                    raise ValueError(f"Unknown shop: {shop_id}")
                trip.shop_id = shop_id
            if "tripDate" in data:
                trip.trip_date = as_optional_date(data.get("tripDate")) or today_iso()
            if "totalPennies" in data:
                trip.total_pennies = as_optional_money(data.get("totalPennies"))
            ts = now_iso()
            trip.updated_at = ts
            session.add(trip)

            receipt = self._receipt_for_trip(session, trip.id)
            if receipt is not None:
                receipt.shop_id = trip.shop_id
                receipt.purchase_date = trip.trip_date
                receipt.total_pennies = trip.total_pennies
                receipt.updated_at = ts
                session.add(receipt)
            for item in session.exec(
                select(models.ShoppingTripItem).where(models.ShoppingTripItem.trip_id == trip.id)
            ).all():
                item.shop_id = trip.shop_id
                session.add(item)
            session.commit()
            return self._history_trip_json(session, trip)

    def update_history_item(self, trip_id: str, item_id: str, data: dict[str, Any]) -> dict[str, Any]:
        with Session(self.engine) as session:
            trip = self._require_trip(session, trip_id)
            item = session.get(models.ShoppingTripItem, _pk(item_id))
            if item is None or item.trip_id != trip.id:
                raise ReceiptNotFound(f"No history item {item_id} on trip {trip_id}")

            if "name" in data:
                item.name = as_item_name(data.get("name"))
            if "quantity" in data:
                item.quantity = as_quantity(data.get("quantity"), default=1.0)
            if "unit" in data:
                item.unit = as_unit(data.get("unit")) or ""
            if "unitPricePennies" in data:
                item.unit_price_pennies = as_optional_money(data.get("unitPricePennies"))
            if "lineTotalPennies" in data:
                item.line_total_pennies = as_optional_money(data.get("lineTotalPennies"))

            catalog = ensure_catalog_item(
                session, item.name, shop_id=trip.shop_id, quantity=item.quantity,
                unit=item.unit, increment_use=False,
            )
            item.item_id = catalog.id
            session.add(item)
            ts = now_iso()
            trip.updated_at = ts
            session.add(trip)

            if item.source_receipt_item_id is not None:
                receipt_item = session.get(models.ReceiptItem, item.source_receipt_item_id)
                if receipt_item is not None:
                    receipt_item.name = item.name
                    receipt_item.quantity = item.quantity
                    receipt_item.unit = item.unit
                    receipt_item.unit_price_pennies = item.unit_price_pennies
                    receipt_item.line_total_pennies = item.line_total_pennies
                    receipt_item.item_id = catalog.id
                    receipt_item.updated_at = ts
                    session.add(receipt_item)
            receipt = self._receipt_for_trip(session, trip.id)
            if receipt is not None:
                receipt.updated_at = ts
                session.add(receipt)
            session.commit()
            return self._history_trip_json(session, trip)

    def delete_history_item(self, trip_id: str, item_id: str) -> dict[str, Any]:
        with Session(self.engine) as session:
            trip = self._require_trip(session, trip_id)
            items = session.exec(
                select(models.ShoppingTripItem).where(models.ShoppingTripItem.trip_id == trip.id)
            ).all()
            item = next((row for row in items if row.id == _pk(item_id)), None)
            if item is None:
                raise ReceiptNotFound(f"No history item {item_id} on trip {trip_id}")
            if len(items) == 1:
                raise ReceiptStateError("Delete the whole history entry instead of its last item")
            ts = now_iso()
            if item.source_receipt_item_id is not None:
                receipt_item = session.get(models.ReceiptItem, item.source_receipt_item_id)
                if receipt_item is not None:
                    receipt_item.excluded = True
                    receipt_item.accepted = False
                    receipt_item.updated_at = ts
                    session.add(receipt_item)
            session.delete(item)
            trip.updated_at = ts
            session.add(trip)
            receipt = self._receipt_for_trip(session, trip.id)
            if receipt is not None:
                receipt.updated_at = ts
                session.add(receipt)
            session.commit()
            return self._history_trip_json(session, trip)

    def delete_history_trip(self, trip_id: str) -> dict[str, Any]:
        with Session(self.engine) as session:
            trip = self._require_trip(session, trip_id)
            receipt = self._receipt_for_trip(session, trip.id)
            if receipt is not None:
                session.delete(receipt)
            session.delete(trip)
            session.commit()
            return {"success": True, "deletedReceipt": receipt is not None}

    # ── helpers ─────────────────────────────────────────────────────────
    @staticmethod
    def _require_receipt(session: Session, receipt_id: str) -> models.Receipt:
        receipt = session.get(models.Receipt, _pk(receipt_id))
        if receipt is None:
            raise ReceiptNotFound(f"No receipt {receipt_id}")
        return receipt

    @staticmethod
    def _require_mutable(receipt: models.Receipt) -> None:
        if receipt.status not in MUTABLE_STATUSES:
            raise ReceiptStateError(f"A receipt with status {receipt.status} can't be edited")

    @staticmethod
    def _require_trip(session: Session, trip_id: str) -> models.ShoppingTrip:
        trip = session.get(models.ShoppingTrip, _pk(trip_id))
        if trip is None:
            raise ReceiptNotFound(f"No history trip {trip_id}")
        return trip

    @staticmethod
    def _receipt_for_trip(session: Session, trip_id: int) -> models.Receipt | None:
        return session.exec(
            select(models.Receipt).where(models.Receipt.shopping_trip_id == trip_id)
        ).first()

    def _sync_saved_receipt_history(self, session: Session, receipt: models.Receipt, ts: str) -> None:
        if receipt.shopping_trip_id is None:
            raise ReceiptStateError("Saved receipt is missing its history link")
        trip = session.get(models.ShoppingTrip, receipt.shopping_trip_id)
        if trip is None:
            raise ReceiptStateError("Saved receipt history entry no longer exists")
        receipt_rows = session.exec(
            select(models.ReceiptItem).where(
                models.ReceiptItem.receipt_id == receipt.id,
                models.ReceiptItem.accepted == True,  # noqa: E712
                models.ReceiptItem.excluded == False,  # noqa: E712
            ).order_by(models.ReceiptItem.line_no, models.ReceiptItem.id)
        ).all()
        if not receipt_rows:
            raise ReceiptStateError("A saved receipt must keep at least one history item")

        for old_item in session.exec(
            select(models.ShoppingTripItem).where(models.ShoppingTripItem.trip_id == trip.id)
        ).all():
            session.delete(old_item)
        trip.shop_id = receipt.shop_id
        trip.trip_date = as_optional_date(receipt.purchase_date) or today_iso()
        trip.total_pennies = as_optional_money(receipt.total_pennies)
        trip.currency = receipt.currency
        trip.updated_at = ts
        session.add(trip)
        session.flush()
        bought_at = trip.completed_at or receipt.reviewed_at or ts
        for row in receipt_rows:
            name = as_item_name(row.name or row.raw_text)
            quantity = as_quantity(row.quantity, default=1.0)
            unit = row.unit or ""
            catalog = ensure_catalog_item(
                session, name, shop_id=receipt.shop_id, quantity=quantity,
                unit=unit, increment_use=False,
            )
            row.item_id = catalog.id
            session.add(row)
            session.add(models.ShoppingTripItem(
                trip_id=trip.id,
                item_id=catalog.id,
                name=name,
                quantity=quantity,
                unit=unit,
                shop_id=receipt.shop_id,
                unit_price_pennies=as_optional_money(row.unit_price_pennies),
                line_total_pennies=as_optional_money(row.line_total_pennies),
                currency=receipt.currency,
                bought_at=bought_at,
                source_receipt_item_id=row.id,
                created_at=ts,
            ))

    @classmethod
    def _history_trip_json(cls, session: Session, trip: models.ShoppingTrip) -> dict[str, Any]:
        rows = session.exec(
            select(models.ShoppingTripItem).where(
                models.ShoppingTripItem.trip_id == trip.id
            ).order_by(models.ShoppingTripItem.id)
        ).all()
        receipt = cls._receipt_for_trip(session, trip.id)
        return {
            "id": str(trip.id),
            "receiptId": str(receipt.id) if receipt is not None else None,
            "shopId": trip.shop_id,
            "tripDate": trip.trip_date,
            "source": trip.source,
            "totalPennies": trip.total_pennies,
            "currency": trip.currency,
            "updatedAt": trip.updated_at,
            "items": [{
                "id": str(row.id),
                "name": row.name,
                "quantity": row.quantity,
                "unit": row.unit,
                "shopId": row.shop_id,
                "unitPricePennies": row.unit_price_pennies,
                "lineTotalPennies": row.line_total_pennies,
                "boughtAt": row.bought_at,
            } for row in rows],
        }

    @staticmethod
    def _receipt_summary_json(session: Session, receipt: models.Receipt) -> dict[str, Any]:
        item_count = len(session.exec(
            select(models.ReceiptItem).where(
                models.ReceiptItem.receipt_id == receipt.id,
                models.ReceiptItem.accepted == True,  # noqa: E712
                models.ReceiptItem.excluded == False,  # noqa: E712
            )
        ).all())
        return {
            "id": str(receipt.id),
            "status": receipt.status,
            "shopId": receipt.shop_id,
            "purchaseDate": receipt.purchase_date,
            "itemCount": item_count,
            "subtotalPennies": receipt.subtotal_pennies,
            "totalPennies": receipt.total_pennies,
            "currency": receipt.currency,
            "ocrEngine": receipt.ocr_engine,
            "createdAt": receipt.created_at,
            "updatedAt": receipt.updated_at,
        }

    @classmethod
    def _receipt_json(cls, session: Session, receipt: models.Receipt) -> dict[str, Any]:
        rows = session.exec(
            select(models.ReceiptItem).where(
                models.ReceiptItem.receipt_id == receipt.id
            ).order_by(models.ReceiptItem.line_no, models.ReceiptItem.id)
        ).all()
        summary = cls._receipt_summary_json(session, receipt)
        summary["items"] = [
            {
                "id": str(row.id),
                "rawText": row.raw_text,
                "name": row.name,
                "quantity": row.quantity,
                "unit": row.unit,
                "unitPricePennies": row.unit_price_pennies,
                "lineTotalPennies": row.line_total_pennies,
                "category": row.category,
                "confidence": row.confidence,
                "excluded": row.excluded,
                "accepted": row.accepted,
            }
            for row in rows
        ]
        return summary
