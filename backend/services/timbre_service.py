

import base64
import json
import os

import requests
from dotenv import load_dotenv

load_dotenv()

TTS_URL = "https://api.vachana.ai/api/v1/tts/sse"
DEFAULT_MODEL = "timbre-v3.0"
DEFAULT_VOICE = "Avinash"


class TimbreError(RuntimeError):
    """Raised when speech synthesis fails."""


def synthesize_speech(
    text: str,
    voice: str = DEFAULT_VOICE,
    model: str = DEFAULT_MODEL,
) -> bytes:
    """Convert text into MP3 audio bytes using Gnani Timbre."""

    if not isinstance(text, str) or not text.strip():
        raise ValueError("Text to synthesize cannot be empty.")

    api_key_id = os.getenv("GNANI_API_KEY_ID")
    if not api_key_id:
        raise TimbreError("GNANI_API_KEY_ID is missing from .env.")

    payload = {
        "audio_config": {
            "bitrate": "192k",
            "container": "mp3",
            "encoding": "linear_pcm",
            "num_channels": 1,
            "sample_rate": 44100,
            "sample_width": 2,
        },
        "model": model,
        "text": text.strip(),
        "voice": voice,
    }

    headers = {
        "X-API-Key-ID": api_key_id,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    try:
        response = requests.post(
            TTS_URL,
            json=payload,
            headers=headers,
            timeout=60,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise TimbreError("TTS request failed.") from exc

    audio_chunks = []
    event_name = None
    data_lines = []

    def process_event(name, lines):
        if not name or not lines:
            return

        data = "\n".join(lines)

        if name == "error":
            raise TimbreError("TTS returned an error event.")

        if name not in ("chunk", "audio_chunk"):
            return

        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            parsed = None

        if isinstance(parsed, dict):
            encoded = parsed.get("audio")
            if encoded:
                audio_chunks.append(base64.b64decode(encoded))
        else:
            audio_chunks.append(base64.b64decode(data))

    try:
        for line in response.text.splitlines() + [""]:
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].lstrip())
            elif line == "":
                process_event(event_name, data_lines)
                event_name = None
                data_lines = []
    except (ValueError, json.JSONDecodeError) as exc:
        raise TimbreError("Could not decode TTS audio response.") from exc

    audio = b"".join(audio_chunks)
    if not audio:
        raise TimbreError("TTS response contained no audio.")

    return audio
