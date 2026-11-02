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
        self._connection.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return self._connection.__exit__(exc_type, exc_value, traceback)

    def close(self):
        self._connection.close()

    @property
    def row_factory(self):
        return getattr(self._connection, "row_factory", None)

    @row_factory.setter
    def row_factory(self, value):
        self._connection.row_factory = value


def get_connection():
    """Open a database connection using SQLite or PostgreSQL."""
    if DATABASE_URL:
        import psycopg

        connection = psycopg.connect(DATABASE_URL)
        connection.row_factory = None
        return DatabaseConnection(connection, is_postgres=True)

    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return DatabaseConnection(connection, is_postgres=False)


def initialize_database():
    """Create the tables required for multi-shop grocery ordering."""

    schema = """
    CREATE TABLE IF NOT EXISTS shops (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 1
            CHECK (is_active IN (0, 1)),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

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
                'cancelled'
            )),
        total_amount INTEGER NOT NULL DEFAULT 0
            CHECK (total_amount >= 0),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (shop_id) REFERENCES shops(id)
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

    with get_connection() as connection:
        for statement in schema.split(";"):
            if statement.strip():
                connection.execute(statement)


if __name__ == "__main__":
    initialize_database()
    print("VoiceKirana database initialized successfully.")
    print(f"Database location: {DATABASE_PATH}")
