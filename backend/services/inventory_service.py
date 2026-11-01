from backend.database import get_connection


def create_shop(name: str) -> int:
    """Create a shop and return its database ID."""
    name = name.strip()
    if not name:
        raise ValueError("Shop name cannot be empty.")

    with get_connection() as connection:
        cursor = connection.execute(
            "INSERT INTO shops (name) VALUES (?)",
            (name,)
        )
        return cursor.lastrowid


def create_product(
    name: str,
    base_unit: str,
    brand: str | None = None,
    variant: str | None = None,
) -> int:
    """Register a product without restricting the product vocabulary."""
    name = name.strip()
    base_unit = base_unit.strip().lower()

    if not name or not base_unit:
        raise ValueError("Product name and base unit are required.")

    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO products
                (name, normalized_name, brand, variant, base_unit)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, name.casefold(), brand, variant, base_unit)
        )
        return cursor.lastrowid


def set_inventory(
    shop_id: int,
    product_id: int,
    price_paise: int,
    stock_quantity: float,
    is_available: bool = True,
) -> None:
    """Set a shop's price and available stock for a product."""
    if price_paise < 0:
        raise ValueError("Price cannot be negative.")
    if stock_quantity < 0:
        raise ValueError("Stock cannot be negative.")

    with get_connection() as connection:
        shop = connection.execute(
            "SELECT id FROM shops WHERE id = ? AND is_active = 1",
            (shop_id,)
        ).fetchone()

        product = connection.execute(
            "SELECT id FROM products WHERE id = ?",
            (product_id,)
        ).fetchone()

        if shop is None:
            raise ValueError("Active shop not found.")
        if product is None:
            raise ValueError("Product not found.")

        connection.execute(
            """
            INSERT INTO inventory
                (shop_id, product_id, price_per_unit,
                 stock_quantity, is_available)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(shop_id, product_id) DO UPDATE SET
                price_per_unit = excluded.price_per_unit,
                stock_quantity = excluded.stock_quantity,
                is_available = excluded.is_available
            """,
            (
                shop_id,
                product_id,
                price_paise,
                stock_quantity,
                int(is_available),
            )
        )


def add_product_alias(product_id: int, alias: str) -> None:
    """Register an alternate name for an existing product."""
    alias = alias.strip()
    normalized_alias = alias.casefold()

    if not normalized_alias:
        raise ValueError("Product alias cannot be empty.")

    with get_connection() as connection:
        product = connection.execute(
            "SELECT id FROM products WHERE id = ?",
            (product_id,),
        ).fetchone()

        if product is None:
            raise ValueError("Product not found.")

        # Avoid treating the product's canonical name as an alias.
        canonical = connection.execute(
            "SELECT normalized_name FROM products WHERE id = ?",
            (product_id,),
        ).fetchone()

        if canonical["normalized_name"] == normalized_alias:
            return

        connection.execute(
            """
            INSERT INTO product_aliases
                (product_id, alias, normalized_alias)
            VALUES (?, ?, ?)
            ON CONFLICT(product_id, normalized_alias)
            DO UPDATE SET alias = excluded.alias
            """,
            (product_id, alias, normalized_alias),
        )


def find_inventory(shop_id: int, product_name: str) -> list[dict]:
    """Find exact product names first, then registered aliases."""
    normalized_name = product_name.strip().casefold()

    if not normalized_name:
        return []

    with get_connection() as connection:
        # Prefer a canonical name over an alias with the same spelling.
        rows = connection.execute(
            """
            SELECT
                p.id AS product_id,
                p.name,
                p.brand,
                p.variant,
                p.base_unit,
                i.price_per_unit,
                i.stock_quantity,
                i.is_available
            FROM inventory i
            JOIN products p ON p.id = i.product_id
            JOIN shops s ON s.id = i.shop_id
            WHERE i.shop_id = ?
              AND s.is_active = 1
              AND p.normalized_name = ?
            """,
            (shop_id, normalized_name),
        ).fetchall()

        if not rows:
            rows = connection.execute(
                """
                SELECT
                    p.id AS product_id,
                    p.name,
                    p.brand,
                    p.variant,
                    p.base_unit,
                    i.price_per_unit,
                    i.stock_quantity,
                    i.is_available
                FROM product_aliases a
                JOIN products p ON p.id = a.product_id
                JOIN inventory i ON i.product_id = p.id
                JOIN shops s ON s.id = i.shop_id
                WHERE i.shop_id = ?
                  AND s.is_active = 1
                  AND a.normalized_alias = ?
                """,
                (shop_id, normalized_name),
            ).fetchall()

    # Keep multiple matches so main.py can request clarification.
    return [dict(row) for row in rows]
