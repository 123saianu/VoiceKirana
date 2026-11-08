
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.database import get_connection
from backend.security import decode_access_token, verify_admin_token


# Allow endpoints to receive Bearer tokens.
# auto_error=False lets us return our own consistent 401 response.
bearer_scheme = HTTPBearer(auto_error=False)


def authenticate_admin_token(admin_token: str | None) -> None:
    """Allow only requests carrying the configured admin token."""

    try:
        is_valid = verify_admin_token(admin_token)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail="Administrator authentication is not configured.",
        ) from exc

    if not is_valid:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing administrator authentication.",
        )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(
        bearer_scheme
    ),
) -> dict:
    """
    Validate a shop-owner JWT and retrieve the user's current
    account and active shop memberships from PostgreSQL.

    Database checks on every request ensure that deactivated
    accounts and memberships immediately lose access.
    """

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Missing Bearer authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id = decode_access_token(credentials.credentials)
    except ValueError as exc:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired access token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    with get_connection() as connection:
        user = connection.execute(
            """
            SELECT id, email, full_name, is_active
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()

        if user is None or not user["is_active"]:
            raise HTTPException(
                status_code=401,
                detail="User account is inactive or no longer exists.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        memberships = connection.execute(
            """
            SELECT
                sm.shop_id,
                s.name AS shop_name,
                sm.role
            FROM shop_memberships sm
            JOIN shops s ON s.id = sm.shop_id
            WHERE sm.user_id = ?
              AND sm.is_active = 1
              AND s.is_active = 1
            ORDER BY sm.shop_id
            """,
            (user_id,),
        ).fetchall()

    return {
        "user_id": user["id"],
        "email": user["email"],
        "full_name": user["full_name"],
        "shops": [
            {
                "shop_id": membership["shop_id"],
                "shop_name": membership["shop_name"],
                "role": membership["role"],
            }
            for membership in memberships
        ],
    }


def get_current_customer(
    credentials: HTTPAuthorizationCredentials | None = Depends(
        bearer_scheme
    ),
) -> dict:
    """Validate a customer JWT and retrieve the active customer."""

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Missing Bearer authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        customer_id = decode_access_token(
            credentials.credentials,
            expected_principal_type="customer",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired customer access token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    with get_connection() as connection:
        customer = connection.execute(
            """
            SELECT
                id, email, full_name, phone, preferred_language, is_active
            FROM customers
            WHERE id = ?
            """,
            (customer_id,),
        ).fetchone()

    if customer is None or not customer["is_active"]:
        raise HTTPException(
            status_code=401,
            detail="Customer account is inactive or no longer exists.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return {
        "customer_id": customer["id"],
        "email": customer["email"],
        "full_name": customer["full_name"],
        "phone": customer["phone"],
        "preferred_language": customer["preferred_language"] or "en-IN",
    }


def authorize_shop_access(
    current_user: dict,
    shop_id: int,
    allowed_roles: set[str] | None = None,
) -> dict:
    """
    Verify that a user belongs to the requested shop.

    Optionally restrict access to particular membership roles.
    """

    membership = next(
        (
            shop
            for shop in current_user["shops"]
            if shop["shop_id"] == shop_id
        ),
        None,
    )

    if membership is None:
        raise HTTPException(
            status_code=403,
            detail="You do not have access to this shop.",
        )

    if allowed_roles and membership["role"] not in allowed_roles:
        raise HTTPException(
            status_code=403,
            detail="Your role does not permit this operation.",
        )

    return membership


def require_shop_access(*allowed_roles: str):
    """
    Create a FastAPI dependency that checks shop membership.

    The endpoint must have a path or query parameter named shop_id.

    Example:
        Depends(require_shop_access("owner", "manager"))
    """

    permitted_roles = set(allowed_roles) if allowed_roles else None

    def shop_access_dependency(
        shop_id: int,
        current_user: dict = Depends(get_current_user),
    ):
        membership = authorize_shop_access(
            current_user=current_user,
            shop_id=shop_id,
            allowed_roles=permitted_roles,
        )

        return {
            **current_user,
            "active_shop": membership,
        }

    return shop_access_dependency
