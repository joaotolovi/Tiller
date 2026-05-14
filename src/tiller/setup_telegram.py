from __future__ import annotations

from .setup_prompts import prompt_text, prompt_yes_no


class TelegramSetupProvider:
    name = "telegram"
    label = "Telegram"

    async def collect(self) -> dict[str, object]:
        bot_token = await prompt_text("Telegram bot token", secret=True)
        state_path = await prompt_text("Local state path", default="~/.tiller/telegram-state.json")
        trigger_status = (await prompt_text("Status que dispara o Tiller", default="new")).strip()
        poll_interval = int(await prompt_text("Polling interval in seconds", default="5"))

        tracker: dict[str, object] = {
            "type": "telegram",
            "bot_token": bot_token,
            "state_path": state_path,
            "trigger_status": trigger_status,
            "poll_interval": poll_interval,
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
