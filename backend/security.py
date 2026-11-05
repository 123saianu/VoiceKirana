import os
import secrets
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import jwt
from dotenv import load_dotenv
from jwt.exceptions import InvalidTokenError
from pwdlib import PasswordHash


# Load environment settings.
load_dotenv()

JWT_ALGORITHM = "HS256"
JWT_ISSUER = "voicekirana"
ACCESS_TOKEN_EXPIRE_MINUTES = 480  # 8 hours

password_hasher = PasswordHash.recommended()


def get_jwt_secret() -> str:
    """Retrieve the JWT signing secret without exposing it."""
    secret = os.getenv("JWT_SECRET_KEY", "").strip()

    if not secret:
        raise RuntimeError("JWT_SECRET_KEY is not configured.")

    if len(secret) < 32:
        raise RuntimeError("JWT_SECRET_KEY is too short.")

    return secret


def hash_password(password: str) -> str:
    """Generate a secure Argon2 password hash."""
    if not isinstance(password, str) or len(password) < 12:
        raise ValueError("Password must contain at least 12 characters.")

    if len(password.encode("utf-8")) > 1024:
        raise ValueError("Password is too long.")

    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against its stored Argon2 hash."""
    if not isinstance(password, str) or not isinstance(password_hash, str):
        return False

    try:
        return password_hasher.verify(password, password_hash)
    except (ValueError, TypeError):
        return False


@lru_cache(maxsize=1)
def create_dummy_password_hash() -> str:
    """
    Create a dummy hash for login attempts with unknown email addresses.

    This helps reduce timing differences between existing and
    non-existing user accounts.
    """
    return password_hasher.hash(
        "VoiceKirana-Dummy-Authentication-Password"
    )


def create_access_token(
    user_id: int,
    principal_type: str = "shop_owner",
) -> str:
    """Create a signed, time-limited JWT for a shop owner or customer."""
    if not isinstance(user_id, int) or user_id <= 0:
        raise ValueError("A valid user ID is required.")

    if principal_type not in {"shop_owner", "customer"}:
        raise ValueError("Invalid account type.")

    now = datetime.now(timezone.utc)

    payload = {
        "sub": str(user_id),
        "iss": JWT_ISSUER,
        "iat": now,
        "exp": now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        "token_type": "access",
        "principal_type": principal_type,
    }

    return jwt.encode(
        payload,
        get_jwt_secret(),
        algorithm=JWT_ALGORITHM,
    )


def decode_access_token(
    token: str,
    expected_principal_type: str = "shop_owner",
) -> int:
    """
    Verify a JWT and return its user ID.

    Only the explicitly permitted signing algorithm is accepted.
    """
    try:
        payload = jwt.decode(
            token,
            get_jwt_secret(),
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
            options={
                "require": ["sub", "iss", "iat", "exp"],
            },
        )

        if payload.get("token_type") != "access":
            raise InvalidTokenError("Incorrect token type.")

        principal_type = payload.get(
            "principal_type",
            "shop_owner",
        )

        if principal_type != expected_principal_type:
            raise InvalidTokenError("Incorrect account type.")

        user_id = int(payload["sub"])

        if user_id <= 0:
            raise InvalidTokenError("Invalid user ID.")

        return user_id

    except (InvalidTokenError, TypeError, ValueError, KeyError) as exc:
        raise ValueError("Invalid or expired access token.") from exc


def verify_admin_token(provided_token: str | None) -> bool:
    """Verify the separate administrator token."""
    expected_token = os.getenv(
        "VOICEKIRANA_ADMIN_TOKEN",
        "",
    ).strip()

    if not expected_token:
        raise RuntimeError(
            "VOICEKIRANA_ADMIN_TOKEN is not configured."
        )

    if not provided_token:
        return False

    return secrets.compare_digest(
        provided_token,
        expected_token,
    )
