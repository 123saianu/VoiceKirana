from pathlib import Path
import sqlite3

DB_PATH = Path(__file__).resolve().parent / "data" / "voicekirana.db"

print("=" * 55)
print("VOICEKIRANA - CATALOGUE INSPECTION")
print("=" * 55)
print("Database:", DB_PATH)

if not DB_PATH.exists():
    print("ERROR: Database file not found!")
    raise SystemExit(1)

connection = sqlite3.connect(
    f"{DB_PATH.resolve().as_uri()}?mode=ro",
    uri=True
)
connection.row_factory = sqlite3.Row

try:
    print("\n--- PRODUCTS MATCHING JAGGERY ---")

    products = connection.execute(
        """
        SELECT
            p.id,
            p.name,
            p.normalized_name,
            p.brand,
            p.variant,
            p.base_unit,
            i.shop_id,
            i.price_per_unit,
            i.stock_quantity,
            i.is_available
        FROM products p
        LEFT JOIN inventory i
            ON i.product_id = p.id
            AND i.shop_id = 6
        WHERE LOWER(p.name) LIKE '%jaggery%'
           OR LOWER(p.normalized_name) LIKE '%jaggery%'
        ORDER BY p.id
        """
    ).fetchall()

    if products:
        for product in products:
            print(dict(product))
    else:
        print("No matching canonical products found.")

    print("\n--- JAGGERY ALIASES ---")

    aliases = connection.execute(
        """
        SELECT
            a.normalized_alias,
            p.id AS product_id,
            p.name,
            p.normalized_name,
            p.base_unit,
            i.shop_id,
            i.price_per_unit,
            i.stock_quantity,
            i.is_available
        FROM product_aliases a
        JOIN products p
            ON p.id = a.product_id
        LEFT JOIN inventory i
            ON i.product_id = p.id
            AND i.shop_id = 6
        WHERE LOWER(a.normalized_alias) LIKE '%jaggery%'
        ORDER BY a.normalized_alias
        """
    ).fetchall()

    if aliases:
        for alias in aliases:
            print(dict(alias))
    else:
        print("No matching aliases found.")

    print("\n--- SHOP 6 ---")

    shop = connection.execute(
        "SELECT id, name, is_active FROM shops WHERE id = 6"
    ).fetchone()

    print(dict(shop) if shop else "Shop 6 not found.")

finally:
    connection.close()

print("\nInspection completed. Database was not modified.")
