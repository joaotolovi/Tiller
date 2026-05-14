"""Synchronous ClickUp tracker adapter for agent-facing local commands."""

from __future__ import annotations

from pathlib import Path

import httpx

from ..models import Task, TaskComment
from .sync_base import SyncTrackerAdapter


class SyncClickUpTrackerAdapter(SyncTrackerAdapter):
    """ClickUp tracker using synchronous httpx.Client."""

    def __init__(self, token: str, team_id: str, **kwargs):
        self.team_id = team_id
        self._client = httpx.Client(
            base_url="https://api.clickup.com/api/v2",
            headers={"Authorization": token},
        )

    def get_task(self, task_id: str) -> Task:
        resp = self._client.get(
            f"/task/{task_id}",
            params={"include_markdown_description": "true"},
        )
        resp.raise_for_status()
        data = resp.json()

        comments = self._get_comments(task_id)

        return Task(
            id=data["id"],
            title=data.get("name", ""),
            description=data.get("markdown_description") or data.get("description", ""),
            status=data.get("status", {}).get("status", ""),
            comments=comments,
            attachments=[],
            metadata=data,
        )

    def list_status_options(self, task_id: str) -> list[str]:
        task = self.get_task(task_id)
        statuses_payload = task.metadata.get("statuses") if isinstance(task.metadata, dict) else None
        statuses: list[str] = []
        if isinstance(statuses_payload, list):
            for item in statuses_payload:
                if isinstance(item, dict):
                    name = str(item.get("status") or "").strip()
                    if name:
                        statuses.append(name)
        if statuses:
            return sorted(set(statuses))
        return [task.status] if task.status else []

    def add_comment(self, task_id: str, text: str) -> None:
        resp = self._client.post(
            f"/task/{task_id}/comment",
            json={"comment_text": text},
        )
        resp.raise_for_status()

    def update_status(self, task_id: str, status: str) -> None:
        resp = self._client.put(
            f"/task/{task_id}",
            json={"status": status},
        )
        resp.raise_for_status()

    def download_attachments(self, task_id: str, dest: Path) -> list[Path]:
        dest.mkdir(parents=True, exist_ok=True)
        resp = self._client.get(f"/task/{task_id}")
        resp.raise_for_status()
        data = resp.json()

        paths: list[Path] = []
        for att in data.get("attachments", []):
            url = att.get("url")
            name = att.get("title") or att.get("id", "file")
            if not url:
                continue
            dl = self._client.get(url)
            dl.raise_for_status()
            file_path = dest / name
            file_path.write_bytes(dl.content)
            paths.append(file_path)
        return paths

    def close(self) -> None:
        self._client.close()

    def _get_comments(self, task_id: str) -> list[TaskComment]:
        resp = self._client.get(f"/task/{task_id}/comment")
        resp.raise_for_status()
        data = resp.json()
        comments: list[TaskComment] = []
        for c in data.get("comments", []):
            text = c.get("comment_text", "")
            if not text and c.get("comment"):
                parts = []
                for item in c["comment"]:
                    parts.append(item.get("text", ""))
                text = "".join(parts)
            if text:
                author = None
                if isinstance(c.get("user"), dict):
                    author = c["user"].get("username") or c["user"].get("email")
                comments.append(
                    TaskComment(
                        id=str(c.get("id", "")),
                        author=author,
                        body=text,
                        created_at=str(c.get("date")) if c.get("date") is not None else None,
                    )
                )
        return comments
