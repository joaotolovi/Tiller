from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .config import dump_json, load_config
from .models import ProjectSpec, SessionPaths, SessionRecord, Task
from .session import SessionManager
from .trackers import build_tracker_adapter


class SessionContext:
    def __init__(self, root: Path, record: SessionRecord, config_path: Path) -> None:
        self.root = root
        self.record = record
        self.config_path = config_path


def resolve_session_root(explicit: str | Path | None = None, *, cwd: Path | None = None) -> Path:
    if explicit is not None:
        root = Path(explicit).expanduser().resolve()
        if not (root / "session.json").exists():
            raise FileNotFoundError(f"Session not found at {root}")
        return root

    current = (cwd or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "session.json").exists():
            return candidate
    raise FileNotFoundError("Could not locate a Tiller session from the current directory")


def load_session_context(explicit: str | Path | None = None, *, cwd: Path | None = None) -> SessionContext:
    root = resolve_session_root(explicit, cwd=cwd)
    payload = json.loads((root / "session.json").read_text(encoding="utf-8"))
    config_path = Path(payload["config_path"]).expanduser().resolve()
    record = SessionRecord(
        internal_task_id=payload["internal_task_id"],
        tracker_task_id=payload["tracker_task_id"],
        agent_name=payload["agent_name"],
        workspace=Path(payload["workspace"]),
        config_path=config_path,
        process_id=payload.get("process_id"),
        started_at=payload.get("started_at"),
        status=payload.get("status", "created"),
        provisioned_repos=list(payload.get("provisioned_repos", [])),
    )
    return SessionContext(root=root, record=record, config_path=config_path)


def session_paths(context: SessionContext) -> SessionPaths:
    root = context.root
    return SessionPaths(
        root=root,
        agents_md=root / "AGENTS.md",
        task_md=root / "TASK.md",
        state_md=root / "STATE.md",
        projects_json=root / "projects.json",
        repos_dir=root / "repos",
        attachments_dir=root / "attachments",
        mcp_dir=root / ".mcp",
        mcp_config=root / ".mcp" / "config.json",
        state_json=root / "session.json",
    )


def load_session_projects(context: SessionContext) -> dict[str, dict[str, Any]]:
    return json.loads((context.root / "projects.json").read_text(encoding="utf-8"))


def save_session_record(context: SessionContext) -> None:
    dump_json(context.root / "session.json", context.record)


def tracker_for_context(context: SessionContext):
    config = load_config(context.config_path)
    return build_tracker_adapter(config.tracker.type, **config.tracker.options)


def session_manager_for_context(context: SessionContext) -> SessionManager:
    config = load_config(context.config_path)
    tracker = build_tracker_adapter(config.tracker.type, **config.tracker.options)
    return SessionManager(config, tracker)


def project_spec_from_payload(name: str, payload: dict[str, Any]) -> ProjectSpec:
    return ProjectSpec(
        name=name,
        url=payload["url"],
        default_branch=payload.get("default_branch", "main"),
    )


def serialize_task(task: Task) -> dict[str, Any]:
    return asdict(task)
