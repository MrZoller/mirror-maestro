from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, ConfigDict

from app.database import get_db
from app.models import InstancePair, GitLabInstance, Mirror, GroupMirrorDefaults
from app.core.auth import verify_credentials


router = APIRouter(prefix="/api/pairs", tags=["pairs"])


class InstancePairCreate(BaseModel):
    name: str
    source_instance_id: int
    target_instance_id: int
    mirror_direction: str = "pull"
    mirror_protected_branches: bool = True
    mirror_overwrite_diverged: bool = False
    mirror_trigger_builds: bool = False
    only_mirror_protected_branches: bool = False
    mirror_branch_regex: str | None = None
    mirror_user_id: int | None = None
    description: str = ""


class InstancePairUpdate(BaseModel):
    name: str | None = None
    source_instance_id: int | None = None
    target_instance_id: int | None = None
    mirror_direction: str | None = None
    mirror_protected_branches: bool | None = None
    mirror_overwrite_diverged: bool | None = None
    mirror_trigger_builds: bool | None = None
    only_mirror_protected_branches: bool | None = None
    mirror_branch_regex: str | None = None
    mirror_user_id: int | None = None
    description: str | None = None


class InstancePairResponse(BaseModel):
    id: int
    name: str
    source_instance_id: int
    target_instance_id: int
    mirror_direction: str
    mirror_protected_branches: bool
    mirror_overwrite_diverged: bool
    mirror_trigger_builds: bool
    only_mirror_protected_branches: bool
    mirror_branch_regex: str | None
    mirror_user_id: int | None
    description: str | None
    created_at: str
    updated_at: str

    model_config = ConfigDict(from_attributes=True)


@router.get("", response_model=List[InstancePairResponse])
async def list_pairs(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """List all instance pairs."""
    result = await db.execute(select(InstancePair))
    pairs = result.scalars().all()
    return [
        InstancePairResponse(
            id=pair.id,
            name=pair.name,
            source_instance_id=pair.source_instance_id,
            target_instance_id=pair.target_instance_id,
            mirror_direction=pair.mirror_direction,
            mirror_protected_branches=pair.mirror_protected_branches,
            mirror_overwrite_diverged=pair.mirror_overwrite_diverged,
            mirror_trigger_builds=pair.mirror_trigger_builds,
            only_mirror_protected_branches=pair.only_mirror_protected_branches,
            mirror_branch_regex=pair.mirror_branch_regex,
            mirror_user_id=pair.mirror_user_id,
            description=pair.description,
            created_at=pair.created_at.isoformat(),
            updated_at=pair.updated_at.isoformat()
        )
        for pair in pairs
    ]


@router.post("", response_model=InstancePairResponse)
async def create_pair(
    pair: InstancePairCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Create a new instance pair."""
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

    # Create the pair
    db_pair = InstancePair(
        name=pair.name,
        source_instance_id=pair.source_instance_id,
        target_instance_id=pair.target_instance_id,
        mirror_direction=pair.mirror_direction,
        mirror_protected_branches=pair.mirror_protected_branches,
        mirror_overwrite_diverged=pair.mirror_overwrite_diverged,
        mirror_trigger_builds=pair.mirror_trigger_builds,
        only_mirror_protected_branches=pair.only_mirror_protected_branches,
        mirror_branch_regex=pair.mirror_branch_regex,
        mirror_user_id=pair.mirror_user_id,
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
        mirror_protected_branches=db_pair.mirror_protected_branches,
        mirror_overwrite_diverged=db_pair.mirror_overwrite_diverged,
        mirror_trigger_builds=db_pair.mirror_trigger_builds,
        only_mirror_protected_branches=db_pair.only_mirror_protected_branches,
        mirror_branch_regex=db_pair.mirror_branch_regex,
        mirror_user_id=db_pair.mirror_user_id,
        description=db_pair.description,
        created_at=db_pair.created_at.isoformat(),
        updated_at=db_pair.updated_at.isoformat()
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
        mirror_protected_branches=pair.mirror_protected_branches,
        mirror_overwrite_diverged=pair.mirror_overwrite_diverged,
        mirror_trigger_builds=pair.mirror_trigger_builds,
        only_mirror_protected_branches=pair.only_mirror_protected_branches,
        mirror_branch_regex=pair.mirror_branch_regex,
        mirror_user_id=pair.mirror_user_id,
        description=pair.description,
        created_at=pair.created_at.isoformat(),
        updated_at=pair.updated_at.isoformat()
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

    # Update fields (presence-aware to allow explicit null clears)
    if "name" in fields:
        pair.name = pair_update.name
    if "source_instance_id" in fields:
        pair.source_instance_id = pair_update.source_instance_id
    if "target_instance_id" in fields:
        pair.target_instance_id = pair_update.target_instance_id
    if "mirror_direction" in fields:
        pair.mirror_direction = pair_update.mirror_direction
    if "mirror_protected_branches" in fields:
        pair.mirror_protected_branches = pair_update.mirror_protected_branches
    if "mirror_overwrite_diverged" in fields:
        pair.mirror_overwrite_diverged = pair_update.mirror_overwrite_diverged
    if "mirror_trigger_builds" in fields:
        pair.mirror_trigger_builds = pair_update.mirror_trigger_builds
    if "only_mirror_protected_branches" in fields:
        pair.only_mirror_protected_branches = pair_update.only_mirror_protected_branches
    if "mirror_branch_regex" in fields:
        pair.mirror_branch_regex = pair_update.mirror_branch_regex
    if "mirror_user_id" in fields:
        pair.mirror_user_id = pair_update.mirror_user_id
    if "description" in fields:
        pair.description = pair_update.description

    await db.commit()
    await db.refresh(pair)

    return InstancePairResponse(
        id=pair.id,
        name=pair.name,
        source_instance_id=pair.source_instance_id,
        target_instance_id=pair.target_instance_id,
        mirror_direction=pair.mirror_direction,
        mirror_protected_branches=pair.mirror_protected_branches,
        mirror_overwrite_diverged=pair.mirror_overwrite_diverged,
        mirror_trigger_builds=pair.mirror_trigger_builds,
        only_mirror_protected_branches=pair.only_mirror_protected_branches,
        mirror_branch_regex=pair.mirror_branch_regex,
        mirror_user_id=pair.mirror_user_id,
        description=pair.description,
        created_at=pair.created_at.isoformat(),
        updated_at=pair.updated_at.isoformat()
    )


@router.delete("/{pair_id}")
async def delete_pair(
    pair_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Delete an instance pair."""
    result = await db.execute(
        select(InstancePair).where(InstancePair.id == pair_id)
    )
    pair = result.scalar_one_or_none()

    if not pair:
        raise HTTPException(status_code=404, detail="Instance pair not found")

    # Cascade-delete dependent entities (mirrors + group defaults) to avoid leaving
    # orphaned rows referencing this pair.
    await db.execute(delete(Mirror).where(Mirror.instance_pair_id == pair_id))
    await db.execute(delete(GroupMirrorDefaults).where(GroupMirrorDefaults.instance_pair_id == pair_id))

    await db.delete(pair)
    await db.commit()

    return {"status": "deleted"}
