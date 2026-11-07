import sqlite3

import pytest

import backend.database as database


@pytest.fixture
def legacy_database(monkeypatch, tmp_path):
    """Create an old-schema database with existing related records."""
    db_path = tmp_path / "legacy_orders.db"

    connection = sqlite3.connect(db_path)

    try:
        connection.execute("PRAGMA foreign_keys = ON")

        connection.executescript("""
            CREATE TABLE shops (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            );

            CREATE TABLE customers (
                id INTEGER PRIMARY KEY,
                email TEXT NOT NULL
            );

            CREATE TABLE products (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            );

            CREATE TABLE orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_reference TEXT NOT NULL UNIQUE,
                shop_id INTEGER NOT NULL,
                status TEXT NOT NULL
                    CHECK (status IN (
                        'pending',
                        'confirmed',
                        'cancelled'
                    )),
                total_amount INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (shop_id) REFERENCES shops(id)
            );

            CREATE TABLE order_items (
                id INTEGER PRIMARY KEY,
                order_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                quantity REAL NOT NULL,
                unit TEXT NOT NULL,
                unit_price INTEGER NOT NULL,
                line_total INTEGER NOT NULL,
                FOREIGN KEY (order_id) REFERENCES orders(id),
                FOREIGN KEY (product_id) REFERENCES products(id)
            );

            CREATE TABLE order_idempotency (
                shop_id INTEGER NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_fingerprint TEXT NOT NULL,
                order_id INTEGER,
                response_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (shop_id, idempotency_key),
                FOREIGN KEY (shop_id) REFERENCES shops(id),
                FOREIGN KEY (order_id) REFERENCES orders(id)
            );

            CREATE TABLE customer_order_ownership (
                order_id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL,
                FOREIGN KEY (order_id) REFERENCES orders(id),
                FOREIGN KEY (customer_id) REFERENCES customers(id)
            );

            CREATE TABLE payments (
                id INTEGER PRIMARY KEY,
                order_id INTEGER NOT NULL,
                customer_id INTEGER NOT NULL,
                FOREIGN KEY (order_id) REFERENCES orders(id),
                FOREIGN KEY (customer_id) REFERENCES customers(id)
            );
        """)

        connection.execute(
            "INSERT INTO shops (id, name) VALUES (1, 'Migration Test Shop')"
        )
        connection.execute(
            "INSERT INTO customers (id, email) VALUES (1, 'test@example.com')"
        )
        connection.execute(
            "INSERT INTO products (id, name) VALUES (1, 'Rice')"
        )

        # Create a deleted high ID to test AUTOINCREMENT preservation.
        connection.execute("""
            INSERT INTO orders (
                id, order_reference, shop_id, status, total_amount
            )
            VALUES (30, 'VK-DELETED-HIGH', 1, 'pending', 0)
        """)
        connection.execute(
            "DELETE FROM orders WHERE id = 30"
        )

        connection.execute("""
            INSERT INTO orders (
                id, order_reference, shop_id, status, total_amount
            )
            VALUES (7, 'VK-LEGACY-ORDER', 1, 'pending', 13000)
        """)

        connection.execute("""
            INSERT INTO order_items (
                order_id, product_id, quantity, unit,
                unit_price, line_total
            )
            VALUES (7, 1, 2, 'kg', 6500, 13000)
        """)

        connection.execute("""
            INSERT INTO order_idempotency (
                shop_id, idempotency_key, request_fingerprint,
                order_id, response_json
            )
            VALUES (1, 'legacy-key', 'legacy-fingerprint', 7, '{}')
        """)

        connection.execute("""
            INSERT INTO customer_order_ownership (
                order_id, customer_id
            )
            VALUES (7, 1)
        """)

        connection.execute("""
            INSERT INTO payments (id, order_id, customer_id)
            VALUES (1, 7, 1)
        """)

        connection.commit()

    finally:
        connection.close()

    monkeypatch.setattr(database, "DATABASE_URL", "")
    monkeypatch.setattr(database, "DATABASE_PATH", db_path)

    return db_path


def test_sqlite_migration_preserves_existing_order_data(legacy_database):
    database._migrate_sqlite_orders_schema()

    # Running the migration a second time must be safe.
    database._migrate_sqlite_orders_schema()

    connection = sqlite3.connect(legacy_database)
    connection.row_factory = sqlite3.Row

    try:
        connection.execute("PRAGMA foreign_keys = ON")

        order = connection.execute(
            "SELECT * FROM orders WHERE id = 7"
        ).fetchone()

        assert order is not None
        assert order["order_reference"] == "VK-LEGACY-ORDER"
        assert order["status"] == "pending"
        assert order["total_amount"] == 13000
        assert order["rejection_reason"] is None

        # All related records must survive the table rebuild.
        for table in (
            "order_items",
            "order_idempotency",
            "customer_order_ownership",
            "payments",
        ):
            row = connection.execute(
                f"SELECT COUNT(*) AS total FROM {table} WHERE "
                + (
                    "order_id = 7"
                    if table != "order_idempotency"
                    else "order_id = 7"
                )
            ).fetchone()

            assert row["total"] == 1, table

        assert connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall() == []

    finally:
        connection.close()


def test_sqlite_migration_allows_rejected_status_and_preserves_sequence(
    legacy_database,
):
    database._migrate_sqlite_orders_schema()

    connection = sqlite3.connect(legacy_database)

    try:
        cursor = connection.execute("""
            INSERT INTO orders (
                order_reference,
                shop_id,
                status,
                total_amount,
                rejection_reason
            )
            VALUES (
                'VK-REJECTED-TEST',
                1,
                'rejected',
                5000,
                'Product unavailable'
            )
        """)

        rejected_id = cursor.lastrowid

        assert rejected_id > 30

        reason = connection.execute(
            """
            SELECT rejection_reason
            FROM orders
            WHERE id = ?
            """,
            (rejected_id,),
        ).fetchone()[0]

        assert reason == "Product unavailable"

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("""
                INSERT INTO orders (
                    order_reference,
                    shop_id,
                    status,
                    total_amount
                )
                VALUES (
                    'VK-INVALID-STATUS',
                    1,
                    'invalid_status',
                    100
                )
            """)

        connection.rollback()

    finally:
        connection.close()