from backend.auth_dependencies import (
    authenticate_admin_token,
    get_current_user,
)

from backend.security import create_access_token


def test_admin_token_rejection():
    try:
        authenticate_admin_token("incorrect-token")
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 401
    else:
        raise AssertionError("Invalid admin token was accepted.")

    print("PASS: Invalid administrator token rejected")


def test_jwt_creation():
    token = create_access_token(123)

    assert isinstance(token, str)
    print("PASS: JWT generated for a valid user ID")


if __name__ == "__main__":
    test_admin_token_rejection()
    test_jwt_creation()
    print("\nAuthentication dependency tests passed!")
