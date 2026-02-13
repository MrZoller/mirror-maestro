"""Unit tests for issue sync engine."""

import pytest
from datetime import datetime
from unittest.mock import Mock, AsyncMock, patch, MagicMock

from app.core.issue_sync import (
    IssueSyncEngine,
    compute_content_hash,
    extract_footer,
    build_footer,
    convert_pm_fields_to_labels,
    get_mirror_from_label,
    _extract_hostname,
    extract_mirror_urls_from_description,
    extract_filename_from_url,
    replace_urls_in_description,
)
from app.models import (
    MirrorIssueConfig,
    Mirror,
    GitLabInstance,
    InstancePair,
    IssueMapping,
)


# -------------------------------------------------------------------------
# Helper Function Tests
# -------------------------------------------------------------------------


def test_compute_content_hash():
    """Test content hash computation is consistent."""
    content = "Test issue title|||Test description"
    hash1 = compute_content_hash(content)
    hash2 = compute_content_hash(content)

    assert hash1 == hash2
    assert len(hash1) == 64  # SHA256 produces 64 hex characters

    # Different content produces different hash
    different = "Different title|||Different description"
    hash3 = compute_content_hash(different)
    assert hash1 != hash3


def test_extract_footer_no_footer():
    """Test extracting footer when none exists."""
    description = "This is a regular description with no footer."
    main, footer = extract_footer(description)

    assert main == description
    assert footer is None


def test_extract_footer_with_footer():
    """Test extracting footer when it exists."""
    main_content = "This is the main content"
    footer_content = "\n### Mirror Information\nSource: project#123"
    description = f"{main_content}<!-- MIRROR_MAESTRO_FOOTER -->{footer_content}"

    main, footer = extract_footer(description)

    assert main == main_content
    assert footer == footer_content


def test_build_footer():
    """Test building description footer with PM fields."""
    footer = build_footer(
        source_instance_url="https://gitlab.example.com",
        source_project_path="group/project",
        source_issue_iid=123,
        source_web_url="https://gitlab.example.com/group/project/-/issues/123",
        milestone={"title": "v1.0"},
        iteration={"title": "Sprint 1"},
        epic={"iid": 42, "title": "Big Feature"},
        assignees=[
            {"username": "alice", "name": "Alice Smith"},
            {"username": "bob", "name": "Bob Jones"}
        ]
    )

    assert "<!-- MIRROR_MAESTRO_FOOTER -->" in footer
    assert "group/project#123" in footer
    assert "https://gitlab.example.com/group/project/-/issues/123" in footer
    assert "v1.0" in footer
    assert "Sprint 1" in footer
    assert "&42" in footer
    assert "Alice Smith" in footer or "alice" in footer
    assert "Bob Jones" in footer or "bob" in footer


def test_convert_pm_fields_to_labels():
    """Test converting PM fields to label names."""
    labels = convert_pm_fields_to_labels(
        milestone={"title": "v1.0"},
        iteration={"title": "Sprint 1"},
        epic={"iid": 42},
        assignees=[
            {"username": "alice"},
            {"username": "bob"}
        ]
    )

    assert "Milestone::v1.0" in labels
    assert "Iteration::Sprint 1" in labels
    assert "Epic::&42" in labels
    assert "Assignee::@alice" in labels
    assert "Assignee::@bob" in labels


def test_convert_pm_fields_to_labels_empty():
    """Test PM field conversion with no fields."""
    labels = convert_pm_fields_to_labels(
        milestone=None,
        iteration=None,
        epic=None,
        assignees=[]
    )

    assert labels == []


def test_get_mirror_from_label():
    """Test mirror-from label generation uses URL hostname."""
    label = get_mirror_from_label("https://gitlab.example.com")
    assert label == "Mirrored-From::gitlab.example.com"

    # With non-standard port
    label = get_mirror_from_label("https://gitlab.example.com:8443")
    assert label == "Mirrored-From::gitlab.example.com:8443"

    # Standard HTTPS port is omitted
    label = get_mirror_from_label("https://gitlab.example.com:443")
    assert label == "Mirrored-From::gitlab.example.com"

    # IP address with port
    label = get_mirror_from_label("http://10.0.0.1:8080")
    assert label == "Mirrored-From::10.0.0.1:8080"


def test_extract_hostname():
    """Test hostname extraction from URLs."""
    assert _extract_hostname("https://gitlab.example.com") == "gitlab.example.com"
    assert _extract_hostname("https://gitlab.example.com/") == "gitlab.example.com"
    assert _extract_hostname("https://gitlab.example.com:8443") == "gitlab.example.com:8443"
    assert _extract_hostname("http://10.0.0.1:8080/") == "10.0.0.1:8080"
    assert _extract_hostname("https://gitlab.example.com:443") == "gitlab.example.com"
    assert _extract_hostname("http://gitlab.example.com:80") == "gitlab.example.com"


def test_extract_mirror_urls_from_description():
    """Test extracting URLs from markdown description."""
    description = """
    # Test Issue

    Here's an image: ![screenshot](/uploads/abc123/screenshot.png)

    And a link: [documentation](https://example.com/docs/guide.pdf)

    Another image: ![diagram](https://gitlab.example.com/uploads/def456/diagram.png)
    """

    urls = extract_mirror_urls_from_description(description)

    assert "https://example.com/docs/guide.pdf" in urls
    assert "https://gitlab.example.com/uploads/def456/diagram.png" in urls
    # Note: relative URLs won't match the https:// pattern
    assert len([u for u in urls if "screenshot.png" in u]) == 0


def test_extract_filename_from_url():
    """Test extracting filename from URL."""
    assert extract_filename_from_url("https://example.com/uploads/abc/file.png") == "file.png"
    assert extract_filename_from_url("https://example.com/path/to/document.pdf") == "document.pdf"
    assert extract_filename_from_url("https://example.com/") == "attachment"


def test_replace_urls_in_description():
    """Test URL replacement in description."""
    description = "Check out ![image](https://old.com/image.png) and [link](https://old.com/doc.pdf)"
    mapping = {
        "https://old.com/image.png": "https://new.com/uploads/image.png",
        "https://old.com/doc.pdf": "https://new.com/uploads/doc.pdf"
    }

    result = replace_urls_in_description(description, mapping)

    assert "https://old.com/image.png" not in result
    assert "https://old.com/doc.pdf" not in result
    assert "https://new.com/uploads/image.png" in result
    assert "https://new.com/uploads/doc.pdf" in result


# -------------------------------------------------------------------------
# Sync Engine Tests
# -------------------------------------------------------------------------


@pytest.fixture
def mock_config():
    """Create mock issue mirror configuration."""
    config = MagicMock(spec=MirrorIssueConfig)
    config.id = 1
    config.mirror_id = 10
    config.enabled = True
    config.sync_comments = True
    config.sync_labels = True
    config.sync_attachments = True
    config.sync_weight = True
    config.sync_time_estimate = True
    config.sync_time_spent = True
    config.sync_closed_issues = False
    config.update_existing = True
    config.sync_existing_issues = False
    config.last_sync_at = None
    config.sync_interval_minutes = 15
    return config


@pytest.fixture
def mock_mirror():
    """Create mock mirror."""
    mirror = MagicMock(spec=Mirror)
    mirror.id = 10
    mirror.instance_pair_id = 5
    mirror.source_project_id = 100
    mirror.source_project_path = "group/source"
    mirror.target_project_id = 200
    mirror.target_project_path = "group/target"
    return mirror


@pytest.fixture
def mock_instances():
    """Create mock GitLab instances."""
    source = MagicMock(spec=GitLabInstance)
    source.id = 1
    source.name = "Source GitLab"
    source.url = "https://gitlab-source.example.com"
    source.encrypted_token = "enc:source-token"

    target = MagicMock(spec=GitLabInstance)
    target.id = 2
    target.name = "Target GitLab"
    target.url = "https://gitlab-target.example.com"
    target.encrypted_token = "enc:target-token"

    return source, target


@pytest.fixture
def mock_pair():
    """Create mock instance pair."""
    pair = MagicMock(spec=InstancePair)
    pair.id = 5
    pair.source_instance_id = 1
    pair.target_instance_id = 2
    pair.mirror_direction = "pull"
    return pair


@pytest.mark.asyncio
async def test_sync_engine_initialization(mock_config, mock_mirror, mock_instances, mock_pair):
    """Test sync engine initializes correctly."""
    source, target = mock_instances
    db = AsyncMock()

    with patch('app.core.issue_sync.GitLabClient') as MockClient:
        engine = IssueSyncEngine(
            db=db,
            config=mock_config,
            mirror=mock_mirror,
            source_instance=source,
            target_instance=target,
            instance_pair=mock_pair
        )

        assert engine.config == mock_config
        assert engine.mirror == mock_mirror
        assert engine.source_instance == source
        assert engine.target_instance == target
        assert MockClient.call_count == 2  # source and target clients


@pytest.mark.asyncio
async def test_sync_engine_push_mirror_uses_same_direction(mock_config, mock_mirror, mock_instances):
    """Test that push mirrors sync issues source→target (same direction as code).

    Previously, push mirrors incorrectly reversed the direction, reading issues
    from the push target and writing to the push source. This caused issue sync
    to silently produce 0 results for push mirrors since the push destination
    typically has no issues.
    """
    source, target = mock_instances
    db = AsyncMock()

    push_pair = MagicMock(spec=InstancePair)
    push_pair.id = 5
    push_pair.source_instance_id = 1
    push_pair.target_instance_id = 2
    push_pair.mirror_direction = "push"

    with patch('app.core.issue_sync.GitLabClient'):
        engine = IssueSyncEngine(
            db=db,
            config=mock_config,
            mirror=mock_mirror,
            source_instance=source,
            target_instance=target,
            instance_pair=push_pair
        )

        # Push mirrors should NOT swap project IDs - issues flow source→target
        assert engine.source_project_id == mock_mirror.source_project_id
        assert engine.target_project_id == mock_mirror.target_project_id
        assert engine.source_project_path == mock_mirror.source_project_path
        assert engine.target_project_path == mock_mirror.target_project_path


@pytest.mark.asyncio
async def test_prepare_labels(mock_config, mock_mirror, mock_instances, mock_pair):
    """Test label preparation with PM field conversion."""
    source, target = mock_instances
    db = AsyncMock()

    with patch('app.core.issue_sync.GitLabClient'):
        engine = IssueSyncEngine(
            db=db,
            config=mock_config,
            mirror=mock_mirror,
            source_instance=source,
            target_instance=target,
            instance_pair=mock_pair
        )

        source_issue = {
            "labels": ["bug", "priority::high"],
            "milestone": {"title": "v1.0"},
            "iteration": None,
            "epic": {"iid": 42},
            "assignees": [{"username": "alice"}]
        }

        labels = engine._prepare_labels(source_issue)

        # Should include Mirrored-From label based on source URL hostname
        assert "Mirrored-From::gitlab-source.example.com" in labels

        # Should include source labels
        assert "bug" in labels
        assert "priority::high" in labels

        # Should include PM field labels
        assert "Milestone::v1.0" in labels
        assert "Epic::&42" in labels
        assert "Assignee::@alice" in labels


@pytest.mark.asyncio
async def test_prepare_description(mock_config, mock_mirror, mock_instances, mock_pair):
    """Test description preparation with footer."""
    source, target = mock_instances
    db = AsyncMock()

    with patch('app.core.issue_sync.GitLabClient'):
        engine = IssueSyncEngine(
            db=db,
            config=mock_config,
            mirror=mock_mirror,
            source_instance=source,
            target_instance=target,
            instance_pair=mock_pair
        )

        source_issue = {
            "iid": 123,
            "description": "Original issue description",
            "web_url": "https://gitlab-source.example.com/group/source/-/issues/123",
            "milestone": {"title": "v1.0"},
            "iteration": None,
            "epic": None,
            "assignees": []
        }

        description = engine._prepare_description(source_issue)

        # Should include original content
        assert "Original issue description" in description

        # Should include footer marker
        assert "<!-- MIRROR_MAESTRO_FOOTER -->" in description

        # Should include source link
        assert "group/source#123" in description

        # Should include milestone info
        assert "v1.0" in description


@pytest.mark.asyncio
async def test_seconds_to_duration():
    """Test time conversion to GitLab duration format."""
    from app.core.issue_sync import IssueSyncEngine

    assert IssueSyncEngine._seconds_to_duration(3600) == "1h"
    assert IssueSyncEngine._seconds_to_duration(1800) == "30m"
    assert IssueSyncEngine._seconds_to_duration(7200) == "2h"
    assert IssueSyncEngine._seconds_to_duration(5400) == "1h30m"
    assert IssueSyncEngine._seconds_to_duration(0) == "0m"
    assert IssueSyncEngine._seconds_to_duration(90) == "1m"


@pytest.mark.asyncio
async def test_sync_engine_loop_prevention(mock_config, mock_mirror, mock_instances, mock_pair):
    """Test that issues with Mirrored-From label pointing to target are skipped.

    This prevents infinite loops in bidirectional mirroring setups (A→B and B→A).
    If an issue on B has label 'Mirrored-From::instance-A', it originated from A
    and should not be synced back to A by the B→A sync.
    """
    source, target = mock_instances
    db = AsyncMock()

    # Mock db.execute to return empty result for IssueMapping check
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=mock_result)

    with patch('app.core.issue_sync.GitLabClient'):
        engine = IssueSyncEngine(
            db=db,
            config=mock_config,
            mirror=mock_mirror,
            source_instance=source,
            target_instance=target,
            instance_pair=mock_pair
        )

        # Verify the originated_from_target_label is set correctly
        # Target instance URL is https://gitlab-target.example.com
        assert engine.originated_from_target_label == "Mirrored-From::gitlab-target.example.com"

        # Source issue that originated from target (has the target's Mirrored-From label)
        # This simulates an issue on source that was originally mirrored from target
        source_issue_from_target = {
            "id": 500,
            "iid": 50,
            "title": "Issue that came from target",
            "description": "This issue was synced from target to source",
            "labels": ["bug", "Mirrored-From::gitlab-target.example.com"],  # Has target's label
            "web_url": "https://gitlab-source.example.com/group/source/-/issues/50",
        }

        # Native source issue (no Mirrored-From label)
        native_source_issue = {
            "id": 501,
            "iid": 51,
            "title": "Native issue on source",
            "description": "This issue was created natively on source",
            "labels": ["feature"],  # No Mirrored-From label
            "web_url": "https://gitlab-source.example.com/group/source/-/issues/51",
        }

        stats = {
            "issues_processed": 0,
            "issues_created": 0,
            "issues_updated": 0,
            "issues_skipped": 0,
            "issues_failed": 0,
            "errors": [],
        }

        # Test: Issue with target's Mirrored-From label (target hostname) should be skipped
        await engine._sync_issue(source_issue_from_target, stats)

        assert stats["issues_processed"] == 1
        assert stats["issues_skipped"] == 1
        assert stats["issues_created"] == 0
        # Database should NOT be queried for mapping since we skip early
        # (The db.execute call for IssueMapping should not happen)

        # Reset stats
        stats = {
            "issues_processed": 0,
            "issues_created": 0,
            "issues_updated": 0,
            "issues_skipped": 0,
            "issues_failed": 0,
            "errors": [],
        }

        # Test: Native issue should proceed to mapping check
        # We'll mock the full flow for this test
        with patch.object(engine, '_create_target_issue', new_callable=AsyncMock) as mock_create:
            await engine._sync_issue(native_source_issue, stats)

            assert stats["issues_processed"] == 1
            # Should proceed past loop prevention check
            # Since no mapping exists, it should try to create
            assert mock_create.called or stats["issues_created"] == 1 or stats["issues_skipped"] == 0


@pytest.mark.asyncio
async def test_sync_engine_loop_prevention_with_different_label(mock_config, mock_mirror, mock_instances, mock_pair):
    """Test that issues with a DIFFERENT Mirrored-From label are NOT skipped.

    An issue with Mirrored-From::gitlab-third.example.com (a third instance) should
    still be synced, only Mirrored-From::{target_hostname} should be skipped.
    This is the key behavior that enables multi-hop syncing (A→B→C).
    """
    source, target = mock_instances
    db = AsyncMock()

    with patch('app.core.issue_sync.GitLabClient'):
        engine = IssueSyncEngine(
            db=db,
            config=mock_config,
            mirror=mock_mirror,
            source_instance=source,
            target_instance=target,
            instance_pair=mock_pair
        )

        # Issue from a third instance (not source or target)
        issue_from_third_instance = {
            "id": 600,
            "iid": 60,
            "title": "Issue from third instance",
            "description": "This came from a third GitLab",
            "labels": ["bug", "Mirrored-From::gitlab-third.example.com"],  # Different instance
            "web_url": "https://gitlab-source.example.com/group/source/-/issues/60",
        }

        stats = {
            "issues_processed": 0,
            "issues_created": 0,
            "issues_updated": 0,
            "issues_skipped": 0,
            "issues_failed": 0,
            "errors": [],
        }

        # Mock db.execute to return empty result (no existing mapping)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=mock_result)

        with patch.object(engine, '_create_target_issue', new_callable=AsyncMock) as mock_create:
            await engine._sync_issue(issue_from_third_instance, stats)

            # Should NOT be skipped - the label is for a different hostname
            assert stats["issues_processed"] == 1
            assert stats["issues_skipped"] == 0
            # Should proceed to create since no mapping exists
            assert mock_create.called


@pytest.mark.asyncio
async def test_multi_instance_issue_chain_a_to_b_to_c(mock_config, mock_mirror):
    """Test issue syncing across multiple Mirror Maestro instances (A→B→C).

    Scenario:
      MM1 manages instances A and B.
      MM2 manages instances B and C.
      An issue on A is synced to B by MM1, then from B to C by MM2.

    Previously, using local DB IDs (instance-1, instance-2) could cause ID
    collisions across MM instances, incorrectly skipping issues. Using URL
    hostnames as identifiers resolves this.
    """
    # --- MM1: Instance A (id=1) → Instance B (id=2) ---
    instance_a = MagicMock(spec=GitLabInstance)
    instance_a.id = 1
    instance_a.name = "GitLab A"
    instance_a.url = "https://gitlab-a.example.com"
    instance_a.encrypted_token = "enc:a-token"

    instance_b_mm1 = MagicMock(spec=GitLabInstance)
    instance_b_mm1.id = 2
    instance_b_mm1.name = "GitLab B"
    instance_b_mm1.url = "https://gitlab-b.example.com"
    instance_b_mm1.encrypted_token = "enc:b-token"

    pair_ab = MagicMock(spec=InstancePair)
    pair_ab.id = 1
    pair_ab.source_instance_id = 1
    pair_ab.target_instance_id = 2
    pair_ab.mirror_direction = "push"

    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=mock_result)

    with patch('app.core.issue_sync.GitLabClient'):
        engine_mm1 = IssueSyncEngine(
            db=db, config=mock_config, mirror=mock_mirror,
            source_instance=instance_a, target_instance=instance_b_mm1,
            instance_pair=pair_ab,
        )

    # Verify labels use URL hostnames, not DB IDs
    assert engine_mm1.mirror_from_label == "Mirrored-From::gitlab-a.example.com"
    assert engine_mm1.originated_from_target_label == "Mirrored-From::gitlab-b.example.com"

    # --- MM2: Instance B (id=1 in MM2!) → Instance C (id=2 in MM2) ---
    # Note: B has id=1 in MM2 (same auto-increment starting point, different DB)
    instance_b_mm2 = MagicMock(spec=GitLabInstance)
    instance_b_mm2.id = 1  # Different DB ID, same GitLab instance
    instance_b_mm2.name = "GitLab B"
    instance_b_mm2.url = "https://gitlab-b.example.com"  # Same URL as in MM1
    instance_b_mm2.encrypted_token = "enc:b-token"

    instance_c = MagicMock(spec=GitLabInstance)
    instance_c.id = 2
    instance_c.name = "GitLab C"
    instance_c.url = "https://gitlab-c.example.com"
    instance_c.encrypted_token = "enc:c-token"

    pair_bc = MagicMock(spec=InstancePair)
    pair_bc.id = 1
    pair_bc.source_instance_id = 1
    pair_bc.target_instance_id = 2
    pair_bc.mirror_direction = "push"

    with patch('app.core.issue_sync.GitLabClient'):
        engine_mm2 = IssueSyncEngine(
            db=db, config=mock_config, mirror=mock_mirror,
            source_instance=instance_b_mm2, target_instance=instance_c,
            instance_pair=pair_bc,
        )

    # MM2 uses URL hostnames too — B and C are correctly identified
    assert engine_mm2.mirror_from_label == "Mirrored-From::gitlab-b.example.com"
    assert engine_mm2.originated_from_target_label == "Mirrored-From::gitlab-c.example.com"

    # Issue on B that was mirrored from A (by MM1)
    issue_on_b_from_a = {
        "id": 100, "iid": 10,
        "title": "Issue originally from A",
        "description": "Created on A, mirrored to B",
        "labels": ["bug", "Mirrored-From::gitlab-a.example.com"],
        "web_url": "https://gitlab-b.example.com/group/project/-/issues/10",
    }

    stats = {
        "issues_processed": 0, "issues_created": 0, "issues_updated": 0,
        "issues_skipped": 0, "issues_failed": 0, "errors": [],
    }

    # MM2 syncing B→C should NOT skip this issue
    # (it has Mirrored-From::gitlab-a.example.com, not gitlab-c.example.com)
    with patch.object(engine_mm2, '_create_target_issue', new_callable=AsyncMock) as mock_create:
        await engine_mm2._sync_issue(issue_on_b_from_a, stats)
        assert stats["issues_skipped"] == 0
        assert mock_create.called, "Issue from A should be synced from B to C"


@pytest.mark.asyncio
async def test_multi_instance_label_filtering_in_prepare_labels(mock_config, mock_mirror):
    """Test that Mirrored-From labels from upstream hops are filtered during propagation.

    When syncing B→C, source labels on B may include Mirrored-From::gitlab-a.example.com
    (from A→B sync). This should be filtered out so C only gets
    Mirrored-From::gitlab-b.example.com.
    """
    instance_b = MagicMock(spec=GitLabInstance)
    instance_b.id = 1
    instance_b.url = "https://gitlab-b.example.com"
    instance_b.encrypted_token = "enc:b-token"

    instance_c = MagicMock(spec=GitLabInstance)
    instance_c.id = 2
    instance_c.url = "https://gitlab-c.example.com"
    instance_c.encrypted_token = "enc:c-token"

    pair_bc = MagicMock(spec=InstancePair)
    pair_bc.id = 1
    pair_bc.source_instance_id = 1
    pair_bc.target_instance_id = 2
    pair_bc.mirror_direction = "push"

    db = AsyncMock()

    with patch('app.core.issue_sync.GitLabClient'):
        engine = IssueSyncEngine(
            db=db, config=mock_config, mirror=mock_mirror,
            source_instance=instance_b, target_instance=instance_c,
            instance_pair=pair_bc,
        )

    # Source issue on B has Mirrored-From label from A→B sync
    source_issue = {
        "labels": ["bug", "priority::high", "Mirrored-From::gitlab-a.example.com"],
        "milestone": None, "iteration": None, "epic": None, "assignees": [],
    }

    labels = engine._prepare_labels(source_issue)

    # Should include the current hop's Mirrored-From label
    assert "Mirrored-From::gitlab-b.example.com" in labels
    # Should include regular source labels
    assert "bug" in labels
    assert "priority::high" in labels
    # Should NOT include Mirrored-From labels from previous hops
    assert "Mirrored-From::gitlab-a.example.com" not in labels


# -------------------------------------------------------------------------
# Sync Status Propagation Tests
# -------------------------------------------------------------------------


@pytest.fixture
def make_engine(mock_config, mock_mirror, mock_instances, mock_pair):
    """Create a sync engine with mocked GitLab clients and pre-sync setup bypassed."""
    def _make(source_issues):
        source, target = mock_instances
        db = AsyncMock()
        db.commit = AsyncMock()

        with patch('app.core.issue_sync.GitLabClient'):
            engine = IssueSyncEngine(
                db=db, config=mock_config, mirror=mock_mirror,
                source_instance=source, target_instance=target,
                instance_pair=mock_pair,
            )

        # Bypass pre-sync setup (label loading, label creation) to avoid
        # __self__ attribute errors on mock objects in _execute_gitlab_api_call
        engine._load_target_labels_cache = AsyncMock()
        engine._ensure_mirror_from_label = AsyncMock()

        # Mock _execute_gitlab_api_call for the get_issues call to return our test data
        async def fake_execute(func, name, *args, **kwargs):
            if "fetch_source_issues" in name:
                return source_issues
            return MagicMock()

        engine._execute_gitlab_api_call = AsyncMock(side_effect=fake_execute)

        return engine

    return _make


@pytest.mark.asyncio
async def test_sync_sets_status_success_when_no_failures(mock_config, make_engine):
    """Test that sync sets last_sync_status to 'success' when all issues sync without errors."""
    mock_config.last_sync_at = None
    mock_config.sync_existing_issues = True

    source_issues = [
        {"id": 1, "iid": 1, "title": "Issue 1", "description": "Desc",
         "state": "opened", "labels": [], "web_url": "https://example.com/issues/1"},
    ]
    engine = make_engine(source_issues)

    with patch.object(engine, '_sync_issue', new_callable=AsyncMock) as mock_sync_issue:
        async def fake_sync_issue(issue, stats):
            stats["issues_processed"] += 1
            stats["issues_created"] += 1

        mock_sync_issue.side_effect = fake_sync_issue
        stats = await engine.sync()

    assert stats["issues_created"] == 1
    assert stats["issues_failed"] == 0
    assert mock_config.last_sync_status == "success"
    assert mock_config.last_sync_error is None


@pytest.mark.asyncio
async def test_sync_sets_status_partial_when_some_failures(mock_config, make_engine):
    """Test that sync sets last_sync_status to 'partial' when some issues fail."""
    mock_config.last_sync_at = None
    mock_config.sync_existing_issues = True

    source_issues = [
        {"id": 1, "iid": 1, "title": "Issue 1", "description": "Desc",
         "state": "opened", "labels": [], "web_url": "https://example.com/issues/1"},
        {"id": 2, "iid": 2, "title": "Issue 2", "description": "Desc",
         "state": "opened", "labels": [], "web_url": "https://example.com/issues/2"},
    ]
    engine = make_engine(source_issues)

    call_count = 0

    with patch.object(engine, '_sync_issue', new_callable=AsyncMock) as mock_sync_issue:
        async def fake_sync_issue(issue, stats):
            nonlocal call_count
            call_count += 1
            stats["issues_processed"] += 1
            if call_count == 1:
                stats["issues_created"] += 1
            else:
                raise Exception("GitLab API error")

        mock_sync_issue.side_effect = fake_sync_issue
        stats = await engine.sync()

    assert stats["issues_created"] == 1
    assert stats["issues_failed"] == 1
    assert mock_config.last_sync_status == "partial"


@pytest.mark.asyncio
async def test_sync_sets_status_failed_when_all_fail(mock_config, make_engine):
    """Test that sync sets last_sync_status to 'failed' when all issues fail."""
    mock_config.last_sync_at = None
    mock_config.sync_existing_issues = True

    source_issues = [
        {"id": 1, "iid": 1, "title": "Issue 1", "description": "Desc",
         "state": "opened", "labels": [], "web_url": "https://example.com/issues/1"},
    ]
    engine = make_engine(source_issues)

    with patch.object(engine, '_sync_issue', new_callable=AsyncMock) as mock_sync_issue:
        async def fake_sync_issue(issue, stats):
            stats["issues_processed"] += 1
            raise Exception("GitLab API error")

        mock_sync_issue.side_effect = fake_sync_issue
        stats = await engine.sync()

    assert stats["issues_created"] == 0
    assert stats["issues_failed"] == 1
    assert mock_config.last_sync_status == "failed"
