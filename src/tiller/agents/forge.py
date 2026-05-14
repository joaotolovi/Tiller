from __future__ import annotations

from .base import CLIAdapter, SpawnResult
from ..models import AgentRunRequest


class ForgeAdapter(CLIAdapter):
    def __init__(self) -> None:
        super().__init__("forge", "forge", tool_transport="mcp")

    def spawn(self, request: AgentRunRequest) -> SpawnResult:
        command = [self.command, "-p", request.goal]
        return self._spawn_process(request=request, command=command)
