from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from pathlib import Path

MANIFEST_FILE = "manifest.json"
CATALOG_FILE = "catalog.json"
# Visible marts are the models in this schema (data platform spec §11).
MARTS_SCHEMA = "marts"
PUBLIC_ACCESS = "public"
METRIC_PREFIX = "metric_"
ACCEPTED_VALUES_TEST = "accepted_values"
_MISSING_HINT = "run `make build-prod` and `make docs-prod` (catalog.json is written by `make docs-prod`)"


class CatalogUnavailable(Exception):
    """The dbt artifacts are missing or unreadable."""


class MartColumn(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    data_type: str
    description: str
    is_pii: bool
    is_primary_key: bool
    accepted_values: tuple[str, ...] | None
    synonyms: tuple[str, ...]
    metric_refs: tuple[str, ...]


class Mart(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    domain: str
    grain: str
    primary_key: tuple[str, ...]
    columns: tuple[MartColumn, ...]
    synonyms: tuple[str, ...]
    tests: tuple[str, ...]

    def column(self, name: str) -> MartColumn | None:
        wanted = name.lower()
        return next((column for column in self.columns if column.name.lower() == wanted), None)


class MetricDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    definition: str


class MartCatalog(BaseModel):
    model_config = ConfigDict(frozen=True)

    marts: tuple[Mart, ...]
    metrics: tuple[MetricDefinition, ...]
    generated_at: datetime

    def mart(self, name: str) -> Mart | None:
        wanted = name.lower()
        return next((mart for mart in self.marts if mart.name.lower() == wanted), None)

    def visible_names(self) -> frozenset[str]:
        return frozenset(mart.name for mart in self.marts)

    def mart_columns(self) -> dict[str, frozenset[str]]:
        """Each visible mart and its column names: what `SqlGuard` resolves qualified columns against."""
        return {mart.name: frozenset(column.name for column in mart.columns) for mart in self.marts}

    def pii_columns(self) -> frozenset[str]:
        return frozenset(column.name.lower() for mart in self.marts for column in mart.columns if column.is_pii)

    def domains(self) -> tuple[str, ...]:
        return tuple(sorted({mart.domain for mart in self.marts}))


class MartCatalogLoader:
    """Builds the MartCatalog from dbt's artifacts and rebuilds it when either file changes."""

    def __init__(self, target_path: Path) -> None:
        self._target_path = target_path
        self._stamp: tuple[int, int] | None = None
        self._catalog: MartCatalog | None = None

    def load(self) -> MartCatalog:
        stamp = self._mtimes()
        if self._catalog is None or stamp != self._stamp:
            manifest, catalog = self._read(MANIFEST_FILE), self._read(CATALOG_FILE)
            try:
                self._catalog = _build_catalog(manifest, catalog)
            except (KeyError, TypeError, AttributeError, ValueError) as exc:
                raise CatalogUnavailable(
                    f"unexpected layout in {MANIFEST_FILE} or {CATALOG_FILE}: {_MISSING_HINT}"
                ) from exc
            self._stamp = stamp
        return self._catalog

    def _mtimes(self) -> tuple[int, int]:
        try:
            return (
                (self._target_path / MANIFEST_FILE).stat().st_mtime_ns,
                (self._target_path / CATALOG_FILE).stat().st_mtime_ns,
            )
        except OSError as exc:
            raise CatalogUnavailable(
                f"dbt artifacts not found ({MANIFEST_FILE}, {CATALOG_FILE}): {_MISSING_HINT}"
            ) from exc

    def _read(self, file_name: str) -> dict[str, Any]:
        path = self._target_path / file_name
        try:
            content: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CatalogUnavailable(f"cannot read {file_name}: {_MISSING_HINT}") from exc
        return content


def _build_catalog(manifest: dict[str, Any], catalog: dict[str, Any]) -> MartCatalog:
    nodes: dict[str, Any] = manifest["nodes"]
    models = {uid: node for uid, node in nodes.items() if node["resource_type"] == "model"}
    tests = [node for node in nodes.values() if node["resource_type"] == "test"]
    # Once any model is declared public only those count; until then every marts model does.
    public_rule = any(node.get("access") == PUBLIC_ACCESS for node in models.values())
    marts = tuple(
        _build_mart(uid, node, tests, catalog["nodes"].get(uid, {}).get("columns", {}))
        for uid, node in sorted(models.items(), key=lambda item: item[1]["name"])
        if node["schema"] == MARTS_SCHEMA and (not public_rule or node.get("access") == PUBLIC_ACCESS)
    )
    metrics = tuple(
        MetricDefinition(name=doc["name"].removeprefix(METRIC_PREFIX), definition=doc["block_contents"])
        for doc in sorted(manifest.get("docs", {}).values(), key=lambda doc: doc["name"])
        if doc["name"].startswith(METRIC_PREFIX)
    )
    return MartCatalog(
        marts=marts, metrics=metrics, generated_at=datetime.fromisoformat(manifest["metadata"]["generated_at"])
    )


def _build_mart(uid: str, node: dict[str, Any], tests: list[dict[str, Any]], db_columns: dict[str, Any]) -> Mart:
    meta = node.get("meta", {})
    primary_key = tuple(name for name in _as_tuple(meta.get("primary_key")) if name)
    if not primary_key:  # describe_table orders its sample rows by it
        raise CatalogUnavailable(f"mart {node['name']} has no meta.primary_key; add it in its dbt YAML and rebuild")
    model_tests = [test for test in tests if uid in test["depends_on"]["nodes"]]
    accepted = {
        test["column_name"]: tuple(str(value) for value in test["test_metadata"]["kwargs"]["values"])
        for test in model_tests
        if test.get("test_metadata", {}).get("name") == ACCEPTED_VALUES_TEST and test.get("attached_node") == uid
    }
    columns = tuple(
        MartColumn(
            name=name,
            data_type=db_columns.get(name, {}).get("type") or column.get("data_type") or "",
            description=column.get("description", ""),
            is_pii=bool(column.get("meta", {}).get("pii", False)),
            is_primary_key=name in primary_key,
            accepted_values=accepted.get(name),
            synonyms=_as_tuple(column.get("meta", {}).get("synonyms")),
            metric_refs=tuple(
                block.rsplit(".", 1)[-1].removeprefix(METRIC_PREFIX)
                for block in column.get("doc_blocks", [])
                if block.rsplit(".", 1)[-1].startswith(METRIC_PREFIX)
            ),
        )
        for name, column in node["columns"].items()
    )
    return Mart(
        name=node["name"],
        description=node.get("description", ""),
        domain=meta.get("domain", ""),
        grain=meta.get("grain", ""),
        primary_key=primary_key,
        columns=columns,
        synonyms=_as_tuple(meta.get("synonyms")),
        tests=tuple(sorted(test["name"] for test in model_tests)),
    )


def _as_tuple(value: str | list[str] | None) -> tuple[str, ...]:
    match value:
        case None:
            return ()
        case str():
            return (value,)
        case _:
            return tuple(value)
