from __future__ import annotations

from .base import CLIAdapter, SpawnResult
from ..models import AgentRunRequest


class CursorAdapter(CLIAdapter):
    def __init__(self) -> None:
        super().__init__("cursor", "cursor-agent", tool_transport="mcp")

    def spawn(self, request: AgentRunRequest) -> SpawnResult:
        from .common import write_cursor_project_mcp
        if request.mcp_config:
            write_cursor_project_mcp(request.workspace, request.mcp_config)
        command = [
            self.command,
            "-p",
            "--workspace", str(request.workspace),
            "--output-format", "stream-json",
            "--trust",
            "--approve-mcps",
        ]
        if request.model:
            command.extend(["--model", request.model])
        command.extend(["--force", request.goal])
        return self._spawn_process(request=request, command=command)
