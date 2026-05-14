from __future__ import annotations

import shutil

from .base import CLIAdapter, SpawnResult
from ..models import AgentRunRequest


class ComposioAdapter(CLIAdapter):
    def __init__(self) -> None:
        super().__init__("composio", "ao", tool_transport="cli")

    def is_available(self) -> bool:
        return shutil.which("ao") is not None and shutil.which("composio") is not None

    def spawn(self, request: AgentRunRequest) -> SpawnResult:
        command = [self.command, "spawn", "--prompt", request.goal]
        return self._spawn_process(request=request, command=command)
