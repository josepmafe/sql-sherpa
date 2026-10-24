"""Few-shot question/SQL examples, ranked against a new question by token overlap (agent spec §4.2)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

import yaml
from pydantic import BaseModel, ConfigDict, TypeAdapter

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

MIN_K: Final = 1
MAX_K: Final = 10
STOP_WORDS: Final = (
    *("a", "an", "the", "of", "in", "on", "for", "to", "by", "per", "and", "or", "is", "are", "was"),
    *("were", "what", "which", "how", "many", "much", "show", "me", "give", "list", "with", "from"),
    *("do", "does", "did", "our", "we", "my", "all", "each", "that", "this"),
)
_TOKEN = re.compile(r"[a-z0-9]+")


class Example(BaseModel):
    model_config = ConfigDict(frozen=True)

    question: str
    sql: str
    notes: str


class ExampleIndex:
    def __init__(self, examples: Sequence[Example]) -> None:
        self.examples: tuple[Example, ...] = tuple(examples)
        self._tokens = [_tokens(example.question) for example in self.examples]

    @classmethod
    def from_yaml(cls, path: Path) -> ExampleIndex:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(TypeAdapter(list[Example]).validate_python(raw))

    def top(self, question: str, k: int) -> list[Example]:
        if not MIN_K <= k <= MAX_K:
            raise ValueError(f"k must be between {MIN_K} and {MAX_K}, got {k}")
        wanted = _tokens(question)
        scores = [_jaccard(wanted, tokens) for tokens in self._tokens]
        # sorted() is stable, so equal scores keep file order (and all-zero scores give the first k).
        order = sorted(range(len(self.examples)), key=lambda i: -scores[i])
        return [self.examples[i] for i in order[:k]]


def _tokens(text: str) -> frozenset[str]:
    return frozenset(_TOKEN.findall(text.lower())) - frozenset(STOP_WORDS)


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0
