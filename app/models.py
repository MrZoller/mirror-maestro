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

    # Status tracking
    mirror_id: Mapped[Optional[int]] = mapped_column(Integer)  # GitLab mirror ID
    last_successful_update: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_update_status: Mapped[Optional[str]] = mapped_column(String(50))
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
