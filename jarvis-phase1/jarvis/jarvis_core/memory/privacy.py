from __future__ import annotations

from typing import Iterable

from .schemas import Classification, Memory


class MemoryPrivacyPolicy:
    """Single source of truth for what may leave the device. Default-deny."""

    _CLAUDE_ALLOWED = frozenset({Classification.PUBLIC})

    def claude_allowed_classifications(self) -> list[Classification]:
        return sorted(self._CLAUDE_ALLOWED, key=lambda c: c.value)

    def can_send_to_claude(self, memory: Memory) -> bool:
        return (
            memory.classification in self._CLAUDE_ALLOWED
            and memory.deleted_at is None
        )

    def filter_for_claude(self, memories: Iterable[Memory]) -> list[Memory]:
        return [m for m in memories if self.can_send_to_claude(m)]

    def can_export(self, memory: Memory, include_sensitive: bool) -> bool:
        if memory.classification is Classification.SENSITIVE:
            return include_sensitive
        return True

    def encrypts_at_rest(self, classification: Classification) -> bool:
        return classification is not Classification.PUBLIC
