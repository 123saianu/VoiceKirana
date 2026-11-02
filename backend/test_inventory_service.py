import backend.database as database

from backend.database import initialize_database
from backend.services.inventory_service import (
    add_product_alias,
    create_product,
    create_shop,
    find_inventory,
    set_inventory,
)


def test_inventory_service_smoke(tmp_path, monkeypatch):
    # Use a fresh, isolated database for this test.
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(
        database,
        "DATABASE_PATH",
        tmp_path / "voicekirana_test.db",
    )

    initialize_database()

    # The test database is empty, so no existing rows need deleting.
    shop_id = create_shop("Smoke Test Shop")
    product_id = create_product("Rice", "kg")

    set_inventory(
        shop_id=shop_id,
        product_id=product_id,
        price_paise=6500,
        stock_quantity=20,
    )
    add_product_alias(product_id, "biyyam")

    direct_match = find_inventory(shop_id, "Rice")
    alias_match = find_inventory(shop_id, "biyyam")

    assert len(direct_match) == 1
    assert direct_match[0]["name"] == "Rice"
    assert direct_match[0]["stock_quantity"] == 20

    assert len(alias_match) == 1
    assert alias_match[0]["name"] == "Rice"
    assert alias_match[0]["price_per_unit"] == 6500
