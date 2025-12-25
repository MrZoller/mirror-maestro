from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, ConfigDict
from datetime import datetime

from app.database import get_db
from app.models import Mirror, InstancePair, GitLabInstance, GroupAccessToken, GroupMirrorDefaults
from app.core.auth import verify_credentials
from app.core.gitlab_client import GitLabClient
from app.core.encryption import encryption
from urllib.parse import urlparse, quote


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


async def get_authenticated_url(
    db: AsyncSession,
    instance: GitLabInstance,
    project_path: str
) -> str:
    """
    Build an authenticated Git URL for mirroring.

    Supports multi-level group paths by searching for tokens from most specific
    to least specific. For example, for "platform/core/api-gateway":
    1. Try "platform/core" (parent group, excluding project name)
    2. Try "platform" (top-level group)

    This allows tokens to be created at any level of the group hierarchy.
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

    # Extract group path from project path
    # For "platform/core/api-gateway", parts are ["platform", "core", "api-gateway"]
    path_parts = project_path.split("/")

    # The last part is the project name, everything before is the namespace/group
    if len(path_parts) < 2:
        # Single-level project (no group), unlikely to have a token
        return _build_git_url(scheme=scheme, hostname=hostname, port=port, project_path=project_path)

    # Try to find a token, starting from the most specific group path
    # For "platform/core/api-gateway", try: "platform/core", then "platform"
    group_token = None
    for i in range(len(path_parts) - 1, 0, -1):
        candidate_group_path = "/".join(path_parts[:i])

        token_result = await db.execute(
            select(GroupAccessToken).where(
                GroupAccessToken.gitlab_instance_id == instance.id,
                GroupAccessToken.group_path == candidate_group_path
            )
        )
        group_token = token_result.scalar_one_or_none()

        if group_token:
            # Found a token at this level
            break

    if group_token:
        # Decrypt the token
        token_value = encryption.decrypt(group_token.encrypted_token)
        # Build authenticated URL: https://token_name:token@hostname/path.git
        # NOTE: userinfo must be percent-encoded to avoid URL corruption/injection.
        return _build_git_url(
            scheme=scheme,
            hostname=hostname,
            port=port,
            project_path=project_path,
            username=group_token.token_name,
            password=token_value,
        )
    else:
        # No token found at any level - return unauthenticated URL
        # In production, you might want to raise an exception here
        return _build_git_url(scheme=scheme, hostname=hostname, port=port, project_path=project_path)


def _namespace_candidates(project_path: str) -> list[str]:
    """
    For "platform/core/api-gateway" return ["platform/core", "platform"].
    Returns [] for projects without a namespace.
    """
    parts = [p for p in (project_path or "").split("/") if p]
    if len(parts) < 2:
        return []
    ns_parts = parts[:-1]
    out: list[str] = []
    for i in range(len(ns_parts), 0, -1):
        out.append("/".join(ns_parts[:i]))
    return out


async def _get_group_defaults(
    db: AsyncSession,
    *,
    instance_pair_id: int,
    source_project_path: str,
    target_project_path: str,
) -> GroupMirrorDefaults | None:
    """
    Find the most specific group defaults for this pair.

    We check both source and target namespaces (most specific -> least), since
    users may mirror across different namespaces.
    """
    candidates: list[str] = []
    seen: set[str] = set()
    for c in _namespace_candidates(source_project_path) + _namespace_candidates(target_project_path):
        if c in seen:
            continue
        seen.add(c)
        candidates.append(c)

    for group_path in candidates:
        res = await db.execute(
            select(GroupMirrorDefaults).where(
                GroupMirrorDefaults.instance_pair_id == instance_pair_id,
                GroupMirrorDefaults.group_path == group_path,
            )
        )
        row = res.scalar_one_or_none()
        if row:
            return row
    return None


class MirrorCreate(BaseModel):
    instance_pair_id: int
    source_project_id: int
    source_project_path: str
    target_project_id: int
    target_project_path: str
    mirror_direction: str | None = None
    mirror_protected_branches: bool | None = None
    mirror_overwrite_diverged: bool | None = None
    mirror_trigger_builds: bool | None = None
    only_mirror_protected_branches: bool | None = None
    mirror_branch_regex: str | None = None
    mirror_user_id: int | None = None
    enabled: bool = True


class MirrorPreflight(BaseModel):
    """
    Preflight a mirror creation by checking GitLab for existing remote mirrors
    on the owning project.
    """
    instance_pair_id: int
    source_project_id: int
    source_project_path: str
    target_project_id: int
    target_project_path: str
    mirror_direction: str | None = None


class MirrorPreflightResponse(BaseModel):
    effective_direction: str
    owner_project_id: int
    existing_mirrors: list[dict]
    existing_same_direction: list[dict]


class MirrorRemoveExisting(BaseModel):
    """
    Remove existing GitLab remote mirrors on the owning project for the effective direction.
    If `remote_mirror_ids` is omitted, deletes all mirrors for that direction.
    """
    instance_pair_id: int
    source_project_id: int
    source_project_path: str
    target_project_id: int
    target_project_path: str
    mirror_direction: str | None = None
    remote_mirror_ids: list[int] | None = None


class MirrorUpdate(BaseModel):
    mirror_direction: str | None = None
    mirror_protected_branches: bool | None = None
    mirror_overwrite_diverged: bool | None = None
    mirror_trigger_builds: bool | None = None
    only_mirror_protected_branches: bool | None = None
    mirror_branch_regex: str | None = None
    mirror_user_id: int | None = None
    enabled: bool | None = None


class MirrorResponse(BaseModel):
    id: int
    instance_pair_id: int
    source_project_id: int
    source_project_path: str
    target_project_id: int
    target_project_path: str
    mirror_direction: str | None
    mirror_protected_branches: bool | None
    mirror_overwrite_diverged: bool | None
    mirror_trigger_builds: bool | None
    only_mirror_protected_branches: bool | None
    mirror_branch_regex: str | None
    mirror_user_id: int | None
    # Effective settings (mirror overrides -> group defaults -> pair defaults)
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
    created_at: str
    updated_at: str

    model_config = ConfigDict(from_attributes=True)


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
    mirror overrides -> group defaults -> pair defaults (+ pull mirror user fallback).

    Note: some settings are pull-only in GitLab and are intentionally treated as
    not applicable for push mirrors.
    """
    group_defaults = await _get_group_defaults(
        db,
        instance_pair_id=pair.id,
        source_project_path=mirror.source_project_path,
        target_project_path=mirror.target_project_path,
    )

    direction = (
        mirror.mirror_direction
        or (group_defaults.mirror_direction if group_defaults else None)
        or pair.mirror_direction
    )
    direction = (direction or "").lower()

    overwrite_diverged = (
        mirror.mirror_overwrite_diverged
        if mirror.mirror_overwrite_diverged is not None
        else (
            group_defaults.mirror_overwrite_diverged
            if group_defaults and group_defaults.mirror_overwrite_diverged is not None
            else pair.mirror_overwrite_diverged
        )
    )
    only_protected = (
        mirror.only_mirror_protected_branches
        if mirror.only_mirror_protected_branches is not None
        else (
            group_defaults.only_mirror_protected_branches
            if group_defaults and group_defaults.only_mirror_protected_branches is not None
            else pair.only_mirror_protected_branches
        )
    )
    trigger_builds = (
        mirror.mirror_trigger_builds
        if mirror.mirror_trigger_builds is not None
        else (
            group_defaults.mirror_trigger_builds
            if group_defaults and group_defaults.mirror_trigger_builds is not None
            else pair.mirror_trigger_builds
        )
    )
    branch_regex = (
        mirror.mirror_branch_regex
        if mirror.mirror_branch_regex is not None
        else (
            group_defaults.mirror_branch_regex
            if group_defaults and group_defaults.mirror_branch_regex is not None
            else pair.mirror_branch_regex
        )
    )
    mirror_user_id = (
        mirror.mirror_user_id
        if mirror.mirror_user_id is not None
        else (
            group_defaults.mirror_user_id
            if group_defaults and group_defaults.mirror_user_id is not None
            else pair.mirror_user_id
        )
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
                mirror_direction=mirror.mirror_direction,
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
        )

    return out


@router.post("", response_model=MirrorResponse)
async def create_mirror(
    mirror: MirrorCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Create a new mirror."""
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

    group_defaults = await _get_group_defaults(
        db,
        instance_pair_id=pair.id,
        source_project_path=mirror.source_project_path,
        target_project_path=mirror.target_project_path,
    )

    # Effective defaults: mirror overrides -> group overrides -> pair defaults
    direction = (
        mirror.mirror_direction
        or (group_defaults.mirror_direction if group_defaults else None)
        or pair.mirror_direction
    )

    # Validate direction is set and valid
    if not direction or direction not in ("push", "pull"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mirror direction: {direction}. Must be 'push' or 'pull'"
        )

    protected_branches = (
        mirror.mirror_protected_branches
        if mirror.mirror_protected_branches is not None
        else (group_defaults.mirror_protected_branches if group_defaults and group_defaults.mirror_protected_branches is not None else pair.mirror_protected_branches)
    )
    overwrite_diverged = (
        mirror.mirror_overwrite_diverged
        if mirror.mirror_overwrite_diverged is not None
        else (group_defaults.mirror_overwrite_diverged if group_defaults and group_defaults.mirror_overwrite_diverged is not None else pair.mirror_overwrite_diverged)
    )
    trigger_builds = (
        mirror.mirror_trigger_builds
        if mirror.mirror_trigger_builds is not None
        else (group_defaults.mirror_trigger_builds if group_defaults and group_defaults.mirror_trigger_builds is not None else pair.mirror_trigger_builds)
    )
    only_protected = (
        mirror.only_mirror_protected_branches
        if mirror.only_mirror_protected_branches is not None
        else (group_defaults.only_mirror_protected_branches if group_defaults and group_defaults.only_mirror_protected_branches is not None else pair.only_mirror_protected_branches)
    )
    branch_regex = (
        mirror.mirror_branch_regex
        if mirror.mirror_branch_regex is not None
        else (group_defaults.mirror_branch_regex if group_defaults and group_defaults.mirror_branch_regex is not None else pair.mirror_branch_regex)
    )
    mirror_user_id = (
        mirror.mirror_user_id
        if mirror.mirror_user_id is not None
        else (group_defaults.mirror_user_id if group_defaults and group_defaults.mirror_user_id is not None else pair.mirror_user_id)
    )
    # If nothing set anywhere, prefer the API token's user for pull mirrors.
    if mirror_user_id is None and direction == "pull":
        mirror_user_id = target_instance.api_user_id

    # Create the mirror in GitLab
    gitlab_mirror_id = None
    try:
        if direction == "push":
            # For push mirrors, configure on source to push to target
            client = GitLabClient(source_instance.url, source_instance.encrypted_token)
            # Build authenticated target URL with group access token
            target_url = await get_authenticated_url(db, target_instance, mirror.target_project_path)
            result = client.create_push_mirror(
                mirror.source_project_id,
                target_url,
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

            # Build authenticated source URL with group access token
            source_url = await get_authenticated_url(db, source_instance, mirror.source_project_path)
            result = client.create_pull_mirror(
                mirror.target_project_id,
                source_url,
                enabled=mirror.enabled,
                only_protected_branches=only_protected,
                keep_divergent_refs=not overwrite_diverged,
                trigger_builds=trigger_builds,
                mirror_branch_regex=branch_regex,
                mirror_user_id=mirror_user_id,
            )
            gitlab_mirror_id = result.get("id")
    except HTTPException:
        raise
    except Exception as e:
        # Log the full error but return a generic message to avoid exposing sensitive details
        import logging
        logging.error(f"Failed to create mirror in GitLab: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Failed to create mirror in GitLab. Check server logs for details."
        )

    # Create the mirror record in database
    db_mirror = Mirror(
        instance_pair_id=mirror.instance_pair_id,
        source_project_id=mirror.source_project_id,
        source_project_path=mirror.source_project_path,
        target_project_id=mirror.target_project_id,
        target_project_path=mirror.target_project_path,
        # Persist resolved direction to avoid ambiguity if pair/group defaults change later.
        mirror_direction=direction,
        mirror_protected_branches=mirror.mirror_protected_branches,
        mirror_overwrite_diverged=mirror.mirror_overwrite_diverged,
        mirror_trigger_builds=mirror.mirror_trigger_builds,
        only_mirror_protected_branches=mirror.only_mirror_protected_branches,
        mirror_branch_regex=mirror.mirror_branch_regex,
        mirror_user_id=mirror.mirror_user_id,
        mirror_id=gitlab_mirror_id,
        enabled=mirror.enabled,
        last_update_status="pending"
    )
    db.add(db_mirror)
    await db.commit()
    await db.refresh(db_mirror)

    return MirrorResponse(
        id=db_mirror.id,
        instance_pair_id=db_mirror.instance_pair_id,
        source_project_id=db_mirror.source_project_id,
        source_project_path=db_mirror.source_project_path,
        target_project_id=db_mirror.target_project_id,
        target_project_path=db_mirror.target_project_path,
        mirror_direction=db_mirror.mirror_direction,
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
        effective_mirror_user_id=mirror_user_id if direction == "pull" else None,
        mirror_id=db_mirror.mirror_id,
        last_successful_update=db_mirror.last_successful_update.isoformat() if db_mirror.last_successful_update else None,
        last_update_status=db_mirror.last_update_status,
        enabled=db_mirror.enabled,
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

    group_defaults = await _get_group_defaults(
        db,
        instance_pair_id=pair.id,
        source_project_path=req.source_project_path,
        target_project_path=req.target_project_path,
    )

    direction = (
        req.mirror_direction
        or (group_defaults.mirror_direction if group_defaults else None)
        or pair.mirror_direction
        or "pull"
    )
    direction = (direction or "pull").lower()

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

    group_defaults = await _get_group_defaults(
        db,
        instance_pair_id=pair.id,
        source_project_path=req.source_project_path,
        target_project_path=req.target_project_path,
    )

    direction = (
        req.mirror_direction
        or (group_defaults.mirror_direction if group_defaults else None)
        or pair.mirror_direction
        or "pull"
    )
    direction = (direction or "pull").lower()

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
        mirror_direction=mirror.mirror_direction,
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

    if mirror_update.mirror_direction is not None and mirror.mirror_id:
        # GitLab doesn't support switching direction in-place; UI expects delete/re-add.
        raise HTTPException(status_code=400, detail="Mirror direction cannot be changed after creation. Delete and recreate the mirror.")

    # Update database fields.
    #
    # Important: allow clearing overrides by accepting explicit nulls in the payload.
    # (FastAPI/Pydantic v2 tracks presence via `model_fields_set`.)
    fields = getattr(mirror_update, "model_fields_set", set())
    if "mirror_direction" in fields:
        mirror.mirror_direction = mirror_update.mirror_direction
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
    if mirror.mirror_id:
        pair_result = await db.execute(
            select(InstancePair).where(InstancePair.id == mirror.instance_pair_id)
        )
        pair = pair_result.scalar_one_or_none()
        if not pair:
            raise HTTPException(status_code=404, detail="Instance pair not found")

        group_defaults = await _get_group_defaults(
            db,
            instance_pair_id=pair.id,
            source_project_path=mirror.source_project_path,
            target_project_path=mirror.target_project_path,
        )

        direction = (
            mirror.mirror_direction
            or (group_defaults.mirror_direction if group_defaults else None)
            or pair.mirror_direction
        )

        # Resolve which GitLab project holds the remote mirror entry.
        instance_id = pair.source_instance_id if direction == "push" else pair.target_instance_id
        project_id = mirror.source_project_id if direction == "push" else mirror.target_project_id

        instance_result = await db.execute(
            select(GitLabInstance).where(GitLabInstance.id == instance_id)
        )
        instance = instance_result.scalar_one_or_none()
        if not instance:
            raise HTTPException(status_code=404, detail="GitLab instance not found")

        # Effective settings: mirror overrides -> group overrides -> pair defaults.
        overwrite_diverged = (
            mirror.mirror_overwrite_diverged
            if mirror.mirror_overwrite_diverged is not None
            else (group_defaults.mirror_overwrite_diverged if group_defaults and group_defaults.mirror_overwrite_diverged is not None else pair.mirror_overwrite_diverged)
        )
        only_protected = (
            mirror.only_mirror_protected_branches
            if mirror.only_mirror_protected_branches is not None
            else (group_defaults.only_mirror_protected_branches if group_defaults and group_defaults.only_mirror_protected_branches is not None else pair.only_mirror_protected_branches)
        )
        trigger_builds = (
            mirror.mirror_trigger_builds
            if mirror.mirror_trigger_builds is not None
            else (group_defaults.mirror_trigger_builds if group_defaults and group_defaults.mirror_trigger_builds is not None else pair.mirror_trigger_builds)
        )
        branch_regex = (
            mirror.mirror_branch_regex
            if mirror.mirror_branch_regex is not None
            else (group_defaults.mirror_branch_regex if group_defaults and group_defaults.mirror_branch_regex is not None else pair.mirror_branch_regex)
        )
        mirror_user_id = (
            mirror.mirror_user_id
            if mirror.mirror_user_id is not None
            else (group_defaults.mirror_user_id if group_defaults and group_defaults.mirror_user_id is not None else pair.mirror_user_id)
        )
        if mirror_user_id is None and direction == "pull":
            mirror_user_id = instance.api_user_id

        try:
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
        except Exception as e:
            await db.rollback()
            import logging
            logging.error(f"Failed to update mirror in GitLab: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail="Failed to update mirror in GitLab. Check server logs for details."
            )

    await db.commit()
    await db.refresh(mirror)

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
        mirror_direction=mirror.mirror_direction,
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
    try:
        if mirror.mirror_id:
            pair_result = await db.execute(
                select(InstancePair).where(InstancePair.id == mirror.instance_pair_id)
            )
            pair = pair_result.scalar_one_or_none()

            if pair:
                group_defaults = await _get_group_defaults(
                    db,
                    instance_pair_id=pair.id,
                    source_project_path=mirror.source_project_path,
                    target_project_path=mirror.target_project_path,
                )
                direction = (
                    mirror.mirror_direction
                    or (group_defaults.mirror_direction if group_defaults else None)
                    or pair.mirror_direction
                )
                instance_id = pair.source_instance_id if direction == "push" else pair.target_instance_id
                project_id = mirror.source_project_id if direction == "push" else mirror.target_project_id

                instance_result = await db.execute(
                    select(GitLabInstance).where(GitLabInstance.id == instance_id)
                )
                instance = instance_result.scalar_one_or_none()

                if instance:
                    client = GitLabClient(instance.url, instance.encrypted_token)
                    client.delete_mirror(project_id, mirror.mirror_id)
    except Exception:
        pass  # Continue even if GitLab deletion fails

    await db.delete(mirror)
    await db.commit()

    return {"status": "deleted"}


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

    group_defaults = await _get_group_defaults(
        db,
        instance_pair_id=pair.id,
        source_project_path=mirror.source_project_path,
        target_project_path=mirror.target_project_path,
    )
    direction = (
        mirror.mirror_direction
        or (group_defaults.mirror_direction if group_defaults else None)
        or pair.mirror_direction
    )
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
