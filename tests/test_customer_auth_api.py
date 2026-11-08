
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from backend import database
from backend.main import app
from backend.security import create_access_token


JWT_SECRET = "VoiceKirana-Customer-Test-Secret-123456789"
CUSTOMER_PASSWORD = "Customer-Test-Password-123!"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Give every customer API test an isolated SQLite database."""

    monkeypatch.setattr(database, "DATABASE_URL", "")
    monkeypatch.setattr(
        database,
        "DATABASE_PATH",
        tmp_path / "voicekirana_customer_test.db",
    )

    monkeypatch.setenv("JWT_SECRET_KEY", JWT_SECRET)

    database.initialize_database()

    with TestClient(app) as test_client:
        yield test_client


def register_customer(
    client,
    email="customer@example.com",
    password=CUSTOMER_PASSWORD,
):
    return client.post(
        "/customers/register",
        json={
            "full_name": "Test Customer",
            "email": email,
            "password": password,
        },
    )


def login_customer(client, email="customer@example.com",
                   password=CUSTOMER_PASSWORD):
    return client.post(
        "/customers/login",
        json={
            "email": email,
            "password": password,
        },
    )


def test_customer_can_register(client):
    response = client.post(
        "/customers/register",
        json={
            "full_name": "  Test Customer  ",
            "email": "  Customer@Example.com  ",
            "password": CUSTOMER_PASSWORD,
        },
    )

    assert response.status_code == 201

    data = response.json()

    assert data["full_name"] == "Test Customer"
    assert data["email"] == "customer@example.com"
    assert data["customer_id"] > 0
    assert "password" not in data
    assert "password_hash" not in data

    print("PASS: Customer registration and input normalization")


def test_duplicate_customer_email_is_rejected(client):
    first = register_customer(client)

    assert first.status_code == 201

    second = register_customer(
        client,
        email="CUSTOMER@example.com",
    )

    assert second.status_code == 409

    print("PASS: Duplicate customer email rejected")


def test_invalid_customer_email_is_rejected(client):
    response = client.post(
        "/customers/register",
        json={
            "full_name": "Test Customer",
            "email": "invalid-email",
            "password": CUSTOMER_PASSWORD,
        },
    )

    assert response.status_code == 422

    print("PASS: Invalid customer email rejected")


def test_short_customer_password_is_rejected(client):
    response = register_customer(
        client,
        password="short",
    )

    assert response.status_code == 422

    print("PASS: Short customer password rejected")


def test_customer_can_login_and_view_profile(client):
    registration = register_customer(client)

    assert registration.status_code == 201

    login_response = login_customer(client)

    assert login_response.status_code == 200

    token = login_response.json()["access_token"]

    assert login_response.json()["token_type"] == "bearer"

    profile_response = client.get(
        "/customers/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert profile_response.status_code == 200

    profile = profile_response.json()

    assert profile["email"] == "customer@example.com"
    assert profile["full_name"] == "Test Customer"
    assert profile["customer_id"] == registration.json()["customer_id"]

    print("PASS: Customer login and profile retrieval")


def test_incorrect_customer_password_is_rejected(client):
    register_customer(client)

    response = login_customer(
        client,
        password="Incorrect-Password-123!",
    )

    assert response.status_code == 401

    print("PASS: Incorrect customer password rejected")


def test_unknown_customer_email_is_rejected(client):
    response = login_customer(
        client,
        email="unknown@example.com",
    )

    assert response.status_code == 401

    print("PASS: Unknown customer email rejected")


def test_inactive_customer_account_loses_access(client):
    registration = register_customer(client)

    login_response = login_customer(client)

    token = login_response.json()["access_token"]
    customer_id = registration.json()["customer_id"]

    with closing(database.get_connection()) as connection:
        with connection:
            connection.execute(
                "UPDATE customers SET is_active = 0 WHERE id = ?",
                (customer_id,),
            )

    response = client.get(
        "/customers/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401

    print("PASS: Inactive customer account loses access")


def test_shop_owner_token_cannot_access_customer_profile(client):
    owner_token = create_access_token(1)

    response = client.get(
        "/customers/me",
        headers={"Authorization": f"Bearer {owner_token}"},
    )

    assert response.status_code == 401

    print("PASS: Shop-owner token rejected by customer endpoint")


def test_customer_token_cannot_access_shop_owner_profile(client):
    customer_token = create_access_token(
        1,
        principal_type="customer",
    )

    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {customer_token}"},
    )

    assert response.status_code == 401

    print("PASS: Customer token rejected by shop-owner endpoint")


def test_customer_profile_requires_authentication(client):
    response = client.get("/customers/me")

    assert response.status_code == 401

    print("PASS: Customer profile requires authentication")


def test_customer_registration_does_not_expose_password_hash(client):
    response = register_customer(client)

    assert response.status_code == 201

    data = response.json()

    assert "password_hash" not in data
    assert "password" not in data

    print("PASS: Customer credentials are not exposed")


def _customer_auth_headers(client, email):
    login = login_customer(client, email=email)
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _address_payload(**overrides):
    payload = {
        "label": "Home",
        "recipient_name": "Test Customer",
        "phone": "+91 9876543210",
        "address_line1": "12 Market Road",
        "address_line2": "Near Bus Stop",
        "landmark": "Opposite Park",
        "city": "Hyderabad",
        "state": "Telangana",
        "postal_code": "500072",
        "country": "India",
        "is_default": False,
    }
    payload.update(overrides)
    return payload


def test_customer_can_update_profile_without_changing_email(client):
    register_customer(client)
    headers = _customer_auth_headers(client, "customer@example.com")
    response = client.patch(
        "/customers/me",
        headers=headers,
        json={
            "full_name": "Updated Customer",
            "phone": "+91 9876543210",
            "preferred_language": "te-IN",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["full_name"] == "Updated Customer"
    assert data["email"] == "customer@example.com"
    assert data["phone"] == "+91 9876543210"
    assert data["preferred_language"] == "te-IN"
    print("PASS: Customer can update editable profile fields")


def test_customer_profile_patch_requires_at_least_one_field(client):
    register_customer(client)
    headers = _customer_auth_headers(client, "customer@example.com")
    response = client.patch("/customers/me", headers=headers, json={})
    assert response.status_code == 400
    print("PASS: Empty profile update rejected")


def test_customer_can_create_list_update_and_delete_addresses(client):
    register_customer(client)
    headers = _customer_auth_headers(client, "customer@example.com")
    created = client.post(
        "/customers/addresses", headers=headers, json=_address_payload()
    )
    assert created.status_code == 201
    assert created.json()["is_default"] is True

    second = client.post(
        "/customers/addresses",
        headers=headers,
        json=_address_payload(
            label="Work", address_line1="44 Office Road", is_default=False
        ),
    )
    assert second.status_code == 201
    second_id = second.json()["address_id"]
    assert second.json()["is_default"] is False

    listed = client.get("/customers/addresses", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 2

    updated = client.put(
        f"/customers/addresses/{second_id}",
        headers=headers,
        json=_address_payload(
            label="Work", address_line1="45 Updated Office Road", is_default=True
        ),
    )
    assert updated.status_code == 200
    assert updated.json()["address_line1"] == "45 Updated Office Road"
    assert updated.json()["is_default"] is True
    listed = client.get("/customers/addresses", headers=headers).json()
    assert sum(bool(item["is_default"]) for item in listed) == 1

    deleted = client.delete(
        f"/customers/addresses/{second_id}", headers=headers
    )
    assert deleted.status_code == 200
    remaining = client.get("/customers/addresses", headers=headers).json()
    assert len(remaining) == 1
    assert remaining[0]["is_default"] is True
    print("PASS: Customer address CRUD and default-address handling")


def test_first_saved_address_becomes_default_automatically(client):
    register_customer(client)
    headers = _customer_auth_headers(client, "customer@example.com")
    response = client.post(
        "/customers/addresses",
        headers=headers,
        json=_address_payload(is_default=False),
    )
    assert response.status_code == 201
    assert response.json()["is_default"] is True
    print("PASS: First saved address automatically becomes default")


def test_customer_cannot_access_another_customers_address(client):
    register_customer(client, email="first@example.com")
    first_headers = _customer_auth_headers(client, "first@example.com")
    created = client.post(
        "/customers/addresses", headers=first_headers, json=_address_payload()
    )
    address_id = created.json()["address_id"]

    register_customer(client, email="second@example.com")
    second_headers = _customer_auth_headers(client, "second@example.com")
    update_response = client.put(
        f"/customers/addresses/{address_id}",
        headers=second_headers,
        json=_address_payload(label="Hijack"),
    )
    delete_response = client.delete(
        f"/customers/addresses/{address_id}", headers=second_headers
    )
    assert update_response.status_code == 404
    assert delete_response.status_code == 404
    first_list = client.get("/customers/addresses", headers=first_headers)
    assert len(first_list.json()) == 1
    assert first_list.json()[0]["label"] == "Home"
    print("PASS: Customer address ownership is enforced")


def test_customer_address_endpoints_require_authentication(client):
    response = client.get("/customers/addresses")
    assert response.status_code == 401
    print("PASS: Saved addresses require customer authentication")


def test_customer_profile_rejects_null_preferred_language(client):
    register_customer(client)
    headers = _customer_auth_headers(client, "customer@example.com")

    response = client.patch(
        "/customers/me",
        headers=headers,
        json={"preferred_language": None},
    )

    assert response.status_code == 422


def test_editing_default_address_keeps_a_default_selected(client):
    register_customer(client)
    headers = _customer_auth_headers(client, "customer@example.com")

    created = client.post(
        "/customers/addresses",
        headers=headers,
        json=_address_payload(is_default=True),
    )
    assert created.status_code == 201
    address_id = created.json()["address_id"]

    updated = client.put(
        f"/customers/addresses/{address_id}",
        headers=headers,
        json=_address_payload(
            label="Home",
            address_line1="13 Updated Market Road",
            is_default=False,
        ),
    )

    assert updated.status_code == 200
    assert updated.json()["is_default"] is True

    listed = client.get("/customers/addresses", headers=headers).json()
    assert len(listed) == 1
    assert listed[0]["is_default"] is True

def test_customer_profile_rejects_null_full_name(client):
    register_customer(client)
    headers = _customer_auth_headers(client, "customer@example.com")

    response = client.patch(
        "/customers/me",
        headers=headers,
        json={"full_name": None},
    )

    assert response.status_code == 422
