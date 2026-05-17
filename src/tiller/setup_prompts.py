from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
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
    if not github_enabled:
        return github

    gh_path = detect_gh_cli()
    auth_options: list[tuple[str, str]] = []
    if gh_path:
        auth_options.append(("browser", "Browser login via GitHub CLI"))
    else:
        auth_options.append(("browser_disabled", "Browser login (requires GitHub CLI)"))
    auth_options.append(("token", "API key (personal access token)"))

    auth_choice = await choose_option("Choose GitHub authentication method:", auth_options)
    assert auth_choice is not None

    github["token_env"] = "GITHUB_API_TOKEN"
    if auth_choice == "token":
        token_default = os.environ.get("GITHUB_API_TOKEN")
        github_token = await prompt_text(
            "GitHub API token (press enter to use GITHUB_API_TOKEN from the environment)",
            default=token_default or "",
            secret=True,
        )
        github["auth_method"] = "token"
        if github_token:
            github["token"] = github_token
        return github

    if auth_choice == "browser_disabled":
        print("GitHub browser login is unavailable because gh CLI is not installed.")
        github["auth_method"] = "token"
        github["browser_auth_available"] = False
        return github

    github["auth_method"] = "browser"
    github["gh_path"] = gh_path
    is_authenticated = await run_with_loading("Checking GitHub CLI authentication", is_gh_authenticated, gh_path)
    if not is_authenticated:
        print("GitHub CLI login is required. A browser or device login flow may open next.")
        await asyncio.to_thread(login_with_gh_cli, gh_path)
        confirmed = await run_with_loading("Confirming GitHub CLI authentication", is_gh_authenticated, gh_path)
        if not confirmed:
            raise ValueError("GitHub CLI authentication did not complete successfully.")
    return github


async def collect_projects(
    github_token: str | None = None,
    github_payload: dict[str, object] | None = None,
    *,
    validate_clone: Callable[[str, str | None], None] | None = None,
    resolve_default_branch: Callable[[str, str | None], str] | None = None,
    list_accessible_repos: Callable[[dict[str, object]], list[dict[str, object]]] | None = None,
) -> dict[str, dict[str, str]]:
    projects: dict[str, dict[str, str]] = {}
    github_ready = bool(github_payload and github_payload.get("enabled") and github_token)
    can_import_from_github = bool(github_ready and list_accessible_repos is not None)
    if github_payload and github_payload.get("enabled") and list_accessible_repos is not None:
        options: list[tuple[str, str]] = []
        if can_import_from_github:
            options.extend(
                [
                    ("select", "Select repositories from GitHub"),
                    ("all", "Import all accessible GitHub repositories"),
                ]
            )
        options.append(("manual", "Enter repositories manually"))
        mode = await choose_option("How do you want to configure repositories?", options)
        assert mode is not None
        if mode in {"all", "select"}:
            repo_items = await run_with_loading("Loading accessible repositories", list_accessible_repos, github_payload)
            return await _projects_from_accessible_repos(repo_items, select_mode=mode == "select")

    add_more_prompt = "Add another repository?"
    while await prompt_yes_no("Do you want to add a repository?", default=False):
        name = await prompt_text("Project name")
        description = await prompt_text("Project description (optional)", default="")
        url = await prompt_text("Repository URL")
        if validate_clone is not None:
            await run_with_loading("Validating repository access", validate_clone, url, github_token)
        default_branch = (
            await run_with_loading("Detecting default branch", resolve_default_branch, url, github_token)
            if resolve_default_branch is not None
            else "main"
        )
        project: dict[str, str] = {
            "url": url,
            "default_branch": default_branch,
        }
        if description:
            project["description"] = description
        projects[name] = project
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


async def _projects_from_accessible_repos(
    repo_items: list[dict[str, object]], *, select_mode: bool
) -> dict[str, dict[str, str]]:
    selected = repo_items
    if select_mode:
        print("Use Space to select repositories and Enter to continue.")
        while True:
            choices = [
                Choice(
                    title=f"{item['full_name']} — {item.get('description') or 'No description'}",
                    value=item,
                )
                for item in repo_items
            ]
            chosen = await questionary.checkbox("Select repositories to import", choices=choices).ask_async()
            if chosen is None:
                raise KeyboardInterrupt()
            selected = list(chosen)

            if not selected:
                print("No repositories selected yet.")
                retry = await choose_option(
                    "What do you want to do?",
                    [
                        ("change", "Alter selection"),
                        ("confirm", "Confirm selection"),
                    ],
                )
                if retry == "confirm":
                    break
                continue

            print("Selected repositories:")
            for item in selected:
                print(f"- {item['full_name']}")
            review = await choose_option(
                "What do you want to do?",
                [
                    ("confirm", "Confirm selection"),
                    ("change", "Alter selection"),
                ],
            )
            if review == "confirm":
                break

    projects: dict[str, dict[str, str]] = {}
    for item in selected:
        project_name = str(item.get("name") or item.get("full_name") or "").strip()
        if not project_name:
            continue
        project: dict[str, str] = {
            "url": str(item["url"]),
            "default_branch": str(item.get("default_branch") or "main"),
        }
        description = item.get("description")
        if isinstance(description, str) and description.strip():
            project["description"] = description.strip()
        projects[project_name] = project
    return projects


def detect_gh_cli() -> str | None:
    configured_path = os.environ.get("TILLER_GH_PATH", "").strip()
    if configured_path:
        candidate = Path(configured_path).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())
    gh_path = shutil.which("gh")
    return gh_path if gh_path else None


def is_gh_authenticated(gh_path: str) -> bool:
    status = subprocess.run([gh_path, "auth", "status"], capture_output=True, text=True, check=False)
    return status.returncode == 0


def login_with_gh_cli(gh_path: str) -> None:
    login = subprocess.run(
        [gh_path, "auth", "login", "--web", "--git-protocol", "https", "--skip-ssh-key"],
        input="Y\n",
        text=True,
        check=False,
    )
    if login.returncode != 0:
        raise ValueError("Unable to authenticate with GitHub CLI.")


def ensure_gh_authentication(gh_path: str) -> None:
    if is_gh_authenticated(gh_path):
        return
    login_with_gh_cli(gh_path)
    if not is_gh_authenticated(gh_path):
        raise ValueError("GitHub CLI authentication did not complete successfully.")


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
        "repo_store_path": "~/.tiller/repo-mirrors",
        "cleanup_after_hours": 24,
        "keep_finished_sessions": True,
    }
