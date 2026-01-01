"""API endpoints for managing issue mirror configurations."""

from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import MirrorIssueConfig, Mirror
from app.core.auth import verify_credentials


router = APIRouter(prefix="/api/issue-mirrors", tags=["issue-mirrors"])


# Pydantic Schemas

class MirrorIssueConfigCreate(BaseModel):
    """Schema for creating an issue mirror configuration."""
    mirror_id: int
    enabled: bool = True
    sync_comments: bool = True
    sync_labels: bool = True
    sync_attachments: bool = True
    sync_weight: bool = True
    sync_time_estimate: bool = True
    sync_time_spent: bool = True
    sync_closed_issues: bool = False
    update_existing: bool = True
    sync_interval_minutes: int = 15


class MirrorIssueConfigUpdate(BaseModel):
    """Schema for updating an issue mirror configuration."""
    enabled: Optional[bool] = None
    sync_comments: Optional[bool] = None
    sync_labels: Optional[bool] = None
    sync_attachments: Optional[bool] = None
    sync_weight: Optional[bool] = None
    sync_time_estimate: Optional[bool] = None
    sync_time_spent: Optional[bool] = None
    sync_closed_issues: Optional[bool] = None
    update_existing: Optional[bool] = None
    sync_interval_minutes: Optional[int] = None


class MirrorIssueConfigResponse(BaseModel):
    """Schema for issue mirror configuration response."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    mirror_id: int
    enabled: bool
    sync_comments: bool
    sync_labels: bool
    sync_attachments: bool
    sync_weight: bool
    sync_time_estimate: bool
    sync_time_spent: bool
    sync_closed_issues: bool
    update_existing: bool
    last_sync_at: Optional[datetime]
    last_sync_status: Optional[str]
    last_sync_error: Optional[str]
    next_sync_at: Optional[datetime]
    sync_interval_minutes: int
    created_at: datetime
    updated_at: datetime


# API Endpoints

@router.get("", response_model=List[MirrorIssueConfigResponse])
async def list_issue_configs(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """List all issue mirror configurations."""
    result = await db.execute(select(MirrorIssueConfig))
    return result.scalars().all()


@router.get("/{config_id}", response_model=MirrorIssueConfigResponse)
async def get_issue_config(
    config_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Get a specific issue mirror configuration."""
    result = await db.execute(
        select(MirrorIssueConfig).where(MirrorIssueConfig.id == config_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(
            status_code=404,
            detail=f"Issue mirror configuration {config_id} not found"
        )
    return config


@router.get("/by-mirror/{mirror_id}", response_model=MirrorIssueConfigResponse)
async def get_issue_config_by_mirror(
    mirror_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Get issue mirror configuration for a specific mirror."""
    result = await db.execute(
        select(MirrorIssueConfig).where(MirrorIssueConfig.mirror_id == mirror_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(
            status_code=404,
            detail=f"No issue mirror configuration found for mirror {mirror_id}"
        )
    return config


@router.post("", response_model=MirrorIssueConfigResponse, status_code=201)
async def create_issue_config(
    config_data: MirrorIssueConfigCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Create a new issue mirror configuration."""
    # Verify mirror exists
    mirror_result = await db.execute(
        select(Mirror).where(Mirror.id == config_data.mirror_id)
    )
    mirror = mirror_result.scalar_one_or_none()
    if not mirror:
        raise HTTPException(
            status_code=404,
            detail=f"Mirror {config_data.mirror_id} not found"
        )

    # Check if configuration already exists
    existing_result = await db.execute(
        select(MirrorIssueConfig).where(
            MirrorIssueConfig.mirror_id == config_data.mirror_id
        )
    )
    existing = existing_result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Issue mirror configuration already exists for mirror {config_data.mirror_id}"
        )

    # Create new configuration
    config = MirrorIssueConfig(**config_data.model_dump())
    db.add(config)
    await db.commit()
    await db.refresh(config)
    return config


@router.put("/{config_id}", response_model=MirrorIssueConfigResponse)
async def update_issue_config(
    config_id: int,
    config_data: MirrorIssueConfigUpdate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Update an issue mirror configuration."""
    result = await db.execute(
        select(MirrorIssueConfig).where(MirrorIssueConfig.id == config_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(
            status_code=404,
            detail=f"Issue mirror configuration {config_id} not found"
        )

    # Update only provided fields
    for field, value in config_data.model_dump(exclude_unset=True).items():
        setattr(config, field, value)

    await db.commit()
    await db.refresh(config)
    return config


@router.delete("/{config_id}", status_code=204)
async def delete_issue_config(
    config_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Delete an issue mirror configuration."""
    result = await db.execute(
        select(MirrorIssueConfig).where(MirrorIssueConfig.id == config_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(
            status_code=404,
            detail=f"Issue mirror configuration {config_id} not found"
        )

    await db.delete(config)
    await db.commit()


@router.post("/{config_id}/trigger-sync", status_code=202)
async def trigger_sync(
    config_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Manually trigger an issue sync for a configuration."""
    result = await db.execute(
        select(MirrorIssueConfig).where(MirrorIssueConfig.id == config_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(
            status_code=404,
            detail=f"Issue mirror configuration {config_id} not found"
        )

    if not config.enabled:
        raise HTTPException(
            status_code=400,
            detail="Cannot trigger sync for disabled configuration"
        )

    # TODO: Phase 2 - Create sync job and process
    # For now, just return accepted status
    return {
        "message": "Sync triggered (will be implemented in Phase 2)",
        "config_id": config_id
    }
