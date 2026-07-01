from __future__ import annotations

import io
import sqlite3
from pathlib import Path
import tempfile
import unittest

from PIL import Image
from fastapi.testclient import TestClient
import pillow_heif

from src.shopping_list.app import create_app
from src.shopping_list.auth import hash_password
from src.shopping_list.config import DOCS_DIR, TEMPLATES_DIR, Settings
from src.shopping_list.db import bootstrap
from src.shopping_list.receipt_images import InvalidReceiptImage, process_upload
from src.shopping_list.receipts_service import ReceiptService, ReceiptStateError

pillow_heif.register_heif_opener()

ORIGIN = "http://127.0.0.1:8770"


def _upload(client: TestClient, raw: bytes | None = None, *, origin: str | None = ORIGIN):
    headers = {
        "content-type": "image/jpeg",
        "x-receipt-filename": "receipt.jpg",
    }
    if origin is not None:
        headers["origin"] = origin
    return client.post("/api/receipts", content=raw or _jpeg_bytes(), headers=headers)


def _jpeg_bytes(size: tuple[int, int] = (800, 600), color: tuple[int, int, int] = (10, 20, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="JPEG")
    return buf.getvalue()


def _heic_bytes(size: tuple[int, int] = (400, 300)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=(50, 60, 70)).save(buf, format="HEIF")
    return buf.getvalue()


class ReceiptImageProcessingTests(unittest.TestCase):
    def test_valid_jpeg_is_normalised_and_hashed(self) -> None:
        result = process_upload(_jpeg_bytes((3000, 2000)))
        self.assertEqual(result.width, 1600)  # downscaled to the long-edge target
        self.assertEqual(len(result.content_sha256), 64)
        # Re-decoding the output must itself be a plain JPEG with no leftover EXIF.
        out = Image.open(io.BytesIO(result.jpeg_bytes))
        self.assertEqual(out.format, "JPEG")
        self.assertNotIn("exif", out.info)

    def test_heic_upload_is_converted_to_jpeg(self) -> None:
        result = process_upload(_heic_bytes())
        out = Image.open(io.BytesIO(result.jpeg_bytes))
        self.assertEqual(out.format, "JPEG")

    def test_garbage_bytes_are_rejected(self) -> None:
        with self.assertRaises(InvalidReceiptImage):
            process_upload(b"this is not an image")

    def test_empty_upload_is_rejected(self) -> None:
        with self.assertRaises(InvalidReceiptImage):
            process_upload(b"")

    def test_oversize_pixel_dimensions_are_rejected_before_decode(self) -> None:
        # A tiny PNG that *claims* a huge size via a crafted header would be the
        # real decompression-bomb case; here we just confirm the guard fires for
        # a real (if impractically large to construct) oversize image by
        # shrinking the threshold instead of building a multi-gigapixel fixture.
        import src.shopping_list.receipt_images as ri

        original_limit = ri.MAX_DECODED_PIXELS
        ri.MAX_DECODED_PIXELS = 100
        try:
            with self.assertRaises(InvalidReceiptImage):
                process_upload(_jpeg_bytes((800, 600)))
        finally:
            ri.MAX_DECODED_PIXELS = original_limit


class ReceiptServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.engines = []

    def tearDown(self) -> None:
        for engine in self.engines:
            engine.dispose()

    def _service(self) -> ReceiptService:
        tmp = tempfile.mkdtemp()
        engine = bootstrap(Path(tmp) / "app.sqlite")
        self.engines.append(engine)
        return ReceiptService(engine)

    async def test_upload_dedupes_identical_image_without_creating_a_second_row(self) -> None:
        service = self._service()
        raw = _jpeg_bytes()

        first = await service.create_receipt(raw, original_filename="a.jpg")
        second = await service.create_receipt(raw, original_filename="a-again.jpg")

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(service.list_receipts()), 1)

    async def test_manual_add_item_then_accept_creates_exactly_one_trip(self) -> None:
        service = self._service()
        receipt = await service.create_receipt(_jpeg_bytes(), original_filename="r.jpg")
        service.add_item(receipt["id"], {"name": "Milk", "quantity": 1, "unit": "L", "unitPricePennies": 145})
        service.add_item(receipt["id"], {"name": "Bread", "quantity": 1})

        result = service.accept_receipt(receipt["id"])

        self.assertTrue(result["success"])
        self.assertEqual(result["itemCount"], 2)
        saved = service.get_receipt(receipt["id"])
        self.assertEqual(saved["status"], "saved")

    async def test_accept_is_idempotent(self) -> None:
        service = self._service()
        receipt = await service.create_receipt(_jpeg_bytes(), original_filename="r.jpg")
        service.add_item(receipt["id"], {"name": "Milk"})
        service.accept_receipt(receipt["id"])

        with self.assertRaises(ReceiptStateError):
            service.accept_receipt(receipt["id"])

    async def test_accept_requires_at_least_one_included_item(self) -> None:
        service = self._service()
        receipt = await service.create_receipt(_jpeg_bytes(), original_filename="r.jpg")

        with self.assertRaises(ValueError):
            service.accept_receipt(receipt["id"])

    async def test_excluded_item_is_not_carried_into_history(self) -> None:
        service = self._service()
        receipt = await service.create_receipt(_jpeg_bytes(), original_filename="r.jpg")
        keep = service.add_item(receipt["id"], {"name": "Milk"})
        drop = service.add_item(receipt["id"], {"name": "Offer line"})
        drop_id = drop["items"][-1]["id"]
        service.update_item(receipt["id"], drop_id, {"excluded": True})

        result = service.accept_receipt(receipt["id"])

        self.assertEqual(result["itemCount"], 1)

    async def test_accept_trip_total_counts_only_included_rows(self) -> None:
        service = self._service()
        receipt = await service.create_receipt(_jpeg_bytes(), original_filename="r.jpg")
        service.add_item(receipt["id"], {"name": "Milk", "quantity": 2, "unitPricePennies": 145})
        service.add_item(receipt["id"], {"name": "Bread", "lineTotalPennies": 120})
        dropped = service.add_item(receipt["id"], {"name": "Crisps", "lineTotalPennies": 999})
        drop_id = dropped["items"][-1]["id"]
        service.update_item(receipt["id"], drop_id, {"excluded": True})
        service.patch_receipt(receipt["id"], {"totalPennies": 5000})  # printed paper total

        service.accept_receipt(receipt["id"])
        trip = service.list_history()[0]

        # 2 × £1.45 derives a £2.90 line total, plus £1.20 for the bread. The
        # removed £9.99 row and the printed £50 paper total must not count.
        self.assertEqual(trip["totalPennies"], 410)
        milk = next(i for i in trip["items"] if i["name"] == "Milk")
        bread = next(i for i in trip["items"] if i["name"] == "Bread")
        self.assertEqual(milk["lineTotalPennies"], 290)
        self.assertEqual(milk["unitPricePennies"], 145)
        self.assertEqual(bread["unitPricePennies"], 120)  # derived back from the line total

    async def test_saved_receipt_exclusion_recomputes_trip_total(self) -> None:
        service = self._service()
        receipt = await service.create_receipt(_jpeg_bytes(), original_filename="r.jpg")
        service.add_item(receipt["id"], {"name": "Milk", "lineTotalPennies": 145})
        added = service.add_item(receipt["id"], {"name": "Bread", "lineTotalPennies": 120})
        bread_id = added["items"][-1]["id"]
        service.accept_receipt(receipt["id"])
        self.assertEqual(service.list_history()[0]["totalPennies"], 265)

        service.update_item(receipt["id"], bread_id, {"excluded": True})

        self.assertEqual(service.list_history()[0]["totalPennies"], 145)

    async def test_history_item_price_edit_recomputes_trip_total(self) -> None:
        service = self._service()
        receipt = await service.create_receipt(_jpeg_bytes(), original_filename="r.jpg")
        service.add_item(receipt["id"], {"name": "Milk", "lineTotalPennies": 145})
        service.add_item(receipt["id"], {"name": "Bread", "lineTotalPennies": 120})
        service.accept_receipt(receipt["id"])
        trip = service.list_history()[0]
        milk = next(i for i in trip["items"] if i["name"] == "Milk")

        updated = service.update_history_item(trip["id"], milk["id"], {"lineTotalPennies": 200})

        self.assertEqual(updated["totalPennies"], 320)

    async def test_discard_unsaved_receipt_does_not_touch_history(self) -> None:
        service = self._service()
        receipt = await service.create_receipt(_jpeg_bytes(), original_filename="r.jpg")
        service.add_item(receipt["id"], {"name": "Milk"})

        service.discard_receipt(receipt["id"])

        self.assertEqual(service.list_receipts(), [])

    async def test_discard_saved_receipt_removes_linked_history(self) -> None:
        service = self._service()
        receipt = await service.create_receipt(_jpeg_bytes(), original_filename="r.jpg")
        service.add_item(receipt["id"], {"name": "Milk"})
        service.accept_receipt(receipt["id"])

        service.discard_receipt(receipt["id"])

        self.assertEqual(service.list_receipts(), [])
        self.assertEqual(service.list_history(), [])

    async def test_saved_receipt_edits_rebuild_linked_history_atomically(self) -> None:
        service = self._service()
        receipt = await service.create_receipt(_jpeg_bytes(), original_filename="r.jpg")
        added = service.add_item(receipt["id"], {
            "name": "Milk", "quantity": 1, "unit": "L", "lineTotalPennies": 145,
        })
        milk_id = added["items"][0]["id"]
        service.accept_receipt(receipt["id"])

        service.patch_receipt(receipt["id"], {
            "shopId": "aldi", "purchaseDate": "2026-06-20", "totalPennies": 345,
        })
        service.update_item(receipt["id"], milk_id, {
            "name": "Whole milk", "quantity": 2, "unit": "L", "lineTotalPennies": 290,
        })
        service.add_item(receipt["id"], {"name": "Bread", "quantity": 1, "lineTotalPennies": 55})

        saved = service.get_receipt(receipt["id"])
        history = service.list_history()[0]
        self.assertEqual(saved["status"], "saved")
        self.assertEqual(history["receiptId"], receipt["id"])
        self.assertEqual(history["shopId"], "aldi")
        self.assertEqual(history["tripDate"], "2026-06-20")
        self.assertEqual(history["totalPennies"], 345)
        self.assertEqual(
            [(row["name"], row["quantity"], row["lineTotalPennies"]) for row in history["items"]],
            [("Whole milk", 2.0, 290), ("Bread", 1.0, 55)],
        )

    async def test_saved_receipt_cannot_remove_its_last_history_item(self) -> None:
        service = self._service()
        receipt = await service.create_receipt(_jpeg_bytes(), original_filename="r.jpg")
        added = service.add_item(receipt["id"], {"name": "Milk"})
        item_id = added["items"][0]["id"]
        service.accept_receipt(receipt["id"])

        with self.assertRaises(ReceiptStateError):
            service.update_item(receipt["id"], item_id, {"excluded": True})

        self.assertEqual(len(service.list_history()[0]["items"]), 1)

    async def test_history_edits_flow_back_to_linked_receipt(self) -> None:
        service = self._service()
        receipt = await service.create_receipt(_jpeg_bytes(), original_filename="r.jpg")
        service.add_item(receipt["id"], {"name": "Milk", "quantity": 1, "lineTotalPennies": 145})
        accepted = service.accept_receipt(receipt["id"])
        trip_id = accepted["tripId"]
        history_item_id = service.get_history_trip(trip_id)["items"][0]["id"]

        service.patch_history_trip(trip_id, {
            "shopId": "lidl", "tripDate": "2026-06-21", "totalPennies": 300,
        })
        service.update_history_item(trip_id, history_item_id, {
            "name": "Oat milk", "quantity": 2, "unit": "L", "lineTotalPennies": 300,
        })

        linked = service.get_receipt(receipt["id"])
        self.assertEqual(linked["shopId"], "lidl")
        self.assertEqual(linked["purchaseDate"], "2026-06-21")
        self.assertEqual(linked["totalPennies"], 300)
        self.assertEqual(linked["items"][0]["name"], "Oat milk")
        self.assertEqual(linked["items"][0]["quantity"], 2.0)

    async def test_delete_history_trip_removes_linked_receipt(self) -> None:
        service = self._service()
        receipt = await service.create_receipt(_jpeg_bytes(), original_filename="r.jpg")
        service.add_item(receipt["id"], {"name": "Milk"})
        trip_id = service.accept_receipt(receipt["id"])["tripId"]

        result = service.delete_history_trip(trip_id)

        self.assertTrue(result["deletedReceipt"])
        self.assertEqual(service.list_receipts(), [])
        self.assertEqual(service.list_history(), [])

    async def test_delete_one_history_item_excludes_linked_receipt_row(self) -> None:
        service = self._service()
        receipt = await service.create_receipt(_jpeg_bytes(), original_filename="r.jpg")
        service.add_item(receipt["id"], {"name": "Milk"})
        service.add_item(receipt["id"], {"name": "Bread"})
        trip_id = service.accept_receipt(receipt["id"])["tripId"]
        history_item_id = service.get_history_trip(trip_id)["items"][0]["id"]

        remaining = service.delete_history_item(trip_id, history_item_id)

        self.assertEqual(len(remaining["items"]), 1)
        linked = service.get_receipt(receipt["id"])
        self.assertEqual(sum(not row["excluded"] for row in linked["items"]), 1)

    async def test_stored_path_is_always_empty_no_image_bytes_persisted(self) -> None:
        service = self._service()
        receipt = await service.create_receipt(_jpeg_bytes(), original_filename="r.jpg")
        self.assertNotIn("storedPath", receipt)  # not even exposed, let alone populated

        # Confirm at the DB layer too, not just the JSON projection.
        from sqlmodel import Session, select

        from src.shopping_list import models

        with Session(service.engine) as session:
            row = session.exec(select(models.Receipt)).one()
            self.assertEqual(row.stored_path, "")

    async def test_update_item_on_wrong_receipt_is_rejected(self) -> None:
        from src.shopping_list.receipts_service import ReceiptNotFound

        service = self._service()
        r1 = await service.create_receipt(_jpeg_bytes((800, 600)), original_filename="r1.jpg")
        r2 = await service.create_receipt(_jpeg_bytes((801, 601)), original_filename="r2.jpg")
        added = service.add_item(r1["id"], {"name": "Milk"})
        item_id = added["items"][-1]["id"]

        with self.assertRaises(ReceiptNotFound):
            service.update_item(r2["id"], item_id, {"name": "Hacked"})

    async def test_invalid_dates_money_and_quantities_are_rejected(self) -> None:
        service = self._service()
        receipt = await service.create_receipt(_jpeg_bytes(), original_filename="r.jpg")

        for value in ("not-a-date", "2026-02-30", "2999-01-01"):
            with self.subTest(date=value), self.assertRaises(ValueError):
                service.patch_receipt(receipt["id"], {"purchaseDate": value})
        for value in (-1, 1.5, True):
            with self.subTest(money=value), self.assertRaises(ValueError):
                service.patch_receipt(receipt["id"], {"totalPennies": value})
        for value in (-1, 0, float("nan"), float("inf")):
            with self.subTest(quantity=value), self.assertRaises(ValueError):
                service.add_item(receipt["id"], {"name": "Milk", "quantity": value})

    async def test_boolean_patch_requires_a_real_json_boolean(self) -> None:
        service = self._service()
        receipt = await service.create_receipt(_jpeg_bytes(), original_filename="r.jpg")
        added = service.add_item(receipt["id"], {"name": "Milk"})
        item_id = added["items"][0]["id"]

        with self.assertRaises(ValueError):
            service.update_item(receipt["id"], item_id, {"excluded": "false"})

    def test_fresh_schema_has_one_content_hash_index(self) -> None:
        tmp = tempfile.mkdtemp()
        db_path = Path(tmp) / "app.sqlite"
        engine = bootstrap(db_path)
        engine.dispose()
        with sqlite3.connect(db_path) as conn:
            indexes = conn.execute("PRAGMA index_list(receipts)").fetchall()
        hash_indexes = [row for row in indexes if "content_sha256" in row[1]]
        self.assertEqual([row[1] for row in hash_indexes], ["ix_receipts_content_sha256"])


def _fake_registry(fakes: dict, fallback_order: tuple[str, ...] = ("a",)):
    from unittest.mock import MagicMock

    from src.shopping_list.receipt_extraction import ExtractorRegistry

    registry = ExtractorRegistry.__new__(ExtractorRegistry)
    registry._settings = MagicMock(fallback_order=fallback_order)
    registry._extractors = fakes
    return registry


class _StubExtractor:
    provider = "anthropic"
    model = "claude-haiku-4-5"

    def __init__(self, payload=None, *, error=None):
        self._payload = payload
        self._error = error

    async def extract(self, image_bytes, mime_type):
        if self._error is not None:
            raise self._error
        from src.shopping_list.receipt_extraction import validate_extraction_payload
        import json as _json
        return validate_extraction_payload(
            self._payload, provider=self.provider, model=self.model, raw_json=_json.dumps(self._payload),
        )


class ReceiptExtractionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.engines = []

    def tearDown(self) -> None:
        for engine in self.engines:
            engine.dispose()

    def _service_with_registry(self, registry) -> ReceiptService:
        tmp = tempfile.mkdtemp()
        engine = bootstrap(Path(tmp) / "app.sqlite")
        self.engines.append(engine)
        return ReceiptService(engine, extractor_registry=registry)

    async def test_successful_extraction_populates_items_and_excludes_non_item_lines(self) -> None:
        payload = {
            "shop_name_guess": None, "purchase_date": "2026-06-30", "currency": "GBP",
            "subtotal_pennies": 145, "total_pennies": 145,
            "lines": [
                {"raw_text": "MILK", "name": "Milk", "quantity": 1, "unit": "L",
                 "unit_price_pennies": 145, "line_total_pennies": 145, "category": "item", "confidence": 0.9},
                {"raw_text": "LOYALTY PTS", "name": None, "quantity": None, "unit": None,
                 "unit_price_pennies": None, "line_total_pennies": None, "category": "loyalty", "confidence": 0.5},
            ],
        }
        registry = _fake_registry({"a": _StubExtractor(payload)})
        service = self._service_with_registry(registry)

        receipt = await service.create_receipt(_jpeg_bytes(), original_filename="r.jpg")

        self.assertEqual(receipt["status"], "ready")
        self.assertEqual(len(receipt["items"]), 2)
        milk = next(i for i in receipt["items"] if i["category"] == "item")
        loyalty = next(i for i in receipt["items"] if i["category"] == "loyalty")
        self.assertTrue(milk["accepted"])
        self.assertFalse(milk["excluded"])
        self.assertFalse(loyalty["accepted"])
        self.assertTrue(loyalty["excluded"])
        self.assertEqual(receipt["itemCount"], 1)  # only the included item line counts

    async def test_shop_name_guess_auto_matches_existing_shop(self) -> None:
        payload = {**GOOD_PAYLOAD_FOR_TESTS(), "shop_name_guess": "Morrisons"}
        registry = _fake_registry({"a": _StubExtractor(payload)})
        service = self._service_with_registry(registry)

        receipt = await service.create_receipt(_jpeg_bytes(), original_filename="r.jpg")

        self.assertEqual(receipt["shopId"], "morrisons")

    async def test_all_providers_failing_marks_receipt_failed_and_records_attempts(self) -> None:
        from src.shopping_list.receipt_extraction import ExtractionRefused

        registry = _fake_registry({"a": _StubExtractor(error=ExtractionRefused("nope"))})
        service = self._service_with_registry(registry)

        receipt = await service.create_receipt(_jpeg_bytes(), original_filename="r.jpg")

        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["items"], [])

        from sqlmodel import Session, select

        from src.shopping_list import models

        with Session(service.engine) as session:
            attempts = session.exec(select(models.ReceiptExtractionAttempt)).all()
            self.assertEqual(len(attempts), 1)
            self.assertEqual(attempts[0].outcome, "refused")

    async def test_retry_replaces_existing_items(self) -> None:
        first_payload = GOOD_PAYLOAD_FOR_TESTS()
        registry = _fake_registry({"a": _StubExtractor(first_payload)})
        service = self._service_with_registry(registry)
        receipt = await service.create_receipt(_jpeg_bytes(), original_filename="r.jpg")
        self.assertEqual(len(receipt["items"]), 1)

        second_payload = {
            **GOOD_PAYLOAD_FOR_TESTS(),
            "lines": [
                {"raw_text": "BREAD", "name": "Bread", "quantity": 1, "unit": None,
                 "unit_price_pennies": 120, "line_total_pennies": 120, "category": "item", "confidence": 0.8},
            ],
        }
        registry._extractors["a"] = _StubExtractor(second_payload)

        retried = await service.retry_receipt(receipt["id"], _jpeg_bytes((801, 601)))

        self.assertEqual(len(retried["items"]), 1)
        self.assertEqual(retried["items"][0]["name"], "Bread")

    async def test_retry_rejected_once_saved(self) -> None:
        registry = _fake_registry({"a": _StubExtractor(GOOD_PAYLOAD_FOR_TESTS())})
        service = self._service_with_registry(registry)
        receipt = await service.create_receipt(_jpeg_bytes(), original_filename="r.jpg")
        service.accept_receipt(receipt["id"])

        with self.assertRaises(ReceiptStateError):
            await service.retry_receipt(receipt["id"], _jpeg_bytes((801, 601)))

    async def test_extraction_derives_missing_price_side_per_line(self) -> None:
        # Morrisons prints both unit price and line total; other receipts print
        # only one. Whichever side the model returns, both end up recorded.
        payload = {
            "shop_name_guess": None, "purchase_date": "2026-06-30", "currency": "GBP",
            "subtotal_pennies": 480, "total_pennies": 480,
            "lines": [
                {"raw_text": "CUCUMBER x3", "name": "Cucumber", "quantity": 3, "unit": None,
                 "unit_price_pennies": 80, "line_total_pennies": None, "category": "item", "confidence": 0.9},
                {"raw_text": "MILK x3", "name": "Milk", "quantity": 3, "unit": None,
                 "unit_price_pennies": None, "line_total_pennies": 240, "category": "item", "confidence": 0.9},
            ],
        }
        registry = _fake_registry({"a": _StubExtractor(payload)})
        service = self._service_with_registry(registry)

        receipt = await service.create_receipt(_jpeg_bytes(), original_filename="r.jpg")

        cucumber = next(i for i in receipt["items"] if i["name"] == "Cucumber")
        milk = next(i for i in receipt["items"] if i["name"] == "Milk")
        self.assertEqual(cucumber["lineTotalPennies"], 240)  # derived from 3 × 80p
        self.assertEqual(milk["unitPricePennies"], 80)       # derived from 240p / 3
        self.assertEqual(receipt["itemsTotalPennies"], 480)

    async def test_no_registry_configured_behaves_like_5a(self) -> None:
        service = self._service_with_registry(None)  # falls back to an empty/unconfigured registry

        receipt = await service.create_receipt(_jpeg_bytes(), original_filename="r.jpg")

        self.assertEqual(receipt["status"], "ready")
        self.assertEqual(receipt["items"], [])

    async def test_cancelled_upload_does_not_strand_receipt_in_processing(self) -> None:
        # A client disconnect (phone lock, dropped connection) cancels the
        # request handler while it awaits extraction. The receipt was already
        # committed as 'processing', so the shielded extraction must still run
        # to completion and record its outcome — not leave the receipt stuck
        # until the next service restart.
        import asyncio

        started = asyncio.Event()
        release = asyncio.Event()

        class _SlowExtractor(_StubExtractor):
            async def extract(self, image_bytes, mime_type):
                started.set()
                await release.wait()
                return await super().extract(image_bytes, mime_type)

        registry = _fake_registry({"a": _SlowExtractor(GOOD_PAYLOAD_FOR_TESTS())})
        service = self._service_with_registry(registry)

        task = asyncio.create_task(service.create_receipt(_jpeg_bytes(), original_filename="r.jpg"))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        release.set()
        from sqlmodel import Session, select

        from src.shopping_list import models

        receipt = None
        for _ in range(200):  # give the shielded background task time to finish
            await asyncio.sleep(0.01)
            with Session(service.engine) as session:
                receipt = session.exec(select(models.Receipt)).first()
                if receipt is not None and receipt.status != "processing":
                    break
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.status, "ready")
        with Session(service.engine) as session:
            items = session.exec(select(models.ReceiptItem)).all()
            attempts = session.exec(select(models.ReceiptExtractionAttempt)).all()
        self.assertEqual(len(items), 1)
        self.assertEqual([a.outcome for a in attempts], ["success"])


def GOOD_PAYLOAD_FOR_TESTS() -> dict:
    return {
        "shop_name_guess": None, "purchase_date": "2026-06-30", "currency": "GBP",
        "subtotal_pennies": 145, "total_pennies": 145,
        "lines": [{
            "raw_text": "MILK 1.45", "name": "Milk", "quantity": 1, "unit": "L",
            "unit_price_pennies": 145, "line_total_pennies": 145, "category": "item", "confidence": 0.95,
        }],
    }


def _make_client(tmp: str) -> TestClient:
    settings = Settings(
        docs_dir=DOCS_DIR,
        templates_dir=TEMPLATES_DIR,
        data_backend="sqlite",
        app_db=Path(tmp) / "app.sqlite",
        users={"jamie": hash_password("secret", salt=b"3" * 16, iterations=1_000)},
        session_db=Path(tmp) / "sessions.sqlite",
        max_upload_mb=1,
    )
    client = TestClient(create_app(settings=settings), base_url=ORIGIN)
    client.post("/login", data={"username": "jamie", "password": "secret"})
    return client


class ReceiptRouteTests(unittest.TestCase):
    def test_routes_require_login(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                docs_dir=DOCS_DIR,
                templates_dir=TEMPLATES_DIR,
                data_backend="sqlite",
                app_db=Path(tmp) / "app.sqlite",
                users={"jamie": hash_password("secret", salt=b"3" * 16, iterations=1_000)},
                session_db=Path(tmp) / "sessions.sqlite",
            )
            client = TestClient(create_app(settings=settings), base_url=ORIGIN)

            self.assertEqual(client.get("/api/receipts").status_code, 401)
            self.assertEqual(
                _upload(client).status_code,
                401,
            )

    def test_upload_without_origin_or_referer_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = _make_client(tmp)

            res = _upload(client, origin=None)

            self.assertEqual(res.status_code, 403)

    def test_upload_with_mismatched_origin_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = _make_client(tmp)

            res = _upload(client, origin="https://evil.example")

            self.assertEqual(res.status_code, 403)

    def test_get_routes_have_no_origin_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = _make_client(tmp)

            res = client.get("/api/receipts")

            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json(), {"receipts": []})

    def test_receipt_ai_options_route_is_empty_when_unconfigured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = _make_client(tmp)

            res = client.get("/api/receipt-ai/options")

            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json(), {"options": []})

    def test_retry_route_requires_login_and_origin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = _make_client(tmp)
            headers = {"origin": ORIGIN}
            upload = _upload(client)
            receipt_id = upload.json()["id"]

            no_origin = client.post(f"/api/receipts/{receipt_id}/retry", content=_jpeg_bytes())
            self.assertEqual(no_origin.status_code, 403)

            # Unconfigured (no AI options) — the route exists but has nothing to retry with.
            res = client.post(f"/api/receipts/{receipt_id}/retry", content=_jpeg_bytes(), headers=headers)
            self.assertEqual(res.status_code, 400)

    def test_upload_review_accept_round_trip_over_http(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = _make_client(tmp)
            headers = {"origin": ORIGIN}

            upload = _upload(client)
            self.assertEqual(upload.status_code, 201)
            receipt_id = upload.json()["id"]

            added = client.post(
                f"/api/receipts/{receipt_id}/items",
                json={"name": "Milk", "quantity": 1, "unit": "L"},
                headers=headers,
            )
            self.assertEqual(added.status_code, 201)

            accepted = client.post(f"/api/receipts/{receipt_id}/accept", headers=headers)
            self.assertEqual(accepted.status_code, 200)
            self.assertEqual(accepted.json()["itemCount"], 1)

            second_accept = client.post(f"/api/receipts/{receipt_id}/accept", headers=headers)
            self.assertEqual(second_accept.status_code, 409)

    def test_oversize_upload_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = _make_client(tmp)  # max_upload_mb=1
            oversized = b"\xff" * (2 * 1024 * 1024)

            res = _upload(client, oversized)

            self.assertEqual(res.status_code, 413)

    def test_get_unknown_receipt_is_404(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = _make_client(tmp)

            res = client.get("/api/receipts/999")

            self.assertEqual(res.status_code, 404)

    def test_discard_route_removes_unsaved_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = _make_client(tmp)
            headers = {"origin": ORIGIN}
            upload = _upload(client)
            receipt_id = upload.json()["id"]

            res = client.delete(f"/api/receipts/{receipt_id}", headers=headers)

            self.assertEqual(res.status_code, 200)
            self.assertEqual(client.get("/api/receipts").json(), {"receipts": []})

    def test_saved_receipt_and_history_rest_routes_stay_linked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = _make_client(tmp)
            headers = {"origin": ORIGIN}
            receipt_id = _upload(client).json()["id"]
            client.post(
                f"/api/receipts/{receipt_id}/items", json={"name": "Milk", "quantity": 1},
                headers=headers,
            )
            trip_id = client.post(f"/api/receipts/{receipt_id}/accept", headers=headers).json()["tripId"]
            history = client.get("/api/history")
            self.assertEqual(history.status_code, 200)
            item_id = history.json()["trips"][0]["items"][0]["id"]

            patched = client.patch(
                f"/api/history/{trip_id}/items/{item_id}",
                json={"name": "Oat milk", "quantity": 2}, headers=headers,
            )
            self.assertEqual(patched.status_code, 200)
            self.assertEqual(client.get(f"/api/receipts/{receipt_id}").json()["items"][0]["name"], "Oat milk")

            deleted = client.delete(f"/api/history/{trip_id}", headers=headers)
            self.assertEqual(deleted.status_code, 200)
            self.assertTrue(deleted.json()["deletedReceipt"])
            self.assertEqual(client.get("/api/receipts").json(), {"receipts": []})

    def test_history_mutations_require_login_and_same_origin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = _make_client(tmp)
            self.assertEqual(client.get("/api/history").status_code, 200)
            self.assertEqual(client.patch("/api/history/999", json={}).status_code, 403)
            self.assertEqual(client.delete("/api/history/999").status_code, 403)


if __name__ == "__main__":
    unittest.main()
