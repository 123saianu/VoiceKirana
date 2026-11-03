
import pytest

from backend import database
from backend.database import get_connection
from backend.services.inventory_service import (
    add_product_alias,
    create_product,
    create_shop,
    find_inventory,
    set_inventory,
)


@pytest.fixture(autouse=True)
def use_sqlite_for_inventory_alias_tests(monkeypatch):
    """Prevent inventory alias tests from hanging on PostgreSQL connections."""
    monkeypatch.setattr(database, "DATABASE_URL", "")


def test_biyyam_alias_resolves_to_rice():
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT i.shop_id, p.id AS product_id
            FROM products p
            JOIN inventory i ON i.product_id = p.id
            JOIN shops s ON s.id = i.shop_id
            WHERE p.normalized_name = 'rice'
              AND s.is_active = 1
            ORDER BY i.shop_id DESC
            LIMIT 1
            """
        ).fetchone()

    assert row is not None, "No active Rice inventory found."

    shop_id = row["shop_id"]
    product_id = row["product_id"]

    add_product_alias(product_id, "biyyam")
    results = find_inventory(shop_id, "biyyam")

    assert len(results) == 1
    assert results[0]["name"] == "Rice"
    assert results[0]["price_per_unit"] == 6500


def test_shared_alias_returns_both_products():
    shop_id = None
    product_ids = []

    try:
        shop_id = create_shop("Alias Test Shop")

        for name in ("Alias Test Rice", "Alias Test Basmati"):
            product_id = create_product(name=name, base_unit="kg")
            product_ids.append(product_id)

            set_inventory(
                shop_id=shop_id,
                product_id=product_id,
                price_paise=5000,
                stock_quantity=10,
            )
            add_product_alias(product_id, "shared-test-grain")

        results = find_inventory(shop_id, "shared-test-grain")

        assert len(results) == 2
        assert {item["name"] for item in results} == {
            "Alias Test Rice",
            "Alias Test Basmati",
        }

    finally:
        with get_connection() as connection:
            for product_id in product_ids:
                connection.execute(
                    "DELETE FROM product_aliases WHERE product_id = ?",
                    (product_id,),
                )
                connection.execute(
                    "DELETE FROM inventory WHERE product_id = ?",
                    (product_id,),
                )
                connection.execute(
                    "DELETE FROM products WHERE id = ?",
                    (product_id,),
                )

            if shop_id is not None:
                connection.execute(
                    "DELETE FROM shops WHERE id = ?",
                    (shop_id,),
                )


def test_find_inventory_returns_empty_for_blank_name():
    assert find_inventory(1, "   ") == []


def test_create_shop_rejects_blank_name():
    with pytest.raises(ValueError, match="Shop name cannot be empty"):
        create_shop("   ")


def test_create_product_rejects_blank_name_or_unit():
    with pytest.raises(
        ValueError, match="Product name and base unit are required"
    ):
        create_product("   ", "kg")

    with pytest.raises(
        ValueError, match="Product name and base unit are required"
    ):
        create_product("Rice", "   ")


def test_set_inventory_rejects_negative_price_and_stock():
    shop_id = create_shop("Validation Test Shop")
    product_id = create_product("Validation Test Rice", "kg")

    try:
        with pytest.raises(ValueError, match="Price cannot be negative"):
            set_inventory(shop_id, product_id, -1, 5)

        with pytest.raises(ValueError, match="Stock cannot be negative"):
            set_inventory(shop_id, product_id, 100, -1)
    finally:
        with get_connection() as connection:
            connection.execute(
                "DELETE FROM inventory WHERE product_id = ?",
                (product_id,),
            )
            connection.execute(
                "DELETE FROM products WHERE id = ?",
                (product_id,),
            )
            connection.execute(
                "DELETE FROM shops WHERE id = ?",
                (shop_id,),
            )


def test_add_product_alias_rejects_blank_alias():
    product_id = create_product("Blank Alias Test Product", "kg")

    try:
        with pytest.raises(ValueError, match="Product alias cannot be empty"):
            add_product_alias(product_id, "   ")
    finally:
        with get_connection() as connection:
            connection.execute(
                "DELETE FROM product_aliases WHERE product_id = ?",
                (product_id,),
            )
            connection.execute(
                "DELETE FROM products WHERE id = ?",
                (product_id,),
            )


def test_set_inventory_rejects_nonexistent_shop():
    product_id = create_product("Missing Shop Test Product", "kg")

    try:
        with pytest.raises(ValueError, match="Active shop not found"):
            set_inventory(
                shop_id=-999999,
                product_id=product_id,
                price_paise=100,
                stock_quantity=5,
            )
    finally:
        with get_connection() as connection:
            connection.execute(
                "DELETE FROM products WHERE id = ?",
                (product_id,),
            )


def test_set_inventory_rejects_nonexistent_product():
    shop_id = create_shop("Missing Product Test Shop")

    try:
        with pytest.raises(ValueError, match="Product not found"):
            set_inventory(
                shop_id=shop_id,
                product_id=-999999,
                price_paise=100,
                stock_quantity=5,
            )
    finally:
        with get_connection() as connection:
            connection.execute(
                "DELETE FROM shops WHERE id = ?",
                (shop_id,),
            )


def test_add_alias_rejects_nonexistent_product():
    with pytest.raises(ValueError, match="Product not found"):
        add_product_alias(-999999, "missing-product-alias")


def test_canonical_product_name_is_not_added_as_alias():
    product_id = create_product("Canonical Alias Test Product", "kg")

    try:
        add_product_alias(
            product_id,
            "  CANONICAL ALIAS TEST PRODUCT  ",
        )

        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM product_aliases
                WHERE product_id = ?
                """,
                (product_id,),
            ).fetchone()

        assert row["count"] == 0
    finally:
        with get_connection() as connection:
            connection.execute(
                "DELETE FROM product_aliases WHERE product_id = ?",
                (product_id,),
            )
            connection.execute(
                "DELETE FROM products WHERE id = ?",
                (product_id,),
            )
