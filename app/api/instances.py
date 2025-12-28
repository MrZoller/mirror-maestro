from typing import List
from urllib.parse import urlparse
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, delete, or_
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, ConfigDict, field_validator

from app.database import get_db
from app.models import GitLabInstance, InstancePair, Mirror
from app.core.auth import verify_credentials
from app.core.encryption import encryption
from app.core.gitlab_client import GitLabClient


router = APIRouter(prefix="/api/instances", tags=["instances"])


class GitLabInstanceCreate(BaseModel):
    name: str
    url: str
    token: str
    description: str = ""

    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        """Validate instance name is not empty and has reasonable length."""
        if not v or not v.strip():
            raise ValueError("Instance name cannot be empty")
        v = v.strip()
        if len(v) > 100:
            raise ValueError("Instance name must be 100 characters or less")
        return v

    @field_validator('url')
    @classmethod
    def validate_url(cls, v):
        """Validate GitLab instance URL format."""
        if not v or not v.strip():
            raise ValueError("Instance URL cannot be empty")
        v = v.strip()
        # Add scheme if missing for validation
        test_url = v if '://' in v else f'https://{v}'
        try:
            parsed = urlparse(test_url)
            if not parsed.hostname:
                raise ValueError("Invalid URL: no hostname found")
            # Return original value (normalization happens in the API logic)
            return v
        except Exception as e:
            raise ValueError(f"Invalid URL format: {str(e)}")

    @field_validator('token')
    @classmethod
    def validate_token(cls, v):
        """Validate token is not empty."""
        if not v or not v.strip():
            raise ValueError("Access token cannot be empty")
        return v.strip()


class GitLabInstanceUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    token: str | None = None
    description: str | None = None

    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        """Validate instance name if provided."""
        if v is not None:
            if not v.strip():
                raise ValueError("Instance name cannot be empty")
            v = v.strip()
            if len(v) > 100:
                raise ValueError("Instance name must be 100 characters or less")
        return v

    @field_validator('url')
    @classmethod
    def validate_url(cls, v):
        """Validate GitLab instance URL if provided."""
        if v is not None:
            if not v.strip():
                raise ValueError("Instance URL cannot be empty")
            v = v.strip()
            test_url = v if '://' in v else f'https://{v}'
            try:
                parsed = urlparse(test_url)
                if not parsed.hostname:
                    raise ValueError("Invalid URL: no hostname found")
                return v
            except Exception as e:
                raise ValueError(f"Invalid URL format: {str(e)}")
        return v

    @field_validator('token')
    @classmethod
    def validate_token(cls, v):
        """Validate token if provided."""
        if v is not None and not v.strip():
            raise ValueError("Access token cannot be empty")
        return v.strip() if v else None


class GitLabInstanceResponse(BaseModel):
    id: int
    name: str
    url: str
    token_user_id: int | None = None
    token_username: str | None = None
    description: str | None
    created_at: str
    updated_at: str

    model_config = ConfigDict(from_attributes=True)


@router.get("", response_model=List[GitLabInstanceResponse])
async def list_instances(
    search: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """
    List all GitLab instances with optional filtering.

    Query parameters:
    - search: Search in instance name, URL, and description (case-insensitive)
    """
    query = select(GitLabInstance)

    if search is not None and search.strip():
        search_term = f"%{search.strip().lower()}%"
        query = query.where(
            (GitLabInstance.name.ilike(search_term)) |
            (GitLabInstance.url.ilike(search_term)) |
            (GitLabInstance.description.ilike(search_term))
        )

    result = await db.execute(query)
    instances = result.scalars().all()
    return [
        GitLabInstanceResponse(
            id=inst.id,
            name=inst.name,
            url=inst.url,
            token_user_id=inst.api_user_id,
            token_username=inst.api_username,
            description=inst.description,
            created_at=inst.created_at.isoformat(),
            updated_at=inst.updated_at.isoformat()
        )
        for inst in instances
    ]


@router.post("", response_model=GitLabInstanceResponse)
async def create_instance(
    instance: GitLabInstanceCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Create a new GitLab instance."""
    # Encrypt the token
    encrypted_token = encryption.encrypt(instance.token)

    # Test connection first
    try:
        client = GitLabClient(instance.url, encrypted_token)
        if not client.test_connection():
            raise HTTPException(status_code=400, detail="Failed to connect to GitLab instance")
    except HTTPException:
        raise
    except Exception as e:
        import logging
        logging.error(f"Failed to connect to GitLab: {str(e)}")
        raise HTTPException(
            status_code=400,
            detail="Failed to connect to GitLab instance. Check server logs for details."
        )

    # Best-effort: resolve token user for a friendly display / defaults
    token_user_id = None
    token_username = None
    try:
        u = client.get_current_user()
        token_user_id = u.get("id")
        token_username = u.get("username")
    except Exception:
        pass

    # Create the instance
    db_instance = GitLabInstance(
        name=instance.name,
        url=instance.url,
        encrypted_token=encrypted_token,
        api_user_id=token_user_id,
        api_username=token_username,
        description=instance.description
    )
    db.add(db_instance)
    await db.commit()
    await db.refresh(db_instance)

    return GitLabInstanceResponse(
        id=db_instance.id,
        name=db_instance.name,
        url=db_instance.url,
        token_user_id=db_instance.api_user_id,
        token_username=db_instance.api_username,
        description=db_instance.description,
        created_at=db_instance.created_at.isoformat(),
        updated_at=db_instance.updated_at.isoformat()
    )


@router.get("/{instance_id}", response_model=GitLabInstanceResponse)
async def get_instance(
    instance_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Get a specific GitLab instance."""
    result = await db.execute(
        select(GitLabInstance).where(GitLabInstance.id == instance_id)
    )
    instance = result.scalar_one_or_none()

    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")

    return GitLabInstanceResponse(
        id=instance.id,
        name=instance.name,
        url=instance.url,
        token_user_id=instance.api_user_id,
        token_username=instance.api_username,
        description=instance.description,
        created_at=instance.created_at.isoformat(),
        updated_at=instance.updated_at.isoformat()
    )


@router.put("/{instance_id}", response_model=GitLabInstanceResponse)
async def update_instance(
    instance_id: int,
    instance_update: GitLabInstanceUpdate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Update a GitLab instance."""
    result = await db.execute(
        select(GitLabInstance).where(GitLabInstance.id == instance_id)
    )
    instance = result.scalar_one_or_none()

    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")

    # Update fields.
    #
    # Important: allow clearing fields by accepting explicit nulls in the payload
    # (FastAPI/Pydantic v2 tracks presence via `model_fields_set`).
    fields = getattr(instance_update, "model_fields_set", set())
    if "name" in fields:
        instance.name = instance_update.name

    if "url" in fields:
        # Changing an instance URL can invalidate existing pairs/mirrors because
        # the instance is identified by its DB id throughout the configuration.
        # Keep this safe: don't allow changing the URL once the instance is used
        # by any instance pair.
        pair_res = await db.execute(
            select(InstancePair.id).where(
                or_(
                    InstancePair.source_instance_id == instance_id,
                    InstancePair.target_instance_id == instance_id,
                )
            ).limit(1)
        )
        if pair_res.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=400,
                detail="Instance URL cannot be changed once it is used by an instance pair. Create a new instance instead.",
            )
        instance.url = instance_update.url

    if "token" in fields:
        if instance_update.token is not None:
            instance.encrypted_token = encryption.encrypt(instance_update.token)
            # Best-effort: refresh token user identity
            try:
                client = GitLabClient(instance.url, instance.encrypted_token)
                u = client.get_current_user()
                instance.api_user_id = u.get("id")
                instance.api_username = u.get("username")
            except Exception:
                instance.api_user_id = None
                instance.api_username = None

    if "description" in fields:
        instance.description = instance_update.description

    await db.commit()
    await db.refresh(instance)

    return GitLabInstanceResponse(
        id=instance.id,
        name=instance.name,
        url=instance.url,
        token_user_id=instance.api_user_id,
        token_username=instance.api_username,
        description=instance.description,
        created_at=instance.created_at.isoformat(),
        updated_at=instance.updated_at.isoformat()
    )


@router.delete("/{instance_id}")
async def delete_instance(
    instance_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Delete a GitLab instance."""
    result = await db.execute(
        select(GitLabInstance).where(GitLabInstance.id == instance_id)
    )
    instance = result.scalar_one_or_none()

    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")

    # Cascade-delete related entities:
    # - Mirrors that belong to pairs that reference this instance
    # - Group defaults for those pairs
    # - The pairs themselves
    # - Group access tokens attached to this instance
    #
    # NOTE: This is intentionally implemented at the application layer since the
    # schema does not currently enforce FK relationships with ON DELETE CASCADE.
    #
    # CRITICAL: All delete operations must succeed atomically or be rolled back together
    try:
        pair_ids_res = await db.execute(
            select(InstancePair.id).where(
                or_(
                    InstancePair.source_instance_id == instance_id,
                    InstancePair.target_instance_id == instance_id,
                )
            )
        )
        pair_ids = list(pair_ids_res.scalars().all())

        if pair_ids:
            # Delete mirrors first (they reference pairs)
            await db.execute(delete(Mirror).where(Mirror.instance_pair_id.in_(pair_ids)))
            # Delete pairs (they reference the instance)
            await db.execute(delete(InstancePair).where(InstancePair.id.in_(pair_ids)))

        # Finally delete the instance itself
        await db.delete(instance)

        # Commit all changes atomically
        await db.commit()
    except Exception as e:
        # Rollback all changes if any operation fails to maintain data integrity
        await db.rollback()
        import logging
        logging.error(f"Failed to delete instance {instance_id}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Failed to delete instance. Database changes have been rolled back to maintain data integrity."
        )

    return {"status": "deleted"}


@router.get("/{instance_id}/projects")
async def get_instance_projects(
    instance_id: int,
    search: str | None = None,
    per_page: int = 50,
    page: int = 1,
    get_all: bool = False,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Get projects from a GitLab instance."""
    result = await db.execute(
        select(GitLabInstance).where(GitLabInstance.id == instance_id)
    )
    instance = result.scalar_one_or_none()

    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")

    try:
        client = GitLabClient(instance.url, instance.encrypted_token)
        # Clamp pagination to keep requests bounded.
        per_page = max(1, min(int(per_page), 100))
        page = max(1, int(page))
        projects = client.get_projects(search=search, per_page=per_page, page=page, get_all=get_all)
        return {"projects": projects}
    except Exception as e:
        import logging
        logging.error(f"Failed to fetch projects: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch projects from GitLab. Check server logs for details."
        )


@router.get("/{instance_id}/groups")
async def get_instance_groups(
    instance_id: int,
    search: str | None = None,
    per_page: int = 50,
    page: int = 1,
    get_all: bool = False,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Get groups from a GitLab instance."""
    result = await db.execute(
        select(GitLabInstance).where(GitLabInstance.id == instance_id)
    )
    instance = result.scalar_one_or_none()

    if not instance:
        raise HTTPException(status_code=404, detail="Instance not found")

    try:
        client = GitLabClient(instance.url, instance.encrypted_token)
        per_page = max(1, min(int(per_page), 100))
        page = max(1, int(page))
        groups = client.get_groups(search=search, per_page=per_page, page=page, get_all=get_all)
        return {"groups": groups}
    except Exception as e:
        import logging
        logging.error(f"Failed to fetch groups: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch groups from GitLab. Check server logs for details."
        )
