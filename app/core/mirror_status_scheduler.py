"""Background scheduler for automatic mirror status refresh.

Periodically fetches mirror statuses from GitLab to keep the dashboard
and mirror health metrics accurate without manual user intervention.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import Mirror


logger = logging.getLogger(__name__)


class MirrorStatusScheduler:
    """Background scheduler that periodically refreshes mirror statuses from GitLab.

    Uses the same rate limiting and circuit breaker infrastructure as manual
    status refreshes to avoid overwhelming GitLab instances.
    """

    def __init__(self):
        self.running = False
        self.task: Optional[asyncio.Task] = None
        self.shutdown_event = asyncio.Event()
        self._last_refresh_started_at: Optional[datetime] = None
        self._last_refresh_completed_at: Optional[datetime] = None
        self._last_refresh_total: int = 0
        self._last_refresh_success: int = 0
        self._last_refresh_errors: int = 0
        self._refreshing: bool = False

    async def start(self):
        """Start the scheduler."""
        if not settings.mirror_status_refresh_enabled:
            logger.info("Mirror status auto-refresh is disabled")
            return

        if self.running:
            logger.warning("Mirror status scheduler is already running")
            return

        self.running = True
        self.shutdown_event.clear()
        self.task = asyncio.create_task(self._run())
        logger.info(
            f"Mirror status scheduler started "
            f"(interval: {settings.mirror_status_refresh_interval_minutes}m)"
        )

    async def stop(self):
        """Stop the scheduler gracefully."""
        if not self.running:
            return

        logger.info("Stopping mirror status scheduler...")
        self.running = False
        self.shutdown_event.set()

        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

        logger.info("Mirror status scheduler stopped")

    async def _run(self):
        """Main scheduler loop."""
        # Initial delay to let the app finish starting up and avoid
        # competing with other startup tasks for GitLab API resources
        try:
            await asyncio.wait_for(
                self.shutdown_event.wait(),
                timeout=60,
            )
            return  # Shutdown requested during initial delay
        except asyncio.TimeoutError:
            pass  # Normal: initial delay elapsed

        while self.running:
            try:
                await self._refresh_cycle()
            except Exception as e:
                logger.error(f"Error in mirror status refresh cycle: {e}", exc_info=True)

            # Interruptible sleep until next cycle
            interval = settings.mirror_status_refresh_interval_minutes * 60
            try:
                await asyncio.wait_for(
                    self.shutdown_event.wait(),
                    timeout=interval,
                )
                break  # Shutdown requested
            except asyncio.TimeoutError:
                pass  # Normal: interval elapsed, run next cycle

    async def _refresh_cycle(self):
        """Run one refresh cycle for all mirrors."""
        # Import here to avoid circular imports at module level
        from app.api.mirrors import _refresh_mirror_status

        self._refreshing = True
        self._last_refresh_started_at = datetime.utcnow()

        try:
            async with AsyncSessionLocal() as db:
                # Get all mirrors, ordered by stalest first (oldest updated_at)
                result = await db.execute(
                    select(Mirror).order_by(Mirror.updated_at.asc().nullsfirst())
                )
                mirrors = result.scalars().all()

                if not mirrors:
                    self._last_refresh_completed_at = datetime.utcnow()
                    self._last_refresh_total = 0
                    self._last_refresh_success = 0
                    self._last_refresh_errors = 0
                    return

                success_count = 0
                error_count = 0
                skipped_count = 0

                for mirror in mirrors:
                    if not self.running:
                        logger.info(
                            "Mirror status refresh interrupted by shutdown "
                            f"({success_count + error_count}/{len(mirrors)} processed)"
                        )
                        break

                    try:
                        refresh_result = await _refresh_mirror_status(db, mirror)
                        if refresh_result.success:
                            success_count += 1
                        else:
                            # Not necessarily an error - mirror might not be
                            # created on GitLab yet, or pair may be missing
                            if "not been created" in (refresh_result.error or ""):
                                skipped_count += 1
                            else:
                                error_count += 1
                    except Exception as e:
                        error_count += 1
                        logger.warning(
                            f"Unexpected error refreshing mirror {mirror.id}: {e}"
                        )

                self._last_refresh_completed_at = datetime.utcnow()
                self._last_refresh_total = len(mirrors)
                self._last_refresh_success = success_count
                self._last_refresh_errors = error_count

                duration = (
                    self._last_refresh_completed_at - self._last_refresh_started_at
                ).total_seconds()

                log_parts = [
                    f"Mirror status refresh complete: "
                    f"{success_count}/{len(mirrors)} refreshed"
                ]
                if error_count:
                    log_parts.append(f"{error_count} errors")
                if skipped_count:
                    log_parts.append(f"{skipped_count} skipped")
                log_parts.append(f"in {duration:.1f}s")

                logger.info(", ".join(log_parts))

        finally:
            self._refreshing = False

    def get_status(self) -> dict:
        """Return scheduler status for API consumption."""
        return {
            "enabled": settings.mirror_status_refresh_enabled,
            "running": self.running,
            "refreshing": self._refreshing,
            "interval_minutes": settings.mirror_status_refresh_interval_minutes,
            "last_refresh_started_at": (
                self._last_refresh_started_at.isoformat() + "Z"
                if self._last_refresh_started_at
                else None
            ),
            "last_refresh_completed_at": (
                self._last_refresh_completed_at.isoformat() + "Z"
                if self._last_refresh_completed_at
                else None
            ),
            "last_refresh_total": self._last_refresh_total,
            "last_refresh_success": self._last_refresh_success,
            "last_refresh_errors": self._last_refresh_errors,
        }


# Global scheduler instance
mirror_status_scheduler = MirrorStatusScheduler()
