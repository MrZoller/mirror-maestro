"""Tests for database initialization and session management."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.database import init_db
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
async def test_tables_have_expected_columns():
    """Test that tables have all expected columns defined in models."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

            # Check gitlab_instances columns
            result = await conn.execute(text("PRAGMA table_info(gitlab_instances)"))
            columns = {row[1] for row in result.fetchall()}
            assert "id" in columns
            assert "name" in columns
            assert "url" in columns
            assert "encrypted_token" in columns
            assert "api_user_id" in columns
            assert "api_username" in columns
            assert "created_at" in columns
            assert "updated_at" in columns

            # Check instance_pairs columns
            result = await conn.execute(text("PRAGMA table_info(instance_pairs)"))
            columns = {row[1] for row in result.fetchall()}
            assert "id" in columns
            assert "name" in columns
            assert "source_instance_id" in columns
            assert "target_instance_id" in columns
            assert "mirror_direction" in columns

            # Check mirrors columns
            result = await conn.execute(text("PRAGMA table_info(mirrors)"))
            columns = {row[1] for row in result.fetchall()}
            assert "id" in columns
            assert "instance_pair_id" in columns
            assert "source_project_id" in columns
            assert "target_project_id" in columns
            assert "enabled" in columns

    finally:
        await engine.dispose()
