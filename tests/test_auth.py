from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import tempfile
import unittest

from src.shopping_list.auth import SessionStore, User, UserStore, hash_password, verify_password
from src.shopping_list.config import parse_users


class PasswordHashTests(unittest.TestCase):
    def test_hash_password_verifies_matching_password_only(self) -> None:
        encoded = hash_password("correct horse battery staple", salt=b"1" * 16, iterations=1_000)

        self.assertTrue(verify_password("correct horse battery staple", encoded))
        self.assertFalse(verify_password("wrong", encoded))


class UserStoreTests(unittest.TestCase):
    def test_parse_users_accepts_and_normalises_usernames(self) -> None:
        password_hash = hash_password("secret", salt=b"4" * 16, iterations=1_000)

        users = parse_users(f" Jamie : {password_hash}; WIFE:{password_hash} ")

        self.assertEqual(users, {"jamie": password_hash, "wife": password_hash})

    def test_parse_users_rejects_malformed_entries(self) -> None:
        with self.assertRaises(ValueError):
            parse_users("jamie-without-hash")

        with self.assertRaises(ValueError):
            parse_users("jamie:")

    def test_authenticate_requires_known_user_and_correct_password(self) -> None:
        users = {"jamie": hash_password("secret", salt=b"2" * 16, iterations=1_000)}
        store = UserStore(users)

        self.assertEqual(store.authenticate("Jamie", "secret"), User(username="jamie"))
        self.assertIsNone(store.authenticate("jamie", "bad"))
        self.assertIsNone(store.authenticate("missing", "secret"))


class SessionStoreTests(unittest.TestCase):
    def test_session_round_trip_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(db_path=Path(tmp) / "sessions.sqlite")
            token, expires_at = store.create_session(User(username="jamie"), duration=timedelta(days=1))

            self.assertGreater(expires_at.isoformat(), "")
            self.assertEqual(store.get_user_for_token(token), User(username="jamie"))

            store.delete_session(token)
            self.assertIsNone(store.get_user_for_token(token))


if __name__ == "__main__":
    unittest.main()
