
from pydantic import BaseModel, Field


class RazorpayPaymentVerificationRequest(BaseModel):
    razorpay_payment_id: str = Field(
        min_length=1,
        max_length=100,
    )

    razorpay_order_id: str = Field(
        min_length=1,
        max_length=100,
    )

    razorpay_signature: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
    )
