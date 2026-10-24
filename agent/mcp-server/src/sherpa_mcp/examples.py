"""Few-shot question/SQL examples, ranked against a new question by token Jaccard (agent spec §4.1, §4.3)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

import sqlglot
import yaml
from pydantic import BaseModel, ConfigDict, TypeAdapter
from sqlglot import exp

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

MIN_K: Final = 1
MAX_K: Final = 10
SCORE_DIGITS: Final = 3
# The score weighs the Jaccard against the example's question over the one against everything the example says (its
# question, notes and the table and column names of its SQL): long notes would otherwise outweigh a matching question.
QUESTION_WEIGHT: Final = 0.75
STOP_WORDS: Final = (
    *("a", "an", "the", "of", "in", "on", "for", "to", "by", "per", "and", "or", "is", "are", "was"),
    *("were", "what", "which", "how", "many", "much", "show", "me", "give", "list", "with", "from"),
    *("do", "does", "did", "our", "we", "my", "all", "each", "that", "this", "it", "as", "so", "be", "have", "has"),
)
# Crude stemming, so that "monthly", "months" and "month" meet: the first suffix that leaves at least MIN_STEM
# characters is cut. "ies" becomes "y"; "es" only goes after ss, us, x, zz, ch or sh (boxes, statuses), otherwise
# just the "s" (purchases -> purchase, sales -> sale); a trailing "s" stays after ss, us or is (status); a doubled
# consonant other than ss left by "ed"/"ing" is halved (cancelled -> cancel, shipping -> ship), and a stem they leave
# ending in c, v, z or a, e, i or o and s gets its "e" back (placed -> place, increased -> increase). Accepted
# misses: caused -> caus, changed -> chang.
SUFFIXES: Final = ("ing", "ies", "ed", "ly", "es", "s")
MIN_STEM: Final = 3
ES_AFTER: Final = ("ss", "us", "x", "zz", "ch", "sh")
KEEP_S_AFTER: Final = ("ss", "us", "is")
E_AFTER: Final = ("c", "v", "z", "as", "es", "is", "os")
# Words people use for what the examples name otherwise: a word or its stem -> the stems it also stands for.
SYNONYMS: Final[dict[str, tuple[str, ...]]] = {
    "aov": ("average", "order", "value"),
    "sale": ("revenue", "gross"),
    "turnover": ("revenue", "gross"),
    "basket": ("order",),
    "unit": ("item", "quantity"),
    "coupon": ("discount", "promotion"),
    "promo": ("promotion", "discount"),
    "voucher": ("discount", "promotion"),
    "wow": ("week",),
    "mom": ("month",),
    "yoy": ("year",),
    "buyer": ("customer",),
    "client": ("customer",),
    "segment": ("lifecycle", "stage"),
    "delivery": ("deliver",),
    "cancellation": ("cancel",),
}
_TOKEN = re.compile(r"[a-z0-9]+")


class Example(BaseModel):
    model_config = ConfigDict(frozen=True)

    question: str
    sql: str
    notes: str


class RankedExample(Example):
    score: float  # 0 to 1, see QUESTION_WEIGHT


class ExampleIndex:
    def __init__(self, examples: Sequence[Example]) -> None:
        self.examples: tuple[Example, ...] = tuple(examples)
        self._questions = [_tokens(example.question) for example in self.examples]
        self._everything = [_example_tokens(example) for example in self.examples]

    @classmethod
    def from_yaml(cls, path: Path) -> ExampleIndex:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(TypeAdapter(list[Example]).validate_python(raw))

    def top(self, question: str, k: int) -> list[RankedExample]:
        """The `k` best examples, best first; equal scores keep file order. An example sharing no token with the
        question is never returned, so a question unlike every example gets an empty list."""
        if not MIN_K <= k <= MAX_K:
            raise ValueError(f"k must be between {MIN_K} and {MAX_K}, got {k}")
        wanted = _tokens(question)
        scores = [
            QUESTION_WEIGHT * _jaccard(wanted, question) + (1 - QUESTION_WEIGHT) * _jaccard(wanted, everything)
            for question, everything in zip(self._questions, self._everything, strict=True)
        ]
        order = sorted((i for i, score in enumerate(scores) if score > 0), key=lambda i: -scores[i])
        return [RankedExample(**self.examples[i].model_dump(), score=round(scores[i], SCORE_DIGITS)) for i in order[:k]]


def _example_tokens(example: Example) -> frozenset[str]:
    """An example is matched on its question, its notes, and the table and column names in its SQL."""
    tree = sqlglot.parse_one(example.sql, read="postgres")
    names = {table.name for table in tree.find_all(exp.Table)} | {column.name for column in tree.find_all(exp.Column)}
    return _tokens(" ".join((example.question, example.notes, *sorted(names))).replace("_", " "))


def _tokens(text: str) -> frozenset[str]:
    words = [word for word in _TOKEN.findall(text.lower()) if word not in STOP_WORDS]
    stems = {_stem(word) for word in words}
    aliases = {alias for word in words for alias in SYNONYMS.get(word, SYNONYMS.get(_stem(word), ()))}
    return frozenset(stems | aliases)


def _stem(word: str) -> str:
    for suffix in SUFFIXES:
        stem = word.removesuffix(suffix)
        if stem == word or len(stem) < MIN_STEM:
            continue
        match suffix:
            case "ies":
                return stem + "y"
            case "es" if not stem.endswith(ES_AFTER):
                continue
            case "s" if word.endswith(KEEP_S_AFTER):
                continue
            case "ed" | "ing" if stem[-1] == stem[-2] and stem[-1] not in "aeious":
                return stem[:-1]
            case "ed" | "ing" if stem.endswith(E_AFTER):
                return stem + "e"
        return stem
    return word


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0
