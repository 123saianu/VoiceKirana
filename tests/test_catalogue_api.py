import pytest

from fastapi.testclient import TestClient

from backend.main import app
from backend import main
from backend.auth_dependencies import get_current_user


@pytest.fixture
def client():
    """
    Use a controlled authenticated user for route-level tests.
    The user's default membership is owner of shop 10.
    """
    original_overrides = app.dependency_overrides.copy()

    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": 1,
        "email": "owner@example.com",
        "full_name": "Test Owner",
        "shops": [
            {
                "shop_id": 10,
                "role": "owner",
            }
        ],
    }

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
    app.dependency_overrides.update(original_overrides)


def set_test_memberships(memberships):
    """
    Change the authenticated user's shop memberships for a test.
    """
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": 1,
        "email": "owner@example.com",
        "full_name": "Test User",
        "shops": memberships,
    }


# ------------------------------------------------------------
# SUCCESSFUL ACCESS TESTS
# ------------------------------------------------------------

def test_owner_can_view_assigned_shop_inventory(client, monkeypatch):
    monkeypatch.setattr(
        main,
        "get_shop_inventory",
        lambda shop_id: [
            {
                "product_id": 101,
                "name": "rice",
                "price_rupees": 65.0,
                "stock_quantity": 10,
            }
        ],
    )

    response = client.get("/shops/10/inventory")

    assert response.status_code == 200
    assert response.json()["shop_id"] == 10
    assert response.json()["count"] == 1


def test_owner_can_register_product_in_assigned_shop(client, monkeypatch):
    monkeypatch.setattr(
        main,
        "register_product_for_shop",
        lambda shop_id, payload: {
            "shop_id": shop_id,
            "product_id": 101,
            "product_created": True,
        },
    )

    response = client.post(
        "/shops/10/products",
        json={
            "name": "rice",
            "base_unit": "kg",
            "price_paise": 6500,
            "stock_quantity": 10,
            "aliases": ["biyyam"],
        },
    )

    assert response.status_code == 200
    assert response.json()["product_id"] == 101


def test_owner_can_update_assigned_shop_inventory(client, monkeypatch):
    monkeypatch.setattr(
        main,
        "update_shop_inventory",
        lambda shop_id, product_id, updates: {
            "shop_id": shop_id,
            "product_id": product_id,
            "stock_quantity": updates["stock_quantity"],
        },
    )

    response = client.put(
        "/shops/10/inventory/101",
        json={"stock_quantity": 5},
    )

    assert response.status_code == 200
    assert response.json()["stock_quantity"] == 5


def test_owner_can_add_product_alias(client, monkeypatch):
    monkeypatch.setattr(
        main,
        "add_shop_product_alias",
        lambda shop_id, product_id, alias: {
            "shop_id": shop_id,
            "product_id": product_id,
            "alias": alias,
        },
    )

    response = client.post(
        "/shops/10/products/101/aliases",
        json={"alias": "biyyam"},
    )

    assert response.status_code == 200
    assert response.json()["alias"] == "biyyam"


def test_manager_can_access_assigned_shop(client, monkeypatch):
    set_test_memberships([
        {
            "shop_id": 10,
            "role": "manager",
        }
    ])

    monkeypatch.setattr(main, "get_shop_inventory", lambda shop_id: [])

    response = client.get("/shops/10/inventory")

    assert response.status_code == 200


# ------------------------------------------------------------
# CROSS-SHOP ACCESS PROTECTION
# ------------------------------------------------------------

@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("GET", "/shops/20/inventory", None),
        (
            "POST",
            "/shops/20/products",
            {
                "name": "rice",
                "base_unit": "kg",
                "price_paise": 6500,
                "stock_quantity": 10,
            },
        ),
        (
            "PUT",
            "/shops/20/inventory/101",
            {"stock_quantity": 5},
        ),
        (
            "POST",
            "/shops/20/products/101/aliases",
            {"alias": "biyyam"},
        ),
    ],
)
def test_owner_cannot_access_another_shops_catalogue(
    client,
    method,
    path,
    payload,
):
    response = client.request(
        method,
        path,
        json=payload,
    )

    assert response.status_code == 403


def test_user_with_unauthorized_role_cannot_manage_catalogue(client):
    set_test_memberships([
        {
            "shop_id": 10,
            "role": "cashier",
        }
    ])

    response = client.get("/shops/10/inventory")

    assert response.status_code == 403
