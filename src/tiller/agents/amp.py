from __future__ import annotations

from .base import CLIAdapter, SpawnResult
from ..models import AgentRunRequest


class AmpAdapter(CLIAdapter):
    def __init__(self) -> None:
        super().__init__("amp", "amp", tool_transport="cli")

    def spawn(self, request: AgentRunRequest) -> SpawnResult:
        command = [self.command, "--headless"]
        if request.model:
            command.extend(["--model", request.model])
        command.extend(["--prompt", request.goal])
        return self._spawn_process(request=request, command=command)
