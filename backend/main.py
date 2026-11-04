
import json
import os
import secrets

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header, Query, Request

from backend.database import get_connection
from backend.models.order import GroceryOrder
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
    request: Request,
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
        payload = await request.json()

        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")

        if "order_request" in payload:
            order_payload = payload["order_request"]

            # Decode the complete order sent as a JSON string by Gnani.
            if isinstance(order_payload, str):
                order_payload = json.loads(order_payload)

            if not isinstance(order_payload, dict):
                raise ValueError("order_request must be a JSON object.")

            order_payload.setdefault(
                "intent",
                payload.get(
                    "customer_intent",
                    payload.get("intent", "place_order"),
                ),
            )
            order_payload.setdefault("language", payload.get("language"))

            if payload.get("additional_instructions") is not None:
                order_payload.setdefault(
                    "additional_instructions",
                    payload["additional_instructions"],
                )
        else:
            # Preserve support for direct GroceryOrder requests.
            order_payload = payload

        order = GroceryOrder.model_validate(order_payload)

    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid inventory request: {exc}",
        ) from exc

    # Reuse the same inventory logic as order confirmation.
    return _check_inventory_for_order(
        order=order,
        shop_id=shop_id,
    )


from contextlib import closing


@app.post("/orders/confirm")
def confirm_order(
    order: GroceryOrder,
    shop_id: int = Query(gt=0),
    api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    """
    Save an order only after a fresh successful inventory check.

    Saving the order does not reserve stock or guarantee shop acceptance.
    """
    authenticate_api_key(api_key)

    if order.intent != "place_order":
        raise HTTPException(
            status_code=400,
            detail="Only new grocery orders can be confirmed here.",
        )

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

    order_reference = f"VK-{secrets.token_hex(6).upper()}"
    total_paise = inventory_result["total_paise"]
    checked_items = inventory_result["items"]

    with closing(get_connection()) as connection:
        with connection:
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

    return {
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


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )