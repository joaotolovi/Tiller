from __future__ import annotations

from .base import CLIAdapter, SpawnResult
from .common import write_kimi_project_mcp
from ..models import AgentRunRequest


class KimiAdapter(CLIAdapter):
    def __init__(self) -> None:
        super().__init__("kimi", "kimi", tool_transport="mcp")

    def spawn(self, request: AgentRunRequest) -> SpawnResult:
        mcp_path = write_kimi_project_mcp(request.workspace, request.mcp_config)
        command = [self.command, "--mcp-config-file", str(mcp_path), "--yolo", "-c", request.goal]
        return self._spawn_process(request=request, command=command)
