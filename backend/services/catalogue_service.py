from contextlib import closing

from backend.database import get_connection


def _normalize(value: str) -> str:
    """Apply the same basic case-insensitive normalization as inventory search."""
    return value.strip().casefold()


def _normalize_optional(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return _normalize(value)


def _get_active_shop(connection, shop_id: int):
    return connection.execute(
        """
        SELECT id, name
        FROM shops
        WHERE id = ? AND is_active = 1
        """,
        (shop_id,),
    ).fetchone()


def register_product_for_shop(shop_id: int, payload) -> dict:
    """
    Register a product for a shop, or reuse an identical product
    already listed in that shop.

    Product identity is based on name, brand, variant and base unit.
    Price and stock remain shop-specific.
    """

    normalized_name = _normalize(payload.name)
    normalized_unit = _normalize(payload.base_unit)
    normalized_brand = _normalize_optional(payload.brand)
    normalized_variant = _normalize_optional(payload.variant)

    with closing(get_connection()) as connection:
        with connection:
            shop = _get_active_shop(connection, shop_id)

            if shop is None:
                raise ValueError("Active shop not found.")

            # Search only within this shop's existing inventory.
            candidates = connection.execute(
                """
                SELECT
                    p.id,
                    p.name,
                    p.normalized_name,
                    p.brand,
                    p.variant,
                    p.base_unit
                FROM inventory i
                JOIN products p ON p.id = i.product_id
                WHERE i.shop_id = ?
                  AND p.normalized_name = ?
                """,
                (shop_id, normalized_name),
            ).fetchall()

            matching_products = [
                product
                for product in candidates
                if (
                    _normalize(product["base_unit"]) == normalized_unit
                    and _normalize_optional(product["brand"])
                    == normalized_brand
                    and _normalize_optional(product["variant"])
                    == normalized_variant
                )
            ]

            if len(matching_products) > 1:
                raise ValueError(
                    "Multiple identical product records already exist "
                    "in this shop. Resolve them before registering again."
                )

            if matching_products:
                product_id = matching_products[0]["id"]
                product_created = False
            else:
                product_id = connection.insert_returning_id(
                    """
                    INSERT INTO products (
                        name,
                        normalized_name,
                        brand,
                        variant,
                        base_unit
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        payload.name.strip(),
                        normalized_name,
                        payload.brand,
                        payload.variant,
                        normalized_unit,
                    ),
                )
                product_created = True

            # Create or update this shop's price and stock.
            connection.execute(
                """
                INSERT INTO inventory (
                    shop_id,
                    product_id,
                    price_per_unit,
                    stock_quantity,
                    is_available
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(shop_id, product_id) DO UPDATE SET
                    price_per_unit = excluded.price_per_unit,
                    stock_quantity = excluded.stock_quantity,
                    is_available = excluded.is_available
                """,
                (
                    shop_id,
                    product_id,
                    payload.price_paise,
                    payload.stock_quantity,
                    int(payload.is_available),
                ),
            )

            # Register alternate names for this product.
            for alias in payload.aliases:
                normalized_alias = _normalize(alias)

                # The canonical name does not need to be registered again.
                if normalized_alias == normalized_name:
                    continue

                connection.execute(
                    """
                    INSERT INTO product_aliases (
                        product_id,
                        alias,
                        normalized_alias
                    )
                    VALUES (?, ?, ?)
                    ON CONFLICT(product_id, normalized_alias)
                    DO UPDATE SET alias = excluded.alias
                    """,
                    (
                        product_id,
                        alias.strip(),
                        normalized_alias,
                    ),
                )

    return {
        "product_id": product_id,
        "product_created": product_created,
        "shop_id": shop_id,
        "shop_name": shop["name"],
        "name": payload.name.strip(),
        "brand": payload.brand,
        "variant": payload.variant,
        "base_unit": normalized_unit,
        "price_per_unit": payload.price_paise,
        "stock_quantity": payload.stock_quantity,
        "is_available": payload.is_available,
        "aliases": payload.aliases,
    }


def get_shop_inventory(shop_id: int) -> list[dict]:
    """Return all products and inventory details for an active shop."""

    with closing(get_connection()) as connection:
        shop = _get_active_shop(connection, shop_id)

        if shop is None:
            raise ValueError("Active shop not found.")

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
            WHERE i.shop_id = ?
            ORDER BY p.name, p.brand, p.variant
            """,
            (shop_id,),
        ).fetchall()

        return [
            {
                **dict(row),
                "price_rupees": round(row["price_per_unit"] / 100, 2),
                "is_available": bool(row["is_available"]),
            }
            for row in rows
        ]


def update_shop_inventory(
    shop_id: int,
    product_id: int,
    updates: dict,
) -> dict:
    """Update selected inventory fields belonging to a specific shop."""

    allowed_columns = {
        "price_paise": "price_per_unit",
        "stock_quantity": "stock_quantity",
        "is_available": "is_available",
    }

    if not updates:
        raise ValueError("At least one inventory field is required.")

    assignments = []
    parameters = []

    for field, value in updates.items():
        if field not in allowed_columns:
            raise ValueError(f"Unsupported inventory field: {field}")

        if value is None:
            raise ValueError(f"{field} cannot be null.")

        if field == "is_available":
            value = int(value)

        assignments.append(f"{allowed_columns[field]} = ?")
        parameters.append(value)

    parameters.extend([shop_id, product_id])

    with closing(get_connection()) as connection:
        with connection:
            shop = _get_active_shop(connection, shop_id)

            if shop is None:
                raise ValueError("Active shop not found.")

            existing = connection.execute(
                """
                SELECT product_id
                FROM inventory
                WHERE shop_id = ? AND product_id = ?
                """,
                (shop_id, product_id),
            ).fetchone()

            if existing is None:
                raise ValueError(
                    "Product is not registered in this shop."
                )

            sql = (
                "UPDATE inventory SET "
                + ", ".join(assignments)
                + " WHERE shop_id = ? AND product_id = ?"
            )

            connection.execute(sql, tuple(parameters))

            updated = connection.execute(
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
                WHERE i.shop_id = ? AND i.product_id = ?
                """,
                (shop_id, product_id),
            ).fetchone()

    result = dict(updated)
    result["price_rupees"] = round(result["price_per_unit"] / 100, 2)
    result["is_available"] = bool(result["is_available"])

    return result


def add_shop_product_alias(
    shop_id: int,
    product_id: int,
    alias: str,
) -> None:
    """Add an alternate name only for a product listed by this shop."""

    alias = alias.strip()
    normalized_alias = _normalize(alias)

    if not normalized_alias:
        raise ValueError("Product alias cannot be empty.")

    with closing(get_connection()) as connection:
        with connection:
            shop = _get_active_shop(connection, shop_id)

            if shop is None:
                raise ValueError("Active shop not found.")

            product = connection.execute(
                """
                SELECT p.id, p.normalized_name
                FROM products p
                JOIN inventory i ON i.product_id = p.id
                WHERE i.shop_id = ?
                  AND p.id = ?
                """,
                (shop_id, product_id),
            ).fetchone()

            if product is None:
                raise ValueError(
                    "Product is not registered in this shop."
                )

            if normalized_alias == product["normalized_name"]:
                raise ValueError(
                    "The canonical product name cannot be registered as an alias."
                )

            connection.execute(
                """
                INSERT INTO product_aliases (
                    product_id,
                    alias,
                    normalized_alias
                )
                VALUES (?, ?, ?)
                ON CONFLICT(product_id, normalized_alias)
                DO UPDATE SET alias = excluded.alias
                """,
                (product_id, alias, normalized_alias),
            )
