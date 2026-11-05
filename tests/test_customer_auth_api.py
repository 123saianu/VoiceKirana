
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
