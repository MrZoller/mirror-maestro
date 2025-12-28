"""Global search API for searching across all entities."""

from typing import List
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.database import get_db
from app.models import GitLabInstance, InstancePair, Mirror
from app.core.auth import verify_credentials


router = APIRouter(prefix="/api/search", tags=["search"])


class SearchResultItem(BaseModel):
    """A single search result item."""
    type: str  # "instance", "pair", or "mirror"
    id: int
    title: str
    subtitle: str | None = None
    url: str  # API URL to the resource


class SearchResponse(BaseModel):
    """Response containing search results grouped by type."""
    query: str
    total_count: int
    instances: List[SearchResultItem]
    pairs: List[SearchResultItem]
    mirrors: List[SearchResultItem]


@router.get("", response_model=SearchResponse)
async def global_search(
    q: str = Query(..., min_length=1, description="Search query (minimum 1 character)"),
    limit: int = Query(10, ge=1, le=50, description="Maximum results per category"),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials)
):
    """
    Search across all entities (instances, pairs, mirrors).

    Returns up to `limit` results per category, sorted by relevance.
    The search is case-insensitive and matches partial strings.
    """
    search_term = f"%{q.strip().lower()}%"
    results: SearchResponse = SearchResponse(
        query=q,
        total_count=0,
        instances=[],
        pairs=[],
        mirrors=[],
    )

    # Search instances
    instance_query = select(GitLabInstance).where(
        or_(
            GitLabInstance.name.ilike(search_term),
            GitLabInstance.url.ilike(search_term),
            GitLabInstance.description.ilike(search_term),
        )
    ).limit(limit)
    instance_result = await db.execute(instance_query)
    for inst in instance_result.scalars().all():
        results.instances.append(SearchResultItem(
            type="instance",
            id=inst.id,
            title=inst.name,
            subtitle=inst.url,
            url=f"/api/instances/{inst.id}",
        ))

    # Search pairs
    pair_query = select(InstancePair).where(
        or_(
            InstancePair.name.ilike(search_term),
            InstancePair.description.ilike(search_term),
        )
    ).limit(limit)
    pair_result = await db.execute(pair_query)
    for pair in pair_result.scalars().all():
        results.pairs.append(SearchResultItem(
            type="pair",
            id=pair.id,
            title=pair.name,
            subtitle=f"{pair.mirror_direction} mirror",
            url=f"/api/pairs/{pair.id}",
        ))

    # Search mirrors
    mirror_query = select(Mirror).where(
        or_(
            Mirror.source_project_path.ilike(search_term),
            Mirror.target_project_path.ilike(search_term),
        )
    ).limit(limit)
    mirror_result = await db.execute(mirror_query)
    for mirror in mirror_result.scalars().all():
        results.mirrors.append(SearchResultItem(
            type="mirror",
            id=mirror.id,
            title=f"{mirror.source_project_path} → {mirror.target_project_path}",
            subtitle=f"Status: {mirror.last_update_status or 'unknown'}",
            url=f"/api/mirrors/{mirror.id}",
        ))

    results.total_count = len(results.instances) + len(results.pairs) + len(results.mirrors)

    return results
