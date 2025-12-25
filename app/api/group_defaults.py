from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import verify_credentials
from app.database import get_db
from app.models import GroupMirrorDefaults, InstancePair


router = APIRouter(prefix="/api/group-defaults", tags=["group-defaults"])


class GroupMirrorDefaultsUpsert(BaseModel):
    instance_pair_id: int
    group_path: str
    mirror_direction: str | None = None
    mirror_protected_branches: bool | None = None
    mirror_overwrite_diverged: bool | None = None
    mirror_trigger_builds: bool | None = None
    only_mirror_protected_branches: bool | None = None
    mirror_branch_regex: str | None = None
    mirror_user_id: int | None = None


class GroupMirrorDefaultsResponse(BaseModel):
    id: int
    instance_pair_id: int
    group_path: str
    mirror_direction: str | None
    mirror_protected_branches: bool | None
    mirror_overwrite_diverged: bool | None
    mirror_trigger_builds: bool | None
    only_mirror_protected_branches: bool | None
    mirror_branch_regex: str | None
    mirror_user_id: int | None
    created_at: str
    updated_at: str

    model_config = ConfigDict(from_attributes=True)


@router.get("", response_model=List[GroupMirrorDefaultsResponse])
async def list_group_defaults(
    instance_pair_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials),
):
    query = select(GroupMirrorDefaults)
    if instance_pair_id is not None:
        query = query.where(GroupMirrorDefaults.instance_pair_id == instance_pair_id)

    res = await db.execute(query)
    rows = res.scalars().all()
    return [
        GroupMirrorDefaultsResponse(
            id=r.id,
            instance_pair_id=r.instance_pair_id,
            group_path=r.group_path,
            mirror_direction=r.mirror_direction,
            mirror_protected_branches=r.mirror_protected_branches,
            mirror_overwrite_diverged=r.mirror_overwrite_diverged,
            mirror_trigger_builds=r.mirror_trigger_builds,
            only_mirror_protected_branches=r.only_mirror_protected_branches,
            mirror_branch_regex=r.mirror_branch_regex,
            mirror_user_id=r.mirror_user_id,
            created_at=r.created_at.isoformat(),
            updated_at=r.updated_at.isoformat(),
        )
        for r in rows
    ]


@router.post("", response_model=GroupMirrorDefaultsResponse)
async def upsert_group_defaults(
    payload: GroupMirrorDefaultsUpsert,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials),
):
    # Verify instance pair exists
    pair_res = await db.execute(select(InstancePair).where(InstancePair.id == payload.instance_pair_id))
    if not pair_res.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Instance pair not found")

    existing_res = await db.execute(
        select(GroupMirrorDefaults).where(
            GroupMirrorDefaults.instance_pair_id == payload.instance_pair_id,
            GroupMirrorDefaults.group_path == payload.group_path,
        )
    )
    row = existing_res.scalar_one_or_none()

    if row is None:
        row = GroupMirrorDefaults(
            instance_pair_id=payload.instance_pair_id,
            group_path=payload.group_path,
            mirror_direction=payload.mirror_direction,
            mirror_protected_branches=payload.mirror_protected_branches,
            mirror_overwrite_diverged=payload.mirror_overwrite_diverged,
            mirror_trigger_builds=payload.mirror_trigger_builds,
            only_mirror_protected_branches=payload.only_mirror_protected_branches,
            mirror_branch_regex=payload.mirror_branch_regex,
            mirror_user_id=payload.mirror_user_id,
        )
        db.add(row)
    else:
        # Only update fields that were explicitly set in the request
        update_data = payload.model_dump(exclude_unset=True, exclude={'instance_pair_id', 'group_path'})
        for field, value in update_data.items():
            setattr(row, field, value)

    await db.commit()
    await db.refresh(row)

    return GroupMirrorDefaultsResponse(
        id=row.id,
        instance_pair_id=row.instance_pair_id,
        group_path=row.group_path,
        mirror_direction=row.mirror_direction,
        mirror_protected_branches=row.mirror_protected_branches,
        mirror_overwrite_diverged=row.mirror_overwrite_diverged,
        mirror_trigger_builds=row.mirror_trigger_builds,
        only_mirror_protected_branches=row.only_mirror_protected_branches,
        mirror_branch_regex=row.mirror_branch_regex,
        mirror_user_id=row.mirror_user_id,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


@router.delete("/{group_default_id}")
async def delete_group_defaults(
    group_default_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials),
):
    res = await db.execute(select(GroupMirrorDefaults).where(GroupMirrorDefaults.id == group_default_id))
    row = res.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Group defaults not found")

    await db.delete(row)
    await db.commit()
    return {"status": "deleted"}

