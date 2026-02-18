from datetime import datetime, timedelta
from typing import List, Optional
import re
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, delete, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.database import get_db
from app.models import InstancePair, GitLabInstance, Mirror, MirrorIssueConfig
from app.core.auth import verify_credentials
from app.api.mirrors import _delete_issue_sync_data_for_mirrors
from app.core.gitlab_client import GitLabClient
from app.core.rate_limiter import RateLimiter, BatchOperationTracker
from app.config import settings

logger = logging.getLogger(__name__)


def _is_token_expired(expires_at: datetime | None) -> bool:
    """Check if a token has expired based on its expiration date."""
    if expires_at is None:
        return False
    return expires_at <= datetime.utcnow()


# Maximum allowed regex pattern length to prevent resource exhaustion
MAX_REGEX_LENGTH = 500


def _validate_regex_safety(pattern: str) -> None:
    """
    Validate a regex pattern for safety against ReDoS attacks.

    Checks for:
    - Maximum pattern length
    - Nested quantifiers that can cause catastrophic backtracking
    - Valid regex syntax

    Raises:
        ValueError: If pattern is unsafe or invalid
    """
    if len(pattern) > MAX_REGEX_LENGTH:
        raise ValueError(f"Regex pattern too long (max {MAX_REGEX_LENGTH} characters)")

    # Check for common ReDoS patterns (nested quantifiers)
    # Patterns like (a+)+, (a*)+, (a+)*, etc. can cause catastrophic backtracking
    redos_patterns = [
        r'\([^)]*[+*][^)]*\)[+*]',  # (something+)+ or (something*)* etc
        r'\([^)]*\|[^)]*\)[+*]',    # (a|b)+ with alternation can be problematic
    ]
    for danger_pattern in redos_patterns:
        if re.search(danger_pattern, pattern):
            raise ValueError(
                "Regex pattern contains potentially dangerous nested quantifiers. "
                "Please simplify the pattern to avoid performance issues."
            )


router = APIRouter(prefix="/api/pairs", tags=["pairs"])


class InstancePairCreate(BaseModel):
    name: str
    source_instance_id: int
    target_instance_id: int
    mirror_direction: str = "pull"
    mirror_overwrite_diverged: bool = False
    mirror_trigger_builds: bool = False
    only_mirror_protected_branches: bool = False
    mirror_branch_regex: str | None = None
    issue_sync_enabled: bool = False
    description: str = ""

    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        """Validate pair name is not empty and has reasonable length."""
        if not v or not v.strip():
            raise ValueError("Pair name cannot be empty")
        v = v.strip()
        if len(v) > 200:
            raise ValueError("Pair name must be 200 characters or less")
        return v

    @field_validator('mirror_direction')
    @classmethod
    def validate_direction(cls, v):
        """Validate mirror direction is either 'push' or 'pull'."""
        if v is not None and v.lower() not in ('push', 'pull'):
            raise ValueError("Mirror direction must be 'push' or 'pull'")
        return v.lower() if v else None

    @field_validator('mirror_branch_regex')
    @classmethod
    def validate_branch_regex(cls, v):
        """Validate that branch regex is valid and safe."""
        if v is not None and v.strip():
            # Check for ReDoS vulnerabilities first
            _validate_regex_safety(v)
            # Then verify it compiles
            try:
                re.compile(v)
            except re.error as e:
                raise ValueError(f"Invalid regex pattern: {str(e)}")
        return v

    @field_validator('source_instance_id', 'target_instance_id')
    @classmethod
    def validate_instance_ids(cls, v):
        """Validate instance IDs are positive."""
        if v <= 0:
            raise ValueError("Instance ID must be a positive integer")
        return v

    @field_validator('description')
    @classmethod
    def validate_description(cls, v):
        """Validate description length."""
        if v and len(v) > 500:
            raise ValueError("Description must be 500 characters or less")
        return v.strip() if v else ""

    @model_validator(mode='after')
    def validate_not_self_referential(self):
        """Validate that source and target instances are different."""
        if self.source_instance_id == self.target_instance_id:
            raise ValueError(
                "Source and target instances must be different. "
                "A pair cannot mirror an instance to itself."
            )
        return self


class InstancePairUpdate(BaseModel):
    name: str | None = None
    source_instance_id: int | None = None
    target_instance_id: int | None = None
    mirror_direction: str | None = None
    mirror_overwrite_diverged: bool | None = None
    mirror_trigger_builds: bool | None = None
    only_mirror_protected_branches: bool | None = None
    mirror_branch_regex: str | None = None
    issue_sync_enabled: bool | None = None
    description: str | None = None

    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        """Validate pair name if provided."""
        if v is not None:
            if not v.strip():
                raise ValueError("Pair name cannot be empty")
            v = v.strip()
            if len(v) > 200:
                raise ValueError("Pair name must be 200 characters or less")
        return v

    @field_validator('mirror_direction')
    @classmethod
    def validate_direction(cls, v):
        """Validate mirror direction if provided."""
        if v is not None and v.lower() not in ('push', 'pull'):
            raise ValueError("Mirror direction must be 'push' or 'pull'")
        return v.lower() if v else None

    @field_validator('mirror_branch_regex')
    @classmethod
    def validate_branch_regex(cls, v):
        """Validate that branch regex is valid and safe if provided."""
        if v is not None and v.strip():
            # Check for ReDoS vulnerabilities first
            _validate_regex_safety(v)
            # Then verify it compiles
            try:
                re.compile(v)
            except re.error as e:
                raise ValueError(f"Invalid regex pattern: {str(e)}")
        return v

    @field_validator('source_instance_id', 'target_instance_id')
    @classmethod
    def validate_instance_ids(cls, v):
        """Validate instance IDs are positive if provided."""
        if v is not None and v <= 0:
            raise ValueError("Instance ID must be a positive integer")
        return v

    @field_validator('description')
    @classmethod
    def validate_description(cls, v):
        """Validate description length if provided."""
        if v is not None and len(v) > 500:
            raise ValueError("Description must be 500 characters or less")
        return v.strip() if v else None


class InstancePairResponse(BaseModel):
    id: int
    name: str
    source_instance_id: int
    target_instance_id: int
    mirror_direction: str
    mirror_overwrite_diverged: bool
    mirror_trigger_builds: bool
    only_mirror_protected_branches: bool
    mirror_branch_regex: str | None
    issue_sync_enabled: bool
    description: str | None
    created_at: str
    updated_at: str
    # Optional warnings for bidirectional mirroring scenarios
    warnings: Optional[List[str]] = None
    reverse_pair_id: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


@router.get("", response_model=List[InstancePairResponse])
async def list_pairs(
    search: str | None = Query(default=None, max_length=500),
    direction: str | None = Query(default=None, pattern="^(push|pull)$"),
    source_instance_id: int | None = None,
    target_instance_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """
    List all instance pairs with optional filtering.

    Query parameters:
    - search: Search in pair name and description (case-insensitive)
    - direction: Filter by mirror direction ('push' or 'pull')
    - source_instance_id: Filter by source instance ID
    - target_instance_id: Filter by target instance ID
    """
    query = select(InstancePair)

    if search is not None and search.strip():
        search_term = f"%{search.strip().lower()}%"
        query = query.where(
            (InstancePair.name.ilike(search_term)) |
            (InstancePair.description.ilike(search_term))
        )

    if direction is not None and direction.strip():
        query = query.where(InstancePair.mirror_direction == direction.strip().lower())

    if source_instance_id is not None:
        query = query.where(InstancePair.source_instance_id == source_instance_id)

    if target_instance_id is not None:
        query = query.where(InstancePair.target_instance_id == target_instance_id)

    result = await db.execute(query)
    pairs = result.scalars().all()
    return [
        InstancePairResponse(
            id=pair.id,
            name=pair.name,
            source_instance_id=pair.source_instance_id,
            target_instance_id=pair.target_instance_id,
            mirror_direction=pair.mirror_direction,
            mirror_overwrite_diverged=pair.mirror_overwrite_diverged,
            mirror_trigger_builds=pair.mirror_trigger_builds,
            only_mirror_protected_branches=pair.only_mirror_protected_branches,
            mirror_branch_regex=pair.mirror_branch_regex,
            issue_sync_enabled=pair.issue_sync_enabled,
            description=pair.description,
            created_at=pair.created_at.isoformat() + "Z",
            updated_at=pair.updated_at.isoformat() + "Z"
        )
        for pair in pairs
    ]


@router.post("", response_model=InstancePairResponse, status_code=201)
async def create_pair(
    pair: InstancePairCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Create a new instance pair."""
    # Check if pair with same name already exists
    existing_result = await db.execute(
        select(InstancePair).where(InstancePair.name == pair.name)
    )
    if existing_result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Instance pair with name '{pair.name}' already exists. Please choose a different name."
        )

    # Validate that both instances exist
    source_result = await db.execute(
        select(GitLabInstance).where(GitLabInstance.id == pair.source_instance_id)
    )
    source = source_result.scalar_one_or_none()

    target_result = await db.execute(
        select(GitLabInstance).where(GitLabInstance.id == pair.target_instance_id)
    )
    target = target_result.scalar_one_or_none()

    if not source:
        raise HTTPException(status_code=404, detail="Source instance not found")
    if not target:
        raise HTTPException(status_code=404, detail="Target instance not found")

    # Check for reverse pair (bidirectional mirroring scenario)
    reverse_pair_result = await db.execute(
        select(InstancePair).where(
            and_(
                InstancePair.source_instance_id == pair.target_instance_id,
                InstancePair.target_instance_id == pair.source_instance_id
            )
        )
    )
    reverse_pair = reverse_pair_result.scalar_one_or_none()

    warnings = []
    reverse_pair_id = None
    if reverse_pair:
        reverse_pair_id = reverse_pair.id
        warnings.append(
            f"Bidirectional mirroring detected: A reverse pair '{reverse_pair.name}' "
            f"(ID: {reverse_pair.id}) already exists between these instances. "
            f"This creates bidirectional mirroring where changes flow both directions. "
            f"For issue syncing, last-write-wins semantics apply. "
            f"Consider using one instance as the source of truth for agile planning."
        )
        logger.info(
            f"Creating bidirectional pair: new pair ({pair.source_instance_id}→{pair.target_instance_id}) "
            f"is reverse of existing pair {reverse_pair.id} ({reverse_pair.source_instance_id}→{reverse_pair.target_instance_id})"
        )

    # Create the pair
    db_pair = InstancePair(
        name=pair.name,
        source_instance_id=pair.source_instance_id,
        target_instance_id=pair.target_instance_id,
        mirror_direction=pair.mirror_direction,
        mirror_overwrite_diverged=pair.mirror_overwrite_diverged,
        mirror_trigger_builds=pair.mirror_trigger_builds,
        only_mirror_protected_branches=pair.only_mirror_protected_branches,
        mirror_branch_regex=pair.mirror_branch_regex,
        issue_sync_enabled=pair.issue_sync_enabled,
        description=pair.description
    )
    db.add(db_pair)
    await db.commit()
    await db.refresh(db_pair)

    return InstancePairResponse(
        id=db_pair.id,
        name=db_pair.name,
        source_instance_id=db_pair.source_instance_id,
        target_instance_id=db_pair.target_instance_id,
        mirror_direction=db_pair.mirror_direction,
        mirror_overwrite_diverged=db_pair.mirror_overwrite_diverged,
        mirror_trigger_builds=db_pair.mirror_trigger_builds,
        only_mirror_protected_branches=db_pair.only_mirror_protected_branches,
        mirror_branch_regex=db_pair.mirror_branch_regex,
        issue_sync_enabled=db_pair.issue_sync_enabled,
        description=db_pair.description,
        created_at=db_pair.created_at.isoformat() + "Z",
        updated_at=db_pair.updated_at.isoformat() + "Z",
        warnings=warnings if warnings else None,
        reverse_pair_id=reverse_pair_id
    )


@router.get("/{pair_id}", response_model=InstancePairResponse)
async def get_pair(
    pair_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Get a specific instance pair."""
    result = await db.execute(
        select(InstancePair).where(InstancePair.id == pair_id)
    )
    pair = result.scalar_one_or_none()

    if not pair:
        raise HTTPException(status_code=404, detail="Instance pair not found")

    return InstancePairResponse(
        id=pair.id,
        name=pair.name,
        source_instance_id=pair.source_instance_id,
        target_instance_id=pair.target_instance_id,
        mirror_direction=pair.mirror_direction,
        mirror_overwrite_diverged=pair.mirror_overwrite_diverged,
        mirror_trigger_builds=pair.mirror_trigger_builds,
        only_mirror_protected_branches=pair.only_mirror_protected_branches,
        mirror_branch_regex=pair.mirror_branch_regex,
        issue_sync_enabled=pair.issue_sync_enabled,
        description=pair.description,
        created_at=pair.created_at.isoformat() + "Z",
        updated_at=pair.updated_at.isoformat() + "Z"
    )


@router.put("/{pair_id}", response_model=InstancePairResponse)
async def update_pair(
    pair_id: int,
    pair_update: InstancePairUpdate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Update an instance pair."""
    result = await db.execute(
        select(InstancePair).where(InstancePair.id == pair_id)
    )
    pair = result.scalar_one_or_none()

    if not pair:
        raise HTTPException(status_code=404, detail="Instance pair not found")

    fields = getattr(pair_update, "model_fields_set", set())

    # Safety: do not allow changing which instances a pair points at once mirrors exist.
    if ("source_instance_id" in fields) or ("target_instance_id" in fields):
        mirrors_res = await db.execute(select(Mirror.id).where(Mirror.instance_pair_id == pair_id).limit(1))
        if mirrors_res.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=400,
                detail="Cannot change source/target instances for a pair that already has mirrors.",
            )

    # Calculate what source/target will be after update
    new_source_id = pair_update.source_instance_id if "source_instance_id" in fields else pair.source_instance_id
    new_target_id = pair_update.target_instance_id if "target_instance_id" in fields else pair.target_instance_id

    # Check for self-referential pair
    if new_source_id == new_target_id:
        raise HTTPException(
            status_code=400,
            detail="Source and target instances must be different. A pair cannot mirror an instance to itself."
        )

    # Check for reverse pair (bidirectional mirroring scenario) if instances are changing
    warnings = []
    reverse_pair_id = None
    if ("source_instance_id" in fields) or ("target_instance_id" in fields):
        reverse_pair_result = await db.execute(
            select(InstancePair).where(
                and_(
                    InstancePair.source_instance_id == new_target_id,
                    InstancePair.target_instance_id == new_source_id,
                    InstancePair.id != pair_id  # Exclude self
                )
            )
        )
        reverse_pair = reverse_pair_result.scalar_one_or_none()

        if reverse_pair:
            reverse_pair_id = reverse_pair.id
            warnings.append(
                f"Bidirectional mirroring detected: A reverse pair '{reverse_pair.name}' "
                f"(ID: {reverse_pair.id}) exists between these instances. "
                f"This creates bidirectional mirroring where changes flow both directions. "
                f"For issue syncing, last-write-wins semantics apply. "
                f"Consider using one instance as the source of truth for agile planning."
            )
            logger.info(
                f"Updating pair {pair_id} creates bidirectional setup with existing pair {reverse_pair.id}"
            )

    # Update fields (presence-aware to allow explicit null clears)
    if "name" in fields:
        pair.name = pair_update.name
    if "source_instance_id" in fields:
        pair.source_instance_id = pair_update.source_instance_id
    if "target_instance_id" in fields:
        pair.target_instance_id = pair_update.target_instance_id
    if "mirror_direction" in fields:
        pair.mirror_direction = pair_update.mirror_direction
    if "mirror_overwrite_diverged" in fields:
        pair.mirror_overwrite_diverged = pair_update.mirror_overwrite_diverged
    if "mirror_trigger_builds" in fields:
        pair.mirror_trigger_builds = pair_update.mirror_trigger_builds
    if "only_mirror_protected_branches" in fields:
        pair.only_mirror_protected_branches = pair_update.only_mirror_protected_branches
    if "mirror_branch_regex" in fields:
        pair.mirror_branch_regex = pair_update.mirror_branch_regex
    if "issue_sync_enabled" in fields:
        pair.issue_sync_enabled = pair_update.issue_sync_enabled
    if "description" in fields:
        pair.description = pair_update.description

    await db.commit()
    await db.refresh(pair)

    # Auto-create MirrorIssueConfig for mirrors under this pair when issue sync
    # is enabled at the pair level, so the scheduler can pick them up without
    # requiring the user to manually open each mirror's issue sync dialog.
    if "issue_sync_enabled" in fields and pair_update.issue_sync_enabled is True:
        mirrors_result = await db.execute(
            select(Mirror).where(Mirror.instance_pair_id == pair.id)
        )
        mirrors = mirrors_result.scalars().all()
        for m in mirrors:
            # Only create config for mirrors that would effectively inherit
            # issue_sync_enabled=True (i.e., no per-mirror override to False)
            if m.issue_sync_enabled is not False:
                existing = await db.execute(
                    select(MirrorIssueConfig).where(MirrorIssueConfig.mirror_id == m.id)
                )
                if not existing.scalar_one_or_none():
                    db.add(MirrorIssueConfig(mirror_id=m.id))
                    logger.info(f"Auto-created issue sync config for mirror {m.id} (pair-level enable)")
        try:
            await db.commit()
        except Exception as e:
            await db.rollback()
            logger.warning(f"Failed to auto-create issue sync configs for pair {pair.id}: {e}")

    return InstancePairResponse(
        id=pair.id,
        name=pair.name,
        source_instance_id=pair.source_instance_id,
        target_instance_id=pair.target_instance_id,
        mirror_direction=pair.mirror_direction,
        mirror_overwrite_diverged=pair.mirror_overwrite_diverged,
        mirror_trigger_builds=pair.mirror_trigger_builds,
        only_mirror_protected_branches=pair.only_mirror_protected_branches,
        mirror_branch_regex=pair.mirror_branch_regex,
        issue_sync_enabled=pair.issue_sync_enabled,
        description=pair.description,
        created_at=pair.created_at.isoformat() + "Z",
        updated_at=pair.updated_at.isoformat() + "Z",
        warnings=warnings if warnings else None,
        reverse_pair_id=reverse_pair_id
    )


@router.delete("/{pair_id}")
async def delete_pair(
    pair_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """
    Delete an instance pair.

    This performs cascade deletion with proper GitLab cleanup:
    1. Cleans up all mirrors from GitLab (with rate limiting)
    2. Deletes mirrors and pair from database
    """
    result = await db.execute(
        select(InstancePair).where(InstancePair.id == pair_id)
    )
    pair = result.scalar_one_or_none()

    if not pair:
        raise HTTPException(status_code=404, detail="Instance pair not found")

    # Fetch all mirrors for this pair
    mirrors_result = await db.execute(
        select(Mirror).where(Mirror.instance_pair_id == pair_id)
    )
    mirrors_to_delete = list(mirrors_result.scalars().all())

    # Import the cleanup helper from mirrors module
    from app.api.mirrors import _cleanup_mirror_from_gitlab

    # Clean up mirrors from GitLab with rate limiting (if any)
    cleanup_warnings = []
    if mirrors_to_delete:
        logger.info(f"Cleaning up {len(mirrors_to_delete)} mirrors from GitLab before deleting pair {pair_id}")

        rate_limiter = RateLimiter(
            delay_ms=settings.gitlab_api_delay_ms,
            max_retries=settings.gitlab_api_max_retries
        )
        tracker = BatchOperationTracker(total_items=len(mirrors_to_delete))
        rate_limiter.start_tracking()

        for idx, mirror in enumerate(mirrors_to_delete):
            try:
                # Clean up from GitLab (best effort)
                gitlab_failed, gitlab_err, token_failed, token_err = await _cleanup_mirror_from_gitlab(mirror, db)

                if gitlab_failed or token_failed:
                    warning = f"Mirror {mirror.id} ({mirror.source_project_path}→{mirror.target_project_path}): "
                    if gitlab_failed:
                        warning += f"GitLab cleanup failed ({gitlab_err}); "
                    if token_failed:
                        warning += f"Token cleanup failed ({token_err})"
                    cleanup_warnings.append(warning)
                    tracker.record_failure(warning)
                else:
                    tracker.record_success()

                logger.info(f"[{idx + 1}/{len(mirrors_to_delete)}] Cleaned up mirror {mirror.id}")

            except Exception as e:
                error_msg = f"Mirror {mirror.id}: {str(e)}"
                cleanup_warnings.append(error_msg)
                tracker.record_failure(error_msg)
                logger.error(f"Failed to clean up mirror {mirror.id}: {str(e)}")

            # Apply rate limiting delay (except after last mirror)
            if idx < len(mirrors_to_delete) - 1:
                await rate_limiter.delay()

        summary = tracker.get_summary()
        metrics = rate_limiter.get_metrics()
        logger.info(
            f"GitLab cleanup completed for pair {pair_id}: "
            f"{summary['succeeded']} succeeded, {summary['failed']} failed "
            f"in {summary['duration_seconds']}s ({metrics['operations_per_second']} ops/sec)"
        )

    # Now delete from database
    # CRITICAL: All delete operations must succeed atomically or be rolled back together
    try:
        # Delete issue sync data for all mirrors in this pair
        mirror_ids_result = await db.execute(
            select(Mirror.id).where(Mirror.instance_pair_id == pair_id)
        )
        pair_mirror_ids = [row[0] for row in mirror_ids_result.all()]
        if pair_mirror_ids:
            await _delete_issue_sync_data_for_mirrors(db, pair_mirror_ids)

        # Delete mirrors (they reference the pair)
        await db.execute(delete(Mirror).where(Mirror.instance_pair_id == pair_id))

        # Finally delete the pair itself
        await db.delete(pair)

        # Commit all changes atomically
        await db.commit()
    except Exception as e:
        # Rollback all changes if any operation fails to maintain data integrity
        await db.rollback()
        logger.error(f"Failed to delete instance pair {pair_id}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Failed to delete instance pair. Database changes have been rolled back to maintain data integrity."
        )

    # Return status with warnings if GitLab cleanup had issues
    response = {"status": "deleted"}
    if cleanup_warnings:
        response["warnings"] = cleanup_warnings
        response["warning_count"] = len(cleanup_warnings)
        logger.warning(f"Pair {pair_id} deleted with {len(cleanup_warnings)} cleanup warnings")

    return response


@router.post("/{pair_id}/sync-mirrors")
async def sync_all_mirrors(
    pair_id: int,
    limit: int = Query(default=100, ge=1, le=1000, description="Maximum number of mirrors to sync (1-1000)"),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """
    Trigger batch sync for enabled mirrors in this instance pair.

    This endpoint processes mirrors sequentially with rate limiting to avoid
    overwhelming GitLab instances. Use this to resume mirrors after a
    GitLab instance outage or scheduled maintenance.

    Args:
        pair_id: Instance pair ID
        limit: Maximum number of mirrors to sync (default: 100, max: 1000)

    Returns:
        Summary of sync operations including counts and any errors
    """
    # Verify pair exists
    pair_result = await db.execute(
        select(InstancePair).where(InstancePair.id == pair_id)
    )
    pair = pair_result.scalar_one_or_none()

    if not pair:
        raise HTTPException(status_code=404, detail="Instance pair not found")

    # Get enabled mirrors for this pair with limit
    mirrors_result = await db.execute(
        select(Mirror).where(
            Mirror.instance_pair_id == pair_id,
            Mirror.enabled == True
        ).limit(limit)
    )
    mirrors = mirrors_result.scalars().all()

    if not mirrors:
        return {
            "status": "completed",
            "total": 0,
            "succeeded": 0,
            "failed": 0,
            "skipped": 0,
            "errors": [],
            "message": "No enabled mirrors found for this pair"
        }

    # Get source and target instances
    source_result = await db.execute(
        select(GitLabInstance).where(GitLabInstance.id == pair.source_instance_id)
    )
    source_instance = source_result.scalar_one_or_none()

    target_result = await db.execute(
        select(GitLabInstance).where(GitLabInstance.id == pair.target_instance_id)
    )
    target_instance = target_result.scalar_one_or_none()

    if not source_instance or not target_instance:
        raise HTTPException(status_code=404, detail="Source or target instance not found")

    # Determine which instance owns the mirror configuration
    direction = pair.mirror_direction
    mirror_instance = source_instance if direction == "push" else target_instance

    # Initialize rate limiter and tracker
    rate_limiter = RateLimiter(
        delay_ms=settings.gitlab_api_delay_ms,
        max_retries=settings.gitlab_api_max_retries
    )
    tracker = BatchOperationTracker(total_items=len(mirrors))
    rate_limiter.start_tracking()

    # Create GitLab client
    client = GitLabClient(mirror_instance.url, mirror_instance.encrypted_token, timeout=settings.gitlab_api_timeout)

    # Process each mirror
    skipped = 0
    errors = []

    for idx, mirror in enumerate(mirrors):
        mirror_identifier = f"{mirror.source_project_path} → {mirror.target_project_path}"
        project_id = mirror.source_project_id if direction == "push" else mirror.target_project_id

        # Skip if mirror not configured in GitLab
        if not mirror.mirror_id:
            logger.warning(f"Skipping mirror {mirror.id}: not configured in GitLab")
            skipped += 1
            tracker.record_success()  # Count as processed but don't track as error
            continue

        # Skip if mirror token has expired
        if _is_token_expired(mirror.mirror_token_expires_at):
            error_msg = f"{mirror_identifier}: Mirror token has expired. Please rotate the token."
            logger.warning(f"Skipping mirror {mirror.id}: token expired")
            errors.append(error_msg)
            tracker.record_failure(error_msg)
            continue

        try:
            # Trigger mirror update with retry logic (use correct method for direction)
            if direction == "push":
                def trigger_update():
                    return client.trigger_mirror_update(project_id, mirror.mirror_id)
            else:
                def trigger_update():
                    return client.trigger_pull_mirror_update(project_id)

            await rate_limiter.execute_with_retry(
                trigger_update,
                operation_name=f"sync mirror {mirror.id}"
            )

            # Update mirror status in database
            mirror.last_update_status = "updating"
            try:
                await db.commit()
            except Exception as commit_error:
                # Rollback the failed commit to maintain session state
                await db.rollback()
                # Record as failure and continue instead of re-raising
                error_msg = f"{mirror_identifier}: Database commit failed - {type(commit_error).__name__}"
                errors.append(error_msg)
                tracker.record_failure(error_msg)
                logger.error(f"Failed to update status for mirror {mirror.id}: {type(commit_error).__name__}")
            else:
                # Only record success if commit succeeded
                tracker.record_success()
                logger.info(f"[{idx + 1}/{len(mirrors)}] Triggered sync for {mirror_identifier}")

        except Exception as e:
            # Rollback for errors during GitLab API operations
            await db.rollback()
            error_msg = f"{mirror_identifier}: {str(e)}"
            errors.append(error_msg)
            tracker.record_failure(error_msg)
            logger.error(f"Failed to trigger sync for mirror {mirror.id}: {str(e)}")
            # Continue with next mirror instead of failing entirely

        # Apply rate limiting delay (except after last mirror)
        if idx < len(mirrors) - 1:
            await rate_limiter.delay()

    # Get final summary
    summary = tracker.get_summary()
    metrics = rate_limiter.get_metrics()

    logger.info(
        f"Batch sync completed for pair {pair_id}: "
        f"{summary['succeeded']} succeeded, {summary['failed']} failed, {skipped} skipped "
        f"in {summary['duration_seconds']}s ({metrics['operations_per_second']} ops/sec)"
    )

    return {
        "status": "completed",
        "total": len(mirrors),
        "succeeded": summary["succeeded"],
        "failed": summary["failed"],
        "skipped": skipped,
        "errors": errors,
        "duration_seconds": summary["duration_seconds"],
        "operations_per_second": metrics["operations_per_second"]
    }
