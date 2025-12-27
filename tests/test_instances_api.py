import pytest
from sqlalchemy import select

from app.models import GitLabInstance, InstancePair, Mirror


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
        # Mirror attached to the pair.
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
        s.add(m)
        await s.commit()

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


@pytest.mark.asyncio
async def test_instances_update_url_disallowed_when_used_by_pair(client, session_maker, monkeypatch):
    from app.api import instances as inst_mod

    monkeypatch.setattr(inst_mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.test_ok = True

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

    resp = await client.post(
        "/api/pairs",
        json={"name": "pair-url-lock", "source_instance_id": src_id, "target_instance_id": tgt_id, "mirror_direction": "pull"},
    )
    assert resp.status_code == 200, resp.text

    resp = await client.put(f"/api/instances/{src_id}", json={"url": "https://new.example.com"})
    assert resp.status_code == 400
    assert "cannot be changed" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_instances_get_not_found(client):
    """Test 404 when getting non-existent instance."""
    resp = await client.get("/api/instances/9999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_instances_update_not_found(client):
    """Test 404 when updating non-existent instance."""
    resp = await client.put("/api/instances/9999", json={"description": "test"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_instances_delete_not_found(client):
    """Test 404 when deleting non-existent instance."""
    resp = await client.delete("/api/instances/9999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_instances_projects_not_found(client):
    """Test 404 when getting projects for non-existent instance."""
    resp = await client.get("/api/instances/9999/projects")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_instances_groups_not_found(client):
    """Test 404 when getting groups for non-existent instance."""
    resp = await client.get("/api/instances/9999/groups")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_instances_projects_pagination_clamping(client, session_maker, monkeypatch):
    """Test that pagination parameters are clamped to safe values."""
    from app.api import instances as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.test_ok = True
    FakeGitLabClient.projects = []

    # Create instance
    resp = await client.post(
        "/api/instances",
        json={"name": "inst1", "url": "https://x", "token": "t1", "description": ""},
    )
    instance_id = resp.json()["id"]

    # Test per_page is clamped to max 100
    resp = await client.get(f"/api/instances/{instance_id}/projects?per_page=999&page=5")
    assert resp.status_code == 200

    # Test page is clamped to minimum 1
    resp = await client.get(f"/api/instances/{instance_id}/projects?page=0")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_instances_projects_gitlab_error(client, session_maker, monkeypatch):
    """Test error handling when GitLab API fails for projects."""
    from app.api import instances as mod

    class FailingGitLabClient:
        def __init__(self, url: str, encrypted_token: str):
            pass

        def get_projects(self, *args, **kwargs):
            raise Exception("GitLab API down")

    monkeypatch.setattr(mod, "GitLabClient", FailingGitLabClient)

    # Create instance
    async with session_maker() as s:
        inst = GitLabInstance(
            name="inst", url="https://gitlab.com", encrypted_token="enc:token"
        )
        s.add(inst)
        await s.commit()
        await s.refresh(inst)
        instance_id = inst.id

    resp = await client.get(f"/api/instances/{instance_id}/projects")
    assert resp.status_code == 500
    assert "failed to fetch projects" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_instances_groups_gitlab_error(client, session_maker, monkeypatch):
    """Test error handling when GitLab API fails for groups."""
    from app.api import instances as mod

    class FailingGitLabClient:
        def __init__(self, url: str, encrypted_token: str):
            pass

        def get_groups(self, *args, **kwargs):
            raise Exception("GitLab API error")

    monkeypatch.setattr(mod, "GitLabClient", FailingGitLabClient)

    # Create instance
    async with session_maker() as s:
        inst = GitLabInstance(
            name="inst", url="https://gitlab.com", encrypted_token="enc:token"
        )
        s.add(inst)
        await s.commit()
        await s.refresh(inst)
        instance_id = inst.id

    resp = await client.get(f"/api/instances/{instance_id}/groups")
    assert resp.status_code == 500
    assert "failed to fetch groups" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_instances_projects_with_search(client, session_maker, monkeypatch):
    """Test fetching projects with search parameter."""
    from app.api import instances as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.test_ok = True
    FakeGitLabClient.projects = [
        {"id": 1, "name": "matching-project"},
        {"id": 2, "name": "another-match"}
    ]

    # Create instance
    resp = await client.post(
        "/api/instances",
        json={"name": "inst1", "url": "https://x", "token": "t1", "description": ""},
    )
    instance_id = resp.json()["id"]

    resp = await client.get(f"/api/instances/{instance_id}/projects?search=match")
    assert resp.status_code == 200
    data = resp.json()
    assert "projects" in data


@pytest.mark.asyncio
async def test_instances_groups_with_pagination(client, session_maker, monkeypatch):
    """Test fetching groups with pagination."""
    from app.api import instances as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.test_ok = True
    FakeGitLabClient.groups = [
        {"id": 1, "name": "group1"},
        {"id": 2, "name": "group2"}
    ]

    # Create instance
    resp = await client.post(
        "/api/instances",
        json={"name": "inst1", "url": "https://x", "token": "t1", "description": ""},
    )
    instance_id = resp.json()["id"]

    resp = await client.get(f"/api/instances/{instance_id}/groups?page=2&per_page=10")
    assert resp.status_code == 200
    data = resp.json()
    assert "groups" in data


@pytest.mark.asyncio
async def test_instances_update_description_only(client, session_maker, monkeypatch):
    """Test updating only the description field."""
    from app.api import instances as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.test_ok = True

    # Create instance
    resp = await client.post(
        "/api/instances",
        json={"name": "inst1", "url": "https://x", "token": "t1", "description": "original"},
    )
    instance_id = resp.json()["id"]

    # Update only description
    resp = await client.put(f"/api/instances/{instance_id}", json={"description": "updated"})
    assert resp.status_code == 200
    assert resp.json()["description"] == "updated"


@pytest.mark.asyncio
async def test_instances_update_name_only(client, session_maker, monkeypatch):
    """Test updating only the name field."""
    from app.api import instances as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.test_ok = True

    # Create instance
    resp = await client.post(
        "/api/instances",
        json={"name": "inst1", "url": "https://x", "token": "t1", "description": ""},
    )
    instance_id = resp.json()["id"]

    # Update only name
    resp = await client.put(f"/api/instances/{instance_id}", json={"name": "renamed"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "renamed"


@pytest.mark.asyncio
async def test_instances_create_with_empty_description(client, monkeypatch):
    """Test creating instance with empty description."""
    from app.api import instances as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.test_ok = True

    resp = await client.post(
        "/api/instances",
        json={"name": "inst1", "url": "https://x", "token": "t1", "description": ""},
    )
    assert resp.status_code == 200
    # Empty string is allowed
    assert resp.json()["description"] == ""

