from unittest.mock import Mock
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

import backend.main as main

from backend.services import twilio_whatsapp_service as whatsapp
from backend.services.twilio_whatsapp_service import (
    TwilioConfigurationError,
)


TEST_AUTH_TOKEN = "VoiceKirana-Twilio-Test-Auth-Token"
TEST_WEBHOOK_URL = (
    "https://example.ngrok-free.dev/webhooks/twilio/whatsapp"
)


@pytest.fixture
def configured_twilio(monkeypatch):
    monkeypatch.setenv(
        "TWILIO_AUTH_TOKEN",
        TEST_AUTH_TOKEN,
    )
    monkeypatch.setenv(
        "TWILIO_WEBHOOK_URL",
        TEST_WEBHOOK_URL,
    )


@pytest.fixture
def client():
    with TestClient(main.app) as test_client:
        yield test_client


def make_valid_signature(parameters):
    validator = RequestValidator(TEST_AUTH_TOKEN)
    return validator.compute_signature(
        TEST_WEBHOOK_URL,
        parameters,
    )


def test_valid_twilio_signature_is_accepted(
    configured_twilio,
):
    parameters = {
        "MessageSid": "SM123456789",
        "From": "whatsapp:+919999999999",
        "To": "whatsapp:+14155238886",
        "Body": "Hi",
    }

    signature = make_valid_signature(parameters)

    assert whatsapp.validate_twilio_webhook(
        signature,
        parameters,
    ) is True


def test_modified_message_invalidates_signature(
    configured_twilio,
):
    original_parameters = {
        "MessageSid": "SM123456789",
        "From": "whatsapp:+919999999999",
        "To": "whatsapp:+14155238886",
        "Body": "Hi",
    }

    signature = make_valid_signature(original_parameters)

    modified_parameters = {
        **original_parameters,
        "Body": "Send me free groceries",
    }

    assert whatsapp.validate_twilio_webhook(
        signature,
        modified_parameters,
    ) is False


def test_missing_signature_is_rejected(
    configured_twilio,
):
    assert whatsapp.validate_twilio_webhook(
        "",
        {"Body": "Hi"},
    ) is False


def test_missing_auth_token_raises_configuration_error(
    monkeypatch,
):
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("TWILIO_WEBHOOK_URL", TEST_WEBHOOK_URL)

    with pytest.raises(TwilioConfigurationError):
        whatsapp.validate_twilio_webhook(
            "test-signature",
            {"Body": "Hi"},
        )


def test_missing_webhook_url_raises_configuration_error(
    monkeypatch,
):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", TEST_AUTH_TOKEN)
    monkeypatch.delenv("TWILIO_WEBHOOK_URL", raising=False)

    with pytest.raises(TwilioConfigurationError):
        whatsapp.validate_twilio_webhook(
            "test-signature",
            {"Body": "Hi"},
        )


def test_non_https_webhook_url_is_rejected(
    monkeypatch,
):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", TEST_AUTH_TOKEN)
    monkeypatch.setenv(
        "TWILIO_WEBHOOK_URL",
        "http://example.com/webhook",
    )

    with pytest.raises(TwilioConfigurationError):
        whatsapp.get_twilio_webhook_settings()


@pytest.mark.parametrize(
    "message",
    ["Hi", "hello", "HEY", "namaste"],
)
def test_greeting_receives_voicekirana_welcome(message):
    twiml = whatsapp.build_whatsapp_reply(message)

    root = ET.fromstring(twiml)

    assert root.tag == "Response"
    assert "Welcome to VoiceKirana" in root.findtext("Message")


def test_help_message_receives_help_response():
    twiml = whatsapp.build_whatsapp_reply("help")

    root = ET.fromstring(twiml)

    assert "grocery list" in root.findtext("Message").lower()


def test_other_message_receives_default_response():
    twiml = whatsapp.build_whatsapp_reply(
        "I need rice and oil"
    )

    root = ET.fromstring(twiml)

    assert "message has been received" in (
        root.findtext("Message").lower()
    )


def test_valid_incoming_webhook_returns_twiml(
    client,
    monkeypatch,
):
    monkeypatch.setattr(
        main,
        "validate_twilio_webhook",
        lambda signature, parameters: True,
    )

    response = client.post(
        "/webhooks/twilio/whatsapp",
        data={
            "MessageSid": "SM123456789",
            "From": "whatsapp:+919999999999",
            "To": "whatsapp:+14155238886",
            "Body": "Hi",
        },
        headers={
            "X-Twilio-Signature": "test-signature",
        },
    )

    assert response.status_code == 200
    assert "application/xml" in response.headers["content-type"]

    root = ET.fromstring(response.text)

    assert root.tag == "Response"
    assert "Welcome to VoiceKirana" in root.findtext("Message")


def test_invalid_incoming_webhook_is_rejected(
    client,
    monkeypatch,
):
    monkeypatch.setattr(
        main,
        "validate_twilio_webhook",
        lambda signature, parameters: False,
    )

    response = client.post(
        "/webhooks/twilio/whatsapp",
        data={
            "From": "whatsapp:+919999999999",
            "Body": "Hi",
        },
        headers={
            "X-Twilio-Signature": "invalid-signature",
        },
    )

    assert response.status_code == 403


def test_missing_twilio_configuration_returns_503(
    client,
    monkeypatch,
):
    monkeypatch.setattr(
        main,
        "validate_twilio_webhook",
        Mock(
            side_effect=TwilioConfigurationError(
                "Test configuration error"
            )
        ),
    )

    response = client.post(
        "/webhooks/twilio/whatsapp",
        data={
            "From": "whatsapp:+919999999999",
            "Body": "Hi",
        },
        headers={
            "X-Twilio-Signature": "test-signature",
        },
    )

    assert response.status_code == 503


def test_non_whatsapp_sender_is_rejected(
    client,
    monkeypatch,
):
    monkeypatch.setattr(
        main,
        "validate_twilio_webhook",
        lambda signature, parameters: True,
    )

    response = client.post(
        "/webhooks/twilio/whatsapp",
        data={
            "From": "+919999999999",
            "Body": "Hi",
        },
        headers={
            "X-Twilio-Signature": "test-signature",
        },
    )

    assert response.status_code == 400


def test_oversized_incoming_message_is_rejected(
    client,
    monkeypatch,
):
    monkeypatch.setattr(
        main,
        "validate_twilio_webhook",
        lambda signature, parameters: True,
    )

    response = client.post(
        "/webhooks/twilio/whatsapp",
        data={
            "From": "whatsapp:+919999999999",
            "Body": "A" * 1601,
        },
        headers={
            "X-Twilio-Signature": "test-signature",
        },
    )

    assert response.status_code == 413
