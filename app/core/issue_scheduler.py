"""Background scheduler for automatic issue mirroring."""

import asyncio
import logging
import threading
from datetime import datetime, timedelta
from typing import Optional, Set

from sqlalchemy import select, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import (
    MirrorIssueConfig,
    Mirror,
    GitLabInstance,
    InstancePair,
    IssueSyncJob,
)
from app.core.issue_sync import IssueSyncEngine


async def check_bidirectional_sync_conflict(
    db: AsyncSession,
    source_project_id: int,
    target_project_id: int,
    source_instance_id: int,
    target_instance_id: int,
    exclude_config_id: Optional[int] = None
) -> Optional[IssueSyncJob]:
    """
    Check if there's a running sync that conflicts with a bidirectional sync.

    A conflict occurs when:
    - There's a running sync FROM target TO source (reverse direction)
    - On the SAME GitLab instances (project IDs are only unique per instance)

    This prevents race conditions where A→B and B→A syncs run simultaneously,
    which could cause issues to be created/updated inconsistently.

    Args:
        db: Database session
        source_project_id: Source project ID for the sync we want to start
        target_project_id: Target project ID for the sync we want to start
        source_instance_id: Source GitLab instance ID
        target_instance_id: Target GitLab instance ID
        exclude_config_id: Optional config ID to exclude (for same-config checks)

    Returns:
        The conflicting job if found, None otherwise
    """
    # Check for reverse sync (target→source while we want source→target)
    # Must also match instance IDs since project IDs are only unique per instance
    query = select(IssueSyncJob).where(
        and_(
            IssueSyncJob.status.in_(["pending", "running"]),
            # Reverse direction: their source is our target, their target is our source
            IssueSyncJob.source_project_id == target_project_id,
            IssueSyncJob.target_project_id == source_project_id,
            # Also check instance IDs to ensure we're comparing same projects
            IssueSyncJob.source_instance_id == target_instance_id,
            IssueSyncJob.target_instance_id == source_instance_id
        )
    )

    if exclude_config_id is not None:
        query = query.where(IssueSyncJob.mirror_issue_config_id != exclude_config_id)

    result = await db.execute(query)
    return result.scalar_one_or_none()


logger = logging.getLogger(__name__)


async def cleanup_stale_jobs(db: AsyncSession) -> int:
    """
    Mark stale jobs as failed to prevent permanent sync blocking.

    Jobs that have been in 'running' or 'pending' status for longer than
    stale_job_timeout_minutes are considered stale (likely due to crashes
    or restarts) and are marked as failed.

    Args:
        db: Database session

    Returns:
        Number of stale jobs cleaned up
    """
    stale_threshold = datetime.utcnow() - timedelta(minutes=settings.stale_job_timeout_minutes)

    # Find stale jobs: running/pending jobs that started before the threshold
    # For pending jobs without started_at, use created_at
    stale_jobs_result = await db.execute(
        select(IssueSyncJob).where(
            and_(
                IssueSyncJob.status.in_(["pending", "running"]),
                or_(
                    # Running jobs with started_at before threshold
                    and_(
                        IssueSyncJob.status == "running",
                        IssueSyncJob.started_at != None,
                        IssueSyncJob.started_at < stale_threshold
                    ),
                    # Pending jobs created before threshold (stuck in queue)
                    and_(
                        IssueSyncJob.status == "pending",
                        IssueSyncJob.created_at < stale_threshold
                    )
                )
            )
        )
    )
    stale_jobs = stale_jobs_result.scalars().all()

    if not stale_jobs:
        return 0

    for job in stale_jobs:
        logger.warning(
            f"Marking stale job {job.id} as failed (config: {job.mirror_issue_config_id}, "
            f"status: {job.status}, started: {job.started_at}, created: {job.created_at})"
        )
        job.status = "failed"
        job.completed_at = datetime.utcnow()
        job.error_details = {
            "error": f"Job marked as stale after {settings.stale_job_timeout_minutes} minutes. "
                     f"This usually indicates the application crashed or restarted during sync."
        }

    await db.commit()
    logger.info(f"Cleaned up {len(stale_jobs)} stale sync job(s)")
    return len(stale_jobs)


class IssueScheduler:
    """Background scheduler for automatic issue syncing with graceful shutdown support."""

    def __init__(self):
        """Initialize scheduler."""
        self.running = False
        self.task: Optional[asyncio.Task] = None
        self.active_sync_tasks: Set[asyncio.Task] = set()
        self._active_sync_tasks_lock = threading.Lock()
        self.shutdown_event = asyncio.Event()

    async def start(self):
        """Start the scheduler."""
        if self.running:
            logger.warning("Scheduler is already running")
            return

        self.running = True
        self.task = asyncio.create_task(self._run())
        logger.info("Issue sync scheduler started")

    async def stop(self):
        """
        Stop the scheduler with graceful shutdown.

        Waits for active sync jobs to complete, up to sync_shutdown_timeout seconds.
        """
        if not self.running:
            return

        logger.info("Stopping issue sync scheduler (graceful shutdown)...")
        self.running = False

        # Signal shutdown to any running jobs
        self.shutdown_event.set()

        # Cancel the main scheduler task
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

        # Wait for active sync tasks to complete (take snapshot under lock)
        with self._active_sync_tasks_lock:
            if not self.active_sync_tasks:
                logger.info("Issue sync scheduler stopped")
                return
            tasks_snapshot = list(self.active_sync_tasks)

        active_count = len(tasks_snapshot)
        logger.info(f"Waiting for {active_count} active sync job(s) to complete (timeout: {settings.sync_shutdown_timeout}s)...")

        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks_snapshot, return_exceptions=True),
                timeout=settings.sync_shutdown_timeout
            )
            # Check for and log any exceptions from the gathered tasks
            exceptions = [r for r in results if isinstance(r, Exception)]
            if exceptions:
                for exc in exceptions:
                    logger.error(f"Sync task exception during shutdown: {exc}")
                logger.warning(f"All sync jobs finished, but {len(exceptions)} task(s) raised exceptions")
            else:
                logger.info("All sync jobs completed gracefully")
        except asyncio.TimeoutError:
            remaining = [t for t in tasks_snapshot if not t.done()]
            logger.warning(
                f"Timeout waiting for sync jobs to complete after {settings.sync_shutdown_timeout}s. "
                f"{len(remaining)} job(s) may have been interrupted."
            )
            # Cancel remaining tasks
            for task in remaining:
                task.cancel()

        logger.info("Issue sync scheduler stopped")

    async def _run(self):
        """Main scheduler loop."""
        while self.running:
            try:
                await self._check_and_sync()
            except Exception as e:
                logger.error(f"Error in scheduler loop: {e}", exc_info=True)

            # Sleep for 1 minute before next check
            await asyncio.sleep(60)

    async def _check_and_sync(self):
        """Check for configs that need syncing and trigger them."""
        async with AsyncSessionLocal() as db:
            # Clean up any stale jobs first to prevent permanent blocking
            await cleanup_stale_jobs(db)

            # Find configs that are enabled and due for sync
            now = datetime.utcnow()

            result = await db.execute(
                select(MirrorIssueConfig).where(
                    MirrorIssueConfig.enabled == True,
                    (MirrorIssueConfig.next_sync_at == None) |
                    (MirrorIssueConfig.next_sync_at <= now)
                )
            )
            configs = result.scalars().all()

            if not configs:
                return

            logger.info(f"Found {len(configs)} issue mirror configs due for sync")

            for config in configs:
                # Spawn sync as a background task and track it
                task = asyncio.create_task(self._sync_config_wrapper(config.id))

                # Thread-safe add/remove from task set
                def remove_task(t, lock=self._active_sync_tasks_lock, tasks=self.active_sync_tasks):
                    with lock:
                        tasks.discard(t)

                with self._active_sync_tasks_lock:
                    self.active_sync_tasks.add(task)
                task.add_done_callback(remove_task)

    async def _sync_config_wrapper(self, config_id: int):
        """Wrapper to sync a config with its own database session."""
        async with AsyncSessionLocal() as db:
            try:
                # Reload config in this session
                result = await db.execute(
                    select(MirrorIssueConfig).where(MirrorIssueConfig.id == config_id)
                )
                config = result.scalar_one_or_none()
                if config:
                    await self._sync_config(db, config)
            except Exception as e:
                logger.error(
                    f"Failed to sync issue mirror config {config_id}: {e}",
                    exc_info=True
                )

    async def _sync_config(self, db: AsyncSession, config: MirrorIssueConfig):
        """Sync a single issue mirror configuration."""
        logger.info(f"Starting sync for issue mirror config {config.id}")

        # Load mirror info first to get project IDs for conflict detection
        mirror_result = await db.execute(
            select(Mirror).where(Mirror.id == config.mirror_id)
        )
        mirror = mirror_result.scalar_one_or_none()

        if not mirror:
            logger.error(f"Mirror {config.mirror_id} not found for config {config.id}")
            return

        # Load instance pair early - needed for conflict detection with instance context
        pair_result = await db.execute(
            select(InstancePair).where(InstancePair.id == mirror.instance_pair_id)
        )
        pair = pair_result.scalar_one_or_none()

        if not pair:
            logger.error(f"Instance pair {mirror.instance_pair_id} not found for config {config.id}")
            return

        # Determine source and target instance IDs based on mirror direction
        if pair.mirror_direction == "pull":
            source_instance_id = pair.source_instance_id
            target_instance_id = pair.target_instance_id
        else:  # push
            source_instance_id = pair.target_instance_id
            target_instance_id = pair.source_instance_id

        # Check if there's already a running or pending sync for this config
        existing_job_result = await db.execute(
            select(IssueSyncJob).where(
                IssueSyncJob.mirror_issue_config_id == config.id,
                IssueSyncJob.status.in_(["pending", "running"])
            )
        )
        existing_job = existing_job_result.scalar_one_or_none()

        if existing_job:
            logger.info(
                f"Skipping sync for config {config.id} - already in progress "
                f"(job ID: {existing_job.id}, status: {existing_job.status})"
            )
            return

        # Check for bidirectional sync conflict
        # This prevents A→B and B→A syncs from running simultaneously
        # Instance IDs are required because project IDs are only unique per GitLab instance
        conflicting_job = await check_bidirectional_sync_conflict(
            db,
            source_project_id=mirror.source_project_id,
            target_project_id=mirror.target_project_id,
            source_instance_id=source_instance_id,
            target_instance_id=target_instance_id,
            exclude_config_id=config.id
        )

        if conflicting_job:
            logger.info(
                f"Skipping sync for config {config.id} - bidirectional sync conflict detected. "
                f"Reverse sync job {conflicting_job.id} is in progress "
                f"(syncing {conflicting_job.source_project_id}→{conflicting_job.target_project_id}). "
                f"Will retry on next schedule."
            )
            # Update next_sync_at to retry soon (in 1 minute)
            config.next_sync_at = datetime.utcnow() + timedelta(minutes=1)
            await db.commit()
            return

        # Create sync job with project and instance tracking for conflict detection
        job = IssueSyncJob(
            mirror_issue_config_id=config.id,
            job_type="scheduled",
            status="running",
            started_at=datetime.utcnow(),
            source_project_id=mirror.source_project_id,
            target_project_id=mirror.target_project_id,
            source_instance_id=source_instance_id,
            target_instance_id=target_instance_id,
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)

        try:
            source_instance_result = await db.execute(
                select(GitLabInstance).where(GitLabInstance.id == source_instance_id)
            )
            source_instance = source_instance_result.scalar_one_or_none()

            target_instance_result = await db.execute(
                select(GitLabInstance).where(GitLabInstance.id == target_instance_id)
            )
            target_instance = target_instance_result.scalar_one_or_none()

            if not source_instance or not target_instance:
                raise ValueError("Source or target instance not found")

            # Create sync engine and run sync
            engine = IssueSyncEngine(
                db=db,
                config=config,
                mirror=mirror,
                source_instance=source_instance,
                target_instance=target_instance,
                instance_pair=pair,
            )

            stats = await engine.sync()

            # Update job with results
            job.status = "completed"
            job.completed_at = datetime.utcnow()
            job.issues_processed = stats["issues_processed"]
            job.issues_created = stats["issues_created"]
            job.issues_updated = stats["issues_updated"]
            job.issues_failed = stats["issues_failed"]
            if stats["errors"]:
                job.error_details = {"errors": stats["errors"]}

            # Update config status
            config.last_sync_at = datetime.utcnow()
            config.last_sync_status = "success"
            config.last_sync_error = None
            config.next_sync_at = datetime.utcnow() + timedelta(
                minutes=config.sync_interval_minutes
            )

            await db.commit()

            logger.info(
                f"Completed sync for issue mirror config {config.id}: "
                f"{stats['issues_created']} created, {stats['issues_updated']} updated, "
                f"{stats['issues_failed']} failed"
            )

        except Exception as e:
            logger.error(f"Sync failed for issue mirror config {config.id}: {e}", exc_info=True)

            # Update job as failed
            job.status = "failed"
            job.completed_at = datetime.utcnow()
            job.error_details = {"error": str(e)}

            # Update config status
            config.last_sync_at = datetime.utcnow()
            config.last_sync_status = "failed"
            config.last_sync_error = str(e)
            config.next_sync_at = datetime.utcnow() + timedelta(
                minutes=config.sync_interval_minutes
            )

            await db.commit()


# Global scheduler instance
scheduler = IssueScheduler()
