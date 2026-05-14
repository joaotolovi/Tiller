from __future__ import annotations

from .base import CLIAdapter, SpawnResult
from .common import write_copilot_project_mcp
from ..models import AgentRunRequest


class CopilotAdapter(CLIAdapter):
    def __init__(self) -> None:
        super().__init__("copilot", "copilot", tool_transport="mcp")

    def spawn(self, request: AgentRunRequest) -> SpawnResult:
        mcp_path = write_copilot_project_mcp(request.workspace, request.mcp_config)
        command = [
            self.command,
            "--additional-mcp-config",
            str(mcp_path),
            "--allow-all-tools",
            "-i",
            request.goal,
        ]
        return self._spawn_process(request=request, command=command)
