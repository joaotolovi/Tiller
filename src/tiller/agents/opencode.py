from __future__ import annotations

from .base import CLIAdapter, SpawnResult
from ..models import AgentRunRequest


class OpenCodeAdapter(CLIAdapter):
    def __init__(self) -> None:
        super().__init__("opencode", "opencode", tool_transport="mcp")

    def spawn(self, request: AgentRunRequest) -> SpawnResult:
        command = [self.command, "run"]
        if request.model:
            command.extend(["-m", request.model])
        command.extend([
            "--format",
            "json",
            request.goal,
        ])
        return self._spawn_process(request=request, command=command)
