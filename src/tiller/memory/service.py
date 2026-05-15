from __future__ import annotations

from dataclasses import asdict
import os
from typing import Any

from ..models import TillerConfig
from ..runtime import SessionContext
from .provider import HindsightMemoryProvider, LangMemMemoryProvider, LocalMemoryProvider, MemoryProvider


class SessionMemoryService:
    def __init__(self, context: SessionContext, config: TillerConfig, provider: MemoryProvider) -> None:
        self.context = context
        self.config = config
        self.provider = provider

    @classmethod
    def from_context(cls, context: SessionContext, config: TillerConfig) -> "SessionMemoryService":
        provider_name = config.memory.provider
        if provider_name == "local":
            provider = LocalMemoryProvider(config.memory.base_path)
            return cls(context, config, provider)
        if provider_name == "hindsight":
            api_key = config.memory.llm_api_key or os.environ.get(config.memory.llm_api_key_env)
            if not api_key:
                raise ValueError(f"Missing memory LLM API key. Set memory.llm_api_key or env {config.memory.llm_api_key_env}")
            provider = HindsightMemoryProvider(
                llm_provider=config.memory.llm_provider,
                llm_model=config.memory.llm_model,
                llm_api_key=api_key,
                base_path=config.memory.base_path,
            )
            return cls(context, config, provider)
        if provider_name == "langmem":
            api_key = config.memory.llm_api_key or os.environ.get(config.memory.llm_api_key_env)
            if not api_key:
                raise ValueError(f"Missing memory LLM API key. Set memory.llm_api_key or env {config.memory.llm_api_key_env}")
            provider = LangMemMemoryProvider(
                llm_provider=config.memory.llm_provider,
                llm_model=config.memory.llm_model,
                llm_api_key=api_key,
                llm_api_key_env=config.memory.llm_api_key_env,
                base_path=config.memory.base_path,
            )
            return cls(context, config, provider)
        raise ValueError(f"Unsupported memory provider: {provider_name}")

    def enabled(self) -> bool:
        return self.config.memory.enabled

    def retain(self, scope: str, content: str, context: str | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        bank_id = self._bank_id_for_scope(scope)
        entry = self.provider.retain(
            bank_id=bank_id,
            content=content,
            context=context,
            metadata=self._metadata(metadata, scope),
        )
        return {"scope": scope, **asdict(entry)}

    def recall(self, query: str, limit: int = 5, scope: str | None = None) -> dict[str, Any]:
        bank_ids = self._bank_ids_for_recall(scope)
        if len(bank_ids) == 1:
            result = self.provider.recall(bank_id=bank_ids[0], query=query, limit=limit)
            return {
                "scope": self._scope_from_bank_id(result.bank_id),
                "bank_id": result.bank_id,
                "query": result.query,
                "entries": [asdict(entry) for entry in result.entries],
            }

        entries: list[dict[str, Any]] = []
        for bank_id in bank_ids:
            result = self.provider.recall(bank_id=bank_id, query=query, limit=limit)
            for entry in result.entries:
                item = asdict(entry)
                item["scope"] = self._scope_from_bank_id(entry.bank_id)
                entries.append(item)
        return {
            "scope": None,
            "bank_ids": bank_ids,
            "query": query,
            "entries": entries[: max(1, limit)],
        }

    def _bank_id_for_scope(self, scope: str) -> str:
        normalized = scope.strip()
        if not normalized:
            raise ValueError("memory scope cannot be empty")
        if normalized in {"user", "domain", "history"}:
            return normalized
        if normalized.startswith("project:"):
            project_name = normalized.split(":", 1)[1].strip()
            if not project_name:
                raise ValueError("project scope must be in the format 'project:<project_name>'")
            return f"project:{project_name}"
        raise ValueError("memory scope must be one of: user, domain, history, project:<project_name>")

    def _bank_ids_for_recall(self, scope: str | None) -> list[str]:
        if scope:
            return [self._bank_id_for_scope(scope)]
        bank_ids = ["history", "domain", "user"]
        project_name = self.config.memory.project or self._default_project_name()
        if project_name:
            bank_ids.insert(0, f"project:{project_name}")
        return bank_ids

    def _scope_from_bank_id(self, bank_id: str) -> str:
        return bank_id

    def _default_project_name(self) -> str | None:
        if self.context.record.provisioned_repos:
            return self.context.record.provisioned_repos[0]
        if self.config.memory.project:
            return self.config.memory.project
        if self.config.projects:
            return next(iter(self.config.projects))
        return None

    def _metadata(self, metadata: dict[str, Any] | None, scope: str) -> dict[str, Any]:
        payload = {
            "task_id": self.context.record.tracker_task_id,
            "session_id": self.context.record.internal_task_id,
            "agent_name": self.context.record.agent_name,
            "scope": scope,
        }
        if metadata:
            payload.update(metadata)
        return payload
