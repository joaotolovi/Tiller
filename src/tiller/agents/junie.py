from __future__ import annotations

from .base import CLIAdapter, SpawnResult
from ..models import AgentRunRequest


class JunieAdapter(CLIAdapter):
    def __init__(self) -> None:
        super().__init__("junie", "junie", tool_transport="mcp")

    def spawn(self, request: AgentRunRequest) -> SpawnResult:
        runtime_dir = self._runtime_dir(request.workspace)
        prompt_file = runtime_dir / "junie-prompt.txt"
        prompt_file.write_text(request.goal, encoding="utf-8")
        command = [self.command, "run", "--headless"]
        if request.model:
            command.extend(["--model", request.model])
        command.extend(["--prompt-file", str(prompt_file)])
        return self._spawn_process(request=request, command=command)
