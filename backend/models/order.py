
from typing import Optional, Literal

from pydantic import BaseModel, Field, model_validator


class RequestedItem(BaseModel):
    """
    Represents one product requested by the customer.

    Product names are open-ended. They are NOT restricted
    to products stored in our shop catalogue.
    """

    raw_name: str = Field(
        description="The product name exactly as understood from the customer's request."
    )

    normalized_name: Optional[str] = Field(
        default=None,
        description="A standardised product name, if it can be identified."
    )

    quantity: Optional[float] = Field(
        default=None,
        gt=0,
        description="Requested quantity. Leave empty if not specified or unclear."
    )

    unit: Optional[str] = Field(
        default=None,
        description="Unit such as kg, gram, litre, ml, packet, piece, dozen or bundle."
    )

    brand: Optional[str] = None

    variant: Optional[str] = Field(
        default=None,
        description="Product variant, type or specification, if mentioned."
    )

    needs_clarification: bool = False

    clarification_reason: Optional[str] = None


class GroceryOrder(BaseModel):
    """
    Represents a customer's grocery order.
    Supports different languages and multiple products.
    """

    intent: Literal[
        "place_order",
        "modify_order",
        "cancel_order",
        "ask_question",
        "unknown"
    ] = "unknown"

    language: Optional[str] = None

    items: list[RequestedItem] = Field(default_factory=list)

    additional_instructions: Optional[str] = None

    needs_clarification: bool = False

    @model_validator(mode="after")
    def derive_clarification_status(self):
        """
        Automatically flag the overall order if any item
        requires clarification.
        """
        self.needs_clarification = (
            self.needs_clarification
            or any(item.needs_clarification for item in self.items)
        )

        return self

    def refresh_clarification_status(self) -> bool:
        """
        Recalculate the order flag from the current item states.
        """
        self.needs_clarification = any(
            item.needs_clarification for item in self.items
        )
        return self.needs_clarification
