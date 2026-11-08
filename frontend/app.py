
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    layout="wide",
)

# ---------------------------------------------------------
# VoiceKirana visual styling
# ---------------------------------------------------------

st.markdown(
    """
    <style>
    :root {
        --vk-green: #166534;
        --vk-dark-green: #14532D;
        --vk-soft-green: #F3F7EF;
        --vk-page: #F7F9F5;
        --vk-text: #17251D;
        --vk-muted: #68766C;
        --vk-border: #E4EAE2;
    }

    .stApp {
        background: var(--vk-page);
    }

    .block-container {
        max-width: 1180px;
        padding-top: 1.8rem;
        padding-bottom: 3rem;
    }

    h1, h2, h3, h4 {
        color: var(--vk-dark-green);
        letter-spacing: -0.035em;
    }

    .stApp p,
    .stApp label,
    .stApp [data-testid="stCaptionContainer"] {
        color: var(--vk-text);
    }

    .stButton > button {
        min-height: 44px;
        border-radius: 11px;
        font-weight: 650;
        transition: all 0.18s ease;
        border: 1px solid var(--vk-border);
    }

    .stButton > button[kind="primary"] {
        background: var(--vk-green);
        border-color: var(--vk-green);
        color: #FFFFFF !important;
    }

    .stButton > button[kind="primary"]:hover {
        background: var(--vk-dark-green);
        border-color: var(--vk-dark-green);
        color: #FFFFFF !important;
    }

    .stButton > button:not([kind="primary"]) {
        background: #FFFFFF;
        color: var(--vk-dark-green) !important;
    }

    .stButton > button:not([kind="primary"]):hover {
        border-color: var(--vk-green);
        color: var(--vk-green) !important;
    }

    .stTextInput input,
    .stTextArea textarea,
    .stNumberInput input,
    .stSelectbox div[data-baseweb="select"] {
        border-radius: 10px;
        background: #FFFFFF;
    }

    div[data-testid="stAlert"] {
        border-radius: 12px;
    }

    details[data-testid="stExpander"] {
        border-radius: 12px;
        border: 1px solid var(--vk-border);
        background: #FFFFFF;
    }

    .vk-hero {
        background: linear-gradient(125deg, #FFFFFF 0%, #F0F7ED 100%);
        border: 1px solid var(--vk-border);
        border-radius: 22px;
        padding: 30px 34px;
        margin: 0 0 24px 0;
        box-shadow: 0 8px 28px rgba(20, 83, 45, 0.055);
    }

    .vk-brand {
        color: var(--vk-green);
        font-size: 0.82rem;
        font-weight: 800;
        letter-spacing: 0.13em;
        text-transform: uppercase;
        margin-bottom: 12px;
    }

    .vk-hero h1 {
        color: var(--vk-dark-green);
        font-size: clamp(2rem, 4vw, 3.1rem);
        line-height: 1.12;
        font-weight: 800;
        margin: 0 0 12px 0;
    }

    .vk-hero p {
        color: var(--vk-muted);
        font-size: 1.04rem;
        line-height: 1.65;
        max-width: 720px;
        margin: 0;
    }

    .vk-hero-note {
        display: inline-block;
        margin-top: 18px;
        padding: 7px 12px;
        border-radius: 999px;
        background: #E4F1E2;
        color: var(--vk-dark-green);
        font-size: 0.82rem;
        font-weight: 650;
    }

    .vk-section-intro {
        background: #FFFFFF;
        border: 1px solid var(--vk-border);
        border-radius: 16px;
        padding: 18px 20px;
        margin: 8px 0 14px 0;
    }

    .vk-section-intro h3 {
        margin: 0 0 5px 0;
        color: var(--vk-dark-green);
        font-size: 1.2rem;
    }

    .vk-section-intro p {
        margin: 0;
        color: var(--vk-muted);
        font-size: 0.93rem;
    }

    @media (max-width: 640px) {
        .block-container {
            padding: 1rem 1rem 2rem 1rem;
        }

        .vk-hero {
            padding: 23px 20px;
            border-radius: 17px;
        }

        .vk-hero p {
            font-size: 0.95rem;
        }

        .stButton > button {
            min-height: 46px;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <section class="vk-hero">
        <div class="vk-brand">&#128722; VOICEKIRANA</div>
        <h1>Groceries made simple.</h1>
        <p>
            Your neighbourhood grocery shopping, powered by your voice.
            Speak naturally, review your cart and check shop availability
            in one simple experience.
        </p>
        <div class="vk-hero-note">
            &#10022; Multilingual voice shopping assistant
        </div>
    </section>
    """,
    unsafe_allow_html=True,
)

backend_url = os.getenv(
    "BACKEND_URL",
    "http://127.0.0.1:8001",
).rstrip("/")

api_token = os.getenv("VOICEKIRANA_API_TOKEN")

@st.cache_data(ttl=60, show_spinner=False)
def fetch_active_shops(api_base_url):
    """Fetch active shops available to customers."""

    response = requests.get(
        f"{api_base_url}/shops",
        timeout=10,
    )
    response.raise_for_status()

    payload = response.json()
    shops = payload.get("shops", [])

    if not isinstance(shops, list):
        raise ValueError("The shop directory response is invalid.")

    return shops


@st.cache_data(ttl=60, show_spinner=False)
def fetch_public_shop_catalogue(api_base_url, selected_shop_id):
    """Fetch customer-visible catalogue details for one shop."""

    response = requests.get(
        f"{api_base_url}/shops/{int(selected_shop_id)}/catalogue",
        timeout=10,
    )
    response.raise_for_status()

    payload = response.json()
    items = payload.get("items", [])

    if not isinstance(items, list):
        raise ValueError("The shop catalogue response is invalid.")

    return items



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
    "voice_extraction_completed": False,
    "voice_extraction_language": None,
    "voice_order_needs_clarification": False,
    "voice_order_clarification_resolved": False,
}

for key, default in VOICE_DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = default

st.markdown(
    """
    <div class="vk-section-intro">
        <h3>&#127908; Talk to VoiceKirana</h3>
        <p>
            Tell us what you need in Telugu, Hindi, English or another
            supported language. Prisma automatically identifies your
            language and converts your speech into text.
        </p>
    </div>
    """,
    unsafe_allow_html=True,
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
        st.session_state.voice_extraction_error = None
        st.session_state.voice_extraction_completed = False
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
        st.session_state.voice_extraction_completed = False

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
                st.session_state.voice_extraction_completed = True

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

    if st.session_state.voice_extraction_completed:
        st.success(
            "Transcript converted into editable cart items. "
            "Review the cart before inventory validation. "
            "No order has been placed."
        )
    else:
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

# ---------------------------------------------------------
# Customer shop discovery and catalogue
# ---------------------------------------------------------

with st.container(border=True):
    st.markdown("### Choose your grocery shop")
    st.caption(
        "Select a shop to browse its products, prices and availability. "
        "No shop-owner login is needed."
    )

    try:
        active_shops = fetch_active_shops(backend_url)

    except (requests.RequestException, ValueError) as exc:
        st.error(
            "Could not load available shops. Check that the backend is "
            f"running and try again. Details: {exc}"
        )
        st.stop()

    shop_options = []

    for shop in active_shops:
        try:
            shop_options.append(
                (
                    int(shop["shop_id"]),
                    str(shop["shop_name"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue

    if not shop_options:
        st.info(
            "No active grocery shops are currently available. "
            "Please try again later."
        )
        st.stop()

    selected_shop = st.selectbox(
        "Available shops",
        options=shop_options,
        format_func=lambda option: option[1],
        key="customer_shop_selector",
    )

    shop_id = int(selected_shop[0])
    selected_shop_name = selected_shop[1]

    st.caption(f"Shopping at: {selected_shop_name}")

    st.divider()
    st.markdown("#### Browse this shop's catalogue")

    try:
        catalogue_items = fetch_public_shop_catalogue(
            backend_url,
            shop_id,
        )

    except (requests.RequestException, ValueError) as exc:
        st.error(
            "Could not load this shop's catalogue. "
            f"Details: {exc}"
        )
        st.stop()

    if not catalogue_items:
        st.info(
            "This shop has not listed any products yet."
        )

    else:
        rupee = chr(0x20B9)

        for row_start in range(0, len(catalogue_items), 3):
            product_columns = st.columns(3)

            for column, product in zip(
                product_columns,
                catalogue_items[row_start:row_start + 3],
            ):
                with column:
                    with st.container(border=True):
                        st.markdown(
                            f"**{product.get('name', 'Product')}**"
                        )

                        product_details = [
                            product.get("brand"),
                            product.get("variant"),
                        ]
                        product_details = [
                            str(value).strip()
                            for value in product_details
                            if value and str(value).strip()
                        ]

                        if product_details:
                            st.caption(" ? ".join(product_details))
                        else:
                            st.caption("Standard grocery product")

                        price = product.get("price_rupees")
                        unit = product.get("base_unit", "")

                        if price is not None:
                            st.markdown(
                                f"**{rupee}{float(price):,.2f} / {unit}**"
                            )

                        if product.get("is_available"):
                            st.success("Available")
                        else:
                            st.warning("Currently unavailable")

        st.caption(
            "Prices and availability are specific to the selected shop. "
            "Exact stock quantities are not publicly displayed."
        )


language = st.selectbox(
    "Request language",
    ["English", "Telugu", "Hindi", "Other / unspecified"],
    key="request_language",
)

cart_title_col, add_item_col = st.columns([3, 1])

with cart_title_col:
    st.markdown("### Your cart")
    st.caption(
        f"{len(st.session_state.cart_item_ids)} item(s) "
        "in your shopping cart"
    )

with add_item_col:
    st.write("")
    st.button(
        "Add item",
        on_click=add_cart_item,
        type="secondary",
        key="add_cart_item_button",
        use_container_width=True,
    )

for item_id in list(st.session_state.cart_item_ids):
    with st.container(border=True):
        item_header_col, remove_col = st.columns([4, 1])

        with item_header_col:
            st.markdown(f"**Item {item_id + 1}**")
            st.caption("Product details")

        with remove_col:
            st.button(
                "Remove",
                key=f"remove_cart_item_{item_id}",
                disabled=len(st.session_state.cart_item_ids) == 1,
                on_click=remove_cart_item,
                args=(item_id,),
                use_container_width=True,
            )

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

        quantity_col, unit_col = st.columns(2)

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

# ---------------------------------------------------------
# Shared cart validation and cross-shop search
# ---------------------------------------------------------

def build_cart_order_payload():
    """Build and validate one consistent payload from the editable cart."""

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

    if (
        st.session_state.voice_order_needs_clarification
        and not st.session_state.voice_order_clarification_resolved
    ):
        validation_errors.append(
            "Review and resolve the overall order clarification."
        )

    if not order_items:
        validation_errors.append(
            "Please add at least one grocery item."
        )

    if validation_errors:
        return None, validation_errors

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

    return order_payload, []


def select_shop_from_comparison(selected_shop_id, selected_shop_name):
    """Select a fully matching shop before the next Streamlit rerun."""

    st.session_state["customer_shop_selector"] = (
        int(selected_shop_id),
        str(selected_shop_name),
    )

    # A change of shop invalidates any previous confirmation preview.
    st.session_state.pending_order_payload = None
    st.session_state.pending_shop_id = None
    st.session_state.pending_idempotency_key = None


st.session_state.setdefault("shop_comparison_results", None)
st.session_state.setdefault("shop_comparison_payload_signature", None)

current_order_payload, current_payload_errors = (
    build_cart_order_payload()
)

current_signature = (
    repr(current_order_payload)
    if current_order_payload is not None
    else None
)

# Clear old search results as soon as the cart content changes.
if (
    st.session_state.shop_comparison_payload_signature
    != current_signature
):
    st.session_state.shop_comparison_results = None
    st.session_state.shop_comparison_payload_signature = (
        current_signature
    )

validate_col, search_col = st.columns(2)

with validate_col:
    submitted = st.button(
        "Validate cart and check inventory",
        type="primary",
        use_container_width=True,
        key="validate_cart_button",
    )

with search_col:
    find_shops_clicked = st.button(
        "Find shops for my cart",
        type="secondary",
        use_container_width=True,
        key="find_shops_for_cart_button",
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

    order_payload = current_order_payload
    validation_errors = list(current_payload_errors)

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

        st.markdown("### Inventory results")

        inventory_status = result.get("status", "unknown")
        item_results = result.get("items", [])
        rupee = chr(0x20B9)

        # Overall inventory result
        if inventory_status == "inventory_checked":
            with st.container(border=True):
                st.success(
                    "All requested items are available in the required quantities."
                )

                st.caption(
                    f"Shop: {result.get('shop_name', 'Unknown')}"
                )

                total = result.get("total_rupees")

                if total is not None:
                    st.metric(
                        "Estimated order total",
                        f"{rupee}{float(total):,.2f}",
                    )

                st.caption(
                    "This is an estimate only. Your order is not saved "
                    "until you explicitly confirm it."
                )
        else:
            with st.container(border=True):
                st.warning(
                    message
                    or "Some products need clarification or cannot be fulfilled. "
                       "Review the item results before proceeding."
                )

                st.caption(
                    "Review each product below. Unmatched items must be "
                    "resolved before order confirmation."
                )

        status_messages = {
            "matched": "Matched successfully with the shop catalogue.",
            "insufficient_stock": (
                "Not enough stock. Reduce the quantity or contact the shop."
            ),
            "unavailable": (
                "This product is unavailable at this shop. "
                "Ask the shop about alternatives."
            ),
            "not_found": (
                "We couldn't find this product in the shop catalogue. "
                "Check the product name or requested brand."
            ),
            "ambiguous": (
                "More than one product may match. Select the correct "
                "product before continuing."
            ),
        }

        if not item_results:
            st.info("No individual inventory results were returned.")

        for item_result in item_results:
            item_name = item_result.get("requested_name", "Unknown item")
            item_status = item_result.get("status", "unknown")
            display_status = item_status.replace("_", " ").title()

            with st.container(border=True):
                item_header_col, status_col = st.columns([3, 2])

                with item_header_col:
                    st.markdown(f"**{item_name}**")

                with status_col:
                    st.caption(f"STATUS: {display_status.upper()}")

                if item_status == "matched":
                    st.success("Available at this shop.")

                    quantity_col, price_col = st.columns(2)

                    with quantity_col:
                        quantity = item_result.get("quantity", "?")
                        unit = item_result.get("unit", "")
                        st.metric(
                            "Requested quantity",
                            f"{quantity} {unit}".strip(),
                        )

                    with price_col:
                        unit_price = item_result.get("unit_price_rupees")
                        st.metric(
                            "Unit price",
                            (
                                f"{rupee}{float(unit_price):,.2f}"
                                if unit_price is not None else "?"
                            ),
                        )

                    total_col, stock_col = st.columns(2)

                    with total_col:
                        line_total = item_result.get("line_total_rupees")
                        st.metric(
                            "Item total",
                            (
                                f"{rupee}{float(line_total):,.2f}"
                                if line_total is not None else "?"
                            ),
                        )

                    with stock_col:
                        available = item_result.get("available_quantity")
                        unit = item_result.get("unit", "")
                        st.metric(
                            "Available stock",
                            (
                                f"{available} {unit}".strip()
                                if available is not None else "?"
                            ),
                        )

                elif item_status == "insufficient_stock":
                    st.warning("Insufficient stock.")

                    requested_col, available_col = st.columns(2)

                    with requested_col:
                        st.metric(
                            "Requested quantity",
                            item_result.get("requested_quantity", "Unknown"),
                        )

                    with available_col:
                        st.metric(
                            "Available quantity",
                            item_result.get("available_quantity", "Unknown"),
                        )

                    st.caption(
                        item_result.get("message")
                        or status_messages[item_status]
                    )

                else:
                    if item_status in ("not_found", "unavailable"):
                        st.error(
                            status_messages.get(
                                item_status,
                                "This item could not be fulfilled.",
                            )
                        )
                    else:
                        st.warning(
                            status_messages.get(
                                item_status,
                                "Please review this item with the shop.",
                            )
                        )

                    item_message = item_result.get("message")

                    if item_message:
                        st.write(item_message)

        st.info(
            "No stock has been reserved and no order has been placed. "
            "You must explicitly confirm an eligible order."
        )

        # Spoken explanation
        with st.container(border=True):
            st.markdown("**VoiceKirana spoken response**")
            st.caption(
                "Listen to the explanation of your inventory results."
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

# ---------------------------------------------------------
# ---------------------------------------------------------
# Find shops that can fulfil the complete cart
# ---------------------------------------------------------

if find_shops_clicked:
    st.session_state.shop_comparison_results = None

    if current_payload_errors:
        for error in current_payload_errors:
            st.error(error)

    elif not api_token:
        st.error(
            "VOICEKIRANA_API_TOKEN is missing from your .env file."
        )

    else:
        try:
            # Validate the same payload before comparing any shop.
            with st.spinner("Validating your grocery request..."):
                preview_response = requests.post(
                    f"{backend_url}/orders/preview",
                    json=current_order_payload,
                    headers={"X-API-Key": api_token},
                    timeout=15,
                )

            if not preview_response.ok:
                try:
                    detail = preview_response.json().get(
                        "detail",
                        "The backend rejected the grocery request.",
                    )
                except ValueError:
                    detail = "The backend returned an invalid response."

                st.error(
                    f"Cart validation failed "
                    f"({preview_response.status_code}): {detail}"
                )

            else:
                preview = preview_response.json()

                if preview.get("status") != "ready_for_inventory_check":
                    st.warning(
                        preview.get(
                            "message",
                            "Resolve the grocery request before searching."
                        )
                    )

                else:
                    shops = fetch_active_shops(backend_url)

                    comparison = {
                        "full_matches": [],
                        "partial_matches": [],
                        "failed": [],
                        "checked_count": 0,
                        "shop_count": len(shops),
                        "signature": current_signature,
                    }

                    if not shops:
                        st.info("No active shops are available to compare.")

                    else:
                        def check_one_shop(shop):
                            """Check one shop without touching Streamlit state."""
                            try:
                                shop_id_for_check = int(shop["shop_id"])
                                shop_name_for_check = str(shop["shop_name"])

                                response = requests.post(
                                    f"{backend_url}/inventory/check",
                                    params={"shop_id": shop_id_for_check},
                                    json=current_order_payload,
                                    headers={"X-API-Key": api_token},
                                    timeout=(3, 5),
                                )

                                if not response.ok:
                                    try:
                                        detail = response.json().get(
                                            "detail",
                                            "Inventory check failed.",
                                        )
                                    except ValueError:
                                        detail = (
                                            "The backend returned an invalid "
                                            "response."
                                        )

                                    return "failed", {
                                        "shop_id": shop_id_for_check,
                                        "shop_name": shop_name_for_check,
                                        "error": str(detail),
                                    }

                                result = response.json()

                                if not isinstance(result, dict):
                                    raise ValueError(
                                        "Invalid inventory response format."
                                    )

                                item_results = result.get("items", [])

                                if not isinstance(item_results, list):
                                    raise ValueError(
                                        "Invalid item-result format."
                                    )

                                if not all(
                                    isinstance(item_result, dict)
                                    for item_result in item_results
                                ):
                                    raise ValueError(
                                        "An inventory item result was invalid."
                                    )

                                matched_count = sum(
                                    1
                                    for item_result in item_results
                                    if item_result.get("status") == "matched"
                                )

                                total_items = len(
                                    current_order_payload["items"]
                                )

                                shop_result = {
                                    "shop_id": shop_id_for_check,
                                    "shop_name": shop_name_for_check,
                                    "matched_count": matched_count,
                                    "total_items": total_items,
                                    "items": item_results,
                                    "status": result.get("status"),
                                }

                                is_full_match = (
                                    result.get("status") == "inventory_checked"
                                    and len(item_results) == total_items
                                    and matched_count == total_items
                                )

                                if is_full_match:
                                    shop_result["total_rupees"] = float(
                                        result["total_rupees"]
                                    )
                                    return "full", shop_result

                                return "partial", shop_result

                            except (
                                KeyError,
                                TypeError,
                                ValueError,
                                requests.RequestException,
                            ) as exc:
                                return "failed", {
                                    "shop_id": shop.get("shop_id"),
                                    "shop_name": shop.get(
                                        "shop_name", "Unknown shop"
                                    ),
                                    "error": str(exc),
                                }

                        progress_bar = st.progress(
                            0,
                            text=f"Preparing to check {len(shops)} shops...",
                        )
                        progress_status = st.empty()

                        # Limit concurrency to avoid overwhelming the API/database.
                        max_workers = min(12, len(shops))

                        with ThreadPoolExecutor(
                            max_workers=max_workers
                        ) as executor:
                            futures = {
                                executor.submit(check_one_shop, shop): shop
                                for shop in shops
                            }

                            completed_count = 0

                            for future in as_completed(futures):
                                completed_count += 1

                                try:
                                    category, shop_result = future.result()

                                    if category == "full":
                                        comparison["full_matches"].append(
                                            shop_result
                                        )
                                        comparison["checked_count"] += 1

                                    elif category == "partial":
                                        comparison["partial_matches"].append(
                                            shop_result
                                        )
                                        comparison["checked_count"] += 1

                                    else:
                                        comparison["failed"].append(
                                            shop_result
                                        )

                                except Exception as exc:
                                    shop = futures[future]
                                    comparison["failed"].append({
                                        "shop_id": shop.get("shop_id"),
                                        "shop_name": shop.get(
                                            "shop_name", "Unknown shop"
                                        ),
                                        "error": str(exc),
                                    })

                                progress_bar.progress(
                                    completed_count / len(shops),
                                    text=(
                                        f"Checked {completed_count} "
                                        f"of {len(shops)} shops"
                                    ),
                                )
                                progress_status.caption(
                                    f"{len(comparison['full_matches'])} "
                                    "complete matches ? "
                                    f"{len(comparison['partial_matches'])} "
                                    "partial matches ? "
                                    f"{len(comparison['failed'])} "
                                    "could not be checked"
                                )

                        progress_bar.empty()
                        progress_status.empty()

                        # Rank complete matches by their complete-cart total.
                        comparison["full_matches"].sort(
                            key=lambda item: item["total_rupees"]
                        )

                    st.session_state.shop_comparison_results = comparison

        except (requests.RequestException, ValueError) as exc:
            st.error(
                f"Could not compare shops. Please try again. Details: {exc}"
            )


comparison = st.session_state.get("shop_comparison_results")

if (
    comparison
    and comparison.get("signature") == current_signature
):
    st.markdown("### Shops matching your grocery cart")

    st.caption(
        f"Successfully checked {comparison['checked_count']} of "
        f"{comparison['shop_count']} active shop(s)."
    )

    full_matches = comparison["full_matches"]
    partial_matches = comparison["partial_matches"]

    if full_matches:
        st.success(
            f"{len(full_matches)} shop(s) can fulfil your complete cart."
        )

        for shop_result in full_matches:
            with st.container(border=True):
                name_col, price_col, action_col = st.columns([3, 2, 2])

                with name_col:
                    st.markdown(f"**{shop_result['shop_name']}**")
                    st.caption(
                        f"All {shop_result['total_items']} requested "
                        "products are available."
                    )

                with price_col:
                    st.metric(
                        "Complete-cart estimate",
                        f"?{shop_result['total_rupees']:,.2f}",
                    )

                with action_col:
                    st.write("")
                    st.button(
                        "Use this shop",
                        key=(
                            f"use_matching_shop_"
                            f"{shop_result['shop_id']}"
                        ),
                        type="primary",
                        use_container_width=True,
                        on_click=select_shop_from_comparison,
                        args=(
                            shop_result["shop_id"],
                            shop_result["shop_name"],
                        ),
                    )

    else:
        st.warning(
            "No checked shop can currently fulfil your entire cart."
        )

    if partial_matches:
        st.markdown("#### Shops with partial availability")

        for shop_result in partial_matches:
            with st.container(border=True):
                st.markdown(f"**{shop_result['shop_name']}**")
                st.caption(
                    f"{shop_result['matched_count']} of "
                    f"{shop_result['total_items']} requested products matched."
                )

                for item_result in shop_result["items"]:
                    item_name = item_result.get(
                        "requested_name", "Unknown product"
                    )
                    item_status = item_result.get(
                        "status", "unknown"
                    )

                    if item_status == "matched":
                        st.write(f"Available: {item_name}")
                    else:
                        st.write(
                            f"Needs attention: {item_name} ? "
                            f"{item_result.get('message') or item_status.replace('_', ' ')}"
                        )

    if comparison["failed"]:
        with st.expander(
            f"{len(comparison['failed'])} shop(s) could not be checked"
        ):
            for failure in comparison["failed"]:
                st.write(
                    f"{failure['shop_name']}: {failure['error']}"
                )

    st.info(
        "Shop comparison is only an availability and price preview. "
        "No stock has been reserved and no order has been placed. "
        "After selecting a shop, run the normal inventory validation "
        "before explicitly confirming an order."
    )


# Explicit order confirmation
# ---------------------------------------------------------

st.markdown("### Confirm your order")
st.caption(
    "Review your validated grocery request before saving it."
)

pending_payload = st.session_state.pending_order_payload
pending_shop_id = st.session_state.pending_shop_id

with st.container(border=True):
    if pending_payload and pending_shop_id:
        st.success("Inventory check passed. Your order is ready for confirmation.")

        st.warning(
            "Nothing has been saved yet. Confirm below to save a pending "
            "order request. Stock will not be reserved, and shop acceptance "
            "is not guaranteed."
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
        rupee = chr(0x20B9)

        st.success("Your order request has been saved successfully.")

        reference_col, status_col = st.columns(2)

        with reference_col:
            st.caption("ORDER REFERENCE")
            st.markdown(f"**{saved['order_reference']}**")

        with status_col:
            st.caption("ORDER STATUS")
            st.markdown(f"**{saved['status'].replace('_', ' ').title()}**")

        st.metric(
            "Saved order total",
            f"{rupee}{float(saved['total_rupees']):,.2f}",
        )

        st.info(
            "This is a saved pending order request. Stock is not reserved, "
            "and shop acceptance has not been confirmed."
        )

    else:
        st.info(
            "Your cart must pass inventory validation before an order "
            "can be confirmed. The confirmation button will appear when "
            "every requested item is eligible."
        )



# ---------------------------------------------------------
# Saved order history
# ---------------------------------------------------------

st.markdown("### Your order history")
st.caption(
    "View saved grocery orders and track their current status."
)

with st.container(border=True):
    st.markdown("**Order records**")
    st.write(
        "Load the orders associated with the currently selected shop."
    )

    if st.button(
        "Load order history",
        key="load_order_history_button",
        use_container_width=True,
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
    orders = history.get("orders", [])

    if not orders:
        st.info("No saved orders found for this shop.")
    else:
        st.caption(
            f"{history['count']} saved order(s) for {history['shop_name']}"
        )

        rupee = chr(0x20B9)

        history_status_labels = {
            "pending": "Pending - awaiting shop acceptance",
            "confirmed": "Confirmed",
            "accepted": "Accepted by shop",
            "completed": "Completed",
            "cancelled": "Cancelled",
            "rejected": "Rejected by shop",
        }

        for saved_order in orders:
            status = str(
                saved_order.get("status", "unknown")
            ).lower()

            status_label = history_status_labels.get(
                status,
                status.replace("_", " ").title(),
            )

            with st.container(border=True):
                reference_col, status_col = st.columns([3, 2])

                with reference_col:
                    st.caption("ORDER REFERENCE")
                    st.markdown(
                        f"**{saved_order['order_reference']}**"
                    )

                with status_col:
                    st.caption("ORDER STATUS")
                    if status == "pending":
                        st.warning(status_label)
                    elif status in ("confirmed", "accepted", "completed"):
                        st.success(status_label)
                    elif status == "rejected":
                        st.error(status_label)
                    else:
                        st.info(status_label)

                total = float(saved_order["total_rupees"])

                st.metric(
                    "Order total",
                    f"{rupee}{total:,.2f}",
                )

                st.caption(
                    f"Created: {saved_order['created_at']}"
                )

                with st.expander("View ordered products"):
                    for saved_item in saved_order["items"]:
                        product_label = saved_item["product_name"]

                        if saved_item.get("brand"):
                            product_label += f" ({saved_item['brand']})"

                        if saved_item.get("variant"):
                            product_label += f" - {saved_item['variant']}"

                        st.write(
                            f"{product_label}: {saved_item['quantity']} "
                            f"{saved_item['unit']} x "
                            f"{rupee}{saved_item['unit_price_paise'] / 100:.2f} = "
                            f"{rupee}{saved_item['line_total_paise'] / 100:.2f}"
                        )



# ---------------------------------------------------------
# Order lookup and cancellation
# ---------------------------------------------------------

st.markdown("### Find or cancel an order")
st.caption(
    "Track a grocery order using its reference. "
    "Only pending orders can be cancelled."
)

if "order_lookup_result" not in st.session_state:
    st.session_state.order_lookup_result = None

if "order_lookup_shop_id" not in st.session_state:
    st.session_state.order_lookup_shop_id = None

with st.container(border=True):
    st.markdown("**Track your order**")
    st.write(
        "Enter the order reference you received when your order was saved."
    )

    reference_col, lookup_col = st.columns([3, 1])

    with reference_col:
        order_reference_input = st.text_input(
            "Order reference",
            placeholder="e.g. VK-EB505AA5BFA7",
            key="order_lookup_reference_input",
        ).strip()

    with lookup_col:
        st.write("")
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
    status = str(lookup_result.get("status", "unknown"))
    status_normalized = status.lower()
    rupee = chr(0x20B9)

    with st.container(border=True):
        st.markdown("**Order details**")

        reference_col, status_col = st.columns([3, 2])

        with reference_col:
            st.caption("ORDER REFERENCE")
            st.markdown(
                f"**{lookup_result.get('order_reference', 'Unknown')}**"
            )

        with status_col:
            st.caption("ORDER STATUS")

            if status_normalized == "pending":
                st.warning("Pending - awaiting shop acceptance")
            elif status_normalized in ("confirmed", "accepted", "completed"):
                st.success(status.replace("_", " ").title())
            elif status_normalized == "rejected":
                st.error("Rejected")
            else:
                st.info(status.replace("_", " ").title())

        st.caption(
            f"Created: {lookup_result.get('created_at', 'Unknown')}"
        )

        st.metric(
            "Order total",
            f"{rupee}{float(lookup_result.get('total_rupees', 0)):,.2f}",
        )

        with st.expander("View ordered products", expanded=True):
            for saved_item in lookup_result.get("items", []):
                product_label = saved_item.get(
                    "product_name", "Unknown product"
                )

                if saved_item.get("brand"):
                    product_label += f" ({saved_item['brand']})"

                if saved_item.get("variant"):
                    product_label += f" - {saved_item['variant']}"

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
                    f"{product_label}: {quantity} {unit} x "
                    f"{rupee}{unit_price:.2f} = {rupee}{line_total:.2f}"
                )

        if status_normalized == "pending":
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
                    st.error(
                        "VOICEKIRANA_API_TOKEN is missing from your .env file."
                    )
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
                                detail = (
                                    "The backend returned an invalid response."
                                )

                            st.error(
                                f"Cancellation failed "
                                f"({cancel_response.status_code}): {detail}"
                            )

                    except requests.RequestException as exc:
                        st.error(f"Could not connect to the backend: {exc}")

        elif status_normalized == "cancelled":
            st.info("This order is already cancelled.")
        else:
            st.info(
                "This order is not pending, so cancellation is not available."
            )

elif lookup_result and lookup_result_shop_id != int(shop_id):
    st.info(
        "The selected shop changed. Look up the order again for this shop."
    )
