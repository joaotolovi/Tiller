from __future__ import annotations

from .base import CLIAdapter, SpawnResult
from ..models import AgentRunRequest


class GooseAdapter(CLIAdapter):
    def __init__(self) -> None:
        super().__init__("goose", "goose", tool_transport="cli")

    def spawn(self, request: AgentRunRequest) -> SpawnResult:
        command = [self.command, "run", "--instruction", request.goal]
        if request.model:
            command.extend(["--model", request.model])
        return self._spawn_process(request=request, command=command)
