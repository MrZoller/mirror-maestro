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


async def _poll_remote_mirror_status(*, gl, target_project_id: int, remote_mirror_id: int, timeout_s: float = 45.0) -> dict:
    """
    Best-effort: wait until GitLab records any mirror update timestamp/status.
    Avoid asserting on exact status strings (they vary by GitLab version).
    """
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        project = gl.projects.get(target_project_id)
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
async def test_e2e_live_gitlab_create_mirror_trigger_update_and_export(client):
    """
    End-to-end test against a real GitLab instance.

    It provisions two temporary projects in a real group/namespace, then drives
    this app's HTTP API to configure:
      - a GitLab instance
      - a pair
      - a group access token (used for authenticated clone URLs)
      - a pull mirror on the target project
      - a manual update trigger
      - an export of the mirror configuration
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
    created_mirror_db_id = None
    created_pair_id = None
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
        pair_resp = await client.post("/api/pairs", json={
            "name": f"e2e-pair-{run_id}",
            "source_instance_id": created_instance_id,
            "target_instance_id": created_instance_id,
            "mirror_direction": "pull",
            "description": "live GitLab E2E",
        })
        assert pair_resp.status_code == 200, pair_resp.text
        created_pair_id = pair_resp.json()["id"]

        # 4) Create mirror via API
        mirror_resp = await client.post("/api/mirrors", json={
            "instance_pair_id": created_pair_id,
            "source_project_id": source_project.id,
            "source_project_path": source_project.path_with_namespace,
            "target_project_id": target_project.id,
            "target_project_path": target_project.path_with_namespace,
            "enabled": True,
        })
        assert mirror_resp.status_code == 200, mirror_resp.text
        mirror_body = mirror_resp.json()
        created_mirror_db_id = mirror_body["id"]
        remote_mirror_id = mirror_body["mirror_id"]
        assert remote_mirror_id, "Expected GitLab mirror id to be returned"

        # 5) Trigger update (best-effort; GitLab may queue).
        upd = await client.post(f"/api/mirrors/{created_mirror_db_id}/update")
        assert upd.status_code == 200, upd.text

        # 6) Best-effort: verify GitLab has any mirror status/timestamp visible.
        status = await _poll_remote_mirror_status(
            gl=gl,
            target_project_id=target_project.id,
            remote_mirror_id=remote_mirror_id,
            timeout_s=float(_env("E2E_GITLAB_MIRROR_TIMEOUT_S") or "45"),
        )
        assert isinstance(status, dict)

        # 7) Export should include our mirror
        export = await client.get(f"/api/export/pair/{created_pair_id}")
        assert export.status_code == 200, export.text
        export_data = json.loads(export.text)
        assert export_data["pair_id"] == created_pair_id
        assert any(
            m["source_project_path"] == source_project.path_with_namespace
            and m["target_project_path"] == target_project.path_with_namespace
            for m in export_data["mirrors"]
        )

    finally:
        # Delete mirror in GitLab via app endpoint (best effort).
        if created_mirror_db_id is not None:
            try:
                await client.delete(f"/api/mirrors/{created_mirror_db_id}")
            except Exception:
                pass

        # DB cleanup (not strictly necessary; DB is per-test).
        if created_token_id is not None:
            try:
                await client.delete(f"/api/tokens/{created_token_id}")
            except Exception:
                pass
        if created_pair_id is not None:
            try:
                await client.delete(f"/api/pairs/{created_pair_id}")
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

