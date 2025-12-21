from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, ConfigDict
from datetime import datetime

from app.database import get_db
from app.models import Mirror, InstancePair, GitLabInstance, GroupAccessToken
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
    enabled: bool = True


class MirrorUpdate(BaseModel):
    mirror_direction: str | None = None
    mirror_protected_branches: bool | None = None
    mirror_overwrite_diverged: bool | None = None
    mirror_trigger_builds: bool | None = None
    only_mirror_protected_branches: bool | None = None
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
    mirror_id: int | None
    last_successful_update: str | None
    last_update_status: str | None
    enabled: bool
    created_at: str
    updated_at: str

    model_config = ConfigDict(from_attributes=True)


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

    return [
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
            mirror_id=mirror.mirror_id,
            last_successful_update=mirror.last_successful_update.isoformat() if mirror.last_successful_update else None,
            last_update_status=mirror.last_update_status,
            enabled=mirror.enabled,
            created_at=mirror.created_at.isoformat(),
            updated_at=mirror.updated_at.isoformat()
        )
        for mirror in mirrors
    ]


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

    # Use pair defaults if mirror-specific settings not provided
    direction = mirror.mirror_direction or pair.mirror_direction
    protected_branches = mirror.mirror_protected_branches if mirror.mirror_protected_branches is not None else pair.mirror_protected_branches
    overwrite_diverged = mirror.mirror_overwrite_diverged if mirror.mirror_overwrite_diverged is not None else pair.mirror_overwrite_diverged
    trigger_builds = mirror.mirror_trigger_builds if mirror.mirror_trigger_builds is not None else pair.mirror_trigger_builds
    only_protected = mirror.only_mirror_protected_branches if mirror.only_mirror_protected_branches is not None else pair.only_mirror_protected_branches

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
                only_protected_branches=only_protected
            )
            gitlab_mirror_id = result.get("project_id")
        else:  # pull
            # For pull mirrors, configure on target to pull from source
            client = GitLabClient(target_instance.url, target_instance.encrypted_token)
            # Build authenticated source URL with group access token
            source_url = await get_authenticated_url(db, source_instance, mirror.source_project_path)
            result = client.create_pull_mirror(
                mirror.target_project_id,
                source_url,
                enabled=mirror.enabled,
                only_protected_branches=only_protected
            )
            gitlab_mirror_id = result.get("id")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create mirror in GitLab: {str(e)}")

    # Create the mirror record in database
    db_mirror = Mirror(
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
        mirror_id=db_mirror.mirror_id,
        last_successful_update=db_mirror.last_successful_update.isoformat() if db_mirror.last_successful_update else None,
        last_update_status=db_mirror.last_update_status,
        enabled=db_mirror.enabled,
        created_at=db_mirror.created_at.isoformat(),
        updated_at=db_mirror.updated_at.isoformat()
    )


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
        mirror_id=mirror.mirror_id,
        last_successful_update=mirror.last_successful_update.isoformat() if mirror.last_successful_update else None,
        last_update_status=mirror.last_update_status,
        enabled=mirror.enabled,
        created_at=mirror.created_at.isoformat(),
        updated_at=mirror.updated_at.isoformat()
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

    # Update database fields
    if mirror_update.mirror_direction is not None:
        mirror.mirror_direction = mirror_update.mirror_direction
    if mirror_update.mirror_protected_branches is not None:
        mirror.mirror_protected_branches = mirror_update.mirror_protected_branches
    if mirror_update.mirror_overwrite_diverged is not None:
        mirror.mirror_overwrite_diverged = mirror_update.mirror_overwrite_diverged
    if mirror_update.mirror_trigger_builds is not None:
        mirror.mirror_trigger_builds = mirror_update.mirror_trigger_builds
    if mirror_update.only_mirror_protected_branches is not None:
        mirror.only_mirror_protected_branches = mirror_update.only_mirror_protected_branches
    if mirror_update.enabled is not None:
        mirror.enabled = mirror_update.enabled

    await db.commit()
    await db.refresh(mirror)

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
        mirror_id=mirror.mirror_id,
        last_successful_update=mirror.last_successful_update.isoformat() if mirror.last_successful_update else None,
        last_update_status=mirror.last_update_status,
        enabled=mirror.enabled,
        created_at=mirror.created_at.isoformat(),
        updated_at=mirror.updated_at.isoformat()
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
                direction = mirror.mirror_direction or pair.mirror_direction
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

    direction = mirror.mirror_direction or pair.mirror_direction
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
        raise HTTPException(status_code=500, detail=f"Failed to trigger update: {str(e)}")
