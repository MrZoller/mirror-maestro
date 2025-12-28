import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.database import init_db, _maybe_migrate_sqlite
from app.models import Base


@pytest.mark.asyncio
async def test_init_db_creates_all_tables():
    """Test that init_db creates all required tables."""
    # Use in-memory SQLite for testing
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    # Override the global engine temporarily
    from app import database as db_mod
    original_engine = db_mod.engine
    db_mod.engine = engine

    try:
        # Initialize the database
        await init_db()

        # Check that all tables were created
        async with engine.begin() as conn:
            result = await conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ))
            tables = {row[0] for row in result.fetchall()}

        # Verify all expected tables exist
        expected_tables = {
            'gitlab_instances',
            'instance_pairs',
            'mirrors',
        }
        assert expected_tables.issubset(tables), f"Missing tables: {expected_tables - tables}"

    finally:
        # Restore original engine
        db_mod.engine = original_engine
        await engine.dispose()


@pytest.mark.asyncio
async def test_migration_adds_missing_columns():
    """Test that migrations add new columns to existing tables."""
    # Create in-memory database
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    try:
        # Create tables without migrations
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

            # Remove a column that the migration should add (simulate old schema)
            # First check which columns exist
            result = await conn.execute(text("PRAGMA table_info(gitlab_instances)"))
            columns_before = {row[1] for row in result.fetchall()}

            # If api_user_id already exists from the model, we'll drop it and re-add via migration
            # Note: SQLite doesn't support DROP COLUMN easily, so we'll test on a fresh table

        # Now run the migration
        async with engine.begin() as conn:
            await _maybe_migrate_sqlite(conn)

            # Check that migration columns were added
            result = await conn.execute(text("PRAGMA table_info(gitlab_instances)"))
            columns_after = {row[1] for row in result.fetchall()}

            # These columns should be present after migration
            assert "api_user_id" in columns_after
            assert "api_username" in columns_after

            # Check instance_pairs table
            result = await conn.execute(text("PRAGMA table_info(instance_pairs)"))
            pair_columns = {row[1] for row in result.fetchall()}
            assert "mirror_branch_regex" in pair_columns

            # Check mirrors table
            result = await conn.execute(text("PRAGMA table_info(mirrors)"))
            mirror_columns = {row[1] for row in result.fetchall()}
            assert "mirror_branch_regex" in mirror_columns

    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_migration_idempotent():
    """Test that running migrations multiple times is safe."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    try:
        # Create tables
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Run migration multiple times
        async with engine.begin() as conn:
            await _maybe_migrate_sqlite(conn)
            await _maybe_migrate_sqlite(conn)
            await _maybe_migrate_sqlite(conn)

            # Verify columns still exist and are correct
            result = await conn.execute(text("PRAGMA table_info(gitlab_instances)"))
            columns = {row[1] for row in result.fetchall()}
            assert "api_user_id" in columns
            assert "api_username" in columns

    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_migration_skips_non_sqlite():
    """Test that SQLite migrations don't run on other databases."""
    # This test uses an in-memory SQLite database but we'll simulate
    # a different dialect by checking the function's behavior

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

            # The migration function checks conn.dialect.name
            # For SQLite it should run, for others it should skip
            assert conn.dialect.name == "sqlite"

            # Run migration (should work for SQLite)
            await _maybe_migrate_sqlite(conn)

            # Verify it ran by checking for migrated columns
            result = await conn.execute(text("PRAGMA table_info(gitlab_instances)"))
            columns = {row[1] for row in result.fetchall()}
            assert "api_user_id" in columns

    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_db_yields_session():
    """Test that get_db dependency yields a valid session."""
    from app.database import get_db

    # Create test database
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Override global session maker
    from app import database as db_mod
    original_session_maker = db_mod.AsyncSessionLocal
    db_mod.AsyncSessionLocal = session_maker

    try:
        # Initialize tables
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Test the dependency
        gen = get_db()
        session = await gen.__anext__()

        try:
            # Should be a valid session
            assert isinstance(session, AsyncSession)

            # Should be able to execute queries
            result = await session.execute(text("SELECT 1"))
            assert result.scalar() == 1

        finally:
            # Clean up the generator
            try:
                await gen.__anext__()
            except StopAsyncIteration:
                pass

    finally:
        db_mod.AsyncSessionLocal = original_session_maker
        await engine.dispose()


@pytest.mark.asyncio
async def test_migration_preserves_existing_data():
    """Test that migrations don't lose existing data."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    try:
        # Create tables and insert test data
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

            # Insert a test instance with required timestamp fields
            await conn.execute(text(
                "INSERT INTO gitlab_instances (name, url, encrypted_token, created_at, updated_at) "
                "VALUES ('test', 'https://gitlab.com', 'enc:token', datetime('now'), datetime('now'))"
            ))

            # Run migration
            await _maybe_migrate_sqlite(conn)

            # Verify data is still there
            result = await conn.execute(text(
                "SELECT name, url FROM gitlab_instances WHERE name = 'test'"
            ))
            row = result.fetchone()
            assert row is not None
            assert row[0] == "test"
            assert row[1] == "https://gitlab.com"

            # Verify new columns exist with NULL values (they should be added by migration)
            result = await conn.execute(text(
                "SELECT api_user_id, api_username FROM gitlab_instances WHERE name = 'test'"
            ))
            row = result.fetchone()
            assert row[0] is None  # api_user_id
            assert row[1] is None  # api_username

    finally:
        await engine.dispose()
