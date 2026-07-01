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


# Starter layouts, editable in Settings. Morrisons/Aldi/Lidl follow researched
# generic UK walk orders (2026-07-02): Aldi's nationally consistent format puts
# produce at the entrance ("Project Fresh") with Specialbuys mid-store; Lidl's
# current concept puts the bakery by the door, meat/fish at the very back,
# alcohol at the back and the freezer at the end of the journey; Morrisons
# leads produce into the Market Street counters. Branch-specific plans
# (Wetherby/Knaresborough) aren't published — drag departments to match reality.
DEFAULT_LAYOUTS = {
    "morrisons": [
        ("Fruit & Vegetables", "fruit,veg,vegetable,salad,banana,apple,orange,berry,strawberry,grape,melon,lemon,lime,avocado,tomato,cucumber,pepper,lettuce,spinach,potato,onion,carrot,broccoli,cauliflower,cabbage,mushroom,garlic,ginger,herb,chilli"),
        ("Flowers & Plants", "flower,bouquet,plant"),
        ("Market Street Bakery", "bread,loaf,roll,bap,baguette,croissant,pastry,doughnut,muffin,crumpet,bagel,scone,wrap,pitta,naan,cake"),
        ("Butcher & Fresh Meat", "chicken,beef,pork,lamb,mince,steak,sausage,bacon,turkey,gammon,burger,meatball,chop,ribs"),
        ("Fishmonger", "fish,salmon,cod,haddock,tuna,prawn,seafood,mackerel,scampi"),
        ("Deli & Cooked Meats", "ham,deli,salami,chorizo,pate,olive,antipasti,cooked meat,sliced meat,coleslaw"),
        ("Pies & Hot Food", "pie,quiche,rotisserie,cooked chicken,sausage roll,scotch egg,pasty"),
        ("Cheese, Dairy & Eggs", "cheese,cheddar,brie,feta,milk,butter,spread,margarine,yogurt,yoghurt,cream,egg,custard"),
        ("Chilled & Ready Meals", "ready meal,fresh pasta,pizza,dip,houmous,hummus,quorn,tofu,dessert,trifle,juice"),
        ("Food Cupboard", "tin,tinned,soup,beans,spaghetti,pasta,rice,noodle,sauce,curry,oil,vinegar,stock,gravy,spice,seasoning,flour,sugar,baking,jam,honey,marmalade,peanut butter,pickle,mayo,mayonnaise,ketchup,mustard"),
        ("Breakfast Cereals", "cereal,porridge,oats,granola,muesli,weetabix"),
        ("World Foods", "world,mexican,indian,chinese,thai,polish,halal,kosher,soy,wasabi,tortilla,fajita"),
        ("Biscuits & Chocolate", "biscuit,cookie,chocolate,sweets,candy,cake bar,mints"),
        ("Crisps & Snacks", "crisps,nuts,popcorn,snack,cracker,rice cake,breadsticks"),
        ("Tea, Coffee & Soft Drinks", "tea,coffee,hot chocolate,water,juice,squash,cordial,cola,lemonade,pop,fizzy,energy drink,tonic"),
        ("Beer, Wine & Spirits", "beer,lager,ale,cider,wine,prosecco,champagne,gin,vodka,whisky,rum,brandy,spirits"),
        ("Frozen", "frozen,ice cream,lolly,fish finger,frozen chips,frozen peas,frozen pizza"),
        ("Health & Beauty", "shampoo,conditioner,soap,shower gel,toothpaste,toothbrush,mouthwash,deodorant,razor,shaving,sanitary,tampon,makeup,skincare,moisturiser,suncream,medicine,paracetamol,ibuprofen,vitamin,plaster,tissues"),
        ("Baby & Toddler", "nappy,nappies,wipes,baby,formula"),
        ("Household & Cleaning", "cleaner,bleach,washing up,laundry,detergent,softener,dishwasher,bin bag,foil,cling film,baking paper,kitchen roll,toilet roll,sponge,cloth,air freshener,battery,lightbulb,candle"),
        ("Pet", "dog,cat,pet,litter,bird seed"),
        ("Home & Seasonal", "homeware,seasonal,stationery,card,magazine,toy"),
        ("Checkout", "gum,mint"),
    ],
    "aldi": [
        ("Fruit & Vegetables", "fruit,veg,vegetable,salad,banana,apple,orange,berry,strawberry,grape,melon,lemon,avocado,tomato,cucumber,pepper,lettuce,spinach,potato,onion,carrot,broccoli,cauliflower,mushroom,garlic,herb,chilli"),
        ("Bakery & Bread", "bread,loaf,roll,bap,baguette,croissant,pastry,doughnut,muffin,crumpet,bagel,wrap,pitta,cake,brioche"),
        ("Fresh Meat & Fish", "chicken,beef,pork,lamb,mince,steak,turkey,gammon,burger,chop,fish,salmon,cod,prawn,seafood"),
        ("Cooked Meats, Deli & Dips", "ham,deli,salami,chorizo,pate,olive,antipasti,cooked meat,dip,houmous,hummus,coleslaw"),
        ("Dairy, Eggs & Juice", "milk,cheese,cheddar,butter,spread,yogurt,yoghurt,cream,egg,custard,juice"),
        ("Chilled & Ready Meals", "ready meal,fresh pasta,pizza,quorn,tofu,dessert,sausage,bacon"),
        ("Food Cupboard", "tin,tinned,soup,beans,spaghetti,pasta,rice,noodle,sauce,curry,oil,vinegar,stock,gravy,spice,seasoning,jam,honey,peanut butter,mayo,ketchup,world,mexican,indian,chinese"),
        ("Breakfast & Baking", "cereal,porridge,oats,granola,muesli,flour,sugar,baking"),
        ("Biscuits, Sweets & Snacks", "biscuit,cookie,chocolate,sweets,candy,crisps,nuts,popcorn,snack,cracker"),
        ("Middle Aisle Specialbuys", "specialbuy,special buy,tool,diy,garden,kitchenware,clothing,gadget"),
        ("Frozen", "frozen,ice cream,lolly,fish finger,frozen chips,frozen peas,frozen pizza"),
        ("Tea, Coffee & Soft Drinks", "tea,coffee,hot chocolate,water,squash,cola,lemonade,pop,fizzy,energy drink"),
        ("Beer, Wine & Spirits", "beer,lager,ale,cider,wine,prosecco,gin,vodka,whisky,rum,spirits"),
        ("Health & Beauty", "shampoo,conditioner,soap,shower gel,toothpaste,toothbrush,deodorant,razor,sanitary,makeup,skincare,suncream,medicine,paracetamol,vitamin,plaster,tissues"),
        ("Household & Cleaning", "cleaner,bleach,washing up,laundry,detergent,softener,bin bag,foil,cling film,kitchen roll,toilet roll,sponge,battery,candle"),
        ("Baby & Pet", "nappy,nappies,wipes,baby,formula,dog,cat,pet,litter"),
        ("Checkout", "gum,mint"),
    ],
    "lidl": [
        ("Bakery", "bread,loaf,roll,bap,baguette,croissant,pastry,doughnut,muffin,pretzel,bagel,pain au chocolat,cake"),
        ("Fruit & Vegetables", "fruit,veg,vegetable,salad,banana,apple,orange,berry,strawberry,grape,melon,lemon,avocado,tomato,cucumber,pepper,lettuce,spinach,potato,onion,carrot,broccoli,cauliflower,mushroom,garlic,herb,chilli"),
        ("Dairy, Eggs & Juice", "milk,cheese,cheddar,butter,spread,yogurt,yoghurt,cream,egg,custard,juice"),
        ("Cooked Meats, Deli & Dips", "ham,deli,salami,chorizo,pate,olive,antipasti,cooked meat,dip,houmous,hummus,coleslaw"),
        ("Fresh Meat, Fish & Poultry", "chicken,beef,pork,lamb,mince,steak,turkey,gammon,burger,chop,sausage,bacon,fish,salmon,cod,prawn,seafood"),
        ("Chilled & Ready Meals", "ready meal,fresh pasta,pizza,quorn,tofu,dessert"),
        ("Food Cupboard", "tin,tinned,soup,beans,spaghetti,pasta,rice,noodle,sauce,curry,oil,vinegar,stock,gravy,spice,seasoning,jam,honey,peanut butter,mayo,ketchup,world,mexican,indian,chinese"),
        ("Breakfast & Baking", "cereal,porridge,oats,granola,muesli,flour,sugar,baking"),
        ("Biscuits, Sweets & Snacks", "biscuit,cookie,chocolate,sweets,candy,crisps,nuts,popcorn,snack,cracker"),
        ("Middle of Lidl", "middle of lidl,parkside,tool,diy,garden,kitchenware,clothing,gadget"),
        ("Tea, Coffee & Soft Drinks", "tea,coffee,hot chocolate,water,squash,cola,lemonade,pop,fizzy,energy drink"),
        ("Beer, Wine & Spirits", "beer,lager,ale,cider,wine,prosecco,gin,vodka,whisky,rum,spirits"),
        ("Frozen", "frozen,ice cream,lolly,fish finger,frozen chips,frozen peas,frozen pizza"),
        ("Health & Beauty", "shampoo,conditioner,soap,shower gel,toothpaste,toothbrush,deodorant,razor,sanitary,makeup,skincare,suncream,medicine,paracetamol,vitamin,plaster,tissues"),
        ("Household & Cleaning", "cleaner,bleach,washing up,laundry,detergent,softener,bin bag,foil,cling film,kitchen roll,toilet roll,sponge,battery,candle"),
        ("Baby & Pet", "nappy,nappies,wipes,baby,formula,dog,cat,pet,litter"),
        ("Checkout", "gum,mint"),
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
