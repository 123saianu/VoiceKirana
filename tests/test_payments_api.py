from concurrent.futures import ThreadPoolExecutor

from contextlib import closing
from unittest.mock import Mock

import threading

import pytest
from fastapi.testclient import TestClient

import backend.database as database
import backend.main as main
from backend.security import create_access_token

from backend.services.inventory_service import (
    create_shop,
    create_product,
    set_inventory,
)


JWT_SECRET = "VoiceKirana-Payment-API-Test-Secret-123456"
TEST_PUBLIC_KEY = "rzp_test_voicekirana123"
CUSTOMER_PASSWORD = "Customer-Test-Password-123!"


@pytest.fixture(autouse=True)
def isolate_database(tmp_path, monkeypatch):
    """Use a fresh isolated SQLite database for every test."""

    monkeypatch.setattr(database, "DATABASE_URL", "")
    monkeypatch.setattr(
        database,
        "DATABASE_PATH",
        tmp_path / "voicekirana_payments_test.db",
    )

    monkeypatch.setenv("JWT_SECRET_KEY", JWT_SECRET)

    database.initialize_database()


@pytest.fixture
def client():
    with TestClient(main.app) as test_client:
        yield test_client


def prepare_customer(client, email):
    registration = client.post(
        "/customers/register",
        json={
            "full_name": "Payment Test Customer",
            "email": email,
            "password": CUSTOMER_PASSWORD,
        },
    )

    assert registration.status_code == 201

    login = client.post(
        "/customers/login",
        json={
            "email": email,
            "password": CUSTOMER_PASSWORD,
        },
    )

    assert login.status_code == 200

    token = login.json()["access_token"]

    return {
        "customer_id": registration.json()["customer_id"],
        "headers": {
            "Authorization": f"Bearer {token}",
        },
    }


def accept_order_as_shop(client, shop_id, order_reference):
    """Accept a customer order using an authenticated shop owner."""
    with database.get_connection() as connection:
        with connection:
            user_id = connection.insert_returning_id(
                """
                INSERT INTO users (
                    email,
                    full_name,
                    password_hash
                )
                VALUES (?, ?, ?)
                """,
                (
                    f"payment-shop-owner-{shop_id}@example.com",
                    "Payment Test Shop Owner",
                    "unused-test-password-hash",
                ),
            )

            connection.execute(
                """
                INSERT INTO shop_memberships (
                    user_id,
                    shop_id,
                    role
                )
                VALUES (?, ?, 'owner')
                """,
                (user_id, shop_id),
            )

    token = create_access_token(user_id)
    response = client.post(
        f"/orders/{order_reference}/accept?shop_id={shop_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "confirmed"
    return response.json()


def payment_test_shop_owner_headers(shop_id):
    """Create an authenticated shop owner for cancellation tests."""
    with database.get_connection() as connection:
        with connection:
            user_id = connection.insert_returning_id(
                """
                INSERT INTO users (
                    email,
                    full_name,
                    password_hash
                )
                VALUES (?, ?, ?)
                """,
                (
                    f"payment-cancel-owner-{shop_id}@example.com",
                    "Payment Cancellation Test Owner",
                    "unused-test-password-hash",
                ),
            )

            connection.execute(
                """
                INSERT INTO shop_memberships (
                    user_id,
                    shop_id,
                    role
                )
                VALUES (?, ?, 'owner')
                """,
                (user_id, shop_id),
            )

    return {
        "Authorization": f"Bearer {create_access_token(user_id)}"
    }


def get_reserved_stock_for_order(shop_id, order):
    product_id = order["items"][0]["product_id"]

    with database.get_connection() as connection:
        row = connection.execute(
            """
            SELECT reserved_quantity
            FROM inventory
            WHERE shop_id = ? AND product_id = ?
            """,
            (shop_id, product_id),
        ).fetchone()

    assert row is not None
    return float(row["reserved_quantity"])


def prepare_customer_order(client, email="buyer@example.com"):
    """Create an authenticated customer and a shop-accepted rice order."""

    customer = prepare_customer(client, email)

    shop_id = create_shop("Razorpay Payment Test Shop")

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

    response = client.post(
        f"/customers/orders/confirm?shop_id={shop_id}",
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
        headers={
            **customer["headers"],
            "Idempotency-Key": f"order-{email}",
        },
    )

    assert response.status_code == 201

    order_data = response.json()
    accept_order_as_shop(
        client,
        shop_id,
        order_data["order_reference"],
    )

    return customer, shop_id, order_data


def mock_razorpay(monkeypatch, side_effect=None):
    """Replace Razorpay network calls with a controlled mock."""

    monkeypatch.setattr(
        main,
        "get_razorpay_public_key_id",
        lambda: TEST_PUBLIC_KEY,
    )

    mock = Mock(
        return_value={
            "razorpay_order_id": "order_test123",
            "amount_paise": 6800,
            "currency": "INR",
            "status": "created",
            "key_id": TEST_PUBLIC_KEY,
        }
    )

    if side_effect is not None:
        mock.side_effect = side_effect

    monkeypatch.setattr(
        main,
        "create_razorpay_order",
        mock,
    )

    return mock


def create_payment(
    client,
    order_reference,
    customer,
    key="payment-attempt-001",
):
    return client.post(
        f"/customers/orders/{order_reference}/payments",
        headers={
            **customer["headers"],
            "Idempotency-Key": key,
        },
    )


def get_payment_record(payment_id):
    with closing(database.get_connection()) as connection:
        return connection.execute(
            "SELECT * FROM payments WHERE id = ?",
            (payment_id,),
        ).fetchone()


def test_customer_payment_requires_authentication(client, monkeypatch):
    shop_id = create_shop("Authentication Payment Shop")

    mock = mock_razorpay(monkeypatch)

    response = client.post(
        "/customers/orders/VK-UNKNOWN/payments",
        headers={"Idempotency-Key": "payment-auth-test"},
    )

    assert response.status_code == 401
    mock.assert_not_called()

    print("PASS: Payment endpoint requires customer authentication")


def test_customer_can_create_razorpay_payment(
    client,
    monkeypatch,
):
    mock = mock_razorpay(monkeypatch)

    customer, shop_id, order = prepare_customer_order(client)

    response = create_payment(
        client,
        order["order_reference"],
        customer,
    )

    assert response.status_code == 201

    result = response.json()

    assert result["order_reference"] == order["order_reference"]
    assert result["razorpay_order_id"] == "order_test123"
    assert result["amount_paise"] == 6800
    assert result["currency"] == "INR"
    assert result["key_id"] == TEST_PUBLIC_KEY
    assert result["status"] == "created"

    mock.assert_called_once()

    args = mock.call_args.kwargs

    assert args["amount_paise"] == 6800
    assert args["receipt"] == f"VKP-{result['payment_id']}"
    assert args["notes"]["order_reference"] == order["order_reference"]

    payment = get_payment_record(result["payment_id"])

    assert payment is not None
    assert payment["customer_id"] == customer["customer_id"]
    assert payment["order_id"] == order["order_id"]
    assert payment["razorpay_order_id"] == "order_test123"
    assert payment["amount_paise"] == 6800
    assert payment["status"] == "created"
    order_response = client.get(
        f"/customers/orders/{order['order_reference']}",
        headers=customer["headers"],
    )

    assert order_response.status_code == 200

    payment_summary = order_response.json()["payment"]

    assert payment_summary["payment_id"] == result["payment_id"]
    assert payment_summary["status"] == "created"
    assert payment_summary["amount_paise"] == 6800
    assert payment_summary["amount_refunded_paise"] == 0
    assert payment_summary["currency"] == "INR"
    assert payment_summary["razorpay_order_id"] == "order_test123"
    assert payment_summary["key_id"] == TEST_PUBLIC_KEY
    print("PASS: Customer payment order creation and persistence")


def test_customer_cannot_pay_another_customers_order(
    client,
    monkeypatch,
):
    mock = mock_razorpay(monkeypatch)

    customer_a, shop_id, order = prepare_customer_order(
        client,
        "buyer-a@example.com",
    )

    customer_b = prepare_customer(
        client,
        "buyer-b@example.com",
    )

    response = create_payment(
        client,
        order["order_reference"],
        customer_b,
    )

    assert response.status_code == 404
    mock.assert_not_called()

    print("PASS: Cross-customer payment access rejected")


@pytest.mark.parametrize(
    "blocked_status",
    ["pending", "cancelled", "rejected"],
)
def test_non_confirmed_order_cannot_be_paid(
    client,
    monkeypatch,
    blocked_status,
):
    mock = mock_razorpay(monkeypatch)

    customer, shop_id, order = prepare_customer_order(client)

    with database.get_connection() as connection:
        with connection:
            connection.execute(
                "UPDATE orders SET status = ? WHERE id = ?",
                (blocked_status, order["order_id"]),
            )

    response = create_payment(
        client,
        order["order_reference"],
        customer,
    )

    assert response.status_code == 409
    assert "Only shop-accepted orders" in response.json()["detail"]
    mock.assert_not_called()

    print(f"PASS: {blocked_status} order cannot start payment")


def test_order_below_minimum_amount_cannot_be_paid(
    client,
    monkeypatch,
):
    mock = mock_razorpay(monkeypatch)

    customer, shop_id, order = prepare_customer_order(client)

    with database.get_connection() as connection:
        with connection:
            connection.execute(
                "UPDATE orders SET total_amount = 99 WHERE id = ?",
                (order["order_id"],),
            )

    response = create_payment(
        client,
        order["order_reference"],
        customer,
    )

    assert response.status_code == 400
    mock.assert_not_called()

    print("PASS: Order below Razorpay minimum rejected")


def test_same_payment_idempotency_key_reuses_existing_payment(
    client,
    monkeypatch,
):
    mock = mock_razorpay(monkeypatch)

    customer, shop_id, order = prepare_customer_order(client)

    first = create_payment(
        client,
        order["order_reference"],
        customer,
        "same-payment-key",
    )

    second = create_payment(
        client,
        order["order_reference"],
        customer,
        "same-payment-key",
    )

    assert first.status_code == 201
    assert second.status_code == 201

    assert first.json()["payment_id"] == second.json()["payment_id"]
    assert (
        first.json()["razorpay_order_id"]
        == second.json()["razorpay_order_id"]
    )

    mock.assert_called_once()

    print("PASS: Payment retry reuses the existing Razorpay order")


def test_second_active_payment_attempt_is_rejected(
    client,
    monkeypatch,
):
    mock = mock_razorpay(monkeypatch)

    customer, shop_id, order = prepare_customer_order(client)

    first = create_payment(
        client,
        order["order_reference"],
        customer,
        "first-payment-key",
    )

    second = create_payment(
        client,
        order["order_reference"],
        customer,
        "second-payment-key",
    )

    assert first.status_code == 201
    assert second.status_code == 409

    mock.assert_called_once()

    print("PASS: Second active payment attempt rejected")


def test_definite_razorpay_failure_is_recorded(
    client,
    monkeypatch,
):
    mock = mock_razorpay(
        monkeypatch,
        side_effect=main.RazorpayAPIError(
            "Invalid Razorpay request",
            status_code=400,
        ),
    )

    customer, shop_id, order = prepare_customer_order(client)

    response = create_payment(
        client,
        order["order_reference"],
        customer,
    )

    assert response.status_code == 502

    with closing(database.get_connection()) as connection:
        payment = connection.execute(
            "SELECT status FROM payments WHERE order_id = ?",
            (order["order_id"],),
        ).fetchone()

    assert payment["status"] == "creation_failed"
    mock.assert_called_once()

    print("PASS: Definite Razorpay failure recorded")


def test_ambiguous_razorpay_failure_keeps_attempt_pending(
    client,
    monkeypatch,
):
    mock = mock_razorpay(
        monkeypatch,
        side_effect=main.RazorpayAPIError(
            "Razorpay request timed out",
        ),
    )

    customer, shop_id, order = prepare_customer_order(client)

    first = create_payment(
        client,
        order["order_reference"],
        customer,
        "ambiguous-payment-key",
    )

    assert first.status_code == 502

    retry = create_payment(
        client,
        order["order_reference"],
        customer,
        "ambiguous-payment-key",
    )

    assert retry.status_code == 409
    mock.assert_called_once()

    with closing(database.get_connection()) as connection:
        payment = connection.execute(
            "SELECT status FROM payments WHERE order_id = ?",
            (order["order_id"],),
        ).fetchone()

    assert payment["status"] == "creating"

    print("PASS: Ambiguous Razorpay attempt is not duplicated")


def test_missing_payment_idempotency_key_rejected(
    client,
    monkeypatch,
):
    mock = mock_razorpay(monkeypatch)

    customer, shop_id, order = prepare_customer_order(client)

    response = client.post(
        f"/customers/orders/{order['order_reference']}/payments",
        headers=customer["headers"],
    )

    assert response.status_code == 400
    mock.assert_not_called()

    print("PASS: Missing payment idempotency key rejected")


def test_missing_razorpay_configuration_returns_503(
    client,
    monkeypatch,
):
    customer, shop_id, order = prepare_customer_order(client)

    monkeypatch.setattr(
        main,
        "get_razorpay_public_key_id",
        Mock(
            side_effect=main.RazorpayConfigurationError(
                "Test configuration error"
            )
        ),
    )

    response = create_payment(
        client,
        order["order_reference"],
        customer,
    )

    assert response.status_code == 503

    with closing(database.get_connection()) as connection:
        count = connection.execute(
            "SELECT COUNT(*) AS total FROM payments"
        ).fetchone()["total"]

    assert count == 0

    print("PASS: Missing Razorpay configuration handled safely")

def prepare_payment_for_verification(client, monkeypatch, email):
    """Create a customer-owned order and a mocked Razorpay payment."""

    mock_razorpay(monkeypatch)

    customer, shop_id, order = prepare_customer_order(client, email)

    created = create_payment(
        client,
        order["order_reference"],
        customer,
        "verification-payment-key",
    )

    assert created.status_code == 201

    signature_mock = Mock(return_value=True)
    monkeypatch.setattr(
        main,
        "verify_payment_signature",
        signature_mock,
    )

    return customer, order, created.json(), signature_mock


def send_payment_verification(
    client,
    customer,
    order_reference,
    payment_id="pay_test123",
    order_id="order_test123",
):
    return client.post(
        f"/customers/orders/{order_reference}/payments/verify",
        headers=customer["headers"],
        json={
            "razorpay_payment_id": payment_id,
            "razorpay_order_id": order_id,
            "razorpay_signature": "a" * 64,
        },
    )


def test_valid_captured_payment_is_verified(client, monkeypatch):
    customer, order, created, signature_mock = (
        prepare_payment_for_verification(
            client, monkeypatch, "verify-success@example.com"
        )
    )

    fetch_mock = Mock(return_value={
        "razorpay_payment_id": "pay_test123",
        "razorpay_order_id": "order_test123",
        "amount_paise": 6800,
        "currency": "INR",
        "status": "captured",
        "captured": True,
    })

    monkeypatch.setattr(main, "fetch_razorpay_payment", fetch_mock)

    response = send_payment_verification(
        client,
        customer,
        order["order_reference"],
    )

    assert response.status_code == 200
    assert response.json()["status"] == "captured"
    assert response.json()["captured"] is True

    signature_mock.assert_called_once_with(
        "order_test123",
        "pay_test123",
        "a" * 64,
    )
    fetch_mock.assert_called_once_with("pay_test123")

    payment = get_payment_record(created["payment_id"])

    assert payment["status"] == "captured"
    assert payment["razorpay_payment_id"] == "pay_test123"

    with closing(database.get_connection()) as connection:
        saved_order = connection.execute(
            "SELECT status FROM orders WHERE id = ?",
            (order["order_id"],),
        ).fetchone()

    assert saved_order["status"] == "confirmed"

    print("PASS: Captured payment verified without changing order acceptance")


def test_invalid_checkout_signature_rejected(client, monkeypatch):
    customer, order, created, signature_mock = (
        prepare_payment_for_verification(
            client, monkeypatch, "verify-invalid-signature@example.com"
        )
    )

    signature_mock.return_value = False

    fetch_mock = Mock()
    monkeypatch.setattr(main, "fetch_razorpay_payment", fetch_mock)

    response = send_payment_verification(
        client, customer, order["order_reference"]
    )

    assert response.status_code == 400
    fetch_mock.assert_not_called()

    payment = get_payment_record(created["payment_id"])
    assert payment["status"] == "created"

    print("PASS: Invalid Checkout signature rejected")


def test_unknown_razorpay_order_id_rejected(client, monkeypatch):
    customer, order, created, signature_mock = (
        prepare_payment_for_verification(
            client, monkeypatch, "verify-order-mismatch@example.com"
        )
    )

    fetch_mock = Mock()
    monkeypatch.setattr(main, "fetch_razorpay_payment", fetch_mock)

    response = send_payment_verification(
        client,
        customer,
        order["order_reference"],
        order_id="order_wrong123",
    )

    assert response.status_code == 404
    signature_mock.assert_not_called()
    fetch_mock.assert_not_called()

    print("PASS: Unknown Razorpay order ID rejected")


@pytest.mark.parametrize(
    "changed_field, changed_value",
    [
        ("razorpay_order_id", "order_another123"),
        ("amount_paise", 5000),
        ("currency", "USD"),
    ],
)
def test_provider_payment_mismatch_rejected(
    client,
    monkeypatch,
    changed_field,
    changed_value,
):
    customer, order, created, signature_mock = (
        prepare_payment_for_verification(
            client,
            monkeypatch,
            f"verify-mismatch-{changed_field}@example.com",
        )
    )

    provider_data = {
        "razorpay_payment_id": "pay_test123",
        "razorpay_order_id": "order_test123",
        "amount_paise": 6800,
        "currency": "INR",
        "status": "captured",
        "captured": True,
    }

    provider_data[changed_field] = changed_value

    monkeypatch.setattr(
        main,
        "fetch_razorpay_payment",
        Mock(return_value=provider_data),
    )

    response = send_payment_verification(
        client, customer, order["order_reference"]
    )

    assert response.status_code == 409

    payment = get_payment_record(created["payment_id"])
    assert payment["status"] == "created"

    print(f"PASS: Provider {changed_field} mismatch rejected")


def test_authorized_payment_is_not_marked_captured(client, monkeypatch):
    customer, order, created, signature_mock = (
        prepare_payment_for_verification(
            client, monkeypatch, "verify-authorized@example.com"
        )
    )

    monkeypatch.setattr(
        main,
        "fetch_razorpay_payment",
        Mock(return_value={
            "razorpay_payment_id": "pay_test123",
            "razorpay_order_id": "order_test123",
            "amount_paise": 6800,
            "currency": "INR",
            "status": "authorized",
            "captured": False,
        }),
    )

    response = send_payment_verification(
        client, customer, order["order_reference"]
    )

    assert response.status_code == 200
    assert response.json()["status"] == "authorized"
    assert response.json()["captured"] is False

    payment = get_payment_record(created["payment_id"])
    assert payment["status"] == "authorized"

    print("PASS: Authorized payment remains distinct from captured")


def test_failed_payment_is_recorded(client, monkeypatch):
    customer, order, created, signature_mock = (
        prepare_payment_for_verification(
            client, monkeypatch, "verify-failed@example.com"
        )
    )

    monkeypatch.setattr(
        main,
        "fetch_razorpay_payment",
        Mock(return_value={
            "razorpay_payment_id": "pay_test123",
            "razorpay_order_id": "order_test123",
            "amount_paise": 6800,
            "currency": "INR",
            "status": "failed",
            "captured": False,
        }),
    )

    response = send_payment_verification(
        client, customer, order["order_reference"]
    )

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["captured"] is False

    payment = get_payment_record(created["payment_id"])
    assert payment["status"] == "failed"
    assert payment["failure_description"] is not None

    print("PASS: Failed payment status recorded")


def test_another_customer_cannot_verify_payment(client, monkeypatch):
    customer_a, order, created, signature_mock = (
        prepare_payment_for_verification(
            client, monkeypatch, "verify-private-a@example.com"
        )
    )

    customer_b = prepare_customer(
        client, "verify-private-b@example.com"
    )

    fetch_mock = Mock()
    monkeypatch.setattr(main, "fetch_razorpay_payment", fetch_mock)

    response = send_payment_verification(
        client, customer_b, order["order_reference"]
    )

    assert response.status_code == 404
    signature_mock.assert_not_called()
    fetch_mock.assert_not_called()

    print("PASS: Cross-customer payment verification rejected")


def test_repeated_captured_verification_is_idempotent(
    client,
    monkeypatch,
):
    customer, order, created, signature_mock = (
        prepare_payment_for_verification(
            client, monkeypatch, "verify-repeat@example.com"
        )
    )

    fetch_mock = Mock(return_value={
        "razorpay_payment_id": "pay_test123",
        "razorpay_order_id": "order_test123",
        "amount_paise": 6800,
        "currency": "INR",
        "status": "captured",
        "captured": True,
    })

    monkeypatch.setattr(main, "fetch_razorpay_payment", fetch_mock)

    first = send_payment_verification(
        client, customer, order["order_reference"]
    )

    second = send_payment_verification(
        client, customer, order["order_reference"]
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] == "captured"

    assert fetch_mock.call_count == 1

    with closing(database.get_connection()) as connection:
        count = connection.execute(
            "SELECT COUNT(*) AS total FROM payments WHERE order_id = ?",
            (order["order_id"],),
        ).fetchone()["total"]

    assert count == 1

    print("PASS: Repeated captured verification is idempotent")


def test_different_payment_id_cannot_replace_existing_attempt(
    client,
    monkeypatch,
):
    customer, order, created, signature_mock = (
        prepare_payment_for_verification(
            client, monkeypatch, "verify-payment-id-change@example.com"
        )
    )

    fetch_mock = Mock(return_value={
        "razorpay_payment_id": "pay_test123",
        "razorpay_order_id": "order_test123",
        "amount_paise": 6800,
        "currency": "INR",
        "status": "authorized",
        "captured": False,
    })

    monkeypatch.setattr(main, "fetch_razorpay_payment", fetch_mock)

    first = send_payment_verification(
        client, customer, order["order_reference"]
    )

    assert first.status_code == 200
    assert first.json()["status"] == "authorized"

    second = send_payment_verification(
        client,
        customer,
        order["order_reference"],
        payment_id="pay_different123",
    )

    assert second.status_code == 409
    assert fetch_mock.call_count == 1

    print("PASS: Payment ID cannot be replaced within an attempt")


def test_razorpay_status_unavailable_does_not_change_payment(
    client,
    monkeypatch,
):
    customer, order, created, signature_mock = (
        prepare_payment_for_verification(
            client, monkeypatch, "verify-provider-error@example.com"
        )
    )

    monkeypatch.setattr(
        main,
        "fetch_razorpay_payment",
        Mock(
            side_effect=main.RazorpayAPIError(
                "Test provider unavailable"
            )
        ),
    )

    response = send_payment_verification(
        client, customer, order["order_reference"]
    )

    assert response.status_code == 502

    payment = get_payment_record(created["payment_id"])
    assert payment["status"] == "created"
    assert payment["razorpay_payment_id"] is None

    print("PASS: Provider error does not mark payment successful")

def _build_razorpay_webhook_payload(
    event_type,
    payment_status,
    payment_id="pay_test123",
    order_id="order_test123",
    amount=6800,
    currency="INR",
    captured=False,
):
    import json

    entity = {
        "id": payment_id,
        "entity": "payment",
        "order_id": order_id,
        "amount": amount,
        "currency": currency,
        "status": payment_status,
        "captured": captured,
    }

    if payment_status == "failed":
        entity["error_code"] = "BAD_REQUEST_ERROR"
        entity["error_description"] = "Test payment failure"

    payload = {
        "entity": "event",
        "event": event_type,
        "contains": ["payment"],
        "payload": {
            "payment": {
                "entity": entity,
            },
        },
    }

    return json.dumps(payload).encode("utf-8")


def _send_razorpay_webhook(
    client,
    raw_body,
    event_id="event_test_001",
):
    return client.post(
        "/webhooks/razorpay",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": "b" * 64,
            **(
                {"x-razorpay-event-id": event_id}
                if event_id is not None
                else {}
            ),
        },
    )


def _get_webhook_event_count():
    with closing(database.get_connection()) as connection:
        return connection.execute(
            "SELECT COUNT(*) AS total FROM razorpay_webhook_events"
        ).fetchone()["total"]


def test_valid_captured_webhook_updates_payment(
    client,
    monkeypatch,
):
    customer, order, created, _ = prepare_payment_for_verification(
        client,
        monkeypatch,
        "webhook-captured@example.com",
    )

    raw_body = _build_razorpay_webhook_payload(
        "payment.captured",
        "captured",
        captured=True,
    )

    signature_mock = Mock(return_value=True)
    monkeypatch.setattr(
        main,
        "verify_webhook_signature",
        signature_mock,
    )

    response = _send_razorpay_webhook(client, raw_body)

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert response.json()["payment_updated"] is True
    assert response.json()["payment_status"] == "captured"

    signature_mock.assert_called_once_with(
        raw_body,
        "b" * 64,
    )

    payment = get_payment_record(created["payment_id"])

    assert payment["status"] == "captured"
    assert payment["razorpay_payment_id"] == "pay_test123"

    with closing(database.get_connection()) as connection:
        saved_order = connection.execute(
            "SELECT status FROM orders WHERE id = ?",
            (order["order_id"],),
        ).fetchone()

    assert saved_order["status"] == "confirmed"
    assert _get_webhook_event_count() == 1

    print("PASS: Captured webhook updates payment without changing shop acceptance")


def test_invalid_webhook_signature_is_rejected(
    client,
    monkeypatch,
):
    raw_body = _build_razorpay_webhook_payload(
        "payment.captured",
        "captured",
        captured=True,
    )

    signature_mock = Mock(return_value=False)
    monkeypatch.setattr(
        main,
        "verify_webhook_signature",
        signature_mock,
    )

    response = _send_razorpay_webhook(client, raw_body)

    assert response.status_code == 400
    assert _get_webhook_event_count() == 0

    print("PASS: Invalid webhook signature rejected")


def test_missing_webhook_event_id_is_rejected(
    client,
    monkeypatch,
):
    raw_body = _build_razorpay_webhook_payload(
        "payment.captured",
        "captured",
        captured=True,
    )

    signature_mock = Mock(return_value=True)
    monkeypatch.setattr(
        main,
        "verify_webhook_signature",
        signature_mock,
    )

    response = _send_razorpay_webhook(
        client,
        raw_body,
        event_id=None,
    )

    assert response.status_code == 400
    signature_mock.assert_not_called()
    assert _get_webhook_event_count() == 0

    print("PASS: Missing webhook event ID rejected")


def test_duplicate_webhook_event_is_not_processed_twice(
    client,
    monkeypatch,
):
    customer, order, created, _ = prepare_payment_for_verification(
        client,
        monkeypatch,
        "webhook-duplicate@example.com",
    )

    raw_body = _build_razorpay_webhook_payload(
        "payment.captured",
        "captured",
        captured=True,
    )

    monkeypatch.setattr(
        main,
        "verify_webhook_signature",
        Mock(return_value=True),
    )

    first = _send_razorpay_webhook(
        client,
        raw_body,
        "event_duplicate_001",
    )

    second = _send_razorpay_webhook(
        client,
        raw_body,
        "event_duplicate_001",
    )

    assert first.status_code == 200
    assert first.json()["payment_updated"] is True

    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    assert second.json()["duplicate"] is True

    assert _get_webhook_event_count() == 1

    payment = get_payment_record(created["payment_id"])
    assert payment["status"] == "captured"

    print("PASS: Duplicate webhook event ignored")


def test_webhook_amount_mismatch_does_not_update_payment(
    client,
    monkeypatch,
):
    customer, order, created, _ = prepare_payment_for_verification(
        client,
        monkeypatch,
        "webhook-amount-mismatch@example.com",
    )

    raw_body = _build_razorpay_webhook_payload(
        "payment.captured",
        "captured",
        amount=9999,
        captured=True,
    )

    monkeypatch.setattr(
        main,
        "verify_webhook_signature",
        Mock(return_value=True),
    )

    response = _send_razorpay_webhook(
        client,
        raw_body,
        "event_amount_mismatch",
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert response.json()["payment_updated"] is False

    payment = get_payment_record(created["payment_id"])
    assert payment["status"] == "created"
    assert payment["razorpay_payment_id"] is None

    print("PASS: Webhook with incorrect amount cannot update payment")


def test_authorized_webhook_can_be_followed_by_captured_webhook(
    client,
    monkeypatch,
):
    customer, order, created, _ = prepare_payment_for_verification(
        client,
        monkeypatch,
        "webhook-authorized-captured@example.com",
    )

    monkeypatch.setattr(
        main,
        "verify_webhook_signature",
        Mock(return_value=True),
    )

    authorized_body = _build_razorpay_webhook_payload(
        "payment.authorized",
        "authorized",
        captured=False,
    )

    first = _send_razorpay_webhook(
        client,
        authorized_body,
        "event_authorized_001",
    )

    assert first.status_code == 200
    assert first.json()["payment_status"] == "authorized"

    payment = get_payment_record(created["payment_id"])
    assert payment["status"] == "authorized"

    captured_body = _build_razorpay_webhook_payload(
        "payment.captured",
        "captured",
        captured=True,
    )

    second = _send_razorpay_webhook(
        client,
        captured_body,
        "event_captured_001",
    )

    assert second.status_code == 200
    assert second.json()["payment_status"] == "captured"

    payment = get_payment_record(created["payment_id"])
    assert payment["status"] == "captured"

    print("PASS: Authorized payment can transition to captured")


def test_delayed_failed_webhook_does_not_downgrade_captured_payment(
    client,
    monkeypatch,
):
    customer, order, created, _ = prepare_payment_for_verification(
        client,
        monkeypatch,
        "webhook-out-of-order@example.com",
    )

    monkeypatch.setattr(
        main,
        "verify_webhook_signature",
        Mock(return_value=True),
    )

    captured_body = _build_razorpay_webhook_payload(
        "payment.captured",
        "captured",
        captured=True,
    )

    first = _send_razorpay_webhook(
        client,
        captured_body,
        "event_captured_first",
    )

    assert first.status_code == 200
    assert first.json()["payment_status"] == "captured"

    delayed_failed_body = _build_razorpay_webhook_payload(
        "payment.failed",
        "failed",
        captured=False,
    )

    second = _send_razorpay_webhook(
        client,
        delayed_failed_body,
        "event_failed_delayed",
    )

    assert second.status_code == 200
    assert second.json()["payment_status"] == "captured"

    payment = get_payment_record(created["payment_id"])
    assert payment["status"] == "captured"

    print("PASS: Delayed failed event does not downgrade captured payment")


def test_failed_webhook_records_failure_details(
    client,
    monkeypatch,
):
    customer, order, created, _ = prepare_payment_for_verification(
        client,
        monkeypatch,
        "webhook-failed@example.com",
    )

    raw_body = _build_razorpay_webhook_payload(
        "payment.failed",
        "failed",
    )

    monkeypatch.setattr(
        main,
        "verify_webhook_signature",
        Mock(return_value=True),
    )

    response = _send_razorpay_webhook(
        client,
        raw_body,
        "event_payment_failed",
    )

    assert response.status_code == 200
    assert response.json()["payment_status"] == "failed"

    payment = get_payment_record(created["payment_id"])

    assert payment["status"] == "failed"
    assert payment["failure_code"] == "BAD_REQUEST_ERROR"
    assert payment["failure_description"] == "Test payment failure"

    print("PASS: Failed webhook records provider failure details")


def test_unsupported_webhook_event_is_acknowledged_without_payment_update(
    client,
    monkeypatch,
):
    import json

    raw_body = json.dumps({
        "entity": "event",
        "event": "order.paid",
        "payload": {},
    }).encode("utf-8")

    monkeypatch.setattr(
        main,
        "verify_webhook_signature",
        Mock(return_value=True),
    )

    response = _send_razorpay_webhook(
        client,
        raw_body,
        "event_unsupported_001",
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert response.json()["payment_updated"] is False
    assert _get_webhook_event_count() == 1

    print("PASS: Unsupported webhook event safely acknowledged")


def test_mismatched_event_and_payment_status_are_rejected(
    client,
    monkeypatch,
):
    raw_body = _build_razorpay_webhook_payload(
        "payment.captured",
        "authorized",
        captured=False,
    )

    monkeypatch.setattr(
        main,
        "verify_webhook_signature",
        Mock(return_value=True),
    )

    response = _send_razorpay_webhook(
        client,
        raw_body,
        "event_status_mismatch",
    )

    assert response.status_code == 400
    assert _get_webhook_event_count() == 0

    print("PASS: Mismatched webhook event and payment status rejected")

def _build_razorpay_refund_webhook_payload(
    event_type,
    refund_status,
    amount_refunded,
    refund_amount=2500,
    refund_id="rfnd_test123",
    payment_id="pay_test123",
    refund_payment_id=None,
):
    import json

    payload = {
        "entity": "event",
        "event": event_type,
        "contains": ["refund", "payment"],
        "payload": {
            "refund": {
                "entity": {
                    "id": refund_id,
                    "entity": "refund",
                    "amount": refund_amount,
                    "currency": "INR",
                    "payment_id": refund_payment_id or payment_id,
                    "status": refund_status,
                },
            },
            "payment": {
                "entity": {
                    "id": payment_id,
                    "entity": "payment",
                    "order_id": "order_test123",
                    "amount": 6800,
                    "currency": "INR",
                    "status": "captured",
                    "captured": True,
                    "amount_refunded": amount_refunded,
                    "refund_status": (
                        "full" if amount_refunded == 6800
                        else "partial" if amount_refunded > 0
                        else None
                    ),
                },
            },
        },
    }

    return json.dumps(payload).encode("utf-8")


def _prepare_captured_webhook_payment(client, monkeypatch, email):
    customer, order, created, _ = prepare_payment_for_verification(
        client,
        monkeypatch,
        email,
    )

    with database.get_connection() as connection:
        with connection:
            connection.execute(
                """
                UPDATE payments
                SET status = 'captured',
                    razorpay_payment_id = 'pay_test123'
                WHERE id = ?
                """,
                (created["payment_id"],),
            )

    monkeypatch.setattr(
        main,
        "verify_webhook_signature",
        Mock(return_value=True),
    )

    return customer, order, created


def test_partial_refund_webhook_updates_cumulative_amount(
    client,
    monkeypatch,
):
    _, order, created = _prepare_captured_webhook_payment(
        client, monkeypatch, "refund-partial@example.com"
    )

    raw_body = _build_razorpay_refund_webhook_payload(
        "refund.processed",
        "processed",
        amount_refunded=2500,
        refund_amount=2500,
    )

    response = _send_razorpay_webhook(
        client, raw_body, event_id="refund-partial-event"
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert response.json()["payment_updated"] is True
    assert response.json()["payment_status"] == "captured"
    assert response.json()["amount_refunded_paise"] == 2500

    payment = get_payment_record(created["payment_id"])
    assert payment["status"] == "captured"
    assert payment["amount_refunded_paise"] == 2500


def test_full_refund_webhook_marks_payment_refunded(
    client,
    monkeypatch,
):
    _, order, created = _prepare_captured_webhook_payment(
        client, monkeypatch, "refund-full@example.com"
    )

    raw_body = _build_razorpay_refund_webhook_payload(
        "refund.processed",
        "processed",
        amount_refunded=6800,
        refund_amount=6800,
        refund_id="rfnd_full123",
    )

    response = _send_razorpay_webhook(
        client, raw_body, event_id="refund-full-event"
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert response.json()["payment_status"] == "refunded"
    assert response.json()["amount_refunded_paise"] == 6800

    payment = get_payment_record(created["payment_id"])
    assert payment["status"] == "refunded"
    assert payment["amount_refunded_paise"] == 6800


def test_out_of_order_refund_events_never_reduce_refunded_amount(
    client,
    monkeypatch,
):
    _, order, created = _prepare_captured_webhook_payment(
        client, monkeypatch, "refund-ordering@example.com"
    )

    first_body = _build_razorpay_refund_webhook_payload(
        "refund.processed",
        "processed",
        amount_refunded=3000,
        refund_amount=3000,
        refund_id="rfnd_first123",
    )
    second_body = _build_razorpay_refund_webhook_payload(
        "refund.processed",
        "processed",
        amount_refunded=5000,
        refund_amount=2000,
        refund_id="rfnd_second123",
    )
    older_body = _build_razorpay_refund_webhook_payload(
        "refund.processed",
        "processed",
        amount_refunded=3000,
        refund_amount=3000,
        refund_id="rfnd_first123",
    )

    first = _send_razorpay_webhook(
        client, first_body, event_id="refund-order-event-1"
    )
    second = _send_razorpay_webhook(
        client, second_body, event_id="refund-order-event-2"
    )
    older = _send_razorpay_webhook(
        client, older_body, event_id="refund-order-event-3"
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert older.status_code == 200

    payment = get_payment_record(created["payment_id"])
    assert payment["amount_refunded_paise"] == 5000
    assert payment["status"] == "captured"


@pytest.mark.parametrize(
    "event_type, refund_status",
    [
        ("refund.created", "pending"),
        ("refund.failed", "failed"),
        ("refund.speed_changed", "processed"),
    ],
)
def test_non_processed_refund_events_do_not_change_refunded_amount(
    client,
    monkeypatch,
    event_type,
    refund_status,
):
    _, order, created = _prepare_captured_webhook_payment(
        client,
        monkeypatch,
        f"refund-nonprocessed-{event_type.replace('.', '-') }@example.com",
    )

    raw_body = _build_razorpay_refund_webhook_payload(
        event_type,
        refund_status,
        amount_refunded=0,
        refund_amount=2500,
    )

    response = _send_razorpay_webhook(
        client,
        raw_body,
        event_id=f"nonprocessed-{event_type}",
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert response.json()["payment_updated"] is False

    payment = get_payment_record(created["payment_id"])
    assert payment["status"] == "captured"
    assert payment["amount_refunded_paise"] == 0


def test_refund_webhook_rejects_mismatched_payment_id(
    client,
    monkeypatch,
):
    _, order, created = _prepare_captured_webhook_payment(
        client, monkeypatch, "refund-payment-mismatch@example.com"
    )

    raw_body = _build_razorpay_refund_webhook_payload(
        "refund.processed",
        "processed",
        amount_refunded=2500,
        refund_amount=2500,
        refund_payment_id="pay_another123",
    )

    response = _send_razorpay_webhook(
        client, raw_body, event_id="refund-mismatch-event"
    )

    assert response.status_code == 400
    assert "IDs do not match" in response.json()["detail"]
    assert _get_webhook_event_count() == 0

    payment = get_payment_record(created["payment_id"])
    assert payment["amount_refunded_paise"] == 0


def test_duplicate_refund_webhook_event_is_idempotent(
    client,
    monkeypatch,
):
    _, order, created = _prepare_captured_webhook_payment(
        client, monkeypatch, "refund-duplicate@example.com"
    )

    raw_body = _build_razorpay_refund_webhook_payload(
        "refund.processed",
        "processed",
        amount_refunded=2500,
        refund_amount=2500,
    )

    first = _send_razorpay_webhook(
        client, raw_body, event_id="refund-duplicate-event"
    )
    second = _send_razorpay_webhook(
        client, raw_body, event_id="refund-duplicate-event"
    )

    assert first.status_code == 200
    assert first.json()["payment_updated"] is True
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"

    payment = get_payment_record(created["payment_id"])
    assert payment["amount_refunded_paise"] == 2500
    assert _get_webhook_event_count() == 1

@pytest.mark.parametrize(
    "payment_status",
    ["creating", "created", "authorized", "captured"],
)
def test_cancellation_blocked_while_payment_is_active(
    client,
    monkeypatch,
    payment_status,
):
    mock = mock_razorpay(monkeypatch)

    customer, shop_id, order = prepare_customer_order(
        client,
        f"active-payment-{payment_status}@example.com",
    )

    payment_response = create_payment(
        client,
        order["order_reference"],
        customer,
        f"active-{payment_status}-key",
    )

    assert payment_response.status_code == 201

    with database.get_connection() as connection:
        with connection:
            connection.execute(
                """
                UPDATE payments
                SET status = ?
                WHERE id = ?
                """,
                (payment_status, payment_response.json()["payment_id"]),
            )

    shop_headers = payment_test_shop_owner_headers(shop_id)

    cancellation = client.post(
        f"/orders/{order['order_reference']}/cancel?shop_id={shop_id}",
        headers=shop_headers,
    )

    assert cancellation.status_code == 409
    assert "active or captured payment" in cancellation.json()["detail"]

    lookup = client.get(
        f"/orders/{order['order_reference']}?shop_id={shop_id}",
        headers=shop_headers,
    )

    assert lookup.status_code == 200
    assert lookup.json()["status"] == "confirmed"
    assert get_reserved_stock_for_order(shop_id, order) == 1

    mock.assert_called_once()


@pytest.mark.parametrize(
    "terminal_status",
    ["failed", "creation_failed", "refunded"],
)
def test_cancellation_allowed_after_payment_is_terminal(
    client,
    monkeypatch,
    terminal_status,
):
    mock = mock_razorpay(monkeypatch)

    customer, shop_id, order = prepare_customer_order(
        client,
        f"terminal-payment-{terminal_status}@example.com",
    )

    payment_response = create_payment(
        client,
        order["order_reference"],
        customer,
        f"terminal-{terminal_status}-key",
    )

    assert payment_response.status_code == 201

    with database.get_connection() as connection:
        with connection:
            if terminal_status == "refunded":
                connection.execute(
                    """
                    UPDATE payments
                    SET status = 'refunded',
                        amount_refunded_paise = amount_paise
                    WHERE id = ?
                    """,
                    (payment_response.json()["payment_id"],),
                )
            else:
                connection.execute(
                    """
                    UPDATE payments
                    SET status = ?
                    WHERE id = ?
                    """,
                    (
                        terminal_status,
                        payment_response.json()["payment_id"],
                    ),
                )

    shop_headers = payment_test_shop_owner_headers(shop_id)

    cancellation = client.post(
        f"/orders/{order['order_reference']}/cancel?shop_id={shop_id}",
        headers=shop_headers,
    )

    assert cancellation.status_code == 200
    assert cancellation.json()["status"] == "cancelled"
    assert cancellation.json()["stock_released"] is True
    assert get_reserved_stock_for_order(shop_id, order) == 0

    mock.assert_called_once()

def test_payment_creation_and_cancellation_are_serialized(
    client,
    monkeypatch,
):
    mock = mock_razorpay(monkeypatch)

    customer, shop_id, order = prepare_customer_order(
        client,
        "payment-cancellation-race@example.com",
    )

    shop_headers = payment_test_shop_owner_headers(shop_id)
    barrier = threading.Barrier(3)

    def start_payment():
        barrier.wait(timeout=10)
        return create_payment(
            client,
            order["order_reference"],
            customer,
            "concurrent-payment-key",
        )

    def cancel_order():
        barrier.wait(timeout=10)
        return client.post(
            f"/orders/{order['order_reference']}/cancel?shop_id={shop_id}",
            headers=shop_headers,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        payment_future = executor.submit(start_payment)
        cancellation_future = executor.submit(cancel_order)

        # Release both requests at nearly the same time.
        barrier.wait(timeout=10)

        payment_response = payment_future.result(timeout=30)
        cancellation_response = cancellation_future.result(timeout=30)

    payment_status = payment_response.status_code
    cancellation_status = cancellation_response.status_code

    with database.get_connection() as connection:
        final_order = connection.execute(
            """
            SELECT status
            FROM orders
            WHERE id = ?
            """,
            (order["order_id"],),
        ).fetchone()

        payment_rows = connection.execute(
            """
            SELECT status
            FROM payments
            WHERE order_id = ?
            """,
            (order["order_id"],),
        ).fetchall()

    reserved_quantity = get_reserved_stock_for_order(shop_id, order)

    if payment_status == 201:
        # Payment creation won the order-row lock.
        assert cancellation_status == 409
        assert final_order["status"] == "confirmed"
        assert len(payment_rows) == 1
        assert payment_rows[0]["status"] in {"creating", "created"}
        assert reserved_quantity == 1

    elif payment_status == 409:
        # Cancellation won the order-row lock.
        assert cancellation_status == 200
        assert final_order["status"] == "cancelled"
        assert len(payment_rows) == 0
        assert reserved_quantity == 0

    else:
        pytest.fail(
            "Unexpected concurrent result: "
            f"payment={payment_status}, "
            f"cancellation={cancellation_status}, "
            f"payment_body={payment_response.text}, "
            f"cancellation_body={cancellation_response.text}"
        )

    if payment_status == 201:
        mock.assert_called_once()
    else:
        mock.assert_not_called()

    print(
        "PASS: Concurrent payment creation and cancellation were serialized."
    )
