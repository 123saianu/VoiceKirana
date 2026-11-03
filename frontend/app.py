
import os

import requests
import streamlit as st
from dotenv import load_dotenv

from backend.services.timbre_service import (
    synthesize_speech,
    TimbreError,
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
    "http://127.0.0.1:8000",
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

    for field in ("name", "normalized", "quantity", "unit"):
        key = f"cart_{item_id}_{field}"
        if key in st.session_state:
            del st.session_state[key]


st.divider()
st.subheader("🛒 Grocery order")

st.info(
    "Current test data: Smoke Test Shop (ID 203). "
    "Rice is the only inventory item currently registered. "
    "Other products may require clarification or may not be in stock."
)

shop_id = st.number_input(
    "Shop ID",
    min_value=1,
    value=6,
    step=1,
    key="shop_id",
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

    st.divider()

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

        # Ignore a completely untouched empty row.
        if not name and not normalized and not unit and quantity == 0:
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
            None
            if language == "Other / unspecified"
            else language
        ),
        "items": order_items,
        "needs_clarification": False,
    }

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
        else:
            st.session_state.pending_order_payload = None
            st.session_state.pending_shop_id = None

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
                        headers={"X-API-Key": api_token},
                        timeout=20,
                    )

                if confirm_response.ok:
                    st.session_state.saved_order_result = confirm_response.json()
                    st.session_state.pending_order_payload = None
                    st.session_state.pending_shop_id = None
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
