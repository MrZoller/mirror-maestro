from typing import List
import re
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, ConfigDict, field_validator
from datetime import datetime, timedelta

from app.database import get_db
from app.models import Mirror, InstancePair, GitLabInstance
from app.core.auth import verify_credentials
from app.core.gitlab_client import GitLabClient
from app.core.encryption import encryption
from urllib.parse import urlparse, quote

# Token expiration: 1 year from creation
TOKEN_EXPIRY_DAYS = 365


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
    mirror_protected_branches: bool | None = None
    mirror_overwrite_diverged: bool | None = None
    mirror_trigger_builds: bool | None = None
    only_mirror_protected_branches: bool | None = None
    mirror_branch_regex: str | None = None
    mirror_user_id: int | None = None
    enabled: bool = True

    @field_validator('mirror_branch_regex')
    @classmethod
    def validate_branch_regex(cls, v):
        """Validate that branch regex is valid regex syntax."""
        if v is not None and v.strip():
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


class MirrorUpdate(BaseModel):
    # Direction cannot be changed - it's determined by the instance pair
    mirror_protected_branches: bool | None = None
    mirror_overwrite_diverged: bool | None = None
    mirror_trigger_builds: bool | None = None
    only_mirror_protected_branches: bool | None = None
    mirror_branch_regex: str | None = None
    mirror_user_id: int | None = None
    enabled: bool | None = None

    @field_validator('mirror_branch_regex')
    @classmethod
    def validate_branch_regex(cls, v):
        """Validate that branch regex is valid regex syntax."""
        if v is not None and v.strip():
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
    mirror_protected_branches: bool | None
    mirror_overwrite_diverged: bool | None
    mirror_trigger_builds: bool | None
    only_mirror_protected_branches: bool | None
    mirror_branch_regex: str | None
    mirror_user_id: int | None
    # Effective settings (mirror overrides -> pair defaults)
    effective_mirror_direction: str | None = None
    effective_mirror_overwrite_diverged: bool | None = None
    effective_mirror_trigger_builds: bool | None = None
    effective_only_mirror_protected_branches: bool | None = None
    effective_mirror_branch_regex: str | None = None
    effective_mirror_user_id: int | None = None
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
    mirror_user_id = (
        mirror.mirror_user_id
        if mirror.mirror_user_id is not None
        else pair.mirror_user_id
    )

    # Pull-only settings: if direction is push, treat as not applicable.
    if direction == "push":
        trigger_builds = None
        branch_regex = None
        mirror_user_id = None

    # If nothing set anywhere, prefer the API token's user for pull mirrors.
    if direction == "pull" and mirror_user_id is None:
        owner = target_instance
        if owner is None:
            # Best-effort: fetch only if needed (avoid extra queries otherwise)
            res = await db.execute(select(GitLabInstance).where(GitLabInstance.id == pair.target_instance_id))
            owner = res.scalar_one_or_none()
        mirror_user_id = owner.api_user_id if owner else None

    return {
        "effective_mirror_direction": direction or None,
        "effective_mirror_overwrite_diverged": overwrite_diverged,
        "effective_only_mirror_protected_branches": only_protected,
        "effective_mirror_trigger_builds": trigger_builds,
        "effective_mirror_branch_regex": branch_regex,
        "effective_mirror_user_id": mirror_user_id,
    }


@router.get("", response_model=List[MirrorResponse])
async def list_mirrors(
    instance_pair_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """List all mirrors, optionally filtered by instance pair."""
    query = select(Mirror)
    if instance_pair_id is not None:
        query = query.where(Mirror.instance_pair_id == instance_pair_id)

    result = await db.execute(query)
    mirrors = result.scalars().all()

    # Best-effort caches to avoid N+1 on common paths (e.g., filtered by pair)
    pair_cache: dict[int, InstancePair] = {}
    instance_cache: dict[int, GitLabInstance] = {}

    async def _get_pair(pid: int) -> InstancePair | None:
        if pid in pair_cache:
            return pair_cache[pid]
        r = await db.execute(select(InstancePair).where(InstancePair.id == pid))
        p = r.scalar_one_or_none()
        if p:
            pair_cache[pid] = p
        return p

    async def _get_instance(iid: int) -> GitLabInstance | None:
        if iid in instance_cache:
            return instance_cache[iid]
        r = await db.execute(select(GitLabInstance).where(GitLabInstance.id == iid))
        inst = r.scalar_one_or_none()
        if inst:
            instance_cache[iid] = inst
        return inst

    out: list[MirrorResponse] = []
    for mirror in mirrors:
        pair = await _get_pair(mirror.instance_pair_id)
        eff: dict[str, object] = {}
        if pair:
            src = await _get_instance(pair.source_instance_id)
            tgt = await _get_instance(pair.target_instance_id)
            eff = await _resolve_effective_settings(db, mirror=mirror, pair=pair, source_instance=src, target_instance=tgt)

        out.append(
            MirrorResponse(
                id=mirror.id,
                instance_pair_id=mirror.instance_pair_id,
                source_project_id=mirror.source_project_id,
                source_project_path=mirror.source_project_path,
                target_project_id=mirror.target_project_id,
                target_project_path=mirror.target_project_path,
                mirror_protected_branches=mirror.mirror_protected_branches,
                mirror_overwrite_diverged=mirror.mirror_overwrite_diverged,
                mirror_trigger_builds=mirror.mirror_trigger_builds,
                only_mirror_protected_branches=mirror.only_mirror_protected_branches,
                mirror_branch_regex=mirror.mirror_branch_regex,
                mirror_user_id=mirror.mirror_user_id,
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

    return out


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

    # Check for duplicate mirror (same pair + source + target projects)
    duplicate_check = await db.execute(
        select(Mirror).where(
            Mirror.instance_pair_id == mirror.instance_pair_id,
            Mirror.source_project_id == mirror.source_project_id,
            Mirror.target_project_id == mirror.target_project_id
        )
    )
    existing_mirror = duplicate_check.scalar_one_or_none()
    if existing_mirror:
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"Mirror already exists for this project pair",
                "existing_mirror_id": existing_mirror.id,
                "source_project": mirror.source_project_path,
                "target_project": mirror.target_project_path
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

    protected_branches = (
        mirror.mirror_protected_branches
        if mirror.mirror_protected_branches is not None
        else pair.mirror_protected_branches
    )
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
    mirror_user_id_setting = (
        mirror.mirror_user_id
        if mirror.mirror_user_id is not None
        else pair.mirror_user_id
    )
    # If nothing set anywhere, prefer the API token's user for pull mirrors.
    if mirror_user_id_setting is None and direction == "pull":
        mirror_user_id_setting = target_instance.api_user_id

    # Determine which project needs the token and create it
    # Push: token on target (allows pushing to it)
    # Pull: token on source (allows reading from it)
    if direction == "push":
        token_instance = target_instance
        token_project_id = mirror.target_project_id
        token_project_path = mirror.target_project_path
        token_scopes = ["write_repository"]
    else:
        token_instance = source_instance
        token_project_id = mirror.source_project_id
        token_project_path = mirror.source_project_path
        token_scopes = ["read_repository"]

    # Calculate token expiration (1 year from now)
    token_expires_at = datetime.utcnow() + timedelta(days=TOKEN_EXPIRY_DAYS)
    token_expires_str = token_expires_at.strftime("%Y-%m-%d")

    # Create project access token
    token_info = None
    encrypted_token = None
    gitlab_token_id = None
    token_name = None

    try:
        token_client = GitLabClient(token_instance.url, token_instance.encrypted_token)
        # Use a unique token name that includes a timestamp for uniqueness
        token_name = f"mirror-maestro-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        token_info = token_client.create_project_access_token(
            project_id=token_project_id,
            name=token_name,
            scopes=token_scopes,
            expires_at=token_expires_str,
        )
        gitlab_token_id = token_info.get("id")
        plaintext_token = token_info.get("token")
        if plaintext_token:
            encrypted_token = encryption.encrypt(plaintext_token)
        logging.info(f"Created project access token '{token_name}' on project {token_project_id}")
    except Exception as e:
        logging.warning(f"Failed to create project access token: {str(e)}. Mirror will be created without token.")
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
            result = client.create_push_mirror(
                mirror.source_project_id,
                remote_url,
                enabled=mirror.enabled,
                keep_divergent_refs=not overwrite_diverged,
                only_protected_branches=only_protected,
            )
            gitlab_mirror_id = result.get("id")
        else:  # pull
            # For pull mirrors, configure on target to pull from source
            client = GitLabClient(target_instance.url, target_instance.encrypted_token)

            # GitLab effectively supports only one pull mirror per project.
            existing = client.get_project_mirrors(mirror.target_project_id)
            existing_pull = [m for m in (existing or []) if str(m.get("mirror_direction") or "").lower() == "pull"]
            if existing_pull:
                # Cleanup the token we just created
                if gitlab_token_id:
                    try:
                        token_client.delete_project_access_token(token_project_id, gitlab_token_id)
                    except Exception:
                        logging.warning(f"Failed to cleanup token {gitlab_token_id} after mirror conflict")
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": (
                            "Target project already has a pull mirror configured in GitLab. "
                            "GitLab allows only one pull mirror per project. "
                            "Remove the existing pull mirror first."
                        ),
                        "existing_pull_mirrors": existing_pull,
                    },
                )

            result = client.create_pull_mirror(
                mirror.target_project_id,
                remote_url,
                enabled=mirror.enabled,
                only_protected_branches=only_protected,
                keep_divergent_refs=not overwrite_diverged,
                trigger_builds=trigger_builds,
                mirror_branch_regex=branch_regex,
                mirror_user_id=mirror_user_id_setting,
            )
            gitlab_mirror_id = result.get("id")
    except HTTPException:
        raise
    except Exception as e:
        # Cleanup the token we created
        if gitlab_token_id:
            try:
                token_client.delete_project_access_token(token_project_id, gitlab_token_id)
            except Exception:
                logging.warning(f"Failed to cleanup token {gitlab_token_id} after mirror creation failed")
        logging.error(f"Failed to create mirror in GitLab: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Failed to create mirror in GitLab. Check server logs for details."
        )

    # Create the mirror record in database
    # CRITICAL: If DB commit fails, we must clean up the GitLab mirror and token
    db_mirror = Mirror(
        instance_pair_id=mirror.instance_pair_id,
        source_project_id=mirror.source_project_id,
        source_project_path=mirror.source_project_path,
        target_project_id=mirror.target_project_id,
        target_project_path=mirror.target_project_path,
        # Direction is determined by pair, not stored on mirror
        mirror_protected_branches=mirror.mirror_protected_branches,
        mirror_overwrite_diverged=mirror.mirror_overwrite_diverged,
        mirror_trigger_builds=mirror.mirror_trigger_builds,
        only_mirror_protected_branches=mirror.only_mirror_protected_branches,
        mirror_branch_regex=mirror.mirror_branch_regex,
        mirror_user_id=mirror.mirror_user_id,
        mirror_id=gitlab_mirror_id,
        enabled=mirror.enabled,
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

        logging.warning(f"Database commit failed. Attempting cleanup...")

        # Cleanup: Delete the GitLab mirror that was just created
        if gitlab_mirror_id:
            try:
                cleanup_instance = source_instance if direction == "push" else target_instance
                cleanup_project_id = mirror.source_project_id if direction == "push" else mirror.target_project_id
                cleanup_client = GitLabClient(cleanup_instance.url, cleanup_instance.encrypted_token)
                cleanup_client.delete_mirror(cleanup_project_id, gitlab_mirror_id)
                logging.info(f"Successfully cleaned up orphaned GitLab mirror {gitlab_mirror_id}")
            except Exception as cleanup_error:
                logging.error(f"Failed to cleanup GitLab mirror {gitlab_mirror_id}: {str(cleanup_error)}")

        # Cleanup: Delete the project access token
        if gitlab_token_id:
            try:
                token_client.delete_project_access_token(token_project_id, gitlab_token_id)
                logging.info(f"Successfully cleaned up orphaned token {gitlab_token_id}")
            except Exception as cleanup_error:
                logging.error(f"Failed to cleanup token {gitlab_token_id}: {str(cleanup_error)}")

        logging.error(f"Failed to save mirror to database: {str(db_error)}")
        raise HTTPException(
            status_code=500,
            detail="Failed to save mirror to database. GitLab resources have been cleaned up."
        )

    return MirrorResponse(
        id=db_mirror.id,
        instance_pair_id=db_mirror.instance_pair_id,
        source_project_id=db_mirror.source_project_id,
        source_project_path=db_mirror.source_project_path,
        target_project_id=db_mirror.target_project_id,
        target_project_path=db_mirror.target_project_path,
        mirror_protected_branches=db_mirror.mirror_protected_branches,
        mirror_overwrite_diverged=db_mirror.mirror_overwrite_diverged,
        mirror_trigger_builds=db_mirror.mirror_trigger_builds,
        only_mirror_protected_branches=db_mirror.only_mirror_protected_branches,
        mirror_branch_regex=db_mirror.mirror_branch_regex,
        mirror_user_id=db_mirror.mirror_user_id,
        effective_mirror_direction=direction,
        effective_mirror_overwrite_diverged=overwrite_diverged,
        effective_mirror_trigger_builds=trigger_builds if direction == "pull" else None,
        effective_only_mirror_protected_branches=only_protected,
        effective_mirror_branch_regex=branch_regex if direction == "pull" else None,
        effective_mirror_user_id=mirror_user_id_setting if direction == "pull" else None,
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
    existing = client.get_project_mirrors(owner_project_id) or []
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
    existing = client.get_project_mirrors(owner_project_id) or []
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
            client.delete_mirror(owner_project_id, mid)
            deleted_ids.append(mid)
        except Exception as e:
            import logging
            logging.error(f"Failed to delete existing mirror {mid} in GitLab: {str(e)}")
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
        mirror_protected_branches=mirror.mirror_protected_branches,
        mirror_overwrite_diverged=mirror.mirror_overwrite_diverged,
        mirror_trigger_builds=mirror.mirror_trigger_builds,
        only_mirror_protected_branches=mirror.only_mirror_protected_branches,
        mirror_branch_regex=mirror.mirror_branch_regex,
        mirror_user_id=mirror.mirror_user_id,
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
    if "mirror_protected_branches" in fields:
        mirror.mirror_protected_branches = mirror_update.mirror_protected_branches
    if "mirror_overwrite_diverged" in fields:
        mirror.mirror_overwrite_diverged = mirror_update.mirror_overwrite_diverged
    if "mirror_trigger_builds" in fields:
        mirror.mirror_trigger_builds = mirror_update.mirror_trigger_builds
    if "only_mirror_protected_branches" in fields:
        mirror.only_mirror_protected_branches = mirror_update.only_mirror_protected_branches
    if "mirror_branch_regex" in fields:
        mirror.mirror_branch_regex = mirror_update.mirror_branch_regex
    if "mirror_user_id" in fields:
        mirror.mirror_user_id = mirror_update.mirror_user_id
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
            mirror_user_id = (
                mirror.mirror_user_id
                if mirror.mirror_user_id is not None
                else pair.mirror_user_id
            )
            if mirror_user_id is None and direction == "pull":
                mirror_user_id = instance.api_user_id

            # Update the mirror in GitLab
            client = GitLabClient(instance.url, instance.encrypted_token)
            client.update_mirror(
                project_id=project_id,
                mirror_id=mirror.mirror_id,
                enabled=mirror.enabled,
                only_protected_branches=only_protected,
                keep_divergent_refs=not overwrite_diverged,
                trigger_builds=trigger_builds if direction == "pull" else None,
                mirror_branch_regex=branch_regex if direction == "pull" else None,
                mirror_user_id=mirror_user_id if direction == "pull" else None,
                mirror_direction=direction,
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
        import logging
        logging.error(f"Failed to update mirror: {str(e)}")
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
        mirror_protected_branches=mirror.mirror_protected_branches,
        mirror_overwrite_diverged=mirror.mirror_overwrite_diverged,
        mirror_trigger_builds=mirror.mirror_trigger_builds,
        only_mirror_protected_branches=mirror.only_mirror_protected_branches,
        mirror_branch_regex=mirror.mirror_branch_regex,
        mirror_user_id=mirror.mirror_user_id,
        mirror_id=mirror.mirror_id,
        last_successful_update=mirror.last_successful_update.isoformat() if mirror.last_successful_update else None,
        last_update_status=mirror.last_update_status,
        enabled=mirror.enabled,
        created_at=mirror.created_at.isoformat(),
        updated_at=mirror.updated_at.isoformat(),
        **eff,
    )


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

    # Try to delete from GitLab (best effort)
    # If GitLab deletion fails, we still delete from database but warn the user
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
                    import logging
                    logging.info(f"Attempting to delete GitLab mirror {mirror.mirror_id} from project {project_id} on {instance.url}")
                    client = GitLabClient(instance.url, instance.encrypted_token)
                    client.delete_mirror(project_id, mirror.mirror_id)
                    logging.info(f"Successfully deleted GitLab mirror {mirror.mirror_id}")
                else:
                    import logging
                    logging.warning(f"GitLab instance not found for mirror {mirror_id}, skipping GitLab cleanup")
                    gitlab_cleanup_failed = True
                    gitlab_error_msg = "GitLab instance not found"
            else:
                import logging
                logging.warning(f"Instance pair not found for mirror {mirror_id}, skipping GitLab cleanup")
                gitlab_cleanup_failed = True
                gitlab_error_msg = "Instance pair not found"
    except Exception as e:
        # Log the error but continue with database deletion
        import logging
        logging.error(f"Failed to delete mirror {mirror.mirror_id} from GitLab (project {project_id if 'project_id' in locals() else 'unknown'}): {str(e)}")
        gitlab_cleanup_failed = True
        gitlab_error_msg = str(e)

    # Try to delete project access token (best effort)
    token_cleanup_failed = False
    token_error_msg = None

    if mirror.gitlab_token_id and mirror.token_project_id:
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
                    import logging
                    logging.info(f"Deleting project access token {mirror.gitlab_token_id} from project {mirror.token_project_id}")
                    token_client = GitLabClient(token_instance.url, token_instance.encrypted_token)
                    token_client.delete_project_access_token(mirror.token_project_id, mirror.gitlab_token_id)
                    logging.info(f"Successfully deleted project access token {mirror.gitlab_token_id}")
                else:
                    import logging
                    logging.warning(f"Token instance not found for mirror {mirror_id}, token may be orphaned")
                    token_cleanup_failed = True
                    token_error_msg = "Token instance not found"
        except Exception as e:
            import logging
            logging.error(f"Failed to delete project access token {mirror.gitlab_token_id}: {str(e)}")
            token_cleanup_failed = True
            token_error_msg = str(e)

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
        client.trigger_mirror_update(project_id, mirror.mirror_id)

        # Update status
        mirror.last_update_status = "updating"
        await db.commit()

        return {"status": "update_triggered"}
    except Exception as e:
        import logging
        logging.error(f"Failed to trigger update: {str(e)}")
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
    import logging

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
    if mirror.gitlab_token_id and mirror.token_project_id:
        try:
            logging.info(f"Deleting old token {mirror.gitlab_token_id} from project {mirror.token_project_id}")
            token_client.delete_project_access_token(mirror.token_project_id, mirror.gitlab_token_id)
        except Exception as e:
            logging.warning(f"Failed to delete old token (may already be expired/deleted): {str(e)}")

    # Create new token
    token_name = f"mirror-maestro-{mirror.id}"
    expires_at = (datetime.utcnow() + timedelta(days=TOKEN_EXPIRY_DAYS)).strftime("%Y-%m-%d")

    try:
        token_result = token_client.create_project_access_token(
            project_id=token_project_id,
            name=token_name,
            scopes=scopes,
            expires_at=expires_at,
            access_level=40,  # Maintainer
        )
    except Exception as e:
        logging.error(f"Failed to create new token: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create new project access token: {str(e)}"
        )

    # Build new authenticated URL
    new_token_value = token_result["token"]
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
            effective_settings = _resolve_effective_settings(mirror, pair)

            mirror_client.update_mirror(
                project_id=mirror_project_id,
                mirror_id=mirror.mirror_id,
                url=authenticated_url,
                enabled=True,
                only_protected_branches=effective_settings.get("only_mirror_protected_branches", False),
                keep_divergent_refs=not effective_settings.get("mirror_overwrite_diverged", False),
            )
            logging.info(f"Updated mirror {mirror.mirror_id} with new token")
        except Exception as e:
            logging.error(f"Failed to update mirror with new token: {str(e)}")
            # Token was created but mirror update failed - still save the token
            # so user can manually fix if needed

    # Store new token details
    mirror.encrypted_mirror_token = encryption.encrypt(new_token_value)
    mirror.mirror_token_name = token_name
    mirror.mirror_token_expires_at = datetime.strptime(expires_at, "%Y-%m-%d")
    mirror.gitlab_token_id = token_result["id"]
    mirror.token_project_id = token_project_id

    await db.commit()
    await db.refresh(mirror)

    return {
        "status": "rotated",
        "token_expires_at": expires_at,
        "token_status": _compute_token_status(mirror.mirror_token_expires_at),
    }
