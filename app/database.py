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
            "owner_instance_id": "INTEGER",
            "owner_project_id": "INTEGER",
        },
    }

    for table, cols in desired.items():
        existing = await _existing_columns(table)
        for col, ddl in cols.items():
            if col in existing:
                continue
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))


async def get_db() -> AsyncSession:
    """Dependency for getting database sessions."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
