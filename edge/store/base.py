from __future__ import annotations

from abc import ABC, abstractmethod

from edge.ingestion.schemas import RawMessage


class Store(ABC):
    """Abstract time-series store. Swap SQLite → Postgres/Timescale without touching callers."""

    @abstractmethod
    async def write_raw(self, msg: RawMessage) -> None: ...

    @abstractmethod
    async def query_window(
        self, home_id: str, start_ms: int, end_ms: int
    ) -> list[RawMessage]: ...

    async def __aenter__(self) -> "Store":
        await self.open()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def open(self) -> None:
        pass

    async def close(self) -> None:
        pass
