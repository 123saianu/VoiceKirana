
import json

import backend.database as database
import backend.main as main

from fastapi.testclient import TestClient
from backend.database import initialize_database
from backend.services.inventory_service import (
    create_product,
    create_shop,
    set_inventory,
)


TEST_TOKEN = "test-token-for-order-confirmation"


def setup_test_environment(tmp_path, monkeypatch, stock_quantity=20):
    """Create a completely isolated test database and API client."""

    monkeypatch.setattr(database, "DATABASE_URL", "")
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(
        database,
        "DATABASE_PATH",
        tmp_path / "voicekirana_order_test.db",
    )

    initialize_database()

    monkeypatch.setattr(
        main,
        "VOICEKIRANA_API_TOKEN",
        TEST_TOKEN,
    )

    shop_id = create_shop("Order Confirmation Test Shop")
    product_id = create_product("Rice", "kg")

    set_inventory(
        shop_id=shop_id,
        product_id=product_id,
        price_paise=6800,
        stock_quantity=stock_quantity,
    )

    client = TestClient(main.app)

    return client, shop_id


def make_order_payload(raw_name="బియ్యం", quantity=1):
    """Create the same JSON-string wrapper used by Gnani."""

    order = {
        "intent": "place_order",
        "language": "te-IN",
        "items": [
            {
                "raw_name": raw_name,
                "normalized_name": "rice",
                "quantity": quantity,
                "unit": "kg",
                "brand": None,
                "variant": None,
                "needs_clarification": False,
            }
        ],
        "needs_clarification": False,
    }

    return {
        "order_request": json.dumps(
            order,
            ensure_ascii=False,
        )
    }


def get_order_count():
    """Count saved orders in the isolated test database."""

    from contextlib import closing

    with closing(database.get_connection()) as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS total FROM orders"
        ).fetchone()

    return row["total"]


def test_gnani_adapter_successful_order(tmp_path, monkeypatch):
    """A valid Gnani wrapper should create a pending order."""

    client, shop_id = setup_test_environment(tmp_path, monkeypatch)

    response = client.post(
        f"/orders/confirm/gnani?shop_id={shop_id}",
        headers={"X-API-Key": TEST_TOKEN},
        json=make_order_payload(),
    )

    assert response.status_code == 200

    result = response.json()

    assert result["status"] == "pending"
    assert result["shop_id"] == shop_id
    assert result["total_paise"] == 6800
    assert result["total_rupees"] == 68.0
    assert result["stock_reserved"] is False

    assert result["order_reference"].startswith("VK-")
    assert result["items"][0]["product_name"] == "Rice"
    assert result["items"][0]["quantity"] == 1
    assert get_order_count() == 1


def test_gnani_adapter_rejects_invalid_json(tmp_path, monkeypatch):
    """Invalid JSON inside order_request must return HTTP 422."""

    client, shop_id = setup_test_environment(tmp_path, monkeypatch)

    response = client.post(
        f"/orders/confirm/gnani?shop_id={shop_id}",
        headers={"X-API-Key": TEST_TOKEN},
        json={"order_request": "{invalid-json"},
    )

    assert response.status_code == 422
    assert get_order_count() == 0


def test_gnani_adapter_rejects_missing_order_request(
    tmp_path, monkeypatch
):
    """The wrapper must contain an order_request string."""

    client, shop_id = setup_test_environment(tmp_path, monkeypatch)

    response = client.post(
        f"/orders/confirm/gnani?shop_id={shop_id}",
        headers={"X-API-Key": TEST_TOKEN},
        json={"other_field": "test"},
    )

    assert response.status_code == 422
    assert get_order_count() == 0


def test_gnani_adapter_rejects_invalid_order_model(
    tmp_path, monkeypatch
):
    """An order without a required product name must be rejected."""

    client, shop_id = setup_test_environment(tmp_path, monkeypatch)

    invalid_order = {
        "intent": "place_order",
        "language": "te-IN",
        "items": [
            {
                "quantity": 1,
                "unit": "kg",
            }
        ],
        "needs_clarification": False,
    }

    response = client.post(
        f"/orders/confirm/gnani?shop_id={shop_id}",
        headers={"X-API-Key": TEST_TOKEN},
        json={
            "order_request": json.dumps(invalid_order)
        },
    )

    assert response.status_code == 422
    assert get_order_count() == 0


def test_gnani_adapter_rejects_missing_api_key(
    tmp_path, monkeypatch
):
    """Requests without authentication must be rejected."""

    client, shop_id = setup_test_environment(tmp_path, monkeypatch)

    response = client.post(
        f"/orders/confirm/gnani?shop_id={shop_id}",
        json=make_order_payload(),
    )

    assert response.status_code == 401
    assert get_order_count() == 0


def test_gnani_adapter_rejects_incorrect_api_key(
    tmp_path, monkeypatch
):
    """Requests with an incorrect API key must be rejected."""

    client, shop_id = setup_test_environment(tmp_path, monkeypatch)

    response = client.post(
        f"/orders/confirm/gnani?shop_id={shop_id}",
        headers={"X-API-Key": "incorrect-test-token"},
        json=make_order_payload(),
    )

    assert response.status_code == 401
    assert get_order_count() == 0


def test_gnani_adapter_rejects_insufficient_stock(
    tmp_path, monkeypatch
):
    """Insufficient inventory must prevent order creation."""

    client, shop_id = setup_test_environment(
        tmp_path,
        monkeypatch,
        stock_quantity=0,
    )

    response = client.post(
        f"/orders/confirm/gnani?shop_id={shop_id}",
        headers={"X-API-Key": TEST_TOKEN},
        json=make_order_payload(quantity=1),
    )

    assert response.status_code == 409
    assert get_order_count() == 0
