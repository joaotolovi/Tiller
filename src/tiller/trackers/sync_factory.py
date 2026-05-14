"""Factory for synchronous tracker adapters used by local commands."""

from __future__ import annotations

from .sync_base import SyncTrackerAdapter
from .sync_clickup import SyncClickUpTrackerAdapter
from .telegram import SyncTelegramTrackerAdapter


def build_sync_tracker(tracker_type: str, **options) -> SyncTrackerAdapter:
    """Build a synchronous tracker adapter from config."""
    if tracker_type == "clickup":
        token = options.pop("token")
        team_id = options.pop("team_id")
        return SyncClickUpTrackerAdapter(token=token, team_id=team_id, **options)
    if tracker_type == "telegram":
        bot_token = options.pop("bot_token")
        state_path = options.pop("state_path")
        return SyncTelegramTrackerAdapter(bot_token=bot_token, state_path=state_path, **options)
    raise ValueError(f"Unknown tracker type for sync: {tracker_type}")
