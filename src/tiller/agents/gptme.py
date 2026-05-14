from __future__ import annotations

from .base import CLIAdapter, SpawnResult
from ..models import AgentRunRequest


class GptmeAdapter(CLIAdapter):
    def __init__(self) -> None:
        super().__init__("gptme", "gptme", tool_transport="mcp")

    def spawn(self, request: AgentRunRequest) -> SpawnResult:
        command = [self.command, "-n"]
        if request.model:
            command.extend(["-m", request.model])
        command.append(request.goal)
        return self._spawn_process(request=request, command=command)
