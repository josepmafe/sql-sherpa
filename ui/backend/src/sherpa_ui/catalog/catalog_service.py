from __future__ import annotations

from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict
from sherpa_mcp.catalog import MARTS_SCHEMA

if TYPE_CHECKING:
    from sherpa_mcp.catalog import Mart, MartCatalog, MartCatalogLoader, MartColumn

SHARED_DOMAIN: Final = "shared"  # listed last, after the business domains


class ColumnSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    data_type: str
    description: str
    is_pii: bool
    is_primary_key: bool


class TableSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    domain: str
    grain: str
    primary_key: tuple[str, ...]
    columns: tuple[ColumnSummary, ...]


class DomainNode(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    tables: tuple[TableSummary, ...]


class CatalogTree(BaseModel):
    """database, then the `marts` schema, then domains, tables and columns."""

    model_config = ConfigDict(frozen=True)

    database: str
    schema_name: str
    domains: tuple[DomainNode, ...]


class MetricRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    definition: str


class ColumnDetails(ColumnSummary):
    accepted_values: tuple[str, ...] | None
    synonyms: tuple[str, ...]
    metric_refs: tuple[MetricRef, ...]


class TableDetails(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    domain: str
    grain: str
    primary_key: tuple[str, ...]
    synonyms: tuple[str, ...]
    tests: tuple[str, ...]
    columns: tuple[ColumnDetails, ...]


class CatalogService:
    """Maps the MCP server's `MartCatalog` into the API's models. Blocking (it may read the dbt artifacts)."""

    def __init__(self, loader: MartCatalogLoader, database: str) -> None:
        self._loader = loader
        self._database = database

    def tree(self) -> CatalogTree:
        by_domain: dict[str, list[Mart]] = {}
        for mart in self._loader.load().marts:
            by_domain.setdefault(mart.domain, []).append(mart)

        domains = tuple(
            DomainNode(name=name, tables=tuple(_summary(mart) for mart in sorted(marts, key=lambda m: m.name)))
            for name, marts in sorted(by_domain.items(), key=lambda item: _domain_order(item[0]))
        )
        return CatalogTree(database=self._database, schema_name=MARTS_SCHEMA, domains=domains)

    def table(self, name: str) -> TableDetails | None:
        """The Inspector's view of a mart, or None when no visible mart has that name."""
        catalog = self._loader.load()
        mart = catalog.mart(name)
        return _details(mart, catalog) if mart else None


def _domain_order(domain: str) -> tuple[bool, str]:
    return (domain == SHARED_DOMAIN, domain)


def _column_summary(column: MartColumn) -> ColumnSummary:
    return ColumnSummary(
        name=column.name,
        data_type=column.data_type,
        description=column.description,
        is_pii=column.is_pii,
        is_primary_key=column.is_primary_key,
    )


def _summary(mart: Mart) -> TableSummary:
    return TableSummary(
        name=mart.name,
        description=mart.description,
        domain=mart.domain,
        grain=mart.grain,
        primary_key=mart.primary_key,
        columns=tuple(_column_summary(column) for column in mart.columns),
    )


def _details(mart: Mart, catalog: MartCatalog) -> TableDetails:
    definitions = {metric.name: metric.definition for metric in catalog.metrics}
    columns = tuple(
        ColumnDetails(
            **_column_summary(column).model_dump(),
            accepted_values=column.accepted_values,
            synonyms=column.synonyms,
            metric_refs=tuple(
                MetricRef(name=ref, definition=definitions[ref]) for ref in column.metric_refs if ref in definitions
            ),
        )
        for column in mart.columns
    )
    return TableDetails(
        name=mart.name,
        description=mart.description,
        domain=mart.domain,
        grain=mart.grain,
        primary_key=mart.primary_key,
        synonyms=mart.synonyms,
        tests=mart.tests,
        columns=columns,
    )
