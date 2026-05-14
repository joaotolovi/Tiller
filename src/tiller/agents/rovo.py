from __future__ import annotations

from .base import CLIAdapter, SpawnResult
from ..models import AgentRunRequest


class RovoAdapter(CLIAdapter):
    def __init__(self) -> None:
        super().__init__("rovo", "acli", tool_transport="cli")

    def spawn(self, request: AgentRunRequest) -> SpawnResult:
        command = [self.command, "rovodev", "run", "--yolo", request.goal]
        return self._spawn_process(request=request, command=command)
