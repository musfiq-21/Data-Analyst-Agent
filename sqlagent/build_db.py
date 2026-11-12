"""Create and fill the shop database from a fixed random seed.

    python -m sqlagent.build_db            # writes data/shop.db with seed 42
    python -m sqlagent.build_db --seed 7 --out other.db

Same seed -> byte-for-byte identical rows on any machine (only `random` is used,
never the clock).
"""
import argparse
import os
import random
import sqlite3
from datetime import date, timedelta

from sqlagent import DEFAULT_DB

SEED = 42

SCHEMA = """
CREATE TABLE customers (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,
    city        TEXT    NOT NULL,
    signup_date TEXT    NOT NULL          -- ISO date, YYYY-MM-DD
);
CREATE TABLE products (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,
    category    TEXT    NOT NULL,
    unit_price  REAL    NOT NULL          -- current list price
);
CREATE TABLE orders (
    id          INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    order_date  TEXT    NOT NULL,         -- ISO date, YYYY-MM-DD
    status      TEXT    NOT NULL          -- 'completed', 'shipped' or 'cancelled'
);
CREATE TABLE order_items (
    id          INTEGER PRIMARY KEY,
    order_id    INTEGER NOT NULL REFERENCES orders(id),
    product_id  INTEGER NOT NULL REFERENCES products(id),
    quantity    INTEGER NOT NULL,
    unit_price  REAL    NOT NULL          -- price paid per unit on this order
);
""".strip()

FIRST = ["Rahim", "Karim", "Ayesha", "Fatima", "Tanvir", "Nusrat", "Imran", "Sadia",
         "Arif", "Mitu", "Shakil", "Riya", "Hasan", "Tania", "Rafi", "Lamia",
         "Sabbir", "Nadia", "Jahid", "Priya"]
LAST = ["Ahmed", "Hossain", "Islam", "Rahman", "Khan", "Chowdhury", "Das", "Sarkar",
        "Uddin", "Akter"]
CITIES = ["Dhaka", "Chattogram", "Khulna", "Rajshahi", "Sylhet", "Barishal"]
CITY_WEIGHTS = [8, 4, 3, 2, 2, 1]

PRODUCTS = [  # name, category, price
    ("Wireless Mouse", "Electronics", 25.0), ("Mechanical Keyboard", "Electronics", 85.0),
    ("USB-C Charger", "Electronics", 30.0), ("Bluetooth Speaker", "Electronics", 60.0),
    ("27-inch Monitor", "Electronics", 240.0), ("Noise-Cancelling Headphones", "Electronics", 150.0),
    ("Python Crash Course", "Books", 35.0), ("Clean Code", "Books", 40.0),
    ("The Pragmatic Programmer", "Books", 45.0), ("Database Internals", "Books", 55.0),
    ("Cotton T-Shirt", "Clothing", 12.0), ("Denim Jacket", "Clothing", 65.0),
    ("Running Shoes", "Clothing", 80.0), ("Winter Hoodie", "Clothing", 40.0),
    ("Ceramic Mug", "Home", 9.0), ("Desk Lamp", "Home", 28.0),
    ("Office Chair", "Home", 180.0), ("Bookshelf", "Home", 95.0),
    ("Yoga Mat", "Sports", 22.0), ("Dumbbell Set", "Sports", 70.0),
]

N_CUSTOMERS = 50
N_ORDERS = 200
START = date(2024, 1, 1)
DAYS = 731  # 2024-01-01 .. 2025-12-31


def _rows(seed):
    rng = random.Random(seed)

    customers = []
    for cid in range(1, N_CUSTOMERS + 1):
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        city = rng.choices(CITIES, weights=CITY_WEIGHTS)[0]
        signup = START - timedelta(days=rng.randint(0, 700))
        customers.append((cid, name, city, signup.isoformat()))

    products = [(i, n, c, p) for i, (n, c, p) in enumerate(PRODUCTS, start=1)]

    orders, items = [], []
    item_id = 1
    for oid in range(1, N_ORDERS + 1):
        cust = rng.randint(1, N_CUSTOMERS)
        # more orders in 2025 than 2024, so "growth" questions have a real answer
        day = int(DAYS * (rng.random() ** 0.8))
        odate = START + timedelta(days=min(day, DAYS - 1))
        status = rng.choices(["completed", "shipped", "cancelled"], weights=[75, 15, 10])[0]
        orders.append((oid, cust, odate.isoformat(), status))
        for pid in rng.sample(range(1, len(products) + 1), k=rng.randint(1, 4)):
            qty = rng.randint(1, 5)
            list_price = products[pid - 1][3]
            paid = round(list_price * rng.choice([1.0, 1.0, 1.0, 0.9, 0.95]), 2)
            items.append((item_id, oid, pid, qty, paid))
            item_id += 1
    return customers, products, orders, items


def build(path=DEFAULT_DB, seed=SEED):
    """(Re)create the database at `path`. Returns the path."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if os.path.exists(path):
        os.remove(path)
    customers, products, orders, items = _rows(seed)
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)
        conn.executemany("INSERT INTO customers VALUES (?,?,?,?)", customers)
        conn.executemany("INSERT INTO products VALUES (?,?,?,?)", products)
        conn.executemany("INSERT INTO orders VALUES (?,?,?,?)", orders)
        conn.executemany("INSERT INTO order_items VALUES (?,?,?,?,?)", items)
        conn.commit()
    finally:
        conn.close()
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=DEFAULT_DB)
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()
    path = build(args.out, args.seed)
    conn = sqlite3.connect(path)
    for t in ["customers", "products", "orders", "order_items"]:
        print(f"{t:12s} {conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]:5d} rows")
    conn.close()
    print(f"built {path} (seed={args.seed})")


if __name__ == "__main__":
    main()
