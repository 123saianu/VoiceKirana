
import json
from types import SimpleNamespace

import pytest

from backend.services import gemini_extraction_service as gemini


def make_response(items=None):
    payload = {
        "intent": "place_order",
        "language": "en-IN",
        "items": items if items is not None else [
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

    return SimpleNamespace(text=json.dumps(payload))


class FakeModels:

    def __init__(self, response):
        self.response = response
        self.call_arguments = None

    def generate_content(self, **kwargs):
        self.call_arguments = kwargs
        return self.response


class FakeClient:

    def __init__(self, api_key, http_options, response):
        self.api_key = api_key
        self.http_options = http_options
        self.models = FakeModels(response)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_gemini_extracts_and_validates_order(monkeypatch):

    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

    fake_client = FakeClient(
        api_key="test-gemini-key",
        http_options=None,
        response=make_response(),
    )

    def fake_client_factory(**kwargs):
        return fake_client

    monkeypatch.setattr(
        gemini.genai,
        "Client",
        fake_client_factory,
    )

    order = gemini.extract_order_from_transcript(
        "I want 2 kg of rice.",
        detected_language_code="en-IN",
    )

    assert order.intent == "place_order"
    assert order.language == "en-IN"
    assert order.items[0].raw_name == "rice"
    assert order.items[0].quantity == 2
    assert order.items[0].unit == "kg"
    assert order.needs_clarification is False

    arguments = fake_client.models.call_arguments

    assert arguments["model"] == "gemini-3.5-flash-lite"
    assert arguments["config"].response_mime_type == "application/json"
    assert arguments["config"].response_schema is gemini.GeminiGroceryOrder


def test_gemini_preserves_missing_details_as_clarification(monkeypatch):

    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

    fake_client = FakeClient(
        api_key="test-gemini-key",
        http_options=None,
        response=make_response([
            {
                "raw_name": "jaggery",
                "quantity": None,
                "unit": "kg",
            }
        ]),
    )

    monkeypatch.setattr(
        gemini.genai,
        "Client",
        lambda **kwargs: fake_client,
    )

    order = gemini.extract_order_from_transcript(
        "I need jaggery.",
        detected_language_code="en-IN",
    )

    assert order.items[0].raw_name == "jaggery"
    assert order.items[0].needs_clarification is True
    assert order.needs_clarification is True


def test_gemini_uses_detected_language(monkeypatch):

    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

    fake_client = FakeClient(
        api_key="test-gemini-key",
        http_options=None,
        response=make_response(),
    )

    monkeypatch.setattr(
        gemini.genai,
        "Client",
        lambda **kwargs: fake_client,
    )

    order = gemini.extract_order_from_transcript(
        "నాకు రెండు కేజీల బియ్యం కావాలి.",
        detected_language_code="te-IN",
    )

    assert order.language == "te-IN"


def test_gemini_requires_api_key(monkeypatch):

    monkeypatch.setenv("GEMINI_API_KEY", "")

    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        gemini.extract_order_from_transcript(
            "I want rice.",
            detected_language_code="en-IN",
        )


def test_gemini_rejects_empty_transcript(monkeypatch):

    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

    with pytest.raises(ValueError, match="non-empty transcript"):
        gemini.extract_order_from_transcript(
            "   ",
            detected_language_code="en-IN",
        )


def test_gemini_reports_empty_model_response(monkeypatch):

    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

    fake_client = FakeClient(
        api_key="test-gemini-key",
        http_options=None,
        response=SimpleNamespace(text=None),
    )

    monkeypatch.setattr(
        gemini.genai,
        "Client",
        lambda **kwargs: fake_client,
    )

    with pytest.raises(RuntimeError, match="empty structured extraction"):
        gemini.extract_order_from_transcript(
            "I want rice.",
            detected_language_code="en-IN",
        )



def test_gemini_provider_schema_avoids_exclusive_minimum():
    schema = gemini.GeminiGroceryOrder.model_json_schema()
    assert "exclusiveMinimum" not in json.dumps(schema)


def test_gemini_retries_once_after_temporary_server_error(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

    class TemporaryServerError(Exception):
        code = 503

    class SequenceModels:
        def __init__(self):
            self.call_count = 0

        def generate_content(self, **kwargs):
            self.call_count += 1

            if self.call_count == 1:
                raise TemporaryServerError("Temporary overload")

            return make_response()

    fake_client = FakeClient(
        api_key="test-gemini-key",
        http_options=None,
        response=make_response(),
    )

    sequence_models = SequenceModels()
    fake_client.models = sequence_models

    monkeypatch.setattr(
        gemini.genai,
        "Client",
        lambda **kwargs: fake_client,
    )
    monkeypatch.setattr(gemini.time, "sleep", lambda delay: None)

    order = gemini.extract_order_from_transcript(
        "I want 2 kg of rice.",
        detected_language_code="en-IN",
    )

    assert order.items[0].raw_name == "rice"
    assert sequence_models.call_count == 2


def test_gemini_does_not_retry_nontransient_error(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

    class InvalidRequestError(Exception):
        code = 400

    class FailingModels:
        def __init__(self):
            self.call_count = 0

        def generate_content(self, **kwargs):
            self.call_count += 1
            raise InvalidRequestError("Invalid request")

    fake_client = FakeClient(
        api_key="test-gemini-key",
        http_options=None,
        response=make_response(),
    )

    failing_models = FailingModels()
    fake_client.models = failing_models

    monkeypatch.setattr(
        gemini.genai,
        "Client",
        lambda **kwargs: fake_client,
    )
    monkeypatch.setattr(gemini.time, "sleep", lambda delay: None)

    with pytest.raises(
        RuntimeError,
        match="Gemini rejected the extraction request",
    ):
        gemini.extract_order_from_transcript(
            "I want rice.",
            detected_language_code="en-IN",
        )

    assert failing_models.call_count == 1
