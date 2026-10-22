from __future__ import annotations

from sherpa_generator.records import RawRecord


def all_record_types() -> list[type[RawRecord]]:
    return _subclasses(RawRecord)


def _subclasses(cls: type[RawRecord]) -> list[type[RawRecord]]:
    found: list[type[RawRecord]] = []
    for sub in cls.__subclasses__():
        found.append(sub)
        found.extend(_subclasses(sub))
    return found
