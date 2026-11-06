import hashlib
import json
import os
import secrets
from fastapi.responses import Response
from backend.services.twilio_whatsapp_service import (
    TwilioConfigurationError,
    build_whatsapp_reply,
    validate_twilio_webhook,
)
from backend.models.payment import (
    RazorpayPaymentVerificationRequest,
)

from backend.services.razorpay_service import (
    RazorpayAPIError,
    RazorpayConfigurationError,
    create_razorpay_order,
    fetch_razorpay_payment,
    get_razorpay_public_key_id,
    verify_payment_signature,
    verify_webhook_signature,
)
from dotenv import load_dotenv
from fastapi import Body, Depends, FastAPI, HTTPException, Header, Query, Request, status

from backend.auth_dependencies import (
    authenticate_admin_token,
    get_current_user,
    get_current_customer,
    require_shop_access,
)
from backend.database import get_connection
from backend.models.auth import (
    LoginRequest,
    AdminCreateShopRequest,
    AdminCreateShopOwnerRequest,
    AccessTokenResponse,
    CurrentUserResponse,
)
from backend.models.customer import (
    CustomerRegisterRequest,
    CustomerProfileResponse,
)
from backend.models.catalogue import (
    ShopProductCreate,
    InventoryUpdate,
    ProductAliasCreate,
)
from backend.models.order import GroceryOrder
from backend.security import (
    hash_password,
    verify_password,
    create_access_token,
    create_dummy_password_hash,
    ACCESS_TOKEN_EXPIRE_MINUTES,
)
from backend.services.catalogue_service import (
    get_shop_inventory,
    register_product_for_shop,
    update_shop_inventory,
    add_shop_product_alias,
)
from backend.services.razorpay_service import (
    RazorpayAPIError,
    RazorpayConfigurationError,
    create_razorpay_order,
    get_razorpay_public_key_id,
)
from backend.services.inventory_service import find_inventory


load_dotenv()

VOICEKIRANA_API_TOKEN = os.getenv("VOICEKIRANA_API_TOKEN")

app = FastAPI(
    title="VoiceKirana API",
    description="Backend API for the multilingual VoiceKirana grocery assistant.",
    version="1.0.0",
)


def authenticate_api_key(api_key: str | None) -> None:
    """Reject requests without the configured API token."""
    if not VOICEKIRANA_API_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="API authentication is not configured.",
        )

    if not api_key or not secrets.compare_digest(
        api_key,
        VOICEKIRANA_API_TOKEN,
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API authentication.",
        )


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "voicekirana-backend",
    }


@app.post("/orders/preview")
async def preview_order(
    request: Request,
    api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    """Validate direct orders or JSON-string orders received from Gnani."""
    authenticate_api_key(api_key)

    try:
        payload = await request.json()

        if "order_request" in payload:
            order_payload = payload["order_request"]

            # Gnani may send the order as a JSON string.
            if isinstance(order_payload, str):
                order_payload = json.loads(order_payload)

            if not isinstance(order_payload, dict):
                raise ValueError("order_request must be a JSON object.")

            order_payload.setdefault(
                "intent",
                payload.get("customer_intent", payload.get("intent", "unknown")),
            )
            order_payload.setdefault("language", payload.get("language"))

            if payload.get("additional_instructions") is not None:
                order_payload.setdefault(
                    "additional_instructions",
                    payload["additional_instructions"],
                )
        else:
            order_payload = payload

        order = GroceryOrder.model_validate(order_payload)

    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid order request: {exc}",
        ) from exc

    order_data = order.model_dump()

    if order.needs_clarification:
        return {
            "status": "needs_clarification",
            "message": (
                "Some grocery details are missing or unclear. "
                "Please clarify them before proceeding."
            ),
            "needs_clarification": True,
            "order": order_data,
        }

    return {
        "status": "ready_for_inventory_check",
        "message": (
            "The grocery request has been validated successfully. "
            "Inventory and prices must be checked next."
        ),
        "needs_clarification": False,
        "order": order_data,
    }


def _check_inventory_for_order(order: GroceryOrder, shop_id: int) -> dict:
    """
    Shared inventory-checking logic.

    Can be called directly by confirm_order() or through the
    /inventory/check HTTP endpoint.
    Does not reserve stock or place an order.
    """

    if order.needs_clarification:
        return {
            "status": "needs_clarification",
            "message": "Clarify the grocery request before checking inventory.",
            "needs_clarification": True,
            "items": [],
            "total_paise": None,
            "total_rupees": None,
        }

    if not order.items:
        raise HTTPException(
            status_code=400,
            detail="The grocery order must contain at least one item.",
        )

    # Verify that the requested shop exists and is active.
    with get_connection() as connection:
        shop = connection.execute(
            "SELECT id, name FROM shops WHERE id = ? AND is_active = 1",
            (shop_id,),
        ).fetchone()

    if shop is None:
        raise HTTPException(
            status_code=404,
            detail="Active shop not found.",
        )

    results = []
    total_paise = 0
    all_items_matched = True

    for item in order.items:
        if (
            item.needs_clarification
            or item.quantity is None
            or not item.unit
        ):
            results.append({
                "requested_name": item.raw_name,
                "status": "needs_clarification",
                "message": (
                    item.clarification_reason
                    or "A clear quantity and unit are required."
                ),
            })
            all_items_matched = False
            continue

        search_name = (
            item.normalized_name.strip()
            if item.normalized_name and item.normalized_name.strip()
            else item.raw_name.strip()
        )

        matches = find_inventory(shop_id, search_name)

        if not matches:
            results.append({
                "requested_name": item.raw_name,
                "status": "product_not_found",
                "message": "No exact catalogue match was found.",
            })
            all_items_matched = False
            continue

        # Never silently choose between different brands or variants.
        if len(matches) > 1:
            results.append({
                "requested_name": item.raw_name,
                "status": "ambiguous_match",
                "message": "More than one catalogue product matches.",
                "candidates": [
                    {
                        "product_id": match["product_id"],
                        "name": match["name"],
                        "brand": match["brand"],
                        "variant": match["variant"],
                        "unit": match["base_unit"],
                    }
                    for match in matches
                ],
            })
            all_items_matched = False
            continue

        match = matches[0]

        # No unit conversions are performed in this first version.
        requested_unit = item.unit.strip().casefold()
        catalogue_unit = match["base_unit"].strip().casefold()

        if requested_unit != catalogue_unit:
            results.append({
                "requested_name": item.raw_name,
                "status": "unit_mismatch",
                "message": (
                    f"Requested unit '{item.unit}' does not match "
                    f"catalogue unit '{match['base_unit']}'."
                ),
            })
            all_items_matched = False
            continue

        if item.brand and (
            not match["brand"]
            or item.brand.strip().casefold()
            != match["brand"].strip().casefold()
        ):
            results.append({
                "requested_name": item.raw_name,
                "status": "product_not_found",
                "message": "The requested brand is not an exact match.",
            })
            all_items_matched = False
            continue

        if item.variant and (
            not match["variant"]
            or item.variant.strip().casefold()
            != match["variant"].strip().casefold()
        ):
            results.append({
                "requested_name": item.raw_name,
                "status": "product_not_found",
                "message": "The requested variant is not an exact match.",
            })
            all_items_matched = False
            continue

        if not match["is_available"]:
            results.append({
                "requested_name": item.raw_name,
                "status": "unavailable",
                "message": "This product is marked unavailable by the shop.",
            })
            all_items_matched = False
            continue

        if match["stock_quantity"] < item.quantity:
            results.append({
                "requested_name": item.raw_name,
                "status": "insufficient_stock",
                "available_quantity": match["stock_quantity"],
                "requested_quantity": item.quantity,
                "unit": match["base_unit"],
            })
            all_items_matched = False
            continue

        price_paise = int(match["price_per_unit"])
        line_total_paise = round(price_paise * item.quantity)
        total_paise += line_total_paise

        results.append({
            "requested_name": item.raw_name,
            "status": "matched",
            "product_id": match["product_id"],
            "product_name": match["name"],
            "brand": match["brand"],
            "variant": match["variant"],
            "quantity": item.quantity,
            "unit": match["base_unit"],
            "unit_price_paise": price_paise,
            "unit_price_rupees": round(price_paise / 100, 2),
            "line_total_paise": line_total_paise,
            "line_total_rupees": round(line_total_paise / 100, 2),
            "available_quantity": match["stock_quantity"],
        })

    if not all_items_matched:
        return {
            "status": "needs_attention",
            "message": (
                "Some products need clarification or cannot be fulfilled. "
                "Review the item results before proceeding."
            ),
            "needs_clarification": any(
                result["status"] in {
                    "needs_clarification",
                    "ambiguous_match",
                    "unit_mismatch",
                }
                for result in results
            ),
            "items": results,
            "total_paise": None,
            "total_rupees": None,
        }

    return {
        "status": "inventory_checked",
        "message": (
            "All requested products matched and sufficient stock was found. "
            "No stock has been reserved and no order has been placed."
        ),
        "needs_clarification": False,
        "shop_id": shop_id,
        "shop_name": shop["name"],
        "items": results,
        "total_paise": total_paise,
        "total_rupees": round(total_paise / 100, 2),
        "order_placed": False,
        "stock_reserved": False,
    }


@app.post("/inventory/check")
async def check_inventory(
    payload: dict = Body(
        ...,
        description="Grocery order to check against the selected shop."
    ),
    shop_id: int = Query(gt=0),
    api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    """
    Check requested products against one shop's catalogue.

    Accepts either a direct GroceryOrder or a JSON-string order_request.
    Does not reserve stock or place an order.
    """
    authenticate_api_key(api_key)

    try:
        if "order_request" in payload:
            order_payload = payload["order_request"]

            if isinstance(order_payload, str):
                order_payload = json.loads(order_payload)

            if not isinstance(order_payload, dict):
                raise ValueError(
                    "order_request must be a JSON object."
                )

            order_payload.setdefault(
                "intent",
                payload.get(
                    "customer_intent",
                    payload.get("intent", "place_order"),
                ),
            )

            order_payload.setdefault(
                "language",
                payload.get("language"),
            )

            if payload.get("additional_instructions") is not None:
                order_payload.setdefault(
                    "additional_instructions",
                    payload["additional_instructions"],
                )
        else:
            order_payload = payload

        order = GroceryOrder.model_validate(order_payload)

    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid inventory request: {exc}",
        ) from exc

    return _check_inventory_for_order(
        order=order,
        shop_id=shop_id,
    )


from contextlib import closing


def _order_request_fingerprint(order: GroceryOrder) -> str:
    """Create a stable SHA-256 fingerprint of the complete grocery order."""

    canonical_order = json.dumps(
        order.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    return hashlib.sha256(
        canonical_order.encode("utf-8")
    ).hexdigest()


def _get_idempotent_response(
    shop_id: int,
    idempotency_key: str,
    request_fingerprint: str,
) -> dict | None:
    """Return the original response when this confirmation was processed."""

    with closing(get_connection()) as connection:
        row = connection.execute(
            """
            SELECT request_fingerprint, response_json
            FROM order_idempotency
            WHERE shop_id = ? AND idempotency_key = ?
            """,
            (shop_id, idempotency_key),
        ).fetchone()

    if row is None:
        return None

    if row["request_fingerprint"] != request_fingerprint:
        raise HTTPException(
            status_code=409,
            detail="This Idempotency-Key was already used with different order data.",
        )

    if not row["response_json"]:
        raise HTTPException(
            status_code=409,
            detail="This confirmation is still being processed. Check its status before retrying.",
        )

    try:
        return json.loads(row["response_json"])
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=500,
            detail="The stored confirmation response could not be read.",
        ) from exc


def _save_confirmed_order(
    shop_id: int,
    inventory_result: dict,
    idempotency_key: str | None = None,
    request_fingerprint: str | None = None,
    customer_id: int | None = None,
) -> dict:
    """
    Save the order and, when supplied, its idempotency record
    inside one database transaction.
    """

    order_reference = f"VK-{secrets.token_hex(6).upper()}"
    total_paise = inventory_result["total_paise"]
    checked_items = inventory_result["items"]

    with closing(get_connection()) as connection:
        with connection:

            # Claim the confirmation key inside the same transaction
            # that will create the order.
            if idempotency_key is not None:
                cursor = connection.execute(
                    """
                    INSERT INTO order_idempotency (
                        shop_id,
                        idempotency_key,
                        request_fingerprint,
                        order_id,
                        response_json
                    )
                    VALUES (?, ?, ?, NULL, NULL)
                    ON CONFLICT (shop_id, idempotency_key) DO NOTHING
                    """,
                    (
                        shop_id,
                        idempotency_key,
                        request_fingerprint,
                    ),
                )

                # Another request already claimed this key.
                if cursor.rowcount == 0:
                    existing = connection.execute(
                        """
                        SELECT request_fingerprint, response_json
                        FROM order_idempotency
                        WHERE shop_id = ? AND idempotency_key = ?
                        """,
                        (shop_id, idempotency_key),
                    ).fetchone()

                    if existing is None:
                        raise HTTPException(
                            status_code=409,
                            detail="The confirmation key could not be resolved. Please check order status.",
                        )

                    if existing["request_fingerprint"] != request_fingerprint:
                        raise HTTPException(
                            status_code=409,
                            detail="This Idempotency-Key was already used with different order data.",
                        )

                    if not existing["response_json"]:
                        raise HTTPException(
                            status_code=409,
                            detail="This confirmation is still being processed. Check its status before retrying.",
                        )

                    try:
                        return json.loads(existing["response_json"])
                    except json.JSONDecodeError as exc:
                        raise HTTPException(
                            status_code=500,
                            detail="The stored confirmation response could not be read.",
                        ) from exc

            # Create the actual grocery order.
            order_id = connection.insert_returning_id(
                """
                INSERT INTO orders (
                    order_reference,
                    shop_id,
                    status,
                    total_amount
                )
                VALUES (?, ?, 'pending', ?)
                """,
                (order_reference, shop_id, total_paise),
            )

            # Save all verified order items.
            for item in checked_items:
                connection.execute(
                    """
                    INSERT INTO order_items (
                        order_id,
                        product_id,
                        quantity,
                        unit,
                        unit_price,
                        line_total
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        order_id,
                        item["product_id"],
                        item["quantity"],
                        item["unit"],
                        item["unit_price_paise"],
                        item["line_total_paise"],
                    ),
                )
            # Link this order to its customer when customer-authenticated.
            if customer_id is not None:
                connection.execute(
                    """
                    INSERT INTO customer_order_ownership (
                        order_id,
                        customer_id
                    )
                    VALUES (?, ?)
                    """,
                    (order_id, customer_id),
                )
            response = {
                "status": "pending",
                "message": (
                    "The order request has been saved. "
                    "Shop acceptance is not yet confirmed, and stock "
                    "has not been reserved."
                ),
                "order_id": order_id,
                "order_reference": order_reference,
                "shop_id": shop_id,
                "shop_name": inventory_result["shop_name"],
                "total_paise": total_paise,
                "total_rupees": round(total_paise / 100, 2),
                "items": checked_items,
                "stock_reserved": False,
            }

            # Store the original response before committing.
            if idempotency_key is not None:
                connection.execute(
                    """
                    UPDATE order_idempotency
                    SET order_id = ?, response_json = ?
                    WHERE shop_id = ? AND idempotency_key = ?
                    """,
                    (
                        order_id,
                        json.dumps(
                            response,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        shop_id,
                        idempotency_key,
                    ),
                )

    return response


@app.post("/orders/confirm")
def confirm_order(
    order: GroceryOrder,
    shop_id: int = Query(gt=0),
    api_key: str | None = Header(default=None, alias="X-API-Key"),
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
    ),
):
    """
    Save an order only after a fresh successful inventory check.

    Supports idempotent retries when an Idempotency-Key is supplied.
    Saving does not reserve stock or guarantee shop acceptance.
    """

    authenticate_api_key(api_key)

    if order.intent != "place_order":
        raise HTTPException(
            status_code=400,
            detail="Only new grocery orders can be confirmed here.",
        )

    request_fingerprint = None

    if idempotency_key is not None:
        idempotency_key = idempotency_key.strip()

        if not idempotency_key or len(idempotency_key) > 200:
            raise HTTPException(
                status_code=400,
                detail="Idempotency-Key must contain between 1 and 200 characters.",
            )

        request_fingerprint = _order_request_fingerprint(order)

        # Check for an already completed confirmation before checking
        # inventory again. This preserves the original result on retries.
        existing_response = _get_idempotent_response(
            shop_id=shop_id,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )

        if existing_response is not None:
            return existing_response

    inventory_result = _check_inventory_for_order(
        order=order,
        shop_id=shop_id,
    )

    if inventory_result["status"] != "inventory_checked":
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "The order was not saved because some items "
                    "need attention or inventory could not be confirmed."
                ),
                "inventory_result": inventory_result,
            },
        )

    return _save_confirmed_order(
        shop_id=shop_id,
        inventory_result=inventory_result,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
    )


@app.post("/orders/confirm/gnani")
def confirm_order_from_gnani(
    payload: dict = Body(...),
    shop_id: int = Query(gt=0),
    api_key: str | None = Header(default=None, alias="X-API-Key"),
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
    ),
):
    """
    Accept Gnani's JSON-string wrapper and pass the validated
    grocery order to the existing confirmation logic.
    """

    authenticate_api_key(api_key)

    order_request = payload.get("order_request")

    if not isinstance(order_request, str):
        raise HTTPException(
            status_code=422,
            detail="order_request must be a JSON string.",
        )

    try:
        order = GroceryOrder.model_validate_json(order_request)

    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid grocery order JSON: {exc}",
        ) from exc

    return confirm_order(
        order=order,
        shop_id=shop_id,
        api_key=api_key,
        idempotency_key=idempotency_key,
    )


@app.get("/orders/history")
def order_history(
    shop_id: int = Query(gt=0),
    api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    """Return saved orders and their items for one shop."""
    authenticate_api_key(api_key)

    with closing(get_connection()) as connection:
        shop = connection.execute(
            "SELECT id, name FROM shops WHERE id = ? AND is_active = 1",
            (shop_id,),
        ).fetchone()

        if shop is None:
            raise HTTPException(
                status_code=404,
                detail="Active shop not found.",
            )

        order_rows = connection.execute(
            """
            SELECT
                id,
                order_reference,
                status,
                total_amount,
                created_at
            FROM orders
            WHERE shop_id = ?
            ORDER BY id DESC
            """,
            (shop_id,),
        ).fetchall()

        history = []

        for saved_order in order_rows:
            item_rows = connection.execute(
                """
                SELECT
                    p.name AS product_name,
                    p.brand,
                    p.variant,
                    oi.quantity,
                    oi.unit,
                    oi.unit_price,
                    oi.line_total
                FROM order_items AS oi
                JOIN products AS p ON p.id = oi.product_id
                WHERE oi.order_id = ?
                ORDER BY oi.id
                """,
                (saved_order["id"],),
            ).fetchall()

            history.append({
                "order_id": saved_order["id"],
                "order_reference": saved_order["order_reference"],
                "status": saved_order["status"],
                "total_paise": saved_order["total_amount"],
                "total_rupees": round(
                    saved_order["total_amount"] / 100, 2
                ),
                "created_at": saved_order["created_at"],
                "items": [
                    {
                        "product_name": item["product_name"],
                        "brand": item["brand"],
                        "variant": item["variant"],
                        "quantity": item["quantity"],
                        "unit": item["unit"],
                        "unit_price_paise": item["unit_price"],
                        "line_total_paise": item["line_total"],
                    }
                    for item in item_rows
                ],
            })

    return {
        "shop_id": shop_id,
        "shop_name": shop["name"],
        "count": len(history),
        "orders": history,
    }


def _load_order_details(connection, order_row):
    """Build a complete order response, including its saved item rows."""
    item_rows = connection.execute(
        """
        SELECT
            p.name AS product_name,
            p.brand,
            p.variant,
            oi.quantity,
            oi.unit,
            oi.unit_price,
            oi.line_total
        FROM order_items AS oi
        JOIN products AS p ON p.id = oi.product_id
        WHERE oi.order_id = ?
        ORDER BY oi.id
        """,
        (order_row["id"],),
    ).fetchall()

    total_paise = order_row["total_amount"]
    return {
        "order_id": order_row["id"],
        "order_reference": order_row["order_reference"],
        "shop_id": order_row["shop_id"],
        "status": order_row["status"],
        "total_paise": total_paise,
        "total_rupees": round(total_paise / 100, 2),
        "created_at": order_row["created_at"],
        "items": [
            {
                "product_name": item["product_name"],
                "brand": item["brand"],
                "variant": item["variant"],
                "quantity": item["quantity"],
                "unit": item["unit"],
                "unit_price_paise": item["unit_price"],
                "unit_price_rupees": round(item["unit_price"] / 100, 2),
                "line_total_paise": item["line_total"],
                "line_total_rupees": round(item["line_total"] / 100, 2),
            }
            for item in item_rows
        ],
    }


@app.get("/orders/{order_reference}")
def get_order(
    order_reference: str,
    shop_id: int = Query(gt=0),
    api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    """Retrieve one saved order by reference, scoped to its shop."""
    authenticate_api_key(api_key)

    with closing(get_connection()) as connection:
        shop = connection.execute(
            "SELECT id FROM shops WHERE id = ? AND is_active = 1",
            (shop_id,),
        ).fetchone()
        if shop is None:
            raise HTTPException(status_code=404, detail="Active shop not found.")

        order_row = connection.execute(
            """
            SELECT id, order_reference, shop_id, status, total_amount, created_at
            FROM orders
            WHERE order_reference = ? AND shop_id = ?
            """,
            (order_reference, shop_id),
        ).fetchone()

        if order_row is None:
            raise HTTPException(status_code=404, detail="Order not found.")

        return _load_order_details(connection, order_row)


@app.post("/orders/{order_reference}/cancel")
def cancel_order(
    order_reference: str,
    shop_id: int = Query(gt=0),
    api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    """Cancel a pending order and persist the status change."""
    authenticate_api_key(api_key)

    with closing(get_connection()) as connection:
        shop = connection.execute(
            "SELECT id FROM shops WHERE id = ? AND is_active = 1",
            (shop_id,),
        ).fetchone()
        if shop is None:
            raise HTTPException(status_code=404, detail="Active shop not found.")

        with connection:
            order_row = connection.execute(
                """
                SELECT id, order_reference, shop_id, status, total_amount, created_at
                FROM orders
                WHERE order_reference = ? AND shop_id = ?
                """,
                (order_reference, shop_id),
            ).fetchone()

            if order_row is None:
                raise HTTPException(status_code=404, detail="Order not found.")

            if order_row["status"] != "pending":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Only pending orders can be cancelled. "
                        f"Current status: {order_row['status']}."
                    ),
                )

            cursor = connection.execute(
                """
                UPDATE orders
                SET status = 'cancelled'
                WHERE id = ? AND status = 'pending'
                """,
                (order_row["id"],),
            )
            if cursor.rowcount != 1:
                raise HTTPException(
                    status_code=409,
                    detail="The order status changed before cancellation completed.",
                )

        updated_order = connection.execute(
            """
            SELECT id, order_reference, shop_id, status, total_amount, created_at
            FROM orders
            WHERE id = ?
            """,
            (order_row["id"],),
        ).fetchone()

        result = _load_order_details(connection, updated_order)

    result["message"] = (
        "The pending order was cancelled. No stock reservation was made by "
        "VoiceKirana, so no stock was returned to inventory."
    )
    return result


@app.post("/admin/shops", status_code=status.HTTP_201_CREATED)
def admin_create_shop(
    payload: AdminCreateShopRequest,
    admin_token: str | None = Header(
        default=None,
        alias="X-Admin-Token",
    ),
):
    """Allow the administrator to register a new grocery shop."""

    authenticate_admin_token(admin_token)

    with closing(get_connection()) as connection:
        with connection:
            shop_id = connection.insert_returning_id(
                """
                INSERT INTO shops (name)
                VALUES (?)
                """,
                (payload.name,),
            )

    return {
        "status": "created",
        "message": "Shop registered successfully.",
        "shop_id": shop_id,
        "shop_name": payload.name,
    }


@app.post(
    "/admin/shops/owners",
    status_code=status.HTTP_201_CREATED,
)
def admin_create_shop_owner(
    payload: AdminCreateShopOwnerRequest,
    admin_token: str | None = Header(
        default=None,
        alias="X-Admin-Token",
    ),
):
    """
    Create a shop-owner account and assign it to an active shop.

    Only the administrator can create shop-owner accounts.
    """

    authenticate_admin_token(admin_token)

    # Perform password hashing before opening the database transaction.
    password_hash = hash_password(payload.password)

    with closing(get_connection()) as connection:
        with connection:
            shop = connection.execute(
                """
                SELECT id, name
                FROM shops
                WHERE id = ? AND is_active = 1
                """,
                (payload.shop_id,),
            ).fetchone()

            if shop is None:
                raise HTTPException(
                    status_code=404,
                    detail="Active shop not found.",
                )

            existing_user = connection.execute(
                """
                SELECT id
                FROM users
                WHERE email = ?
                """,
                (payload.email,),
            ).fetchone()

            if existing_user is not None:
                raise HTTPException(
                    status_code=409,
                    detail="An account with this email already exists.",
                )

            user_id = connection.insert_returning_id(
                """
                INSERT INTO users (
                    email,
                    full_name,
                    password_hash
                )
                VALUES (?, ?, ?)
                """,
                (
                    payload.email,
                    payload.full_name,
                    password_hash,
                ),
            )

            connection.execute(
                """
                INSERT INTO shop_memberships (
                    user_id,
                    shop_id,
                    role
                )
                VALUES (?, ?, ?)
                """,
                (
                    user_id,
                    payload.shop_id,
                    payload.role,
                ),
            )

    return {
        "status": "created",
        "message": "Shop-owner account created successfully.",
        "user_id": user_id,
        "email": payload.email,
        "full_name": payload.full_name,
        "shop_id": payload.shop_id,
        "shop_name": shop["name"],
        "role": payload.role,
    }


@app.post("/auth/login", response_model=AccessTokenResponse)
def shop_owner_login(payload: LoginRequest):
    """Authenticate a shop owner and issue a signed JWT."""

    with closing(get_connection()) as connection:
        user = connection.execute(
            """
            SELECT
                id,
                email,
                full_name,
                password_hash,
                is_active
            FROM users
            WHERE email = ?
            """,
            (payload.email,),
        ).fetchone()

    # Return the same error for unknown email, inactive account,
    # and incorrect password.
    if (
        user is None
        or not user["is_active"]
        or not verify_password(
            payload.password,
            user["password_hash"],
        )
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(user["id"])

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }


@app.get("/auth/me", response_model=CurrentUserResponse)
def get_my_profile(
    current_user: dict = Depends(get_current_user),
):
    """
    Return the authenticated user's profile and active shop memberships.
    """

    return current_user


@app.post(
    "/customers/register",
    response_model=CustomerProfileResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_customer(payload: CustomerRegisterRequest):
    """Register a new independent customer account."""

    password_hash = hash_password(payload.password)

    with closing(get_connection()) as connection:
        with connection:
            cursor = connection.execute(
                """
                INSERT INTO customers (
                    email,
                    full_name,
                    password_hash
                )
                VALUES (?, ?, ?)
                ON CONFLICT (email) DO NOTHING
                """,
                (
                    payload.email,
                    payload.full_name,
                    password_hash,
                ),
            )

            if cursor.rowcount == 0:
                raise HTTPException(
                    status_code=409,
                    detail="An account with this email already exists.",
                )

            customer = connection.execute(
                """
                SELECT id, email, full_name
                FROM customers
                WHERE email = ?
                """,
                (payload.email,),
            ).fetchone()

    return {
        "customer_id": customer["id"],
        "email": customer["email"],
        "full_name": customer["full_name"],
    }


@app.post("/customers/login", response_model=AccessTokenResponse)
def customer_login(payload: LoginRequest):
    """Authenticate a customer and issue a customer-specific JWT."""

    with closing(get_connection()) as connection:
        customer = connection.execute(
            """
            SELECT
                id,
                email,
                full_name,
                password_hash,
                is_active
            FROM customers
            WHERE email = ?
            """,
            (payload.email,),
        ).fetchone()

    # Verify a real or dummy hash to reduce email-enumeration
    # timing differences between existing and unknown accounts.
    stored_hash = (
        customer["password_hash"]
        if customer is not None
        else create_dummy_password_hash()
    )

    password_valid = verify_password(
        payload.password,
        stored_hash,
    )

    if (
        customer is None
        or not customer["is_active"]
        or not password_valid
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(
        customer["id"],
        principal_type="customer",
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }


@app.get(
    "/customers/me",
    response_model=CustomerProfileResponse,
)
def get_customer_profile(
    current_customer: dict = Depends(get_current_customer),
):
    """Return the authenticated customer's profile."""

    return current_customer

@app.post("/customers/orders/confirm", status_code=status.HTTP_201_CREATED)
def confirm_customer_order(
    order: GroceryOrder,
    shop_id: int = Query(gt=0),
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
    ),
    current_customer: dict = Depends(get_current_customer),
):
    """
    Confirm a grocery order for the authenticated customer.

    Customer identity is attached to the order transaction.
    Idempotency keys are scoped to individual customers.
    """

    if order.intent != "place_order":
        raise HTTPException(
            status_code=400,
            detail="Only new grocery orders can be confirmed here.",
        )

    if idempotency_key is None:
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key header is required.",
        )

    idempotency_key = idempotency_key.strip()

    if not idempotency_key or len(idempotency_key) > 200:
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key must contain between 1 and 200 characters.",
        )

    customer_id = current_customer["customer_id"]

    # Create a customer-specific key so different customers
    # cannot retrieve each other's idempotent responses.
    scoped_key = hashlib.sha256(
        f"customer:{customer_id}:{idempotency_key}".encode("utf-8")
    ).hexdigest()

    request_fingerprint = _order_request_fingerprint(order)

    existing_response = _get_idempotent_response(
        shop_id=shop_id,
        idempotency_key=scoped_key,
        request_fingerprint=request_fingerprint,
    )

    if existing_response is not None:
        return existing_response

    inventory_result = _check_inventory_for_order(
        order=order,
        shop_id=shop_id,
    )

    if inventory_result["status"] != "inventory_checked":
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "The order was not saved because some items "
                    "need attention or inventory could not be confirmed."
                ),
                "inventory_result": inventory_result,
            },
        )

    return _save_confirmed_order(
        shop_id=shop_id,
        inventory_result=inventory_result,
        idempotency_key=scoped_key,
        request_fingerprint=request_fingerprint,
        customer_id=customer_id,
    )


@app.get("/customers/orders")
def get_customer_order_history(
    current_customer: dict = Depends(get_current_customer),
):
    """Return only orders belonging to the authenticated customer."""

    customer_id = current_customer["customer_id"]

    with closing(get_connection()) as connection:
        order_rows = connection.execute(
            """
            SELECT
                o.id,
                o.order_reference,
                o.shop_id,
                o.status,
                o.total_amount,
                o.created_at
            FROM orders AS o
            JOIN customer_order_ownership AS ownership
                ON ownership.order_id = o.id
            WHERE ownership.customer_id = ?
            ORDER BY o.id DESC
            """,
            (customer_id,),
        ).fetchall()

        orders = [
            _load_order_details(connection, order_row)
            for order_row in order_rows
        ]

    return {
        "customer_id": customer_id,
        "count": len(orders),
        "orders": orders,
    }


@app.get("/customers/orders/{order_reference}")
def get_customer_order(
    order_reference: str,
    current_customer: dict = Depends(get_current_customer),
):
    """
    Retrieve one order only when it belongs to the authenticated customer.
    """

    customer_id = current_customer["customer_id"]

    with closing(get_connection()) as connection:
        order_row = connection.execute(
            """
            SELECT
                o.id,
                o.order_reference,
                o.shop_id,
                o.status,
                o.total_amount,
                o.created_at
            FROM orders AS o
            JOIN customer_order_ownership AS ownership
                ON ownership.order_id = o.id
            WHERE o.order_reference = ?
              AND ownership.customer_id = ?
            """,
            (order_reference, customer_id),
        ).fetchone()

        if order_row is None:
            raise HTTPException(
                status_code=404,
                detail="Order not found.",
            )

        return _load_order_details(connection, order_row)

@app.post(
    "/customers/orders/{order_reference}/payments",
    status_code=status.HTTP_201_CREATED,
)
def create_customer_payment(
    order_reference: str,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
    ),
    current_customer: dict = Depends(get_current_customer),
):
    """Create a Razorpay payment attempt for a customer's own order."""

    if not idempotency_key:
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key header is required.",
        )

    idempotency_key = idempotency_key.strip()

    if not idempotency_key or len(idempotency_key) > 200:
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key must contain between 1 and 200 characters.",
        )

    customer_id = current_customer["customer_id"]

    try:
        key_id = get_razorpay_public_key_id()
    except RazorpayConfigurationError as exc:
        raise HTTPException(
            status_code=503,
            detail="Razorpay payment configuration is unavailable.",
        ) from exc

    # Only retrieve an order owned by this authenticated customer.
    with closing(get_connection()) as connection:
        order = connection.execute(
            """
            SELECT
                o.id,
                o.order_reference,
                o.status,
                o.total_amount
            FROM orders AS o
            JOIN customer_order_ownership AS ownership
                ON ownership.order_id = o.id
            WHERE o.order_reference = ?
              AND ownership.customer_id = ?
            """,
            (order_reference, customer_id),
        ).fetchone()

        if order is None:
            raise HTTPException(
                status_code=404,
                detail="Order not found.",
            )

        if order["status"] != "pending":
            raise HTTPException(
                status_code=409,
                detail="Only pending orders can start a payment.",
            )

        amount_paise = order["total_amount"]

        if type(amount_paise) is not int or amount_paise < 100:
            raise HTTPException(
                status_code=400,
                detail="Order amount must be at least ₹1.00 to accept payment.",
            )

        # Reuse the original payment order for an identical retry.
        existing = connection.execute(
            """
            SELECT
                id,
                razorpay_order_id,
                amount_paise,
                status
            FROM payments
            WHERE order_id = ?
              AND customer_id = ?
              AND idempotency_key = ?
            """,
            (order["id"], customer_id, idempotency_key),
        ).fetchone()

        if existing is not None:
            if existing["status"] in {
                "created",
                "authorized",
                "captured",
            } and existing["razorpay_order_id"]:
                return {
                    "payment_id": existing["id"],
                    "order_reference": order_reference,
                    "razorpay_order_id": existing["razorpay_order_id"],
                    "amount_paise": existing["amount_paise"],
                    "currency": "INR",
                    "key_id": key_id,
                    "status": existing["status"],
                }

            raise HTTPException(
                status_code=409,
                detail=(
                    "This payment attempt cannot be safely repeated. "
                    "Check its payment status before starting another attempt."
                ),
            )

        # Do not create a second active payment session for this order.
        active_payment = connection.execute(
            """
            SELECT id, status
            FROM payments
            WHERE order_id = ?
              AND status IN (
                  'creating',
                  'created',
                  'authorized',
                  'captured',
                  'refunded'
              )
            LIMIT 1
            """,
            (order["id"],),
        ).fetchone()

        if active_payment is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "An active payment attempt already exists for this order. "
                    "Reuse its existing payment session."
                ),
            )

        order_id = order["id"]

    # Claim the payment attempt in the database before calling Razorpay.
    try:
        with closing(get_connection()) as connection:
            with connection:
                payment_id = connection.insert_returning_id(
                    """
                    INSERT INTO payments (
                        order_id,
                        customer_id,
                        idempotency_key,
                        amount_paise,
                        currency,
                        status
                    )
                    VALUES (?, ?, ?, ?, 'INR', 'creating')
                    """,
                    (
                        order_id,
                        customer_id,
                        idempotency_key,
                        amount_paise,
                    ),
                )

    except Exception as exc:
        # The database's partial unique index prevents concurrent
        # active attempts for the same order.
        with closing(get_connection()) as connection:
            active = connection.execute(
                """
                SELECT id, status
                FROM payments
                WHERE order_id = ?
                  AND status IN (
                      'creating',
                      'created',
                      'authorized',
                      'captured',
                      'refunded'
                  )
                LIMIT 1
                """,
                (order_id,),
            ).fetchone()

        if active is not None:
            raise HTTPException(
                status_code=409,
                detail="Another payment attempt was created concurrently.",
            ) from exc

        raise

    # Use the internal payment ID to create a unique Razorpay receipt.
    receipt = f"VKP-{payment_id}"

    try:
        razorpay_result = create_razorpay_order(
            amount_paise=amount_paise,
            receipt=receipt,
            notes={
                "voicekirana_payment_id": str(payment_id),
                "order_reference": order_reference,
            },
        )

    except RazorpayAPIError as exc:
        # A definite 4xx rejection means Razorpay rejected creation.
        # A timeout or 5xx response is ambiguous, so keep status
        # 'creating' to prevent an unsafe duplicate attempt.
        if (
            exc.status_code is not None
            and 400 <= exc.status_code < 500
        ):
            with closing(get_connection()) as connection:
                with connection:
                    connection.execute(
                        """
                        UPDATE payments
                        SET
                            status = 'creation_failed',
                            failure_description = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                          AND status = 'creating'
                        """,
                        (str(exc)[:300], payment_id),
                    )

        raise HTTPException(
            status_code=502,
            detail=(
                "Razorpay could not confirm payment-order creation. "
                "Check the payment attempt before retrying."
            ),
        ) from exc

    with closing(get_connection()) as connection:
        with connection:
            connection.execute(
                """
                UPDATE payments
                SET
                    razorpay_order_id = ?,
                    status = 'created',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND status = 'creating'
                """,
                (
                    razorpay_result["razorpay_order_id"],
                    payment_id,
                ),
            )

    return {
        "payment_id": payment_id,
        "order_reference": order_reference,
        "razorpay_order_id": razorpay_result["razorpay_order_id"],
        "amount_paise": amount_paise,
        "currency": "INR",
        "key_id": key_id,
        "status": "created",
    }


@app.post("/customers/orders/{order_reference}/payments/verify")
def verify_customer_payment(
    order_reference: str,
    payload: RazorpayPaymentVerificationRequest,
    current_customer: dict = Depends(get_current_customer),
):
    """
    Verify a customer's Razorpay Checkout response.

    Payment is marked captured only after:
    1. Checkout signature verification.
    2. Server-side Razorpay payment retrieval.
    3. Order ID, amount, currency and capture-status checks.
    """

    customer_id = current_customer["customer_id"]

    # Retrieve the payment using the authenticated customer's
    # ownership and the payment's server-stored Razorpay order ID.
    with closing(get_connection()) as connection:
        payment = connection.execute(
            """
            SELECT
                p.id,
                p.order_id,
                p.customer_id,
                p.razorpay_order_id,
                p.razorpay_payment_id,
                p.amount_paise,
                p.currency,
                p.status
            FROM payments AS p
            JOIN orders AS o
                ON o.id = p.order_id
            JOIN customer_order_ownership AS ownership
                ON ownership.order_id = o.id
            WHERE o.order_reference = ?
              AND ownership.customer_id = ?
              AND p.customer_id = ?
              AND p.razorpay_order_id = ?
            ORDER BY p.id DESC
            LIMIT 1
            """,
            (
                order_reference,
                customer_id,
                customer_id,
                payload.razorpay_order_id,
            ),
        ).fetchone()

    if payment is None:
        raise HTTPException(
            status_code=404,
            detail="Payment attempt not found.",
        )
    if (
        payment["razorpay_payment_id"] is not None
        and payment["razorpay_payment_id"] != payload.razorpay_payment_id
    ):
        raise HTTPException(
            status_code=409,
            detail="A different Razorpay payment ID is already associated with this attempt.",
        )
    if payment["status"] in {"creating", "creation_failed"}:
        raise HTTPException(
            status_code=409,
            detail="This payment attempt is not ready for verification.",
        )

    if payment["status"] == "refunded":
        raise HTTPException(
            status_code=409,
            detail="This payment has already been refunded.",
        )

    # Never allow a different payment ID to replace an already
    # captured payment.
    if (
        payment["status"] == "captured"
        and payment["razorpay_payment_id"] != payload.razorpay_payment_id
    ):
        raise HTTPException(
            status_code=409,
            detail="A different payment has already been captured.",
        )

    # Verify against the order ID stored by our backend.
    try:
        signature_valid = verify_payment_signature(
            payment["razorpay_order_id"],
            payload.razorpay_payment_id,
            payload.razorpay_signature,
        )
    except RazorpayConfigurationError as exc:
        raise HTTPException(
            status_code=503,
            detail="Payment verification is not configured.",
        ) from exc

    if not signature_valid:
        raise HTTPException(
            status_code=400,
            detail="Invalid Razorpay payment signature.",
        )

    # An already recorded successful verification is idempotent.
    if (
        payment["status"] == "captured"
        and payment["razorpay_payment_id"] == payload.razorpay_payment_id
    ):
        return {
            "status": "captured",
            "payment_id": payment["id"],
            "order_reference": order_reference,
            "razorpay_payment_id": payload.razorpay_payment_id,
            "amount_paise": payment["amount_paise"],
            "currency": payment["currency"],
            "captured": True,
            "message": "Payment has already been verified and captured.",
        }

    # Independently fetch payment details from Razorpay.
    try:
        provider_payment = fetch_razorpay_payment(
            payload.razorpay_payment_id
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid Razorpay payment identifier.",
        ) from exc
    except RazorpayAPIError as exc:
        raise HTTPException(
            status_code=502,
            detail="Could not verify payment status with Razorpay.",
        ) from exc

    # Compare all critical values with our saved payment attempt.
    if (
        provider_payment["razorpay_order_id"]
        != payment["razorpay_order_id"]
        or provider_payment["amount_paise"]
        != payment["amount_paise"]
        or provider_payment["currency"] != "INR"
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Razorpay payment details do not match "
                "the saved VoiceKirana payment."
            ),
        )

    provider_status = provider_payment["status"]
    provider_captured = provider_payment["captured"]

    if provider_status == "captured" and provider_captured:
        new_status = "captured"

    elif provider_status == "authorized":
        new_status = "authorized"

    elif provider_status == "failed":
        new_status = "failed"

    elif provider_status == "refunded":
        new_status = "refunded"

    else:
        new_status = "created"

    failure_description = (
        "Razorpay reported that this payment failed."
        if new_status == "failed"
        else None
    )

    # Never downgrade a captured/refunded payment if a delayed
    # verification request races with another status update.
    with closing(get_connection()) as connection:
        with connection:
            connection.execute(
                """
                UPDATE payments
                SET
                    razorpay_payment_id = COALESCE(
                        razorpay_payment_id,
                        ?
                    ),
                    status = CASE
                        WHEN status = 'refunded' THEN 'refunded'
                        WHEN status = 'captured' THEN 'captured'
                        WHEN ? = 'captured' THEN 'captured'
                        WHEN ? = 'refunded' THEN 'refunded'
                        WHEN ? = 'authorized' THEN 'authorized'
                        WHEN ? = 'failed' THEN 'failed'
                        ELSE status
                    END,
                    failure_description = CASE
                        WHEN ? = 'failed' THEN ?
                        ELSE failure_description
                    END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    payload.razorpay_payment_id,
                    new_status,
                    new_status,
                    new_status,
                    new_status,
                    new_status,
                    failure_description,
                    payment["id"],
                ),
            )

            updated_payment = connection.execute(
                """
                SELECT status, razorpay_payment_id
                FROM payments
                WHERE id = ?
                """,
                (payment["id"],),
            ).fetchone()

    final_status = updated_payment["status"]
    captured = final_status == "captured"

    return {
        "status": final_status,
        "payment_id": payment["id"],
        "order_reference": order_reference,
        "razorpay_payment_id": updated_payment["razorpay_payment_id"],
        "amount_paise": payment["amount_paise"],
        "currency": payment["currency"],
        "captured": captured,
        "message": (
            "Payment successfully verified and captured."
            if captured
            else (
                "Payment is authorized but has not yet been captured."
                if final_status == "authorized"
                else "Payment status has been verified."
            )
        ),
    }

@app.post("/webhooks/razorpay")
async def handle_razorpay_webhook(request: Request):
    """
    Receive and process verified Razorpay payment webhooks.

    Security:
    1. Verify the original raw request body.
    2. Require Razorpay's unique event ID.
    3. Persist each event exactly once.
    4. Match payment identity, order and amount before updating.
    5. Protect terminal payment states from older events.

    This endpoint never accepts or reserves a grocery order.
    """

    raw_body = await request.body()

    if len(raw_body) > 1_000_000:
        raise HTTPException(
            status_code=413,
            detail="Webhook request body is too large.",
        )

    webhook_signature = request.headers.get("X-Razorpay-Signature")
    event_id = request.headers.get("x-razorpay-event-id")

    if not webhook_signature:
        raise HTTPException(
            status_code=400,
            detail="Missing Razorpay webhook signature.",
        )

    if not event_id or not event_id.strip() or len(event_id) > 255:
        raise HTTPException(
            status_code=400,
            detail="Missing or invalid Razorpay event ID.",
        )

    event_id = event_id.strip()

    # Verify the original raw bytes before parsing JSON.
    try:
        signature_valid = verify_webhook_signature(
            raw_body,
            webhook_signature,
        )
    except RazorpayConfigurationError as exc:
        raise HTTPException(
            status_code=503,
            detail="Razorpay webhook verification is not configured.",
        ) from exc

    if not signature_valid:
        raise HTTPException(
            status_code=400,
            detail="Invalid Razorpay webhook signature.",
        )

    # Parse JSON only after signature verification.
    try:
        event_data = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid webhook JSON payload.",
        ) from exc

    if not isinstance(event_data, dict):
        raise HTTPException(
            status_code=400,
            detail="Webhook payload must be a JSON object.",
        )

    event_type = event_data.get("event")

    if not isinstance(event_type, str) or not event_type.strip():
        raise HTTPException(
            status_code=400,
            detail="Webhook event type is missing.",
        )

    event_type = event_type.strip()

    supported_events = {
        "payment.authorized": "authorized",
        "payment.captured": "captured",
        "payment.failed": "failed",
    }

    target_status = supported_events.get(event_type)

    provider_payment_id = None
    provider_order_id = None
    provider_amount = None
    provider_currency = None
    failure_code = None
    failure_description = None

    payload = event_data.get("payload")
    if not isinstance(payload, dict):
        payload = {}

    payment_data = payload.get("payment")
    if not isinstance(payment_data, dict):
        payment_data = {}

    payment_entity = payment_data.get("entity")
    if not isinstance(payment_entity, dict):
        payment_entity = {}

    provider_payment_id = payment_entity.get("id")

    if target_status is not None:
        provider_order_id = payment_entity.get("order_id")
        provider_amount = payment_entity.get("amount")
        provider_currency = payment_entity.get("currency")
        provider_status = payment_entity.get("status")

        if (
            not isinstance(provider_payment_id, str)
            or not provider_payment_id.startswith("pay_")
            or len(provider_payment_id) > 100
        ):
            raise HTTPException(
                status_code=400,
                detail="Invalid payment ID in webhook payload.",
            )

        if (
            not isinstance(provider_order_id, str)
            or not provider_order_id.startswith("order_")
            or len(provider_order_id) > 100
        ):
            raise HTTPException(
                status_code=400,
                detail="Invalid order ID in webhook payload.",
            )

        if type(provider_amount) is not int or provider_amount <= 0:
            raise HTTPException(
                status_code=400,
                detail="Invalid payment amount in webhook payload.",
            )

        if provider_currency != "INR":
            raise HTTPException(
                status_code=400,
                detail="Unexpected payment currency in webhook payload.",
            )

        if provider_status != target_status:
            raise HTTPException(
                status_code=400,
                detail="Webhook event and payment status do not match.",
            )

        if (
            target_status == "captured"
            and payment_entity.get("captured") is not True
        ):
            raise HTTPException(
                status_code=400,
                detail="Captured event does not contain a captured payment.",
            )

        failure_code = payment_entity.get("error_code")
        failure_description = payment_entity.get("error_description")

        if failure_code is not None:
            failure_code = str(failure_code)[:100]

        if failure_description is not None:
            failure_description = str(failure_description)[:300]

        if target_status == "failed" and not failure_description:
            failure_description = "Razorpay reported a failed payment."

    duplicate = False
    payment_updated = False
    final_payment_status = None

    # Store the event and update the payment in one transaction.
    with closing(get_connection()) as connection:
        with connection:
            insert_result = connection.execute(
                """
                INSERT INTO razorpay_webhook_events (
                    event_id,
                    event_type,
                    razorpay_payment_id
                )
                VALUES (?, ?, ?)
                ON CONFLICT(event_id) DO NOTHING
                """,
                (
                    event_id,
                    event_type,
                    provider_payment_id,
                ),
            )

            if insert_result.rowcount == 0:
                duplicate = True

            elif target_status is not None:
                payment = connection.execute(
                    """
                    SELECT
                        id,
                        amount_paise,
                        currency,
                        status,
                        razorpay_payment_id
                    FROM payments
                    WHERE razorpay_order_id = ?
                    """,
                    (provider_order_id,),
                ).fetchone()

                if (
                    payment is not None
                    and payment["amount_paise"] == provider_amount
                    and payment["currency"] == provider_currency
                    and (
                        payment["razorpay_payment_id"] is None
                        or payment["razorpay_payment_id"]
                        == provider_payment_id
                    )
                ):
                    # Do not associate one Razorpay payment with
                    # two different VoiceKirana payment attempts.
                    existing_payment = connection.execute(
                        """
                        SELECT id
                        FROM payments
                        WHERE razorpay_payment_id = ?
                          AND id != ?
                        LIMIT 1
                        """,
                        (
                            provider_payment_id,
                            payment["id"],
                        ),
                    ).fetchone()

                    if existing_payment is None:
                        connection.execute(
                            """
                            UPDATE payments
                            SET
                                razorpay_payment_id = COALESCE(
                                    razorpay_payment_id,
                                    ?
                                ),
                                status = CASE
                                    WHEN status IN (
                                        'captured',
                                        'refunded',
                                        'failed'
                                    ) THEN status
                                    WHEN ? = 'captured' THEN 'captured'
                                    WHEN ? = 'failed' THEN 'failed'
                                    WHEN ? = 'authorized'
                                        AND status = 'created'
                                        THEN 'authorized'
                                    ELSE status
                                END,
                                failure_code = CASE
                                    WHEN ? = 'failed' THEN ?
                                    ELSE failure_code
                                END,
                                failure_description = CASE
                                    WHEN ? = 'failed' THEN ?
                                    ELSE failure_description
                                END,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                            """,
                            (
                                provider_payment_id,
                                target_status,
                                target_status,
                                target_status,
                                target_status,
                                failure_code,
                                target_status,
                                failure_description,
                                payment["id"],
                            ),
                        )

                        updated_payment = connection.execute(
                            """
                            SELECT status
                            FROM payments
                            WHERE id = ?
                            """,
                            (payment["id"],),
                        ).fetchone()

                        final_payment_status = updated_payment["status"]
                        payment_updated = True

    if duplicate:
        return {
            "status": "duplicate",
            "duplicate": True,
            "event_id": event_id,
            "message": "Webhook event was already processed.",
        }

    return {
        "status": "accepted" if payment_updated else "ignored",
        "duplicate": False,
        "event_id": event_id,
        "event_type": event_type,
        "payment_updated": payment_updated,
        "payment_status": final_payment_status,
    }


@app.post("/webhooks/twilio/whatsapp")
async def receive_twilio_whatsapp_message(request: Request):
    """
    Receive WhatsApp messages through Twilio Sandbox.

    Validate the Twilio signature before processing the message.
    Return TwiML so Twilio can send an automatic WhatsApp reply.
    """

    signature = request.headers.get("X-Twilio-Signature", "")

    try:
        form = await request.form()
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid Twilio webhook form data.",
        ) from exc

    # Include all form parameters in signature verification.
    parameters = dict(form)

    try:
        signature_valid = validate_twilio_webhook(
            signature,
            parameters,
        )
    except TwilioConfigurationError as exc:
        raise HTTPException(
            status_code=503,
            detail="Twilio webhook configuration is unavailable.",
        ) from exc

    if not signature_valid:
        raise HTTPException(
            status_code=403,
            detail="Invalid Twilio webhook signature.",
        )

    sender = parameters.get("From", "")
    message_body = parameters.get("Body", "")

    if not sender.startswith("whatsapp:"):
        raise HTTPException(
            status_code=400,
            detail="Invalid WhatsApp sender address.",
        )

    if len(message_body) > 1600:
        raise HTTPException(
            status_code=413,
            detail="Incoming WhatsApp message is too long.",
        )

    twiml = build_whatsapp_reply(message_body)

    return Response(
        content=twiml,
        media_type="application/xml",
    )

# ============================================================
# SHOP CATALOGUE MANAGEMENT
# ============================================================


def handle_catalogue_error(error: ValueError):
    """
    Convert catalogue service errors into useful HTTP responses.
    """
    message = str(error)
    normalized_message = message.casefold()

    if any(term in normalized_message for term in (
        "not found",
        "does not exist",
        "not registered",
        "does not belong",
    )):
        status_code = status.HTTP_404_NOT_FOUND

    elif any(term in normalized_message for term in (
        "multiple",
        "ambiguous",
        "conflict",
    )):
        status_code = status.HTTP_409_CONFLICT

    else:
        status_code = status.HTTP_400_BAD_REQUEST

    raise HTTPException(
        status_code=status_code,
        detail=message,
    )


@app.get("/shops/{shop_id}/inventory")
def list_shop_inventory(
    shop_id: int,
    shop_access: dict = Depends(require_shop_access("owner", "manager")),
):
    """
    Retrieve inventory belonging to an authorized shop.
    """
    try:
        items = get_shop_inventory(shop_id)

        return {
            "shop_id": shop_id,
            "count": len(items),
            "items": items,
        }

    except ValueError as error:
        handle_catalogue_error(error)


@app.post("/shops/{shop_id}/products")
def create_shop_product(
    shop_id: int,
    payload: ShopProductCreate,
    shop_access: dict = Depends(require_shop_access("owner", "manager")),
):
    """
    Register a product for a shop and add it to that shop's inventory.
    """
    try:
        result = register_product_for_shop(shop_id, payload)

        return {
            "message": "Product registered successfully.",
            **result,
        }

    except ValueError as error:
        handle_catalogue_error(error)


@app.put("/shops/{shop_id}/inventory/{product_id}")
def update_shop_product_inventory(
    shop_id: int,
    product_id: int,
    payload: InventoryUpdate,
    shop_access: dict = Depends(require_shop_access("owner", "manager")),
):
    """
    Update price, stock quantity, or availability for a shop's product.
    """
    try:
        updates = payload.dict(
            exclude_unset=True,
            exclude_none=True,
        )

        result = update_shop_inventory(
            shop_id=shop_id,
            product_id=product_id,
            updates=updates,
        )

        return {
            "message": "Inventory updated successfully.",
            **result,
        }

    except ValueError as error:
        handle_catalogue_error(error)


@app.post("/shops/{shop_id}/products/{product_id}/aliases")
def create_shop_product_alias(
    shop_id: int,
    product_id: int,
    payload: ProductAliasCreate,
    shop_access: dict = Depends(require_shop_access("owner", "manager")),
):
    """
    Add a regional or alternative name for a shop's product.
    """
    try:
        result = add_shop_product_alias(
            shop_id=shop_id,
            product_id=product_id,
            alias=payload.alias,
        )

        return {
            "message": "Product alias added successfully.",
            **result,
        }

    except ValueError as error:
        handle_catalogue_error(error)


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )