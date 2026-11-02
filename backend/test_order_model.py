
from models.order import GroceryOrder, RequestedItem


def test_general_grocery_order():

    order = GroceryOrder(
        intent="place_order",
        language="te-IN",
        items=[
            RequestedItem(
                raw_name="rice",
                normalized_name="rice",
                quantity=2,
                unit="kg"
            ),
            RequestedItem(
                raw_name="ragi flour",
                normalized_name="ragi flour",
                quantity=1,
                unit="packet"
            ),
            RequestedItem(
                raw_name="Alphonso mangoes",
                normalized_name="mango",
                quantity=3,
                unit="pieces",
                variant="Alphonso"
            ),
            RequestedItem(
                raw_name="organic jaggery",
                normalized_name="jaggery",
                quantity=None,
                unit=None,
                needs_clarification=True,
                clarification_reason="Quantity was not specified."
            )
        ]
    )

    assert order.needs_clarification is True

    print("\nTest 1 PASSED: Clarification detected automatically.")

    print("\n===== VOICEKIRANA ORDER MODEL TEST =====")
    print(order.model_dump_json(indent=4))

    print("\n===== VALIDATION RESULTS =====")

    for item in order.items:
        print(f"\nProduct: {item.raw_name}")
        print(f"Quantity: {item.quantity}")
        print(f"Unit: {item.unit}")
        print(f"Clarification needed: {item.needs_clarification}")

    print("\nAll products were represented successfully!")


def test_complete_order_without_clarification():

    order = GroceryOrder(
        intent="place_order",
        language="en",
        items=[
            RequestedItem(
                raw_name="rice",
                normalized_name="rice",
                quantity=2,
                unit="kg"
            ),
            RequestedItem(
                raw_name="sunflower oil",
                normalized_name="sunflower oil",
                quantity=1,
                unit="litre"
            ),
            RequestedItem(
                raw_name="sugar",
                normalized_name="sugar",
                quantity=500,
                unit="grams"
            )
        ]
    )

    assert order.needs_clarification is False

    print("\nTest 2 PASSED: No clarification needed for complete order.")


def test_order_clarification_after_item_update():
    item = RequestedItem(
        raw_name="jaggery",
        quantity=1,
        unit="packet",
        needs_clarification=False,
    )

    order = GroceryOrder(items=[item])

    assert order.needs_clarification is False

    # Simulate the conversation discovering missing information.
    item.needs_clarification = True

    assert order.refresh_clarification_status() is True
    assert order.needs_clarification is True


if __name__ == "__main__":
    test_general_grocery_order()
    test_complete_order_without_clarification()
    test_order_clarification_after_item_update()

    print("\n===== ALL ORDER MODEL TESTS PASSED =====")
