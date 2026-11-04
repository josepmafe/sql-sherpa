"""The Home page's data (UI spec §6.2): the figures part by part, so one failing dependency shows its own reason and
leaves the rest of the screen alone."""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Final, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from sherpa_ui.build.build_status import BuildStatusUnavailable
from sherpa_ui.home.data_status import (
    BUILD_UNAVAILABLE_NOTE,
    FRESHNESS_UNAVAILABLE_NOTE,
    NO_BUILD_NOTE,
    NO_FRESHNESS_NOTE,
    BuildSummary,
    DataStatus,
    Freshness,
    FreshnessUnavailable,
    failed_node_count,
    failing_test_count,
)
from sherpa_ui.home.figures import Tile, TrendRow, TrendWeek, comparable, tile, trend_weeks
from sherpa_ui.readonly.query_runner import QueryErrorCode, QueryOutcome, UiQueryError

if TYPE_CHECKING:
    from sherpa_ui.build.build_status import BuildStatusReader, LastFullBuild
    from sherpa_ui.home.data_status import FreshnessReader
    from sherpa_ui.home.home_queries import HomeQueries, TileQuery
    from sherpa_ui.readonly.query_runner import QueryRunner

logger = logging.getLogger(__name__)

NO_DATA_MESSAGE: Final = (
    "no orders in the marts yet: run make build-prod "
    "(first make backfill if uv run sherpa-generate status shows no simulation)"
)
UNDEFINED_TABLE: Final = "42P01"  # SQLSTATE: a missing relation, and a missing schema
QUERY_ID_PREFIX: Final = "home"


class HomeFigures(BaseModel):
    model_config = ConfigDict(frozen=True)

    tiles: tuple[Tile, ...]
    trend: tuple[TrendWeek, ...]


class FiguresUnavailable(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: Literal["no_data"] | QueryErrorCode
    message: str


class Home(BaseModel):
    """Exactly one of `figures` and `figures_error` is set."""

    model_config = ConfigDict(frozen=True)

    figures: HomeFigures | None
    figures_error: FiguresUnavailable | None
    status: DataStatus


class HomeService:
    """Blocking: the router calls `home` in a worker thread. The queries run one after another, each in a runner
    slot, so a full runner answers `busy` for the figures only."""

    def __init__(
        self, runner: QueryRunner, builds: BuildStatusReader, freshness: FreshnessReader, queries: HomeQueries
    ) -> None:
        self._runner = runner
        self._builds = builds
        self._freshness = freshness
        self._queries = queries

    def home(self) -> Home:
        figures, error, as_of = self._figures()
        build, build_note = self._build()
        freshness, freshness_note = self._read_freshness()
        status = DataStatus(
            as_of_date=as_of,
            build=build,
            build_note=build_note,
            freshness=freshness,
            freshness_note=freshness_note,
        )
        return Home(figures=figures, figures_error=error, status=status)

    def _figures(self) -> tuple[HomeFigures | None, FiguresUnavailable | None, date | None]:
        """The figures or why there are none, and `as_of` once the period query has answered."""
        try:
            period = self._run(self._queries.period)
        except UiQueryError as error:
            if error.sqlstate == UNDEFINED_TABLE:
                return None, FiguresUnavailable(code="no_data", message=NO_DATA_MESSAGE), None

            return None, _unavailable(error), None

        if not period or period[0][0] is None:
            return None, FiguresUnavailable(code="no_data", message=NO_DATA_MESSAGE), None

        as_of = date.fromisoformat(str(period[0][0]))
        whole_window = comparable(as_of, date.fromisoformat(str(period[0][1])))
        try:
            tiles = tuple(self._tile(query, whole_window=whole_window) for query in self._queries.tiles)
            weeks = [
                TrendRow(week_start=date.fromisoformat(str(w)), net_revenue=_decimal(r), is_black_friday_week=bool(b))
                for w, r, b in self._run(self._queries.trend)
            ]
        except UiQueryError as error:
            return None, _unavailable(error), as_of

        return HomeFigures(tiles=tiles, trend=trend_weeks(weeks, as_of)), None, as_of

    def _tile(self, query: TileQuery, *, whole_window: bool) -> Tile:
        rows = self._run(query.sql)
        this_period, last_period = (_optional_decimal(value) for value in rows[0])
        return tile(query, this_period, last_period, comparable=whole_window)

    def _run(self, sql: str) -> tuple[tuple[object, ...], ...]:
        outcome: QueryOutcome = self._runner.run(f"{QUERY_ID_PREFIX}-{uuid4()}", sql, (), ())
        return outcome.rows

    def _build(self) -> tuple[BuildSummary | None, str | None]:
        try:
            last = self._builds.last_full_build()
        except BuildStatusUnavailable:
            logger.warning("home: the build record could not be read", exc_info=True)
            return None, BUILD_UNAVAILABLE_NOTE

        if last is None:
            return None, NO_BUILD_NOTE

        return _summary(last), None

    def _read_freshness(self) -> tuple[Freshness | None, str | None]:
        try:
            freshness = self._freshness.read()
        except FreshnessUnavailable:
            logger.warning("home: sources.json could not be read", exc_info=True)
            return None, FRESHNESS_UNAVAILABLE_NOTE

        if freshness is None:
            return None, NO_FRESHNESS_NOTE

        return freshness, None


def _unavailable(error: UiQueryError) -> FiguresUnavailable:
    return FiguresUnavailable(code=error.code, message=error.message)


def _summary(last: LastFullBuild) -> BuildSummary:
    return BuildSummary(
        started_at=last.started_at,
        node_count=last.node_count,
        failing_tests=failing_test_count(last),
        failed_nodes=failed_node_count(last),
    )


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


def _optional_decimal(value: object) -> Decimal | None:
    return None if value is None else _decimal(value)
