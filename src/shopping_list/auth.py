from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import os
import secrets
import sqlite3
import sys
from pathlib import Path


HASH_ALGORITHM = "pbkdf2_sha256"
HASH_ITERATIONS = 260_000


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def hash_password(password: str, *, salt: bytes | None = None, iterations: int = HASH_ITERATIONS) -> str:
    if not password:
        raise ValueError("Password must not be empty")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{HASH_ALGORITHM}${iterations}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        algorithm, iterations_raw, salt_raw, digest_raw = encoded_hash.split("$", 3)
        if algorithm != HASH_ALGORITHM:
            return False
        iterations = int(iterations_raw)
        salt = _unb64(salt_raw)
        expected = _unb64(digest_raw)
    except Exception:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_db_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def from_db_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class User:
    username: str


class UserStore:
    def __init__(self, users: dict[str, str] | None):
        self._users = {username.lower(): password_hash for username, password_hash in (users or {}).items()}

    def authenticate(self, username: str, password: str) -> User | None:
        normalized = username.strip().lower()
        password_hash_value = self._users.get(normalized)
        # Run a dummy hash check for unknown users so the obvious timing gap is smaller.
        if not password_hash_value:
            verify_password(password or "x", hash_password("dummy", salt=b"0" * 16))
            return None
        if not verify_password(password, password_hash_value):
            return None
        return User(username=normalized)


class SessionStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def create_session(self, user: User, *, duration: timedelta) -> tuple[str, datetime]:
        token = secrets.token_urlsafe(32)
        expires_at = utcnow() + duration
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO sessions (token_hash, username, expires_at, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (token_hash(token), user.username, to_db_datetime(expires_at), to_db_datetime(utcnow())),
            )
            conn.commit()
        finally:
            conn.close()
        return token, expires_at

    def get_user_for_token(self, token: str | None) -> User | None:
        if not token:
            return None
        hashed = token_hash(token)
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT username, expires_at FROM sessions WHERE token_hash = ?",
                (hashed,),
            ).fetchone()
            if not row:
                return None
            expires_at = from_db_datetime(row["expires_at"])
            if expires_at <= utcnow():
                conn.execute("DELETE FROM sessions WHERE token_hash = ?", (hashed,))
                conn.commit()
                return None
            return User(username=row["username"])
        finally:
            conn.close()

    def delete_session(self, token: str | None) -> None:
        if not token:
            return
        conn = self._connect()
        try:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))
            conn.commit()
        finally:
            conn.close()


def _main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "hash-password":
        if len(argv) >= 3:
            password = argv[2]
        else:
            import getpass

            password = getpass.getpass("Password to hash: ")
        print(hash_password(password))
        return 0
    print("Usage: python -m src.shopping_list.auth hash-password [password]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
