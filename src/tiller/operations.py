from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import load_config
from .github import GitHubClient, PullRequestRef, read_repo_metadata, repo_ref_from_project
from .models import ProjectSpec, SessionPaths, Task
from .runtime import (
    load_session_context,
    load_session_projects,
    project_spec_from_payload,
    save_session_record,
    serialize_task,
    session_manager_for_context,
    session_paths,
)
from .trackers.factory import build_tracker_adapter
from .trackers.sync_factory import build_sync_tracker
from .memory import SessionMemoryService
from .workspace import EventRecord, MessageRecord, WorkspaceRepository


class SessionOperations:
    def __init__(self, session: str | Path | None = None) -> None:
        self.context = load_session_context(session)

    @property
    def root(self) -> Path:
        return self.context.root

    @property
    def task_id(self) -> str:
        return self.context.record.tracker_task_id

    def _config(self):
        return load_config(self.context.config_path)

    def _projects(self) -> dict[str, dict[str, Any]]:
        return load_session_projects(self.context)

    def _project_spec(self, name: str) -> ProjectSpec:
        projects = self._projects()
        if name not in projects:
            raise ValueError(f"Unknown project: {name}")
        return project_spec_from_payload(name, projects[name])

    def tracker_get_task(self) -> dict[str, Any]:
        config = self._config()
        tracker = build_sync_tracker(config.tracker.type, **config.tracker.options)
        task = tracker.get_task(self.task_id)
        return serialize_task(task)

    def tracker_comment(self, text: str) -> dict[str, Any]:
        config = self._config()
        tracker = build_sync_tracker(config.tracker.type, **config.tracker.options)
        tracker.add_comment(self.task_id, text)
        self._append_message(text)
        return {"task_id": self.task_id, "comment": text}

    def tracker_set_status(self, status: str) -> dict[str, Any]:
        config = self._config()
        tracker = build_sync_tracker(config.tracker.type, **config.tracker.options)
        tracker.update_status(self.task_id, status)
        return {"task_id": self.task_id, "status": status}

    def tracker_status_options(self) -> dict[str, Any]:
        config = self._config()
        tracker = build_sync_tracker(config.tracker.type, **config.tracker.options)
        return {"task_id": self.task_id, "statuses": tracker.list_status_options(self.task_id)}

    def tracker_download_attachments(self, dest: str | None = None) -> dict[str, Any]:
        config = self._config()
        tracker = build_sync_tracker(config.tracker.type, **config.tracker.options)
        target = Path(dest).expanduser().resolve() if dest else self.context.root / "attachments"
        downloaded = tracker.download_attachments(self.task_id, target)
        return {"task_id": self.task_id, "files": [str(item) for item in downloaded]}

    def project_list(self) -> dict[str, dict[str, Any]]:
        return self._projects()

    def project_status(self) -> list[dict[str, Any]]:
        provisioned = set(self.context.record.provisioned_repos)
        payload: list[dict[str, Any]] = []
        for name, data in self._projects().items():
            payload.append(
                {
                    "name": name,
                    "path": str(self.context.root / data["repo_path"]),
                    "provisioned": name in provisioned,
                    "url": data["url"],
                    "default_branch": data.get("default_branch", "main"),
                }
            )
        return payload

    def project_use(self, name: str, reason: str | None = None) -> dict[str, Any]:
        projects = self._projects()
        if name not in projects:
            raise ValueError(f"Unknown project: {name}")
        reason = reason or "implementation work"

        config = self._config()
        tracker = build_sync_tracker(config.tracker.type, **config.tracker.options)
        already_provisioned = name in self.context.record.provisioned_repos
        if not already_provisioned:
            if not self.context.record.provisioned_repos:
                tracker.add_comment(self.task_id, f"Requesting access to repo `{name}` for {reason}.")
            else:
                tracker.add_comment(self.task_id, f"Detected dependency on repo `{name}`. Requesting access.")

        spec = project_spec_from_payload(name, projects[name])
        manager = session_manager_for_context(self.context)
        paths = session_paths(self.context)
        repo_path = manager.provision_repo(paths, spec)

        if not already_provisioned:
            self.context.record.provisioned_repos.append(name)
            save_session_record(self.context)

        return {
            "name": name,
            "path": str(repo_path),
            "already_provisioned": already_provisioned,
            "url": spec.url,
            "default_branch": spec.default_branch,
        }

    def github_auth_status(self) -> dict[str, Any]:
        config = self._config()
        client = GitHubClient(config.github)
        try:
            return client.auth_status()
        finally:
            client.close()

    def github_repo_status(self, repo_name: str) -> dict[str, Any]:
        config = self._config()
        client = GitHubClient(config.github)
        try:
            repo_ref = repo_ref_from_project(self._project_spec(repo_name))
            return client.repo_status(repo_ref)
        finally:
            client.close()

    def github_create_pr(
        self,
        *,
        repo_name: str,
        title: str,
        body: str,
        base: str | None = None,
        head: str | None = None,
    ) -> dict[str, Any]:
        config = self._config()
        client = GitHubClient(config.github)
        try:
            projects = self._projects()
            if repo_name not in projects:
                raise ValueError(f"Unknown project: {repo_name}")
            project = project_spec_from_payload(repo_name, projects[repo_name])
            repo_ref = repo_ref_from_project(project)
            repo_path = self.context.root / projects[repo_name]["repo_path"]
            metadata = read_repo_metadata(repo_path)
            resolved_head = head or str(metadata.get("branch") or "")
            if not resolved_head:
                raise ValueError(f"No branch metadata found for project: {repo_name}")
            pull_request = client.create_pull_request(
                repo=repo_ref,
                title=title,
                body=body,
                head=resolved_head,
                base=base or project.default_branch,
            )
            return serialize_pull_request(pull_request)
        finally:
            client.close()

    def github_pr_view(self, *, repo_name: str, number: int) -> dict[str, Any]:
        config = self._config()
        client = GitHubClient(config.github)
        try:
            repo_ref = repo_ref_from_project(self._project_spec(repo_name))
            pull_request = client.get_pull_request(repo=repo_ref, number=number)
            return serialize_pull_request(pull_request)
        finally:
            client.close()

    def session_status(self) -> dict[str, Any]:
        return {
            "internal_task_id": self.context.record.internal_task_id,
            "tracker_task_id": self.context.record.tracker_task_id,
            "agent_name": self.context.record.agent_name,
            "state": self.context.record.state,
            "process_id": self.context.record.process_id,
            "workspace": str(self.context.record.workspace),
            "provisioned_repos": self.context.record.provisioned_repos,
        }

    def session_paths(self) -> dict[str, Any]:
        paths = session_paths(self.context)
        return {
            "root": str(paths.root),
            "agents_md": str(paths.agents_md),
            "task_md": str(paths.task_md),
            "task_json": str(paths.task_json),
            "state_md": str(paths.state_md),
            "projects_json": str(paths.projects_json),
            "repos_dir": str(paths.repos_dir),
            "attachments_dir": str(paths.attachments_dir),
            "session_json": str(paths.state_json),
        }

    def _workspace_repo(self) -> WorkspaceRepository:
        config = self._config()
        return WorkspaceRepository(config.session.base_path)

    def _memory_service(self) -> SessionMemoryService:
        config = self._config()
        if not config.memory.enabled:
            raise ValueError("Memory is disabled in the current configuration")
        return SessionMemoryService.from_context(self.context, config)

    def memory_retain(self, scope: str, content: str, context: str | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._memory_service().retain(scope=scope, content=content, context=context, metadata=metadata)

    def memory_recall(self, query: str, limit: int = 5, scope: str | None = None) -> dict[str, Any]:
        return self._memory_service().recall(query=query, limit=limit, scope=scope)


    def _append_message(self, text: str) -> None:
        self._workspace_repo().append_message(
            self.context.root,
            MessageRecord(
                id=f"msg-local-{uuid.uuid4().hex}",
                direction="outbound",
                channel="tracker",
                message_type="comment",
                author="tiller",
                body=text,
                created_at=datetime.now(UTC).isoformat(),
            ),
        )
        self._workspace_repo().append_event(
            self.context.root,
            EventRecord(
                id=f"evt-local-{uuid.uuid4().hex}",
                type="comment_published",
                created_at=datetime.now(UTC).isoformat(),
                data={"task_id": self.task_id},
            ),
        )


async def tracker_get_task_async(session_root: Path) -> dict[str, Any]:
    payload = json.loads((session_root / ".mcp" / "config.json").read_text(encoding="utf-8"))
    record = json.loads((session_root / "session.json").read_text(encoding="utf-8"))
    tracker = build_tracker_adapter(payload["tracker"]["type"], **payload["tracker"].get("options", {}))
    task = await tracker.get_task(str(record["tracker_task_id"]))
    return serialize_task(task)


async def tracker_comment_async(session_root: Path, text: str) -> dict[str, Any]:
    payload = json.loads((session_root / ".mcp" / "config.json").read_text(encoding="utf-8"))
    record = json.loads((session_root / "session.json").read_text(encoding="utf-8"))
    tracker = build_tracker_adapter(payload["tracker"]["type"], **payload["tracker"].get("options", {}))
    task_id = str(record["tracker_task_id"])
    await tracker.add_comment(task_id, text)
    workspace_repo = WorkspaceRepository(Path(payload["session"]["base_path"]).expanduser().resolve())
    workspace_repo.append_message(
        session_root,
        MessageRecord(
            id=f"msg-local-{uuid.uuid4().hex}",
            direction="outbound",
            channel="tracker",
            message_type="comment",
            author="tiller",
            body=text,
            created_at=datetime.now(UTC).isoformat(),
        ),
    )
    workspace_repo.append_event(
        session_root,
        EventRecord(
            id=f"evt-local-{uuid.uuid4().hex}",
            type="comment_published",
            created_at=datetime.now(UTC).isoformat(),
            data={"task_id": task_id},
        ),
    )
    return {"task_id": task_id, "comment": text}


async def tracker_set_status_async(session_root: Path, status: str) -> dict[str, Any]:
    payload = json.loads((session_root / ".mcp" / "config.json").read_text(encoding="utf-8"))
    record = json.loads((session_root / "session.json").read_text(encoding="utf-8"))
    tracker = build_tracker_adapter(payload["tracker"]["type"], **payload["tracker"].get("options", {}))
    task_id = str(record["tracker_task_id"])
    await tracker.update_status(task_id, status)
    return {"task_id": task_id, "status": status}


async def tracker_download_attachments_async(session_root: Path, dest: str | None = None) -> dict[str, Any]:
    payload = json.loads((session_root / ".mcp" / "config.json").read_text(encoding="utf-8"))
    record = json.loads((session_root / "session.json").read_text(encoding="utf-8"))
    tracker = build_tracker_adapter(payload["tracker"]["type"], **payload["tracker"].get("options", {}))
    task_id = str(record["tracker_task_id"])
    target = Path(dest).expanduser().resolve() if dest else session_root / "attachments"
    downloaded = await tracker.download_attachments(task_id, target)
    return {"task_id": task_id, "files": [str(item) for item in downloaded]}


async def tracker_status_options_async(session_root: Path) -> dict[str, Any]:
    payload = json.loads((session_root / ".mcp" / "config.json").read_text(encoding="utf-8"))
    record = json.loads((session_root / "session.json").read_text(encoding="utf-8"))
    tracker = build_tracker_adapter(payload["tracker"]["type"], **payload["tracker"].get("options", {}))
    task_id = str(record["tracker_task_id"])
    return {"task_id": task_id, "statuses": await tracker.list_status_options(task_id)}


def serialize_pull_request(pull_request: PullRequestRef) -> dict[str, object]:
    return {
        "number": pull_request.number,
        "url": pull_request.url,
        "html_url": pull_request.html_url,
        "title": pull_request.title,
        "head": pull_request.head,
        "base": pull_request.base,
        "state": pull_request.state,
    }
