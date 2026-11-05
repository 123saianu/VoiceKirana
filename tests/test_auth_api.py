import pytest

from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import database
from backend.auth_dependencies import authorize_shop_access
from backend.main import app


ADMIN_TOKEN = "test-admin-token-for-voicekirana"
JWT_SECRET = "test-jwt-secret-for-voicekirana-at-least-32-characters"

OWNER_PASSWORD = "VoiceKirana-Test-Password-123!"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Give every API test its own isolated SQLite database."""

    monkeypatch.setattr(database, "DATABASE_URL", "")
    monkeypatch.setattr(
        database,
        "DATABASE_PATH",
        tmp_path / "voicekirana_auth_test.db",
    )

    monkeypatch.setenv("JWT_SECRET_KEY", JWT_SECRET)
    monkeypatch.setenv("VOICEKIRANA_ADMIN_TOKEN", ADMIN_TOKEN)

    database.initialize_database()

    with TestClient(app) as test_client:
        yield test_client


def create_shop(client, name="Authentication Test Shop"):
    response = client.post(
        "/admin/shops",
        headers={"X-Admin-Token": ADMIN_TOKEN},
        json={"name": name},
    )

    assert response.status_code == 201
    return response.json()["shop_id"]


def create_owner(client, shop_id, email="owner@example.com"):
    response = client.post(
        "/admin/shops/owners",
        headers={"X-Admin-Token": ADMIN_TOKEN},
        json={
            "email": email,
            "full_name": "Test Shop Owner",
            "password": OWNER_PASSWORD,
            "shop_id": shop_id,
            "role": "owner",
        },
    )

    assert response.status_code == 201
    return response.json()


def login_owner(client, password=OWNER_PASSWORD):
    return client.post(
        "/auth/login",
        json={
            "email": "owner@example.com",
            "password": password,
        },
    )


def test_admin_token_required_for_shop_creation(client):
    response = client.post(
        "/admin/shops",
        json={"name": "Unauthorized Shop"},
    )

    assert response.status_code == 401

    print("PASS: Administrator token is required")


def test_admin_can_create_shop(client):
    response = client.post(
        "/admin/shops",
        headers={"X-Admin-Token": ADMIN_TOKEN},
        json={"name": "Sai Kirana Store"},
    )

    assert response.status_code == 201
    assert response.json()["shop_name"] == "Sai Kirana Store"

    print("PASS: Administrator can create a shop")


def test_admin_can_create_shop_owner(client):
    shop_id = create_shop(client)

    owner = create_owner(client, shop_id)

    assert owner["shop_id"] == shop_id
    assert owner["email"] == "owner@example.com"
    assert owner["role"] == "owner"

    print("PASS: Administrator can create and assign an owner")


def test_owner_can_login_and_view_profile(client):
    shop_id = create_shop(client)
    create_owner(client, shop_id)

    login_response = login_owner(client)

    assert login_response.status_code == 200

    token = login_response.json()["access_token"]

    profile_response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert profile_response.status_code == 200

    profile = profile_response.json()

    assert profile["email"] == "owner@example.com"
    assert len(profile["shops"]) == 1
    assert profile["shops"][0]["shop_id"] == shop_id

    print("PASS: Owner login and profile retrieval")


def test_owner_sees_only_assigned_shops(client):
    assigned_shop = create_shop(client, "Assigned Shop")
    create_shop(client, "Other Shop")

    create_owner(client, assigned_shop)

    login_response = login_owner(client)
    token = login_response.json()["access_token"]

    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200

    shops = response.json()["shops"]

    assert len(shops) == 1
    assert shops[0]["shop_id"] == assigned_shop

    print("PASS: Owner profile only exposes assigned shops")


def test_incorrect_password_is_rejected(client):
    shop_id = create_shop(client)
    create_owner(client, shop_id)

    response = login_owner(
        client,
        password="Incorrect-Password-123!",
    )

    assert response.status_code == 401

    print("PASS: Incorrect password rejected")


def test_duplicate_owner_email_is_rejected(client):
    shop_id = create_shop(client)

    create_owner(client, shop_id)

    second_shop_id = create_shop(client, "Second Test Shop")

    response = client.post(
        "/admin/shops/owners",
        headers={"X-Admin-Token": ADMIN_TOKEN},
        json={
            "email": "owner@example.com",
            "full_name": "Another Owner",
            "password": OWNER_PASSWORD,
            "shop_id": second_shop_id,
            "role": "owner",
        },
    )

    assert response.status_code == 409

    print("PASS: Duplicate owner email rejected")


def test_owner_cannot_register_for_inactive_shop(client):
    shop_id = create_shop(client)

    with database.get_connection() as connection:
        connection.execute(
            "UPDATE shops SET is_active = 0 WHERE id = ?",
            (shop_id,),
        )

    response = client.post(
        "/admin/shops/owners",
        headers={"X-Admin-Token": ADMIN_TOKEN},
        json={
            "email": "inactive@example.com",
            "full_name": "Inactive Shop Owner",
            "password": OWNER_PASSWORD,
            "shop_id": shop_id,
        },
    )

    assert response.status_code == 404

    print("PASS: Owners cannot be assigned to inactive shops")


def test_invalid_jwt_is_rejected(client):
    response = client.get(
        "/auth/me",
        headers={"Authorization": "Bearer invalid-token"},
    )

    assert response.status_code == 401

    print("PASS: Invalid JWT rejected")


def test_inactive_owner_account_loses_access(client):
    shop_id = create_shop(client)
    owner = create_owner(client, shop_id)

    login_response = login_owner(client)
    token = login_response.json()["access_token"]

    with database.get_connection() as connection:
        connection.execute(
            "UPDATE users SET is_active = 0 WHERE id = ?",
            (owner["user_id"],),
        )

    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401

    print("PASS: Deactivated owner account loses access")


def test_owner_can_access_assigned_shop():
    user = {
        "user_id": 1,
        "shops": [
            {
                "shop_id": 10,
                "shop_name": "Shop A",
                "role": "owner",
            }
        ],
    }

    membership = authorize_shop_access(user, 10)

    assert membership["shop_name"] == "Shop A"

    print("PASS: Owner can access assigned shop")


def test_owner_cannot_access_another_shop():
    user = {
        "user_id": 1,
        "shops": [
            {
                "shop_id": 10,
                "shop_name": "Shop A",
                "role": "owner",
            }
        ],
    }

    with pytest.raises(HTTPException) as exc:
        authorize_shop_access(user, 20)

    assert exc.value.status_code == 403

    print("PASS: Cross-shop access rejected")


def test_manager_cannot_perform_owner_only_operation():
    user = {
        "user_id": 2,
        "shops": [
            {
                "shop_id": 10,
                "shop_name": "Shop A",
                "role": "manager",
            }
        ],
    }

    with pytest.raises(HTTPException) as exc:
        authorize_shop_access(
            user,
            10,
            allowed_roles={"owner"},
        )

    assert exc.value.status_code == 403

    print("PASS: Manager restricted from owner-only operation")
