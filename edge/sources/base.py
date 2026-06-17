from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator

from edge.ingestion.schemas import RawMessage


class Source(ABC):
    """Abstract async source of RawMessages."""

    @abstractmethod
    async def stream(self) -> AsyncIterator[RawMessage]:
        """Yield RawMessages indefinitely (or until the log is exhausted for replay)."""
        ...

    async def __aenter__(self) -> "Source":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        pass
