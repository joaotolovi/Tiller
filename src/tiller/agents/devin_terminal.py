from __future__ import annotations

from .base import CLIAdapter, SpawnResult
from ..models import AgentRunRequest


class DevinTerminalAdapter(CLIAdapter):
    def __init__(self) -> None:
        super().__init__("devin_terminal", "devin", tool_transport="cli")

    def spawn(self, request: AgentRunRequest) -> SpawnResult:
        command = [
            self.command,
            "--permission-mode", "bypass",
        ]
        if request.model:
            command.extend(["--model", request.model])
        command.extend(["--print", request.goal])
        return self._spawn_process(request=request, command=command)
