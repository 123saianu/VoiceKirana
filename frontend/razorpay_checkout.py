
import streamlit as st


_CHECKOUT_HTML = """
<div class="vk-checkout">
    <button id="vk-open-checkout" type="button">
        Continue to secure payment
    </button>
    <p id="vk-checkout-message" role="status" aria-live="polite"></p>
</div>
"""


_CHECKOUT_CSS = """
.vk-checkout {
    display: flex;
    flex-direction: column;
    gap: 8px;
    width: 100%;
}

#vk-open-checkout {
    width: 100%;
    padding: 0.7rem 1rem;
    border: none;
    border-radius: 8px;
    background: var(--st-primary-color);
    color: white;
    font-weight: 600;
    cursor: pointer;
}

#vk-open-checkout:disabled {
    opacity: 0.6;
    cursor: not-allowed;
}

#vk-checkout-message {
    color: var(--st-text-color);
    font-size: 0.85rem;
    margin: 0;
}
"""


_CHECKOUT_JS = """
export default function(component) {
    const { data, setTriggerValue, parentElement } = component;

    const button = parentElement.querySelector("#vk-open-checkout");
    const message = parentElement.querySelector("#vk-checkout-message");

    if (!button || !message) {
        return;
    }

    function setMessage(value) {
        message.textContent = value;
    }

    function ensureRazorpayLoaded() {
        if (window.Razorpay) {
            return Promise.resolve();
        }

        if (window.__voiceKiranaRazorpayPromise) {
            return window.__voiceKiranaRazorpayPromise;
        }

        window.__voiceKiranaRazorpayPromise = new Promise(
            function(resolve, reject) {
                const script = document.createElement("script");

                script.src = "https://checkout.razorpay.com/v1/checkout.js";
                script.async = true;

                script.onload = function() {
                    if (window.Razorpay) {
                        resolve();
                    } else {
                        window.__voiceKiranaRazorpayPromise = null;
                        reject(new Error("Razorpay Checkout did not initialize."));
                    }
                };

                script.onerror = function() {
                    window.__voiceKiranaRazorpayPromise = null;
                    reject(new Error("Could not load Razorpay Checkout."));
                };

                document.head.appendChild(script);
            }
        );

        return window.__voiceKiranaRazorpayPromise;
    }

    function safeText(value, limit) {
        return typeof value === "string" ? value.slice(0, limit) : "";
    }

    button.onclick = async function() {
        const checkoutData = component.data || {};

        if (
            !checkoutData.key_id ||
            !checkoutData.razorpay_order_id ||
            !checkoutData.amount_paise ||
            !checkoutData.payment_id
        ) {
            setMessage("Payment details are incomplete. Refresh your order.");
            setTriggerValue("checkout_error", {
                message: "Payment details are incomplete."
            });
            return;
        }

        button.disabled = true;
        setMessage("Connecting to Razorpay...");

        try {
            await ensureRazorpayLoaded();

            const options = {
                key: checkoutData.key_id,
                amount: checkoutData.amount_paise,
                currency: checkoutData.currency || "INR",
                name: "VoiceKirana",
                description: "Grocery order " + checkoutData.order_reference,
                order_id: checkoutData.razorpay_order_id,

                prefill: {
                    name: safeText(checkoutData.customer_name, 150),
                    email: safeText(checkoutData.customer_email, 254),
                    contact: safeText(checkoutData.customer_phone, 20)
                },

                theme: {
                    color: "#16A34A"
                },

                handler: function(response) {
                    button.disabled = true;
                    setMessage("Payment response received. Verifying securely...");

                    setTriggerValue("payment_success", {
                        payment_id: checkoutData.payment_id,
                        razorpay_payment_id: response.razorpay_payment_id,
                        razorpay_order_id: response.razorpay_order_id,
                        razorpay_signature: response.razorpay_signature
                    });
                },

                modal: {
                    ondismiss: function() {
                        button.disabled = false;
                        setMessage("Checkout closed. No payment confirmation received.");

                        setTriggerValue("payment_dismissed", {
                            payment_id: checkoutData.payment_id
                        });
                    }
                }
            };

            const checkout = new window.Razorpay(options);

            checkout.on("payment.failed", function(response) {
                const error = response && response.error
                    ? response.error
                    : {};

                const metadata = error.metadata || {};

                button.disabled = false;
                setMessage(
                    safeText(error.description, 250) ||
                    "Payment was unsuccessful. Refresh your order status."
                );

                setTriggerValue("payment_failed", {
                    payment_id: checkoutData.payment_id,
                    code: safeText(error.code, 100),
                    description: safeText(error.description, 250),
                    reason: safeText(error.reason, 150),
                    razorpay_order_id: safeText(metadata.order_id, 100)
                });
            });

            checkout.open();

        } catch (error) {
            button.disabled = false;
            setMessage("Could not open Razorpay Checkout. Please try again.");

            setTriggerValue("checkout_error", {
                message: safeText(error.message, 250)
            });
        }
    };
}
"""


_CHECKOUT_COMPONENT = st.components.v2.component(
    name="voicekirana_razorpay_checkout",
    html=_CHECKOUT_HTML,
    css=_CHECKOUT_CSS,
    js=_CHECKOUT_JS,
)


def render_razorpay_checkout(checkout_data: dict, component_key: str):
    """Render Razorpay Checkout and return its browser events to Python."""

    return _CHECKOUT_COMPONENT(
        key=component_key,
        data=checkout_data,
        on_payment_success_change=lambda: None,
        on_payment_failed_change=lambda: None,
        on_payment_dismissed_change=lambda: None,
        on_checkout_error_change=lambda: None,
    )
