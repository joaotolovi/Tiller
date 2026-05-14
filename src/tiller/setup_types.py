from __future__ import annotations

from typing import Protocol


class SetupProvider(Protocol):
    name: str
    label: str

    async def collect(self) -> dict[str, object]: ...
