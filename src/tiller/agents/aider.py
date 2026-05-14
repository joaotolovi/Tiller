from __future__ import annotations

from .base import CLIAdapter, SpawnResult
from ..models import AgentRunRequest


class AiderAdapter(CLIAdapter):
    def __init__(self) -> None:
        super().__init__("aider", "aider")

    def spawn(self, request: AgentRunRequest) -> SpawnResult:
        command = [self.command]
        if request.model:
            command.extend(["--model", request.model])
        command.extend([
            "--message",
            request.goal,
            "--yes",
            "--auto-commits",
            "--map-tokens",
            "2048",
            "--no-auto-lint",
        ])
        return self._spawn_process(request=request, command=command)
