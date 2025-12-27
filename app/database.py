from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import text
from app.config import settings
from app.models import Base


engine = create_async_engine(
    settings.database_url,
    echo=settings.log_level == "DEBUG",
    future=True
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)


async def init_db():
    """Initialize the database, creating all tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _maybe_migrate_sqlite(conn)


async def _maybe_migrate_sqlite(conn) -> None:
    """
    Minimal, SQLite-only schema migrations.

    This project intentionally avoids a full migration framework, but we still
    want upgrades to be non-destructive for existing installs using SQLite.
    """
    if conn.dialect.name != "sqlite":
        return

    # SECURITY: Define allowed table names to prevent SQL injection
    # While values are currently hardcoded, this prevents dangerous copy-paste errors
    ALLOWED_TABLES = {
        "gitlab_instances",
        "instance_pairs",
        "mirrors",
        "group_access_tokens",
        "group_mirror_defaults"
    }

    def _validate_table_name(table: str) -> str:
        """Validate that table name is in our allowed set to prevent SQL injection."""
        if table not in ALLOWED_TABLES:
            raise ValueError(f"Invalid table name: {table}")
        return table

    def _validate_identifier(identifier: str) -> str:
        """
        Validate SQL identifier (table/column/index name) to prevent SQL injection.
        Allows alphanumeric characters and underscores only.
        """
        if not identifier.replace("_", "").isalnum():
            raise ValueError(f"Invalid SQL identifier: {identifier}")
        return identifier

    async def _existing_columns(table: str) -> set[str]:
        # Validate table name before using in SQL
        safe_table = _validate_table_name(table)
        res = await conn.execute(text(f"PRAGMA table_info({safe_table})"))
        # PRAGMA table_info: cid, name, type, notnull, dflt_value, pk
        return {row[1] for row in res.fetchall()}

    async def _existing_indexes(table: str) -> set[str]:
        # Validate table name before using in SQL
        safe_table = _validate_table_name(table)
        res = await conn.execute(text(f"PRAGMA index_list({safe_table})"))
        # PRAGMA index_list: seq, name, unique, origin, partial
        return {row[1] for row in res.fetchall()}

    # Columns added after initial release.
    desired: dict[str, dict[str, str]] = {
        "gitlab_instances": {
            "api_user_id": "INTEGER",
            "api_username": "VARCHAR(255)",
        },
        "instance_pairs": {
            "mirror_branch_regex": "VARCHAR(255)",
            "mirror_user_id": "INTEGER",
        },
        "mirrors": {
            "mirror_branch_regex": "VARCHAR(255)",
            "mirror_user_id": "INTEGER",
        },
    }

    for table, cols in desired.items():
        existing = await _existing_columns(table)
        for col, ddl in cols.items():
            if col in existing:
                continue
            # Validate table and column names before using in SQL
            safe_table = _validate_table_name(table)
            safe_col = _validate_identifier(col)
            # DDL types are from our hardcoded dictionary, but validate them too
            if not all(c.isalnum() or c in "()_ " for c in ddl):
                raise ValueError(f"Invalid DDL type: {ddl}")
            await conn.execute(text(f"ALTER TABLE {safe_table} ADD COLUMN {safe_col} {ddl}"))

    # Add unique constraints via unique indexes for existing databases
    # (New databases will get these from __table_args__ in models)
    unique_constraints = {
        "group_access_tokens": {
            "index_name": "uq_group_access_token_instance_group",
            "columns": "(gitlab_instance_id, group_path)"
        },
        "group_mirror_defaults": {
            "index_name": "uq_group_mirror_defaults_pair_group",
            "columns": "(instance_pair_id, group_path)"
        }
    }

    for table, constraint in unique_constraints.items():
        existing_indexes = await _existing_indexes(table)
        if constraint["index_name"] not in existing_indexes:
            # Check if there are any duplicate rows before creating the unique index
            # If duplicates exist, we keep the most recent one
            if table == "group_access_tokens":
                await conn.execute(text("""
                    DELETE FROM group_access_tokens
                    WHERE id NOT IN (
                        SELECT MAX(id)
                        FROM group_access_tokens
                        GROUP BY gitlab_instance_id, group_path
                    )
                """))
            elif table == "group_mirror_defaults":
                await conn.execute(text("""
                    DELETE FROM group_mirror_defaults
                    WHERE id NOT IN (
                        SELECT MAX(id)
                        FROM group_mirror_defaults
                        GROUP BY instance_pair_id, group_path
                    )
                """))

            # Now create the unique index
            # Validate identifiers before using in SQL
            safe_table = _validate_table_name(table)
            safe_index_name = _validate_identifier(constraint['index_name'])
            # Validate columns format (should be like "(col1, col2)")
            columns_str = constraint['columns']
            if not columns_str.startswith("(") or not columns_str.endswith(")"):
                raise ValueError(f"Invalid columns format: {columns_str}")
            # Extract and validate column names from the parentheses
            cols_inner = columns_str[1:-1].replace(" ", "")
            for col_name in cols_inner.split(","):
                _validate_identifier(col_name)

            await conn.execute(text(
                f"CREATE UNIQUE INDEX {safe_index_name} "
                f"ON {safe_table} {columns_str}"
            ))


async def get_db() -> AsyncSession:
    """Dependency for getting database sessions."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
