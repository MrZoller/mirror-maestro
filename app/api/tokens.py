from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, ConfigDict

from app.database import get_db
from app.models import GroupAccessToken, GitLabInstance
from app.core.auth import verify_credentials
from app.core.encryption import encryption


router = APIRouter(prefix="/api/tokens", tags=["tokens"])


class GroupAccessTokenCreate(BaseModel):
    gitlab_instance_id: int
    group_path: str
    token: str
    token_name: str


class GroupAccessTokenUpdate(BaseModel):
    group_path: str | None = None
    token: str | None = None
    token_name: str | None = None


class GroupAccessTokenResponse(BaseModel):
    id: int
    gitlab_instance_id: int
    group_path: str
    token_name: str
    created_at: str
    updated_at: str

    model_config = ConfigDict(from_attributes=True)


@router.get("", response_model=List[GroupAccessTokenResponse])
async def list_tokens(
    gitlab_instance_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """List all group access tokens, optionally filtered by GitLab instance."""
    query = select(GroupAccessToken)
    if gitlab_instance_id is not None:
        query = query.where(GroupAccessToken.gitlab_instance_id == gitlab_instance_id)

    result = await db.execute(query)
    tokens = result.scalars().all()

    return [
        GroupAccessTokenResponse(
            id=token.id,
            gitlab_instance_id=token.gitlab_instance_id,
            group_path=token.group_path,
            token_name=token.token_name,
            created_at=token.created_at.isoformat(),
            updated_at=token.updated_at.isoformat()
        )
        for token in tokens
    ]


@router.post("", response_model=GroupAccessTokenResponse)
async def create_token(
    token: GroupAccessTokenCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Create a new group access token."""
    # Verify instance exists
    instance_result = await db.execute(
        select(GitLabInstance).where(GitLabInstance.id == token.gitlab_instance_id)
    )
    instance = instance_result.scalar_one_or_none()

    if not instance:
        raise HTTPException(status_code=404, detail="GitLab instance not found")

    # Check if token already exists for this group/instance
    existing_result = await db.execute(
        select(GroupAccessToken).where(
            GroupAccessToken.gitlab_instance_id == token.gitlab_instance_id,
            GroupAccessToken.group_path == token.group_path
        )
    )
    existing = existing_result.scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Token already exists for group '{token.group_path}' on this instance"
        )

    # Encrypt the token
    encrypted_token = encryption.encrypt(token.token)

    # Create the token record
    db_token = GroupAccessToken(
        gitlab_instance_id=token.gitlab_instance_id,
        group_path=token.group_path,
        encrypted_token=encrypted_token,
        token_name=token.token_name
    )
    db.add(db_token)
    await db.commit()
    await db.refresh(db_token)

    return GroupAccessTokenResponse(
        id=db_token.id,
        gitlab_instance_id=db_token.gitlab_instance_id,
        group_path=db_token.group_path,
        token_name=db_token.token_name,
        created_at=db_token.created_at.isoformat(),
        updated_at=db_token.updated_at.isoformat()
    )


@router.get("/{token_id}", response_model=GroupAccessTokenResponse)
async def get_token(
    token_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Get a specific group access token."""
    result = await db.execute(
        select(GroupAccessToken).where(GroupAccessToken.id == token_id)
    )
    token = result.scalar_one_or_none()

    if not token:
        raise HTTPException(status_code=404, detail="Token not found")

    return GroupAccessTokenResponse(
        id=token.id,
        gitlab_instance_id=token.gitlab_instance_id,
        group_path=token.group_path,
        token_name=token.token_name,
        created_at=token.created_at.isoformat(),
        updated_at=token.updated_at.isoformat()
    )


@router.put("/{token_id}", response_model=GroupAccessTokenResponse)
async def update_token(
    token_id: int,
    token_update: GroupAccessTokenUpdate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Update a group access token."""
    result = await db.execute(
        select(GroupAccessToken).where(GroupAccessToken.id == token_id)
    )
    token = result.scalar_one_or_none()

    if not token:
        raise HTTPException(status_code=404, detail="Token not found")

    # Update fields
    if token_update.group_path is not None:
        token.group_path = token_update.group_path
    if token_update.token is not None:
        token.encrypted_token = encryption.encrypt(token_update.token)
    if token_update.token_name is not None:
        token.token_name = token_update.token_name

    await db.commit()
    await db.refresh(token)

    return GroupAccessTokenResponse(
        id=token.id,
        gitlab_instance_id=token.gitlab_instance_id,
        group_path=token.group_path,
        token_name=token.token_name,
        created_at=token.created_at.isoformat(),
        updated_at=token.updated_at.isoformat()
    )


@router.delete("/{token_id}")
async def delete_token(
    token_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Delete a group access token."""
    result = await db.execute(
        select(GroupAccessToken).where(GroupAccessToken.id == token_id)
    )
    token = result.scalar_one_or_none()

    if not token:
        raise HTTPException(status_code=404, detail="Token not found")

    await db.delete(token)
    await db.commit()

    return {"status": "deleted"}
