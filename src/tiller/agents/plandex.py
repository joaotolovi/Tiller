from __future__ import annotations

from .base import CLIAdapter, SpawnResult
from ..models import AgentRunRequest


class PlandexAdapter(CLIAdapter):
    def __init__(self) -> None:
        super().__init__("plandex", "plandex", tool_transport="cli")

    def spawn(self, request: AgentRunRequest) -> SpawnResult:
        command = [
            self.command,
            "tell", request.goal,
            "--apply",
            "--auto-exec",
            "--skip-menu",
            "--stop",
        ]
        return self._spawn_process(request=request, command=command)
