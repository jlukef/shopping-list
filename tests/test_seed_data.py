import os
import tempfile
import unittest

from sqlmodel import Session, select

from src.shopping_list import db, models


class SeedDataTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self._tmp, "test_seed.sqlite")
        self.engine = db.bootstrap(self.db_path)

    def tearDown(self):
        self.engine.dispose()

    def test_seed_requires_exactly_seven_canonical_shops(self):
        # We expect exactly these slugs in this exact order.
        expected_slugs = [
            "morrisons",
            "aldi",
            "lidl",
            "butcher",
            "fruit-veg",
            "boots-superdrug",
            "other"
        ]
        
        with Session(self.engine) as s:
            shops = s.exec(select(models.Shop).order_by(models.Shop.sort_order)).all()
            
        slugs = [shop.id for shop in shops]
        
        self.assertEqual(
            slugs, 
            expected_slugs, 
            "The initial shops must match the batch 4 exact canonical list and order."
        )

        # Explicitly verify the old defaults are gone
        old_defaults = {"tesco", "sainsburys", "asda", "waitrose", "amazon"}
        for old in old_defaults:
            self.assertNotIn(old, slugs, f"{old} should not be in the fresh start shops.")

    def test_seed_is_idempotent(self):
        with Session(self.engine) as s:
            # Re-run the bootstrap seed
            db.seed_default_shops(s)
            
            # The count should remain exactly 7
            shops = s.exec(select(models.Shop)).all()
            
        self.assertEqual(len(shops), 7, "Seeding again should not duplicate shops.")
        
    def test_shop_ids_are_unique(self):
        with Session(self.engine) as s:
            shops = s.exec(select(models.Shop)).all()
            
        slugs = [shop.id for shop in shops]
        self.assertEqual(len(slugs), len(set(slugs)), "Shop IDs must be unique.")

    def test_every_shop_has_departments_and_keywords(self):
        # "at least one ordered department plus nonblank keyword coverage for every seeded shop"
        with Session(self.engine) as s:
            shops = s.exec(select(models.Shop)).all()
            
            for shop in shops:
                # Find departments for this shop
                departments = s.exec(
                    select(models.StoreLayoutDepartment)
                    .where(models.StoreLayoutDepartment.shop_id == shop.id)
                ).all()
                
                self.assertGreaterEqual(
                    len(departments), 1, 
                    f"Shop {shop.id} must have at least one department."
                )
                
                # Check keywords exist for those departments
                dept_ids = [d.id for d in departments]
                keywords = s.exec(
                    select(models.StoreLayoutKeyword)
                    .where(models.StoreLayoutKeyword.department_id.in_(dept_ids))
                ).all()
                
                self.assertGreaterEqual(
                    len(keywords), 1,
                    f"Shop {shop.id} must have at least one keyword across its departments."
                )
                
                for kw in keywords:
                    self.assertTrue(bool(kw.keyword.strip()), "Keywords must be non-blank.")

    def test_reseeding_does_not_overwrite_an_edited_layout(self):
        # bootstrap() reseeds layouts on every app start. Simulate the user editing
        # Aldi's layout (e.g. via saveLayout), which replaces its departments
        # entirely, then confirm a re-seed leaves that edit alone instead of
        # silently reverting it back to the guessed default.
        with Session(self.engine) as s:
            # Match _save_layout's own pattern: delete only the department rows
            # and let the ON DELETE CASCADE foreign key remove their keywords.
            for dept in s.exec(
                select(models.StoreLayoutDepartment).where(models.StoreLayoutDepartment.shop_id == "aldi")
            ).all():
                s.delete(dept)
            s.commit()

            custom = models.StoreLayoutDepartment(shop_id="aldi", name="My Custom Aisle", sort_order=1)
            s.add(custom)
            s.commit()
            s.refresh(custom)
            s.add(models.StoreLayoutKeyword(department_id=custom.id, keyword="custom"))
            s.commit()

            db.seed_default_layouts(s)

            departments = s.exec(
                select(models.StoreLayoutDepartment).where(models.StoreLayoutDepartment.shop_id == "aldi")
            ).all()

        self.assertEqual(len(departments), 1, "re-seeding must not touch a shop that already has departments")
        self.assertEqual(departments[0].name, "My Custom Aisle")

if __name__ == "__main__":
    unittest.main()
