
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

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
    """Create a direct structured grocery-order JSON payload."""

    return {
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

def get_order_count():
    """Count saved orders in the isolated test database."""

    from contextlib import closing

    with closing(database.get_connection()) as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS total FROM orders"
        ).fetchone()

    return row["total"]


def test_direct_confirmation_successful_order(tmp_path, monkeypatch):
    """A valid direct JSON order should create a pending order."""

    client, shop_id = setup_test_environment(tmp_path, monkeypatch)

    response = client.post(
        f"/orders/confirm?shop_id={shop_id}",
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


def test_direct_confirmation_rejects_malformed_json(
    tmp_path, monkeypatch
):
    """Malformed request JSON must return HTTP 422."""

    client, shop_id = setup_test_environment(tmp_path, monkeypatch)

    response = client.post(
        f"/orders/confirm?shop_id={shop_id}",
        headers={
            "X-API-Key": TEST_TOKEN,
            "Content-Type": "application/json",
        },
        content="{invalid-json",
    )

    assert response.status_code == 422
    assert get_order_count() == 0



def test_direct_confirmation_rejects_invalid_order_model(
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
        f"/orders/confirm?shop_id={shop_id}",
        headers={"X-API-Key": TEST_TOKEN},
        json=invalid_order,
    )

    assert response.status_code == 422
    assert get_order_count() == 0


def test_direct_confirmation_rejects_missing_api_key(
    tmp_path, monkeypatch
):
    """Requests without authentication must be rejected."""

    client, shop_id = setup_test_environment(tmp_path, monkeypatch)

    response = client.post(
        f"/orders/confirm?shop_id={shop_id}",
        json=make_order_payload(),
    )

    assert response.status_code == 401
    assert get_order_count() == 0


def test_direct_confirmation_rejects_incorrect_api_key(
    tmp_path, monkeypatch
):
    """Requests with an incorrect API key must be rejected."""

    client, shop_id = setup_test_environment(tmp_path, monkeypatch)

    response = client.post(
        f"/orders/confirm?shop_id={shop_id}",
        headers={"X-API-Key": "incorrect-test-token"},
        json=make_order_payload(),
    )

    assert response.status_code == 401
    assert get_order_count() == 0


def test_direct_confirmation_rejects_insufficient_stock(
    tmp_path, monkeypatch
):
    """Insufficient inventory must prevent order creation."""

    client, shop_id = setup_test_environment(
        tmp_path,
        monkeypatch,
        stock_quantity=0,
    )

    response = client.post(
        f"/orders/confirm?shop_id={shop_id}",
        headers={"X-API-Key": TEST_TOKEN},
        json=make_order_payload(quantity=1),
    )

    assert response.status_code == 409
    assert get_order_count() == 0


def get_idempotency_count():
    """Count idempotency records in the isolated test database."""
    from contextlib import closing

    with closing(database.get_connection()) as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS total FROM order_idempotency"
        ).fetchone()

    return row["total"]


def test_same_idempotency_key_returns_original_order(
    tmp_path, monkeypatch
):
    """Retrying the same confirmation must not create another order."""

    client, shop_id = setup_test_environment(tmp_path, monkeypatch)

    headers = {
        "X-API-Key": TEST_TOKEN,
        "Idempotency-Key": "retry-test-001",
    }

    first = client.post(
        f"/orders/confirm?shop_id={shop_id}",
        headers=headers,
        json=make_order_payload(),
    )

    second = client.post(
        f"/orders/confirm?shop_id={shop_id}",
        headers=headers,
        json=make_order_payload(),
    )

    assert first.status_code == 200
    assert second.status_code == 200

    assert first.json() == second.json()
    assert first.json()["order_reference"] == second.json()["order_reference"]

    assert get_order_count() == 1
    assert get_idempotency_count() == 1


def test_same_idempotency_key_rejects_different_order(
    tmp_path, monkeypatch
):
    """A key cannot be reused with different order contents."""

    client, shop_id = setup_test_environment(tmp_path, monkeypatch)

    headers = {
        "X-API-Key": TEST_TOKEN,
        "Idempotency-Key": "retry-test-002",
    }

    first = client.post(
        f"/orders/confirm?shop_id={shop_id}",
        headers=headers,
        json=make_order_payload(quantity=1),
    )

    second = client.post(
        f"/orders/confirm?shop_id={shop_id}",
        headers=headers,
        json=make_order_payload(quantity=2),
    )

    assert first.status_code == 200
    assert second.status_code == 409

    assert get_order_count() == 1
    assert get_idempotency_count() == 1


def test_new_idempotency_key_allows_legitimate_repeat_order(
    tmp_path, monkeypatch
):
    """A new confirmation key permits an identical legitimate purchase."""

    client, shop_id = setup_test_environment(tmp_path, monkeypatch)

    first_headers = {
        "X-API-Key": TEST_TOKEN,
        "Idempotency-Key": "new-purchase-001",
    }

    second_headers = {
        "X-API-Key": TEST_TOKEN,
        "Idempotency-Key": "new-purchase-002",
    }

    first = client.post(
        f"/orders/confirm?shop_id={shop_id}",
        headers=first_headers,
        json=make_order_payload(),
    )

    second = client.post(
        f"/orders/confirm?shop_id={shop_id}",
        headers=second_headers,
        json=make_order_payload(),
    )

    assert first.status_code == 200
    assert second.status_code == 200

    assert first.json()["order_reference"] != second.json()["order_reference"]

    assert get_order_count() == 2
    assert get_idempotency_count() == 2


def test_concurrent_same_key_creates_only_one_order(
    tmp_path, monkeypatch
):
    """
    Two simultaneous requests with the same idempotency key
    must create only one database order.
    """

    client, shop_id = setup_test_environment(
        tmp_path,
        monkeypatch,
    )

    headers = {
        "X-API-Key": TEST_TOKEN,
        "Idempotency-Key": "concurrent-confirmation-001",
    }

    payload = make_order_payload()

    barrier = Barrier(2)

    def submit_confirmation():
        # Ensure both requests begin together.
        barrier.wait(timeout=10)

        # Each thread gets its own TestClient.
        with TestClient(main.app) as test_client:
            return test_client.post(
                f"/orders/confirm?shop_id={shop_id}",
                headers=headers,
                json=payload,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(submit_confirmation),
            executor.submit(submit_confirmation),
        ]

        responses = [
            future.result(timeout=30)
            for future in futures
        ]

    # Both requests should receive successful responses.
    assert all(
        response.status_code == 200
        for response in responses
    )

    first_result = responses[0].json()
    second_result = responses[1].json()

    # Both callers must receive exactly the same order.
    assert first_result == second_result

    assert (
        first_result["order_reference"]
        == second_result["order_reference"]
    )

    # Only one order and one idempotency record may exist.
    assert get_order_count() == 1
    assert get_idempotency_count() == 1


def test_missing_idempotency_key_preserves_legacy_behavior(
    tmp_path, monkeypatch
):
    """Requests without a key should retain backward compatibility."""

    client, shop_id = setup_test_environment(tmp_path, monkeypatch)

    response = client.post(
        f"/orders/confirm?shop_id={shop_id}",
        headers={"X-API-Key": TEST_TOKEN},
        json=make_order_payload(),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "pending"

    assert get_order_count() == 1
    assert get_idempotency_count() == 0


def test_empty_idempotency_key_is_rejected(
    tmp_path, monkeypatch
):
    """An empty confirmation key must be rejected."""

    client, shop_id = setup_test_environment(tmp_path, monkeypatch)

    headers = {
        "X-API-Key": TEST_TOKEN,
        "Idempotency-Key": "",
    }

    response = client.post(
        f"/orders/confirm?shop_id={shop_id}",
        headers=headers,
        json=make_order_payload(),
    )

    assert response.status_code == 400
    assert get_order_count() == 0
    assert get_idempotency_count() == 0


def test_oversized_idempotency_key_is_rejected(
    tmp_path, monkeypatch
):
    """Keys exceeding 200 characters must be rejected."""

    client, shop_id = setup_test_environment(tmp_path, monkeypatch)

    headers = {
        "X-API-Key": TEST_TOKEN,
        "Idempotency-Key": "K" * 201,
    }

    response = client.post(
        f"/orders/confirm?shop_id={shop_id}",
        headers=headers,
        json=make_order_payload(),
    )

    assert response.status_code == 400
    assert get_order_count() == 0
    assert get_idempotency_count() == 0


def test_failed_order_insert_rolls_back_everything(
    tmp_path, monkeypatch
):
    """
    Simulate a database failure during order-item insertion.

    Both the order and idempotency record must roll back.
    """

    client, shop_id = setup_test_environment(tmp_path, monkeypatch)

    # Simulate a database failure when inserting an order item.
    with database.get_connection() as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_test_order_item_insert
            BEFORE INSERT ON order_items
            BEGIN
                SELECT RAISE(ABORT, 'forced test failure');
            END;
            """
        )

    headers = {
        "X-API-Key": TEST_TOKEN,
        "Idempotency-Key": "rollback-test-001",
    }

    # TestClient propagates the simulated database exception.
    with pytest.raises(sqlite3.IntegrityError):
        client.post(
            f"/orders/confirm?shop_id={shop_id}",
            headers=headers,
            json=make_order_payload(),
        )

    # Neither the order nor its idempotency record may remain.
    assert get_order_count() == 0
    assert get_idempotency_count() == 0
