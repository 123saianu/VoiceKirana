
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

from backend.database import get_connection

SHOP_ID = 6


def inspect_shop_inventory():
    print("=" * 60)
    print("VOICEKIRANA - SHOP 6 INVENTORY INSPECTION")
    print("=" * 60)

    with get_connection() as connection:

        # 1. Show every product actually listed in Shop 6.
        print("\n--- SHOP 6 INVENTORY ---")

        inventory = connection.execute(
            """
            SELECT
                p.id AS product_id,
                p.name,
                p.normalized_name,
                p.brand,
                p.variant,
                p.base_unit,
                i.price_per_unit,
                i.stock_quantity,
                i.is_available
            FROM inventory i
            JOIN products p ON p.id = i.product_id
            WHERE i.shop_id = ?
            ORDER BY p.name, p.id
            """,
            (SHOP_ID,),
        ).fetchall()

        if inventory:
            for item in inventory:
                print(dict(item))
        else:
            print("No inventory products found.")

        # 2. Count products sharing the same normalized name.
        print("\n--- DUPLICATE PRODUCT SUMMARY ---")

        duplicates = connection.execute(
            """
            SELECT
                normalized_name,
                COUNT(*) AS product_records
            FROM products
            GROUP BY normalized_name
            HAVING COUNT(*) > 1
            ORDER BY product_records DESC
            LIMIT 20
            """
        ).fetchall()

        for item in duplicates:
            print(dict(item))

        # 3. Show aliases belonging to products in Shop 6.
        print("\n--- SHOP 6 PRODUCT ALIASES ---")

        aliases = connection.execute(
            """
            SELECT
                a.normalized_alias,
                p.id AS product_id,
                p.name,
                p.normalized_name
            FROM product_aliases a
            JOIN products p ON p.id = a.product_id
            JOIN inventory i ON i.product_id = p.id
            WHERE i.shop_id = ?
            ORDER BY p.name, a.normalized_alias
            """,
            (SHOP_ID,),
        ).fetchall()

        if aliases:
            for alias in aliases:
                print(dict(alias))
        else:
            print("No aliases registered for Shop 6 products.")

        print("\n--- INVENTORY COUNTS BY SHOP ---")

        shop_counts = connection.execute(
            """
            SELECT
                s.id,
                s.name,
                s.is_active,
                COUNT(i.product_id) AS inventory_records
            FROM shops s
            LEFT JOIN inventory i ON i.shop_id = s.id
            GROUP BY s.id, s.name, s.is_active
            ORDER BY s.id
            """
        ).fetchall()

        for row in shop_counts:
            print(dict(row))

        print("\n--- POSSIBLE DUPLICATE PRODUCT GROUPS ---")

        duplicate_groups = connection.execute(
            """
            SELECT
                p.normalized_name,
                p.brand,
                p.variant,
                p.base_unit,
                COUNT(p.id) AS product_records,
                COUNT(i.product_id) AS inventory_records,
                COUNT(DISTINCT i.shop_id) AS shops_with_inventory
            FROM products p
            LEFT JOIN inventory i ON i.product_id = p.id
            GROUP BY
                p.normalized_name,
                p.brand,
                p.variant,
                p.base_unit
            HAVING COUNT(p.id) > 1
            ORDER BY product_records DESC
            LIMIT 20
            """
        ).fetchall()

        for row in duplicate_groups:
            print(dict(row))

        print("\n--- PRODUCT TABLE INDEXES ---")

        indexes = connection.execute(
            """
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE tablename = 'products'
            ORDER BY indexname
            """
        ).fetchall()

        for row in indexes:
            print(dict(row))

    print("\nInspection completed. No records modified.")


if __name__ == "__main__":
    try:
        inspect_shop_inventory()
    except Exception as exc:
        print("Inspection failed:", type(exc).__name__, str(exc))
