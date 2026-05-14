from __future__ import annotations

from .base import CLIAdapter, SpawnResult
from ..models import AgentRunRequest


class KiloAdapter(CLIAdapter):
    def __init__(self) -> None:
        super().__init__("kilo", "kilo", tool_transport="mcp")

    def spawn(self, request: AgentRunRequest) -> SpawnResult:
        import json as _json
        command = [self.command, "run", "--prompt", request.goal, "--yes"]
        if request.model:
            command.extend(["--model", request.model])
        if request.mcp_config:
            command.extend(["--mcp", _json.dumps(request.mcp_config)])
        return self._spawn_process(request=request, command=command)
