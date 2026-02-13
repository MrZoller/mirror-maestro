from datetime import datetime
from typing import Optional
from sqlalchemy import String, Boolean, Integer, DateTime, Text, JSON, UniqueConstraint, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all database models."""
    pass


class User(Base):
    """Represents an application user."""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), unique=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class GitLabInstance(Base):
    """Represents a GitLab instance configuration."""
    __tablename__ = "gitlab_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    encrypted_token: Mapped[str] = mapped_column(Text, nullable=False)
    # Best-effort: user identity of the stored API token (for friendly display / defaults).
    api_user_id: Mapped[Optional[int]] = mapped_column(Integer)
    api_username: Mapped[Optional[str]] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(String(500))
    # GitLab instance version/edition (best-effort, fetched on creation and refresh)
    gitlab_version: Mapped[Optional[str]] = mapped_column(String(50))
    gitlab_edition: Mapped[Optional[str]] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class InstancePair(Base):
    """Represents a pair of GitLab instances for mirroring."""
    __tablename__ = "instance_pairs"
    __table_args__ = (
        Index('idx_pair_source_instance', 'source_instance_id'),
        Index('idx_pair_target_instance', 'target_instance_id'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    source_instance_id: Mapped[int] = mapped_column(Integer, nullable=False)
    target_instance_id: Mapped[int] = mapped_column(Integer, nullable=False)

    # Default mirror settings for this pair
    mirror_direction: Mapped[str] = mapped_column(String(10), default="pull", nullable=False)  # "pull" or "push"
    mirror_overwrite_diverged: Mapped[bool] = mapped_column(Boolean, default=False)
    mirror_trigger_builds: Mapped[bool] = mapped_column(Boolean, default=False)
    only_mirror_protected_branches: Mapped[bool] = mapped_column(Boolean, default=False)
    # Additional GitLab UI mirror settings
    mirror_branch_regex: Mapped[Optional[str]] = mapped_column(String(255))

    # Issue sync default for mirrors in this pair
    issue_sync_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    description: Mapped[Optional[str]] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Mirror(Base):
    """Represents a mirror configuration between two GitLab projects."""
    __tablename__ = "mirrors"
    __table_args__ = (
        UniqueConstraint('instance_pair_id', 'source_project_id', 'target_project_id',
                         name='uq_mirror_pair_projects'),
        Index('idx_mirror_instance_pair', 'instance_pair_id'),
        Index('idx_mirror_last_update_status', 'last_update_status'),
        Index('idx_mirror_updated_at', 'updated_at'),
        Index('idx_mirror_source_path', 'source_project_path'),  # For group filtering and search
        Index('idx_mirror_target_path', 'target_project_path'),  # For group filtering and search
        Index('idx_mirror_enabled', 'enabled'),  # For filtering by enabled status
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_pair_id: Mapped[int] = mapped_column(Integer, nullable=False)

    # Project information
    source_project_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_project_path: Mapped[str] = mapped_column(String(500), nullable=False)
    target_project_id: Mapped[int] = mapped_column(Integer, nullable=False)
    target_project_path: Mapped[str] = mapped_column(String(500), nullable=False)

    # Mirror settings (can override instance pair defaults, except direction which is pair-only)
    mirror_overwrite_diverged: Mapped[Optional[bool]] = mapped_column(Boolean)
    mirror_trigger_builds: Mapped[Optional[bool]] = mapped_column(Boolean)
    only_mirror_protected_branches: Mapped[Optional[bool]] = mapped_column(Boolean)
    mirror_branch_regex: Mapped[Optional[str]] = mapped_column(String(255))
    issue_sync_enabled: Mapped[Optional[bool]] = mapped_column(Boolean)

    # Status tracking
    mirror_id: Mapped[Optional[int]] = mapped_column(Integer)  # GitLab mirror ID
    last_successful_update: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_update_at: Mapped[Optional[datetime]] = mapped_column(DateTime)  # Last update attempt
    last_update_status: Mapped[Optional[str]] = mapped_column(String(50))
    last_error: Mapped[Optional[str]] = mapped_column(Text)  # Error message from GitLab
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    # Project access token for mirror authentication (auto-managed)
    # Token is created on the "remote" project: target for push, source for pull
    encrypted_mirror_token: Mapped[Optional[str]] = mapped_column(Text)
    mirror_token_name: Mapped[Optional[str]] = mapped_column(String(100))
    mirror_token_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    gitlab_token_id: Mapped[Optional[int]] = mapped_column(Integer)  # GitLab's token ID for rotation/deletion
    # Which project has the token (needed for token management)
    token_project_id: Mapped[Optional[int]] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MirrorIssueConfig(Base):
    """Configuration for issue mirroring on a repository mirror."""
    __tablename__ = "mirror_issue_configs"
    __table_args__ = (
        Index('idx_mirror_issue_config_mirror', 'mirror_id'),
        Index('idx_mirror_issue_config_next_sync', 'next_sync_at'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mirror_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)

    # Issue sync settings
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    # What to sync
    sync_comments: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_labels: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_attachments: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_weight: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_time_estimate: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_time_spent: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_closed_issues: Mapped[bool] = mapped_column(Boolean, default=False)

    # Sync behavior
    update_existing: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_existing_issues: Mapped[bool] = mapped_column(Boolean, default=False)

    # Sync state
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_sync_status: Mapped[Optional[str]] = mapped_column(String(50))
    last_sync_error: Mapped[Optional[str]] = mapped_column(Text)
    next_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Polling interval (minutes)
    sync_interval_minutes: Mapped[int] = mapped_column(Integer, default=15)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class IssueMapping(Base):
    """Tracks which issues correspond across instances."""
    __tablename__ = "issue_mappings"
    __table_args__ = (
        Index('idx_issue_mappings_source', 'source_project_id', 'source_issue_iid'),
        Index('idx_issue_mappings_target', 'target_project_id', 'target_issue_iid'),
        Index('idx_issue_mappings_sync_status', 'sync_status'),
        UniqueConstraint('mirror_issue_config_id', 'source_issue_id', name='uq_issue_mapping_source'),
        UniqueConstraint('mirror_issue_config_id', 'target_issue_id', name='uq_issue_mapping_target'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mirror_issue_config_id: Mapped[int] = mapped_column(Integer, nullable=False)

    # Source issue info
    source_issue_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_issue_iid: Mapped[int] = mapped_column(Integer, nullable=False)
    source_project_id: Mapped[int] = mapped_column(Integer, nullable=False)

    # Target issue info
    target_issue_id: Mapped[int] = mapped_column(Integer, nullable=False)
    target_issue_iid: Mapped[int] = mapped_column(Integer, nullable=False)
    target_project_id: Mapped[int] = mapped_column(Integer, nullable=False)

    # Sync tracking
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    source_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    target_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    sync_status: Mapped[str] = mapped_column(String(50), default='synced')
    sync_error: Mapped[Optional[str]] = mapped_column(Text)

    # Hash of source content for change detection
    source_content_hash: Mapped[Optional[str]] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CommentMapping(Base):
    """Tracks comment correspondence across instances."""
    __tablename__ = "comment_mappings"
    __table_args__ = (
        Index('idx_comment_mappings_issue', 'issue_mapping_id'),
        UniqueConstraint('issue_mapping_id', 'source_note_id', name='uq_comment_mapping_source'),
        UniqueConstraint('issue_mapping_id', 'target_note_id', name='uq_comment_mapping_target'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    issue_mapping_id: Mapped[int] = mapped_column(Integer, nullable=False)

    source_note_id: Mapped[int] = mapped_column(Integer, nullable=False)
    target_note_id: Mapped[int] = mapped_column(Integer, nullable=False)

    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    source_content_hash: Mapped[Optional[str]] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class LabelMapping(Base):
    """Custom label mappings across instances."""
    __tablename__ = "label_mappings"
    __table_args__ = (
        UniqueConstraint('mirror_issue_config_id', 'source_label_name', name='uq_label_mapping_source'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mirror_issue_config_id: Mapped[int] = mapped_column(Integer, nullable=False)

    source_label_name: Mapped[str] = mapped_column(String(255), nullable=False)
    target_label_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Strategy: 'exact' (same name), 'mapped' (explicit mapping), 'skip' (don't sync this label)
    mapping_strategy: Mapped[str] = mapped_column(String(20), default='exact')

    # If target label doesn't exist, should we create it?
    auto_create: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AttachmentMapping(Base):
    """Tracks uploaded file correspondence."""
    __tablename__ = "attachment_mappings"
    __table_args__ = (
        Index('idx_attachment_mappings_issue', 'issue_mapping_id'),
        Index('idx_attachment_mappings_comment', 'comment_mapping_id'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    issue_mapping_id: Mapped[Optional[int]] = mapped_column(Integer)
    comment_mapping_id: Mapped[Optional[int]] = mapped_column(Integer)

    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    target_url: Mapped[str] = mapped_column(Text, nullable=False)

    filename: Mapped[Optional[str]] = mapped_column(String(500))
    content_type: Mapped[Optional[str]] = mapped_column(String(100))
    file_size: Mapped[Optional[int]] = mapped_column(Integer)

    uploaded_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class IssueSyncJob(Base):
    """Track sync jobs for async processing."""
    __tablename__ = "issue_sync_jobs"
    __table_args__ = (
        Index('idx_sync_jobs_status', 'status'),
        Index('idx_sync_jobs_config', 'mirror_issue_config_id', 'created_at'),
        # Index for bidirectional conflict detection - find running syncs by project+instance
        # Instance IDs are needed because project IDs are only unique per GitLab instance
        Index('idx_sync_jobs_projects', 'source_project_id', 'target_project_id',
              'source_instance_id', 'target_instance_id', 'status'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mirror_issue_config_id: Mapped[int] = mapped_column(Integer, nullable=False)

    job_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default='pending')

    # Project and instance tracking for bidirectional sync conflict detection
    # Instance IDs are needed because project IDs are only unique per GitLab instance
    source_project_id: Mapped[Optional[int]] = mapped_column(Integer)
    target_project_id: Mapped[Optional[int]] = mapped_column(Integer)
    source_instance_id: Mapped[Optional[int]] = mapped_column(Integer)
    target_instance_id: Mapped[Optional[int]] = mapped_column(Integer)

    # Job parameters (JSON)
    parameters: Mapped[Optional[dict]] = mapped_column(JSON)

    # Results
    issues_processed: Mapped[int] = mapped_column(Integer, default=0)
    issues_created: Mapped[int] = mapped_column(Integer, default=0)
    issues_updated: Mapped[int] = mapped_column(Integer, default=0)
    issues_failed: Mapped[int] = mapped_column(Integer, default=0)
    error_details: Mapped[Optional[dict]] = mapped_column(JSON)

    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # For idempotency
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(255), unique=True)
