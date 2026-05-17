from __future__ import annotations

from .setup_prompts import prompt_text, prompt_yes_no


DEFAULT_TELEGRAM_STATE_PATH = "~/.tiller/telegram-state.json"
DEFAULT_TELEGRAM_TRIGGER_STATUS = "new"
DEFAULT_TELEGRAM_POLL_INTERVAL = 5


class TelegramSetupProvider:
    name = "telegram"
    label = "Telegram"

    async def collect(self) -> dict[str, object]:
        bot_token = await prompt_text("Telegram bot token", secret=True)

        tracker: dict[str, object] = {
            "type": "telegram",
            "bot_token": bot_token,
            "state_path": DEFAULT_TELEGRAM_STATE_PATH,
            "trigger_status": DEFAULT_TELEGRAM_TRIGGER_STATUS,
            "poll_interval": DEFAULT_TELEGRAM_POLL_INTERVAL,
        }

        if await prompt_yes_no("Filter by chat IDs?", default=False):
            allowed_chat_ids = [
                item.strip()
                for item in (await prompt_text("Allowed chat IDs (comma-separated)")).split(",")
                if item.strip()
            ]
            if allowed_chat_ids:
                tracker["allowed_chat_ids"] = allowed_chat_ids

        if await prompt_yes_no("Filter by user IDs?", default=False):
            allowed_user_ids = [
                item.strip()
                for item in (await prompt_text("Allowed user IDs (comma-separated)")).split(",")
                if item.strip()
            ]
            if allowed_user_ids:
                tracker["allowed_user_ids"] = allowed_user_ids

        return tracker
