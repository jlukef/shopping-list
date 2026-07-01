"""Products screen: catalog listing with live purchase stats, and merging.

Merge contract (Jamie, 2026-07-02): stats are summed onto the surviving
product, all history repoints to it, the old names become aliases, and future
purchases under an old name count against the merged product.
"""

from __future__ import annotations

import io
from pathlib import Path
import tempfile
import unittest

from PIL import Image
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from src.shopping_list import models
from src.shopping_list.app import create_app
from src.shopping_list.auth import hash_password
from src.shopping_list.config import DOCS_DIR, TEMPLATES_DIR, Settings
from src.shopping_list.db import bootstrap
from src.shopping_list.products_service import ProductNotFound, ProductsService
from src.shopping_list.receipts_service import ReceiptService

ORIGIN = "http://127.0.0.1:8770"


def _jpeg_bytes(size: tuple[int, int] = (800, 600), color: tuple[int, int, int] = (10, 20, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="JPEG")
    return buf.getvalue()


class ProductsServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.engines = []

    def tearDown(self) -> None:
        for engine in self.engines:
            engine.dispose()

    def _engine(self):
        tmp = tempfile.mkdtemp()
        engine = bootstrap(Path(tmp) / "app.sqlite")
        self.engines.append(engine)
        return engine

    async def _save_receipt(self, receipts: ReceiptService, lines: list[dict], color=(10, 20, 30)) -> None:
        receipt = await receipts.create_receipt(_jpeg_bytes(color=color), original_filename="r.jpg")
        for line in lines:
            receipts.add_item(receipt["id"], line)
        receipts.accept_receipt(receipt["id"])

    async def test_list_products_reports_live_purchase_stats(self) -> None:
        engine = self._engine()
        receipts = ReceiptService(engine)
        products = ProductsService(engine)
        await self._save_receipt(receipts, [
            {"name": "Milk", "lineTotalPennies": 145},
            {"name": "Bread", "lineTotalPennies": 120},
        ])
        await self._save_receipt(receipts, [
            {"name": "Milk", "lineTotalPennies": 155},
        ], color=(40, 50, 60))

        listed = {p["name"]: p for p in products.list_products()}

        milk = listed["Milk"]
        self.assertEqual(milk["purchaseCount"], 2)
        self.assertEqual(milk["totalSpendPennies"], 300)
        self.assertEqual(milk["lastUnitPricePennies"], 155)  # most recent purchase wins
        self.assertEqual(listed["Bread"]["purchaseCount"], 1)

    async def test_merge_sums_stats_repoints_history_and_deletes_source(self) -> None:
        engine = self._engine()
        receipts = ReceiptService(engine)
        products = ProductsService(engine)
        await self._save_receipt(receipts, [{"name": "Cucumber", "lineTotalPennies": 80}])
        await self._save_receipt(receipts, [{"name": "MORR Cucumber", "lineTotalPennies": 75}], color=(40, 50, 60))

        listed = {p["name"]: p for p in products.list_products()}
        target_id, source_id = listed["Cucumber"]["id"], listed["MORR Cucumber"]["id"]
        result = products.merge_products(target_id, [source_id])

        self.assertEqual(result["merged"], 1)
        after = products.list_products()
        names = [p["name"] for p in after]
        self.assertIn("Cucumber", names)
        self.assertNotIn("MORR Cucumber", names)
        merged = next(p for p in after if p["name"] == "Cucumber")
        self.assertEqual(merged["purchaseCount"], 2)          # history repointed
        self.assertEqual(merged["totalSpendPennies"], 155)    # spend combined
        self.assertEqual(merged["useCount"], 2)               # use counts summed
        self.assertIn("morr cucumber", merged["aliases"])     # old name preserved as alias

    async def test_future_purchase_under_old_name_counts_against_merged_product(self) -> None:
        engine = self._engine()
        receipts = ReceiptService(engine)
        products = ProductsService(engine)
        await self._save_receipt(receipts, [{"name": "Cucumber", "lineTotalPennies": 80}])
        await self._save_receipt(receipts, [{"name": "MORR Cucumber", "lineTotalPennies": 75}], color=(40, 50, 60))
        listed = {p["name"]: p for p in products.list_products()}
        products.merge_products(listed["Cucumber"]["id"], [listed["MORR Cucumber"]["id"]])

        # A later receipt still prints the shop's own name for it.
        await self._save_receipt(receipts, [{"name": "MORR Cucumber", "lineTotalPennies": 85}], color=(70, 80, 90))

        after = products.list_products()
        merged = next(p for p in after if p["name"] == "Cucumber")
        self.assertEqual(merged["purchaseCount"], 3)
        self.assertEqual(merged["lastUnitPricePennies"], 85)
        self.assertNotIn("MORR Cucumber", [p["name"] for p in after])  # no new product created
        # The alias hit must not rename the merged product.
        with Session(engine) as session:
            item = session.exec(
                select(models.Item).where(models.Item.canonical_name == "cucumber")
            ).one()
            self.assertEqual(item.display_name, "Cucumber")

    async def test_merge_validation(self) -> None:
        engine = self._engine()
        receipts = ReceiptService(engine)
        products = ProductsService(engine)
        await self._save_receipt(receipts, [{"name": "Milk", "lineTotalPennies": 145}])
        milk_id = products.list_products()[0]["id"]

        with self.assertRaises(ProductNotFound):
            products.merge_products("9999", [milk_id])
        with self.assertRaises(ProductNotFound):
            products.merge_products(milk_id, ["9999"])
        with self.assertRaises(ValueError):
            products.merge_products(milk_id, [milk_id])  # nothing else selected
        with self.assertRaises(ValueError):
            products.merge_products(milk_id, [])


class ProductRouteTests(unittest.TestCase):
    def _settings(self, tmp: str) -> Settings:
        return Settings(
            docs_dir=DOCS_DIR,
            templates_dir=TEMPLATES_DIR,
            data_backend="sqlite",
            app_db=Path(tmp) / "app.sqlite",
            users={"jamie": hash_password("secret", salt=b"3" * 16, iterations=1_000)},
            session_db=Path(tmp) / "sessions.sqlite",
        )

    def test_products_routes_require_login(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(create_app(settings=self._settings(tmp)), base_url=ORIGIN)

            self.assertEqual(client.get("/api/products").status_code, 401)
            self.assertEqual(client.post("/api/products/merge", json={}).status_code, 401)

    def test_merge_requires_same_origin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(create_app(settings=self._settings(tmp)), base_url=ORIGIN)
            client.post("/login", data={"username": "jamie", "password": "secret"})

            res = client.post("/api/products/merge", json={"targetId": "1", "sourceIds": ["2"]})

            self.assertEqual(res.status_code, 403)

    def test_products_round_trip_via_routes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(create_app(settings=self._settings(tmp)), base_url=ORIGIN)
            client.post("/login", data={"username": "jamie", "password": "secret"})

            # Two list items via the legacy action API create catalog entries.
            client.get("/api", params={"action": "addItem", "data": '{"item": "Cucumber"}'})
            client.get("/api", params={"action": "addItem", "data": '{"item": "Cucumber Whole"}'})
            listed = client.get("/api/products").json()["products"]
            ids = {p["name"]: p["id"] for p in listed}
            self.assertEqual(len(ids), 2)

            res = client.post(
                "/api/products/merge",
                json={"targetId": ids["Cucumber"], "sourceIds": [ids["Cucumber Whole"]]},
                headers={"origin": ORIGIN},
            )

            self.assertEqual(res.status_code, 200)
            after = client.get("/api/products").json()["products"]
            self.assertEqual([p["name"] for p in after], ["Cucumber"])
            self.assertEqual(after[0]["useCount"], 2)
            self.assertIn("cucumber whole", after[0]["aliases"])
