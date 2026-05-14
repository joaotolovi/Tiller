from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import operations as operations_module
from .operations import GitHubClient, SessionOperations


def register_session_subcommands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    tracker_parser = subparsers.add_parser("tracker", help="Interact with the current session tracker task")
    tracker_parser.add_argument("tracker_command", choices=["get-task", "comment", "set-status", "status-options", "download-attachments"])
    tracker_parser.add_argument("value", nargs="?")
    tracker_parser.add_argument("--session", dest="session", default=None)
    tracker_parser.add_argument("--dest", dest="dest", default=None)

    project_parser = subparsers.add_parser("project", help="Interact with session projects")
    project_parser.add_argument("project_command", choices=["list", "status", "use"])
    project_parser.add_argument("name", nargs="?")
    project_parser.add_argument("--reason", dest="reason", default=None)
    project_parser.add_argument("--session", dest="session", default=None)

    github_parser = subparsers.add_parser("github", help="Interact with GitHub for the current session")
    github_subparsers = github_parser.add_subparsers(dest="github_command", required=True)

    github_auth = github_subparsers.add_parser("auth-status", help="Check GitHub authentication")
    github_auth.add_argument("--session", dest="session", default=None)

    github_repo = github_subparsers.add_parser("repo-status", help="Inspect the GitHub repository for a project")
    github_repo.add_argument("repo")
    github_repo.add_argument("--session", dest="session", default=None)

    github_pr_create = github_subparsers.add_parser("create-pr", help="Create a pull request for a provisioned project")
    github_pr_create.add_argument("--repo", required=True)
    github_pr_create.add_argument("--title", required=True)
    github_pr_create.add_argument("--body", dest="body", default=None)
    github_pr_create.add_argument("--body-file", dest="body_file", default=None)
    github_pr_create.add_argument("--base", dest="base", default=None)
    github_pr_create.add_argument("--head", dest="head", default=None)
    github_pr_create.add_argument("--session", dest="session", default=None)

    github_pr_view = github_subparsers.add_parser("pr-view", help="View a pull request for a project")
    github_pr_view.add_argument("--repo", required=True)
    github_pr_view.add_argument("--number", required=True, type=int)
    github_pr_view.add_argument("--session", dest="session", default=None)

    session_parser = subparsers.add_parser("session", help="Inspect current session state")
    session_parser.add_argument("session_command", choices=["status", "paths"])
    session_parser.add_argument("--session", dest="session", default=None)


def handle_session_command(args: argparse.Namespace) -> int:
    operations_module.GitHubClient = GitHubClient
    operations = SessionOperations(getattr(args, "session", None))

    if args.command == "tracker":
        return _print_payload(_handle_tracker_command(args, operations))
    if args.command == "project":
        return _print_payload(_handle_project_command(args, operations))
    if args.command == "github":
        return _print_payload(_handle_github_command(args, operations))
    if args.command == "session":
        return _print_payload(_handle_session_info_command(args, operations))
    raise ValueError(f"Unsupported session command: {args.command}")


def _handle_tracker_command(args: argparse.Namespace, operations: SessionOperations):
    if args.tracker_command == "get-task":
        return operations.tracker_get_task()

    if args.tracker_command == "comment":
        if not args.value:
            raise ValueError("tracker comment requires a message")
        return operations.tracker_comment(args.value)

    if args.tracker_command == "set-status":
        if not args.value:
            raise ValueError("tracker set-status requires a status")
        return operations.tracker_set_status(args.value)

    if args.tracker_command == "status-options":
        return operations.tracker_status_options()

    if args.tracker_command == "download-attachments":
        return operations.tracker_download_attachments(args.dest)

    raise ValueError(f"Unsupported tracker command: {args.tracker_command}")


def _handle_project_command(args: argparse.Namespace, operations: SessionOperations):
    if args.project_command == "list":
        return operations.project_list()

    if args.project_command == "status":
        return operations.project_status()

    if args.project_command == "use":
        if not args.name:
            raise ValueError("project use requires a project name")
        return operations.project_use(args.name, args.reason)

    raise ValueError(f"Unsupported project command: {args.project_command}")


def _handle_github_command(args: argparse.Namespace, operations: SessionOperations):
    if args.github_command == "auth-status":
        return operations.github_auth_status()

    if args.github_command == "repo-status":
        return operations.github_repo_status(args.repo)

    if args.github_command == "create-pr":
        body = _read_pr_body(args.body, args.body_file)
        return operations.github_create_pr(
            repo_name=args.repo,
            title=args.title,
            body=body,
            base=args.base,
            head=args.head,
        )

    if args.github_command == "pr-view":
        return operations.github_pr_view(repo_name=args.repo, number=args.number)

    raise ValueError(f"Unsupported github command: {args.github_command}")


def _handle_session_info_command(args: argparse.Namespace, operations: SessionOperations):
    if args.session_command == "status":
        return operations.session_status()

    if args.session_command == "paths":
        return operations.session_paths()

    raise ValueError(f"Unsupported session command: {args.session_command}")


def _read_pr_body(body: str | None, body_file: str | None) -> str:
    if body and body_file:
        raise ValueError("Use either --body or --body-file, not both")
    if body_file:
        return Path(body_file).expanduser().resolve().read_text(encoding="utf-8")
    if body is not None:
        return body
    return ""


def _print_payload(payload: object) -> int:
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0
