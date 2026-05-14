from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..config import dump_json
from ..models import TillerConfig


def build_tracker_mcp_server(path: Path) -> dict[str, Any]:
    return {
        "name": "tracker",
        "transport": "stdio",
        "command": "tiller-mcp",
        "args": ["tracker", "--session", str(path.parent.parent)],
    }


def build_github_mcp_server(config: TillerConfig) -> dict[str, Any] | None:
    if not config.github.enabled:
        return None

    token = config.github.token or os.environ.get(config.github.token_env)
    if not token:
        return None

    return {
        "name": "github",
        "type": "http",
        "url": config.github.url,
        "headers": {
            "Authorization": f"Bearer {token}",
        },
    }


def write_mcp_config(path: Path, config: TillerConfig) -> dict[str, Any]:
    servers = {
        "tracker": build_tracker_mcp_server(path),
    }
    github_server = build_github_mcp_server(config)
    if github_server is not None:
        servers["github"] = github_server

    payload = {
        "servers": servers,
        "tracker": {
            "type": config.tracker.type,
            "options": config.tracker.options,
        },
        "session": {
            "root": str(path.parent.parent),
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    dump_json(path, payload)
    return payload
