
import re

from pydantic import BaseModel, Field, field_validator


class CustomerRegisterRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=150)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("full_name")
    @classmethod
    def normalize_full_name(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("Full name cannot be empty.")

        return value

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().casefold()

        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Please provide a valid email address.")

        return value


class CustomerProfileResponse(BaseModel):
    customer_id: int
    full_name: str
    email: str
    phone: str | None = None
    preferred_language: str = "en-IN"


class CustomerProfileUpdateRequest(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=150)
    phone: str | None = Field(default=None, max_length=20)
    preferred_language: str | None = Field(default=None, min_length=2, max_length=15)

    @field_validator("full_name")
    @classmethod
    def normalize_profile_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Full name cannot be empty.")
        return value

    @field_validator("phone")
    @classmethod
    def normalize_profile_phone(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        digits = re.sub(r"\D", "", value)
        if not 7 <= len(digits) <= 15:
            raise ValueError("Phone number must contain 7 to 15 digits.")
        if not re.fullmatch(r"[+0-9() .-]+", value):
            raise ValueError("Phone number contains unsupported characters.")
        return value

    @field_validator("preferred_language")
    @classmethod
    def normalize_preferred_language(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("Preferred language cannot be null.")
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?", value):
            raise ValueError("Use a language code such as en-IN or te-IN.")
        return value


class CustomerAddressRequest(BaseModel):
    label: str = Field(default="Home", min_length=1, max_length=40)
    recipient_name: str = Field(min_length=1, max_length=150)
    phone: str = Field(min_length=7, max_length=20)
    address_line1: str = Field(min_length=1, max_length=200)
    address_line2: str | None = Field(default=None, max_length=200)
    landmark: str | None = Field(default=None, max_length=150)
    city: str = Field(min_length=1, max_length=100)
    state: str = Field(min_length=1, max_length=100)
    postal_code: str = Field(min_length=3, max_length=20)
    country: str = Field(default="India", min_length=2, max_length=80)
    is_default: bool = False

    @field_validator(
        "label", "recipient_name", "address_line1",
        "city", "state", "postal_code", "country",
    )
    @classmethod
    def normalize_required_address_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This address field cannot be empty.")
        return value

    @field_validator("address_line2", "landmark")
    @classmethod
    def normalize_optional_address_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("phone")
    @classmethod
    def validate_address_phone(cls, value: str) -> str:
        value = value.strip()
        digits = re.sub(r"\D", "", value)
        if not 7 <= len(digits) <= 15:
            raise ValueError("Phone number must contain 7 to 15 digits.")
        if not re.fullmatch(r"[+0-9() .-]+", value):
            raise ValueError("Phone number contains unsupported characters.")
        return value


class CustomerAddressResponse(BaseModel):
    address_id: int
    label: str
    recipient_name: str
    phone: str
    address_line1: str
    address_line2: str | None = None
    landmark: str | None = None
    city: str
    state: str
    postal_code: str
    country: str
    is_default: bool
    created_at: str


from backend.models.auth import LoginRequest


class CustomerLoginRequest(LoginRequest):
    """Customer login with optional persistent sign-in."""
    remember_me: bool = True


class CustomerRememberRequest(BaseModel):
    """Opaque persistent-session token supplied by the browser."""

    remember_token: str = Field(min_length=40, max_length=256)


class CustomerLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    remember_token: str | None = None
    remember_expires_in: int | None = None
    customer: CustomerProfileResponse | None = None
