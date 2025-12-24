import pytest

from app.models import GitLabInstance, InstancePair, Mirror
from datetime import datetime, timedelta


async def seed_instance(session_maker, *, name: str, url: str) -> int:
    async with session_maker() as s:
        inst = GitLabInstance(name=name, url=url, encrypted_token="enc:t", description="")
        s.add(inst)
        await s.commit()
        await s.refresh(inst)
        return inst.id


async def seed_pair(session_maker, *, name: str, src_id: int, tgt_id: int, direction: str) -> int:
    async with session_maker() as s:
        pair = InstancePair(
            name=name,
            source_instance_id=src_id,
            target_instance_id=tgt_id,
            mirror_direction=direction,
        )
        s.add(pair)
        await s.commit()
        await s.refresh(pair)
        return pair.id


@pytest.mark.asyncio
async def test_topology_aggregates_links_and_node_stats(client, session_maker):
    a_id = await seed_instance(session_maker, name="A", url="https://a.example.com")
    b_id = await seed_instance(session_maker, name="B", url="https://b.example.com")
    c_id = await seed_instance(session_maker, name="C", url="https://c.example.com")

    pair_ab = await seed_pair(session_maker, name="ab", src_id=a_id, tgt_id=b_id, direction="push")
    pair_bc = await seed_pair(session_maker, name="bc", src_id=b_id, tgt_id=c_id, direction="pull")

    async with session_maker() as s:
        # A -> B (push), 3 mirrors, 1 disabled
        s.add_all(
            [
                Mirror(
                    instance_pair_id=pair_ab,
                    source_project_id=1,
                    source_project_path="g/a1",
                    target_project_id=2,
                    target_project_path="g/b1",
                    mirror_direction=None,  # inherit pair direction
                    enabled=True,
                    last_update_status="failed",
                    last_successful_update=datetime.utcnow() - timedelta(hours=6),
                ),
                Mirror(
                    instance_pair_id=pair_ab,
                    source_project_id=3,
                    source_project_path="g/a2",
                    target_project_id=4,
                    target_project_path="g/b2",
                    mirror_direction=None,
                    enabled=True,
                    last_update_status="finished",
                    last_successful_update=datetime.utcnow() - timedelta(hours=1),
                ),
                Mirror(
                    instance_pair_id=pair_ab,
                    source_project_id=5,
                    source_project_path="g/a3",
                    target_project_id=6,
                    target_project_path="g/b3",
                    mirror_direction=None,
                    enabled=False,
                    last_update_status="pending",
                    last_successful_update=None,
                ),
            ]
        )

        # B -> C pull + B -> C push override (separate link buckets)
        s.add(
            Mirror(
                instance_pair_id=pair_bc,
                source_project_id=10,
                source_project_path="g/bx",
                target_project_id=20,
                target_project_path="g/cx",
                mirror_direction=None,  # inherit pull
                enabled=True,
                last_update_status="pending",
            )
        )
        s.add(
            Mirror(
                instance_pair_id=pair_bc,
                source_project_id=11,
                source_project_path="g/by",
                target_project_id=21,
                target_project_path="g/cy",
                mirror_direction="push",  # override
                enabled=True,
                last_update_status="pending",
            )
        )

        await s.commit()

    resp = await client.get("/api/topology")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert {n["name"] for n in body["nodes"]} == {"A", "B", "C"}

    nodes_by_name = {n["name"]: n for n in body["nodes"]}
    assert nodes_by_name["A"]["mirrors_out"] == 3
    assert nodes_by_name["A"]["mirrors_in"] == 0
    assert nodes_by_name["B"]["mirrors_in"] == 3
    assert nodes_by_name["B"]["mirrors_out"] == 2
    assert nodes_by_name["C"]["mirrors_in"] == 2

    # Node health should aggregate mirror statuses for any incident mirrors.
    assert nodes_by_name["A"]["health"] == "error"
    assert nodes_by_name["B"]["health"] == "error"
    assert nodes_by_name["C"]["health"] == "warning"

    links = body["links"]
    assert len(links) == 3

    def key(l):
        return (l["source"], l["target"], l["mirror_direction"])

    links_by_key = {key(l): l for l in links}
    ab_push = links_by_key[(a_id, b_id, "push")]
    assert ab_push["mirror_count"] == 3
    assert ab_push["enabled_count"] == 2
    assert ab_push["disabled_count"] == 1
    assert ab_push["pair_count"] == 1
    assert ab_push["status_counts"]["failed"] == 1
    assert ab_push["status_counts"]["finished"] == 1
    assert ab_push["status_counts"]["pending"] == 1
    assert ab_push["health"] == "error"
    assert ab_push["last_successful_update"] is not None

    bc_pull = links_by_key[(b_id, c_id, "pull")]
    assert bc_pull["mirror_count"] == 1
    assert bc_pull["enabled_count"] == 1
    assert bc_pull["disabled_count"] == 0
    assert bc_pull["pair_count"] == 1

    bc_push = links_by_key[(b_id, c_id, "push")]
    assert bc_push["mirror_count"] == 1

    # Pair filter should narrow the topology to one pair.
    resp = await client.get(f"/api/topology?instance_pair_id={pair_ab}")
    assert resp.status_code == 200, resp.text
    filtered = resp.json()
    assert len(filtered["links"]) == 1
    assert filtered["links"][0]["source"] == a_id
    assert filtered["links"][0]["target"] == b_id
    assert filtered["links"][0]["mirror_direction"] == "push"


@pytest.mark.asyncio
async def test_topology_staleness_thresholds_influence_health(client, session_maker):
    a_id = await seed_instance(session_maker, name="A", url="https://a.example.com")
    b_id = await seed_instance(session_maker, name="B", url="https://b.example.com")
    pair_ab = await seed_pair(session_maker, name="ab2", src_id=a_id, tgt_id=b_id, direction="push")

    # A single successful mirror, but very old.
    async with session_maker() as s:
        s.add(
            Mirror(
                instance_pair_id=pair_ab,
                source_project_id=1,
                source_project_path="g/a1",
                target_project_id=2,
                target_project_path="g/b1",
                mirror_direction=None,
                enabled=True,
                last_update_status="finished",
                last_successful_update=datetime.utcnow() - timedelta(hours=10),
            )
        )
        await s.commit()

    # Force thresholds small so this becomes "stale error"
    resp = await client.get("/api/topology?stale_warning_seconds=60&stale_error_seconds=120")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Only one link exists
    link = body["links"][0]
    assert link["staleness"] == "error"
    assert link["health"] in {"error"}  # staleness should elevate

    nodes_by_name = {n["name"]: n for n in body["nodes"]}
    assert nodes_by_name["A"]["staleness"] == "error"
    assert nodes_by_name["B"]["staleness"] == "error"
    assert nodes_by_name["A"]["health"] == "error"
    assert nodes_by_name["B"]["health"] == "error"

