import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

# Load .env before reading database configuration.
load_dotenv()
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

DATABASE_PATH = Path(
    os.getenv(
        "VOICEKIRANA_SQLITE_PATH",
        str(DATA_DIR / "voicekirana.db"),
    )
)


class DatabaseConnection:
    """Keep SQL calls compatible with SQLite and PostgreSQL."""

    def __init__(self, connection, is_postgres: bool):
        self._connection = connection
        self._is_postgres = is_postgres
        self._closed = False
        self._context_depth = 0

    def execute(self, sql, parameters=()):
        if self._is_postgres:
            sql = sql.replace("?", "%s")

        return self._connection.execute(sql, parameters)

    def insert_returning_id(self, sql, parameters=()):
        """Insert a row and return its generated primary key."""
        if self._is_postgres:
            sql = sql.rstrip().rstrip(";") + " RETURNING id"
            cursor = self.execute(sql, parameters)
            row = cursor.fetchone()
            return row["id"]

        cursor = self.execute(sql, parameters)
        return cursor.lastrowid

    def executescript(self, sql):
        if self._is_postgres:
            raise NotImplementedError(
                "DatabaseConnection does not support executescript for PostgreSQL."
            )
        return self._connection.executescript(sql)

    def __enter__(self):
        if self._closed:
            raise RuntimeError("Cannot use a closed database connection.")

        self._context_depth += 1
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """
        Finalize the transaction and close the connection only
        when the outermost context exits.
        """
        if self._context_depth == 0:
            return False
        self._context_depth -= 1
        if self._context_depth > 0:
            return False
        try:
            if self._is_postgres:
                if exc_type is None:
                    self._connection.commit()
                else:
                    self._connection.rollback()
            else:
                self._connection.__exit__(
                    exc_type,
                    exc_value,
                    traceback,
                )
        finally:
            self.close()

        return False


    def close(self):
        """Close the underlying connection exactly once."""
        if not self._closed:
            self._closed = True
            self._connection.close()

    @property
    def row_factory(self):
        return getattr(self._connection, "row_factory", None)

    @row_factory.setter
    def row_factory(self, value):
        self._connection.row_factory = value


def get_connection():
    """Open a database connection using SQLite or PostgreSQL."""
    print(
        "VOICEKIRANA DATABASE MODE:",
        "PostgreSQL" if DATABASE_URL else "SQLite",
    )
    if DATABASE_URL:
        import psycopg
        from psycopg.rows import dict_row

        connection = psycopg.connect(
            DATABASE_URL,
            row_factory=dict_row,
        )

        return DatabaseConnection(
            connection,
            is_postgres=True,
        )

    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    print("ACTIVE SQLITE DATABASE:", DATABASE_PATH.resolve())
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return DatabaseConnection(connection, is_postgres=False)



def _migrate_sqlite_orders_schema():
    """Safely rebuild the SQLite orders table while preserving related rows."""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

    raw_connection = sqlite3.connect(DATABASE_PATH, timeout=10)

    try:
        # Must be disabled before beginning the migration transaction.
        raw_connection.execute("PRAGMA foreign_keys = OFF")
        raw_connection.execute("BEGIN IMMEDIATE")

        table_row = raw_connection.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'table' AND name = 'orders'
            """
        ).fetchone()

        if table_row is None:
            raw_connection.commit()
            return

        table_sql = table_row[0] or ""

        columns = {
            row[1]
            for row in raw_connection.execute(
                "PRAGMA table_info(orders)"
            ).fetchall()
        }

        # The migration is idempotent.
        if "'rejected'" in table_sql and "rejection_reason" in columns:
            raw_connection.commit()
            return

        old_sequence = None
        try:
            sequence_row = raw_connection.execute(
                """
                SELECT seq FROM sqlite_sequence
                WHERE name = 'orders'
                """
            ).fetchone()

            if sequence_row is not None:
                old_sequence = sequence_row[0]
        except sqlite3.OperationalError:
            pass

        reason_expression = (
            "rejection_reason"
            if "rejection_reason" in columns
            else "NULL"
        )

        raw_connection.execute("DROP TABLE IF EXISTS orders_new")

        raw_connection.execute(
            """
            CREATE TABLE orders_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_reference TEXT NOT NULL UNIQUE,
                shop_id INTEGER NOT NULL,
                status TEXT NOT NULL
                    CHECK (status IN (
                        'pending',
                        'confirmed',
                        'cancelled',
                        'rejected'
                    )),
                total_amount INTEGER NOT NULL DEFAULT 0
                    CHECK (total_amount >= 0),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                rejection_reason TEXT,
                FOREIGN KEY (shop_id) REFERENCES shops(id)
            )
            """
        )

        raw_connection.execute(
            f"""
            INSERT INTO orders_new (
                id,
                order_reference,
                shop_id,
                status,
                total_amount,
                created_at,
                rejection_reason
            )
            SELECT
                id,
                order_reference,
                shop_id,
                status,
                total_amount,
                created_at,
                {reason_expression}
            FROM orders
            """
        )

        raw_connection.execute("DROP TABLE orders")
        raw_connection.execute(
            "ALTER TABLE orders_new RENAME TO orders"
        )

        # Preserve the old AUTOINCREMENT sequence if it exceeded
        # the highest order ID still present.
        if old_sequence is not None:
            sequence_row = raw_connection.execute(
                """
                SELECT seq FROM sqlite_sequence
                WHERE name = 'orders'
                """
            ).fetchone()

            if sequence_row is None:
                raw_connection.execute(
                    """
                    INSERT INTO sqlite_sequence (name, seq)
                    VALUES ('orders', ?)
                    """,
                    (old_sequence,),
                )
            elif sequence_row[0] < old_sequence:
                raw_connection.execute(
                    """
                    UPDATE sqlite_sequence
                    SET seq = ?
                    WHERE name = 'orders'
                    """,
                    (old_sequence,),
                )

        raw_connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_orders_shop_status
            ON orders(shop_id, status)
            """
        )

        violations = raw_connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

        if violations:
            raise sqlite3.IntegrityError(
                f"Foreign-key violations after orders migration: {violations[:5]}"
            )

        raw_connection.commit()
        raw_connection.execute("PRAGMA foreign_keys = ON")

        violations = raw_connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

        if violations:
            raise sqlite3.IntegrityError(
                f"Foreign-key violations after migration: {violations[:5]}"
            )

    except Exception:
        if raw_connection.in_transaction:
            raw_connection.rollback()
        raise

    finally:
        if raw_connection.in_transaction:
            raw_connection.rollback()

        try:
            raw_connection.execute("PRAGMA foreign_keys = ON")
        except sqlite3.Error:
            pass

        raw_connection.close()


def _migrate_postgres_orders_schema():
    """Upgrade PostgreSQL order status schema only when required."""
    with get_connection() as connection:
        column = connection.execute(
            """
            SELECT 1 AS present
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'orders'
              AND column_name = 'rejection_reason'
            """
        ).fetchone()

        if column is None:
            connection.execute(
                """
                ALTER TABLE orders
                ADD COLUMN rejection_reason TEXT
                """
            )

        constraint = connection.execute(
            """
            SELECT pg_get_constraintdef(oid) AS definition
            FROM pg_constraint
            WHERE conrelid = 'orders'::regclass
              AND conname = 'orders_status_check'
              AND contype = 'c'
            """
        ).fetchone()

        if constraint is None or "'rejected'" not in constraint["definition"]:
            if constraint is not None:
                connection.execute(
                    """
                    ALTER TABLE orders
                    DROP CONSTRAINT orders_status_check
                    """
                )

            connection.execute(
                """
                ALTER TABLE orders
                ADD CONSTRAINT orders_status_check
                CHECK (status IN (
                    'pending',
                    'confirmed',
                    'cancelled',
                    'rejected'
                ))
                """
            )


def _migrate_customer_visibility_schema():
    """Add a safe, opt-in customer visibility flag to shops."""

    with get_connection() as connection:
        if DATABASE_URL:
            column = connection.execute(
                """
                SELECT 1 AS present
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'shops'
                  AND column_name = 'is_customer_visible'
                """
            ).fetchone()

            if column is None:
                connection.execute(
                    """
                    ALTER TABLE shops
                    ADD COLUMN is_customer_visible INTEGER NOT NULL
                    DEFAULT 0
                    CHECK (is_customer_visible IN (0, 1))
                    """
                )
        else:
            columns = connection.execute(
                "PRAGMA table_info(shops)"
            ).fetchall()

            if not any(
                row["name"] == "is_customer_visible"
                for row in columns
            ):
                connection.execute(
                    """
                    ALTER TABLE shops
                    ADD COLUMN is_customer_visible INTEGER NOT NULL
                    DEFAULT 0
                    CHECK (is_customer_visible IN (0, 1))
                    """
                )


def _migrate_inventory_reservations_schema():
    """Add reserved stock tracking to existing SQLite/PostgreSQL databases."""
    with get_connection() as connection:
        if DATABASE_URL:
            column = connection.execute(
                """
                SELECT 1 AS present
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'inventory'
                  AND column_name = 'reserved_quantity'
                """
            ).fetchone()

            if column is None:
                connection.execute(
                    """
                    ALTER TABLE inventory
                    ADD COLUMN reserved_quantity REAL NOT NULL DEFAULT 0
                    CHECK (
                        reserved_quantity >= 0
                        AND reserved_quantity <= stock_quantity
                    )
                    """
                )
        else:
            columns = connection.execute(
                "PRAGMA table_info(inventory)"
            ).fetchall()

            if not any(row["name"] == "reserved_quantity" for row in columns):
                connection.execute(
                    """
                    ALTER TABLE inventory
                    ADD COLUMN reserved_quantity REAL NOT NULL DEFAULT 0
                    CHECK (
                        reserved_quantity >= 0
                        AND reserved_quantity <= stock_quantity
                    )
                    """
                )



def _migrate_payment_refunds_schema():
    """Add cumulative refund tracking to existing payment tables."""
    with get_connection() as connection:
        if DATABASE_URL:
            column = connection.execute(
                """
                SELECT 1 AS present
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'payments'
                  AND column_name = 'amount_refunded_paise'
                """
            ).fetchone()

            if column is None:
                connection.execute(
                    """
                    ALTER TABLE payments
                    ADD COLUMN amount_refunded_paise INTEGER NOT NULL
                    DEFAULT 0
                    CHECK (
                        amount_refunded_paise >= 0
                        AND amount_refunded_paise <= amount_paise
                    )
                    """
                )
        else:
            columns = connection.execute(
                "PRAGMA table_info(payments)"
            ).fetchall()

            if not any(
                row["name"] == "amount_refunded_paise"
                for row in columns
            ):
                connection.execute(
                    """
                    ALTER TABLE payments
                    ADD COLUMN amount_refunded_paise INTEGER NOT NULL
                    DEFAULT 0
                    CHECK (
                        amount_refunded_paise >= 0
                        AND amount_refunded_paise <= amount_paise
                    )
                    """
                )



def _migrate_customer_profile_schema():
    """Add editable customer profile fields and customer-owned addresses."""

    with get_connection() as connection:
        if DATABASE_URL:
            connection.execute(
                "ALTER TABLE customers ADD COLUMN IF NOT EXISTS phone TEXT"
            )
            connection.execute(
                """
                ALTER TABLE customers
                ADD COLUMN IF NOT EXISTS preferred_language TEXT
                NOT NULL DEFAULT 'en-IN'
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS customer_addresses (
                    id BIGSERIAL PRIMARY KEY,
                    customer_id BIGINT NOT NULL,
                    label TEXT NOT NULL,
                    recipient_name TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    address_line1 TEXT NOT NULL,
                    address_line2 TEXT,
                    landmark TEXT,
                    city TEXT NOT NULL,
                    state TEXT NOT NULL,
                    postal_code TEXT NOT NULL,
                    country TEXT NOT NULL DEFAULT 'India',
                    is_default BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (customer_id)
                        REFERENCES customers(id)
                        ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_customer_addresses_customer
                ON customer_addresses(customer_id)
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS
                uq_customer_addresses_one_default
                ON customer_addresses(customer_id)
                WHERE is_default = TRUE
                """
            )
        else:
            customer_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(customers)"
                ).fetchall()
            }
            if "phone" not in customer_columns:
                connection.execute(
                    "ALTER TABLE customers ADD COLUMN phone TEXT"
                )
            if "preferred_language" not in customer_columns:
                connection.execute(
                    """
                    ALTER TABLE customers
                    ADD COLUMN preferred_language TEXT NOT NULL DEFAULT 'en-IN'
                    """
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS customer_addresses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_id INTEGER NOT NULL,
                    label TEXT NOT NULL,
                    recipient_name TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    address_line1 TEXT NOT NULL,
                    address_line2 TEXT,
                    landmark TEXT,
                    city TEXT NOT NULL,
                    state TEXT NOT NULL,
                    postal_code TEXT NOT NULL,
                    country TEXT NOT NULL DEFAULT 'India',
                    is_default INTEGER NOT NULL DEFAULT 0
                        CHECK (is_default IN (0, 1)),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (customer_id)
                        REFERENCES customers(id)
                        ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_customer_addresses_customer
                ON customer_addresses(customer_id)
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS
                uq_customer_addresses_one_default
                ON customer_addresses(customer_id)
                WHERE is_default = 1
                """
            )


def _migrate_customer_remember_sessions_schema():
    """Create revocable customer remember-me sessions."""

    with get_connection() as connection:
        if DATABASE_URL:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS customer_remember_sessions (
                    id BIGSERIAL PRIMARY KEY,
                    customer_id BIGINT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_used_at TEXT,
                    revoked_at TEXT,
                    FOREIGN KEY (customer_id)
                        REFERENCES customers(id)
                        ON DELETE CASCADE
                )
                """
            )
        else:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS customer_remember_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_id INTEGER NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_used_at TEXT,
                    revoked_at TEXT,
                    FOREIGN KEY (customer_id)
                        REFERENCES customers(id)
                        ON DELETE CASCADE
                )
                """
            )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_customer_remember_sessions_customer
            ON customer_remember_sessions(customer_id)
            """
        )


def initialize_database():
    """Create the tables required for multi-shop grocery ordering."""

    schema = """
    CREATE TABLE IF NOT EXISTS shops (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 1
            CHECK (is_active IN (0, 1)),
        is_customer_visible INTEGER NOT NULL DEFAULT 0
            CHECK (is_customer_visible IN (0, 1)),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL UNIQUE,
        full_name TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 1
            CHECK (is_active IN (0, 1)),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL UNIQUE,
        full_name TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 1
            CHECK (is_active IN (0, 1)),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS shop_memberships (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        shop_id INTEGER NOT NULL,
        role TEXT NOT NULL DEFAULT 'owner'
            CHECK (role IN ('owner', 'manager')),
        is_active INTEGER NOT NULL DEFAULT 1
            CHECK (is_active IN (0, 1)),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id)
            REFERENCES users(id)
            ON DELETE CASCADE,
        FOREIGN KEY (shop_id)
            REFERENCES shops(id)
            ON DELETE CASCADE,
        UNIQUE (user_id, shop_id)
    );

    CREATE INDEX IF NOT EXISTS idx_shop_memberships_user
        ON shop_memberships(user_id);

    CREATE INDEX IF NOT EXISTS idx_shop_memberships_shop
        ON shop_memberships(shop_id);

    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        normalized_name TEXT NOT NULL,
        brand TEXT,
        variant TEXT,
        base_unit TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS product_aliases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL,
        alias TEXT NOT NULL,
        normalized_alias TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (product_id)
            REFERENCES products(id)
            ON DELETE CASCADE,
        UNIQUE (product_id, normalized_alias)
    );

    CREATE INDEX IF NOT EXISTS idx_product_aliases_normalized
        ON product_aliases(normalized_alias);

    CREATE TABLE IF NOT EXISTS inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        price_per_unit INTEGER NOT NULL CHECK (price_per_unit >= 0),
        stock_quantity REAL NOT NULL DEFAULT 0
            CHECK (stock_quantity >= 0),
        reserved_quantity REAL NOT NULL DEFAULT 0
            CHECK (
                reserved_quantity >= 0
                AND reserved_quantity <= stock_quantity
            ),
        is_available INTEGER NOT NULL DEFAULT 1
            CHECK (is_available IN (0, 1)),
        FOREIGN KEY (shop_id) REFERENCES shops(id),
        FOREIGN KEY (product_id) REFERENCES products(id),
        UNIQUE (shop_id, product_id)
    );

    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_reference TEXT NOT NULL UNIQUE,
        shop_id INTEGER NOT NULL,
        status TEXT NOT NULL
            CHECK (status IN (
                'pending',
                'confirmed',
                'cancelled',
                'rejected'
            )),
        total_amount INTEGER NOT NULL DEFAULT 0
            CHECK (total_amount >= 0),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        rejection_reason TEXT,
        FOREIGN KEY (shop_id) REFERENCES shops(id)
    );

    CREATE TABLE IF NOT EXISTS order_idempotency (
        shop_id INTEGER NOT NULL,
        idempotency_key TEXT NOT NULL,
        request_fingerprint TEXT NOT NULL,
        order_id BIGINT,
        response_json TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

        PRIMARY KEY (shop_id, idempotency_key),

        FOREIGN KEY (shop_id) REFERENCES shops(id),
        FOREIGN KEY (order_id) REFERENCES orders(id)
    );

    CREATE TABLE IF NOT EXISTS customer_order_ownership (
        order_id INTEGER NOT NULL,
        customer_id INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

        PRIMARY KEY (order_id),

        FOREIGN KEY (order_id)
            REFERENCES orders(id)
            ON DELETE CASCADE,

        FOREIGN KEY (customer_id)
            REFERENCES customers(id)
            ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        customer_id INTEGER NOT NULL,

        idempotency_key TEXT NOT NULL,

        razorpay_order_id TEXT UNIQUE,
        razorpay_payment_id TEXT UNIQUE,

        amount_paise INTEGER NOT NULL
            CHECK (amount_paise > 0),

        amount_refunded_paise INTEGER NOT NULL DEFAULT 0
            CHECK (
                amount_refunded_paise >= 0
                AND amount_refunded_paise <= amount_paise
            ),

        currency TEXT NOT NULL DEFAULT 'INR'
            CHECK (currency = 'INR'),

        status TEXT NOT NULL DEFAULT 'creating'
            CHECK (status IN (
                'creating',
                'created',
                'authorized',
                'captured',
                'failed',
                'creation_failed',
                'refunded'
            )),

        failure_code TEXT,
        failure_description TEXT,

        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

        FOREIGN KEY (order_id)
            REFERENCES orders(id)
            ON DELETE CASCADE,

        FOREIGN KEY (customer_id)
            REFERENCES customers(id)
            ON DELETE CASCADE,

        UNIQUE (order_id, idempotency_key)
    );


    CREATE TABLE IF NOT EXISTS order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        quantity REAL NOT NULL CHECK (quantity > 0),
        unit TEXT NOT NULL,
        unit_price INTEGER NOT NULL CHECK (unit_price >= 0),
        line_total INTEGER NOT NULL CHECK (line_total >= 0),
        FOREIGN KEY (order_id) REFERENCES orders(id),
        FOREIGN KEY (product_id) REFERENCES products(id)
    );

    CREATE INDEX IF NOT EXISTS idx_inventory_shop
        ON inventory(shop_id);

    CREATE INDEX IF NOT EXISTS idx_orders_shop_status
        ON orders(shop_id, status);

    CREATE INDEX IF NOT EXISTS idx_customer_order_ownership_customer
        ON customer_order_ownership(customer_id);
        CREATE INDEX IF NOT EXISTS idx_payments_customer_created
        ON payments(customer_id, created_at);

    CREATE INDEX IF NOT EXISTS idx_payments_order
        ON payments(order_id);
    CREATE INDEX IF NOT EXISTS idx_inventory_product
        ON inventory(product_id);
    CREATE INDEX IF NOT EXISTS idx_order_idempotency_order
        ON order_idempotency(order_id);
    CREATE INDEX IF NOT EXISTS idx_order_items_order
        ON order_items(order_id);
    CREATE INDEX IF NOT EXISTS idx_order_items_product
        ON order_items(product_id);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_one_active_per_order
        ON payments(order_id)
        WHERE status IN (
            'creating',
            'created',
            'authorized',
            'captured',
            'refunded'
        );
    CREATE TABLE IF NOT EXISTS razorpay_webhook_events (
        event_id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        razorpay_payment_id TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """

    if DATABASE_URL:
        # PostgreSQL uses BIGSERIAL for generated primary keys.
        schema = schema.replace(
            "INTEGER PRIMARY KEY AUTOINCREMENT",
            "BIGSERIAL PRIMARY KEY",
        )

        # Match PostgreSQL's BIGSERIAL primary-key types.
        schema = schema.replace(
            "shop_id INTEGER NOT NULL",
            "shop_id BIGINT NOT NULL",
        )
        schema = schema.replace(
            "product_id INTEGER NOT NULL",
            "product_id BIGINT NOT NULL",
        )
        schema = schema.replace(
            "order_id INTEGER NOT NULL",
            "order_id BIGINT NOT NULL",
        )
        schema = schema.replace(
            "user_id INTEGER NOT NULL",
            "user_id BIGINT NOT NULL",
        )
        schema = schema.replace(
            "customer_id INTEGER NOT NULL",
            "customer_id BIGINT NOT NULL",
        )

    with get_connection() as connection:
        for statement in schema.split(";"):
            if statement.strip():
                connection.execute(statement)

    # Upgrade existing databases after creating any missing tables.
    _migrate_customer_visibility_schema()
    _migrate_customer_remember_sessions_schema()
    _migrate_customer_profile_schema()

    if DATABASE_URL:
        _migrate_postgres_orders_schema()
    else:
        _migrate_sqlite_orders_schema()

    _migrate_inventory_reservations_schema()
    _migrate_payment_refunds_schema()


if __name__ == "__main__":
    initialize_database()
    print("VoiceKirana database initialized successfully.")
    print(f"Database location: {DATABASE_PATH}")
