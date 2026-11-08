import pytest
from fastapi.testclient import TestClient

from backend.security import create_access_token
import backend.main as main
import backend.database as database
from backend.database import get_connection, initialize_database
from backend.services.inventory_service import (
    create_shop,
    create_product,
    set_inventory,
)

TEST_API_TOKEN = "voicekirana-test-token"
TEST_JWT_SECRET = (
    "test-jwt-secret-for-voicekirana-orders-at-least-32-characters"
)


@pytest.fixture(autouse=True)
def isolate_orders_api_database(monkeypatch, tmp_path):
    """Use a fresh SQLite database for every order API test."""
    test_db_path = tmp_path / "voicekirana_orders_test.db"

    monkeypatch.setattr(database, "DATABASE_URL", "")
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "DATABASE_PATH", test_db_path)
    monkeypatch.setattr(main, "VOICEKIRANA_API_TOKEN", TEST_API_TOKEN)
    monkeypatch.setenv("JWT_SECRET_KEY", TEST_JWT_SECRET)

    initialize_database()


@pytest.fixture
def client():
    """Create a test client using the isolated database."""
    return TestClient(main.app)


def create_shop_owner_headers(shop_id, role="owner"):
    """Create a test shop member and return its Bearer JWT header."""
    email = f"orders-test-{shop_id}-{role}@example.com"

    with get_connection() as connection:
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
                email,
                "Orders API Test User",
                "unused-test-password-hash",
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
            (user_id, shop_id, role),
        )

    token = create_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


def test_public_shop_directory_lists_active_shops(client):
    active_shop_id = create_shop("Public Catalogue Test Shop")
    inactive_shop_id = create_shop("Inactive Catalogue Test Shop")

    with get_connection() as connection:
        connection.execute(
            "UPDATE shops SET is_active = 0 WHERE id = ?",
            (inactive_shop_id,),
        )

    # No API key or shop-owner JWT is supplied.
    response = client.get("/shops")

    assert response.status_code == 200

    body = response.json()
    returned_ids = {
        shop["shop_id"] for shop in body["shops"]
    }

    assert active_shop_id in returned_ids
    assert inactive_shop_id not in returned_ids
    assert body["count"] == len(body["shops"])


def test_public_shop_directory_sorts_names_case_insensitively(client):
    create_shop("zeta Grocery")
    create_shop("Alpha Grocery")
    create_shop("beta Grocery")

    response = client.get("/shops")

    assert response.status_code == 200

    names = [
        shop["shop_name"]
        for shop in response.json()["shops"]
    ]

    assert names == sorted(names, key=str.casefold)


def test_public_shop_catalogue_hides_exact_stock_quantities(client):
    shop_id = create_shop("Public Product Catalogue Test Shop")

    product_id = create_product(
        name="Rice",
        base_unit="kg",
        brand="Aashirvaad",
        variant="Sona Masoori",
    )

    set_inventory(
        shop_id=shop_id,
        product_id=product_id,
        price_paise=6500,
        stock_quantity=20,
        is_available=True,
    )

    # Public catalogue access must not require staff authentication.
    response = client.get(f"/shops/{shop_id}/catalogue")

    assert response.status_code == 200

    body = response.json()
    assert body["shop_id"] == shop_id
    assert body["count"] == 1

    item = body["items"][0]

    assert item["product_id"] == product_id
    assert item["name"] == "Rice"
    assert item["brand"] == "Aashirvaad"
    assert item["variant"] == "Sona Masoori"
    assert item["base_unit"] == "kg"
    assert item["price_rupees"] == 65.0
    assert item["is_available"] is True

    assert "stock_quantity" not in item
    assert "reserved_quantity" not in item


def test_public_catalogue_rejects_inactive_or_unknown_shop(client):
    response = client.get("/shops/999999/catalogue")

    assert response.status_code == 404


def test_health_endpoint(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_complete_grocery_order(client):
    payload = {
        "intent": "place_order",
        "language": "te-IN",
        "items": [
            {
                "raw_name": "rice",
                "normalized_name": "rice",
                "quantity": 2,
                "unit": "kg"
            },
            {
                "raw_name": "sunflower oil",
                "normalized_name": "sunflower oil",
                "quantity": 1,
                "unit": "litre"
            }
        ]
    }

    response = client.post(
        "/orders/preview",
        json=payload,
        headers={"X-API-Key": TEST_API_TOKEN}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ready_for_inventory_check"
    assert response.json()["needs_clarification"] is False


def test_order_needing_clarification(client):
    payload = {
        "intent": "place_order",
        "language": "te-IN",
        "items": [
            {
                "raw_name": "organic jaggery",
                "normalized_name": "jaggery",
                "quantity": None,
                "unit": None,
                "needs_clarification": True,
                "clarification_reason": "Quantity was not specified."
            }
        ]
    }

    response = client.post(
        "/orders/preview",
        json=payload,
        headers={"X-API-Key": TEST_API_TOKEN}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "needs_clarification"
    assert response.json()["needs_clarification"] is True


def test_negative_quantity_is_rejected(client):
    payload = {
        "intent": "place_order",
        "language": "en",
        "items": [
            {
                "raw_name": "rice",
                "quantity": -2,
                "unit": "kg"
            }
        ]
    }

    response = client.post(
        "/orders/preview",
        json=payload,
        headers={"X-API-Key": TEST_API_TOKEN}
    )

    assert response.status_code == 422


def test_zero_quantity_is_rejected(client):
    payload = {
        "intent": "place_order",
        "language": "en",
        "items": [
            {
                "raw_name": "rice",
                "quantity": 0,
                "unit": "kg"
            }
        ]
    }

    response = client.post(
        "/orders/preview",
        json=payload,
        headers={"X-API-Key": TEST_API_TOKEN}
    )

    assert response.status_code == 422


def test_missing_api_key_is_rejected(client):
    payload = {
        "intent": "place_order",
        "language": "en",
        "items": [
            {
                "raw_name": "rice",
                "quantity": 2,
                "unit": "kg"
            }
        ]
    }

    response = client.post(
        "/orders/preview",
        json=payload
    )

    assert response.status_code == 401


def test_invalid_api_key_is_rejected(client):
    payload = {
        "intent": "place_order",
        "language": "en",
        "items": [
            {
                "raw_name": "rice",
                "quantity": 2,
                "unit": "kg"
            }
        ]
    }

    response = client.post(
        "/orders/preview",
        json=payload,
        headers={"X-API-Key": "incorrect-test-token"}
    )

    assert response.status_code == 401


def test_inventory_check_success(client):
    initialize_database()
    shop_id = create_shop("Inventory API Test Shop")
    product_id = create_product(
        name="Rice",
        base_unit="kg",
    )
    set_inventory(
        shop_id=shop_id,
        product_id=product_id,
        price_paise=6500,
        stock_quantity=20,
    )

    response = client.post(
        f"/inventory/check?shop_id={shop_id}",
        json={
            "intent": "place_order",
            "items": [
                {
                    "raw_name": "rice",
                    "normalized_name": "rice",
                    "quantity": 2,
                    "unit": "kg",
                }
            ],
        },
        headers={"X-API-Key": TEST_API_TOKEN},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "inventory_checked"
    assert data["total_paise"] == 13000
    assert data["total_rupees"] == 130.0
    assert data["order_placed"] is False
    assert data["stock_reserved"] is False


def test_inventory_check_product_not_found(client):
    initialize_database()
    shop_id = create_shop("Missing Product Test Shop")

    response = client.post(
        f"/inventory/check?shop_id={shop_id}",
        json={
            "intent": "place_order",
            "items": [
                {
                    "raw_name": "dragon fruit",
                    "quantity": 1,
                    "unit": "piece",
                }
            ],
        },
        headers={"X-API-Key": TEST_API_TOKEN},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "needs_attention"
    assert data["items"][0]["status"] == "product_not_found"
    assert data["total_paise"] is None


def test_inventory_check_insufficient_stock(client):
    initialize_database()
    shop_id = create_shop("Stock Test Shop")
    product_id = create_product(
        name="Rice",
        base_unit="kg",
    )
    set_inventory(
        shop_id=shop_id,
        product_id=product_id,
        price_paise=6500,
        stock_quantity=1,
    )

    response = client.post(
        f"/inventory/check?shop_id={shop_id}",
        json={
            "intent": "place_order",
            "items": [
                {
                    "raw_name": "rice",
                    "quantity": 2,
                    "unit": "kg",
                }
            ],
        },
        headers={"X-API-Key": TEST_API_TOKEN},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "needs_attention"
    assert data["items"][0]["status"] == "insufficient_stock"
    assert data["total_paise"] is None


def test_inventory_check_requires_authentication(client):
    response = client.post(
        "/inventory/check?shop_id=1",
        json={
            "intent": "place_order",
            "items": [
                {
                    "raw_name": "rice",
                    "quantity": 1,
                    "unit": "kg",
                }
            ],
        },
    )

    assert response.status_code == 401


def test_confirm_order_saves_order_and_items(client):
    initialize_database()

    shop_id = create_shop("Order Confirmation Test Shop")
    product_id = create_product(
        name="Rice",
        base_unit="kg",
    )
    set_inventory(
        shop_id=shop_id,
        product_id=product_id,
        price_paise=6500,
        stock_quantity=20,
    )

    response = client.post(
        f"/orders/confirm?shop_id={shop_id}",
        json={
            "intent": "place_order",
            "items": [
                {
                    "raw_name": "rice",
                    "normalized_name": "rice",
                    "quantity": 2,
                    "unit": "kg",
                }
            ],
        },
        headers={"X-API-Key": TEST_API_TOKEN},
    )

    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "pending"
    assert data["order_reference"].startswith("VK-")
    assert data["total_paise"] == 13000
    assert data["stock_reserved"] is False
    assert len(data["items"]) == 1

    shop_headers = create_shop_owner_headers(shop_id)

    history_response = client.get(
        f"/orders/history?shop_id={shop_id}",
        headers=shop_headers,
    )

    assert history_response.status_code == 200

    history = history_response.json()
    assert history["count"] == 1
    assert history["orders"][0]["order_reference"] == data["order_reference"]
    assert history["orders"][0]["total_paise"] == 13000
    assert history["orders"][0]["items"][0]["product_name"] == "Rice"


def test_confirm_order_rejects_unmatched_product(client):
    initialize_database()

    shop_id = create_shop("Invalid Order Test Shop")

    with get_connection() as connection:
        before = connection.execute(
            "SELECT COUNT(*) AS total FROM orders"
        ).fetchone()["total"]

    response = client.post(
        f"/orders/confirm?shop_id={shop_id}",
        json={
            "intent": "place_order",
            "items": [
                {
                    "raw_name": "unlisted mystery product",
                    "quantity": 1,
                    "unit": "piece",
                }
            ],
        },
        headers={"X-API-Key": TEST_API_TOKEN},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["inventory_result"]["status"] == (
        "needs_attention"
    )

    with get_connection() as connection:
        after = connection.execute(
            "SELECT COUNT(*) AS total FROM orders"
        ).fetchone()["total"]

    assert after == before


def test_order_history_requires_authentication(client):
    response = client.get(
        "/orders/history?shop_id=1",
        headers={"X-API-Key": TEST_API_TOKEN},
    )

    assert response.status_code == 401


def test_manager_can_access_assigned_shop_order_history(client):
    shop_id = create_shop("Manager Order History Test Shop")
    manager_headers = create_shop_owner_headers(
        shop_id,
        role="manager",
    )

    response = client.get(
        f"/orders/history?shop_id={shop_id}",
        headers=manager_headers,
    )

    assert response.status_code == 200
    assert response.json()["shop_id"] == shop_id
    assert response.json()["count"] == 0


def _create_pending_rice_order(client, shop_name="Day 8 Order Test Shop"):
    """Create an isolated active shop and one pending rice order."""
    initialize_database()
    shop_id = create_shop(shop_name)
    product_id = create_product(name="Rice", base_unit="kg")
    set_inventory(
        shop_id=shop_id,
        product_id=product_id,
        price_paise=6500,
        stock_quantity=20,
    )

    response = client.post(
        f"/orders/confirm?shop_id={shop_id}",
        json={
            "intent": "place_order",
            "items": [
                {
                    "raw_name": "rice",
                    "normalized_name": "rice",
                    "quantity": 2,
                    "unit": "kg",
                }
            ],
        },
        headers={"X-API-Key": TEST_API_TOKEN},
    )
    assert response.status_code == 200
    shop_headers = create_shop_owner_headers(shop_id)
    return shop_id, response.json(), shop_headers


def test_get_order_by_reference_returns_saved_order(client):
    shop_id, saved, shop_headers = _create_pending_rice_order(
        client, "Day 8 Lookup Shop"
    )

    response = client.get(
        f"/orders/{saved['order_reference']}?shop_id={shop_id}",
        headers=shop_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["order_reference"] == saved["order_reference"]
    assert data["status"] == "pending"
    assert data["total_paise"] == 13000
    assert data["items"][0]["product_name"] == "Rice"
    assert data["items"][0]["quantity"] == 2


def test_get_order_by_unknown_reference_returns_404(client):
    initialize_database()
    shop_id = create_shop("Day 8 Unknown Order Shop")

    shop_headers = create_shop_owner_headers(shop_id)
    response = client.get(
        f"/orders/VK-DOESNOTEXIST?shop_id={shop_id}",
        headers=shop_headers,
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Order not found."


def test_order_lookup_is_scoped_to_shop(client):
    shop_id, saved, shop_headers = _create_pending_rice_order(
        client, "Day 8 Private Shop"
    )
    other_shop_id = create_shop("Day 8 Other Shop")

    response = client.get(
        f"/orders/{saved['order_reference']}?shop_id={other_shop_id}",
        headers=shop_headers,
    )

    assert response.status_code == 403


def test_shop_owner_cannot_cancel_another_shops_order(client):
    shop_id, saved, shop_headers = _create_pending_rice_order(
        client, "Day 8 Private Cancellation Shop"
    )
    other_shop_id = create_shop("Day 8 Other Cancellation Shop")

    response = client.post(
        f"/orders/{saved['order_reference']}/cancel?shop_id={other_shop_id}",
        headers=shop_headers,
    )

    assert response.status_code == 403

    # The original shop's order must remain pending.
    lookup = client.get(
        f"/orders/{saved['order_reference']}?shop_id={shop_id}",
        headers=shop_headers,
    )
    assert lookup.status_code == 200
    assert lookup.json()["status"] == "pending"


def test_cancel_pending_order_persists_cancelled_status(client):
    shop_id, saved, shop_headers = _create_pending_rice_order(
        client, "Day 8 Cancellation Shop"
    )

    response = client.post(
        f"/orders/{saved['order_reference']}/cancel?shop_id={shop_id}",
        headers=shop_headers,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"

    lookup = client.get(
        f"/orders/{saved['order_reference']}?shop_id={shop_id}",
        headers=shop_headers,
    )
    assert lookup.status_code == 200
    assert lookup.json()["status"] == "cancelled"

    history = client.get(
        f"/orders/history?shop_id={shop_id}",
        headers=shop_headers,
    )
    assert history.status_code == 200
    assert history.json()["orders"][0]["status"] == "cancelled"


def test_cancelled_order_cannot_be_cancelled_again(client):
    shop_id, saved, shop_headers = _create_pending_rice_order(
        client, "Day 8 Repeat Cancel Shop"
    )
    url = f"/orders/{saved['order_reference']}/cancel?shop_id={shop_id}"
    headers = shop_headers

    first = client.post(url, headers=headers)
    second = client.post(url, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 409
    assert "Only pending or confirmed orders" in second.json()["detail"]


def test_order_lookup_requires_authentication(client):
    response = client.get(
        "/orders/VK-ANYREFERENCE?shop_id=1",
        headers={"X-API-Key": TEST_API_TOKEN},
    )
    assert response.status_code == 401


def test_order_cancellation_requires_authentication(client):
    response = client.post(
        "/orders/VK-ANYREFERENCE/cancel?shop_id=1",
        headers={"X-API-Key": TEST_API_TOKEN},
    )
    assert response.status_code == 401


def _deactivate_shop(shop_id):
    """Mark a shop inactive for API edge-case tests."""
    with get_connection() as connection:
        with connection:
            connection.execute(
                "UPDATE shops SET is_active = 0 WHERE id = ?",
                (shop_id,),
            )


def test_get_order_rejects_inactive_shop(client):
    shop_id, saved, shop_headers = _create_pending_rice_order(
        client, "Day 10 Inactive Lookup Shop"
    )
    _deactivate_shop(shop_id)

    response = client.get(
        f"/orders/{saved['order_reference']}?shop_id={shop_id}",
        headers=shop_headers,
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "You do not have access to this shop."


def test_cancel_order_rejects_inactive_shop(client):
    shop_id, saved, shop_headers = _create_pending_rice_order(
        client, "Day 10 Inactive Cancellation Shop"
    )
    _deactivate_shop(shop_id)

    response = client.post(
        f"/orders/{saved['order_reference']}/cancel?shop_id={shop_id}",
        headers=shop_headers,
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "You do not have access to this shop."


def test_cancelling_unknown_order_returns_404(client):
    initialize_database()
    shop_id = create_shop("Day 10 Missing Cancellation Shop")

    shop_headers = create_shop_owner_headers(shop_id)
    response = client.post(
        f"/orders/VK-DOESNOTEXIST/cancel?shop_id={shop_id}",
        headers=shop_headers,
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Order not found."


def test_cancellation_preserves_order_total_and_items(client):
    shop_id, saved, shop_headers = _create_pending_rice_order(
        client, "Day 10 Order Integrity Shop"
    )
    reference = saved["order_reference"]
    headers = shop_headers

    before = client.get(
        f"/orders/{reference}?shop_id={shop_id}",
        headers=headers,
    )
    assert before.status_code == 200

    before_data = before.json()

    cancelled = client.post(
        f"/orders/{reference}/cancel?shop_id={shop_id}",
        headers=headers,
    )
    assert cancelled.status_code == 200

    after = client.get(
        f"/orders/{reference}?shop_id={shop_id}",
        headers=headers,
    )
    assert after.status_code == 200

    after_data = after.json()
    assert after_data["status"] == "cancelled"
    assert after_data["total_paise"] == before_data["total_paise"]
    assert after_data["items"] == before_data["items"]

def test_shop_owner_can_accept_pending_order(client):
    shop_id, saved, shop_headers = _create_pending_rice_order(
        client, "Day 20 Acceptance Shop"
    )

    url = (
        f"/orders/{saved['order_reference']}/accept"
        f"?shop_id={shop_id}"
    )

    response = client.post(url, headers=shop_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "confirmed"
    assert data["stock_reserved"] is True

    with get_connection() as connection:
        inventory = connection.execute(
            "SELECT stock_quantity, reserved_quantity FROM inventory "
            "WHERE shop_id = ?",
            (shop_id,),
        ).fetchone()

    assert inventory["stock_quantity"] == 20
    assert inventory["reserved_quantity"] == 2
    assert data["order_reference"] == saved["order_reference"]

    lookup = client.get(
        f"/orders/{saved['order_reference']}?shop_id={shop_id}",
        headers=shop_headers,
    )
    assert lookup.status_code == 200
    assert lookup.json()["status"] == "confirmed"

    # A second acceptance must not silently succeed.
    repeated = client.post(url, headers=shop_headers)
    assert repeated.status_code == 409


def test_shop_manager_can_accept_pending_order(client):
    shop_id, saved, _ = _create_pending_rice_order(
        client, "Day 20 Manager Acceptance Shop"
    )
    manager_headers = create_shop_owner_headers(
        shop_id,
        role="manager",
    )

    response = client.post(
        f"/orders/{saved['order_reference']}/accept?shop_id={shop_id}",
        headers=manager_headers,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "confirmed"


def test_order_acceptance_requires_shop_authentication(client):
    shop_id, saved, _ = _create_pending_rice_order(
        client, "Day 20 Acceptance Authentication Shop"
    )

    response = client.post(
        f"/orders/{saved['order_reference']}/accept?shop_id={shop_id}"
    )

    assert response.status_code == 401


def test_shop_member_cannot_accept_another_shops_order(client):
    shop_id, saved, shop_headers = _create_pending_rice_order(
        client, "Day 20 Private Acceptance Shop"
    )
    other_shop_id = create_shop("Day 20 Other Acceptance Shop")

    response = client.post(
        f"/orders/{saved['order_reference']}/accept?shop_id={other_shop_id}",
        headers=shop_headers,
    )

    assert response.status_code == 403

    lookup = client.get(
        f"/orders/{saved['order_reference']}?shop_id={shop_id}",
        headers=shop_headers,
    )
    assert lookup.status_code == 200
    assert lookup.json()["status"] == "pending"


def test_cancelled_order_cannot_be_accepted(client):
    shop_id, saved, shop_headers = _create_pending_rice_order(
        client, "Day 20 Cancelled Acceptance Shop"
    )

    cancel_response = client.post(
        f"/orders/{saved['order_reference']}/cancel?shop_id={shop_id}",
        headers=shop_headers,
    )
    assert cancel_response.status_code == 200

    accept_response = client.post(
        f"/orders/{saved['order_reference']}/accept?shop_id={shop_id}",
        headers=shop_headers,
    )

    assert accept_response.status_code == 409
    assert accept_response.json()["detail"].startswith(
        "Only pending orders can be accepted."
    )

def test_shop_owner_can_reject_pending_order(client):
    shop_id, saved, shop_headers = _create_pending_rice_order(
        client, "Day 20 Rejection Shop"
    )
    reason = "Requested brand unavailable"

    url = (
        f"/orders/{saved['order_reference']}/reject"
        f"?shop_id={shop_id}"
    )

    response = client.post(
        url,
        json={"reason": reason},
        headers=shop_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "rejected"
    assert data["rejection_reason"] == reason
    assert data["stock_reserved"] is False

    lookup = client.get(
        f"/orders/{saved['order_reference']}?shop_id={shop_id}",
        headers=shop_headers,
    )
    assert lookup.status_code == 200
    assert lookup.json()["rejection_reason"] == reason

    history = client.get(
        f"/orders/history?shop_id={shop_id}",
        headers=shop_headers,
    )
    assert history.status_code == 200
    assert history.json()["orders"][0]["rejection_reason"] == reason

    repeated = client.post(
        url,
        json={"reason": reason},
        headers=shop_headers,
    )
    assert repeated.status_code == 409


def test_shop_manager_can_reject_pending_order(client):
    shop_id, saved, _ = _create_pending_rice_order(
        client, "Day 20 Manager Rejection Shop"
    )
    manager_headers = create_shop_owner_headers(
        shop_id,
        role="manager",
    )

    response = client.post(
        f"/orders/{saved['order_reference']}/reject?shop_id={shop_id}",
        json={"reason": "Shop temporarily out of stock"},
        headers=manager_headers,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


def test_order_rejection_requires_shop_authentication(client):
    shop_id, saved, _ = _create_pending_rice_order(
        client, "Day 20 Rejection Authentication Shop"
    )

    response = client.post(
        f"/orders/{saved['order_reference']}/reject?shop_id={shop_id}",
        json={"reason": "Product unavailable"},
    )

    assert response.status_code == 401


def test_shop_member_cannot_reject_another_shops_order(client):
    shop_id, saved, shop_headers = _create_pending_rice_order(
        client, "Day 20 Private Rejection Shop"
    )
    other_shop_id = create_shop("Day 20 Other Rejection Shop")

    response = client.post(
        f"/orders/{saved['order_reference']}/reject?shop_id={other_shop_id}",
        json={"reason": "Product unavailable"},
        headers=shop_headers,
    )

    assert response.status_code == 403

    lookup = client.get(
        f"/orders/{saved['order_reference']}?shop_id={shop_id}",
        headers=shop_headers,
    )
    assert lookup.status_code == 200
    assert lookup.json()["status"] == "pending"


def test_confirmed_order_cannot_be_rejected(client):
    shop_id, saved, shop_headers = _create_pending_rice_order(
        client, "Day 20 Confirmed Rejection Shop"
    )

    accepted = client.post(
        f"/orders/{saved['order_reference']}/accept?shop_id={shop_id}",
        headers=shop_headers,
    )
    assert accepted.status_code == 200

    rejected = client.post(
        f"/orders/{saved['order_reference']}/reject?shop_id={shop_id}",
        json={"reason": "Product unavailable"},
        headers=shop_headers,
    )

    assert rejected.status_code == 409
    assert rejected.json()["detail"].startswith(
        "Only pending orders can be rejected."
    )


@pytest.mark.parametrize("reason", ["", "  ", "ab"])
def test_invalid_order_rejection_reason_is_rejected(client, reason):
    shop_id, saved, shop_headers = _create_pending_rice_order(
        client, "Day 20 Invalid Rejection Reason Shop"
    )

    response = client.post(
        f"/orders/{saved['order_reference']}/reject?shop_id={shop_id}",
        json={"reason": reason},
        headers=shop_headers,
    )

    assert response.status_code == 422

def _create_rice_order_for_reservation(client, shop_id, quantity):
    response = client.post(
        f"/orders/confirm?shop_id={shop_id}",
        json={
            "intent": "place_order",
            "items": [
                {
                    "raw_name": "rice",
                    "normalized_name": "rice",
                    "quantity": quantity,
                    "unit": "kg",
                }
            ],
        },
        headers={"X-API-Key": TEST_API_TOKEN},
    )
    assert response.status_code == 200
    return response.json()


def _get_inventory_quantities(shop_id, product_id):
    with get_connection() as connection:
        return connection.execute(
            "SELECT stock_quantity, reserved_quantity FROM inventory "
            "WHERE shop_id = ? AND product_id = ?",
            (shop_id, product_id),
        ).fetchone()


def test_two_orders_cannot_reserve_the_same_stock(client):
    shop_id = create_shop("Day 20 Reservation Concurrency Shop")
    product_id = create_product("Rice", "kg")
    set_inventory(shop_id, product_id, 6500, 5)

    first = _create_rice_order_for_reservation(client, shop_id, 4)
    second = _create_rice_order_for_reservation(client, shop_id, 4)
    headers = create_shop_owner_headers(shop_id)

    first_accept = client.post(
        f"/orders/{first['order_reference']}/accept?shop_id={shop_id}",
        headers=headers,
    )
    second_accept = client.post(
        f"/orders/{second['order_reference']}/accept?shop_id={shop_id}",
        headers=headers,
    )

    assert first_accept.status_code == 200
    assert first_accept.json()["stock_reserved"] is True

    assert second_accept.status_code == 409
    assert second_accept.json()["detail"]["available_quantity"] == 1
    assert second_accept.json()["detail"]["requested_quantity"] == 4

    inventory = _get_inventory_quantities(shop_id, product_id)
    assert inventory["stock_quantity"] == 5
    assert inventory["reserved_quantity"] == 4

    second_lookup = client.get(
        f"/orders/{second['order_reference']}?shop_id={shop_id}",
        headers=headers,
    )
    assert second_lookup.status_code == 200
    assert second_lookup.json()["status"] == "pending"


def test_failed_multi_product_reservation_rolls_back_all_stock(client):
    shop_id = create_shop("Day 20 Reservation Rollback Shop")
    rice_id = create_product("Rice", "kg")
    wheat_id = create_product("Wheat", "kg")

    set_inventory(shop_id, rice_id, 6500, 10)
    set_inventory(shop_id, wheat_id, 5000, 10)

    response = client.post(
        f"/orders/confirm?shop_id={shop_id}",
        json={
            "intent": "place_order",
            "items": [
                {"raw_name": "rice", "quantity": 2, "unit": "kg"},
                {"raw_name": "wheat", "quantity": 2, "unit": "kg"},
            ],
        },
        headers={"X-API-Key": TEST_API_TOKEN},
    )
    assert response.status_code == 200

    # Simulate stock changing after order creation but before acceptance.
    set_inventory(shop_id, wheat_id, 5000, 1)

    headers = create_shop_owner_headers(shop_id)
    reference = response.json()["order_reference"]

    accepted = client.post(
        f"/orders/{reference}/accept?shop_id={shop_id}",
        headers=headers,
    )

    assert accepted.status_code == 409

    rice_stock = _get_inventory_quantities(shop_id, rice_id)
    wheat_stock = _get_inventory_quantities(shop_id, wheat_id)

    # The earlier rice reservation must also be rolled back.
    assert rice_stock["reserved_quantity"] == 0
    assert wheat_stock["reserved_quantity"] == 0

    lookup = client.get(
        f"/orders/{reference}?shop_id={shop_id}",
        headers=headers,
    )
    assert lookup.status_code == 200
    assert lookup.json()["status"] == "pending"


def test_inventory_check_subtracts_reserved_stock(client):
    shop_id = create_shop("Day 20 Available Stock Shop")
    product_id = create_product("Rice", "kg")
    set_inventory(shop_id, product_id, 6500, 5)

    saved = _create_rice_order_for_reservation(client, shop_id, 4)
    headers = create_shop_owner_headers(shop_id)

    accepted = client.post(
        f"/orders/{saved['order_reference']}/accept?shop_id={shop_id}",
        headers=headers,
    )
    assert accepted.status_code == 200

    check = client.post(
        f"/inventory/check?shop_id={shop_id}",
        json={
            "intent": "place_order",
            "items": [
                {"raw_name": "rice", "quantity": 2, "unit": "kg"}
            ],
        },
        headers={"X-API-Key": TEST_API_TOKEN},
    )

    assert check.status_code == 200
    item = check.json()["items"][0]
    assert item["status"] == "insufficient_stock"
    assert item["available_quantity"] == 1


def test_stock_cannot_be_reduced_below_reserved_quantity(client):
    shop_id = create_shop("Day 20 Reserved Stock Update Shop")
    product_id = create_product("Rice", "kg")
    set_inventory(shop_id, product_id, 6500, 5)

    saved = _create_rice_order_for_reservation(client, shop_id, 4)
    headers = create_shop_owner_headers(shop_id)

    accepted = client.post(
        f"/orders/{saved['order_reference']}/accept?shop_id={shop_id}",
        headers=headers,
    )
    assert accepted.status_code == 200

    with pytest.raises(
        ValueError,
        match="Physical stock cannot be reduced below",
    ):
        set_inventory(shop_id, product_id, 6500, 3)

    inventory = _get_inventory_quantities(shop_id, product_id)
    assert inventory["stock_quantity"] == 5
    assert inventory["reserved_quantity"] == 4

def _read_reserved_quantity(shop_id, product_id):
    """Read the current reserved quantity for one shop product."""
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT reserved_quantity
            FROM inventory
            WHERE shop_id = ? AND product_id = ?
            """,
            (shop_id, product_id),
        ).fetchone()

    assert row is not None
    return float(row["reserved_quantity"])


def test_cancelling_confirmed_order_releases_reserved_stock_once(client):
    shop_id, saved, shop_headers = _create_pending_rice_order(
        client, "Day 20 Confirmed Cancellation Shop"
    )
    order_reference = saved["order_reference"]
    product_id = saved["items"][0]["product_id"]

    accepted = client.post(
        f"/orders/{order_reference}/accept?shop_id={shop_id}",
        headers=shop_headers,
    )

    assert accepted.status_code == 200
    assert accepted.json()["status"] == "confirmed"
    assert _read_reserved_quantity(shop_id, product_id) == 2

    cancel_url = (
        f"/orders/{order_reference}/cancel?shop_id={shop_id}"
    )
    cancelled = client.post(cancel_url, headers=shop_headers)

    assert cancelled.status_code == 200
    data = cancelled.json()
    assert data["status"] == "cancelled"
    assert data["stock_released"] is True
    assert data["released_items"] == [
        {"product_id": product_id, "quantity": 2.0}
    ]
    assert _read_reserved_quantity(shop_id, product_id) == 0

    # A repeated cancellation must not release inventory a second time.
    repeated = client.post(cancel_url, headers=shop_headers)

    assert repeated.status_code == 409
    assert _read_reserved_quantity(shop_id, product_id) == 0


def test_cancelling_confirmed_multi_product_order_releases_all_stock(client):
    initialize_database()
    shop_id = create_shop("Day 20 Multi Product Cancellation Shop")

    rice_id = create_product(name="Rice", base_unit="kg")
    wheat_id = create_product(name="Wheat", base_unit="kg")

    set_inventory(
        shop_id=shop_id,
        product_id=rice_id,
        price_paise=6500,
        stock_quantity=20,
    )
    set_inventory(
        shop_id=shop_id,
        product_id=wheat_id,
        price_paise=5000,
        stock_quantity=20,
    )

    shop_headers = create_shop_owner_headers(shop_id)

    created = client.post(
        f"/orders/confirm?shop_id={shop_id}",
        json={
            "intent": "place_order",
            "items": [
                {
                    "raw_name": "rice",
                    "normalized_name": "rice",
                    "quantity": 2,
                    "unit": "kg",
                },
                {
                    "raw_name": "wheat",
                    "normalized_name": "wheat",
                    "quantity": 3,
                    "unit": "kg",
                },
            ],
        },
        headers={"X-API-Key": TEST_API_TOKEN},
    )

    assert created.status_code == 200
    order_reference = created.json()["order_reference"]

    accepted = client.post(
        f"/orders/{order_reference}/accept?shop_id={shop_id}",
        headers=shop_headers,
    )

    assert accepted.status_code == 200
    assert _read_reserved_quantity(shop_id, rice_id) == 2
    assert _read_reserved_quantity(shop_id, wheat_id) == 3

    cancelled = client.post(
        f"/orders/{order_reference}/cancel?shop_id={shop_id}",
        headers=shop_headers,
    )

    assert cancelled.status_code == 200
    assert cancelled.json()["stock_released"] is True
    assert _read_reserved_quantity(shop_id, rice_id) == 0
    assert _read_reserved_quantity(shop_id, wheat_id) == 0


def test_failed_inventory_release_rolls_back_cancellation(client):
    shop_id, saved, shop_headers = _create_pending_rice_order(
        client, "Day 20 Failed Release Rollback Shop"
    )
    order_reference = saved["order_reference"]
    product_id = saved["items"][0]["product_id"]

    accepted = client.post(
        f"/orders/{order_reference}/accept?shop_id={shop_id}",
        headers=shop_headers,
    )

    assert accepted.status_code == 200
    assert _read_reserved_quantity(shop_id, product_id) == 2

    # Simulate inconsistent reservation data: the order requires 2,
    # but only 1 unit is currently recorded as reserved.
    with get_connection() as connection:
        with connection:
            connection.execute(
                """
                UPDATE inventory
                SET reserved_quantity = 1
                WHERE shop_id = ? AND product_id = ?
                """,
                (shop_id, product_id),
            )

    cancelled = client.post(
        f"/orders/{order_reference}/cancel?shop_id={shop_id}",
        headers=shop_headers,
    )

    assert cancelled.status_code == 409
    assert "Cancellation was rolled back" in cancelled.json()["detail"]

    lookup = client.get(
        f"/orders/{order_reference}?shop_id={shop_id}",
        headers=shop_headers,
    )

    assert lookup.status_code == 200
    assert lookup.json()["status"] == "confirmed"
    assert _read_reserved_quantity(shop_id, product_id) == 1


def test_rejected_order_cannot_be_cancelled(client):
    shop_id, saved, shop_headers = _create_pending_rice_order(
        client, "Day 20 Rejected Cancellation Shop"
    )
    order_reference = saved["order_reference"]

    rejected = client.post(
        f"/orders/{order_reference}/reject?shop_id={shop_id}",
        json={"reason": "The requested item is unavailable."},
        headers=shop_headers,
    )

    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"

    cancelled = client.post(
        f"/orders/{order_reference}/cancel?shop_id={shop_id}",
        headers=shop_headers,
    )

    assert cancelled.status_code == 409
    assert "Only pending or confirmed orders" in cancelled.json()["detail"]

    lookup = client.get(
        f"/orders/{order_reference}?shop_id={shop_id}",
        headers=shop_headers,
    )

    assert lookup.status_code == 200
    assert lookup.json()["status"] == "rejected"
