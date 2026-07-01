"""Database engine, PRAGMAs, schema creation and clean-start seed data."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, create_engine, select

from . import models
from .config import ROOT

# Separate from the Phase 1 session DB on purpose; "one file vs two" is open
# question #1 in PHASE2_DATA_MODEL.md.
DEFAULT_DB_PATH = ROOT / "data" / "shopping_list.sqlite"


def now_iso() -> str:
    """ISO-8601 UTC timestamp, matching the convention in auth.py."""
    return datetime.now(timezone.utc).isoformat()


def today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _enable_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    # SQLite defaults foreign_keys OFF every connection — must be turned on each time.
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")   # concurrent reads/writes for two users
    cursor.close()


def get_engine(db_path: Path | str = DEFAULT_DB_PATH, *, echo: bool = False) -> Engine:
    db_path = Path(db_path)
    if db_path.parent and str(db_path) != ":memory:":
        db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_path}",
        echo=echo,
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    event.listen(engine, "connect", _enable_sqlite_pragmas)
    return engine


def init_db(engine: Engine) -> None:
    """Create all tables. Satisfies the Phase 2 exit criterion: start empty, build schema."""
    SQLModel.metadata.create_all(engine)


def _ensure_receipt_migrations(engine: Engine) -> None:
    """Idempotently bring an existing ``receipts`` table up to date.

    ``create_all`` only creates missing tables — it never alters an existing
    one. Production already has a ``receipts`` table from before
    ``content_sha256`` existed on the model, so add the column by hand here.
    Safe to run on every startup: skipped once the column is present.
    """
    with engine.begin() as conn:
        columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(receipts)").fetchall()}
        if not columns:
            return  # table doesn't exist yet; create_all() will have made it with the column already
        if "content_sha256" not in columns:
            conn.exec_driver_sql("ALTER TABLE receipts ADD COLUMN content_sha256 TEXT")
        indexes = {row[1] for row in conn.exec_driver_sql("PRAGMA index_list(receipts)").fetchall()}
        # Fresh schemas already get ix_receipts_content_sha256 from SQLModel.
        # Existing schemas need it created after ALTER TABLE. Avoid maintaining
        # two equivalent indexes under different names.
        if "ix_receipts_content_sha256" not in indexes and "idx_receipts_content_sha256" not in indexes:
            conn.exec_driver_sql(
                "CREATE INDEX ix_receipts_content_sha256 ON receipts(content_sha256)"
            )
        elif "ix_receipts_content_sha256" in indexes and "idx_receipts_content_sha256" in indexes:
            conn.exec_driver_sql("DROP INDEX idx_receipts_content_sha256")


# Editable defaults for a brand-new database, chosen by Jamie on 2026-06-30.
DEFAULT_SHOPS = [
    ("morrisons",       "Morrisons",          "🛒", "#007A33"),
    ("aldi",            "Aldi",               "🔵", "#1E4D9B"),
    ("lidl",            "Lidl",               "🟡", "#0050AA"),
    ("butcher",         "Butcher",            "🥩", "#8B1E1E"),
    ("fruit-veg",       "Fruit and Veg Shop", "🥕", "#4F8A10"),
    ("boots-superdrug", "Boots/Superdrug",    "💊", "#5B57A6"),
    ("other",           "Other",              "🏪", "#777777"),
]


# These are deliberately broad starter layouts, not claims about a specific branch.
# Each tuple is (department name, comma-separated matching keywords).
DEFAULT_LAYOUTS = {
    "morrisons": [
        ("Fruit & Vegetables", "fruit,vegetable,veg,salad,banana,apple,potato,onion"),
        ("Bakery", "bread,roll,bagel,cake,pastry,wrap"),
        ("Deli", "deli,ham,salami,olives,hummus"),
        ("Meat & Fish", "chicken,beef,pork,lamb,fish,sausage,bacon"),
        ("Dairy & Eggs", "milk,cheese,butter,yogurt,cream,egg"),
        ("Chilled", "ready meal,pizza,chilled,juice,dip"),
        ("Cupboard", "tin,pasta,rice,cereal,sauce,spice,baking"),
        ("Frozen", "frozen,ice cream,chips,peas"),
        ("Drinks", "water,juice,squash,coffee,tea,beer,wine"),
        ("Household", "cleaner,washing,bin bag,kitchen roll,toilet roll"),
        ("Toiletries", "shampoo,soap,toothpaste,deodorant"),
        ("Checkout", "snack,chocolate,sweets,gum"),
    ],
    "aldi": [
        ("Entrance & Produce", "fruit,vegetable,veg,salad,banana,apple,potato"),
        ("Bakery", "bread,roll,cake,pastry,wrap"),
        ("Chilled", "ready meal,pizza,chilled,dip"),
        ("Meat & Fish", "chicken,beef,pork,lamb,fish,sausage,bacon"),
        ("Dairy & Eggs", "milk,cheese,butter,yogurt,egg"),
        ("Cupboard", "tin,pasta,rice,cereal,sauce,spice,baking"),
        ("Middle Aisle", "special buy,tool,garden,kitchen,clothing"),
        ("Frozen", "frozen,ice cream,chips,peas"),
        ("Drinks", "water,juice,squash,coffee,tea,beer,wine"),
        ("Household", "cleaner,washing,bin bag,kitchen roll,toilet roll,toiletry"),
        ("Checkout", "snack,chocolate,sweets,gum"),
    ],
    "lidl": [
        ("Entrance & Produce", "fruit,vegetable,veg,salad,banana,apple,potato"),
        ("Bakery", "bread,roll,cake,pastry,pretzel,wrap"),
        ("Chilled", "ready meal,pizza,chilled,dip"),
        ("Meat & Fish", "chicken,beef,pork,lamb,fish,sausage,bacon"),
        ("Dairy & Eggs", "milk,cheese,butter,yogurt,egg"),
        ("Cupboard", "tin,pasta,rice,cereal,sauce,spice,baking"),
        ("Middle of Lidl", "special buy,tool,garden,kitchen,clothing"),
        ("Frozen", "frozen,ice cream,chips,peas"),
        ("Drinks", "water,juice,squash,coffee,tea,beer,wine"),
        ("Household", "cleaner,washing,bin bag,kitchen roll,toilet roll,toiletry"),
        ("Checkout", "snack,chocolate,sweets,gum"),
    ],
    "butcher": [
        ("Poultry", "chicken,turkey,duck,poultry"),
        ("Beef", "beef,steak,mince,brisket"),
        ("Pork", "pork,chop,gammon,ham"),
        ("Lamb", "lamb,mutton"),
        ("Sausages & Bacon", "sausage,bacon,black pudding"),
        ("Prepared & Deli", "burger,kebab,marinated,pie,deli"),
        ("Counter & Collection", "order,collection,other"),
    ],
    "fruit-veg": [
        ("Fruit", "fruit,banana,apple,orange,berry,grape,melon"),
        ("Salad", "salad,lettuce,tomato,cucumber,pepper"),
        ("Vegetables", "vegetable,veg,carrot,broccoli,cauliflower,cabbage"),
        ("Potatoes & Onions", "potato,onion,garlic,sweet potato"),
        ("Herbs", "herb,basil,coriander,parsley,mint"),
        ("Seasonal & Local", "seasonal,local,flower,plant"),
        ("Checkout", "juice,nut,snack,other"),
    ],
    "boots-superdrug": [
        ("Pharmacy & Health", "medicine,tablet,painkiller,vitamin,first aid"),
        ("Dental", "toothpaste,toothbrush,mouthwash,floss"),
        ("Toiletries", "soap,shower gel,deodorant,razor,sanitary"),
        ("Hair", "shampoo,conditioner,hair dye,styling"),
        ("Skincare", "moisturiser,cleanser,sunscreen,skin"),
        ("Beauty", "makeup,cosmetic,mascara,lipstick,nail"),
        ("Baby", "nappy,wipe,baby,formula"),
        ("Household", "tissue,cotton wool,cleaner,battery"),
        ("Checkout", "travel size,gift,snack,other"),
    ],
    "other": [
        ("Fresh", "fruit,vegetable,bread,fresh"),
        ("Chilled", "milk,cheese,chilled"),
        ("Cupboard", "tin,pasta,rice,cereal,cupboard"),
        ("Household", "cleaner,household,toiletry"),
        ("Other", "other,miscellaneous"),
    ],
}


def seed_default_shops(session: Session) -> int:
    """Insert default shops if missing. Idempotent. Returns number inserted."""
    inserted = 0
    for order, (slug, name, emoji, color) in enumerate(DEFAULT_SHOPS):
        existing = session.get(models.Shop, slug)
        if existing:
            continue
        ts = now_iso()
        session.add(models.Shop(
            id=slug, name=name, emoji=emoji, color=color,
            active=True, sort_order=order, created_at=ts, updated_at=ts,
        ))
        inserted += 1
    session.commit()
    return inserted


def seed_default_layouts(session: Session) -> int:
    """Seed a starter layout only when a seeded shop has no departments."""
    inserted = 0
    for shop_id, departments in DEFAULT_LAYOUTS.items():
        existing = session.exec(
            select(models.StoreLayoutDepartment).where(
                models.StoreLayoutDepartment.shop_id == shop_id
            )
        ).first()
        if existing:
            continue
        for order, (name, keyword_text) in enumerate(departments, start=1):
            department = models.StoreLayoutDepartment(
                shop_id=shop_id,
                name=name,
                sort_order=order,
            )
            session.add(department)
            session.flush()
            for keyword in (part.strip() for part in keyword_text.split(",")):
                if keyword:
                    session.add(models.StoreLayoutKeyword(
                        department_id=department.id,
                        keyword=keyword,
                    ))
            inserted += 1
    session.commit()
    return inserted


def bootstrap(db_path: Path | str = DEFAULT_DB_PATH) -> Engine:
    """Create the DB file, schema and default shops in one call. Idempotent."""
    engine = get_engine(db_path)
    init_db(engine)
    _ensure_receipt_migrations(engine)
    with Session(engine) as session:
        seed_default_shops(session)
        seed_default_layouts(session)
    return engine


if __name__ == "__main__":
    eng = bootstrap()
    with Session(eng) as s:
        n = len(s.exec(select(models.Shop)).all())
    print(f"Bootstrapped {DEFAULT_DB_PATH} — {n} shops present.")
