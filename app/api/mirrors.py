from typing import List, TypeVar, Callable, Any
import re
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, ConfigDict, Field, field_validator
from datetime import datetime, timedelta

from app.database import get_db
from app.models import Mirror, InstancePair, GitLabInstance
from app.core.auth import verify_credentials
from app.core.gitlab_client import (
    GitLabClient,
    GitLabClientError,
    GitLabConnectionError,
    GitLabRateLimitError,
)
from app.core.encryption import encryption
from app.core.mirror_gitlab_service import get_mirror_gitlab_service
from urllib.parse import urlparse, quote

T = TypeVar('T')

# Token expiration: 1 year from creation
TOKEN_EXPIRY_DAYS = 365

# Maximum allowed regex pattern length to prevent resource exhaustion
MAX_REGEX_LENGTH = 500

logger = logging.getLogger(__name__)


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


async def _execute_gitlab_op(
    client: GitLabClient,
    operation: Callable[[GitLabClient], T],
    operation_name: str,
) -> T:
    """
    Execute a GitLab operation with rate limiting, retry, and circuit breaker.

    This helper wraps all GitLab API calls with enterprise-grade robustness:
    - Rate limiting (configurable delay between operations)
    - Exponential backoff retry on rate limit errors
    - Circuit breaker to prevent cascading failures

    Args:
        client: The GitLabClient to use
        operation: A callable that takes the client and returns the result
        operation_name: Descriptive name for logging

    Returns:
        The result of the operation

    Raises:
        HTTPException: If the operation fails after retries
    """
    service = get_mirror_gitlab_service()
    try:
        return await service.execute(
            client=client,
            operation=operation,
            operation_name=operation_name,
        )
    except GitLabConnectionError as e:
        logger.error(f"{operation_name} failed - connection error: {e}")
        raise HTTPException(
            status_code=503,
            detail="GitLab service unavailable. Check server logs for details."
        )
    except GitLabRateLimitError as e:
        logger.error(f"{operation_name} failed - rate limit exceeded: {e}")
        raise HTTPException(
            status_code=429,
            detail="GitLab rate limit exceeded. Please try again later."
        )
    except GitLabClientError as e:
        logger.error(f"{operation_name} failed: {e}")
        raise HTTPException(
            status_code=500,
            detail="GitLab operation failed. Check server logs for details."
        )


router = APIRouter(prefix="/api/mirrors", tags=["mirrors"])


def _normalize_instance_url(url: str) -> str:
    """
    Ensure instance URLs parse correctly even if users omit the scheme.
    Examples:
      - "gitlab.example.com" -> "https://gitlab.example.com"
      - "https://gitlab.example.com" -> unchanged
    """
    if "://" not in url:
        return f"https://{url}"
    return url


def _build_git_url(*, scheme: str, hostname: str, port: int | None, project_path: str, username: str | None = None, password: str | None = None) -> str:
    # Percent-encode userinfo to prevent URL corruption / host injection.
    userinfo = ""
    if username is not None and password is not None:
        user = quote(username, safe="")
        pw = quote(password, safe="")
        userinfo = f"{user}:{pw}@"

    hostport = hostname
    if port is not None:
        hostport = f"{hostname}:{port}"

    # Keep slashes for namespaces, but escape other unsafe chars.
    safe_path = quote(project_path, safe="/-._~")
    return f"{scheme}://{userinfo}{hostport}/{safe_path}.git"


def build_authenticated_url(
    instance: GitLabInstance,
    project_path: str,
    token_name: str | None = None,
    token_value: str | None = None,
) -> str:
    """
    Build an authenticated Git URL for mirroring.

    If token_name and token_value are provided, builds an authenticated URL.
    Otherwise, builds an unauthenticated URL.
    """
    # Parse the instance URL first
    parsed = urlparse(_normalize_instance_url(instance.url))
    scheme = parsed.scheme or "https"
    if scheme not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="Invalid GitLab instance URL scheme")

    hostname = parsed.hostname
    port = parsed.port
    if not hostname:
        raise HTTPException(status_code=400, detail="Invalid GitLab instance URL")

    if token_name and token_value:
        return _build_git_url(
            scheme=scheme,
            hostname=hostname,
            port=port,
            project_path=project_path,
            username=token_name,
            password=token_value,
        )
    else:
        return _build_git_url(scheme=scheme, hostname=hostname, port=port, project_path=project_path)




class MirrorCreate(BaseModel):
    instance_pair_id: int
    source_project_id: int
    source_project_path: str
    target_project_id: int
    target_project_path: str
    # Direction is determined by the instance pair, not overridable per-mirror
    mirror_overwrite_diverged: bool | None = None
    mirror_trigger_builds: bool | None = None
    only_mirror_protected_branches: bool | None = None
    mirror_branch_regex: str | None = None
    enabled: bool = True

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

    @field_validator('source_project_path', 'target_project_path')
    @classmethod
    def validate_project_path(cls, v):
        """Validate GitLab project path format."""
        if not v or not v.strip():
            raise ValueError("Project path cannot be empty")
        # Remove leading/trailing slashes
        v = v.strip().strip('/')
        if not v:
            raise ValueError("Project path cannot be empty")
        # GitLab paths should be namespace/project or namespace/subgroup/project
        if not re.match(r'^[a-zA-Z0-9_.-]+(/[a-zA-Z0-9_.-]+)+$', v):
            raise ValueError("Invalid GitLab project path format. Expected: 'namespace/project' or 'namespace/subgroup/project'")
        return v


class MirrorPreflight(BaseModel):
    """
    Preflight a mirror creation by checking GitLab for existing remote mirrors
    on the owning project. Direction is determined by the instance pair.
    """
    instance_pair_id: int
    source_project_id: int
    source_project_path: str
    target_project_id: int
    target_project_path: str

    @field_validator('source_project_path', 'target_project_path')
    @classmethod
    def validate_project_path(cls, v):
        """Validate GitLab project path format."""
        if not v or not v.strip():
            raise ValueError("Project path cannot be empty")
        v = v.strip().strip('/')
        if not v:
            raise ValueError("Project path cannot be empty")
        if not re.match(r'^[a-zA-Z0-9_.-]+(/[a-zA-Z0-9_.-]+)+$', v):
            raise ValueError("Invalid GitLab project path format. Expected: 'namespace/project' or 'namespace/subgroup/project'")
        return v


class MirrorPreflightResponse(BaseModel):
    effective_direction: str
    owner_project_id: int
    existing_mirrors: list[dict]
    existing_same_direction: list[dict]


class MirrorRemoveExisting(BaseModel):
    """
    Remove existing GitLab remote mirrors on the owning project for the pair's direction.
    If `remote_mirror_ids` is omitted, deletes all mirrors for that direction.
    """
    instance_pair_id: int
    source_project_id: int
    source_project_path: str
    target_project_id: int
    target_project_path: str
    remote_mirror_ids: list[int] | None = None

    @field_validator('source_project_path', 'target_project_path')
    @classmethod
    def validate_project_path(cls, v):
        """Validate GitLab project path format."""
        if not v or not v.strip():
            raise ValueError("Project path cannot be empty")
        v = v.strip().strip('/')
        if not v:
            raise ValueError("Project path cannot be empty")
        if not re.match(r'^[a-zA-Z0-9_.-]+(/[a-zA-Z0-9_.-]+)+$', v):
            raise ValueError("Invalid GitLab project path format. Expected: 'namespace/project' or 'namespace/subgroup/project'")
        return v


class DriftDetail(BaseModel):
    """Details about a drifted setting."""
    field: str
    expected: bool | str | None
    actual: bool | str | None


class MirrorVerifyResponse(BaseModel):
    """Response for mirror verification (orphan/drift detection)."""
    mirror_id: int
    status: str  # "healthy", "orphan", "drift", "error", "not_created"
    orphan: bool = False
    drift: list[DriftDetail] = []
    gitlab_mirror: dict | None = None
    error: str | None = None


class MirrorVerifyRequest(BaseModel):
    """Request to verify multiple mirrors."""
    # Limit to 1000 mirrors per request to prevent DoS
    mirror_ids: list[int] = Field(max_length=1000)


class MirrorUpdate(BaseModel):
    # Direction cannot be changed - it's determined by the instance pair
    mirror_overwrite_diverged: bool | None = None
    mirror_trigger_builds: bool | None = None
    only_mirror_protected_branches: bool | None = None
    mirror_branch_regex: str | None = None
    enabled: bool | None = None

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


class MirrorResponse(BaseModel):
    id: int
    instance_pair_id: int
    source_project_id: int
    source_project_path: str
    target_project_id: int
    target_project_path: str
    # Per-mirror setting overrides (direction is pair-only, not here)
    mirror_overwrite_diverged: bool | None
    mirror_trigger_builds: bool | None
    only_mirror_protected_branches: bool | None
    mirror_branch_regex: str | None
    # Effective settings (mirror overrides -> pair defaults)
    effective_mirror_direction: str | None = None
    effective_mirror_overwrite_diverged: bool | None = None
    effective_mirror_trigger_builds: bool | None = None
    effective_only_mirror_protected_branches: bool | None = None
    effective_mirror_branch_regex: str | None = None
    mirror_id: int | None
    last_successful_update: str | None
    last_update_status: str | None
    enabled: bool
    # Token status fields
    mirror_token_expires_at: str | None = None
    token_status: str | None = None  # "active", "expiring_soon", "expired", "none"
    created_at: str
    updated_at: str

    model_config = ConfigDict(from_attributes=True)


class MirrorListResponse(BaseModel):
    """Paginated mirror list response."""
    mirrors: List[MirrorResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class GroupSummary(BaseModel):
    """Summary statistics for a group path."""
    group_path: str
    mirror_count: int
    enabled_count: int
    disabled_count: int
    failed_count: int
    level: int  # Nesting level (0 = top-level, 1 = first subgroup, etc.)


def _compute_token_status(expires_at: datetime | None) -> str:
    """Compute token status based on expiration date."""
    if expires_at is None:
        return "none"
    now = datetime.utcnow()
    if expires_at <= now:
        return "expired"
    elif expires_at <= now + timedelta(days=30):
        return "expiring_soon"
    else:
        return "active"


async def _resolve_effective_settings(
    db: AsyncSession,
    *,
    mirror: Mirror,
    pair: InstancePair,
    source_instance: GitLabInstance | None = None,
    target_instance: GitLabInstance | None = None,
) -> dict[str, object]:
    """
    Compute the effective mirror settings as the tool will apply them:
    mirror overrides -> pair defaults (+ pull mirror user fallback).

    Note: some settings are pull-only in GitLab and are intentionally treated as
    not applicable for push mirrors.
    """
    # Direction comes from pair only (not overridable per-mirror)
    direction = (pair.mirror_direction or "").lower()

    overwrite_diverged = (
        mirror.mirror_overwrite_diverged
        if mirror.mirror_overwrite_diverged is not None
        else pair.mirror_overwrite_diverged
    )
    only_protected = (
        mirror.only_mirror_protected_branches
        if mirror.only_mirror_protected_branches is not None
        else pair.only_mirror_protected_branches
    )
    trigger_builds = (
        mirror.mirror_trigger_builds
        if mirror.mirror_trigger_builds is not None
        else pair.mirror_trigger_builds
    )
    branch_regex = (
        mirror.mirror_branch_regex
        if mirror.mirror_branch_regex is not None
        else pair.mirror_branch_regex
    )

    # Pull-only settings: if direction is push, treat as not applicable.
    if direction == "push":
        trigger_builds = None
        branch_regex = None

    return {
        "effective_mirror_direction": direction or None,
        "effective_mirror_overwrite_diverged": overwrite_diverged,
        "effective_only_mirror_protected_branches": only_protected,
        "effective_mirror_trigger_builds": trigger_builds,
        "effective_mirror_branch_regex": branch_regex,
    }


@router.get("", response_model=MirrorListResponse)
async def list_mirrors(
    instance_pair_id: int | None = None,
    status: str | None = Query(default=None, max_length=50),
    enabled: bool | None = None,
    search: str | None = Query(default=None, max_length=500),
    token_status: str | None = Query(default=None, pattern="^(active|expiring_soon|expired|none)$"),
    group_path: str | None = Query(default=None, max_length=500),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    order_by: str = Query(default="created_at", pattern="^(created_at|updated_at|source_project_path|target_project_path)$"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """
    List all mirrors with optional filtering and pagination.

    Query parameters:
    - instance_pair_id: Filter by instance pair ID
    - status: Filter by last_update_status (e.g., 'success', 'failed', 'pending')
    - enabled: Filter by enabled status (true/false)
    - search: Search in source and target project paths (case-insensitive)
    - token_status: Filter by token status ('active', 'expiring_soon', 'expired', 'none')
    - group_path: Filter by group path prefix (e.g., 'group1/subgroup1')
    - page: Page number (default: 1)
    - page_size: Items per page (default: 50, max: 200)
    - order_by: Field to order by (created_at, updated_at, source_project_path, target_project_path, last_update_status)
    - order_dir: Order direction (asc, desc)

    Note: When using token_status filter, pagination metadata (total, total_pages) reflects
    only the filtered items on the current page, not the total across all pages. This is
    because token_status is computed post-query. For accurate totals with token_status,
    request all items (page_size=200) or use client-side filtering.
    """
    # Validate and limit page_size
    page = max(1, page)
    page_size = max(1, min(200, page_size))

    # Build base query
    query = select(Mirror)

    # Apply filters
    if instance_pair_id is not None:
        query = query.where(Mirror.instance_pair_id == instance_pair_id)

    if status is not None:
        query = query.where(Mirror.last_update_status == status)

    if enabled is not None:
        query = query.where(Mirror.enabled == enabled)

    if search is not None and search.strip():
        search_term = f"%{search.strip().lower()}%"
        query = query.where(
            (Mirror.source_project_path.ilike(search_term)) |
            (Mirror.target_project_path.ilike(search_term))
        )

    if group_path is not None and group_path.strip():
        # Filter by group path prefix (e.g., "group1/subgroup1" matches "group1/subgroup1/project")
        group_prefix = f"{group_path.strip()}/%"
        query = query.where(
            (Mirror.source_project_path.ilike(group_prefix)) |
            (Mirror.target_project_path.ilike(group_prefix))
        )

    # Count total before pagination
    from sqlalchemy import func, select as sql_select
    count_query = sql_select(func.count()).select_from(query.alias())
    count_result = await db.execute(count_query)
    total_count = count_result.scalar() or 0

    # Apply ordering
    order_column = {
        'created_at': Mirror.created_at,
        'updated_at': Mirror.updated_at,
        'source_project_path': Mirror.source_project_path,
        'target_project_path': Mirror.target_project_path,
        'last_update_status': Mirror.last_update_status,
    }.get(order_by, Mirror.created_at)

    if order_dir.lower() == 'asc':
        query = query.order_by(order_column.asc())
    else:
        query = query.order_by(order_column.desc())

    # Apply pagination
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    result = await db.execute(query)
    mirrors = result.scalars().all()

    # Token status filter is applied post-query since it's computed
    if token_status is not None:
        mirrors = [m for m in mirrors if _compute_token_status(m.mirror_token_expires_at) == token_status]
        # Adjust total count for post-query filter
        total_count = len(mirrors)

    if not mirrors:
        return MirrorListResponse(
            mirrors=[],
            total=total_count,
            page=page,
            page_size=page_size,
            total_pages=0
        )

    # Bulk-load all pairs and instances to avoid N+1 queries
    # Get unique pair IDs from mirrors
    pair_ids = {m.instance_pair_id for m in mirrors}

    # Fetch all pairs in one query
    pairs_result = await db.execute(
        select(InstancePair).where(InstancePair.id.in_(pair_ids))
    )
    pair_cache: dict[int, InstancePair] = {p.id: p for p in pairs_result.scalars().all()}

    # Collect all instance IDs from pairs
    instance_ids: set[int] = set()
    for pair in pair_cache.values():
        instance_ids.add(pair.source_instance_id)
        instance_ids.add(pair.target_instance_id)

    # Fetch all instances in one query
    if instance_ids:
        instances_result = await db.execute(
            select(GitLabInstance).where(GitLabInstance.id.in_(instance_ids))
        )
        instance_cache: dict[int, GitLabInstance] = {i.id: i for i in instances_result.scalars().all()}
    else:
        instance_cache = {}

    out: list[MirrorResponse] = []
    for mirror in mirrors:
        pair = pair_cache.get(mirror.instance_pair_id)
        eff: dict[str, object] = {}
        if pair:
            src = instance_cache.get(pair.source_instance_id)
            tgt = instance_cache.get(pair.target_instance_id)
            eff = await _resolve_effective_settings(db, mirror=mirror, pair=pair, source_instance=src, target_instance=tgt)

        out.append(
            MirrorResponse(
                id=mirror.id,
                instance_pair_id=mirror.instance_pair_id,
                source_project_id=mirror.source_project_id,
                source_project_path=mirror.source_project_path,
                target_project_id=mirror.target_project_id,
                target_project_path=mirror.target_project_path,
                mirror_overwrite_diverged=mirror.mirror_overwrite_diverged,
                mirror_trigger_builds=mirror.mirror_trigger_builds,
                only_mirror_protected_branches=mirror.only_mirror_protected_branches,
                mirror_branch_regex=mirror.mirror_branch_regex,
                mirror_id=mirror.mirror_id,
                last_successful_update=mirror.last_successful_update.isoformat() if mirror.last_successful_update else None,
                last_update_status=mirror.last_update_status,
                enabled=mirror.enabled,
                mirror_token_expires_at=mirror.mirror_token_expires_at.isoformat() if mirror.mirror_token_expires_at else None,
                token_status=_compute_token_status(mirror.mirror_token_expires_at),
                created_at=mirror.created_at.isoformat(),
                updated_at=mirror.updated_at.isoformat(),
                **eff,
            )
        )

    # Calculate pagination metadata
    total_pages = (total_count + page_size - 1) // page_size

    return MirrorListResponse(
        mirrors=out,
        total=total_count,
        page=page,
        page_size=page_size,
        total_pages=total_pages
    )


@router.get("/groups", response_model=List[GroupSummary])
async def list_mirror_groups(
    instance_pair_id: int | None = None,
    max_level: int = 2,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """
    Get summary statistics of mirrors grouped by path hierarchy.

    Returns group paths at various nesting levels with mirror counts and status.
    This is useful for navigating large sets of mirrors organized in nested groups.

    Query parameters:
    - instance_pair_id: Filter by instance pair ID
    - max_level: Maximum nesting level to return (0=top-level only, 1=include first subgroup, etc.)
    """
    # Build query to get all mirrors
    query = select(Mirror)

    if instance_pair_id is not None:
        query = query.where(Mirror.instance_pair_id == instance_pair_id)

    result = await db.execute(query)
    mirrors = result.scalars().all()

    if not mirrors:
        return []

    # Build group statistics from paths
    from collections import defaultdict
    group_stats = defaultdict(lambda: {
        'count': 0,
        'enabled': 0,
        'disabled': 0,
        'failed': 0,
        'level': 0
    })

    for mirror in mirrors:
        # Process both source and target paths
        for path in [mirror.source_project_path, mirror.target_project_path]:
            # Split path into parts (e.g., "group1/subgroup1/project" -> ["group1", "subgroup1", "project"])
            parts = path.split('/')

            # Generate all parent group paths up to max_level
            for level in range(min(len(parts) - 1, max_level + 1)):  # -1 to exclude project name
                group_path = '/'.join(parts[:level + 1])
                stats = group_stats[group_path]
                stats['count'] += 1
                stats['level'] = level

                if mirror.enabled:
                    stats['enabled'] += 1
                else:
                    stats['disabled'] += 1

                if mirror.last_update_status == 'failed':
                    stats['failed'] += 1

    # Convert to response models
    summaries = [
        GroupSummary(
            group_path=group_path,
            mirror_count=stats['count'],
            enabled_count=stats['enabled'],
            disabled_count=stats['disabled'],
            failed_count=stats['failed'],
            level=stats['level']
        )
        for group_path, stats in sorted(group_stats.items())
    ]

    return summaries


async def _create_mirror_internal(
    db: AsyncSession,
    pair: InstancePair,
    source_instance: GitLabInstance,
    target_instance: GitLabInstance,
    mirror_data: MirrorCreate,
    skip_duplicate_check: bool = False
) -> Mirror:
    """
    Internal helper to create a mirror with all GitLab API calls and token management.

    Args:
        db: Database session
        pair: Instance pair
        source_instance: Source GitLab instance
        target_instance: Target GitLab instance
        mirror_data: Mirror configuration data
        skip_duplicate_check: If True, skip duplicate checking (for imports that already checked)

    Returns:
        Created Mirror object

    Raises:
        HTTPException: On any error during creation
    """
    # Check for duplicate mirror (same pair + source + target projects)
    if not skip_duplicate_check:
        duplicate_check = await db.execute(
            select(Mirror).where(
                Mirror.instance_pair_id == mirror_data.instance_pair_id,
                Mirror.source_project_id == mirror_data.source_project_id,
                Mirror.target_project_id == mirror_data.target_project_id
            )
        )
        existing_mirror = duplicate_check.scalar_one_or_none()
        if existing_mirror:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": f"Mirror already exists for this project pair",
                    "existing_mirror_id": existing_mirror.id,
                    "source_project": mirror_data.source_project_path,
                    "target_project": mirror_data.target_project_path
                }
            )

    # Direction comes from the instance pair (not overridable per-mirror)
    direction = pair.mirror_direction

    # Validate direction is set and valid
    if not direction or direction not in ("push", "pull"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mirror direction: {direction}. Must be 'push' or 'pull'"
        )

    overwrite_diverged = (
        mirror_data.mirror_overwrite_diverged
        if mirror_data.mirror_overwrite_diverged is not None
        else pair.mirror_overwrite_diverged
    )
    trigger_builds = (
        mirror_data.mirror_trigger_builds
        if mirror_data.mirror_trigger_builds is not None
        else pair.mirror_trigger_builds
    )
    only_protected = (
        mirror_data.only_mirror_protected_branches
        if mirror_data.only_mirror_protected_branches is not None
        else pair.only_mirror_protected_branches
    )
    branch_regex = (
        mirror_data.mirror_branch_regex
        if mirror_data.mirror_branch_regex is not None
        else pair.mirror_branch_regex
    )

    # Determine which project needs the token and create it
    # Push: token on target (allows pushing to it)
    # Pull: token on source (allows reading from it)
    if direction == "push":
        token_instance = target_instance
        token_project_id = mirror_data.target_project_id
        token_project_path = mirror_data.target_project_path
        token_scopes = ["write_repository"]
    else:
        token_instance = source_instance
        token_project_id = mirror_data.source_project_id
        token_project_path = mirror_data.source_project_path
        token_scopes = ["read_repository"]

    # Calculate token expiration (1 year from now)
    token_expires_at = datetime.utcnow() + timedelta(days=TOKEN_EXPIRY_DAYS)
    token_expires_str = token_expires_at.strftime("%Y-%m-%d")

    # Create project access token
    token_info = None
    encrypted_token = None
    gitlab_token_id = None
    token_name = None
    token_client = GitLabClient(token_instance.url, token_instance.encrypted_token)

    try:
        # Use a unique token name that includes a timestamp for uniqueness
        token_name = f"mirror-maestro-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        token_info = await _execute_gitlab_op(
            client=token_client,
            operation=lambda c: c.create_project_access_token(
                project_id=token_project_id,
                name=token_name,
                scopes=token_scopes,
                expires_at=token_expires_str,
            ),
            operation_name=f"create_project_access_token({token_project_id})",
        )
        # Validate response - token_info must be a dict with id and token
        if not isinstance(token_info, dict):
            logger.error(f"GitLab API returned invalid token response: expected dict, got {type(token_info).__name__}")
            raise HTTPException(
                status_code=500,
                detail="Failed to create access token: GitLab returned invalid response"
            )
        gitlab_token_id = token_info.get("id")
        plaintext_token = token_info.get("token")
        # Use 'is not None' for ID since 0 is theoretically a valid ID (falsy but valid)
        if gitlab_token_id is None or not plaintext_token:
            logger.error(
                f"GitLab API returned incomplete token response. "
                f"Has id: {gitlab_token_id is not None}, Has token: {bool(plaintext_token)}"
            )
            raise HTTPException(
                status_code=500,
                detail="Failed to create access token: GitLab returned incomplete response"
            )
        if plaintext_token:
            encrypted_token = encryption.encrypt(plaintext_token)
        logger.info(f"Created project access token '{token_name}' on project {token_project_id}")
    except HTTPException:
        # Rate limit or service unavailable - let it propagate
        raise
    except Exception as e:
        logger.warning(f"Failed to create project access token: {str(e)}. Mirror will be created without token.")
        # Continue without token - mirror may still work if project is public or using SSH

    # Build authenticated URL using the new token
    if encrypted_token:
        token_value = encryption.decrypt(encrypted_token)
        remote_url = build_authenticated_url(
            token_instance,
            token_project_path,
            token_name=token_name,
            token_value=token_value,
        )
    else:
        # No token - build unauthenticated URL
        remote_url = build_authenticated_url(token_instance, token_project_path)

    # Create the mirror in GitLab
    gitlab_mirror_id = None
    try:
        if direction == "push":
            # For push mirrors, configure on source to push to target
            client = GitLabClient(source_instance.url, source_instance.encrypted_token)
            result = await _execute_gitlab_op(
                client=client,
                operation=lambda c: c.create_push_mirror(
                    mirror_data.source_project_id,
                    remote_url,
                    enabled=mirror_data.enabled,
                    keep_divergent_refs=not overwrite_diverged,
                    only_protected_branches=only_protected,
                ),
                operation_name=f"create_push_mirror({mirror_data.source_project_id})",
            )
            gitlab_mirror_id = result.get("id")
        else:  # pull
            # For pull mirrors, configure on target to pull from source
            client = GitLabClient(target_instance.url, target_instance.encrypted_token)

            # GitLab effectively supports only one pull mirror per project.
            existing = await _execute_gitlab_op(
                client=client,
                operation=lambda c: c.get_project_mirrors(mirror_data.target_project_id),
                operation_name=f"get_project_mirrors({mirror_data.target_project_id})",
            )
            existing_pull = [m for m in (existing or []) if str(m.get("mirror_direction") or "").lower() == "pull"]
            if existing_pull:
                # Cleanup the token we just created
                if gitlab_token_id is not None:
                    try:
                        await _execute_gitlab_op(
                            client=token_client,
                            operation=lambda c: c.delete_project_access_token(token_project_id, gitlab_token_id),
                            operation_name=f"delete_project_access_token({token_project_id}, {gitlab_token_id})",
                        )
                    except Exception:
                        logger.warning(f"Failed to cleanup token {gitlab_token_id} after mirror conflict")
                # Sanitize existing mirrors - only expose safe fields (no tokens/credentials)
                safe_mirrors = [
                    {
                        "id": m.get("id"),
                        "url": m.get("url"),  # URL is already sanitized by GitLab (no auth)
                        "enabled": m.get("enabled"),
                    }
                    for m in existing_pull
                ]
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": (
                            "Target project already has a pull mirror configured in GitLab. "
                            "GitLab allows only one pull mirror per project. "
                            "Remove the existing pull mirror first."
                        ),
                        "existing_pull_mirrors": safe_mirrors,
                    },
                )

            result = await _execute_gitlab_op(
                client=client,
                operation=lambda c: c.create_pull_mirror(
                    mirror_data.target_project_id,
                    remote_url,
                    enabled=mirror_data.enabled,
                    only_protected_branches=only_protected,
                    keep_divergent_refs=not overwrite_diverged,
                    trigger_builds=trigger_builds,
                    mirror_branch_regex=branch_regex,
                    mirror_user_id=target_instance.api_user_id,
                ),
                operation_name=f"create_pull_mirror({mirror_data.target_project_id})",
            )
            gitlab_mirror_id = result.get("id")
    except HTTPException:
        # Cleanup the token we created before re-raising
        if gitlab_token_id is not None:
            try:
                await _execute_gitlab_op(
                    client=token_client,
                    operation=lambda c: c.delete_project_access_token(token_project_id, gitlab_token_id),
                    operation_name=f"delete_project_access_token({token_project_id}, {gitlab_token_id})",
                )
            except Exception:
                logger.warning(f"Failed to cleanup token {gitlab_token_id} after mirror creation failed (HTTPException path)")
        raise
    except Exception as e:
        # Cleanup the token we created
        if gitlab_token_id is not None:
            try:
                await _execute_gitlab_op(
                    client=token_client,
                    operation=lambda c: c.delete_project_access_token(token_project_id, gitlab_token_id),
                    operation_name=f"delete_project_access_token({token_project_id}, {gitlab_token_id})",
                )
            except Exception:
                logger.warning(f"Failed to cleanup token {gitlab_token_id} after mirror creation failed")
        logger.error(f"Failed to create mirror in GitLab: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Failed to create mirror in GitLab. Check server logs for details."
        )

    # Create the mirror record in database
    # CRITICAL: If DB commit fails, we must clean up the GitLab mirror and token
    db_mirror = Mirror(
        instance_pair_id=mirror_data.instance_pair_id,
        source_project_id=mirror_data.source_project_id,
        source_project_path=mirror_data.source_project_path,
        target_project_id=mirror_data.target_project_id,
        target_project_path=mirror_data.target_project_path,
        # Direction is determined by pair, not stored on mirror
        mirror_overwrite_diverged=mirror_data.mirror_overwrite_diverged,
        mirror_trigger_builds=mirror_data.mirror_trigger_builds,
        only_mirror_protected_branches=mirror_data.only_mirror_protected_branches,
        mirror_branch_regex=mirror_data.mirror_branch_regex,
        mirror_id=gitlab_mirror_id,
        enabled=mirror_data.enabled,
        last_update_status="pending",
        # Token fields
        encrypted_mirror_token=encrypted_token,
        mirror_token_name=token_name,
        mirror_token_expires_at=token_expires_at if encrypted_token else None,
        gitlab_token_id=gitlab_token_id,
        token_project_id=token_project_id,
    )
    db.add(db_mirror)

    try:
        await db.commit()
        await db.refresh(db_mirror)
    except Exception as db_error:
        # Rollback the failed transaction
        await db.rollback()

        logger.warning(f"Database commit failed. Attempting cleanup...")

        # Cleanup: Delete the GitLab mirror that was just created
        if gitlab_mirror_id:
            try:
                cleanup_instance = source_instance if direction == "push" else target_instance
                cleanup_project_id = mirror_data.source_project_id if direction == "push" else mirror_data.target_project_id
                cleanup_client = GitLabClient(cleanup_instance.url, cleanup_instance.encrypted_token)
                await _execute_gitlab_op(
                    client=cleanup_client,
                    operation=lambda c: c.delete_mirror(cleanup_project_id, gitlab_mirror_id),
                    operation_name=f"delete_mirror({cleanup_project_id}, {gitlab_mirror_id})",
                )
                logger.info(f"Successfully cleaned up orphaned GitLab mirror {gitlab_mirror_id}")
            except Exception as cleanup_error:
                logger.error(f"Failed to cleanup GitLab mirror {gitlab_mirror_id}: {str(cleanup_error)}")

        # Cleanup: Delete the project access token
        if gitlab_token_id is not None:
            try:
                await _execute_gitlab_op(
                    client=token_client,
                    operation=lambda c: c.delete_project_access_token(token_project_id, gitlab_token_id),
                    operation_name=f"delete_project_access_token({token_project_id}, {gitlab_token_id})",
                )
                logger.info(f"Successfully cleaned up orphaned token {gitlab_token_id}")
            except Exception as cleanup_error:
                logger.error(f"Failed to cleanup token {gitlab_token_id}: {str(cleanup_error)}")

        logger.error(f"Failed to save mirror to database: {str(db_error)}")
        raise HTTPException(
            status_code=500,
            detail="Failed to save mirror to database. GitLab resources have been cleaned up."
        )

    # Return the created mirror object (not a response model)
    return db_mirror


@router.post("", response_model=MirrorResponse)
async def create_mirror(
    mirror: MirrorCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Create a new mirror with automatic project access token."""
    # Get the instance pair
    pair_result = await db.execute(
        select(InstancePair).where(InstancePair.id == mirror.instance_pair_id)
    )
    pair = pair_result.scalar_one_or_none()
    if not pair:
        raise HTTPException(status_code=404, detail="Instance pair not found")

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

    # Call the internal helper to do the actual work
    db_mirror = await _create_mirror_internal(
        db=db,
        pair=pair,
        source_instance=source_instance,
        target_instance=target_instance,
        mirror_data=mirror,
        skip_duplicate_check=False
    )

    # Get direction for response
    direction = pair.mirror_direction
    overwrite_diverged = (
        mirror.mirror_overwrite_diverged
        if mirror.mirror_overwrite_diverged is not None
        else pair.mirror_overwrite_diverged
    )
    trigger_builds = (
        mirror.mirror_trigger_builds
        if mirror.mirror_trigger_builds is not None
        else pair.mirror_trigger_builds
    )
    only_protected = (
        mirror.only_mirror_protected_branches
        if mirror.only_mirror_protected_branches is not None
        else pair.only_mirror_protected_branches
    )
    branch_regex = (
        mirror.mirror_branch_regex
        if mirror.mirror_branch_regex is not None
        else pair.mirror_branch_regex
    )

    return MirrorResponse(
        id=db_mirror.id,
        instance_pair_id=db_mirror.instance_pair_id,
        source_project_id=db_mirror.source_project_id,
        source_project_path=db_mirror.source_project_path,
        target_project_id=db_mirror.target_project_id,
        target_project_path=db_mirror.target_project_path,
        mirror_overwrite_diverged=db_mirror.mirror_overwrite_diverged,
        mirror_trigger_builds=db_mirror.mirror_trigger_builds,
        only_mirror_protected_branches=db_mirror.only_mirror_protected_branches,
        mirror_branch_regex=db_mirror.mirror_branch_regex,
        effective_mirror_direction=direction,
        effective_mirror_overwrite_diverged=overwrite_diverged,
        effective_mirror_trigger_builds=trigger_builds if direction == "pull" else None,
        effective_only_mirror_protected_branches=only_protected,
        effective_mirror_branch_regex=branch_regex if direction == "pull" else None,
        mirror_id=db_mirror.mirror_id,
        last_successful_update=db_mirror.last_successful_update.isoformat() if db_mirror.last_successful_update else None,
        last_update_status=db_mirror.last_update_status,
        enabled=db_mirror.enabled,
        mirror_token_expires_at=db_mirror.mirror_token_expires_at.isoformat() if db_mirror.mirror_token_expires_at else None,
        token_status=_compute_token_status(db_mirror.mirror_token_expires_at),
        created_at=db_mirror.created_at.isoformat(),
        updated_at=db_mirror.updated_at.isoformat()
    )


@router.post("/preflight", response_model=MirrorPreflightResponse)
async def preflight_mirror(
    req: MirrorPreflight,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials),
):
    """
    Check GitLab for existing remote mirrors on the owning project before creation.

    - Pull mirrors live on the target project (target pulls from source)
    - Push mirrors live on the source project (source pushes to target)
    """
    pair_result = await db.execute(select(InstancePair).where(InstancePair.id == req.instance_pair_id))
    pair = pair_result.scalar_one_or_none()
    if not pair:
        raise HTTPException(status_code=404, detail="Instance pair not found")

    src_result = await db.execute(select(GitLabInstance).where(GitLabInstance.id == pair.source_instance_id))
    tgt_result = await db.execute(select(GitLabInstance).where(GitLabInstance.id == pair.target_instance_id))
    source_instance = src_result.scalar_one_or_none()
    target_instance = tgt_result.scalar_one_or_none()
    if not source_instance or not target_instance:
        raise HTTPException(status_code=404, detail="Source or target instance not found")

    # Direction comes from pair only
    direction = (pair.mirror_direction or "pull").lower()

    owner_project_id = req.source_project_id if direction == "push" else req.target_project_id
    owner_instance = source_instance if direction == "push" else target_instance

    client = GitLabClient(owner_instance.url, owner_instance.encrypted_token)
    existing = await _execute_gitlab_op(
        client=client,
        operation=lambda c: c.get_project_mirrors(owner_project_id) or [],
        operation_name=f"get_project_mirrors({owner_project_id})",
    )
    same_dir = [m for m in existing if str(m.get("mirror_direction") or "").lower() == direction]

    return MirrorPreflightResponse(
        effective_direction=direction,
        owner_project_id=owner_project_id,
        existing_mirrors=existing,
        existing_same_direction=same_dir,
    )


@router.post("/remove-existing")
async def remove_existing_gitlab_mirrors(
    req: MirrorRemoveExisting,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials),
):
    """Delete existing remote mirrors on the owning GitLab project for the effective direction."""
    pair_result = await db.execute(select(InstancePair).where(InstancePair.id == req.instance_pair_id))
    pair = pair_result.scalar_one_or_none()
    if not pair:
        raise HTTPException(status_code=404, detail="Instance pair not found")

    src_result = await db.execute(select(GitLabInstance).where(GitLabInstance.id == pair.source_instance_id))
    tgt_result = await db.execute(select(GitLabInstance).where(GitLabInstance.id == pair.target_instance_id))
    source_instance = src_result.scalar_one_or_none()
    target_instance = tgt_result.scalar_one_or_none()
    if not source_instance or not target_instance:
        raise HTTPException(status_code=404, detail="Source or target instance not found")

    # Direction comes from pair only
    direction = (pair.mirror_direction or "pull").lower()

    owner_project_id = req.source_project_id if direction == "push" else req.target_project_id
    owner_instance = source_instance if direction == "push" else target_instance

    client = GitLabClient(owner_instance.url, owner_instance.encrypted_token)
    existing = await _execute_gitlab_op(
        client=client,
        operation=lambda c: c.get_project_mirrors(owner_project_id) or [],
        operation_name=f"get_project_mirrors({owner_project_id})",
    )
    same_dir = [m for m in existing if str(m.get("mirror_direction") or "").lower() == direction]

    wanted = set(req.remote_mirror_ids or [])
    to_delete: list[int] = []
    for m in same_dir:
        mid = m.get("id")
        if not isinstance(mid, int):
            continue
        if wanted and mid not in wanted:
            continue
        to_delete.append(mid)

    deleted_ids: list[int] = []
    for mid in to_delete:
        try:
            await _execute_gitlab_op(
                client=client,
                operation=lambda c, mirror_id=mid: c.delete_mirror(owner_project_id, mirror_id),
                operation_name=f"delete_mirror({owner_project_id}, {mid})",
            )
            deleted_ids.append(mid)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to delete existing mirror {mid} in GitLab: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to delete existing mirror {mid} in GitLab. Check server logs for details."
            )

    return {"deleted": len(deleted_ids), "deleted_ids": deleted_ids, "direction": direction, "project_id": owner_project_id}


@router.get("/{mirror_id}", response_model=MirrorResponse)
async def get_mirror(
    mirror_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Get a specific mirror."""
    result = await db.execute(
        select(Mirror).where(Mirror.id == mirror_id)
    )
    mirror = result.scalar_one_or_none()

    if not mirror:
        raise HTTPException(status_code=404, detail="Mirror not found")

    pair_result = await db.execute(select(InstancePair).where(InstancePair.id == mirror.instance_pair_id))
    pair = pair_result.scalar_one_or_none()
    eff: dict[str, object] = {}
    if pair:
        src_result = await db.execute(select(GitLabInstance).where(GitLabInstance.id == pair.source_instance_id))
        tgt_result = await db.execute(select(GitLabInstance).where(GitLabInstance.id == pair.target_instance_id))
        eff = await _resolve_effective_settings(
            db,
            mirror=mirror,
            pair=pair,
            source_instance=src_result.scalar_one_or_none(),
            target_instance=tgt_result.scalar_one_or_none(),
        )

    return MirrorResponse(
        id=mirror.id,
        instance_pair_id=mirror.instance_pair_id,
        source_project_id=mirror.source_project_id,
        source_project_path=mirror.source_project_path,
        target_project_id=mirror.target_project_id,
        target_project_path=mirror.target_project_path,
        mirror_overwrite_diverged=mirror.mirror_overwrite_diverged,
        mirror_trigger_builds=mirror.mirror_trigger_builds,
        only_mirror_protected_branches=mirror.only_mirror_protected_branches,
        mirror_branch_regex=mirror.mirror_branch_regex,
        mirror_id=mirror.mirror_id,
        last_successful_update=mirror.last_successful_update.isoformat() if mirror.last_successful_update else None,
        last_update_status=mirror.last_update_status,
        enabled=mirror.enabled,
        mirror_token_expires_at=mirror.mirror_token_expires_at.isoformat() if mirror.mirror_token_expires_at else None,
        token_status=_compute_token_status(mirror.mirror_token_expires_at),
        created_at=mirror.created_at.isoformat(),
        updated_at=mirror.updated_at.isoformat(),
        **eff,
    )


@router.put("/{mirror_id}", response_model=MirrorResponse)
async def update_mirror(
    mirror_id: int,
    mirror_update: MirrorUpdate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Update a mirror."""
    result = await db.execute(
        select(Mirror).where(Mirror.id == mirror_id)
    )
    mirror = result.scalar_one_or_none()

    if not mirror:
        raise HTTPException(status_code=404, detail="Mirror not found")

    # Update database fields.
    # Direction is pair-only, not updatable here.
    # Important: allow clearing overrides by accepting explicit nulls in the payload.
    # (FastAPI/Pydantic v2 tracks presence via `model_fields_set`.)
    fields = getattr(mirror_update, "model_fields_set", set())
    if "mirror_overwrite_diverged" in fields:
        mirror.mirror_overwrite_diverged = mirror_update.mirror_overwrite_diverged
    if "mirror_trigger_builds" in fields:
        mirror.mirror_trigger_builds = mirror_update.mirror_trigger_builds
    if "only_mirror_protected_branches" in fields:
        mirror.only_mirror_protected_branches = mirror_update.only_mirror_protected_branches
    if "mirror_branch_regex" in fields:
        mirror.mirror_branch_regex = mirror_update.mirror_branch_regex
    if "enabled" in fields:
        mirror.enabled = mirror_update.enabled

    # Best-effort: if this mirror is configured in GitLab, apply settings there too.
    # CRITICAL: Only commit DB changes if GitLab update succeeds to maintain consistency
    try:
        if mirror.mirror_id:
            pair_result = await db.execute(
                select(InstancePair).where(InstancePair.id == mirror.instance_pair_id)
            )
            pair = pair_result.scalar_one_or_none()
            if not pair:
                raise HTTPException(status_code=404, detail="Instance pair not found")

            # Direction comes from pair only
            direction = pair.mirror_direction

            # Resolve which GitLab project holds the remote mirror entry.
            instance_id = pair.source_instance_id if direction == "push" else pair.target_instance_id
            project_id = mirror.source_project_id if direction == "push" else mirror.target_project_id

            instance_result = await db.execute(
                select(GitLabInstance).where(GitLabInstance.id == instance_id)
            )
            instance = instance_result.scalar_one_or_none()
            if not instance:
                raise HTTPException(status_code=404, detail="GitLab instance not found")

            # Effective settings: mirror overrides -> pair defaults.
            overwrite_diverged = (
                mirror.mirror_overwrite_diverged
                if mirror.mirror_overwrite_diverged is not None
                else pair.mirror_overwrite_diverged
            )
            only_protected = (
                mirror.only_mirror_protected_branches
                if mirror.only_mirror_protected_branches is not None
                else pair.only_mirror_protected_branches
            )
            trigger_builds = (
                mirror.mirror_trigger_builds
                if mirror.mirror_trigger_builds is not None
                else pair.mirror_trigger_builds
            )
            branch_regex = (
                mirror.mirror_branch_regex
                if mirror.mirror_branch_regex is not None
                else pair.mirror_branch_regex
            )

            # Update the mirror in GitLab
            client = GitLabClient(instance.url, instance.encrypted_token)
            await _execute_gitlab_op(
                client=client,
                operation=lambda c: c.update_mirror(
                    project_id=project_id,
                    mirror_id=mirror.mirror_id,
                    enabled=mirror.enabled,
                    only_protected_branches=only_protected,
                    keep_divergent_refs=not overwrite_diverged,
                    trigger_builds=trigger_builds if direction == "pull" else None,
                    mirror_branch_regex=branch_regex if direction == "pull" else None,
                    mirror_user_id=instance.api_user_id if direction == "pull" else None,
                    mirror_direction=direction,
                ),
                operation_name=f"update_mirror({project_id}, {mirror.mirror_id})",
            )

        # Only commit if all operations succeeded
        await db.commit()
        await db.refresh(mirror)
    except HTTPException:
        # Re-raise HTTPExceptions as-is
        await db.rollback()
        raise
    except Exception as e:
        # Rollback DB changes if GitLab update or commit fails
        await db.rollback()
        logger.error(f"Failed to update mirror: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Failed to update mirror. Database changes have been rolled back."
        )

    pair_result = await db.execute(select(InstancePair).where(InstancePair.id == mirror.instance_pair_id))
    pair = pair_result.scalar_one_or_none()
    eff: dict[str, object] = {}
    if pair:
        src_result = await db.execute(select(GitLabInstance).where(GitLabInstance.id == pair.source_instance_id))
        tgt_result = await db.execute(select(GitLabInstance).where(GitLabInstance.id == pair.target_instance_id))
        eff = await _resolve_effective_settings(
            db,
            mirror=mirror,
            pair=pair,
            source_instance=src_result.scalar_one_or_none(),
            target_instance=tgt_result.scalar_one_or_none(),
        )

    return MirrorResponse(
        id=mirror.id,
        instance_pair_id=mirror.instance_pair_id,
        source_project_id=mirror.source_project_id,
        source_project_path=mirror.source_project_path,
        target_project_id=mirror.target_project_id,
        target_project_path=mirror.target_project_path,
        mirror_overwrite_diverged=mirror.mirror_overwrite_diverged,
        mirror_trigger_builds=mirror.mirror_trigger_builds,
        only_mirror_protected_branches=mirror.only_mirror_protected_branches,
        mirror_branch_regex=mirror.mirror_branch_regex,
        mirror_id=mirror.mirror_id,
        last_successful_update=mirror.last_successful_update.isoformat() if mirror.last_successful_update else None,
        last_update_status=mirror.last_update_status,
        enabled=mirror.enabled,
        created_at=mirror.created_at.isoformat(),
        updated_at=mirror.updated_at.isoformat(),
        **eff,
    )


async def _cleanup_mirror_from_gitlab(
    mirror: Mirror,
    db: AsyncSession
) -> tuple[bool, str | None, bool, str | None]:
    """
    Clean up a mirror from GitLab (delete mirror and token).

    Returns:
        Tuple of (gitlab_cleanup_failed, gitlab_error_msg, token_cleanup_failed, token_error_msg)
    """
    # Try to delete from GitLab (best effort)
    gitlab_cleanup_failed = False
    gitlab_error_msg = None

    try:
        if mirror.mirror_id:
            pair_result = await db.execute(
                select(InstancePair).where(InstancePair.id == mirror.instance_pair_id)
            )
            pair = pair_result.scalar_one_or_none()

            if pair:
                # Direction comes from pair only
                direction = pair.mirror_direction
                instance_id = pair.source_instance_id if direction == "push" else pair.target_instance_id
                project_id = mirror.source_project_id if direction == "push" else mirror.target_project_id

                instance_result = await db.execute(
                    select(GitLabInstance).where(GitLabInstance.id == instance_id)
                )
                instance = instance_result.scalar_one_or_none()

                if instance:
                    logger.info(f"Attempting to delete GitLab mirror {mirror.mirror_id} from project {project_id} on {instance.url}")
                    client = GitLabClient(instance.url, instance.encrypted_token)
                    await _execute_gitlab_op(
                        client=client,
                        operation=lambda c: c.delete_mirror(project_id, mirror.mirror_id),
                        operation_name=f"delete_mirror({project_id}, {mirror.mirror_id})",
                    )
                    logger.info(f"Successfully deleted GitLab mirror {mirror.mirror_id}")
                else:
                    logger.warning(f"GitLab instance not found for mirror {mirror.id}, skipping GitLab cleanup")
                    gitlab_cleanup_failed = True
                    gitlab_error_msg = "GitLab instance not found"
            else:
                logger.warning(f"Instance pair not found for mirror {mirror.id}, skipping GitLab cleanup")
                gitlab_cleanup_failed = True
                gitlab_error_msg = "Instance pair not found"
    except Exception as e:
        # Log the error but continue
        project_id_str = str(mirror.source_project_id if hasattr(mirror, 'source_project_id') else 'unknown')
        logger.error(f"Failed to delete mirror {mirror.mirror_id} from GitLab (project {project_id_str}): {str(e)}")
        gitlab_cleanup_failed = True
        gitlab_error_msg = str(e)

    # Try to delete project access token (best effort)
    token_cleanup_failed = False
    token_error_msg = None

    if mirror.gitlab_token_id is not None and mirror.token_project_id:
        try:
            # Get the instance that has the token
            pair_result = await db.execute(
                select(InstancePair).where(InstancePair.id == mirror.instance_pair_id)
            )
            pair = pair_result.scalar_one_or_none()

            if pair:
                direction = pair.mirror_direction
                # Token is on the "remote" project: target for push, source for pull
                token_instance_id = pair.target_instance_id if direction == "push" else pair.source_instance_id

                instance_result = await db.execute(
                    select(GitLabInstance).where(GitLabInstance.id == token_instance_id)
                )
                token_instance = instance_result.scalar_one_or_none()

                if token_instance:
                    logger.info(f"Deleting project access token {mirror.gitlab_token_id} from project {mirror.token_project_id}")
                    token_client = GitLabClient(token_instance.url, token_instance.encrypted_token)
                    await _execute_gitlab_op(
                        client=token_client,
                        operation=lambda c: c.delete_project_access_token(mirror.token_project_id, mirror.gitlab_token_id),
                        operation_name=f"delete_project_access_token({mirror.token_project_id}, {mirror.gitlab_token_id})",
                    )
                    logger.info(f"Successfully deleted project access token {mirror.gitlab_token_id}")
                else:
                    logger.warning(f"Token instance not found for mirror {mirror.id}, token may be orphaned")
                    token_cleanup_failed = True
                    token_error_msg = "Token instance not found"
        except Exception as e:
            logger.error(f"Failed to delete project access token {mirror.gitlab_token_id}: {str(e)}")
            token_cleanup_failed = True
            token_error_msg = str(e)

    return gitlab_cleanup_failed, gitlab_error_msg, token_cleanup_failed, token_error_msg


@router.delete("/{mirror_id}")
async def delete_mirror(
    mirror_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Delete a mirror."""
    result = await db.execute(
        select(Mirror).where(Mirror.id == mirror_id)
    )
    mirror = result.scalar_one_or_none()

    if not mirror:
        raise HTTPException(status_code=404, detail="Mirror not found")

    # Clean up from GitLab (best effort)
    gitlab_cleanup_failed, gitlab_error_msg, token_cleanup_failed, token_error_msg = await _cleanup_mirror_from_gitlab(mirror, db)

    # Always delete from database
    await db.delete(mirror)
    await db.commit()

    # Return status with warnings if cleanup failed
    response = {"status": "deleted"}
    warnings = []
    if gitlab_cleanup_failed:
        warnings.append(f"GitLab mirror cleanup failed: {gitlab_error_msg}. The mirror may still exist in GitLab.")
        response["gitlab_cleanup_failed"] = True
    if token_cleanup_failed:
        warnings.append(f"Token cleanup failed: {token_error_msg}. The token may still exist in GitLab.")
        response["token_cleanup_failed"] = True
    if warnings:
        response["warning"] = " ".join(warnings)

    return response


@router.post("/{mirror_id}/update")
async def trigger_mirror_update(
    mirror_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Trigger an immediate update of a mirror."""
    result = await db.execute(
        select(Mirror).where(Mirror.id == mirror_id)
    )
    mirror = result.scalar_one_or_none()

    if not mirror:
        raise HTTPException(status_code=404, detail="Mirror not found")

    if not mirror.mirror_id:
        raise HTTPException(status_code=400, detail="Mirror not configured in GitLab")

    # Check if mirror token has expired before attempting update
    if mirror.mirror_token_expires_at:
        token_status = _compute_token_status(mirror.mirror_token_expires_at)
        if token_status == "expired":
            raise HTTPException(
                status_code=400,
                detail="Mirror token has expired. Please rotate the token before triggering an update."
            )

    # Get instance and trigger update
    pair_result = await db.execute(
        select(InstancePair).where(InstancePair.id == mirror.instance_pair_id)
    )
    pair = pair_result.scalar_one_or_none()

    if not pair:
        raise HTTPException(status_code=404, detail="Instance pair not found")

    # Direction comes from pair only
    direction = pair.mirror_direction
    instance_id = pair.source_instance_id if direction == "push" else pair.target_instance_id
    project_id = mirror.source_project_id if direction == "push" else mirror.target_project_id

    instance_result = await db.execute(
        select(GitLabInstance).where(GitLabInstance.id == instance_id)
    )
    instance = instance_result.scalar_one_or_none()

    if not instance:
        raise HTTPException(status_code=404, detail="GitLab instance not found")

    try:
        client = GitLabClient(instance.url, instance.encrypted_token)
        await _execute_gitlab_op(
            client=client,
            operation=lambda c: c.trigger_mirror_update(project_id, mirror.mirror_id),
            operation_name=f"trigger_mirror_update({project_id}, {mirror.mirror_id})",
        )

        # Update status
        mirror.last_update_status = "updating"
        await db.commit()

        return {"status": "update_triggered"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to trigger update: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Failed to trigger mirror update. Check server logs for details."
        )


@router.post("/{mirror_id}/rotate-token")
async def rotate_mirror_token(
    mirror_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """
    Rotate the project access token for this mirror.

    This creates a new token and updates the mirror configuration in GitLab.
    The old token is automatically revoked.
    """
    result = await db.execute(
        select(Mirror).where(Mirror.id == mirror_id)
    )
    mirror = result.scalar_one_or_none()

    if not mirror:
        raise HTTPException(status_code=404, detail="Mirror not found")

    # Get instance pair
    pair_result = await db.execute(
        select(InstancePair).where(InstancePair.id == mirror.instance_pair_id)
    )
    pair = pair_result.scalar_one_or_none()

    if not pair:
        raise HTTPException(status_code=404, detail="Instance pair not found")

    # Direction comes from pair only
    direction = pair.mirror_direction

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
        raise HTTPException(status_code=404, detail="GitLab instance not found")

    # Determine which project gets the token and which instance has the mirror config
    if direction == "push":
        # Push: source → target, token on TARGET, mirror config on SOURCE
        token_instance = target_instance
        token_project_id = mirror.target_project_id
        token_project_path = mirror.target_project_path
        mirror_instance = source_instance
        mirror_project_id = mirror.source_project_id
        scopes = ["write_repository"]
    else:
        # Pull: target ← source, token on SOURCE, mirror config on TARGET
        token_instance = source_instance
        token_project_id = mirror.source_project_id
        token_project_path = mirror.source_project_path
        mirror_instance = target_instance
        mirror_project_id = mirror.target_project_id
        scopes = ["read_repository"]

    token_client = GitLabClient(token_instance.url, token_instance.encrypted_token)
    mirror_client = GitLabClient(mirror_instance.url, mirror_instance.encrypted_token)

    # Delete old token if it exists
    if mirror.gitlab_token_id is not None and mirror.token_project_id:
        try:
            logger.info(f"Deleting old token {mirror.gitlab_token_id} from project {mirror.token_project_id}")
            await _execute_gitlab_op(
                client=token_client,
                operation=lambda c: c.delete_project_access_token(mirror.token_project_id, mirror.gitlab_token_id),
                operation_name=f"delete_project_access_token({mirror.token_project_id}, {mirror.gitlab_token_id})",
            )
        except Exception as e:
            logger.warning(f"Failed to delete old token (may already be expired/deleted): {str(e)}")

    # Create new token
    token_name = f"mirror-maestro-{mirror.id}"
    expires_at = (datetime.utcnow() + timedelta(days=TOKEN_EXPIRY_DAYS)).strftime("%Y-%m-%d")

    try:
        token_result = await _execute_gitlab_op(
            client=token_client,
            operation=lambda c: c.create_project_access_token(
                project_id=token_project_id,
                name=token_name,
                scopes=scopes,
                expires_at=expires_at,
                access_level=40,  # Maintainer
            ),
            operation_name=f"create_project_access_token({token_project_id})",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create new token: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Failed to create new project access token. Check server logs for details."
        )

    # Validate token result
    new_token_value = token_result.get("token")
    new_token_id = token_result.get("id")
    if not new_token_value or new_token_id is None:
        # Log which fields are missing without exposing the actual token value
        missing_fields = []
        if not new_token_value:
            missing_fields.append("token")
        if new_token_id is None:
            missing_fields.append("id")
        logger.error(
            f"GitLab API returned incomplete token response. Missing fields: {missing_fields}. "
            f"Response keys: {list(token_result.keys()) if isinstance(token_result, dict) else 'not a dict'}"
        )
        raise HTTPException(
            status_code=500,
            detail="GitLab API returned incomplete token response (missing 'token' or 'id')"
        )

    # Build new authenticated URL
    authenticated_url = build_authenticated_url(
        token_instance,
        token_project_path,
        token_name=token_name,
        token_value=new_token_value,
    )

    # Update mirror in GitLab with new URL
    if mirror.mirror_id:
        try:
            # Get current effective settings
            effective_settings = await _resolve_effective_settings(db, mirror=mirror, pair=pair)

            await _execute_gitlab_op(
                client=mirror_client,
                operation=lambda c: c.update_mirror(
                    project_id=mirror_project_id,
                    mirror_id=mirror.mirror_id,
                    url=authenticated_url,
                    enabled=True,
                    only_protected_branches=effective_settings.get("only_mirror_protected_branches", False),
                    keep_divergent_refs=not effective_settings.get("mirror_overwrite_diverged", False),
                ),
                operation_name=f"update_mirror({mirror_project_id}, {mirror.mirror_id})",
            )
            logger.info(f"Updated mirror {mirror.mirror_id} with new token")
        except Exception as e:
            logger.error(f"Failed to update mirror with new token: {str(e)}")
            # Token was created but mirror update failed - still save the token
            # so user can manually fix if needed

    # Store new token details
    mirror.encrypted_mirror_token = encryption.encrypt(new_token_value)
    mirror.mirror_token_name = token_name
    mirror.mirror_token_expires_at = datetime.strptime(expires_at, "%Y-%m-%d")
    mirror.gitlab_token_id = new_token_id
    mirror.token_project_id = token_project_id

    await db.commit()
    await db.refresh(mirror)

    return {
        "status": "rotated",
        "token_expires_at": expires_at,
        "token_status": _compute_token_status(mirror.mirror_token_expires_at),
    }


async def _verify_single_mirror(
    db: AsyncSession,
    mirror: Mirror,
) -> MirrorVerifyResponse:
    """
    Verify a single mirror for orphan/drift status.

    Orphan: mirror_id exists in DB but the GitLab remote mirror doesn't exist.
    Drift: Settings in DB don't match settings on GitLab.
    """
    # If mirror was never created on GitLab, return early
    if mirror.mirror_id is None:
        return MirrorVerifyResponse(
            mirror_id=mirror.id,
            status="not_created",
            orphan=False,
            drift=[],
            gitlab_mirror=None,
            error=None,
        )

    # Get pair and instances
    pair_result = await db.execute(
        select(InstancePair).where(InstancePair.id == mirror.instance_pair_id)
    )
    pair = pair_result.scalar_one_or_none()
    if not pair:
        return MirrorVerifyResponse(
            mirror_id=mirror.id,
            status="error",
            orphan=False,
            drift=[],
            gitlab_mirror=None,
            error="Instance pair not found",
        )

    src_result = await db.execute(
        select(GitLabInstance).where(GitLabInstance.id == pair.source_instance_id)
    )
    tgt_result = await db.execute(
        select(GitLabInstance).where(GitLabInstance.id == pair.target_instance_id)
    )
    source_instance = src_result.scalar_one_or_none()
    target_instance = tgt_result.scalar_one_or_none()

    if not source_instance or not target_instance:
        return MirrorVerifyResponse(
            mirror_id=mirror.id,
            status="error",
            orphan=False,
            drift=[],
            gitlab_mirror=None,
            error="Source or target instance not found",
        )

    # Direction comes from pair
    direction = (pair.mirror_direction or "pull").lower()

    # Determine owner project (where the mirror config lives on GitLab)
    # Push: mirror config on source, Pull: mirror config on target
    if direction == "push":
        owner_project_id = mirror.source_project_id
        owner_instance = source_instance
    else:
        owner_project_id = mirror.target_project_id
        owner_instance = target_instance

    # Get mirrors from GitLab
    try:
        client = GitLabClient(owner_instance.url, owner_instance.encrypted_token)
        gitlab_mirrors = await _execute_gitlab_op(
            client=client,
            operation=lambda c: c.get_project_mirrors(owner_project_id) or [],
            operation_name=f"get_project_mirrors({owner_project_id})",
        )
    except HTTPException as e:
        return MirrorVerifyResponse(
            mirror_id=mirror.id,
            status="error",
            orphan=False,
            drift=[],
            gitlab_mirror=None,
            error=f"Failed to fetch GitLab mirrors: {e.detail}",
        )
    except Exception as e:
        return MirrorVerifyResponse(
            mirror_id=mirror.id,
            status="error",
            orphan=False,
            drift=[],
            gitlab_mirror=None,
            error=f"Failed to fetch GitLab mirrors: {str(e)}",
        )

    # Find our mirror by ID
    gitlab_mirror = None
    for gm in gitlab_mirrors:
        if gm.get("id") == mirror.mirror_id:
            gitlab_mirror = gm
            break

    # Check for orphan
    if gitlab_mirror is None:
        return MirrorVerifyResponse(
            mirror_id=mirror.id,
            status="orphan",
            orphan=True,
            drift=[],
            gitlab_mirror=None,
            error=None,
        )

    # Calculate effective settings
    effective = await _resolve_effective_settings(
        db,
        mirror=mirror,
        pair=pair,
        source_instance=source_instance,
        target_instance=target_instance,
    )

    # Check for drift
    drift_list: list[DriftDetail] = []

    # enabled
    expected_enabled = mirror.enabled
    actual_enabled = gitlab_mirror.get("enabled")
    if expected_enabled != actual_enabled:
        drift_list.append(DriftDetail(
            field="enabled",
            expected=expected_enabled,
            actual=actual_enabled,
        ))

    # only_protected_branches
    expected_protected = effective.get("effective_only_mirror_protected_branches")
    actual_protected = gitlab_mirror.get("only_protected_branches")
    if expected_protected != actual_protected:
        drift_list.append(DriftDetail(
            field="only_protected_branches",
            expected=expected_protected,
            actual=actual_protected,
        ))

    # keep_divergent_refs (inverse of mirror_overwrite_diverged)
    expected_overwrite = effective.get("effective_mirror_overwrite_diverged")
    if expected_overwrite is not None:
        expected_keep_divergent = not expected_overwrite
        actual_keep_divergent = gitlab_mirror.get("keep_divergent_refs")
        if expected_keep_divergent != actual_keep_divergent:
            drift_list.append(DriftDetail(
                field="keep_divergent_refs",
                expected=expected_keep_divergent,
                actual=actual_keep_divergent,
            ))

    # Pull-only settings
    if direction == "pull":
        # trigger_builds
        expected_trigger = effective.get("effective_mirror_trigger_builds")
        actual_trigger = gitlab_mirror.get("trigger_builds")
        if expected_trigger is not None and expected_trigger != actual_trigger:
            drift_list.append(DriftDetail(
                field="trigger_builds",
                expected=expected_trigger,
                actual=actual_trigger,
            ))

        # mirror_branch_regex
        expected_regex = effective.get("effective_mirror_branch_regex")
        actual_regex = gitlab_mirror.get("mirror_branch_regex")
        # Normalize empty strings to None for comparison
        if expected_regex == "":
            expected_regex = None
        if actual_regex == "":
            actual_regex = None
        if expected_regex != actual_regex:
            drift_list.append(DriftDetail(
                field="mirror_branch_regex",
                expected=expected_regex,
                actual=actual_regex,
            ))

    status = "healthy" if not drift_list else "drift"

    return MirrorVerifyResponse(
        mirror_id=mirror.id,
        status=status,
        orphan=False,
        drift=drift_list,
        gitlab_mirror=gitlab_mirror,
        error=None,
    )


@router.get("/{mirror_id}/verify", response_model=MirrorVerifyResponse)
async def verify_mirror(
    mirror_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials),
):
    """
    Verify a single mirror for orphan/drift status.

    - **orphan**: Mirror exists in DB but not on GitLab (was deleted externally)
    - **drift**: Mirror settings in DB don't match GitLab (was modified externally)
    """
    result = await db.execute(select(Mirror).where(Mirror.id == mirror_id))
    mirror = result.scalar_one_or_none()

    if not mirror:
        raise HTTPException(status_code=404, detail="Mirror not found")

    return await _verify_single_mirror(db, mirror)


@router.post("/verify", response_model=list[MirrorVerifyResponse])
async def verify_mirrors(
    req: MirrorVerifyRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials),
):
    """
    Verify multiple mirrors for orphan/drift status.

    Returns verification results for each requested mirror ID.
    Mirrors that don't exist will be omitted from the response.
    """
    if not req.mirror_ids:
        return []

    # Fetch all requested mirrors
    result = await db.execute(
        select(Mirror).where(Mirror.id.in_(req.mirror_ids))
    )
    mirrors = result.scalars().all()

    # Verify each mirror
    results: list[MirrorVerifyResponse] = []
    for mirror in mirrors:
        verification = await _verify_single_mirror(db, mirror)
        results.append(verification)

    return results
