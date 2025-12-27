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
            # Token fields for automatic mirror authentication
            "encrypted_mirror_token": "TEXT",
            "mirror_token_name": "VARCHAR(100)",
            "mirror_token_expires_at": "DATETIME",
            "gitlab_token_id": "INTEGER",
            "token_project_id": "INTEGER",
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
        "mirrors": {
            "index_name": "uq_mirror_pair_projects",
            "columns": "(instance_pair_id, source_project_id, target_project_id)"
        }
    }

    for table, constraint in unique_constraints.items():
        existing_indexes = await _existing_indexes(table)
        if constraint["index_name"] not in existing_indexes:
            # Check if there are any duplicate rows before creating the unique index
            # If duplicates exist, we keep the most recent one
            if table == "mirrors":
                await conn.execute(text("""
                    DELETE FROM mirrors
                    WHERE id NOT IN (
                        SELECT MAX(id)
                        FROM mirrors
                        GROUP BY instance_pair_id, source_project_id, target_project_id
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

                token_client = GitLabClient(token_instance.url, token_instance.encrypted_token)
                token_result = token_client.create_project_access_token(
                    project_id=token_project_id,
                    name=token_name,
                    scopes=scopes,
                    expires_at=expires_at,
                    access_level=40,  # Maintainer
                )

                # Store token info
                mirror.encrypted_mirror_token = encryption.encrypt(token_result["token"])
                mirror.mirror_token_name = token_name
                mirror.mirror_token_expires_at = datetime.strptime(expires_at, "%Y-%m-%d")
                mirror.gitlab_token_id = token_result["id"]
                mirror.token_project_id = token_project_id

                # Update GitLab mirror with new authenticated URL if mirror exists
                if mirror.mirror_id:
                    # Build authenticated URL
                    parsed = urlparse(token_instance.url)
                    username = quote(token_name, safe="")
                    password = quote(token_result["token"], safe="")
                    authenticated_url = f"{parsed.scheme}://{username}:{password}@{parsed.netloc}/{token_project_path}.git"

                    # Get mirror instance and update
                    if direction == "push":
                        mirror_instance = source_instance
                        mirror_project_id = mirror.source_project_id
                    else:
                        mirror_instance = target_instance
                        mirror_project_id = mirror.target_project_id

                    mirror_client = GitLabClient(mirror_instance.url, mirror_instance.encrypted_token)
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
    Drop the legacy group_access_tokens and group_mirror_defaults tables.

    This should only be called after confirming all mirrors have been migrated
    to use automatic tokens.
    """
    import logging

    async with engine.begin() as conn:
        if conn.dialect.name != "sqlite":
            logging.info("Legacy table cleanup only supported for SQLite")
            return

        # Check if tables exist before dropping
        result = await conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('group_access_tokens', 'group_mirror_defaults')"
        ))
        tables = [row[0] for row in result.fetchall()]

        for table in tables:
            logging.info(f"Dropping legacy table: {table}")
            await conn.execute(text(f"DROP TABLE IF EXISTS {table}"))

        if tables:
            logging.info("Legacy group tables dropped successfully")
        else:
            logging.info("No legacy group tables to drop")
