import json
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.database import get_db
from app.models import Mirror, InstancePair, GitLabInstance
from app.core.auth import verify_credentials
from app.core.gitlab_client import GitLabClient
from app.api.mirrors import _create_mirror_internal, MirrorCreate


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
    """
    Mirror export format - portable across environments.
    Project IDs are looked up at import time via GitLab API.
    """
    source_project_path: str
    target_project_path: str
    # Direction is determined by pair, not stored per-mirror
    mirror_overwrite_diverged: bool | None = None
    mirror_trigger_builds: bool | None = None
    only_mirror_protected_branches: bool | None = None
    mirror_branch_regex: str | None = None
    enabled: bool = True


class ExportMetadata(BaseModel):
    """Metadata about where the export came from (informational only)."""
    exported_at: str
    pair_name: str
    source_instance_name: str
    source_instance_url: str
    target_instance_name: str
    target_instance_url: str
    mirror_direction: str
    total_mirrors: int


class ExportData(BaseModel):
    """Complete export file format."""
    metadata: ExportMetadata
    mirrors: List[MirrorExport]


class ImportData(BaseModel):
    """
    Import format - only mirrors array is used.
    Metadata is ignored (just for user reference).
    """
    metadata: ExportMetadata | None = None  # Optional, ignored on import
    mirrors: List[MirrorExport]


@router.get("/pair/{pair_id}")
async def export_pair_mirrors(
    pair_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """
    Export all mirrors for a specific instance pair as JSON.

    The export is portable - it contains only project paths, not IDs.
    Project IDs are looked up at import time via GitLab API.
    """
    # Verify pair exists and get instances
    pair_result = await db.execute(
        select(InstancePair).where(InstancePair.id == pair_id)
    )
    pair = pair_result.scalar_one_or_none()

    if not pair:
        raise HTTPException(status_code=404, detail="Instance pair not found")

    # Get source and target instances for metadata
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

    # Get all mirrors for this pair
    mirrors_result = await db.execute(
        select(Mirror).where(Mirror.instance_pair_id == pair_id)
    )
    mirrors = mirrors_result.scalars().all()

    # Export mirrors (path-only, no IDs)
    export_mirrors = [
        MirrorExport(
            source_project_path=m.source_project_path,
            target_project_path=m.target_project_path,
            mirror_overwrite_diverged=m.mirror_overwrite_diverged,
            mirror_trigger_builds=m.mirror_trigger_builds,
            only_mirror_protected_branches=m.only_mirror_protected_branches,
            mirror_branch_regex=m.mirror_branch_regex,
            enabled=m.enabled
        )
        for m in mirrors
    ]

    # Create metadata (informational, not used on import)
    from datetime import datetime
    metadata = ExportMetadata(
        exported_at=datetime.utcnow().isoformat() + "Z",
        pair_name=pair.name,
        source_instance_name=source_instance.name,
        source_instance_url=source_instance.url,
        target_instance_name=target_instance.name,
        target_instance_url=target_instance.url,
        mirror_direction=pair.mirror_direction,
        total_mirrors=len(export_mirrors)
    )

    export_data = ExportData(
        metadata=metadata,
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
    """
    Import mirrors for a specific instance pair from JSON.

    This creates actual mirrors in GitLab with tokens, exactly like creating via the UI.
    Project IDs are looked up from project paths via GitLab API at import time.
    """
    # Verify pair exists and get instances
    pair_result = await db.execute(
        select(InstancePair).where(InstancePair.id == pair_id)
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

    # Create GitLab clients for looking up project IDs
    source_client = GitLabClient(source_instance.url, source_instance.encrypted_token)
    target_client = GitLabClient(target_instance.url, target_instance.encrypted_token)

    imported_count = 0
    skipped_count = 0
    errors = []
    skipped = []  # Track which mirrors were skipped with details
    total_mirrors = len(import_data.mirrors)

    for idx, mirror_data in enumerate(import_data.mirrors, start=1):
        mirror_identifier = f"[{idx}/{total_mirrors}] {mirror_data.source_project_path} → {mirror_data.target_project_path}"

        # Check if mirror already exists (by source/target project paths)
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
            skipped.append(f"{mirror_identifier}: Already exists in database")
            continue

        try:
            # Look up project IDs from paths via GitLab API
            try:
                source_project = source_client.get_project_by_path(mirror_data.source_project_path)
                source_project_id = source_project["id"]
            except Exception as e:
                raise Exception(f"Source project '{mirror_data.source_project_path}' not found: {str(e)}")

            try:
                target_project = target_client.get_project_by_path(mirror_data.target_project_path)
                target_project_id = target_project["id"]
            except Exception as e:
                raise Exception(f"Target project '{mirror_data.target_project_path}' not found: {str(e)}")

            # Convert MirrorExport to MirrorCreate with looked-up IDs
            mirror_create = MirrorCreate(
                instance_pair_id=pair_id,
                source_project_id=source_project_id,
                source_project_path=mirror_data.source_project_path,
                target_project_id=target_project_id,
                target_project_path=mirror_data.target_project_path,
                mirror_overwrite_diverged=mirror_data.mirror_overwrite_diverged,
                mirror_trigger_builds=mirror_data.mirror_trigger_builds,
                only_mirror_protected_branches=mirror_data.only_mirror_protected_branches,
                mirror_branch_regex=mirror_data.mirror_branch_regex,
                enabled=mirror_data.enabled
            )

            # Create the mirror using the same logic as the create endpoint
            # This will:
            # 1. Create project access token in GitLab
            # 2. Create the actual mirror in GitLab
            # 3. Store the mirror record in the database
            await _create_mirror_internal(
                db=db,
                pair=pair,
                source_instance=source_instance,
                target_instance=target_instance,
                mirror_data=mirror_create,
                skip_duplicate_check=True  # We already checked above
            )
            imported_count += 1

        except HTTPException as http_exc:
            # HTTP exceptions from the helper (like 409 conflicts for existing pull mirrors)
            await db.rollback()
            # Extract the detail - could be a dict with 'message' key or a string
            detail_msg = http_exc.detail
            if isinstance(detail_msg, dict):
                detail_msg = detail_msg.get("message", str(detail_msg))
            errors.append(f"{mirror_identifier}: {detail_msg}")
        except Exception as e:
            # Any other error
            await db.rollback()
            errors.append(f"{mirror_identifier}: {str(e)}")

    return {
        "status": "completed",
        "imported": imported_count,
        "skipped": skipped_count,
        "skipped_details": skipped,
        "errors": errors
    }
