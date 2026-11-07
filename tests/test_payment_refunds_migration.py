import sqlite3

import pytest

import backend.database as database


def test_existing_payment_table_gets_refund_amount_column(
    tmp_path,
    monkeypatch,
):
    database_path = tmp_path / "legacy_payments.db"

    monkeypatch.setattr(database, "DATABASE_URL", "")
    monkeypatch.setattr(database, "DATABASE_PATH", database_path)

    # Build a legacy payments table without amount_refunded_paise.
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE payments (
                id INTEGER PRIMARY KEY,
                amount_paise INTEGER NOT NULL,
                status TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO payments (id, amount_paise, status)
            VALUES (1, 6800, 'captured')
            """
        )

    database._migrate_payment_refunds_schema()

    with database.get_connection() as connection:
        row = connection.execute(
            """
            SELECT amount_paise, amount_refunded_paise, status
            FROM payments
            WHERE id = 1
            """
        ).fetchone()

    assert row["amount_paise"] == 6800
    assert row["amount_refunded_paise"] == 0
    assert row["status"] == "captured"

    # The new database constraint must reject negative refunds.
    with pytest.raises(sqlite3.IntegrityError):
        with database.get_connection() as connection:
            with connection:
                connection.execute(
                    """
                    UPDATE payments
                    SET amount_refunded_paise = -1
                    WHERE id = 1
                    """
                )

    # It must also reject refunded amounts above the original payment.
    with pytest.raises(sqlite3.IntegrityError):
        with database.get_connection() as connection:
            with connection:
                connection.execute(
                    """
                    UPDATE payments
                    SET amount_refunded_paise = 6801
                    WHERE id = 1
                    """
                )


def test_payment_refund_migration_is_rerunnable(
    tmp_path,
    monkeypatch,
):
    database_path = tmp_path / "legacy_payments_rerun.db"

    monkeypatch.setattr(database, "DATABASE_URL", "")
    monkeypatch.setattr(database, "DATABASE_PATH", database_path)

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE payments (
                id INTEGER PRIMARY KEY,
                amount_paise INTEGER NOT NULL,
                status TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO payments (id, amount_paise, status)
            VALUES (1, 12000, 'captured')
            """
        )

    database._migrate_payment_refunds_schema()
    database._migrate_payment_refunds_schema()

    with database.get_connection() as connection:
        columns = connection.execute(
            "PRAGMA table_info(payments)"
        ).fetchall()

        assert sum(
            row["name"] == "amount_refunded_paise"
            for row in columns
        ) == 1

        payment = connection.execute(
            """
            SELECT amount_refunded_paise
            FROM payments
            WHERE id = 1
            """
        ).fetchone()

    assert payment["amount_refunded_paise"] == 0
