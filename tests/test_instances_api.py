import pytest
from sqlalchemy import select

from app.models import GitLabInstance


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

    def get_groups(self, search=None):
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

