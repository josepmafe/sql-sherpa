from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, cast

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from sherpa_generator.records import RawRecord

if TYPE_CHECKING:
    from collections.abc import Iterable


class RecordUpdate[T: RawRecord](BaseModel):
    """A validated column update of one existing row, identified by its key columns."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    record_type: type[T]
    key: dict[str, object]
    values: dict[str, object]


class DayChanges:
    """Everything one simulated day wants to write; the RecordWriter applies it in a single transaction."""

    def __init__(self) -> None:
        self._inserts: dict[type[RawRecord], list[RawRecord]] = {}
        self._updates: list[RecordUpdate[RawRecord]] = []
        self._pending_removals: list[str] = []

    @property
    def inserts(self) -> dict[type[RawRecord], list[RawRecord]]:
        return self._inserts

    @property
    def updates(self) -> list[RecordUpdate[RawRecord]]:
        return self._updates

    @property
    def pending_removals(self) -> list[str]:
        return self._pending_removals

    def insert(self, record: RawRecord) -> None:
        self._inserts.setdefault(type(record), []).append(record)

    def update(self, record_type: type[RawRecord], key: int | tuple[object, ...], **values: object) -> None:
        """Queue a column update; unknown columns and invalid values raise ValueError immediately."""
        if not values:
            msg = f"update of {record_type.TABLE} needs at least one column"
            raise ValueError(msg)

        validated = {name: self._validate(record_type, name, value) for name, value in values.items()}
        self._updates.append(
            RecordUpdate(record_type=record_type, key=self._key_columns(record_type, key), values=validated)
        )

    def remove_pending(self, event_ids: Iterable[str]) -> None:
        self._pending_removals.extend(event_ids)

    def inserted[T: RawRecord](self, record_type: type[T]) -> list[T]:
        return cast("list[T]", list(self._inserts.get(record_type, [])))

    def insert_counts(self) -> dict[str, int]:
        return {record_type.TABLE: len(records) for record_type, records in self._inserts.items()}

    @staticmethod
    def _validate(record_type: type[RawRecord], name: str, value: object) -> object:
        field = record_type.model_fields.get(name)
        if field is None:
            msg = f"{record_type.__name__} has no column {name!r}"
            raise ValueError(msg)

        # field.annotation drops Annotated metadata (e.g. Money's rounding), so re-attach it.
        annotation: Any = Annotated[field.annotation, *field.metadata] if field.metadata else field.annotation
        adapter: TypeAdapter[object] = TypeAdapter(annotation)
        try:
            return adapter.validate_python(value)
        except ValidationError as exc:
            msg = f"invalid value for {record_type.__name__}.{name}: {value!r}"
            raise ValueError(msg) from exc

    @staticmethod
    def _key_columns(record_type: type[RawRecord], key: int | tuple[object, ...]) -> dict[str, object]:
        parts = key if isinstance(key, tuple) else (key,)
        if len(parts) != len(record_type.KEY):
            msg = f"key {key!r} does not match {record_type.__name__} key columns {record_type.KEY}"
            raise ValueError(msg)

        return dict(zip(record_type.KEY, parts, strict=True))
