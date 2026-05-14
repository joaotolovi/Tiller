from __future__ import annotations

import importlib.util
import shutil

from .base import CLIAdapter, SpawnResult
from ..models import AgentRunRequest


class OpenAIAgentsAdapter(CLIAdapter):
    def __init__(self) -> None:
        super().__init__("openai_agents", "python", tool_transport="mcp")

    def is_available(self) -> bool:
        return shutil.which("python") is not None and importlib.util.find_spec("openai_agents_runner") is not None

    def spawn(self, request: AgentRunRequest) -> SpawnResult:
        import json as _json
        runtime_dir = self._runtime_dir(request.workspace)
        manifest_path = runtime_dir / "openai_agents_manifest.json"
        manifest = {
            "prompt": request.goal,
            "workdir": str(request.workspace),
            "model": request.model or "gpt-4o",
            "mcp_servers": request.mcp_config.get("servers", {}) if request.mcp_config else {},
        }
        manifest_path.write_text(_json.dumps(manifest), encoding="utf-8")
        command = [
            self.command,
            "-m", "openai_agents_runner",
            "--manifest", str(manifest_path),
        ]
        return self._spawn_process(request=request, command=command)
