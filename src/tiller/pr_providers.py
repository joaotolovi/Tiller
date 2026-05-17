from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .github import GitHubClient, PullRequestRef, read_repo_metadata, repo_ref_from_project
from .models import GitHubConfig, ProjectSpec, TillerConfig


class PullRequestProvider(Protocol):
    def create_pull_request(
        self,
        *,
        project: ProjectSpec,
        repo_path: Path,
        title: str,
        body: str,
        base: str | None = None,
        head: str | None = None,
    ) -> PullRequestRef:
        ...


@dataclass(slots=True)
class GitHubPullRequestProvider:
    config: GitHubConfig

    def enabled(self) -> bool:
        return self.config.enabled and self.config.resolve_token() is not None

    def create_pull_request(
        self,
        *,
        project: ProjectSpec,
        repo_path: Path,
        title: str,
        body: str,
        base: str | None = None,
        head: str | None = None,
    ) -> PullRequestRef:
        client = GitHubClient(self.config)
        try:
            repo_ref = repo_ref_from_project(project)
            metadata = read_repo_metadata(repo_path)
            resolved_head = head or str(metadata.get("branch") or "")
            if not resolved_head:
                raise ValueError(f"No branch metadata found for project: {project.name}")
            return client.create_pull_request(
                repo=repo_ref,
                title=title,
                body=body,
                head=resolved_head,
                base=base or project.default_branch,
            )
        finally:
            client.close()


@dataclass(slots=True)
class PullRequestProviderBinding:
    name: str
    provider: PullRequestProvider


def get_pull_request_provider(config: TillerConfig) -> PullRequestProviderBinding | None:
    github_provider = GitHubPullRequestProvider(config.github)
    if github_provider.enabled():
        return PullRequestProviderBinding(name="github", provider=github_provider)
    return None
