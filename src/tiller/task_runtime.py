from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from .models import SessionRecord, Task, TillerConfig
from .session import SessionManager
from .trackers import TrackerAdapter
from .workspace import EventRecord, MessageRecord, SessionState


class TaskRuntime:
    def __init__(self, *, config: TillerConfig, tracker: TrackerAdapter, session_manager: SessionManager) -> None:
        self.config = config
        self.tracker = tracker
        self.session_manager = session_manager

    async def claim_task(self, task_id: str) -> None:
        if self.config.tracker.processing_status:
            await self.tracker.update_status(task_id, self.config.tracker.processing_status)

    async def publish_comment(self, *, task: Task, text: str, workspace: Path | None = None) -> None:
        await self.tracker.add_comment(task.id, text)
        if workspace is None:
            workspace = self._workspace_for_task(task)
        self.session_manager.workspace_repo.append_message(
            workspace,
            MessageRecord(
                id=f"msg-{uuid.uuid4().hex}",
                direction="outbound",
                channel="tracker",
                message_type="comment",
                author="tiller",
                body=text,
                created_at=self._now(),
            ),
        )
        self.session_manager.workspace_repo.append_event(
            workspace,
            EventRecord(
                id=f"evt-{uuid.uuid4().hex}",
                type="comment_published",
                created_at=self._now(),
                data={"task_id": task.id},
            ),
        )

    def mark_agent_started(self, *, record: SessionRecord, workspace: Path, process_id: int, adapter_name: str) -> None:
        now = self._now()
        self.session_manager.workspace_repo.save_session(
            SessionState(
                internal_task_id=record.internal_task_id,
                tracker_task_id=record.tracker_task_id,
                tracker_type=self.config.tracker.type,
                workspace=workspace,
                state="running",
                agent_name=record.agent_name,
                config_path=record.config_path,
                process_id=process_id,
                started_at=record.started_at,
                updated_at=now,
                provisioned_repos=list(record.provisioned_repos),
            )
        )
        self.session_manager.workspace_repo.append_event(
            workspace,
            EventRecord(
                id=f"evt-{uuid.uuid4().hex}",
                type="agent_started",
                created_at=now,
                data={"task_id": record.tracker_task_id, "process_id": process_id, "adapter": adapter_name},
            ),
        )

    async def finalize_session(self, *, task: Task, record: SessionRecord, workspace: Path, exit_code: int) -> str:
        now = self._now()
        state = "completed" if exit_code == 0 else "failed"
        self.session_manager.workspace_repo.save_session(
            SessionState(
                internal_task_id=record.internal_task_id,
                tracker_task_id=record.tracker_task_id,
                tracker_type=self.config.tracker.type,
                workspace=workspace,
                state=state,
                agent_name=record.agent_name,
                config_path=record.config_path,
                process_id=None,
                started_at=record.started_at,
                updated_at=now,
                provisioned_repos=list(record.provisioned_repos),
            )
        )
        self.session_manager.workspace_repo.append_event(
            workspace,
            EventRecord(
                id=f"evt-{uuid.uuid4().hex}",
                type="agent_stopped",
                created_at=now,
                data={"task_id": task.id, "exit_code": exit_code, "state": state},
            ),
        )
        final_comment = (
            "Task completed by Tiller. Check the session logs and the agent comments/PRs for the final summary."
            if exit_code == 0
            else f"Task finished with an agent failure (exit code {exit_code}). Check the session logs at `{workspace}`."
        )
        await self.publish_comment(task=task, text=final_comment, workspace=workspace)
        return state

    def now(self) -> str:
        return self._now()

    def _workspace_for_task(self, task: Task) -> Path:
        return self.config.session.base_path / self.session_manager.make_internal_task_id(task)

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()
