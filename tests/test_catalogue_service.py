import pytest

from backend import database
from backend.models.catalogue import (
    ShopProductCreate,
    InventoryUpdate,
)
from backend.services.inventory_service import (
    create_shop,
    find_inventory,
)
from backend.services.catalogue_service import (
    register_product_for_shop,
    get_shop_inventory,
    update_shop_inventory,
    add_shop_product_alias,
)


@pytest.fixture
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATABASE_URL", "")
    monkeypatch.setattr(
        database,
        "DATABASE_PATH",
        tmp_path / "catalogue_test.db",
    )
    database.initialize_database()


def make_product(
    name="Rice",
    brand=None,
    price=6500,
    stock=20,
    aliases=None,
):
    return ShopProductCreate(
        name=name,
        base_unit="kg",
        brand=brand,
        price_paise=price,
        stock_quantity=stock,
        aliases=aliases or [],
    )


def test_register_product_for_shop(isolated_database):
    shop_id = create_shop("Catalogue Test Shop")

    result = register_product_for_shop(
        shop_id,
        make_product(aliases=["Biyyam", "బియ్యం"]),
    )

    assert result["product_created"] is True
    assert result["shop_id"] == shop_id
    assert result["name"] == "Rice"

    matches = find_inventory(shop_id, "biyyam")

    assert len(matches) == 1
    assert matches[0]["price_per_unit"] == 6500

    print("PASS: Product and aliases registered")


def test_duplicate_registration_reuses_product_in_same_shop(
    isolated_database,
):
    shop_id = create_shop("Duplicate Prevention Shop")

    first = register_product_for_shop(
        shop_id,
        make_product(price=6500, stock=20),
    )

    second = register_product_for_shop(
        shop_id,
        make_product(price=6800, stock=15),
    )

    assert first["product_id"] == second["product_id"]
    assert first["product_created"] is True
    assert second["product_created"] is False

    with database.get_connection() as connection:
        product_count = connection.execute(
            """
            SELECT COUNT(*) AS total
            FROM products p
            JOIN inventory i ON i.product_id = p.id
            WHERE i.shop_id = ?
            """,
            (shop_id,),
        ).fetchone()["total"]

    assert product_count == 1

    inventory = get_shop_inventory(shop_id)

    assert inventory[0]["price_per_unit"] == 6800
    assert inventory[0]["stock_quantity"] == 15

    print("PASS: Duplicate registration reuses existing product")


def test_products_are_shop_scoped(isolated_database):
    shop_a = create_shop("Shop A")
    shop_b = create_shop("Shop B")

    product_a = register_product_for_shop(
        shop_a,
        make_product(price=6500),
    )

    product_b = register_product_for_shop(
        shop_b,
        make_product(price=7000),
    )

    assert product_a["product_id"] != product_b["product_id"]

    assert get_shop_inventory(shop_a)[0]["price_per_unit"] == 6500
    assert get_shop_inventory(shop_b)[0]["price_per_unit"] == 7000

    print("PASS: Different shops maintain separate inventory products")


def test_inventory_update_changes_only_requested_fields(
    isolated_database,
):
    shop_id = create_shop("Inventory Update Shop")

    product = register_product_for_shop(
        shop_id,
        make_product(price=6500, stock=20),
    )

    result = update_shop_inventory(
        shop_id,
        product["product_id"],
        InventoryUpdate(stock_quantity=12).model_dump(
            exclude_unset=True
        ),
    )

    assert result["stock_quantity"] == 12
    assert result["price_per_unit"] == 6500

    print("PASS: Partial inventory update preserves other fields")


def test_inventory_update_rejects_another_shops_product(
    isolated_database,
):
    shop_a = create_shop("Owner A Shop")
    shop_b = create_shop("Owner B Shop")

    product_b = register_product_for_shop(
        shop_b,
        make_product(),
    )

    with pytest.raises(ValueError, match="not registered"):
        update_shop_inventory(
            shop_a,
            product_b["product_id"],
            {"price_paise": 7000},
        )

    print("PASS: Cross-shop inventory update rejected")


def test_alias_cannot_be_added_from_another_shop(
    isolated_database,
):
    shop_a = create_shop("Alias Shop A")
    shop_b = create_shop("Alias Shop B")

    product_b = register_product_for_shop(
        shop_b,
        make_product(),
    )

    with pytest.raises(ValueError, match="not registered"):
        add_shop_product_alias(
            shop_a,
            product_b["product_id"],
            "biyyam",
        )

    print("PASS: Cross-shop alias registration rejected")


def test_inactive_shop_inventory_cannot_be_retrieved(
    isolated_database,
):
    shop_id = create_shop("Inactive Catalogue Shop")

    with database.get_connection() as connection:
        connection.execute(
            "UPDATE shops SET is_active = 0 WHERE id = ?",
            (shop_id,),
        )

    with pytest.raises(ValueError, match="Active shop not found"):
        get_shop_inventory(shop_id)

    print("PASS: Inactive shop inventory access rejected")
