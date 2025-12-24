from __future__ import annotations

from collections import defaultdict
from typing import Any
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
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
    # health/status (computed, aggregated)
    status_counts: dict[str, int] = Field(default_factory=dict)
    last_successful_update: str | None = None
    health: str = "unknown"  # "ok" | "warning" | "error" | "unknown"
    staleness: str = "unknown"  # "ok" | "warning" | "error" | "unknown"
    staleness_age_seconds: int | None = None
    never_succeeded_count: int = 0


class TopologyLink(BaseModel):
    source: int
    target: int
    mirror_direction: str  # "push" | "pull"
    mirror_count: int
    enabled_count: int
    disabled_count: int
    pair_count: int
    # health/status (computed, aggregated)
    status_counts: dict[str, int] = Field(default_factory=dict)
    last_successful_update: str | None = None
    health: str = "unknown"  # "ok" | "warning" | "error" | "unknown"
    staleness: str = "unknown"  # "ok" | "warning" | "error" | "unknown"
    staleness_age_seconds: int | None = None
    never_succeeded_count: int = 0


class TopologyResponse(BaseModel):
    nodes: list[TopologyNode]
    links: list[TopologyLink]


def _norm_dir(raw: str | None) -> str:
    d = (raw or "").strip().lower()
    return d if d in {"push", "pull"} else "pull"


def _norm_status(raw: str | None) -> str:
    s = (raw or "").strip().lower()
    return s or "unknown"


def _health_from_status_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "unknown"
    if counts.get("failed", 0) > 0:
        return "error"
    # "updating" and "pending" are treated as warning: mirrors exist but are not healthy/settled.
    if counts.get("updating", 0) > 0 or counts.get("pending", 0) > 0:
        return "warning"
    if counts.get("finished", 0) > 0:
        return "ok"
    return "unknown"


def _normalize_stale_thresholds(
    *,
    stale_warning_seconds: int | None,
    stale_error_seconds: int | None,
) -> tuple[int, int]:
    # Reasonable defaults: warn after 24h, error after 7d
    warn = 24 * 60 * 60 if stale_warning_seconds is None else int(stale_warning_seconds)
    err = 7 * 24 * 60 * 60 if stale_error_seconds is None else int(stale_error_seconds)
    warn = max(0, warn)
    err = max(0, err)
    if err < warn:
        err = warn
    return warn, err


def _staleness_level(
    *,
    now: datetime,
    last_successful_update: datetime | None,
    mirror_count: int,
    warn_s: int,
    err_s: int,
) -> tuple[str, int | None]:
    """
    Determine staleness from the most recent successful update time.

    - If there are mirrors but we've never seen a success timestamp: warning
    - If there are no mirrors: unknown (not applicable)
    - Otherwise compare age against warn/error thresholds
    """
    if mirror_count <= 0:
        return "unknown", None
    if last_successful_update is None:
        return "warning", None
    # Stored timestamps are naive utcnow(); treat naive as UTC.
    last = last_successful_update
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    age_s = int(max(0, (now - last).total_seconds()))
    if age_s >= err_s and err_s > 0:
        return "error", age_s
    if age_s >= warn_s and warn_s > 0:
        return "warning", age_s
    return "ok", age_s


def _normalize_never_succeeded_level(level: str | None) -> str:
    v = (level or "").strip().lower()
    if v in {"warning", "error"}:
        return v
    return "warning"


def _combine_health(*, base: str, staleness: str) -> str:
    """
    Combine status-based health with staleness-based health.
    Severity order: error > warning > ok > unknown.
    """
    order = {"unknown": 0, "ok": 1, "warning": 2, "error": 3}
    inv = {v: k for k, v in order.items()}
    return inv[max(order.get(base, 0), order.get(staleness, 0))]


@router.get("", response_model=TopologyResponse)
async def get_topology(
    instance_pair_id: int | None = None,
    stale_warning_seconds: int | None = None,
    stale_error_seconds: int | None = None,
    never_succeeded_level: str | None = None,
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
    warn_s, err_s = _normalize_stale_thresholds(
        stale_warning_seconds=stale_warning_seconds,
        stale_error_seconds=stale_error_seconds,
    )
    never_level = _normalize_never_succeeded_level(never_succeeded_level)
    now = datetime.now(timezone.utc)

    inst_rows = (await db.execute(select(GitLabInstance))).scalars().all()

    pair_q = select(InstancePair)
    mirror_q = select(Mirror)
    if instance_pair_id is not None:
        pair_q = pair_q.where(InstancePair.id == instance_pair_id)
        mirror_q = mirror_q.where(Mirror.instance_pair_id == instance_pair_id)

    pair_rows = (await db.execute(pair_q)).scalars().all()
    mirror_rows = (await db.execute(mirror_q)).scalars().all()

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
    agg: dict[tuple[int, int, str], dict[str, Any]] = defaultdict(
        lambda: {
            "mirror_count": 0,
            "enabled": 0,
            "disabled": 0,
            "pair_ids": set(),
            "status_counts": defaultdict(int),
            "last_successful_update": None,
            "never_succeeded_count": 0,
        }
    )

    # Aggregate mirror status per node (instance)
    node_agg: dict[int, dict[str, Any]] = defaultdict(
        lambda: {"status_counts": defaultdict(int), "last_successful_update": None, "never_succeeded_count": 0}
    )

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
        agg[key]["status_counts"][_norm_status(m.last_update_status)] += 1
        if m.last_successful_update is not None:
            cur = agg[key]["last_successful_update"]
            if cur is None or m.last_successful_update > cur:
                agg[key]["last_successful_update"] = m.last_successful_update
        else:
            agg[key]["never_succeeded_count"] += 1

        # Per-node mirror flow counts
        if src in nodes:
            nodes[src].mirrors_out += 1
        if tgt in nodes:
            nodes[tgt].mirrors_in += 1

        # Per-node health stats (count the same mirror status for both involved instances)
        for iid in (src, tgt):
            node_agg[iid]["status_counts"][_norm_status(m.last_update_status)] += 1
            if m.last_successful_update is not None:
                cur_n = node_agg[iid]["last_successful_update"]
                if cur_n is None or m.last_successful_update > cur_n:
                    node_agg[iid]["last_successful_update"] = m.last_successful_update
            else:
                node_agg[iid]["never_succeeded_count"] += 1

    links: list[TopologyLink] = []
    for (src, tgt, direction), v in agg.items():
        last = v["last_successful_update"]
        status_counts = dict(v["status_counts"])
        base_health = _health_from_status_counts(status_counts)

        # Special case: if *all* mirrors in this edge have never succeeded, allow
        # caller-configurable severity (warning vs error).
        mirror_count = int(v["mirror_count"])
        never_count = int(v["never_succeeded_count"])
        if mirror_count > 0 and never_count == mirror_count:
            stale_level = never_level
            stale_age_s = None
        else:
            stale_level, stale_age_s = _staleness_level(
                now=now,
                last_successful_update=last,
                mirror_count=mirror_count,
                warn_s=warn_s,
                err_s=err_s,
            )
        health = _combine_health(base=base_health, staleness=stale_level)
        links.append(
            TopologyLink(
                source=src,
                target=tgt,
                mirror_direction=direction,
                mirror_count=mirror_count,
                enabled_count=int(v["enabled"]),
                disabled_count=int(v["disabled"]),
                pair_count=len(v["pair_ids"]),
                status_counts=status_counts,
                last_successful_update=last.isoformat() if last is not None else None,
                health=health,
                staleness=stale_level,
                staleness_age_seconds=stale_age_s,
                never_succeeded_count=never_count,
            )
        )

    # Finalize node health fields
    for iid, n in nodes.items():
        a = node_agg.get(iid)
        if not a:
            continue
        counts = dict(a["status_counts"])
        last = a["last_successful_update"]
        never_count = int(a.get("never_succeeded_count") or 0)
        n.status_counts = counts
        n.last_successful_update = last.isoformat() if last is not None else None
        base_health = _health_from_status_counts(counts)
        mirror_count = int(n.mirrors_in) + int(n.mirrors_out)
        if mirror_count > 0 and never_count == mirror_count:
            stale_level = never_level
            stale_age_s = None
        else:
            stale_level, stale_age_s = _staleness_level(
                now=now,
                last_successful_update=last,
                mirror_count=mirror_count,
                warn_s=warn_s,
                err_s=err_s,
            )
        n.staleness = stale_level
        n.staleness_age_seconds = stale_age_s
        n.health = _combine_health(base=base_health, staleness=stale_level)
        n.never_succeeded_count = never_count

    # Keep output stable for UI diffs
    nodes_out = sorted(nodes.values(), key=lambda n: (n.name.lower(), n.id))
    links_out = sorted(links, key=lambda l: (l.source, l.target, l.mirror_direction))
    return TopologyResponse(nodes=nodes_out, links=links_out)

