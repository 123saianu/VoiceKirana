
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from backend import database
from backend import main as main_module
from backend.main import app
from backend.services.inventory_service import (
    create_shop,
    create_product,
    set_inventory,
)


JWT_SECRET = "VoiceKirana-Customer-Orders-Test-Secret-123456"


@pytest.fixture(autouse=True)
def isolate_database(tmp_path, monkeypatch):
    """Use a separate SQLite database for every test."""

    monkeypatch.setattr(database, "DATABASE_URL", "")
    monkeypatch.setattr(
        database,
        "DATABASE_PATH",
        tmp_path / "customer_orders_test.db",
    )
    monkeypatch.setenv("JWT_SECRET_KEY", JWT_SECRET)

    database.initialize_database()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def register_and_login(client, email):
    registration = client.post(
        "/customers/register",
        json={
            "full_name": "Test Customer",
            "email": email,
            "password": "Customer-Test-Password-123!",
        },
    )

    assert registration.status_code == 201

    login = client.post(
        "/customers/login",
        json={
            "email": email,
            "password": "Customer-Test-Password-123!",
        },
    )

    assert login.status_code == 200

    return {
        "customer_id": registration.json()["customer_id"],
        "token": login.json()["access_token"],
        "headers": {
            "Authorization": f"Bearer {login.json()['access_token']}"
        },
    }


def create_test_inventory():
    """Create an active shop with sufficient rice inventory."""

    shop_id = create_shop("Customer Order Test Shop")

    product_id = create_product(
        name="Rice",
        base_unit="kg",
    )

    set_inventory(
        shop_id=shop_id,
        product_id=product_id,
        price_paise=6800,
        stock_quantity=20,
    )

    return shop_id


def rice_order(quantity=1):
    return {
        "intent": "place_order",
        "language": "en",
        "items": [
            {
                "raw_name": "rice",
                "normalized_name": "rice",
                "quantity": quantity,
                "unit": "kg",
            }
        ],
    }


def place_customer_order(
    client,
    shop_id,
    customer,
    idempotency_key,
    quantity=1,
):
    return client.post(
        f"/customers/orders/confirm?shop_id={shop_id}",
        json=rice_order(quantity),
        headers={
            **customer["headers"],
            "Idempotency-Key": idempotency_key,
        },
    )


def test_customer_order_confirmation_requires_authentication(client):
    response = client.post(
        "/customers/orders/confirm?shop_id=1",
        json=rice_order(),
        headers={"Idempotency-Key": "unauthenticated-test"},
    )

    assert response.status_code == 401


def test_customer_order_confirmation_requires_idempotency_key(client):
    shop_id = create_test_inventory()
    customer = register_and_login(client, "missing-key@example.com")

    response = client.post(
        f"/customers/orders/confirm?shop_id={shop_id}",
        json=rice_order(),
        headers=customer["headers"],
    )

    assert response.status_code == 400


def test_customer_can_confirm_order_and_view_it(client):
    shop_id = create_test_inventory()
    customer = register_and_login(client, "buyer@example.com")

    response = place_customer_order(
        client,
        shop_id,
        customer,
        "customer-order-001",
    )

    assert response.status_code == 201

    saved = response.json()

    assert saved["status"] == "pending"
    assert saved["total_paise"] == 6800
    assert saved["stock_reserved"] is False
    assert saved["acceptance_mode"] == "manual"

    history = client.get(
        "/customers/orders",
        headers=customer["headers"],
    )

    assert history.status_code == 200
    assert history.json()["customer_id"] == customer["customer_id"]
    assert history.json()["count"] == 1
    history_order = history.json()["orders"][0]
    assert history_order["payment"]["status"] == "not_started"
    assert history_order["payment"]["amount_refunded_paise"] == 0
    assert history_order["payment"]["razorpay_order_id"] is None

    order = client.get(
        f"/customers/orders/{saved['order_reference']}",
        headers=customer["headers"],
    )

    assert order.status_code == 200
    assert order.json()["order_reference"] == saved["order_reference"]
    assert order.json()["items"][0]["product_name"] == "Rice"
    assert order.json()["payment"]["status"] == "not_started"
    assert order.json()["payment"]["amount_refunded_paise"] == 0

    with closing(database.get_connection()) as connection:
        ownership = connection.execute(
            """
            SELECT customer_id
            FROM customer_order_ownership
            WHERE order_id = ?
            """,
            (saved["order_id"],),
        ).fetchone()

    assert ownership is not None
    assert ownership["customer_id"] == customer["customer_id"]

    print("PASS: Customer order creation and ownership")


def test_customer_cannot_view_another_customers_order(client):
    shop_id = create_test_inventory()

    customer_a = register_and_login(client, "customer-a@example.com")
    customer_b = register_and_login(client, "customer-b@example.com")

    order = place_customer_order(
        client,
        shop_id,
        customer_a,
        "customer-a-private-order",
    )

    assert order.status_code == 201

    reference = order.json()["order_reference"]

    response = client.get(
        f"/customers/orders/{reference}",
        headers=customer_b["headers"],
    )

    assert response.status_code == 404

    print("PASS: Cross-customer order access rejected")


def test_customer_history_contains_only_own_orders(client):
    shop_id = create_test_inventory()

    customer_a = register_and_login(client, "history-a@example.com")
    customer_b = register_and_login(client, "history-b@example.com")

    order_a = place_customer_order(
        client,
        shop_id,
        customer_a,
        "history-order-a",
    )

    order_b = place_customer_order(
        client,
        shop_id,
        customer_b,
        "history-order-b",
    )

    assert order_a.status_code == 201
    assert order_b.status_code == 201

    history_a = client.get(
        "/customers/orders",
        headers=customer_a["headers"],
    )

    history_b = client.get(
        "/customers/orders",
        headers=customer_b["headers"],
    )

    assert history_a.status_code == 200
    assert history_b.status_code == 200

    assert history_a.json()["count"] == 1
    assert history_b.json()["count"] == 1

    assert (
        history_a.json()["orders"][0]["order_reference"]
        == order_a.json()["order_reference"]
    )

    assert (
        history_b.json()["orders"][0]["order_reference"]
        == order_b.json()["order_reference"]
    )

    print("PASS: Customer histories remain isolated")


def test_customer_idempotent_retry_does_not_duplicate_order(client):
    shop_id = create_test_inventory()
    customer = register_and_login(client, "retry@example.com")

    first = place_customer_order(
        client,
        shop_id,
        customer,
        "retry-customer-order",
    )

    second = place_customer_order(
        client,
        shop_id,
        customer,
        "retry-customer-order",
    )

    assert first.status_code == 201
    assert second.status_code == 201

    assert (
        first.json()["order_reference"]
        == second.json()["order_reference"]
    )

    with closing(database.get_connection()) as connection:
        order_count = connection.execute(
            "SELECT COUNT(*) AS total FROM orders"
        ).fetchone()["total"]

        ownership_count = connection.execute(
            "SELECT COUNT(*) AS total FROM customer_order_ownership"
        ).fetchone()["total"]

        idempotency_count = connection.execute(
            "SELECT COUNT(*) AS total FROM order_idempotency"
        ).fetchone()["total"]

    assert order_count == 1
    assert ownership_count == 1
    assert idempotency_count == 1

    print("PASS: Customer idempotent retry prevents duplicate orders")


def test_same_idempotency_key_is_isolated_between_customers(client):
    shop_id = create_test_inventory()

    customer_a = register_and_login(client, "key-a@example.com")
    customer_b = register_and_login(client, "key-b@example.com")

    first = place_customer_order(
        client,
        shop_id,
        customer_a,
        "shared-key",
    )

    second = place_customer_order(
        client,
        shop_id,
        customer_b,
        "shared-key",
    )

    assert first.status_code == 201
    assert second.status_code == 201

    assert (
        first.json()["order_reference"]
        != second.json()["order_reference"]
    )

    history_a = client.get(
        "/customers/orders",
        headers=customer_a["headers"],
    )

    history_b = client.get(
        "/customers/orders",
        headers=customer_b["headers"],
    )

    assert history_a.json()["count"] == 1
    assert history_b.json()["count"] == 1

    print("PASS: Idempotency keys are isolated between customers")


def test_same_customer_cannot_reuse_key_with_different_order(client):
    shop_id = create_test_inventory()
    customer = register_and_login(client, "conflict@example.com")

    first = place_customer_order(
        client,
        shop_id,
        customer,
        "conflicting-customer-key",
        quantity=1,
    )

    second = place_customer_order(
        client,
        shop_id,
        customer,
        "conflicting-customer-key",
        quantity=2,
    )

    assert first.status_code == 201
    assert second.status_code == 409

    with closing(database.get_connection()) as connection:
        order_count = connection.execute(
            "SELECT COUNT(*) AS total FROM orders"
        ).fetchone()["total"]

    assert order_count == 1

    print("PASS: Different order data rejected for reused key")


def test_customer_history_requires_authentication(client):
    response = client.get("/customers/orders")

    assert response.status_code == 401


def test_customer_order_lookup_requires_authentication(client):
    response = client.get("/customers/orders/VK-UNKNOWN")

    assert response.status_code == 401


def test_customer_invalid_idempotency_key_is_rejected(client):
    shop_id = create_test_inventory()
    customer = register_and_login(client, "invalid-key@example.com")

    response = place_customer_order(
        client,
        shop_id,
        customer,
        " " * 1,
    )

    assert response.status_code == 400

    print("PASS: Empty idempotency key rejected")



def _enable_auto_accept_for_shop(shop_id):
    with closing(database.get_connection()) as connection:
        with connection:
            connection.execute(
                "UPDATE shops SET auto_accept_orders = 1 WHERE id = ?",
                (shop_id,),
            )


def test_auto_accept_shop_confirms_customer_order_and_reserves_stock(client):
    shop_id = create_test_inventory()
    _enable_auto_accept_for_shop(shop_id)
    customer = register_and_login(client, "auto-accept@example.com")

    response = place_customer_order(
        client,
        shop_id,
        customer,
        "auto-accept-customer-order-001",
    )

    assert response.status_code == 201
    saved = response.json()
    assert saved["status"] == "confirmed"
    assert saved["stock_reserved"] is True
    assert saved["acceptance_mode"] == "automatic"
    assert saved["reserved_items"][0]["reserved_quantity"] == 1

    with closing(database.get_connection()) as connection:
        inventory = connection.execute(
            """
            SELECT stock_quantity, reserved_quantity
            FROM inventory
            WHERE shop_id = ?
            """,
            (shop_id,),
        ).fetchone()

        order_row = connection.execute(
            "SELECT status FROM orders WHERE id = ?",
            (saved["order_id"],),
        ).fetchone()

    assert order_row["status"] == "confirmed"
    assert float(inventory["stock_quantity"]) == 20.0
    assert float(inventory["reserved_quantity"]) == 1.0

    detail = client.get(
        f"/customers/orders/{saved['order_reference']}",
        headers=customer["headers"],
    )
    assert detail.status_code == 200
    assert detail.json()["status"] == "confirmed"
    assert detail.json()["payment"]["status"] == "not_started"


def test_auto_accept_rolls_back_if_stock_changes_before_reservation(
    client,
    monkeypatch,
):
    shop_id = create_test_inventory()
    _enable_auto_accept_for_shop(shop_id)
    customer = register_and_login(client, "auto-accept-stock-race@example.com")

    original_check = main_module._check_inventory_for_order

    def check_then_stock_disappears(order, shop_id):
        result = original_check(order=order, shop_id=shop_id)
        product_id = result["items"][0]["product_id"]
        with closing(database.get_connection()) as connection:
            with connection:
                connection.execute(
                    """
                    UPDATE inventory
                    SET stock_quantity = 0
                    WHERE shop_id = ? AND product_id = ?
                    """,
                    (shop_id, product_id),
                )
        return result

    monkeypatch.setattr(
        main_module,
        "_check_inventory_for_order",
        check_then_stock_disappears,
    )

    response = place_customer_order(
        client,
        shop_id,
        customer,
        "auto-accept-stock-race-001",
    )

    assert response.status_code == 409
    assert "No order was saved" in response.json()["detail"]["message"]

    with closing(database.get_connection()) as connection:
        order_count = connection.execute(
            "SELECT COUNT(*) AS total FROM orders"
        ).fetchone()["total"]
        ownership_count = connection.execute(
            "SELECT COUNT(*) AS total FROM customer_order_ownership"
        ).fetchone()["total"]
        idempotency_count = connection.execute(
            "SELECT COUNT(*) AS total FROM order_idempotency"
        ).fetchone()["total"]
        inventory = connection.execute(
            "SELECT reserved_quantity FROM inventory WHERE shop_id = ?",
            (shop_id,),
        ).fetchone()

    assert order_count == 0
    assert ownership_count == 0
    assert idempotency_count == 0
    assert float(inventory["reserved_quantity"]) == 0.0


def test_auto_accept_idempotent_retry_does_not_reserve_stock_twice(client):
    shop_id = create_test_inventory()
    _enable_auto_accept_for_shop(shop_id)
    customer = register_and_login(client, "auto-retry@example.com")

    first = place_customer_order(
        client,
        shop_id,
        customer,
        "auto-accept-retry-001",
    )

    second = place_customer_order(
        client,
        shop_id,
        customer,
        "auto-accept-retry-001",
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["order_reference"] == second.json()["order_reference"]
    assert second.json()["status"] == "confirmed"
    assert second.json()["stock_reserved"] is True

    with closing(database.get_connection()) as connection:
        order_count = connection.execute(
            "SELECT COUNT(*) AS total FROM orders"
        ).fetchone()["total"]

        reservation = connection.execute(
            """
            SELECT reserved_quantity
            FROM inventory
            WHERE shop_id = ?
            """,
            (shop_id,),
        ).fetchone()

    assert order_count == 1
    assert float(reservation["reserved_quantity"]) == 1.0