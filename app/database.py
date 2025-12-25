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

    async def _existing_columns(table: str) -> set[str]:
        res = await conn.execute(text(f"PRAGMA table_info({table})"))
        # PRAGMA table_info: cid, name, type, notnull, dflt_value, pk
        return {row[1] for row in res.fetchall()}

    async def _existing_indexes(table: str) -> set[str]:
        res = await conn.execute(text(f"PRAGMA index_list({table})"))
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
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))

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
            await conn.execute(text(
                f"CREATE UNIQUE INDEX {constraint['index_name']} "
                f"ON {table} {constraint['columns']}"
            ))


async def get_db() -> AsyncSession:
    """Dependency for getting database sessions."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
