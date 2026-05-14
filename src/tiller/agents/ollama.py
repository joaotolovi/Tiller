from __future__ import annotations

from .base import CLIAdapter, SpawnResult
from ..models import AgentRunRequest


class OllamaAdapter(CLIAdapter):
    def __init__(self) -> None:
        super().__init__("ollama", "aider", tool_transport="cli")

    def spawn(self, request: AgentRunRequest) -> SpawnResult:
        command = [
            self.command,
            "--message", request.goal,
            "--yes",
            "--auto-commits",
            "--map-tokens", "1024",
            "--no-auto-lint",
        ]
        if request.model:
            command.extend(["--model", f"ollama/{request.model}"])
        return self._spawn_process(request=request, command=command)
