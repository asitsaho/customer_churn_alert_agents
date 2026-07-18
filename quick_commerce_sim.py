#!/usr/bin/env python3
"""
Quick-Commerce Platform Simulator (Blinkit / Zepto style)
=========================================================

Creates a local SQLite database, seeds reference + user data, backfills
>= 100 days of historical activity, and can simulate live data generation.

Standard library only -- no external dependencies.

USAGE
-----
    # 1. Build the DB and backfill 120 days of history (default)
    python quick_commerce_sim.py init

    # 2. Backfill a custom number of days into a custom db path
    python quick_commerce_sim.py init --db ./qcommerce.db --days 150

    # 3. Simulate live traffic (appends new events on top of existing data)
    python quick_commerce_sim.py live --db ./qcommerce.db --interval 2 --ticks 60

    # 4. Do both in one go
    python quick_commerce_sim.py all --db ./qcommerce.db --days 120

CHURN
-----
A configurable fraction of customers are marked as "churned": they generate
NO activity in the last N weeks, so the data naturally contains users who
have stopped using the platform recently.
"""

import argparse
import hashlib
import os
import random
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
DEFAULT_DB_PATH = "qcommerce.db"
DEFAULT_HISTORY_DAYS = 120          # >= 100 as required
NUM_CUSTOMERS = 40
CHURN_FRACTION = 0.25               # ~25% of customers go quiet
CHURN_MIN_WEEKS = 2                 # churned users inactive for >= 2 weeks
CHURN_MAX_WEEKS = 8

RANDOM_SEED = 42                    # reproducible; set to None for true randomness
if RANDOM_SEED is not None:
    random.seed(RANDOM_SEED)

IST = timezone(timedelta(hours=5, minutes=30))  # Indian Standard Time


# --------------------------------------------------------------------------- #
# Reference data pools
# --------------------------------------------------------------------------- #
FIRST_NAMES = [
    "Ananya", "Rohit", "Priya", "Karan", "Sneha", "Devraj", "Meera", "Arjun",
    "Kavya", "Vikram", "Isha", "Aditya", "Nisha", "Rahul", "Divya", "Sanjay",
    "Pooja", "Amit", "Riya", "Manish", "Shreya", "Nikhil", "Tara", "Varun",
    "Lakshmi", "Gaurav", "Aisha", "Sameer", "Neha", "Kunal",
]
LAST_NAMES = [
    "Rao", "Sharma", "Menon", "Gill", "Iyer", "Singh", "Nair", "Reddy",
    "Patel", "Kapoor", "Gupta", "Nanda", "Bose", "Chopra", "Verma", "Joshi",
]
CITIES = [
    ("Hyderabad", "Telangana", "5000"), ("Mumbai", "Maharashtra", "4000"),
    ("Bengaluru", "Karnataka", "5600"), ("Chennai", "Tamil Nadu", "6000"),
    ("Delhi", "Delhi", "1100"),         ("Pune", "Maharashtra", "4110"),
    ("Gurugram", "Haryana", "1220"),    ("Kolkata", "West Bengal", "7000"),
]
GENDERS = ["MALE", "FEMALE", "OTHER"]
DEVICE_TYPES = ["ANDROID", "IOS", "WEB"]
DEVICE_INFO = {
    "ANDROID": ["Pixel 8, Android 15", "Samsung S23, Android 14", "OnePlus 12, Android 15",
                "Redmi Note 13, Android 14"],
    "IOS":     ["iPhone 15, iOS 18.1", "iPhone 13, iOS 17.5", "iPhone 14, iOS 18.0"],
    "WEB":     ["Chrome 126 / Windows 11", "Safari / macOS", "Firefox / Ubuntu"],
}
APP_VERSIONS = ["4.10.0", "4.11.5", "4.12.0", "4.12.1"]

PRODUCTS = [
    ("Amul Toned Milk 500ml",       "Dairy",         28.00),
    ("Britannia Brown Bread",       "Bakery",        45.00),
    ("Tomato 1kg",                  "Vegetables",    32.00),
    ("Lay's Classic Salted 52g",    "Snacks",        20.00),
    ("Colgate Strong Teeth 200g",   "Personal Care", 95.00),
    ("Red Bull Energy Drink 250ml", "Beverages",    125.00),
    ("Onion 1kg",                   "Vegetables",    38.00),
    ("Aashirvaad Atta 5kg",         "Staples",      265.00),
    ("Maggi Noodles 4-pack",        "Snacks",        56.00),
    ("Dettol Handwash 200ml",       "Personal Care", 99.00),
    ("Coca-Cola 750ml",             "Beverages",     40.00),
    ("Farm Eggs 6-pack",            "Dairy",         66.00),
]

TICKET_CATEGORIES = ["ORDER_ISSUE", "PAYMENT", "DELIVERY_DELAY",
                     "PRODUCT_QUALITY", "REFUND", "APP_ISSUE", "OTHER"]
TICKET_PRIORITY = ["LOW", "MEDIUM", "HIGH", "URGENT"]
TICKET_STATUS = ["OPEN", "IN_PROGRESS", "WAITING_ON_CUSTOMER", "RESOLVED", "CLOSED"]

TICKET_TEMPLATES = {
    "ORDER_ISSUE":     ("Order issue with my delivery", "One or more items were missing from my order."),
    "PAYMENT":         ("Payment deducted but order not placed", "Money was debited but the order didn't confirm."),
    "DELIVERY_DELAY":  ("Delivery took too long", "App promised 10 minutes but it took much longer."),
    "PRODUCT_QUALITY": ("Product quality issue", "The item received was damaged / stale / leaking."),
    "REFUND":          ("Refund not received", "I was told a refund was issued but haven't received it."),
    "APP_ISSUE":       ("App crashing", "The app crashes when I try to checkout."),
    "OTHER":           ("General query", "I have a question about my account."),
}

REVIEW_SNIPPETS = {
    5: [("Excellent!", "Fresh and delivered super fast."), ("Loved it", "Great quality, will order again.")],
    4: [("Pretty good", "Good quality, minor delay."), ("Nice", "Fresh and well packed.")],
    3: [("Average", "It was okay, nothing special."), ("Decent", "Product fine, delivery slow.")],
    2: [("Not great", "Item quality could be better."), ("Disappointed", "Packet was leaking.")],
    1: [("Bad experience", "Item was stale / expired."), ("Terrible", "Wrong item delivered.")],
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def iso(dt: datetime) -> str:
    """Store timestamps as ISO strings (SQLite has no native datetime type)."""
    return dt.isoformat(sep=" ", timespec="seconds")


def fake_hash(seed: str) -> str:
    return "hash_" + hashlib.sha256(seed.encode()).hexdigest()[:16]


def rand_phone() -> str:
    return "9" + "".join(random.choice("0123456789") for _ in range(9))


def rand_ip() -> str:
    return ".".join(str(random.randint(1, 254)) for _ in range(4))


def business_hour_time(day: datetime) -> datetime:
    """Return a datetime on `day` biased toward realistic ordering hours."""
    hour = random.choices(
        population=list(range(7, 24)),
        weights=[3, 4, 5, 4, 3, 3, 4, 6, 7, 6, 4, 3, 4, 7, 9, 8, 6],  # peaks morning + evening
        k=1,
    )[0]
    return day.replace(hour=hour, minute=random.randint(0, 59),
                       second=random.randint(0, 59), microsecond=0)


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    user_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name         TEXT    NOT NULL,
    email             TEXT    NOT NULL UNIQUE,
    phone_number      TEXT    NOT NULL UNIQUE,
    password_hash     TEXT    NOT NULL,
    date_of_birth     TEXT,
    gender            TEXT,
    address_line      TEXT,
    city              TEXT,
    state             TEXT,
    pincode           TEXT,
    user_type         TEXT    NOT NULL DEFAULT 'CUSTOMER'
                        CHECK (user_type IN ('CUSTOMER','DELIVERY_PARTNER','ADMIN','SUPPORT_AGENT')),
    account_status    TEXT    NOT NULL DEFAULT 'ACTIVE'
                        CHECK (account_status IN ('ACTIVE','INACTIVE','SUSPENDED','DELETED')),
    is_email_verified INTEGER NOT NULL DEFAULT 0,
    is_phone_verified INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    product_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name TEXT    NOT NULL,
    category     TEXT,
    price        REAL    NOT NULL,
    is_active    INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS orders (
    order_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    order_status     TEXT    NOT NULL DEFAULT 'DELIVERED'
                        CHECK (order_status IN ('PLACED','PACKED','OUT_FOR_DELIVERY','DELIVERED','CANCELLED')),
    total_amount     REAL    NOT NULL,
    delivery_address TEXT,
    placed_at        TEXT    NOT NULL,
    delivered_at     TEXT
);

CREATE TABLE IF NOT EXISTS auth_audit_log (
    log_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    event_type      TEXT    NOT NULL
                      CHECK (event_type IN ('LOGIN','LOGOUT','LOGIN_FAILED','PASSWORD_RESET')),
    event_status    TEXT    NOT NULL DEFAULT 'SUCCESS'
                      CHECK (event_status IN ('SUCCESS','FAILED')),
    session_id      TEXT,
    ip_address      TEXT,
    device_type     TEXT    CHECK (device_type IN ('ANDROID','IOS','WEB')),
    device_info     TEXT,
    app_version     TEXT,
    failure_reason  TEXT,
    event_timestamp TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS support_tickets (
    ticket_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    order_id          INTEGER REFERENCES orders(order_id) ON DELETE SET NULL,
    assigned_agent_id INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    subject           TEXT    NOT NULL,
    description       TEXT    NOT NULL,
    category          TEXT    NOT NULL
                        CHECK (category IN ('ORDER_ISSUE','PAYMENT','DELIVERY_DELAY',
                                            'PRODUCT_QUALITY','REFUND','APP_ISSUE','OTHER')),
    priority          TEXT    NOT NULL DEFAULT 'MEDIUM'
                        CHECK (priority IN ('LOW','MEDIUM','HIGH','URGENT')),
    status            TEXT    NOT NULL DEFAULT 'OPEN'
                        CHECK (status IN ('OPEN','IN_PROGRESS','WAITING_ON_CUSTOMER','RESOLVED','CLOSED')),
    resolution_notes  TEXT,
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL,
    resolved_at       TEXT
);

CREATE TABLE IF NOT EXISTS reviews (
    review_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id              INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    product_id           INTEGER NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    order_id             INTEGER REFERENCES orders(order_id) ON DELETE SET NULL,
    rating               INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    review_title         TEXT,
    review_text          TEXT,
    is_verified_purchase INTEGER NOT NULL DEFAULT 1,
    helpful_count        INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT    NOT NULL,
    UNIQUE (user_id, product_id, order_id)
);

CREATE INDEX IF NOT EXISTS idx_auth_log_user      ON auth_audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_auth_log_time      ON auth_audit_log(event_timestamp);
CREATE INDEX IF NOT EXISTS idx_orders_user        ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_placed      ON orders(placed_at);
CREATE INDEX IF NOT EXISTS idx_tickets_user       ON support_tickets(user_id);
CREATE INDEX IF NOT EXISTS idx_tickets_status     ON support_tickets(status);
CREATE INDEX IF NOT EXISTS idx_reviews_product    ON reviews(product_id);
CREATE INDEX IF NOT EXISTS idx_reviews_user       ON reviews(user_id);
"""


# --------------------------------------------------------------------------- #
# Core DB operations
# --------------------------------------------------------------------------- #
def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def seed_products(conn: sqlite3.Connection) -> list[int]:
    cur = conn.cursor()
    ids = []
    for name, cat, price in PRODUCTS:
        cur.execute(
            "INSERT INTO products (product_name, category, price, is_active) VALUES (?,?,?,1)",
            (name, cat, price),
        )
        ids.append(cur.lastrowid)
    conn.commit()
    return ids


def seed_staff(conn: sqlite3.Connection, now: datetime) -> list[int]:
    """Create support agents + an admin. Returns list of agent user_ids."""
    cur = conn.cursor()
    agent_ids = []
    staff = [
        ("Devraj Singh", "devraj.support@platform.com", "SUPPORT_AGENT"),
        ("Farah Khan",   "farah.support@platform.com",  "SUPPORT_AGENT"),
        ("Meera Nair",   "meera.admin@platform.com",    "ADMIN"),
    ]
    created = now - timedelta(days=365)
    for name, email, utype in staff:
        cur.execute(
            """INSERT INTO users (full_name, email, phone_number, password_hash,
                                  user_type, account_status, is_email_verified,
                                  is_phone_verified, created_at, updated_at)
               VALUES (?,?,?,?,?, 'ACTIVE', 1, 1, ?, ?)""",
            (name, email, rand_phone(), fake_hash(email), utype, iso(created), iso(created)),
        )
        if utype == "SUPPORT_AGENT":
            agent_ids.append(cur.lastrowid)
    conn.commit()
    return agent_ids


def seed_customers(conn: sqlite3.Connection, n: int, history_days: int, now: datetime) -> list[dict]:
    """
    Create n customers. Each gets a signup date within the history window and,
    for a churned subset, an `active_until` cutoff some weeks before `now`.
    Returns metadata used later to drive activity generation.
    """
    cur = conn.cursor()
    customers = []
    used_emails, used_phones = set(), set()

    for i in range(n):
        fn = random.choice(FIRST_NAMES)
        ln = random.choice(LAST_NAMES)
        full_name = f"{fn} {ln}"
        email = f"{fn.lower()}.{ln.lower()}{i}@example.com"
        while email in used_emails:
            email = f"{fn.lower()}.{ln.lower()}{random.randint(1000,9999)}@example.com"
        used_emails.add(email)

        phone = rand_phone()
        while phone in used_phones:
            phone = rand_phone()
        used_phones.add(phone)

        city, state, pin_prefix = random.choice(CITIES)
        pincode = pin_prefix + str(random.randint(10, 99))

        # signup somewhere in the first 80% of the window (so they have history)
        signup_offset = random.randint(int(history_days * 0.2), history_days)
        signup = business_hour_time(now - timedelta(days=signup_offset))

        # churn logic: some customers stop using the app in the last few weeks
        churned = random.random() < CHURN_FRACTION
        if churned:
            weeks_quiet = random.randint(CHURN_MIN_WEEKS, CHURN_MAX_WEEKS)
            active_until = now - timedelta(weeks=weeks_quiet)
            status = "ACTIVE"  # still active account, just dormant
        else:
            active_until = now
            status = random.choices(["ACTIVE", "SUSPENDED"], weights=[0.95, 0.05])[0]

        cur.execute(
            """INSERT INTO users (full_name, email, phone_number, password_hash,
                                  date_of_birth, gender, address_line, city, state, pincode,
                                  user_type, account_status, is_email_verified,
                                  is_phone_verified, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?, 'CUSTOMER', ?, ?, ?, ?, ?)""",
            (full_name, email, phone, fake_hash(email),
             iso(now - timedelta(days=random.randint(6570, 15000)))[:10],  # dob (~18-41 yrs)
             random.choice(GENDERS),
             f"{random.randint(1,300)}, {random.choice(['MG Road','Park Street','Ring Road','Main Rd'])}",
             city, state, pincode,
             status,
             random.choice([0, 1]), random.choice([0, 1]),
             iso(signup), iso(signup)),
        )
        customers.append({
            "user_id": cur.lastrowid,
            "signup": signup,
            "active_until": active_until,
            "churned": churned,
            "status": status,
            "city": city,
            "device": random.choice(DEVICE_TYPES),
            # each customer has an intrinsic ordering frequency (orders/week)
            "freq": random.choice([0.5, 1, 1.5, 2, 3, 4]),
            "address": f"{city}",
        })
    conn.commit()
    return customers


# --------------------------------------------------------------------------- #
# Activity generation (one customer, one day)
# --------------------------------------------------------------------------- #
def gen_login_session(conn, user, day, made_order: bool):
    """Insert a LOGIN (+ maybe LOGIN_FAILED) and matching LOGOUT for a day."""
    cur = conn.cursor()
    device = user["device"]
    login_time = business_hour_time(day)
    session_id = str(uuid.uuid4())
    ip = rand_ip()
    dinfo = random.choice(DEVICE_INFO[device])
    ver = random.choice(APP_VERSIONS)

    # occasional failed attempt just before success
    if random.random() < 0.12:
        cur.execute(
            """INSERT INTO auth_audit_log (user_id,event_type,event_status,session_id,
                    ip_address,device_type,device_info,app_version,failure_reason,event_timestamp)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (user["user_id"], "LOGIN_FAILED", "FAILED", None, ip, device, dinfo, ver,
             random.choice(["Incorrect password", "OTP expired", "Account locked"]),
             iso(login_time - timedelta(minutes=random.randint(1, 3)))),
        )

    cur.execute(
        """INSERT INTO auth_audit_log (user_id,event_type,event_status,session_id,
                ip_address,device_type,device_info,app_version,failure_reason,event_timestamp)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (user["user_id"], "LOGIN", "SUCCESS", session_id, ip, device, dinfo, ver, None, iso(login_time)),
    )

    # session duration: longer if they placed an order
    dur = random.randint(8, 25) if made_order else random.randint(1, 10)
    logout_time = login_time + timedelta(minutes=dur)
    cur.execute(
        """INSERT INTO auth_audit_log (user_id,event_type,event_status,session_id,
                ip_address,device_type,device_info,app_version,failure_reason,event_timestamp)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (user["user_id"], "LOGOUT", "SUCCESS", session_id, ip, device, dinfo, ver, None, iso(logout_time)),
    )
    return login_time


def gen_order(conn, user, product_ids, day):
    """Create one order (1-5 items) for the user. Returns (order_id, status, items)."""
    cur = conn.cursor()
    n_items = random.randint(1, 5)
    chosen = random.sample(product_ids, n_items)
    total = 0.0
    for pid in chosen:
        price = PRODUCT_PRICE_BY_ID[pid]
        total += price * random.randint(1, 3)

    status = random.choices(
        ["DELIVERED", "CANCELLED", "OUT_FOR_DELIVERY"],
        weights=[0.85, 0.08, 0.07], k=1,
    )[0]
    placed = business_hour_time(day)
    delivered = None
    if status == "DELIVERED":
        delivered = placed + timedelta(minutes=random.randint(8, 40))

    cur.execute(
        """INSERT INTO orders (user_id, order_status, total_amount, delivery_address,
                               placed_at, delivered_at)
           VALUES (?,?,?,?,?,?)""",
        (user["user_id"], status, round(total, 2), user["address"], iso(placed),
         iso(delivered) if delivered else None),
    )
    return cur.lastrowid, status, chosen, placed


def gen_ticket(conn, user, order_id, agent_ids, day):
    cur = conn.cursor()
    cat = random.choice(TICKET_CATEGORIES)
    subject, desc = TICKET_TEMPLATES[cat]
    priority = random.choices(TICKET_PRIORITY, weights=[0.3, 0.4, 0.2, 0.1])[0]
    status = random.choices(TICKET_STATUS, weights=[0.2, 0.15, 0.1, 0.35, 0.2])[0]
    created = business_hour_time(day)
    agent = random.choice(agent_ids) if status != "OPEN" else None
    resolved_at, notes, updated = None, None, created
    if status in ("RESOLVED", "CLOSED"):
        resolved_at = created + timedelta(hours=random.randint(1, 48))
        updated = resolved_at
        notes = random.choice([
            "Refund issued to source.", "Coupon offered as goodwill.",
            "Issue resolved after verification.", "Escalated and fixed.",
        ])
    cur.execute(
        """INSERT INTO support_tickets (user_id, order_id, assigned_agent_id, subject,
                description, category, priority, status, resolution_notes,
                created_at, updated_at, resolved_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (user["user_id"], order_id, agent, subject, desc, cat, priority, status, notes,
         iso(created), iso(updated), iso(resolved_at) if resolved_at else None),
    )
    return cur.lastrowid


def gen_review(conn, user, product_id, order_id, day):
    cur = conn.cursor()
    rating = random.choices([1, 2, 3, 4, 5], weights=[0.06, 0.09, 0.15, 0.3, 0.4])[0]
    title, text = random.choice(REVIEW_SNIPPETS[rating])
    created = business_hour_time(day)
    try:
        cur.execute(
            """INSERT INTO reviews (user_id, product_id, order_id, rating, review_title,
                    review_text, is_verified_purchase, helpful_count, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (user["user_id"], product_id, order_id, rating, title, text,
             1, random.randint(0, 20), iso(created)),
        )
    except sqlite3.IntegrityError:
        pass  # duplicate (user, product, order) -- skip, UNIQUE constraint


# --------------------------------------------------------------------------- #
# Backfill historical data
# --------------------------------------------------------------------------- #
PRODUCT_PRICE_BY_ID: dict[int, float] = {}


def backfill_history(conn, customers, product_ids, agent_ids, history_days, now):
    """
    Walk day-by-day from (now - history_days) to now. Each day, each customer
    may log in / order / review / raise a ticket according to their frequency,
    but only up to their `active_until` cutoff (churn).
    """
    print(f"Backfilling {history_days} days of history for {len(customers)} customers...")
    start = now - timedelta(days=history_days)

    total_orders = total_logins = total_tickets = total_reviews = 0

    for day_offset in range(history_days + 1):
        day = start + timedelta(days=day_offset)
        # weekend uplift factor
        weekend_boost = 1.3 if day.weekday() >= 5 else 1.0

        for user in customers:
            # respect signup date and churn cutoff
            if day < user["signup"] or day > user["active_until"]:
                continue
            if user["status"] == "SUSPENDED" and day > user["active_until"]:
                continue

            # daily probability of any activity, derived from weekly frequency
            p_active = min(0.95, (user["freq"] / 7.0) * weekend_boost)
            if random.random() > p_active:
                continue

            # they will order on some active days, just browse on others
            made_order = random.random() < 0.7
            gen_login_session(conn, user, day, made_order)
            total_logins += 1

            if made_order:
                order_id, status, items, placed = gen_order(conn, user, product_ids, day)
                total_orders += 1

                # tickets: raised for a minority of (esp. cancelled/late) orders
                p_ticket = 0.18 if status != "DELIVERED" else 0.05
                if random.random() < p_ticket:
                    gen_ticket(conn, user, order_id, agent_ids, day + timedelta(days=random.randint(0, 1)))
                    total_tickets += 1

                # reviews: some delivered orders get reviewed, a day or two later
                if status == "DELIVERED" and random.random() < 0.35:
                    review_day = day + timedelta(days=random.randint(0, 2))
                    if review_day <= now:
                        prod = random.choice(items)
                        gen_review(conn, user, prod, order_id, review_day)
                        total_reviews += 1

        if day_offset % 20 == 0:
            conn.commit()

    conn.commit()
    print(f"  logins={total_logins}  orders={total_orders}  "
          f"tickets={total_tickets}  reviews={total_reviews}")


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_init(db_path, history_days, fresh=True):
    global PRODUCT_PRICE_BY_ID
    if fresh and os.path.exists(db_path):
        os.remove(db_path)
        print(f"Removed existing {db_path}")

    conn = connect(db_path)
    create_schema(conn)
    now = datetime.now(IST).replace(tzinfo=None)

    product_ids = seed_products(conn)
    # cache prices by id for order totals
    cur = conn.cursor()
    cur.execute("SELECT product_id, price FROM products")
    PRODUCT_PRICE_BY_ID = dict(cur.fetchall())

    agent_ids = seed_staff(conn, now)
    customers = seed_customers(conn, NUM_CUSTOMERS, history_days, now)

    backfill_history(conn, customers, product_ids, agent_ids, history_days, now)
    print_summary(conn)
    conn.close()
    print(f"\nDatabase ready at: {os.path.abspath(db_path)}")


def cmd_live(db_path, interval, ticks):
    """
    Simulate live traffic: every `interval` seconds, generate a burst of events
    for random currently-active customers, timestamped 'now'. Runs `ticks` times.
    """
    global PRODUCT_PRICE_BY_ID
    if not os.path.exists(db_path):
        print(f"DB {db_path} not found. Run `init` first.")
        return

    conn = connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT product_id, price FROM products")
    PRODUCT_PRICE_BY_ID = dict(cur.fetchall())
    product_ids = list(PRODUCT_PRICE_BY_ID.keys())

    cur.execute("SELECT user_id FROM users WHERE user_type='SUPPORT_AGENT'")
    agent_ids = [r[0] for r in cur.fetchall()]

    # only non-churned, active customers participate in live traffic
    cur.execute("""SELECT user_id, city FROM users
                   WHERE user_type='CUSTOMER' AND account_status='ACTIVE'""")
    active = [{"user_id": r[0], "city": r[1], "address": r[1],
               "device": random.choice(DEVICE_TYPES)} for r in cur.fetchall()]

    print(f"Live simulation: {ticks} ticks every {interval}s "
          f"({len(active)} active customers)...")

    for t in range(ticks):
        now = datetime.now(IST).replace(tzinfo=None)
        burst = random.randint(1, max(2, len(active) // 8))
        for user in random.sample(active, min(burst, len(active))):
            made_order = random.random() < 0.6
            gen_login_session(conn, user, now, made_order)
            if made_order:
                oid, status, items, placed = gen_order(conn, user, product_ids, now)
                if random.random() < 0.1:
                    gen_ticket(conn, user, oid, agent_ids, now)
                if status == "DELIVERED" and random.random() < 0.3:
                    gen_review(conn, user, random.choice(items), oid, now)
        conn.commit()
        print(f"  tick {t+1}/{ticks}  ({now:%H:%M:%S})  +{burst} sessions")
        if t < ticks - 1:
            time.sleep(interval)

    print_summary(conn)
    conn.close()


def print_summary(conn):
    cur = conn.cursor()
    print("\n--- DB summary ---")
    for tbl in ["users", "products", "orders", "auth_audit_log", "support_tickets", "reviews"]:
        n = cur.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        print(f"  {tbl:<18} {n:>7} rows")

    # churn insight
    row = cur.execute("""
        SELECT COUNT(*) FROM users u
        WHERE u.user_type='CUSTOMER'
          AND NOT EXISTS (
              SELECT 1 FROM orders o
              WHERE o.user_id=u.user_id
                AND o.placed_at >= datetime('now','-14 days')
          )
    """).fetchone()
    total_cust = cur.execute("SELECT COUNT(*) FROM users WHERE user_type='CUSTOMER'").fetchone()[0]
    print(f"\n  Customers with NO orders in last 14 days: {row[0]} / {total_cust} (churned/dormant)")

    span = cur.execute("""SELECT MIN(placed_at), MAX(placed_at) FROM orders""").fetchone()
    print(f"  Order date range: {span[0]}  ->  {span[1]}")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main():
    p = argparse.ArgumentParser(description="Quick-commerce SQLite data simulator")
    sub = p.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Create DB + backfill history")
    p_init.add_argument("--db", default=DEFAULT_DB_PATH)
    p_init.add_argument("--days", type=int, default=DEFAULT_HISTORY_DAYS)

    p_live = sub.add_parser("live", help="Simulate live traffic on existing DB")
    p_live.add_argument("--db", default=DEFAULT_DB_PATH)
    p_live.add_argument("--interval", type=float, default=2.0, help="seconds between ticks")
    p_live.add_argument("--ticks", type=int, default=30, help="number of ticks")

    p_all = sub.add_parser("all", help="init then live")
    p_all.add_argument("--db", default=DEFAULT_DB_PATH)
    p_all.add_argument("--days", type=int, default=DEFAULT_HISTORY_DAYS)
    p_all.add_argument("--interval", type=float, default=2.0)
    p_all.add_argument("--ticks", type=int, default=15)

    args = p.parse_args()

    if args.command == "init":
        cmd_init(args.db, args.days)
    elif args.command == "live":
        cmd_live(args.db, args.interval, args.ticks)
    elif args.command == "all":
        cmd_init(args.db, args.days)
        cmd_live(args.db, args.interval, args.ticks)


if __name__ == "__main__":
    main()