from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx

from ..models import Task, TaskAttachment, TaskComment
from .base import TrackerAdapter


class ClickUpTrackerAdapter(TrackerAdapter):
    def __init__(
        self,
        *,
        token: str,
        team_id: str,
        base_url: str = "https://api.clickup.com/api/v2",
        include_closed: bool = False,
        tag: str | None = None,
        assignee: str | None = None,
    ) -> None:
        self.team_id = team_id
        self.include_closed = include_closed
        self.tag = tag
        self.assignee = assignee
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": token, "Content-Type": "application/json"},
            timeout=30.0,
        )

    async def validate(self) -> None:
        response = await self._client.get(f"/team/{self.team_id}")
        response.raise_for_status()

    async def list_teams(self) -> list[dict[str, str]]:
        response = await self._client.get("/team")
        response.raise_for_status()
        payload = response.json()
        teams: list[dict[str, str]] = []
        for item in payload.get("teams", []):
            team_id = str(item.get("id") or "")
            name = str(item.get("name") or team_id)
            if team_id:
                teams.append({"id": team_id, "name": name})
        return teams

    async def list_team_members(self, team_id: str | None = None) -> list[dict[str, str]]:
        response = await self._client.get(f"/team/{team_id or self.team_id}")
        response.raise_for_status()
        payload = response.json()
        team_payload = payload.get("team") if isinstance(payload.get("team"), dict) else payload
        members: list[dict[str, str]] = []
        for item in team_payload.get("members", []):
            user = item.get("user") or {}
            user_id = str(user.get("id") or "")
            name = str(user.get("username") or user.get("email") or user_id).strip()
            if user_id:
                members.append({"id": user_id, "name": name})
        if members:
            return members
        return await self._list_team_members_from_tasks(team_id)

    async def list_team_spaces(self, team_id: str | None = None) -> list[dict[str, Any]]:
        response = await self._client.get(f"/team/{team_id or self.team_id}/space")
        response.raise_for_status()
        payload = response.json()
        spaces: list[dict[str, Any]] = []
        for item in payload.get("spaces", []):
            if isinstance(item, dict):
                spaces.append(item)
        return spaces

    async def list_team_statuses(self, team_id: str | None = None) -> list[str]:
        statuses: list[str] = []
        for space in await self.list_team_spaces(team_id):
            for item in space.get("statuses", []):
                if isinstance(item, dict):
                    name = str(item.get("status") or "").strip()
                    if name:
                        statuses.append(name)
        return sorted(set(statuses))

    async def list_team_tags(self, team_id: str | None = None) -> list[str]:
        tags: list[str] = []
        for space in await self.list_team_spaces(team_id):
            space_id = str(space.get("id") or "").strip()
            if not space_id:
                continue
            response = await self._client.get(f"/space/{space_id}/tag")
            response.raise_for_status()
            payload = response.json()
            for item in payload.get("tags", []):
                name = str(item.get("name") or "").strip()
                if name:
                    tags.append(name)
        if tags:
            return sorted(set(tags))
        return await self._list_team_tags_from_tasks(team_id)

    async def list_tasks(self, status: str) -> list[Task]:
        response = await self._client.get(
            f"/team/{self.team_id}/task",
            params=self._list_task_params(status),
        )
        response.raise_for_status()
        payload = response.json()
        tasks: list[Task] = []
        for item in payload.get("tasks", []):
            tasks.append(self._task_from_clickup(item, include_comments=False))
        return tasks

    async def get_task(self, task_id: str) -> Task:
        task_response, comments_response = await self._gather_task_details(task_id)
        task_payload = task_response.json()
        comments_payload = comments_response.json() if comments_response is not None else {"comments": []}
        task = self._task_from_clickup(task_payload, include_comments=False)
        task.comments = [self._comment_from_clickup(comment) for comment in comments_payload.get("comments", [])]
        return task

    async def list_status_options(self, task_id: str) -> list[str]:
        task = await self.get_task(task_id)
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

    async def update_status(self, task_id: str, status: str) -> None:
        response = await self._client.put(f"/task/{task_id}", json={"status": status})
        response.raise_for_status()

    async def add_comment(self, task_id: str, text: str) -> None:
        response = await self._client.post(
            f"/task/{task_id}/comment",
            json={"comment_text": text, "notify_all": True},
        )
        response.raise_for_status()

    async def download_attachments(self, task_id: str, dest: Path) -> list[Path]:
        task = await self.get_task(task_id)
        dest.mkdir(parents=True, exist_ok=True)
        downloaded: list[Path] = []
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            for attachment in task.attachments:
                if not attachment.url:
                    continue
                response = await client.get(attachment.url)
                response.raise_for_status()
                path = dest / attachment.name
                path.write_bytes(response.content)
                downloaded.append(path)
        return downloaded

    async def _gather_task_details(self, task_id: str) -> tuple[httpx.Response, httpx.Response | None]:
        task_request = self._client.get(f"/task/{task_id}", params={"include_markdown_description": "true"})
        comments_request = self._client.get(f"/task/{task_id}/comment")
        task_response, comments_response = await asyncio.gather(task_request, comments_request, return_exceptions=False)
        task_response.raise_for_status()
        comments_response.raise_for_status()
        return task_response, comments_response

    async def _list_team_members_from_tasks(self, team_id: str | None = None) -> list[dict[str, str]]:
        members: dict[str, str] = {}
        for task in await self._list_team_task_payloads(team_id):
            for assignee in task.get("assignees", []):
                if not isinstance(assignee, dict):
                    continue
                user_id = str(assignee.get("id") or "").strip()
                name = str(assignee.get("username") or assignee.get("email") or user_id).strip()
                if user_id:
                    members[user_id] = name
        return [{"id": user_id, "name": members[user_id]} for user_id in sorted(members, key=lambda item: members[item].lower())]

    async def _list_team_tags_from_tasks(self, team_id: str | None = None) -> list[str]:
        tags: set[str] = set()
        for task in await self._list_team_task_payloads(team_id):
            for tag in task.get("tags", []):
                if not isinstance(tag, dict):
                    continue
                name = str(tag.get("name") or "").strip()
                if name:
                    tags.add(name)
        return sorted(tags)

    async def _list_team_task_payloads(self, team_id: str | None = None) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []
        page = 0
        while True:
            response = await self._client.get(
                f"/team/{team_id or self.team_id}/task",
                params={
                    "include_closed": "true",
                    "subtasks": "true",
                    "page": page,
                },
            )
            response.raise_for_status()
            payload = response.json()
            for item in payload.get("tasks", []):
                if isinstance(item, dict):
                    tasks.append(item)
            if payload.get("last_page", True):
                break
            page += 1
        return tasks

    def _list_task_params(self, status: str) -> dict[str, Any]:
        params: dict[str, Any] = {
            "statuses[]": status,
            "include_closed": str(self.include_closed).lower(),
            "subtasks": "true",
            "page": 0,
        }
        if self.tag:
            params["tags[]"] = self.tag
        if self.assignee:
            params["assignees[]"] = self.assignee
        return params

    def _task_from_clickup(self, payload: dict[str, Any], *, include_comments: bool) -> Task:
        attachments = [
            TaskAttachment(
                id=str(item.get("id") or item.get("url") or item.get("title") or item.get("name")),
                name=item.get("title") or item.get("name") or "attachment",
                url=item.get("url"),
            )
            for item in payload.get("attachments", [])
        ]
        return Task(
            id=str(payload["id"]),
            title=payload.get("name", ""),
            description=payload.get("markdown_description") or payload.get("description") or "",
            status=(payload.get("status") or {}).get("status", ""),
            comments=[] if not include_comments else [self._comment_from_clickup(item) for item in payload.get("comments", [])],
            attachments=attachments,
            metadata=payload,
        )

    def _comment_from_clickup(self, payload: dict[str, Any]) -> TaskComment:
        author = None
        if isinstance(payload.get("user"), dict):
            author = payload["user"].get("username") or payload["user"].get("email")
        return TaskComment(
            id=str(payload.get("id", "")),
            author=author,
            body=payload.get("comment_text") or "\n".join(part.get("text", "") for part in payload.get("comment", [])),
            created_at=str(payload.get("date")) if payload.get("date") is not None else None,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
