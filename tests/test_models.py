import os
import tempfile
import unittest

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from src.shopping_list import db, models


class SchemaTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self._tmp, "test.sqlite")
        self.engine = db.bootstrap(self.db_path)

    def tearDown(self):
        self.engine.dispose()

    def test_bootstrap_creates_schema_and_seeds_default_shops(self):
        with Session(self.engine) as s:
            shops = s.exec(select(models.Shop)).all()
        self.assertEqual(len(shops), len(db.DEFAULT_SHOPS))
        self.assertIn("morrisons", {shop.id for shop in shops})

    def test_seed_default_shops_is_idempotent(self):
        with Session(self.engine) as s:
            inserted_again = db.seed_default_shops(s)
            total = len(s.exec(select(models.Shop)).all())
        self.assertEqual(inserted_again, 0)
        self.assertEqual(total, len(db.DEFAULT_SHOPS))

    def test_foreign_keys_are_enforced(self):
        # PRAGMA foreign_keys=ON should reject an alias pointing at a missing item.
        with Session(self.engine) as s:
            s.add(models.ItemAlias(
                item_id=9999, alias_text="ghost", source="user", created_at=db.now_iso(),
            ))
            with self.assertRaises(IntegrityError):
                s.commit()

    def test_check_constraint_rejects_bad_enum(self):
        ts = db.now_iso()
        with Session(self.engine) as s:
            item = models.Item(canonical_name="milk", created_at=ts, updated_at=ts)
            s.add(item)
            s.commit()
            s.refresh(item)
            s.add(models.Suggestion(
                item_id=item.id, source="not-a-valid-source",
                status="pending", generated_at=ts,
            ))
            with self.assertRaises(IntegrityError):
                s.commit()

    def test_money_is_stored_as_integer_pennies(self):
        ts = db.now_iso()
        with Session(self.engine) as s:
            trip = models.ShoppingTrip(
                shop_id="morrisons", trip_date="2026-06-14", source="receipt",
                total_pennies=5210, currency="GBP", created_at=ts, updated_at=ts,
            )
            s.add(trip)
            s.commit()
            s.refresh(trip)
            s.add(models.ShoppingTripItem(
                trip_id=trip.id, name="Milk", quantity=1, shop_id="morrisons",
                unit_price_pennies=145, line_total_pennies=145, bought_at=ts, created_at=ts,
            ))
            s.commit()
            total = s.exec(
                select(models.ShoppingTripItem.line_total_pennies)
            ).first()
        self.assertEqual(total, 145)
        self.assertIsInstance(total, int)


if __name__ == "__main__":
    unittest.main()
