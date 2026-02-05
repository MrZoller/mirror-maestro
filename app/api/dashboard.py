from datetime import datetime, timedelta
from typing import List, Dict, Any
from fastapi import APIRouter, Depends
from sqlalchemy import select, func, case, literal
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

    # Get all counts in a single query using conditional aggregation
    counts_result = await db.execute(
        select(
            func.count(Mirror.id).label('total_mirrors'),
            func.count(case((Mirror.enabled == True, 1))).label('enabled_mirrors'),
            func.count(case(((Mirror.last_update_status == 'success') | (Mirror.last_update_status == 'finished'), 1))).label('success'),
            func.count(case((Mirror.last_update_status == 'failed', 1))).label('failed'),
            func.count(case(((Mirror.last_update_status == 'pending') | (Mirror.last_update_status == 'started'), 1))).label('pending'),
            func.count(case((Mirror.last_update_status.is_(None), 1))).label('unknown'),
        )
    )
    counts = counts_result.one()

    total_mirrors = counts.total_mirrors or 0
    enabled_mirrors = counts.enabled_mirrors or 0
    status_counts = {
        'success': counts.success or 0,
        'failed': counts.failed or 0,
        'pending': counts.pending or 0,
        'unknown': counts.unknown or 0,
    }

    # Get pair and instance counts in parallel-ish (still 2 queries, but minimal)
    total_pairs_result = await db.execute(select(func.count(InstancePair.id)))
    total_pairs = total_pairs_result.scalar() or 0

    total_instances_result = await db.execute(select(func.count(GitLabInstance.id)))
    total_instances = total_instances_result.scalar() or 0

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
        elif mirror.last_update_status in ("success", "finished"):
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

    # Mirrors by pair - single query with GROUP BY
    mirrors_by_pair_result = await db.execute(
        select(
            InstancePair.id,
            InstancePair.name,
            func.count(Mirror.id).label('count')
        )
        .outerjoin(Mirror, Mirror.instance_pair_id == InstancePair.id)
        .group_by(InstancePair.id, InstancePair.name)
        .order_by(func.count(Mirror.id).desc())
    )
    mirrors_by_pair = [
        {"pair_id": row.id, "pair_name": row.name, "count": row.count or 0}
        for row in mirrors_by_pair_result.all()
    ]

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
