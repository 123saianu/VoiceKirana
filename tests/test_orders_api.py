
import pytest
from fastapi.testclient import TestClient

import backend.main as main


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


from backend.database import initialize_database
from backend.services.inventory_service import (
    create_shop,
    create_product,
    set_inventory,
)


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
