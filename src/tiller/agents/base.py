from __future__ import annotations

import os
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..models import AgentRunRequest, AgentRunResult, DiscoveredAgent


@dataclass(slots=True)
class SpawnResult:
    adapter_name: str
    command: list[str]
    process_id: int
    log_path: Path
    process: subprocess.Popen[bytes] | None = None


class CLIAdapter(ABC):
    def __init__(self, name: str, command: str, *, tool_transport: str = "cli") -> None:
        self._name = name
        self._command = command
        self._tool_transport = tool_transport

    @property
    def name(self) -> str:
        return self._name

    @property
    def command(self) -> str:
        return self._command

    @property
    def tool_transport(self) -> str:
        return self._tool_transport

    def is_available(self) -> bool:
        return shutil.which(self._command) is not None

    def discover(self) -> DiscoveredAgent:
        return DiscoveredAgent(
            name=self._name,
            command=self._command,
            available=self.is_available(),
            path=shutil.which(self._command),
        )

    def _runtime_dir(self, workspace: Path) -> Path:
        runtime_dir = workspace / ".tiller"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        return runtime_dir

    def _spawn_process(
        self,
        *,
        request: AgentRunRequest,
        command: list[str],
        stdin_payload: bytes | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> SpawnResult:
        runtime_dir = self._runtime_dir(request.workspace)
        log_path = runtime_dir / f"{self.name}.log"
        env = {**os.environ, **request.env, **(extra_env or {})}
        with log_path.open("wb") as stream:
            process = subprocess.Popen(
                command,
                cwd=request.workspace,
                stdin=subprocess.PIPE if stdin_payload is not None else None,
                stdout=stream,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )
            if stdin_payload is not None and process.stdin is not None:
                process.stdin.write(stdin_payload)
                process.stdin.close()
        return SpawnResult(
            adapter_name=self.name,
            command=command,
            process_id=process.pid,
            log_path=log_path,
            process=process,
        )

    @abstractmethod
    def spawn(self, request: AgentRunRequest) -> SpawnResult:
        raise NotImplementedError


class AgentHarness:
    def __init__(self, adapters: dict[str, CLIAdapter]) -> None:
        self._adapters = adapters

    def discover(self) -> list[DiscoveredAgent]:
        return [adapter.discover() for adapter in self._adapters.values()]

    def tool_transport_for(self, agent_name: str) -> str:
        if agent_name not in self._adapters:
            raise ValueError(f"Unknown agent adapter: {agent_name}")
        return self._adapters[agent_name].tool_transport

    def spawn(self, request: AgentRunRequest) -> SpawnResult:
        if request.agent_name not in self._adapters:
            raise ValueError(f"Unknown agent adapter: {request.agent_name}")
        adapter = self._adapters[request.agent_name]
        if not adapter.is_available():
            raise RuntimeError(f"Agent CLI '{adapter.command}' is not installed or not in PATH")
        return adapter.spawn(request)

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        result = self.spawn(request)
        return AgentRunResult(
            adapter_name=result.adapter_name,
            command=result.command,
            process_id=result.process_id,
            log_path=result.log_path,
        )
