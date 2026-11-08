"""Persistent, revocable customer remember-me sessions."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from backend.database import get_connection


REMEMBER_SESSION_DAYS = 30
REMEMBER_SESSION_SECONDS = REMEMBER_SESSION_DAYS * 24 * 60 * 60


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def create_customer_remember_session(customer_id: int) -> dict:
    """Create one random opaque session token and store only its hash."""

    if not isinstance(customer_id, int) or customer_id <= 0:
        raise ValueError("A valid customer ID is required.")

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=REMEMBER_SESSION_DAYS)
    raw_token = secrets.token_urlsafe(48)

    with get_connection() as connection:
        with connection:
            # Remove expired and revoked sessions belonging to this customer.
            connection.execute(
                """
                DELETE FROM customer_remember_sessions
                WHERE customer_id = ?
                  AND (
                    revoked_at IS NOT NULL
                    OR expires_at <= ?
                  )
                """,
                (customer_id, now.isoformat()),
            )

            connection.execute(
                """
                INSERT INTO customer_remember_sessions (
                    customer_id,
                    token_hash,
                    expires_at
                )
                VALUES (?, ?, ?)
                """,
                (
                    customer_id,
                    _token_hash(raw_token),
                    expires_at.isoformat(),
                ),
            )

    return {
        "remember_token": raw_token,
        "expires_in": REMEMBER_SESSION_SECONDS,
    }


def rotate_customer_remember_session(token: str) -> dict | None:
    """Validate, rotate and extend one remember-me session."""

    if not isinstance(token, str) or len(token) < 40:
        return None

    now = datetime.now(timezone.utc)
    old_hash = _token_hash(token)
    new_token = secrets.token_urlsafe(48)
    new_expiry = now + timedelta(days=REMEMBER_SESSION_DAYS)

    with get_connection() as connection:
        with connection:
            row = connection.execute(
                """
                SELECT
                    s.id AS session_id,
                    s.customer_id,
                    s.expires_at,
                    s.revoked_at,
                    c.full_name,
                    c.email,
                    c.is_active
                FROM customer_remember_sessions s
                JOIN customers c ON c.id = s.customer_id
                WHERE s.token_hash = ?
                """,
                (old_hash,),
            ).fetchone()

            if row is None or row["revoked_at"] is not None:
                return None

            if not row["is_active"]:
                connection.execute(
                    """
                    UPDATE customer_remember_sessions
                    SET revoked_at = ?
                    WHERE id = ? AND revoked_at IS NULL
                    """,
                    (now.isoformat(), row["session_id"]),
                )
                return None

            try:
                old_expiry = _parse_utc(row["expires_at"])
            except (TypeError, ValueError):
                old_expiry = now - timedelta(seconds=1)

            if old_expiry <= now:
                connection.execute(
                    """
                    UPDATE customer_remember_sessions
                    SET revoked_at = ?
                    WHERE id = ? AND revoked_at IS NULL
                    """,
                    (now.isoformat(), row["session_id"]),
                )
                return None

            cursor = connection.execute(
                """
                UPDATE customer_remember_sessions
                SET token_hash = ?,
                    expires_at = ?,
                    last_used_at = ?
                WHERE id = ?
                  AND token_hash = ?
                  AND revoked_at IS NULL
                """,
                (
                    _token_hash(new_token),
                    new_expiry.isoformat(),
                    now.isoformat(),
                    row["session_id"],
                    old_hash,
                ),
            )

            if cursor.rowcount != 1:
                return None

            customer = {
                "customer_id": row["customer_id"],
                "full_name": row["full_name"],
                "email": row["email"],
            }

    return {
        **customer,
        "remember_token": new_token,
        "expires_in": REMEMBER_SESSION_SECONDS,
    }


def revoke_customer_remember_session(token: str) -> None:
    """Revoke the session associated with a presented token."""

    if not isinstance(token, str) or len(token) < 40:
        return

    with get_connection() as connection:
        with connection:
            connection.execute(
                """
                UPDATE customer_remember_sessions
                SET revoked_at = ?
                WHERE token_hash = ?
                  AND revoked_at IS NULL
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    _token_hash(token),
                ),
            )
