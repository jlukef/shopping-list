from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = ROOT / "docs"
TEMPLATES_DIR = ROOT / "templates"


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    root: Path = ROOT
    docs_dir: Path = DOCS_DIR
    templates_dir: Path = TEMPLATES_DIR
    apps_script_url: str = ""
    data_backend: str = "apps_script"
    app_db: Path = ROOT / "data" / "shopping_list.sqlite"
    users: dict[str, str] | None = None
    session_db: Path = ROOT / "data" / "sessions.sqlite"
    session_days: int = 30
    session_cookie_name: str = "shopping_list_session"
    cookie_secure: bool = False


def parse_users(raw: str) -> dict[str, str]:
    users: dict[str, str] = {}
    for entry in raw.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            raise ValueError("SHOPPING_LIST_USERS entries must be username:password_hash")
        username, password_hash = entry.split(":", 1)
        username = username.strip().lower()
        password_hash = password_hash.strip()
        if not username or not password_hash:
            raise ValueError("SHOPPING_LIST_USERS entries must include username and password hash")
        users[username] = password_hash
    return users


def load_settings() -> Settings:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
    except ImportError:
        pass

    session_db = Path(os.environ.get("SHOPPING_LIST_SESSION_DB", str(ROOT / "data" / "sessions.sqlite")))
    if not session_db.is_absolute():
        session_db = ROOT / session_db

    app_db = Path(os.environ.get("SHOPPING_LIST_DB", str(ROOT / "data" / "shopping_list.sqlite")))
    if not app_db.is_absolute():
        app_db = ROOT / app_db

    data_backend = os.environ.get("SHOPPING_LIST_DATA_BACKEND", "sqlite").strip().lower()
    if data_backend not in {"sqlite", "apps_script"}:
        raise ValueError("SHOPPING_LIST_DATA_BACKEND must be 'sqlite' or 'apps_script'")

    return Settings(
        apps_script_url=os.environ.get("SHOPPING_LIST_APPS_SCRIPT_URL", "").strip(),
        data_backend=data_backend,
        app_db=app_db,
        users=parse_users(os.environ.get("SHOPPING_LIST_USERS", "")),
        session_db=session_db,
        session_days=max(1, int(os.environ.get("SHOPPING_LIST_SESSION_DAYS", "30"))),
        cookie_secure=_bool_env("SHOPPING_LIST_COOKIE_SECURE", default=False),
    )
