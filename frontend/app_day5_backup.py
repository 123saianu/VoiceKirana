
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
    page_icon="🎤",
    layout="centered",
)

st.title("🎤 VoiceKirana")
st.write("Your multilingual grocery assistant.")
st.caption(
    "Enter grocery items, validate the request, "
    "check shop inventory, and hear the response."
)

backend_url = os.getenv(
    "BACKEND_URL",
    "http://127.0.0.1:8000",
).rstrip("/")

api_token = os.getenv("VOICEKIRANA_API_TOKEN")

st.divider()
st.subheader("🛒 Grocery order")

st.info(
    "Current test data: Smoke Test Shop (ID 189). "
    "Rice is the only inventory item currently registered."
)

with st.form("grocery_order_form"):
    shop_id = st.number_input(
        "Shop ID",
        min_value=1,
        value=189,
        step=1,
    )

    language = st.selectbox(
        "Request language",
        ["English", "Telugu", "Hindi", "Other / unspecified"],
    )

    st.markdown("#### Item 1")
    name1 = st.text_input("Product name", value="rice", key="name1")
    normalized1 = st.text_input(
        "Standard product name (optional)",
        value="Rice",
        key="normalized1",
    )
    qty1_col, unit1_col = st.columns(2)
    with qty1_col:
        qty1 = st.number_input(
            "Quantity",
            min_value=0.0,
            value=2.0,
            step=1.0,
            key="qty1",
        )
    with unit1_col:
        unit1 = st.text_input("Unit", value="kg", key="unit1")

    st.markdown("#### Item 2")
    name2 = st.text_input(
        "Product name",
        placeholder="e.g. milk",
        key="name2",
    )
    normalized2 = st.text_input(
        "Standard product name (optional)",
        key="normalized2",
    )
    qty2_col, unit2_col = st.columns(2)
    with qty2_col:
        qty2 = st.number_input(
            "Quantity",
            min_value=0.0,
            value=0.0,
            step=1.0,
            key="qty2",
        )
    with unit2_col:
        unit2 = st.text_input("Unit", key="unit2")

    st.markdown("#### Item 3")
    name3 = st.text_input(
        "Product name",
        placeholder="e.g. sugar",
        key="name3",
    )
    normalized3 = st.text_input(
        "Standard product name (optional)",
        key="normalized3",
    )
    qty3_col, unit3_col = st.columns(2)
    with qty3_col:
        qty3 = st.number_input(
            "Quantity",
            min_value=0.0,
            value=0.0,
            step=1.0,
            key="qty3",
        )
    with unit3_col:
        unit3 = st.text_input("Unit", key="unit3")

    submitted = st.form_submit_button(
        "Validate and check inventory",
        type="primary",
    )

if submitted:
    if not api_token:
        st.error(
            "VOICEKIRANA_API_TOKEN is missing from your .env file."
        )
        st.stop()

    entered_items = [
        (name1, normalized1, qty1, unit1),
        (name2, normalized2, qty2, unit2),
        (name3, normalized3, qty3, unit3),
    ]

    order_items = []
    validation_errors = []

    for index, (name, normalized, quantity, unit) in enumerate(
        entered_items,
        start=1,
    ):
        if not name.strip():
            if quantity > 0 or unit.strip() or normalized.strip():
                validation_errors.append(
                    f"Item {index}: enter a product name."
                )
            continue

        if quantity <= 0:
            validation_errors.append(
                f"Item {index} ({name}): quantity must be greater than zero."
            )
            continue

        if not unit.strip():
            validation_errors.append(
                f"Item {index} ({name}): enter a unit such as kg or litre."
            )
            continue

        item = {
            "raw_name": name.strip(),
            "quantity": quantity,
            "unit": unit.strip(),
            "needs_clarification": False,
        }

        if normalized.strip():
            item["normalized_name"] = normalized.strip()

        order_items.append(item)

    if validation_errors:
        for error in validation_errors:
            st.error(error)
        st.stop()

    if not order_items:
        st.warning("Please enter at least one grocery item.")
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
                f"Validation failed ({preview_response.status_code}): "
                f"{detail}"
            )
            st.stop()

        preview = preview_response.json()

        st.subheader("Order validation")
        st.info(preview.get("message", "Request validated."))
        st.write("**Validation status:**", preview.get("status"))
        st.json(preview.get("order", preview))

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
        message = result.get(
            "message",
            "Inventory check completed.",
        )

        st.divider()
        st.subheader("📦 Inventory results")

        if result.get("status") == "inventory_checked":
            st.success(message)
            st.write("**Shop:**", result.get("shop_name", "Unknown"))
            st.metric(
                "Estimated total",
                f"₹{result['total_rupees']:.2f}",
            )
            st.caption(
                "This is an inventory estimate, not a confirmed purchase."
            )
        else:
            st.warning(message)

        for item_result in result.get("items", []):
            item_name = item_result.get(
                "requested_name",
                "Unknown item",
            )
            item_status = item_result.get("status", "unknown")

            with st.expander(
                f"{item_name} — {item_status.replace('_', ' ').title()}",
                expanded=True,
            ):
                st.write(item_result.get("message", ""))
                if item_status == "matched":
                    st.write(
                        f"Quantity: {item_result['quantity']} "
                        f"{item_result['unit']}"
                    )
                    st.write(
                        f"Unit price: ₹{item_result['unit_price_rupees']:.2f}"
                    )
                    st.write(
                        f"Line total: ₹{item_result['line_total_rupees']:.2f}"
                    )
                    st.write(
                        f"Available stock: "
                        f"{item_result['available_quantity']} "
                        f"{item_result['unit']}"
                    )
                elif item_status == "insufficient_stock":
                    st.write(
                        "Requested quantity:",
                        item_result.get("requested_quantity"),
                    )
                    st.write(
                        "Available quantity:",
                        item_result.get("available_quantity"),
                    )
                elif "candidates" in item_result:
                    st.write("Matching catalogue products:")
                    st.json(item_result["candidates"])

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

