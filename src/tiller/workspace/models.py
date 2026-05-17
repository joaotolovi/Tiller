from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class SessionState:
    internal_task_id: str
    tracker_name: str
    tracker_task_id: str
    tracker_type: str
    workspace: Path
    state: str
    agent_name: str
    config_path: Path | None = None
    process_id: int | None = None
    started_at: str | None = None
    updated_at: str | None = None
    provisioned_repos: list[str] = field(default_factory=list)

    @property
    def external_task_id(self) -> str:
        return self.tracker_task_id

    @property
    def external_task_ref(self) -> str:
        return f"{self.tracker_name}:{self.tracker_task_id}"


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
