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
    """Test mirror-from label generation."""
    label = get_mirror_from_label(123)
    assert label == "Mirrored-From::instance-123"


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

        # Should include Mirrored-From label
        assert "Mirrored-From::instance-1" in labels

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
        # Target instance ID is 2, so label should be Mirrored-From::instance-2
        assert engine.originated_from_target_label == "Mirrored-From::instance-2"

        # Source issue that originated from target (has the target's Mirrored-From label)
        # This simulates an issue on source that was originally mirrored from target
        source_issue_from_target = {
            "id": 500,
            "iid": 50,
            "title": "Issue that came from target",
            "description": "This issue was synced from target to source",
            "labels": ["bug", "Mirrored-From::instance-2"],  # Has target's label
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

        # Test: Issue with target's Mirrored-From label should be skipped
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

    An issue with Mirrored-From::instance-3 (a third instance) should still be synced,
    only Mirrored-From::instance-{target_id} should be skipped.
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
            "labels": ["bug", "Mirrored-From::instance-3"],  # Different instance ID
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

            # Should NOT be skipped - the label is for instance-3, not instance-2 (target)
            assert stats["issues_processed"] == 1
            assert stats["issues_skipped"] == 0
            # Should proceed to create since no mapping exists
            assert mock_create.called
