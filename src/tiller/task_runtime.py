from __future__ import annotations

import traceback
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .models import SessionRecord, Task, TillerConfig, TrackerConfig
from .session import SessionManager
from .trackers import TrackerAdapter
from .workspace import EventRecord, MessageRecord, SessionState


class TaskRuntime:
    def __init__(self, *, config: TillerConfig, tracker: TrackerAdapter, session_manager: SessionManager, tracker_config: TrackerConfig | None = None) -> None:
        self.config = config
        self.tracker = tracker
        self.session_manager = session_manager
        self.tracker_config = tracker_config or config.tracker

    async def claim_task(self, task_id: str) -> None:
        if self.tracker_config.processing_status:
            await self.tracker.update_status(task_id, self.tracker_config.processing_status)

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
            self.session_state(record=record, workspace=workspace, state="running", process_id=process_id, updated_at=now)
        )
        self.session_manager.workspace_repo.append_event(
            workspace,
            EventRecord(
                id=f"evt-{uuid.uuid4().hex}",
                type="agent_started",
                created_at=now,
                data={
                    "tracker_name": record.tracker_name,
                    "task_id": record.tracker_task_id,
                    "process_id": process_id,
                    "adapter": adapter_name,
                },
            ),
        )

    async def finalize_session(self, *, task: Task, record: SessionRecord, workspace: Path, exit_code: int) -> str:
        now = self._now()
        state = "completed" if exit_code == 0 else "failed"
        self.session_manager.workspace_repo.save_session(
            self.session_state(record=record, workspace=workspace, state=state, process_id=None, updated_at=now)
        )
        self.session_manager.workspace_repo.append_event(
            workspace,
            EventRecord(
                id=f"evt-{uuid.uuid4().hex}",
                type="agent_stopped",
                created_at=now,
                data={
                    "tracker_name": record.tracker_name,
                    "task_id": task.id,
                    "exit_code": exit_code,
                    "state": state,
                },
            ),
        )
        final_comment = (
            "Task completed by Tiller. Check the session logs and the agent comments/PRs for the final summary."
            if exit_code == 0
            else f"Task finished with an agent failure (exit code {exit_code}). Check the session logs at `{workspace}`."
        )
        await self.publish_comment(task=task, text=final_comment, workspace=workspace)
        return state

    async def fail_session(self, *, task: Task, error: BaseException, record: SessionRecord | None = None, workspace: Path | None = None) -> None:
        now = self._now()
        detail = str(error).strip() or error.__class__.__name__
        final_comment = (
            "Task failed before completion. "
            f"Error: {error.__class__.__name__}: {detail}."
        )
        if workspace is not None:
            final_comment += f" Check the session logs at `{workspace}`."
        await self.publish_comment(task=task, text=final_comment, workspace=workspace)
        if record is None or workspace is None:
            return
        self.session_manager.workspace_repo.save_session(
            self.session_state(record=record, workspace=workspace, state="failed", process_id=None, updated_at=now)
        )
        self.session_manager.workspace_repo.append_event(
            workspace,
            EventRecord(
                id=f"evt-{uuid.uuid4().hex}",
                type="session_failed",
                created_at=now,
                data={
                    "tracker_name": record.tracker_name,
                    "task_id": task.id,
                    "error_type": error.__class__.__name__,
                    "error": detail,
                    "traceback": "".join(traceback.format_exception(type(error), error, error.__traceback__)),
                },
            ),
        )

    async def mark_session_stopped(self, *, task: Task, tracker_name: str, reason: str) -> None:
        workspace = self._workspace_for_task(task)
        state = self.session_manager.workspace_repo.load_session(workspace)
        if state is None:
            await self.publish_comment(task=task, text=reason, workspace=workspace)
            return
        now = self._now()
        state.state = "stopped"
        state.process_id = None
        state.updated_at = now
        self.session_manager.workspace_repo.save_session(state)
        self.session_manager.workspace_repo.append_event(
            workspace,
            EventRecord(
                id=f"evt-{uuid.uuid4().hex}",
                type="session_stopped",
                created_at=now,
                data={"tracker_name": tracker_name, "task_id": task.id, "reason": reason},
            ),
        )
        await self.publish_comment(task=task, text=f"Agent stopped. {reason}", workspace=workspace)

    def session_state(
        self,
        *,
        record: SessionRecord,
        workspace: Path,
        state: str,
        process_id: int | None,
        updated_at: str | None = None,
    ) -> SessionState:
        return SessionState(
            internal_task_id=record.internal_task_id,
            tracker_name=record.tracker_name,
            tracker_task_id=record.tracker_task_id,
            tracker_type=record.tracker_type,
            workspace=workspace,
            state=state,
            agent_name=record.agent_name,
            config_path=record.config_path,
            process_id=process_id,
            started_at=record.started_at,
            updated_at=updated_at or self._now(),
            provisioned_repos=list(record.provisioned_repos),
        )

    def now(self) -> str:
        return self._now()

    def _workspace_for_task(self, task: Task) -> Path:
        return self.config.session.base_path / self.session_manager.make_internal_task_id(task)

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()
