from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class TaskComment:
    id: str
    author: str | None
    body: str
    created_at: str | None = None


@dataclass(slots=True)
class TaskAttachment:
    id: str
    name: str
    url: str | None = None


@dataclass(slots=True)
class Task:
    id: str
    title: str
    description: str
    status: str
    comments: list[TaskComment] = field(default_factory=list)
    attachments: list[TaskAttachment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ProjectSpec:
    name: str
    url: str
    default_branch: str = "main"


@dataclass(slots=True)
class TrackerConfig:
    type: str
    trigger_status: str
    poll_interval: int = 30
    processing_status: str | None = None
    done_status: str | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentRuntimeConfig:
    default: str
    model: str | None = None
    adapters_path: Path | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SessionConfig:
    base_path: Path
    cleanup_after_hours: int | None = 24
    keep_finished_sessions: bool = False


@dataclass(slots=True)
class GitHubConfig:
    enabled: bool = False
    url: str = "https://api.github.com"
    token: str | None = None
    token_env: str = "GITHUB_API_TOKEN"

    def resolve_token(self) -> str | None:
        if self.token:
            return self.token
        import os

        return os.environ.get(self.token_env)


@dataclass(slots=True)
class MemoryConfig:
    enabled: bool = False
    provider: str = "local"
    base_path: Path = Path("~/.tiller/memory")
    project: str | None = None
    llm_provider: str = "openai"
    llm_model: str | None = None
    llm_api_key: str | None = None
    llm_api_key_env: str = "OPENAI_API_KEY"


@dataclass(slots=True)
class TillerConfig:
    tracker: TrackerConfig
    agent: AgentRuntimeConfig
    projects: dict[str, ProjectSpec]
    session: SessionConfig
    github: GitHubConfig = field(default_factory=GitHubConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)


@dataclass(slots=True)
class SessionPaths:
    root: Path
    agents_md: Path
    task_md: Path
    state_md: Path
    projects_json: Path
    repos_dir: Path
    attachments_dir: Path
    mcp_dir: Path
    mcp_config: Path
    state_json: Path


@dataclass(slots=True)
class SessionRecord:
    internal_task_id: str
    tracker_task_id: str
    agent_name: str
    workspace: Path
    config_path: Path | None = None
    process_id: int | None = None
    started_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None
    state: str = "stopped"
    resume_count: int = 0
    last_checkpoint: str | None = None
    provisioned_repos: list[str] = field(default_factory=list)

    @property
    def external_task_id(self) -> str:
        return self.tracker_task_id


@dataclass(slots=True)
class AgentRunRequest:
    agent_name: str
    workspace: Path
    goal: str
    mcp_config: dict[str, Any]
    model: str | None = None
    env: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class AgentRunResult:
    adapter_name: str
    command: list[str]
    process_id: int
    log_path: Path
    exit_code: int | None = None


@dataclass(slots=True)
class DiscoveredAgent:
    name: str
    command: str
    available: bool
    path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
