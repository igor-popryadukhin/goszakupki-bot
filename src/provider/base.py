from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class Listing:
    external_id: str
    title: str | None
    url: str


class SourceProvider(Protocol):
    source_id: str

    async def fetch_page(self, page: int) -> list[Listing]: ...
