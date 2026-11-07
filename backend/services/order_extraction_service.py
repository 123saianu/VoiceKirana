
import json
import re

from pydantic import ValidationError

from backend.models.order import GroceryOrder


SYSTEM_PROMPT = """
You are the multilingual grocery-order extraction engine for VoiceKirana.

Your task is to extract a customer's grocery request into structured JSON.

IMPORTANT RULES:

1. Understand English, Telugu, Hindi, Kannada, Tamil, Malayalam,
   Bengali, Marathi, Gujarati, Punjabi, Odia, and code-mixed speech.

2. Do not use a predefined grocery catalogue. Customers may request
   any real-world product, regional product, unfamiliar product,
   brand, variant, or spelling.

3. Preserve the customer's original product name in raw_name.
   Keep the original language and script whenever possible.

4. normalized_name is optional. Set it only when the product
   identity is reasonably clear. Never invent a product identity.

5. Extract quantity and unit only when explicitly stated.
   Do not guess missing quantities or units.

6. Preserve brand and variant only when the customer mentions them.

7. If quantity or unit is missing, set needs_clarification to true
   and explain what is missing in clarification_reason.

8. If a product identity is unclear, preserve the spoken name and
   request clarification rather than replacing it with a guess.

9. Identify the intent as one of:
   place_order, modify_order, cancel_order, ask_question, unknown.

10. Do not invent prices, stock, shop availability, or delivery details.

11. Follow the structured response schema supplied by the AI provider.
    Return only the requested structured data. Do not add Markdown,
    code fences, explanations, or text outside the response.
"""


def build_order_extraction_messages(
    transcript: str,
    detected_language_code: str | None = None,
) -> list[dict[str, str]]:
    """
    Build model-ready messages for multilingual grocery extraction.

    This function does not call an AI provider.
    It prepares the request for a future model adapter.
    """

    if not isinstance(transcript, str) or not transcript.strip():
        raise ValueError("A non-empty transcript is required.")

    user_input = {
        "transcript": transcript.strip(),
        "detected_language_code": detected_language_code,
    }

    return [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": json.dumps(
                user_input,
                ensure_ascii=False,
            ),
        },
    ]


def _remove_json_code_fence(response_text: str) -> str:
    """Remove an optional Markdown JSON code fence."""

    text = response_text.strip()

    match = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if match:
        return match.group(1).strip()

    return text


def parse_order_extraction_response(
    response_text: str,
    detected_language_code: str | None = None,
) -> GroceryOrder:
    """
    Validate an AI-generated JSON extraction against GroceryOrder.

    Missing quantities or units automatically require clarification.
    The original raw product name is always preserved.

    This function does not call an AI provider or check inventory.
    """

    if not isinstance(response_text, str) or not response_text.strip():
        raise ValueError("The extraction response is empty.")

    cleaned_response = _remove_json_code_fence(response_text)

    try:
        payload = json.loads(cleaned_response)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "The extraction model did not return valid JSON."
        ) from exc

    if not isinstance(payload, dict):
        raise ValueError("The extraction response must be a JSON object.")

    items = payload.get("items", [])

    if not isinstance(items, list):
        raise ValueError("The extracted items must be a JSON array.")

    clarification_required = False

    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValueError(
                f"Extracted item {index} must be a JSON object."
            )

        raw_name = item.get("raw_name")

        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ValueError(
                f"Extracted item {index} has no valid raw product name."
            )

        item["raw_name"] = raw_name.strip()

        unit = item.get("unit")

        if isinstance(unit, str):
            unit = unit.strip() or None
            item["unit"] = unit

        missing_details = []

        if item.get("quantity") is None:
            missing_details.append("quantity")

        if not item.get("unit"):
            missing_details.append("unit")

        if missing_details:
            item["needs_clarification"] = True

            existing_reason = item.get("clarification_reason")

            if not existing_reason:
                item["clarification_reason"] = (
                    "Please specify the missing "
                    + " and ".join(missing_details)
                    + "."
                )

            clarification_required = True

        if item.get("needs_clarification", False):
            clarification_required = True

    # A place-order intent without identifiable items must not proceed.
    if payload.get("intent") == "place_order" and not items:
        payload["needs_clarification"] = True

        if not payload.get("additional_instructions"):
            payload["additional_instructions"] = (
                "No grocery items were reliably identified. "
                "Please repeat or clarify the request."
            )

        clarification_required = True

    # Trust the language identified by Prisma instead of allowing
    # an extraction model to replace the detected-language metadata.
    if detected_language_code:
        payload["language"] = detected_language_code

    if clarification_required:
        payload["needs_clarification"] = True

    try:
        order = GroceryOrder.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(
            "The extracted grocery order does not match "
            "the VoiceKirana order schema."
        ) from exc

    # GroceryOrder's Pydantic model validator has already propagated
    # item-level clarification flags while preserving order-level flags.
    return order
