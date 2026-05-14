from __future__ import annotations

import os
from pathlib import Path

import yaml

from .agents import load_harness
from .setup_prompts import (
    choose_option,
    collect_agent_config,
    collect_github_config,
    collect_projects,
    default_session_config,
    detect_project_default_branch,
    validate_project_clone_url,
)
from .setup_registry import get_setup_providers


def _github_token_from_payload(payload: dict[str, object]) -> str | None:
    token = payload.get("token")
    if isinstance(token, str) and token:
        return token
    token_env = payload.get("token_env", "GITHUB_API_TOKEN")
    if isinstance(token_env, str) and token_env:
        return os.environ.get(token_env)
    return None


def render_setup_config(payload: dict[str, object]) -> str:
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)


async def run_setup(config_path: str) -> int:
    print("Tiller setup\n")
    harness = load_harness(None)
    agents = [agent for agent in harness.discover() if agent.available]
    if not agents:
        raise RuntimeError("No supported agent CLI was found in PATH")

    agent_name = await choose_option(
        "Select the default agent:",
        [(agent.name, f"{agent.name} -> {agent.path or agent.command}") for agent in agents],
    )
    assert agent_name is not None

    providers = get_setup_providers()
    tracker_name = await choose_option(
        "Select the tracker:",
        [(provider.name, provider.label) for provider in providers],
    )
    assert tracker_name is not None
    provider = next(item for item in providers if item.name == tracker_name)
    tracker = await provider.collect()

    agent_settings = await collect_agent_config()
    github_payload = await collect_github_config()
    payload: dict[str, object] = {
        "tracker": tracker,
        "agent": {
            "default": agent_name,
            **({"model": agent_settings["model"]} if agent_settings.get("model") else {}),
        },
        "github": github_payload,
        "projects": await collect_projects(
            _github_token_from_payload(github_payload),
            validate_clone=validate_project_clone_url,
            resolve_default_branch=detect_project_default_branch,
        ),
        "session": default_session_config(),
    }

    output = Path(config_path)
    output.write_text(render_setup_config(payload), encoding="utf-8")
    print(f"\nConfiguration saved to {output}")
    return 0
