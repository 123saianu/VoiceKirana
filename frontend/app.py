
import os
import sys
import tempfile
from pathlib import Path
import uuid
from datetime import datetime, timedelta, timezone
import time
from urllib.parse import quote

import requests
import streamlit as st
import extra_streamlit_components as stx
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
    /* VoiceKirana layout styling.
       Colors are controlled by .streamlit/config.toml. */

    .block-container {
        max-width: 1180px;
        padding-top: 1.8rem;
        padding-bottom: 3rem;
    }

    h1, h2, h3, h4 {
        letter-spacing: -0.035em;
    }

    .stButton > button {
        min-height: 44px;
        border-radius: 11px;
        font-weight: 650;
        transition: all 0.18s ease;
    }

    .stTextInput input,
    .stTextArea textarea,
    .stNumberInput input,
    .stSelectbox div[data-baseweb="select"] {
        border-radius: 10px;
    }

    div[data-testid="stAlert"] {
        border-radius: 12px;
    }

    details[data-testid="stExpander"] {
        border-radius: 12px;
    }

    .vk-hero {
        border-radius: 22px;
        padding: 30px 34px;
        margin: 0 0 24px 0;
    }

    .vk-hero,
    .vk-hero * {
        color: inherit;
    }

    .vk-brand {
        font-size: 0.82rem;
        font-weight: 800;
        letter-spacing: 0.13em;
        text-transform: uppercase;
        margin-bottom: 12px;
    }

    .vk-hero h1 {
        font-size: clamp(2rem, 4vw, 3.1rem);
        line-height: 1.12;
        font-weight: 800;
        margin: 0 0 12px 0;
    }

    .vk-hero p {
        font-size: 1.04rem;
        line-height: 1.65;
        max-width: 720px;
        margin: 0;
    }

    .vk-hero-note {
        display: inline-block;
        margin-top: 18px;
        padding: 7px 12px;
        border: 1px solid currentColor;
        border-radius: 999px;
        font-size: 0.82rem;
        font-weight: 650;
    }

    .vk-section-intro {
        border-radius: 16px;
        padding: 18px 20px;
        margin: 8px 0 14px 0;
    }

    .vk-section-intro,
    .vk-section-intro * {
        color: inherit;
    }

    .vk-section-intro h3 {
        margin: 0 0 5px 0;
        font-size: 1.2rem;
    }

    .vk-section-intro p {
        margin: 0;
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


# VOICEKIRANA_OPENING_AUTH_GATE
# ---------------------------------------------------------
# Customer sign-in is required before the main application.
# Remember-me uses an opaque, rotating browser cookie.
# ---------------------------------------------------------

REMEMBER_COOKIE_NAME = "vk_customer_remember"
REMEMBER_SESSION_SECONDS = 30 * 24 * 60 * 60

COOKIE_SECURE = os.getenv(
    "VOICEKIRANA_COOKIE_SECURE", "false"
).strip().casefold() in {"1", "true", "yes", "on"}

for _key, _default in (
    ("customer_access_token", None),
    ("customer_access_expires_at", 0),
    ("customer_remember_token", None),
    ("customer_profile", None),
    ("customer_restore_attempted", False),
    ("order_history_result", None),
    ("order_lookup_result", None),
    ("order_lookup_customer_id", None),
):
    if _key not in st.session_state:
        st.session_state[_key] = _default


def _customer_error_detail(response, fallback):
    try:
        detail = response.json().get("detail", fallback)
    except ValueError:
        return fallback

    if isinstance(detail, dict):
        detail = detail.get("message", detail)

    return str(detail)


def _clear_customer_session():
    st.session_state.customer_access_token = None
    st.session_state.customer_access_expires_at = 0
    st.session_state.customer_remember_token = None
    st.session_state.customer_profile = None
    st.session_state.customer_profile_details_loaded_token = None
    st.session_state.customer_addresses_customer_id = None
    st.session_state.customer_addresses = None
    st.session_state.customer_restore_attempted = False
    st.session_state.order_history_result = None
    st.session_state.order_lookup_result = None
    st.session_state.order_lookup_customer_id = None
    st.session_state.saved_order_result = None


def _apply_customer_login(payload, cookie_manager, remember_me):
    """Store the short-lived access token and optionally persist login."""

    access_token = payload.get("access_token")
    token_type = str(payload.get("token_type", "bearer")).casefold()

    if not access_token or token_type != "bearer":
        raise ValueError("The authentication service returned an invalid token.")

    profile = payload.get("customer")

    if not isinstance(profile, dict) or not profile.get("customer_id"):
        profile_response = requests.get(
            f"{backend_url}/customers/me",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        profile_response.raise_for_status()
        profile = profile_response.json()

    st.session_state.customer_access_token = access_token
    st.session_state.customer_access_expires_at = (
        time.time() + int(payload.get("expires_in", 28800))
    )
    st.session_state.customer_profile = profile
    st.session_state.customer_profile_details_loaded_token = None
    st.session_state.customer_addresses_customer_id = None
    st.session_state.customer_addresses = None
    st.session_state.customer_restore_attempted = False
    st.session_state.order_history_result = None
    st.session_state.order_lookup_result = None
    st.session_state.order_lookup_customer_id = None
    st.session_state.saved_order_result = None

    remember_token = payload.get("remember_token")

    if remember_me and remember_token:
        cookie_manager.set(
            REMEMBER_COOKIE_NAME,
            remember_token,
            key="vk_customer_remember_set",
            path="/",
            expires_at=(
                datetime.now(timezone.utc)
                + timedelta(days=30)
            ),
            max_age=REMEMBER_SESSION_SECONDS,
            secure=COOKIE_SECURE,
            same_site="lax",
        )
        st.session_state.customer_remember_token = remember_token
    else:
        st.session_state.customer_remember_token = None
        try:
            cookies = cookie_manager.get_all(
                key="vk_customer_cookie_clear_check"
            ) or {}
            if REMEMBER_COOKIE_NAME in cookies:
                cookie_manager.delete(
                    REMEMBER_COOKIE_NAME,
                    key="vk_customer_remember_delete",
                )
        except (KeyError, TypeError):
            pass


def _sign_out_customer(cookie_manager, backend_url):
    """Revoke the persistent session and clear the local customer session."""

    remember_token = st.session_state.get("customer_remember_token")

    try:
        cookies = cookie_manager.get_all(
            key="vk_customer_signout_cookie_read"
        ) or {}
        remember_token = (
            remember_token or cookies.get(REMEMBER_COOKIE_NAME)
        )

        if remember_token:
            requests.post(
                f"{backend_url}/customers/session/logout",
                json={"remember_token": remember_token},
                timeout=10,
            )

        if REMEMBER_COOKIE_NAME in cookies:
            cookie_manager.delete(
                REMEMBER_COOKIE_NAME,
                key="vk_customer_signout_cookie_delete",
            )

    except (requests.RequestException, KeyError, TypeError):
        # Local logout must still complete if the backend is unreachable.
        pass

    _clear_customer_session()


# Create a browser cookie manager for this Streamlit session.
cookie_manager = stx.CookieManager(
    key="voicekirana_customer_cookie_manager"
)

browser_cookies = cookie_manager.get_all(
    key="voicekirana_customer_cookie_bootstrap"
) or {}

browser_remember_token = browser_cookies.get(REMEMBER_COOKIE_NAME)

# Restore from the opaque browser cookie when the access token is absent
# or close to its 8-hour expiry.
access_token_expiring = (
    not st.session_state.customer_access_token
    or time.time() >= (
        float(st.session_state.customer_access_expires_at or 0) - 60
    )
)

if access_token_expiring:
    st.session_state.customer_access_token = None

    if (
        browser_remember_token
        and not st.session_state.customer_restore_attempted
    ):
        st.session_state.customer_restore_attempted = True

        try:
            with st.spinner("Restoring your VoiceKirana account..."):
                restore_response = requests.post(
                    f"{backend_url}/customers/session/restore",
                    json={
                        "remember_token": browser_remember_token
                    },
                    timeout=15,
                )

            if restore_response.ok:
                restored_payload = restore_response.json()
                _apply_customer_login(
                    restored_payload,
                    cookie_manager,
                    remember_me=True,
                )
                st.rerun()
            else:
                cookie_manager.delete(
                    REMEMBER_COOKIE_NAME,
                    key="vk_customer_expired_cookie_delete",
                )
                st.session_state.customer_remember_token = None
                st.session_state.customer_profile = None
                st.warning(
                    "Your saved sign-in has expired. Please sign in again."
                )

        except requests.RequestException as exc:
            st.error(
                f"Could not restore your customer account: {exc}"
            )

customer_token = st.session_state.get("customer_access_token")
customer_profile = st.session_state.get("customer_profile")

if not customer_token or not customer_profile:
    st.markdown("## Welcome to VoiceKirana")
    st.caption(
        "Sign in or create your customer account to start shopping."
    )

    with st.container(border=True):
        auth_mode = st.radio(
            "Your account",
            options=("Sign in", "Create account"),
            horizontal=True,
            key="opening_customer_auth_mode",
        )

        with st.form("opening_customer_auth_form"):
            full_name = ""

            if auth_mode == "Create account":
                full_name = st.text_input(
                    "Full name",
                    max_chars=150,
                    key="opening_customer_full_name",
                ).strip()

            email = st.text_input(
                "Email address",
                key="opening_customer_email",
            ).strip()

            password = st.text_input(
                "Password",
                type="password",
                key="opening_customer_password",
            )

            remember_me = st.checkbox(
                "Remember me on this device",
                value=True,
                key="opening_customer_remember_me",
            )

            submitted = st.form_submit_button(
                "Create account" if auth_mode == "Create account" else "Sign in",
                type="primary",
                use_container_width=True,
            )

        if submitted:
            if not email or not password:
                st.warning("Enter both your email address and password.")
                st.stop()

            if auth_mode == "Create account" and not full_name:
                st.warning("Enter your full name.")
                st.stop()

            if auth_mode == "Create account" and len(password) < 12:
                st.warning("Your password must contain at least 12 characters.")
                st.stop()

            try:
                with st.spinner("Signing in to VoiceKirana..."):
                    if auth_mode == "Create account":
                        registration = requests.post(
                            f"{backend_url}/customers/register",
                            json={
                                "full_name": full_name,
                                "email": email,
                                "password": password,
                            },
                            timeout=15,
                        )

                        if not registration.ok:
                            st.error(
                                "Account creation failed "
                                f"({registration.status_code}): "
                                f"{_customer_error_detail(registration, 'Could not create your account.')}"
                            )
                            st.stop()

                    login_response = requests.post(
                        f"{backend_url}/customers/login",
                        json={
                            "email": email,
                            "password": password,
                            "remember_me": remember_me,
                        },
                        timeout=15,
                    )

                if not login_response.ok:
                    st.error(
                        f"Sign-in failed ({login_response.status_code}): "
                        f"{_customer_error_detail(login_response, 'Check your email and password.')}"
                    )
                else:
                    login_payload = login_response.json()
                    _apply_customer_login(
                        login_payload,
                        cookie_manager,
                        remember_me=remember_me,
                    )
                    st.success("Welcome to VoiceKirana!")
                    st.rerun()

            except requests.RequestException as exc:
                st.error(f"Could not connect to the account service: {exc}")
            except (ValueError, TypeError) as exc:
                st.error(f"Could not complete sign-in: {exc}")

    st.stop()

# ---------------------------------------------------------
# Authenticated customer — main application is now unlocked
# ---------------------------------------------------------


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


@st.cache_data(ttl=60, show_spinner=False)
def fetch_public_global_catalogue(api_base_url):
    """Fetch products and shop-specific offers across visible shops."""

    response = requests.get(
        f"{api_base_url}/catalogue/all",
        timeout=15,
    )
    response.raise_for_status()

    payload = response.json()
    products = payload.get("products", [])

    if not isinstance(products, list):
        raise ValueError("The global catalogue response is invalid.")

    return {
        "shop_count": payload.get("shop_count", 0),
        "products": products,
    }


def select_catalogue_offer_shop(shop_id, shop_name):
    """Select an offer's shop as the checkout destination."""

    st.session_state["customer_shop_selector"] = (
        int(shop_id),
        str(shop_name),
    )

    st.session_state.pending_order_payload = None
    st.session_state.pending_shop_id = None
    st.session_state.pending_idempotency_key = None




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


def _invalidate_cart_previews():
    """Invalidate previews and comparisons whenever cart contents change."""
    for key in (
        "pending_order_payload",
        "pending_shop_id",
        "pending_idempotency_key",
        "saved_order_result",
        "cart_result",
        "shop_comparison_results",
        "shop_comparison_payload_signature",
    ):
        if key in st.session_state:
            st.session_state[key] = None


def _active_cart_item_count():
    """Count populated cart rows, excluding empty placeholders."""
    count = 0

    for item_id in st.session_state.get("cart_item_ids", []):
        name = str(
            st.session_state.get(f"cart_{item_id}_name", "")
        ).strip()

        try:
            quantity = float(
                st.session_state.get(
                    f"cart_{item_id}_quantity", 0
                ) or 0
            )
        except (TypeError, ValueError):
            quantity = 0

        if name and quantity > 0:
            count += 1

    return count


def add_cart_item():
    """Add a new, empty product row."""
    new_id = st.session_state.next_cart_item_id
    st.session_state.next_cart_item_id += 1
    st.session_state.cart_item_ids.append(new_id)


def remove_cart_item(item_id):
    """Remove a cart row, including when it is the last remaining row."""
    if item_id not in st.session_state.cart_item_ids:
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
        "catalogue_offer_key",
    ):
        key = f"cart_{item_id}_{field}"
        if key in st.session_state:
            del st.session_state[key]

    _invalidate_cart_previews()


def _catalogue_offer_key(shop_id, product_id):
    return f"{int(shop_id)}:{int(product_id)}"


def _find_catalogue_cart_item(
    shop_id,
    product_id,
    name,
    normalized_name,
    unit,
    brand=None,
    variant=None,
):
    """Find an existing catalogue row or matching manually entered row."""

    offer_key = _catalogue_offer_key(shop_id, product_id)

    # Exact shop/product match first.
    for item_id in st.session_state.cart_item_ids:
        if (
            st.session_state.get(
                f"cart_{item_id}_catalogue_offer_key"
            ) == offer_key
        ):
            return item_id

    # Reuse a manual cart row with the same product identity.
    target_identity = (
        (normalized_name or name).strip().casefold(),
        (unit or "").strip().casefold(),
        (brand or "").strip().casefold(),
        (variant or "").strip().casefold(),
    )

    for item_id in st.session_state.cart_item_ids:
        existing_offer_key = st.session_state.get(
            f"cart_{item_id}_catalogue_offer_key"
        )

        if existing_offer_key and existing_offer_key != offer_key:
            continue

        existing_name = str(
            st.session_state.get(f"cart_{item_id}_name", "")
        ).strip()

        existing_normalized = str(
            st.session_state.get(
                f"cart_{item_id}_normalized", ""
            )
        ).strip()

        existing_unit = str(
            st.session_state.get(f"cart_{item_id}_unit", "")
        ).strip()

        existing_brand = str(
            st.session_state.get(f"cart_{item_id}_brand", "")
        ).strip()

        existing_variant = str(
            st.session_state.get(f"cart_{item_id}_variant", "")
        ).strip()

        try:
            existing_quantity = float(
                st.session_state.get(
                    f"cart_{item_id}_quantity", 0
                ) or 0
            )
        except (TypeError, ValueError):
            existing_quantity = 0

        if not existing_name or existing_quantity <= 0:
            continue

        existing_identity = (
            (existing_normalized or existing_name).casefold(),
            existing_unit.casefold(),
            existing_brand.casefold(),
            existing_variant.casefold(),
        )

        if existing_identity == target_identity:
            st.session_state[
                f"cart_{item_id}_catalogue_offer_key"
            ] = offer_key
            return item_id

    return None


def add_catalogue_product_to_cart(
    shop_id,
    shop_name,
    product_id,
    name,
    normalized_name,
    unit,
    brand=None,
    variant=None,
):
    """Add one base unit or increment the existing catalogue cart row."""

    selected_shop = st.session_state.get("customer_shop_selector")

    if selected_shop and int(selected_shop[0]) != int(shop_id):
        st.session_state["catalogue_cart_notice"] = (
            "Choose the checkout shop before adding products from it."
        )
        return

    st.session_state["customer_shop_selector"] = (
        int(shop_id),
        str(shop_name),
    )

    item_id = _find_catalogue_cart_item(
        shop_id,
        product_id,
        name,
        normalized_name,
        unit,
        brand,
        variant,
    )

    if item_id is not None:
        quantity_key = f"cart_{item_id}_quantity"
        current_quantity = float(
            st.session_state.get(quantity_key, 0) or 0
        )
        new_quantity = current_quantity + 1.0
        st.session_state[quantity_key] = new_quantity

    else:
        # Reuse a completely empty row, if one exists.
        item_id = None

        for candidate_id in st.session_state.cart_item_ids:
            fields = (
                "name",
                "normalized",
                "unit",
                "brand",
                "variant",
            )
            row_values = [
                str(
                    st.session_state.get(
                        f"cart_{candidate_id}_{field}", ""
                    )
                ).strip()
                for field in fields
            ]

            try:
                quantity = float(
                    st.session_state.get(
                        f"cart_{candidate_id}_quantity", 0
                    ) or 0
                )
            except (TypeError, ValueError):
                quantity = 0

            if not any(row_values) and quantity == 0:
                item_id = candidate_id
                break

        if item_id is None:
            add_cart_item()
            item_id = st.session_state.cart_item_ids[-1]

        st.session_state[f"cart_{item_id}_name"] = name
        st.session_state[f"cart_{item_id}_normalized"] = (
            normalized_name or name.casefold()
        )
        st.session_state[f"cart_{item_id}_quantity"] = 1.0
        st.session_state[f"cart_{item_id}_unit"] = unit
        st.session_state[f"cart_{item_id}_brand"] = brand or ""
        st.session_state[f"cart_{item_id}_variant"] = variant or ""
        st.session_state[
            f"cart_{item_id}_needs_clarification"
        ] = False
        st.session_state[
            f"cart_{item_id}_clarification_reason"
        ] = ""
        st.session_state[
            f"cart_{item_id}_clarification_resolved"
        ] = False
        st.session_state[
            f"cart_{item_id}_catalogue_offer_key"
        ] = _catalogue_offer_key(shop_id, product_id)

        new_quantity = 1.0

    _invalidate_cart_previews()

    st.session_state["catalogue_cart_notice"] = (
        f"{name}: {new_quantity:g} {unit} in your cart."
    )


def remove_catalogue_product_from_cart(shop_id, product_id):
    """Decrease one base unit; remove the row when it reaches zero."""

    offer_key = _catalogue_offer_key(shop_id, product_id)

    for item_id in list(st.session_state.cart_item_ids):
        if (
            st.session_state.get(
                f"cart_{item_id}_catalogue_offer_key"
            ) != offer_key
        ):
            continue

        quantity_key = f"cart_{item_id}_quantity"
        quantity = float(
            st.session_state.get(quantity_key, 0) or 0
        )

        if quantity <= 1:
            remove_cart_item(item_id)
            st.session_state["catalogue_cart_notice"] = (
                "Product removed from your cart."
            )
        else:
            new_quantity = quantity - 1.0
            st.session_state[quantity_key] = new_quantity
            _invalidate_cart_previews()
            st.session_state["catalogue_cart_notice"] = (
                f"Cart quantity updated to {new_quantity:g}."
            )

        return


def get_catalogue_product_quantity(shop_id, product):
    """Return the quantity currently selected for this shop's product."""

    product_id = product.get("product_id")
    if product_id is None:
        return 0.0

    item_id = _find_catalogue_cart_item(
        shop_id=shop_id,
        product_id=product_id,
        name=product.get("name", ""),
        normalized_name=product.get("normalized_name"),
        unit=product.get("base_unit", ""),
        brand=product.get("brand"),
        variant=product.get("variant"),
    )

    if item_id is None:
        return 0.0

    return float(
        st.session_state.get(
            f"cart_{item_id}_quantity", 0
        ) or 0
    )


def render_catalogue_quantity_control(
    shop_id,
    shop_name,
    product,
    key_prefix,
):
    """Render Add or the minus / quantity / plus control for one offer."""

    if not product.get("is_available"):
        st.caption("Not available")
        return

    product_id = product.get("product_id")
    if product_id is None:
        st.caption("Unavailable")
        return

    selected_shop = st.session_state.get("customer_shop_selector")
    selected_shop_id = (
        int(selected_shop[0]) if selected_shop else None
    )

    if selected_shop_id != int(shop_id):
        st.button(
            "Choose shop",
            key=f"{key_prefix}_choose_shop",
            disabled=_active_cart_item_count() > 0,
            help=(
                "Choose this shop before adding its products. "
                "Empty your cart first to switch shops."
            ),
            on_click=select_catalogue_offer_shop,
            args=(int(shop_id), str(shop_name)),
            use_container_width=True,
        )
        return

    quantity = get_catalogue_product_quantity(shop_id, product)

    callback_args = (
        int(shop_id),
        str(shop_name),
        int(product_id),
        str(product.get("name", "")),
        str(
            product.get("normalized_name")
            or product.get("name", "")
        ),
        str(product.get("base_unit", "")),
        product.get("brand"),
        product.get("variant"),
    )

    if quantity <= 0:
        st.button(
            "Add +",
            key=f"{key_prefix}_add",
            type="primary",
            on_click=add_catalogue_product_to_cart,
            args=callback_args,
            use_container_width=True,
        )
        return

    minus_col, quantity_col, plus_col = st.columns([1, 1, 1])

    with minus_col:
        st.button(
            "−",
            key=f"{key_prefix}_minus",
            on_click=remove_catalogue_product_from_cart,
            args=(int(shop_id), int(product_id)),
            use_container_width=True,
        )

    with quantity_col:
        st.markdown(
            f"<div style='text-align:center; padding-top:0.35rem; "
            f"font-weight:700'>{quantity:g}</div>",
            unsafe_allow_html=True,
        )

    with plus_col:
        st.button(
            "＋",
            key=f"{key_prefix}_plus",
            on_click=add_catalogue_product_to_cart,
            args=callback_args,
            use_container_width=True,
        )


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

voice_audio = st.audio_input(
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
    st.markdown("### Browse grocery products")
    st.caption(
        "Search products across all customer-visible shops, or open "
        "a shop catalogue when you want to browse its products."
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

    cart_has_active_items = _active_cart_item_count() > 0

    selected_shop = st.selectbox(
        "Shop for checkout",
        options=shop_options,
        format_func=lambda option: option[1],
        key="customer_shop_selector",
        disabled=cart_has_active_items,
    )

    if cart_has_active_items:
        st.caption(
            "Remove all cart items before changing the checkout shop."
        )

    shop_id = int(selected_shop[0])
    selected_shop_name = selected_shop[1]

    st.caption(f"Current checkout shop: {selected_shop_name}")

    cart_notice = st.session_state.pop("catalogue_cart_notice", None)
    if cart_notice:
        st.success(cart_notice)

    # Keep catalogue expansion scoped to the currently selected shop.
    previous_shop_id = st.session_state.get(
        "_catalogue_previous_shop_id"
    )

    if previous_shop_id != shop_id:
        st.session_state["global_catalogue_selected_shop_expanded"] = False
        st.session_state["single_shop_catalogue_expanded"] = False
        st.session_state["_catalogue_previous_shop_id"] = shop_id

    st.session_state.setdefault(
        "global_catalogue_selected_shop_expanded", False
    )
    st.session_state.setdefault(
        "single_shop_catalogue_expanded", False
    )

    browse_mode = st.radio(
        "Browse catalogue",
        options=["All shops", "By shop"],
        horizontal=True,
        key="customer_catalogue_browse_mode",
    )

    st.divider()

    if browse_mode == "All shops":
        st.markdown("#### Search products across shops")

        search_col, browse_col = st.columns([3, 2])

        with search_col:
            search_term = st.text_input(
                "Search products",
                placeholder="e.g. rice, milk, sunflower oil",
                key="global_catalogue_search",
            ).strip().casefold()

        with browse_col:
            st.write("")
            expanded = st.session_state[
                "global_catalogue_selected_shop_expanded"
            ]
            button_label = (
                "Close selected shop catalogue"
                if expanded
                else "Show all available products in this shop"
            )

            if st.button(
                button_label,
                key="toggle_global_shop_catalogue",
                use_container_width=True,
            ):
                st.session_state[
                    "global_catalogue_selected_shop_expanded"
                ] = not expanded

        try:
            global_catalogue = fetch_public_global_catalogue(
                backend_url
            )
        except (requests.RequestException, ValueError) as exc:
            st.error(
                "Could not load the combined catalogue. "
                f"Details: {exc}"
            )
            st.stop()

        global_products = global_catalogue["products"]
        visible_shop_count = global_catalogue["shop_count"]

        st.caption(
            f"{visible_shop_count} customer-visible shop(s) | "
            f"{len(global_products)} product group(s)"
        )

        # Show matching products only after the customer searches.
        if search_term:
            filtered_products = []

            for product in global_products:
                searchable_text = " ".join(
                    str(value)
                    for value in (
                        product.get("name"),
                        product.get("normalized_name"),
                        product.get("brand"),
                        product.get("variant"),
                        product.get("base_unit"),
                    )
                    if value
                ).casefold()

                if search_term in searchable_text:
                    filtered_products.append(product)

            st.markdown("**Search results**")

            if not filtered_products:
                st.info("No matching products found. Try another search.")

            for product_index, product in enumerate(filtered_products):
                details = [
                    str(value).strip()
                    for value in (
                        product.get("brand"),
                        product.get("variant"),
                    )
                    if value and str(value).strip()
                ]
                description = " | ".join(details)
                unit = product.get("base_unit", "")
                offers = product.get("offers", [])

                available_offers = [
                    offer for offer in offers
                    if offer.get("is_available")
                ]

                lowest_price = (
                    min(
                        float(offer["price_rupees"])
                        for offer in available_offers
                    )
                    if available_offers else None
                )

                product_col, availability_col = st.columns([3, 2])

                with product_col:
                    st.markdown(
                        f"**{product.get('name', 'Product')}**"
                    )
                    st.caption(
                        f"{description + ' | ' if description else ''}"
                        f"{unit}"
                    )

                with availability_col:
                    if lowest_price is not None:
                        st.write(
                            f"From ₹{lowest_price:,.2f} / {unit}"
                        )
                        st.caption(
                            f"{len(available_offers)} available "
                            f"shop offer(s)"
                        )
                    else:
                        st.warning("Currently unavailable")

                if offers:
                    with st.expander(
                        f"Compare {len(offers)} shop offer(s)",
                        expanded=False,
                    ):
                        for offer_index, offer in enumerate(offers):
                            offer_shop_id = int(offer["shop_id"])
                            offer_shop_name = str(offer["shop_name"])

                            shop_col, price_col, status_col, choose_col = (
                                st.columns([3, 2, 2, 2])
                            )

                            with shop_col:
                                st.write(offer_shop_name)

                            with price_col:
                                st.write(
                                    f"₹{float(offer['price_rupees']):,.2f}"
                                    f" / {unit}"
                                )

                            with status_col:
                                if offer.get("is_available"):
                                    st.success("Available")
                                else:
                                    st.warning("Unavailable")

                            with choose_col:
                                offer_product = {
                                    **product,
                                    "product_id": offer.get("product_id"),
                                    "is_available": offer.get("is_available"),
                                }

                                render_catalogue_quantity_control(
                                    offer_shop_id,
                                    offer_shop_name,
                                    offer_product,
                                    key_prefix=(
                                        f"global_search_{product_index}_"
                                        f"{offer_index}_{offer_shop_id}"
                                    ),
                                )

                st.divider()

        else:
            st.info(
                "Search for a product to compare prices and availability "
                "across shops. Use the button above to browse every "
                "available product in your selected shop."
            )

        # Optional compact list of all available products in selected shop.
        if st.session_state[
            "global_catalogue_selected_shop_expanded"
        ]:
            st.markdown(
                f"#### Available products at {selected_shop_name}"
            )

            selected_shop_products = []

            for product in global_products:
                matching_offers = [
                    offer
                    for offer in product.get("offers", [])
                    if (
                        int(offer["shop_id"]) == shop_id
                        and offer.get("is_available")
                    )
                ]

                for offer in matching_offers:
                    selected_shop_products.append((product, offer))

            if not selected_shop_products:
                st.info(
                    "This shop has no currently available products."
                )
            else:
                st.caption(
                    f"{len(selected_shop_products)} available product(s)"
                )

                for product_index, (product, offer) in enumerate(
                    selected_shop_products
                ):
                    product_col, price_col, status_col, action_col = (
                        st.columns([4, 2, 2, 2])
                    )

                    details = [
                        str(value).strip()
                        for value in (
                            product.get("brand"),
                            product.get("variant"),
                        )
                        if value and str(value).strip()
                    ]

                    with product_col:
                        st.write(f"**{product.get('name', 'Product')}**")
                        st.caption(
                            f"{' | '.join(details) + ' | ' if details else ''}"
                            f"{product.get('base_unit', '')}"
                        )

                    with price_col:
                        st.write(
                            f"₹{float(offer['price_rupees']):,.2f}"
                            f" / {product.get('base_unit', '')}"
                        )

                    with status_col:
                        st.success("Available")

                    with action_col:
                        offer_product = {
                            **product,
                            "product_id": offer.get("product_id"),
                            "is_available": offer.get("is_available"),
                        }

                        render_catalogue_quantity_control(
                            shop_id,
                            selected_shop_name,
                            offer_product,
                            key_prefix=(
                                f"global_shop_list_{product_index}_{shop_id}"
                            ),
                        )

                    st.divider()

    else:
        st.markdown("#### Browse this shop's catalogue")

        expanded = st.session_state[
            "single_shop_catalogue_expanded"
        ]
        button_label = (
            "Close shop catalogue"
            if expanded
            else "Show all products"
        )

        if st.button(
            button_label,
            key="toggle_single_shop_catalogue",
            use_container_width=False,
        ):
            st.session_state[
                "single_shop_catalogue_expanded"
            ] = not expanded

        if st.session_state["single_shop_catalogue_expanded"]:
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
                st.info("This shop has not listed any products yet.")
            else:
                st.caption(
                    f"{len(catalogue_items)} product(s) listed by "
                    f"{selected_shop_name}"
                )

                for product_index, product in enumerate(catalogue_items):
                    product_col, price_col, status_col, action_col = (
                        st.columns([4, 2, 2, 2])
                    )

                    details = [
                        str(value).strip()
                        for value in (
                            product.get("brand"),
                            product.get("variant"),
                        )
                        if value and str(value).strip()
                    ]

                    with product_col:
                        st.write(f"**{product.get('name', 'Product')}**")
                        st.caption(
                            f"{' | '.join(details) + ' | ' if details else ''}"
                            f"{product.get('base_unit', '')}"
                        )

                    with price_col:
                        price = product.get("price_rupees")
                        if price is not None:
                            st.write(
                                f"₹{float(price):,.2f} / "
                                f"{product.get('base_unit', '')}"
                            )

                    with status_col:
                        if product.get("is_available"):
                            st.success("Available")
                        else:
                            st.warning("Unavailable")

                    with action_col:
                        render_catalogue_quantity_control(
                            shop_id,
                            selected_shop_name,
                            product,
                            key_prefix=(
                                f"by_shop_{product_index}_"
                                f"{product.get('product_id')}"
                            ),
                        )

                    st.divider()

            st.caption(
                "Prices and availability are specific to this shop. "
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
        f"{_active_cart_item_count()} item(s) "
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
                disabled=False,
                on_click=remove_cart_item,
                args=(item_id,),
                use_container_width=True,
            )

        name_col, normalized_col = st.columns(2)

        with name_col:
            st.text_input(
                "Product name",
                value="",
                placeholder="e.g. rice, milk, dragon fruit",
                key=f"cart_{item_id}_name",
            )

        with normalized_col:
            st.text_input(
                "Standard product name (optional)",
                value="",
                placeholder="Optional standardized name",
                key=f"cart_{item_id}_normalized",
            )

        quantity_col, unit_col = st.columns(2)

        with quantity_col:
            st.number_input(
                "Quantity",
                min_value=0.0,
                value=0.0,
                step=1.0,
                key=f"cart_{item_id}_quantity",
            )

        with unit_col:
            st.text_input(
                "Unit",
                value="",
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
                    # Keep the result safe when the backend request fails.
                    comparison = None

                    # Compare against every customer-visible shop with one
                    # backend request instead of one request per shop.
                    with st.spinner(
                        "Comparing your cart across customer-visible shops..."
                    ):
                        response = requests.post(
                            f"{backend_url}/inventory/compare",
                            json=current_order_payload,
                            headers={"X-API-Key": api_token},
                            timeout=(5, 30),
                        )

                    if not response.ok:
                        try:
                            detail = response.json().get(
                                "detail",
                                "Bulk inventory comparison failed.",
                            )
                        except ValueError:
                            detail = (
                                "The backend returned an invalid response."
                            )

                        st.error(
                            f"Inventory comparison failed "
                            f"({response.status_code}): {detail}"
                        )

                    else:
                        result = response.json()

                        if not isinstance(result, dict):
                            raise ValueError(
                                "Invalid bulk comparison response format."
                            )

                        comparison = {
                            "full_matches": [],
                            "partial_matches": [],
                            "failed": [],
                            "checked_count": 0,
                            "shop_count": 0,
                            "signature": current_signature,
                        }

                        result_status = result.get("status")

                        if result_status == "needs_clarification":
                            st.warning(
                                result.get(
                                    "message",
                                    "Clarify the grocery request before "
                                    "comparing shops.",
                                )
                            )

                        elif result_status != "completed":
                            raise ValueError(
                                "The backend returned an unexpected "
                                "comparison status."
                            )

                        else:
                            for field in (
                                "full_matches",
                                "partial_matches",
                                "failed",
                            ):
                                values = result.get(field, [])

                                if (
                                    not isinstance(values, list)
                                    or not all(
                                        isinstance(item, dict)
                                        for item in values
                                    )
                                ):
                                    raise ValueError(
                                        f"Invalid '{field}' in comparison "
                                        "response."
                                    )

                                comparison[field] = values

                            shop_count = result.get("shop_count")
                            checked_count = result.get("checked_count")

                            if (
                                not isinstance(shop_count, int)
                                or not isinstance(checked_count, int)
                                or shop_count < 0
                                or checked_count < 0
                            ):
                                raise ValueError(
                                    "Invalid shop counts in comparison "
                                    "response."
                                )

                            comparison["shop_count"] = shop_count
                            comparison["checked_count"] = checked_count

                            # Keep cheapest complete-cart match first.
                            comparison["full_matches"].sort(
                                key=lambda item: float(
                                    item["total_rupees"]
                                )
                            )

                            if shop_count == 0:
                                st.info(
                                    "No customer-visible shops are "
                                    "currently available to compare."
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

# Customer account menu — profile and saved delivery addresses
# ------------------------------------------------------------
customer_token = st.session_state.get("customer_access_token")
customer_profile = st.session_state.get("customer_profile") or {}

# Login returns a compact profile; fetch the complete profile once per token.
if (
    customer_token
    and st.session_state.get("customer_profile_details_loaded_token")
    != customer_token
):
    try:
        complete_profile_response = requests.get(
            f"{backend_url}/customers/me",
            headers={
                "Authorization": f"Bearer {customer_token}"
            },
            timeout=15,
        )

        if complete_profile_response.ok:
            customer_profile = complete_profile_response.json()
            st.session_state.customer_profile = customer_profile
            st.session_state.customer_profile_details_loaded_token = (
                customer_token
            )
        elif complete_profile_response.status_code == 401:
            _clear_customer_session()
            st.error("Your session expired. Please sign in again.")
            st.rerun()
        else:
            st.warning("Some profile details could not be loaded.")

    except requests.RequestException:
        st.warning("Could not refresh your customer profile.")

customer_profile = st.session_state.get("customer_profile") or {}
customer_id = customer_profile.get("customer_id")
if customer_token:
    with st.popover("👤 My Account"):
        st.markdown("### Customer profile")
        st.caption(customer_profile.get("email", ""))

        with st.form("customer_profile_update_form"):
            updated_name = st.text_input(
                "Full name",
                value=customer_profile.get("full_name", ""),
                max_chars=150,
            )
            updated_phone = st.text_input(
                "Phone number",
                value=customer_profile.get("phone") or "",
                max_chars=20,
            )
            language_options = {
                "English (India)": "en-IN",
                "Telugu (India)": "te-IN",
                "Hindi (India)": "hi-IN",
                "Tamil (India)": "ta-IN",
                "Kannada (India)": "kn-IN",
                "Malayalam (India)": "ml-IN",
            }
            current_language = customer_profile.get(
                "preferred_language", "en-IN"
            )
            language_labels = list(language_options.keys())
            selected_language = next(
                (
                    label
                    for label, code in language_options.items()
                    if code == current_language
                ),
                "English (India)",
            )
            chosen_language = st.selectbox(
                "Preferred language",
                options=language_labels,
                index=language_labels.index(selected_language),
            )

            save_profile = st.form_submit_button(
                "Save profile",
                use_container_width=True,
            )

        if save_profile:
            try:
                profile_response = requests.patch(
                    f"{backend_url}/customers/me",
                    json={
                        "full_name": updated_name,
                        "phone": updated_phone.strip() or None,
                        "preferred_language": language_options[chosen_language],
                    },
                    headers={
                        "Authorization": f"Bearer {customer_token}"
                    },
                    timeout=15,
                )

                if profile_response.ok:
                    st.session_state.customer_profile = (
                        profile_response.json()
                    )
                    st.success("Your profile has been updated.")
                    st.rerun()
                else:
                    st.error(
                        _customer_error_detail(
                            profile_response,
                            "Could not update your profile.",
                        )
                    )
            except requests.RequestException as exc:
                st.error(f"Profile update failed: {exc}")

        st.divider()
        st.markdown("### Saved delivery addresses")

        # Keep the address cache tied to the currently signed-in customer.
        if (
            st.session_state.get("customer_addresses_customer_id")
            != customer_id
        ):
            st.session_state.customer_addresses_customer_id = customer_id
            st.session_state.customer_addresses = None

        if st.session_state.get("customer_addresses") is None:
            try:
                address_response = requests.get(
                    f"{backend_url}/customers/addresses",
                    headers={
                        "Authorization": f"Bearer {customer_token}"
                    },
                    timeout=15,
                )

                if address_response.ok:
                    st.session_state.customer_addresses = (
                        address_response.json()
                    )
                elif address_response.status_code == 401:
                    _clear_customer_session()
                    st.error("Your session expired. Please sign in again.")
                    st.rerun()
                else:
                    st.error(
                        _customer_error_detail(
                            address_response,
                            "Could not load saved addresses.",
                        )
                    )
            except requests.RequestException as exc:
                st.error(f"Address loading failed: {exc}")

        def refresh_customer_addresses():
            try:
                response = requests.get(
                    f"{backend_url}/customers/addresses",
                    headers={
                        "Authorization": f"Bearer {customer_token}"
                    },
                    timeout=15,
                )
                response.raise_for_status()
                st.session_state.customer_addresses = response.json()

            except (requests.RequestException, ValueError):
                # The address mutation may have succeeded even if refreshing fails.
                # Clear the cache so the next rerun attempts to load the latest data.
                st.session_state.customer_addresses = None

        addresses = st.session_state.get("customer_addresses")

        if addresses:
            for address in addresses:
                address_id = address["address_id"]

                with st.expander(
                    f"{address['label']} "
                    + ("⭐ Default address" if address["is_default"] else "")
                ):
                    st.write(f"**{address['recipient_name']}**")
                    st.write(address["phone"])
                    st.write(address["address_line1"])

                    if address.get("address_line2"):
                        st.write(address["address_line2"])

                    if address.get("landmark"):
                        st.caption(f"Landmark: {address['landmark']}")

                    st.write(
                        f"{address['city']}, {address['state']} "
                        f"{address['postal_code']}"
                    )
                    st.caption(address["country"])

                    with st.form(f"edit_address_form_{address_id}"):
                        st.markdown("**Edit this address**")

                        edit_label = st.text_input(
                            "Address label",
                            value=address["label"],
                            key=f"edit_label_{address_id}",
                        )
                        edit_recipient = st.text_input(
                            "Recipient name",
                            value=address["recipient_name"],
                            key=f"edit_recipient_{address_id}",
                        )
                        edit_phone = st.text_input(
                            "Contact phone",
                            value=address["phone"],
                            key=f"edit_phone_{address_id}",
                        )
                        edit_line1 = st.text_input(
                            "Address line 1",
                            value=address["address_line1"],
                            key=f"edit_line1_{address_id}",
                        )
                        edit_line2 = st.text_input(
                            "Address line 2",
                            value=address.get("address_line2") or "",
                            key=f"edit_line2_{address_id}",
                        )
                        edit_landmark = st.text_input(
                            "Landmark",
                            value=address.get("landmark") or "",
                            key=f"edit_landmark_{address_id}",
                        )
                        edit_city = st.text_input(
                            "City",
                            value=address["city"],
                            key=f"edit_city_{address_id}",
                        )
                        edit_state = st.text_input(
                            "State",
                            value=address["state"],
                            key=f"edit_state_{address_id}",
                        )
                        edit_postal = st.text_input(
                            "Postal code",
                            value=address["postal_code"],
                            key=f"edit_postal_{address_id}",
                        )
                        edit_country = st.text_input(
                            "Country",
                            value=address["country"],
                            key=f"edit_country_{address_id}",
                        )
                        edit_default = st.checkbox(
                            "Make this my default address",
                            value=bool(address["is_default"]),
                            key=f"edit_default_{address_id}",
                        )

                        save_address = st.form_submit_button(
                            "Save address",
                            use_container_width=True,
                        )

                    if save_address:
                        address_payload = {
                            "label": edit_label,
                            "recipient_name": edit_recipient,
                            "phone": edit_phone,
                            "address_line1": edit_line1,
                            "address_line2": edit_line2 or None,
                            "landmark": edit_landmark or None,
                            "city": edit_city,
                            "state": edit_state,
                            "postal_code": edit_postal,
                            "country": edit_country,
                            "is_default": edit_default,
                        }

                        try:
                            update_response = requests.put(
                                f"{backend_url}/customers/addresses/{address_id}",
                                json=address_payload,
                                headers={
                                    "Authorization": f"Bearer {customer_token}"
                                },
                                timeout=15,
                            )

                            if update_response.ok:
                                refresh_customer_addresses()
                                st.success("Address updated.")
                                st.rerun()
                            else:
                                st.error(
                                    _customer_error_detail(
                                        update_response,
                                        "Could not update this address.",
                                    )
                                )
                        except requests.RequestException as exc:
                            st.error(f"Address update failed: {exc}")

                    if st.button(
                        "Delete address",
                        key=f"delete_address_{address_id}",
                        use_container_width=True,
                    ):
                        try:
                            delete_response = requests.delete(
                                f"{backend_url}/customers/addresses/{address_id}",
                                headers={
                                    "Authorization": f"Bearer {customer_token}"
                                },
                                timeout=15,
                            )

                            if delete_response.ok:
                                refresh_customer_addresses()
                                st.success("Address deleted.")
                                st.rerun()
                            else:
                                st.error(
                                    _customer_error_detail(
                                        delete_response,
                                        "Could not delete this address.",
                                    )
                                )
                        except requests.RequestException as exc:
                            st.error(f"Address deletion failed: {exc}")
        elif addresses == []:
            st.info("You haven't saved a delivery address yet.")

        with st.expander("➕ Add a delivery address"):
            with st.form("add_customer_address_form"):
                new_label = st.selectbox(
                    "Address label",
                    ["Home", "Work", "Other"],
                )
                new_recipient = st.text_input("Recipient name")
                new_phone = st.text_input("Contact phone")
                new_line1 = st.text_input("Address line 1")
                new_line2 = st.text_input("Address line 2")
                new_landmark = st.text_input("Landmark")
                new_city = st.text_input("City")
                new_state = st.text_input("State")
                new_postal = st.text_input("Postal code")
                new_country = st.text_input("Country", value="India")
                new_default = st.checkbox(
                    "Make this my default address",
                    value=not bool(addresses),
                )

                add_address = st.form_submit_button(
                    "Add address",
                    use_container_width=True,
                )

            if add_address:
                address_payload = {
                    "label": new_label,
                    "recipient_name": new_recipient,
                    "phone": new_phone,
                    "address_line1": new_line1,
                    "address_line2": new_line2 or None,
                    "landmark": new_landmark or None,
                    "city": new_city,
                    "state": new_state,
                    "postal_code": new_postal,
                    "country": new_country,
                    "is_default": new_default,
                }

                try:
                    create_response = requests.post(
                        f"{backend_url}/customers/addresses",
                        json=address_payload,
                        headers={
                            "Authorization": f"Bearer {customer_token}"
                        },
                        timeout=15,
                    )

                    if create_response.status_code == 201:
                        refresh_customer_addresses()
                        st.success("Delivery address saved.")
                        st.rerun()
                    else:
                        st.error(
                            _customer_error_detail(
                                create_response,
                                "Could not save this address.",
                            )
                        )
                except requests.RequestException as exc:
                    st.error(f"Address creation failed: {exc}")


    if st.button(
        "Sign out",
        key="customer_sign_out_button",
    ):
        _sign_out_customer(cookie_manager, backend_url)
        st.rerun()

st.markdown("### Confirm your order")
st.caption(
    "Review your validated grocery request before saving it to your customer account."
)

pending_payload = st.session_state.pending_order_payload
pending_shop_id = st.session_state.pending_shop_id
customer_token = st.session_state.get("customer_access_token")

with st.container(border=True):
    if pending_payload and pending_shop_id:
        st.success("Inventory check passed. Your order is ready for confirmation.")
        st.warning(
            "Nothing has been saved yet. Confirm below to save a pending order request. "
            "Stock will not be reserved, and shop acceptance is not guaranteed."
        )

        if not customer_token:
            st.info("Sign in or create a customer account above to save this order.")

        if st.button(
            "Confirm and save order",
            type="primary",
            use_container_width=True,
            key="confirm_and_save_order_button",
            disabled=not bool(customer_token),
        ):
            try:
                with st.spinner("Rechecking inventory and saving your order..."):
                    confirm_response = requests.post(
                        f"{backend_url}/customers/orders/confirm",
                        params={"shop_id": int(pending_shop_id)},
                        json=pending_payload,
                        headers={
                            "Authorization": f"Bearer {customer_token}",
                            "Idempotency-Key": st.session_state.pending_idempotency_key,
                        },
                        timeout=20,
                    )

                if confirm_response.ok:
                    st.session_state.saved_order_result = confirm_response.json()
                    st.session_state.pending_order_payload = None
                    st.session_state.pending_shop_id = None
                    st.session_state.pending_idempotency_key = None
                    st.session_state.order_history_result = None
                    st.session_state.order_lookup_result = None
                    st.session_state.order_lookup_customer_id = None
                    st.rerun()
                elif confirm_response.status_code == 401:
                    _clear_customer_session()
                    st.error("Your customer session expired. Please sign in again to save this order.")
                else:
                    st.error(
                        f"Order was not saved ({confirm_response.status_code}): "
                        f"{_customer_error_detail(confirm_response, 'The order could not be saved.')}"
                    )

            except requests.RequestException as exc:
                st.error(f"Could not connect to the backend: {exc}")

    elif st.session_state.saved_order_result:
        saved = st.session_state.saved_order_result
        rupee = chr(0x20B9)

        st.success("Your customer order request has been saved successfully.")
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
            "Your cart must pass inventory validation before an order can be confirmed. "
            "The confirmation button appears when every requested item is eligible."
        )

# ---------------------------------------------------------
# Customer order history
# ---------------------------------------------------------

st.markdown("### Your order history")
st.caption("View the orders saved under your customer account, across shops.")

customer_token = st.session_state.get("customer_access_token")
customer_profile = st.session_state.get("customer_profile") or {}

with st.container(border=True):
    st.markdown("**Your saved orders**")

    if not customer_token:
        st.info("Sign in to your customer account to view your personal order history.")
    elif st.button(
        "Load order history",
        key="load_order_history_button",
        use_container_width=True,
    ):
        try:
            with st.spinner("Loading your saved orders..."):
                history_response = requests.get(
                    f"{backend_url}/customers/orders",
                    headers={"Authorization": f"Bearer {customer_token}"},
                    timeout=15,
                )

            if history_response.ok:
                st.session_state.order_history_result = history_response.json()
            elif history_response.status_code == 401:
                _clear_customer_session()
                st.error("Your customer session expired. Please sign in again.")
            else:
                st.error(
                    f"History request failed ({history_response.status_code}): "
                    f"{_customer_error_detail(history_response, 'Could not load your order history.')}"
                )

        except requests.RequestException as exc:
            st.error(f"Could not connect to the backend: {exc}")

history = st.session_state.get("order_history_result")
customer_profile = st.session_state.get("customer_profile") or {}
current_customer_id = customer_profile.get("customer_id")

if (
    history
    and current_customer_id is not None
    and history.get("customer_id") == current_customer_id
):
    orders = history.get("orders", [])

    if not orders:
        st.info("You do not have any customer orders yet.")
    else:
        st.caption(f"{history.get('count', len(orders))} order(s) in your account history")
        rupee = chr(0x20B9)
        shop_name_by_id = dict(shop_options)

        history_status_labels = {
            "pending": "Pending - awaiting shop acceptance",
            "confirmed": "Confirmed",
            "accepted": "Accepted by shop",
            "completed": "Completed",
            "cancelled": "Cancelled",
            "rejected": "Rejected by shop",
        }

        for saved_order in orders:
            status = str(saved_order.get("status", "unknown")).lower()
            status_label = history_status_labels.get(
                status,
                status.replace("_", " ").title(),
            )
            order_shop_id = saved_order.get("shop_id")
            shop_label = shop_name_by_id.get(
                order_shop_id,
                f"Shop #{order_shop_id}" if order_shop_id is not None else "Unknown shop",
            )

            with st.container(border=True):
                reference_col, status_col = st.columns([3, 2])
                with reference_col:
                    st.caption("ORDER REFERENCE")
                    st.markdown(f"**{saved_order.get('order_reference', 'Unknown')}**")
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

                st.caption(f"Shop: {shop_label}")
                st.metric(
                    "Order total",
                    f"{rupee}{float(saved_order.get('total_rupees', 0)):,.2f}",
                )
                st.caption(f"Created: {saved_order.get('created_at', 'Unknown')}")

                with st.expander("View ordered products"):
                    for saved_item in saved_order.get("items", []):
                        product_label = saved_item.get("product_name", "Unknown product")
                        if saved_item.get("brand"):
                            product_label += f" ({saved_item['brand']})"
                        if saved_item.get("variant"):
                            product_label += f" - {saved_item['variant']}"

                        unit_price = float(saved_item.get("unit_price_paise", 0)) / 100
                        line_total = float(saved_item.get("line_total_paise", 0)) / 100
                        st.write(
                            f"{product_label}: {saved_item.get('quantity', '?')} "
                            f"{saved_item.get('unit', '')} x {rupee}{unit_price:.2f} = "
                            f"{rupee}{line_total:.2f}"
                        )

# ---------------------------------------------------------
# Customer order lookup
# ---------------------------------------------------------

st.markdown("### Track an order")
st.caption(
    "Look up an order saved under your signed-in customer account. "
    "Order cancellation is currently handled by the shop."
)

customer_token = st.session_state.get("customer_access_token")
customer_profile = st.session_state.get("customer_profile") or {}
current_customer_id = customer_profile.get("customer_id")

with st.container(border=True):
    st.markdown("**Track your order**")

    if not customer_token:
        st.info("Sign in to your customer account to look up an order.")
        order_reference_input = ""
        lookup_clicked = False
    else:
        st.write("Enter the order reference you received when your order was saved.")
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
    st.session_state.order_lookup_customer_id = None

    if not order_reference_input:
        st.warning("Enter an order reference first.")
    else:
        try:
            with st.spinner("Looking up your order..."):
                lookup_response = requests.get(
                    f"{backend_url}/customers/orders/{quote(order_reference_input, safe='')}",
                    headers={"Authorization": f"Bearer {customer_token}"},
                    timeout=15,
                )

            if lookup_response.ok:
                st.session_state.order_lookup_result = lookup_response.json()
                st.session_state.order_lookup_customer_id = current_customer_id
            elif lookup_response.status_code == 401:
                _clear_customer_session()
                st.error("Your customer session expired. Please sign in again.")
            elif lookup_response.status_code == 404:
                st.error(
                    "This order is not linked to the signed-in customer account. "
                    "It may have been saved before customer sign-in was introduced, "
                    "or it may belong to another customer."
                )
            else:
                st.error(
                    f"Order lookup failed ({lookup_response.status_code}): "
                    f"{_customer_error_detail(lookup_response, 'Could not find your order.')}"
                )

        except requests.RequestException as exc:
            st.error(f"Could not connect to the backend: {exc}")

lookup_result = st.session_state.get("order_lookup_result")
lookup_customer_id = st.session_state.get("order_lookup_customer_id")

if (
    lookup_result
    and current_customer_id is not None
    and lookup_customer_id == current_customer_id
):
    status = str(lookup_result.get("status", "unknown"))
    status_normalized = status.lower()
    rupee = chr(0x20B9)

    with st.container(border=True):
        st.markdown("**Order details**")
        reference_col, status_col = st.columns([3, 2])

        with reference_col:
            st.caption("ORDER REFERENCE")
            st.markdown(f"**{lookup_result.get('order_reference', 'Unknown')}**")

        with status_col:
            st.caption("ORDER STATUS")
            if status_normalized == "pending":
                st.warning("Pending - awaiting shop acceptance")
            elif status_normalized in ("confirmed", "accepted", "completed"):
                st.success(status.replace("_", " ").title())
            elif status_normalized == "rejected":
                st.error("Rejected by shop")
            else:
                st.info(status.replace("_", " ").title())

        st.caption(f"Created: {lookup_result.get('created_at', 'Unknown')}")
        st.metric(
            "Order total",
            f"{rupee}{float(lookup_result.get('total_rupees', 0)):,.2f}",
        )

        if lookup_result.get("rejection_reason"):
            st.warning(f"Shop response: {lookup_result['rejection_reason']}")

        with st.expander("View ordered products", expanded=True):
            for saved_item in lookup_result.get("items", []):
                product_label = saved_item.get("product_name", "Unknown product")
                if saved_item.get("brand"):
                    product_label += f" ({saved_item['brand']})"
                if saved_item.get("variant"):
                    product_label += f" - {saved_item['variant']}"

                quantity = saved_item.get("quantity", "?")
                unit = saved_item.get("unit", "")
                unit_price = float(saved_item.get("unit_price_paise", 0)) / 100
                line_total = float(saved_item.get("line_total_paise", 0)) / 100
                st.write(
                    f"{product_label}: {quantity} {unit} x "
                    f"{rupee}{unit_price:.2f} = {rupee}{line_total:.2f}"
                )

        if status_normalized == "pending":
            st.info(
                "Your order is waiting for shop acceptance. To request cancellation, "
                "please contact the shop. Customer-side cancellation is not enabled yet."
            )
        elif status_normalized == "cancelled":
            st.info("This order has been cancelled.")
        elif status_normalized == "rejected":
            st.info("The shop rejected this order. Check the shop response above for details.")
