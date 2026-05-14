from __future__ import annotations

from .base import CLIAdapter, SpawnResult
from ..models import AgentRunRequest


class QwenAdapter(CLIAdapter):
    def __init__(self) -> None:
        super().__init__("qwen", "qwen", tool_transport="mcp")

    def spawn(self, request: AgentRunRequest) -> SpawnResult:
        command = [self.command, "-y"]
        if request.model:
            command.extend(["--model", request.model])
        command.append(request.goal)
        return self._spawn_process(request=request, command=command)
