from __future__ import annotations

import json
from pathlib import Path

from .aider import AiderAdapter
from .base import AgentHarness, CLIAdapter
from .claude import ClaudeCodeAdapter
from .codex import CodexAdapter
from .gemini import GeminiCLIAdapter
from .opencode import OpenCodeAdapter
from .aichat import AIChatAdapter
from .amp import AmpAdapter
from .auggie import AuggieAdapter
from .autohand import AutohandAdapter
from .charm import CharmAdapter
from .cline import ClineAdapter
from .cloudflare_agents import CloudflareAgentsAdapter
from .codebuff import CodebuffAdapter
from .cody import CodyAdapter
from .composio import ComposioAdapter
from .continue_dev import ContinueDevAdapter
from .copilot import CopilotAdapter
from .cursor import CursorAdapter
from .devin_terminal import DevinTerminalAdapter
from .droid import DroidAdapter
from .forge import ForgeAdapter
from .goose import GooseAdapter
from .gptme import GptmeAdapter
from .hermes import HermesAdapter
from .iac import IaCAdapter
from .junie import JunieAdapter
from .kilo import KiloAdapter
from .kimi import KimiAdapter
from .kiro import KiroAdapter
from .letta_code import LettaCodeAdapter
from .mistral import MistralAdapter
from .ollama import OllamaAdapter
from .openai_agents import OpenAIAgentsAdapter
from .openhands import OpenHandsAdapter
from .open_interpreter import OpenInterpreterAdapter
from .pi import PiAdapter
from .plandex import PlandexAdapter
from .q_dev import QDevAdapter
from .qwen import QwenAdapter
from .ralphex import RalphexAdapter
from .rovo import RovoAdapter


class GenericCLIAdapter(CLIAdapter):
    def __init__(
        self,
        *,
        name: str,
        command: str,
        args: list[str] | None = None,
        prompt_mode: str = "arg",
    ) -> None:
        super().__init__(name, command)
        self._args = args or []
        self._prompt_mode = prompt_mode

    def spawn(self, request):
        command = [self.command, *self._args]
        stdin_payload = None
        if self._prompt_mode == "arg":
            command.append(request.goal)
        elif self._prompt_mode == "stdin":
            stdin_payload = request.goal.encode("utf-8")
        else:
            raise ValueError(f"Unsupported prompt mode: {self._prompt_mode}")
        return self._spawn_process(request=request, command=command, stdin_payload=stdin_payload)


DEFAULT_ADAPTERS: dict[str, CLIAdapter] = {
    "claude-code": ClaudeCodeAdapter(),
    "codex": CodexAdapter(),
    "opencode": OpenCodeAdapter(),
    "aider": AiderAdapter(),
    "gemini-cli": GeminiCLIAdapter(),
    "aichat": AIChatAdapter(),
    "amp": AmpAdapter(),
    "auggie": AuggieAdapter(),
    "autohand": AutohandAdapter(),
    "charm": CharmAdapter(),
    "cline": ClineAdapter(),
    "cloudflare_agents": CloudflareAgentsAdapter(),
    "codebuff": CodebuffAdapter(),
    "cody": CodyAdapter(),
    "composio": ComposioAdapter(),
    "continue_dev": ContinueDevAdapter(),
    "copilot": CopilotAdapter(),
    "cursor": CursorAdapter(),
    "devin_terminal": DevinTerminalAdapter(),
    "droid": DroidAdapter(),
    "forge": ForgeAdapter(),
    "goose": GooseAdapter(),
    "gptme": GptmeAdapter(),
    "hermes": HermesAdapter(),
    "iac": IaCAdapter(),
    "junie": JunieAdapter(),
    "kilo": KiloAdapter(),
    "kimi": KimiAdapter(),
    "kiro": KiroAdapter(),
    "letta_code": LettaCodeAdapter(),
    "mistral": MistralAdapter(),
    "ollama": OllamaAdapter(),
    "openai_agents": OpenAIAgentsAdapter(),
    "openhands": OpenHandsAdapter(),
    "open_interpreter": OpenInterpreterAdapter(),
    "pi": PiAdapter(),
    "plandex": PlandexAdapter(),
    "q_dev": QDevAdapter(),
    "qwen": QwenAdapter(),
    "ralphex": RalphexAdapter(),
    "rovo": RovoAdapter(),
}


def load_harness(adapters_path: Path | None = None) -> AgentHarness:
    adapters = dict(DEFAULT_ADAPTERS)
    if adapters_path and adapters_path.exists():
        payload = json.loads(adapters_path.read_text(encoding="utf-8"))
        for item in payload.get("adapters", []):
            adapters[item["name"]] = GenericCLIAdapter(
                name=item["name"],
                command=item["command"],
                args=item.get("args", []),
                prompt_mode=item.get("prompt_mode", "arg"),
            )
    return AgentHarness(adapters)
