from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from .models import ProjectSpec, SessionPaths


def discover_local_projects(mirrors_dir: Path | None) -> dict[str, ProjectSpec]:
    if mirrors_dir is None:
        return {}
    root = Path(mirrors_dir).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return {}

    projects: dict[str, ProjectSpec] = {}
    for child in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir() or not (child / ".git").exists():
            continue
        name = child.name
        url = _read_git_remote_url(child) or child.as_uri()
        default_branch = _read_local_default_branch(child) or "main"
        projects[name] = ProjectSpec(
            name=name,
            url=url,
            default_branch=default_branch,
            source="local_directory",
            source_path=str(child),
        )
    return projects


class RepoSeedManager:
    def __init__(self, storage_root: Path, clone_token: str | None = None, mirrors_dir: Path | None = None) -> None:
        self.storage_root = storage_root
        self.clone_token = clone_token
        self.mirrors_dir = mirrors_dir or (self.storage_root / "repo-mirrors")
        self.mirrors_dir.mkdir(parents=True, exist_ok=True)

    def provision(self, *, paths: SessionPaths, project: ProjectSpec, branch_name: str | None = None) -> Path:
        seed_path = self._ensure_seed(project)
        repo_path = paths.repos_dir / project.name
        if repo_path.exists():
            return repo_path
        repo_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(seed_path, repo_path)
        metadata = {
            "name": project.name,
            "url": project.url,
            "default_branch": project.default_branch,
            "branch": branch_name,
            "seed": str(seed_path),
            "repo": str(repo_path),
        }
        (repo_path / ".tiller-repo.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        return repo_path

    def cleanup(self, repo_path: Path) -> None:
        shutil.rmtree(repo_path, ignore_errors=True)

    def _ensure_seed(self, project: ProjectSpec) -> Path:
        local_source = self._local_source_path(project)
        if local_source is not None:
            return local_source

        seed_path = self.mirrors_dir / project.name
        if seed_path.exists():
            self._git(["git", "fetch", "origin", "--prune"], cwd=seed_path)
            self._git(["git", "checkout", project.default_branch], cwd=seed_path)
            self._git(["git", "reset", "--hard", f"origin/{project.default_branch}"], cwd=seed_path)
            return seed_path
        self._clone_project(project, seed_path)
        self._git(["git", "checkout", project.default_branch], cwd=seed_path)
        self._git(["git", "reset", "--hard", f"origin/{project.default_branch}"], cwd=seed_path)
        return seed_path

    def _local_source_path(self, project: ProjectSpec) -> Path | None:
        if project.source != "local_directory" or not project.source_path:
            return None
        path = Path(project.source_path).expanduser().resolve()
        if not path.exists() or not (path / ".git").exists():
            raise RuntimeError(f"Local repository source is unavailable: {path}")
        return path

    def _clone_project(self, project: ProjectSpec, seed_path: Path) -> None:
        last_error: RuntimeError | None = None
        for clone_url in self._clone_urls(project.url):
            try:
                self._git(["git", "clone", clone_url, str(seed_path)], cwd=self.mirrors_dir)
                return
            except RuntimeError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Unable to clone repository: {project.url}")

    def validate_project_access(self, url: str) -> None:
        self._first_accessible_clone_url(url)

    def detect_project_default_branch(self, url: str) -> str:
        clone_url = self._first_accessible_clone_url(url)
        output = self._git_output(["git", "ls-remote", "--symref", clone_url, "HEAD"], cwd=self.mirrors_dir)
        for line in output.splitlines():
            if line.startswith("ref:") and "\tHEAD" in line:
                ref = line.split()[1]
                prefix = "refs/heads/"
                if ref.startswith(prefix):
                    branch = ref[len(prefix) :].strip()
                    if branch:
                        return branch
        raise RuntimeError(f"Unable to detect default branch for repository: {url}")

    def _first_accessible_clone_url(self, url: str) -> str:
        last_error: RuntimeError | None = None
        for clone_url in self._clone_urls(url):
            try:
                self._git(["git", "ls-remote", clone_url, "HEAD"], cwd=self.mirrors_dir)
                return clone_url
            except RuntimeError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Unable to access repository: {url}")

    def _clone_urls(self, url: str) -> list[str]:
        clone_urls = [url]
        ssh_url = self._http_to_ssh_url(url)
        if ssh_url and ssh_url not in clone_urls:
            clone_urls.append(ssh_url)
        token_url = self._http_with_token_url(url)
        if token_url and token_url not in clone_urls:
            clone_urls.append(token_url)
        return clone_urls

    @staticmethod
    def _http_to_ssh_url(url: str) -> str | None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return None
        if not parsed.netloc or not parsed.path:
            return None
        path = parsed.path.lstrip("/")
        if not path:
            return None
        return f"git@{parsed.netloc}:{path}"

    def _http_with_token_url(self, url: str) -> str | None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return None
        token = self.clone_token
        if not token or not parsed.netloc:
            return None
        return f"{parsed.scheme}://{token}@{parsed.netloc}{parsed.path}"

    def _git(self, command: list[str], *, cwd: Path) -> None:
        self._git_output(command, cwd=cwd)

    def _git_output(self, command: list[str], *, cwd: Path) -> str:
        completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
        if completed.returncode != 0:
            raise RuntimeError(
                f"Git command failed ({' '.join(command)}): {completed.stderr.strip() or completed.stdout.strip()}"
            )
        return completed.stdout


def _read_git_remote_url(repo_path: Path) -> str | None:
    completed = subprocess.run(
        ["git", "config", "--get", "remote.origin.url"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value or None


def _read_local_default_branch(repo_path: Path) -> str | None:
    completed = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value or None
