from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import verify_credentials
from app.database import get_db
from app.models import GitLabInstance, InstancePair, Mirror


router = APIRouter(prefix="/api/topology", tags=["topology"])


class TopologyNode(BaseModel):
    id: int
    name: str
    url: str
    description: str | None = None
    # quick stats (computed)
    mirrors_in: int = 0
    mirrors_out: int = 0
    pairs_in: int = 0
    pairs_out: int = 0


class TopologyLink(BaseModel):
    source: int
    target: int
    mirror_direction: str  # "push" | "pull"
    mirror_count: int
    enabled_count: int
    disabled_count: int
    pair_count: int


class TopologyResponse(BaseModel):
    nodes: list[TopologyNode]
    links: list[TopologyLink]


def _norm_dir(raw: str | None) -> str:
    d = (raw or "").strip().lower()
    return d if d in {"push", "pull"} else "pull"


@router.get("", response_model=TopologyResponse)
async def get_topology(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_credentials),
) -> TopologyResponse:
    """
    Return a graph-friendly view of the configured topology:

    - Nodes are GitLab instances
    - Links are *aggregated* instance-to-instance relationships, grouped by:
        (source_instance_id, target_instance_id, mirror_direction)
      where mirror_direction is the effective direction for each mirror (mirror override
      when present, else pair default).

    Note: for visualization we always draw the arrow source -> target for both push and pull,
    and encode whether the sync is configured as push vs pull via `mirror_direction`.
    """
    inst_rows = (await db.execute(select(GitLabInstance))).scalars().all()
    pair_rows = (await db.execute(select(InstancePair))).scalars().all()
    mirror_rows = (await db.execute(select(Mirror))).scalars().all()

    nodes: dict[int, TopologyNode] = {
        i.id: TopologyNode(id=i.id, name=i.name, url=i.url, description=i.description)
        for i in inst_rows
    }

    pairs_by_id: dict[int, InstancePair] = {p.id: p for p in pair_rows}

    # Pair stats on nodes
    for p in pair_rows:
        if p.source_instance_id in nodes:
            nodes[p.source_instance_id].pairs_out += 1
        if p.target_instance_id in nodes:
            nodes[p.target_instance_id].pairs_in += 1

    # Aggregate mirror edges
    # key: (src, tgt, dir) -> counters
    agg: dict[tuple[int, int, str], dict[str, Any]] = defaultdict(lambda: {"mirror_count": 0, "enabled": 0, "disabled": 0, "pair_ids": set()})

    for m in mirror_rows:
        pair = pairs_by_id.get(m.instance_pair_id)
        if not pair:
            # Orphaned mirror row (shouldn't happen, but be defensive).
            continue

        src = pair.source_instance_id
        tgt = pair.target_instance_id
        direction = _norm_dir(m.mirror_direction or pair.mirror_direction)

        key = (src, tgt, direction)
        agg[key]["mirror_count"] += 1
        agg[key]["enabled"] += 1 if m.enabled else 0
        agg[key]["disabled"] += 0 if m.enabled else 1
        agg[key]["pair_ids"].add(pair.id)

        # Per-node mirror flow counts
        if src in nodes:
            nodes[src].mirrors_out += 1
        if tgt in nodes:
            nodes[tgt].mirrors_in += 1

    links: list[TopologyLink] = []
    for (src, tgt, direction), v in agg.items():
        links.append(
            TopologyLink(
                source=src,
                target=tgt,
                mirror_direction=direction,
                mirror_count=int(v["mirror_count"]),
                enabled_count=int(v["enabled"]),
                disabled_count=int(v["disabled"]),
                pair_count=len(v["pair_ids"]),
            )
        )

    # Keep output stable for UI diffs
    nodes_out = sorted(nodes.values(), key=lambda n: (n.name.lower(), n.id))
    links_out = sorted(links, key=lambda l: (l.source, l.target, l.mirror_direction))
    return TopologyResponse(nodes=nodes_out, links=links_out)

