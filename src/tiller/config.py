from __future__ import annotations

from pathlib import Path
from typing import Any
import json

import yaml

from .models import (
    AgentRuntimeConfig,
    GitHubConfig,
    MemoryConfig,
    ProjectSpec,
    SessionConfig,
    TillerConfig,
    TrackerConfig,
)
from .repo_seed import discover_local_projects


def expand_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def _optional_path(value: str | Path | None) -> Path | None:
    if value is None:
        return None
    return expand_path(value)


def _load_yaml(text: str) -> dict[str, Any]:
    return yaml.safe_load(text) or {}


def _to_plain(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "__dataclass_fields__"):
        return {name: _to_plain(getattr(value, name)) for name in value.__dataclass_fields__}
    if isinstance(value, dict):
        return {k: _to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain(item) for item in value]
    return value


def dump_json(path: Path, payload: Any) -> None:
    serialized = _to_plain(payload)
    if isinstance(serialized, dict):
        tracker_task_id = serialized.get("tracker_task_id")
        if tracker_task_id and "external_task_id" not in serialized:
            serialized["external_task_id"] = tracker_task_id
    path.write_text(json.dumps(serialized, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _merge_projects(
    configured: dict[str, ProjectSpec], local_discovered: dict[str, ProjectSpec]
) -> dict[str, ProjectSpec]:
    merged = dict(local_discovered)
    merged.update(configured)
    return merged


def load_config(path: str | Path) -> TillerConfig:
    config_path = Path(path).expanduser().resolve()
    raw = _load_yaml(config_path.read_text(encoding="utf-8"))

    tracker_raw = raw.get("tracker", {})
    agent_raw = raw.get("agent", {})
    session_raw = raw.get("session", {})
    projects_raw = raw.get("projects", {})
    github_raw = raw.get("github", {})
    memory_raw = raw.get("memory", {})

    projects = {
        name: ProjectSpec(
            name=name,
            url=data["url"],
            default_branch=data.get("default_branch", "main"),
            description=data.get("description"),
            source=data.get("source", "configured"),
            source_path=data.get("source_path"),
        )
        for name, data in projects_raw.items()
    }

    agent = AgentRuntimeConfig(
        default=agent_raw.get("default", "claude-code"),
        model=agent_raw.get("model"),
        adapters_path=_optional_path(agent_raw.get("adapters_path")),
        options=dict(agent_raw.get("options", {})),
    )

    session = SessionConfig(
        base_path=expand_path(session_raw.get("base_path", "~/.tiller/sessions")),
        cleanup_after_hours=session_raw.get("cleanup_after_hours", 24),
        keep_finished_sessions=bool(session_raw.get("keep_finished_sessions", False)),
        repo_store_path=expand_path(session_raw.get("repo_store_path", "~/.tiller/repo-mirrors")),
    )

    local_projects = discover_local_projects(session.repo_store_path)

    return TillerConfig(
        tracker=TrackerConfig(
            type=tracker_raw["type"],
            trigger_status=tracker_raw["trigger_status"],
            poll_interval=int(tracker_raw.get("poll_interval", 30)),
            processing_status=tracker_raw.get("processing_status"),
            done_status=tracker_raw.get("done_status"),
            options={
                k: v
                for k, v in tracker_raw.items()
                if k not in {"type", "trigger_status", "poll_interval", "processing_status", "done_status"}
            },
        ),
        agent=agent,
        projects=_merge_projects(projects, local_projects),
        session=session,
        github=GitHubConfig(
            enabled=bool(github_raw.get("enabled", False)),
            url=github_raw.get("url", "https://api.github.com"),
            token=github_raw.get("token"),
            token_env=github_raw.get("token_env", "GITHUB_API_TOKEN"),
            auth_method=github_raw.get("auth_method", "token"),
            gh_path=github_raw.get("gh_path"),
        ),
        memory=MemoryConfig(
            enabled=bool(memory_raw.get("enabled", False)),
            provider=memory_raw.get("provider", "local"),
            base_path=expand_path(memory_raw.get("base_path", "~/.tiller/memory")),
            project=memory_raw.get("project"),
            llm_provider=memory_raw.get("llm_provider", "openai"),
            llm_model=memory_raw.get("llm_model"),
            llm_api_key=memory_raw.get("llm_api_key"),
            llm_api_key_env=memory_raw.get("llm_api_key_env", "OPENAI_API_KEY"),
        ),
        config_path=config_path,
    )
