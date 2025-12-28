import json
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.database import get_db
from app.models import Mirror, InstancePair
from app.core.auth import verify_credentials


def _safe_download_filename(name: str) -> str:
    """
    Prevent header injection and generate a conservative filename.
    Keep alphanumerics plus ._- ; map everything else to underscores.
    """
    cleaned = name.replace("\r", "").replace("\n", "").strip()
    out = []
    for ch in cleaned:
        if ch.isalnum() or ch in {"_", "-", "."}:
            out.append(ch)
        else:
            out.append("_")
    safe = "".join(out).strip("._")
    return safe or "mirrors"


router = APIRouter(prefix="/api/export", tags=["export"])


class MirrorExport(BaseModel):
    source_project_path: str
    target_project_path: str
    source_project_id: int
    target_project_id: int
    # Direction is determined by pair, not stored per-mirror
    mirror_overwrite_diverged: bool | None = None
    mirror_trigger_builds: bool | None = None
    only_mirror_protected_branches: bool | None = None
    mirror_branch_regex: str | None = None
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
            mirror_overwrite_diverged=m.mirror_overwrite_diverged,
            mirror_trigger_builds=m.mirror_trigger_builds,
            only_mirror_protected_branches=m.only_mirror_protected_branches,
            mirror_branch_regex=m.mirror_branch_regex,
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
    filename = f"mirrors_{_safe_download_filename(pair.name)}.json"

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
            # Create new mirror (direction is determined by pair, not stored per-mirror)
            db_mirror = Mirror(
                instance_pair_id=pair_id,
                source_project_id=mirror_data.source_project_id,
                source_project_path=mirror_data.source_project_path,
                target_project_id=mirror_data.target_project_id,
                target_project_path=mirror_data.target_project_path,
                mirror_overwrite_diverged=mirror_data.mirror_overwrite_diverged,
                mirror_trigger_builds=mirror_data.mirror_trigger_builds,
                only_mirror_protected_branches=mirror_data.only_mirror_protected_branches,
                mirror_branch_regex=mirror_data.mirror_branch_regex,
                enabled=mirror_data.enabled,
                last_update_status="pending"
            )
            db.add(db_mirror)
            # Commit each mirror individually to ensure partial imports work correctly
            # This way, if one mirror fails, previously imported mirrors are still persisted
            await db.commit()
            imported_count += 1
        except Exception as e:
            # Rollback the failed mirror and continue with others
            await db.rollback()
            errors.append(f"Failed to import {mirror_data.source_project_path}: {str(e)}")

    return {
        "status": "completed",
        "imported": imported_count,
        "skipped": skipped_count,
        "errors": errors
    }
