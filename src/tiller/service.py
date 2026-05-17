from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
from pathlib import Path

from .agents import AgentHarness
from .models import AgentRunRequest, TillerConfig
from .session import SessionManager
from .task_runtime import TaskRuntime
from .trackers import TrackerAdapter

logger = logging.getLogger(__name__)


class TillerService:
    def __init__(
        self,
        *,
        config: TillerConfig,
        tracker: TrackerAdapter,
        harness: AgentHarness,
    ) -> None:
        self.config = config
        self.tracker = tracker
        self.harness = harness
        self.session_manager = SessionManager(config, tracker)
        self.runtime = TaskRuntime(config=config, tracker=tracker, session_manager=self.session_manager)
        self.processed_tasks: set[str] = set()
        self.active_sessions: dict[str, asyncio.Task[None]] = {}
        self._active_processes: dict[str, subprocess.Popen[bytes]] = {}

    async def run_forever(self) -> None:
        logger.info(
            "Tiller service started tracker=%s trigger_status=%s poll_interval=%ss agent=%s",
            self.config.tracker.type,
            self.config.tracker.trigger_status,
            self.config.tracker.poll_interval,
            self.config.agent.default,
        )
        try:
            await self.reconcile_orphaned_sessions()
            while True:
                await self.run_once()
                await asyncio.sleep(self.config.tracker.poll_interval)
        except asyncio.CancelledError:
            logger.info("Tiller service cancellation requested")
            await self.shutdown()
            raise

    async def run_once(self) -> None:
        tasks = await self.tracker.list_tasks(self.config.tracker.trigger_status)
        logger.info(
            "Tracker poll completed trigger_status=%s tasks_found=%s active_sessions=%s processed_tasks=%s",
            self.config.tracker.trigger_status,
            len(tasks),
            len(self.active_sessions),
            len(self.processed_tasks),
        )
        for task in tasks:
            if task.id in self.processed_tasks or task.id in self.active_sessions:
                logger.debug(
                    "Skipping task task_id=%s already_processed=%s already_active=%s",
                    task.id,
                    task.id in self.processed_tasks,
                    task.id in self.active_sessions,
                )
                continue
            logger.info("Starting task task_id=%s title=%s", task.id, task.title)
            await self._mark_processing(task.id)
            session_task = asyncio.create_task(self._start_session(task.id))
            self.active_sessions[task.id] = session_task
            session_task.add_done_callback(self._make_session_done_callback(task.id))

    async def _mark_processing(self, task_id: str) -> None:
        self.processed_tasks.add(task_id)
        logger.info("Task claimed task_id=%s", task_id)
        await self.runtime.claim_task(task_id)
        if self.config.tracker.processing_status:
            logger.info("Task moved to processing status task_id=%s status=%s", task_id, self.config.tracker.processing_status)

    async def _start_session(self, task_id: str) -> None:
        task = await self.tracker.get_task(task_id)
        logger.info("Preparing session task_id=%s title=%s", task_id, task.title)
        await self.runtime.publish_comment(
            task=task,
            text="Starting task. Analyzing...",
        )
        tool_transport = self.harness.tool_transport_for(self.config.agent.default)
        record, paths, mcp_payload = await self.session_manager.prepare(task, self.config.agent.default, tool_transport)
        logger.info("Session ready task_id=%s internal_task_id=%s workspace=%s transport=%s", task_id, record.internal_task_id, paths.root, tool_transport)
        goal = self._build_goal_prompt(paths.root, tool_transport)
        spawn_result = await asyncio.to_thread(
            self.harness.spawn,
            AgentRunRequest(
                agent_name=self.config.agent.default,
                workspace=paths.root,
                goal=goal,
                mcp_config=mcp_payload,
                model=self.config.agent.model,
            ),
        )
        record.process_id = spawn_result.process_id
        record.state = "running"
        record.updated_at = self.runtime.now()
        self.runtime.mark_agent_started(
            record=record,
            workspace=paths.root,
            process_id=spawn_result.process_id,
            adapter_name=spawn_result.adapter_name,
        )
        logger.info(
            "Agent started task_id=%s internal_task_id=%s pid=%s adapter=%s log=%s",
            task_id,
            record.internal_task_id,
            spawn_result.process_id,
            spawn_result.adapter_name,
            spawn_result.log_path,
        )

        if spawn_result.process is None:
            raise RuntimeError("Agent process was not returned by harness")

        self._active_processes[task_id] = spawn_result.process
        try:
            exit_code = await asyncio.to_thread(spawn_result.process.wait)
        except asyncio.CancelledError:
            logger.info("Session cancellation requested task_id=%s pid=%s", task_id, spawn_result.process.pid)
            await asyncio.to_thread(self._terminate_process_tree, task_id, spawn_result.process)
            raise
        finally:
            self._active_processes.pop(task_id, None)

        record.state = await self.runtime.finalize_session(
            task=task,
            record=record,
            workspace=paths.root,
            exit_code=exit_code,
        )
        record.updated_at = self.runtime.now()
        logger.info("Agent finished task_id=%s internal_task_id=%s exit_code=%s", task_id, record.internal_task_id, exit_code)

        self.session_manager.cleanup(paths)
        logger.info("Session finalized task_id=%s internal_task_id=%s state=%s workspace=%s", task_id, record.internal_task_id, record.state, paths.root)

    async def shutdown(self) -> None:
        if not self.active_sessions:
            return
        logger.info("Shutting down active sessions count=%s", len(self.active_sessions))
        session_tasks = list(self.active_sessions.values())
        for task in session_tasks:
            task.cancel()
        results = await asyncio.gather(*session_tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                logger.exception("Session shutdown raised an exception", exc_info=result)

    def _terminate_process_tree(self, task_id: str, process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        logger.info("Terminating agent process task_id=%s pid=%s", task_id, process.pid)
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            process.wait(timeout=5)
        except Exception:
            logger.warning("Graceful process termination failed task_id=%s pid=%s; force killing", task_id, process.pid, exc_info=True)
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait(timeout=5)
            except Exception:
                logger.exception("Failed to kill agent process task_id=%s pid=%s", task_id, process.pid)

    def _build_goal_prompt(self, workspace: Path, tool_transport: str) -> str:
        tool_guidance = (
            "Use the MCP tools exposed in this session for tracker and project operations whenever available. "
            if tool_transport == "mcp"
            else "Use the local tiller commands available in this workspace for tracker and project operations. "
        )
        return (
            "You are running a Tiller task. Read AGENTS.md, TASK.md, and STATE.md; use projects.json and the available session tools. "
            "You must keep the tracker updated during the task: comment when you start, when you discover something important, at decision points, when you define or change the plan, when you hit a blocker, when you resolve a blocker, and when you finish with a short overview for humans. "
            "Update STATE.md when making important decisions or changing the plan. "
            f"{tool_guidance}"
            "Always open a PR when code changes are made. "
            f"Workspace: {workspace}"
        )

    async def reconcile_orphaned_sessions(self) -> None:
        for session_root in self.config.session.base_path.iterdir():
            if not session_root.is_dir():
                continue
            session_file = session_root / "session.json"
            if not session_file.exists():
                continue
            state = self.session_manager.workspace_repo.load_session(session_root)
            if state is None or state.state != "running" or state.process_id is None:
                continue
            if self._process_exists(state.process_id):
                continue
            logger.info(
                "Reconciling orphaned session internal_task_id=%s tracker_task_id=%s stale_pid=%s",
                state.internal_task_id,
                state.tracker_task_id,
                state.process_id,
            )
            state.state = "interrupted"
            state.process_id = None
            state.updated_at = self.runtime.now()
            self.session_manager.workspace_repo.save_session(state)
            self.session_manager.workspace_repo.append_event(
                session_root,
                self.runtime_event("session_interrupted", state.tracker_task_id, state.updated_at),
            )

    def runtime_event(self, event_type: str, task_id: str, created_at: str):
        from .workspace import EventRecord

        return EventRecord(
            id=f"evt-reconcile-{task_id}-{created_at}",
            type=event_type,
            created_at=created_at,
            data={"task_id": task_id},
        )

    def _process_exists(self, process_id: int) -> bool:
        try:
            os.kill(process_id, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _make_session_done_callback(self, task_id: str):
        def _callback(task: asyncio.Task[None]) -> None:
            self.active_sessions.pop(task_id, None)
            try:
                task.result()
            except asyncio.CancelledError:
                logger.info("Session cancelled for task %s", task_id)
            except Exception:
                logger.exception("Session failed for task %s", task_id)

        return _callback
