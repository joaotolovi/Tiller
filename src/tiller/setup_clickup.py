from __future__ import annotations

from .setup_prompts import choose_option, prompt_text, prompt_yes_no
from .trackers import ClickUpTrackerAdapter


class ClickUpSetupProvider:
    name = "clickup"
    label = "ClickUp"

    async def collect(self) -> dict[str, object]:
        token = await prompt_text("ClickUp token", secret=True)
        adapter = ClickUpTrackerAdapter(token=token, team_id="setup")
        try:
            teams = await adapter.list_teams()
            if not teams:
                raise RuntimeError("No ClickUp workspace was found for this token")
            team_id = await choose_option(
                "Select the ClickUp workspace:",
                [(item["id"], f"{item['name']} ({item['id']})") for item in teams],
            )
            assert team_id is not None

            trigger_status = (await prompt_text("Status that triggers Tiller", default="DEVELOP")).strip()

            tracker: dict[str, object] = {
                "type": "clickup",
                "token": token,
                "team_id": team_id,
                "trigger_status": trigger_status,
                "poll_interval": int(await prompt_text("Polling interval in seconds", default="60")),
            }

            if await prompt_yes_no("Filter by assignee?", default=False):
                members = await adapter.list_team_members(team_id)
                assignee = (
                    await choose_option(
                        "Select the assignee:",
                        [(item["id"], f"{item['name']} ({item['id']})") for item in members],
                        allow_skip=True,
                    )
                    if members
                    else None
                )
                if assignee:
                    tracker["assignee"] = assignee

            if await prompt_yes_no("Filter by tag?", default=False):
                tags = await adapter.list_team_tags(team_id)
                tag = (
                    await choose_option(
                        "Tag name",
                        [(tag, tag) for tag in tags],
                        allow_skip=True,
                    )
                    if tags
                    else (await prompt_text("Tag name", default="")).strip()
                )
                if tag:
                    tracker["tag"] = tag

            return tracker
        finally:
            await adapter.aclose()
