import pytest
from sqlalchemy import select

from app.models import GitLabInstance, InstancePair, Mirror


class FakeGitLabClient:
    inits = []
    pull_calls = []
    push_calls = []
    trigger_calls = []
    delete_calls = []
    update_calls = []
    token_create_calls = []
    token_delete_calls = []
    project_mirrors = {}  # project_id -> list[dict]

    def __init__(self, url: str, encrypted_token: str, timeout: int = 60):
        self.url = url
        self.encrypted_token = encrypted_token
        self.__class__.inits.append((url, encrypted_token))

    def create_project_access_token(
        self,
        project_id: int,
        name: str,
        scopes: list,
        expires_at: str,
        access_level: int = 40,
    ):
        self.__class__.token_create_calls.append((project_id, name, scopes, expires_at, access_level))
        return {"id": 999, "name": name, "token": "fake-token-value", "scopes": scopes, "expires_at": expires_at}

    def delete_project_access_token(self, project_id: int, token_id: int) -> bool:
        self.__class__.token_delete_calls.append((project_id, token_id))
        return True

    def create_pull_mirror(
        self,
        project_id: int,
        mirror_url: str,
        enabled=True,
        only_protected_branches=False,
        keep_divergent_refs=None,
        trigger_builds=None,
        mirror_branch_regex=None,
        mirror_user_id=None,
    ):
        self.__class__.pull_calls.append(
            (project_id, mirror_url, enabled, only_protected_branches, keep_divergent_refs, trigger_builds, mirror_branch_regex, mirror_user_id)
        )
        return {"id": 77}

    def create_push_mirror(
        self,
        project_id: int,
        mirror_url: str,
        enabled=True,
        keep_divergent_refs=None,
        only_protected_branches=False,
        mirror_branch_regex=None,
        mirror_user_id=None,
    ):
        self.__class__.push_calls.append(
            (project_id, mirror_url, enabled, keep_divergent_refs, only_protected_branches, mirror_branch_regex, mirror_user_id)
        )
        return {"id": 88}

    def get_project_mirrors(self, project_id: int):
        return list(self.__class__.project_mirrors.get(project_id, []))

    def trigger_mirror_update(self, project_id: int, mirror_id: int) -> bool:
        self.__class__.trigger_calls.append((project_id, mirror_id))
        return True

    def delete_mirror(self, project_id: int, mirror_id: int) -> bool:
        self.__class__.delete_calls.append((project_id, mirror_id))
        return True

    def update_mirror(
        self,
        project_id: int,
        mirror_id: int,
        enabled=None,
        only_protected_branches=None,
        keep_divergent_refs=None,
        trigger_builds=None,
        mirror_branch_regex=None,
        mirror_user_id=None,
        mirror_direction=None,
    ):
        self.__class__.update_calls.append(
            (
                project_id,
                mirror_id,
                enabled,
                only_protected_branches,
                keep_divergent_refs,
                trigger_builds,
                mirror_branch_regex,
                mirror_user_id,
                mirror_direction,
            )
        )
        return {"id": mirror_id}


async def seed_instance(session_maker, *, name: str, url: str) -> int:
    async with session_maker() as s:
        inst = GitLabInstance(name=name, url=url, encrypted_token="enc:t", description="", api_user_id=None, api_username=None)
        s.add(inst)
        await s.commit()
        await s.refresh(inst)
        return inst.id


async def seed_pair(session_maker, *, name: str, src_id: int, tgt_id: int, direction: str = "pull") -> int:
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
async def test_mirrors_trigger_update_updates_status(client, session_maker, monkeypatch):
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.trigger_calls.clear()
    FakeGitLabClient.project_mirrors.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    async with session_maker() as s:
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
        await s.refresh(m)
        mirror_id = m.id

    resp = await client.post(f"/api/mirrors/{mirror_id}/update")
    assert resp.status_code == 200
    assert resp.json() == {"status": "update_triggered"}
    assert FakeGitLabClient.trigger_calls[-1] == (2, 77)

    async with session_maker() as s:
        m2 = (await s.execute(select(Mirror).where(Mirror.id == mirror_id))).scalar_one()
        assert m2.last_update_status == "updating"


@pytest.mark.asyncio
async def test_mirrors_delete_best_effort_gitlab_and_db(client, session_maker, monkeypatch):
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.delete_calls.clear()
    FakeGitLabClient.project_mirrors.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    async with session_maker() as s:
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
        await s.refresh(m)
        mirror_id = m.id

    resp = await client.delete(f"/api/mirrors/{mirror_id}")
    assert resp.status_code == 200
    assert resp.json() == {"status": "deleted"}
    assert FakeGitLabClient.delete_calls[-1] == (2, 77)

    async with session_maker() as s:
        row = (await s.execute(select(Mirror).where(Mirror.id == mirror_id))).scalar_one_or_none()
        assert row is None


@pytest.mark.asyncio
async def test_mirrors_update_applies_settings_to_gitlab(client, session_maker, monkeypatch):
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.update_calls.clear()
    FakeGitLabClient.project_mirrors.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    async with session_maker() as s:
        # Pair defaults
        pair = (await s.execute(select(InstancePair).where(InstancePair.id == pair_id))).scalar_one()
        pair.mirror_overwrite_diverged = True
        pair.only_mirror_protected_branches = True
        pair.mirror_trigger_builds = True
        pair.mirror_branch_regex = "^main$"

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
        await s.refresh(m)
        db_mirror_id = m.id

    # Update enabled only; other values should be inherited from pair defaults.
    resp = await client.put(f"/api/mirrors/{db_mirror_id}", json={"enabled": False})
    assert resp.status_code == 200, resp.text

    # Pull direction => update on target project_id (2)
    assert FakeGitLabClient.update_calls[-1] == (
        2,   # project_id
        77,  # mirror_id
        False,  # enabled
        True,   # only_protected_branches (pair default)
        False,  # keep_divergent_refs (not overwrite_diverged)
        True,   # trigger_builds (pair default; pull only)
        "^main$",
        None,  # mirror_user_id (from target instance, which was None)
        "pull",
    )


@pytest.mark.asyncio
async def test_mirrors_create_pull_defaults_mirror_user_to_token_user(client, session_maker, monkeypatch):
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.pull_calls.clear()
    FakeGitLabClient.project_mirrors.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")

    # Store token user identity on the target instance (pull mirror lives on target).
    async with session_maker() as s:
        tgt = (await s.execute(select(GitLabInstance).where(GitLabInstance.id == tgt_id))).scalar_one()
        tgt.api_user_id = 123
        tgt.api_username = "mirror-bot"
        await s.commit()

    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    payload = {
        "instance_pair_id": pair_id,
        "source_project_id": 1,
        "source_project_path": "platform/proj",
        "target_project_id": 2,
        "target_project_path": "platform/proj",
        "enabled": True,
    }
    resp = await client.post("/api/mirrors", json=payload)
    assert resp.status_code == 200, resp.text

    *_head, mirror_user_id = FakeGitLabClient.pull_calls[-1]
    assert mirror_user_id == 123


@pytest.mark.asyncio
async def test_mirrors_update_can_clear_overrides_with_null(client, session_maker):
    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    async with session_maker() as s:
        m = Mirror(
            instance_pair_id=pair_id,
            source_project_id=1,
            source_project_path="platform/proj",
            target_project_id=2,
            target_project_path="platform/proj",
            mirror_id=None,  # avoid any GitLab client calls
            enabled=True,
            last_update_status="pending",
            # Direction comes from pair, not stored on mirror
            mirror_overwrite_diverged=True,  # explicit override
        )
        s.add(m)
        await s.commit()
        await s.refresh(m)
        db_mirror_id = m.id

    resp = await client.put(f"/api/mirrors/{db_mirror_id}", json={"mirror_overwrite_diverged": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["mirror_overwrite_diverged"] is None

    async with session_maker() as s:
        row = (await s.execute(select(Mirror).where(Mirror.id == db_mirror_id))).scalar_one()
        assert row.mirror_overwrite_diverged is None


@pytest.mark.asyncio
async def test_mirrors_create_pull_conflicts_when_existing_pull_mirror_present(client, session_maker, monkeypatch):
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.inits.clear()
    FakeGitLabClient.pull_calls.clear()
    FakeGitLabClient.project_mirrors.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    # Simulate an existing pull mirror on the target project
    FakeGitLabClient.project_mirrors[2] = [{"id": 999, "mirror_direction": "pull", "url": "https://example.com/existing.git"}]

    payload = {
        "instance_pair_id": pair_id,
        "source_project_id": 1,
        "source_project_path": "platform/proj",
        "target_project_id": 2,
        "target_project_path": "platform/proj",
        "enabled": True,
    }
    resp = await client.post("/api/mirrors", json=payload)
    assert resp.status_code == 409
    body = resp.json()
    assert "existing_pull_mirrors" in body["detail"]
    assert FakeGitLabClient.pull_calls == []


@pytest.mark.asyncio
async def test_mirrors_preflight_lists_existing_same_direction(client, session_maker, monkeypatch):
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.project_mirrors.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    FakeGitLabClient.project_mirrors[2] = [
        {"id": 1, "mirror_direction": "pull", "url": "https://example.com/a.git"},
        {"id": 2, "mirror_direction": "push", "url": "https://example.com/b.git"},
    ]

    payload = {
        "instance_pair_id": pair_id,
        "source_project_id": 1,
        "source_project_path": "platform/proj",
        "target_project_id": 2,
        "target_project_path": "platform/proj",
    }
    resp = await client.post("/api/mirrors/preflight", json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["effective_direction"] == "pull"
    assert body["owner_project_id"] == 2
    assert len(body["existing_same_direction"]) == 1
    assert body["existing_same_direction"][0]["id"] == 1


@pytest.mark.asyncio
async def test_mirrors_remove_existing_deletes_same_direction(client, session_maker, monkeypatch):
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.delete_calls.clear()
    FakeGitLabClient.project_mirrors.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    FakeGitLabClient.project_mirrors[2] = [
        {"id": 11, "mirror_direction": "pull", "url": "https://example.com/a.git"},
        {"id": 22, "mirror_direction": "push", "url": "https://example.com/b.git"},
    ]

    payload = {
        "instance_pair_id": pair_id,
        "source_project_id": 1,
        "source_project_path": "platform/proj",
        "target_project_id": 2,
        "target_project_path": "platform/proj",
    }
    resp = await client.post("/api/mirrors/remove-existing", json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted"] == 1
    assert body["deleted_ids"] == [11]
    assert FakeGitLabClient.delete_calls[-1] == (2, 11)


@pytest.mark.asyncio
async def test_mirrors_list_empty(client):
    """Test listing mirrors when none exist."""
    resp = await client.get("/api/mirrors")
    assert resp.status_code == 200
    data = resp.json()
    assert data['mirrors'] == []
    assert data['total'] == 0
    assert data['page'] == 1
    assert data['total_pages'] == 0


@pytest.mark.asyncio
async def test_mirrors_list_returns_all_mirrors(client, session_maker):
    """Test listing all mirrors."""
    # Create instances and pair
    async with session_maker() as s:
        src_inst = GitLabInstance(
            name="src", url="https://src.com", encrypted_token="enc:src"
        )
        tgt_inst = GitLabInstance(
            name="tgt", url="https://tgt.com", encrypted_token="enc:tgt"
        )
        s.add_all([src_inst, tgt_inst])
        await s.commit()
        await s.refresh(src_inst)
        await s.refresh(tgt_inst)

        pair = InstancePair(
            name="pair",
            source_instance_id=src_inst.id,
            target_instance_id=tgt_inst.id,
            mirror_direction="pull",
        )
        s.add(pair)
        await s.commit()
        await s.refresh(pair)

        # Create multiple mirrors
        mirror1 = Mirror(
            instance_pair_id=pair.id,
            source_project_id=1,
            source_project_path="group/proj1",
            target_project_id=2,
            target_project_path="group/proj1",
            mirror_id=101,
            enabled=True,
            last_update_status="finished",
        )
        mirror2 = Mirror(
            instance_pair_id=pair.id,
            source_project_id=3,
            source_project_path="group/proj2",
            target_project_id=4,
            target_project_path="group/proj2",
            mirror_id=102,
            enabled=True,
            last_update_status="pending",
        )
        s.add_all([mirror1, mirror2])
        await s.commit()

    resp = await client.get("/api/mirrors")
    assert resp.status_code == 200
    data = resp.json()
    assert data['total'] == 2
    mirrors = data['mirrors']
    assert len(mirrors) == 2
    # Default order is created_at desc, so mirror2 (created last) appears first
    assert mirrors[0]["source_project_path"] == "group/proj2"
    assert mirrors[1]["source_project_path"] == "group/proj1"


@pytest.mark.asyncio
async def test_mirrors_get_by_id(client, session_maker):
    """Test getting a single mirror by ID."""
    # Create instance, pair, and mirror
    async with session_maker() as s:
        src_inst = GitLabInstance(
            name="src", url="https://src.com", encrypted_token="enc:src"
        )
        tgt_inst = GitLabInstance(
            name="tgt", url="https://tgt.com", encrypted_token="enc:tgt"
        )
        s.add_all([src_inst, tgt_inst])
        await s.commit()
        await s.refresh(src_inst)
        await s.refresh(tgt_inst)

        pair = InstancePair(
            name="pair",
            source_instance_id=src_inst.id,
            target_instance_id=tgt_inst.id,
            mirror_direction="pull",
        )
        s.add(pair)
        await s.commit()
        await s.refresh(pair)

        mirror = Mirror(
            instance_pair_id=pair.id,
            source_project_id=123,
            source_project_path="group/project",
            target_project_id=456,
            target_project_path="group/project",
            mirror_id=789,
            enabled=True,
            last_update_status="finished",
        )
        s.add(mirror)
        await s.commit()
        await s.refresh(mirror)
        mirror_id = mirror.id

    resp = await client.get(f"/api/mirrors/{mirror_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == mirror_id
    assert data["source_project_id"] == 123
    assert data["target_project_id"] == 456
    assert data["mirror_id"] == 789
    assert data["last_update_status"] == "finished"


@pytest.mark.asyncio
async def test_mirrors_get_not_found(client):
    """Test 404 when mirror doesn't exist."""
    resp = await client.get("/api/mirrors/9999")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_mirrors_create_invalid_pair(client):
    """Test creating mirror with non-existent pair."""
    payload = {
        "instance_pair_id": 9999,
        "source_project_id": 1,
        "source_project_path": "group/proj",
        "target_project_id": 2,
        "target_project_path": "group/proj",
    }
    resp = await client.post("/api/mirrors", json=payload)
    assert resp.status_code == 404
    assert "pair" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_mirrors_create_gitlab_api_failure(client, session_maker, monkeypatch):
    """Test error handling when GitLab API fails during mirror creation."""
    from app.api import mirrors as mod

    # Set up fake client that fails
    class FailingGitLabClient:
        def __init__(self, url: str, encrypted_token: str):
            pass

        def create_pull_mirror(self, *args, **kwargs):
            raise Exception("GitLab API error")

    monkeypatch.setattr(mod, "GitLabClient", FailingGitLabClient)

    # Create instances and pair
    async with session_maker() as s:
        src_inst = GitLabInstance(
            name="src", url="https://src.com", encrypted_token="enc:src"
        )
        tgt_inst = GitLabInstance(
            name="tgt", url="https://tgt.com", encrypted_token="enc:tgt"
        )
        s.add_all([src_inst, tgt_inst])
        await s.commit()
        await s.refresh(src_inst)
        await s.refresh(tgt_inst)

        pair = InstancePair(
            name="pair",
            source_instance_id=src_inst.id,
            target_instance_id=tgt_inst.id,
            mirror_direction="pull",
        )
        s.add(pair)
        await s.commit()
        await s.refresh(pair)
        pair_id = pair.id

    payload = {
        "instance_pair_id": pair_id,
        "source_project_id": 1,
        "source_project_path": "group/proj",
        "target_project_id": 2,
        "target_project_path": "group/proj",
    }
    resp = await client.post("/api/mirrors", json=payload)
    # API returns 500 for unhandled exceptions during GitLab API calls
    assert resp.status_code in [400, 500]
    detail = resp.json()["detail"]
    assert "error" in detail.lower() or "failed" in detail.lower()


@pytest.mark.asyncio
async def test_mirrors_update_not_found(client):
    """Test updating a non-existent mirror."""
    payload = {"enabled": False}
    resp = await client.put("/api/mirrors/9999", json=payload)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_mirrors_delete_not_found(client):
    """Test deleting a non-existent mirror."""
    resp = await client.delete("/api/mirrors/9999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_mirrors_trigger_update_not_found(client):
    """Test triggering update on non-existent mirror."""
    resp = await client.post("/api/mirrors/9999/update")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_mirrors_update_multiple_settings(client, session_maker, monkeypatch):
    """Test updating multiple mirror settings at once."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.update_calls.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    async with session_maker() as s:
        m = Mirror(
            instance_pair_id=pair_id,
            source_project_id=1,
            source_project_path="platform/proj",
            target_project_id=2,
            target_project_path="platform/proj",
            mirror_id=77,
            enabled=True,
            last_update_status="finished",
        )
        s.add(m)
        await s.commit()
        await s.refresh(m)
        mirror_id = m.id

    # Update multiple settings
    payload = {
        "enabled": False,
        "mirror_overwrite_diverged": True,
        "only_mirror_protected_branches": True,
        "mirror_trigger_builds": True,
        "mirror_branch_regex": "^release/.*$",
    }
    resp = await client.put(f"/api/mirrors/{mirror_id}", json=payload)
    assert resp.status_code == 200

    data = resp.json()
    assert data["enabled"] is False
    assert data["mirror_overwrite_diverged"] is True
    assert data["only_mirror_protected_branches"] is True

    # Verify DB was updated
    async with session_maker() as s:
        row = (await s.execute(select(Mirror).where(Mirror.id == mirror_id))).scalar_one()
        assert row.enabled is False
        assert row.mirror_overwrite_diverged is True


@pytest.mark.asyncio
async def test_mirrors_update_partial_settings(client, session_maker, monkeypatch):
    """Test updating only some settings leaves others unchanged."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.update_calls.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    async with session_maker() as s:
        m = Mirror(
            instance_pair_id=pair_id,
            source_project_id=1,
            source_project_path="platform/proj",
            target_project_id=2,
            target_project_path="platform/proj",
            mirror_id=77,
            enabled=True,
            mirror_overwrite_diverged=True,
            last_update_status="finished",
        )
        s.add(m)
        await s.commit()
        await s.refresh(m)
        mirror_id = m.id

    # Update only enabled status
    resp = await client.put(f"/api/mirrors/{mirror_id}", json={"enabled": False})
    assert resp.status_code == 200

    async with session_maker() as s:
        row = (await s.execute(select(Mirror).where(Mirror.id == mirror_id))).scalar_one()
        assert row.enabled is False
        # Other settings unchanged
        assert row.mirror_overwrite_diverged is True


@pytest.mark.asyncio
async def test_mirrors_update_gitlab_api_failure(client, session_maker, monkeypatch):
    """Test update continues even when GitLab API fails (best effort)."""
    from app.api import mirrors as mod

    class FailingGitLabClient:
        def __init__(self, url: str, encrypted_token: str):
            pass

        def update_mirror(self, *args, **kwargs):
            raise Exception("GitLab API down")

    monkeypatch.setattr(mod, "GitLabClient", FailingGitLabClient)

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    async with session_maker() as s:
        m = Mirror(
            instance_pair_id=pair_id,
            source_project_id=1,
            source_project_path="platform/proj",
            target_project_id=2,
            target_project_path="platform/proj",
            mirror_id=77,
            enabled=True,
            last_update_status="finished",
        )
        s.add(m)
        await s.commit()
        await s.refresh(m)
        mirror_id = m.id

    # Update should fail with 500/400
    resp = await client.put(f"/api/mirrors/{mirror_id}", json={"enabled": False})
    assert resp.status_code in [400, 500]


@pytest.mark.asyncio
async def test_mirrors_delete_without_mirror_id(client, session_maker, monkeypatch):
    """Test deleting a mirror that was never created in GitLab."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.delete_calls.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    async with session_maker() as s:
        m = Mirror(
            instance_pair_id=pair_id,
            source_project_id=1,
            source_project_path="platform/proj",
            target_project_id=2,
            target_project_path="platform/proj",
            mirror_id=None,  # No GitLab mirror ID
            enabled=False,
            last_update_status="pending",
        )
        s.add(m)
        await s.commit()
        await s.refresh(m)
        mirror_id = m.id

    resp = await client.delete(f"/api/mirrors/{mirror_id}")
    assert resp.status_code == 200
    assert resp.json() == {"status": "deleted"}

    # Should not have tried to delete from GitLab
    assert FakeGitLabClient.delete_calls == []

    # But should still delete from DB
    async with session_maker() as s:
        row = (await s.execute(select(Mirror).where(Mirror.id == mirror_id))).scalar_one_or_none()
        assert row is None


@pytest.mark.asyncio
async def test_mirrors_delete_gitlab_api_failure_still_deletes_db(client, session_maker, monkeypatch):
    """Test delete still removes from DB even when GitLab API fails (best effort)."""
    from app.api import mirrors as mod

    class FailingGitLabClient:
        def __init__(self, url: str, encrypted_token: str):
            pass

        def delete_mirror(self, project_id: int, mirror_id: int):
            raise Exception("GitLab API error")

    monkeypatch.setattr(mod, "GitLabClient", FailingGitLabClient)

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    async with session_maker() as s:
        m = Mirror(
            instance_pair_id=pair_id,
            source_project_id=1,
            source_project_path="platform/proj",
            target_project_id=2,
            target_project_path="platform/proj",
            mirror_id=77,
            enabled=True,
            last_update_status="finished",
        )
        s.add(m)
        await s.commit()
        await s.refresh(m)
        mirror_id = m.id

    # Delete should succeed (best effort)
    resp = await client.delete(f"/api/mirrors/{mirror_id}")
    assert resp.status_code == 200

    # Should still be deleted from DB
    async with session_maker() as s:
        row = (await s.execute(select(Mirror).where(Mirror.id == mirror_id))).scalar_one_or_none()
        assert row is None


@pytest.mark.asyncio
async def test_mirrors_trigger_update_gitlab_api_failure(client, session_maker, monkeypatch):
    """Test trigger update error handling when GitLab API fails."""
    from app.api import mirrors as mod

    class FailingGitLabClient:
        def __init__(self, url: str, encrypted_token: str):
            pass

        def trigger_mirror_update(self, project_id: int, mirror_id: int):
            raise Exception("GitLab API error")

    monkeypatch.setattr(mod, "GitLabClient", FailingGitLabClient)

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    async with session_maker() as s:
        m = Mirror(
            instance_pair_id=pair_id,
            source_project_id=1,
            source_project_path="platform/proj",
            target_project_id=2,
            target_project_path="platform/proj",
            mirror_id=77,
            enabled=True,
            last_update_status="finished",
        )
        s.add(m)
        await s.commit()
        await s.refresh(m)
        mirror_id = m.id

    resp = await client.post(f"/api/mirrors/{mirror_id}/update")
    assert resp.status_code in [400, 500]


@pytest.mark.asyncio
async def test_mirrors_trigger_update_for_push_mirror(client, session_maker, monkeypatch):
    """Test triggering update for push mirror uses source project."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.trigger_calls.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="push")

    async with session_maker() as s:
        m = Mirror(
            instance_pair_id=pair_id,
            source_project_id=10,
            source_project_path="platform/proj",
            target_project_id=20,
            target_project_path="platform/proj",
            mirror_id=88,
            # Direction comes from pair (push), not stored on mirror
            enabled=True,
            last_update_status="finished",
        )
        s.add(m)
        await s.commit()
        await s.refresh(m)
        mirror_id = m.id

    resp = await client.post(f"/api/mirrors/{mirror_id}/update")
    assert resp.status_code == 200

    # Push mirror should trigger on source project (direction from pair)
    assert FakeGitLabClient.trigger_calls[-1] == (10, 88)


@pytest.mark.asyncio
async def test_mirrors_list_filtered_by_pair(client, session_maker):
    """Test listing mirrors filtered by instance pair."""
    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair1_id = await seed_pair(session_maker, name="pair1", src_id=src_id, tgt_id=tgt_id, direction="pull")
    pair2_id = await seed_pair(session_maker, name="pair2", src_id=tgt_id, tgt_id=src_id, direction="push")

    async with session_maker() as s:
        # Create mirrors for pair1
        m1 = Mirror(
            instance_pair_id=pair1_id,
            source_project_id=1,
            source_project_path="group/proj1",
            target_project_id=2,
            target_project_path="group/proj1",
            mirror_id=101,
            enabled=True,
            last_update_status="finished",
        )
        m2 = Mirror(
            instance_pair_id=pair1_id,
            source_project_id=3,
            source_project_path="group/proj2",
            target_project_id=4,
            target_project_path="group/proj2",
            mirror_id=102,
            enabled=True,
            last_update_status="pending",
        )
        # Create mirror for pair2
        m3 = Mirror(
            instance_pair_id=pair2_id,
            source_project_id=5,
            source_project_path="group/proj3",
            target_project_id=6,
            target_project_path="group/proj3",
            mirror_id=103,
            enabled=True,
            last_update_status="failed",
        )
        s.add_all([m1, m2, m3])
        await s.commit()

    # Get all mirrors
    resp = await client.get("/api/mirrors")
    assert resp.status_code == 200
    data = resp.json()
    assert data['total'] == 3
    assert len(data['mirrors']) == 3

    # Filter by pair1
    resp = await client.get(f"/api/mirrors?instance_pair_id={pair1_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data['total'] == 2
    pair1_mirrors = data['mirrors']
    assert len(pair1_mirrors) == 2
    assert all(m["instance_pair_id"] == pair1_id for m in pair1_mirrors)

    # Filter by pair2
    resp = await client.get(f"/api/mirrors?instance_pair_id={pair2_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data['total'] == 1
    pair2_mirrors = data['mirrors']
    assert len(pair2_mirrors) == 1
    assert pair2_mirrors[0]["instance_pair_id"] == pair2_id


@pytest.mark.asyncio
async def test_mirrors_preflight_no_existing_mirrors(client, session_maker, monkeypatch):
    """Test preflight when no existing mirrors are present."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.project_mirrors.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    # No existing mirrors
    FakeGitLabClient.project_mirrors[2] = []

    payload = {
        "instance_pair_id": pair_id,
        "source_project_id": 1,
        "source_project_path": "platform/proj",
        "target_project_id": 2,
        "target_project_path": "platform/proj",
    }
    resp = await client.post("/api/mirrors/preflight", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["effective_direction"] == "pull"
    assert body["owner_project_id"] == 2
    assert body["existing_mirrors"] == []
    assert body["existing_same_direction"] == []


@pytest.mark.asyncio
async def test_mirrors_remove_existing_with_specific_ids(client, session_maker, monkeypatch):
    """Test removing specific mirrors by ID."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.delete_calls.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    FakeGitLabClient.project_mirrors[2] = [
        {"id": 11, "mirror_direction": "pull", "url": "https://example.com/a.git"},
        {"id": 12, "mirror_direction": "pull", "url": "https://example.com/b.git"},
        {"id": 13, "mirror_direction": "pull", "url": "https://example.com/c.git"},
    ]

    # Remove only specific mirrors
    payload = {
        "instance_pair_id": pair_id,
        "source_project_id": 1,
        "source_project_path": "platform/proj",
        "target_project_id": 2,
        "target_project_path": "platform/proj",
        "remote_mirror_ids": [11, 13],  # Only these two
    }
    resp = await client.post("/api/mirrors/remove-existing", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] == 2
    assert set(body["deleted_ids"]) == {11, 13}
    # Both should have been deleted
    assert (2, 11) in FakeGitLabClient.delete_calls
    assert (2, 13) in FakeGitLabClient.delete_calls
    assert (2, 12) not in FakeGitLabClient.delete_calls


@pytest.mark.asyncio
async def test_mirrors_preflight_invalid_pair(client):
    """Test preflight with non-existent pair."""
    payload = {
        "instance_pair_id": 9999,
        "source_project_id": 1,
        "source_project_path": "platform/proj",
        "target_project_id": 2,
        "target_project_path": "platform/proj",
    }
    resp = await client.post("/api/mirrors/preflight", json=payload)
    assert resp.status_code == 404
    assert "pair" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_mirrors_remove_existing_invalid_pair(client):
    """Test remove-existing with non-existent pair."""
    payload = {
        "instance_pair_id": 9999,
        "source_project_id": 1,
        "source_project_path": "platform/proj",
        "target_project_id": 2,
        "target_project_path": "platform/proj",
    }
    resp = await client.post("/api/mirrors/remove-existing", json=payload)
    assert resp.status_code == 404
    assert "pair" in resp.json()["detail"].lower()


# =============================================================================
# Mirror Verification Tests (Orphan/Drift Detection)
# =============================================================================


@pytest.mark.asyncio
async def test_verify_mirror_healthy(client, session_maker, monkeypatch):
    """Test verification returns healthy when mirror exists with matching settings."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.inits.clear()
    FakeGitLabClient.project_mirrors.clear()

    src_id = await seed_instance(session_maker, name="src-verify-healthy", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt-verify-healthy", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair-verify-healthy", src_id=src_id, tgt_id=tgt_id, direction="pull")

    async with session_maker() as s:
        m = Mirror(
            instance_pair_id=pair_id,
            source_project_id=1,
            source_project_path="group/project",
            target_project_id=2,
            target_project_path="group/project-mirror",
            mirror_id=100,
            enabled=True,
            only_mirror_protected_branches=False,
            mirror_overwrite_diverged=False,
        )
        s.add(m)
        await s.commit()
        await s.refresh(m)
        mirror_id = m.id

    # Mock GitLab to return a matching mirror
    FakeGitLabClient.project_mirrors[2] = [
        {
            "id": 100,
            "enabled": True,
            "only_protected_branches": False,
            "keep_divergent_refs": True,  # opposite of mirror_overwrite_diverged=False
            "trigger_builds": False,
            "mirror_branch_regex": None,
        }
    ]

    resp = await client.get(f"/api/mirrors/{mirror_id}/verify")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["orphan"] is False
    assert data["drift"] == []
    assert data["gitlab_mirror"] is not None


@pytest.mark.asyncio
async def test_verify_mirror_orphan(client, session_maker, monkeypatch):
    """Test verification detects orphan when mirror is deleted from GitLab."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.inits.clear()
    FakeGitLabClient.project_mirrors.clear()

    src_id = await seed_instance(session_maker, name="src-verify-orphan", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt-verify-orphan", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair-verify-orphan", src_id=src_id, tgt_id=tgt_id, direction="pull")

    async with session_maker() as s:
        m = Mirror(
            instance_pair_id=pair_id,
            source_project_id=1,
            source_project_path="group/project",
            target_project_id=2,
            target_project_path="group/project-mirror",
            mirror_id=200,  # This ID won't exist on GitLab
            enabled=True,
        )
        s.add(m)
        await s.commit()
        await s.refresh(m)
        mirror_id = m.id

    # GitLab returns empty list (mirror was deleted)
    FakeGitLabClient.project_mirrors[2] = []

    resp = await client.get(f"/api/mirrors/{mirror_id}/verify")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "orphan"
    assert data["orphan"] is True
    assert data["gitlab_mirror"] is None


@pytest.mark.asyncio
async def test_verify_mirror_drift(client, session_maker, monkeypatch):
    """Test verification detects drift when settings mismatch."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.inits.clear()
    FakeGitLabClient.project_mirrors.clear()

    src_id = await seed_instance(session_maker, name="src-verify-drift", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt-verify-drift", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair-verify-drift", src_id=src_id, tgt_id=tgt_id, direction="pull")

    async with session_maker() as s:
        m = Mirror(
            instance_pair_id=pair_id,
            source_project_id=1,
            source_project_path="group/project",
            target_project_id=2,
            target_project_path="group/project-mirror",
            mirror_id=300,
            enabled=True,  # We expect enabled=True
            only_mirror_protected_branches=True,  # We expect True
        )
        s.add(m)
        await s.commit()
        await s.refresh(m)
        mirror_id = m.id

    # GitLab has different settings
    FakeGitLabClient.project_mirrors[2] = [
        {
            "id": 300,
            "enabled": False,  # Drifted from True
            "only_protected_branches": False,  # Drifted from True
            "keep_divergent_refs": True,
            "trigger_builds": False,
            "mirror_branch_regex": None,
        }
    ]

    resp = await client.get(f"/api/mirrors/{mirror_id}/verify")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "drift"
    assert data["orphan"] is False
    assert len(data["drift"]) >= 2

    # Check that enabled and only_protected_branches are in the drift list
    drift_fields = [d["field"] for d in data["drift"]]
    assert "enabled" in drift_fields
    assert "only_protected_branches" in drift_fields


@pytest.mark.asyncio
async def test_verify_mirror_not_created(client, session_maker, monkeypatch):
    """Test verification returns not_created when mirror has no mirror_id."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.inits.clear()
    FakeGitLabClient.project_mirrors.clear()

    src_id = await seed_instance(session_maker, name="src-verify-nc", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt-verify-nc", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair-verify-nc", src_id=src_id, tgt_id=tgt_id, direction="pull")

    async with session_maker() as s:
        m = Mirror(
            instance_pair_id=pair_id,
            source_project_id=1,
            source_project_path="group/project",
            target_project_id=2,
            target_project_path="group/project-mirror",
            mirror_id=None,  # Not created on GitLab yet
            enabled=True,
        )
        s.add(m)
        await s.commit()
        await s.refresh(m)
        mirror_id = m.id

    resp = await client.get(f"/api/mirrors/{mirror_id}/verify")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "not_created"
    assert data["orphan"] is False


@pytest.mark.asyncio
async def test_verify_mirror_not_found(client):
    """Test verification returns 404 for non-existent mirror."""
    resp = await client.get("/api/mirrors/99999/verify")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_verify_mirrors_batch(client, session_maker, monkeypatch):
    """Test batch verification of multiple mirrors."""
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.inits.clear()
    FakeGitLabClient.project_mirrors.clear()

    src_id = await seed_instance(session_maker, name="src-verify-batch", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt-verify-batch", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair-verify-batch", src_id=src_id, tgt_id=tgt_id, direction="pull")

    mirror_ids = []
    async with session_maker() as s:
        # Create a healthy mirror
        m1 = Mirror(
            instance_pair_id=pair_id,
            source_project_id=1,
            source_project_path="group/project1",
            target_project_id=10,
            target_project_path="group/project1-mirror",
            mirror_id=401,
            enabled=True,
        )
        s.add(m1)

        # Create an orphan mirror
        m2 = Mirror(
            instance_pair_id=pair_id,
            source_project_id=2,
            source_project_path="group/project2",
            target_project_id=20,
            target_project_path="group/project2-mirror",
            mirror_id=402,
            enabled=True,
        )
        s.add(m2)

        await s.commit()
        await s.refresh(m1)
        await s.refresh(m2)
        mirror_ids = [m1.id, m2.id]

    # Set up GitLab mock - only m1's mirror exists
    FakeGitLabClient.project_mirrors[10] = [
        {
            "id": 401,
            "enabled": True,
            "only_protected_branches": False,
            "keep_divergent_refs": True,
            "trigger_builds": False,
            "mirror_branch_regex": None,
        }
    ]
    FakeGitLabClient.project_mirrors[20] = []  # m2's mirror is gone (orphan)

    resp = await client.post("/api/mirrors/verify", json={"mirror_ids": mirror_ids})
    assert resp.status_code == 200
    results = resp.json()
    assert len(results) == 2

    # Find each result by mirror_id
    result_by_id = {r["mirror_id"]: r for r in results}
    assert result_by_id[mirror_ids[0]]["status"] == "healthy"
    assert result_by_id[mirror_ids[1]]["status"] == "orphan"


@pytest.mark.asyncio
async def test_verify_mirrors_batch_empty(client):
    """Test batch verification with empty list returns empty result."""
    resp = await client.post("/api/mirrors/verify", json={"mirror_ids": []})
    assert resp.status_code == 200
    assert resp.json() == []

