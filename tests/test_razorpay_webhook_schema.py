
import sqlite3

import pytest

from backend import database


@pytest.fixture
def isolated_webhook_database(tmp_path, monkeypatch):
    """Use a separate temporary SQLite database for each test."""
    monkeypatch.setattr(database, "DATABASE_URL", "")
    monkeypatch.setattr(
        database,
        "DATABASE_PATH",
        tmp_path / "webhook_schema_test.db",
    )

    database.initialize_database()


def test_webhook_event_table_is_created(isolated_webhook_database):
    with database.get_connection() as connection:
        row = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = ? AND name = ?
            """,
            ("table", "razorpay_webhook_events"),
        ).fetchone()

    assert row is not None
    assert row["name"] == "razorpay_webhook_events"


def test_webhook_event_table_has_required_columns(
    isolated_webhook_database,
):
    with database.get_connection() as connection:
        rows = connection.execute(
            "PRAGMA table_info(razorpay_webhook_events)"
        ).fetchall()

    columns = {row["name"]: row for row in rows}

    assert {
        "event_id",
        "event_type",
        "razorpay_payment_id",
        "created_at",
    }.issubset(columns)

    assert columns["event_id"]["pk"] == 1
    assert columns["event_type"]["notnull"] == 1
    assert columns["created_at"]["notnull"] == 1


def test_duplicate_webhook_event_id_is_rejected(
    isolated_webhook_database,
):
    with database.get_connection() as connection:
        connection.execute(
            """
            INSERT INTO razorpay_webhook_events
                (event_id, event_type, razorpay_payment_id)
            VALUES (?, ?, ?)
            """,
            ("event_test_001", "payment.captured", "pay_test123"),
        )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO razorpay_webhook_events
                    (event_id, event_type, razorpay_payment_id)
                VALUES (?, ?, ?)
                """,
                ("event_test_001", "payment.captured", "pay_test123"),
            )


def test_different_webhook_event_ids_are_allowed(
    isolated_webhook_database,
):
    with database.get_connection() as connection:
        connection.execute(
            """
            INSERT INTO razorpay_webhook_events
                (event_id, event_type, razorpay_payment_id)
            VALUES (?, ?, ?)
            """,
            ("event_test_001", "payment.captured", "pay_test123"),
        )

        connection.execute(
            """
            INSERT INTO razorpay_webhook_events
                (event_id, event_type, razorpay_payment_id)
            VALUES (?, ?, ?)
            """,
            ("event_test_002", "payment.failed", "pay_test456"),
        )

        result = connection.execute(
            "SELECT COUNT(*) AS total FROM razorpay_webhook_events"
        ).fetchone()

    assert result["total"] == 2
