from __future__ import annotations

from .setup_clickup import ClickUpSetupProvider
from .setup_telegram import TelegramSetupProvider
from .setup_types import SetupProvider


def get_setup_providers() -> list[SetupProvider]:
    return [ClickUpSetupProvider(), TelegramSetupProvider()]
