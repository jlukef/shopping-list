"""Phase 2 SQLite data model (SQLModel / SQLAlchemy).

Design spec: PHASE2_DATA_MODEL.md. The schema is now wired to the local SQLite
action backend; deployment remains a separate approval step. Conventions enforced here:

- Money is INTEGER pennies (``*_pennies``) plus a ``currency`` column. Never floats.
- Quantities are floats with a separate ``unit`` string.
- Timestamps are ISO-8601 UTC strings (matches ``auth.py``); dates are ``YYYY-MM-DD``.
- Booleans are stored as 0/1 (SQLModel ``bool`` maps to SQLite INTEGER).
- ``shops.id`` is a TEXT slug ('tesco'); every other table uses an INTEGER PK.

CHECK constraints mirror the spec's enum-like columns so a DB browser stays readable
and bad values are rejected at the storage layer.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import CheckConstraint
from sqlmodel import Field, SQLModel


# ── Auth ─────────────────────────────────────────────────────────────────────
class User(SQLModel, table=True):
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True)          # lowercased
    password_hash: str
    display_name: Optional[str] = None
    active: bool = True
    created_at: str
    updated_at: str


class Session(SQLModel, table=True):
    # NB: Phase 1's live session store manages its own sessions table in a separate
    # SQLite file. Whether to unify them here is open question #3 in the spec.
    __tablename__ = "sessions"

    token_hash: str = Field(primary_key=True)
    user_id: int = Field(foreign_key="users.id", ondelete="CASCADE", index=True)
    expires_at: str = Field(index=True)
    created_at: str


# ── Catalog ──────────────────────────────────────────────────────────────────
class Shop(SQLModel, table=True):
    __tablename__ = "shops"

    id: str = Field(primary_key=True)                       # slug: 'tesco'
    name: str
    emoji: Optional[str] = None
    color: Optional[str] = None
    active: bool = True
    sort_order: int = 0
    created_at: str
    updated_at: str


class Item(SQLModel, table=True):
    __tablename__ = "items"

    id: Optional[int] = Field(default=None, primary_key=True)
    canonical_name: str = Field(unique=True, index=True)    # normalised, lowercased
    display_name: Optional[str] = None
    category: Optional[str] = None
    default_shop_id: Optional[str] = Field(default=None, foreign_key="shops.id", ondelete="SET NULL")
    default_quantity: Optional[float] = None
    default_unit: Optional[str] = None
    use_count: int = 0
    last_used_at: Optional[str] = None
    created_at: str
    updated_at: str


class ItemAlias(SQLModel, table=True):
    __tablename__ = "item_aliases"
    __table_args__ = (
        CheckConstraint("source IN ('user','receipt','seed')", name="ck_item_aliases_source"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    item_id: int = Field(foreign_key="items.id", ondelete="CASCADE", index=True)
    alias_text: str = Field(unique=True)                    # raw, lowercased
    source: str = "user"
    created_at: str


# ── Active list ──────────────────────────────────────────────────────────────
class ShoppingListItem(SQLModel, table=True):
    __tablename__ = "shopping_list_items"

    id: Optional[int] = Field(default=None, primary_key=True)
    source_ref: Optional[str] = Field(default=None, unique=True, index=True)
    item_id: Optional[int] = Field(default=None, foreign_key="items.id", ondelete="SET NULL")
    name: str
    quantity: float = 1
    unit: str = ""
    shop_id: Optional[str] = Field(default=None, foreign_key="shops.id", ondelete="SET NULL", index=True)
    bought: bool = Field(default=False, index=True)
    notes: Optional[str] = None
    sort_order: int = 0
    added_by_user_id: Optional[int] = Field(default=None, foreign_key="users.id", ondelete="SET NULL")
    bought_by_user_id: Optional[int] = Field(default=None, foreign_key="users.id", ondelete="SET NULL")
    created_at: str
    bought_at: Optional[str] = None
    updated_at: str


# ── Store layout ─────────────────────────────────────────────────────────────
class StoreLayoutDepartment(SQLModel, table=True):
    __tablename__ = "store_layout_departments"

    id: Optional[int] = Field(default=None, primary_key=True)
    shop_id: str = Field(foreign_key="shops.id", ondelete="CASCADE", index=True)
    name: str
    sort_order: int = 0


class StoreLayoutKeyword(SQLModel, table=True):
    __tablename__ = "store_layout_keywords"

    id: Optional[int] = Field(default=None, primary_key=True)
    department_id: int = Field(foreign_key="store_layout_departments.id", ondelete="CASCADE", index=True)
    keyword: str


# ── History: trips & trip items (analytics backbone) ─────────────────────────
class ShoppingTrip(SQLModel, table=True):
    __tablename__ = "shopping_trips"
    __table_args__ = (
        CheckConstraint(
            "source IN ('receipt','manual','clear_bought','import')",
            name="ck_trips_source",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    shop_id: Optional[str] = Field(default=None, foreign_key="shops.id", ondelete="SET NULL", index=True)
    trip_date: str = Field(index=True)                      # YYYY-MM-DD
    source: str
    total_pennies: Optional[int] = None
    currency: str = "GBP"
    notes: Optional[str] = None
    created_by_user_id: Optional[int] = Field(default=None, foreign_key="users.id", ondelete="SET NULL")
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    created_at: str
    updated_at: str


class ShoppingTripItem(SQLModel, table=True):
    __tablename__ = "shopping_trip_items"

    id: Optional[int] = Field(default=None, primary_key=True)
    trip_id: int = Field(foreign_key="shopping_trips.id", ondelete="CASCADE", index=True)
    item_id: Optional[int] = Field(default=None, foreign_key="items.id", ondelete="SET NULL", index=True)
    name: str
    quantity: float = 1
    unit: str = ""
    shop_id: Optional[str] = Field(default=None, foreign_key="shops.id", ondelete="SET NULL", index=True)
    unit_price_pennies: Optional[int] = None
    line_total_pennies: Optional[int] = None
    currency: str = "GBP"
    bought_at: str = Field(index=True)
    # Stable key used by one-off legacy imports. Nullable because normal app-created
    # rows do not need one; unique so rerunning an import cannot duplicate history.
    source_ref: Optional[str] = Field(default=None, unique=True, index=True)
    source_receipt_item_id: Optional[int] = Field(default=None, foreign_key="receipt_items.id", ondelete="SET NULL")
    created_at: str


# ── Receipts ─────────────────────────────────────────────────────────────────
class Receipt(SQLModel, table=True):
    __tablename__ = "receipts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('uploaded','processing','ready','reviewed','saved','failed')",
            name="ck_receipts_status",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    shopping_trip_id: Optional[int] = Field(default=None, foreign_key="shopping_trips.id", ondelete="SET NULL")
    uploaded_by_user_id: Optional[int] = Field(default=None, foreign_key="users.id", ondelete="SET NULL", index=True)
    shop_id: Optional[str] = Field(default=None, foreign_key="shops.id", ondelete="SET NULL")
    purchase_date: Optional[str] = None
    original_filename: Optional[str] = None
    # Legacy column, kept for schema compatibility. Receipt images are never
    # persisted (Jamie's 2026-07-01 decision, PHASE5_RECEIPT_OCR_PLAN.md §5) —
    # new rows always write "" here, never a real path.
    stored_path: str = ""
    # SHA-256 of the normalised (EXIF-rotated, resized) image bytes, computed
    # transiently before the bytes are discarded. Used to dedupe repeat
    # uploads of the same photo without keeping the photo itself.
    content_sha256: Optional[str] = Field(default=None, index=True)
    mime_type: Optional[str] = None
    file_size_bytes: Optional[int] = None
    status: str = Field(default="uploaded", index=True)
    ocr_engine: Optional[str] = None
    ocr_text: Optional[str] = None
    raw_extraction_json: Optional[str] = None
    subtotal_pennies: Optional[int] = None
    total_pennies: Optional[int] = None
    currency: str = "GBP"
    extracted_at: Optional[str] = None
    reviewed_at: Optional[str] = None
    created_at: str
    updated_at: str


class ReceiptItem(SQLModel, table=True):
    __tablename__ = "receipt_items"
    __table_args__ = (
        CheckConstraint(
            "category IN ('item','discount','loyalty','subtotal','total','tax','other')",
            name="ck_receipt_items_category",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    receipt_id: int = Field(foreign_key="receipts.id", ondelete="CASCADE", index=True)
    item_id: Optional[int] = Field(default=None, foreign_key="items.id", ondelete="SET NULL")
    line_no: Optional[int] = None
    raw_text: str                                           # immutable OCR audit
    name: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    unit_price_pennies: Optional[int] = None
    line_total_pennies: Optional[int] = None
    confidence: Optional[float] = None
    category: str = "item"
    excluded: bool = False
    accepted: bool = False
    created_at: str
    updated_at: str


class ReceiptExtractionAttempt(SQLModel, table=True):
    """Diagnostics for the 5b AI extraction fallback chain (PHASE5_RECEIPT_OCR_PLAN.md §3).

    Deliberately holds no image data — receipt images are never persisted. One row
    per provider attempt, so a receipt that fell through Claude -> Gemini -> GPT
    before succeeding (or failing outright) leaves a full audit trail of why.
    """

    __tablename__ = "receipt_extraction_attempts"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('success','refused','invalid','timeout','rate_limited','unavailable','error')",
            name="ck_receipt_extraction_attempts_outcome",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    receipt_id: int = Field(foreign_key="receipts.id", ondelete="CASCADE", index=True)
    alias: str
    provider: str
    model: str
    outcome: str
    error_class: Optional[str] = None
    duration_ms: Optional[int] = None
    created_at: str


# ── Prediction ───────────────────────────────────────────────────────────────
class ItemPurchaseStats(SQLModel, table=True):
    __tablename__ = "item_purchase_stats"

    item_id: int = Field(foreign_key="items.id", ondelete="CASCADE", primary_key=True)
    total_purchases: int = 0
    first_bought_at: Optional[str] = None
    last_bought_at: Optional[str] = None
    avg_interval_days: Optional[float] = None
    stddev_interval_days: Optional[float] = None
    avg_quantity: Optional[float] = None
    avg_unit_price_pennies: Optional[int] = None
    preferred_shop_id: Optional[str] = Field(default=None, foreign_key="shops.id", ondelete="SET NULL")
    updated_at: str


class AIPredictionRun(SQLModel, table=True):
    __tablename__ = "ai_prediction_runs"

    id: Optional[int] = Field(default=None, primary_key=True)
    model: str
    prompt_version: Optional[str] = None
    input_summary: Optional[str] = None
    raw_response_json: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_pennies: Optional[int] = None
    created_at: str


class Suggestion(SQLModel, table=True):
    __tablename__ = "suggestions"
    __table_args__ = (
        CheckConstraint("source IN ('cadence','ai')", name="ck_suggestions_source"),
        CheckConstraint(
            "status IN ('pending','accepted','dismissed','expired')",
            name="ck_suggestions_status",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    item_id: int = Field(foreign_key="items.id", ondelete="CASCADE", index=True)
    source: str
    model: Optional[str] = None
    ai_run_id: Optional[int] = Field(default=None, foreign_key="ai_prediction_runs.id", ondelete="SET NULL")
    reason: Optional[str] = None
    score: Optional[float] = None
    predicted_shop_id: Optional[str] = Field(default=None, foreign_key="shops.id", ondelete="SET NULL")
    due_date: Optional[str] = None
    status: str = Field(default="pending", index=True)
    generated_at: str
    responded_at: Optional[str] = None
    responded_by_user_id: Optional[int] = Field(default=None, foreign_key="users.id", ondelete="SET NULL")
