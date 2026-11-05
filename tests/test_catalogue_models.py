import pytest
from pydantic import ValidationError

from backend.models.catalogue import (
    ShopProductCreate,
    InventoryUpdate,
    ProductAliasCreate,
)


def test_product_registration_model():
    product = ShopProductCreate(
        name="  Rice  ",
        base_unit=" KG ",
        brand=" Brand A ",
        price_paise=6500,
        stock_quantity=20,
        aliases=[" Biyyam ", "బియ్యం", "biyyam"],
    )

    assert product.name == "Rice"
    assert product.base_unit == "kg"
    assert product.brand == "Brand A"
    assert product.aliases == ["Biyyam", "బియ్యం"]

    print("PASS: Product registration model")


def test_product_rejects_negative_price_and_stock():
    with pytest.raises(ValidationError):
        ShopProductCreate(
            name="Rice",
            base_unit="kg",
            price_paise=-100,
            stock_quantity=10,
        )

    with pytest.raises(ValidationError):
        ShopProductCreate(
            name="Rice",
            base_unit="kg",
            price_paise=6500,
            stock_quantity=-1,
        )

    print("PASS: Negative price and stock rejected")


def test_inventory_update_requires_at_least_one_field():
    with pytest.raises(ValidationError):
        InventoryUpdate()

    update = InventoryUpdate(is_available=False)

    assert update.is_available is False

    print("PASS: Partial inventory updates validated")


def test_product_alias_model():
    alias = ProductAliasCreate(alias="  బియ్యం  ")

    assert alias.alias == "బియ్యం"

    with pytest.raises(ValidationError):
        ProductAliasCreate(alias="   ")

    print("PASS: Product alias validation")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
