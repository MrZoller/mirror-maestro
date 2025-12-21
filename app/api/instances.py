from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, ConfigDict

from app.database import get_db
from app.models import GitLabInstance
from app.core.auth import verify_credentials
from app.core.encryption import encryption
from app.core.gitlab_client import GitLabClient


router = APIRouter(prefix="/api/instances", tags=["instances"])


class GitLabInstanceCreate(BaseModel):
    name: str
    url: str
    token: str
    description: str = ""


class GitLabInstanceUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    token: str | None = None
    description: str | None = None


class GitLabInstanceResponse(BaseModel):
    id: int
    name: str
    url: str
    description: str | None
    created_at: str
    updated_at: str

    model_config = ConfigDict(from_attributes=True)


@router.get("", response_model=List[GitLabInstanceResponse])
async def list_instances(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """List all GitLab instances."""
    result = await db.execute(select(GitLabInstance))
    instances = result.scalars().all()
    return [
        GitLabInstanceResponse(
            id=inst.id,
            name=inst.name,
            url=inst.url,
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
    # Test connection first
    try:
        client = GitLabClient(instance.url, encryption.encrypt(instance.token))
        if not client.test_connection():
            raise HTTPException(status_code=400, detail="Failed to connect to GitLab instance")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to connect to GitLab: {str(e)}")

    # Encrypt the token
    encrypted_token = encryption.encrypt(instance.token)

    # Create the instance
    db_instance = GitLabInstance(
        name=instance.name,
        url=instance.url,
        encrypted_token=encrypted_token,
        description=instance.description
    )
    db.add(db_instance)
    await db.commit()
    await db.refresh(db_instance)

    return GitLabInstanceResponse(
        id=db_instance.id,
        name=db_instance.name,
        url=db_instance.url,
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

    # Update fields
    if instance_update.name is not None:
        instance.name = instance_update.name
    if instance_update.url is not None:
        instance.url = instance_update.url
    if instance_update.token is not None:
        instance.encrypted_token = encryption.encrypt(instance_update.token)
    if instance_update.description is not None:
        instance.description = instance_update.description

    await db.commit()
    await db.refresh(instance)

    return GitLabInstanceResponse(
        id=instance.id,
        name=instance.name,
        url=instance.url,
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

    await db.delete(instance)
    await db.commit()

    return {"status": "deleted"}


@router.get("/{instance_id}/projects")
async def get_instance_projects(
    instance_id: int,
    search: str | None = None,
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
        projects = client.get_projects(search=search)
        return {"projects": projects}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch projects: {str(e)}")


@router.get("/{instance_id}/groups")
async def get_instance_groups(
    instance_id: int,
    search: str | None = None,
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
        groups = client.get_groups(search=search)
        return {"groups": groups}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch groups: {str(e)}")
