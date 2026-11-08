
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
