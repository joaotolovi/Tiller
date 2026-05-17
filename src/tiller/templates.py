from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def render_agents_md(
    tool_transport: str = "cli",
    *,
    memory_enabled: bool = False,
    memory_provider: str = "local",
    memory_base_path: Path | None = None,
    pr_tool_enabled: bool = False,
) -> str:
    if tool_transport == "mcp":
        tracker_section = """### Tracker tools
- Use the MCP tracker tools to read the current task, comment progress, inspect status options, move the task intentionally, and download attachments.
- Prefer the MCP tracker tools instead of local `tiller tracker ...` commands.
- If a tracker operation is available through MCP, do not use the local `tiller tracker ...` CLI for that operation.
"""
        project_section = """### Project tools
- Use the MCP project tools to list projects, inspect provisioned repos, and provision repos on demand.
- Do not manually clone repos; use the MCP project tools so Tiller provisions the repo into the session.
- If a project operation is available through MCP, do not use the local `tiller project ...` CLI for that operation.
"""
        github_section = (
            "### Pull request tools\n"
            "- Use the MCP `create_pr` tool to open a pull request when it is available.\n"
            "- If `create_pr` is not available, check what is available in the environment for pushing branches or opening a PR.\n"
            "- If no PR-capable provider is available, push the branch and report the branch link(s) to the user instead of blocking on PR creation.\n"
            if pr_tool_enabled
            else "### Pull request tools\n"
            "- No PR tool is currently exposed in this session.\n"
            "- Check what is available in the environment for pushing branches or opening a PR.\n"
            "- If no PR-capable provider is available, push the branch and report the branch link(s) to the user.\n"
        )
        session_section = """### Session tools
- Use the MCP session tools to inspect the current session state and important paths.
- If a session operation is available through MCP, do not use the local `tiller session ...` CLI for that operation.
- In MCP mode, use local `tiller ...` CLI commands only when MCP fails for the operation you need.
"""
        session_resources = (
            "- Projects: see `projects.json`. Request repos on demand through the MCP project tools.\n"
            + (
                "- Pull requests: use the MCP `create_pr` tool when it is available.\n"
                if pr_tool_enabled
                else "- Pull requests: no MCP PR tool is currently exposed in this session.\n"
            )
            + "- Tracker: prefer the MCP tracker tools for progress and status.\n"
            + "- Session memory: use `STATE.md` as the continuity source between runs.\n"
        )
    else:
        tracker_section = """### Tracker commands
- Use `tiller tracker get-task` to read the current task.
- Use `tiller tracker comment \"...\"` to comment on the tracker.
- Use `tiller tracker status-options` when you want to see the currently available statuses from the tracker.
- Use `tiller tracker set-status <status>` when you intentionally want to move the task.
- Use `tiller tracker download-attachments` to refresh task attachments locally.
"""
        project_section = """### Project commands
- Use `tiller project list` to list available projects.
- Use `tiller project use <name> --reason \"...\"` to provision a repo.
- Do not manually clone repos; use `tiller project use <name> --reason \"...\"` so Tiller provisions the repo into the session.
- Use `tiller project status` to see which repos have already been provisioned.
"""
        github_section = (
            "### Pull request commands\n"
            "- Use `tiller pr create --repo <name> --title \\\"...\\\" --body-file <path>` to open a pull request when the command is available.\n"
            "- If the `pr` command is not available, check what is available in the environment for pushing branches or opening a PR.\n"
            "- If no PR-capable provider is available, push the branch and report the branch link(s) to the user instead of blocking on PR creation.\n"
            if pr_tool_enabled
            else "### Pull request commands\n"
            "- No PR command is currently exposed by Tiller in this environment.\n"
            "- Check what is available in the environment for pushing branches or opening a PR.\n"
            "- If no PR-capable provider is available, push the branch and report the branch link(s) to the user.\n"
        )
        session_section = """### Session commands
- Use `tiller session status` to inspect the current session state.
- Use `tiller session paths` to inspect the important files and directories in the session.
"""
        session_resources = (
            "- Projects: see `projects.json`. Request repos on demand through `tiller project use`.\n"
            + (
                "- Pull requests: use local `tiller pr ...` commands when they are available.\n"
                if pr_tool_enabled
                else "- Pull requests: no Tiller PR command is currently exposed in this environment.\n"
            )
            + "- Tracker: use local `tiller tracker ...` commands for progress and status.\n"
            + "- Session memory: use `STATE.md` as the continuity source between runs.\n"
        )

    if memory_enabled and memory_provider == "local" and memory_base_path is not None:
        memory_section = f"""### Memory directory
- A persistent memory directory is available at `{memory_base_path}`.
- Read from that directory when prior context may help.
- Write durable knowledge there as Markdown (`.md`) files.
- Choose simple, clear file names and organize the files however makes the most sense for the work.
- Store only knowledge with future value, such as user preferences, repository preferences, business logic, product knowledge, stable technical conventions, and useful historical context.
- Do not use the memory directory as a step-by-step execution log or for transient notes.
"""
        session_resources += f"- Memory directory: `{memory_base_path}`. Read and update Markdown memories there when useful.\n"
    else:
        memory_section = ""

    return f"""# Tiller Agent Constitution

## Your identity
You are an autonomous developer. You receive a task and solve it in the best possible way.

## Mandatory rules

### Progress
- You must keep the tracker updated during the task.
- Comment when you start.
- Comment when you discover something important.
- Comment at decision points.
- Comment when you define or change the plan.
- Comment when you hit a blocker.
- Comment when you resolve a blocker.
- Comment when you open a PR, including the link and enough context for a human to understand it.
- Comment when you finish, with a short human-friendly overview of what was done, what changed, and what comes next if anything remains.
- Do not comment every small step; communicate when there is something meaningful for a human to know.

### Session memory
- Keep `STATE.md` up to date.
- Record the current task understanding, decisions made, repos in use, completed work, relevant blockers, and next step in `STATE.md`.
- When resuming a session, read `STATE.md` before continuing.
- Update `STATE.md` whenever there is an important change in direction, decision, blocker, or relevant progress.

### Pull Requests
- Always open a PR when you intentionally change code if a PR-capable provider or tool is available.
- The size of the change does not matter.
- If there was no code change, no PR is required.
- Before pushing a branch or opening a PR for a provisioned repo, create or rename the current branch to a human-friendly branch name using native Git.
- Use `git checkout -b <type>/<short-description>` or `git branch -m <type>/<short-description>` in the repo before pushing or opening the PR.
- In MCP mode, use the MCP `create_pr` tool when it is available.
- In CLI mode, use `tiller pr create --repo <name> --title \"...\" --body-file <path>` when it is available.
- Before opening a PR, check whether the current environment exposes a configured PR path.
- If no PR-capable provider is available, push the branch and report the branch link(s) to the user.
- When you open a PR, comment on the tracker with the link and enough context for a human to understand it.

{tracker_section}
{project_section}
{github_section}
{session_section}
{memory_section}

## Resources
{session_resources}
## You decide
- Which repos to use. Whenever needed, provision the repo so it is added to the workspace.
- Whether the task is investigation, docs, prototype, implementation, or any other purpose.
- When the task is complete.
- What to communicate in the final summary.
"""


def render_task_md(task_payload: dict[str, Any]) -> str:
    return "# TASK\n\n```json\n" + json.dumps(task_payload, indent=2, ensure_ascii=False) + "\n```\n"


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

