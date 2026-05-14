from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from pathlib import Path

from ..models import Task


class TrackerAdapter(ABC):
    @abstractmethod
    async def list_tasks(self, status: str) -> list[Task]:
        raise NotImplementedError

    @abstractmethod
    async def get_task(self, task_id: str) -> Task:
        raise NotImplementedError

    @abstractmethod
    async def list_status_options(self, task_id: str) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    async def update_status(self, task_id: str, status: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def add_comment(self, task_id: str, text: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def download_attachments(self, task_id: str, dest: Path) -> list[Path]:
        raise NotImplementedError

    async def validate(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


class InMemoryTrackerAdapter(TrackerAdapter):
    def __init__(self, tasks: list[Task] | None = None) -> None:
        self._tasks = {task.id: task for task in tasks or []}
        self.comments: dict[str, list[str]] = {task_id: [] for task_id in self._tasks}

    async def list_tasks(self, status: str) -> list[Task]:
        await asyncio.sleep(0)
        return [task for task in self._tasks.values() if task.status == status]

    async def get_task(self, task_id: str) -> Task:
        await asyncio.sleep(0)
        return self._tasks[task_id]

    async def list_status_options(self, task_id: str) -> list[str]:
        await asyncio.sleep(0)
        task = self._tasks[task_id]
        return [task.status] if task.status else []

    async def update_status(self, task_id: str, status: str) -> None:
        await asyncio.sleep(0)
        self._tasks[task_id].status = status

    async def add_comment(self, task_id: str, text: str) -> None:
        await asyncio.sleep(0)
        self.comments.setdefault(task_id, []).append(text)

    async def download_attachments(self, task_id: str, dest: Path) -> list[Path]:
        await asyncio.sleep(0)
        dest.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for attachment in self._tasks[task_id].attachments:
            target = dest / attachment.name
            target.write_text(f"attachment placeholder for {attachment.id}\n", encoding="utf-8")
            paths.append(target)
        return paths
