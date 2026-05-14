from __future__ import annotations

from pathlib import Path

from .base import TrackerAdapter


class UnsupportedTrackerAdapter(TrackerAdapter):
    def __init__(self, tracker_type: str) -> None:
        self.tracker_type = tracker_type

    async def list_tasks(self, status: str):
        raise NotImplementedError(f"Tracker adapter '{self.tracker_type}' is not configured yet")

    async def get_task(self, task_id: str):
        raise NotImplementedError(f"Tracker adapter '{self.tracker_type}' is not configured yet")

    async def list_status_options(self, task_id: str):
        raise NotImplementedError(f"Tracker adapter '{self.tracker_type}' is not configured yet")

    async def update_status(self, task_id: str, status: str) -> None:
        raise NotImplementedError(f"Tracker adapter '{self.tracker_type}' is not configured yet")

    async def add_comment(self, task_id: str, text: str) -> None:
        raise NotImplementedError(f"Tracker adapter '{self.tracker_type}' is not configured yet")

    async def download_attachments(self, task_id: str, dest: Path):
        raise NotImplementedError(f"Tracker adapter '{self.tracker_type}' is not configured yet")
