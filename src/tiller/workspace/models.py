from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ExternalRef:
    tracker_type: str
    task_id: str
    url: str | None = None


@dataclass(slots=True)
class LocalAttachment:
    id: str
    name: str
    source_url: str | None = None
    local_path: str | None = None


@dataclass(slots=True)
class WorkItemRecord:
    internal_task_id: str
    external: ExternalRef
    title: str
    description: str
    source_status: str
    state: str
    comments_count: int = 0
    attachments: list[LocalAttachment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(slots=True)
class SessionState:
    internal_task_id: str
    external_task_id: str
    tracker_type: str
    workspace: Path
    state: str
    agent_name: str
    config_path: Path | None = None
    process_id: int | None = None
    started_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None
    resume_count: int = 0
    last_checkpoint: str | None = None
    provisioned_repos: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TrackerMetaRecord:
    tracker_type: str
    external_task_id: str
    source_status: str
    last_sync_at: str | None = None
    last_comment_published_at: str | None = None
    capabilities: dict[str, bool] = field(default_factory=dict)


@dataclass(slots=True)
class MessageRecord:
    id: str
    direction: str
    channel: str
    message_type: str
    author: str | None
    body: str
    created_at: str
    external_message_id: str | None = None


@dataclass(slots=True)
class EventRecord:
    id: str
    type: str
    created_at: str
    data: dict[str, Any] = field(default_factory=dict)
