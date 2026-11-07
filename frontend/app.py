
import os
import sys
import tempfile
from pathlib import Path
import uuid

import requests
import streamlit as st
from dotenv import load_dotenv

# VOICEKIRANA_PROJECT_ROOT_BOOTSTRAP
PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.timbre_service import (
    synthesize_speech,
    TimbreError,
)

from backend.services.prisma_service import (
    transcribe_audio_with_metadata,
)

from backend.services.gemini_extraction_service import (
    extract_order_from_transcript,
)

load_dotenv()

st.set_page_config(
    page_title="VoiceKirana",
    page_icon="🛒",
    layout="centered",
)

# ---------------------------------------------------------
# VoiceKirana visual styling
# ---------------------------------------------------------

st.markdown(
    """
    <style>
    /* Main page */
    .stApp {
        background-color: #f7f9fc;
    }

    .block-container {
        max-width: 900px;
        padding-top: 2rem;
        padding-bottom: 3rem;
    }

    /* Headings */
    h1 {
        color: #14532d;
        font-weight: 800;
        letter-spacing: -0.8px;
    }

    h2, h3, h4 {
        color: #1f2937;
    }

    /* Buttons */
    .stButton > button {
        min-height: 42px;
        border-radius: 10px;
        font-weight: 600;
        transition: border-color 0.2s ease;
    }

    .stButton > button[kind="primary"] {
        background-color: #15803d;
        border-color: #15803d;
        color: white;
    }

    .stButton > button[kind="primary"]:hover {
        background-color: #166534;
        border-color: #166534;
        color: white;
    }

    /* Form fields */
    .stTextInput input,
    .stNumberInput input,
    .stSelectbox div[data-baseweb="select"] {
        border-radius: 8px;
    }

    /* Status messages and expandable order cards */
    div[data-testid="stAlert"] {
        border-radius: 10px;
    }

    details[data-testid="stExpander"] {
        border-radius: 10px;
        border: 1px solid #e5e7eb;
        background-color: white;
    }

    /* Readable body text and captions */
    .stApp,
    .stApp p,
    .stApp label,
    .stApp [data-testid="stCaptionContainer"] {
        color: #263238;
    }

    /* Make information and status messages readable */
    div[data-testid="stAlert"] p {
        color: #263238 !important;
    }

    /* Reduce excessive vertical spacing */
    .block-container {
        gap: 0.75rem;
    }

    [data-testid="stVerticalBlock"] {
        gap: 0.75rem;
    }

    /* Keep controls usable on smaller screens */
    @media (max-width: 640px) {
        .block-container {
            padding-top: 1rem;
            padding-left: 1rem;
            padding-right: 1rem;
        }

        h1 {
            font-size: 1.8rem;
        }

        .stButton > button {
            min-height: 44px;
        }
    }

    /* Make primary button text white and readable */
    .stButton > button[kind="primary"],
    .stButton > button[kind="primary"] p {
        color: #ffffff !important;
        font-weight: 700;
    }

    /* Make secondary button text readable */
    .stButton > button:not([kind="primary"]),
    .stButton > button:not([kind="primary"]) p {
        color: #f9fafb !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🛒 VoiceKirana")
st.subheader("Your multilingual grocery assistant")
st.write(
    "Build your grocery cart, check shop inventory, "
    "review estimated prices, and hear the response."
)
st.caption(
    "Order in English, Telugu, Hindi, or a mixture of languages. "
    "Inventory checks do not reserve stock."
)

backend_url = os.getenv(
    "BACKEND_URL",
    "http://127.0.0.1:8001",
).rstrip("/")

api_token = os.getenv("VOICEKIRANA_API_TOKEN")

# ---------------------------------------------------------
# Cart state
# ---------------------------------------------------------

if "cart_item_ids" not in st.session_state:
    st.session_state.cart_item_ids = [0]

if "next_cart_item_id" not in st.session_state:
    st.session_state.next_cart_item_id = 1

if "cart_result" not in st.session_state:
    st.session_state.cart_result = None

# Persist the latest successful inventory preview across Streamlit reruns.
if "pending_order_payload" not in st.session_state:
    st.session_state.pending_order_payload = None

if "pending_shop_id" not in st.session_state:
    st.session_state.pending_shop_id = None

if "pending_idempotency_key" not in st.session_state:
    st.session_state.pending_idempotency_key = None

if "saved_order_result" not in st.session_state:
    st.session_state.saved_order_result = None


def add_cart_item():
    """Add a new, empty product row."""
    new_id = st.session_state.next_cart_item_id
    st.session_state.next_cart_item_id += 1
    st.session_state.cart_item_ids.append(new_id)


def remove_cart_item(item_id):
    """Remove a product row and its widget values."""
    if len(st.session_state.cart_item_ids) <= 1:
        return

    st.session_state.cart_item_ids.remove(item_id)

    for field in (
        "name",
        "normalized",
        "quantity",
        "unit",
        "brand",
        "variant",
        "needs_clarification",
        "clarification_reason",
        "clarification_resolved",
    ):
        key = f"cart_{item_id}_{field}"
        if key in st.session_state:
            del st.session_state[key]


st.divider()

# VOICEKIRANA_MULTILINGUAL_MICROPHONE
# ---------------------------------------------------------
# Multilingual microphone input ? Gnani Prisma Auto-LID
# ---------------------------------------------------------

VOICE_LANGUAGE_NAMES = {
    "en-IN": "English",
    "hi-IN": "Hindi",
    "bn-IN": "Bengali",
    "gu-IN": "Gujarati",
    "kn-IN": "Kannada",
    "ml-IN": "Malayalam",
    "mr-IN": "Marathi",
    "pa-IN": "Punjabi",
    "ta-IN": "Tamil",
    "te-IN": "Telugu",
}

VOICE_DEFAULTS = {
    "voice_widget_version": 0,
    "voice_transcript": None,
    "voice_detected_language": None,
    "voice_request_id": None,
    "voice_temp_file_deleted": None,
    "voice_temp_cleanup_error": None,
    "voice_transcription_error": None,
    "voice_extraction_error": None,
    "voice_extraction_language": None,
    "voice_order_needs_clarification": False,
    "voice_order_clarification_resolved": False,
}

for key, default in VOICE_DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = default

st.divider()
st.subheader("Speak your grocery request")

st.caption(
    "Speak naturally in Telugu, English, Hindi, or another "
    "supported language. Prisma identifies the language automatically. "
    "You do not need to select a language before recording."
)

voice_audio = st.experimental_audio_input(
    "Record your grocery request",
    key=f"voice_input_{st.session_state.voice_widget_version}",
)

if voice_audio is None:
    st.info("Use the microphone to record your grocery request.")

else:
    voice_audio_bytes = voice_audio.getvalue()

    st.success("Recording received.")
    st.caption(
        f"Audio size: {len(voice_audio_bytes):,} bytes | "
        f"Format: {voice_audio.type}"
    )

    st.audio(voice_audio_bytes, format="audio/wav")

    if st.button(
        "Transcribe voice request",
        type="primary",
        use_container_width=True,
        key="transcribe_voice_request_button",
    ):
        temp_audio_path = None
        transcription_succeeded = False

        st.session_state.voice_transcription_error = None
        st.session_state.voice_temp_cleanup_error = None
        st.session_state.voice_temp_file_deleted = None

        st.session_state.voice_transcript = None
        st.session_state.voice_detected_language = None
        st.session_state.voice_request_id = None

        if "voice_transcript_editor" in st.session_state:
            del st.session_state["voice_transcript_editor"]

        try:
            with tempfile.NamedTemporaryFile(
                suffix=".wav",
                delete=False,
            ) as temp_audio:
                temp_audio.write(voice_audio_bytes)
                temp_audio_path = temp_audio.name

            with st.spinner(
                "Prisma is identifying the language and transcribing..."
            ):
                result = transcribe_audio_with_metadata(temp_audio_path)

            st.session_state.voice_transcript = result.transcript
            st.session_state.voice_detected_language = (
                result.detected_language_code
            )
            st.session_state.voice_request_id = result.request_id

            st.session_state.voice_transcript_editor = result.transcript
            transcription_succeeded = True

        except (ValueError, FileNotFoundError, RuntimeError, OSError) as exc:
            st.session_state.voice_transcription_error = str(exc)

        finally:
            if temp_audio_path is not None:
                try:
                    if os.path.exists(temp_audio_path):
                        os.remove(temp_audio_path)

                    st.session_state.voice_temp_file_deleted = (
                        not os.path.exists(temp_audio_path)
                    )

                except OSError as exc:
                    st.session_state.voice_temp_file_deleted = (
                        not os.path.exists(temp_audio_path)
                    )
                    st.session_state.voice_temp_cleanup_error = str(exc)

        if (
            transcription_succeeded
            and st.session_state.voice_temp_file_deleted is True
        ):
            st.session_state.voice_widget_version += 1

        st.rerun()


# Display the transcript after the microphone widget resets.
if st.session_state.voice_transcript:
    st.markdown("#### Recognized voice request")

    detected_code = st.session_state.voice_detected_language

    if detected_code:
        detected_name = VOICE_LANGUAGE_NAMES.get(
            detected_code,
            "Other supported language",
        )
        st.success(
            f"Detected language: {detected_name} ({detected_code})"
        )
    else:
        st.warning(
            "Transcript received, but Prisma did not return "
            "a detected-language code."
        )

    st.text_area(
        "Review or edit your transcript",
        height=120,
        key="voice_transcript_editor",
    )

    if st.button(
        "Extract items from transcript",
        type="primary",
        use_container_width=True,
        key="extract_voice_order_button",
    ):
        transcript_for_extraction = st.session_state.get(
            "voice_transcript_editor", ""
        ).strip()

        st.session_state.voice_extraction_error = None

        if not transcript_for_extraction:
            st.session_state.voice_extraction_error = (
                "Enter or record a grocery request before extraction."
            )
        else:
            # A new extraction invalidates any previous inventory preview.
            st.session_state.pending_order_payload = None
            st.session_state.pending_shop_id = None
            st.session_state.pending_idempotency_key = None
            st.session_state.saved_order_result = None

            try:
                with st.spinner(
                    "Gemini is identifying products and quantities..."
                ):
                    extracted_order = extract_order_from_transcript(
                        transcript=transcript_for_extraction,
                        detected_language_code=(
                            st.session_state.voice_detected_language
                        ),
                    )

                if extracted_order.intent != "place_order":
                    raise ValueError(
                        "This request was not identified as a new grocery "
                        "order. Edit the transcript and try again."
                    )

                if not extracted_order.items:
                    raise ValueError(
                        "No grocery items were extracted. "
                        "Please edit the transcript and try again."
                    )

                # Remove the previous cart rows and their widget values.
                old_ids = list(st.session_state.cart_item_ids)

                cart_fields = (
                    "name",
                    "normalized",
                    "quantity",
                    "unit",
                    "brand",
                    "variant",
                    "needs_clarification",
                    "clarification_reason",
                    "clarification_resolved",
                )

                for old_id in old_ids:
                    for field in cart_fields:
                        state_key = f"cart_{old_id}_{field}"
                        st.session_state.pop(state_key, None)

                # Allocate fresh IDs so stale Streamlit widget values
                # cannot overwrite the extracted items.
                first_new_id = st.session_state.next_cart_item_id
                new_ids = []

                for index, extracted_item in enumerate(
                    extracted_order.items
                ):
                    item_id = first_new_id + index
                    new_ids.append(item_id)

                    st.session_state[f"cart_{item_id}_name"] = (
                        extracted_item.raw_name
                    )
                    st.session_state[
                        f"cart_{item_id}_normalized"
                    ] = extracted_item.normalized_name or ""
                    st.session_state[
                        f"cart_{item_id}_quantity"
                    ] = (
                        extracted_item.quantity
                        if extracted_item.quantity is not None
                        else 0.0
                    )
                    st.session_state[f"cart_{item_id}_unit"] = (
                        extracted_item.unit or ""
                    )
                    st.session_state[f"cart_{item_id}_brand"] = (
                        extracted_item.brand or ""
                    )
                    st.session_state[f"cart_{item_id}_variant"] = (
                        extracted_item.variant or ""
                    )
                    st.session_state[
                        f"cart_{item_id}_needs_clarification"
                    ] = bool(extracted_item.needs_clarification)
                    st.session_state[
                        f"cart_{item_id}_clarification_reason"
                    ] = extracted_item.clarification_reason or ""
                    st.session_state[
                        f"cart_{item_id}_clarification_resolved"
                    ] = False

                st.session_state.cart_item_ids = new_ids
                st.session_state.next_cart_item_id = (
                    first_new_id + len(new_ids)
                )

                # Preserve language and additional instructions returned
                # by the validated extraction.
                st.session_state.voice_extraction_language = (
                    extracted_order.language
                    or st.session_state.voice_detected_language
                )
                st.session_state["additional_instructions_editor"] = (
                    extracted_order.additional_instructions or ""
                )

                # If order-level clarification is not explained by an
                # individual flagged item, require a separate review.
                has_item_clarification = any(
                    item.needs_clarification
                    for item in extracted_order.items
                )

                st.session_state.voice_order_needs_clarification = (
                    bool(extracted_order.needs_clarification)
                    and not has_item_clarification
                )
                st.session_state.voice_order_clarification_resolved = False

                st.success(
                    f"Extracted {len(new_ids)} grocery item(s). "
                    "Review the cart and resolve any warnings."
                )
                st.rerun()

            except (ValueError, RuntimeError) as exc:
                st.session_state.voice_extraction_error = str(exc)

    if st.session_state.voice_extraction_error:
        st.error(
            "Gemini extraction failed: "
            f"{st.session_state.voice_extraction_error}"
        )

    if st.session_state.voice_request_id:
        st.caption(
            f"Prisma request ID: {st.session_state.voice_request_id}"
        )

    st.info(
        "Transcript ready for review. It has not yet been converted "
        "into cart items, and no order has been placed."
    )

if st.session_state.voice_temp_file_deleted is True:
    st.success("Temporary server-side audio file deleted and verified.")

elif st.session_state.voice_temp_file_deleted is False:
    st.error("Temporary server-side audio file could not be deleted.")

    if st.session_state.voice_temp_cleanup_error:
        st.code(st.session_state.voice_temp_cleanup_error)

if st.session_state.voice_transcription_error:
    st.error(
        "Voice transcription failed: "
        f"{st.session_state.voice_transcription_error}"
    )

st.divider()

st.subheader("🛒 Grocery order")

st.info(
    "Enter an active shop ID from the database connected to "
    "the backend. Shop IDs may differ between database environments. "
    "Product availability depends on the selected shop's inventory."
)

shop_id = st.number_input(
    "Shop ID",
    min_value=1,
    value=6,
    step=1,
    key="shop_id",
    help=(
        "Enter the ID of an active shop in the database "
        "currently connected to the backend."
    ),
)

language = st.selectbox(
    "Request language",
    ["English", "Telugu", "Hindi", "Other / unspecified"],
    key="request_language",
)

st.markdown("### Your cart")

# These buttons are outside a form so changes to the cart
# immediately trigger a rerun and update the displayed rows.
st.button(
    "➕ Add item",
    on_click=add_cart_item,
    type="secondary",
    key="add_cart_item_button",
)

for item_id in list(st.session_state.cart_item_ids):
    st.markdown(f"#### Item {item_id + 1}")

    name_col, normalized_col = st.columns(2)

    with name_col:
        st.text_input(
            "Product name",
            value="rice" if item_id == 0 else "",
            placeholder="e.g. rice, milk, dragon fruit",
            key=f"cart_{item_id}_name",
        )

    with normalized_col:
        st.text_input(
            "Standard product name (optional)",
            value="Rice" if item_id == 0 else "",
            placeholder="Optional standardized name",
            key=f"cart_{item_id}_normalized",
        )

    quantity_col, unit_col, remove_col = st.columns([1, 1, 1])

    with quantity_col:
        st.number_input(
            "Quantity",
            min_value=0.0,
            value=2.0 if item_id == 0 else 0.0,
            step=1.0,
            key=f"cart_{item_id}_quantity",
        )

    with unit_col:
        st.text_input(
            "Unit",
            value="kg" if item_id == 0 else "",
            placeholder="e.g. kg, litre, packet",
            key=f"cart_{item_id}_unit",
        )

    with remove_col:
        st.write("")
        st.write("")
        st.button(
            "🗑️ Remove",
            key=f"remove_cart_item_{item_id}",
            disabled=len(st.session_state.cart_item_ids) == 1,
            on_click=remove_cart_item,
            args=(item_id,),
        )

    brand_col, variant_col = st.columns(2)

    with brand_col:
        st.text_input(
            "Brand (optional)",
            placeholder="e.g. Aashirvaad",
            key=f"cart_{item_id}_brand",
        )

    with variant_col:
        st.text_input(
            "Variant (optional)",
            placeholder="e.g. sona masoori",
            key=f"cart_{item_id}_variant",
        )

    if st.session_state.get(
        f"cart_{item_id}_needs_clarification", False
    ):
        reason = st.session_state.get(
            f"cart_{item_id}_clarification_reason", ""
        )

        st.warning(
            "This item needs your review: "
            + (reason or "Please verify the product details.")
        )

        st.checkbox(
            "I have reviewed and resolved this item",
            key=f"cart_{item_id}_clarification_resolved",
        )

    st.divider()

if st.session_state.voice_order_needs_clarification:
    st.warning(
        "Gemini flagged the overall request for review. "
        "Please check the transcript and extracted cart carefully."
    )
    st.checkbox(
        "I have reviewed and resolved the overall request",
        key="voice_order_clarification_resolved",
    )

st.text_area(
    "Additional instructions (optional)",
    height=80,
    key="additional_instructions_editor",
)

submitted = st.button(
    "Validate cart and check inventory",
    type="primary",
    use_container_width=True,
    key="validate_cart_button",
)

if submitted:
    # A new validation attempt invalidates any older confirmation preview.
    st.session_state.pending_order_payload = None
    st.session_state.pending_shop_id = None
    st.session_state.pending_idempotency_key = None
    st.session_state.saved_order_result = None

    if not api_token:
        st.error(
            "VOICEKIRANA_API_TOKEN is missing from your .env file."
        )
        st.stop()

    order_items = []
    validation_errors = []

    for item_number, item_id in enumerate(
        st.session_state.cart_item_ids,
        start=1,
    ):
        name = st.session_state.get(
            f"cart_{item_id}_name", ""
        ).strip()

        normalized = st.session_state.get(
            f"cart_{item_id}_normalized", ""
        ).strip()

        quantity = st.session_state.get(
            f"cart_{item_id}_quantity", 0.0
        )

        unit = st.session_state.get(
            f"cart_{item_id}_unit", ""
        ).strip()

        brand = st.session_state.get(
            f"cart_{item_id}_brand", ""
        ).strip()

        variant = st.session_state.get(
            f"cart_{item_id}_variant", ""
        ).strip()

        needs_clarification = bool(
            st.session_state.get(
                f"cart_{item_id}_needs_clarification", False
            )
        )

        clarification_resolved = bool(
            st.session_state.get(
                f"cart_{item_id}_clarification_resolved", False
            )
        )

        # Ignore a completely untouched empty row.
        if (
            not name
            and not normalized
            and not unit
            and not brand
            and not variant
            and quantity == 0
        ):
            continue

        if needs_clarification and not clarification_resolved:
            reason = st.session_state.get(
                f"cart_{item_id}_clarification_reason", ""
            )
            validation_errors.append(
                f"Item {item_number} ({name or 'unnamed item'}): "
                "resolve the clarification before checking inventory. "
                f"{reason}"
            )
            continue

        if not name:
            validation_errors.append(
                f"Item {item_number}: enter a product name."
            )
            continue

        if quantity <= 0:
            validation_errors.append(
                f"Item {item_number} ({name}): "
                "quantity must be greater than zero."
            )
            continue

        if not unit:
            validation_errors.append(
                f"Item {item_number} ({name}): "
                "enter a unit such as kg or litre."
            )
            continue

        item = {
            "raw_name": name,
            "quantity": quantity,
            "unit": unit,
            "needs_clarification": False,
        }

        if normalized:
            item["normalized_name"] = normalized

        if brand:
            item["brand"] = brand

        if variant:
            item["variant"] = variant

        order_items.append(item)

    if validation_errors:
        for error in validation_errors:
            st.error(error)
        st.stop()

    if not order_items:
        st.warning("Please add at least one grocery item.")
        st.stop()

    order_payload = {
        "intent": "place_order",
        "language": (
            st.session_state.get("voice_extraction_language")
            or (
                None
                if language == "Other / unspecified"
                else language
            )
        ),
        "items": order_items,
        "needs_clarification": False,
    }

    additional_instructions = st.session_state.get(
        "additional_instructions_editor", ""
    ).strip()

    if additional_instructions:
        order_payload["additional_instructions"] = (
            additional_instructions
        )

    if (
        st.session_state.voice_order_needs_clarification
        and not st.session_state.voice_order_clarification_resolved
    ):
        validation_errors.append(
            "Review and resolve the overall order clarification."
        )

    if validation_errors:
        for error in validation_errors:
            st.error(error)
        st.stop()

    headers = {"X-API-Key": api_token}

    try:
        with st.spinner("Validating grocery request..."):
            preview_response = requests.post(
                f"{backend_url}/orders/preview",
                json=order_payload,
                headers=headers,
                timeout=15,
            )

        if not preview_response.ok:
            try:
                detail = preview_response.json().get(
                    "detail",
                    "The backend rejected the request.",
                )
            except ValueError:
                detail = "The backend returned an invalid response."

            st.error(
                f"Validation failed "
                f"({preview_response.status_code}): {detail}"
            )
            st.stop()

        preview = preview_response.json()

        st.subheader("Order validation")
        st.info(preview.get("message", "Request validated."))

        validation_status = preview.get("status", "unknown")
        st.write(
            "**Validation status:**",
            validation_status.replace("_", " ").title()
            if isinstance(validation_status, str)
            else validation_status,
        )

        validated_order = preview.get("order", preview)

        if isinstance(validated_order, dict):
            items = validated_order.get("items", [])

            if items:
                st.markdown("#### Your requested items")

                summary_rows = []

                for item in items:
                    if not isinstance(item, dict):
                        continue

                    product_name = (
                        item.get("raw_name")
                        or item.get("normalized_name")
                        or item.get("name")
                        or "Unknown item"
                    )

                    quantity = item.get("quantity", "—")
                    unit = item.get("unit", "")
                    brand = item.get("brand")

                    summary_rows.append(
                        {
                            "Product": product_name,
                            "Quantity": f"{quantity} {unit}".strip(),
                            "Brand": brand or "Not specified",
                        }
                    )

                if summary_rows:
                    st.dataframe(
                        summary_rows,
                        use_container_width=True,
                        hide_index=True,
                    )
            else:
                st.caption(
                    "The request was validated, but no item summary "
                    "was returned."
                )
        else:
            st.caption("The backend returned an unexpected order format.")

        with st.spinner("Checking shop inventory..."):
            inventory_response = requests.post(
                f"{backend_url}/inventory/check",
                params={"shop_id": int(shop_id)},
                json=order_payload,
                headers=headers,
                timeout=15,
            )

        if not inventory_response.ok:
            try:
                detail = inventory_response.json().get(
                    "detail",
                    "The inventory check failed.",
                )
            except ValueError:
                detail = "The backend returned an invalid response."

            st.error(
                f"Inventory check failed "
                f"({inventory_response.status_code}): {detail}"
            )
            st.stop()

        result = inventory_response.json()

        # Only keep a payload for explicit confirmation when every item
        # passed the inventory check. A preview alone never saves an order.
        if result.get("status") == "inventory_checked":
            st.session_state.pending_order_payload = order_payload
            st.session_state.pending_shop_id = int(shop_id)

            # Generate one key for this confirmed order request.
            st.session_state.pending_idempotency_key = uuid.uuid4().hex
        else:
            st.session_state.pending_order_payload = None
            st.session_state.pending_shop_id = None
            st.session_state.pending_idempotency_key = None

        message = result.get(
            "message",
            "Inventory check completed.",
        )

        st.divider()
        st.subheader("📦 Inventory results")

        inventory_status = result.get("status", "unknown")

        if inventory_status == "inventory_checked":
            st.success("All requested items are available in the required quantities.")
            st.write("**Shop:**", result.get("shop_name", "Unknown"))

            total = result.get("total_rupees")
            if total is not None:
                st.metric("Estimated total", f"₹{total:.2f}")

            st.caption(
                "This is an estimate only. Your order is not saved "
                "until you explicitly confirm it."
            )
        else:
            st.warning(
                message
                or "We couldn't match every item to the shop's inventory. "
                   "Review the items below."
            )

        status_messages = {
            "matched": "Available — matched with shop inventory.",
            "insufficient_stock": (
                "Not enough stock. Reduce the quantity or contact the shop."
            ),
            "unavailable": (
                "This product is unavailable at this shop. "
                "Ask the shop about alternatives."
            ),
            "not_found": (
                "We couldn't find this product. Check the name or ask "
                "the shop to add it to its inventory."
            ),
            "ambiguous": (
                "More than one product may match. Select the correct "
                "product before continuing."
            ),
        }

        for item_result in result.get("items", []):
            item_name = item_result.get("requested_name", "Unknown item")
            item_status = item_result.get("status", "unknown")
            display_status = item_status.replace("_", " ").title()

            with st.expander(
                f"{item_name} — {display_status}",
                expanded=item_status != "matched",
            ):
                st.write(
                    status_messages.get(
                        item_status,
                        item_result.get(
                            "message",
                            "Please review this item with the shop.",
                        ),
                    )
                )

                if item_status == "matched":
                    st.write(
                        f"**Quantity:** {item_result['quantity']} "
                        f"{item_result['unit']}"
                    )
                    st.write(
                        f"**Unit price:** "
                        f"₹{item_result['unit_price_rupees']:.2f}"
                    )
                    st.write(
                        f"**Line total:** "
                        f"₹{item_result['line_total_rupees']:.2f}"
                    )
                    st.write(
                        f"**Available stock:** "
                        f"{item_result['available_quantity']} "
                        f"{item_result['unit']}"
                    )

                elif item_status == "insufficient_stock":
                    st.write(
                        "**Requested quantity:**",
                        item_result.get("requested_quantity", "Unknown"),
                    )
                    st.write(
                        "**Available quantity:**",
                        item_result.get("available_quantity", "Unknown"),
                    )

        st.caption(
            "No stock has been reserved and no order has been placed."
        )

        try:
            with st.spinner("Generating spoken response..."):
                audio_bytes = synthesize_speech(message)

            st.audio(audio_bytes, format="audio/mpeg")

        except TimbreError as exc:
            st.warning(
                "Inventory results were received, but speech generation "
                f"failed: {exc}"
            )

    except requests.RequestException as exc:
        st.error(
            "Could not connect to the backend. Check that FastAPI is "
            f"running at {backend_url}. Details: {exc}"
        )


# ---------------------------------------------------------
# Explicit order confirmation
# ---------------------------------------------------------

st.divider()
st.subheader("✅ Confirm order")

pending_payload = st.session_state.pending_order_payload
pending_shop_id = st.session_state.pending_shop_id

if pending_payload and pending_shop_id:
    st.warning(
        "The inventory preview passed. Nothing has been saved yet. "
        "Confirm below to save a pending order request. Stock will not "
        "be reserved, and shop acceptance is not guaranteed."
    )

    if st.button(
        "Confirm and save order",
        type="primary",
        use_container_width=True,
        key="confirm_and_save_order_button",
    ):
        if not api_token:
            st.error("VOICEKIRANA_API_TOKEN is missing from your .env file.")
        else:
            try:
                with st.spinner("Rechecking inventory and saving order..."):
                    confirm_response = requests.post(
                        f"{backend_url}/orders/confirm",
                        params={"shop_id": pending_shop_id},
                        json=pending_payload,
                        headers={
                            "X-API-Key": api_token,
                            "Idempotency-Key": st.session_state.pending_idempotency_key,
                        },
                        timeout=20,
                    )

                if confirm_response.ok:
                    st.session_state.saved_order_result = confirm_response.json()
                    st.session_state.pending_order_payload = None
                    st.session_state.pending_shop_id = None
                    st.session_state.pending_idempotency_key = None
                    st.rerun()
                else:
                    try:
                        detail = confirm_response.json().get(
                            "detail", "The order could not be saved."
                        )
                    except ValueError:
                        detail = "The backend returned an invalid response."
                    st.error(
                        f"Order was not saved ({confirm_response.status_code}): "
                        f"{detail}"
                    )
            except requests.RequestException as exc:
                st.error(f"Could not connect to the backend: {exc}")
elif st.session_state.saved_order_result:
    saved = st.session_state.saved_order_result
    st.success("Order request saved successfully.")
    st.write("**Order reference:**", saved["order_reference"])
    st.write("**Status:**", saved["status"].title())
    st.metric("Saved total", f"₹{saved['total_rupees']:.2f}")
    st.caption(
        "This is a saved pending order request. Stock is not reserved, "
        "and shop acceptance has not been confirmed."
    )
else:
    st.info(
        "Validate the cart and check inventory first. The confirmation "
        "button appears only when every requested item passes the check."
    )


# ---------------------------------------------------------
# Saved order history
# ---------------------------------------------------------

st.divider()
st.subheader("📜 Order history")

if st.button(
    "Load order history",
    key="load_order_history_button",
):
    if not api_token:
        st.error("VOICEKIRANA_API_TOKEN is missing from your .env file.")
    else:
        try:
            with st.spinner("Loading saved orders..."):
                history_response = requests.get(
                    f"{backend_url}/orders/history",
                    params={"shop_id": int(shop_id)},
                    headers={"X-API-Key": api_token},
                    timeout=15,
                )

            if history_response.ok:
                st.session_state.order_history_result = history_response.json()
            else:
                try:
                    detail = history_response.json().get(
                        "detail", "Could not load order history."
                    )
                except ValueError:
                    detail = "The backend returned an invalid response."
                st.error(
                    f"History request failed "
                    f"({history_response.status_code}): {detail}"
                )
        except requests.RequestException as exc:
            st.error(f"Could not connect to the backend: {exc}")

history = st.session_state.get("order_history_result")
if history and history.get("shop_id") == int(shop_id):
    if not history["orders"]:
        st.info("No saved orders found for this shop.")
    else:
        st.caption(f"{history['count']} saved order(s) for {history['shop_name']}")
        for saved_order in history["orders"]:
            status = str(saved_order.get("status", "unknown")).lower()

            status_display = {
                "pending": ("🟡", "Pending — awaiting shop acceptance"),
                "confirmed": ("🟢", "Confirmed"),
                "accepted": ("🟢", "Accepted by shop"),
                "completed": ("✅", "Completed"),
                "cancelled": ("⚪", "Cancelled"),
                "rejected": ("🔴", "Rejected"),
            }

            status_icon, status_message = status_display.get(
                status,
                ("ℹ️", status.replace("_", " ").title()),
            )

            with st.expander(
                f"{saved_order['order_reference']} — "
                f"{status_icon} {status.replace('_', ' ').title()} — "
                f"₹{saved_order['total_rupees']:.2f}"
            ):
                st.markdown(f"**Order status:** {status_icon} {status_message}")
                st.write("**Created:**", saved_order["created_at"])
                st.write("**Total:**", f"₹{saved_order['total_rupees']:.2f}")

                for saved_item in saved_order["items"]:
                    product_label = saved_item["product_name"]

                    if saved_item.get("brand"):
                        product_label += f" ({saved_item['brand']})"

                    if saved_item.get("variant"):
                        product_label += f" — {saved_item['variant']}"

                    st.write(
                        f"- {product_label}: {saved_item['quantity']} "
                        f"{saved_item['unit']} × "
                        f"₹{saved_item['unit_price_paise'] / 100:.2f} = "
                        f"₹{saved_item['line_total_paise'] / 100:.2f}"
                    )


# ---------------------------------------------------------
# Order lookup and cancellation
# ---------------------------------------------------------

st.divider()
st.subheader("🔎 Find or cancel an order")
st.write(
    "Enter an order reference to view its current status. "
    "Only pending orders can be cancelled."
)

if "order_lookup_result" not in st.session_state:
    st.session_state.order_lookup_result = None

if "order_lookup_shop_id" not in st.session_state:
    st.session_state.order_lookup_shop_id = None

order_reference_input = st.text_input(
    "Order reference",
    placeholder="e.g. VK-EB505AA5BFA7",
    key="order_lookup_reference_input",
).strip()

lookup_clicked = st.button(
    "Look up order",
    key="lookup_order_button",
    use_container_width=True,
)

if lookup_clicked:
    st.session_state.order_lookup_result = None
    st.session_state.order_lookup_shop_id = None

    if not api_token:
        st.error("VOICEKIRANA_API_TOKEN is missing from your .env file.")
    elif not order_reference_input:
        st.warning("Enter an order reference first.")
    else:
        try:
            with st.spinner("Looking up order..."):
                lookup_response = requests.get(
                    f"{backend_url}/orders/{order_reference_input}",
                    params={"shop_id": int(shop_id)},
                    headers={"X-API-Key": api_token},
                    timeout=15,
                )

            if lookup_response.ok:
                st.session_state.order_lookup_result = lookup_response.json()
                st.session_state.order_lookup_shop_id = int(shop_id)
            else:
                try:
                    detail = lookup_response.json().get(
                        "detail", "Could not find the order."
                    )
                except ValueError:
                    detail = "The backend returned an invalid response."

                st.error(
                    f"Order lookup failed "
                    f"({lookup_response.status_code}): {detail}"
                )
        except requests.RequestException as exc:
            st.error(f"Could not connect to the backend: {exc}")

lookup_result = st.session_state.get("order_lookup_result")
lookup_result_shop_id = st.session_state.get("order_lookup_shop_id")

if lookup_result and lookup_result_shop_id == int(shop_id):
    st.markdown("### Order details")

    status = str(lookup_result.get("status", "unknown"))
    st.write("**Order reference:**", lookup_result.get("order_reference"))
    st.write("**Status:**", status.title())
    st.write("**Created:**", lookup_result.get("created_at", "Unknown"))
    st.metric(
        "Order total",
        f"₹{lookup_result.get('total_rupees', 0):.2f}",
    )

    for saved_item in lookup_result.get("items", []):
        product_label = saved_item.get("product_name", "Unknown product")
        if saved_item.get("brand"):
            product_label += f" ({saved_item['brand']})"
        if saved_item.get("variant"):
            product_label += f" — {saved_item['variant']}"

        quantity = saved_item.get("quantity", "?")
        unit = saved_item.get("unit", "")
        unit_price = saved_item.get(
            "unit_price_rupees",
            saved_item.get("unit_price_paise", 0) / 100,
        )
        line_total = saved_item.get(
            "line_total_rupees",
            saved_item.get("line_total_paise", 0) / 100,
        )

        st.write(
            f"- {product_label}: {quantity} {unit} × "
            f"₹{unit_price:.2f} = ₹{line_total:.2f}"
        )

    if status.lower() == "pending":
        st.warning(
            "This order is pending. Cancelling changes its status; "
            "it does not reserve or return inventory."
        )

        if st.button(
            "Cancel pending order",
            type="secondary",
            key="cancel_pending_order_button",
            use_container_width=True,
        ):
            if not api_token:
                st.error("VOICEKIRANA_API_TOKEN is missing from your .env file.")
            else:
                reference = lookup_result.get("order_reference", "")
                try:
                    with st.spinner("Cancelling pending order..."):
                        cancel_response = requests.post(
                            f"{backend_url}/orders/{reference}/cancel",
                            params={"shop_id": int(shop_id)},
                            headers={"X-API-Key": api_token},
                            timeout=15,
                        )

                    if cancel_response.ok:
                        st.session_state.order_lookup_result = (
                            cancel_response.json()
                        )
                        st.success("Order cancelled successfully.")
                        st.rerun()
                    else:
                        try:
                            detail = cancel_response.json().get(
                                "detail", "The order could not be cancelled."
                            )
                        except ValueError:
                            detail = "The backend returned an invalid response."

                        st.error(
                            f"Cancellation failed "
                            f"({cancel_response.status_code}): {detail}"
                        )
                except requests.RequestException as exc:
                    st.error(f"Could not connect to the backend: {exc}")

    elif status.lower() == "cancelled":
        st.info("This order is already cancelled.")
    else:
        st.info(
            "This order is not pending, so cancellation is not available."
        )
elif lookup_result and lookup_result_shop_id != int(shop_id):
    st.info("The selected shop changed. Look up the order again for this shop.")
