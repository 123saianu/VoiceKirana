
import os

from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse


class TwilioConfigurationError(RuntimeError):
    """Raised when required Twilio webhook configuration is missing."""


def get_twilio_webhook_settings() -> tuple[str, str]:
    """Load the Auth Token and exact public webhook URL."""

    auth_token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    webhook_url = os.getenv("TWILIO_WEBHOOK_URL", "").strip()

    if not auth_token:
        raise TwilioConfigurationError(
            "TWILIO_AUTH_TOKEN is not configured."
        )

    if not webhook_url:
        raise TwilioConfigurationError(
            "TWILIO_WEBHOOK_URL is not configured."
        )

    if not webhook_url.startswith("https://"):
        raise TwilioConfigurationError(
            "TWILIO_WEBHOOK_URL must use HTTPS."
        )

    return auth_token, webhook_url


def validate_twilio_webhook(
    signature: str,
    parameters: dict,
) -> bool:
    """
    Verify that an incoming form webhook was signed by Twilio.

    Use the externally configured HTTPS URL rather than request.url,
    because VoiceKirana is running behind an ngrok reverse proxy.
    """

    if not isinstance(signature, str) or not signature:
        return False

    if not isinstance(parameters, dict):
        return False

    auth_token, webhook_url = get_twilio_webhook_settings()

    validator = RequestValidator(auth_token)

    return validator.validate(
        webhook_url,
        parameters,
        signature,
    )


def build_whatsapp_reply(incoming_message: str) -> str:
    """Build a basic TwiML reply for the initial Sandbox integration."""

    response = MessagingResponse()

    message = (
        incoming_message.strip().casefold()
        if isinstance(incoming_message, str)
        else ""
    )

    if message in {"hi", "hello", "hey", "namaste"}:
        reply = (
            "Hello! Welcome to VoiceKirana. "
            "You can send your grocery list in English or Telugu. "
            "For example: 2 kg rice and 1 litre sunflower oil."
        )

    elif message == "help":
        reply = (
            "Welcome to VoiceKirana WhatsApp! "
            "Send a grocery list to get started. "
            "Our multilingual ordering connection is being set up."
        )

    else:
        reply = (
            "Thank you for messaging VoiceKirana! "
            "Your message has been received. "
            "Our WhatsApp grocery-ordering assistant is being connected."
        )

    response.message(reply)

    return str(response)
