from __future__ import annotations

from .base import CLIAdapter, SpawnResult
from ..models import AgentRunRequest


class CodexAdapter(CLIAdapter):
    def __init__(self) -> None:
        super().__init__("codex", "codex", tool_transport="mcp")

    def spawn(self, request: AgentRunRequest) -> SpawnResult:
        runtime_dir = self._runtime_dir(request.workspace)
        output_path = runtime_dir / "codex-last-message.txt"
        command = [self.command, "exec", "--sandbox", "danger-full-access"]
        if request.model:
            command.extend(["-m", request.model])
        command.extend(
            [
                "--skip-git-repo-check",
                "--json",
                "-o",
                str(output_path),
                request.goal,
            ]
        )
        return self._spawn_process(request=request, command=command)

