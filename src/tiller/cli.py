from __future__ import annotations

import argparse
import asyncio
import json
import logging
from dataclasses import asdict

import httpx

from .agents import load_harness
from .commands import handle_session_command, register_session_subcommands
from .config import load_config
from .github import GitHubClient
from .service import TillerService
from .setup import run_setup
from .trackers import build_tracker_adapter

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tiller")
    parser.add_argument("--config", default="tiller.yaml", help="Path to tiller.yaml")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Logging level")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run the tracker watcher service")
    run_parser.add_argument("--config", dest="config", default="tiller.yaml", help="Path to tiller.yaml")
    run_parser.add_argument("--log-level", dest="log_level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Logging level")

    discover_parser = subparsers.add_parser("discover-agents", help="List available agent adapters")
    discover_parser.add_argument("--config", dest="config", default="tiller.yaml", help="Path to tiller.yaml")
    discover_parser.add_argument("--log-level", dest="log_level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Logging level")

    setup_parser = subparsers.add_parser("setup", help="Run interactive setup")
    setup_parser.add_argument("--config", dest="config", default="tiller.yaml", help="Path to tiller.yaml")
    setup_parser.add_argument("--log-level", dest="log_level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Logging level")

    register_session_subcommands(subparsers)
    return parser


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


async def _validate_startup(config_path: str) -> None:
    config = load_config(config_path)
    tracker = build_tracker_adapter(config.tracker.type, **config.tracker.options)
    logger.info("Running startup validation")
    try:
        await tracker.validate()
    except httpx.HTTPError as exc:
        logger.error("Tracker validation failed: %s", exc)
        raise SystemExit(1)
    logger.info("Tracker validation ok tracker=%s", config.tracker.type)
    if config.github.enabled:
        client = GitHubClient(config.github)
        try:
            auth = await asyncio.to_thread(client.validate)
        finally:
            client.close()
        logger.info("GitHub validation ok login=%s", auth.get("login") or "unknown")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.log_level)

    if args.command == "setup":
        return asyncio.run(run_setup(args.config))

    if args.command in {"tracker", "project", "github", "session", "memory"}:
        return handle_session_command(args)

    config = load_config(args.config)
    harness = load_harness(config.agent.adapters_path)

    if args.command == "discover-agents":
        print(json.dumps([asdict(agent) for agent in harness.discover()], indent=2, ensure_ascii=False))
        return 0

    asyncio.run(_validate_startup(args.config))
    tracker = build_tracker_adapter(config.tracker.type, **config.tracker.options)
    service = TillerService(config=config, tracker=tracker, harness=harness)
    try:
        asyncio.run(service.run_forever())
    except KeyboardInterrupt:
        logger.info("Tiller interrupted by user")
        return 130
    return 0
