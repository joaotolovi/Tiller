from __future__ import annotations

import shutil

from .base import CLIAdapter, SpawnResult
from ..models import AgentRunRequest


class CloudflareAgentsAdapter(CLIAdapter):
    def __init__(self) -> None:
        super().__init__("cloudflare_agents", "npx", tool_transport="cli")

    def is_available(self) -> bool:
        return shutil.which("npx") is not None and shutil.which("wrangler") is not None

    def spawn(self, request: AgentRunRequest) -> SpawnResult:
        command = [self.command, "wrangler", "dev", "--var", f"AGENT_PROMPT:{request.goal}"]
        if request.model:
            command.extend(["--var", f"AGENT_MODEL:{request.model}"])
        return self._spawn_process(request=request, command=command)
