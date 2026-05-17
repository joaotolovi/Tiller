from __future__ import annotations

import hashlib
import shutil
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .agents.common import write_native_mcp_project_files
from .config import dump_json
from .mcp import write_mcp_config
from .models import ProjectSpec, SessionPaths, SessionRecord, Task, TillerConfig, TrackerConfig
from .pr_providers import get_pull_request_provider
from .repo_seed import RepoSeedManager
from .templates import render_agents_md, render_task_md
from .trackers import TrackerAdapter
from .workspace import EventRecord, MessageRecord, SessionState, WorkspaceRepository


class SessionManager:
    def __init__(self, config: TillerConfig, tracker: TrackerAdapter, tracker_config: TrackerConfig | None = None) -> None:
        self.config = config
        self.tracker = tracker
        self.tracker_config = tracker_config or config.tracker
        self.config.session.base_path.mkdir(parents=True, exist_ok=True)
        repo_store_path = self.config.session.repo_store_path or (self.config.session.base_path / "repo-mirrors")
        self.repo_seed_manager = RepoSeedManager(
            self.config.session.base_path,
            self.config.github.resolve_token(),
            mirrors_dir=repo_store_path,
        )
        self.workspace_repo = WorkspaceRepository(self.config.session.base_path)

    def make_internal_task_id(self, task: Task) -> str:
        digest_source = f"{self.tracker_config.name}:{task.id}"
        digest = hashlib.sha1(digest_source.encode("utf-8")).hexdigest()[:8]
        return f"TASK-{digest.upper()}"

    def build_paths(self, internal_task_id: str) -> SessionPaths:
        root = self.config.session.base_path / internal_task_id
        return SessionPaths(
            root=root,
            agents_md=root / "AGENTS.md",
            task_md=root / "TASK.md",
            task_json=root / "task.json",
            state_md=root / "STATE.md",
            projects_json=root / "projects.json",
            repos_dir=root / "repos",
            attachments_dir=root / "attachments",
            mcp_dir=root / ".mcp",
            mcp_config=root / ".mcp" / "config.json",
            state_json=root / "session.json",
        )

    async def prepare(self, task: Task, agent_name: str, tool_transport: str = "cli") -> tuple[SessionRecord, SessionPaths, dict[str, Any]]:
        internal_task_id = self.make_internal_task_id(task)
        paths = self.build_paths(internal_task_id)
        existing_record = self._load_existing_record(paths)

        paths.root.mkdir(parents=True, exist_ok=True)
        paths.repos_dir.mkdir(parents=True, exist_ok=True)
        paths.attachments_dir.mkdir(parents=True, exist_ok=True)
        paths.mcp_dir.mkdir(parents=True, exist_ok=True)

        if self.config.memory.enabled and self.config.memory.provider == "local":
            self.config.memory.base_path.mkdir(parents=True, exist_ok=True)

        attachments = await self.tracker.download_attachments(task.id, paths.attachments_dir)
        mcp_payload = write_mcp_config(paths.mcp_config, self.config)
        write_native_mcp_project_files(paths.root, mcp_payload)
        dump_json(paths.projects_json, self._projects_payload())

        task_payload = self._task_payload(task)
        dump_json(paths.task_json, task_payload)
        paths.agents_md.write_text(
            render_agents_md(
                tool_transport=tool_transport,
                memory_enabled=self.config.memory.enabled,
                memory_provider=self.config.memory.provider,
                memory_base_path=self.config.memory.base_path,
                pr_tool_enabled=get_pull_request_provider(self.config) is not None,
            ),
            encoding="utf-8",
        )
        paths.task_md.write_text(render_task_md(task_payload), encoding="utf-8")
        self._ensure_state_md(paths, task, existing_record is not None)

        now = datetime.now(UTC).isoformat()
        if existing_record is not None:
            record = existing_record
            record.tracker_name = self.tracker_config.name
            record.tracker_type = self.tracker_config.type
            record.agent_name = agent_name
            record.workspace = paths.root
            record.config_path = self.config.config_path
            record.state = "prepared"
            record.updated_at = now
            if record.started_at is None:
                record.started_at = now
        else:
            record = SessionRecord(
                internal_task_id=internal_task_id,
                tracker_name=self.tracker_config.name,
                tracker_type=self.tracker_config.type,
                tracker_task_id=task.id,
                agent_name=agent_name,
                workspace=paths.root,
                config_path=self.config.config_path,
                started_at=now,
                updated_at=now,
                state="prepared",
            )

        dump_json(paths.state_json, record)
        self._write_workspace_records(paths, record, task, resumed=existing_record is not None)
        self._record_agent_ready(paths, record, task, attachments, resumed=existing_record is not None)
        return record, paths, mcp_payload

    def cleanup(self, paths: SessionPaths) -> None:
        if self.config.session.keep_finished_sessions:
            return
        for repo_name in self._load_provisioned_repo_names(paths):
            self.repo_seed_manager.cleanup(paths.repos_dir / repo_name)
        shutil.rmtree(paths.root, ignore_errors=True)

    def _projects_payload(self) -> dict[str, Any]:
        return {
            name: {
                **asdict(spec),
                "repo_path": f"repos/{spec.name}",
                "provision_method": "seed_copy",
                "available": True,
            }
            for name, spec in self.config.projects.items()
        }

    def _task_payload(self, task: Task) -> dict[str, Any]:
        return {
            "id": task.id,
            "tracker_name": self.tracker_config.name,
            "tracker_type": self.tracker_config.type,
            "title": task.title,
            "description": task.description,
            "status": task.status,
            "comments": [asdict(comment) for comment in task.comments],
            "attachments": [
                {
                    "id": attachment.id,
                    "name": attachment.name,
                    "url": attachment.url,
                }
                for attachment in task.attachments
            ],
            "metadata": task.metadata,
        }

    def _load_provisioned_repo_names(self, paths: SessionPaths) -> list[str]:
        if not paths.state_json.exists():
            return []
        payload = __import__("json").loads(paths.state_json.read_text(encoding="utf-8"))
        return list(payload.get("provisioned_repos", []))

    def _load_existing_record(self, paths: SessionPaths) -> SessionRecord | None:
        if not paths.state_json.exists():
            return None
        payload = __import__("json").loads(paths.state_json.read_text(encoding="utf-8"))
        return SessionRecord(
            internal_task_id=payload["internal_task_id"],
            tracker_name=payload.get("tracker_name", "default"),
            tracker_type=payload.get("tracker_type", self.tracker_config.type),
            tracker_task_id=payload.get("tracker_task_id") or payload["external_task_id"],
            agent_name=payload["agent_name"],
            workspace=Path(payload["workspace"]),
            config_path=Path(payload["config_path"]) if payload.get("config_path") else None,
            process_id=payload.get("process_id"),
            started_at=payload.get("started_at"),
            updated_at=payload.get("updated_at"),
            state=payload.get("state") or payload.get("status", "prepared"),
            provisioned_repos=list(payload.get("provisioned_repos", [])),
        )

    def _ensure_state_md(self, paths: SessionPaths, task: Task, resumed: bool) -> None:
        if paths.state_md.exists():
            return
        paths.state_md.write_text(self._initial_state_md(task, resumed=resumed), encoding="utf-8")

    def _initial_state_md(self, task: Task, *, resumed: bool) -> str:
        return f"""# STATE

## Session
- task_id: {task.id}
- title: {task.title}
- mode: {'resume' if resumed else 'prepare'}

## Current understanding
- To be filled by the agent.

## Decisions made
- None yet.

## Repositories in use
- None yet.

## Completed work
- Nothing recorded yet.

## Relevant blockers
- None.

## Next step
- Read `TASK.md` and `STATE.md` before proceeding.
"""

    def _write_workspace_records(self, paths: SessionPaths, record: SessionRecord, task: Task, *, resumed: bool) -> None:
        now = datetime.now(UTC).isoformat()
        self.workspace_repo.save_session(
            SessionState(
                internal_task_id=record.internal_task_id,
                tracker_name=record.tracker_name,
                tracker_task_id=record.tracker_task_id,
                tracker_type=record.tracker_type,
                workspace=paths.root,
                state=record.state,
                agent_name=record.agent_name,
                config_path=record.config_path,
                process_id=record.process_id,
                started_at=record.started_at,
                updated_at=record.updated_at or now,
                provisioned_repos=list(record.provisioned_repos),
            )
        )
        for comment in task.comments:
            self.workspace_repo.append_message(
                paths.root,
                MessageRecord(
                    id=comment.id or f"msg-{uuid.uuid4().hex}",
                    direction="inbound",
                    channel="tracker",
                    message_type="comment",
                    author=comment.author,
                    body=comment.body,
                    created_at=comment.created_at or now,
                    external_message_id=comment.id or None,
                ),
            )

    def _record_agent_ready(
        self,
        paths: SessionPaths,
        record: SessionRecord,
        task: Task,
        attachments: list[Path],
        *,
        resumed: bool,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        self.workspace_repo.append_event(
            paths.root,
            EventRecord(
                id=f"evt-{uuid.uuid4().hex}",
                type="agent_ready",
                created_at=now,
                data={
                    "tracker_name": record.tracker_name,
                    "tracker_task_id": task.id,
                    "tracker_type": record.tracker_type,
                    "workspace": str(paths.root),
                    "resumed": resumed,
                },
            ),
        )

    def provision_repo(self, paths: SessionPaths, project: ProjectSpec, branch_name: str | None = None) -> Path:
        return self.repo_seed_manager.provision(paths=paths, project=project, branch_name=branch_name)
