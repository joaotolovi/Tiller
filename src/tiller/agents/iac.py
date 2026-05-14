from __future__ import annotations

import shutil

from .base import CLIAdapter, SpawnResult
from ..models import AgentRunRequest


class IaCAdapter(CLIAdapter):
    def __init__(self) -> None:
        super().__init__("iac", "bash", tool_transport="cli")

    def is_available(self) -> bool:
        return shutil.which("bash") is not None and (
            shutil.which("terraform") is not None or shutil.which("pulumi") is not None
        )

    def spawn(self, request: AgentRunRequest) -> SpawnResult:
        runtime_dir = self._runtime_dir(request.workspace)
        script_path = runtime_dir / f"{request.agent_name}_iac.sh"
        if shutil.which("terraform"):
            script = (
                f"#!/bin/bash\n"
                f"cd {request.workspace}\n"
                f"terraform plan\n"
                f"terraform apply -auto-approve\n"
            )
        elif shutil.which("pulumi"):
            script = (
                f"#!/bin/bash\n"
                f"cd {request.workspace}\n"
                f"pulumi preview\n"
                f"pulumi up --yes\n"
            )
        else:
            raise RuntimeError("Neither terraform nor pulumi found in PATH")
        script_path.write_text(script, encoding="utf-8")
        script_path.chmod(0o755)
        command = [self.command, str(script_path)]
        return self._spawn_process(request=request, command=command)
