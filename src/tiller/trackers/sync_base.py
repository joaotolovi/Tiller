"""Synchronous tracker interface for agent-facing local commands."""

from __future__ import annotations

from pathlib import Path

from ..models import Task


class SyncTrackerAdapter:
    """Synchronous tracker operations used by local CLI commands."""

    def get_task(self, task_id: str) -> Task:
        raise NotImplementedError

    def list_status_options(self, task_id: str) -> list[str]:
        raise NotImplementedError

    def add_comment(self, task_id: str, text: str) -> None:
        raise NotImplementedError

    def update_status(self, task_id: str, status: str) -> None:
        raise NotImplementedError

    def download_attachments(self, task_id: str, dest: Path) -> list[Path]:
        raise NotImplementedError

    def close(self) -> None:
        pass
