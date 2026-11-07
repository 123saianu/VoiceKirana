import logging
import os
import time
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from backend.models.order import GroceryOrder
from backend.services.order_extraction_service import (
    build_order_extraction_messages,
    parse_order_extraction_response,
)


# Gemini-specific output schema.
# Do not add gt=0 here: it generates exclusiveMinimum,
# which the installed Google GenAI SDK rejects.
# The original GroceryOrder parser enforces positive quantities.

class GeminiRequestedItem(BaseModel):
    raw_name: str
    normalized_name: str | None = None
    quantity: float | None = None
    unit: str | None = None
    brand: str | None = None
    variant: str | None = None
    needs_clarification: bool = False
    clarification_reason: str | None = None


class GeminiGroceryOrder(BaseModel):
    intent: Literal[
        "place_order",
        "modify_order",
        "cancel_order",
        "ask_question",
        "unknown",
    ] = "unknown"

    language: str | None = None
    items: list[GeminiRequestedItem] = Field(default_factory=list)
    additional_instructions: str | None = None
    needs_clarification: bool = False


# Resolve .env relative to the project, not the caller's working frame.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=False)

logger = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-3.5-flash-lite"
REQUEST_TIMEOUT_MS = 120_000
MAX_TRANSIENT_RETRIES = 1
RETRYABLE_STATUS_CODES = frozenset({500, 503, 504})
RETRY_DELAY_SECONDS = 1.0


def extract_order_from_transcript(
    transcript: str,
    detected_language_code: str | None = None,
) -> GroceryOrder:
    """
    Convert a multilingual transcript into a validated GroceryOrder.

    Gemini returns structured JSON using the provider-compatible schema.
    VoiceKirana's original parser then enforces business validation.

    This function does not check inventory or save an order.
    """

    api_key = os.getenv("GEMINI_API_KEY", "").strip()

    if not api_key:
        raise ValueError(
            "GEMINI_API_KEY is missing from the .env file."
        )

    messages = build_order_extraction_messages(
        transcript=transcript,
        detected_language_code=detected_language_code,
    )

    try:
        with genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=REQUEST_TIMEOUT_MS
            ),
        ) as client:
            for attempt in range(MAX_TRANSIENT_RETRIES + 1):
                try:
                    response = client.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=messages[1]["content"],
                        config=types.GenerateContentConfig(
                            system_instruction=messages[0]["content"],
                            response_mime_type="application/json",
                            response_schema=GeminiGroceryOrder,
                            max_output_tokens=1500,
                        ),
                    )
                    break

                except Exception as request_exc:
                    request_status = getattr(
                        request_exc, "code", None
                    )

                    if (
                        request_status not in RETRYABLE_STATUS_CODES
                        or attempt >= MAX_TRANSIENT_RETRIES
                    ):
                        raise

                    logger.warning(
                        "Gemini temporary server error (status=%s); "
                        "retrying once.",
                        request_status,
                    )

                    time.sleep(
                        RETRY_DELAY_SECONDS * (2 ** attempt)
                    )
    except Exception as exc:
        status_code = getattr(exc, "code", None)

        logger.warning(
            "Gemini extraction request failed (status=%s, error=%s).",
            status_code,
            type(exc).__name__,
        )

        error_messages = {
            400: "Gemini rejected the extraction request.",
            403: "Gemini access denied. Check API key permissions.",
            404: "The configured Gemini model was not found.",
            429: "Gemini quota or rate limit reached.",
            500: "Gemini encountered a server error.",
            503: "Gemini is temporarily unavailable.",
            504: "Gemini request deadline exceeded. Please retry.",
        }

        message = error_messages.get(
            status_code,
            "Gemini extraction request failed. Please retry.",
        )

        raise RuntimeError(message) from None

    response_text = response.text

    if not isinstance(response_text, str) or not response_text.strip():
        raise RuntimeError(
            "Gemini returned an empty structured extraction."
        )

    try:
        order = parse_order_extraction_response(
            response_text=response_text,
            detected_language_code=detected_language_code,
        )

    except (ValueError, TypeError) as exc:
        logger.warning(
            "Gemini response failed VoiceKirana order validation."
        )

        raise RuntimeError(
            "Gemini returned an extraction that failed "
            "VoiceKirana validation. Please review the request."
        ) from exc

    return order
