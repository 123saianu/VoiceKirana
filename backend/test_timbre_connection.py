
import os
import json
import base64
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY_ID = os.getenv("GNANI_API_KEY_ID")
URL = "https://api.vachana.ai/api/v1/tts/sse"


def main():
    if not API_KEY_ID:
        raise RuntimeError("GNANI_API_KEY_ID is missing from .env")

    payload = {
        "audio_config": {
            "bitrate": "192k",
            "container": "mp3",
            "encoding": "linear_pcm",
            "num_channels": 1,
            "sample_rate": 44100,
            "sample_width": 2,
        },
        "model": "timbre-v3.0",
        "text": "Namaste! Your VoiceKirana test is working.",
        "voice": "Avinash",
    }

    headers = {
        "X-API-Key-ID": API_KEY_ID,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    response = requests.post(
        URL, json=payload, headers=headers, timeout=60
    )

    print("HTTP status:", response.status_code)
    response.raise_for_status()

    audio_chunks = []
    current_event = None
    data_lines = []

    def process_event(event_name, lines):
        if not event_name or not lines:
            return

        data = "\n".join(lines)

        if event_name == "error":
            raise RuntimeError("TTS API returned an error: " + data)

        if event_name not in ("chunk", "audio_chunk"):
            return

        # Some API versions return JSON with an "audio" field.
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            parsed = None

        if isinstance(parsed, dict):
            encoded_audio = parsed.get("audio", "")
            if encoded_audio:
                audio_chunks.append(base64.b64decode(encoded_audio))
        else:
            # Some versions return the base64 audio directly.
            audio_chunks.append(base64.b64decode(data))

    for line in response.text.splitlines() + [""]:
        if line.startswith("event:"):
            current_event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())
        elif line == "":
            process_event(current_event, data_lines)
            current_event = None
            data_lines = []

    if not audio_chunks:
        print("No audio chunks were found.")
        print("Response preview:", response.text[:1000])
        return

    audio_bytes = b"".join(audio_chunks)

    output_dir = Path("data")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "tts_sample.mp3"
    output_file.write_bytes(audio_bytes)

    print("Audio chunks:", len(audio_chunks))
    print("Audio size:", len(audio_bytes), "bytes")
    print("Saved audio to:", output_file.resolve())


if __name__ == "__main__":
    main()

