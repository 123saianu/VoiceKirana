
import json

import pytest

from backend.services.order_extraction_service import (
    build_order_extraction_messages,
    parse_order_extraction_response,
)


def test_build_extraction_messages_preserves_telugu_transcript():
    transcript = "అన్నా నాకు రెండు కేజీల బియ్యం కావాలి."

    messages = build_order_extraction_messages(
        transcript,
        detected_language_code="te-IN",
    )

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"

    user_data = json.loads(messages[1]["content"])

    assert user_data["transcript"] == transcript
    assert user_data["detected_language_code"] == "te-IN"


def test_build_extraction_messages_rejects_empty_transcript():
    with pytest.raises(ValueError):
        build_order_extraction_messages("  ")


def test_parse_complete_grocery_order():
    response = {
        "intent": "place_order",
        "language": "en-IN",
        "items": [
            {
                "raw_name": "rice",
                "normalized_name": "Rice",
                "quantity": 2,
                "unit": "kg",
                "needs_clarification": False,
            }
        ],
        "needs_clarification": False,
    }

    order = parse_order_extraction_response(
        json.dumps(response),
        detected_language_code="en-IN",
    )

    assert order.intent == "place_order"
    assert order.items[0].raw_name == "rice"
    assert order.items[0].quantity == 2
    assert order.items[0].unit == "kg"
    assert order.needs_clarification is False


def test_detected_language_overrides_model_language():
    response = {
        "intent": "place_order",
        "language": "en-IN",
        "items": [],
    }

    order = parse_order_extraction_response(
        json.dumps(response),
        detected_language_code="te-IN",
    )

    assert order.language == "te-IN"


def test_missing_quantity_requires_clarification():
    response = {
        "intent": "place_order",
        "items": [
            {
                "raw_name": "jaggery",
                "quantity": None,
                "unit": "kg",
            }
        ],
    }

    order = parse_order_extraction_response(
        json.dumps(response),
        detected_language_code="en-IN",
    )

    assert order.items[0].needs_clarification is True
    assert "quantity" in order.items[0].clarification_reason.lower()
    assert order.needs_clarification is True


def test_missing_unit_requires_clarification():
    response = {
        "intent": "place_order",
        "items": [
            {
                "raw_name": "rice",
                "quantity": 2,
                "unit": None,
            }
        ],
    }

    order = parse_order_extraction_response(json.dumps(response))

    assert order.items[0].needs_clarification is True
    assert "unit" in order.items[0].clarification_reason.lower()
    assert order.needs_clarification is True


def test_unfamiliar_regional_product_name_is_preserved():
    response = {
        "intent": "place_order",
        "items": [
            {
                "raw_name": "పోపు గింజలు",
                "normalized_name": None,
                "quantity": 1,
                "unit": "packet",
            }
        ],
    }

    order = parse_order_extraction_response(json.dumps(response))

    assert order.items[0].raw_name == "పోపు గింజలు"
    assert order.items[0].normalized_name is None
    assert order.needs_clarification is False


def test_existing_item_clarification_propagates_to_order():
    response = {
        "intent": "place_order",
        "items": [
            {
                "raw_name": "jaggery",
                "quantity": 1,
                "unit": "packet",
                "needs_clarification": True,
                "clarification_reason": "Please confirm the type.",
            }
        ],
    }

    order = parse_order_extraction_response(json.dumps(response))

    assert order.items[0].needs_clarification is True
    assert order.needs_clarification is True


def test_place_order_without_items_requires_clarification():
    response = {
        "intent": "place_order",
        "items": [],
    }

    order = parse_order_extraction_response(json.dumps(response))

    assert order.needs_clarification is True
    assert order.additional_instructions


def test_invalid_json_is_rejected():
    with pytest.raises(ValueError, match="valid JSON"):
        parse_order_extraction_response("not valid JSON")


def test_missing_raw_product_name_is_rejected():
    response = {
        "intent": "place_order",
        "items": [
            {
                "quantity": 2,
                "unit": "kg",
            }
        ],
    }

    with pytest.raises(ValueError, match="raw product name"):
        parse_order_extraction_response(json.dumps(response))


def test_markdown_json_fence_is_supported():
    response = '''```json
{
  "intent": "place_order",
  "items": [
    {
      "raw_name": "rice",
      "quantity": 2,
      "unit": "kg"
    }
  ]
}
```'''

    order = parse_order_extraction_response(response)

    assert order.items[0].raw_name == "rice"
    assert order.items[0].quantity == 2
