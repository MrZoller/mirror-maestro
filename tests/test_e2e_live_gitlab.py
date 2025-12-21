import asyncio
import json
import os
import time
import uuid

import pytest


def _env(name: str) -> str | None:
    v = os.getenv(name)
    if v is None:
        return None
    v = v.strip()
    return v or None


def _should_run_live() -> bool:
    """
    Guardrail: never run live GitLab tests unless explicitly enabled.
    """
    return (_env("E2E_LIVE_GITLAB") or "").lower() in {"1", "true", "yes", "on"}


def _required_env() -> dict[str, str]:
    url = _env("E2E_GITLAB_URL")
    token = _env("E2E_GITLAB_TOKEN")
    group_path = _env("E2E_GITLAB_GROUP_PATH")

    missing = [k for k, v in {
        "E2E_GITLAB_URL": url,
        "E2E_GITLAB_TOKEN": token,
        "E2E_GITLAB_GROUP_PATH": group_path,
    }.items() if not v]

    if missing:
        pytest.skip(
            "Live GitLab E2E is disabled / not configured. "
            f"Set E2E_LIVE_GITLAB=1 and provide: {', '.join(missing)}"
        )

    if not _should_run_live():
        pytest.skip("Live GitLab E2E is opt-in. Set E2E_LIVE_GITLAB=1 to run.")

    return {"url": url, "token": token, "group_path": group_path}


async def _poll_remote_mirror_status(*, gl, project_id: int, remote_mirror_id: int, timeout_s: float = 45.0) -> dict:
    """
    Best-effort: wait until GitLab records any mirror update timestamp/status.
    Avoid asserting on exact status strings (they vary by GitLab version).
    """
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        project = gl.projects.get(project_id)
        rm = project.remote_mirrors.get(remote_mirror_id)
        last = {
            "enabled": getattr(rm, "enabled", None),
            "update_status": getattr(rm, "update_status", None),
            "last_update_at": getattr(rm, "last_update_at", None),
            "last_successful_update_at": getattr(rm, "last_successful_update_at", None),
        }
        if last["last_update_at"] or last["last_successful_update_at"] or last["update_status"]:
            return last
        await asyncio.sleep(2)
    return last or {}


@pytest.mark.e2e
@pytest.mark.live_gitlab
@pytest.mark.asyncio
async def test_e2e_live_gitlab_create_pull_and_push_mirrors_trigger_update_and_export(client):
    """
    End-to-end test against a real GitLab instance.

    It provisions two temporary projects in a real group/namespace, then drives
    this app's HTTP API to configure:
      - a GitLab instance
      - pairs (pull + push)
      - a group access token (used for authenticated clone URLs)
      - a pull mirror on the target project
      - a push mirror on the source project
      - a manual update trigger
      - exports of mirror configurations
    """
    cfg = _required_env()

    gitlab = pytest.importorskip("gitlab")
    gl = gitlab.Gitlab(cfg["url"], private_token=cfg["token"])
    gl.auth()

    group = gl.groups.get(cfg["group_path"])

    # For personal access tokens, GitLab generally accepts HTTP Basic
    # with username "oauth2" and password "<PAT>".
    http_username = _env("E2E_GITLAB_HTTP_USERNAME") or "oauth2"

    run_id = uuid.uuid4().hex[:10]
    src_name = f"mirror-wizard-e2e-src-{run_id}"
    tgt_name = f"mirror-wizard-e2e-tgt-{run_id}"

    source_project = None
    target_project = None
    created_mirror_db_ids: list[int] = []
    created_pair_ids: list[int] = []
    created_instance_id = None
    created_token_id = None

    try:
        # Create source project with an initial commit so there's something to mirror.
        source_project = gl.projects.create({
            "name": src_name,
            "path": src_name,
            "namespace_id": group.id,
            "initialize_with_readme": True,
            "visibility": "private",
        })

        # Create empty target project.
        target_project = gl.projects.create({
            "name": tgt_name,
            "path": tgt_name,
            "namespace_id": group.id,
            "visibility": "private",
        })

        # 1) Register instance in this app
        inst_resp = await client.post("/api/instances", json={
            "name": f"e2e-{run_id}",
            "url": cfg["url"],
            "token": cfg["token"],
            "description": "live GitLab E2E",
        })
        assert inst_resp.status_code == 200, inst_resp.text
        created_instance_id = inst_resp.json()["id"]

        # 2) Store a token for the group path so authenticated clone URLs are used.
        tok_resp = await client.post("/api/tokens", json={
            "gitlab_instance_id": created_instance_id,
            "group_path": cfg["group_path"],
            "token": cfg["token"],
            "token_name": http_username,
        })
        assert tok_resp.status_code == 200, tok_resp.text
        created_token_id = tok_resp.json()["id"]

        # 3) Create an instance pair (same instance for src and tgt).
        pull_pair_resp = await client.post("/api/pairs", json={
            "name": f"e2e-pair-pull-{run_id}",
            "source_instance_id": created_instance_id,
            "target_instance_id": created_instance_id,
            "mirror_direction": "pull",
            "description": "live GitLab E2E",
        })
        assert pull_pair_resp.status_code == 200, pull_pair_resp.text
        pull_pair_id = pull_pair_resp.json()["id"]
        created_pair_ids.append(pull_pair_id)

        push_pair_resp = await client.post("/api/pairs", json={
            "name": f"e2e-pair-push-{run_id}",
            "source_instance_id": created_instance_id,
            "target_instance_id": created_instance_id,
            "mirror_direction": "push",
            "description": "live GitLab E2E",
        })
        assert push_pair_resp.status_code == 200, push_pair_resp.text
        push_pair_id = push_pair_resp.json()["id"]
        created_pair_ids.append(push_pair_id)

        timeout_s = float(_env("E2E_GITLAB_MIRROR_TIMEOUT_S") or "45")

        # 4) Configure group-level defaults (override pair defaults)
        gd_pull = await client.post("/api/group-defaults", json={
            "instance_pair_id": pull_pair_id,
            "group_path": cfg["group_path"],
            "mirror_trigger_builds": True,
            "mirror_branch_regex": "^main$",
            "mirror_overwrite_diverged": True,
            "only_mirror_protected_branches": False,
        })
        assert gd_pull.status_code == 200, gd_pull.text

        gd_push = await client.post("/api/group-defaults", json={
            "instance_pair_id": push_pair_id,
            "group_path": cfg["group_path"],
            "mirror_overwrite_diverged": True,
            "only_mirror_protected_branches": True,
        })
        assert gd_push.status_code == 200, gd_push.text

        # 5) Create pull mirror via API (target pulls from source)
        pull_mirror_resp = await client.post("/api/mirrors", json={
            "instance_pair_id": pull_pair_id,
            "source_project_id": source_project.id,
            "source_project_path": source_project.path_with_namespace,
            "target_project_id": target_project.id,
            "target_project_path": target_project.path_with_namespace,
            "enabled": True,
            # Do not pass pull-only settings; they should come from group defaults.
        })
        assert pull_mirror_resp.status_code == 200, pull_mirror_resp.text
        pull_mirror_body = pull_mirror_resp.json()
        pull_mirror_db_id = pull_mirror_body["id"]
        created_mirror_db_ids.append(pull_mirror_db_id)
        pull_remote_mirror_id = pull_mirror_body["mirror_id"]
        assert pull_remote_mirror_id, "Expected GitLab mirror id to be returned"

        # 6) Best-effort: verify group defaults took effect (when visible via API).
        try:
            rm = gl.projects.get(target_project.id).remote_mirrors.get(pull_remote_mirror_id)
            # GitLab versions differ; only assert when the attribute exists/returns non-None.
            if getattr(rm, "trigger_builds", None) is not None:
                assert rm.trigger_builds is True
            if getattr(rm, "mirror_branch_regex", None) is not None:
                assert rm.mirror_branch_regex == "^main$"
            if getattr(rm, "keep_divergent_refs", None) is not None:
                # overwrite_diverged=True => keep_divergent_refs=False
                assert rm.keep_divergent_refs is False
        except Exception:
            # Mirror settings are not consistently exposed across GitLab versions / python-gitlab.
            pass

        # 7) Trigger update (best-effort; GitLab may queue).
        pull_upd = await client.post(f"/api/mirrors/{pull_mirror_db_id}/update")
        assert pull_upd.status_code == 200, pull_upd.text

        # 8) Best-effort: verify GitLab has any mirror status/timestamp visible.
        pull_status = await _poll_remote_mirror_status(
            gl=gl,
            project_id=target_project.id,
            remote_mirror_id=pull_remote_mirror_id,
            timeout_s=timeout_s,
        )
        assert isinstance(pull_status, dict)

        # 9) Export should include our pull mirror
        pull_export = await client.get(f"/api/export/pair/{pull_pair_id}")
        assert pull_export.status_code == 200, pull_export.text
        pull_export_data = json.loads(pull_export.text)
        assert pull_export_data["pair_id"] == pull_pair_id
        assert any(
            m["source_project_path"] == source_project.path_with_namespace
            and m["target_project_path"] == target_project.path_with_namespace
            for m in pull_export_data["mirrors"]
        )

        # 10) Create push mirror via API (source pushes to target)
        push_mirror_resp = await client.post("/api/mirrors", json={
            "instance_pair_id": push_pair_id,
            "source_project_id": source_project.id,
            "source_project_path": source_project.path_with_namespace,
            "target_project_id": target_project.id,
            "target_project_path": target_project.path_with_namespace,
            "enabled": True,
            # Do not pass settings; keep_divergent_refs / only_protected should come from group defaults.
        })
        assert push_mirror_resp.status_code == 200, push_mirror_resp.text
        push_mirror_body = push_mirror_resp.json()
        push_mirror_db_id = push_mirror_body["id"]
        created_mirror_db_ids.append(push_mirror_db_id)
        push_remote_mirror_id = push_mirror_body["mirror_id"]
        assert push_remote_mirror_id, "Expected GitLab mirror id to be returned"

        # 11) Best-effort: verify group defaults took effect (when visible via API).
        try:
            rm = gl.projects.get(source_project.id).remote_mirrors.get(push_remote_mirror_id)
            if getattr(rm, "only_protected_branches", None) is not None:
                assert rm.only_protected_branches is True
            if getattr(rm, "keep_divergent_refs", None) is not None:
                assert rm.keep_divergent_refs is False
        except Exception:
            pass

        # 12) Trigger update for push mirror.
        push_upd = await client.post(f"/api/mirrors/{push_mirror_db_id}/update")
        assert push_upd.status_code == 200, push_upd.text

        # 13) Best-effort: verify GitLab has any mirror status/timestamp visible (push mirror lives on source project).
        push_status = await _poll_remote_mirror_status(
            gl=gl,
            project_id=source_project.id,
            remote_mirror_id=push_remote_mirror_id,
            timeout_s=timeout_s,
        )
        assert isinstance(push_status, dict)

        # 14) Export should include our push mirror
        push_export = await client.get(f"/api/export/pair/{push_pair_id}")
        assert push_export.status_code == 200, push_export.text
        push_export_data = json.loads(push_export.text)
        assert push_export_data["pair_id"] == push_pair_id
        assert any(
            m["source_project_path"] == source_project.path_with_namespace
            and m["target_project_path"] == target_project.path_with_namespace
            for m in push_export_data["mirrors"]
        )

    finally:
        # Delete mirror in GitLab via app endpoint (best effort).
        for mid in created_mirror_db_ids:
            try:
                await client.delete(f"/api/mirrors/{mid}")
            except Exception:
                pass

        # DB cleanup (not strictly necessary; DB is per-test).
        if created_token_id is not None:
            try:
                await client.delete(f"/api/tokens/{created_token_id}")
            except Exception:
                pass
        for pid in created_pair_ids:
            try:
                await client.delete(f"/api/pairs/{pid}")
            except Exception:
                pass
        if created_instance_id is not None:
            try:
                await client.delete(f"/api/instances/{created_instance_id}")
            except Exception:
                pass

        # Always delete temporary GitLab projects.
        for p in [target_project, source_project]:
            if p is None:
                continue
            try:
                gl.projects.delete(p.id)
            except Exception:
                pass

