from backend.database import DATABASE_URL, get_connection

EXPECTED_TABLES = ["users", "shop_memberships"]

if not DATABASE_URL:
    raise SystemExit(
        "ERROR: PostgreSQL DATABASE_URL is not configured. "
        "Stopping verification."
    )

print("Checking VoiceKirana PostgreSQL authentication schema...")

with get_connection() as connection:
    for table in EXPECTED_TABLES:
        result = connection.execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = ?
            ORDER BY ordinal_position
            """,
            (table,),
        ).fetchall()

        if not result:
            print(f"\nMISSING TABLE: {table}")
            continue

        print(f"\nTABLE EXISTS: {table}")
        for column in result:
            print(
                f"  {column['column_name']}: "
                f"{column['data_type']}"
            )

print("\nSchema verification completed.")
