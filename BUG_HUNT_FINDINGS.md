# Mirror Maestro — Bug Hunt Findings

Ready-to-file GitHub issues. Each entry maps to one issue: use the **Title** as the
issue title, the body below it as the issue body, and the suggested labels.

Findings were produced by three parallel reviews (API layer, core logic, frontend) and
the highest-severity ones were re-verified against the code by hand. Line numbers are
approximate and may drift.

---

## CRITICAL

### 1. Backup restore fails for any real dataset (missing datetime parsing for `mirrors.last_update_at` / `status_checked_at`)
**Labels:** bug, critical, backup

`app/api/backup.py:246-252` parses only `['created_at', 'updated_at', 'last_successful_update', 'mirror_token_expires_at']`
back into `datetime` for the `mirrors` table, but `_model_to_dict()` exports **every**
column, so `database.json` also contains `last_update_at` and `status_checked_at` as ISO
strings. `Mirror(**row)` is then constructed with those two fields still `str`. asyncpg
rejects a `str` bound to a `TIMESTAMP` column, so the flush/commit raises and the whole
restore returns 500.

**Repro:** any mirror that has ever synced has `last_update_at`/`status_checked_at` set
(`_refresh_mirror_status` sets both). Create a backup → restore it → guaranteed 500
"Failed to restore database". The restore feature only works on trivially fresh installs.
Tests pass because they run on SQLite fixtures that don't populate these fields.

**Fix:** add `last_update_at` and `status_checked_at` to the mirrors datetime-parse list
(ideally parse datetimes generically per-column instead of a hardcoded list).

---

### 2. All GitLab / bcrypt / file I/O runs synchronously on the asyncio event loop
**Labels:** bug, critical, performance, availability

There is no `run_in_executor`/`asyncio.to_thread` anywhere in `app/` (verified by grep).
Every `GitLabClient` method is a synchronous python-gitlab/`requests` call (timeout up to
`gitlab_api_timeout`, default 60s) and is invoked directly inside `async` handlers via
`RateLimiter.execute_with_retry` (`app/core/rate_limiter.py:103`),
`MirrorGitLabService.execute` (`app/core/mirror_gitlab_service.py:144`), and
`IssueSyncEngine._execute_gitlab_api_call` (`app/core/issue_sync.py:573-611`). bcrypt
(`app/core/auth.py:55-68`, cost 12, ~200-300ms) and tarfile/JSON work in `backup.py` are
likewise inline.

**Impact:** one slow/hung GitLab response freezes the entire single-worker event loop —
no other request is served, including `/api/health/quick`. The Docker/K8s liveness probe
(2s timeout) then fails and the container is killed mid-operation. Batch endpoints and
issue syncs make the UI hang for the duration.

**Fix:** wrap all synchronous GitLab/bcrypt/tar calls in `asyncio.to_thread(...)` (or run
the client in a thread pool).

---

### 3. Incremental sync watermark advances on failure and mid-window → issues silently skipped forever
**Labels:** bug, critical, issue-sync, data-loss

Incremental sync fetches with `updated_after = config.last_sync_at`, but:
- **On failure** (`app/core/issue_scheduler.py:441`): when `engine.sync()` raises (circuit
  open, GitLab down, network error) the handler still sets
  `config.last_sync_at = datetime.utcnow()`. The next run only fetches issues updated
  *after the failure*, so everything in the failed window is never retried.
- **On success**: the per-batch checkpoint (`app/core/issue_sync.py:707-716`) sets
  `last_sync_at = utcnow()` (batch-completion time), not the time the issue list was
  fetched. Any issue changed between the initial fetch and the final checkpoint falls
  below the new watermark and is never synced.

**Repro:** GitLab briefly unreachable at 09:00 → sync fails → `last_sync_at = 09:00`. All
issues updated between the previous success and 09:00 are dropped permanently, while
`last_sync_status="failed"` is recorded but nothing re-fetches that window.

**Fix:** only advance `last_sync_at` on success, and set it to the fetch-start timestamp
captured before listing issues (not batch-completion time).

---

## HIGH

### 4. Backup create/restore is not admin-gated (encryption-key exfiltration + privilege escalation)
**Labels:** bug, high, security, backup

`create_backup` (`app/api/backup.py:373`) and `restore_backup` (`:538`) use
`Depends(verify_credentials)` only, while `users.py` correctly uses `require_admin`. In
multi-user mode any active non-admin user can:
1. `GET /api/backup/create` → download the Fernet key (`data/encryption.key`) + every
   encrypted GitLab token = plaintext access to all instance/mirror tokens.
2. `POST /api/backup/restore` → wipe and replace the whole DB **including the `users`
   table** (restore arbitrary users with `is_admin=True`) and replace the encryption key.

**Fix:** gate both endpoints behind `require_admin` (they are already admin-only in
spirit).

---

### 5. Change-detection hash covers only title+description — state/label/assignee/weight/time changes never propagate
**Labels:** bug, high, issue-sync

`_sync_issue` (`app/core/issue_sync.py:806-822`) computes `hash(title|||description)` and
returns "skipped" when it matches the stored hash. All state (close/reopen), label, PM
field, weight, and time-tracking sync live inside `_update_target_issue`, which is only
reached when the hash *differs*.

**Repro:** close source issue #42 with no text edit → `updated_at` changes so it's
fetched → hash unchanged → skipped → target issue stays open forever. Same for adding a
label, reassigning, or logging time.

**Fix:** include state/labels/assignees/weight/time (or a broader field set) in the
content hash, or evaluate metadata sync independently of the text hash.

---

### 6. Comment sync silently drops everything past the first 100 notes
**Labels:** bug, high, issue-sync, data-loss

`GitLabClient.get_issue_notes` (`app/core/gitlab_client.py:1864-1906`) fetches exactly one
page (no `get_all`/pagination loop). `_sync_comments` (`app/core/issue_sync.py:1208-1213`)
calls it with only `project_id, issue_iid` → page 1, 100 notes. Issues with >100 comments
lose all later comments with no warning, and new comments on such issues are never seen.

**Fix:** paginate `get_issue_notes` until exhausted (loop over pages / use `get_all`).

---

### 7. `PUT /api/pairs/{id}` allows flipping `mirror_direction` while the pair already has mirrors
**Labels:** bug, high, data-integrity

The safety lock at `app/api/pairs.py:420-431` covers only `source_instance_id`/
`target_instance_id`; `mirror_direction` is applied unconditionally (`:479-480`). Direction
is pair-only and every downstream op derives which project/instance owns the GitLab config
and token from it. After flipping push→pull: `_cleanup_mirror_from_gitlab` deletes the
wrong (pull) mirror and orphans the real push mirror + its access token;
`trigger_mirror_update`/`update_mirror`/`_refresh_mirror_status`/`rotate_mirror_token` all
address the wrong project.

**Repro:** pair with 50 push mirrors → edit pair, toggle to "pull" (nothing blocks it) →
every mirror's status reads from `get_pull_mirror(target)` (reports orphan), and deleting
a mirror leaves the real push mirror + write token live on GitLab forever.

**Fix:** reject a `mirror_direction` change when mirrors exist for the pair, mirroring the
existing instance-change guard.

---

### 8. `rotate-token` revokes the old token before creating the new one, and reports success on partial failure
**Labels:** bug, high, tokens

In `app/api/mirrors.py` (~2226-2345) the old project access token is deleted **before**
`create_project_access_token` runs. If create then fails (429 after retries, 503
circuit-open, permission change) the mirror's only credential is already revoked while the
DB still shows the old token as "active". Separately, if the new token is created but the
subsequent `update_mirror`/`update_pull_mirror` fails, the error is only logged and the
endpoint still commits and returns `{"status": "rotated"}` — GitLab is left configured with
the just-revoked old credential.

**Fix:** create the new token first, reconfigure the mirror, verify success, then delete
the old token; propagate the mirror-update failure instead of swallowing it.

---

### 9. Mirrors "Sort by: Status" sends an `order_by` value the backend rejects (422 breaks the table)
**Labels:** bug, high, frontend

`app/templates/index.html:507` offers `<option value="last_update_status">Status</option>`,
but `app/api/mirrors.py:540` validates
`order_by ... pattern="^(created_at|updated_at|source_project_path|target_project_path)$"`
— `last_update_status` is excluded (though the docstring claims it's allowed). Picking
"Status" → `GET /api/mirrors?...&order_by=last_update_status` → 422 → `loadMirrors()`
error path replaces the table with an error state, and since `orderBy` persists in state
every refresh/CRUD reload keeps failing until another sort is chosen.

**Fix:** add `last_update_status` to the backend regex (and any other sortable columns), or
remove the option.

---

### 10. Cascade-delete warnings never work — paginated `/api/mirrors` response treated as an array
**Labels:** bug, high, frontend, safety

`GET /api/mirrors` returns `{mirrors, total, page, ...}` (`MirrorListResponse`), but
`deleteInstance` (`app/static/js/app.js:1631-1632`) does `(allMirrors || []).filter(...)`
(TypeError, swallowed by the surrounding try → detailed "will also delete N pairs / M
mirrors" prompt never shown) and `deletePair` (`:1940-1942`) does `(mirrors || []).length`
(`undefined` on the object → count always 0). `syncAllMirrors` correctly uses
`response.total`, confirming the shape.

**Repro:** delete an instance backing 10 pairs / 500 mirrors → user gets the bland "Are you
sure?" instead of the blast-radius warning.

**Fix:** read `.mirrors` off the response (and use `.total` for accurate counts; note the
default page_size caps the array anyway).

---

### 11. Attachment URL extraction is wrong in both directions
**Labels:** bug, high, issue-sync

In `app/core/issue_sync.py` (`extract_mirror_urls_from_description` ~205-225,
`_sync_attachments_in_description` ~1306-1411):
1. Regexes match only `https?://` URLs, but GitLab stores issue attachments as **relative**
   markdown paths (`![img](/uploads/<secret>/file.png)`). Native attachments never match →
   never re-uploaded, and the copied description keeps a `/uploads/...` path that 404s on
   the target.
2. The link pattern `\[.*?\]\((https?://[^\)]+)\)` matches *every* absolute markdown link,
   so ordinary hyperlinks are downloaded, re-uploaded to the target, and rewritten —
   corrupting descriptions. `download_file` sends no auth, so a private-source upload can
   yield an HTML login page that gets uploaded as the "attachment".

**Fix:** match relative `/uploads/...` (and `/-/project/.../uploads/...`) paths, restrict
rewriting to actual upload URLs, and authenticate `download_file`.

---

## MEDIUM

### 12. Batch sync skips all pull mirrors that have no `mirror_id` (and reports them as succeeded)
**Labels:** bug, medium, mirrors

`app/api/pairs.py:764-769`: `if not gitlab_mirror_id: skipped += 1; continue` runs
regardless of direction, but pull mirrors legitimately have `mirror_id = None` (the rest of
the codebase explicitly allows this). The skip is recorded via `tracker.record_success()`,
so "Sync all" reports these as succeeded while triggering nothing.

**Fix:** only require `mirror_id` for push mirrors; trigger pull mirrors by `project_id`.

---

### 13. Circuit breakers open on non-transient errors (404/403/400/paused) and then 503 the whole instance
**Labels:** bug, medium, resilience

`expected_exception=GitLabClientError` (`app/core/mirror_gitlab_service.py:97-101`) is the
base class, so `GitLabNotFoundError`, `GitLabPermissionError`, `GitLabValidationError`,
`GitLabMirrorPausedError`, and even `GitLabRateLimitError` all increment the failure
counter. Five consecutive deterministic client errors (e.g. verifying 5 orphaned mirrors →
404s) open the per-instance circuit and return 503 for the recovery window (60s) for **all**
operations against that otherwise-healthy instance. The status scheduler re-triggers this
every 15 minutes for permanently-orphaned mirrors.

**Fix:** count only transient errors (429/5xx/network) toward the breaker; exclude 4xx
client errors.

---

### 14. HTTP rate limiting is largely non-functional
**Labels:** bug, medium, security

`app/core/api_rate_limiter.py` configures `Limiter(default_limits=["200/minute"])` but
`SlowAPIMiddleware` is never added (verified by grep), so default limits on undecorated
routes do nothing — only `@limiter.limit`-decorated endpoints are enforced. Also, behind
the documented nginx proxy with uvicorn started without `--proxy-headers`,
`get_client_identifier` falls back to `request.client.host` = the nginx container IP, so
all clients share one bucket (and `request.state.user` is never set, making the user branch
dead code).

**Impact:** the documented 200/min global limit is silent; one attacker firing 5
logins/min can exhaust the shared `AUTH_RATE_LIMIT` bucket and lock every user out of
`/api/login`.

**Fix:** add `SlowAPIMiddleware`; enable `--proxy-headers` (or trust `X-Forwarded-For`)
so per-client identification works behind nginx.

---

### 15. Legacy-mode `/api/auth/login` issues a JWT the API will never accept
**Labels:** bug, medium

With `MULTI_USER_ENABLED=false`, `_perform_login` (`app/api/auth.py:111-137`) returns a
"pseudo-token", but `verify_credentials` only checks Bearer tokens when
`multi_user_enabled` is true; in legacy mode it accepts Basic auth only. A client that
follows the login contract (POST creds → use `access_token` as `Authorization: Bearer`)
gets 200 + token, then 401 on every subsequent call.

**Fix:** either accept the legacy Bearer token in `verify_credentials`, or have the legacy
login endpoint return an error/401 that steers clients to Basic auth.

---

### 16. Infinite MutationObserver ↔ sort loop once a table column is sorted
**Labels:** bug, medium, frontend, performance

Each enhanced table gets a `MutationObserver` (`app/static/js/app.js:176-180`,
`childList: true, subtree: true`) whose debounced `refresh()` calls `applySorting()`
(`:486-499`), which re-appends every row via a DocumentFragment whenever
`state.sort.colIndex` is set. Re-appending nodes generates childList mutations → re-triggers
the observer → schedules another refresh, forever (~20×/sec) after any header click.

**Fix:** disconnect the observer while re-appending, or skip the mutation-triggered refresh
when the mutation is self-induced (guard flag).

---

### 17. Table-enhancer column configs are out of sync with the actual headers
**Labels:** bug, medium, frontend

`initTableEnhancements` maps config columns positionally against `tr.children[idx]`, but the
configs predate added columns:
- **Instances** (`app/static/js/app.js:130-138`): 5 config entries vs 7 headers (Name, URL,
  **Version**, Description, Access Token, **TLS Keep-Alive**, Actions). The "Description"
  filter/sort actually acts on the Version column; the real Description column becomes
  unsortable/unfilterable; the filter row has 5 cells under 7 headers.
- **Mirrors** (`:151-163`): 8 config entries vs 9 headers (missing the **Health** column),
  so the filter row is one cell short and misaligned.

**Fix:** add the Version, TLS Keep-Alive, and Health entries so configs align 1:1 with
headers.

---

### 18. Live polling overwrites the Enabled/Disabled badge with "Syncing..." in the wrong column and never restores it
**Labels:** bug, medium, frontend

`updateMirrorStatusIndicators` (`app/static/js/app.js:1432-1450`) targets `.mirror-status`,
which is the **Status (Enabled/Disabled)** cell (`:2328`), not the separate Sync Status cell
(`:2329`). It wipes the badge and inserts "Syncing..." in the wrong column, with no path to
remove the indicator when the mirror leaves the syncing set — it persists until the next
full `loadMirrors()`.

**Fix:** target the sync-status cell, and clear the indicator for mirrors no longer syncing.

---

### 19. Project autocomplete has no request sequencing — stale responses overwrite newer results
**Labels:** bug, medium, frontend

`searchProjectsForMirror` (`app/static/js/app.js:3465-3593`) lacks the `AbortController`/
`reqId` guard used by global search and topology drilldown; its no-results fallback issues a
second sequential request, so an earlier query can resolve last and overwrite
`autocompleteState[side].projects` and the dropdown for a query the user has moved on from.

**Fix:** add a per-side request sequence id (or AbortController) and ignore stale responses.

---

### 20. SSRF guard ignores `allow_private_ips` — attachment sync always fails in air-gapped deployments
**Labels:** bug, medium, issue-sync

`settings.allow_private_ips` is honored for instance-URL validation
(`app/api/instances.py:85`) but `download_file`'s SSRF check
(`app/core/issue_sync.py:252-296`) never consults it. Every attachment URL resolving to a
private IP raises `ValueError`, caught and mislogged as "Skipping attachment due to size
limit". With `ALLOW_PRIVATE_IPS=true` and GitLab on `10.x.x.x`, attachments never sync.

**Fix:** pass `allow_private_ips` through to the download SSRF validator, and fix the
misleading log message.

---

### 21. Token migration uses the push-mirror endpoint for pull mirrors
**Labels:** bug, medium, mirrors

`migrate_mirrors_to_auto_tokens` (`app/database.py:429-455`) calls
`mirror_client.update_mirror(...)` (`PUT /remote_mirrors/:id`, push-only) for **both**
directions. For pull mirrors it 404s (warning logged) but token fields are still committed,
so the DB claims the new token while the pull mirror keeps old credentials — and if a
push remote mirror happens to share the numeric id, its URL is overwritten with the wrong
repo's credentials.

**Fix:** branch on direction and call `update_pull_mirror` for pull mirrors.

---

### 22. Check-then-insert race allows duplicate concurrent syncs (then crashes the guard)
**Labels:** bug, medium, issue-sync

Both the scheduler (`app/core/issue_scheduler.py:324-377`) and manual trigger
(`app/api/issue_mirrors.py:369-408`) do a non-atomic SELECT for active jobs → INSERT
`status="running"` with no DB uniqueness constraint and `await` points in between. Two
concurrent syncs of the same config both create the same new issue on the target
(duplicates); afterward `scalar_one_or_none()` raises `MultipleResultsFound`, turning later
triggers into 500s until stale-job cleanup runs.

**Fix:** add a partial unique constraint (one active job per config) or use
`SELECT ... FOR UPDATE`/atomic insert; use `.first()` in the guard.

---

## LOW

### 23. `PUT /api/users/{id}` with explicit `"password": null` → unhandled 500
`app/api/users.py:269-276`: `model_dump(exclude_unset=True)` keeps an explicit `null`
password, so `get_password_hash(None)` → `None.encode(...)` `AttributeError` before the
try/except. Same pattern nulls `is_admin`/`is_active` on NOT NULL columns. **Fix:** skip
`None` values / validate.

### 24. `update_instance` failed token refresh wipes user fields and references a possibly-unbound `client`
`app/api/instances.py:443-460`: on any failure it nulls `api_user_id`/`api_username`, then
the second try references `client` (unbound if the constructor raised → `NameError`
swallowed by bare `except`), leaving stale version info; the update still commits. **Fix:**
don't null identity on transient errors; guard the second block.

### 25. `token_status` filter corrupts pagination metadata
`app/api/mirrors.py:624-628,702`: the filter is applied in Python after pagination, then
`total_count = len(mirrors)` (current page only) → `total_pages` may report 1, so clients
stop paging and miss matching rows on later pages. **Fix:** apply the filter in the query,
or compute totals from the full filtered set.

### 26. `_parse_datetime` strips timezone without converting to UTC
`app/core/issue_sync.py:1654-1667` (and `app/api/mirrors.py:2772-2776`):
`fromisoformat(...).replace(tzinfo=None)` keeps local wall-clock for offset timestamps
(e.g. `+02:00` stored as-is), corrupting UTC comparisons. **Fix:**
`dt.astimezone(timezone.utc).replace(tzinfo=None)`.

### 27. `GitLabClient` sessions are never closed
`close()`/context-manager support exists but no caller in `app/` uses it
(`app/core/issue_sync.py:489-498`, `app/api/mirrors.py:2707`, etc.). Each client holds a
`requests.Session` pool until GC — socket churn under load. **Fix:** use `with`/`close()`.

### 28. Encryption key / JWT secret generation is not multi-process safe
`app/core/encryption.py:42-61`, `app/core/jwt_secret.py:40-70`: exists-check → generate →
write is guarded only by an in-process `threading.Lock`. With multiple workers/replicas
sharing the PVC on first boot, two processes can generate different keys and one clobbers
the other, leaving previously stored tokens undecryptable. **Fix:** atomic create
(`O_CREAT|O_EXCL`) or file lock.

### 29. Assorted frontend low-severity
- Flat-view mirror **edit row** renders 7 cells in the 9-column table (`app.js:2216-2292`) →
  Save/Cancel/Delete under the wrong header.
- Resetting the pair selector to the placeholder sets `state.selectedPair = NaN`
  (`app.js:642-648`) → stale mirrors keep showing; row actions still target the old pair.
- Wrong button labels restored after Refresh Status / Verify on the error path
  (`app.js:2447`, `:2536`): "Status"/"Verify" instead of "Refresh Status"/"Verify Mirror".
- `formatZulu`/`formatZuluDate` return unparseable input unescaped into `innerHTML`
  (`app.js:41-57`, used at `:2277`, `:2333`, `:4310`, `:2303`) — latent XSS sink only
  reachable via a malicious/compromised GitLab status string. **Fix:**
  `escapeHtml(formatZulu(...))`.

### 30. `GET /api/auth/me` fabricates `created_at`
`app/api/auth.py:222` returns `created_at=datetime.utcnow()` even in multi-user mode where
the real DB value is available. **Fix:** use the user's real `created_at`.
