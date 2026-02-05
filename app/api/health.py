"""Health check API for monitoring system status."""

from datetime import datetime, timedelta
from importlib.metadata import version, PackageNotFoundError
from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, case
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.database import get_db, engine
from app.models import GitLabInstance, InstancePair, Mirror
from app.core.auth import verify_credentials
from app.core.gitlab_client import GitLabClient
from app.core.rate_limiter import RateLimiter
from app.config import settings

# Get version from package metadata (same as main.py)
try:
    __version__ = version("mirror-maestro")
except PackageNotFoundError:
    __version__ = "1.0.0"  # Fallback for development


router = APIRouter(prefix="/api/health", tags=["health"])


class ComponentHealth(BaseModel):
    """Health status of a single component."""
    name: str
    status: str  # "healthy", "degraded", "unhealthy"
    message: Optional[str] = None
    latency_ms: Optional[float] = None


class MirrorHealthSummary(BaseModel):
    """Summary of mirror health statistics."""
    total: int
    enabled: int
    disabled: int
    success: int
    failed: int
    pending: int
    unknown: int
    health_percentage: float


class TokenHealthSummary(BaseModel):
    """Summary of token expiration status."""
    total_with_tokens: int
    active: int
    expiring_soon: int  # Within 30 days
    expired: int


class InstanceHealthDetail(BaseModel):
    """Health details for a GitLab instance."""
    id: int
    name: str
    url: str
    status: str  # "healthy", "unreachable", "auth_failed", "unknown"
    latency_ms: Optional[float] = None
    error: Optional[str] = None


class HealthResponse(BaseModel):
    """Complete health check response."""
    status: str  # "healthy", "degraded", "unhealthy"
    timestamp: str
    version: str
    components: List[ComponentHealth]
    mirrors: MirrorHealthSummary
    tokens: TokenHealthSummary
    instances: Optional[List[InstanceHealthDetail]] = None


class QuickHealthResponse(BaseModel):
    """Quick health check for load balancers."""
    status: str
    timestamp: str


@router.get("/quick", response_model=QuickHealthResponse)
async def quick_health():
    """
    Quick health check for load balancers and uptime monitors.

    Returns immediately without checking external dependencies.
    Use this endpoint for frequent polling.
    """
    return QuickHealthResponse(
        status="healthy",
        timestamp=datetime.utcnow().isoformat()
    )


@router.get("", response_model=HealthResponse)
async def detailed_health(
    check_instances: bool = Query(
        False,
        description="Whether to check GitLab instance connectivity (slower)"
    ),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """
    Detailed health check with component status.

    Returns health status of:
    - Database connectivity
    - Mirror sync status summary
    - Token expiration warnings
    - Optionally: GitLab instance connectivity

    Query parameters:
    - check_instances: If true, tests connectivity to each GitLab instance (slower)
    """
    components: List[ComponentHealth] = []
    overall_status = "healthy"

    # Check database
    db_health = await _check_database(db)
    components.append(db_health)
    if db_health.status == "unhealthy":
        overall_status = "unhealthy"

    # Get mirror health summary
    mirrors_summary = await _get_mirror_health(db)

    # Check for degraded mirror health
    if mirrors_summary.total > 0:
        if mirrors_summary.failed > 0:
            if mirrors_summary.health_percentage < 50:
                overall_status = "unhealthy"
            elif overall_status != "unhealthy":
                overall_status = "degraded"
            components.append(ComponentHealth(
                name="mirrors",
                status="degraded" if mirrors_summary.health_percentage >= 50 else "unhealthy",
                message=f"{mirrors_summary.failed} of {mirrors_summary.enabled} enabled mirrors failed"
            ))
        else:
            components.append(ComponentHealth(
                name="mirrors",
                status="healthy",
                message=f"All {mirrors_summary.success} synced mirrors healthy"
            ))
    else:
        components.append(ComponentHealth(
            name="mirrors",
            status="healthy",
            message="No mirrors configured"
        ))

    # Get token health summary
    tokens_summary = await _get_token_health(db)

    # Check for token warnings
    if tokens_summary.expired > 0:
        if overall_status != "unhealthy":
            overall_status = "degraded"
        components.append(ComponentHealth(
            name="tokens",
            status="unhealthy",
            message=f"{tokens_summary.expired} token(s) have expired"
        ))
    elif tokens_summary.expiring_soon > 0:
        if overall_status == "healthy":
            overall_status = "degraded"
        components.append(ComponentHealth(
            name="tokens",
            status="degraded",
            message=f"{tokens_summary.expiring_soon} token(s) expiring within 30 days"
        ))
    else:
        components.append(ComponentHealth(
            name="tokens",
            status="healthy",
            message="All tokens valid"
        ))

    # Optionally check GitLab instances
    instances_health: Optional[List[InstanceHealthDetail]] = None
    if check_instances:
        instances_health = await _check_instances(db)

        # Update overall status based on instance health
        unreachable = sum(1 for i in instances_health if i.status != "healthy")
        if unreachable > 0:
            if overall_status != "unhealthy":
                overall_status = "degraded"
            components.append(ComponentHealth(
                name="gitlab_instances",
                status="degraded",
                message=f"{unreachable} of {len(instances_health)} instance(s) unreachable"
            ))
        else:
            components.append(ComponentHealth(
                name="gitlab_instances",
                status="healthy",
                message=f"All {len(instances_health)} instance(s) reachable"
            ))

    return HealthResponse(
        status=overall_status,
        timestamp=datetime.utcnow().isoformat(),
        version=__version__,
        components=components,
        mirrors=mirrors_summary,
        tokens=tokens_summary,
        instances=instances_health
    )


async def _check_database(db: AsyncSession) -> ComponentHealth:
    """Check database connectivity."""
    import time
    start = time.perf_counter()

    try:
        # Simple query to test connectivity
        await db.execute(select(func.count(Mirror.id)))
        latency = (time.perf_counter() - start) * 1000

        return ComponentHealth(
            name="database",
            status="healthy",
            message="Connected",
            latency_ms=round(latency, 2)
        )
    except Exception as e:
        return ComponentHealth(
            name="database",
            status="unhealthy",
            message=f"Connection failed: {str(e)}"
        )


async def _get_mirror_health(db: AsyncSession) -> MirrorHealthSummary:
    """Get mirror health statistics."""
    result = await db.execute(
        select(
            func.count(Mirror.id).label('total'),
            func.count(case((Mirror.enabled == True, 1))).label('enabled'),
            func.count(case((Mirror.enabled == False, 1))).label('disabled'),
            func.count(case(((Mirror.last_update_status == 'success') | (Mirror.last_update_status == 'finished'), 1))).label('success'),
            func.count(case((Mirror.last_update_status == 'failed', 1))).label('failed'),
            func.count(case(((Mirror.last_update_status == 'pending') | (Mirror.last_update_status == 'started'), 1))).label('pending'),
            func.count(case((Mirror.last_update_status.is_(None), 1))).label('unknown'),
        )
    )
    row = result.one()

    enabled = row.enabled or 0
    success = row.success or 0

    # Calculate health percentage based on enabled mirrors that have succeeded
    if enabled > 0:
        # Only count mirrors that have synced at least once
        synced = success + (row.failed or 0)
        health_pct = (success / synced * 100) if synced > 0 else 100.0
    else:
        health_pct = 100.0

    return MirrorHealthSummary(
        total=row.total or 0,
        enabled=enabled,
        disabled=row.disabled or 0,
        success=success,
        failed=row.failed or 0,
        pending=row.pending or 0,
        unknown=row.unknown or 0,
        health_percentage=round(health_pct, 1)
    )


async def _get_token_health(db: AsyncSession) -> TokenHealthSummary:
    """Get token expiration statistics."""
    now = datetime.utcnow()
    soon = now + timedelta(days=30)

    result = await db.execute(
        select(
            func.count(case((Mirror.encrypted_mirror_token.isnot(None), 1))).label('total'),
            func.count(case((
                (Mirror.mirror_token_expires_at.isnot(None)) &
                (Mirror.mirror_token_expires_at > soon),
                1
            ))).label('active'),
            func.count(case((
                (Mirror.mirror_token_expires_at.isnot(None)) &
                (Mirror.mirror_token_expires_at > now) &
                (Mirror.mirror_token_expires_at <= soon),
                1
            ))).label('expiring_soon'),
            func.count(case((
                (Mirror.mirror_token_expires_at.isnot(None)) &
                (Mirror.mirror_token_expires_at <= now),
                1
            ))).label('expired'),
        )
    )
    row = result.one()

    return TokenHealthSummary(
        total_with_tokens=row.total or 0,
        active=row.active or 0,
        expiring_soon=row.expiring_soon or 0,
        expired=row.expired or 0
    )


async def _check_instances(db: AsyncSession) -> List[InstanceHealthDetail]:
    """
    Check connectivity to all GitLab instances with rate limiting.

    Uses configurable delays between instance checks to avoid overwhelming
    multiple GitLab instances simultaneously.
    """
    import time
    import logging

    logger = logging.getLogger(__name__)

    result = await db.execute(select(GitLabInstance))
    instances = list(result.scalars().all())

    health_results: List[InstanceHealthDetail] = []

    # Apply rate limiting if checking multiple instances
    if len(instances) > 1:
        rate_limiter = RateLimiter(
            delay_ms=settings.gitlab_api_delay_ms,
            max_retries=settings.gitlab_api_max_retries
        )
        logger.info(f"Checking {len(instances)} instances with rate limiting")
    else:
        rate_limiter = None

    for idx, instance in enumerate(instances):
        start = time.perf_counter()
        try:
            def check_connection():
                client = GitLabClient(instance.url, instance.encrypted_token, timeout=settings.gitlab_api_timeout)
                return client.test_connection()

            # Use retry logic if rate limiter is available
            if rate_limiter:
                await rate_limiter.execute_with_retry(
                    check_connection,
                    operation_name=f"check instance {instance.id}"
                )
            else:
                check_connection()

            latency = (time.perf_counter() - start) * 1000

            health_results.append(InstanceHealthDetail(
                id=instance.id,
                name=instance.name,
                url=instance.url,
                status="healthy",
                latency_ms=round(latency, 2)
            ))
        except Exception as e:
            error_msg = str(e).lower()
            if "auth" in error_msg or "401" in error_msg or "token" in error_msg:
                status = "auth_failed"
            elif "connection" in error_msg or "timeout" in error_msg or "unreachable" in error_msg:
                status = "unreachable"
            else:
                status = "unknown"

            health_results.append(InstanceHealthDetail(
                id=instance.id,
                name=instance.name,
                url=instance.url,
                status=status,
                error=str(e)[:200]  # Truncate long errors
            ))

        # Apply rate limiting delay (except after last instance)
        if rate_limiter and idx < len(instances) - 1:
            await rate_limiter.delay()

    return health_results
