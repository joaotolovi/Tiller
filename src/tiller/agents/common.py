from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..config import dump_json


def mcp_config_path(workspace: Path) -> Path:
    return workspace / ".mcp" / "config.json"


def _server_env(server: dict[str, Any]) -> dict[str, str]:
    return {str(key): str(value) for key, value in server.get("environment", {}).items()}


def _server_headers(server: dict[str, Any]) -> dict[str, str]:
    return {str(key): str(value) for key, value in server.get("headers", {}).items()}


def write_claude_project_mcp(workspace: Path, mcp_config: dict[str, Any]) -> Path:
    path = workspace / ".mcp.json"
    servers: dict[str, Any] = {}
    for name, server in mcp_config.get("servers", {}).items():
        if server.get("transport") == "stdio":
            servers[name] = {
                "command": server["command"],
                "args": server.get("args", []),
                "env": _server_env(server),
            }
        elif server.get("type") == "http":
            entry: dict[str, Any] = {"url": server["url"]}
            headers = _server_headers(server)
            if headers:
                entry["headers"] = headers
            servers[name] = entry
    dump_json(path, {"mcpServers": servers})
    return path


def write_opencode_project_mcp(workspace: Path, mcp_config: dict[str, Any]) -> Path:
    path = workspace / "opencode.json"
    payload = {"$schema": "https://opencode.ai/config.json", "mcp": {}}
    for name, server in mcp_config.get("servers", {}).items():
        if server.get("transport") == "stdio":
            payload["mcp"][name] = {
                "type": "local",
                "command": [server["command"], *server.get("args", [])],
                "environment": _server_env(server),
                "enabled": True,
            }
        elif server.get("type") == "http":
            payload["mcp"][name] = {
                "type": "remote",
                "url": server["url"],
                "headers": _server_headers(server),
                "enabled": True,
            }
    dump_json(path, payload)
    return path


def write_gemini_project_mcp(workspace: Path, mcp_config: dict[str, Any]) -> Path:
    path = workspace / ".gemini" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    servers: dict[str, Any] = {}
    for name, server in mcp_config.get("servers", {}).items():
        if server.get("transport") == "stdio":
            servers[name] = {
                "command": server["command"],
                "args": server.get("args", []),
                "env": _server_env(server),
            }
        elif server.get("type") == "http":
            entry: dict[str, Any] = {"httpUrl": server["url"]}
            headers = _server_headers(server)
            if headers:
                entry["headers"] = headers
            servers[name] = entry
    dump_json(path, {"mcpServers": servers})
    return path


def write_codex_project_mcp(workspace: Path, mcp_config: dict[str, Any]) -> Path:
    path = workspace / ".codex" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    blocks: list[str] = []
    for name, server in mcp_config.get("servers", {}).items():
        safe_name = str(name).replace("-", "_")
        block_lines = [f"[mcp_servers.{safe_name}]"]
        if server.get("transport") == "stdio":
            block_lines.append(f'command = {server["command"]!r}')
            args = ", ".join(repr(str(arg)) for arg in server.get("args", []))
            block_lines.append(f"args = [{args}]")
            env = _server_env(server)
            if env:
                env_parts = ", ".join(f"{key} = {value!r}" for key, value in env.items())
                block_lines.append(f"env = {{ {env_parts} }}")
            block_lines.append("enabled = true")
        elif server.get("type") == "http":
            block_lines.append(f'url = {server["url"]!r}')
            headers = _server_headers(server)
            auth = headers.pop("Authorization", None)
            if auth and auth.startswith("Bearer "):
                block_lines.append('bearer_token_env_var = "GITHUB_API_TOKEN"')
            if headers:
                header_parts = ", ".join(f"{key} = {value!r}" for key, value in headers.items())
                block_lines.append(f"http_headers = {{ {header_parts} }}")
            block_lines.append("enabled = true")
        blocks.append("\n".join(block_lines))
    path.write_text("\n\n".join(blocks) + ("\n" if blocks else ""), encoding="utf-8")
    return path


def write_cursor_project_mcp(workspace: Path, mcp_config: dict[str, Any]) -> Path:
    path = workspace / ".cursor" / "mcp.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    servers = {}
    for name, server in mcp_config.get("servers", {}).items():
        if server.get("transport") == "stdio":
            servers[name] = {
                "command": server["command"],
                "args": server.get("args", []),
                "env": _server_env(server),
            }
    dump_json(path, {"mcpServers": servers})
    return path


def write_kilo_project_mcp(workspace: Path, mcp_config: dict[str, Any]) -> Path:
    return write_opencode_project_mcp(workspace, mcp_config)


def write_continue_project_mcp(workspace: Path, mcp_config: dict[str, Any]) -> Path:
    path = workspace / ".continue" / "mcpServers" / "tiller.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    servers: list[dict[str, Any]] = []
    for name, server in mcp_config.get("servers", {}).items():
        entry: dict[str, Any] = {"name": name}
        if server.get("transport") == "stdio":
            entry.update({
                "command": server["command"],
                "args": server.get("args", []),
            })
            env = _server_env(server)
            if env:
                entry["env"] = env
        elif server.get("type") == "http":
            entry["url"] = server["url"]
            headers = _server_headers(server)
            if headers:
                entry["headers"] = headers
        else:
            continue
        servers.append(entry)
    payload = {
        "name": "Tiller MCP Servers",
        "version": "0.0.1",
        "schema": "v1",
        "mcpServers": servers,
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def write_kiro_project_mcp(workspace: Path, mcp_config: dict[str, Any]) -> Path:
    path = workspace / ".kiro" / "settings" / "mcp.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    servers: dict[str, Any] = {}
    for name, server in mcp_config.get("servers", {}).items():
        if server.get("transport") == "stdio":
            servers[name] = {
                "command": server["command"],
                "args": server.get("args", []),
                "env": _server_env(server),
            }
        elif server.get("type") == "http":
            entry: dict[str, Any] = {"url": server["url"]}
            headers = _server_headers(server)
            if headers:
                entry["headers"] = headers
            servers[name] = entry
    dump_json(path, {"mcpServers": servers})
    return path


def write_qwen_project_mcp(workspace: Path, mcp_config: dict[str, Any]) -> Path:
    path = workspace / ".qwen" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    servers: dict[str, Any] = {}
    for name, server in mcp_config.get("servers", {}).items():
        if server.get("transport") == "stdio":
            servers[name] = {
                "command": server["command"],
                "args": server.get("args", []),
                "env": _server_env(server),
            }
        elif server.get("type") == "http":
            entry: dict[str, Any] = {"httpUrl": server["url"]}
            headers = _server_headers(server)
            if headers:
                entry["headers"] = headers
            servers[name] = entry
    dump_json(path, {"mcpServers": servers})
    return path


def write_junie_project_mcp(workspace: Path, mcp_config: dict[str, Any]) -> Path:
    path = workspace / ".junie" / "mcp" / "mcp.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    servers: dict[str, Any] = {}
    for name, server in mcp_config.get("servers", {}).items():
        if server.get("transport") == "stdio":
            servers[name] = {
                "command": server["command"],
                "args": server.get("args", []),
                "env": _server_env(server),
            }
        elif server.get("type") == "http":
            entry: dict[str, Any] = {"url": server["url"]}
            headers = _server_headers(server)
            if headers:
                entry["headers"] = headers
            servers[name] = entry
    dump_json(path, {"mcpServers": servers})
    return path


def write_gptme_project_mcp(workspace: Path, mcp_config: dict[str, Any]) -> Path:
    path = workspace / "gptme.toml"
    blocks = ["[mcp]", "enabled = true", "auto_start = true"]
    for name, server in mcp_config.get("servers", {}).items():
        blocks.append("")
        blocks.append("[[mcp.servers]]")
        blocks.append(f"name = {name!r}")
        blocks.append("enabled = true")
        if server.get("transport") == "stdio":
            blocks.append(f"command = {server['command']!r}")
            args = ", ".join(repr(str(arg)) for arg in server.get("args", []))
            blocks.append(f"args = [{args}]")
            env = _server_env(server)
            if env:
                env_parts = ", ".join(f"{key} = {value!r}" for key, value in env.items())
                blocks.append(f"env = {{ {env_parts} }}")
        elif server.get("type") == "http":
            blocks.append(f"url = {server['url']!r}")
            headers = _server_headers(server)
            if headers:
                header_parts = ", ".join(f"{key} = {value!r}" for key, value in headers.items())
                blocks.append(f"headers = {{ {header_parts} }}")
    path.write_text("\n".join(blocks) + "\n", encoding="utf-8")
    return path


def write_pi_project_mcp(workspace: Path, mcp_config: dict[str, Any]) -> Path:
    path = workspace / ".pi" / "mcp.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    servers: dict[str, Any] = {}
    for name, server in mcp_config.get("servers", {}).items():
        if server.get("transport") == "stdio":
            entry: dict[str, Any] = {
                "command": server["command"],
                "args": server.get("args", []),
            }
            env = _server_env(server)
            if env:
                entry["env"] = env
            servers[name] = entry
        elif server.get("type") == "http":
            entry = {"url": server["url"]}
            headers = _server_headers(server)
            if headers:
                entry["headers"] = headers
            servers[name] = entry
    dump_json(path, {"mcpServers": servers})
    return path


def write_auggie_project_mcp(workspace: Path, mcp_config: dict[str, Any]) -> Path:
    path = workspace / ".augment" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    servers: dict[str, Any] = {}
    for name, server in mcp_config.get("servers", {}).items():
        if server.get("transport") == "stdio":
            servers[name] = {
                "command": server["command"],
                "args": server.get("args", []),
                "env": _server_env(server),
            }
        elif server.get("type") == "http":
            entry: dict[str, Any] = {"url": server["url"]}
            headers = _server_headers(server)
            if headers:
                entry["headers"] = headers
            servers[name] = entry
    dump_json(path, {"mcpServers": servers})
    return path


def write_kimi_project_mcp(workspace: Path, mcp_config: dict[str, Any]) -> Path:
    path = workspace / "mcp.json"
    servers: dict[str, Any] = {}
    for name, server in mcp_config.get("servers", {}).items():
        if server.get("transport") == "stdio":
            entry: dict[str, Any] = {
                "command": server["command"],
                "args": server.get("args", []),
            }
            env = _server_env(server)
            if env:
                entry["env"] = env
            servers[name] = entry
        elif server.get("type") == "http":
            entry = {"url": server["url"]}
            headers = _server_headers(server)
            if headers:
                entry["headers"] = headers
            servers[name] = entry
    dump_json(path, {"mcpServers": servers})
    return path


def write_copilot_project_mcp(workspace: Path, mcp_config: dict[str, Any]) -> Path:
    path = workspace / ".copilot" / "mcp-config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    servers: dict[str, Any] = {}
    for name, server in mcp_config.get("servers", {}).items():
        if server.get("transport") == "stdio":
            servers[name] = {
                "type": "local",
                "command": server["command"],
                "args": server.get("args", []),
                "env": _server_env(server),
                "tools": ["*"],
            }
        elif server.get("type") == "http":
            entry: dict[str, Any] = {
                "type": "http",
                "url": server["url"],
                "tools": ["*"],
            }
            headers = _server_headers(server)
            if headers:
                entry["headers"] = headers
            servers[name] = entry
    dump_json(path, {"mcpServers": servers})
    return path


def write_droid_project_mcp(workspace: Path, mcp_config: dict[str, Any]) -> Path:
    path = workspace / ".factory" / "mcp.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    servers: dict[str, Any] = {}
    for name, server in mcp_config.get("servers", {}).items():
        if server.get("transport") == "stdio":
            servers[name] = {
                "type": "stdio",
                "command": server["command"],
                "args": server.get("args", []),
                "env": _server_env(server),
                "disabled": False,
            }
        elif server.get("type") == "http":
            servers[name] = {
                "type": "http",
                "url": server["url"],
                "disabled": False,
                "disabledTools": [],
            }
    dump_json(path, {"mcpServers": servers})
    return path


def write_native_mcp_project_files(workspace: Path, mcp_config: dict[str, Any]) -> None:
    write_claude_project_mcp(workspace, mcp_config)
    write_opencode_project_mcp(workspace, mcp_config)
    write_gemini_project_mcp(workspace, mcp_config)
    write_codex_project_mcp(workspace, mcp_config)
    write_cursor_project_mcp(workspace, mcp_config)
    write_kilo_project_mcp(workspace, mcp_config)
    write_continue_project_mcp(workspace, mcp_config)
    write_kiro_project_mcp(workspace, mcp_config)
    write_qwen_project_mcp(workspace, mcp_config)
    write_junie_project_mcp(workspace, mcp_config)
    write_gptme_project_mcp(workspace, mcp_config)
    write_pi_project_mcp(workspace, mcp_config)
    write_auggie_project_mcp(workspace, mcp_config)
    write_kimi_project_mcp(workspace, mcp_config)
    write_copilot_project_mcp(workspace, mcp_config)
    write_droid_project_mcp(workspace, mcp_config)
