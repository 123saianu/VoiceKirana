
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
