"""Builds demo/demo.sqlite: a small SYNTHETIC online-shop database (customers, products, orders).
Deterministic (fixed seed). The data is made up; it exists so visitors can try the agent without uploading anything."""
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

OUT = Path("demo/demo.sqlite")
rng = random.Random(7)
COUNTRIES = ["Nigeria", "Kenya", "Ghana", "South Africa", "Egypt", "Morocco"]
CATEGORIES = {"Electronics": ["Phone", "Headphones", "Charger", "Tablet", "Speaker"], "Home": ["Lamp", "Kettle", "Blender", "Fan"],
              "Books": ["Novel", "Textbook", "Cookbook", "Atlas"], "Fashion": ["Sneakers", "Jacket", "Backpack", "Watch"]}
FIRST = ["Ada", "Tunde", "Amina", "Kofi", "Zane", "Lerato", "Omar", "Nia", "Femi", "Sara", "Yusuf", "Chidi", "Hana", "Ife", "Musa"]


def main() -> None:
    OUT.parent.mkdir(exist_ok=True)
    OUT.unlink(missing_ok=True)
    c = sqlite3.connect(OUT)
    c.executescript("""
    CREATE TABLE customers(customer_id INTEGER PRIMARY KEY, name TEXT, country TEXT, signup_date TEXT);
    CREATE TABLE products(product_id INTEGER PRIMARY KEY, name TEXT, category TEXT, price REAL);
    CREATE TABLE orders(order_id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(customer_id), order_date TEXT, status TEXT);
    CREATE TABLE order_items(order_id INTEGER REFERENCES orders(order_id), product_id INTEGER REFERENCES products(product_id), quantity INTEGER);
    """)
    start = date(2025, 1, 1)
    c.executemany("INSERT INTO customers VALUES (?,?,?,?)",
                  [(i, f"{rng.choice(FIRST)} {chr(65 + i % 26)}.", rng.choice(COUNTRIES), (start + timedelta(days=rng.randint(0, 200))).isoformat()) for i in range(1, 81)])
    products = [(pid, f"{name} {rng.choice(['Mini', 'Pro', 'Plus', 'Lite'])}", cat, round(rng.uniform(5, 400), 2))
                for pid, (cat, name) in enumerate(((cat, n) for cat, ns in CATEGORIES.items() for n in ns), 1)]
    c.executemany("INSERT INTO products VALUES (?,?,?,?)", products)
    items = []
    for oid in range(1, 301):
        c.execute("INSERT INTO orders VALUES (?,?,?,?)", (oid, rng.randint(1, 80), (date(2025, 3, 1) + timedelta(days=rng.randint(0, 300))).isoformat(),
                                                           rng.choices(["delivered", "shipped", "cancelled", "returned"], [70, 15, 10, 5])[0]))
        for pid in rng.sample(range(1, len(products) + 1), rng.randint(1, 3)):
            items.append((oid, pid, rng.randint(1, 4)))
    c.executemany("INSERT INTO order_items VALUES (?,?,?)", items)
    c.commit()
    c.close()
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
