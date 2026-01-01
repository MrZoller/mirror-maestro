"""Background scheduler for automatic issue mirroring."""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models import (
    MirrorIssueConfig,
    Mirror,
    GitLabInstance,
    InstancePair,
    IssueSyncJob,
)
from app.core.issue_sync import IssueSyncEngine


logger = logging.getLogger(__name__)


class IssueScheduler:
    """Background scheduler for automatic issue syncing."""

    def __init__(self):
        """Initialize scheduler."""
        self.running = False
        self.task: Optional[asyncio.Task] = None

    async def start(self):
        """Start the scheduler."""
        if self.running:
            logger.warning("Scheduler is already running")
            return

        self.running = True
        self.task = asyncio.create_task(self._run())
        logger.info("Issue sync scheduler started")

    async def stop(self):
        """Stop the scheduler."""
        if not self.running:
            return

        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
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
                try:
                    await self._sync_config(db, config)
                except Exception as e:
                    logger.error(
                        f"Failed to sync issue mirror config {config.id}: {e}",
                        exc_info=True
                    )

    async def _sync_config(self, db: AsyncSession, config: MirrorIssueConfig):
        """Sync a single issue mirror configuration."""
        logger.info(f"Starting sync for issue mirror config {config.id}")

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

        # Create sync job
        job = IssueSyncJob(
            mirror_issue_config_id=config.id,
            job_type="scheduled",
            status="running",
            started_at=datetime.utcnow(),
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)

        try:
            # Load related entities
            mirror_result = await db.execute(
                select(Mirror).where(Mirror.id == config.mirror_id)
            )
            mirror = mirror_result.scalar_one_or_none()

            if not mirror:
                raise ValueError(f"Mirror {config.mirror_id} not found")

            pair_result = await db.execute(
                select(InstancePair).where(InstancePair.id == mirror.instance_pair_id)
            )
            pair = pair_result.scalar_one_or_none()

            if not pair:
                raise ValueError(f"Instance pair {mirror.instance_pair_id} not found")

            # Determine source and target based on mirror direction
            if pair.mirror_direction == "pull":
                source_instance_id = pair.source_instance_id
                target_instance_id = pair.target_instance_id
            else:  # push
                source_instance_id = pair.target_instance_id
                target_instance_id = pair.source_instance_id

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
