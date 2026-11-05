from backend.models.auth import (
    LoginRequest,
    AdminCreateShopRequest,
    AdminCreateShopOwnerRequest,
)


def main():
    login = LoginRequest(
        email="  OWNER@EXAMPLE.COM  ",
        password="correct-password",
    )

    assert login.email == "owner@example.com"
    print("PASS: Login email normalization")

    shop = AdminCreateShopRequest(
        name="  Sai Kirana Store  ",
    )

    assert shop.name == "Sai Kirana Store"
    print("PASS: Shop name validation")

    owner = AdminCreateShopOwnerRequest(
        email="OWNER@EXAMPLE.COM",
        full_name="Sai Anudeep",
        password="VoiceKirana-Test-Password-123!",
        shop_id=6,
        role="owner",
    )

    assert owner.email == "owner@example.com"
    assert owner.shop_id == 6
    assert owner.role == "owner"
    print("PASS: Shop-owner registration validation")

    print("\nAll authentication model tests passed!")


if __name__ == "__main__":
    main()
