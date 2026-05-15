from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
from typing import Any


@dataclass(slots=True)
class MemoryEntry:
    id: str
    bank_id: str
    content: str
    context: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(slots=True)
class MemoryRecallResult:
    entries: list[MemoryEntry]
    query: str
    bank_id: str


class MemoryProvider:
    def retain(self, *, bank_id: str, content: str, context: str | None = None, metadata: dict[str, Any] | None = None) -> MemoryEntry:
        raise NotImplementedError

    def recall(self, *, bank_id: str, query: str, limit: int = 5) -> MemoryRecallResult:
        raise NotImplementedError


class LangMemMemoryProvider(MemoryProvider):
    def __init__(
        self,
        *,
        llm_provider: str,
        llm_model: str | None,
        llm_api_key: str,
        llm_api_key_env: str,
        base_path: Path,
    ) -> None:
        try:
            from langmem import create_memory_manager
        except ImportError as exc:
            raise ImportError("langmem is required for memory.provider='langmem'") from exc

        if not llm_model:
            raise ValueError("memory.llm_model is required for memory.provider='langmem'")

        os.environ[llm_api_key_env] = llm_api_key
        self._manager = create_memory_manager(self._model_name(llm_provider, llm_model))
        self._backend = LocalMemoryProvider(base_path)

    def retain(self, *, bank_id: str, content: str, context: str | None = None, metadata: dict[str, Any] | None = None) -> MemoryEntry:
        content = content.strip()
        if not content:
            raise ValueError("memory content cannot be empty")

        messages = []
        if context:
            messages.append({"role": "system", "content": context.strip()})
        messages.append({"role": "user", "content": content})

        response = self._manager.invoke({"messages": messages})
        extracted = self._extract_memory_texts(response)
        stored_entries: list[MemoryEntry] = []
        for item in extracted or [content]:
            stored_entries.append(
                self._backend.retain(
                    bank_id=bank_id,
                    content=item,
                    context=context,
                    metadata=metadata,
                )
            )
        return stored_entries[0]

    def recall(self, *, bank_id: str, query: str, limit: int = 5) -> MemoryRecallResult:
        return self._backend.recall(bank_id=bank_id, query=query, limit=limit)

    def _extract_memory_texts(self, response: Any) -> list[str]:
        if isinstance(response, list):
            values = response
        else:
            values = [response]
        extracted: list[str] = []
        for item in values:
            text = self._extract_text(item)
            if text:
                extracted.append(text)
        return extracted

    def _extract_text(self, item: Any) -> str | None:
        if isinstance(item, str):
            return item.strip() or None
        content = getattr(item, "content", None)
        if content is not None:
            if isinstance(content, str):
                return content.strip() or None
            nested = getattr(content, "content", None)
            if isinstance(nested, str):
                return nested.strip() or None
            if isinstance(content, dict):
                value = content.get("content") or content.get("text")
                if isinstance(value, str):
                    return value.strip() or None
        if isinstance(item, dict):
            value = item.get("content") or item.get("text")
            if isinstance(value, str):
                return value.strip() or None
        return None

    def _model_name(self, llm_provider: str, llm_model: str) -> str:
        if ":" in llm_model:
            return llm_model
        return f"{llm_provider}:{llm_model}"


class HindsightMemoryProvider(MemoryProvider):
    def __init__(self, *, llm_provider: str, llm_model: str | None, llm_api_key: str, base_path: Path | None = None) -> None:
        try:
            from hindsight import HindsightClient, HindsightServer
        except ImportError as exc:
            raise ImportError("hindsight-all is required for memory.provider='hindsight'") from exc

        server_kwargs: dict[str, Any] = {
            "llm_provider": llm_provider,
            "llm_api_key": llm_api_key,
        }
        if llm_model:
            server_kwargs["llm_model"] = llm_model
        if base_path is not None:
            base_path = base_path.expanduser().resolve()
            base_path.mkdir(parents=True, exist_ok=True)
            server_kwargs["db_path"] = str(base_path)

        self._server = HindsightServer(**server_kwargs)
        self._server.__enter__()
        self._client = HindsightClient(base_url=self._server.url)

    def retain(self, *, bank_id: str, content: str, context: str | None = None, metadata: dict[str, Any] | None = None) -> MemoryEntry:
        content = content.strip()
        if not content:
            raise ValueError("memory content cannot be empty")
        entry = MemoryEntry(
            id=f"{bank_id}:{datetime.now(UTC).timestamp()}",
            bank_id=bank_id,
            content=content,
            context=context.strip() if context else None,
            metadata=metadata or {},
        )
        payload: dict[str, Any] = {"bank_id": bank_id, "content": content}
        if entry.context:
            payload["context"] = entry.context
        if entry.metadata:
            payload["metadata"] = entry.metadata
        self._client.retain(**payload)
        return entry

    def recall(self, *, bank_id: str, query: str, limit: int = 5) -> MemoryRecallResult:
        response = self._client.recall(bank_id=bank_id, query=query)
        entries = self._normalize_entries(bank_id, response, limit)
        return MemoryRecallResult(entries=entries, query=query, bank_id=bank_id)

    def close(self) -> None:
        self._server.__exit__(None, None, None)

    def _normalize_entries(self, bank_id: str, response: Any, limit: int) -> list[MemoryEntry]:
        if isinstance(response, dict):
            candidates = response.get("results") or response.get("entries") or response.get("memories") or []
        elif isinstance(response, list):
            candidates = response
        else:
            candidates = []

        entries: list[MemoryEntry] = []
        for index, item in enumerate(candidates[: max(1, limit)], start=1):
            if isinstance(item, str):
                entries.append(MemoryEntry(id=f"{bank_id}:{index}", bank_id=bank_id, content=item))
                continue
            if not isinstance(item, dict):
                entries.append(MemoryEntry(id=f"{bank_id}:{index}", bank_id=bank_id, content=str(item)))
                continue
            content = item.get("content") or item.get("text") or item.get("memory") or json.dumps(item, ensure_ascii=False)
            entries.append(
                MemoryEntry(
                    id=str(item.get("id") or f"{bank_id}:{index}"),
                    bank_id=bank_id,
                    content=content,
                    context=item.get("context"),
                    metadata=dict(item.get("metadata", {})) if isinstance(item.get("metadata"), dict) else {},
                    created_at=item.get("created_at") or datetime.now(UTC).isoformat(),
                )
            )
        return entries

class LocalMemoryProvider(MemoryProvider):
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def retain(self, *, bank_id: str, content: str, context: str | None = None, metadata: dict[str, Any] | None = None) -> MemoryEntry:
        entry = MemoryEntry(
            bank_id=bank_id,
            content=content.strip(),
            context=context.strip() if context else None,
            metadata=metadata or {},
            id=self._next_id(bank_id),
        )
        if not entry.content:
            raise ValueError("memory content cannot be empty")
        payload = {
            "id": entry.id,
            "bank_id": entry.bank_id,
            "content": entry.content,
            "context": entry.context,
            "metadata": entry.metadata,
            "created_at": entry.created_at,
        }
        with self._bank_path(bank_id).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return entry

    def recall(self, *, bank_id: str, query: str, limit: int = 5) -> MemoryRecallResult:
        entries = self._load_bank(bank_id)
        ranked = sorted(entries, key=lambda entry: self._score(query, entry), reverse=True)
        filtered = [entry for entry in ranked if self._score(query, entry) > 0]
        selected = filtered[: max(1, limit)] if filtered else []
        return MemoryRecallResult(entries=selected, query=query, bank_id=bank_id)

    def _bank_path(self, bank_id: str) -> Path:
        safe_bank = re.sub(r"[^a-zA-Z0-9_.-]+", "-", bank_id).strip("-") or "default"
        return self.root / f"{safe_bank}.jsonl"

    def _load_bank(self, bank_id: str) -> list[MemoryEntry]:
        path = self._bank_path(bank_id)
        if not path.exists():
            return []
        entries: list[MemoryEntry] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            entries.append(
                MemoryEntry(
                    id=payload["id"],
                    bank_id=payload["bank_id"],
                    content=payload["content"],
                    context=payload.get("context"),
                    metadata=dict(payload.get("metadata", {})),
                    created_at=payload.get("created_at") or datetime.now(UTC).isoformat(),
                )
            )
        return entries

    def _next_id(self, bank_id: str) -> str:
        return f"{bank_id}:{len(self._load_bank(bank_id)) + 1}"

    def _score(self, query: str, entry: MemoryEntry) -> int:
        query_terms = self._terms(query)
        haystack_terms = self._terms(entry.content)
        if entry.context:
            haystack_terms.extend(self._terms(entry.context))
        overlap = len(set(query_terms) & set(haystack_terms))
        if query_terms and overlap == 0:
            return 0
        return overlap * 10 + len(entry.content)

    def _terms(self, text: str) -> list[str]:
        return [part for part in re.split(r"\W+", text.lower()) if part]
