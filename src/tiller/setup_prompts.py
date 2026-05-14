from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from pathlib import Path
from typing import Sequence

import questionary
from questionary import Choice

from .repo_seed import RepoSeedManager


async def prompt_text(prompt: str, *, default: str | None = None, secret: bool = False) -> str:
    question = questionary.password(prompt, default=default or "") if secret else questionary.text(prompt, default=default or "")
    value = await question.ask_async()
    if value is None:
        raise KeyboardInterrupt()
    value = value.strip()
    if value:
        return value
    if default is not None:
        return default
    raise ValueError(f"{prompt} is required")


async def prompt_yes_no(prompt: str, *, default: bool = True) -> bool:
    value = await questionary.confirm(prompt, default=default).ask_async()
    if value is None:
        raise KeyboardInterrupt()
    return bool(value)


async def choose_option(title: str, options: Sequence[tuple[str, str]], *, allow_skip: bool = False) -> str | None:
    choices = [Choice(title=label, value=value) for value, label in options]
    if allow_skip:
        choices.append(Choice(title="Skip", value=None))
    value = await questionary.select(title, choices=choices).ask_async()
    if value is None and not allow_skip:
        raise KeyboardInterrupt()
    return value


async def collect_agent_config() -> dict[str, object]:
    return {
        "model": await prompt_text("Agent model (optional)", default=""),
    }


async def collect_github_config() -> dict[str, object]:
    github_enabled = await prompt_yes_no("Enable GitHub integration?", default=True)
    github: dict[str, object] = {"enabled": github_enabled, "url": "https://api.github.com"}
    if github_enabled:
        token_default = os.environ.get("GITHUB_API_TOKEN")
        github_token = await prompt_text(
            "GitHub API token (press enter to use GITHUB_API_TOKEN from the environment)",
            default=token_default or "",
            secret=True,
        )
        if github_token:
            github["token"] = github_token
        github["token_env"] = "GITHUB_API_TOKEN"
    return github


async def collect_projects(
    github_token: str | None = None,
    *,
    validate_clone: Callable[[str, str | None], None] | None = None,
    resolve_default_branch: Callable[[str, str | None], str] | None = None,
) -> dict[str, dict[str, str]]:
    projects: dict[str, dict[str, str]] = {}
    add_more_prompt = "Add another repository?"
    while await prompt_yes_no("Do you want to add a repository?", default=False):
        name = await prompt_text("Project name")
        url = await prompt_text("Repository URL")
        if validate_clone is not None:
            await run_with_loading("Validating repository access", validate_clone, url, github_token)
        default_branch = (
            await run_with_loading("Detecting default branch", resolve_default_branch, url, github_token)
            if resolve_default_branch is not None
            else "main"
        )
        projects[name] = {
            "url": url,
            "default_branch": default_branch,
        }
        if not await prompt_yes_no(add_more_prompt, default=False):
            break
    return projects


def detect_project_default_branch(url: str, github_token: str | None = None) -> str:
    manager = RepoSeedManager(storage_root=_setup_validation_root(), clone_token=github_token)
    try:
        return manager.detect_project_default_branch(url)
    except RuntimeError as exc:
        raise ValueError(f"Unable to detect default branch for repository '{url}': {exc}") from exc


def validate_project_clone_url(url: str, github_token: str | None = None) -> None:
    manager = RepoSeedManager(storage_root=_setup_validation_root(), clone_token=github_token)
    try:
        manager.validate_project_access(url)
    except RuntimeError as exc:
        raise ValueError(f"Unable to access repository URL '{url}': {exc}") from exc


def _setup_validation_root() -> Path:
    return Path(os.path.expanduser("~/.tiller/setup-validation"))


async def run_with_loading(message: str, func: Callable[..., object], *args: object) -> object:
    stop = asyncio.Event()
    spinner = asyncio.create_task(_show_loading(message, stop))
    try:
        result = await asyncio.to_thread(func, *args)
    finally:
        stop.set()
        await spinner
    print(f"✓ {message}")
    return result


async def _show_loading(message: str, stop: asyncio.Event) -> None:
    frames = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
    index = 0
    while not stop.is_set():
        print(f"\r{frames[index % len(frames)]} {message}...", end="", flush=True)
        index += 1
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.1)
        except TimeoutError:
            continue
    print(f"\r{' ' * (len(message) + 6)}\r", end="", flush=True)


def default_session_config() -> dict[str, object]:
    return {
        "base_path": "~/.tiller/sessions",
        "cleanup_after_hours": 24,
        "keep_finished_sessions": True,
    }
