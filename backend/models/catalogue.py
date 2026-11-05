from pydantic import BaseModel, Field, field_validator, model_validator


class ShopProductCreate(BaseModel):
    """Register a product and its shop-specific inventory details."""

    name: str = Field(min_length=1, max_length=150)
    base_unit: str = Field(min_length=1, max_length=30)

    brand: str | None = Field(default=None, max_length=100)
    variant: str | None = Field(default=None, max_length=100)

    price_paise: int = Field(ge=0)
    stock_quantity: float = Field(ge=0)
    is_available: bool = True

    aliases: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("name", "base_unit")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("This field cannot be empty.")

        return value

    @field_validator("base_unit")
    @classmethod
    def normalize_unit(cls, value: str) -> str:
        return value.casefold()

    @field_validator("brand", "variant")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None

        value = value.strip()
        return value or None

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, aliases: list[str]) -> list[str]:
        cleaned = []
        seen = set()

        for alias in aliases:
            alias = alias.strip()

            if not alias:
                raise ValueError("Product aliases cannot be empty.")

            normalized = alias.casefold()

            if normalized not in seen:
                cleaned.append(alias)
                seen.add(normalized)

        return cleaned


class InventoryUpdate(BaseModel):
    """Allow selected shop inventory fields to be updated."""

    price_paise: int | None = Field(default=None, ge=0)
    stock_quantity: float | None = Field(default=None, ge=0)
    is_available: bool | None = None

    @model_validator(mode="after")
    def require_at_least_one_update(self):
        if (
            self.price_paise is None
            and self.stock_quantity is None
            and self.is_available is None
        ):
            raise ValueError("Provide at least one inventory field to update.")

        return self


class ProductAliasCreate(BaseModel):
    """Register an alternate or regional product name."""

    alias: str = Field(min_length=1, max_length=150)

    @field_validator("alias")
    @classmethod
    def normalize_alias(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("Product alias cannot be empty.")

        return value
