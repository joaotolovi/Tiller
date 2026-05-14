from __future__ import annotations

from .base import CLIAdapter, SpawnResult
from ..models import AgentRunRequest


class OpenHandsAdapter(CLIAdapter):
    def __init__(self) -> None:
        super().__init__("openhands", "openhands", tool_transport="cli")

    def spawn(self, request: AgentRunRequest) -> SpawnResult:
        command = [
            self.command,
            "--headless",
            "--override-with-envs",
            "-t", request.goal,
        ]
        return self._spawn_process(request=request, command=command)
