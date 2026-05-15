from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..config import dump_json
from .models import EventRecord, MessageRecord, SessionState


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
            state=payload.get("state") or payload.get("status", "stopped"),
            agent_name=payload["agent_name"],
            config_path=Path(payload["config_path"]) if payload.get("config_path") else None,
            process_id=payload.get("process_id"),
            started_at=payload.get("started_at"),
            updated_at=payload.get("updated_at"),
            provisioned_repos=list(payload.get("provisioned_repos", [])),
        )

    def save_session(self, session: SessionState) -> None:
        workspace = Path(session.workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        dump_json(workspace / "session.json", session)

    def append_message(self, workspace: Path, message: MessageRecord) -> None:
        self._append_jsonl(workspace / "messages.jsonl", asdict(message))

    def append_event(self, workspace: Path, event: EventRecord) -> None:
        self._append_jsonl(workspace / "events.jsonl", asdict(event))

    def _append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
