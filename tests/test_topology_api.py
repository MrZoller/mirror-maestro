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
    pair_bc_pull = await seed_pair(session_maker, name="bc-pull", src_id=b_id, tgt_id=c_id, direction="pull")
    pair_bc_push = await seed_pair(session_maker, name="bc-push", src_id=b_id, tgt_id=c_id, direction="push")

    async with session_maker() as s:
        # A -> B (push), 3 mirrors, 1 disabled
        # Direction comes from pair, not stored on mirror
        s.add_all(
            [
                Mirror(
                    instance_pair_id=pair_ab,
                    source_project_id=1,
                    source_project_path="g/a1",
                    target_project_id=2,
                    target_project_path="g/b1",
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
                    enabled=False,
                    last_update_status="pending",
                    last_successful_update=None,
                ),
            ]
        )

        # B -> C pull (via pull pair)
        s.add(
            Mirror(
                instance_pair_id=pair_bc_pull,
                source_project_id=10,
                source_project_path="g/bx",
                target_project_id=20,
                target_project_path="g/cx",
                enabled=True,
                last_update_status="pending",
            )
        )
        # B -> C push (via push pair - separate link bucket)
        s.add(
            Mirror(
                instance_pair_id=pair_bc_push,
                source_project_id=11,
                source_project_path="g/by",
                target_project_id=21,
                target_project_path="g/cy",
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
    # Direction comes from pair, not stored on mirror
    async with session_maker() as s:
        s.add(
            Mirror(
                instance_pair_id=pair_ab,
                source_project_id=1,
                source_project_path="g/a1",
                target_project_id=2,
                target_project_path="g/b1",
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


@pytest.mark.asyncio
async def test_topology_never_succeeded_level_can_be_error(client, session_maker):
    a_id = await seed_instance(session_maker, name="A", url="https://a.example.com")
    b_id = await seed_instance(session_maker, name="B", url="https://b.example.com")
    pair_ab = await seed_pair(session_maker, name="ab3", src_id=a_id, tgt_id=b_id, direction="pull")

    # Mirror exists but has never recorded a successful update timestamp.
    # Direction comes from pair, not stored on mirror
    async with session_maker() as s:
        s.add(
            Mirror(
                instance_pair_id=pair_ab,
                source_project_id=1,
                source_project_path="g/a1",
                target_project_id=2,
                target_project_path="g/b1",
                enabled=True,
                last_update_status="pending",
                last_successful_update=None,
            )
        )
        await s.commit()

    # Default: never succeeded => staleness warning
    resp = await client.get("/api/topology")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    link = body["links"][0]
    assert link["never_succeeded_count"] == 1
    assert link["staleness"] == "warning"

    # Override: never succeeded => staleness error
    resp = await client.get("/api/topology?never_succeeded_level=error")
    assert resp.status_code == 200, resp.text
    body2 = resp.json()
    link2 = body2["links"][0]
    assert link2["never_succeeded_count"] == 1
    assert link2["staleness"] == "error"
    assert link2["health"] == "error"


@pytest.mark.asyncio
async def test_topology_link_mirrors_drilldown_filters_and_sorts(client, session_maker):
    a_id = await seed_instance(session_maker, name="A", url="https://a.example.com")
    b_id = await seed_instance(session_maker, name="B", url="https://b.example.com")
    pair_ab = await seed_pair(session_maker, name="ab4", src_id=a_id, tgt_id=b_id, direction="push")

    async with session_maker() as s:
        # One failed (worst), one finished (best), one disabled pending (should be filtered if include_disabled=false)
        s.add_all(
            [
                Mirror(
                    instance_pair_id=pair_ab,
                    source_project_id=1,
                    source_project_path="g/a1",
                    target_project_id=2,
                    target_project_path="g/b1",
                    enabled=True,
                    last_update_status="failed",
                    last_successful_update=datetime.utcnow() - timedelta(hours=2),
                ),
                Mirror(
                    instance_pair_id=pair_ab,
                    source_project_id=3,
                    source_project_path="g/a2",
                    target_project_id=4,
                    target_project_path="g/b2",
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
                    enabled=False,
                    last_update_status="pending",
                    last_successful_update=None,
                ),
            ]
        )
        await s.commit()

    # include_disabled=false should exclude the disabled mirror
    resp = await client.get(
        f"/api/topology/link-mirrors?source_instance_id={a_id}&target_instance_id={b_id}&mirror_direction=push&include_disabled=false"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    assert len(body["mirrors"]) == 2
    # First should be the failed one due to sorting by health severity
    assert body["mirrors"][0]["last_update_status"] == "failed"
    assert body["mirrors"][0]["health"] == "error"

    # include_disabled=true should include all
    resp = await client.get(
        f"/api/topology/link-mirrors?source_instance_id={a_id}&target_instance_id={b_id}&mirror_direction=push&include_disabled=true"
    )
    assert resp.status_code == 200, resp.text
    body2 = resp.json()
    assert body2["total"] == 3
    assert len(body2["mirrors"]) == 3


@pytest.mark.asyncio
async def test_topology_bidirectional_shows_two_links(client, session_maker):
    """
    Test that bidirectional pairs (A→B and B→A) show as two separate links in topology.

    This validates that:
    1. Two nodes are created for the two instances
    2. Two links are created (one for each direction)
    3. Each link has correct source, target, and direction
    4. Node stats correctly aggregate mirrors from both directions
    """
    a_id = await seed_instance(session_maker, name="Production", url="https://prod.example.com")
    b_id = await seed_instance(session_maker, name="Staging", url="https://staging.example.com")

    # Create bidirectional pairs: A→B (push) and B→A (push)
    pair_ab = await seed_pair(session_maker, name="prod-to-staging", src_id=a_id, tgt_id=b_id, direction="push")
    pair_ba = await seed_pair(session_maker, name="staging-to-prod", src_id=b_id, tgt_id=a_id, direction="push")

    async with session_maker() as s:
        # A→B mirrors (2 mirrors)
        s.add_all([
            Mirror(
                instance_pair_id=pair_ab,
                source_project_id=1,
                source_project_path="prod/service-a",
                target_project_id=101,
                target_project_path="staging/service-a",
                enabled=True,
                last_update_status="finished",
                last_successful_update=datetime.utcnow() - timedelta(hours=1),
            ),
            Mirror(
                instance_pair_id=pair_ab,
                source_project_id=2,
                source_project_path="prod/service-b",
                target_project_id=102,
                target_project_path="staging/service-b",
                enabled=True,
                last_update_status="finished",
                last_successful_update=datetime.utcnow() - timedelta(hours=2),
            ),
        ])

        # B→A mirrors (1 mirror)
        s.add(
            Mirror(
                instance_pair_id=pair_ba,
                source_project_id=103,
                source_project_path="staging/hotfix",
                target_project_id=3,
                target_project_path="prod/hotfix",
                enabled=True,
                last_update_status="finished",
                last_successful_update=datetime.utcnow() - timedelta(minutes=30),
            )
        )
        await s.commit()

    resp = await client.get("/api/topology")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Should have exactly 2 nodes
    assert len(body["nodes"]) == 2
    nodes_by_name = {n["name"]: n for n in body["nodes"]}
    assert "Production" in nodes_by_name
    assert "Staging" in nodes_by_name

    # Should have exactly 2 links (one for each direction)
    assert len(body["links"]) == 2

    def link_key(l):
        return (l["source"], l["target"], l["mirror_direction"])

    links_by_key = {link_key(l): l for l in body["links"]}

    # A→B link
    ab_link = links_by_key[(a_id, b_id, "push")]
    assert ab_link["mirror_count"] == 2
    assert ab_link["pair_count"] == 1

    # B→A link
    ba_link = links_by_key[(b_id, a_id, "push")]
    assert ba_link["mirror_count"] == 1
    assert ba_link["pair_count"] == 1

    # Verify node stats aggregate correctly
    prod_node = nodes_by_name["Production"]
    staging_node = nodes_by_name["Staging"]

    # Production: 2 mirrors out (to staging), 1 mirror in (from staging)
    assert prod_node["mirrors_out"] == 2
    assert prod_node["mirrors_in"] == 1

    # Staging: 1 mirror out (to prod), 2 mirrors in (from prod)
    assert staging_node["mirrors_out"] == 1
    assert staging_node["mirrors_in"] == 2


@pytest.mark.asyncio
async def test_topology_bidirectional_different_directions(client, session_maker):
    """
    Test bidirectional pairs with different mirror directions (push vs pull).

    A→B with push, B→A with pull should create two distinct links.
    """
    a_id = await seed_instance(session_maker, name="Primary", url="https://primary.example.com")
    b_id = await seed_instance(session_maker, name="Secondary", url="https://secondary.example.com")

    # Create pairs with different directions
    pair_ab_push = await seed_pair(session_maker, name="primary-push-to-secondary", src_id=a_id, tgt_id=b_id, direction="push")
    pair_ba_pull = await seed_pair(session_maker, name="secondary-pull-to-primary", src_id=b_id, tgt_id=a_id, direction="pull")

    async with session_maker() as s:
        # A→B push mirror
        s.add(
            Mirror(
                instance_pair_id=pair_ab_push,
                source_project_id=1,
                source_project_path="primary/app",
                target_project_id=101,
                target_project_path="secondary/app",
                enabled=True,
                last_update_status="finished",
                last_successful_update=datetime.utcnow() - timedelta(minutes=30),
            )
        )
        # B→A pull mirror
        s.add(
            Mirror(
                instance_pair_id=pair_ba_pull,
                source_project_id=102,
                source_project_path="secondary/config",
                target_project_id=2,
                target_project_path="primary/config",
                enabled=True,
                last_update_status="pending",
            )
        )
        await s.commit()

    resp = await client.get("/api/topology")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Should have 2 nodes and 2 links
    assert len(body["nodes"]) == 2
    assert len(body["links"]) == 2

    def link_key(l):
        return (l["source"], l["target"], l["mirror_direction"])

    links_by_key = {link_key(l): l for l in body["links"]}

    # A→B push link
    assert (a_id, b_id, "push") in links_by_key
    ab_push = links_by_key[(a_id, b_id, "push")]
    assert ab_push["mirror_count"] == 1
    assert ab_push["health"] == "ok"  # finished status

    # B→A pull link
    assert (b_id, a_id, "pull") in links_by_key
    ba_pull = links_by_key[(b_id, a_id, "pull")]
    assert ba_pull["mirror_count"] == 1
    assert ba_pull["health"] == "warning"  # pending status


@pytest.mark.asyncio
async def test_topology_link_mirrors_bidirectional_drilldown(client, session_maker):
    """
    Test that link-mirrors drilldown works correctly for bidirectional pairs.

    Each direction should only show mirrors belonging to pairs in that direction.
    """
    a_id = await seed_instance(session_maker, name="A", url="https://a.example.com")
    b_id = await seed_instance(session_maker, name="B", url="https://b.example.com")

    pair_ab = await seed_pair(session_maker, name="a-to-b", src_id=a_id, tgt_id=b_id, direction="push")
    pair_ba = await seed_pair(session_maker, name="b-to-a", src_id=b_id, tgt_id=a_id, direction="push")

    async with session_maker() as s:
        # 3 mirrors on A→B
        for i in range(3):
            s.add(
                Mirror(
                    instance_pair_id=pair_ab,
                    source_project_id=i + 1,
                    source_project_path=f"a/proj{i}",
                    target_project_id=i + 101,
                    target_project_path=f"b/proj{i}",
                    enabled=True,
                    last_update_status="finished",
                )
            )

        # 2 mirrors on B→A
        for i in range(2):
            s.add(
                Mirror(
                    instance_pair_id=pair_ba,
                    source_project_id=i + 201,
                    source_project_path=f"b/back{i}",
                    target_project_id=i + 11,
                    target_project_path=f"a/back{i}",
                    enabled=True,
                    last_update_status="finished",
                )
            )

        await s.commit()

    # Drilldown on A→B link should show 3 mirrors
    resp_ab = await client.get(
        f"/api/topology/link-mirrors?source_instance_id={a_id}&target_instance_id={b_id}&mirror_direction=push"
    )
    assert resp_ab.status_code == 200
    body_ab = resp_ab.json()
    assert body_ab["total"] == 3
    assert all(m["instance_pair_name"] == "a-to-b" for m in body_ab["mirrors"])

    # Drilldown on B→A link should show 2 mirrors
    resp_ba = await client.get(
        f"/api/topology/link-mirrors?source_instance_id={b_id}&target_instance_id={a_id}&mirror_direction=push"
    )
    assert resp_ba.status_code == 200
    body_ba = resp_ba.json()
    assert body_ba["total"] == 2
    assert all(m["instance_pair_name"] == "b-to-a" for m in body_ba["mirrors"])

