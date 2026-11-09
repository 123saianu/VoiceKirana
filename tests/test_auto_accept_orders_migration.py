import sqlite3

from backend import database


def test_auto_accept_migration_defaults_to_manual_and_preserves_shop_setting(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "legacy_auto_accept_shops.db"

    monkeypatch.setattr(database, "DATABASE_URL", "")
    monkeypatch.setattr(database, "DATABASE_PATH", db_path)

    # Simulate a database created before auto_accept_orders existed.
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE shops (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                is_customer_visible INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            "INSERT INTO shops (name) VALUES (?)",
            ("Legacy Shop",),
        )

    database._migrate_shop_auto_accept_schema()

    with database.get_connection() as connection:
        legacy = connection.execute(
            "SELECT auto_accept_orders FROM shops WHERE id = 1"
        ).fetchone()

    assert legacy["auto_accept_orders"] == 0

    # An explicit shop setting must survive subsequent migration runs.
    with database.get_connection() as connection:
        with connection:
            connection.execute(
                "UPDATE shops SET auto_accept_orders = 1 WHERE id = 1"
            )

    database._migrate_shop_auto_accept_schema()

    with database.get_connection() as connection:
        existing = connection.execute(
            "SELECT auto_accept_orders FROM shops WHERE id = 1"
        ).fetchone()
        connection.execute(
            "INSERT INTO shops (name) VALUES (?)",
            ("New Shop",),
        )
        new_shop = connection.execute(
            "SELECT auto_accept_orders FROM shops WHERE name = ?",
            ("New Shop",),
        ).fetchone()

    assert existing["auto_accept_orders"] == 1
    assert new_shop["auto_accept_orders"] == 0
