import pytest
from fastapi.testclient import TestClient

import backend.main as main
import backend.database as database
from backend.database import get_connection, initialize_database
from backend.services.inventory_service import (
    add_product_alias,
    create_shop,
    create_product,
    set_inventory,
)

TEST_API_TOKEN = "voicekirana-test-token"


@pytest.fixture(autouse=True)
def isolate_inventory_api_database(monkeypatch, tmp_path):
    """Use a fresh SQLite database for every test in this module."""
    test_db_path = tmp_path / "voicekirana_test.db"

    monkeypatch.setattr(database, "DATABASE_URL", "")
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "DATABASE_PATH", test_db_path)
    monkeypatch.setattr(main, "VOICEKIRANA_API_TOKEN", TEST_API_TOKEN)

    initialize_database()


@pytest.fixture
def client():
    """Create a test client using the isolated test database."""
    return TestClient(main.app)


@pytest.fixture
def stocked_shop():
    shop_id = create_shop("Inventory API Test Shop")
    rice_id = create_product("Rice", "kg")

    set_inventory(
        shop_id=shop_id,
        product_id=rice_id,
        price_paise=6500,
        stock_quantity=20,
    )

    return shop_id


def test_inventory_check_returns_price_and_total(client, stocked_shop):
    response = client.post(
        f"/inventory/check?shop_id={stocked_shop}",
        headers={"X-API-Key": TEST_API_TOKEN},
        json={
            "intent": "place_order",
            "items": [
                {
                    "raw_name": "rice",
                    "normalized_name": "rice",
                    "quantity": 2,
                    "unit": "kg",
                }
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "inventory_checked"
    assert data["total_paise"] == 13000
    assert data["total_rupees"] == 130.0
    assert data["order_placed"] is False
    assert data["stock_reserved"] is False


def test_bulk_inventory_comparison_returns_full_and_partial_matches(
    client,
    stocked_shop,
):
    """One request compares multiple visible shops without per-shop calls."""

    sunflower_id = create_product(
        name="Sunflower Oil",
        base_unit="litre",
    )
    set_inventory(
        shop_id=stocked_shop,
        product_id=sunflower_id,
        price_paise=15000,
        stock_quantity=10,
    )

    rice_only_shop = create_shop("Rice Only Bulk Comparison Shop")
    rice_only_id = create_product(
        name="Rice",
        base_unit="kg",
    )
    set_inventory(
        shop_id=rice_only_shop,
        product_id=rice_only_id,
        price_paise=7000,
        stock_quantity=10,
    )

    response = client.post(
        "/inventory/compare",
        headers={"X-API-Key": TEST_API_TOKEN},
        json={
            "intent": "place_order",
            "items": [
                {
                    "raw_name": "rice",
                    "normalized_name": "rice",
                    "quantity": 2,
                    "unit": "kg",
                },
                {
                    "raw_name": "sunflower oil",
                    "normalized_name": "sunflower oil",
                    "quantity": 1,
                    "unit": "litre",
                },
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()

    assert body["shop_count"] == 2
    assert body["checked_count"] == 2
    assert body["failed"] == []

    assert len(body["full_matches"]) == 1
    full = body["full_matches"][0]
    assert full["shop_id"] == stocked_shop
    assert full["matched_count"] == 2
    assert full["total_rupees"] == 280.0
    assert all(item["status"] == "matched" for item in full["items"])

    assert len(body["partial_matches"]) == 1
    partial = body["partial_matches"][0]
    assert partial["shop_id"] == rice_only_shop
    assert partial["matched_count"] == 1
    assert partial["items"][1]["status"] == "product_not_found"


def test_bulk_inventory_comparison_excludes_hidden_shops(
    client,
    stocked_shop,
):
    with database.get_connection() as connection:
        connection.execute(
            "UPDATE shops SET is_customer_visible = 0 WHERE id = ?",
            (stocked_shop,),
        )

    response = client.post(
        "/inventory/compare",
        headers={"X-API-Key": TEST_API_TOKEN},
        json={
            "intent": "place_order",
            "items": [
                {
                    "raw_name": "rice",
                    "normalized_name": "rice",
                    "quantity": 1,
                    "unit": "kg",
                }
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["shop_count"] == 0
    assert body["checked_count"] == 0
    assert body["full_matches"] == []
    assert body["partial_matches"] == []


def test_bulk_inventory_comparison_requires_api_key(client):
    response = client.post(
        "/inventory/compare",
        json={
            "intent": "place_order",
            "items": [
                {
                    "raw_name": "rice",
                    "quantity": 1,
                    "unit": "kg",
                }
            ],
        },
    )

    assert response.status_code == 401


def test_public_catalogue_groups_same_product_and_keeps_shop_offers(
    client,
    stocked_shop,
):
    """Identical product identities share one group, not one shared price."""

    second_shop = create_shop("Second Public Rice Shop")
    second_rice_id = create_product("Rice", "kg")
    set_inventory(
        shop_id=second_shop,
        product_id=second_rice_id,
        price_paise=7000,
        stock_quantity=10,
    )

    unavailable_shop = create_shop("Unavailable Rice Shop")
    unavailable_rice_id = create_product("rice", "KG")
    set_inventory(
        shop_id=unavailable_shop,
        product_id=unavailable_rice_id,
        price_paise=6000,
        stock_quantity=5,
        is_available=False,
    )

    response = client.get("/catalogue/all")

    assert response.status_code == 200
    body = response.json()

    rice_groups = [
        product
        for product in body["products"]
        if product["normalized_name"] == "rice"
        and product["brand"] is None
        and product["variant"] is None
        and product["base_unit"].casefold() == "kg"
    ]

    assert len(rice_groups) == 1
    rice = rice_groups[0]

    assert rice["offer_count"] == 3
    assert {
        offer["shop_id"] for offer in rice["offers"]
    } == {stocked_shop, second_shop, unavailable_shop}

    offers_by_shop = {
        offer["shop_id"]: offer
        for offer in rice["offers"]
    }

    assert offers_by_shop[stocked_shop]["price_rupees"] == 65.0
    assert offers_by_shop[second_shop]["price_rupees"] == 70.0
    assert offers_by_shop[unavailable_shop]["is_available"] is False

    assert body["shop_count"] == 3


def test_public_catalogue_keeps_brand_variant_and_unit_distinct(
    client,
    stocked_shop,
):
    brand_rice_id = create_product(
        "Rice",
        "kg",
        brand="Brand A",
    )
    set_inventory(
        shop_id=stocked_shop,
        product_id=brand_rice_id,
        price_paise=8000,
        stock_quantity=10,
    )

    variant_rice_id = create_product(
        "Rice",
        "kg",
        variant="Sona Masoori",
    )
    set_inventory(
        shop_id=stocked_shop,
        product_id=variant_rice_id,
        price_paise=8500,
        stock_quantity=10,
    )

    litre_rice_id = create_product("Rice", "litre")
    set_inventory(
        shop_id=stocked_shop,
        product_id=litre_rice_id,
        price_paise=9000,
        stock_quantity=10,
    )

    response = client.get("/catalogue/all")

    assert response.status_code == 200

    rice_groups = [
        product
        for product in response.json()["products"]
        if product["normalized_name"] == "rice"
    ]

    assert len(rice_groups) == 4

    identities = {
        (
            product["brand"],
            product["variant"],
            product["base_unit"],
        )
        for product in rice_groups
    }

    assert identities == {
        (None, None, "kg"),
        ("Brand A", None, "kg"),
        (None, "Sona Masoori", "kg"),
        (None, None, "litre"),
    }


def test_public_catalogue_excludes_hidden_and_inactive_shops(
    client,
    stocked_shop,
):
    hidden_shop = create_shop("Hidden Catalogue Shop")
    hidden_product_id = create_product("Hidden Wheat", "kg")
    set_inventory(
        shop_id=hidden_shop,
        product_id=hidden_product_id,
        price_paise=5000,
        stock_quantity=10,
    )

    inactive_shop = create_shop("Inactive Catalogue Shop")
    inactive_product_id = create_product("Inactive Sugar", "kg")
    set_inventory(
        shop_id=inactive_shop,
        product_id=inactive_product_id,
        price_paise=4500,
        stock_quantity=10,
    )

    with database.get_connection() as connection:
        connection.execute(
            "UPDATE shops SET is_customer_visible = 0 WHERE id = ?",
            (hidden_shop,),
        )
        connection.execute(
            "UPDATE shops SET is_active = 0 WHERE id = ?",
            (inactive_shop,),
        )

    response = client.get("/catalogue/all")

    assert response.status_code == 200
    body = response.json()

    names = {
        product["normalized_name"]
        for product in body["products"]
    }

    assert "rice" in names
    assert "hidden wheat" not in names
    assert "inactive sugar" not in names
    assert body["shop_count"] == 1


def test_public_catalogue_returns_empty_when_no_shops_are_visible(
    client,
    stocked_shop,
):
    with database.get_connection() as connection:
        connection.execute(
            "UPDATE shops SET is_customer_visible = 0 WHERE id = ?",
            (stocked_shop,),
        )

    response = client.get("/catalogue/all")

    assert response.status_code == 200
    assert response.json() == {
        "count": 0,
        "shop_count": 0,
        "products": [],
    }


def test_inventory_check_rejects_missing_api_key(client, stocked_shop):
    response = client.post(
        f"/inventory/check?shop_id={stocked_shop}",
        json={
            "intent": "place_order",
            "items": [
                {
                    "raw_name": "rice",
                    "quantity": 1,
                    "unit": "kg",
                }
            ],
        },
    )

    assert response.status_code == 401


def test_inventory_check_reports_missing_product(client, stocked_shop):
    response = client.post(
        f"/inventory/check?shop_id={stocked_shop}",
        headers={"X-API-Key": TEST_API_TOKEN},
        json={
            "intent": "place_order",
            "items": [
                {
                    "raw_name": "saffron",
                    "quantity": 1,
                    "unit": "kg",
                }
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "needs_attention"
    assert data["items"][0]["status"] == "product_not_found"
    assert data["total_paise"] is None


def test_inventory_check_reports_insufficient_stock(client, stocked_shop):
    response = client.post(
        f"/inventory/check?shop_id={stocked_shop}",
        headers={"X-API-Key": TEST_API_TOKEN},
        json={
            "intent": "place_order",
            "items": [
                {
                    "raw_name": "rice",
                    "quantity": 25,
                    "unit": "kg",
                }
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "needs_attention"
    assert data["items"][0]["status"] == "insufficient_stock"
    assert data["total_paise"] is None


def test_inventory_check_stops_for_clarification(client, stocked_shop):
    response = client.post(
        f"/inventory/check?shop_id={stocked_shop}",
        headers={"X-API-Key": TEST_API_TOKEN},
        json={
            "intent": "place_order",
            "items": [
                {
                    "raw_name": "jaggery",
                    "needs_clarification": True,
                    "clarification_reason": "Please specify the quantity.",
                }
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "needs_clarification"
    assert data["needs_clarification"] is True


def test_inventory_check_resolves_product_alias(client, stocked_shop):
    with get_connection() as connection:
        product = connection.execute(
            """
            SELECT p.id
            FROM products p
            JOIN inventory i ON i.product_id = p.id
            WHERE i.shop_id = ?
              AND p.normalized_name = 'rice'
            """,
            (stocked_shop,),
        ).fetchone()

    assert product is not None, "Rice fixture was not created."

    product_id = product["id"]
    alias = "biyyam"

    try:
        add_product_alias(product_id, alias)

        response = client.post(
            f"/inventory/check?shop_id={stocked_shop}",
            headers={"X-API-Key": TEST_API_TOKEN},
            json={
                "intent": "place_order",
                "items": [
                    {
                        "raw_name": alias,
                        "normalized_name": alias,
                        "quantity": 2,
                        "unit": "kg",
                    }
                ],
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "inventory_checked"
        assert data["items"][0]["status"] == "matched"
        assert data["items"][0]["product_name"] == "Rice"
        assert data["total_paise"] == 13000

    finally:
        with get_connection() as connection:
            connection.execute(
                """
                DELETE FROM product_aliases
                WHERE product_id = ? AND normalized_alias = ?
                """,
                (product_id, alias),
            )


def test_inventory_check_rejects_nonexistent_shop(client):
    response = client.post(
        "/inventory/check?shop_id=999999",
        json={
            "intent": "place_order",
            "items": [
                {
                    "raw_name": "rice",
                    "quantity": 1,
                    "unit": "kg",
                }
            ],
        },
        headers={"X-API-Key": TEST_API_TOKEN},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Active shop not found."


def test_inventory_check_rejects_empty_order(client):
    response = client.post(
        "/inventory/check?shop_id=1",
        json={
            "intent": "place_order",
            "items": [],
        },
        headers={"X-API-Key": TEST_API_TOKEN},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "The grocery order must contain at least one item."
    )


def test_inventory_check_rejects_zero_shop_id(client):
    response = client.post(
        "/inventory/check?shop_id=0",
        json={
            "intent": "place_order",
            "items": [
                {
                    "raw_name": "rice",
                    "quantity": 1,
                    "unit": "kg",
                }
            ],
        },
        headers={"X-API-Key": TEST_API_TOKEN},
    )

    assert response.status_code == 422


def test_inventory_check_reports_unit_mismatch(client):
    initialize_database()
    shop_id = create_shop("Unit Mismatch Test Shop")
    product_id = create_product(name="Rice", base_unit="kg")
    set_inventory(
        shop_id=shop_id,
        product_id=product_id,
        price_paise=6500,
        stock_quantity=20,
    )

    response = client.post(
        f"/inventory/check?shop_id={shop_id}",
        headers={"X-API-Key": TEST_API_TOKEN},
        json={
            "intent": "place_order",
            "items": [
                {
                    "raw_name": "rice",
                    "quantity": 2,
                    "unit": "litre",
                }
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "needs_attention"
    assert data["items"][0]["status"] == "unit_mismatch"
    assert data["total_paise"] is None


def test_inventory_check_reports_unavailable_product(client):
    initialize_database()
    shop_id = create_shop("Unavailable Product Test Shop")
    product_id = create_product(name="Rice", base_unit="kg")
    set_inventory(
        shop_id=shop_id,
        product_id=product_id,
        price_paise=6500,
        stock_quantity=20,
        is_available=False,
    )

    response = client.post(
        f"/inventory/check?shop_id={shop_id}",
        headers={"X-API-Key": TEST_API_TOKEN},
        json={
            "intent": "place_order",
            "items": [
                {
                    "raw_name": "rice",
                    "quantity": 2,
                    "unit": "kg",
                }
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "needs_attention"
    assert data["items"][0]["status"] == "unavailable"
    assert data["total_paise"] is None


def test_inventory_check_rejects_brand_mismatch(client):
    initialize_database()
    shop_id = create_shop("Brand Mismatch Test Shop")
    product_id = create_product(
        name="Rice",
        base_unit="kg",
        brand="Brand A",
    )
    set_inventory(
        shop_id=shop_id,
        product_id=product_id,
        price_paise=6500,
        stock_quantity=20,
    )

    response = client.post(
        f"/inventory/check?shop_id={shop_id}",
        headers={"X-API-Key": TEST_API_TOKEN},
        json={
            "intent": "place_order",
            "items": [
                {
                    "raw_name": "rice",
                    "quantity": 2,
                    "unit": "kg",
                    "brand": "Brand B",
                }
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "needs_attention"
    assert data["items"][0]["status"] == "product_not_found"
    assert data["total_paise"] is None


def test_inventory_check_reports_ambiguous_match(client):
    initialize_database()
    shop_id = create_shop("Ambiguous Product Test Shop")

    product_a = create_product(
        name="Rice",
        base_unit="kg",
        brand="Brand A",
    )
    product_b = create_product(
        name="Rice",
        base_unit="kg",
        brand="Brand B",
    )

    set_inventory(
        shop_id=shop_id,
        product_id=product_a,
        price_paise=6500,
        stock_quantity=20,
    )
    set_inventory(
        shop_id=shop_id,
        product_id=product_b,
        price_paise=7000,
        stock_quantity=15,
    )

    response = client.post(
        f"/inventory/check?shop_id={shop_id}",
        headers={"X-API-Key": TEST_API_TOKEN},
        json={
            "intent": "place_order",
            "items": [
                {
                    "raw_name": "rice",
                    "normalized_name": "rice",
                    "quantity": 2,
                    "unit": "kg",
                }
            ],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "needs_attention"
    assert data["items"][0]["status"] == "ambiguous_match"
    assert len(data["items"][0]["candidates"]) == 2
    assert data["total_paise"] is None
