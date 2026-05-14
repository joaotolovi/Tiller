from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .models import GitHubConfig, ProjectSpec


@dataclass(slots=True)
class GitHubRepoRef:
    owner: str
    name: str


@dataclass(slots=True)
class PullRequestRef:
    number: int
    url: str
    html_url: str
    title: str
    head: str
    base: str
    state: str


class GitHubClient:
    def __init__(self, config: GitHubConfig) -> None:
        self.config = config
        self.token = config.resolve_token()
        if not self.token:
            raise RuntimeError(
                "GitHub token is not configured. Set github.token in tiller.yaml or export GITHUB_API_TOKEN."
            )
        self._client = httpx.Client(
            base_url=self._normalize_base_url(config.url),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "tiller",
            },
            timeout=30.0,
        )

    @staticmethod
    def _normalize_base_url(url: str) -> str:
        normalized = url.rstrip("/")
        if normalized.endswith("/mcp") or "api.githubcopilot.com/mcp" in normalized:
            return "https://api.github.com"
        return normalized

    def close(self) -> None:
        self._client.close()

    def auth_status(self) -> dict[str, object]:
        response = self._client.get("/user")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                raise RuntimeError(
                    "GitHub validation failed: token is invalid or expired. Update github.token in tiller.yaml or export a valid GITHUB_API_TOKEN."
                ) from exc
            raise
        payload = response.json()
        return {
            "authenticated": True,
            "login": payload.get("login"),
            "id": payload.get("id"),
            "name": payload.get("name"),
        }

    def validate(self) -> dict[str, object]:
        return self.auth_status()

    def repo_status(self, repo: GitHubRepoRef) -> dict[str, object]:
        response = self._client.get(f"/repos/{repo.owner}/{repo.name}")
        response.raise_for_status()
        payload = response.json()
        return {
            "owner": repo.owner,
            "name": repo.name,
            "default_branch": payload.get("default_branch"),
            "private": payload.get("private"),
            "html_url": payload.get("html_url"),
        }

    def create_pull_request(
        self,
        *,
        repo: GitHubRepoRef,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> PullRequestRef:
        response = self._client.post(
            f"/repos/{repo.owner}/{repo.name}/pulls",
            json={
                "title": title,
                "body": body,
                "head": head,
                "base": base,
            },
        )
        response.raise_for_status()
        payload = response.json()
        return PullRequestRef(
            number=int(payload["number"]),
            url=payload["url"],
            html_url=payload["html_url"],
            title=payload["title"],
            head=payload["head"]["ref"],
            base=payload["base"]["ref"],
            state=payload["state"],
        )

    def get_pull_request(self, *, repo: GitHubRepoRef, number: int) -> PullRequestRef:
        response = self._client.get(f"/repos/{repo.owner}/{repo.name}/pulls/{number}")
        response.raise_for_status()
        payload = response.json()
        return PullRequestRef(
            number=int(payload["number"]),
            url=payload["url"],
            html_url=payload["html_url"],
            title=payload["title"],
            head=payload["head"]["ref"],
            base=payload["base"]["ref"],
            state=payload["state"],
        )


def parse_github_repo_url(url: str) -> GitHubRepoRef:
    normalized = url.strip()
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    if normalized.startswith("git@github.com:"):
        path = normalized.split(":", 1)[1]
    else:
        parsed = urlparse(normalized)
        path = parsed.path.lstrip("/")
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        raise ValueError(f"Unsupported GitHub repository URL: {url}")
    return GitHubRepoRef(owner=parts[0], name=parts[1])


def repo_ref_from_project(project: ProjectSpec) -> GitHubRepoRef:
    return parse_github_repo_url(project.url)


def read_repo_metadata(repo_path: Path) -> dict[str, object]:
    metadata_path = repo_path / ".tiller-repo.json"
    if not metadata_path.exists():
        raise FileNotFoundError(f"Repository metadata not found at {metadata_path}")
    import json

    return json.loads(metadata_path.read_text(encoding="utf-8"))
