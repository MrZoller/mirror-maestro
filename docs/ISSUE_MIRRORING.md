## GitLab Issue Mirroring User Guide

### Overview

Mirror Maestro's issue mirroring feature allows you to automatically synchronize issues between GitLab instances. This is particularly useful for:

- **Multi-environment workflows**: Sync issues from your production GitLab to a development environment
- **Backup and redundancy**: Keep issue data synchronized across instances
- **Cross-organization collaboration**: Share issue tracking between separate GitLab instances
- **Migration preparation**: Gradually sync issues before migrating to a new GitLab instance

### Key Features

- ✅ **One-way sync** from source → target (like repository mirroring)
- ✅ **Bidirectional support** via two independent mirror pairs (A→B and B→A)
- ✅ **Automatic scheduling** with configurable intervals (5-1440 minutes)
- ✅ **Manual sync triggers** for immediate synchronization
- ✅ **Comprehensive field support**:
  - Title, description, state (open/closed)
  - Labels, weight
  - Comments/notes
  - Attachments with automatic download/upload
  - Time estimates and time spent
  - PM field conversion (milestones, iterations, epics, assignees → labels)
- ✅ **Smart change detection** using content hashing (avoids unnecessary updates)
- ✅ **Loop prevention** with "Mirrored-From" labels
- ✅ **Incremental syncing** (only processes changed issues)

---

## Getting Started

### Prerequisites

1. **Repository mirror**: Issue mirroring is configured per repository mirror
2. **GitLab instances**: You must have instances and pairs already configured
3. **Access tokens**: Tokens must have `api` scope for issue operations

### Basic Setup

1. **Create a repository mirror** (if you haven't already):
   - Go to the Mirrors tab
   - Configure your source and target projects
   - Save the mirror

2. **Enable issue mirroring**:
   - Click the **"Configure Issue Sync"** button on your mirror
   - Check the **"Enable Issue Sync"** checkbox
   - Configure which fields to sync (see configuration options below)
   - Set your sync interval (default: 15 minutes)
   - Click **"Save Configuration"**

3. **Trigger initial sync** (optional):
   - Click **"Trigger Sync"** to start syncing immediately
   - Or wait for the automatic scheduler to run

4. **Monitor sync status**:
   - View "Last Sync" timestamp in the mirror list
   - Check sync status (success/failed)
   - View error details if sync failed

---

## Configuration Options

### What to Sync

#### **Core Fields** (always synced):
- **Title**: Issue title
- **Description**: Issue description with footer
- **State**: Open/closed status

#### **Optional Fields** (configurable):

**✅ Sync Comments** (default: enabled)
- Syncs all non-system notes/comments
- Updates existing comments if they change
- Preserves comment author information in footer

**✅ Sync Labels** (default: enabled)
- Copies labels from source to target
- Auto-creates labels on target if they don't exist
- Adds "Mirrored-From::instance-{id}" label for tracking

**✅ Sync Attachments** (default: enabled)
- Downloads attachments from source
- Uploads to target project
- Replaces URLs in descriptions and comments
- Supports images, documents, and other file types

**✅ Sync Weight** (default: enabled)
- Syncs issue weight field
- Useful for agile planning workflows

**✅ Sync Time Estimate** (default: enabled)
- Syncs time estimate from source
- Format: GitLab duration strings (e.g., "3h30m")

**✅ Sync Time Spent** (default: enabled)
- Syncs total time spent from source
- Resets target time to match source exactly

**✅ Sync Closed Issues** (default: disabled)
- When enabled, includes closed issues in sync
- When disabled, only syncs open issues

### Sync Behavior

**✅ Update Existing Issues** (default: enabled)
- When enabled: Updates issues that were previously synced if they changed
- When disabled: Only creates new issues, never updates existing ones
- Recommended: Keep enabled for complete sync

**✅ Sync Existing Issues** (default: **disabled**)
- **When disabled** (recommended):
  - Only syncs issues created **after** enabling issue sync
  - Sets a baseline timestamp on first sync
  - Safe for repositories with thousands of existing issues
  - Avoids overwhelming the target with historical data

- **When enabled**:
  - Syncs **all** existing issues on the first sync
  - Subsequent syncs are incremental (only changed issues)
  - Use when you need complete historical issue data

**Sync Interval** (default: 15 minutes)
- How often to check for issue changes
- Range: 5-1440 minutes (1 day)
- Lower = more real-time, higher = lower server load

---

## How It Works

### Sync Flow

1. **Scheduler checks** every minute for configs due for sync
2. **Fetch issues** from source using GitLab API:
   - If incremental: Only issues updated since last sync
   - If first sync with `sync_existing_issues=false`: Sets baseline (no issues synced)
   - If first sync with `sync_existing_issues=true`: Fetches all issues
3. **For each issue**:
   - Check if already mirrored (using issue mapping table)
   - Compute content hash to detect changes
   - **If new**: Create issue on target
   - **If changed**: Update target issue (if `update_existing=true`)
   - **If unchanged**: Skip
4. **Sync comments** (if enabled)
5. **Sync attachments** (if enabled)
6. **Sync time tracking** (if enabled)
7. **Update sync status** and schedule next sync

### PM Field Conversion

GitLab Premium/Ultimate features (milestones, iterations, epics, assignees) are converted to **informational labels** on the target:

| Source Field | Target Label | Example |
|--------------|--------------|---------|
| Milestone | `Milestone::{title}` | `Milestone::v1.0` |
| Iteration | `Iteration::{title}` | `Iteration::Sprint 5` |
| Epic | `Epic::&{iid}` | `Epic::&42` |
| Assignee | `Assignee::@{username}` | `Assignee::@alice` |

**Additionally**, PM field information is included in the **description footer** for easy reference:

```markdown
---

<!-- MIRROR_MAESTRO_FOOTER -->

### 📋 Mirror Information

🔗 **Source**: [group/project#123](https://gitlab-source.com/group/project/-/issues/123)

🎯 **Milestone**: v1.0
🔄 **Iteration**: Sprint 5
🏔️ **Epic**: &42 Big Feature
👤 **Assignees**: Alice Smith, Bob Jones
```

### Loop Prevention

To prevent infinite sync loops when using bidirectional mirroring (A→B and B→A), the sync engine:

1. **Adds a "Mirrored-From" label** to every mirrored issue: `Mirrored-From::instance-{source_id}`
2. **Skips issues with reverse label**: Before syncing, checks if the source issue has a `Mirrored-From::instance-{target_id}` label, indicating it originated from the target instance and shouldn't be synced back

**How it works:**
```
Instance A                          Instance B
-----------                         -----------
Issue #100 (native)
     ↓ A→B sync
                                    Issue #200 (Mirrored-From::instance-A)
                                         ↓ B→A sync attempts
                                    SKIPPED: has Mirrored-From::instance-A label
                                    (target of B→A is A, so skip)
```

This means:
- ✅ Issues sync one direction only (from their origin instance)
- ✅ Updates to the original issue sync to mirrors
- ✅ No duplicate issues created from bidirectional syncs
- ⚠️ Edits made directly on mirrored issues will be overwritten by the next sync from the source
- 💡 Best practice: Edit issues on their origin instance, use mirrors as read-only copies

---

## Use Cases

### Use Case 1: Dev/Prod Issue Sync

**Scenario**: You have a production GitLab and a development GitLab. Issues are created in production, but developers work in the dev environment.

**Setup**:
- **Mirror**: Production → Development
- **Config**:
  - `sync_existing_issues`: false (only sync new issues)
  - `sync_comments`: true
  - `sync_labels`: true
  - `sync_closed_issues`: false (only open issues)
  - `update_existing`: true
  - `sync_interval`: 15 minutes

**Result**: New issues in production appear in development within 15 minutes. Developers see all context (comments, labels) but can't accidentally modify production issues.

### Use Case 2: Bidirectional Development Workflow

**Scenario**: Two development environments where developers work in both, but agile planning (milestones, epics, weight) happens in one.

**Setup**:
- **Mirror 1**: Environment A → Environment B
  - `sync_weight`: true
  - `sync_time_estimate`: false
  - `sync_time_spent`: true
  - All other defaults

- **Mirror 2**: Environment B → Environment A
  - `sync_weight`: false (don't overwrite A's planning)
  - `sync_time_estimate`: true (B sets estimates)
  - `sync_time_spent`: true (B logs time)
  - All other defaults

**Result**:
- Issues created in A appear in B with weight
- Developers in B set time estimates and log time
- Time data flows back to A
- Weight stays controlled by A

### Use Case 3: Pre-Migration Issue Sync

**Scenario**: Preparing to migrate to a new GitLab instance, want to sync issues incrementally before the final cutover.

**Setup**:
- **Mirror**: Old GitLab → New GitLab
- **Config**:
  - `sync_existing_issues`: **true** (sync all historical issues)
  - `sync_comments`: true
  - `sync_attachments`: true
  - `sync_closed_issues`: true (include everything)
  - `update_existing`: true
  - `sync_interval`: 5 minutes (more frequent)

**Result**: All issues (including closed) are synced to the new instance. As developers continue working on old instance, changes sync every 5 minutes. On cutover day, run a final sync and switch to the new instance.

---

## Monitoring and Troubleshooting

### Viewing Sync Status

In the Mirrors tab, each mirror with issue sync shows:
- **Last Sync**: Timestamp of last sync run
- **Status**: Success or Failed
- **Next Sync**: When the next automatic sync will run

### Manual Sync

Click **"Trigger Sync"** to:
- Start a sync immediately (doesn't wait for scheduled interval)
- Useful for testing or forcing an update
- Creates a sync job in the background
- Doesn't interfere with automatic scheduling

### Common Issues

#### "Sync status: failed"

**Check**:
1. GitLab instance tokens are still valid (not expired/revoked)
2. Tokens have `api` scope
3. Projects still exist and are accessible
4. Network connectivity between Mirror Maestro and GitLab instances

**View error details**:
- Error message appears in the config modal under "Last Sync Error"
- Check Mirror Maestro logs for detailed stack traces

#### "Issues not syncing"

**Verify**:
1. Issue sync is **enabled** (`enabled` checkbox checked)
2. Issue was created/updated **after** last sync (or `sync_existing_issues` is enabled)
3. Issue matches filters (e.g., if `sync_closed_issues=false`, closed issues won't sync)
4. Sync interval hasn't passed yet (check "Next Sync At")

**Try**:
- Click "Trigger Sync" to force an immediate sync
- Check that `update_existing=true` if you're expecting updates

#### "Attachments not syncing"

**Possible causes**:
- `sync_attachments=false` in configuration
- Attachment URLs are not in standard GitLab format
- Attachments are too large (network timeout)
- Network connectivity issues downloading from source

**Fix**:
- Enable `sync_attachments` in configuration
- Check Mirror Maestro logs for download/upload errors
- Try manually viewing attachment URLs in a browser

#### "PM fields not appearing"

**Remember**:
- PM fields (milestones, epics, etc.) are converted to **labels**, not actual PM fields
- Look for labels like `Milestone::v1.0`, `Epic::&42`
- Also check the **description footer** for PM field information

#### "Bidirectional sync creating duplicates"

**Cause**: You may have two mirrors syncing the same issues in both directions.

**Solution**: This is expected! Each mirror creates its own issues. The "Mirrored-From" label distinguishes the origin. This is by design for the one-way architecture.

**If you want true bidirectional**:
- Use Mirror 1 (A→B) and Mirror 2 (B→A)
- Issues created in A will sync to B
- Issues created in B will sync to A
- They remain separate issues (not linked)
- Best practice: Create issues in only one instance

---

## API Reference

### Endpoints

#### List issue mirror configurations
```http
GET /api/issue-mirrors
```

Response:
```json
[
  {
    "id": 1,
    "mirror_id": 10,
    "enabled": true,
    "sync_comments": true,
    "sync_labels": true,
    "sync_attachments": true,
    "sync_weight": true,
    "sync_time_estimate": true,
    "sync_time_spent": true,
    "sync_closed_issues": false,
    "update_existing": true,
    "sync_existing_issues": false,
    "sync_interval_minutes": 15,
    "last_sync_at": "2025-01-01T12:00:00Z",
    "last_sync_status": "success",
    "last_sync_error": null,
    "next_sync_at": "2025-01-01T12:15:00Z"
  }
]
```

#### Create issue mirror configuration
```http
POST /api/issue-mirrors
Content-Type: application/json

{
  "mirror_id": 10,
  "enabled": true,
  "sync_comments": true,
  "sync_labels": true,
  "sync_attachments": true,
  "sync_weight": true,
  "sync_time_estimate": true,
  "sync_time_spent": true,
  "sync_closed_issues": false,
  "update_existing": true,
  "sync_existing_issues": false,
  "sync_interval_minutes": 15
}
```

#### Update issue mirror configuration
```http
PUT /api/issue-mirrors/{config_id}
Content-Type: application/json

{
  "enabled": false,
  "sync_interval_minutes": 30
}
```

#### Delete issue mirror configuration
```http
DELETE /api/issue-mirrors/{config_id}
```

#### Trigger manual sync
```http
POST /api/issue-mirrors/{config_id}/trigger-sync
```

Response:
```json
{
  "message": "Sync triggered",
  "config_id": 1,
  "job_id": 42
}
```

---

## Best Practices

### Performance

1. **Use appropriate sync intervals**:
   - 5 min: Near real-time for active projects
   - 15 min: Good balance for most projects (default)
   - 60+ min: Archival/backup scenarios

2. **Disable sync_existing_issues for large projects**:
   - Projects with 100+ issues should leave this disabled
   - Only enable if you need complete historical data

3. **Disable unused features**:
   - If you don't need attachments, disable `sync_attachments` to save bandwidth
   - If you don't use weight, disable `sync_weight`

### Security

1. **Use project access tokens** (not personal tokens):
   - More secure (scoped to project only)
   - Easier to rotate
   - Mirror Maestro auto-creates these for repository mirrors

2. **Monitor sync status regularly**:
   - Failed syncs may indicate revoked/expired tokens
   - Set up monitoring alerts if possible

3. **Limit who can configure issue sync**:
   - Use Mirror Maestro's authentication features
   - Only admins should configure issue mirroring

### Workflow

1. **Start small**:
   - Test issue sync on a single mirror first
   - Verify issues sync correctly before enabling on all mirrors

2. **Communicate with your team**:
   - Let developers know issues are being mirrored
   - Explain that target issues are read-only (source is authoritative)
   - Document which instance is the "source of truth"

3. **Use manual triggers for important changes**:
   - After creating a critical issue, trigger a manual sync
   - Don't wait for the automatic scheduler

4. **Monitor initial sync**:
   - If enabling `sync_existing_issues=true`, watch the first sync
   - Verify issues are created correctly
   - Check for any errors in large batches

---

## Limitations and Known Issues

### Current Limitations

1. **One-way sync only**:
   - Each mirror syncs source → target only
   - For bidirectional, create two separate mirrors (A→B and B→A)

2. **No conflict resolution**:
   - If both sides modify the same field, last sync wins
   - No merge logic or conflict detection

3. **PM fields become labels**:
   - Milestones, epics, iterations, assignees → informational labels
   - Not actual PM fields on target (GitLab API limitation)

4. **Attachment size limits**:
   - Very large attachments may timeout during download/upload
   - Consider disabling `sync_attachments` for projects with large files

5. **No selective issue filtering**:
   - Currently syncs all issues (based on open/closed filter)
   - Cannot filter by specific labels, milestones, etc. (future enhancement)

6. **API rate limits**:
   - Syncing hundreds of issues may hit GitLab API rate limits
   - Sync engine respects rate limits but may take longer

### Future Enhancements (Phase 3+)

- Webhook-based real-time sync (instead of polling)
- Advanced filtering (sync only issues with specific labels)
- Epic syncing for Premium/Ultimate instances
- Target-side modification detection and warnings
- Batch operations (bulk enable/disable across mirrors)
- Sync job history and detailed logs UI
- Issue mapping view (see which source issue maps to which target)

---

## FAQ

**Q: Can I sync issues without syncing the repository?**
A: No, issue mirroring requires a repository mirror. The issue sync is configured per repository mirror.

**Q: Will syncing modify my source issues?**
A: No, the source is read-only. Sync only reads from source and writes to target.

**Q: Can I sync issues from multiple sources to one target?**
A: Yes, create multiple mirrors pointing to the same target project. Issues will be distinguished by the "Mirrored-From" label.

**Q: What happens if I delete an issue on the source?**
A: Currently, deletes are not synced. The target issue remains. (Future enhancement: optional delete sync)

**Q: Can I change the source/target after enabling sync?**
A: No, you must delete the issue mirror config and recreate it. Changing source/target would break issue mappings.

**Q: Do I need Premium/Ultimate GitLab for issue mirroring?**
A: No, issue mirroring works with GitLab Free tier. PM field syncing (milestones, epics) is available on all tiers (converted to labels on target).

**Q: How do I stop syncing issues?**
A: Disable the issue mirror config (uncheck "Enable Issue Sync") or delete the configuration entirely.

**Q: Can I sync in real-time?**
A: Currently, no. Minimum sync interval is 5 minutes. Real-time webhook support is planned for Phase 5.

---

## Support

For issues, questions, or feature requests:
- GitHub Issues: https://github.com/anthropics/mirror-maestro/issues
- Documentation: https://github.com/anthropics/mirror-maestro/docs

