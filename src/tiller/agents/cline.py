from __future__ import annotations

from .base import CLIAdapter, SpawnResult
from ..models import AgentRunRequest


class ClineAdapter(CLIAdapter):
    def __init__(self) -> None:
        super().__init__("cline", "cline", tool_transport="cli")

    def spawn(self, request: AgentRunRequest) -> SpawnResult:
        command = [self.command, "--yolo", request.goal]
        return self._spawn_process(request=request, command=command)
