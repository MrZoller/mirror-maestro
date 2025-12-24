import pytest
from sqlalchemy import select

from app.models import GitLabInstance, InstancePair, Mirror, GroupAccessToken, GroupMirrorDefaults


class FakeGitLabClient:
    test_ok = True
    projects = [{"id": 1, "name": "p"}]
    groups = [{"id": 2, "name": "g"}]
    current_user = {"id": 42, "username": "mirror-bot", "name": "Mirror Bot"}

    def __init__(self, url: str, encrypted_token: str):
        self.url = url
        self.encrypted_token = encrypted_token

    def test_connection(self) -> bool:
        return self.test_ok

    def get_projects(self, search=None, *, per_page=50, page=1, get_all=False):
        return self.projects

    def get_groups(self, search=None, *, per_page=50, page=1, get_all=False):
        return self.groups

    def get_current_user(self):
        return self.current_user


@pytest.mark.asyncio
async def test_instances_list_empty(client):
    resp = await client.get("/api/instances")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_instances_create_and_get_and_delete(client, session_maker, monkeypatch):
    from app.api import instances as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.test_ok = True

    payload = {
        "name": "inst1",
        "url": "https://gitlab.example.com",
        "token": "t1",
        "description": "d",
    }
    resp = await client.post("/api/instances", json=payload)
    assert resp.status_code == 200
    created = resp.json()
    assert created["name"] == "inst1"
    assert created["url"] == "https://gitlab.example.com"

    # Token should be stored encrypted in DB (fixture swaps encryption to FakeEncryption)
    async with session_maker() as s:
        row = (await s.execute(select(GitLabInstance).where(GitLabInstance.id == created["id"]))).scalar_one()
        assert row.encrypted_token == "enc:t1"

    resp = await client.get(f"/api/instances/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]

    resp = await client.delete(f"/api/instances/{created['id']}")
    assert resp.status_code == 200
    assert resp.json() == {"status": "deleted"}


@pytest.mark.asyncio
async def test_instances_delete_cascades_pairs_mirrors_group_settings_and_tokens(client, session_maker, monkeypatch):
    """
    Deleting a GitLab instance should also delete any associated instance pairs and mirrors
    (and related group defaults), plus group access tokens for that instance.
    """
    from app.api import instances as inst_mod

    monkeypatch.setattr(inst_mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.test_ok = True

    # Create two instances via API (exercises encryption swap + token user fetch best-effort).
    resp = await client.post(
        "/api/instances",
        json={"name": "inst-src", "url": "https://src.example.com", "token": "t-src", "description": ""},
    )
    assert resp.status_code == 200, resp.text
    src_id = resp.json()["id"]

    resp = await client.post(
        "/api/instances",
        json={"name": "inst-tgt", "url": "https://tgt.example.com", "token": "t-tgt", "description": ""},
    )
    assert resp.status_code == 200, resp.text
    tgt_id = resp.json()["id"]

    # Pair references src_id (will be deleted when deleting src instance).
    resp = await client.post(
        "/api/pairs",
        json={
            "name": "pair-for-instance-delete",
            "source_instance_id": src_id,
            "target_instance_id": tgt_id,
            "mirror_direction": "pull",
        },
    )
    assert resp.status_code == 200, resp.text
    pair_id = resp.json()["id"]

    async with session_maker() as s:
        # Mirror & group defaults attached to the pair.
        m = Mirror(
            instance_pair_id=pair_id,
            source_project_id=1,
            source_project_path="platform/proj",
            target_project_id=2,
            target_project_path="platform/proj",
            mirror_id=77,
            enabled=True,
            last_update_status="pending",
        )
        gd = GroupMirrorDefaults(
            instance_pair_id=pair_id,
            group_path="platform",
            mirror_direction="pull",
        )

        # Tokens: one for the instance being deleted, one for the other instance.
        tok_src = GroupAccessToken(
            gitlab_instance_id=src_id,
            group_path="platform",
            encrypted_token="enc:tok-src",
            token_name="bot-src",
        )
        tok_tgt = GroupAccessToken(
            gitlab_instance_id=tgt_id,
            group_path="platform",
            encrypted_token="enc:tok-tgt",
            token_name="bot-tgt",
        )
        s.add_all([m, gd, tok_src, tok_tgt])
        await s.commit()
        await s.refresh(tok_src)
        await s.refresh(tok_tgt)
        tok_src_id = tok_src.id
        tok_tgt_id = tok_tgt.id

    # Delete source instance and assert cascade.
    resp = await client.delete(f"/api/instances/{src_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "deleted"}

    async with session_maker() as s:
        inst_src = (await s.execute(select(GitLabInstance).where(GitLabInstance.id == src_id))).scalar_one_or_none()
        inst_tgt = (await s.execute(select(GitLabInstance).where(GitLabInstance.id == tgt_id))).scalar_one_or_none()
        assert inst_src is None
        assert inst_tgt is not None

        pair = (await s.execute(select(InstancePair).where(InstancePair.id == pair_id))).scalar_one_or_none()
        assert pair is None

        mirrors = (await s.execute(select(Mirror).where(Mirror.instance_pair_id == pair_id))).scalars().all()
        assert mirrors == []

        defaults = (await s.execute(select(GroupMirrorDefaults).where(GroupMirrorDefaults.instance_pair_id == pair_id))).scalars().all()
        assert defaults == []

        tok_src_row = (await s.execute(select(GroupAccessToken).where(GroupAccessToken.id == tok_src_id))).scalar_one_or_none()
        tok_tgt_row = (await s.execute(select(GroupAccessToken).where(GroupAccessToken.id == tok_tgt_id))).scalar_one_or_none()
        assert tok_src_row is None
        assert tok_tgt_row is not None


@pytest.mark.asyncio
async def test_instances_create_rejects_bad_connection(client, monkeypatch):
    from app.api import instances as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.test_ok = False

    resp = await client.post(
        "/api/instances",
        json={"name": "inst1", "url": "https://x", "token": "t", "description": ""},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_instances_update_token(client, session_maker, monkeypatch):
    from app.api import instances as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.test_ok = True

    resp = await client.post(
        "/api/instances",
        json={"name": "inst1", "url": "https://x", "token": "t1", "description": ""},
    )
    instance_id = resp.json()["id"]

    resp = await client.put(f"/api/instances/{instance_id}", json={"token": "t2"})
    assert resp.status_code == 200

    async with session_maker() as s:
        row = (await s.execute(select(GitLabInstance).where(GitLabInstance.id == instance_id))).scalar_one()
        assert row.encrypted_token == "enc:t2"


@pytest.mark.asyncio
async def test_instances_projects_and_groups(client, session_maker, monkeypatch):
    from app.api import instances as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.test_ok = True
    FakeGitLabClient.projects = [{"id": 123, "name": "proj"}]
    FakeGitLabClient.groups = [{"id": 456, "name": "grp"}]

    resp = await client.post(
        "/api/instances",
        json={"name": "inst1", "url": "https://x", "token": "t1", "description": ""},
    )
    instance_id = resp.json()["id"]

    resp = await client.get(f"/api/instances/{instance_id}/projects")
    assert resp.status_code == 200
    assert resp.json() == {"projects": [{"id": 123, "name": "proj"}]}

    resp = await client.get(f"/api/instances/{instance_id}/groups")
    assert resp.status_code == 200
    assert resp.json() == {"groups": [{"id": 456, "name": "grp"}]}

