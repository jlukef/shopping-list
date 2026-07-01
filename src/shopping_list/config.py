from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = ROOT / "docs"
TEMPLATES_DIR = ROOT / "templates"

KNOWN_RECEIPT_AI_PROVIDERS = {"anthropic", "google", "openai"}


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ReceiptAIOption:
    alias: str
    provider: str  # one of KNOWN_RECEIPT_AI_PROVIDERS
    model: str


@dataclass(frozen=True)
class ReceiptAISettings:
    """Provider-neutral receipt-extraction configuration (PHASE5_RECEIPT_OCR_PLAN.md §3-4).

    Deliberately optional: an app with no ``SHOPPING_LIST_RECEIPT_AI_OPTIONS`` behaves
    exactly like the 5a slice (manual entry only, no extraction attempted).
    """

    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    openai_api_key: str = ""
    options: tuple[ReceiptAIOption, ...] = ()
    default: str = "auto"
    fallback_order: tuple[str, ...] = ()

    def provider_key(self, provider: str) -> str:
        return {
            "anthropic": self.anthropic_api_key,
            "google": self.gemini_api_key,
            "openai": self.openai_api_key,
        }.get(provider, "")

    def is_enabled(self, alias: str) -> bool:
        option = next((o for o in self.options if o.alias == alias), None)
        return option is not None and bool(self.provider_key(option.provider))

    def enabled_options(self) -> tuple[ReceiptAIOption, ...]:
        return tuple(o for o in self.options if self.provider_key(o.provider))

    @property
    def configured(self) -> bool:
        return bool(self.options)


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
    max_upload_mb: int = 10
    receipt_ai: ReceiptAISettings = field(default_factory=ReceiptAISettings)


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


def parse_receipt_ai_options(raw: str) -> tuple[ReceiptAIOption, ...]:
    """Parse ``alias=provider:model;alias2=provider2:model2`` into option records."""
    options: list[ReceiptAIOption] = []
    seen_aliases: set[str] = set()
    for entry in raw.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        if "=" not in entry or ":" not in entry.split("=", 1)[1]:
            raise ValueError(
                "SHOPPING_LIST_RECEIPT_AI_OPTIONS entries must look like alias=provider:model"
            )
        alias, rest = entry.split("=", 1)
        provider, model = rest.split(":", 1)
        alias, provider, model = alias.strip(), provider.strip().lower(), model.strip()
        if not alias or not model:
            raise ValueError("SHOPPING_LIST_RECEIPT_AI_OPTIONS entries need a non-empty alias and model")
        if alias in seen_aliases:
            raise ValueError(f"Duplicate SHOPPING_LIST_RECEIPT_AI_OPTIONS alias: {alias}")
        if provider not in KNOWN_RECEIPT_AI_PROVIDERS:
            raise ValueError(
                f"Unknown SHOPPING_LIST_RECEIPT_AI_OPTIONS provider '{provider}' for alias '{alias}' "
                f"(expected one of {sorted(KNOWN_RECEIPT_AI_PROVIDERS)})"
            )
        seen_aliases.add(alias)
        options.append(ReceiptAIOption(alias=alias, provider=provider, model=model))
    return tuple(options)


def parse_receipt_ai_fallbacks(raw: str, known_aliases: set[str]) -> tuple[str, ...]:
    fallbacks: list[str] = []
    for entry in raw.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        if entry not in known_aliases:
            raise ValueError(f"SHOPPING_LIST_RECEIPT_AI_FALLBACKS references unknown alias: {entry}")
        fallbacks.append(entry)
    return tuple(fallbacks)


def build_receipt_ai_settings(
    *,
    anthropic_api_key: str,
    gemini_api_key: str,
    openai_api_key: str,
    options_raw: str,
    default_raw: str,
    fallbacks_raw: str,
) -> ReceiptAISettings:
    options = parse_receipt_ai_options(options_raw)
    if not options:
        # Feature not configured at all — 5a-only behaviour, nothing else to validate.
        return ReceiptAISettings(
            anthropic_api_key=anthropic_api_key,
            gemini_api_key=gemini_api_key,
            openai_api_key=openai_api_key,
        )

    known_aliases = {o.alias for o in options}
    settings = ReceiptAISettings(
        anthropic_api_key=anthropic_api_key,
        gemini_api_key=gemini_api_key,
        openai_api_key=openai_api_key,
        options=options,
        default=default_raw.strip() or "auto",
        fallback_order=parse_receipt_ai_fallbacks(fallbacks_raw, known_aliases),
    )
    if not settings.fallback_order:
        raise ValueError(
            "SHOPPING_LIST_RECEIPT_AI_FALLBACKS must list at least one alias when "
            "SHOPPING_LIST_RECEIPT_AI_OPTIONS is configured"
        )
    if settings.default != "auto" and not settings.is_enabled(settings.default):
        raise ValueError(
            "SHOPPING_LIST_RECEIPT_AI_DEFAULT must be 'auto' or an alias whose provider "
            "API key is configured"
        )
    if not settings.enabled_options():
        raise ValueError(
            "SHOPPING_LIST_RECEIPT_AI_OPTIONS is set but no provider API key is configured "
            "for any option (set SHOPPING_LIST_ANTHROPIC_API_KEY / _GEMINI_API_KEY / _OPENAI_API_KEY)"
        )
    return settings


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
        max_upload_mb=max(1, int(os.environ.get("SHOPPING_LIST_MAX_UPLOAD_MB", "10"))),
        receipt_ai=build_receipt_ai_settings(
            anthropic_api_key=os.environ.get("SHOPPING_LIST_ANTHROPIC_API_KEY", "").strip(),
            gemini_api_key=os.environ.get("SHOPPING_LIST_GEMINI_API_KEY", "").strip(),
            openai_api_key=os.environ.get("SHOPPING_LIST_OPENAI_API_KEY", "").strip(),
            options_raw=os.environ.get("SHOPPING_LIST_RECEIPT_AI_OPTIONS", ""),
            default_raw=os.environ.get("SHOPPING_LIST_RECEIPT_AI_DEFAULT", "auto"),
            fallbacks_raw=os.environ.get("SHOPPING_LIST_RECEIPT_AI_FALLBACKS", ""),
        ),
    )
