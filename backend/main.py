
import os
import secrets

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header, Query

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
def preview_order(
    order: GroceryOrder,
    api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    """
    Validate an order.
    This endpoint does not check inventory or place an order.
    """
    authenticate_api_key(api_key)

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


@app.post("/inventory/check")
def check_inventory(
    order: GroceryOrder,
    shop_id: int = Query(gt=0),
    api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    """
    Check requested products against one shop's catalogue.

    This endpoint does not reserve stock, create an order,
    or place a purchase.
    """
    authenticate_api_key(api_key)

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


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )
