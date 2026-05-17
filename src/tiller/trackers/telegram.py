from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from collections.abc import Sequence
from urllib.parse import urlparse

import httpx

from ..models import Task, TaskAttachment, TaskComment
from .base import TrackerAdapter
from .sync_base import SyncTrackerAdapter


@dataclass(slots=True)
class TelegramTrackerState:
    next_task_number: int
    last_update_id: int
    awaiting_new_task_by_chat: dict[str, bool]
    active_task_by_chat: dict[str, str]
    tasks: dict[str, dict[str, Any]]

    def to_payload(self) -> dict[str, Any]:
        return {
            "next_task_number": self.next_task_number,
            "last_update_id": self.last_update_id,
            "awaiting_new_task_by_chat": self.awaiting_new_task_by_chat,
            "active_task_by_chat": self.active_task_by_chat,
            "tasks": self.tasks,
        }

    @classmethod
    def empty(cls) -> "TelegramTrackerState":
        return cls(
            next_task_number=1,
            last_update_id=0,
            awaiting_new_task_by_chat={},
            active_task_by_chat={},
            tasks={},
        )

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "TelegramTrackerState":
        return cls(
            next_task_number=int(payload.get("next_task_number", 1)),
            last_update_id=int(payload.get("last_update_id", 0)),
            awaiting_new_task_by_chat={str(k): bool(v) for k, v in dict(payload.get("awaiting_new_task_by_chat", {})).items()},
            active_task_by_chat={str(k): str(v) for k, v in dict(payload.get("active_task_by_chat", {})).items()},
            tasks={str(k): v for k, v in dict(payload.get("tasks", {})).items()},
        )


class TelegramStateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()

    def load(self) -> TelegramTrackerState:
        if not self.path.exists():
            return TelegramTrackerState.empty()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return TelegramTrackerState.from_payload(payload)

    def save(self, state: TelegramTrackerState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(state.to_payload(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


TELEGRAM_BOT_COMMANDS: tuple[dict[str, str], ...] = (
    {"command": "start", "description": "Show bot instructions"},
    {"command": "new", "description": "Create a new task"},
)


def _commands_payload(commands: Sequence[dict[str, str]]) -> list[dict[str, str]]:
    return [{"command": str(item["command"]), "description": str(item["description"])} for item in commands]


class TelegramTrackerAdapter(TrackerAdapter):
    def __init__(
        self,
        *,
        bot_token: str,
        state_path: str | Path,
        allowed_chat_ids: list[str] | None = None,
        allowed_user_ids: list[str] | None = None,
        base_url: str = "https://api.telegram.org",
    ) -> None:
        self.store = TelegramStateStore(state_path)
        self.allowed_chat_ids = {str(item) for item in (allowed_chat_ids or [])}
        self.allowed_user_ids = {str(item) for item in (allowed_user_ids or [])}
        self._client = httpx.AsyncClient(base_url=f"{base_url.rstrip('/')}/bot{bot_token}", timeout=30.0)

    async def validate(self) -> None:
        response = await self._client.get("/getMe")
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise ValueError("Telegram tracker validation failed")
        await self._register_commands()

    async def list_tasks(self, status: str) -> list[Task]:
        state = await self._sync_updates()
        tasks: list[Task] = []
        for task_id, payload in state.tasks.items():
            if str(payload.get("status", "")) != status:
                continue
            tasks.append(self._task_from_payload(task_id, payload))
        tasks.sort(key=lambda item: item.metadata.get("created_at", ""))
        return tasks

    async def get_task(self, task_id: str) -> Task:
        state = self.store.load()
        payload = state.tasks.get(task_id)
        if payload is None:
            raise KeyError(task_id)
        return self._task_from_payload(task_id, payload)

    async def list_status_options(self, task_id: str) -> list[str]:
        task = await self.get_task(task_id)
        return ["new", "processing", "done"] if task.status else []

    async def update_status(self, task_id: str, status: str) -> None:
        state = self.store.load()
        payload = state.tasks.get(task_id)
        if payload is None:
            raise KeyError(task_id)
        payload["status"] = status
        state.tasks[task_id] = payload
        self.store.save(state)

    async def add_comment(self, task_id: str, text: str) -> None:
        state = self.store.load()
        payload = state.tasks.get(task_id)
        if payload is None:
            raise KeyError(task_id)
        chat_id = str(payload["chat_id"])
        response = await self._client.post("/sendMessage", json={"chat_id": chat_id, "text": text})
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            raise ValueError("Telegram sendMessage failed")
        message = body.get("result") or {}
        comment = self._comment_payload_from_message(message, fallback_text=text, default_author="tiller")
        payload.setdefault("comments", []).append(comment)
        state.tasks[task_id] = payload
        self.store.save(state)

    async def download_attachments(self, task_id: str, dest: Path) -> list[Path]:
        state = self.store.load()
        payload = state.tasks.get(task_id)
        if payload is None:
            raise KeyError(task_id)
        attachments = payload.get("attachments", [])
        if not attachments:
            return []
        dest.mkdir(parents=True, exist_ok=True)
        downloaded: list[Path] = []
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            for attachment in attachments:
                file_id = str(attachment.get("telegram_file_id") or "").strip()
                if not file_id:
                    continue
                file_response = await self._client.get("/getFile", params={"file_id": file_id})
                file_response.raise_for_status()
                file_payload = file_response.json()
                if not file_payload.get("ok"):
                    raise ValueError("Telegram getFile failed")
                result = file_payload.get("result") or {}
                file_path = str(result.get("file_path") or "").strip()
                if not file_path:
                    continue
                filename = str(attachment.get("name") or Path(file_path).name or file_id)
                parsed = urlparse(str(self._client.base_url))
                download_url = f"{parsed.scheme}://{parsed.netloc}/file/bot{parsed.path.removeprefix('/bot')}/{file_path}"
                data_response = await client.get(download_url)
                data_response.raise_for_status()
                target = dest / filename
                target.write_bytes(data_response.content)
                downloaded.append(target)
        return downloaded

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _register_commands(self) -> None:
        response = await self._client.post("/setMyCommands", json={"commands": _commands_payload(TELEGRAM_BOT_COMMANDS)})
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise ValueError("Telegram setMyCommands failed")

    def _send_system_message(self, chat_id: str, text: str) -> None:
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self._client.post("/sendMessage", json={"chat_id": chat_id, "text": text}))
            return
        loop.create_task(self._client.post("/sendMessage", json={"chat_id": chat_id, "text": text}))

    async def _sync_updates(self) -> TelegramTrackerState:
        state = self.store.load()
        response = await self._client.get(
            "/getUpdates",
            params={"offset": state.last_update_id + 1, "timeout": 0, "allowed_updates": json.dumps(["message"])},
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise ValueError("Telegram getUpdates failed")
        for update in payload.get("result", []):
            state.last_update_id = max(state.last_update_id, int(update.get("update_id", 0)))
            message = update.get("message")
            if isinstance(message, dict):
                self._apply_message(state, message)
        self.store.save(state)
        return state

    def _apply_message(self, state: TelegramTrackerState, message: dict[str, Any]) -> None:
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id") or "").strip()
        if not chat_id:
            return
        if self.allowed_chat_ids and chat_id not in self.allowed_chat_ids:
            return
        user = message.get("from") or {}
        user_id = str(user.get("id") or "").strip()
        if self.allowed_user_ids and user_id not in self.allowed_user_ids:
            return

        text = str(message.get("text") or message.get("caption") or "").strip()
        if text == "/new":
            state.awaiting_new_task_by_chat[chat_id] = True
            state.active_task_by_chat.pop(chat_id, None)
            self._send_system_message(chat_id, "What do you need?")
            return
        if text == "/start":
            self._send_system_message(chat_id, "Tiller is your programmer. Use /new to start a new task.")
            return
        if text.startswith("/"):
            return
        if not text and not self._message_attachments(message):
            return

        active_task_id = state.active_task_by_chat.get(chat_id)
        should_create = state.awaiting_new_task_by_chat.get(chat_id, False) or active_task_id is None
        if should_create:
            task_id = self._new_task_id(state)
            state.awaiting_new_task_by_chat[chat_id] = False
            state.active_task_by_chat[chat_id] = task_id
            state.tasks[task_id] = self._new_task_payload(task_id=task_id, chat_id=chat_id, message=message, text=text)
            return

        payload = state.tasks.get(active_task_id)
        if payload is None:
            task_id = self._new_task_id(state)
            state.active_task_by_chat[chat_id] = task_id
            state.awaiting_new_task_by_chat[chat_id] = False
            state.tasks[task_id] = self._new_task_payload(task_id=task_id, chat_id=chat_id, message=message, text=text)
            return

        self._append_or_merge_comment(payload, message, fallback_text=text)
        attachments = self._message_attachments(message)
        if attachments:
            payload.setdefault("attachments", []).extend(attachments)
        state.tasks[active_task_id] = payload

    def _new_task_id(self, state: TelegramTrackerState) -> str:
        task_id = f"telegram-{state.next_task_number}"
        state.next_task_number += 1
        return task_id

    def _new_task_payload(self, *, task_id: str, chat_id: str, message: dict[str, Any], text: str) -> dict[str, Any]:
        created_at = self._message_timestamp(message)
        return {
            "id": task_id,
            "chat_id": chat_id,
            "title": text or f"Telegram task {task_id}",
            "description": text,
            "status": "new",
            "comments": [self._comment_payload_from_message(message, fallback_text=text)],
            "attachments": self._message_attachments(message),
            "metadata": {
                "chat_id": chat_id,
                "message_id": message.get("message_id"),
                "created_at": created_at,
            },
            "created_at": created_at,
        }

    def _message_timestamp(self, message: dict[str, Any]) -> str:
        raw = message.get("date")
        if raw is None:
            return datetime.now(UTC).isoformat()
        return datetime.fromtimestamp(int(raw), tz=UTC).isoformat()

    def _comment_payload_from_message(self, message: dict[str, Any], fallback_text: str = "", default_author: str | None = None) -> dict[str, Any]:
        user = message.get("from") or {}
        author = default_author or str(user.get("username") or user.get("first_name") or user.get("id") or "telegram")
        return {
            "id": str(message.get("message_id") or f"comment-{datetime.now(UTC).timestamp()}") ,
            "author": author,
            "body": str(message.get("text") or message.get("caption") or fallback_text),
            "created_at": self._message_timestamp(message),
        }

    def _append_or_merge_comment(self, payload: dict[str, Any], message: dict[str, Any], fallback_text: str = "") -> None:
        comments = payload.setdefault("comments", [])
        comment = self._comment_payload_from_message(message, fallback_text=fallback_text)
        if comments and self._should_merge_comment(comments[-1], comment):
            previous_body = str(comments[-1].get("body") or "").strip()
            next_body = str(comment.get("body") or "").strip()
            if next_body:
                comments[-1]["body"] = f"{previous_body}\n{next_body}" if previous_body else next_body
            comments[-1]["id"] = str(comment.get("id") or comments[-1].get("id") or "")
            return
        comments.append(comment)

    def _should_merge_comment(self, previous: dict[str, Any], current: dict[str, Any]) -> bool:
        previous_author = str(previous.get("author") or "").strip()
        current_author = str(current.get("author") or "").strip()
        if not previous_author or previous_author != current_author:
            return False
        previous_created_at = str(previous.get("created_at") or "").strip()
        current_created_at = str(current.get("created_at") or "").strip()
        return bool(previous_created_at and previous_created_at == current_created_at)

    def _message_attachments(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        attachments: list[dict[str, Any]] = []
        document = message.get("document")
        if isinstance(document, dict):
            attachments.append(
                {
                    "id": str(document.get("file_unique_id") or document.get("file_id") or "document"),
                    "name": str(document.get("file_name") or document.get("file_unique_id") or "document"),
                    "telegram_file_id": str(document.get("file_id") or ""),
                    "url": None,
                }
            )
        photos = message.get("photo")
        if isinstance(photos, list) and photos:
            photo = photos[-1]
            if isinstance(photo, dict):
                attachments.append(
                    {
                        "id": str(photo.get("file_unique_id") or photo.get("file_id") or "photo"),
                        "name": f"photo-{photo.get('file_unique_id') or photo.get('file_id') or 'image'}.jpg",
                        "telegram_file_id": str(photo.get("file_id") or ""),
                        "url": None,
                    }
                )
        return attachments

    def _task_from_payload(self, task_id: str, payload: dict[str, Any]) -> Task:
        return Task(
            id=task_id,
            title=str(payload.get("title") or ""),
            description=str(payload.get("description") or ""),
            status=str(payload.get("status") or ""),
            comments=[
                TaskComment(
                    id=str(item.get("id") or ""),
                    author=item.get("author"),
                    body=str(item.get("body") or ""),
                    created_at=item.get("created_at"),
                )
                for item in payload.get("comments", [])
            ],
            attachments=[
                TaskAttachment(
                    id=str(item.get("id") or ""),
                    name=str(item.get("name") or "attachment"),
                    url=item.get("url"),
                )
                for item in payload.get("attachments", [])
            ],
            metadata=dict(payload.get("metadata", {})),
        )


class SyncTelegramTrackerAdapter(SyncTrackerAdapter):
    def __init__(
        self,
        *,
        bot_token: str,
        state_path: str | Path,
        allowed_chat_ids: list[str] | None = None,
        allowed_user_ids: list[str] | None = None,
        base_url: str = "https://api.telegram.org",
    ) -> None:
        self.store = TelegramStateStore(state_path)
        self._client = httpx.Client(base_url=f"{base_url.rstrip('/')}/bot{bot_token}", timeout=30.0)
        self.allowed_chat_ids = {str(item) for item in (allowed_chat_ids or [])}
        self.allowed_user_ids = {str(item) for item in (allowed_user_ids or [])}

    def _message_timestamp(self, message: dict[str, Any]) -> str:
        return TelegramTrackerAdapter._message_timestamp(self, message)

    def get_task(self, task_id: str) -> Task:
        payload = self.store.load().tasks.get(task_id)
        if payload is None:
            raise KeyError(task_id)
        return TelegramTrackerAdapter._task_from_payload(self, task_id, payload)

    def list_status_options(self, task_id: str) -> list[str]:
        task = self.get_task(task_id)
        return ["new", "processing", "done"] if task.status else []

    def add_comment(self, task_id: str, text: str) -> None:
        state = self.store.load()
        payload = state.tasks.get(task_id)
        if payload is None:
            raise KeyError(task_id)
        response = self._client.post("/sendMessage", json={"chat_id": str(payload["chat_id"]), "text": text})
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            raise ValueError("Telegram sendMessage failed")
        message = body.get("result") or {}
        comment = TelegramTrackerAdapter._comment_payload_from_message(self, message, fallback_text=text, default_author="tiller")
        payload.setdefault("comments", []).append(comment)
        state.tasks[task_id] = payload
        self.store.save(state)

    def update_status(self, task_id: str, status: str) -> None:
        state = self.store.load()
        payload = state.tasks.get(task_id)
        if payload is None:
            raise KeyError(task_id)
        payload["status"] = status
        state.tasks[task_id] = payload
        self.store.save(state)

    def download_attachments(self, task_id: str, dest: Path) -> list[Path]:
        state = self.store.load()
        payload = state.tasks.get(task_id)
        if payload is None:
            raise KeyError(task_id)
        attachments = payload.get("attachments", [])
        if not attachments:
            return []
        dest.mkdir(parents=True, exist_ok=True)
        downloaded: list[Path] = []
        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
            for attachment in attachments:
                file_id = str(attachment.get("telegram_file_id") or "").strip()
                if not file_id:
                    continue
                file_response = self._client.get("/getFile", params={"file_id": file_id})
                file_response.raise_for_status()
                file_payload = file_response.json()
                if not file_payload.get("ok"):
                    raise ValueError("Telegram getFile failed")
                result = file_payload.get("result") or {}
                file_path = str(result.get("file_path") or "").strip()
                if not file_path:
                    continue
                filename = str(attachment.get("name") or Path(file_path).name or file_id)
                parsed = urlparse(str(self._client.base_url))
                download_url = f"{parsed.scheme}://{parsed.netloc}/file/bot{parsed.path.removeprefix('/bot')}/{file_path}"
                data_response = client.get(download_url)
                data_response.raise_for_status()
                target = dest / filename
                target.write_bytes(data_response.content)
                downloaded.append(target)
        return downloaded

    def close(self) -> None:
        self._client.close()
