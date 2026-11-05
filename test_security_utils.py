from backend.security import (
    hash_password,
    verify_password,
    create_access_token,
    decode_access_token,
    verify_admin_token,
)


def main():
    # Test password hashing.
    password = "VoiceKirana-Test-Password-123!"

    hashed = hash_password(password)

    assert hashed != password
    assert verify_password(password, hashed)
    assert not verify_password("Wrong-Password-123!", hashed)

    print("PASS: Argon2 password hashing")

    # Test JWT creation and verification.
    token = create_access_token(123)

    assert isinstance(token, str)
    assert decode_access_token(token) == 123

    try:
        decode_access_token(token + "invalid")
    except ValueError:
        pass
    else:
        raise AssertionError("Invalid JWT was accepted.")

    print("PASS: JWT creation and verification")
    print("PASS: Invalid JWT rejected")

    # Test administrator token.
    assert verify_admin_token(None) is False

    configured_admin_token = __import__("os").getenv(
        "VOICEKIRANA_ADMIN_TOKEN"
    )

    assert verify_admin_token(configured_admin_token)
    assert verify_admin_token("incorrect-token") is False

    print("PASS: Administrator token verification")
    print("\nAll security utility tests passed!")


if __name__ == "__main__":
    main()
