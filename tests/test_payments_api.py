
from contextlib import closing
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

import backend.database as database
import backend.main as main

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


def prepare_customer_order(client, email="buyer@example.com"):
    """Create an authenticated customer and a pending rice order."""

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

    return customer, shop_id, response.json()


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


def test_non_pending_order_cannot_be_paid(
    client,
    monkeypatch,
):
    mock = mock_razorpay(monkeypatch)

    customer, shop_id, order = prepare_customer_order(client)

    with database.get_connection() as connection:
        with connection:
            connection.execute(
                "UPDATE orders SET status = 'confirmed' WHERE id = ?",
                (order["order_id"],),
            )

    response = create_payment(
        client,
        order["order_reference"],
        customer,
    )

    assert response.status_code == 409
    mock.assert_not_called()

    print("PASS: Non-pending order cannot be paid")


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

    assert saved_order["status"] == "pending"

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

    assert saved_order["status"] == "pending"
    assert _get_webhook_event_count() == 1

    print("PASS: Captured webhook updates payment without accepting grocery order")


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
