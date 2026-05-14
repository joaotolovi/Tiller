from __future__ import annotations

from .base import CLIAdapter, SpawnResult
from ..models import AgentRunRequest


class GeminiCLIAdapter(CLIAdapter):
    def __init__(self) -> None:
        super().__init__("gemini-cli", "gemini", tool_transport="mcp")

    def spawn(self, request: AgentRunRequest) -> SpawnResult:
        command = [self.command, "-p", request.goal]
        if request.model:
            command.extend(["-m", request.model])
        command.extend([
            "--output-format",
            "json",
            "--yolo",
        ])
        return self._spawn_process(request=request, command=command)
