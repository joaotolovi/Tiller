from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..config import dump_json
from .models import EventRecord, ExternalRef, LocalAttachment, MessageRecord, SessionState, TrackerMetaRecord, WorkItemRecord


class WorkspaceRepository:
    def __init__(self, base_path: Path) -> None:
        self.base_path = base_path

    def ensure_workspace(self, internal_task_id: str) -> Path:
        root = self.base_path / internal_task_id
        root.mkdir(parents=True, exist_ok=True)
        return root

    def load_session(self, workspace: Path) -> SessionState | None:
        path = workspace / "session.json"
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return SessionState(
            internal_task_id=payload["internal_task_id"],
            external_task_id=payload["external_task_id"],
            tracker_type=payload["tracker_type"],
            workspace=Path(payload["workspace"]),
            status=payload["status"],
            agent_name=payload["agent_name"],
            config_path=Path(payload["config_path"]) if payload.get("config_path") else None,
            process_id=payload.get("process_id"),
            started_at=payload.get("started_at"),
            updated_at=payload.get("updated_at"),
            completed_at=payload.get("completed_at"),
            resume_count=int(payload.get("resume_count", 0)),
            last_checkpoint=payload.get("last_checkpoint"),
            provisioned_repos=list(payload.get("provisioned_repos", [])),
        )

    def save_session(self, session: SessionState) -> None:
        workspace = Path(session.workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        dump_json(workspace / "session.json", session)

    def load_task(self, workspace: Path) -> WorkItemRecord | None:
        path = workspace / "task.json"
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        external = payload["external"]
        return WorkItemRecord(
            internal_task_id=payload["internal_task_id"],
            external=ExternalRef(
                tracker_type=external["tracker_type"],
                task_id=external["task_id"],
                url=external.get("url"),
            ),
            title=payload.get("title", ""),
            description=payload.get("description", ""),
            source_status=payload.get("source_status", ""),
            internal_status=payload.get("internal_status", ""),
            comments_count=int(payload.get("comments_count", 0)),
            attachments=[
                LocalAttachment(
                    id=str(item.get("id") or ""),
                    name=str(item.get("name") or "attachment"),
                    source_url=item.get("source_url"),
                    local_path=item.get("local_path"),
                )
                for item in payload.get("attachments", [])
            ],
            metadata=dict(payload.get("metadata", {})),
            created_at=payload.get("created_at"),
            updated_at=payload.get("updated_at"),
        )

    def save_task(self, workspace: Path, task: WorkItemRecord) -> None:
        workspace.mkdir(parents=True, exist_ok=True)
        dump_json(workspace / "task.json", task)

    def load_tracker_meta(self, workspace: Path) -> TrackerMetaRecord | None:
        path = workspace / "tracker.json"
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return TrackerMetaRecord(
            tracker_type=payload["tracker_type"],
            external_task_id=payload["external_task_id"],
            source_status=payload.get("source_status", ""),
            last_sync_at=payload.get("last_sync_at"),
            last_comment_published_at=payload.get("last_comment_published_at"),
            capabilities=dict(payload.get("capabilities", {})),
        )

    def save_tracker_meta(self, workspace: Path, payload: TrackerMetaRecord | dict[str, Any]) -> None:
        workspace.mkdir(parents=True, exist_ok=True)
        dump_json(workspace / "tracker.json", payload)

    def append_message(self, workspace: Path, message: MessageRecord) -> None:
        self._append_jsonl(workspace / "messages.jsonl", asdict(message))

    def append_event(self, workspace: Path, event: EventRecord) -> None:
        self._append_jsonl(workspace / "events.jsonl", asdict(event))

    def _append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
