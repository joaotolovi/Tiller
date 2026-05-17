from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
from pathlib import Path

from .agents import AgentHarness
from .models import AgentRunRequest, TillerConfig, TrackerConfig, TaskControlRequest
from .session import SessionManager
from .task_runtime import TaskRuntime
from .trackers import TrackerAdapter

logger = logging.getLogger(__name__)


class TrackerService:
    def __init__(
        self,
        *,
        config: TillerConfig,
        tracker_config: TrackerConfig,
        tracker: TrackerAdapter,
        harness: AgentHarness,
    ) -> None:
        self.config = config
        self.tracker_config = tracker_config
        self.tracker = tracker
        self.harness = harness
        self.session_manager = SessionManager(config, tracker, tracker_config)
        self.runtime = TaskRuntime(
            config=config,
            tracker=tracker,
            session_manager=self.session_manager,
            tracker_config=tracker_config,
        )
        self.processed_tasks: set[str] = set()
        self.active_sessions: dict[str, asyncio.Task[None]] = {}
        self._active_processes: dict[str, subprocess.Popen[bytes]] = {}

    async def run_forever(self) -> None:
        logger.info(
            "Tiller tracker service started tracker_name=%s tracker_type=%s trigger_status=%s poll_interval=%ss agent=%s",
            self.tracker_config.name,
            self.tracker_config.type,
            self.tracker_config.trigger_status,
            self.tracker_config.poll_interval,
            self.config.agent.default,
        )
        try:
            await self.reconcile_orphaned_sessions()
            while True:
                await self.run_once()
                await asyncio.sleep(self.tracker_config.poll_interval)
        except asyncio.CancelledError:
            logger.info("Tracker service cancellation requested tracker_name=%s", self.tracker_config.name)
            await self.shutdown()
            raise

    async def run_once(self) -> None:
        await self._process_control_requests()
        tasks = await self.tracker.list_tasks(self.tracker_config.trigger_status)
        logger.info(
            "Tracker poll completed tracker_name=%s trigger_status=%s tasks_found=%s active_sessions=%s processed_tasks=%s",
            self.tracker_config.name,
            self.tracker_config.trigger_status,
            len(tasks),
            len(self.active_sessions),
            len(self.processed_tasks),
        )
        for task in tasks:
            task_ref = self._task_ref(task.id)
            if task_ref in self.processed_tasks or task_ref in self.active_sessions:
                logger.debug(
                    "Skipping task tracker_name=%s task_id=%s already_processed=%s already_active=%s",
                    self.tracker_config.name,
                    task.id,
                    task_ref in self.processed_tasks,
                    task_ref in self.active_sessions,
                )
                continue
            logger.info("Starting task tracker_name=%s task_id=%s title=%s", self.tracker_config.name, task.id, task.title)
            await self._mark_processing(task.id)
            session_task = asyncio.create_task(self._start_session(task.id))
            self.active_sessions[task_ref] = session_task
            session_task.add_done_callback(self._make_session_done_callback(task_ref))

    async def _mark_processing(self, task_id: str) -> None:
        task_ref = self._task_ref(task_id)
        self.processed_tasks.add(task_ref)
        logger.info("Task claimed tracker_name=%s task_id=%s", self.tracker_config.name, task_id)
        await self.runtime.claim_task(task_id)
        if self.tracker_config.processing_status:
            logger.info(
                "Task moved to processing status tracker_name=%s task_id=%s status=%s",
                self.tracker_config.name,
                task_id,
                self.tracker_config.processing_status,
            )

    async def _start_session(self, task_id: str) -> None:
        task = await self.tracker.get_task(task_id)
        record = None
        paths = None
        logger.info("Preparing session tracker_name=%s task_id=%s title=%s", self.tracker_config.name, task_id, task.title)
        try:
            await self.runtime.publish_comment(
                task=task,
                text="Starting task. Analyzing...",
            )
            tool_transport = self.harness.tool_transport_for(self.config.agent.default)
            record, paths, mcp_payload = await self.session_manager.prepare(task, self.config.agent.default, tool_transport)
            logger.info(
                "Session ready tracker_name=%s task_id=%s internal_task_id=%s workspace=%s transport=%s",
                self.tracker_config.name,
                task_id,
                record.internal_task_id,
                paths.root,
                tool_transport,
            )
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
                "Agent started tracker_name=%s task_id=%s internal_task_id=%s pid=%s adapter=%s log=%s",
                self.tracker_config.name,
                task_id,
                record.internal_task_id,
                spawn_result.process_id,
                spawn_result.adapter_name,
                spawn_result.log_path,
            )

            if spawn_result.process is None:
                raise RuntimeError("Agent process was not returned by harness")

            task_ref = self._task_ref(task_id)
            self._active_processes[task_ref] = spawn_result.process
            try:
                exit_code = await asyncio.to_thread(spawn_result.process.wait)
            except asyncio.CancelledError:
                logger.info(
                    "Session cancellation requested tracker_name=%s task_id=%s pid=%s",
                    self.tracker_config.name,
                    task_id,
                    spawn_result.process.pid,
                )
                await asyncio.to_thread(self._terminate_process_tree, task_ref, spawn_result.process)
                raise
            finally:
                self._active_processes.pop(task_ref, None)

            record.state = await self.runtime.finalize_session(
                task=task,
                record=record,
                workspace=paths.root,
                exit_code=exit_code,
            )
            record.updated_at = self.runtime.now()
            logger.info(
                "Agent finished tracker_name=%s task_id=%s internal_task_id=%s exit_code=%s",
                self.tracker_config.name,
                task_id,
                record.internal_task_id,
                exit_code,
            )

            self.session_manager.cleanup(paths)
            logger.info(
                "Session finalized tracker_name=%s task_id=%s internal_task_id=%s state=%s workspace=%s",
                self.tracker_config.name,
                task_id,
                record.internal_task_id,
                record.state,
                paths.root,
            )
        except asyncio.CancelledError:
            if record is not None and paths is not None:
                record.state = "stopped"
                record.updated_at = self.runtime.now()
                self.session_manager.workspace_repo.save_session(self.runtime.session_state(record=record, workspace=paths.root, state="stopped", process_id=None))
            raise
        except Exception as error:
            logger.warning(
                "Session error before completion tracker_name=%s task_id=%s error=%s",
                self.tracker_config.name,
                task_id,
                error,
            )
            await self.runtime.fail_session(
                task=task,
                error=error,
                record=record,
                workspace=paths.root if paths is not None else None,
            )
            raise

    async def shutdown(self) -> None:
        if not self.active_sessions:
            return
        logger.info("Shutting down active sessions tracker_name=%s count=%s", self.tracker_config.name, len(self.active_sessions))
        session_tasks = list(self.active_sessions.values())
        for task in session_tasks:
            task.cancel()
        results = await asyncio.gather(*session_tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                logger.exception("Session shutdown raised an exception", exc_info=result)

    async def _process_control_requests(self) -> None:
        requests = await self.tracker.poll_control_requests()
        for request in requests:
            try:
                await self._apply_control_request(request)
            finally:
                await self.tracker.acknowledge_control_request(request)

    async def _apply_control_request(self, request: TaskControlRequest) -> None:
        task_ref = self._task_ref(request.task_id)
        if request.action == "stop":
            await self._stop_task(task_ref, request)
            return
        if request.action == "continue":
            await self._continue_task(task_ref, request)
            return

    async def _stop_task(self, task_ref: str, request: TaskControlRequest) -> None:
        session_task = self.active_sessions.get(task_ref)
        task = await self.tracker.get_task(request.task_id)
        if session_task is None:
            await self.runtime.publish_comment(task=task, text="No running agent was found for this task.")
            return
        session_task.cancel()
        try:
            await session_task
        except asyncio.CancelledError:
            pass
        await self.runtime.mark_session_stopped(task=task, tracker_name=self.tracker_config.name, reason=f"Stop requested via {request.source}.")

    async def _continue_task(self, task_ref: str, request: TaskControlRequest) -> None:
        if task_ref in self.active_sessions:
            task = await self.tracker.get_task(request.task_id)
            await self.runtime.publish_comment(task=task, text="Agent is already running for this task.")
            return
        self.processed_tasks.discard(task_ref)
        await self._mark_processing(request.task_id)
        session_task = asyncio.create_task(self._start_session(request.task_id))
        self.active_sessions[task_ref] = session_task
        session_task.add_done_callback(self._make_session_done_callback(task_ref))
        task = await self.tracker.get_task(request.task_id)
        await self.runtime.publish_comment(task=task, text=f"Continue requested via {request.source}. Resuming agent...")

    def _terminate_process_tree(self, task_ref: str, process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        logger.info("Terminating agent process task_ref=%s pid=%s", task_ref, process.pid)
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            process.wait(timeout=5)
        except Exception:
            logger.warning("Graceful process termination failed task_ref=%s pid=%s; force killing", task_ref, process.pid, exc_info=True)
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait(timeout=5)
            except Exception:
                logger.exception("Failed to kill agent process task_ref=%s pid=%s", task_ref, process.pid)

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
            if state is None or state.tracker_name != self.tracker_config.name or state.state != "running" or state.process_id is None:
                continue
            if self._process_exists(state.process_id):
                continue
            logger.info(
                "Reconciling orphaned session tracker_name=%s internal_task_id=%s tracker_task_id=%s stale_pid=%s",
                state.tracker_name,
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
                self.runtime_event("session_interrupted", state.tracker_task_id, state.updated_at, state.tracker_name),
            )

    def runtime_event(self, event_type: str, task_id: str, created_at: str, tracker_name: str):
        from .workspace import EventRecord

        return EventRecord(
            id=f"evt-reconcile-{tracker_name}-{task_id}-{created_at}",
            type=event_type,
            created_at=created_at,
            data={"tracker_name": tracker_name, "task_id": task_id},
        )

    def _process_exists(self, process_id: int) -> bool:
        try:
            os.kill(process_id, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _make_session_done_callback(self, task_ref: str):
        def _callback(task: asyncio.Task[None]) -> None:
            self.active_sessions.pop(task_ref, None)
            try:
                task.result()
            except asyncio.CancelledError:
                logger.info("Session cancelled for task %s", task_ref)
            except Exception:
                logger.exception("Session failed for task %s", task_ref)

        return _callback

    def _task_ref(self, task_id: str) -> str:
        return f"{self.tracker_config.name}:{task_id}"


class MultiTrackerService:
    def __init__(self, services: list[TrackerService]) -> None:
        self.services = services

    async def run_forever(self) -> None:
        await asyncio.gather(*(service.run_forever() for service in self.services))

    async def shutdown(self) -> None:
        await asyncio.gather(*(service.shutdown() for service in self.services), return_exceptions=True)


TillerService = TrackerService
