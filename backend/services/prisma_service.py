
import logging
import os
from dataclasses import dataclass

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

GNANI_API_KEY_ID = os.getenv("GNANI_API_KEY_ID")
GNANI_STT_URL = "https://api.vachana.ai/stt/v3"

PRISMA_MODEL = "prisma-v3.0"
AUTO_LANGUAGE_CODE = "indic-auto"
DEFAULT_FORMAT = "transcribe"


@dataclass(frozen=True)
class TranscriptionResult:
    """Transcript and metadata returned by Gnani Prisma."""

    transcript: str
    detected_language_code: str | None
    request_id: str | None


def transcribe_audio_with_metadata(
    audio_path: str,
    language_code: str = AUTO_LANGUAGE_CODE,
) -> TranscriptionResult:
    """
    Transcribe audio using Prisma v3.0.

    By default, indic-auto enables automatic identification across
    Prisma's supported Indian languages and English.

    A specific BCP-47 language code can still be supplied when needed.
    """

    if not GNANI_API_KEY_ID:
        raise ValueError(
            "Gnani API key is missing. Check GNANI_API_KEY_ID in your .env file."
        )

    if not os.path.isfile(audio_path):
        raise FileNotFoundError("Audio file not found.")

    headers = {
        "X-API-Key-ID": GNANI_API_KEY_ID,
    }

    # Prisma v3.0 is opt-in. Both model and language_code
    # must be sent to enable automatic language identification.
    data = {
        "model": PRISMA_MODEL,
        "language_code": language_code,
        "format": DEFAULT_FORMAT,
    }

    try:
        with open(audio_path, "rb") as audio_file:
            files = {
                "audio_file": (
                    os.path.basename(audio_path),
                    audio_file,
                )
            }

            response = requests.post(
                GNANI_STT_URL,
                headers=headers,
                data=data,
                files=files,
                timeout=60,
            )

        response.raise_for_status()

        try:
            result = response.json()
        except ValueError:
            logger.warning(
                "Gnani STT returned invalid JSON (HTTP %s).",
                response.status_code,
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
                "Gnani STT reported unsuccessful transcription (HTTP %s).",
                response.status_code,
            )
            raise RuntimeError(
                "Gnani transcription failed. Please try again."
            )

        transcript = result.get("transcript")

        if not isinstance(transcript, str) or not transcript.strip():
            raise RuntimeError("Gnani returned an empty transcript.")

        detected_language = result.get("detected_language_code")
        if not isinstance(detected_language, str) or not detected_language.strip():
            detected_language = None
        else:
            detected_language = detected_language.strip()

        request_id = result.get("request_id")
        if not isinstance(request_id, str) or not request_id.strip():
            request_id = None
        else:
            request_id = request_id.strip()

        return TranscriptionResult(
            transcript=transcript.strip(),
            detected_language_code=detected_language,
            request_id=request_id,
        )

    except requests.exceptions.Timeout:
        logger.warning("Gnani STT request timed out.")
        raise RuntimeError(
            "Gnani API request timed out. Please try again."
        ) from None

    except requests.exceptions.HTTPError:
        status_code = getattr(response, "status_code", "unknown")

        logger.warning(
            "Gnani STT HTTP request failed (status=%s).",
            status_code,
        )

        message = (
            f"Gnani API request failed (HTTP {status_code})."
        )

        if status_code in (400, 403, 404):
            message += (
                " Verify that Prisma v3.0 is available "
                "for this API key."
            )

        raise RuntimeError(
            message + " Please try again."
        ) from None

    except requests.exceptions.RequestException as error:
        logger.warning(
            "Gnani STT connection failed (%s).",
            type(error).__name__,
        )

        raise RuntimeError(
            "Could not connect to Gnani. Please try again."
        ) from None


def transcribe_audio(
    audio_path: str,
    language_code: str = AUTO_LANGUAGE_CODE,
) -> str:
    """
    Backward-compatible helper that returns only the transcript.

    Existing callers do not need to handle metadata.
    """

    result = transcribe_audio_with_metadata(
        audio_path,
        language_code=language_code,
    )

    return result.transcript
