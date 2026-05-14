from __future__ import annotations

from .base import CLIAdapter, SpawnResult
from ..models import AgentRunRequest


class RalphexAdapter(CLIAdapter):
    def __init__(self) -> None:
        super().__init__("ralphex", "ralphex", tool_transport="cli")

    def spawn(self, request: AgentRunRequest) -> SpawnResult:
        runtime_dir = self._runtime_dir(request.workspace)
        plan_path = runtime_dir / "ralphex-plan.md"
        plan_path.write_text(f"# Plan\n\n{request.goal}\n", encoding="utf-8")
        command = [self.command, "--no-color"]
        if request.model:
            command.extend(["--task-model", request.model])
        command.append(str(plan_path))
        return self._spawn_process(request=request, command=command)
