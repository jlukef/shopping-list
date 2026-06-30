from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from sqlmodel import Session, select
from starlette.datastructures import QueryParams

from src.shopping_list import models
from src.shopping_list.app import create_app
from src.shopping_list.auth import hash_password
from src.shopping_list.config import DOCS_DIR, TEMPLATES_DIR, Settings
from src.shopping_list.sqlite_api import SQLiteActionService


class SQLiteActionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "shopping.sqlite"
        self.service = SQLiteActionService(self.db_path)

    def tearDown(self) -> None:
        self.service.close()
        self._tmp.cleanup()

    def api(self, action: str, data: dict | None = None, **query: str):
        params = {"action": action, **query}
        if data is not None:
            params["data"] = json.dumps(data)
        return self.service.dispatch(QueryParams(params), username="jamie")

    def test_clean_start_contract_and_string_ids(self) -> None:
        shops = self.api("getShops")["shops"]
        self.assertEqual(
            [shop["id"] for shop in shops],
            ["morrisons", "aldi", "lidl", "butcher", "fruit-veg", "boots-superdrug", "other"],
        )
        self.assertEqual(self.api("getList"), {"items": []})

        added = self.api("addItem", {
            "item": "Whole milk", "quantity": 2, "unit": "litres", "shop": "aldi",
        })
        self.assertTrue(added["success"])
        self.assertIsInstance(added["id"], str)

        row = self.api("getList")["items"][0]
        self.assertEqual(row["id"], added["id"])
        self.assertEqual(row["quantity"], 2)
        self.assertEqual(row["unit"], "litres")
        self.assertEqual(row["shop"], "aldi")

        suggestions = self.api("getAutocomplete", q="milk")["items"]
        self.assertEqual(suggestions[0]["item"], "Whole milk")
        self.assertEqual(suggestions[0]["defaultQty"], 2)

    def test_bought_transition_preserves_history_once_and_clear_does_not_duplicate(self) -> None:
        item_id = self.api("addItem", {
            "item": "Bread", "quantity": 2, "unit": "loaves", "shop": "morrisons",
        })["id"]

        self.assertTrue(self.api("updateItem", {"id": item_id, "bought": True})["success"])
        self.assertTrue(self.api("updateItem", {"id": item_id, "bought": True})["success"])
        self.assertTrue(self.api("updateItem", {"id": item_id, "bought": False})["success"])
        self.assertTrue(self.api("updateItem", {"id": item_id, "bought": True})["success"])

        history = self.api("getHistory")["trips"]
        history_items = [item for trip in history for item in trip["items"]]
        self.assertEqual(len(history_items), 1)
        self.assertEqual(history_items[0]["item"], "Bread")
        self.assertEqual(history_items[0]["quantity"], 2)
        self.assertEqual(history_items[0]["unit"], "loaves")
        self.assertTrue(history_items[0]["boughtAt"].endswith("+00:00"))

        cleared = self.api("clearBought")
        self.assertEqual(cleared["removed"], 1)
        self.assertEqual(self.api("getList"), {"items": []})
        history_after = self.api("getHistory")["trips"]
        self.assertEqual(sum(len(trip["items"]) for trip in history_after), 1)

    def test_keyword_sort_layout_edit_and_shop_management(self) -> None:
        sorted_result = self.api("sortList", {
            "shop": "morrisons",
            "items": [
                {"id": "1", "item": "Milk"},
                {"id": "2", "item": "Apples"},
                {"id": "3", "item": "Bin bags"},
            ],
        })
        self.assertEqual(sorted_result["method"], "keywords")
        self.assertEqual([item["id"] for item in sorted_result["items"]], ["2", "1", "3"])

        saved = self.api("saveLayout", {
            "shop": "other",
            "departments": [
                {"name": "Counter", "order": 1, "keywords": "order,collection"},
                {"name": "Everything Else", "order": 2, "keywords": "other"},
            ],
        })
        self.assertTrue(saved["success"])
        layouts = self.api("getLayouts", shop="other")["layouts"]
        self.assertEqual([row["department"] for row in layouts], ["Counter", "Everything Else"])

        added_shop = self.api("addShop", {"name": "Farm Shop", "emoji": "🌾", "color": "#336633"})
        self.assertEqual(added_shop["id"], "farm-shop")
        farm_item = self.api("addItem", {"item": "Eggs", "shop": "farm-shop"})["id"]
        self.assertTrue(self.api("deleteShop", {"id": "farm-shop"})["success"])
        remaining = next(item for item in self.api("getList")["items"] if item["id"] == farm_item)
        self.assertEqual(remaining["shop"], "other")

    def test_history_survives_sqlite_rowid_reuse_after_delete(self) -> None:
        # shopping_list_items.id is a plain SQLite INTEGER PRIMARY KEY (no
        # AUTOINCREMENT), so once the table is emptied SQLite is free to reuse a
        # previously-deleted row's rowid for the next insert. _archive_list_item's
        # de-duplication key must not collide across two unrelated purchases just
        # because they happened to share a reused row id.
        first_id = self.api("addItem", {"item": "Milk", "shop": "morrisons"})["id"]
        self.assertTrue(self.api("updateItem", {"id": first_id, "bought": True})["success"])
        self.assertTrue(self.api("deleteItem", {"id": first_id})["success"])

        second_id = self.api("addItem", {"item": "Bread", "shop": "morrisons"})["id"]
        self.assertEqual(second_id, first_id, "test assumes SQLite reused the freed rowid")
        self.assertTrue(self.api("updateItem", {"id": second_id, "bought": True})["success"])

        history_items = [item for trip in self.api("getHistory")["trips"] for item in trip["items"]]
        names = sorted(item["item"] for item in history_items)
        self.assertEqual(
            names, ["Bread", "Milk"],
            "Bread's purchase must still be archived even though it reused Milk's deleted row id",
        )

    def test_clear_bought_archives_preexisting_unarchived_bought_row(self) -> None:
        with Session(self.service.engine) as session:
            now = "2026-06-30T12:00:00+00:00"
            row = models.ShoppingListItem(
                name="Carrots", quantity=3, unit="bags", shop_id="fruit-veg",
                bought=True, created_at=now, updated_at=now,
            )
            session.add(row)
            session.commit()

        self.assertEqual(self.api("clearBought")["removed"], 1)
        history = self.api("getHistory")["trips"]
        item = history[0]["items"][0]
        self.assertEqual((item["item"], item["quantity"], item["unit"]), ("Carrots", 3, "bags"))


class SQLiteAppRouteTests(unittest.TestCase):
    def test_authenticated_route_uses_sqlite_and_logged_out_route_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                docs_dir=DOCS_DIR,
                templates_dir=TEMPLATES_DIR,
                data_backend="sqlite",
                app_db=Path(tmp) / "shopping.sqlite",
                users={"jamie": hash_password("secret", salt=b"7" * 16, iterations=1_000)},
                session_db=Path(tmp) / "sessions.sqlite",
                cookie_secure=False,
            )
            with TestClient(create_app(settings=settings)) as client:
                self.assertEqual(client.get("/api?action=getList").status_code, 401)
                client.post("/login", data={"username": "jamie", "password": "secret"})
                response = client.get("/api", params={"action": "getShops"})

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["shops"][0]["id"], "morrisons")


if __name__ == "__main__":
    unittest.main()
