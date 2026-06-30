import os
import tempfile
import unittest

from sqlmodel import Session, select

from src.shopping_list import db, importer, models


class ImporterTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.engine = db.bootstrap(os.path.join(self._tmp, "test.sqlite"))

    def tearDown(self):
        self.engine.dispose()

    def session(self):
        return Session(self.engine)

    def test_parse_timestamp_handles_common_formats(self):
        self.assertTrue(importer.parse_timestamp("2026-06-14").startswith("2026-06-14"))
        self.assertTrue(importer.parse_timestamp("2026-06-14T09:30:00Z").startswith("2026-06-14"))
        self.assertTrue(importer.parse_timestamp("14/06/2026").startswith("2026-06-14"))
        self.assertIsNone(importer.parse_timestamp("not a date"))
        self.assertIsNone(importer.parse_timestamp(""))

    def test_import_shops_upserts_idempotently(self):
        rows = [{"id": "morrisons", "name": "Morrisons", "emoji": "🏪", "color": "#0a0"}]
        with self.session() as s:
            importer.import_shops(s, rows)
            importer.import_shops(s, rows)  # second run must not duplicate
            shops = s.exec(select(models.Shop).where(models.Shop.id == "morrisons")).all()
        self.assertEqual(len(shops), 1)
        self.assertEqual(shops[0].name, "Morrisons")

    def test_import_items_canonicalises_and_dedupes(self):
        with self.session() as s:
            importer.import_items(s, [{
                "item": "  Semi  Skimmed   Milk ", "count": 7,
                "lastUsed": "2026-06-10T12:00:00Z", "defaultQty": 2,
                "defaultUnit": "pints", "category": "Dairy",
            }])
            importer.import_items(s, [{
                "item": "Semi Skimmed Milk", "count": 8,
                "lastUsed": "2026-06-11T12:00:00Z", "defaultQty": 4,
                "defaultUnit": "litres", "category": "Chilled",
            }])  # same canonical; changed source values must update
            items = s.exec(select(models.Item)).all()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].canonical_name, "semi skimmed milk")
        self.assertEqual(items[0].display_name, "Semi Skimmed Milk")
        self.assertEqual(items[0].use_count, 8)
        self.assertEqual(items[0].default_quantity, 4)
        self.assertEqual(items[0].default_unit, "litres")
        self.assertEqual(items[0].category, "Chilled")
        self.assertTrue(items[0].last_used_at.startswith("2026-06-11"))

    def test_import_list_replaces_and_links_items(self):
        with self.session() as s:
            importer.import_list(s, [
                {"item": "Bananas", "shop": "aldi", "quantity": 6, "unit": "", "bought": False},
                {"item": "Bleach", "shop": "aldi", "bought": "true"},
            ])
            # Re-import with a different list — should replace, not append.
            importer.import_list(s, [{
                "id": "legacy-list-uuid", "item": "Milk", "shop": "morrisons",
                "dateAdded": "2026-06-01T10:30:00Z",
            }])
            rows = s.exec(select(models.ShoppingListItem)).all()
            milk = rows[0]
        self.assertEqual(len(rows), 1)
        self.assertEqual(milk.name, "Milk")
        self.assertIsNotNone(milk.item_id)  # auto-created master item
        self.assertEqual(milk.source_ref, "legacy-list-uuid")
        self.assertTrue(milk.created_at.startswith("2026-06-01"))

    def test_import_list_replacement_is_atomic_on_bad_input(self):
        with self.session() as s:
            importer.import_list(s, [{"item": "Milk", "shop": "morrisons"}])
            with self.assertRaises(ValueError):
                importer.import_list(s, [{"item": "Broken", "quantity": "not-a-number"}])
            s.rollback()
            rows = s.exec(select(models.ShoppingListItem)).all()
        self.assertEqual([row.name for row in rows], ["Milk"])

    def test_import_layouts_splits_keywords(self):
        with self.session() as s:
            importer.import_layouts(s, "morrisons", [
                {"department": "Produce", "order": 0, "keywords": "fruit, veg, apple"},
            ])
            depts = s.exec(
                select(models.StoreLayoutDepartment).where(
                    models.StoreLayoutDepartment.shop_id == "morrisons"
                )
            ).all()
            kws = s.exec(
                select(models.StoreLayoutKeyword).where(
                    models.StoreLayoutKeyword.department_id == depts[0].id
                )
            ).all()
        self.assertEqual(len(depts), 1)
        self.assertEqual(sorted(k.keyword for k in kws), ["apple", "fruit", "veg"])

    def test_import_history_groups_into_trips_and_is_idempotent(self):
        rows = [
            {"item": "Milk", "quantity": 2, "unit": "pints", "shop": "morrisons", "dateBought": "2026-06-14T09:00:00Z"},
            {"item": "Bread", "quantity": 1, "unit": "loaf", "shop": "morrisons", "dateBought": "2026-06-14T09:00:00Z"},
            {"item": "Coffee", "quantity": 3, "unit": "jars", "shop": "aldi", "dateBought": "2026-06-10"},
                {"item": "Broken", "quantity": 1, "unit": "", "shop": "morrisons", "dateBought": "garbage"},
        ]
        with self.session() as s:
            res1 = importer.import_history(s, rows)
            res2 = importer.import_history(s, rows)  # idempotent
            trips = s.exec(select(models.ShoppingTrip)).all()
            trip_items = s.exec(select(models.ShoppingTripItem)).all()
        self.assertEqual(res1["imported"], 3)
        self.assertEqual(res1["unparseable"], 1)
        self.assertEqual(res2["imported"], 0)       # nothing new second time
        self.assertEqual(res2["skipped"], 3)
        self.assertEqual(len(trips), 2)             # (morrisons,14th) and (aldi,10th)
        self.assertEqual(len(trip_items), 3)
        milk = next(item for item in trip_items if item.name == "Milk")
        self.assertEqual(milk.quantity, 2)
        self.assertEqual(milk.unit, "pints")
        self.assertTrue(milk.bought_at.startswith("2026-06-14"))

    def test_import_history_preserves_identical_duplicate_products(self):
        row = {
            "item": "Milk", "quantity": 1, "unit": "bottle",
            "shop": "morrisons", "dateBought": "2026-06-14T09:00:00Z",
        }
        with self.session() as s:
            first = importer.import_history(s, [row, dict(row)])
            second = importer.import_history(s, [row, dict(row)])
            items = s.exec(select(models.ShoppingTripItem)).all()
        self.assertEqual(first["imported"], 2)
        self.assertEqual(second["skipped"], 2)
        self.assertEqual(len(items), 2)
        self.assertEqual(len({item.source_ref for item in items}), 2)

    def test_verify_import_fails_when_history_rows_were_not_imported(self):
        with self.session() as s:
            result = importer.import_history(s, [{
                "item": "Milk", "quantity": 1, "unit": "pint",
                "shop": "morrisons", "dateBought": "not-a-date",
            }])
            report = importer.verify_import(s, {"history": 1})
        self.assertEqual(result["unparseable"], 1)
        self.assertFalse(report["checks"]["row_counts_match"])
        self.assertFalse(report["ok"])

    def test_verify_import_passes_on_clean_data_and_flags_problems(self):
        with self.session() as s:
            importer.import_items(s, [{"item": "Milk"}])
            importer.import_list(s, [{"item": "Milk", "shop": "morrisons"}])
            good = importer.verify_import(s, {"list": 1})
            self.assertTrue(good["ok"])
            self.assertTrue(good["checks"]["no_orphan_shop_refs"])
            self.assertTrue(good["checks"]["fk_integrity_ok"])

            # A blank-name list item should trip the no_blank_item_names check.
            s.add(models.ShoppingListItem(
                name="", shop_id=None, created_at=db.now_iso(), updated_at=db.now_iso(),
            ))
            s.commit()
            bad = importer.verify_import(s)
        self.assertFalse(bad["ok"])
        self.assertFalse(bad["checks"]["no_blank_item_names"])


if __name__ == "__main__":
    unittest.main()
