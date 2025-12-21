import json
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.database import get_db
from app.models import Mirror, InstancePair, GitLabInstance
from app.core.auth import verify_credentials
from app.core.encryption import encryption


router = APIRouter(prefix="/api/export", tags=["export"])


class MirrorExport(BaseModel):
    source_project_path: str
    target_project_path: str
    source_project_id: int
    target_project_id: int
    mirror_direction: str | None = None
    mirror_protected_branches: bool | None = None
    mirror_overwrite_diverged: bool | None = None
    mirror_trigger_builds: bool | None = None
    only_mirror_protected_branches: bool | None = None
    enabled: bool = True


class ExportData(BaseModel):
    pair_id: int
    pair_name: str
    mirrors: List[MirrorExport]


class ImportData(BaseModel):
    pair_id: int
    mirrors: List[MirrorExport]


@router.get("/pair/{pair_id}")
async def export_pair_mirrors(
    pair_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Export all mirrors for a specific instance pair as JSON."""
    # Verify pair exists
    pair_result = await db.execute(
        select(InstancePair).where(InstancePair.id == pair_id)
    )
    pair = pair_result.scalar_one_or_none()

    if not pair:
        raise HTTPException(status_code=404, detail="Instance pair not found")

    # Get all mirrors for this pair
    mirrors_result = await db.execute(
        select(Mirror).where(Mirror.instance_pair_id == pair_id)
    )
    mirrors = mirrors_result.scalars().all()

    export_mirrors = [
        MirrorExport(
            source_project_path=m.source_project_path,
            target_project_path=m.target_project_path,
            source_project_id=m.source_project_id,
            target_project_id=m.target_project_id,
            mirror_direction=m.mirror_direction,
            mirror_protected_branches=m.mirror_protected_branches,
            mirror_overwrite_diverged=m.mirror_overwrite_diverged,
            mirror_trigger_builds=m.mirror_trigger_builds,
            only_mirror_protected_branches=m.only_mirror_protected_branches,
            enabled=m.enabled
        )
        for m in mirrors
    ]

    export_data = ExportData(
        pair_id=pair.id,
        pair_name=pair.name,
        mirrors=export_mirrors
    )

    # Return as downloadable JSON
    content = json.dumps(export_data.model_dump(), indent=2)
    filename = f"mirrors_{pair.name.replace(' ', '_')}.json"

    return Response(
        content=content,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        }
    )


@router.post("/pair/{pair_id}")
async def import_pair_mirrors(
    pair_id: int,
    import_data: ImportData,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Import mirrors for a specific instance pair from JSON."""
    # Verify pair exists
    pair_result = await db.execute(
        select(InstancePair).where(InstancePair.id == pair_id)
    )
    pair = pair_result.scalar_one_or_none()

    if not pair:
        raise HTTPException(status_code=404, detail="Instance pair not found")

    imported_count = 0
    skipped_count = 0
    errors = []

    for mirror_data in import_data.mirrors:
        # Check if mirror already exists
        existing_result = await db.execute(
            select(Mirror).where(
                Mirror.instance_pair_id == pair_id,
                Mirror.source_project_path == mirror_data.source_project_path,
                Mirror.target_project_path == mirror_data.target_project_path
            )
        )
        existing = existing_result.scalar_one_or_none()

        if existing:
            skipped_count += 1
            continue

        try:
            # Create new mirror
            db_mirror = Mirror(
                instance_pair_id=pair_id,
                source_project_id=mirror_data.source_project_id,
                source_project_path=mirror_data.source_project_path,
                target_project_id=mirror_data.target_project_id,
                target_project_path=mirror_data.target_project_path,
                mirror_direction=mirror_data.mirror_direction,
                mirror_protected_branches=mirror_data.mirror_protected_branches,
                mirror_overwrite_diverged=mirror_data.mirror_overwrite_diverged,
                mirror_trigger_builds=mirror_data.mirror_trigger_builds,
                only_mirror_protected_branches=mirror_data.only_mirror_protected_branches,
                enabled=mirror_data.enabled,
                last_update_status="pending"
            )
            db.add(db_mirror)
            imported_count += 1
        except Exception as e:
            errors.append(f"Failed to import {mirror_data.source_project_path}: {str(e)}")

    await db.commit()

    return {
        "status": "completed",
        "imported": imported_count,
        "skipped": skipped_count,
        "errors": errors
    }
