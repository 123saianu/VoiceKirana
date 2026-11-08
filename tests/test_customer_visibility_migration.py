import sqlite3

from backend import database


def test_customer_visibility_migration_preserves_existing_shops_as_hidden(
    tmp_path,
    monkeypatch,
):
    """Old shops default to hidden, and rerunning migration is safe."""

    db_path = tmp_path / "legacy_shops.db"

    monkeypatch.setattr(database, "DATABASE_URL", "")
    monkeypatch.setattr(database, "DATABASE_PATH", db_path)

    # Simulate the old PostgreSQL/SQLite shops schema.
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE shops (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            "INSERT INTO shops (name, is_active) VALUES (?, ?)",
            ("Existing Active Shop", 1),
        )
        connection.execute(
            "INSERT INTO shops (name, is_active) VALUES (?, ?)",
            ("Existing Inactive Shop", 0),
        )

    # First migration adds the visibility column.
    database._migrate_customer_visibility_schema()

    with database.get_connection() as connection:
        rows = connection.execute(
            """
            SELECT name, is_active, is_customer_visible
            FROM shops
            ORDER BY id
            """
        ).fetchall()

    assert len(rows) == 2
    assert all(row["is_customer_visible"] == 0 for row in rows)

    # Simulate an administrator approving the first shop.
    with database.get_connection() as connection:
        with connection:
            connection.execute(
                """
                UPDATE shops
                SET is_customer_visible = 1
                WHERE name = ?
                """,
                ("Existing Active Shop",),
            )

    # A second migration must not reset an approved shop.
    database._migrate_customer_visibility_schema()

    with database.get_connection() as connection:
        approved = connection.execute(
            """
            SELECT is_customer_visible
            FROM shops
            WHERE name = ?
            """,
            ("Existing Active Shop",),
        ).fetchone()

        # Newly added rows must also default to hidden.
        connection.execute(
            "INSERT INTO shops (name) VALUES (?)",
            ("New Shop",),
        )

        new_shop = connection.execute(
            """
            SELECT is_customer_visible
            FROM shops
            WHERE name = ?
            """,
            ("New Shop",),
        ).fetchone()

    assert approved["is_customer_visible"] == 1
    assert new_shop["is_customer_visible"] == 0
