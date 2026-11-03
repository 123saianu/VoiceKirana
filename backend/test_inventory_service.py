import backend.database as database
import backend.main as main
from fastapi.testclient import TestClient

from backend.database import initialize_database
from backend.services.inventory_service import (
    add_product_alias,
    create_product,
    create_shop,
    find_inventory,
    set_inventory,
)


def test_inventory_service_smoke(tmp_path, monkeypatch):
    # Use a fresh, isolated database for this test.
    monkeypatch.setattr(database, "DATABASE_URL", "")
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(
        database,
        "DATABASE_PATH",
        tmp_path / "voicekirana_test.db",
    )

    initialize_database()

    # The test database is empty, so no existing rows need deleting.
    shop_id = create_shop("Smoke Test Shop")
    product_id = create_product("Rice", "kg")

    set_inventory(
        shop_id=shop_id,
        product_id=product_id,
        price_paise=6500,
        stock_quantity=20,
    )
    add_product_alias(product_id, "biyyam")

    direct_match = find_inventory(shop_id, "Rice")
    alias_match = find_inventory(shop_id, "biyyam")

    assert len(direct_match) == 1
    assert direct_match[0]["name"] == "Rice"
    assert direct_match[0]["stock_quantity"] == 20

    assert len(alias_match) == 1
    assert alias_match[0]["name"] == "Rice"
    assert alias_match[0]["price_per_unit"] == 6500

    # Matching should ignore capitalization.
    case_match = find_inventory(shop_id, "rIcE")
    assert len(case_match) == 1
    assert case_match[0]["name"] == "Rice"

    # Unknown products must not be silently matched to Rice.
    unknown_match = find_inventory(shop_id, "unfamiliar grain")
    assert unknown_match == []


def test_inventory_returns_multiple_matches_for_shared_alias(
    tmp_path, monkeypatch
):
    # Use a fresh, isolated database.
    monkeypatch.setattr(database, "DATABASE_URL", "")
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(
        database,
        "DATABASE_PATH",
        tmp_path / "voicekirana_ambiguous_test.db",
    )

    initialize_database()

    shop_id = create_shop("Ambiguous Match Test Shop")

    rice_id = create_product("Rice", "kg")
    brown_rice_id = create_product("Brown Rice", "kg")

    set_inventory(shop_id, rice_id, 6500, 20)
    set_inventory(shop_id, brown_rice_id, 8000, 15)

    # Both products share the same alternate name.
    add_product_alias(rice_id, "rice variety")
    add_product_alias(brown_rice_id, "rice variety")

    matches = find_inventory(shop_id, "rice variety")

    assert len(matches) == 2
    assert {match["name"] for match in matches} == {
        "Rice",
        "Brown Rice",
    }


def test_inventory_api_reports_ambiguous_match(tmp_path, monkeypatch):
    # Isolate the database for this API test.
    monkeypatch.setattr(database, "DATABASE_URL", "")
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(
        database,
        "DATABASE_PATH",
        tmp_path / "voicekirana_api_ambiguous_test.db",
    )

    initialize_database()

    # Configure test authentication without exposing the real API key.
    test_token = "test-token-for-local-tests"
    monkeypatch.setattr(main, "VOICEKIRANA_API_TOKEN", test_token)

    shop_id = create_shop("API Ambiguity Test Shop")

    rice_id = create_product("Rice", "kg")
    brown_rice_id = create_product("Brown Rice", "kg")

    set_inventory(shop_id, rice_id, 6500, 20)
    set_inventory(shop_id, brown_rice_id, 8000, 15)

    add_product_alias(rice_id, "rice variety")
    add_product_alias(brown_rice_id, "rice variety")

    client = TestClient(main.app)

    response = client.post(
        f"/inventory/check?shop_id={shop_id}",
        headers={"X-API-Key": test_token},
        json={
            "intent": "place_order",
            "items": [
                {
                    "raw_name": "rice variety",
                    "normalized_name": "rice variety",
                    "quantity": 1,
                    "unit": "kg",
                }
            ],
        },
    )

    assert response.status_code == 200

    result = response.json()
    assert result["status"] == "needs_attention"
    assert result["needs_clarification"] is True
    assert result["total_paise"] is None

    item_result = result["items"][0]
    assert item_result["status"] == "ambiguous_match"

    candidate_names = {
        candidate["name"]
        for candidate in item_result["candidates"]
    }
    assert candidate_names == {"Rice", "Brown Rice"}
