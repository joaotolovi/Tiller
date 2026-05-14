from __future__ import annotations

from .base import CLIAdapter, SpawnResult
from ..models import AgentRunRequest


class ClaudeCodeAdapter(CLIAdapter):
    def __init__(self) -> None:
        super().__init__("claude", "claude", tool_transport="mcp")

    def spawn(self, request: AgentRunRequest) -> SpawnResult:
        command = [
            self.command,
            "--print",
            "--permission-mode",
            "bypassPermissions",
            "--output-format",
            "text",
        ]
        if request.model:
            command.extend(["--model", request.model])
        command.extend([
            "-p",
            request.goal,
        ])
        return self._spawn_process(request=request, command=command)
