from __future__ import annotations

from dataclasses import dataclass, field

from ..db.repo import KeywordRecord, Repository


def _normalize_phrase(text: str) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in text).split())


@dataclass(slots=True)
class KeywordEntryView:
    id: int
    source_phrase: str
    normalized_phrase: str
    synonyms: list[str] = field(default_factory=list)
    negative_contexts: list[str] = field(default_factory=list)
    weight: float = 1.0

    @property
    def aliases(self) -> list[str]:
        items = [self.normalized_phrase]
        items.extend(_normalize_phrase(item) for item in self.synonyms if item.strip())
        unique: list[str] = []
        seen: set[str] = set()
        for item in items:
            if not item or item in seen:
                continue
            seen.add(item)
            unique.append(item)
        return unique


class KeywordRegistry:
    def __init__(self, repository: Repository) -> None:
        self._repo = repository
        self._cached_version = 0
        self._entries: list[KeywordEntryView] = []

    async def refresh(self) -> int:
        version = await self._repo.sync_keyword_registry()
        entries = await self._repo.list_active_keyword_records()
        self._entries = [self._to_entry(item) for item in entries]
        self._cached_version = version
        return version

    async def get_entries(self, *, force_refresh: bool = False) -> list[KeywordEntryView]:
        prefs = await self._repo.get_preferences()
        current_version = prefs.keyword_version if prefs is not None else 0
        if force_refresh or not self._entries or current_version != self._cached_version:
            await self.refresh()
        return list(self._entries)

    @property
    def version(self) -> int:
        return self._cached_version

    @staticmethod
    def _to_entry(record: KeywordRecord) -> KeywordEntryView:
        return KeywordEntryView(
            id=record.id,
            source_phrase=record.source_phrase,
            normalized_phrase=record.normalized_phrase,
            synonyms=[_normalize_phrase(item) for item in record.synonyms if item.strip()],
            negative_contexts=[_normalize_phrase(item) for item in record.negative_contexts if item.strip()],
            weight=record.weight,
        )
