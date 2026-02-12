"""API endpoints for managing issue mirror configurations."""

import asyncio
import logging
import threading
from datetime import datetime
from typing import List, Optional, Set
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, AsyncSessionLocal
from app.models import MirrorIssueConfig, Mirror
from app.core.auth import verify_credentials

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/issue-mirrors", tags=["issue-mirrors"])

# Track manual sync tasks globally for graceful shutdown
manual_sync_tasks: Set[asyncio.Task] = set()
_manual_sync_tasks_lock = threading.Lock()


async def wait_for_manual_syncs(timeout: int = 300):
    """
    Wait for all manual sync tasks to complete.

    Args:
        timeout: Maximum seconds to wait (default: 300).
    """
    import logging

    logger = logging.getLogger(__name__)

    # Take a snapshot under the lock to avoid race conditions
    with _manual_sync_tasks_lock:
        if not manual_sync_tasks:
            return
        tasks_snapshot = list(manual_sync_tasks)

    active_count = len(tasks_snapshot)
    logger.info(f"Waiting for {active_count} manual sync task(s) to complete (timeout: {timeout}s)...")

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*tasks_snapshot, return_exceptions=True),
            timeout=timeout
        )
        # Check for and log any exceptions from the gathered tasks
        exceptions = [r for r in results if isinstance(r, Exception)]
        if exceptions:
            for exc in exceptions:
                logger.error(f"Manual sync task exception during shutdown: {exc}")
            logger.warning(f"All manual sync tasks finished, but {len(exceptions)} task(s) raised exceptions")
        else:
            logger.info("All manual sync tasks completed gracefully")
    except asyncio.TimeoutError:
        remaining = [t for t in tasks_snapshot if not t.done()]
        logger.warning(
            f"Timeout waiting for manual sync tasks after {timeout}s. "
            f"{len(remaining)} task(s) may have been interrupted."
        )
        # Cancel remaining tasks
        for task in remaining:
            task.cancel()


# Pydantic Schemas

class MirrorIssueConfigCreate(BaseModel):
    """Schema for creating an issue mirror configuration."""
    mirror_id: int
    enabled: bool = True
    sync_comments: bool = True
    sync_labels: bool = True
    sync_attachments: bool = True
    sync_weight: bool = True
    sync_time_estimate: bool = True
    sync_time_spent: bool = True
    sync_closed_issues: bool = False
    update_existing: bool = True
    sync_existing_issues: bool = False
    sync_interval_minutes: int = Field(default=15, ge=5, le=1440)


class MirrorIssueConfigUpdate(BaseModel):
    """Schema for updating an issue mirror configuration."""
    enabled: Optional[bool] = None
    sync_comments: Optional[bool] = None
    sync_labels: Optional[bool] = None
    sync_attachments: Optional[bool] = None
    sync_weight: Optional[bool] = None
    sync_time_estimate: Optional[bool] = None
    sync_time_spent: Optional[bool] = None
    sync_closed_issues: Optional[bool] = None
    update_existing: Optional[bool] = None
    sync_existing_issues: Optional[bool] = None
    sync_interval_minutes: Optional[int] = Field(default=None, ge=5, le=1440)


class MirrorIssueConfigResponse(BaseModel):
    """Schema for issue mirror configuration response."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    mirror_id: int
    enabled: bool
    sync_comments: bool
    sync_labels: bool
    sync_attachments: bool
    sync_weight: bool
    sync_time_estimate: bool
    sync_time_spent: bool
    sync_closed_issues: bool
    update_existing: bool
    sync_existing_issues: bool
    last_sync_at: Optional[datetime]
    last_sync_status: Optional[str]
    last_sync_error: Optional[str]
    next_sync_at: Optional[datetime]
    sync_interval_minutes: int
    created_at: datetime
    updated_at: datetime


# API Endpoints

@router.get("", response_model=List[MirrorIssueConfigResponse])
async def list_issue_configs(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """List all issue mirror configurations."""
    result = await db.execute(select(MirrorIssueConfig))
    return result.scalars().all()


@router.get("/{config_id}", response_model=MirrorIssueConfigResponse)
async def get_issue_config(
    config_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Get a specific issue mirror configuration."""
    result = await db.execute(
        select(MirrorIssueConfig).where(MirrorIssueConfig.id == config_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(
            status_code=404,
            detail=f"Issue mirror configuration {config_id} not found"
        )
    return config


@router.get("/by-mirror/{mirror_id}", response_model=MirrorIssueConfigResponse)
async def get_issue_config_by_mirror(
    mirror_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Get issue mirror configuration for a specific mirror."""
    result = await db.execute(
        select(MirrorIssueConfig).where(MirrorIssueConfig.mirror_id == mirror_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(
            status_code=404,
            detail=f"No issue mirror configuration found for mirror {mirror_id}"
        )
    return config


@router.post("", response_model=MirrorIssueConfigResponse, status_code=201)
async def create_issue_config(
    config_data: MirrorIssueConfigCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Create a new issue mirror configuration."""
    # Verify mirror exists
    mirror_result = await db.execute(
        select(Mirror).where(Mirror.id == config_data.mirror_id)
    )
    mirror = mirror_result.scalar_one_or_none()
    if not mirror:
        raise HTTPException(
            status_code=404,
            detail=f"Mirror {config_data.mirror_id} not found"
        )

    # Check if configuration already exists
    existing_result = await db.execute(
        select(MirrorIssueConfig).where(
            MirrorIssueConfig.mirror_id == config_data.mirror_id
        )
    )
    existing = existing_result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Issue mirror configuration already exists for mirror {config_data.mirror_id}"
        )

    # Create new configuration
    config = MirrorIssueConfig(**config_data.model_dump())
    db.add(config)
    try:
        await db.commit()
        await db.refresh(config)
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to create issue mirror config: {type(e).__name__}")
        raise HTTPException(
            status_code=500,
            detail="Failed to create issue mirror configuration"
        )
    return config


@router.put("/{config_id}", response_model=MirrorIssueConfigResponse)
async def update_issue_config(
    config_id: int,
    config_data: MirrorIssueConfigUpdate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Update an issue mirror configuration."""
    result = await db.execute(
        select(MirrorIssueConfig).where(MirrorIssueConfig.id == config_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(
            status_code=404,
            detail=f"Issue mirror configuration {config_id} not found"
        )

    # Update only provided fields
    for field, value in config_data.model_dump(exclude_unset=True).items():
        setattr(config, field, value)

    try:
        await db.commit()
        await db.refresh(config)
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to update issue mirror config {config_id}: {type(e).__name__}")
        raise HTTPException(
            status_code=500,
            detail="Failed to update issue mirror configuration"
        )
    return config


@router.delete("/{config_id}", status_code=204)
async def delete_issue_config(
    config_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Delete an issue mirror configuration."""
    result = await db.execute(
        select(MirrorIssueConfig).where(MirrorIssueConfig.id == config_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(
            status_code=404,
            detail=f"Issue mirror configuration {config_id} not found"
        )

    await db.delete(config)
    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to delete issue mirror config {config_id}: {type(e).__name__}")
        raise HTTPException(
            status_code=500,
            detail="Failed to delete issue mirror configuration"
        )


@router.post("/{config_id}/trigger-sync", status_code=202)
async def trigger_sync(
    config_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Manually trigger an issue sync for a configuration."""
    from datetime import datetime, timedelta
    from app.models import IssueSyncJob, GitLabInstance, InstancePair
    from app.core.issue_sync import IssueSyncEngine
    import asyncio

    result = await db.execute(
        select(MirrorIssueConfig).where(MirrorIssueConfig.id == config_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(
            status_code=404,
            detail=f"Issue mirror configuration {config_id} not found"
        )

    if not config.enabled:
        raise HTTPException(
            status_code=400,
            detail="Cannot trigger sync for disabled configuration"
        )

    # Load related entities
    mirror_result = await db.execute(
        select(Mirror).where(Mirror.id == config.mirror_id)
    )
    mirror = mirror_result.scalar_one_or_none()
    if not mirror:
        raise HTTPException(
            status_code=404,
            detail=f"Mirror {config.mirror_id} not found"
        )

    pair_result = await db.execute(
        select(InstancePair).where(InstancePair.id == mirror.instance_pair_id)
    )
    pair = pair_result.scalar_one_or_none()
    if not pair:
        raise HTTPException(
            status_code=404,
            detail=f"Instance pair {mirror.instance_pair_id} not found"
        )

    # Issue sync always flows source → target, same as mirror direction.
    # mirror.source_project lives on pair.source_instance and
    # mirror.target_project lives on pair.target_instance for both push
    # and pull mirrors.
    source_instance_id = pair.source_instance_id
    target_instance_id = pair.target_instance_id

    source_instance_result = await db.execute(
        select(GitLabInstance).where(GitLabInstance.id == source_instance_id)
    )
    source_instance = source_instance_result.scalar_one_or_none()

    target_instance_result = await db.execute(
        select(GitLabInstance).where(GitLabInstance.id == target_instance_id)
    )
    target_instance = target_instance_result.scalar_one_or_none()

    if not source_instance or not target_instance:
        raise HTTPException(
            status_code=404,
            detail="Source or target instance not found"
        )

    # Check if there's already a running or pending sync for this config
    existing_job_result = await db.execute(
        select(IssueSyncJob).where(
            IssueSyncJob.mirror_issue_config_id == config.id,
            IssueSyncJob.status.in_(["pending", "running"])
        )
    )
    existing_job = existing_job_result.scalar_one_or_none()

    if existing_job:
        raise HTTPException(
            status_code=409,
            detail=f"Sync already in progress (job ID: {existing_job.id}, status: {existing_job.status})"
        )

    # Check for bidirectional sync conflict
    # This prevents A→B and B→A syncs from running simultaneously
    # Instance IDs are required because project IDs are only unique per GitLab instance
    from app.core.issue_scheduler import check_bidirectional_sync_conflict

    conflicting_job = await check_bidirectional_sync_conflict(
        db,
        source_project_id=mirror.source_project_id,
        target_project_id=mirror.target_project_id,
        source_instance_id=source_instance_id,
        target_instance_id=target_instance_id,
        exclude_config_id=config.id
    )

    if conflicting_job:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Bidirectional sync conflict: A reverse sync job (ID: {conflicting_job.id}) is in progress "
                f"syncing {conflicting_job.source_project_id}→{conflicting_job.target_project_id}. "
                f"Please wait for it to complete before triggering this sync."
            )
        )

    # Create sync job with project and instance tracking for conflict detection
    job = IssueSyncJob(
        mirror_issue_config_id=config.id,
        job_type="manual",
        status="pending",
        source_project_id=mirror.source_project_id,
        target_project_id=mirror.target_project_id,
        source_instance_id=source_instance_id,
        target_instance_id=target_instance_id,
    )
    db.add(job)
    try:
        await db.commit()
        await db.refresh(job)
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create sync job: database error"
        )

    # Trigger sync in background
    async def run_sync():
        import logging
        sync_logger = logging.getLogger(__name__)

        async with AsyncSessionLocal() as sync_db:
            sync_job = None
            sync_config = None
            try:
                # Update job status
                job_result = await sync_db.execute(
                    select(IssueSyncJob).where(IssueSyncJob.id == job.id)
                )
                sync_job = job_result.scalar_one_or_none()
                if sync_job is None:
                    sync_logger.warning(f"Sync job {job.id} was deleted, aborting sync")
                    return
                sync_job.status = "running"
                sync_job.started_at = datetime.utcnow()
                await sync_db.commit()

                # Reload config with new session
                config_result = await sync_db.execute(
                    select(MirrorIssueConfig).where(MirrorIssueConfig.id == config_id)
                )
                sync_config = config_result.scalar_one_or_none()
                if sync_config is None:
                    sync_logger.warning(f"Issue config {config_id} was deleted, aborting sync")
                    sync_job.status = "failed"
                    sync_job.completed_at = datetime.utcnow()
                    sync_job.error_details = {"error": "Issue config was deleted during sync"}
                    await sync_db.commit()
                    return

                # Reload mirror
                mirror_result = await sync_db.execute(
                    select(Mirror).where(Mirror.id == sync_config.mirror_id)
                )
                sync_mirror = mirror_result.scalar_one_or_none()
                if sync_mirror is None:
                    sync_logger.warning(f"Mirror {sync_config.mirror_id} was deleted, aborting sync")
                    sync_job.status = "failed"
                    sync_job.completed_at = datetime.utcnow()
                    sync_job.error_details = {"error": "Mirror was deleted during sync"}
                    await sync_db.commit()
                    return

                # Run sync
                engine = IssueSyncEngine(
                    db=sync_db,
                    config=sync_config,
                    mirror=sync_mirror,
                    source_instance=source_instance,
                    target_instance=target_instance,
                    instance_pair=pair,
                )

                stats = await engine.sync()

                # Update job with results
                sync_job.status = "completed"
                sync_job.completed_at = datetime.utcnow()
                sync_job.issues_processed = stats["issues_processed"]
                sync_job.issues_created = stats["issues_created"]
                sync_job.issues_updated = stats["issues_updated"]
                sync_job.issues_failed = stats["issues_failed"]
                if stats["errors"]:
                    sync_job.error_details = {"errors": stats["errors"]}

                # Update config scheduling fields
                # Note: last_sync_status and last_sync_error are already set by
                # the sync engine (which determined success/partial/failed based
                # on actual results). Do NOT overwrite them here.
                sync_config.last_sync_at = datetime.utcnow()
                sync_config.next_sync_at = datetime.utcnow() + timedelta(
                    minutes=sync_config.sync_interval_minutes
                )

                await sync_db.commit()

            except Exception as e:
                sync_logger.error(f"Sync failed for config {config_id}: {e}", exc_info=True)

                try:
                    # Update job as failed (if we have a reference to it)
                    if sync_job is not None:
                        sync_job.status = "failed"
                        sync_job.completed_at = datetime.utcnow()
                        sync_job.error_details = {"error": str(e)}

                    # Update config status (reload if needed)
                    if sync_config is None:
                        config_result = await sync_db.execute(
                            select(MirrorIssueConfig).where(MirrorIssueConfig.id == config_id)
                        )
                        sync_config = config_result.scalar_one_or_none()

                    if sync_config is not None:
                        sync_config.last_sync_at = datetime.utcnow()
                        sync_config.last_sync_status = "failed"
                        sync_config.last_sync_error = str(e)

                    await sync_db.commit()
                except Exception as inner_e:
                    sync_logger.error(f"Failed to update job/config status: {inner_e}")

    # Start background task and track it for graceful shutdown
    task = asyncio.create_task(run_sync())

    # Thread-safe add/remove from task set
    def remove_task(t):
        with _manual_sync_tasks_lock:
            manual_sync_tasks.discard(t)

    with _manual_sync_tasks_lock:
        manual_sync_tasks.add(task)
    task.add_done_callback(remove_task)

    return {
        "message": "Sync triggered",
        "config_id": config_id,
        "job_id": job.id
    }
