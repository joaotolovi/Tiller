from __future__ import annotations

from pathlib import Path

import yaml

from .agents import load_harness
from .github import GitHubClient
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


def _github_token_for_setup(payload: dict[str, object]) -> str | None:
    from .models import GitHubConfig

    config = GitHubConfig(
        enabled=bool(payload.get("enabled", False)),
        url=str(payload.get("url", "https://api.github.com")),
        token=payload.get("token") if isinstance(payload.get("token"), str) else None,
        token_env=str(payload.get("token_env", "GITHUB_API_TOKEN")),
        auth_method=str(payload.get("auth_method", "token")),
        gh_path=payload.get("gh_path") if isinstance(payload.get("gh_path"), str) else None,
    )
    return config.resolve_token()


def render_setup_config(payload: dict[str, object]) -> str:
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)


def _list_accessible_github_repos(github_payload: dict[str, object]) -> list[dict[str, object]]:
    from .models import GitHubConfig

    client = GitHubClient(
        GitHubConfig(
            enabled=bool(github_payload.get("enabled", False)),
            url=str(github_payload.get("url", "https://api.github.com")),
            token=github_payload.get("token") if isinstance(github_payload.get("token"), str) else None,
            token_env=str(github_payload.get("token_env", "GITHUB_API_TOKEN")),
            auth_method=str(github_payload.get("auth_method", "token")),
            gh_path=github_payload.get("gh_path") if isinstance(github_payload.get("gh_path"), str) else None,
        )
    )
    try:
        repos = client.list_accessible_repos()
        repos = sorted(repos, key=lambda repo: repo.pushed_at or "", reverse=True)
        return [
            {
                "name": repo.name,
                "full_name": repo.full_name,
                "url": repo.url,
                "default_branch": repo.default_branch,
                "description": repo.description,
                "pushed_at": repo.pushed_at,
            }
            for repo in repos
        ]
    finally:
        client.close()


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
            _github_token_for_setup(github_payload),
            github_payload,
            validate_clone=validate_project_clone_url,
            resolve_default_branch=detect_project_default_branch,
            list_accessible_repos=_list_accessible_github_repos,
        ),
        "session": default_session_config(),
    }

    output = Path(config_path)
    output.write_text(render_setup_config(payload), encoding="utf-8")
    print(f"\nConfiguration saved to {output}")
    return 0
