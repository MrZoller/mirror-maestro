# GitLab Issue Mirroring Implementation Plan

## Executive Summary

This document outlines a comprehensive plan for implementing optional issue mirroring between GitLab instances in Mirror Maestro. Unlike repository mirroring (which GitLab natively supports), issue mirroring requires custom implementation via the GitLab API to synchronize issues, comments, labels, milestones, and related metadata across instances.

**Key Challenges:**
1. Cross-instance reference mapping (labels, milestones, users, epics)
2. Handling missing or different entities across instances
3. Attachment/file handling
4. Maintaining sync reliability and performance

**Design Decision:**
Issue mirroring follows the same pattern as repository mirroring: always one-way (source → target). For bidirectional issue sync, users create two separate mirror pairs (A→B and B→A), just like with repository mirrors. This eliminates all conflict resolution complexity.

## 1. Architecture Overview

### 1.1 Sync Direction

**One-Way Sync Only (Source → Target)**
- Issues always flow from source → target
- Consistent with repository mirror behavior
- No conflict resolution needed
- Clear ownership model: source instance is authoritative
- Changes on target issues are overwritten by subsequent syncs (target issues are read-only mirrors)

**For Bidirectional Issue Sync:**
- Create two separate mirror pairs:
  - Mirror 1: Instance A → Instance B (syncs issues from A to B)
  - Mirror 2: Instance B → Instance A (syncs issues from B to A)
- Each mirror operates independently
- No cross-mirror conflict detection (user responsibility to avoid editing same issue on both sides)
- Simpler architecture, easier to reason about and debug

**Bidirectional Workflow Example:**

Common use case: Two development environments where developers work in both, but agile planning happens in one.

```
Environment A (Primary - Agile Hub):
- Issues created here
- Sprint planning, iterations, milestones, epics managed here
- Weight assigned during planning
- Some developers work and log time here
- All MRs and code merging happen here

Environment B (Secondary - Work Environment):
- Developers see mirrored issues
- Set time estimates on issues
- Log time spent working
- Create MRs (that get mirrored to A for merging)

Bidirectional Mirrors: A→B and B→A

Field Flow:
1. Issue created in A with weight=5
2. A→B sync: Issue appears in B with weight=5
3. Dev in B sets time_estimate=2h, logs time_spent=1h
4. B→A sync: A now has time_estimate=2h, time_spent=1h
5. Dev in A logs time_spent=30m more
6. A→B sync: B now has time_spent=1.5h total

Result: Fields naturally flow both ways through independent one-way syncs
Risk: If both environments update same field simultaneously (within sync interval),
      last sync wins (rare edge case, acceptable for most workflows)
```

### 1.2 Sync Trigger Mechanisms

**Option 1: Webhook-Based (Real-time)**
- GitLab sends webhook events when issues change
- Near real-time synchronization
- Requires publicly accessible endpoint
- More complex setup (webhook registration, signature verification)
- Lower server load (event-driven)

**Option 2: Polling-Based (Scheduled)**
- Periodic polling (e.g., every 5-15 minutes)
- Simpler to implement
- Works with private instances
- Higher latency
- Easier to debug and test
- Can batch operations for efficiency

**Option 3: Hybrid**
- Webhooks for instances that support them
- Polling as fallback
- Best of both worlds, most complex

**Recommendation:** Start with Option 2 (polling) for simplicity, add Option 1 as enhancement.

## 2. Database Schema Design

### 2.1 Core Issue Mirroring Tables

```sql
-- Main configuration: which mirrors should sync issues
-- Note: Issue sync is always one-way (source → target), matching repository mirror behavior
CREATE TABLE mirror_issue_configs (
    id SERIAL PRIMARY KEY,
    mirror_id INTEGER NOT NULL REFERENCES mirrors(id) ON DELETE CASCADE,

    -- Issue sync settings
    enabled BOOLEAN DEFAULT true,

    -- What to sync
    sync_comments BOOLEAN DEFAULT true,
    sync_labels BOOLEAN DEFAULT true,
    sync_attachments BOOLEAN DEFAULT true,
    sync_weight BOOLEAN DEFAULT true,
    sync_time_estimate BOOLEAN DEFAULT true,
    sync_time_spent BOOLEAN DEFAULT true,
    sync_closed_issues BOOLEAN DEFAULT false, -- Only sync open issues by default

    -- Note: Milestones, iterations, epics, and assignees are converted to labels (not synced as fields)

    -- Sync behavior
    update_existing BOOLEAN DEFAULT true, -- Update already-synced issues on subsequent syncs

    -- Sync state
    last_sync_at TIMESTAMP,
    last_sync_status VARCHAR(50), -- 'success', 'partial', 'failed'
    last_sync_error TEXT,
    next_sync_at TIMESTAMP,

    -- Polling interval (minutes)
    sync_interval_minutes INTEGER DEFAULT 15,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(mirror_id)
);

-- Issue mapping: tracks which issues correspond across instances
CREATE TABLE issue_mappings (
    id SERIAL PRIMARY KEY,
    mirror_issue_config_id INTEGER NOT NULL REFERENCES mirror_issue_configs(id) ON DELETE CASCADE,

    -- Source issue info
    source_issue_id INTEGER NOT NULL, -- GitLab issue ID
    source_issue_iid INTEGER NOT NULL, -- GitLab issue IID (project-scoped)
    source_project_id INTEGER NOT NULL,

    -- Target issue info
    target_issue_id INTEGER NOT NULL,
    target_issue_iid INTEGER NOT NULL,
    target_project_id INTEGER NOT NULL,

    -- Sync tracking
    last_synced_at TIMESTAMP,
    source_updated_at TIMESTAMP, -- Last update time in source GitLab
    target_updated_at TIMESTAMP, -- Last update time in target GitLab
    sync_status VARCHAR(50) DEFAULT 'synced', -- 'synced', 'pending', 'conflict', 'error'
    sync_error TEXT,

    -- Hash of source content for change detection
    source_content_hash VARCHAR(64),

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(mirror_issue_config_id, source_issue_id),
    UNIQUE(mirror_issue_config_id, target_issue_id)
);

CREATE INDEX idx_issue_mappings_source ON issue_mappings(source_project_id, source_issue_iid);
CREATE INDEX idx_issue_mappings_target ON issue_mappings(target_project_id, target_issue_iid);
CREATE INDEX idx_issue_mappings_sync_status ON issue_mappings(sync_status);

-- Comment mapping: tracks comment correspondence
CREATE TABLE comment_mappings (
    id SERIAL PRIMARY KEY,
    issue_mapping_id INTEGER NOT NULL REFERENCES issue_mappings(id) ON DELETE CASCADE,

    source_note_id INTEGER NOT NULL,
    target_note_id INTEGER NOT NULL,

    last_synced_at TIMESTAMP,
    source_content_hash VARCHAR(64),

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(issue_mapping_id, source_note_id),
    UNIQUE(issue_mapping_id, target_note_id)
);

CREATE INDEX idx_comment_mappings_issue ON comment_mappings(issue_mapping_id);
```

### 2.2 Reference Mapping Tables

```sql
-- Label mapping: how labels correspond across instances (optional - for explicit overrides)
-- Most labels use exact match with auto-create, but this allows custom mappings
CREATE TABLE label_mappings (
    id SERIAL PRIMARY KEY,
    mirror_issue_config_id INTEGER NOT NULL REFERENCES mirror_issue_configs(id) ON DELETE CASCADE,

    source_label_name VARCHAR(255) NOT NULL,
    target_label_name VARCHAR(255) NOT NULL,

    -- Strategy: 'exact' (same name), 'mapped' (explicit mapping), 'skip' (don't sync this label)
    mapping_strategy VARCHAR(20) DEFAULT 'exact',

    -- If target label doesn't exist, should we create it?
    auto_create BOOLEAN DEFAULT true,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(mirror_issue_config_id, source_label_name)
);

-- NOTE: Milestones, iterations, epics, and assignees are converted to labels rather than being mapped
-- This avoids complex cross-instance object mapping and permission requirements
-- Target users can manually assign issues to themselves based on the informational labels

-- Attachment mapping: track uploaded files
CREATE TABLE attachment_mappings (
    id SERIAL PRIMARY KEY,
    issue_mapping_id INTEGER REFERENCES issue_mappings(id) ON DELETE CASCADE,
    comment_mapping_id INTEGER REFERENCES comment_mappings(id) ON DELETE CASCADE,

    source_url TEXT NOT NULL,
    target_url TEXT NOT NULL,

    filename VARCHAR(500),
    content_type VARCHAR(100),
    file_size INTEGER,

    uploaded_at TIMESTAMP,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT attachment_parent_check CHECK (
        (issue_mapping_id IS NOT NULL AND comment_mapping_id IS NULL) OR
        (issue_mapping_id IS NULL AND comment_mapping_id IS NOT NULL)
    )
);

CREATE INDEX idx_attachment_mappings_issue ON attachment_mappings(issue_mapping_id);
CREATE INDEX idx_attachment_mappings_comment ON attachment_mappings(comment_mapping_id);
```

### 2.3 Sync Job Queue

```sql
-- Track sync jobs for async processing
CREATE TABLE issue_sync_jobs (
    id SERIAL PRIMARY KEY,
    mirror_issue_config_id INTEGER NOT NULL REFERENCES mirror_issue_configs(id) ON DELETE CASCADE,

    job_type VARCHAR(50) NOT NULL, -- 'full_sync', 'incremental_sync', 'single_issue'
    status VARCHAR(50) DEFAULT 'pending', -- 'pending', 'running', 'completed', 'failed'

    -- Job parameters (JSON)
    parameters JSONB,

    -- Results
    issues_processed INTEGER DEFAULT 0,
    issues_created INTEGER DEFAULT 0,
    issues_updated INTEGER DEFAULT 0,
    issues_failed INTEGER DEFAULT 0,
    error_details JSONB,

    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- For idempotency
    idempotency_key VARCHAR(255) UNIQUE
);

CREATE INDEX idx_sync_jobs_status ON issue_sync_jobs(status);
CREATE INDEX idx_sync_jobs_config ON issue_sync_jobs(mirror_issue_config_id, created_at);
```

## 3. GitLab API Integration

### 3.1 Required API Endpoints

Based on [GitLab Issues API](https://docs.gitlab.com/api/issues/), [Notes API](https://docs.gitlab.com/api/notes/), [Resource Label Events API](https://docs.gitlab.com/api/resource_label_events/), and [Markdown Uploads API](https://docs.gitlab.com/api/project_markdown_uploads/):

**Issues:**
- `GET /projects/:id/issues` - List issues with pagination
- `GET /projects/:id/issues/:issue_iid` - Get single issue
- `POST /projects/:id/issues` - Create issue
- `PUT /projects/:id/issues/:issue_iid` - Update issue
- `GET /projects/:id/issues/:issue_iid/resource_label_events` - Get label history
- `GET /projects/:id/issues/:issue_iid/resource_state_events` - Get state changes

**Notes/Comments:**
- `GET /projects/:id/issues/:issue_iid/notes` - List comments
- `POST /projects/:id/issues/:issue_iid/notes` - Create comment
- `PUT /projects/:id/issues/:issue_iid/notes/:note_id` - Update comment

**Attachments:**
- `POST /projects/:id/uploads` - Upload file, returns markdown link
- Download via returned URL

**Labels:**
- `GET /projects/:id/labels` - List project labels
- `POST /projects/:id/labels` - Create label

**Milestones:**
- `GET /projects/:id/milestones` - List milestones
- `POST /projects/:id/milestones` - Create milestone (if auto_create enabled)

**Users:**
- `GET /users?username=<username>` - Find user by username
- `GET /projects/:id/members` - List project members

### 3.2 API Client Enhancements

Extend `app/core/gitlab_client.py` with issue-related methods:

```python
class GitLabClient:
    # ... existing methods ...

    # Issues
    def list_issues(self, project_id: int, updated_after: datetime = None,
                   state: str = 'opened', per_page: int = 100) -> List[dict]:
        """List issues, optionally filtered by update time."""

    def get_issue(self, project_id: int, issue_iid: int) -> dict:
        """Get single issue details."""

    def create_issue(self, project_id: int, title: str, description: str = None,
                    labels: List[str] = None, assignee_ids: List[int] = None,
                    milestone_id: int = None, **kwargs) -> dict:
        """Create new issue."""

    def update_issue(self, project_id: int, issue_iid: int, **kwargs) -> dict:
        """Update existing issue."""

    # Comments
    def list_issue_notes(self, project_id: int, issue_iid: int) -> List[dict]:
        """List all comments on an issue."""

    def create_issue_note(self, project_id: int, issue_iid: int, body: str) -> dict:
        """Create comment on issue."""

    def update_issue_note(self, project_id: int, issue_iid: int,
                         note_id: int, body: str) -> dict:
        """Update existing comment."""

    # Attachments
    def upload_file(self, project_id: int, file_path: str = None,
                   file_content: bytes = None, filename: str = None) -> dict:
        """Upload file to project, returns markdown link."""

    def download_file(self, url: str) -> bytes:
        """Download file from GitLab."""

    # Labels
    def list_labels(self, project_id: int) -> List[dict]:
        """List project labels."""

    def create_label(self, project_id: int, name: str, color: str = '#428BCA') -> dict:
        """Create new label."""

    # Milestones
    def list_milestones(self, project_id: int, state: str = 'active') -> List[dict]:
        """List project milestones."""

    def create_milestone(self, project_id: int, title: str, **kwargs) -> dict:
        """Create new milestone."""

    # Users
    def find_user_by_username(self, username: str) -> dict | None:
        """Find user by username."""

    def list_project_members(self, project_id: int) -> List[dict]:
        """List project members."""
```

## 4. Sync Logic Implementation

### 4.1 Sync Flow (One-Way: Source → Target)

```
1. Initialization Phase:
   ├─ Load mirror_issue_config
   ├─ Get source and target instances
   └─ Verify API connectivity

2. Discovery Phase:
   ├─ Fetch all issues from source (filtered by updated_after if incremental)
   ├─ Fetch existing issue_mappings
   └─ Determine which issues need sync (new, updated, or unchanged)

3. Reference Mapping Phase (per issue):
   ├─ Map labels (create missing if auto_create enabled)
   ├─ Map milestone (or skip if not found)
   ├─ Map assignees (or apply fallback strategy)
   └─ Store mappings in database

4. Issue Sync Phase (per issue):
   ├─ Check if issue already mapped
   ├─ If new:
   │  ├─ Create issue on target with mapped references
   │  ├─ Store issue_mapping
   │  └─ Add "mirror metadata" footer to description
   ├─ If existing and update_existing=true:
   │  ├─ Check content hash for changes
   │  ├─ Update issue if changed
   │  └─ Update issue_mapping
   └─ Handle errors (log, mark as failed, continue)

5. Comment Sync Phase (per issue):
   ├─ Fetch source comments
   ├─ Fetch existing comment_mappings
   ├─ For new comments:
   │  ├─ Create on target
   │  └─ Store comment_mapping
   ├─ For updated comments:
   │  └─ Update on target if content changed
   └─ Handle attachments (download, re-upload, replace URLs)

6. Attachment Processing:
   ├─ Parse markdown for attachment URLs
   ├─ Download from source
   ├─ Upload to target
   ├─ Replace URLs in description/comments
   └─ Store attachment_mapping

7. Finalization Phase:
   ├─ Update mirror_issue_config (last_sync_at, status)
   ├─ Update issue_mappings (last_synced_at, hashes)
   ├─ Log summary statistics
   └─ Schedule next sync
```

### 4.2 Content Hash Calculation

To detect changes without full comparison:

```python
import hashlib
import json

def calculate_issue_hash(issue: dict) -> str:
    """Calculate hash of issue content for change detection."""
    content = {
        'title': issue.get('title'),
        'description': issue.get('description'),
        'state': issue.get('state'),
        'labels': sorted(issue.get('labels', [])),
        'milestone': issue.get('milestone', {}).get('id') if issue.get('milestone') else None,
        'assignees': sorted([a['id'] for a in issue.get('assignees', [])]),
        'updated_at': issue.get('updated_at'),
    }
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
```

### 4.3 Mirror Metadata Footer

Add metadata footer to synced issues so users know they're mirrors:

```markdown
---
> **🔄 Mirror Information**
>
> This issue is automatically mirrored from [source-instance/project#123](https://source.gitlab.com/project/-/issues/123)
>
> Last synced: 2026-01-01 12:00:00 UTC
>
> ⚠️ Changes made here may be overwritten by the next sync.
```

### 4.4 URL Rewriting for Attachments

When syncing comments/descriptions with attachments:

```python
import re

def rewrite_attachment_urls(content: str, attachment_mappings: dict) -> str:
    """Replace source attachment URLs with target URLs."""
    for source_url, target_url in attachment_mappings.items():
        content = content.replace(source_url, target_url)
    return content

def extract_attachment_urls(content: str) -> List[str]:
    """Extract attachment URLs from markdown content."""
    # GitLab attachment pattern: ![filename](/uploads/hash/filename)
    pattern = r'!\[.*?\]\((\/uploads\/[^\)]+)\)'
    return re.findall(pattern, content)
```

## 5. Reference Mapping Strategies

### 5.1 Label Mapping

**Strategy 1: Exact Match (Default)**
- Source label "bug" → Target label "bug"
- If target doesn't have "bug" label:
  - If `auto_create=true`: Create label with same name and default color
  - If `auto_create=false`: Skip label

**Strategy 2: Explicit Mapping**
- User defines mappings in UI: "bug" → "defect", "enhancement" → "feature"
- Store in `label_mappings` table
- If no mapping exists, fall back to exact match or skip

**Strategy 3: Prefix/Suffix**
- Add prefix to all synced labels: "bug" → "mirror::bug"
- Helps distinguish mirrored issues visually
- Optional configuration per mirror

### 5.2 Milestone, Iteration, Epic, and Assignee Handling

**Challenge:** Milestones, iterations, and epics can be group-level objects with complex hierarchies that may not exist or may differ between instances. Similarly, users/assignees often have different usernames across instances, making reliable mapping impossible.

**Recommended Strategy: Convert to Labels + Description Footer**

Instead of trying to sync or map these complex GitLab objects and user identities, convert them to informational labels on the target:

```python
def convert_pm_fields_to_labels(source_issue: dict) -> List[str]:
    """Convert milestone/iteration/epic/assignees to labels for target."""
    labels = []

    # Milestone → Label
    if source_issue.get('milestone'):
        milestone_title = source_issue['milestone']['title']
        labels.append(f"Milestone::{milestone_title}")

    # Iteration → Label
    if source_issue.get('iteration'):
        iteration_title = source_issue['iteration']['title']
        labels.append(f"Iteration::{iteration_title}")

    # Epic → Label (simplified path)
    if source_issue.get('epic'):
        epic_title = simplify_epic_title(source_issue['epic']['title'])
        labels.append(f"Epic::{epic_title}")

    # Assignees → Labels
    if source_issue.get('assignees'):
        for assignee in source_issue['assignees']:
            username = assignee['username']
            labels.append(f"Assigned::{username}")

    return labels

def build_description_with_pm_context(source_issue: dict) -> str:
    """Add PM context to issue description footer."""
    footer = "\n\n---\n\n> **🔄 Mirrored Issue**\n>\n"
    footer += f"> **Source:** [{source_issue['references']['full']}]({source_issue['web_url']})\n"

    if source_issue.get('milestone'):
        m = source_issue['milestone']
        footer += f">\n> **📅 Milestone:** {m['title']}"
        if m.get('due_date'):
            footer += f" (Due: {m['due_date']})"

    if source_issue.get('iteration'):
        it = source_issue['iteration']
        footer += f">\n> **🏃 Iteration:** {it['title']}"
        if it.get('start_date') and it.get('due_date'):
            footer += f" ({it['start_date']} to {it['due_date']})"

    if source_issue.get('epic'):
        footer += f">\n> **🎯 Epic:** {source_issue['epic']['title']} ([view]({source_issue['epic']['web_url']}))"

    if source_issue.get('assignees'):
        assignee_names = ', '.join([f"@{a['username']}" for a in source_issue['assignees']])
        footer += f">\n> **👤 Originally assigned to:** {assignee_names}"

    footer += f">\n> Last synced: {datetime.utcnow().isoformat()} UTC"

    return source_issue['description'] + footer
```

**Benefits:**
- ✅ Works regardless of group structure differences or username mismatches
- ✅ No group-level API permissions required
- ✅ No complex user mapping when usernames differ between instances
- ✅ Developers can filter by iteration/milestone/assignee using labels
- ✅ Full context preserved in description with links back to source
- ✅ Target developers can manually assign to themselves or local milestones/iterations
- ✅ Simple implementation, no complex mapping tables (removed `user_mappings` table!)

**Target Developers Can Still Use Local PM Fields:**

The labels are informational only. Target developers can:
1. See label `Iteration::Sprint-24` (from source Environment A)
2. See label `Assigned::alice.smith` (from source Environment A)
3. Manually assign issue to their local "Sprint 24" iteration in Environment B
4. Manually assign to themselves (even if username differs: alice.smith in A, alice.b in B)
5. Manual assignments won't be overwritten (we don't sync milestone/iteration/assignee fields)

**Example Synced Issue:**

```markdown
# Fix authentication timeout

Users experiencing timeouts after 30 seconds...

[original description]

---

> **🔄 Mirrored Issue**
>
> **Source:** [env-a/backend/api#123](https://gitlab-a.com/backend/api/-/issues/123)
>
> **📅 Milestone:** Q1 2024 (Due: 2024-03-31)
> **🏃 Iteration:** Sprint 24 (2024-01-01 to 2024-01-14)
> **🎯 Epic:** Platform → Authentication → SSO Implementation ([view](link))
> **👤 Originally assigned to:** @alice.smith, @bob.jones
>
> Last synced: 2024-01-15T10:30:00 UTC
```

**Labels on target:**
- `Milestone::Q1-2024`
- `Iteration::Sprint-24`
- `Epic::Platform-Auth`
- `Assigned::alice.smith`
- `Assigned::bob.jones`
- `🔄 MIRRORED`

**Actual GitLab fields on target:**
- Milestone: NULL (or manually set by user)
- Iteration: NULL (or manually set by user)
- Assignees: [] (or manually set by user)
- Epic: NULL (or manually set by user)

### 5.3 Weight and Time Tracking

**Weight (Story Points):**
```python
# Simple field sync
if source_issue.get('weight') is not None:
    target_issue.weight = source_issue['weight']
```

**Time Estimate:**
```python
# Estimated time to complete (in seconds)
if source_issue.get('time_estimate') is not None:
    target_issue.time_estimate = source_issue['time_estimate']
```

**Time Spent:**
```python
# Total time logged (in seconds)
# Requires reset + set due to GitLab API design
async def sync_time_spent(project_id: int, issue_iid: int, total_seconds: int):
    """Reset and set time spent to match source."""
    # Reset existing time tracking
    await client.reset_time_tracking(project_id, issue_iid)

    # Set to new total
    if total_seconds > 0:
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        duration = f"{hours}h{minutes}m"
        await client.add_spent_time(project_id, issue_iid, duration)
```

**Bidirectional Flow:**

With mirrors A→B and B→A, these fields flow naturally both ways:
- Weight set in A → flows to B
- Time estimate set in B → flows to A
- Time logged in either → flows to other
- Last sync wins (acceptable for rare simultaneous updates)

## 6. Handling Target-Side Modifications

Since issue sync is always one-way (source → target), there's no conflict resolution complexity:

**Sync Behavior:**
- Source is always authoritative
- Target issues are overwritten on each sync (if `update_existing=true`)
- Target-side modifications are lost when source issue is updated

**Preventing Accidental Overwrites:**
- Add prominent "🔄 MIRRORED ISSUE" label to target issues
- Include mirror metadata footer warning users not to edit
- Consider making target issues "confidential" or adding a bot comment warning about overwrites

**For Users Who Need Bidirectional Sync:**
- Create two separate mirror pairs (A→B and B→A)
- User is responsible for avoiding editing the same issue on both sides
- If both sides are edited, last sync wins (no automatic merge)
- This is consistent with how repository mirroring works

**Optional: Overwrite Protection (Future Enhancement)**
- Track if target issue was modified since last sync
- Mark as `sync_status='target_modified'`
- Require manual confirmation before overwriting
- Show warning in UI: "Target issue was modified. Overwrite?"

## 7. Performance Optimization

### 7.1 Pagination and Batching

```python
async def sync_issues_batch(config: MirrorIssueConfig, batch_size: int = 50):
    """Sync issues in batches to avoid memory issues."""
    page = 1
    while True:
        issues = source_client.list_issues(
            project_id=source_project_id,
            per_page=batch_size,
            page=page,
            updated_after=config.last_sync_at
        )
        if not issues:
            break

        for issue in issues:
            await sync_single_issue(issue)

        page += 1
        await asyncio.sleep(0.5)  # Rate limiting
```

### 7.2 Rate Limiting

GitLab has API rate limits (typically 600 requests/minute per user):

```python
import asyncio
from datetime import datetime, timedelta

class RateLimiter:
    def __init__(self, max_requests: int = 500, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests = []

    async def acquire(self):
        """Wait if necessary to respect rate limits."""
        now = datetime.utcnow()
        cutoff = now - timedelta(seconds=self.window_seconds)

        # Remove old requests
        self.requests = [r for r in self.requests if r > cutoff]

        if len(self.requests) >= self.max_requests:
            # Wait until oldest request expires
            sleep_time = (self.requests[0] - cutoff).total_seconds()
            await asyncio.sleep(sleep_time)

        self.requests.append(now)
```

### 7.3 Caching

Cache frequently accessed data:

```python
from functools import lru_cache

@lru_cache(maxsize=1000)
def get_label_mapping(config_id: int, label_name: str) -> str | None:
    """Cached label lookup."""
    # DB query
    pass

@lru_cache(maxsize=500)
def get_user_mapping(config_id: int, user_id: int) -> int | None:
    """Cached user lookup."""
    # DB query
    pass
```

### 7.4 Incremental Sync

Only sync issues updated since last sync:

```python
# In sync job
updated_after = config.last_sync_at or (datetime.utcnow() - timedelta(days=30))
issues = client.list_issues(
    project_id=source_project_id,
    updated_after=updated_after,
    state='all'  # Include both open and closed if sync_closed_issues=true
)
```

### 7.5 Parallel Processing

Process multiple issues concurrently:

```python
import asyncio

async def sync_issues_parallel(issues: List[dict], max_concurrent: int = 10):
    """Sync multiple issues in parallel."""
    semaphore = asyncio.Semaphore(max_concurrent)

    async def sync_with_limit(issue):
        async with semaphore:
            return await sync_single_issue(issue)

    results = await asyncio.gather(
        *[sync_with_limit(issue) for issue in issues],
        return_exceptions=True
    )
    return results
```

## 8. Error Handling and Reliability

### 8.1 Retry Logic

```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((ConnectionError, TimeoutError))
)
async def create_issue_with_retry(client, project_id, **kwargs):
    """Create issue with automatic retries on transient errors."""
    return client.create_issue(project_id, **kwargs)
```

### 8.2 Transaction Safety

Use database transactions for atomic updates:

```python
async def sync_issue_atomic(db: AsyncSession, issue_data: dict):
    """Sync issue in atomic transaction."""
    async with db.begin():
        # Create issue mapping
        mapping = IssueMapping(...)
        db.add(mapping)

        # Create reference mappings
        for label in issue_data['labels']:
            label_map = LabelMapping(...)
            db.add(label_map)

        # If any operation fails, entire transaction rolls back
        await db.flush()
```

### 8.3 Idempotency

Ensure sync operations are idempotent:

```python
# Use idempotency keys for jobs
job = IssueSyncJob(
    mirror_issue_config_id=config.id,
    job_type='full_sync',
    idempotency_key=f"{config.id}:full_sync:{datetime.utcnow().date()}"
)
# If job with same key exists, skip
```

### 8.4 Error Recovery

Store detailed error information for debugging:

```python
try:
    target_issue = await create_issue_with_retry(...)
except Exception as e:
    error_details = {
        'error_type': type(e).__name__,
        'error_message': str(e),
        'source_issue_iid': source_issue['iid'],
        'timestamp': datetime.utcnow().isoformat(),
        'stack_trace': traceback.format_exc()
    }

    await db.execute(
        update(IssueMapping)
        .where(IssueMapping.id == mapping.id)
        .values(sync_status='error', sync_error=json.dumps(error_details))
    )
```

### 8.5 Health Monitoring

Track sync health metrics:

```python
class SyncMetrics:
    total_issues: int = 0
    successful: int = 0
    failed: int = 0
    skipped: int = 0
    duration_seconds: float = 0
    api_calls: int = 0

    def to_dict(self):
        return {
            'total_issues': self.total_issues,
            'success_rate': self.successful / self.total_issues if self.total_issues else 0,
            'failed': self.failed,
            'avg_time_per_issue': self.duration_seconds / self.total_issues if self.total_issues else 0,
        }
```

## 9. API Endpoints

### 9.1 Configuration Endpoints

```
POST   /api/mirrors/:mirror_id/issue-sync
       Enable issue syncing for a mirror
       Body: { sync_direction, sync_comments, sync_labels, ... }

GET    /api/mirrors/:mirror_id/issue-sync
       Get current issue sync configuration

PUT    /api/mirrors/:mirror_id/issue-sync
       Update issue sync configuration

DELETE /api/mirrors/:mirror_id/issue-sync
       Disable issue syncing
```

### 9.2 Mapping Endpoints

```
GET    /api/mirrors/:mirror_id/issue-sync/labels
       List label mappings

POST   /api/mirrors/:mirror_id/issue-sync/labels
       Create/update label mapping
       Body: { source_label, target_label, auto_create }

GET    /api/mirrors/:mirror_id/issue-sync/milestones
       List milestone mappings

GET    /api/mirrors/:mirror_id/issue-sync/users
       List user mappings

POST   /api/mirrors/:mirror_id/issue-sync/users
       Create user mapping
```

### 9.3 Sync Control Endpoints

```
POST   /api/mirrors/:mirror_id/issue-sync/sync
       Trigger manual sync now
       Body: { sync_type: 'full' | 'incremental' }

GET    /api/mirrors/:mirror_id/issue-sync/status
       Get current sync status

GET    /api/mirrors/:mirror_id/issue-sync/jobs
       List recent sync jobs

GET    /api/mirrors/:mirror_id/issue-sync/jobs/:job_id
       Get details of specific sync job
```

### 9.4 Issue Mapping Endpoints

```
GET    /api/mirrors/:mirror_id/issue-sync/issues
       List all synced issues with status
       Query params: ?status=conflict&page=1&per_page=50

GET    /api/mirrors/:mirror_id/issue-sync/issues/:mapping_id
       Get details of specific issue mapping

POST   /api/mirrors/:mirror_id/issue-sync/issues/:mapping_id/resolve-conflict
       Resolve conflict manually
       Body: { resolution: 'use_source' | 'use_target' }

DELETE /api/mirrors/:mirror_id/issue-sync/issues/:mapping_id
       Unlink issue (delete mapping, optionally delete target issue)
```

## 10. Frontend Implementation

### 10.1 Configuration UI

Add "Issue Sync" tab to mirror details page:

```
┌─────────────────────────────────────────────────┐
│ Mirror: gitlab-prod → gitlab-backup             │
├─────────────────────────────────────────────────┤
│ [Repository] [Issue Sync] [Settings]            │
├─────────────────────────────────────────────────┤
│                                                  │
│ Issue Synchronization (Source → Target)          │
│                                                  │
│ [✓] Enable issue syncing                        │
│                                                  │
│ ℹ️  Issues always sync from source to target.   │
│    For bidirectional sync, create a reverse     │
│    mirror pair (target → source).               │
│                                                  │
│ What to sync:                                    │
│ [✓] Comments                                    │
│ [✓] Labels                                      │
│ [✓] Attachments                                 │
│ [✓] Weight (story points)                       │
│ [✓] Time estimate                               │
│ [✓] Time spent                                  │
│     └─ ℹ️  With bidirectional mirrors (A→B +   │
│        B→A), fields flow both ways using       │
│        last-sync-wins strategy.                 │
│ [ ] Closed issues                               │
│                                                  │
│ ℹ️  Milestones, iterations, epics, and          │
│    assignees are converted to informational     │
│    labels. Users can manually set these fields  │
│    on target issues if desired.                 │
│                                                  │
│ Sync Interval: [15] minutes                     │
│                                                  │
│ [Save Configuration]                             │
│                                                  │
└─────────────────────────────────────────────────┘
```

### 10.2 Mapping Management UI

```
┌─────────────────────────────────────────────────┐
│ Label Mappings                                   │
├─────────────────────────────────────────────────┤
│ Source Label    → Target Label     Auto-Create │
├─────────────────────────────────────────────────┤
│ bug             → defect           [ ]         │
│ enhancement     → feature          [✓]         │
│ documentation   → (exact match)    [✓]         │
│ [+ Add Mapping]                                 │
└─────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────┐
│ Milestone Mappings                               │
├─────────────────────────────────────────────────┤
│ Source          → Target           Strategy     │
├─────────────────────────────────────────────────┤
│ v1.0            → v1.0             By title    │
│ v2.0            → (not found)      Skip        │
│ [+ Add Mapping]                                 │
└─────────────────────────────────────────────────┘
```

### 10.3 Sync Status Dashboard

```
┌─────────────────────────────────────────────────┐
│ Sync Status                                      │
├─────────────────────────────────────────────────┤
│ Last Sync: 5 minutes ago                        │
│ Status: ✓ Success                               │
│ Next Sync: in 10 minutes                        │
│                                                  │
│ Statistics:                                      │
│ • Total Issues: 150                             │
│ • Synced: 148                                   │
│ • Conflicts: 2                                  │
│ • Errors: 0                                     │
│                                                  │
│ [Sync Now] [View History]                       │
└─────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────┐
│ Synced Issues                        [Search]   │
├─────────────────────────────────────────────────┤
│ Status  │ Source Issue  │ Target Issue  │ Last │
├─────────────────────────────────────────────────┤
│ ✓       │ #123 Bug      │ #456          │ 5m   │
│ ⚠️      │ #124 Feature  │ #457          │ 10m  │
│ ✓       │ #125 Docs     │ #458          │ 15m  │
│                                                  │
│ [View All] [Filter by Status]                   │
└─────────────────────────────────────────────────┘
```

### 10.4 Conflict Resolution UI

```
┌─────────────────────────────────────────────────┐
│ Conflict Resolution                              │
├─────────────────────────────────────────────────┤
│ Issue #124: "Add dark mode feature"             │
│                                                  │
│ ┌─ Source (gitlab-prod) ─────────────┐          │
│ │ Updated: 2h ago by @bob            │          │
│ │                                     │          │
│ │ Title: Add dark mode feature       │          │
│ │ Description: Implement dark mode   │          │
│ │              for better UX          │          │
│ │ Labels: enhancement, ui             │          │
│ │ Assignee: @bob                     │          │
│ └─────────────────────────────────────┘          │
│                                                  │
│ ┌─ Target (gitlab-backup) ───────────┐          │
│ │ Updated: 1h ago by @alice          │          │
│ │                                     │          │
│ │ Title: Add dark mode feature       │          │
│ │ Description: Implement dark mode   │          │
│ │              Updated requirements  │          │
│ │ Labels: enhancement, ui, urgent     │          │
│ │ Assignee: @alice                   │          │
│ └─────────────────────────────────────┘          │
│                                                  │
│ [Use Source] [Use Target] [View Diff]           │
│ [Manual Merge ↗]                                │
└─────────────────────────────────────────────────┘
```

## 11. Testing Strategy

### 11.1 Unit Tests

Test individual components:
- Hash calculation
- URL rewriting
- Reference mapping logic
- Conflict detection

### 11.2 Integration Tests

Test API interactions:
- Create issue on target GitLab
- Sync comments
- Upload/download attachments
- Label/milestone creation

### 11.3 End-to-End Tests

Full sync workflows:
- Full sync from scratch
- Incremental sync
- Handling missing references
- Error recovery

### 11.4 Load Tests

- Sync 1000+ issues
- Handle rate limiting gracefully
- Memory usage with large attachments

## 12. Implementation Phases

### Phase 1: Foundation (Week 1-2)
- [ ] Database schema implementation (6 tables - removed user_mappings!)
- [ ] GitLab API client extensions (issue, note, upload, time tracking endpoints)
- [ ] Basic issue sync engine (title, description, state, labels)
- [ ] Label auto-creation (exact match)
- [ ] Milestone/iteration/epic/assignee → label conversion
- [ ] Sync job scheduler (polling-based)
- [ ] Weight and time tracking field sync

### Phase 2: Core Features (Week 3-4)
- [ ] Comment syncing with content hash tracking
- [ ] Attachment download/upload/URL rewriting
- [ ] Issue description footer with PM context (milestone/iteration/epic/assignee info)
- [ ] Configuration API endpoints

### Phase 3: UI and UX (Week 5-6)
- [ ] Frontend configuration UI with inline help
- [ ] Sync status dashboard with metrics and bidirectional flow visualization
- [ ] Issue mapping list view with status indicators
- [ ] Manual sync trigger ("Sync Now" button)
- [ ] Optional: Label mapping management UI (for custom label overrides)

### Phase 4: Reliability & Testing (Week 7-8)
- [ ] Error handling and retry logic
- [ ] Rate limiting
- [ ] Incremental sync optimization
- [ ] Comprehensive testing
- [ ] Documentation

### Phase 5: Advanced Features (Future)
- [ ] Webhook-based real-time sync
- [ ] Epic syncing (for Premium/Ultimate instances)
- [ ] Advanced mapping strategies (custom label transformations, regex-based filtering)
- [ ] Target-side modification detection and warnings
- [ ] Batch operations (bulk enable/disable, mapping management)

## 13. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| API rate limits exceeded | Sync fails | Implement rate limiter, backoff, batching |
| Large attachments consume memory | OOM errors | Stream downloads/uploads, size limits |
| Target instance has different data | Mapping fails | Flexible fallback strategies, manual mapping |
| GitLab API changes | Sync breaks | Pin API version, automated testing, version checks |
| Network failures during sync | Partial sync | Transactions, idempotency, resume capability |
| User confusion about mirror behavior | Support burden | Clear UI indicators, documentation, metadata footers |
| Data inconsistency bugs | Data loss | Extensive testing, audit logs, manual override |
| Performance with 1000s of issues | Slow/timeout | Pagination, incremental sync, parallel processing |

## 14. Open Questions for Discussion

1. **Closed Issues:** Should we sync closed/resolved issues by default, or only open ones?
   - Current default: `sync_closed_issues=false` (only open issues)

2. **Existing Issues:** When enabling issue sync on a mirror with existing issues on both sides, how to handle initial mapping? Options:
   - Only sync new issues going forward (safest)
   - Try to match by title and link existing issues (risky - false matches)
   - Let user manually map existing issues first (most control)

3. **Label Auto-Create:** Default to true or false?
   - Creating labels automatically is convenient but may clutter target
   - Current recommendation: `auto_create=true` for simplicity

4. **Attachment Size Limit:** What's reasonable?
   - 10MB? 50MB? 100MB? Configurable per mirror?
   - Consider memory usage during download/upload

5. **Sync Frequency:** Default to 15 minutes? Allow faster polling?
   - 1 minute for "near real-time"
   - 5 minutes for good balance
   - 15 minutes for lower server load
   - Configurable per mirror

6. **Performance Target:** What's acceptable sync time?
   - 100 issues in < 1 minute?
   - 1000 issues in < 10 minutes?

7. **Webhook Support:** Priority for Phase 1, or defer to later phase?
   - Polling is simpler, webhooks are faster
   - Recommend defer to Phase 5

8. **Issue Types:** Should we sync all issue types (issue, incident, test case) or allow filtering?
   - Default: sync all types
   - Add filter option later if needed

## 15. Conclusion

GitLab issue mirroring is a valuable feature that requires careful design around reference mapping and sync reliability. The recommended approach is:

1. **Start Simple:** One-way sync (source→target), polling-based, exact label matching
2. **Build Reliability:** Focus on error handling, rate limiting, idempotency
3. **Iterate Based on Feedback:** Add webhooks, advanced mapping strategies later
4. **Maintain Transparency:** Clear UI indicators that issues are mirrored, metadata footers
5. **Consistent Architecture:** Follow the same pattern as repository mirroring (one-way only, use two pairs for bidirectional flow)

**Estimated Development Time:**
- Phase 1 (Foundation): 2 weeks
- Phase 2 (Core Features): 2 weeks
- Phase 3 (UI/UX): 1.5 weeks
- Phase 4 (Reliability & Testing): 2 weeks
- **Total for MVP:** ~7.5 weeks (~2 months) for single developer
- Phase 5 (Advanced - Webhooks, etc.): +2-3 weeks

**Recommended Tech Stack:**
- Backend: Extend existing FastAPI app
- Background Jobs: Add `asyncio` task scheduler or integrate Celery/Dramatiq for robustness
- Frontend: Extend existing vanilla JS (simpler than originally estimated - no conflict resolution UI needed)
- Database: PostgreSQL (already in use)

**Key Simplifications from One-Way Design:**
- No conflict resolution UI needed (saves ~1 week)
- No bidirectional sync state management (saves ~2 weeks)
- Simpler database schema (fewer fields to manage)
- Clearer mental model for users (consistent with repository mirroring)

This plan provides a solid foundation for implementing reliable GitLab issue mirroring using a proven architectural pattern.

---

## References

- [GitLab Issues API Documentation](https://docs.gitlab.com/api/issues/)
- [GitLab Notes API Documentation](https://docs.gitlab.com/api/notes/)
- [GitLab Resource Label Events API](https://docs.gitlab.com/api/resource_label_events/)
- [GitLab Resource State Events API](https://docs.gitlab.com/ee/api/resource_state_events.html)
- [GitLab Markdown Uploads API](https://docs.gitlab.com/api/project_markdown_uploads/)
