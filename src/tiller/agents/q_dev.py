from __future__ import annotations

from .base import CLIAdapter, SpawnResult
from ..models import AgentRunRequest


class QDevAdapter(CLIAdapter):
    def __init__(self) -> None:
        super().__init__("q_dev", "q", tool_transport="cli")

    def spawn(self, request: AgentRunRequest) -> SpawnResult:
        command = [
            self.command,
            "chat",
            "--no-interactive",
            "--trust-all-tools",
            request.goal,
        ]
        return self._spawn_process(request=request, command=command)
