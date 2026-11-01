
import base64
import json

import pytest

from backend.services import timbre_service


def test_rejects_empty_text():
    with pytest.raises(ValueError):
        timbre_service.synthesize_speech("   ")


def test_rejects_missing_api_key(monkeypatch):
    monkeypatch.delenv("GNANI_API_KEY_ID", raising=False)

    with pytest.raises(timbre_service.TimbreError):
        timbre_service.synthesize_speech("Hello")


def test_synthesizes_audio_from_mocked_sse(monkeypatch):
    audio_bytes = b"fake-mp3-audio"
    encoded = base64.b64encode(audio_bytes).decode("ascii")

    class FakeResponse:
        text = (
            "event: start\n"
            "data: {}\n\n"
            f"event: chunk\ndata: {encoded}\n\n"
            'event: completed\ndata: {"status":"success"}\n\n'
        )

        def raise_for_status(self):
            pass

    def fake_post(*args, **kwargs):
        return FakeResponse()

    monkeypatch.setenv("GNANI_API_KEY_ID", "test-key-id")
    monkeypatch.setattr(timbre_service.requests, "post", fake_post)

    result = timbre_service.synthesize_speech("Hello")

    assert result == audio_bytes


def test_http_error_is_converted_to_timbre_error(monkeypatch):
    import requests

    class FakeResponse:
        def raise_for_status(self):
            raise requests.HTTPError("503 Service Unavailable")

    monkeypatch.setenv("GNANI_API_KEY_ID", "test-key-id")
    monkeypatch.setattr(
        timbre_service.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(),
    )

    with pytest.raises(
        timbre_service.TimbreError,
        match="TTS request failed",
    ):
        timbre_service.synthesize_speech("Hello")


def test_network_error_is_converted_to_timbre_error(monkeypatch):
    import requests

    def fake_post(*args, **kwargs):
        raise requests.ConnectionError("Network unavailable")

    monkeypatch.setenv("GNANI_API_KEY_ID", "test-key-id")
    monkeypatch.setattr(
        timbre_service.requests,
        "post",
        fake_post,
    )

    with pytest.raises(
        timbre_service.TimbreError,
        match="TTS request failed",
    ):
        timbre_service.synthesize_speech("Hello")


def test_tts_error_event_is_rejected(monkeypatch):
    class FakeResponse:
        text = (
            "event: error\n"
            'data: {"message":"Synthesis failed"}\n\n'
        )

        def raise_for_status(self):
            pass

    monkeypatch.setenv("GNANI_API_KEY_ID", "test-key-id")
    monkeypatch.setattr(
        timbre_service.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(),
    )

    with pytest.raises(
        timbre_service.TimbreError,
        match="TTS returned an error event",
    ):
        timbre_service.synthesize_speech("Hello")


def test_empty_audio_response_is_rejected(monkeypatch):
    class FakeResponse:
        text = (
            "event: start\n"
            "data: {}\n\n"
            "event: completed\n"
            'data: {"status":"success"}\n\n'
        )

        def raise_for_status(self):
            pass

    monkeypatch.setenv("GNANI_API_KEY_ID", "test-key-id")
    monkeypatch.setattr(
        timbre_service.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(),
    )

    with pytest.raises(
        timbre_service.TimbreError,
        match="TTS response contained no audio",
    ):
        timbre_service.synthesize_speech("Hello")


def test_synthesizes_audio_from_json_audio_chunk(monkeypatch):
    import base64

    audio_bytes = b"json-audio-chunk"
    encoded = base64.b64encode(audio_bytes).decode("ascii")

    class FakeResponse:
        text = (
            "event: chunk\n"
            f'data: {{"audio": "{encoded}"}}\n\n'
        )

        def raise_for_status(self):
            pass

    monkeypatch.setenv("GNANI_API_KEY_ID", "test-key-id")
    monkeypatch.setattr(
        timbre_service.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(),
    )

    result = timbre_service.synthesize_speech("Hello")

    assert result == audio_bytes


def test_rejects_invalid_base64_audio_chunk(monkeypatch):
    class FakeResponse:
        text = (
            "event: chunk\n"
            "data: !!!not-valid-base64!!!\n\n"
        )

        def raise_for_status(self):
            pass

    monkeypatch.setenv("GNANI_API_KEY_ID", "test-key-id")
    monkeypatch.setattr(
        timbre_service.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(),
    )

    with pytest.raises(
        timbre_service.TimbreError,
        match="Could not decode TTS audio response",
    ):
        timbre_service.synthesize_speech("Hello")
