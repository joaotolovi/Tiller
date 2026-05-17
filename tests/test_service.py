from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tiller.agents import AgentHarness, CLIAdapter, SpawnResult
from tiller.agents.kimi import KimiAdapter
from tiller.agents.copilot import CopilotAdapter
from tiller.setup import render_setup_config, run_setup, _list_accessible_github_repos
from tiller.config import load_config
from tiller.mcp.config import write_mcp_config
from tiller.mcp.server import build_tracker_server
from tiller.agents.common import write_native_mcp_project_files
from tiller.setup_prompts import (
    collect_projects,
    detect_project_default_branch,
    validate_project_clone_url,
    collect_github_config,
    detect_gh_cli,
    ensure_gh_authentication,
    is_gh_authenticated,
    login_with_gh_cli,
)
from tiller.github import GitHubClient, PullRequestRef, GitHubAccessibleRepo
from tiller.models import AgentRunRequest, DiscoveredAgent, GitHubConfig, ProjectSpec, SessionPaths, Task, TaskComment, TaskControlRequest
from tiller.runtime import serialize_task
from tiller.pr_providers import GitHubPullRequestProvider, get_pull_request_provider
from tiller.service import TillerService, MultiTrackerService, TrackerService
from tiller.trackers import InMemoryTrackerAdapter, ClickUpTrackerAdapter, TelegramTrackerAdapter
from tiller.trackers.telegram import TelegramTrackerState, TelegramStateStore
from tiller.trackers.factory import build_tracker_adapter
from tiller.trackers.sync_factory import build_sync_tracker
from tiller.trackers.sync_clickup import SyncClickUpTrackerAdapter
from tiller.trackers.sync_base import SyncTrackerAdapter
from tiller.setup_clickup import ClickUpSetupProvider
from tiller.setup_telegram import TelegramSetupProvider
from tiller.repo_seed import RepoSeedManager, discover_local_projects
from tiller.templates import render_agents_md
from tiller.mcp.server import build_tracker_server
from tiller.agents.cloudflare_agents import CloudflareAgentsAdapter
from tiller.agents.composio import ComposioAdapter
from tiller.agents.iac import IaCAdapter
from tiller.agents.openai_agents import OpenAIAgentsAdapter


class QuestionaryPromptStub:
    def __init__(self, answer):
        self._answer = answer

    def ask(self):
        return self._answer

    async def ask_async(self):
        return self._answer


def install_questionary_stub(monkeypatch, *, text_answers=None, password_answers=None, confirm_answers=None, select_answers=None, checkbox_answers=None) -> None:
    text_iter = iter(text_answers or [])
    password_iter = iter(password_answers or [])
    confirm_iter = iter(confirm_answers or [])
    select_iter = iter(select_answers or [])
    checkbox_iter = iter(checkbox_answers or [])

    monkeypatch.setattr("tiller.setup_prompts.questionary.text", lambda *args, **kwargs: QuestionaryPromptStub(next(text_iter)))
    monkeypatch.setattr("tiller.setup_prompts.questionary.password", lambda *args, **kwargs: QuestionaryPromptStub(next(password_iter)))
    monkeypatch.setattr("tiller.setup_prompts.questionary.confirm", lambda *args, **kwargs: QuestionaryPromptStub(next(confirm_iter)))
    monkeypatch.setattr("tiller.setup_prompts.questionary.select", lambda *args, **kwargs: QuestionaryPromptStub(next(select_iter)))
    monkeypatch.setattr("tiller.setup_prompts.questionary.checkbox", lambda *args, **kwargs: QuestionaryPromptStub(next(checkbox_iter)))


def test_unix_installer_installs_gh_after_source_sync() -> None:
    installer = Path("installers/install.sh").read_text(encoding="utf-8")

    ensure_gh_index = installer.index("  ensure_gh\n")
    download_source_index = installer.index("  download_source\n")
    ensure_runtime_index = installer.index("  ensure_runtime\n")

    assert download_source_index < ensure_runtime_index < ensure_gh_index


def test_windows_installer_installs_gh_after_source_sync() -> None:
    installer = Path("installers/install.ps1").read_text(encoding="utf-8")

    gh_index = installer.index("$GhPath = Get-GhPath")
    download_source_index = installer.index("Download-Source")
    sync_runtime_index = installer.index("Sync-Runtime -UvPath $UvPath")

    assert download_source_index < sync_runtime_index < gh_index


def test_detect_gh_cli_prefers_installer_path(monkeypatch, tmp_path: Path) -> None:
    gh_path = tmp_path / "gh"
    gh_path.write_text("#!/bin/sh\n", encoding="utf-8")
    gh_path.chmod(0o755)

    monkeypatch.setenv("TILLER_GH_PATH", str(gh_path))
    monkeypatch.setattr("tiller.setup_prompts.shutil.which", lambda command: None)

    assert detect_gh_cli() == str(gh_path.resolve())


def test_collect_projects_selects_github_repositories_async(monkeypatch) -> None:
    install_questionary_stub(
        monkeypatch,
        select_answers=["select", "confirm"],
        checkbox_answers=[
            [
                {
                    "name": "frontend",
                    "full_name": "org/frontend",
                    "url": "https://github.com/org/frontend",
                    "default_branch": "main",
                    "description": "Frontend app",
                    "pushed_at": "2026-05-15T10:00:00Z",
                }
            ]
        ],
    )

    async def fake_run_with_loading(message, func, *args):
        return [
            {
                "name": "frontend",
                "full_name": "org/frontend",
                "url": "https://github.com/org/frontend",
                "default_branch": "main",
                "description": "Frontend app",
                "pushed_at": "2026-05-15T10:00:00Z",
            },
            {
                "name": "backend",
                "full_name": "org/backend",
                "url": "https://github.com/org/backend",
                "default_branch": "main",
                "description": "Backend app",
                "pushed_at": "2026-05-10T10:00:00Z",
            },
        ]

    monkeypatch.setattr("tiller.setup_prompts.run_with_loading", fake_run_with_loading)

    projects = asyncio.run(
        collect_projects(
            github_token="token",
            github_payload={"enabled": True},
            list_accessible_repos=lambda payload: [],
        )
    )

    assert projects == {
        "frontend": {
            "url": "https://github.com/org/frontend",
            "default_branch": "main",
            "description": "Frontend app",
        }
    }



def test_collect_github_config_supports_browser_auth_when_gh_is_available(monkeypatch) -> None:
    monkeypatch.setattr("tiller.setup_prompts.detect_gh_cli", lambda: "/tmp/gh")
    monkeypatch.setattr("tiller.setup_prompts.run_with_loading", lambda message, func, *args: asyncio.sleep(0, result=True))
    login = Mock()
    monkeypatch.setattr("tiller.setup_prompts.login_with_gh_cli", login)

    install_questionary_stub(
        monkeypatch,
        select_answers=["browser"],
        confirm_answers=[True],
    )

    github = asyncio.run(collect_github_config())

    assert github["enabled"] is True
    assert github["auth_method"] == "browser"
    assert github["gh_path"] == "/tmp/gh"
    login.assert_not_called()


def test_collect_github_config_runs_login_when_not_authenticated(monkeypatch) -> None:
    monkeypatch.setattr("tiller.setup_prompts.detect_gh_cli", lambda: "/tmp/gh")

    responses = iter([False, True])

    async def fake_run_with_loading(message, func, *args):
        return next(responses)

    login = Mock()
    monkeypatch.setattr("tiller.setup_prompts.run_with_loading", fake_run_with_loading)
    monkeypatch.setattr("tiller.setup_prompts.login_with_gh_cli", login)

    install_questionary_stub(
        monkeypatch,
        select_answers=["browser"],
        confirm_answers=[True],
    )

    github = asyncio.run(collect_github_config())

    assert github["auth_method"] == "browser"
    login.assert_called_once_with("/tmp/gh")


    monkeypatch.setattr("tiller.setup_prompts.detect_gh_cli", lambda: None)

    install_questionary_stub(
        monkeypatch,
        select_answers=["browser_disabled"],
        confirm_answers=[True],
    )

    github = asyncio.run(collect_github_config())

    assert github["enabled"] is True
    assert github["auth_method"] == "token"
    assert github["browser_auth_available"] is False
    assert "gh_path" not in github


def test_detect_gh_cli_prefers_environment_path(monkeypatch) -> None:
    monkeypatch.setattr("tiller.setup_prompts.shutil.which", lambda command: "/usr/local/bin/gh" if command == "gh" else None)
    assert detect_gh_cli() == "/usr/local/bin/gh"


def test_ensure_gh_authentication_logs_in_when_needed(monkeypatch) -> None:
    calls: list[list[str]] = []

    class Completed:
        def __init__(self, returncode: int) -> None:
            self.returncode = returncode
            self.stdout = ""
            self.stderr = ""

    def fake_run(command, capture_output=False, text=False, check=False, input=None):
        calls.append(command)
        if command[-2:] == ["auth", "status"] and len(calls) == 1:
            return Completed(1)
        if command[1:] == ["auth", "login", "--web", "--git-protocol", "https", "--skip-ssh-key"]:
            assert input == "Y\n"
            assert text is True
            return Completed(0)
        return Completed(0)

    monkeypatch.setattr("tiller.setup_prompts.subprocess.run", fake_run)

    ensure_gh_authentication("/tmp/gh")

    assert calls == [
        ["/tmp/gh", "auth", "status"],
        ["/tmp/gh", "auth", "login", "--web", "--git-protocol", "https", "--skip-ssh-key"],
        ["/tmp/gh", "auth", "status"],
    ]


def test_login_with_gh_cli_uses_non_interactive_flags(monkeypatch) -> None:
    calls: list[list[str]] = []

    class Completed:
        def __init__(self, returncode: int) -> None:
            self.returncode = returncode

    def fake_run(command, input=None, text=False, check=False):
        calls.append(command)
        assert input == "Y\n"
        assert text is True
        return Completed(0)

    monkeypatch.setattr("tiller.setup_prompts.subprocess.run", fake_run)

    login_with_gh_cli("/tmp/gh")

    assert calls == [["/tmp/gh", "auth", "login", "--web", "--git-protocol", "https", "--skip-ssh-key"]]


def test_login_with_gh_cli_raises_on_login_failure(monkeypatch) -> None:
    class Completed:
        def __init__(self, returncode: int) -> None:
            self.returncode = returncode

    monkeypatch.setattr(
        "tiller.setup_prompts.subprocess.run",
        lambda command, input=None, text=False, check=False: Completed(1),
    )

    with pytest.raises(ValueError, match="Unable to authenticate with GitHub CLI"):
        login_with_gh_cli("/tmp/gh")


def test_is_gh_authenticated_returns_false_when_status_fails(monkeypatch) -> None:
    class Completed:
        def __init__(self, returncode: int) -> None:
            self.returncode = returncode
            self.stdout = ""
            self.stderr = ""

    monkeypatch.setattr(
        "tiller.setup_prompts.subprocess.run",
        lambda command, capture_output=False, text=False, check=False: Completed(1),
    )

    assert is_gh_authenticated("/tmp/gh") is False


def test_github_config_resolve_token_uses_gh_for_browser_auth(monkeypatch) -> None:
    class Completed:
        def __init__(self, returncode: int, stdout: str) -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ""

    monkeypatch.delenv("GITHUB_API_TOKEN", raising=False)
    monkeypatch.setattr("subprocess.run", lambda command, capture_output, text, check: Completed(0, "gh-token\n"))

    config = GitHubConfig(enabled=True, auth_method="browser", gh_path="/tmp/gh")

    assert config.resolve_token() == "gh-token"


def test_collect_projects_shows_loading_during_validation(monkeypatch, capsys) -> None:
    install_questionary_stub(
        monkeypatch,
        text_answers=["backend", "", "git@github.com:org/repo.git"],
        confirm_answers=[True, False],
    )

    def fake_validate_project_clone_url(url: str, github_token: str | None) -> None:
        assert url == "git@github.com:org/repo.git"
        assert github_token == "gh-token"

    def fake_detect_project_default_branch(url: str, github_token: str | None) -> str:
        assert url == "git@github.com:org/repo.git"
        assert github_token == "gh-token"
        return "main"

    projects = asyncio.run(
        collect_projects(
            "gh-token",
            validate_clone=fake_validate_project_clone_url,
            resolve_default_branch=fake_detect_project_default_branch,
        )
    )

    assert projects == {
        "backend": {
            "url": "git@github.com:org/repo.git",
            "default_branch": "main",
        }
    }
    output = capsys.readouterr().out
    assert "Validating repository access" in output
    assert "Detecting default branch" in output


def test_collect_projects_hides_github_import_without_auth(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_choose_option(title: str, options, *, allow_skip: bool = False):
        captured["title"] = title
        captured["options"] = options
        return "manual"

    monkeypatch.setattr("tiller.setup_prompts.choose_option", fake_choose_option)
    install_questionary_stub(
        monkeypatch,
        confirm_answers=[False],
    )

    projects = asyncio.run(
        collect_projects(
            None,
            {"enabled": True, "auth_method": "token", "browser_auth_available": False},
            list_accessible_repos=lambda payload: [_ for _ in ()],
        )
    )

    assert projects == {}
    assert captured["title"] == "How do you want to configure repositories?"
    assert captured["options"] == [("manual", "Enter repositories manually")]


def test_collect_projects_can_import_all_accessible_repos(monkeypatch) -> None:
    install_questionary_stub(
        monkeypatch,
        select_answers=["all"],
    )

    projects = asyncio.run(
        collect_projects(
            "gh-token",
            {"enabled": True},
            list_accessible_repos=lambda payload: [
                {
                    "name": "frontend",
                    "full_name": "org/frontend",
                    "url": "https://github.com/org/frontend.git",
                    "default_branch": "main",
                    "description": "Frontend app",
                },
                {
                    "name": "backend",
                    "full_name": "org/backend",
                    "url": "https://github.com/org/backend.git",
                    "default_branch": "develop",
                    "description": None,
                },
            ],
        )
    )

    assert projects == {
        "frontend": {
            "url": "https://github.com/org/frontend.git",
            "default_branch": "main",
            "description": "Frontend app",
        },
        "backend": {
            "url": "https://github.com/org/backend.git",
            "default_branch": "develop",
        },
    }


def test_collect_projects_uses_repo_name_when_importing_accessible_repos(monkeypatch) -> None:
    install_questionary_stub(
        monkeypatch,
        select_answers=["all"],
    )

    projects = asyncio.run(
        collect_projects(
            "gh-token",
            {"enabled": True},
            list_accessible_repos=lambda payload: [
                {
                    "name": "payments-api",
                    "full_name": "org/payments-api",
                    "url": "https://github.com/org/payments-api.git",
                    "default_branch": "main",
                    "description": "Payments service",
                }
            ],
        )
    )

    assert list(projects.keys()) == ["payments-api"]
    assert projects["payments-api"]["description"] == "Payments service"


def test_collect_projects_can_select_accessible_repos(monkeypatch) -> None:
    selected = [
        {
            "name": "backend",
            "full_name": "org/backend",
            "url": "https://github.com/org/backend.git",
            "default_branch": "main",
            "description": "Backend service",
        },
        {
            "name": "frontend",
            "full_name": "org/frontend",
            "url": "https://github.com/org/frontend.git",
            "default_branch": "main",
            "description": "Frontend app",
        },
    ]
    install_questionary_stub(
        monkeypatch,
        select_answers=["select", "confirm"],
        checkbox_answers=[selected],
    )

    projects = asyncio.run(
        collect_projects(
            "gh-token",
            {"enabled": True},
            list_accessible_repos=lambda payload: [
                {
                    "name": "frontend",
                    "full_name": "org/frontend",
                    "url": "https://github.com/org/frontend.git",
                    "default_branch": "main",
                    "description": "Frontend app",
                },
                {
                    "name": "backend",
                    "full_name": "org/backend",
                    "url": "https://github.com/org/backend.git",
                    "default_branch": "main",
                    "description": "Backend service",
                },
            ],
        )
    )

    assert list(projects.keys()) == ["backend", "frontend"]
    assert projects["backend"]["description"] == "Backend service"
    assert projects["frontend"]["description"] == "Frontend app"


def test_collect_projects_allows_reviewing_checkbox_selection(monkeypatch) -> None:
    first_selection = [
        {
            "name": "backend",
            "full_name": "org/backend",
            "url": "https://github.com/org/backend.git",
            "default_branch": "main",
            "description": "Backend service",
        }
    ]
    final_selection = [
        {
            "name": "backend",
            "full_name": "org/backend",
            "url": "https://github.com/org/backend.git",
            "default_branch": "main",
            "description": "Backend service",
        },
        {
            "name": "frontend",
            "full_name": "org/frontend",
            "url": "https://github.com/org/frontend.git",
            "default_branch": "main",
            "description": "Frontend app",
        },
    ]
    install_questionary_stub(
        monkeypatch,
        select_answers=["select", "change", "confirm"],
        checkbox_answers=[first_selection, final_selection],
    )

    projects = asyncio.run(
        collect_projects(
            "gh-token",
            {"enabled": True},
            list_accessible_repos=lambda payload: [
                {
                    "name": "frontend",
                    "full_name": "org/frontend",
                    "url": "https://github.com/org/frontend.git",
                    "default_branch": "main",
                    "description": "Frontend app",
                },
                {
                    "name": "backend",
                    "full_name": "org/backend",
                    "url": "https://github.com/org/backend.git",
                    "default_branch": "main",
                    "description": "Backend service",
                },
            ],
        )
    )

    assert list(projects.keys()) == ["backend", "frontend"]


def test_list_accessible_github_repos_serializes_repos(monkeypatch) -> None:
    repos = [
        GitHubAccessibleRepo(
            name="backend",
            owner="org",
            full_name="org/backend",
            url="https://github.com/org/backend.git",
            default_branch="main",
            description="Backend service",
            private=True,
            pushed_at="2026-05-10T10:00:00Z",
        ),
        GitHubAccessibleRepo(
            name="frontend",
            owner="org",
            full_name="org/frontend",
            url="https://github.com/org/frontend.git",
            default_branch="main",
            description="Frontend app",
            private=False,
            pushed_at="2026-05-15T10:00:00Z",
        ),
    ]

    class FakeClient:
        def __init__(self, config) -> None:
            self.config = config

        def list_accessible_repos(self):
            return repos

        def close(self) -> None:
            return None

    monkeypatch.setattr("tiller.setup.GitHubClient", FakeClient)

    payload = _list_accessible_github_repos({"enabled": True, "token": "gh-token"})

    assert payload == [
        {
            "name": "frontend",
            "full_name": "org/frontend",
            "url": "https://github.com/org/frontend.git",
            "default_branch": "main",
            "description": "Frontend app",
            "pushed_at": "2026-05-15T10:00:00Z",
        },
        {
            "name": "backend",
            "full_name": "org/backend",
            "url": "https://github.com/org/backend.git",
            "default_branch": "main",
            "description": "Backend service",
            "pushed_at": "2026-05-10T10:00:00Z",
        },
    ]


def test_detect_project_default_branch_parses_head_reference(monkeypatch) -> None:
    class Completed:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(command, cwd, capture_output, text):
        assert capture_output is True
        assert text is True
        assert cwd.name == "repo-mirrors"
        if command == ["git", "ls-remote", "git@github.com:org/repo.git", "HEAD"]:
            return Completed(0, "abc123\tHEAD\n")
        if command == ["git", "ls-remote", "--symref", "git@github.com:org/repo.git", "HEAD"]:
            return Completed(0, "ref: refs/heads/master\tHEAD\nabc123\tHEAD\n")
        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr("tiller.repo_seed.subprocess.run", fake_run)

    assert detect_project_default_branch("git@github.com:org/repo.git") == "master"


def test_detect_project_default_branch_raises_when_git_fails(monkeypatch) -> None:
    class Completed:
        returncode = 1
        stdout = ""
        stderr = "auth failed"

    monkeypatch.setattr("tiller.repo_seed.subprocess.run", lambda *args, **kwargs: Completed())

    try:
        detect_project_default_branch("git@github.com:org/private.git")
    except ValueError as exc:
        assert "Unable to detect default branch" in str(exc)
        assert "auth failed" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_validate_project_clone_url_uses_access_check_with_fallbacks(monkeypatch) -> None:
    class Completed:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    commands: list[list[str]] = []

    def fake_run(command, cwd, capture_output, text):
        commands.append(command)
        assert capture_output is True
        assert text is True
        assert cwd.name == "repo-mirrors"
        if command == ["git", "ls-remote", "https://github.com/org/private.git", "HEAD"]:
            return Completed(1, stderr="https failed")
        if command == ["git", "ls-remote", "git@github.com:org/private.git", "HEAD"]:
            return Completed(0, stdout="abc123\tHEAD\n")
        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr("tiller.repo_seed.subprocess.run", fake_run)

    validate_project_clone_url("https://github.com/org/private.git", "gh-token")

    assert commands == [
        ["git", "ls-remote", "https://github.com/org/private.git", "HEAD"],
        ["git", "ls-remote", "git@github.com:org/private.git", "HEAD"],
    ]


class StubAdapter(CLIAdapter):
    def __init__(self) -> None:
        super().__init__("stub", "python")

    def is_available(self) -> bool:
        return True

    def spawn(self, request):
        runtime_dir = request.workspace / ".tiller"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        log_path = runtime_dir / "stub.log"
        process = __import__("subprocess").Popen(
            ["python", "-c", "import time; time.sleep(0.05)"],
            cwd=request.workspace,
            stdout=log_path.open("wb"),
            stderr=__import__("subprocess").STDOUT,
            start_new_session=True,
        )
        return SpawnResult(
            adapter_name=self.name,
            command=["python", "-c", "import time; time.sleep(0.05)"],
            process_id=process.pid,
            log_path=log_path,
            process=process,
        )


class StubSyncTracker(SyncTrackerAdapter):
    def __init__(self) -> None:
        self.statuses = ["Backlog", "In Progress", "Done"]

    def get_task(self, task_id: str) -> Task:
        return Task(id=task_id, title="Demo", description="Body", status="Backlog")

    def list_status_options(self) -> list[str]:
        return self.statuses

    def add_comment(self, task_id: str, text: str) -> None:
        return None

    def update_status(self, task_id: str, status: str) -> None:
        return None

    def download_attachments(self, task_id: str, dest: Path) -> list[Path]:
        return []


class AvailableSetupAdapter(CLIAdapter):
    def __init__(self, name: str, command: str) -> None:
        super().__init__(name, command)

    def is_available(self) -> bool:
        return True

    def discover(self) -> DiscoveredAgent:
        return DiscoveredAgent(name=self.name, command=self.command, available=True, path=f"/usr/bin/{self.command}")

    def spawn(self, request):
        raise AssertionError("setup should not spawn agents")


def test_service_marks_task_processing_and_prepares_session(tmp_path: Path) -> None:
    config_path = tmp_path / "tiller.yaml"
    config_path.write_text(
        """
tracker:
  type: memory
  trigger_status: in_development
  poll_interval: 30
  processing_status: processing
agent:
  default: stub
projects:
  repo:
    url: https://github.com/org/repo
session:
  base_path: %s
  keep_finished_sessions: true
""" % tmp_path.as_posix(),
        encoding="utf-8",
    )

    config = load_config(config_path)
    tracker = InMemoryTrackerAdapter(
        [Task(id="1", title="Demo", description="Body", status="in_development")]
    )
    harness = AgentHarness({"stub": StubAdapter()})
    service = TillerService(config=config, tracker_config=config.tracker, tracker=tracker, harness=harness)

    async def run_test() -> None:
        await service.run_once()
        task_ref = service._task_ref("1")
        active = service.active_sessions[task_ref]
        await active

    asyncio.run(run_test())

    session_root = config.session.base_path / service.session_manager.make_internal_task_id(tracker._tasks["1"])
    assert tracker._tasks["1"].status == "processing"
    assert tracker.comments["1"][0].startswith("Starting task. Analyzing")
    assert tracker.comments["1"][-1].startswith("Task completed")
    assert (session_root / "AGENTS.md").exists()
    assert (session_root / "TASK.md").exists()
    assert (session_root / "task.json").exists()
    assert (session_root / "STATE.md").exists()
    assert (session_root / "projects.json").exists()
    assert (session_root / ".mcp" / "config.json").exists()
    assert (session_root / "messages.jsonl").exists()
    assert (session_root / "events.jsonl").exists()

    session_payload = json.loads((session_root / "session.json").read_text(encoding="utf-8"))
    assert session_payload["tracker_task_id"] == "1"
    assert session_payload["external_task_id"] == "1"
    assert session_payload["tracker_type"] == "memory"
    assert session_payload["state"] == "completed"

    task_payload = json.loads((session_root / "task.json").read_text(encoding="utf-8"))
    assert task_payload["id"] == "1"
    assert task_payload["tracker_type"] == "memory"
    assert task_payload["title"] == "Demo"
    assert task_payload["status"] == "processing"

    message_lines = [json.loads(line) for line in (session_root / "messages.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(item["direction"] == "outbound" and item["body"].startswith("Starting task. Analyzing") for item in message_lines)
    assert any(item["direction"] == "outbound" and item["body"].startswith("Task completed") for item in message_lines)

    event_lines = [json.loads(line) for line in (session_root / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    event_types = {item["type"] for item in event_lines}
    assert {"agent_ready", "agent_started", "agent_stopped"}.issubset(event_types)


def test_service_comments_error_when_agent_spawn_fails(tmp_path: Path) -> None:
    config_path = tmp_path / "tiller.yaml"
    config_path.write_text(
        """
tracker:
  type: memory
  trigger_status: in_development
  poll_interval: 30
  processing_status: processing
agent:
  default: stub
projects:
  repo:
    url: https://github.com/org/repo
session:
  base_path: %s
  keep_finished_sessions: true
""" % tmp_path.as_posix(),
        encoding="utf-8",
    )

    class FailingStubAdapter(CLIAdapter):
        def __init__(self) -> None:
            super().__init__("stub", "python")

        def is_available(self) -> bool:
            return True

        def spawn(self, request):
            raise RuntimeError("failed to initialize agent cli")

    config = load_config(config_path)
    tracker = InMemoryTrackerAdapter(
        [Task(id="1", title="Demo", description="Body", status="in_development")]
    )
    harness = AgentHarness({"stub": FailingStubAdapter()})
    service = TillerService(config=config, tracker_config=config.tracker, tracker=tracker, harness=harness)

    async def run_test() -> None:
        await service.run_once()
        task_ref = service._task_ref("1")
        active = service.active_sessions[task_ref]
        with pytest.raises(RuntimeError, match="failed to initialize agent cli"):
            await active

    asyncio.run(run_test())

    session_root = config.session.base_path / service.session_manager.make_internal_task_id(tracker._tasks["1"])
    assert tracker._tasks["1"].status == "processing"
    assert tracker.comments["1"][0].startswith("Starting task. Analyzing")
    assert tracker.comments["1"][-1].startswith("Task failed before completion")
    assert "failed to initialize agent cli" in tracker.comments["1"][-1]

    session_payload = json.loads((session_root / "session.json").read_text(encoding="utf-8"))
    assert session_payload["state"] == "failed"
    assert session_payload["process_id"] is None

    message_lines = [json.loads(line) for line in (session_root / "messages.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(item["direction"] == "outbound" and item["body"].startswith("Task failed before completion") for item in message_lines)

    event_lines = [json.loads(line) for line in (session_root / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(item["type"] == "session_failed" for item in event_lines)


def test_service_cancellation_terminates_agent_process(tmp_path: Path) -> None:
    config_path = tmp_path / "tiller.yaml"
    config_path.write_text(
        """
tracker:
  type: memory
  trigger_status: in_development
  poll_interval: 30
  processing_status: processing
agent:
  default: stub
projects:
  repo:
    url: https://github.com/org/repo
session:
  base_path: %s
  keep_finished_sessions: true
""" % tmp_path.as_posix(),
        encoding="utf-8",
    )

    class LongRunningStubAdapter(CLIAdapter):
        def __init__(self) -> None:
            super().__init__("stub", "python")

        def is_available(self) -> bool:
            return True

        def spawn(self, request):
            runtime_dir = request.workspace / ".tiller"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            log_path = runtime_dir / "stub.log"
            process = __import__("subprocess").Popen(
                ["python", "-c", "import time; time.sleep(30)"],
                cwd=request.workspace,
                stdout=log_path.open("wb"),
                stderr=__import__("subprocess").STDOUT,
                start_new_session=True,
            )
            return SpawnResult(
                adapter_name=self.name,
                command=["python", "-c", "import time; time.sleep(30)"],
                process_id=process.pid,
                log_path=log_path,
                process=process,
            )

    config = load_config(config_path)
    tracker = InMemoryTrackerAdapter(
        [Task(id="1", title="Demo", description="Body", status="in_development")]
    )
    harness = AgentHarness({"stub": LongRunningStubAdapter()})
    service = TillerService(config=config, tracker_config=config.tracker, tracker=tracker, harness=harness)

    async def run_test() -> None:
        await service.run_once()
        task_ref = service._task_ref("1")
        active = service.active_sessions[task_ref]
        await asyncio.sleep(0.2)
        process = service._active_processes[task_ref]
        assert process.poll() is None
        active.cancel()
        try:
            await active
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.2)
        assert process.poll() is not None
        assert task_ref not in service._active_processes

    asyncio.run(run_test())


def test_service_stop_and_continue_control_requests(tmp_path: Path) -> None:
    config_path = tmp_path / "tiller.yaml"
    config_path.write_text(
        """
tracker:
  type: memory
  trigger_status: in_development
  poll_interval: 30
  processing_status: processing
agent:
  default: stub
projects:
  repo:
    url: https://github.com/org/repo
session:
  base_path: %s
  keep_finished_sessions: true
""" % tmp_path.as_posix(),
        encoding="utf-8",
    )

    class ControllableTracker(InMemoryTrackerAdapter):
        def __init__(self, tasks: list[Task]) -> None:
            super().__init__(tasks)
            self.requests: list[TaskControlRequest] = []

        async def poll_control_requests(self) -> list[TaskControlRequest]:
            pending = list(self.requests)
            self.requests = []
            return pending

    class LongRunningStubAdapter(CLIAdapter):
        def __init__(self) -> None:
            super().__init__("stub", "python")

        def is_available(self) -> bool:
            return True

        def spawn(self, request):
            runtime_dir = request.workspace / ".tiller"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            log_path = runtime_dir / "stub.log"
            process = __import__("subprocess").Popen(
                ["python", "-c", "import time; time.sleep(2)"],
                cwd=request.workspace,
                stdout=log_path.open("wb"),
                stderr=__import__("subprocess").STDOUT,
                start_new_session=True,
            )
            return SpawnResult(
                adapter_name=self.name,
                command=["python", "-c", "import time; time.sleep(2)"],
                process_id=process.pid,
                log_path=log_path,
                process=process,
            )

    config = load_config(config_path)
    tracker = ControllableTracker([Task(id="1", title="Demo", description="Body", status="in_development")])
    harness = AgentHarness({"stub": LongRunningStubAdapter()})
    service = TillerService(config=config, tracker_config=config.tracker, tracker=tracker, harness=harness)

    async def run_test() -> None:
        await service.run_once()
        task_ref = service._task_ref("1")
        await asyncio.sleep(0.2)
        tracker.requests.append(TaskControlRequest(task_id="1", action="stop", source="test"))
        await service.run_once()
        session_root = config.session.base_path / service.session_manager.make_internal_task_id(tracker._tasks["1"])
        payload = json.loads((session_root / "session.json").read_text(encoding="utf-8"))
        assert payload["state"] == "stopped"
        assert any("Agent stopped" in item for item in tracker.comments["1"])

        tracker.requests.append(TaskControlRequest(task_id="1", action="continue", source="test"))
        await service.run_once()
        active = service.active_sessions[task_ref]
        await active
        payload = json.loads((session_root / "session.json").read_text(encoding="utf-8"))
        assert payload["state"] == "completed"
        assert any("Continue requested" in item for item in tracker.comments["1"])

    asyncio.run(run_test())


def test_session_prepare_resumes_existing_session(tmp_path: Path) -> None:
    config_path = tmp_path / "tiller.yaml"
    config_path.write_text(
        """
tracker:
  type: memory
  trigger_status: in_development
  poll_interval: 30
agent:
  default: stub
projects:
  repo:
    url: https://github.com/org/repo
session:
  base_path: %s
  keep_finished_sessions: true
""" % tmp_path.as_posix(),
        encoding="utf-8",
    )

    config = load_config(config_path)
    tracker = InMemoryTrackerAdapter(
        [Task(id="1", title="Demo", description="Body", status="in_development")]
    )
    service = TillerService(config=config, tracker_config=config.tracker, tracker=tracker, harness=AgentHarness({"stub": StubAdapter()}))
    manager = service.session_manager
    task = tracker._tasks["1"]

    record1, paths1, _ = asyncio.run(manager.prepare(task, "stub"))
    paths1.state_md.write_text("# STATE\n\ncustom state\n", encoding="utf-8")

    tracker._tasks["1"] = Task(
        id="1",
        title="Demo",
        description="Updated body",
        status="in_development",
        comments=[TaskComment(id="c1", author="human", body="Resposta ao blocker")],
    )

    record2, paths2, _ = asyncio.run(manager.prepare(tracker._tasks["1"], "stub"))

    assert record1.internal_task_id == record2.internal_task_id
    assert record2.state == "prepared"
    assert paths1.root == paths2.root
    assert paths2.state_md.read_text(encoding="utf-8") == "# STATE\n\ncustom state\n"
    assert "Updated body" in paths2.task_md.read_text(encoding="utf-8")
    assert "Resposta ao blocker" in paths2.task_md.read_text(encoding="utf-8")


def test_render_agents_md_mcp_is_mcp_first() -> None:
    rendered = render_agents_md(tool_transport="mcp")

    assert "Prefer the MCP tracker tools instead of local `tiller tracker ...` commands." in rendered
    assert "If a tracker operation is available through MCP, do not use the local `tiller tracker ...` CLI for that operation." in rendered
    assert "If a project operation is available through MCP, do not use the local `tiller project ...` CLI for that operation." in rendered
    assert "Use the MCP `create_pr` tool to open a pull request when it is available." not in rendered
    assert "No PR tool is currently exposed in this session." in rendered
    assert "Use the MCP session tools to inspect the current session state and important paths." in rendered
    assert "If a session operation is available through MCP, do not use the local `tiller session ...` CLI for that operation." in rendered
    assert "In MCP mode, use local `tiller ...` CLI commands only when MCP fails for the operation you need." in rendered


def test_render_agents_md_cli_keeps_cli_instructions() -> None:
    rendered = render_agents_md(tool_transport="cli")

    assert "Use `tiller tracker get-task` to read the current task." in rendered
    assert "Use `tiller project use <name> --reason \"...\"` to provision a repo." in rendered
    assert "No PR command is currently exposed by Tiller in this environment." in rendered
    assert "Use `tiller session status` to inspect the current session state." in rendered
    assert "If a tracker operation is available through MCP" not in rendered


def test_render_agents_md_mentions_branch_fallback_when_no_pr_tool() -> None:
    from tiller.templates import render_agents_md

    rendered = render_agents_md(tool_transport="cli", pr_tool_enabled=False)

    assert "No PR command is currently exposed by Tiller in this environment." in rendered
    assert "push the branch and report the branch link(s) to the user" in rendered


def test_build_tracker_server_includes_cli_parity_tools(tmp_path: Path) -> None:
    session_root = tmp_path / "session"
    session_root.mkdir(parents=True, exist_ok=True)
    (tmp_path / "tiller.yaml").write_text(
        """
tracker:
  type: memory
  trigger_status: in_development
agent:
  default: stub
github:
  enabled: true
  token: yaml-token
projects: {}
session:
  base_path: %s
""" % tmp_path.as_posix(),
        encoding="utf-8",
    )
    (session_root / "session.json").write_text(
        json.dumps(
            {
                "internal_task_id": "TASK-123",
                "tracker_task_id": "1",
                "agent_name": "stub",
                "workspace": str(session_root),
                "config_path": str(tmp_path / "tiller.yaml"),
                "state": "running",
                "provisioned_repos": [],
            }
        ),
        encoding="utf-8",
    )
    (session_root / "projects.json").write_text("{}", encoding="utf-8")

    server = build_tracker_server(session_root)
    tool_names = {tool.name for tool in server._tool_manager.list_tools()}

    assert {
        "tracker_get_task",
        "tracker_update_status",
        "tracker_add_comment",
        "tracker_download_attachments",
        "tracker_status_options",
        "project_list",
        "project_status",
        "project_use",
        "create_pr",
        "session_status",
        "session_paths",
    } <= tool_names
    assert "github_create_pr" not in tool_names
    assert "github_auth_status" not in tool_names
    assert "github_repo_status" not in tool_names
    assert "github_pr_view" not in tool_names


def test_get_pull_request_provider_returns_github_binding_when_enabled() -> None:
    config = type(
        "Config",
        (),
        {
            "github": GitHubConfig(enabled=True, token="yaml-token"),
        },
    )()

    binding = get_pull_request_provider(config)

    assert binding is not None
    assert binding.name == "github"
    assert isinstance(binding.provider, GitHubPullRequestProvider)


def test_get_pull_request_provider_returns_none_when_unconfigured() -> None:
    config = type(
        "Config",
        (),
        {
            "github": GitHubConfig(enabled=False),
        },
    )()

    assert get_pull_request_provider(config) is None


def test_build_parser_hides_pr_command_without_provider(tmp_path: Path) -> None:
    from tiller.cli import build_parser

    config_path = tmp_path / "tiller.yaml"
    config_path.write_text(
        """
tracker:
  type: memory
  trigger_status: in_development
agent:
  default: stub
projects: {}
session:
  base_path: %s
""" % tmp_path.as_posix(),
        encoding="utf-8",
    )

    parser = build_parser(str(config_path))

    with pytest.raises(SystemExit):
        parser.parse_args(["pr", "create", "--repo", "backend", "--title", "feat: test"])


def test_build_tracker_server_hides_create_pr_without_provider(tmp_path: Path) -> None:
    session_root = tmp_path / "session-no-pr"
    session_root.mkdir(parents=True, exist_ok=True)
    (tmp_path / "tiller-no-pr.yaml").write_text(
        """
tracker:
  type: memory
  trigger_status: in_development
agent:
  default: stub
projects: {}
session:
  base_path: %s
""" % tmp_path.as_posix(),
        encoding="utf-8",
    )
    (session_root / "session.json").write_text(
        json.dumps(
            {
                "internal_task_id": "TASK-124",
                "tracker_task_id": "2",
                "agent_name": "stub",
                "workspace": str(session_root),
                "config_path": str(tmp_path / "tiller-no-pr.yaml"),
                "state": "running",
                "provisioned_repos": [],
            }
        ),
        encoding="utf-8",
    )
    (session_root / "projects.json").write_text("{}", encoding="utf-8")

    server = build_tracker_server(session_root)
    tool_names = {tool.name for tool in server._tool_manager.list_tools()}

    assert "create_pr" not in tool_names

def test_build_tracker_server_does_not_expose_memory_tools(tmp_path: Path) -> None:
    session_root = tmp_path / "session-mcp-memory"
    session_root.mkdir(parents=True, exist_ok=True)
    (session_root / "session.json").write_text(
        json.dumps(
            {
                "internal_task_id": "TASK-555",
                "tracker_task_id": "42",
                "agent_name": "stub",
                "workspace": str(session_root),
                "config_path": str(tmp_path / "tiller-mcp-memory.yaml"),
                "state": "running",
                "provisioned_repos": [],
            }
        ),
        encoding="utf-8",
    )
    (session_root / "projects.json").write_text("{}", encoding="utf-8")
    (tmp_path / "tiller-mcp-memory.yaml").write_text(
        """
tracker:
  type: memory
  trigger_status: in_development
agent:
  default: stub
memory:
  enabled: true
  provider: local
  base_path: %s
projects: {}
session:
  base_path: %s
""" % ((tmp_path / "memory-mcp-store").as_posix(), tmp_path.as_posix()),
        encoding="utf-8",
    )

    server = build_tracker_server(session_root)
    tool_names = sorted(server._tool_manager._tools.keys())

    assert "memory_retain" not in tool_names
    assert "memory_recall" not in tool_names
    assert "memory_reflect" not in tool_names


def test_sync_clickup_comments_serialize_as_task_comments() -> None:
    class FakeResponse:
        def __init__(self, payload: dict):
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    class FakeClient:
        def get(self, path: str, params: dict | None = None) -> FakeResponse:
            if path.startswith("/task/") and path.endswith("/comment"):
                return FakeResponse(
                    {
                        "comments": [
                            {
                                "id": "c1",
                                "comment": [{"text": "Resposta ao blocker"}],
                                "user": {"username": "human"},
                                "date": 1710000000,
                            }
                        ]
                    }
                )
            return FakeResponse(
                {
                    "id": "task-1",
                    "name": "Demo",
                    "markdown_description": "Body",
                    "status": {"status": "in_development"},
                    "url": "https://clickup/task-1",
                }
            )

        def close(self) -> None:
            return None

    adapter = SyncClickUpTrackerAdapter(token="token", team_id="team-1")
    adapter._client = FakeClient()  # type: ignore[assignment]

    task = adapter.get_task("task-1")
    payload = serialize_task(task)

    assert task.comments[0].id == "c1"
    assert task.comments[0].author == "human"
    assert task.comments[0].body == "Resposta ao blocker"
    assert payload["comments"][0]["id"] == "c1"
    assert payload["comments"][0]["body"] == "Resposta ao blocker"


def test_sync_clickup_status_options_use_task_scoped_statuses() -> None:
    class FakeResponse:
        def __init__(self, payload: dict):
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    class FakeClient:
        def get(self, path: str, params: dict | None = None) -> FakeResponse:
            if path.startswith("/task/") and path.endswith("/comment"):
                return FakeResponse({"comments": []})
            if path == "/task/task-1":
                return FakeResponse(
                    {
                        "id": "task-1",
                        "name": "Demo",
                        "markdown_description": "Body",
                        "status": {"status": "to do"},
                        "statuses": [
                            {"status": "to do"},
                            {"status": "in review"},
                            {"status": "done"},
                        ],
                    }
                )
            raise AssertionError(f"unexpected path: {path}")

        def close(self) -> None:
            return None

    adapter = SyncClickUpTrackerAdapter(token="token", team_id="team-1")
    adapter._client = FakeClient()  # type: ignore[assignment]

    assert adapter.list_status_options("task-1") == ["done", "in review", "to do"]




def test_telegram_tracker_creates_task_comments_and_rotates_on_new(tmp_path: Path) -> None:
    class FakeResponse:
        def __init__(self, payload: dict[str, object], content: bytes = b"") -> None:
            self._payload = payload
            self.content = content

        def json(self):
            return self._payload

        def raise_for_status(self) -> None:
            return None

    class FakeAsyncClient:
        def __init__(self, updates: list[dict[str, object]]) -> None:
            self.updates = updates
            self.sent_messages: list[dict[str, object]] = []
            self.command_registrations: list[dict[str, object]] = []
            self.base_url = "https://api.telegram.org/bottoken"

        async def get(self, path: str, params=None):
            if path == "/getUpdates":
                result = self.updates
                self.updates = []
                return FakeResponse({"ok": True, "result": result})
            if path == "/getMe":
                return FakeResponse({"ok": True, "result": {"id": 1}})
            raise AssertionError(f"unexpected path: {path}")

        async def post(self, path: str, json=None):
            if path == "/setMyCommands":
                self.command_registrations.append(dict(json or {}))
                return FakeResponse({"ok": True, "result": True})
            if path != "/sendMessage":
                raise AssertionError(f"unexpected path: {path}")
            self.sent_messages.append(dict(json or {}))
            return FakeResponse(
                {
                    "ok": True,
                    "result": {
                        "message_id": 900 + len(self.sent_messages),
                        "date": 1715640000 + len(self.sent_messages),
                        "text": (json or {}).get("text", ""),
                        "from": {"username": "tiller"},
                    },
                }
            )

        async def aclose(self) -> None:
            return None

    state_path = tmp_path / "telegram-state.json"
    adapter = TelegramTrackerAdapter(bot_token="token", state_path=state_path, allowed_chat_ids=["100"], allowed_user_ids=["7"])
    adapter._client = FakeAsyncClient(
        [
            {
                "update_id": 0,
                "message": {
                    "message_id": 8,
                    "date": 1715639998,
                    "text": "/start",
                    "chat": {"id": 100},
                    "from": {"id": 7, "username": "joao"},
                },
            },
            {
                "update_id": 1,
                "message": {
                    "message_id": 9,
                    "date": 1715639999,
                    "text": "ignorar",
                    "chat": {"id": 100},
                    "from": {"id": 99, "username": "intruso"},
                },
            },
            {
                "update_id": 2,
                "message": {
                    "message_id": 10,
                    "date": 1715640000,
                    "text": "primeira task",
                    "chat": {"id": 100},
                    "from": {"id": 7, "username": "joao"},
                },
            },
            {
                "update_id": 3,
                "message": {
                    "message_id": 11,
                    "date": 1715640001,
                    "text": "detalhe adicional",
                    "chat": {"id": 100},
                    "from": {"id": 7, "username": "joao"},
                },
            },
            {
                "update_id": 4,
                "message": {
                    "message_id": 12,
                    "date": 1715640002,
                    "text": "/new",
                    "chat": {"id": 100},
                    "from": {"id": 7, "username": "joao"},
                },
            },
            {
                "update_id": 5,
                "message": {
                    "message_id": 13,
                    "date": 1715640003,
                    "text": "segunda task",
                    "chat": {"id": 100},
                    "from": {"id": 7, "username": "joao"},
                },
            },
        ]
    )  # type: ignore[assignment]

    asyncio.run(adapter.validate())
    tasks = asyncio.run(adapter.list_tasks("new"))
    assert adapter._client.command_registrations == [
        {
            "commands": [
                {"command": "start", "description": "Show bot instructions"},
                {"command": "new", "description": "Create a new task"},
                {"command": "stop", "description": "Stop the current task agent"},
                {"command": "continue", "description": "Continue the current task agent"},
            ]
        }
    ]
    assert [task.id for task in tasks] == ["telegram-1", "telegram-2"]
    assert tasks[0].title == "primeira task"
    assert [comment.body for comment in tasks[0].comments] == ["primeira task", "detalhe adicional"]
    assert tasks[1].title == "segunda task"
    assert [comment.body for comment in tasks[1].comments] == ["segunda task"]
    assert adapter._client.sent_messages[0] == {
        "chat_id": "100",
        "text": "Tiller is your programmer. Use /new to start a new task.",
    }
    assert adapter._client.sent_messages[1] == {
        "chat_id": "100",
        "text": "What do you need?",
    }

    asyncio.run(adapter.add_comment("telegram-1", "andamento"))
    updated = asyncio.run(adapter.get_task("telegram-1"))
    assert updated.comments[-1].body == "andamento"
    assert adapter._client.sent_messages[-1] == {"chat_id": "100", "text": "andamento"}


def test_telegram_tracker_emits_stop_and_continue_control_requests(tmp_path: Path) -> None:
    class FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def json(self):
            return self._payload

        def raise_for_status(self) -> None:
            return None

    class FakeAsyncClient:
        def __init__(self, updates: list[dict[str, object]]) -> None:
            self.updates = updates
            self.base_url = "https://api.telegram.org/bottoken"

        async def get(self, path: str, params=None):
            if path == "/getUpdates":
                result = self.updates
                self.updates = []
                return FakeResponse({"ok": True, "result": result})
            if path == "/getMe":
                return FakeResponse({"ok": True, "result": {"id": 1}})
            raise AssertionError(f"unexpected path: {path}")

        async def post(self, path: str, json=None):
            return FakeResponse({"ok": True, "result": True})

        async def aclose(self) -> None:
            return None

    state_path = tmp_path / "telegram-control-state.json"
    adapter = TelegramTrackerAdapter(bot_token="token", state_path=state_path, allowed_chat_ids=["100"], allowed_user_ids=["7"])
    adapter._client = FakeAsyncClient(
        [
            {
                "update_id": 1,
                "message": {
                    "message_id": 10,
                    "date": 1715640000,
                    "text": "primeira task",
                    "chat": {"id": 100},
                    "from": {"id": 7, "username": "joao"},
                },
            },
            {
                "update_id": 2,
                "message": {
                    "message_id": 11,
                    "date": 1715640001,
                    "text": "/stop@demo_bot",
                    "chat": {"id": 100},
                    "from": {"id": 7, "username": "joao"},
                },
            },
            {
                "update_id": 3,
                "message": {
                    "message_id": 12,
                    "date": 1715640002,
                    "text": "/continue",
                    "chat": {"id": 100},
                    "from": {"id": 7, "username": "joao"},
                },
            },
        ]
    )  # type: ignore[assignment]

    tasks = asyncio.run(adapter.list_tasks("new"))
    requests = asyncio.run(adapter.poll_control_requests())

    assert [task.id for task in tasks] == ["telegram-1"]
    assert [(item.task_id, item.action) for item in requests] == [("telegram-1", "stop"), ("telegram-1", "continue")]

    for item in requests:
        asyncio.run(adapter.acknowledge_control_request(item))

    assert asyncio.run(adapter.poll_control_requests()) == []


def test_clickup_tracker_polls_stop_and_continue_comments_once(monkeypatch) -> None:
    class FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def get(self, path: str, params=None):
            if path == "/team/team-1/task":
                return FakeResponse(
                    {
                        "tasks": [
                            {
                                "id": "task-1",
                                "name": "Demo",
                                "markdown_description": "Body",
                                "status": {"status": "in_development"},
                                "attachments": [],
                            }
                        ]
                    }
                )
            if path == "/task/task-1/comment":
                return FakeResponse(
                    {
                        "comments": [
                            {"id": "c-stop", "comment_text": "/stop", "user": {"username": "human"}, "date": 1710000000},
                            {"id": "c-continue", "comment_text": "/continue", "user": {"username": "human"}, "date": 1710000001},
                        ]
                    }
                )
            if path == "/task/task-1":
                return FakeResponse(
                    {
                        "id": "task-1",
                        "name": "Demo",
                        "markdown_description": "Body",
                        "status": {"status": "in_development"},
                        "attachments": [],
                    }
                )
            raise AssertionError(f"unexpected path: {path}")

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr("tiller.trackers.clickup.httpx.AsyncClient", FakeAsyncClient)
    adapter = ClickUpTrackerAdapter(token="token", team_id="team-1")

    requests = asyncio.run(adapter.poll_control_requests())
    assert [(item.task_id, item.action, item.message_id) for item in requests] == [
        ("task-1", "stop", "c-stop"),
        ("task-1", "continue", "c-continue"),
    ]

    for item in requests:
        asyncio.run(adapter.acknowledge_control_request(item))

    assert asyncio.run(adapter.poll_control_requests()) == []


def test_telegram_tracker_merges_fragmented_long_messages_into_single_comment(tmp_path: Path) -> None:
    class FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def json(self):
            return self._payload

        def raise_for_status(self) -> None:
            return None

    class FakeAsyncClient:
        def __init__(self, updates: list[dict[str, object]]) -> None:
            self.updates = updates
            self.sent_messages: list[dict[str, object]] = []
            self.base_url = "https://api.telegram.org/bottoken"

        async def get(self, path: str, params=None):
            if path == "/getUpdates":
                result = self.updates
                self.updates = []
                return FakeResponse({"ok": True, "result": result})
            if path == "/getMe":
                return FakeResponse({"ok": True, "result": {"id": 1}})
            raise AssertionError(f"unexpected path: {path}")

        async def post(self, path: str, json=None):
            raise AssertionError(f"unexpected path: {path}")

        async def aclose(self) -> None:
            return None

    state_path = tmp_path / "telegram-state.json"
    adapter = TelegramTrackerAdapter(bot_token="token", state_path=state_path, allowed_chat_ids=["100"], allowed_user_ids=["7"])
    adapter._client = FakeAsyncClient(
        [
            {
                "update_id": 1,
                "message": {
                    "message_id": 70,
                    "date": 1715781636,
                    "text": "Part 1 of a long message",
                    "chat": {"id": 100},
                    "from": {"id": 7, "first_name": "Jhon"},
                },
            },
            {
                "update_id": 2,
                "message": {
                    "message_id": 71,
                    "date": 1715781636,
                    "text": "Part 2 of the same long message",
                    "chat": {"id": 100},
                    "from": {"id": 7, "first_name": "Jhon"},
                },
            },
            {
                "update_id": 3,
                "message": {
                    "message_id": 72,
                    "date": 1715781636,
                    "text": "Part 3 of the same long message",
                    "chat": {"id": 100},
                    "from": {"id": 7, "first_name": "Jhon"},
                },
            },
        ]
    )  # type: ignore[assignment]

    tasks = asyncio.run(adapter.list_tasks("new"))
    assert [task.id for task in tasks] == ["telegram-1"]
    assert len(tasks[0].comments) == 1
    assert tasks[0].comments[0].body == "Part 1 of a long message\nPart 2 of the same long message\nPart 3 of the same long message"


def test_sync_telegram_tracker_add_comment_persists_comment(tmp_path: Path, monkeypatch) -> None:
    state_path = tmp_path / "telegram-state.json"
    TelegramStateStore(state_path).save(
        TelegramTrackerState.from_payload(
            {
                "next_task_number": 2,
                "last_update_id": 0,
                "awaiting_new_task_by_chat": {},
                "active_task_by_chat": {"100": "telegram-1"},
                "tasks": {
                    "telegram-1": {
                        "id": "telegram-1",
                        "chat_id": "100",
                        "title": "primeira task",
                        "description": "primeira task",
                        "status": "new",
                        "comments": [],
                        "attachments": [],
                        "metadata": {"chat_id": "100", "created_at": "2026-05-15T00:00:00+00:00"},
                        "created_at": "2026-05-15T00:00:00+00:00",
                    }
                },
            }
        )
    )

    class StubSyncResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "ok": True,
                "result": {
                    "message_id": 99,
                    "date": 1715640009,
                    "text": "andamento",
                    "from": {"username": "tiller"},
                },
            }

    class StubSyncClient:
        def __init__(self, *args, **kwargs) -> None:
            self.sent_messages: list[dict[str, str]] = []
            self.base_url = "https://api.telegram.org/bottoken"

        def post(self, path: str, json: dict[str, str]) -> StubSyncResponse:
            assert path == "/sendMessage"
            self.sent_messages.append(json)
            return StubSyncResponse()

        def close(self) -> None:
            return None

    monkeypatch.setattr("tiller.trackers.telegram.httpx.Client", StubSyncClient)

    adapter = build_sync_tracker("telegram", bot_token="token", state_path=str(state_path))
    adapter.add_comment("telegram-1", "andamento")

    task = adapter.get_task("telegram-1")
    assert task.comments[-1].body == "andamento"



def test_clickup_factory_accepts_optional_filters() -> None:
    adapter = build_tracker_adapter(
        "clickup",
        token="token",
        team_id="team-123",
        tag="tiller",
        assignee="user-123",
    )
    assert adapter.__class__.__name__ == "ClickUpTrackerAdapter"
    assert adapter.tag == "tiller"
    assert adapter.assignee == "user-123"


def test_native_mcp_project_files_are_written_per_cli(tmp_path: Path) -> None:
    mcp_payload = {
        "servers": {
            "tracker": {
                "name": "tracker",
                "transport": "stdio",
                "command": "tiller-mcp",
                "args": ["tracker", "--session", "/tmp/session"],
            },
            "github": {
                "name": "github",
                "type": "http",
                "url": "https://api.github.com/mcp",
                "headers": {"Authorization": "Bearer secret-token", "X-Test": "1"},
            },
        }
    }

    write_native_mcp_project_files(tmp_path, mcp_payload)

    claude_payload = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
    assert claude_payload["mcpServers"]["tracker"]["command"] == "tiller-mcp"
    assert claude_payload["mcpServers"]["github"]["url"] == "https://api.github.com/mcp"
    assert claude_payload["mcpServers"]["github"]["headers"]["Authorization"] == "Bearer secret-token"

    opencode_payload = json.loads((tmp_path / "opencode.json").read_text(encoding="utf-8"))
    assert opencode_payload["mcp"]["tracker"]["type"] == "local"
    assert opencode_payload["mcp"]["github"]["type"] == "remote"
    assert opencode_payload["mcp"]["github"]["headers"]["Authorization"] == "Bearer secret-token"

    gemini_payload = json.loads((tmp_path / ".gemini" / "settings.json").read_text(encoding="utf-8"))
    assert gemini_payload["mcpServers"]["tracker"]["command"] == "tiller-mcp"
    assert gemini_payload["mcpServers"]["github"]["httpUrl"] == "https://api.github.com/mcp"

    codex_toml = (tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert "[mcp_servers.tracker]" in codex_toml
    assert 'command = "tiller-mcp"' in codex_toml or "command = 'tiller-mcp'" in codex_toml
    assert "[mcp_servers.github]" in codex_toml
    assert 'url = "https://api.github.com/mcp"' in codex_toml or "url = 'https://api.github.com/mcp'" in codex_toml
    assert 'bearer_token_env_var = "GITHUB_API_TOKEN"' in codex_toml

    cursor_payload = json.loads((tmp_path / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    assert cursor_payload == {
        "mcpServers": {
            "tracker": {
                "command": "tiller-mcp",
                "args": ["tracker", "--session", "/tmp/session"],
                "env": {},
            }
        }
    }

    continue_yaml = (tmp_path / ".continue" / "mcpServers" / "tiller.yaml").read_text(encoding="utf-8")
    assert "schema: v1" in continue_yaml
    assert "name: tracker" in continue_yaml
    assert "command: tiller-mcp" in continue_yaml
    assert "url: https://api.github.com/mcp" in continue_yaml

    kiro_payload = json.loads((tmp_path / ".kiro" / "settings" / "mcp.json").read_text(encoding="utf-8"))
    assert kiro_payload["mcpServers"]["tracker"]["command"] == "tiller-mcp"
    assert kiro_payload["mcpServers"]["github"]["url"] == "https://api.github.com/mcp"

    qwen_payload = json.loads((tmp_path / ".qwen" / "settings.json").read_text(encoding="utf-8"))
    assert qwen_payload["mcpServers"]["tracker"]["command"] == "tiller-mcp"
    assert qwen_payload["mcpServers"]["github"]["httpUrl"] == "https://api.github.com/mcp"

    junie_payload = json.loads((tmp_path / ".junie" / "mcp" / "mcp.json").read_text(encoding="utf-8"))
    assert junie_payload["mcpServers"]["tracker"]["command"] == "tiller-mcp"
    assert junie_payload["mcpServers"]["github"]["url"] == "https://api.github.com/mcp"

    gptme_toml = (tmp_path / "gptme.toml").read_text(encoding="utf-8")
    assert "[mcp]" in gptme_toml
    assert "[[mcp.servers]]" in gptme_toml
    assert "name = 'tracker'" in gptme_toml or 'name = "tracker"' in gptme_toml
    assert "url = 'https://api.github.com/mcp'" in gptme_toml or 'url = "https://api.github.com/mcp"' in gptme_toml

    pi_payload = json.loads((tmp_path / ".pi" / "mcp.json").read_text(encoding="utf-8"))
    assert pi_payload["mcpServers"]["tracker"]["command"] == "tiller-mcp"
    assert pi_payload["mcpServers"]["github"]["url"] == "https://api.github.com/mcp"

    auggie_payload = json.loads((tmp_path / ".augment" / "settings.json").read_text(encoding="utf-8"))
    assert auggie_payload["mcpServers"]["tracker"]["command"] == "tiller-mcp"
    assert auggie_payload["mcpServers"]["github"]["url"] == "https://api.github.com/mcp"

    kimi_payload = json.loads((tmp_path / "mcp.json").read_text(encoding="utf-8"))
    assert kimi_payload["mcpServers"]["tracker"]["command"] == "tiller-mcp"
    assert kimi_payload["mcpServers"]["github"]["url"] == "https://api.github.com/mcp"

    copilot_payload = json.loads((tmp_path / ".copilot" / "mcp-config.json").read_text(encoding="utf-8"))
    assert copilot_payload["mcpServers"]["tracker"]["type"] == "local"
    assert copilot_payload["mcpServers"]["github"]["type"] == "http"
    assert copilot_payload["mcpServers"]["github"]["url"] == "https://api.github.com/mcp"

    droid_payload = json.loads((tmp_path / ".factory" / "mcp.json").read_text(encoding="utf-8"))
    assert droid_payload["mcpServers"]["tracker"]["type"] == "stdio"
    assert droid_payload["mcpServers"]["tracker"]["command"] == "tiller-mcp"
    assert droid_payload["mcpServers"]["tracker"]["disabled"] is False
    assert droid_payload["mcpServers"]["github"]["type"] == "http"
    assert droid_payload["mcpServers"]["github"]["url"] == "https://api.github.com/mcp"
    assert droid_payload["mcpServers"]["github"]["disabledTools"] == []


def test_kimi_adapter_uses_local_mcp_config_file(tmp_path: Path, monkeypatch) -> None:
    adapter = KimiAdapter()
    captured: dict[str, object] = {}

    def fake_spawn_process(self, *, request, command, stdin_payload=None, extra_env=None):
        captured["command"] = command
        return SpawnResult(
            adapter_name=self.name,
            command=command,
            process_id=123,
            log_path=tmp_path / ".tiller" / "kimi.log",
            process=None,
        )

    monkeypatch.setattr(KimiAdapter, "_spawn_process", fake_spawn_process)
    request = AgentRunRequest(
        agent_name="kimi",
        workspace=tmp_path,
        goal="do something",
        mcp_config={
            "servers": {
                "tracker": {
                    "transport": "stdio",
                    "command": "tiller-mcp",
                    "args": ["tracker"],
                }
            }
        },
    )
    result = adapter.spawn(request)
    assert "--mcp-config-file" in result.command
    config_index = result.command.index("--mcp-config-file") + 1
    assert result.command[config_index].endswith("mcp.json")
    assert captured["command"] == result.command
    assert (tmp_path / "mcp.json").exists()


def test_copilot_adapter_uses_additional_mcp_config_flag(tmp_path: Path, monkeypatch) -> None:
    adapter = CopilotAdapter()
    captured: dict[str, object] = {}

    def fake_spawn_process(self, *, request, command, stdin_payload=None, extra_env=None):
        captured["command"] = command
        return SpawnResult(
            adapter_name=self.name,
            command=command,
            process_id=123,
            log_path=tmp_path / ".tiller" / "copilot.log",
            process=None,
        )

    monkeypatch.setattr(CopilotAdapter, "_spawn_process", fake_spawn_process)
    request = AgentRunRequest(
        agent_name="copilot",
        workspace=tmp_path,
        goal="do something",
        mcp_config={
            "servers": {
                "tracker": {
                    "transport": "stdio",
                    "command": "tiller-mcp",
                    "args": ["tracker"],
                }
            }
        },
    )
    result = adapter.spawn(request)
    assert "--additional-mcp-config" in result.command
    config_index = result.command.index("--additional-mcp-config") + 1
    assert result.command[config_index].endswith(".copilot/mcp-config.json")
    assert captured["command"] == result.command
    assert (tmp_path / ".copilot" / "mcp-config.json").exists()


def test_write_mcp_config_prefers_yaml_github_token(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_API_TOKEN", "env-token")
    config_path = tmp_path / "tiller.yaml"
    config_path.write_text(
        """
tracker:
  type: memory
  trigger_status: in_development
agent:
  default: stub
github:
  enabled: true
  token: yaml-token
projects: {}
session:
  base_path: %s
""" % tmp_path.as_posix(),
        encoding="utf-8",
    )
    config = load_config(config_path)
    output = write_mcp_config(tmp_path / ".mcp" / "config.json", config)

    assert output["servers"]["github"]["type"] == "http"
    assert output["servers"]["github"]["url"] == "https://api.github.com"
    assert output["servers"]["github"]["headers"]["Authorization"] == "Bearer yaml-token"


def test_write_mcp_config_falls_back_to_env_github_token(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_API_TOKEN", "env-token")
    config_path = tmp_path / "tiller.yaml"
    config_path.write_text(
        """
tracker:
  type: memory
  trigger_status: in_development
agent:
  default: stub
github:
  enabled: true
projects: {}
session:
  base_path: %s
""" % tmp_path.as_posix(),
        encoding="utf-8",
    )
    config = load_config(config_path)
    output = write_mcp_config(tmp_path / ".mcp" / "config.json", config)

    assert output["servers"]["github"]["headers"]["Authorization"] == "Bearer env-token"


def test_github_client_normalizes_legacy_mcp_url(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class DummyClient:
        def __init__(self, *, base_url: str, headers: dict[str, str], timeout: float) -> None:
            captured["base_url"] = base_url
            captured["headers"] = headers
            captured["timeout"] = timeout

        def close(self) -> None:
            return None

    monkeypatch.setattr("tiller.github.httpx.Client", DummyClient)

    client = GitHubClient(
        GitHubConfig(
            enabled=True,
            url="https://api.githubcopilot.com/mcp/",
            token="token",
            token_env="GITHUB_API_TOKEN",
        )
    )
    try:
        assert captured["base_url"] == "https://api.github.com"
    finally:
        client.close()


def test_codex_adapter_does_not_force_workspace_write_sandbox(tmp_path: Path) -> None:
    from tiller.agents.codex import CodexAdapter
    from tiller.models import AgentRunRequest

    adapter = CodexAdapter()
    captured: dict[str, object] = {}

    def fake_spawn_process(*, request, command, stdin_payload=None, extra_env=None):
        captured["command"] = command
        return SpawnResult(
            adapter_name="codex",
            command=command,
            process_id=123,
            log_path=tmp_path / "codex.log",
            process=None,
        )

    adapter._spawn_process = fake_spawn_process  # type: ignore[method-assign]
    adapter.spawn(
        AgentRunRequest(
            agent_name="codex",
            workspace=tmp_path,
            goal="Do the work",
            mcp_config={},
            env={},
            model="gpt-5",
        )
    )

    command = captured["command"]
    assert command[:5] == ["codex", "exec", "--sandbox", "danger-full-access", "-m"]
    assert "--sandbox" in command
    assert "workspace-write" not in command
    assert "--skip-git-repo-check" in command
    assert "--yolo" not in command
    assert "--json" in command
    assert "-o" in command


def test_claude_adapter_uses_project_mcp_transport(tmp_path: Path) -> None:
    from tiller.agents.claude import ClaudeCodeAdapter
    from tiller.models import AgentRunRequest

    adapter = ClaudeCodeAdapter()
    captured: dict[str, object] = {}

    def fake_spawn_process(*, request, command, stdin_payload=None, extra_env=None):
        captured["command"] = command
        captured["extra_env"] = extra_env
        return SpawnResult(
            adapter_name="claude-code",
            command=command,
            process_id=123,
            log_path=tmp_path / "claude.log",
            process=None,
        )

    adapter._spawn_process = fake_spawn_process  # type: ignore[method-assign]
    adapter.spawn(
        AgentRunRequest(
            agent_name="claude-code",
            workspace=tmp_path,
            goal="Do the work",
            mcp_config={},
            env={},
            model="sonnet",
        )
    )

    assert captured["command"] == [
        "claude",
        "--print",
        "--permission-mode",
        "bypassPermissions",
        "--output-format",
        "text",
        "--model",
        "sonnet",
        "-p",
        "Do the work",
    ]
    assert captured["extra_env"] is None


def test_other_cli_adapters_use_expected_autonomous_flags(tmp_path: Path) -> None:
    from tiller.agents.aider import AiderAdapter
    from tiller.agents.claude import ClaudeCodeAdapter
    from tiller.agents.gemini import GeminiCLIAdapter
    from tiller.agents.opencode import OpenCodeAdapter
    from tiller.models import AgentRunRequest

    cases = [
        (
            ClaudeCodeAdapter(),
            "claude-code",
            ["claude", "--print", "--permission-mode", "bypassPermissions", "--output-format", "text", "--model", "sonnet", "-p", "Do the work"],
        ),
        (
            OpenCodeAdapter(),
            "opencode",
            ["opencode", "run", "-m", "gpt-5", "--format", "json", "Do the work"],
        ),
        (
            AiderAdapter(),
            "aider",
            ["aider", "--model", "gpt-5", "--message", "Do the work", "--yes", "--auto-commits", "--map-tokens", "2048", "--no-auto-lint"],
        ),
        (
            GeminiCLIAdapter(),
            "gemini-cli",
            ["gemini", "-p", "Do the work", "-m", "gemini-2.5-pro", "--output-format", "json", "--yolo"],
        ),
    ]

    for adapter, agent_name, expected_command in cases:
        captured: dict[str, object] = {}

        def fake_spawn_process(*, request, command, stdin_payload=None, extra_env=None):
            captured["command"] = command
            return SpawnResult(
                adapter_name=agent_name,
                command=command,
                process_id=123,
                log_path=tmp_path / f"{agent_name}.log",
                process=None,
            )

        adapter._spawn_process = fake_spawn_process  # type: ignore[method-assign]
        model = "sonnet" if agent_name == "claude-code" else "gemini-2.5-pro" if agent_name == "gemini-cli" else "gpt-5"
        adapter.spawn(
            AgentRunRequest(
                agent_name=agent_name,
                workspace=tmp_path,
                goal="Do the work",
                mcp_config={},
                env={},
                model=model,
            )
        )

        assert captured["command"] == expected_command


def test_github_client_reports_invalid_token_clearly(monkeypatch) -> None:
    class DummyResponse:
        status_code = 401

        def raise_for_status(self) -> None:
            request = httpx.Request("GET", "https://api.github.com/user")
            response = httpx.Response(401, request=request)
            raise httpx.HTTPStatusError("401", request=request, response=response)

    class DummyClient:
        def __init__(self, *, base_url: str, headers: dict[str, str], timeout: float) -> None:
            pass

        def get(self, path: str) -> DummyResponse:
            return DummyResponse()

        def close(self) -> None:
            return None

    monkeypatch.setattr("tiller.github.httpx.Client", DummyClient)

    client = GitHubClient(GitHubConfig(enabled=True, token="bad-token"))
    try:
        try:
            client.validate()
        except RuntimeError as exc:
            assert "invalid or expired" in str(exc)
        else:
            raise AssertionError("Expected invalid GitHub token error")
    finally:
        client.close()


def test_discover_local_projects_from_repo_store(tmp_path: Path, monkeypatch) -> None:
    mirrors = tmp_path / "repo-mirrors"
    backend = mirrors / "backend"
    backend_git = backend / ".git"
    backend_git.mkdir(parents=True, exist_ok=True)
    notes = mirrors / "notes"
    notes.mkdir(parents=True, exist_ok=True)

    class Completed:
        def __init__(self, returncode: int, stdout: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ""

    def fake_run(command, cwd, capture_output, text):
        assert cwd == backend
        assert capture_output is True
        assert text is True
        if command == ["git", "config", "--get", "remote.origin.url"]:
            return Completed(0, "git@internal.example.com:core/backend.git\n")
        if command == ["git", "symbolic-ref", "--short", "HEAD"]:
            return Completed(0, "develop\n")
        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr("tiller.repo_seed.subprocess.run", fake_run)

    projects = discover_local_projects(mirrors)

    assert list(projects.keys()) == ["backend"]
    backend_project = projects["backend"]
    assert backend_project.url == "git@internal.example.com:core/backend.git"
    assert backend_project.default_branch == "develop"
    assert backend_project.source == "local_directory"
    assert backend_project.source_path == str(backend)


def test_load_config_merges_local_repo_store_projects(tmp_path: Path, monkeypatch) -> None:
    repo_store = tmp_path / "repo-mirrors"
    backend = repo_store / "backend"
    backend_git = backend / ".git"
    backend_git.mkdir(parents=True, exist_ok=True)

    class Completed:
        def __init__(self, returncode: int, stdout: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ""

    def fake_run(command, cwd, capture_output, text):
        if cwd == backend and command == ["git", "config", "--get", "remote.origin.url"]:
            return Completed(0, "git@internal.example.com:core/backend.git\n")
        if cwd == backend and command == ["git", "symbolic-ref", "--short", "HEAD"]:
            return Completed(0, "main\n")
        raise AssertionError(f"Unexpected command: {command} cwd={cwd}")

    monkeypatch.setattr("tiller.repo_seed.subprocess.run", fake_run)

    config_path = tmp_path / "tiller.yaml"
    config_path.write_text(
        f"""
tracker:
  type: clickup
  token: clickup-token
  team_id: team-1
  trigger_status: DEVELOP
agent:
  default: codex
projects:
  frontend:
    url: https://github.com/org/frontend
    description: Frontend app
session:
  base_path: {tmp_path / 'sessions'}
  repo_store_path: {repo_store}
""",
        encoding="utf-8",
    )

    loaded = load_config(config_path)

    assert loaded.session.repo_store_path == repo_store.resolve()
    assert set(loaded.projects.keys()) == {"frontend", "backend"}
    assert loaded.projects["backend"].source == "local_directory"
    assert loaded.projects["backend"].source_path == str(backend)
    assert loaded.projects["frontend"].source == "configured"


def test_repo_seed_manager_uses_local_directory_source_without_git_fetch(tmp_path: Path) -> None:
    source_repo = tmp_path / "repo-mirrors" / "backend"
    (source_repo / ".git").mkdir(parents=True, exist_ok=True)
    (source_repo / "README.md").write_text("backend\n", encoding="utf-8")

    manager = RepoSeedManager(tmp_path, mirrors_dir=tmp_path / "repo-mirrors")
    project = ProjectSpec(
        name="backend",
        url="file:///ignored",
        default_branch="main",
        source="local_directory",
        source_path=str(source_repo),
    )
    paths = SessionPaths(
        root=tmp_path / "session",
        agents_md=tmp_path / "session" / "AGENTS.md",
        task_md=tmp_path / "session" / "TASK.md",
        task_json=tmp_path / "session" / "task.json",
        state_md=tmp_path / "session" / "STATE.md",
        projects_json=tmp_path / "session" / "projects.json",
        repos_dir=tmp_path / "session" / "repos",
        attachments_dir=tmp_path / "session" / "attachments",
        mcp_dir=tmp_path / "session" / ".mcp",
        mcp_config=tmp_path / "session" / ".mcp" / "config.json",
        state_json=tmp_path / "session" / "session.json",
    )
    paths.repos_dir.mkdir(parents=True, exist_ok=True)

    repo_path = manager.provision(paths=paths, project=project)

    assert repo_path == paths.repos_dir / "backend"
    assert (repo_path / "README.md").read_text(encoding="utf-8") == "backend\n"
    metadata = json.loads((repo_path / ".tiller-repo.json").read_text(encoding="utf-8"))
    assert metadata["seed"] == str(source_repo)



def test_repo_seed_manager_writes_repo_metadata(tmp_path: Path) -> None:
    manager = RepoSeedManager(tmp_path)
    project = ProjectSpec(name="demo", url="https://github.com/org/demo", default_branch="main")
    paths = SessionPaths(
        root=tmp_path / "session",
        agents_md=tmp_path / "session" / "AGENTS.md",
        task_md=tmp_path / "session" / "TASK.md",
        task_json=tmp_path / "session" / "task.json",
        state_md=tmp_path / "session" / "STATE.md",
        projects_json=tmp_path / "session" / "projects.json",
        repos_dir=tmp_path / "session" / "repos",
        attachments_dir=tmp_path / "session" / "attachments",
        mcp_dir=tmp_path / "session" / ".mcp",
        mcp_config=tmp_path / "session" / ".mcp" / "config.json",
        state_json=tmp_path / "session" / "session.json",
    )
    paths.repos_dir.mkdir(parents=True, exist_ok=True)

    commands: list[tuple[list[str], Path]] = []

    def fake_git(command: list[str], *, cwd: Path) -> None:
        commands.append((command, cwd))
        if command[:2] == ["git", "clone"]:
            (tmp_path / "repo-mirrors" / "demo").mkdir(parents=True, exist_ok=True)

    manager._git = fake_git  # type: ignore[method-assign]

    repo_path = manager.provision(paths=paths, project=project)

    assert repo_path == paths.repos_dir / "demo"
    assert (repo_path / ".tiller-repo.json").exists()
    metadata = json.loads((repo_path / ".tiller-repo.json").read_text(encoding="utf-8"))
    assert metadata["name"] == "demo"
    assert metadata["branch"] is None
    assert metadata["seed"] == str(tmp_path / "repo-mirrors" / "demo")
    assert any(command[:2] == ["git", "clone"] for command, _ in commands)
    assert commands[0][0] == ["git", "clone", "https://github.com/org/demo", str(tmp_path / "repo-mirrors" / "demo")]
    assert any(command[:3] == ["git", "fetch", "origin"] for command, _ in commands) is False
    assert any(command[:2] == ["git", "checkout"] and command[-1] == "main" for command, _ in commands)
    assert any(command[:3] == ["git", "reset", "--hard"] and command[-1] == "origin/main" for command, _ in commands)

    shutil.rmtree(tmp_path / "repo-mirrors" / "demo")

    manager = RepoSeedManager(tmp_path)
    project = ProjectSpec(name="demo", url="https://github.com/org/demo.git", default_branch="main")

    commands: list[list[str]] = []

    def fake_git(command: list[str], *, cwd: Path) -> None:
        commands.append(command)
        if command[:2] == ["git", "clone"] and command[2] == project.url:
            raise RuntimeError("Git command failed (git clone): auth failed")
        if command[:2] == ["git", "clone"]:
            (tmp_path / "repo-mirrors" / "demo").mkdir(parents=True, exist_ok=True)

    manager._git = fake_git  # type: ignore[method-assign]

    seed_path = manager._ensure_seed(project)

    assert seed_path == tmp_path / "repo-mirrors" / "demo"
    assert commands[0] == ["git", "clone", "https://github.com/org/demo.git", str(tmp_path / "repo-mirrors" / "demo")]
    assert commands[1] == ["git", "clone", "git@github.com:org/demo.git", str(tmp_path / "repo-mirrors" / "demo")]


def test_repo_seed_manager_falls_back_to_http_with_token(tmp_path: Path) -> None:
    manager = RepoSeedManager(tmp_path, clone_token="secret-token")
    project = ProjectSpec(name="demo", url="https://github.com/org/demo", default_branch="main")

    commands: list[list[str]] = []

    def fake_git(command: list[str], *, cwd: Path) -> None:
        commands.append(command)
        if command[:2] == ["git", "clone"] and command[2] != "https://secret-token@github.com/org/demo":
            raise RuntimeError("Git command failed (git clone): auth failed")
        if command[:2] == ["git", "clone"]:
            (tmp_path / "repo-mirrors" / "demo").mkdir(parents=True, exist_ok=True)

    manager._git = fake_git  # type: ignore[method-assign]

    seed_path = manager._ensure_seed(project)

    assert seed_path == tmp_path / "repo-mirrors" / "demo"
    assert commands[0] == ["git", "clone", "https://github.com/org/demo", str(tmp_path / "repo-mirrors" / "demo")]
    assert commands[1] == ["git", "clone", "git@github.com:org/demo", str(tmp_path / "repo-mirrors" / "demo")]
    assert commands[2] == ["git", "clone", "https://secret-token@github.com/org/demo", str(tmp_path / "repo-mirrors" / "demo")]


def test_repo_seed_manager_cleanup_removes_repo_copy(tmp_path: Path) -> None:
    manager = RepoSeedManager(tmp_path)
    repo_path = tmp_path / "session" / "repos" / "frontend"
    nested = repo_path / "src"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "app.py").write_text("print('ok')\n", encoding="utf-8")

    manager.cleanup(repo_path)

    assert not repo_path.exists()


def test_setup_validation_root_is_path() -> None:
    from tiller.setup_prompts import _setup_validation_root

    assert isinstance(_setup_validation_root(), Path)


def test_session_cleanup_removes_provisioned_worktrees_before_workspace(tmp_path: Path) -> None:
    from tiller.models import AgentRuntimeConfig, SessionConfig, TillerConfig, TrackerConfig
    from tiller.trackers import InMemoryTrackerAdapter

    config = TillerConfig(
        tracker=TrackerConfig(type="memory", trigger_status="in_development"),
        agent=AgentRuntimeConfig(default="stub"),
        projects={
            "frontend": ProjectSpec(name="frontend", url="https://github.com/org/frontend", default_branch="main")
        },
        session=SessionConfig(base_path=tmp_path, keep_finished_sessions=False),
    )
    manager = __import__("tiller.session", fromlist=["SessionManager"]).SessionManager(
        config,
        InMemoryTrackerAdapter([]),
    )
    paths = SessionPaths(
        root=tmp_path / "session",
        agents_md=tmp_path / "session" / "AGENTS.md",
        task_md=tmp_path / "session" / "TASK.md",
        task_json=tmp_path / "session" / "task.json",
        state_md=tmp_path / "session" / "STATE.md",
        projects_json=tmp_path / "session" / "projects.json",
        repos_dir=tmp_path / "session" / "repos",
        attachments_dir=tmp_path / "session" / "attachments",
        mcp_dir=tmp_path / "session" / ".mcp",
        mcp_config=tmp_path / "session" / ".mcp" / "config.json",
        state_json=tmp_path / "session" / "session.json",
    )
    (paths.repos_dir / "frontend").mkdir(parents=True, exist_ok=True)
    paths.state_json.write_text(
        json.dumps({"provisioned_repos": ["frontend"]}),
        encoding="utf-8",
    )

    cleaned: list[Path] = []

    def fake_cleanup(repo_path: Path) -> None:
        cleaned.append(repo_path)

    manager.repo_seed_manager.cleanup = fake_cleanup  # type: ignore[method-assign]

    manager.cleanup(paths)

    assert cleaned == [paths.repos_dir / "frontend"]
    assert not paths.root.exists()


def test_project_status_reports_local_project_metadata(tmp_path: Path, monkeypatch) -> None:
    from tiller.operations import SessionOperations

    session_root = tmp_path / "session"
    session_root.mkdir(parents=True, exist_ok=True)
    config_path = tmp_path / "tiller.yaml"
    config_path.write_text(
        f"""
tracker:
  type: clickup
  token: clickup-token
  team_id: team-1
  trigger_status: DEVELOP
agent:
  default: stub
session:
  base_path: {tmp_path / 'sessions'}
  repo_store_path: {tmp_path / 'repo-mirrors'}
""",
        encoding="utf-8",
    )
    (session_root / "session.json").write_text(
        json.dumps(
            {
                "internal_task_id": "TASK-123",
                "tracker_task_id": "1",
                "agent_name": "stub",
                "workspace": str(session_root),
                "config_path": str(config_path),
                "state": "running",
                "provisioned_repos": [],
            }
        ),
        encoding="utf-8",
    )
    (session_root / "projects.json").write_text(
        json.dumps(
            {
                "backend": {
                    "url": "git@internal.example.com:core/backend.git",
                    "default_branch": "develop",
                    "description": "Backend service",
                    "source": "local_directory",
                    "source_path": str(tmp_path / "repo-mirrors" / "backend"),
                    "repo_path": "repos/backend",
                    "provision_method": "seed_copy",
                    "available": True,
                }
            }
        ),
        encoding="utf-8",
    )

    status = SessionOperations(session_root).project_status()

    assert status == [
        {
            "name": "backend",
            "path": str(session_root / "repos/backend"),
            "provisioned": False,
            "available": True,
            "url": "git@internal.example.com:core/backend.git",
            "default_branch": "develop",
            "description": "Backend service",
            "source": "local_directory",
            "source_path": str(tmp_path / "repo-mirrors" / "backend"),
        }
    ]


def test_handle_tracker_command_closes_tracker(tmp_path: Path, monkeypatch, capsys) -> None:
    from tiller.commands import handle_session_command

    session_root = tmp_path / "session"
    session_root.mkdir(parents=True, exist_ok=True)
    (session_root / "session.json").write_text(
        json.dumps(
            {
                "internal_task_id": "TASK-123",
                "tracker_task_id": "1",
                "agent_name": "stub",
                "workspace": str(session_root),
                "config_path": str(tmp_path / "tiller.yaml"),
                "state": "running",
                "provisioned_repos": [],
            }
        ),
        encoding="utf-8",
    )
    (session_root / "projects.json").write_text("{}", encoding="utf-8")
    (tmp_path / "tiller.yaml").write_text(
        """
tracker:
  type: clickup
  token: clickup-token
  team_id: team-1
  trigger_status: in_development
agent:
  default: stub
projects: {}
session:
  base_path: %s
""" % tmp_path.as_posix(),
        encoding="utf-8",
    )

    output = {"task_id": "1", "statuses": ["Backlog", "In Progress", "Done"]}
    monkeypatch.setattr("tiller.commands.SessionOperations.tracker_status_options", lambda self: output)

    exit_code = handle_session_command(
        __import__("argparse").Namespace(
            command="tracker",
            tracker_command="status-options",
            value=None,
            session=str(session_root),
            dest=None,
        )
    )

    assert exit_code == 0
    rendered = json.loads(capsys.readouterr().out)
    assert rendered == output


def test_handle_tracker_status_options_returns_live_statuses(tmp_path: Path, monkeypatch, capsys) -> None:
    from tiller.commands import handle_session_command

    session_root = tmp_path / "session-status-options"
    session_root.mkdir(parents=True, exist_ok=True)
    (session_root / "session.json").write_text(
        json.dumps(
            {
                "internal_task_id": "TASK-456",
                "tracker_task_id": "9",
                "agent_name": "stub",
                "workspace": str(session_root),
                "config_path": str(tmp_path / "tiller-status-options.yaml"),
                "state": "running",
                "provisioned_repos": [],
            }
        ),
        encoding="utf-8",
    )
    (session_root / "projects.json").write_text("{}", encoding="utf-8")
    (tmp_path / "tiller-status-options.yaml").write_text(
        """
tracker:
  type: clickup
  token: clickup-token
  team_id: team-1
  trigger_status: in_development
agent:
  default: stub
projects: {}
session:
  base_path: %s
""" % tmp_path.as_posix(),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "tiller.commands.SessionOperations.tracker_status_options",
        lambda self: {"task_id": "9", "statuses": ["Backlog", "In Progress", "Done"]},
    )

    exit_code = handle_session_command(
        __import__("argparse").Namespace(
            command="tracker",
            tracker_command="status-options",
            value=None,
            session=str(session_root),
            dest=None,
        )
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output == {"task_id": "9", "statuses": ["Backlog", "In Progress", "Done"]}


def test_memory_provider_retain_recall(tmp_path: Path) -> None:
    from tiller.memory.provider import LocalMemoryProvider

    provider = LocalMemoryProvider(tmp_path / "memory")
    provider.retain(bank_id="session:TASK-123", content="Project uses poetry", context="tooling")
    provider.retain(bank_id="session:TASK-123", content="Run pytest with -q", context="tests")

    recall = provider.recall(bank_id="session:TASK-123", query="how do tests run", limit=5)
    assert recall.bank_id == "session:TASK-123"
    assert len(recall.entries) == 1
    assert recall.entries[0].content == "Run pytest with -q"



def test_langmem_memory_provider_uses_manager_and_local_recall(monkeypatch, tmp_path: Path) -> None:
    import sys
    import types

    from tiller.memory.provider import LangMemMemoryProvider

    invoked_payloads: list[dict[str, object]] = []

    class FakeManager:
        def invoke(self, payload):
            invoked_payloads.append(payload)
            return [{"content": "Project uses poetry"}, {"content": "Tests run with pytest -q"}]

    def fake_create_memory_manager(model_name: str):
        assert model_name == "openai:gpt-5-mini"
        return FakeManager()

    monkeypatch.setitem(sys.modules, "langmem", types.SimpleNamespace(create_memory_manager=fake_create_memory_manager))

    provider = LangMemMemoryProvider(
        llm_provider="openai",
        llm_model="gpt-5-mini",
        llm_api_key="secret",
        llm_api_key_env="OPENAI_API_KEY",
        base_path=tmp_path / "langmem-db",
    )
    retained = provider.retain(bank_id="session:TASK-2", content="The project uses poetry and pytest", context="tooling")
    recalled = provider.recall(bank_id="session:TASK-2", query="pytest")

    assert retained.content == "Project uses poetry"
    assert invoked_payloads == [
        {
            "messages": [
                {"role": "system", "content": "tooling"},
                {"role": "user", "content": "The project uses poetry and pytest"},
            ]
        }
    ]
    assert [entry.content for entry in recalled.entries] == ["Tests run with pytest -q"]
def test_hindsight_memory_provider_uses_embedded_client(monkeypatch, tmp_path: Path) -> None:
    import sys
    import types

    from tiller.memory.provider import HindsightMemoryProvider

    retained_payloads: list[dict[str, object]] = []

    class FakeHindsightServer:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self.url = "http://127.0.0.1:9999"
            self.exited = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            self.exited = True

    class FakeHindsightClient:
        def __init__(self, base_url: str) -> None:
            self.base_url = base_url

        def retain(self, **payload):
            retained_payloads.append(payload)
            return {"ok": True}

        def recall(self, **payload):
            assert payload == {"bank_id": "history", "query": "pytest"}
            return {
                "results": [
                    {
                        "id": "mem-1",
                        "content": "Run pytest -q",
                        "context": "tests",
                        "metadata": {"kind": "tip"},
                    }
                ]
            }

    monkeypatch.setitem(
        sys.modules,
        "hindsight",
        types.SimpleNamespace(HindsightServer=FakeHindsightServer, HindsightClient=FakeHindsightClient),
    )

    provider = HindsightMemoryProvider(
        llm_provider="openai",
        llm_model="gpt-5-mini",
        llm_api_key="secret",
        base_path=tmp_path / "hindsight-db",
    )
    retained = provider.retain(bank_id="history", content="Run pytest -q", context="tests", metadata={"kind": "tip"})
    recalled = provider.recall(bank_id="history", query="pytest")
    provider.close()

    assert retained.content == "Run pytest -q"
    assert retained_payloads == [
        {
            "bank_id": "history",
            "content": "Run pytest -q",
            "context": "tests",
            "metadata": {"kind": "tip"},
        }
    ]
    assert recalled.entries[0].content == "Run pytest -q"
    assert recalled.entries[0].metadata == {"kind": "tip"}



def test_load_config_persists_resolved_config_path(tmp_path: Path) -> None:
    config_path = tmp_path / "nested" / "custom-tiller.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        """
tracker:
  type: memory
  trigger_status: in_development
agent:
  default: stub
projects: {}
session:
  base_path: %s
""" % tmp_path.as_posix(),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.config_path == config_path.resolve()


def test_load_config_supports_named_trackers(tmp_path: Path) -> None:
    config_path = tmp_path / "multi-tiller.yaml"
    config_path.write_text(
        """
trackers:
  support:
    type: memory
    trigger_status: support-new
  product:
    type: memory
    trigger_status: product-new
    poll_interval: 15
agent:
  default: stub
projects: {}
session:
  base_path: %s
""" % tmp_path.as_posix(),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert set(config.trackers) == {"support", "product"}
    assert config.get_tracker("support").name == "support"
    assert config.get_tracker("support").trigger_status == "support-new"
    assert config.get_tracker("product").poll_interval == 15
    assert config.tracker.name == "support"


def test_load_session_context_accepts_external_task_id_fallback(tmp_path: Path) -> None:
    from tiller.runtime import load_session_context

    session_root = tmp_path / "session-external-id"
    session_root.mkdir(parents=True, exist_ok=True)
    (session_root / "session.json").write_text(
        json.dumps(
            {
                "internal_task_id": "TASK-EXT1",
                "external_task_id": "telegram-123",
                "agent_name": "stub",
                "workspace": str(session_root),
                "config_path": str(tmp_path / "tiller.yaml"),
                "state": "running",
                "provisioned_repos": [],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "tiller.yaml").write_text(
        """
tracker:
  type: memory
  trigger_status: in_development
agent:
  default: stub
projects: {}
session:
  base_path: %s
""" % (tmp_path.as_posix(),),
        encoding="utf-8",
    )

    context = load_session_context(session_root)

    assert context.record.tracker_name == "default"
    assert context.record.tracker_type == "memory"
    assert context.record.tracker_task_id == "telegram-123"



def test_build_tracker_server_accepts_external_task_id_fallback(tmp_path: Path) -> None:
    session_root = tmp_path / "session-mcp-external-id"
    session_root.mkdir(parents=True, exist_ok=True)
    (session_root / "session.json").write_text(
        json.dumps(
            {
                "internal_task_id": "TASK-EXT2",
                "external_task_id": "telegram-456",
                "agent_name": "stub",
                "workspace": str(session_root),
                "config_path": str(tmp_path / "tiller.yaml"),
                "state": "running",
                "provisioned_repos": [],
            }
        ),
        encoding="utf-8",
    )
    (session_root / "projects.json").write_text("{}", encoding="utf-8")

    server = build_tracker_server(session_root)

    assert server is not None



def test_service_reconciles_orphaned_running_session(tmp_path: Path) -> None:
    config_path = tmp_path / "tiller.yaml"
    config_path.write_text(
        """
tracker:
  type: memory
  trigger_status: in_development
agent:
  default: stub
projects: {}
session:
  base_path: %s
  keep_finished_sessions: true
""" % tmp_path.as_posix(),
        encoding="utf-8",
    )

    config = load_config(config_path)
    tracker_config = config.tracker
    tracker = InMemoryTrackerAdapter([])
    service = TillerService(config=config, tracker_config=tracker_config, tracker=tracker, harness=AgentHarness({"stub": StubAdapter()}))

    session_root = config.session.base_path / "TASK-ORPHAN"
    session_root.mkdir(parents=True, exist_ok=True)
    (session_root / "session.json").write_text(
        json.dumps(
            {
                "internal_task_id": "TASK-ORPHAN",
                "tracker_name": "default",
                "tracker_task_id": "task-1",
                "external_task_id": "task-1",
                "tracker_type": "memory",
                "agent_name": "stub",
                "workspace": str(session_root),
                "config_path": str(config_path.resolve()),
                "state": "running",
                "process_id": 999999,
                "provisioned_repos": [],
            }
        ),
        encoding="utf-8",
    )

    asyncio.run(service.reconcile_orphaned_sessions())

    payload = json.loads((session_root / "session.json").read_text(encoding="utf-8"))
    assert payload["state"] == "interrupted"
    assert payload["process_id"] is None
    events = [json.loads(line) for line in (session_root / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(event["type"] == "session_interrupted" for event in events)


def test_tracker_service_uses_tracker_name_in_session_and_dedup(tmp_path: Path) -> None:
    config_path = tmp_path / "multi-tracker.yaml"
    config_path.write_text(
        """
trackers:
  alpha:
    type: memory
    trigger_status: in_development
  beta:
    type: memory
    trigger_status: in_development
agent:
  default: stub
projects: {}
session:
  base_path: %s
  keep_finished_sessions: true
""" % tmp_path.as_posix(),
        encoding="utf-8",
    )

    config = load_config(config_path)
    shared_task = Task(id="same-id", title="Shared", description="Body", status="in_development")
    alpha_tracker = InMemoryTrackerAdapter([shared_task])
    beta_tracker = InMemoryTrackerAdapter([Task(id="same-id", title="Shared", description="Body", status="in_development")])
    harness = AgentHarness({"stub": StubAdapter()})

    alpha_service = TrackerService(
        config=config,
        tracker_config=config.get_tracker("alpha"),
        tracker=alpha_tracker,
        harness=harness,
    )
    beta_service = TrackerService(
        config=config,
        tracker_config=config.get_tracker("beta"),
        tracker=beta_tracker,
        harness=harness,
    )

    async def run_services() -> None:
        await alpha_service.run_once()
        await beta_service.run_once()
        await asyncio.gather(*alpha_service.active_sessions.values(), *beta_service.active_sessions.values())

    asyncio.run(run_services())

    assert "alpha:same-id" in alpha_service.processed_tasks
    assert "beta:same-id" in beta_service.processed_tasks

    alpha_session = json.loads((config.session.base_path / alpha_service.session_manager.make_internal_task_id(shared_task) / "session.json").read_text(encoding="utf-8"))
    beta_session = json.loads((config.session.base_path / beta_service.session_manager.make_internal_task_id(shared_task) / "session.json").read_text(encoding="utf-8"))

    assert alpha_session["tracker_name"] == "alpha"
    assert alpha_session["tracker_type"] == "memory"
    assert beta_session["tracker_name"] == "beta"
    assert beta_session["tracker_type"] == "memory"
    assert alpha_session["internal_task_id"] != beta_session["internal_task_id"]


def test_multi_tracker_service_runs_all_services() -> None:
    class RecordingService:
        def __init__(self) -> None:
            self.runs = 0

        async def run_forever(self) -> None:
            self.runs += 1

        async def shutdown(self) -> None:
            return None

    first = RecordingService()
    second = RecordingService()

    asyncio.run(MultiTrackerService([first, second]).run_forever())

    assert first.runs == 1
    assert second.runs == 1


def test_session_memory_service_builds_langmem_provider(monkeypatch, tmp_path: Path) -> None:
    from tiller.config import load_config
    from tiller.memory.service import SessionMemoryService
    from tiller.runtime import load_session_context

    session_root = tmp_path / "session-langmem"
    session_root.mkdir(parents=True, exist_ok=True)
    (session_root / "session.json").write_text(
        json.dumps(
            {
                "internal_task_id": "TASK-901",
                "tracker_task_id": "78",
                "agent_name": "stub",
                "workspace": str(session_root),
                "config_path": str(tmp_path / "tiller-langmem.yaml"),
                "state": "running",
                "provisioned_repos": [],
            }
        ),
        encoding="utf-8",
    )
    (session_root / "projects.json").write_text("{}", encoding="utf-8")
    (tmp_path / "tiller-langmem.yaml").write_text(
        """
tracker:
  type: memory
  trigger_status: in_development
agent:
  default: stub
memory:
  enabled: true
  provider: langmem
  base_path: %s
  llm_provider: openai
  llm_model: gpt-5-mini
  llm_api_key_env: LANGMEM_TEST_KEY
projects: {}
session:
  base_path: %s
""" % ((tmp_path / "langmem-store").as_posix(), tmp_path.as_posix()),
        encoding="utf-8",
    )

    captured: dict[str, object] = {}

    class StubLangMemProvider:
        def __init__(self, *, llm_provider: str, llm_model: str | None, llm_api_key: str, llm_api_key_env: str, base_path: Path) -> None:
            captured["llm_provider"] = llm_provider
            captured["llm_model"] = llm_model
            captured["llm_api_key"] = llm_api_key
            captured["llm_api_key_env"] = llm_api_key_env
            captured["base_path"] = base_path

        def retain(self, *, bank_id: str, content: str, context: str | None = None, metadata: dict[str, object] | None = None):
            raise AssertionError("retain should not be called")

        def recall(self, *, bank_id: str, query: str, limit: int = 5):
            raise AssertionError("recall should not be called")

    monkeypatch.setattr("tiller.memory.service.LangMemMemoryProvider", StubLangMemProvider)
    monkeypatch.setenv("LANGMEM_TEST_KEY", "langmem-secret")

    config = load_config(tmp_path / "tiller-langmem.yaml")
    context = load_session_context(session_root)
    service = SessionMemoryService.from_context(context, config)

    assert service.enabled() is True
    assert captured == {
        "llm_provider": "openai",
        "llm_model": "gpt-5-mini",
        "llm_api_key": "langmem-secret",
        "llm_api_key_env": "LANGMEM_TEST_KEY",
        "base_path": (tmp_path / "langmem-store").resolve(),
    }



def test_session_memory_service_builds_hindsight_provider(monkeypatch, tmp_path: Path) -> None:
    from tiller.config import load_config
    from tiller.memory.service import SessionMemoryService
    from tiller.runtime import load_session_context

    session_root = tmp_path / "session-hindsight"
    session_root.mkdir(parents=True, exist_ok=True)
    (session_root / "session.json").write_text(
        json.dumps(
            {
                "internal_task_id": "TASK-900",
                "tracker_task_id": "77",
                "agent_name": "stub",
                "workspace": str(session_root),
                "config_path": str(tmp_path / "tiller-hindsight.yaml"),
                "state": "running",
                "provisioned_repos": [],
            }
        ),
        encoding="utf-8",
    )
    (session_root / "projects.json").write_text("{}", encoding="utf-8")
    (tmp_path / "tiller-hindsight.yaml").write_text(
        """
tracker:
  type: memory
  trigger_status: in_development
agent:
  default: stub
memory:
  enabled: true
  provider: hindsight
  base_path: %s
  llm_provider: openai
  llm_model: gpt-5-mini
  llm_api_key_env: HINDSIGHT_TEST_KEY
projects: {}
session:
  base_path: %s
""" % ((tmp_path / "hindsight-store").as_posix(), tmp_path.as_posix()),
        encoding="utf-8",
    )

    captured: dict[str, object] = {}

    class StubHindsightProvider:
        def __init__(self, *, llm_provider: str, llm_model: str | None, llm_api_key: str, base_path: Path | None = None) -> None:
            captured["llm_provider"] = llm_provider
            captured["llm_model"] = llm_model
            captured["llm_api_key"] = llm_api_key
            captured["base_path"] = base_path

        def retain(self, *, bank_id: str, content: str, context: str | None = None, metadata: dict[str, object] | None = None):
            raise AssertionError("not used")

        def recall(self, *, bank_id: str, query: str, limit: int = 5):
            raise AssertionError("not used")

    monkeypatch.setenv("HINDSIGHT_TEST_KEY", "api-key")
    monkeypatch.setattr("tiller.memory.service.HindsightMemoryProvider", StubHindsightProvider)

    config = load_config(tmp_path / "tiller-hindsight.yaml")
    context = load_session_context(session_root)
    service = SessionMemoryService.from_context(context, config)

    assert service.provider.__class__ is StubHindsightProvider
    assert captured == {
        "llm_provider": "openai",
        "llm_model": "gpt-5-mini",
        "llm_api_key": "api-key",
        "base_path": (tmp_path / "hindsight-store").resolve(),
    }



def test_handle_project_use_uses_seed_copy_without_branch(tmp_path: Path, monkeypatch, capsys) -> None:
    from tiller.commands import handle_session_command

    session_root = tmp_path / "session"
    session_root.mkdir(parents=True, exist_ok=True)
    (session_root / "session.json").write_text(
        json.dumps(
            {
                "internal_task_id": "TASK-123",
                "tracker_task_id": "1",
                "agent_name": "stub",
                "workspace": str(session_root),
                "config_path": str(tmp_path / "tiller.yaml"),
                "state": "running",
                "provisioned_repos": [],
            }
        ),
        encoding="utf-8",
    )
    (session_root / "projects.json").write_text(
        json.dumps(
            {
                "backend": {
                    "url": "https://github.com/org/backend",
                    "default_branch": "main",
                    "repo_path": "repos/backend",
                    "provision_method": "seed_copy",
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "tiller.yaml").write_text(
        """
tracker:
  type: memory
  trigger_status: in_development
agent:
  default: stub
projects:
  backend:
    url: https://github.com/org/backend
    default_branch: main
session:
  base_path: %s
""" % tmp_path.as_posix(),
        encoding="utf-8",
    )

    expected = {
        "name": "backend",
        "path": str(session_root / "repos" / "backend"),
        "already_provisioned": False,
        "url": "https://github.com/org/backend",
        "default_branch": "main",
        "branch": None,
    }
    monkeypatch.setattr("tiller.commands.SessionOperations.project_use", lambda self, name, reason: expected)

    exit_code = handle_session_command(
        __import__("argparse").Namespace(
            command="project",
            project_command="use",
            name="backend",
            reason="implementation",
            branch=None,
            session=str(session_root),
        )
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output == expected


def test_handle_create_pr_reads_repo_metadata(tmp_path: Path, monkeypatch, capsys) -> None:
    from tiller.commands import handle_session_command

    session_root = tmp_path / "session"
    repo_root = session_root / "repos" / "backend"
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / ".tiller-repo.json").write_text(
        json.dumps({"project": "backend", "branch": "tiller/task-backend"}),
        encoding="utf-8",
    )
    (session_root / "session.json").write_text(
        json.dumps(
            {
                "internal_task_id": "TASK-123",
                "tracker_task_id": "1",
                "agent_name": "stub",
                "workspace": str(session_root),
                "config_path": str(tmp_path / "tiller.yaml"),
                "state": "running",
                "provisioned_repos": ["backend"],
            }
        ),
        encoding="utf-8",
    )
    (session_root / "projects.json").write_text(
        json.dumps(
            {
                "backend": {
                    "url": "https://github.com/org/backend",
                    "default_branch": "main",
                    "repo_path": "repos/backend",
                    "provision_method": "seed_copy",
                }
            }
        ),
        encoding="utf-8",
    )
    body_file = tmp_path / "pr.md"
    body_file.write_text("PR body", encoding="utf-8")
    (tmp_path / "tiller.yaml").write_text(
        """
tracker:
  type: memory
  trigger_status: in_development
agent:
  default: stub
github:
  enabled: true
  token: yaml-token
projects:
  backend:
    url: https://github.com/org/backend
    default_branch: main
session:
  base_path: %s
""" % tmp_path.as_posix(),
        encoding="utf-8",
    )

    class StubGitHubClient:
        def __init__(self, config) -> None:
            self.config = config

        def create_pull_request(self, *, repo, title, body, head, base) -> PullRequestRef:
            assert repo.owner == "org"
            assert repo.name == "backend"
            assert title == "feat: test"
            assert body == "PR body"
            assert head == "tiller/task-backend"
            assert base == "main"
            return PullRequestRef(
                number=7,
                url="https://api.github.com/repos/org/backend/pulls/7",
                html_url="https://github.com/org/backend/pull/7",
                title=title,
                head=head,
                base=base,
                state="open",
            )

        def close(self) -> None:
            return None

    monkeypatch.setattr("tiller.pr_providers.GitHubClient", StubGitHubClient)
    exit_code = handle_session_command(
        __import__("argparse").Namespace(
            command="pr",
            pr_command="create",
            repo="backend",
            title="feat: test",
            body=None,
            body_file=str(body_file),
            base=None,
            head=None,
            session=str(session_root),
        )
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["number"] == 7
    assert output["html_url"] == "https://github.com/org/backend/pull/7"
    assert output["provider"] == "github"



def test_openai_agents_adapter_requires_runner_module(monkeypatch) -> None:
    adapter = OpenAIAgentsAdapter()

    monkeypatch.setattr("tiller.agents.openai_agents.shutil.which", lambda command: "/usr/bin/python" if command == "python" else None)
    monkeypatch.setattr("tiller.agents.openai_agents.importlib.util.find_spec", lambda name: None)

    assert adapter.is_available() is False


def test_cloudflare_agents_adapter_requires_wrangler(monkeypatch) -> None:
    adapter = CloudflareAgentsAdapter()

    def fake_which(command: str) -> str | None:
        return {
            "npx": "/usr/bin/npx",
            "wrangler": None,
        }.get(command)

    monkeypatch.setattr("tiller.agents.cloudflare_agents.shutil.which", fake_which)

    assert adapter.is_available() is False


def test_composio_adapter_requires_composio_binary(monkeypatch) -> None:
    adapter = ComposioAdapter()

    def fake_which(command: str) -> str | None:
        return {
            "ao": "/usr/bin/ao",
            "composio": None,
        }.get(command)

    monkeypatch.setattr("tiller.agents.composio.shutil.which", fake_which)

    assert adapter.is_available() is False


def test_iac_adapter_requires_terraform_or_pulumi(monkeypatch) -> None:
    adapter = IaCAdapter()

    def fake_which(command: str) -> str | None:
        return {
            "bash": "/usr/bin/bash",
            "terraform": None,
            "pulumi": None,
        }.get(command)

    monkeypatch.setattr("tiller.agents.iac.shutil.which", fake_which)

    assert adapter.is_available() is False


def test_clickup_setup_uses_manual_tag_filter(monkeypatch) -> None:
    provider = ClickUpSetupProvider()
    calls: list[tuple[str, str | None]] = []

    async def fake_list_teams(self):
        return [{"id": "team-1", "name": "Workspace"}]

    async def fake_list_team_members(self, team_id=None):
        calls.append(("members", team_id))
        return [{"id": "user-1", "name": "Alice"}]

    async def fake_list_team_tags(self, team_id=None):
        calls.append(("tags", team_id))
        return ["backend", "frontend"]

    async def fake_aclose(self):
        return None

    monkeypatch.setattr("tiller.setup_clickup.ClickUpTrackerAdapter.list_teams", fake_list_teams)
    monkeypatch.setattr("tiller.setup_clickup.ClickUpTrackerAdapter.list_team_members", fake_list_team_members)
    monkeypatch.setattr("tiller.setup_clickup.ClickUpTrackerAdapter.list_team_tags", fake_list_team_tags)
    monkeypatch.setattr("tiller.setup_clickup.ClickUpTrackerAdapter.aclose", fake_aclose)

    install_questionary_stub(
        monkeypatch,
        password_answers=["clickup-token"],
        text_answers=["develop", "60"],
        confirm_answers=[False, True],
        select_answers=["team-1", "backend"],
    )

    result = asyncio.run(provider.collect())

    assert result["type"] == "clickup"
    assert result["team_id"] == "team-1"
    assert result["trigger_status"] == "develop"
    assert result["tag"] == "backend"
    assert calls == [("tags", "team-1")]


def test_clickup_list_team_members_reads_nested_team_payload(monkeypatch) -> None:
    adapter = ClickUpTrackerAdapter(token="token", team_id="team-1")
    requested_paths: list[str] = []

    async def fake_get(path: str, *args, **kwargs):
        requested_paths.append(path)
        request = httpx.Request("GET", f"https://api.clickup.com/api/v2{path}")
        if path == "/team/team-1":
            return httpx.Response(
                status_code=200,
                request=request,
                json={
                    "team": {
                        "members": [
                            {"user": {"id": "user-2", "username": "Bob"}},
                            {"user": {"id": "user-1", "email": "alice@example.com"}},
                        ]
                    }
                },
            )
        raise AssertionError(f"Unexpected path: {path}")

    async def fake_aclose():
        return None

    monkeypatch.setattr(adapter._client, "get", fake_get)
    monkeypatch.setattr(adapter._client, "aclose", fake_aclose)

    try:
        assert asyncio.run(adapter.list_team_members()) == [
            {"id": "user-2", "name": "Bob"},
            {"id": "user-1", "name": "alice@example.com"},
        ]
        assert requested_paths == ["/team/team-1"]
    finally:
        asyncio.run(adapter.aclose())


def test_clickup_list_team_members_falls_back_to_task_assignees(monkeypatch) -> None:
    adapter = ClickUpTrackerAdapter(token="token", team_id="team-1")
    requested: list[tuple[str, dict[str, object] | None]] = []

    async def fake_get(path: str, *args, **kwargs):
        requested.append((path, kwargs.get("params")))
        request = httpx.Request("GET", f"https://api.clickup.com/api/v2{path}")
        if path == "/team/team-1":
            return httpx.Response(status_code=200, request=request, json={"team": {"members": []}})
        if path == "/team/team-1/task":
            return httpx.Response(
                status_code=200,
                request=request,
                json={
                    "tasks": [
                        {
                            "assignees": [
                                {"id": "user-2", "username": " Bob "},
                                {"id": "user-1", "email": "alice@example.com"},
                            ]
                        },
                        {
                            "assignees": [
                                {"id": "user-2", "username": "Bob"},
                            ]
                        },
                    ],
                    "last_page": True,
                },
            )
        raise AssertionError(f"Unexpected path: {path}")

    async def fake_aclose():
        return None

    monkeypatch.setattr(adapter._client, "get", fake_get)
    monkeypatch.setattr(adapter._client, "aclose", fake_aclose)

    try:
        assert asyncio.run(adapter.list_team_members()) == [
            {"id": "user-1", "name": "alice@example.com"},
            {"id": "user-2", "name": "Bob"},
        ]
        assert requested == [
            ("/team/team-1", None),
            (
                "/team/team-1/task",
                {
                    "include_closed": "true",
                    "subtasks": "true",
                    "page": 0,
                },
            ),
        ]
    finally:
        asyncio.run(adapter.aclose())


def test_clickup_list_team_statuses_uses_space_statuses(monkeypatch) -> None:
    adapter = ClickUpTrackerAdapter(token="token", team_id="team-1")

    async def fake_list_team_spaces(team_id=None):
        assert team_id == "team-1"
        return [
            {
                "id": "space-1",
                "statuses": [
                    {"status": "to do"},
                    {"status": "develop"},
                ],
            },
            {
                "id": "space-2",
                "statuses": [
                    {"status": "review"},
                    {"status": "develop"},
                ],
            },
        ]

    async def fake_aclose():
        return None

    monkeypatch.setattr(adapter, "list_team_spaces", fake_list_team_spaces)
    monkeypatch.setattr(adapter._client, "aclose", fake_aclose)

    try:
        assert asyncio.run(adapter.list_team_statuses("team-1")) == ["develop", "review", "to do"]
    finally:
        asyncio.run(adapter.aclose())


def test_clickup_list_team_tags_uses_space_tag_endpoints(monkeypatch) -> None:
    adapter = ClickUpTrackerAdapter(token="token", team_id="team-1")
    requested_paths: list[str] = []

    async def fake_get(path: str, *args, **kwargs):
        requested_paths.append(path)
        request = httpx.Request("GET", f"https://api.clickup.com/api/v2{path}")
        if path == "/team/team-1/space":
            return httpx.Response(
                status_code=200,
                request=request,
                json={"spaces": [{"id": "space-1"}, {"id": "space-2"}]},
            )
        if path == "/space/space-1/tag":
            return httpx.Response(
                status_code=200,
                request=request,
                json={"tags": [{"name": "backend"}, {"name": "api"}]},
            )
        if path == "/space/space-2/tag":
            return httpx.Response(
                status_code=200,
                request=request,
                json={"tags": [{"name": "api"}, {"name": "frontend"}]},
            )
        raise AssertionError(f"Unexpected path: {path}")

    async def fake_aclose():
        return None

    monkeypatch.setattr(adapter._client, "get", fake_get)
    monkeypatch.setattr(adapter._client, "aclose", fake_aclose)

    try:
        assert asyncio.run(adapter.list_team_tags()) == ["api", "backend", "frontend"]
        assert requested_paths == ["/team/team-1/space", "/space/space-1/tag", "/space/space-2/tag"]
    finally:
        asyncio.run(adapter.aclose())


def test_clickup_list_team_tags_falls_back_to_task_tags(monkeypatch) -> None:
    adapter = ClickUpTrackerAdapter(token="token", team_id="team-1")
    requested: list[tuple[str, dict[str, object] | None]] = []

    async def fake_get(path: str, *args, **kwargs):
        requested.append((path, kwargs.get("params")))
        request = httpx.Request("GET", f"https://api.clickup.com/api/v2{path}")
        if path == "/team/team-1/space":
            return httpx.Response(status_code=200, request=request, json={"spaces": []})
        if path == "/team/team-1/task":
            return httpx.Response(
                status_code=200,
                request=request,
                json={
                    "tasks": [
                        {"tags": [{"name": " backend "}, {"name": "api"}]},
                        {"tags": [{"name": "api"}, {"name": "frontend"}]},
                    ],
                    "last_page": True,
                },
            )
        raise AssertionError(f"Unexpected path: {path}")

    async def fake_aclose():
        return None

    monkeypatch.setattr(adapter._client, "get", fake_get)
    monkeypatch.setattr(adapter._client, "aclose", fake_aclose)

    try:
        assert asyncio.run(adapter.list_team_tags()) == ["api", "backend", "frontend"]
        assert requested == [
            ("/team/team-1/space", None),
            (
                "/team/team-1/task",
                {
                    "include_closed": "true",
                    "subtasks": "true",
                    "page": 0,
                },
            ),
        ]
    finally:
        asyncio.run(adapter.aclose())


    rendered = render_setup_config({"projects": {}, "github": {"enabled": True}})
    assert "projects: {}" in rendered
    assert "enabled: true" in rendered


def test_run_startup_validation_checks_tracker_and_github(monkeypatch, tmp_path: Path) -> None:
    import tiller.cli as cli_module

    config_path = tmp_path / "tiller.yaml"
    config_path.write_text(
        """
tracker:
  type: clickup
  token: clickup-token
  team_id: team-1
  trigger_status: DEVELOP
agent:
  default: stub
github:
  enabled: true
  token: github-token
projects: {}
session:
  base_path: %s
""" % tmp_path.as_posix(),
        encoding="utf-8",
    )

    validated: list[str] = []

    class StubTracker:
        async def validate(self) -> None:
            validated.append("tracker")

    class StubGitHubClient:
        def __init__(self, config) -> None:
            assert config.token == "github-token"

        def validate(self) -> dict[str, object]:
            validated.append("github")
            return {"login": "octocat"}

        def close(self) -> None:
            return None

    monkeypatch.setattr(cli_module, "build_tracker_adapter", lambda tracker_type, **options: StubTracker())
    monkeypatch.setattr(cli_module, "GitHubClient", StubGitHubClient)

    asyncio.run(cli_module._validate_startup(str(config_path)))

    assert validated == ["tracker", "github"]


def test_run_startup_validation_skips_github_when_disabled(monkeypatch, tmp_path: Path) -> None:
    import tiller.cli as cli_module

    config_path = tmp_path / "tiller.yaml"
    config_path.write_text(
        """
tracker:
  type: memory
  trigger_status: in_development
agent:
  default: stub
projects: {}
session:
  base_path: %s
""" % tmp_path.as_posix(),
        encoding="utf-8",
    )

    validated: list[str] = []

    class StubTracker:
        async def validate(self) -> None:
            validated.append("tracker")

    def fail_github_client(_config):
        raise AssertionError("GitHub client should not be created when GitHub is disabled")

    monkeypatch.setattr(cli_module, "build_tracker_adapter", lambda tracker_type, **options: StubTracker())
    monkeypatch.setattr(cli_module, "GitHubClient", fail_github_client)

    asyncio.run(cli_module._validate_startup(str(config_path)))

    assert validated == ["tracker"]


def test_run_setup_writes_guided_config(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "tiller.yaml"

    async def fake_collect_clickup_setup(self) -> dict[str, object]:
        return {
            "type": "clickup",
            "token": "clickup-token",
            "team_id": "team-1",
            "trigger_status": "DEVELOP",
            "poll_interval": 60,
            "assignee": "user-1",
            "tag": "backend",
        }

    repo_payload_calls: list[dict[str, object]] = []

    def fake_list_accessible_github_repos(github_payload: dict[str, object]) -> list[dict[str, object]]:
        repo_payload_calls.append(github_payload)
        return [
            {
                "name": "frontend",
                "full_name": "org/frontend",
                "url": "https://github.com/org/frontend.git",
                "default_branch": "main",
                "description": "Frontend app",
            }
        ]

    monkeypatch.setattr("tiller.setup_clickup.ClickUpSetupProvider.collect", fake_collect_clickup_setup)
    monkeypatch.setattr(
        "tiller.setup.load_harness",
        lambda _path: AgentHarness(
            {
                "codex": AvailableSetupAdapter("codex", "codex"),
                "claude-code": AvailableSetupAdapter("claude-code", "claude"),
            }
        ),
    )
    monkeypatch.setattr("tiller.setup._list_accessible_github_repos", fake_list_accessible_github_repos)

    install_questionary_stub(
        monkeypatch,
        select_answers=["codex", "clickup", "token", "all"],
        text_answers=["gpt-5"],
        password_answers=["yaml-github-token"],
        confirm_answers=[True],
    )

    exit_code = asyncio.run(run_setup(str(config_path)))

    assert exit_code == 0
    rendered = config_path.read_text(encoding="utf-8")
    assert "trackers:" in rendered
    assert "main:" in rendered
    assert "default: codex" in rendered
    assert "model: gpt-5" in rendered
    assert "token: clickup-token" in rendered
    assert "team_id: team-1" in rendered
    assert "assignee: user-1" in rendered
    assert "tag: backend" in rendered
    assert "token: yaml-github-token" in rendered
    assert "token_env: GITHUB_API_TOKEN" in rendered
    assert "frontend:" in rendered
    assert "description: Frontend app" in rendered
    assert "url: https://github.com/org/frontend.git" in rendered
    assert repo_payload_calls and repo_payload_calls[0]["token"] == "yaml-github-token"

    loaded = load_config(config_path)
    assert loaded.agent.default == "codex"
    assert loaded.agent.model == "gpt-5"
    assert loaded.trackers["main"].options["team_id"] == "team-1"
    assert loaded.trackers["main"].options["assignee"] == "user-1"
    assert loaded.github.token == "yaml-github-token"
    assert loaded.projects["frontend"].description == "Frontend app"
    assert loaded.session.base_path == Path("~/.tiller/sessions").expanduser().resolve()
    assert loaded.session.cleanup_after_hours == 24
    assert loaded.session.keep_finished_sessions is True


def test_run_setup_writes_guided_config_for_telegram(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "tiller-telegram.yaml"

    async def fake_collect_telegram_setup(self) -> dict[str, object]:
        return {
            "type": "telegram",
            "bot_token": "telegram-bot-token",
            "state_path": "~/.tiller/telegram-state.json",
            "allowed_chat_ids": ["-1001234567890"],
            "allowed_user_ids": ["111", "222"],
            "trigger_status": "new",
            "poll_interval": 5,
        }

    repo_payload_calls: list[dict[str, object]] = []

    def fake_list_accessible_github_repos(github_payload: dict[str, object]) -> list[dict[str, object]]:
        repo_payload_calls.append(github_payload)
        return [
            {
                "name": "backend",
                "full_name": "org/backend",
                "url": "https://github.com/org/backend.git",
                "default_branch": "develop",
                "description": "Backend service",
            }
        ]

    monkeypatch.setattr("tiller.setup_telegram.TelegramSetupProvider.collect", fake_collect_telegram_setup)
    monkeypatch.setattr(
        "tiller.setup.load_harness",
        lambda _path: AgentHarness(
            {
                "codex": AvailableSetupAdapter("codex", "codex"),
                "claude-code": AvailableSetupAdapter("claude-code", "claude"),
            }
        ),
    )
    monkeypatch.setattr("tiller.setup._list_accessible_github_repos", fake_list_accessible_github_repos)

    install_questionary_stub(
        monkeypatch,
        select_answers=["codex", "telegram", "token", "all"],
        text_answers=["gpt-5"],
        password_answers=["yaml-github-token"],
        confirm_answers=[True],
    )

    exit_code = asyncio.run(run_setup(str(config_path)))

    assert exit_code == 0
    rendered = config_path.read_text(encoding="utf-8")
    assert "trackers:" in rendered
    assert "main:" in rendered
    assert "default: codex" in rendered
    assert "model: gpt-5" in rendered
    assert "type: telegram" in rendered
    assert "bot_token: telegram-bot-token" in rendered
    assert "state_path: ~/.tiller/telegram-state.json" in rendered
    assert "allowed_chat_ids:" in rendered
    assert '- "-1001234567890"' in rendered or "- '-1001234567890'" in rendered or "- -1001234567890" in rendered
    assert "allowed_user_ids:" in rendered
    assert "trigger_status: new" in rendered
    assert "poll_interval: 5" in rendered
    assert "token: yaml-github-token" in rendered
    assert "backend:" in rendered
    assert "description: Backend service" in rendered
    assert repo_payload_calls and repo_payload_calls[0]["token"] == "yaml-github-token"

    loaded = load_config(config_path)
    assert loaded.agent.default == "codex"
    assert loaded.agent.model == "gpt-5"
    assert loaded.trackers["main"].type == "telegram"
    assert loaded.trackers["main"].options["bot_token"] == "telegram-bot-token"
    assert loaded.trackers["main"].options["state_path"] == "~/.tiller/telegram-state.json"
    assert loaded.trackers["main"].options["allowed_chat_ids"] == ["-1001234567890"]
    assert loaded.trackers["main"].options["allowed_user_ids"] == ["111", "222"]
    assert loaded.github.token == "yaml-github-token"
    assert loaded.projects["backend"].description == "Backend service"


def test_telegram_setup_provider_collects_filters(monkeypatch) -> None:
    provider = TelegramSetupProvider()

    install_questionary_stub(
        monkeypatch,
        text_answers=[
            "-1001234567890, -1009999999999",
            "111, 222",
        ],
        password_answers=["telegram-secret-token"],
        confirm_answers=[True, True],
    )

    tracker = asyncio.run(provider.collect())

    assert tracker == {
        "type": "telegram",
        "bot_token": "telegram-secret-token",
        "state_path": "~/.tiller/telegram-state.json",
        "trigger_status": "new",
        "poll_interval": 5,
        "allowed_chat_ids": ["-1001234567890", "-1009999999999"],
        "allowed_user_ids": ["111", "222"],
    }


def test_run_setup_fails_early_when_project_clone_validation_fails(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "tiller.yaml"

    async def fake_collect_clickup_setup(self) -> dict[str, object]:
        return {
            "type": "clickup",
            "token": "clickup-token",
            "team_id": "team-1",
            "trigger_status": "DEVELOP",
            "poll_interval": 60,
        }

    def fake_validate_project_clone_url(url: str, github_token) -> None:
        raise ValueError(f"Unable to access repository URL '{url}': auth failed")

    monkeypatch.setattr("tiller.setup_clickup.ClickUpSetupProvider.collect", fake_collect_clickup_setup)
    monkeypatch.setattr(
        "tiller.setup.load_harness",
        lambda _path: AgentHarness(
            {
                "codex": AvailableSetupAdapter("codex", "codex"),
            }
        ),
    )
    monkeypatch.setattr("tiller.setup.validate_project_clone_url", fake_validate_project_clone_url)

    install_questionary_stub(
        monkeypatch,
        select_answers=["codex", "clickup", "token", "manual"],
        text_answers=["gpt-5", "frontend", "", "https://github.com/org/frontend"],
        password_answers=["yaml-github-token"],
        confirm_answers=[True, True],
    )

    try:
        asyncio.run(run_setup(str(config_path)))
    except ValueError as exc:
        assert "Unable to access repository URL 'https://github.com/org/frontend'" in str(exc)
    else:
        raise AssertionError("Expected setup to fail when repository validation fails")

    assert not config_path.exists()
