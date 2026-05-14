from __future__ import annotations

from .base import CLIAdapter, SpawnResult
from ..models import AgentRunRequest


class LettaCodeAdapter(CLIAdapter):
    def __init__(self) -> None:
        super().__init__("letta_code", "letta", tool_transport="cli")

    def spawn(self, request: AgentRunRequest) -> SpawnResult:
        command = [self.command, "--yolo", "-p", request.goal]
        return self._spawn_process(request=request, command=command)
