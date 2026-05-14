from __future__ import annotations

from .base import InMemoryTrackerAdapter, TrackerAdapter
from .clickup import ClickUpTrackerAdapter
from .telegram import TelegramTrackerAdapter
from .stubs import UnsupportedTrackerAdapter


def build_tracker_adapter(tracker_type: str, **options: object) -> TrackerAdapter:
    normalized = tracker_type.strip().lower()
    if normalized == "memory":
        return InMemoryTrackerAdapter()
    if normalized == "clickup":
        token = str(options.get("token") or "")
        team_id = str(options.get("team_id") or "")
        if not token or not team_id:
            raise ValueError("ClickUp tracker requires 'token' and 'team_id'")
        tag = str(options.get("tag") or "") or None
        assignee = str(options.get("assignee") or "") or None
        return ClickUpTrackerAdapter(
            token=token,
            team_id=team_id,
            base_url=str(options.get("base_url") or "https://api.clickup.com/api/v2"),
            include_closed=bool(options.get("include_closed", False)),
            tag=tag,
            assignee=assignee,
        )
    if normalized == "telegram":
        bot_token = str(options.get("bot_token") or "")
        state_path = str(options.get("state_path") or "")
        if not bot_token or not state_path:
            raise ValueError("Telegram tracker requires 'bot_token' and 'state_path'")
        allowed_chat_ids = options.get("allowed_chat_ids")
        if allowed_chat_ids is None:
            normalized_chat_ids = None
        elif isinstance(allowed_chat_ids, list):
            normalized_chat_ids = [str(item) for item in allowed_chat_ids]
        else:
            normalized_chat_ids = [str(allowed_chat_ids)]
        allowed_user_ids = options.get("allowed_user_ids")
        if allowed_user_ids is None:
            normalized_user_ids = None
        elif isinstance(allowed_user_ids, list):
            normalized_user_ids = [str(item) for item in allowed_user_ids]
        else:
            normalized_user_ids = [str(allowed_user_ids)]
        return TelegramTrackerAdapter(
            bot_token=bot_token,
            state_path=state_path,
            allowed_chat_ids=normalized_chat_ids,
            allowed_user_ids=normalized_user_ids,
            base_url=str(options.get("base_url") or "https://api.telegram.org"),
        )
    if normalized in {"trello", "linear", "github", "github-issues"}:
        return UnsupportedTrackerAdapter(normalized)
    raise ValueError(f"Unknown tracker type: {tracker_type}")
