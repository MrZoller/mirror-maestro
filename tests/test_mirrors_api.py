import pytest
from sqlalchemy import select

from app.models import GitLabInstance, InstancePair, GroupAccessToken, Mirror
from app.models import GroupMirrorDefaults


class FakeGitLabClient:
    inits = []
    pull_calls = []
    push_calls = []
    trigger_calls = []
    delete_calls = []
    update_calls = []

    def __init__(self, url: str, encrypted_token: str):
        self.url = url
        self.encrypted_token = encrypted_token
        self.__class__.inits.append((url, encrypted_token))

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


async def seed_group_token(session_maker, *, instance_id: int, group_path: str, token="tok", token_name="bot") -> int:
    async with session_maker() as s:
        t = GroupAccessToken(
            gitlab_instance_id=instance_id,
            group_path=group_path,
            encrypted_token=f"enc:{token}",
            token_name=token_name,
        )
        s.add(t)
        await s.commit()
        await s.refresh(t)
        return t.id


async def seed_group_defaults(
    session_maker,
    *,
    pair_id: int,
    group_path: str,
    mirror_direction: str | None = None,
    mirror_overwrite_diverged: bool | None = None,
    mirror_trigger_builds: bool | None = None,
    only_mirror_protected_branches: bool | None = None,
    mirror_branch_regex: str | None = None,
    mirror_user_id: int | None = None,
) -> int:
    async with session_maker() as s:
        d = GroupMirrorDefaults(
            instance_pair_id=pair_id,
            group_path=group_path,
            mirror_direction=mirror_direction,
            mirror_overwrite_diverged=mirror_overwrite_diverged,
            mirror_trigger_builds=mirror_trigger_builds,
            only_mirror_protected_branches=only_mirror_protected_branches,
            mirror_branch_regex=mirror_branch_regex,
            mirror_user_id=mirror_user_id,
        )
        s.add(d)
        await s.commit()
        await s.refresh(d)
        return d.id


@pytest.mark.asyncio
async def test_get_authenticated_url_prefers_most_specific_group(client, session_maker):
    from app.api.mirrors import get_authenticated_url

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    await seed_group_token(session_maker, instance_id=src_id, group_path="platform", token="tok1", token_name="bot1")
    await seed_group_token(session_maker, instance_id=src_id, group_path="platform/core", token="tok2", token_name="bot2")

    async with session_maker() as s:
        src = (await s.execute(select(GitLabInstance).where(GitLabInstance.id == src_id))).scalar_one()
        url = await get_authenticated_url(s, src, "platform/core/api-gateway")

    assert url.startswith("https://")
    assert "bot2:tok2@" in url
    assert url.endswith("/platform/core/api-gateway.git")


@pytest.mark.asyncio
async def test_mirrors_create_pull_uses_source_authenticated_url(client, session_maker, monkeypatch):
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.inits.clear()
    FakeGitLabClient.pull_calls.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    # Source token is used to build authenticated source URL for pull mirrors.
    await seed_group_token(session_maker, instance_id=src_id, group_path="platform/core", token="s", token_name="bot")

    payload = {
        "instance_pair_id": pair_id,
        "source_project_id": 1,
        "source_project_path": "platform/core/api",
        "target_project_id": 2,
        "target_project_path": "platform/core/api",
        "enabled": True,
    }
    resp = await client.post("/api/mirrors", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["mirror_id"] == 77

    # Pull mirror: client should be created for target instance, and mirror URL should include source token.
    assert FakeGitLabClient.inits[-1][0] == "https://tgt.example.com"
    assert FakeGitLabClient.pull_calls[-1][0] == 2
    assert "bot:s@" in FakeGitLabClient.pull_calls[-1][1]


@pytest.mark.asyncio
async def test_mirrors_create_push_uses_target_authenticated_url(client, session_maker, monkeypatch):
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.inits.clear()
    FakeGitLabClient.push_calls.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="push")

    # Target token is used to build authenticated target URL for push mirrors.
    await seed_group_token(session_maker, instance_id=tgt_id, group_path="platform", token="t", token_name="bot")

    payload = {
        "instance_pair_id": pair_id,
        "source_project_id": 10,
        "source_project_path": "platform/proj",
        "target_project_id": 20,
        "target_project_path": "platform/proj",
        "enabled": True,
    }
    resp = await client.post("/api/mirrors", json=payload)
    assert resp.status_code == 200
    assert resp.json()["mirror_id"] == 88

    assert FakeGitLabClient.inits[-1][0] == "https://src.example.com"
    assert FakeGitLabClient.push_calls[-1][0] == 10
    assert "bot:t@" in FakeGitLabClient.push_calls[-1][1]


@pytest.mark.asyncio
async def test_mirrors_trigger_update_updates_status(client, session_maker, monkeypatch):
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.trigger_calls.clear()

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
        pair.mirror_user_id = 42

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
        42,
        "pull",
    )


@pytest.mark.asyncio
async def test_mirrors_create_pull_uses_group_defaults_over_pair(client, session_maker, monkeypatch):
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.inits.clear()
    FakeGitLabClient.pull_calls.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    # Pair defaults (intentionally opposite of group defaults)
    async with session_maker() as s:
        pair = (await s.execute(select(InstancePair).where(InstancePair.id == pair_id))).scalar_one()
        pair.mirror_overwrite_diverged = False
        pair.only_mirror_protected_branches = False
        pair.mirror_trigger_builds = False
        pair.mirror_branch_regex = None
        pair.mirror_user_id = None
        await s.commit()

    await seed_group_defaults(
        session_maker,
        pair_id=pair_id,
        group_path="platform/core",
        mirror_overwrite_diverged=True,
        only_mirror_protected_branches=True,
        mirror_trigger_builds=True,
        mirror_branch_regex="^main$",
        mirror_user_id=7,
    )

    payload = {
        "instance_pair_id": pair_id,
        "source_project_id": 1,
        "source_project_path": "platform/core/api",
        "target_project_id": 2,
        "target_project_path": "platform/core/api",
        "enabled": True,
    }
    resp = await client.post("/api/mirrors", json=payload)
    assert resp.status_code == 200, resp.text

    # create_pull_mirror(project_id, mirror_url, enabled, only_protected, keep_divergent, trigger, regex, user_id)
    _project_id, _url, enabled, only_protected, keep_divergent, trigger, regex, user_id = FakeGitLabClient.pull_calls[-1]
    assert enabled is True
    assert only_protected is True
    assert keep_divergent is False  # overwrite_diverged=True => keep_divergent_refs=False
    assert trigger is True
    assert regex == "^main$"
    assert user_id == 7


@pytest.mark.asyncio
async def test_mirrors_create_pull_defaults_mirror_user_to_token_user(client, session_maker, monkeypatch):
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.pull_calls.clear()

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
async def test_mirrors_create_pull_mirror_override_wins_over_group_defaults(client, session_maker, monkeypatch):
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.pull_calls.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    await seed_group_defaults(
        session_maker,
        pair_id=pair_id,
        group_path="platform",
        mirror_overwrite_diverged=True,
    )

    payload = {
        "instance_pair_id": pair_id,
        "source_project_id": 1,
        "source_project_path": "platform/proj",
        "target_project_id": 2,
        "target_project_path": "platform/proj",
        "enabled": True,
        "mirror_overwrite_diverged": False,  # explicit mirror override should win
    }
    resp = await client.post("/api/mirrors", json=payload)
    assert resp.status_code == 200, resp.text

    _project_id, _url, _enabled, _only_protected, keep_divergent, *_rest = FakeGitLabClient.pull_calls[-1]
    assert keep_divergent is True  # overwrite_diverged=False => keep_divergent_refs=True


@pytest.mark.asyncio
async def test_mirrors_update_uses_group_defaults_over_pair(client, session_maker, monkeypatch):
    from app.api import mirrors as mod

    monkeypatch.setattr(mod, "GitLabClient", FakeGitLabClient)
    FakeGitLabClient.update_calls.clear()

    src_id = await seed_instance(session_maker, name="src", url="https://src.example.com")
    tgt_id = await seed_instance(session_maker, name="tgt", url="https://tgt.example.com")
    pair_id = await seed_pair(session_maker, name="pair", src_id=src_id, tgt_id=tgt_id, direction="pull")

    async with session_maker() as s:
        pair = (await s.execute(select(InstancePair).where(InstancePair.id == pair_id))).scalar_one()
        pair.mirror_overwrite_diverged = True
        pair.only_mirror_protected_branches = True
        pair.mirror_trigger_builds = True
        pair.mirror_branch_regex = "^main$"
        pair.mirror_user_id = 42

        m = Mirror(
            instance_pair_id=pair_id,
            source_project_id=1,
            source_project_path="platform/core/proj",
            target_project_id=2,
            target_project_path="platform/core/proj",
            mirror_id=77,
            enabled=True,
            last_update_status="pending",
            mirror_direction="pull",
        )
        s.add(m)
        await s.commit()
        await s.refresh(m)
        db_mirror_id = m.id

    # Override just a subset at the group level
    await seed_group_defaults(
        session_maker,
        pair_id=pair_id,
        group_path="platform/core",
        mirror_overwrite_diverged=False,
        only_mirror_protected_branches=False,
        mirror_trigger_builds=False,
    )

    resp = await client.put(f"/api/mirrors/{db_mirror_id}", json={"enabled": False})
    assert resp.status_code == 200, resp.text

    assert FakeGitLabClient.update_calls[-1] == (
        2,
        77,
        False,
        False,  # group default
        True,   # keep_divergent_refs (overwrite_diverged=False)
        False,  # group default
        "^main$",  # pair default (not overridden)
        42,        # pair default (not overridden)
        "pull",
    )


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
            mirror_direction="pull",
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

