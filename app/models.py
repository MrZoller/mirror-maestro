from datetime import datetime
from typing import Optional
from sqlalchemy import String, Boolean, Integer, DateTime, Text, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all database models."""
    pass


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

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    source_instance_id: Mapped[int] = mapped_column(Integer, nullable=False)
    target_instance_id: Mapped[int] = mapped_column(Integer, nullable=False)

    # Default mirror settings for this pair
    mirror_direction: Mapped[str] = mapped_column(String(10), default="pull")  # "pull" or "push"
    mirror_protected_branches: Mapped[bool] = mapped_column(Boolean, default=True)
    mirror_overwrite_diverged: Mapped[bool] = mapped_column(Boolean, default=False)
    mirror_trigger_builds: Mapped[bool] = mapped_column(Boolean, default=False)
    only_mirror_protected_branches: Mapped[bool] = mapped_column(Boolean, default=False)
    # Additional GitLab UI mirror settings
    mirror_branch_regex: Mapped[Optional[str]] = mapped_column(String(255))
    mirror_user_id: Mapped[Optional[int]] = mapped_column(Integer)

    description: Mapped[Optional[str]] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Mirror(Base):
    """Represents a mirror configuration between two GitLab projects."""
    __tablename__ = "mirrors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_pair_id: Mapped[int] = mapped_column(Integer, nullable=False)

    # Project information
    source_project_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_project_path: Mapped[str] = mapped_column(String(500), nullable=False)
    target_project_id: Mapped[int] = mapped_column(Integer, nullable=False)
    target_project_path: Mapped[str] = mapped_column(String(500), nullable=False)

    # Mirror settings (can override instance pair defaults)
    mirror_direction: Mapped[Optional[str]] = mapped_column(String(10))
    mirror_protected_branches: Mapped[Optional[bool]] = mapped_column(Boolean)
    mirror_overwrite_diverged: Mapped[Optional[bool]] = mapped_column(Boolean)
    mirror_trigger_builds: Mapped[Optional[bool]] = mapped_column(Boolean)
    only_mirror_protected_branches: Mapped[Optional[bool]] = mapped_column(Boolean)
    mirror_branch_regex: Mapped[Optional[str]] = mapped_column(String(255))
    mirror_user_id: Mapped[Optional[int]] = mapped_column(Integer)

    # Status tracking
    mirror_id: Mapped[Optional[int]] = mapped_column(Integer)  # GitLab mirror ID
    last_successful_update: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_update_status: Mapped[Optional[str]] = mapped_column(String(50))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class GroupAccessToken(Base):
    """Stores encrypted group access tokens for mirroring."""
    __tablename__ = "group_access_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gitlab_instance_id: Mapped[int] = mapped_column(Integer, nullable=False)
    group_path: Mapped[str] = mapped_column(String(500), nullable=False)
    encrypted_token: Mapped[str] = mapped_column(Text, nullable=False)
    token_name: Mapped[str] = mapped_column(String(100), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class GroupMirrorDefaults(Base):
    """
    Group-level mirror default overrides for a specific instance pair.

    These settings sit between:
      per-mirror overrides (Mirror.*) -> group overrides (this table) -> pair defaults (InstancePair.*)

    `group_path` is the GitLab namespace path (e.g. "platform/core") that matches
    the namespace portion of `path_with_namespace` (project name excluded).
    """
    __tablename__ = "group_mirror_defaults"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_pair_id: Mapped[int] = mapped_column(Integer, nullable=False)
    group_path: Mapped[str] = mapped_column(String(500), nullable=False)

    # Optional overrides (None => inherit from pair defaults)
    mirror_direction: Mapped[Optional[str]] = mapped_column(String(10))  # "pull" or "push"
    mirror_protected_branches: Mapped[Optional[bool]] = mapped_column(Boolean)
    mirror_overwrite_diverged: Mapped[Optional[bool]] = mapped_column(Boolean)
    mirror_trigger_builds: Mapped[Optional[bool]] = mapped_column(Boolean)
    only_mirror_protected_branches: Mapped[Optional[bool]] = mapped_column(Boolean)
    mirror_branch_regex: Mapped[Optional[str]] = mapped_column(String(255))
    mirror_user_id: Mapped[Optional[int]] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
