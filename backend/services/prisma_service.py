
import os
import requests

from dotenv import load_dotenv

# Load credentials from the project's .env file
load_dotenv()

GNANI_API_KEY_ID = os.getenv("GNANI_API_KEY_ID")

GNANI_STT_URL = "https://api.vachana.ai/stt/v3"


def transcribe_audio(audio_path: str, language_code: str = "te-IN") -> str:
    """
    Convert a Telugu or Telugu-English audio recording into text
    using Gnani Prisma Speech-to-Text.
    """

    if not GNANI_API_KEY_ID:
        raise ValueError(
            "Gnani API key is missing. Check GNANI_API_KEY_ID in your .env file."
        )

    if not os.path.isfile(audio_path):
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    headers = {
        "X-API-Key-ID": GNANI_API_KEY_ID
    }

    data = {
        "language_code": language_code,
        "format": "transcribe"
    }

    try:
        with open(audio_path, "rb") as audio_file:
            files = {
                "audio_file": (
                    os.path.basename(audio_path),
                    audio_file
                )
            }

            response = requests.post(
                GNANI_STT_URL,
                headers=headers,
                data=data,
                files=files,
                timeout=60
            )

        response.raise_for_status()

        result = response.json()

        if not result.get("success"):
            raise RuntimeError(f"Gnani transcription failed: {result}")

        transcript = result.get("transcript", "").strip()

        if not transcript:
            raise RuntimeError("Gnani returned an empty transcript.")

        return transcript

    except requests.exceptions.Timeout:
        raise RuntimeError("Gnani API request timed out. Please try again.")

    except requests.exceptions.HTTPError:
        raise RuntimeError(
            f"Gnani API error ({response.status_code}): {response.text}"
        )

    except requests.exceptions.RequestException as error:
        raise RuntimeError(f"Could not connect to Gnani: {error}")
