
import pytest
from fastapi.testclient import TestClient

import backend.main as main
from backend.database import get_connection, initialize_database
from backend.services.inventory_service import (
    create_shop,
    create_product,
    set_inventory,
)


TEST_API_TOKEN = "voicekirana-test-token"


@pytest.fixture
def client(monkeypatch):
    # Use a test-only token instead of the real secret.
    monkeypatch.setattr(
        main,
        "VOICEKIRANA_API_TOKEN",
        TEST_API_TOKEN
    )

    return TestClient(main.app)


def test_health_endpoint(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_complete_grocery_order(client):
    payload = {
        "intent": "place_order",
        "language": "te-IN",
        "items": [
            {
                "raw_name": "rice",
                "normalized_name": "rice",
                "quantity": 2,
                "unit": "kg"
            },
            {
                "raw_name": "sunflower oil",
                "normalized_name": "sunflower oil",
                "quantity": 1,
                "unit": "litre"
            }
        ]
    }

    response = client.post(
        "/orders/preview",
        json=payload,
        headers={"X-API-Key": TEST_API_TOKEN}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ready_for_inventory_check"
    assert response.json()["needs_clarification"] is False


def test_order_needing_clarification(client):
    payload = {
        "intent": "place_order",
        "language": "te-IN",
        "items": [
            {
                "raw_name": "organic jaggery",
                "normalized_name": "jaggery",
                "quantity": None,
                "unit": None,
                "needs_clarification": True,
                "clarification_reason": "Quantity was not specified."
            }
        ]
    }

    response = client.post(
        "/orders/preview",
        json=payload,
        headers={"X-API-Key": TEST_API_TOKEN}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "needs_clarification"
    assert response.json()["needs_clarification"] is True


def test_negative_quantity_is_rejected(client):
    payload = {
        "intent": "place_order",
        "language": "en",
        "items": [
            {
                "raw_name": "rice",
                "quantity": -2,
                "unit": "kg"
            }
        ]
    }

    response = client.post(
        "/orders/preview",
        json=payload,
        headers={"X-API-Key": TEST_API_TOKEN}
    )

    assert response.status_code == 422


def test_zero_quantity_is_rejected(client):
    payload = {
        "intent": "place_order",
        "language": "en",
        "items": [
            {
                "raw_name": "rice",
                "quantity": 0,
                "unit": "kg"
            }
        ]
    }

    response = client.post(
        "/orders/preview",
        json=payload,
        headers={"X-API-Key": TEST_API_TOKEN}
    )

    assert response.status_code == 422


def test_missing_api_key_is_rejected(client):
    payload = {
        "intent": "place_order",
        "language": "en",
        "items": [
            {
                "raw_name": "rice",
                "quantity": 2,
                "unit": "kg"
            }
        ]
    }

    response = client.post(
        "/orders/preview",
        json=payload
    )

    assert response.status_code == 401


def test_invalid_api_key_is_rejected(client):
    payload = {
        "intent": "place_order",
        "language": "en",
        "items": [
            {
                "raw_name": "rice",
                "quantity": 2,
                "unit": "kg"
            }
        ]
    }

    response = client.post(
        "/orders/preview",
        json=payload,
        headers={"X-API-Key": "incorrect-test-token"}
    )

    assert response.status_code == 401


def test_inventory_check_success(client):
    initialize_database()
    shop_id = create_shop("Inventory API Test Shop")
    product_id = create_product(
        name="Rice",
        base_unit="kg",
    )
    set_inventory(
        shop_id=shop_id,
        product_id=product_id,
        price_paise=6500,
        stock_quantity=20,
    )

    response = client.post(
        f"/inventory/check?shop_id={shop_id}",
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
        headers={"X-API-Key": TEST_API_TOKEN},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "inventory_checked"
    assert data["total_paise"] == 13000
    assert data["total_rupees"] == 130.0
    assert data["order_placed"] is False
    assert data["stock_reserved"] is False


def test_inventory_check_product_not_found(client):
    initialize_database()
    shop_id = create_shop("Missing Product Test Shop")

    response = client.post(
        f"/inventory/check?shop_id={shop_id}",
        json={
            "intent": "place_order",
            "items": [
                {
                    "raw_name": "dragon fruit",
                    "quantity": 1,
                    "unit": "piece",
                }
            ],
        },
        headers={"X-API-Key": TEST_API_TOKEN},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "needs_attention"
    assert data["items"][0]["status"] == "product_not_found"
    assert data["total_paise"] is None


def test_inventory_check_insufficient_stock(client):
    initialize_database()
    shop_id = create_shop("Stock Test Shop")
    product_id = create_product(
        name="Rice",
        base_unit="kg",
    )
    set_inventory(
        shop_id=shop_id,
        product_id=product_id,
        price_paise=6500,
        stock_quantity=1,
    )

    response = client.post(
        f"/inventory/check?shop_id={shop_id}",
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
        headers={"X-API-Key": TEST_API_TOKEN},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "needs_attention"
    assert data["items"][0]["status"] == "insufficient_stock"
    assert data["total_paise"] is None


def test_inventory_check_requires_authentication(client):
    response = client.post(
        "/inventory/check?shop_id=1",
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


def test_confirm_order_saves_order_and_items(client):
    initialize_database()

    shop_id = create_shop("Order Confirmation Test Shop")
    product_id = create_product(
        name="Rice",
        base_unit="kg",
    )
    set_inventory(
        shop_id=shop_id,
        product_id=product_id,
        price_paise=6500,
        stock_quantity=20,
    )

    response = client.post(
        f"/orders/confirm?shop_id={shop_id}",
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
        headers={"X-API-Key": TEST_API_TOKEN},
    )

    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "pending"
    assert data["order_reference"].startswith("VK-")
    assert data["total_paise"] == 13000
    assert data["stock_reserved"] is False
    assert len(data["items"]) == 1

    history_response = client.get(
        f"/orders/history?shop_id={shop_id}",
        headers={"X-API-Key": TEST_API_TOKEN},
    )

    assert history_response.status_code == 200

    history = history_response.json()
    assert history["count"] == 1
    assert history["orders"][0]["order_reference"] == data["order_reference"]
    assert history["orders"][0]["total_paise"] == 13000
    assert history["orders"][0]["items"][0]["product_name"] == "Rice"


def test_confirm_order_rejects_unmatched_product(client):
    initialize_database()

    shop_id = create_shop("Invalid Order Test Shop")

    with get_connection() as connection:
        before = connection.execute(
            "SELECT COUNT(*) AS total FROM orders"
        ).fetchone()["total"]

    response = client.post(
        f"/orders/confirm?shop_id={shop_id}",
        json={
            "intent": "place_order",
            "items": [
                {
                    "raw_name": "unlisted mystery product",
                    "quantity": 1,
                    "unit": "piece",
                }
            ],
        },
        headers={"X-API-Key": TEST_API_TOKEN},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["inventory_result"]["status"] == (
        "needs_attention"
    )

    with get_connection() as connection:
        after = connection.execute(
            "SELECT COUNT(*) AS total FROM orders"
        ).fetchone()["total"]

    assert after == before


def test_order_history_requires_authentication(client):
    response = client.get("/orders/history?shop_id=1")

    assert response.status_code == 401


def _create_pending_rice_order(client, shop_name="Day 8 Order Test Shop"):
    """Create an isolated active shop and one pending rice order."""
    initialize_database()
    shop_id = create_shop(shop_name)
    product_id = create_product(name="Rice", base_unit="kg")
    set_inventory(
        shop_id=shop_id,
        product_id=product_id,
        price_paise=6500,
        stock_quantity=20,
    )

    response = client.post(
        f"/orders/confirm?shop_id={shop_id}",
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
        headers={"X-API-Key": TEST_API_TOKEN},
    )
    assert response.status_code == 200
    return shop_id, response.json()


def test_get_order_by_reference_returns_saved_order(client):
    shop_id, saved = _create_pending_rice_order(client, "Day 8 Lookup Shop")

    response = client.get(
        f"/orders/{saved['order_reference']}?shop_id={shop_id}",
        headers={"X-API-Key": TEST_API_TOKEN},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["order_reference"] == saved["order_reference"]
    assert data["status"] == "pending"
    assert data["total_paise"] == 13000
    assert data["items"][0]["product_name"] == "Rice"
    assert data["items"][0]["quantity"] == 2


def test_get_order_by_unknown_reference_returns_404(client):
    initialize_database()
    shop_id = create_shop("Day 8 Unknown Order Shop")

    response = client.get(
        f"/orders/VK-DOESNOTEXIST?shop_id={shop_id}",
        headers={"X-API-Key": TEST_API_TOKEN},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Order not found."


def test_order_lookup_is_scoped_to_shop(client):
    shop_id, saved = _create_pending_rice_order(client, "Day 8 Private Shop")
    other_shop_id = create_shop("Day 8 Other Shop")

    response = client.get(
        f"/orders/{saved['order_reference']}?shop_id={other_shop_id}",
        headers={"X-API-Key": TEST_API_TOKEN},
    )

    assert response.status_code == 404


def test_cancel_pending_order_persists_cancelled_status(client):
    shop_id, saved = _create_pending_rice_order(client, "Day 8 Cancellation Shop")

    response = client.post(
        f"/orders/{saved['order_reference']}/cancel?shop_id={shop_id}",
        headers={"X-API-Key": TEST_API_TOKEN},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"

    lookup = client.get(
        f"/orders/{saved['order_reference']}?shop_id={shop_id}",
        headers={"X-API-Key": TEST_API_TOKEN},
    )
    assert lookup.status_code == 200
    assert lookup.json()["status"] == "cancelled"

    history = client.get(
        f"/orders/history?shop_id={shop_id}",
        headers={"X-API-Key": TEST_API_TOKEN},
    )
    assert history.status_code == 200
    assert history.json()["orders"][0]["status"] == "cancelled"


def test_cancelled_order_cannot_be_cancelled_again(client):
    shop_id, saved = _create_pending_rice_order(client, "Day 8 Repeat Cancel Shop")
    url = f"/orders/{saved['order_reference']}/cancel?shop_id={shop_id}"
    headers = {"X-API-Key": TEST_API_TOKEN}

    first = client.post(url, headers=headers)
    second = client.post(url, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 409
    assert "Only pending orders" in second.json()["detail"]


def test_order_lookup_requires_authentication(client):
    response = client.get("/orders/VK-ANYREFERENCE?shop_id=1")
    assert response.status_code == 401


def test_order_cancellation_requires_authentication(client):
    response = client.post(
        "/orders/VK-ANYREFERENCE/cancel?shop_id=1"
    )
    assert response.status_code == 401
