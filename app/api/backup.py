"""
Database backup and restore endpoints.

This module provides functionality to backup and restore the entire Mirror Maestro
database and encryption key as a compressed archive.

Backups are stored as JSON exports of all tables, making them portable and
database-agnostic.
"""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)
from pathlib import Path
import json
import shutil
import tarfile
import tempfile
from typing import Dict, Any, List

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import verify_credentials
from app.core.encryption import encryption
from app.database import get_db, engine
from app.config import settings
from app.models import (
    GitLabInstance,
    InstancePair,
    Mirror,
    MirrorIssueConfig,
    IssueMapping,
    CommentMapping,
    LabelMapping,
    AttachmentMapping,
    IssueSyncJob
)

router = APIRouter(prefix="/api/backup", tags=["backup"])


def _get_encryption_key_path() -> Path:
    """Get path to encryption key file."""
    return Path(settings.encryption_key_path).resolve()


def _safe_tar_extract(tar: tarfile.TarFile, extract_path: Path) -> None:
    """
    Safely extract tar archive members, preventing path traversal attacks.

    Validates each member to ensure:
    - No absolute paths
    - No path traversal (../)
    - No symbolic links pointing outside extract directory
    - Final resolved path is within extract_path

    Args:
        tar: Open TarFile object to extract from
        extract_path: Directory to extract files into

    Raises:
        ValueError: If a member has a malicious path
    """
    extract_path = extract_path.resolve()

    for member in tar.getmembers():
        # Normalize and check the member path
        member_path = Path(member.name)

        # Reject absolute paths
        if member_path.is_absolute():
            raise ValueError(f"Absolute path in archive rejected: {member.name}")

        # Reject path traversal attempts
        if ".." in member_path.parts:
            raise ValueError(f"Path traversal attempt rejected: {member.name}")

        # Compute the final resolved path
        target_path = (extract_path / member_path).resolve()

        # Ensure the target is within the extract directory
        try:
            target_path.relative_to(extract_path)
        except ValueError:
            raise ValueError(f"Path escapes extract directory: {member.name}")

        # Reject symlinks that could point outside
        if member.issym() or member.islnk():
            link_target = Path(member.linkname)
            if link_target.is_absolute() or ".." in link_target.parts:
                raise ValueError(f"Suspicious symlink rejected: {member.name} -> {member.linkname}")

    # All members validated, now extract
    tar.extractall(extract_path)


def _model_to_dict(obj: Any) -> Dict:
    """Convert SQLAlchemy model instance to dictionary."""
    result = {}
    for column in obj.__table__.columns:
        value = getattr(obj, column.name)
        # Handle datetime serialization
        if hasattr(value, 'isoformat'):
            value = value.isoformat() + "Z"
        result[column.name] = value
    return result


async def _export_table_data(db: AsyncSession) -> Dict[str, List[Dict]]:
    """Export all table data as dictionaries."""
    data = {}

    # Export GitLab instances
    result = await db.execute(select(GitLabInstance).order_by(GitLabInstance.id))
    data['gitlab_instances'] = [_model_to_dict(row) for row in result.scalars().all()]

    # Export instance pairs
    result = await db.execute(select(InstancePair).order_by(InstancePair.id))
    data['instance_pairs'] = [_model_to_dict(row) for row in result.scalars().all()]

    # Export mirrors
    result = await db.execute(select(Mirror).order_by(Mirror.id))
    data['mirrors'] = [_model_to_dict(row) for row in result.scalars().all()]

    # Export issue mirroring configurations
    result = await db.execute(select(MirrorIssueConfig).order_by(MirrorIssueConfig.id))
    data['mirror_issue_configs'] = [_model_to_dict(row) for row in result.scalars().all()]

    # Export issue mappings
    result = await db.execute(select(IssueMapping).order_by(IssueMapping.id))
    data['issue_mappings'] = [_model_to_dict(row) for row in result.scalars().all()]

    # Export comment mappings
    result = await db.execute(select(CommentMapping).order_by(CommentMapping.id))
    data['comment_mappings'] = [_model_to_dict(row) for row in result.scalars().all()]

    # Export label mappings
    result = await db.execute(select(LabelMapping).order_by(LabelMapping.id))
    data['label_mappings'] = [_model_to_dict(row) for row in result.scalars().all()]

    # Export attachment mappings
    result = await db.execute(select(AttachmentMapping).order_by(AttachmentMapping.id))
    data['attachment_mappings'] = [_model_to_dict(row) for row in result.scalars().all()]

    # Export issue sync jobs
    result = await db.execute(select(IssueSyncJob).order_by(IssueSyncJob.id))
    data['issue_sync_jobs'] = [_model_to_dict(row) for row in result.scalars().all()]

    return data


async def _import_table_data(db: AsyncSession, data: Dict[str, List[Dict]]) -> Dict[str, int]:
    """Import data into tables, replacing existing data."""
    from datetime import datetime as dt
    counts = {}

    def _parse_naive_utc(value: str) -> datetime:
        """Parse ISO 8601 string and return a naive UTC datetime.

        Strips timezone info so the result can be stored in PostgreSQL
        TIMESTAMP WITHOUT TIME ZONE columns.
        """
        parsed = dt.fromisoformat(value)
        return parsed.replace(tzinfo=None)

    # Clear existing data (in reverse order due to foreign keys)
    await db.execute(text("DELETE FROM issue_sync_jobs"))
    await db.execute(text("DELETE FROM attachment_mappings"))
    await db.execute(text("DELETE FROM comment_mappings"))
    await db.execute(text("DELETE FROM label_mappings"))
    await db.execute(text("DELETE FROM issue_mappings"))
    await db.execute(text("DELETE FROM mirror_issue_configs"))
    await db.execute(text("DELETE FROM mirrors"))
    await db.execute(text("DELETE FROM instance_pairs"))
    await db.execute(text("DELETE FROM gitlab_instances"))

    # Import GitLab instances
    for row in data.get('gitlab_instances', []):
        # Parse datetime fields
        for field in ['created_at', 'updated_at']:
            if row.get(field) and isinstance(row[field], str):
                row[field] = _parse_naive_utc(row[field])
        instance = GitLabInstance(**row)
        db.add(instance)
    counts['gitlab_instances'] = len(data.get('gitlab_instances', []))

    # Import instance pairs
    for row in data.get('instance_pairs', []):
        for field in ['created_at', 'updated_at']:
            if row.get(field) and isinstance(row[field], str):
                row[field] = _parse_naive_utc(row[field])
        pair = InstancePair(**row)
        db.add(pair)
    counts['instance_pairs'] = len(data.get('instance_pairs', []))

    # Import mirrors
    for row in data.get('mirrors', []):
        for field in ['created_at', 'updated_at', 'last_successful_update', 'mirror_token_expires_at']:
            if row.get(field) and isinstance(row[field], str):
                row[field] = _parse_naive_utc(row[field])
        mirror = Mirror(**row)
        db.add(mirror)
    counts['mirrors'] = len(data.get('mirrors', []))

    # Import issue mirroring configurations
    for row in data.get('mirror_issue_configs', []):
        for field in ['created_at', 'updated_at', 'last_sync_at', 'next_sync_at']:
            if row.get(field) and isinstance(row[field], str):
                row[field] = _parse_naive_utc(row[field])
        config = MirrorIssueConfig(**row)
        db.add(config)
    counts['mirror_issue_configs'] = len(data.get('mirror_issue_configs', []))

    # Import issue mappings
    for row in data.get('issue_mappings', []):
        for field in ['created_at', 'updated_at', 'last_synced_at', 'source_updated_at', 'target_updated_at']:
            if row.get(field) and isinstance(row[field], str):
                row[field] = _parse_naive_utc(row[field])
        mapping = IssueMapping(**row)
        db.add(mapping)
    counts['issue_mappings'] = len(data.get('issue_mappings', []))

    # Import comment mappings
    for row in data.get('comment_mappings', []):
        for field in ['created_at', 'updated_at', 'last_synced_at']:
            if row.get(field) and isinstance(row[field], str):
                row[field] = _parse_naive_utc(row[field])
        mapping = CommentMapping(**row)
        db.add(mapping)
    counts['comment_mappings'] = len(data.get('comment_mappings', []))

    # Import label mappings
    for row in data.get('label_mappings', []):
        for field in ['created_at', 'updated_at']:
            if row.get(field) and isinstance(row[field], str):
                row[field] = _parse_naive_utc(row[field])
        mapping = LabelMapping(**row)
        db.add(mapping)
    counts['label_mappings'] = len(data.get('label_mappings', []))

    # Import attachment mappings
    for row in data.get('attachment_mappings', []):
        for field in ['created_at', 'uploaded_at']:
            if row.get(field) and isinstance(row[field], str):
                row[field] = _parse_naive_utc(row[field])
        mapping = AttachmentMapping(**row)
        db.add(mapping)
    counts['attachment_mappings'] = len(data.get('attachment_mappings', []))

    # Import issue sync jobs
    for row in data.get('issue_sync_jobs', []):
        for field in ['created_at', 'started_at', 'completed_at']:
            if row.get(field) and isinstance(row[field], str):
                row[field] = _parse_naive_utc(row[field])
        job = IssueSyncJob(**row)
        db.add(job)
    counts['issue_sync_jobs'] = len(data.get('issue_sync_jobs', []))

    await db.commit()

    # Reset sequences for PostgreSQL
    # Use a whitelist of valid table names to prevent SQL injection
    VALID_SEQUENCE_TABLES = frozenset({
        'gitlab_instances',
        'instance_pairs',
        'mirrors',
        'mirror_issue_configs',
        'issue_mappings',
        'comment_mappings',
        'label_mappings',
        'attachment_mappings',
        'issue_sync_jobs'
    })

    try:
        # Get max IDs and reset sequences
        for table, model in [
            ('gitlab_instances', GitLabInstance),
            ('instance_pairs', InstancePair),
            ('mirrors', Mirror),
            ('mirror_issue_configs', MirrorIssueConfig),
            ('issue_mappings', IssueMapping),
            ('comment_mappings', CommentMapping),
            ('label_mappings', LabelMapping),
            ('attachment_mappings', AttachmentMapping),
            ('issue_sync_jobs', IssueSyncJob)
        ]:
            # Validate table name against whitelist (defense in depth)
            if table not in VALID_SEQUENCE_TABLES:
                continue

            max_id_result = await db.execute(
                select(model.id).order_by(model.id.desc()).limit(1)
            )
            max_id = max_id_result.scalar() or 0

            # Table name is validated above; sequence name follows PostgreSQL convention
            sequence_name = f"{table}_id_seq"
            await db.execute(
                text(f"SELECT setval('{sequence_name}', :max_id, true)"),
                {"max_id": max_id}
            )
        await db.commit()
    except Exception as e:
        # Sequence reset is PostgreSQL-specific; log for debugging but continue
        logger.debug(f"Sequence reset skipped (not PostgreSQL or not supported): {e}")

    return counts


@router.get("/create")
async def create_backup(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
) -> Response:
    """
    Create and download a complete backup of the database and encryption key.

    Returns a compressed tar.gz archive containing:
    - database.json: All database data as JSON
    - encryption.key: Fernet encryption key
    - backup_metadata.json: Backup information

    The backup format is database-agnostic and can be restored to any
    supported database (PostgreSQL).

    ⚠️  WARNING: The backup file contains sensitive data including the encryption
    key which can decrypt all stored GitLab tokens. Store securely!
    """
    key_path = _get_encryption_key_path()

    # Export database data
    db_data = await _export_table_data(db)

    # Get encryption key content
    if key_path.exists():
        key_content = key_path.read_bytes()
    else:
        # In test environments, use placeholder
        if settings.encryption_key_env:
            key_content = settings.encryption_key_env.encode()
        else:
            key_content = b"test-encryption-key-placeholder"

    # Create temporary directory for staging
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Write database JSON
        db_file = temp_path / "database.json"
        db_file.write_text(json.dumps(db_data, indent=2, default=str))

        # Write encryption key
        key_file = temp_path / "encryption.key"
        key_file.write_bytes(key_content)

        # Create metadata file
        metadata = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "version": "2.1",  # Updated version for issue mirroring support
            "format": "json",
            "database_type": "postgresql",
            "app_version": settings.app_title,
            "record_counts": {
                "gitlab_instances": len(db_data.get('gitlab_instances', [])),
                "instance_pairs": len(db_data.get('instance_pairs', [])),
                "mirrors": len(db_data.get('mirrors', [])),
                "mirror_issue_configs": len(db_data.get('mirror_issue_configs', [])),
                "issue_mappings": len(db_data.get('issue_mappings', [])),
                "comment_mappings": len(db_data.get('comment_mappings', [])),
                "label_mappings": len(db_data.get('label_mappings', [])),
                "attachment_mappings": len(db_data.get('attachment_mappings', [])),
                "issue_sync_jobs": len(db_data.get('issue_sync_jobs', []))
            },
            "files": ["database.json", "encryption.key"]
        }

        metadata_file = temp_path / "backup_metadata.json"
        metadata_file.write_text(json.dumps(metadata, indent=2))

        # Create tar.gz archive
        timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        archive_name = f"mirror-maestro-backup-{timestamp}.tar.gz"
        archive_path = temp_path / archive_name

        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(db_file, arcname="database.json")
            tar.add(key_file, arcname="encryption.key")
            tar.add(metadata_file, arcname="backup_metadata.json")

        # Read the archive into memory before temp dir is cleaned up
        archive_bytes = archive_path.read_bytes()

        return Response(
            content=archive_bytes,
            media_type="application/gzip",
            headers={
                "Content-Disposition": f'attachment; filename="{archive_name}"',
                "Cache-Control": "no-cache"
            }
        )


def _validate_backup_archive(archive_path: Path) -> Dict:
    """
    Validate backup archive and extract metadata.

    Supports both v1 (SQLite file) and v2 (JSON) backup formats.
    """
    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            members = tar.getnames()

            # Check for required files
            # v2 format: database.json + encryption.key
            # v1 format: mirrors.db + encryption.key (legacy, no longer supported for restore)
            has_v2 = "database.json" in members
            has_v1 = "mirrors.db" in members
            has_key = "encryption.key" in members

            if not has_key:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid backup archive. Missing encryption.key"
                )

            if not has_v2 and not has_v1:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid backup archive. Missing database file (database.json)"
                )

            if has_v1 and not has_v2:
                raise HTTPException(
                    status_code=400,
                    detail="This backup is from an older SQLite-based version and cannot be restored. "
                           "Please create a new backup from the current version."
                )

            # Extract metadata if present
            metadata = {}
            if "backup_metadata.json" in members:
                metadata_file = tar.extractfile("backup_metadata.json")
                if metadata_file:
                    try:
                        metadata = json.loads(metadata_file.read().decode())
                    finally:
                        metadata_file.close()

            return {
                "valid": True,
                "format": "v2" if has_v2 else "v1",
                "files": members,
                "metadata": metadata
            }

    except tarfile.TarError as e:
        logger.error(f"Tar archive error during validation: {e}")
        raise HTTPException(
            status_code=400,
            detail="Invalid or corrupt backup archive. Please check the file format."
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error during backup validation: {e}", exc_info=True)
        raise HTTPException(
            status_code=400,
            detail="Failed to validate backup. Please check the file format and try again."
        )


@router.post("/restore")
async def restore_backup(
    file: UploadFile = File(...),
    create_backup_first: bool = Form(default=True),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
) -> Dict:
    """
    Restore database and encryption key from a backup archive.

    Args:
        file: Backup tar.gz file
        create_backup_first: If True, creates a backup before restoring

    Returns:
        Dict with restoration status and details

    ⚠️  WARNING: This will REPLACE all current data including:
    - All GitLab instances
    - All instance pairs
    - All mirrors
    - The encryption key
    """
    # Validate file extension
    if not file.filename or not file.filename.endswith(".tar.gz"):
        raise HTTPException(
            status_code=400,
            detail="Invalid file type. Please upload a .tar.gz backup file."
        )

    key_path = _get_encryption_key_path()

    # Create temporary directory for extraction
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Save uploaded file
        upload_path = temp_path / "uploaded_backup.tar.gz"
        with open(upload_path, "wb") as f:
            content = await file.read()
            f.write(content)

        # Validate archive
        validation = _validate_backup_archive(upload_path)

        # Create backup of current state if requested
        pre_restore_backup = None
        if create_backup_first:
            try:
                # Export current data
                current_data = await _export_table_data(db)

                # Create backup archive
                timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
                pre_restore_name = f"pre-restore-{timestamp}.tar.gz"
                pre_restore_path = Path("./data") / pre_restore_name

                pre_backup_dir = temp_path / "pre_backup"
                pre_backup_dir.mkdir()

                # Write current data
                (pre_backup_dir / "database.json").write_text(
                    json.dumps(current_data, indent=2, default=str)
                )
                if key_path.exists():
                    shutil.copy2(key_path, pre_backup_dir / "encryption.key")

                with tarfile.open(pre_restore_path, "w:gz") as tar:
                    tar.add(pre_backup_dir / "database.json", arcname="database.json")
                    if (pre_backup_dir / "encryption.key").exists():
                        tar.add(pre_backup_dir / "encryption.key", arcname="encryption.key")

                pre_restore_backup = str(pre_restore_path)
            except Exception as e:
                # Log but don't fail - user explicitly requested restore
                logging.warning(f"Failed to create pre-restore backup: {e}")

        # Extract backup files
        extract_path = temp_path / "extracted"
        extract_path.mkdir()

        with tarfile.open(upload_path, "r:gz") as tar:
            try:
                _safe_tar_extract(tar, extract_path)
            except ValueError as e:
                logger.error(f"Security validation failed during extraction: {e}")
                raise HTTPException(
                    status_code=400,
                    detail="Invalid backup archive: security validation failed."
                )

        # Load and validate database JSON
        db_json_path = extract_path / "database.json"
        try:
            db_data = json.loads(db_json_path.read_text())

            # Basic validation
            required_tables = ['gitlab_instances', 'instance_pairs', 'mirrors']
            missing_tables = [t for t in required_tables if t not in db_data]
            if missing_tables:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid backup. Missing tables: {', '.join(missing_tables)}"
                )
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error in database backup: {e}")
            raise HTTPException(
                status_code=400,
                detail="Invalid or corrupt database backup: JSON parsing failed."
            )

        # Import data into database
        try:
            counts = await _import_table_data(db, db_data)
        except Exception as e:
            logger.error(f"Failed to restore database: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail="Failed to restore database. Please check the backup file integrity and try again."
            )

        # Restore encryption key
        restored_key = extract_path / "encryption.key"
        if restored_key.exists():
            # Ensure data directory exists
            key_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(restored_key, key_path)

            # Reload encryption module
            encryption._initialize()

        return {
            "success": True,
            "message": "Backup restored successfully",
            "metadata": validation.get("metadata", {}),
            "pre_restore_backup": pre_restore_backup,
            "restored_files": validation.get("files", []),
            "imported_counts": counts
        }


@router.get("/stats")
async def get_backup_stats(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
) -> Dict:
    """
    Get current database statistics for backup display.

    Returns counts of instances, pairs, mirrors, and issue mirroring data.
    """
    from sqlalchemy import func

    # Get counts
    instance_count = await db.scalar(select(func.count()).select_from(GitLabInstance))
    pair_count = await db.scalar(select(func.count()).select_from(InstancePair))
    mirror_count = await db.scalar(select(func.count()).select_from(Mirror))

    # Get issue mirroring counts
    issue_config_count = await db.scalar(select(func.count()).select_from(MirrorIssueConfig))
    issue_mapping_count = await db.scalar(select(func.count()).select_from(IssueMapping))
    comment_mapping_count = await db.scalar(select(func.count()).select_from(CommentMapping))
    label_mapping_count = await db.scalar(select(func.count()).select_from(LabelMapping))
    attachment_mapping_count = await db.scalar(select(func.count()).select_from(AttachmentMapping))
    sync_job_count = await db.scalar(select(func.count()).select_from(IssueSyncJob))

    # Get database size (PostgreSQL specific)
    try:
        result = await db.execute(text("SELECT pg_database_size(current_database())"))
        db_size = result.scalar() or 0
    except Exception as e:
        # Not PostgreSQL or function not available; log and continue with 0
        logger.debug(f"Could not retrieve database size (not PostgreSQL or not supported): {e}")
        db_size = 0

    return {
        "instances": instance_count or 0,
        "pairs": pair_count or 0,
        "mirrors": mirror_count or 0,
        "mirror_issue_configs": issue_config_count or 0,
        "issue_mappings": issue_mapping_count or 0,
        "comment_mappings": comment_mapping_count or 0,
        "label_mappings": label_mapping_count or 0,
        "attachment_mappings": attachment_mapping_count or 0,
        "issue_sync_jobs": sync_job_count or 0,
        "database_size_bytes": db_size,
        "database_size_mb": round(db_size / (1024 * 1024), 2)
    }
