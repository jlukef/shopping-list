import tempfile
import unittest
import json
from pathlib import Path

from fastapi.testclient import TestClient

from src.shopping_list.app import create_app
from src.shopping_list.config import Settings, DOCS_DIR, TEMPLATES_DIR
from src.shopping_list.auth import hash_password

def make_client_for_contract(tmp: str) -> TestClient:
    settings_kwargs = {
        "docs_dir": DOCS_DIR,
        "templates_dir": TEMPLATES_DIR,
        "apps_script_url": "", # Disable Apps Script to force local handling (or 503)
        "data_backend": "sqlite",
        "app_db": Path(tmp) / "shopping_list.sqlite",
        "users": {"testuser": hash_password("secret", salt=b"3" * 16, iterations=1_000)},
        "session_db": Path(tmp) / "sessions.sqlite",
        "cookie_secure": False,
    }
    settings = Settings(**settings_kwargs)
    app = create_app(settings=settings)
    client = TestClient(app)
    
    # Authenticate
    client.post("/login", data={"username": "testuser", "password": "secret"})
    return client


class FreshStartContractTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.client = make_client_for_contract(self._tmp)

    def tearDown(self):
        self.client.close()

    def _api_get(self, action: str, data: dict = None, **query_params) -> dict:
        # Real GET ?query=params and the JSON `data` envelope are different parts of
        # the contract (e.g. getAutocomplete's `q` and getLayouts' `shop` are real
        # query params, never inside `data`). Keep them distinct here so a test can't
        # accidentally "pass" by putting a parameter where the backend never looks.
        params = {"action": action, **query_params}
        if data is not None:
            params["data"] = json.dumps(data)
        res = self.client.get("/api", params=params)
        # We expect a 200 OK with JSON for a successful API call.
        # If this fails with 503, it means the SQLite backend is not yet implemented.
        if res.status_code == 503:
            self.fail(f"Backend not yet implemented for action {action} (Got 503)")
        self.assertEqual(res.status_code, 200, f"API action {action} failed with {res.status_code}: {res.text}")
        return res.json()

    def test_get_shops_returns_seven_default_shops(self):
        data = self._api_get("getShops")
        self.assertIn("shops", data)
        shops = data["shops"]
        self.assertEqual(len(shops), 7)
        self.assertEqual(shops[0]["id"], "morrisons")

    def test_empty_initial_list(self):
        data = self._api_get("getList")
        self.assertIn("items", data)
        self.assertEqual(len(data["items"]), 0, "Initial list should be empty.")

    def test_add_get_update_delete_item(self):
        # Add item
        add_res = self._api_get("addItem", {"item": "Milk", "quantity": 2, "unit": "litres", "shop": "aldi"})
        self.assertTrue(add_res.get("success"))
        item_id = add_res.get("id")
        self.assertIsNotNone(item_id, "addItem must return the new item ID")

        # Get list
        list_res = self._api_get("getList")
        items = list_res.get("items", [])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], item_id)
        self.assertEqual(items[0]["item"], "Milk")
        self.assertEqual(items[0]["quantity"], 2)
        self.assertEqual(items[0]["unit"], "litres")
        self.assertEqual(items[0]["shop"], "aldi")
        self.assertFalse(items[0].get("bought"))

        # Update item (mark bought)
        upd_res = self._api_get("updateItem", {"id": item_id, "bought": True})
        self.assertTrue(upd_res.get("success"))

        list_res2 = self._api_get("getList")
        self.assertTrue(list_res2["items"][0]["bought"])

        # Delete item
        del_res = self._api_get("deleteItem", {"id": item_id})
        self.assertTrue(del_res.get("success"))

        list_res3 = self._api_get("getList")
        self.assertEqual(len(list_res3["items"]), 0)

    def test_repeated_bought_true_does_not_duplicate_history(self):
        add_res = self._api_get("addItem", {"item": "Bread", "quantity": 2, "unit": "loaves", "shop": "morrisons"})
        item_id = add_res["id"]

        # Mark bought multiple times, including an unbought/rebought round trip.
        self._api_get("updateItem", {"id": item_id, "bought": True})
        self._api_get("updateItem", {"id": item_id, "bought": True})
        self._api_get("updateItem", {"id": item_id, "bought": False})
        self._api_get("updateItem", {"id": item_id, "bought": True})

        list_res = self._api_get("getList")
        self.assertEqual(len(list_res["items"]), 1)
        self.assertTrue(list_res["items"][0]["bought"])

        # getHistory exists now (Codex's sqlite_api.py) — assert exactly one durable
        # history row was archived, with the expected quantity/unit, and a real
        # bought-at timestamp, instead of only checking the list contract didn't break.
        history = self._api_get("getHistory")
        history_items = [item for trip in history["trips"] for item in trip["items"]]
        bread_items = [item for item in history_items if item["item"] == "Bread"]
        self.assertEqual(len(bread_items), 1, f"expected exactly one Bread history row, got {bread_items}")
        self.assertEqual(bread_items[0]["quantity"], 2)
        self.assertEqual(bread_items[0]["unit"], "loaves")
        self.assertEqual(bread_items[0]["shop"], "morrisons")
        # boughtAt must be a real, parseable UTC timestamp, not blank/placeholder.
        from datetime import datetime
        datetime.fromisoformat(bread_items[0]["boughtAt"])

    def test_clear_bought(self):
        # Add two items, buy one, clear bought
        id1 = self._api_get("addItem", {"item": "A", "quantity": 1, "shop": "other"})["id"]
        id2 = self._api_get("addItem", {"item": "B", "quantity": 1, "shop": "other"})["id"]
        
        self._api_get("updateItem", {"id": id1, "bought": True})
        
        clear_res = self._api_get("clearBought")
        self.assertTrue(clear_res.get("success"))
        
        list_res = self._api_get("getList")
        items = list_res.get("items", [])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], id2)
        self.assertFalse(items[0]["bought"])

    def test_autocomplete(self):
        # `q` is a real query param (see docs/app.js apiQ('getAutocomplete', {q})),
        # never inside the JSON `data` envelope — assert it actually filters by
        # adding a known item and querying for a substring of its name.
        self._api_get("addItem", {"item": "Whole milk", "quantity": 2, "unit": "litres", "shop": "aldi"})
        self._api_get("addItem", {"item": "Eggs", "quantity": 6, "shop": "morrisons"})

        res = self._api_get("getAutocomplete", q="mi")
        self.assertIn("items", res)
        names = [item["item"] for item in res["items"]]
        self.assertIn("Whole milk", names, f"expected 'Whole milk' to match q=mi, got {names}")
        self.assertNotIn("Eggs", names, f"unrelated item leaked into a filtered autocomplete result: {names}")

        match = next(item for item in res["items"] if item["item"] == "Whole milk")
        self.assertEqual(match["defaultQty"], 2)
        self.assertEqual(match["defaultUnit"], "litres")
        self.assertEqual(match["defaultShop"], "aldi")

    def test_layouts(self):
        # `shop` is a real query param (see docs/app.js apiQ('getLayouts', {shop})),
        # never inside the JSON `data` envelope. Assert the filter is actually
        # applied: every returned row belongs to the requested shop, and the
        # filtered count is smaller than the unfiltered (all-shops) count.
        all_layouts = self._api_get("getLayouts", shop="")["layouts"]
        self.assertGreater(len(all_layouts), 0, "seeded layouts should exist across all shops")

        res = self._api_get("getLayouts", shop="aldi")
        self.assertIn("layouts", res)
        self.assertGreater(len(res["layouts"]), 0)
        self.assertTrue(
            all(row["shop"] == "aldi" for row in res["layouts"]),
            f"getLayouts(shop=aldi) returned rows for other shops: {res['layouts']}",
        )
        self.assertLess(
            len(res["layouts"]), len(all_layouts),
            "filtering by shop should return fewer rows than the unfiltered total",
        )

    def test_unauthenticated_api_is_rejected(self):
        settings = Settings(
            apps_script_url="",
            data_backend="sqlite",
            app_db=Path(self._tmp) / "unauth-shopping.sqlite",
            session_db=Path(self._tmp) / "unauth-sessions.sqlite",
            cookie_secure=False,
        )
        with TestClient(create_app(settings=settings)) as client:
            res = client.get("/api?action=getList")
        self.assertEqual(res.status_code, 401, "Unauthenticated API request must be rejected.")

if __name__ == "__main__":
    unittest.main()
