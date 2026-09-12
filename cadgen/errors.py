"""Error type shared by every layer. Messages are written for a person or an LLM to act on."""

from __future__ import annotations


class CadgenError(Exception):
    """A user-facing error: what failed, in which feature, and hints on how to fix it."""

    def __init__(self, message: str, feature_id: str | None = None, hints: list[str] | None = None):
        self.message = message
        self.feature_id = feature_id
        self.hints = hints or []
        super().__init__(self.format())

    def format(self) -> str:
        head = f"[{self.feature_id}] {self.message}" if self.feature_id else self.message
        if not self.hints:
            return head
        return head + "\n" + "\n".join(f"  - {h}" for h in self.hints)

    def __str__(self) -> str:  # keep the formatted form even if re-raised
        return self.format()
