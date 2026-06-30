import json
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import redirect_stderr, redirect_stdout

from sqlmodel import Session, select

from scripts import import_from_sheets
from src.shopping_list import db, models


class ImportScriptTestCase(unittest.TestCase):
    def test_requires_an_explicit_or_environment_import_source(self):
        with patch.dict(os.environ, {}, clear=True):
            with redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    import_from_sheets.main([])
        self.assertEqual(raised.exception.code, 2)

    def test_exported_layout_rows_are_imported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export_dir = root / "export"
            export_dir.mkdir()
            (export_dir / "shops.json").write_text(json.dumps([
                {"id": "tesco", "name": "Tesco", "emoji": "🛒", "color": "#005994"},
            ]), encoding="utf-8")
            (export_dir / "layouts.json").write_text(json.dumps([
                {"shop": "tesco", "department": "Produce", "order": 1, "keywords": "fruit,veg"},
            ]), encoding="utf-8")
            db_path = root / "shopping.sqlite"

            with redirect_stdout(io.StringIO()):
                result = import_from_sheets.main([
                    "--no-fetch", "--export-dir", str(export_dir), "--db", str(db_path),
                ])

            engine = db.get_engine(db_path)
            with Session(engine) as session:
                departments = session.exec(select(models.StoreLayoutDepartment)).all()
                keywords = session.exec(select(models.StoreLayoutKeyword)).all()
            engine.dispose()

        self.assertEqual(result, 0)
        self.assertEqual([department.name for department in departments], ["Produce"])
        self.assertEqual(sorted(keyword.keyword for keyword in keywords), ["fruit", "veg"])


if __name__ == "__main__":
    unittest.main()
