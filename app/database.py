from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.config import settings
from app.models import Base


# SQL echo should only be enabled in development with explicit flag
# Never enable in production as it may log sensitive data (tokens, credentials)
_enable_sql_echo = (
    settings.log_level.upper() == "DEBUG" and
    settings.environment == "development"
)

engine = create_async_engine(
    settings.database_url,
    echo=_enable_sql_echo,
    future=True,
    # Connection pool configuration for production resilience
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_pool_max_overflow,
    pool_recycle=settings.db_pool_recycle,
    pool_pre_ping=settings.db_pool_pre_ping,
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
    # Run migrations for existing databases
    await _migrate_add_mirror_status_columns()
    # Clean up orphaned issue sync data from previously deleted mirrors
    await _cleanup_orphaned_issue_sync_data()


async def _migrate_add_mirror_status_columns():
    """
    Add last_update_at and last_error columns to mirrors table if they don't exist.

    These columns store the actual mirror status from GitLab:
    - last_update_at: Timestamp of the last sync attempt
    - last_error: Error message from GitLab if the last sync failed
    """
    import logging
    from sqlalchemy import text, inspect

    async with engine.begin() as conn:
        # Get the inspector to check existing columns (works with both SQLite and PostgreSQL)
        def get_columns(sync_conn):
            inspector = inspect(sync_conn)
            try:
                columns = inspector.get_columns('mirrors')
                return {col['name'] for col in columns}
            except Exception:
                # Table doesn't exist yet, will be created by metadata.create_all
                return set()

        existing_columns = await conn.run_sync(get_columns)

        # Add last_update_at column if not present
        if 'last_update_at' not in existing_columns:
            logging.info("Adding last_update_at column to mirrors table")
            try:
                await conn.execute(text(
                    "ALTER TABLE mirrors ADD COLUMN last_update_at TIMESTAMP"
                ))
            except Exception as e:
                # Column might already exist or table doesn't exist yet
                logging.debug(f"Could not add last_update_at column: {e}")

        # Add last_error column if not present
        if 'last_error' not in existing_columns:
            logging.info("Adding last_error column to mirrors table")
            try:
                await conn.execute(text(
                    "ALTER TABLE mirrors ADD COLUMN last_error TEXT"
                ))
            except Exception as e:
                # Column might already exist or table doesn't exist yet
                logging.debug(f"Could not add last_error column: {e}")


async def _cleanup_orphaned_issue_sync_data():
    """
    Remove issue sync data whose parent mirror no longer exists.

    This handles data left behind by mirror/pair/instance deletions that
    occurred before cascade cleanup was added.
    """
    import logging
    from sqlalchemy import text

    async with engine.begin() as conn:
        # Check if the tables exist before attempting cleanup
        try:
            result = await conn.execute(text(
                "SELECT COUNT(*) FROM mirror_issue_configs mic "
                "LEFT JOIN mirrors m ON mic.mirror_id = m.id "
                "WHERE m.id IS NULL"
            ))
            orphan_count = result.scalar()
        except Exception:
            # Tables don't exist yet (fresh install), nothing to clean up
            return

        if not orphan_count:
            return

        logging.info(f"Cleaning up {orphan_count} orphaned issue sync config(s)")

        # Delete in child → parent order using raw SQL for efficiency.
        # Step 1: attachment_mappings via comment_mappings via issue_mappings
        await conn.execute(text("""
            DELETE FROM attachment_mappings WHERE comment_mapping_id IN (
                SELECT cm.id FROM comment_mappings cm
                JOIN issue_mappings im ON cm.issue_mapping_id = im.id
                JOIN mirror_issue_configs mic ON im.mirror_issue_config_id = mic.id
                LEFT JOIN mirrors m ON mic.mirror_id = m.id
                WHERE m.id IS NULL
            )
        """))

        # Step 2: attachment_mappings via issue_mappings directly
        await conn.execute(text("""
            DELETE FROM attachment_mappings WHERE issue_mapping_id IN (
                SELECT im.id FROM issue_mappings im
                JOIN mirror_issue_configs mic ON im.mirror_issue_config_id = mic.id
                LEFT JOIN mirrors m ON mic.mirror_id = m.id
                WHERE m.id IS NULL
            )
        """))

        # Step 3: comment_mappings
        await conn.execute(text("""
            DELETE FROM comment_mappings WHERE issue_mapping_id IN (
                SELECT im.id FROM issue_mappings im
                JOIN mirror_issue_configs mic ON im.mirror_issue_config_id = mic.id
                LEFT JOIN mirrors m ON mic.mirror_id = m.id
                WHERE m.id IS NULL
            )
        """))

        # Step 4: issue_mappings
        await conn.execute(text("""
            DELETE FROM issue_mappings WHERE mirror_issue_config_id IN (
                SELECT mic.id FROM mirror_issue_configs mic
                LEFT JOIN mirrors m ON mic.mirror_id = m.id
                WHERE m.id IS NULL
            )
        """))

        # Step 5: label_mappings
        await conn.execute(text("""
            DELETE FROM label_mappings WHERE mirror_issue_config_id IN (
                SELECT mic.id FROM mirror_issue_configs mic
                LEFT JOIN mirrors m ON mic.mirror_id = m.id
                WHERE m.id IS NULL
            )
        """))

        # Step 6: issue_sync_jobs
        await conn.execute(text("""
            DELETE FROM issue_sync_jobs WHERE mirror_issue_config_id IN (
                SELECT mic.id FROM mirror_issue_configs mic
                LEFT JOIN mirrors m ON mic.mirror_id = m.id
                WHERE m.id IS NULL
            )
        """))

        # Step 7: mirror_issue_configs
        await conn.execute(text("""
            DELETE FROM mirror_issue_configs WHERE mirror_id NOT IN (
                SELECT id FROM mirrors
            )
        """))

        logging.info("Orphaned issue sync data cleanup complete")


async def get_db() -> AsyncSession:
    """Dependency for getting database sessions."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def migrate_mirrors_to_auto_tokens():
    """
    Migrate existing mirrors to use automatic project access tokens.

    For each mirror without a token:
    1. Create a project access token on the remote project
    2. Update the mirror in GitLab with the authenticated URL
    3. Store the token info on the Mirror record

    This should be called once at startup. Mirrors that already have tokens are skipped.
    """
    import logging
    from datetime import datetime, timedelta
    from sqlalchemy import select
    from app.models import Mirror, InstancePair, GitLabInstance
    from app.core.gitlab_client import GitLabClient
    from app.core.encryption import encryption
    from urllib.parse import urlparse, quote

    TOKEN_EXPIRY_DAYS = 365

    async with AsyncSessionLocal() as db:
        # Find all mirrors without tokens
        result = await db.execute(
            select(Mirror).where(Mirror.encrypted_mirror_token.is_(None))
        )
        mirrors = result.scalars().all()

        if not mirrors:
            logging.info("No mirrors need token migration")
            return

        logging.info(f"Migrating {len(mirrors)} mirrors to automatic tokens")
        success_count = 0
        error_count = 0

        for mirror in mirrors:
            try:
                # Get instance pair
                pair_result = await db.execute(
                    select(InstancePair).where(InstancePair.id == mirror.instance_pair_id)
                )
                pair = pair_result.scalar_one_or_none()

                if not pair:
                    logging.warning(f"Mirror {mirror.id}: Instance pair not found, skipping")
                    error_count += 1
                    continue

                # Two-tier resolution: mirror → pair
                direction = mirror.mirror_direction or pair.mirror_direction

                # Get both instances
                source_result = await db.execute(
                    select(GitLabInstance).where(GitLabInstance.id == pair.source_instance_id)
                )
                source_instance = source_result.scalar_one_or_none()

                target_result = await db.execute(
                    select(GitLabInstance).where(GitLabInstance.id == pair.target_instance_id)
                )
                target_instance = target_result.scalar_one_or_none()

                if not source_instance or not target_instance:
                    logging.warning(f"Mirror {mirror.id}: Instances not found, skipping")
                    error_count += 1
                    continue

                # Determine which project gets the token
                if direction == "push":
                    # Push: source → target, token on TARGET
                    token_instance = target_instance
                    token_project_id = mirror.target_project_id
                    token_project_path = mirror.target_project_path
                    scopes = ["write_repository"]
                else:
                    # Pull: target ← source, token on SOURCE
                    token_instance = source_instance
                    token_project_id = mirror.source_project_id
                    token_project_path = mirror.source_project_path
                    scopes = ["read_repository"]

                # Create token
                token_name = f"mirror-maestro-{mirror.id}"
                expires_at = (datetime.utcnow() + timedelta(days=TOKEN_EXPIRY_DAYS)).strftime("%Y-%m-%d")

                token_client = GitLabClient(token_instance.url, token_instance.encrypted_token, timeout=settings.gitlab_api_timeout)
                token_result = token_client.create_project_access_token(
                    project_id=token_project_id,
                    name=token_name,
                    scopes=scopes,
                    expires_at=expires_at,
                    access_level=40,  # Maintainer
                )

                # Validate token response contains required fields
                token_value = token_result.get("token")
                token_id = token_result.get("id")
                if not token_value or token_id is None:
                    raise ValueError(f"GitLab token creation returned invalid response: missing 'token' or 'id'")

                # Store token info
                mirror.encrypted_mirror_token = encryption.encrypt(token_value)
                mirror.mirror_token_name = token_name
                mirror.mirror_token_expires_at = datetime.strptime(expires_at, "%Y-%m-%d")
                mirror.gitlab_token_id = token_id
                mirror.token_project_id = token_project_id

                # Update GitLab mirror with new authenticated URL if mirror exists
                if mirror.mirror_id:
                    # Build authenticated URL
                    parsed = urlparse(token_instance.url)
                    username = quote(token_name, safe="")
                    password = quote(token_value, safe="")
                    authenticated_url = f"{parsed.scheme}://{username}:{password}@{parsed.netloc}/{token_project_path}.git"

                    # Get mirror instance and update
                    if direction == "push":
                        mirror_instance = source_instance
                        mirror_project_id = mirror.source_project_id
                    else:
                        mirror_instance = target_instance
                        mirror_project_id = mirror.target_project_id

                    mirror_client = GitLabClient(mirror_instance.url, mirror_instance.encrypted_token, timeout=settings.gitlab_api_timeout)
                    try:
                        mirror_client.update_mirror(
                            project_id=mirror_project_id,
                            mirror_id=mirror.mirror_id,
                            url=authenticated_url,
                            enabled=True,
                        )
                    except Exception as e:
                        logging.warning(f"Mirror {mirror.id}: Failed to update GitLab mirror URL: {e}")
                        # Token is still saved so manual fix is possible

                await db.commit()
                success_count += 1
                logging.info(f"Mirror {mirror.id}: Token created successfully")

            except Exception as e:
                logging.error(f"Mirror {mirror.id}: Failed to create token: {e}")
                error_count += 1
                await db.rollback()

        logging.info(f"Token migration complete: {success_count} succeeded, {error_count} failed")


async def drop_legacy_group_tables():
    """
    Placeholder for backwards compatibility.

    This function previously cleaned up legacy SQLite tables.
    With PostgreSQL, this is no longer needed as we start fresh.
    """
    pass
