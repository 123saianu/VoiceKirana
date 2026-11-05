
import os
import hashlib
import hmac
import re

import requests


RAZORPAY_ORDERS_URL = "https://api.razorpay.com/v1/orders"
RAZORPAY_PAYMENTS_URL = "https://api.razorpay.com/v1/payments"
RAZORPAY_TIMEOUT = (5, 20)


class RazorpayConfigurationError(RuntimeError):
    """Raised when Razorpay credentials or mode are invalid."""


class RazorpayAPIError(RuntimeError):
    """Raised when Razorpay rejects an API request or is unavailable."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def get_razorpay_credentials() -> tuple[str, str]:
    """Retrieve Razorpay credentials from environment variables."""

    key_id = os.getenv("RAZORPAY_KEY_ID", "").strip()
    key_secret = os.getenv("RAZORPAY_KEY_SECRET", "").strip()
    mode = os.getenv("RAZORPAY_MODE", "test").strip().lower()

    if not key_id or not key_secret:
        raise RazorpayConfigurationError(
            "Razorpay API credentials are not configured."
        )

    if mode not in {"test", "live"}:
        raise RazorpayConfigurationError(
            "RAZORPAY_MODE must be either 'test' or 'live'."
        )

    expected_prefix = "rzp_test_" if mode == "test" else "rzp_live_"

    if not key_id.startswith(expected_prefix):
        raise RazorpayConfigurationError(
            f"The Razorpay Key ID does not match the configured {mode} mode."
        )

    return key_id, key_secret


def get_razorpay_public_key_id() -> str:
    """Return the public Key ID for frontend Checkout initialization."""

    key_id, _ = get_razorpay_credentials()
    return key_id


def create_razorpay_order(
    amount_paise: int,
    receipt: str,
    notes: dict | None = None,
) -> dict:
    """
    Create a Razorpay Order using server-side credentials.

    Amounts are always INR paise. The caller must calculate the
    amount from the saved VoiceKirana order, never from frontend input.
    """

    if type(amount_paise) is not int or amount_paise < 100:
        raise ValueError(
            "Payment amount must be an integer of at least 100 paise."
        )

    if (
        not isinstance(receipt, str)
        or not receipt.strip()
        or len(receipt) > 40
    ):
        raise ValueError(
            "Receipt must contain between 1 and 40 characters."
        )

    receipt = receipt.strip()

    if notes is not None:
        if not isinstance(notes, dict) or len(notes) > 15:
            raise ValueError("Notes must be an object with at most 15 entries.")

        for key, value in notes.items():
            if (
                not isinstance(key, str)
                or not isinstance(value, str)
                or len(key) > 256
                or len(value) > 256
            ):
                raise ValueError(
                    "Razorpay note keys and values must be strings "
                    "of at most 256 characters."
                )

    key_id, key_secret = get_razorpay_credentials()

    payload = {
        "amount": amount_paise,
        "currency": "INR",
        "receipt": receipt,
        "notes": notes or {},
    }

    try:
        response = requests.post(
            RAZORPAY_ORDERS_URL,
            auth=(key_id, key_secret),
            json=payload,
            timeout=RAZORPAY_TIMEOUT,
        )

    except requests.Timeout as exc:
        raise RazorpayAPIError(
            "Razorpay request timed out. Payment status must be checked "
            "before attempting another payment order."
        ) from exc

    except requests.RequestException as exc:
        raise RazorpayAPIError(
            "Could not connect to Razorpay. Check network connectivity "
            "before retrying."
        ) from exc

    try:
        response_data = response.json()
    except ValueError as exc:
        raise RazorpayAPIError(
            "Razorpay returned an invalid response.",
            response.status_code,
        ) from exc

    if not response.ok:
        error_data = response_data.get("error", {})
        description = error_data.get(
            "description",
            "Razorpay rejected the order request.",
        )

        raise RazorpayAPIError(
            str(description)[:300],
            response.status_code,
        )

    if not isinstance(response_data, dict):
        raise RazorpayAPIError("Invalid Razorpay order response.")

    razorpay_order_id = response_data.get("id")

    if (
        not isinstance(razorpay_order_id, str)
        or not razorpay_order_id.startswith("order_")
    ):
        raise RazorpayAPIError(
            "Razorpay returned an invalid order identifier."
        )

    if (
        response_data.get("amount") != amount_paise
        or response_data.get("currency") != "INR"
        or response_data.get("status") != "created"
    ):
        raise RazorpayAPIError(
            "Razorpay returned order details that do not match "
            "the requested amount and currency."
        )

    return {
        "razorpay_order_id": razorpay_order_id,
        "amount_paise": amount_paise,
        "currency": "INR",
        "receipt": receipt,
        "status": "created",
        "key_id": key_id,
    }

def verify_payment_signature(
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str,
) -> bool:
    """
    Verify Razorpay Checkout's payment signature.

    IMPORTANT:
    razorpay_order_id must be retrieved from our database,
    not trusted from the customer's request.
    """

    if not all(
        isinstance(value, str) and value.strip()
        for value in (
            razorpay_order_id,
            razorpay_payment_id,
            razorpay_signature,
        )
    ):
        return False

    if not re.fullmatch(r"[0-9a-fA-F]{64}", razorpay_signature):
        return False

    _, key_secret = get_razorpay_credentials()

    message = (
        f"{razorpay_order_id}|{razorpay_payment_id}"
    ).encode("utf-8")

    expected_signature = hmac.new(
        key_secret.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(
        expected_signature,
        razorpay_signature.lower(),
    )

def verify_webhook_signature(
    raw_body: bytes,
    webhook_signature: str,
) -> bool:
    """
    Verify Razorpay's webhook signature against the ORIGINAL raw body.

    Never reconstruct the JSON body before verification.
    """

    if not isinstance(raw_body, bytes):
        return False

    if not isinstance(webhook_signature, str):
        return False

    if not re.fullmatch(
        r"[0-9a-fA-F]{64}",
        webhook_signature,
    ):
        return False

    webhook_secret = os.getenv(
        "RAZORPAY_WEBHOOK_SECRET",
        "",
    ).strip()

    if not webhook_secret:
        raise RazorpayConfigurationError(
            "Razorpay webhook secret is not configured."
        )

    expected_signature = hmac.new(
        webhook_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(
        expected_signature,
        webhook_signature.lower(),
    )

def fetch_razorpay_payment(payment_id: str) -> dict:
    """
    Fetch payment details directly from Razorpay.

    The returned status is trusted only after the API response
    has been authenticated and validated.
    """

    if (
        not isinstance(payment_id, str)
        or not re.fullmatch(r"pay_[A-Za-z0-9]+", payment_id)
    ):
        raise ValueError("Invalid Razorpay payment ID.")

    key_id, key_secret = get_razorpay_credentials()

    try:
        response = requests.get(
            f"{RAZORPAY_PAYMENTS_URL}/{payment_id}",
            auth=(key_id, key_secret),
            timeout=RAZORPAY_TIMEOUT,
        )

    except requests.Timeout as exc:
        raise RazorpayAPIError(
            "Razorpay payment status request timed out."
        ) from exc

    except requests.RequestException as exc:
        raise RazorpayAPIError(
            "Could not retrieve payment details from Razorpay."
        ) from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise RazorpayAPIError(
            "Razorpay returned an invalid payment response.",
            response.status_code,
        ) from exc

    if not response.ok:
        error_data = data.get("error", {}) if isinstance(data, dict) else {}
        description = error_data.get(
            "description",
            "Could not retrieve Razorpay payment.",
        )

        raise RazorpayAPIError(
            str(description)[:300],
            response.status_code,
        )

    if not isinstance(data, dict):
        raise RazorpayAPIError("Invalid Razorpay payment response.")

    if data.get("id") != payment_id:
        raise RazorpayAPIError("Razorpay payment ID mismatch.")

    order_id = data.get("order_id")

    if (
        not isinstance(order_id, str)
        or not order_id.startswith("order_")
    ):
        raise RazorpayAPIError("Invalid Razorpay order ID in payment response.")

    amount = data.get("amount")

    if type(amount) is not int or amount <= 0:
        raise RazorpayAPIError("Invalid payment amount from Razorpay.")

    currency = data.get("currency")

    if currency != "INR":
        raise RazorpayAPIError("Unexpected payment currency.")

    payment_status = data.get("status")

    if payment_status not in {
        "created",
        "authorized",
        "captured",
        "refunded",
        "failed",
    }:
        raise RazorpayAPIError("Unknown Razorpay payment status.")

    return {
        "razorpay_payment_id": payment_id,
        "razorpay_order_id": order_id,
        "amount_paise": amount,
        "currency": currency,
        "status": payment_status,
        "captured": data.get("captured") is True,
    }

