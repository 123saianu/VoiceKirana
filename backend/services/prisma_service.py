import logging
import os

import requests
from dotenv import load_dotenv

# Load credentials from the project's .env file.
load_dotenv()

logger = logging.getLogger(__name__)

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
        raise FileNotFoundError("Audio file not found.")

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

        try:
            result = response.json()
        except ValueError:
            logger.warning(
                "Gnani STT returned invalid JSON (HTTP %s).",
                response.status_code
            )
            raise RuntimeError(
                "Gnani returned an invalid response. Please try again."
            ) from None

        if not isinstance(result, dict):
            logger.warning("Gnani STT returned an unexpected response format.")
            raise RuntimeError(
                "Gnani returned an invalid response. Please try again."
            )

        if result.get("success") is not True:
            logger.warning(
                "Gnani STT reported an unsuccessful transcription (HTTP %s).",
                response.status_code
            )
            raise RuntimeError(
                "Gnani transcription failed. Please try again."
            )

        transcript = result.get("transcript")

        if not isinstance(transcript, str) or not transcript.strip():
            raise RuntimeError("Gnani returned an empty transcript.")

        return transcript.strip()

    except requests.exceptions.Timeout:
        logger.warning("Gnani STT request timed out.")
        raise RuntimeError(
            "Gnani API request timed out. Please try again."
        ) from None

    except requests.exceptions.HTTPError:
        status_code = getattr(response, "status_code", "unknown")

        logger.warning(
            "Gnani STT HTTP request failed (status=%s).",
            status_code
        )

        raise RuntimeError(
            f"Gnani API request failed (HTTP {status_code}). Please try again."
        ) from None

    except requests.exceptions.RequestException as error:
        logger.warning(
            "Gnani STT connection failed (%s).",
            type(error).__name__
        )

        raise RuntimeError(
            "Could not connect to Gnani. Please try again."
        ) from None