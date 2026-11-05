import hashlib
import hmac
import pytest
import requests

from unittest.mock import Mock, patch

from backend.services import razorpay_service as razorpay


TEST_KEY_ID = "rzp_test_voicekirana123"
TEST_KEY_SECRET = "test-secret-not-real"


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 300

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload

        return self.payload


@pytest.fixture(autouse=True)
def configure_test_credentials(monkeypatch):
    monkeypatch.setenv("RAZORPAY_MODE", "test")
    monkeypatch.setenv("RAZORPAY_KEY_ID", TEST_KEY_ID)
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", TEST_KEY_SECRET)


def successful_payload(
    amount=6800,
    order_id="order_test_123456",
):
    return {
        "id": order_id,
        "amount": amount,
        "currency": "INR",
        "receipt": "VK-TEST-001",
        "status": "created",
    }


def test_razorpay_order_creation_success():
    response = FakeResponse(successful_payload())

    with patch.object(
        razorpay.requests,
        "post",
        return_value=response,
    ) as mock_post:

        result = razorpay.create_razorpay_order(
            amount_paise=6800,
            receipt="VK-TEST-001",
        )

    assert result["razorpay_order_id"] == "order_test_123456"
    assert result["amount_paise"] == 6800
    assert result["currency"] == "INR"
    assert result["status"] == "created"
    assert result["key_id"] == TEST_KEY_ID

    mock_post.assert_called_once()

    args, kwargs = mock_post.call_args

    assert args[0] == razorpay.RAZORPAY_ORDERS_URL
    assert kwargs["auth"] == (TEST_KEY_ID, TEST_KEY_SECRET)
    assert kwargs["json"]["amount"] == 6800
    assert kwargs["json"]["currency"] == "INR"
    assert kwargs["timeout"] == razorpay.RAZORPAY_TIMEOUT

    print("PASS: Razorpay order creation")


@pytest.mark.parametrize(
    "amount",
    [0, 1, 99, -100, True, 6800.0],
)
def test_invalid_payment_amount_is_rejected(amount):
    with patch.object(razorpay.requests, "post") as mock_post:
        with pytest.raises(ValueError):
            razorpay.create_razorpay_order(
                amount_paise=amount,
                receipt="VK-TEST-001",
            )

    mock_post.assert_not_called()

    print("PASS: Invalid payment amount rejected")


@pytest.mark.parametrize(
    "receipt",
    ["", " " * 2, "R" * 41, None],
)
def test_invalid_receipt_is_rejected(receipt):
    with patch.object(razorpay.requests, "post") as mock_post:
        with pytest.raises(ValueError):
            razorpay.create_razorpay_order(
                amount_paise=6800,
                receipt=receipt,
            )

    mock_post.assert_not_called()

    print("PASS: Invalid receipt rejected")


def test_missing_razorpay_credentials_are_rejected(monkeypatch):
    monkeypatch.delenv("RAZORPAY_KEY_ID")
    monkeypatch.delenv("RAZORPAY_KEY_SECRET")

    with patch.object(razorpay.requests, "post") as mock_post:
        with pytest.raises(razorpay.RazorpayConfigurationError):
            razorpay.create_razorpay_order(
                6800,
                "VK-TEST-001",
            )

    mock_post.assert_not_called()

    print("PASS: Missing credentials rejected")


def test_test_mode_rejects_live_key(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_wrongkey")

    with patch.object(razorpay.requests, "post") as mock_post:
        with pytest.raises(razorpay.RazorpayConfigurationError):
            razorpay.create_razorpay_order(
                6800,
                "VK-TEST-001",
            )

    mock_post.assert_not_called()

    print("PASS: Live key rejected in test mode")


def test_invalid_razorpay_mode_is_rejected(monkeypatch):
    monkeypatch.setenv("RAZORPAY_MODE", "production")

    with patch.object(razorpay.requests, "post") as mock_post:
        with pytest.raises(razorpay.RazorpayConfigurationError):
            razorpay.create_razorpay_order(
                6800,
                "VK-TEST-001",
            )

    mock_post.assert_not_called()

    print("PASS: Invalid Razorpay mode rejected")


def test_razorpay_api_error_is_handled():
    response = FakeResponse(
        {
            "error": {
                "description": "Invalid credentials",
            }
        },
        status_code=401,
    )

    with patch.object(
        razorpay.requests,
        "post",
        return_value=response,
    ):
        with pytest.raises(razorpay.RazorpayAPIError) as exc:
            razorpay.create_razorpay_order(
                6800,
                "VK-TEST-001",
            )

    assert exc.value.status_code == 401

    print("PASS: Razorpay API rejection handled")


def test_razorpay_timeout_is_handled():
    with patch.object(
        razorpay.requests,
        "post",
        side_effect=requests.Timeout("Test timeout"),
    ) as mock_post:

        with pytest.raises(razorpay.RazorpayAPIError):
            razorpay.create_razorpay_order(
                6800,
                "VK-TEST-001",
            )

    mock_post.assert_called_once()

    print("PASS: Razorpay timeout handled")


def test_razorpay_connection_error_is_handled():
    with patch.object(
        razorpay.requests,
        "post",
        side_effect=requests.ConnectionError("Test connection error"),
    ):
        with pytest.raises(razorpay.RazorpayAPIError):
            razorpay.create_razorpay_order(
                6800,
                "VK-TEST-001",
            )

    print("PASS: Razorpay connection error handled")


def test_invalid_json_response_is_rejected():
    response = FakeResponse(ValueError("Invalid JSON"))

    with patch.object(
        razorpay.requests,
        "post",
        return_value=response,
    ):
        with pytest.raises(razorpay.RazorpayAPIError):
            razorpay.create_razorpay_order(
                6800,
                "VK-TEST-001",
            )

    print("PASS: Invalid Razorpay JSON rejected")


def test_mismatched_amount_is_rejected():
    response = FakeResponse(successful_payload(amount=5000))

    with patch.object(
        razorpay.requests,
        "post",
        return_value=response,
    ):
        with pytest.raises(razorpay.RazorpayAPIError):
            razorpay.create_razorpay_order(
                6800,
                "VK-TEST-001",
            )

    print("PASS: Mismatched Razorpay amount rejected")


def test_invalid_razorpay_order_id_is_rejected():
    response = FakeResponse(
        successful_payload(order_id="invalid_order_id")
    )

    with patch.object(
        razorpay.requests,
        "post",
        return_value=response,
    ):
        with pytest.raises(razorpay.RazorpayAPIError):
            razorpay.create_razorpay_order(
                6800,
                "VK-TEST-001",
            )

    print("PASS: Invalid Razorpay order ID rejected")


def test_invalid_notes_are_rejected():
    with patch.object(razorpay.requests, "post") as mock_post:
        with pytest.raises(ValueError):
            razorpay.create_razorpay_order(
                6800,
                "VK-TEST-001",
                notes={"invalid": 123},
            )

    mock_post.assert_not_called()

    print("PASS: Invalid Razorpay notes rejected")

def test_valid_payment_signature():
    order_id = "order_test_123456"
    payment_id = "pay_test_123456"

    signature = hmac.new(
        TEST_KEY_SECRET.encode(),
        f"{order_id}|{payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()

    assert razorpay.verify_payment_signature(
        order_id,
        payment_id,
        signature,
    )

    print("PASS: Valid Checkout signature accepted")


def test_tampered_payment_id_rejected():
    order_id = "order_test_123456"
    payment_id = "pay_test_123456"

    signature = hmac.new(
        TEST_KEY_SECRET.encode(),
        f"{order_id}|{payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()

    assert not razorpay.verify_payment_signature(
        order_id,
        "pay_tampered",
        signature,
    )

    print("PASS: Tampered payment ID rejected")


def test_tampered_order_id_rejected():
    signature = hmac.new(
        TEST_KEY_SECRET.encode(),
        b"order_test_123456|pay_test_123456",
        hashlib.sha256,
    ).hexdigest()

    assert not razorpay.verify_payment_signature(
        "order_tampered",
        "pay_test_123456",
        signature,
    )

    print("PASS: Tampered order ID rejected")


def test_wrong_payment_secret_rejected(monkeypatch):
    signature = hmac.new(
        TEST_KEY_SECRET.encode(),
        b"order_test_123456|pay_test_123456",
        hashlib.sha256,
    ).hexdigest()

    monkeypatch.setenv(
        "RAZORPAY_KEY_SECRET",
        "different-test-secret",
    )

    assert not razorpay.verify_payment_signature(
        "order_test_123456",
        "pay_test_123456",
        signature,
    )

    print("PASS: Incorrect API secret rejected")


def test_malformed_payment_signature_rejected():
    assert not razorpay.verify_payment_signature(
        "order_test_123456",
        "pay_test_123456",
        "not-a-valid-signature",
    )

    print("PASS: Malformed Checkout signature rejected")


def test_valid_webhook_signature(monkeypatch):
    raw_body = b'{"event":"payment.captured","test":true}'
    webhook_secret = "test-webhook-secret"

    monkeypatch.setenv(
        "RAZORPAY_WEBHOOK_SECRET",
        webhook_secret,
    )

    signature = hmac.new(
        webhook_secret.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    assert razorpay.verify_webhook_signature(
        raw_body,
        signature,
    )

    print("PASS: Valid webhook signature accepted")


def test_modified_webhook_body_rejected(monkeypatch):
    raw_body = b'{"event":"payment.captured"}'
    webhook_secret = "test-webhook-secret"

    monkeypatch.setenv(
        "RAZORPAY_WEBHOOK_SECRET",
        webhook_secret,
    )

    signature = hmac.new(
        webhook_secret.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    assert not razorpay.verify_webhook_signature(
        b'{"event":"payment.failed"}',
        signature,
    )

    print("PASS: Modified webhook body rejected")


def test_wrong_webhook_secret_rejected(monkeypatch):
    raw_body = b'{"event":"payment.captured"}'

    signature = hmac.new(
        b"original-secret",
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    monkeypatch.setenv(
        "RAZORPAY_WEBHOOK_SECRET",
        "different-secret",
    )

    assert not razorpay.verify_webhook_signature(
        raw_body,
        signature,
    )

    print("PASS: Incorrect webhook secret rejected")


def test_missing_webhook_secret_rejected(monkeypatch):
    monkeypatch.delenv(
        "RAZORPAY_WEBHOOK_SECRET",
        raising=False,
    )

    with pytest.raises(razorpay.RazorpayConfigurationError):
        razorpay.verify_webhook_signature(
            b'{"event":"payment.captured"}',
            "a" * 64,
        )

    print("PASS: Missing webhook secret rejected")


def test_webhook_requires_raw_bytes(monkeypatch):
    monkeypatch.setenv(
        "RAZORPAY_WEBHOOK_SECRET",
        "test-webhook-secret",
    )

    assert not razorpay.verify_webhook_signature(
        '{"event":"payment.captured"}',
        "a" * 64,
    )

    print("PASS: Parsed string body rejected")

def test_fetch_razorpay_payment_success():
    response = FakeResponse({
        "id": "pay_test123",
        "order_id": "order_test123",
        "amount": 6800,
        "currency": "INR",
        "status": "captured",
        "captured": True,
    })

    with patch.object(
        razorpay.requests,
        "get",
        return_value=response,
    ) as mock_get:
        result = razorpay.fetch_razorpay_payment("pay_test123")

    assert result["razorpay_payment_id"] == "pay_test123"
    assert result["razorpay_order_id"] == "order_test123"
    assert result["amount_paise"] == 6800
    assert result["currency"] == "INR"
    assert result["status"] == "captured"
    assert result["captured"] is True

    args, kwargs = mock_get.call_args

    assert args[0] == (
        f"{razorpay.RAZORPAY_PAYMENTS_URL}/pay_test123"
    )
    assert kwargs["auth"] == (TEST_KEY_ID, TEST_KEY_SECRET)
    assert kwargs["timeout"] == razorpay.RAZORPAY_TIMEOUT

    print("PASS: Razorpay payment details fetched")


@pytest.mark.parametrize(
    "payment_id",
    ["", "invalid", "pay_test/123", "../payment"],
)
def test_invalid_payment_id_rejected(payment_id):
    with patch.object(razorpay.requests, "get") as mock_get:
        with pytest.raises(ValueError):
            razorpay.fetch_razorpay_payment(payment_id)

    mock_get.assert_not_called()

    print("PASS: Invalid payment ID rejected")


def test_fetch_payment_api_error():
    response = FakeResponse(
        {"error": {"description": "Payment not found"}},
        status_code=404,
    )

    with patch.object(
        razorpay.requests,
        "get",
        return_value=response,
    ):
        with pytest.raises(razorpay.RazorpayAPIError) as exc:
            razorpay.fetch_razorpay_payment("pay_test123")

    assert exc.value.status_code == 404

    print("PASS: Payment API error handled")


def test_fetch_payment_timeout():
    with patch.object(
        razorpay.requests,
        "get",
        side_effect=requests.Timeout("Test timeout"),
    ):
        with pytest.raises(razorpay.RazorpayAPIError):
            razorpay.fetch_razorpay_payment("pay_test123")

    print("PASS: Payment fetch timeout handled")


def test_fetch_payment_id_mismatch():
    response = FakeResponse({
        "id": "pay_different123",
        "order_id": "order_test123",
        "amount": 6800,
        "currency": "INR",
        "status": "captured",
        "captured": True,
    })

    with patch.object(
        razorpay.requests,
        "get",
        return_value=response,
    ):
        with pytest.raises(razorpay.RazorpayAPIError):
            razorpay.fetch_razorpay_payment("pay_test123")

    print("PASS: Mismatched payment ID rejected")
