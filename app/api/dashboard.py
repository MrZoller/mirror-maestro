from datetime import datetime, timedelta
from typing import List, Dict, Any
from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Mirror, InstancePair, GitLabInstance
from app.core.auth import verify_credentials


router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/metrics")
async def get_dashboard_metrics(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Get dashboard metrics and statistics."""

    # Total counts
    total_mirrors_result = await db.execute(select(func.count(Mirror.id)))
    total_mirrors = total_mirrors_result.scalar() or 0

    total_pairs_result = await db.execute(select(func.count(InstancePair.id)))
    total_pairs = total_pairs_result.scalar() or 0

    total_instances_result = await db.execute(select(func.count(GitLabInstance.id)))
    total_instances = total_instances_result.scalar() or 0

    # Mirror health statistics
    enabled_mirrors_result = await db.execute(
        select(func.count(Mirror.id)).where(Mirror.enabled == True)
    )
    enabled_mirrors = enabled_mirrors_result.scalar() or 0

    # Count mirrors by status
    status_counts = {}
    for status in ['success', 'failed', 'pending', None]:
        if status is None:
            result = await db.execute(
                select(func.count(Mirror.id)).where(Mirror.last_update_status.is_(None))
            )
        else:
            result = await db.execute(
                select(func.count(Mirror.id)).where(Mirror.last_update_status == status)
            )
        status_counts[status or 'unknown'] = result.scalar() or 0

    # Calculate health percentage
    successful = status_counts.get('success', 0)
    health_percentage = round((successful / total_mirrors * 100) if total_mirrors > 0 else 100, 1)

    # Recent activity (last 24 hours based on updated_at)
    yesterday = datetime.utcnow() - timedelta(hours=24)
    recent_mirrors_result = await db.execute(
        select(Mirror)
        .where(Mirror.updated_at >= yesterday)
        .order_by(Mirror.updated_at.desc())
        .limit(10)
    )
    recent_mirrors = recent_mirrors_result.scalars().all()

    # Format recent activity
    recent_activity = []
    for mirror in recent_mirrors:
        # Determine activity type
        if mirror.created_at >= yesterday:
            activity_type = "created"
            icon = "✨"
        elif mirror.last_update_status == "success":
            activity_type = "synced"
            icon = "✓"
        elif mirror.last_update_status == "failed":
            activity_type = "failed"
            icon = "✗"
        else:
            activity_type = "updated"
            icon = "↻"

        # Calculate time ago
        time_diff = datetime.utcnow() - mirror.updated_at
        if time_diff.total_seconds() < 60:
            time_ago = f"{int(time_diff.total_seconds())}s"
        elif time_diff.total_seconds() < 3600:
            time_ago = f"{int(time_diff.total_seconds() / 60)}m"
        elif time_diff.total_seconds() < 86400:
            time_ago = f"{int(time_diff.total_seconds() / 3600)}h"
        else:
            time_ago = f"{int(time_diff.total_seconds() / 86400)}d"

        recent_activity.append({
            "id": mirror.id,
            "project": mirror.source_project_path,
            "activity_type": activity_type,
            "icon": icon,
            "time_ago": time_ago,
            "status": mirror.last_update_status or "unknown",
            "timestamp": mirror.updated_at.isoformat()
        })

    # Mirrors by pair
    pairs_result = await db.execute(select(InstancePair))
    pairs = pairs_result.scalars().all()

    mirrors_by_pair = []
    for pair in pairs:
        count_result = await db.execute(
            select(func.count(Mirror.id)).where(Mirror.instance_pair_id == pair.id)
        )
        count = count_result.scalar() or 0
        mirrors_by_pair.append({
            "pair_id": pair.id,
            "pair_name": pair.name,
            "count": count
        })

    # Sort by count descending
    mirrors_by_pair.sort(key=lambda x: x['count'], reverse=True)

    return {
        "summary": {
            "total_mirrors": total_mirrors,
            "total_pairs": total_pairs,
            "total_instances": total_instances,
            "enabled_mirrors": enabled_mirrors,
            "health_percentage": health_percentage
        },
        "health": {
            "success": status_counts.get('success', 0),
            "failed": status_counts.get('failed', 0),
            "pending": status_counts.get('pending', 0),
            "unknown": status_counts.get('unknown', 0)
        },
        "recent_activity": recent_activity,
        "mirrors_by_pair": mirrors_by_pair[:5]  # Top 5 pairs
    }


@router.get("/quick-stats")
async def get_quick_stats(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """Get quick stats for real-time updates."""

    # Count mirrors currently syncing (updated in last 5 minutes with pending status)
    five_min_ago = datetime.utcnow() - timedelta(minutes=5)
    syncing_result = await db.execute(
        select(func.count(Mirror.id)).where(
            Mirror.last_update_status == 'pending',
            Mirror.updated_at >= five_min_ago
        )
    )
    syncing_count = syncing_result.scalar() or 0

    # Count recently failed (last hour)
    one_hour_ago = datetime.utcnow() - timedelta(hours=1)
    recent_failures_result = await db.execute(
        select(func.count(Mirror.id)).where(
            Mirror.last_update_status == 'failed',
            Mirror.updated_at >= one_hour_ago
        )
    )
    recent_failures = recent_failures_result.scalar() or 0

    # Get list of syncing mirror IDs for status indicators
    syncing_mirrors_result = await db.execute(
        select(Mirror.id).where(
            Mirror.last_update_status == 'pending',
            Mirror.updated_at >= five_min_ago
        )
    )
    syncing_mirror_ids = [id for id in syncing_mirrors_result.scalars().all()]

    return {
        "syncing_count": syncing_count,
        "recent_failures": recent_failures,
        "syncing_mirror_ids": syncing_mirror_ids,
        "timestamp": datetime.utcnow().isoformat()
    }
