import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend import database
from backend.main import app


JWT_SECRET = "test-jwt-secret-for-voicekirana-at-least-32-characters"
CUSTOMER_PASSWORD = "VoiceKirana-Customer-Password-123!"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATABASE_URL", "")
    monkeypatch.setattr(
        database,
        "DATABASE_PATH",
        tmp_path / "voicekirana_remember_me_test.db",
    )
    monkeypatch.setenv("JWT_SECRET_KEY", JWT_SECRET)

    database.initialize_database()

    with TestClient(app) as test_client:
        yield test_client


def register_and_login(client, remember_me=True):
    registration = client.post(
        "/customers/register",
        json={
            "full_name": "Remember Me Customer",
            "email": "remember@example.com",
            "password": CUSTOMER_PASSWORD,
        },
    )
    assert registration.status_code == 201

    login = client.post(
        "/customers/login",
        json={
            "email": "remember@example.com",
            "password": CUSTOMER_PASSWORD,
            "remember_me": remember_me,
        },
    )
    assert login.status_code == 200
    return login.json()


def test_remember_me_restores_and_rotates_customer_session(client):
    original = register_and_login(client, remember_me=True)
    old_remember_token = original["remember_token"]

    assert original["expires_in"] == 8 * 60 * 60
    assert original["remember_expires_in"] == 30 * 24 * 60 * 60

    restored = client.post(
        "/customers/session/restore",
        json={"remember_token": old_remember_token},
    )

    assert restored.status_code == 200
    body = restored.json()
    assert body["customer"]["email"] == "remember@example.com"
    assert body["remember_token"] != old_remember_token

    profile = client.get(
        "/customers/me",
        headers={
            "Authorization": f"Bearer {body['access_token']}"
        },
    )
    assert profile.status_code == 200
    assert profile.json()["email"] == "remember@example.com"

    old_token_reuse = client.post(
        "/customers/session/restore",
        json={"remember_token": old_remember_token},
    )
    assert old_token_reuse.status_code == 401


def test_customer_can_revoke_remember_me_session(client):
    login = register_and_login(client, remember_me=True)
    token = login["remember_token"]

    logout = client.post(
        "/customers/session/logout",
        json={"remember_token": token},
    )
    assert logout.status_code == 200
    assert logout.json()["status"] == "logged_out"

    restore = client.post(
        "/customers/session/restore",
        json={"remember_token": token},
    )
    assert restore.status_code == 401


def test_login_without_remember_me_does_not_create_persistent_token(client):
    login = register_and_login(client, remember_me=False)

    assert "remember_token" not in login
    assert login["token_type"] == "bearer"


def test_expired_remember_me_session_is_rejected(client):
    login = register_and_login(client, remember_me=True)
    token_hash = hashlib.sha256(
        login["remember_token"].encode("utf-8")
    ).hexdigest()

    expired = (
        datetime.now(timezone.utc) - timedelta(days=1)
    ).isoformat()

    with database.get_connection() as connection:
        connection.execute(
            """
            UPDATE customer_remember_sessions
            SET expires_at = ?
            WHERE token_hash = ?
            """,
            (expired, token_hash),
        )

    response = client.post(
        "/customers/session/restore",
        json={"remember_token": login["remember_token"]},
    )

    assert response.status_code == 401


def test_deactivated_customer_cannot_restore_remember_me_session(client):
    login = register_and_login(client, remember_me=True)

    with database.get_connection() as connection:
        connection.execute(
            "UPDATE customers SET is_active = 0 WHERE email = ?",
            ("remember@example.com",),
        )

    response = client.post(
        "/customers/session/restore",
        json={"remember_token": login["remember_token"]},
    )

    assert response.status_code == 401
