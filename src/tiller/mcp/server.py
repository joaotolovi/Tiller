from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..operations import (
    SessionOperations,
    tracker_comment_async,
    tracker_download_attachments_async,
    tracker_get_task_async,
    tracker_set_status_async,
    tracker_status_options_async,
)

logger = logging.getLogger(__name__)


def _session_record(session_root: Path) -> dict[str, Any]:
    return json.loads((session_root / "session.json").read_text(encoding="utf-8"))


def _write_session_record(session_root: Path, record: dict[str, Any]) -> None:
    dump_json(session_root / "session.json", record)


def _session_projects(session_root: Path) -> dict[str, dict[str, Any]]:
    return json.loads((session_root / "projects.json").read_text(encoding="utf-8"))


def _log_tool_call(session_root: Path, tracker_task_id: str, tool_name: str, **fields: Any) -> None:
    details = " ".join(f"{key}={value}" for key, value in fields.items())
    suffix = f" {details}" if details else ""
    logger.info("MCP tool call session=%s task_id=%s tool=%s%s", session_root.name, tracker_task_id, tool_name, suffix)


def build_tracker_server(session_root: Path) -> FastMCP:
    session_root = session_root.expanduser().resolve()
    record = _session_record(session_root)
    tracker_task_id = str(record["tracker_task_id"])
    mcp = FastMCP("tiller")

    @mcp.tool()
    async def tracker_get_task() -> str:
        _log_tool_call(session_root, tracker_task_id, "tracker_get_task")
        return json.dumps(await tracker_get_task_async(session_root), indent=2, ensure_ascii=False)

    @mcp.tool()
    async def tracker_update_status(status: str) -> str:
        _log_tool_call(session_root, tracker_task_id, "tracker_update_status", status=status)
        return json.dumps(await tracker_set_status_async(session_root, status), ensure_ascii=False)

    @mcp.tool()
    async def tracker_add_comment(text: str) -> str:
        _log_tool_call(session_root, tracker_task_id, "tracker_add_comment", text_length=len(text))
        return json.dumps(await tracker_comment_async(session_root, text), ensure_ascii=False)

    @mcp.tool()
    async def tracker_download_attachments(dest: str | None = None) -> str:
        target = Path(dest).expanduser().resolve() if dest else session_root / "attachments"
        _log_tool_call(session_root, tracker_task_id, "tracker_download_attachments", dest=target)
        return json.dumps(await tracker_download_attachments_async(session_root, dest), indent=2)

    @mcp.tool()
    async def tracker_status_options() -> str:
        _log_tool_call(session_root, tracker_task_id, "tracker_status_options")
        return json.dumps(await tracker_status_options_async(session_root), indent=2, ensure_ascii=False)

    @mcp.tool()
    async def project_list() -> str:
        _log_tool_call(session_root, tracker_task_id, "project_list")
        return json.dumps(SessionOperations(session_root).project_list(), indent=2, ensure_ascii=False)

    @mcp.tool()
    async def project_status() -> str:
        _log_tool_call(session_root, tracker_task_id, "project_status")
        return json.dumps(SessionOperations(session_root).project_status(), indent=2, ensure_ascii=False)

    @mcp.tool()
    async def project_use(name: str, reason: str | None = None) -> str:
        _log_tool_call(session_root, tracker_task_id, "project_use", name=name)
        return json.dumps(SessionOperations(session_root).project_use(name, reason), indent=2, ensure_ascii=False)

    @mcp.tool()
    async def github_auth_status() -> str:
        _log_tool_call(session_root, tracker_task_id, "github_auth_status")
        return json.dumps(SessionOperations(session_root).github_auth_status(), indent=2, ensure_ascii=False)

    @mcp.tool()
    async def github_repo_status(repo: str) -> str:
        _log_tool_call(session_root, tracker_task_id, "github_repo_status", repo=repo)
        return json.dumps(SessionOperations(session_root).github_repo_status(repo), indent=2, ensure_ascii=False)

    @mcp.tool()
    async def github_create_pr(
        repo: str,
        title: str,
        body: str = "",
        base: str | None = None,
        head: str | None = None,
    ) -> str:
        _log_tool_call(session_root, tracker_task_id, "github_create_pr", repo=repo)
        return json.dumps(
            SessionOperations(session_root).github_create_pr(
                repo_name=repo,
                title=title,
                body=body,
                base=base,
                head=head,
            ),
            indent=2,
            ensure_ascii=False,
        )

    @mcp.tool()
    async def github_pr_view(repo: str, number: int) -> str:
        _log_tool_call(session_root, tracker_task_id, "github_pr_view", repo=repo, number=number)
        return json.dumps(
            SessionOperations(session_root).github_pr_view(repo_name=repo, number=number),
            indent=2,
            ensure_ascii=False,
        )

    @mcp.tool()
    async def session_status() -> str:
        _log_tool_call(session_root, tracker_task_id, "session_status")
        return json.dumps(SessionOperations(session_root).session_status(), indent=2, ensure_ascii=False)

    @mcp.tool()
    async def session_paths() -> str:
        _log_tool_call(session_root, tracker_task_id, "session_paths")
        return json.dumps(SessionOperations(session_root).session_paths(), indent=2, ensure_ascii=False)

    return mcp


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tiller-mcp")
    subparsers = parser.add_subparsers(dest="command", required=True)
    tracker_parser = subparsers.add_parser("tracker")
    tracker_parser.add_argument("--session", required=True)
    args = parser.parse_args(argv)

    if args.command == "tracker":
        server = build_tracker_server(Path(args.session))
        asyncio.run(server.run_stdio_async())
        return 0
    return 1
