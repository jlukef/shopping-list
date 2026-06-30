from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
import unittest

from src.shopping_list.config import ROOT, load_settings, parse_users

class ParseUsersTests(unittest.TestCase):
    def test_parse_users_accepts_and_normalises_valid_entries(self) -> None:
        raw = " Jamie:hash1; WIFE:hash2 ;"
        users = parse_users(raw)
        self.assertEqual(users, {"jamie": "hash1", "wife": "hash2"})

    def test_parse_users_rejects_malformed_entries(self) -> None:
        with self.assertRaises(ValueError):
            parse_users("jamie")  # missing colon
        
        with self.assertRaises(ValueError):
            parse_users("jamie:")  # missing hash
            
        with self.assertRaises(ValueError):
            parse_users(":hash")  # missing username


class LoadSettingsTests(unittest.TestCase):
    def test_load_settings_reads_environment_and_resolves_relative_session_db(self) -> None:
        env = {
            "SHOPPING_LIST_APPS_SCRIPT_URL": " https://example.test/exec ",
            "SHOPPING_LIST_DATA_BACKEND": "sqlite",
            "SHOPPING_LIST_DB": "runtime/shopping.sqlite",
            "SHOPPING_LIST_USERS": " Jamie:hash1; Wife:hash2 ",
            "SHOPPING_LIST_SESSION_DB": "runtime/sessions.sqlite",
            "SHOPPING_LIST_SESSION_DAYS": "45",
            "SHOPPING_LIST_COOKIE_SECURE": "true",
        }

        with patch.dict("os.environ", env, clear=True):
            settings = load_settings()

        self.assertEqual(settings.apps_script_url, "https://example.test/exec")
        self.assertEqual(settings.data_backend, "sqlite")
        self.assertEqual(settings.app_db, ROOT / Path("runtime/shopping.sqlite"))
        self.assertEqual(settings.users, {"jamie": "hash1", "wife": "hash2"})
        self.assertEqual(settings.session_db, ROOT / Path("runtime/sessions.sqlite"))
        self.assertEqual(settings.session_days, 45)
        self.assertTrue(settings.cookie_secure)

    def test_load_settings_clamps_session_days_to_at_least_one(self) -> None:
        with patch.dict("os.environ", {"SHOPPING_LIST_SESSION_DAYS": "0"}, clear=True):
            settings = load_settings()

        self.assertEqual(settings.session_days, 1)

    def test_load_settings_rejects_unknown_data_backend(self) -> None:
        with patch.dict("os.environ", {"SHOPPING_LIST_DATA_BACKEND": "mystery"}, clear=True):
            with self.assertRaises(ValueError):
                load_settings()


if __name__ == "__main__":
    unittest.main()
