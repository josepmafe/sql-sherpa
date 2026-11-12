"""The never-fabricate guards (agent spec §5.2). Pure: each check returns `None` or the retry message for the model."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from datetime import date

    from pydantic import JsonValue

    from sherpa_agent.recording import Execution

MAGNITUDE_TOLERANCE: Final = Decimal("0.05")  # §5.2: magnitude-rounded forms are within a relative 5%
HALF_UNIT: Final = Decimal("0.5")  # §5.2: within rounding is +-0.5 in the last shown digit
# §5.2: a plain integer gets the relative 5% only when hedged ("about 3,400"); the suffix forms ("1.2M") always do
HEDGE: Final = re.compile(
    r"(?:about|around|roughly|approximately|nearly|almost|~|\u2248)\s*(?:\u20ac|eur\b)?\s*$", re.I
)
MIN_UNQUOTED_DIGITS: Final = 5  # a digits-only text cell of fewer digits is a number the answer must verify
MIN_ABBREVIATED_HASH: Final = 6  # letters-and-digits tokens at least this long inside a text cell are the cell
PERCENT_FACTOR: Final = Decimal(100)  # §5.2: percentages are also tried x100
NUMERIC_TYPE_PREFIXES: Final = ("int", "numeric", "float")  # int2/int4/int8/integer, numeric, float4/float8/float
DATE_TYPE_PREFIXES: Final = ("date", "timestamp")
QUARTER_MONTHS: Final = 3
MAGNITUDES: Final = {
    "k": Decimal(10) ** 3,
    "mn": Decimal(10) ** 6,
    "bn": Decimal(10) ** 9,
    "m": Decimal(10) ** 6,
    "b": Decimal(10) ** 9,
    "thousand": Decimal(10) ** 3,
    "million": Decimal(10) ** 6,
    "billion": Decimal(10) ** 9,
}
MINUS_SIGNS: Final = ("-", "\u2212")

NO_SUCH_EXECUTION: Final = "no successful run_sql has execution_id {execution_id}"
NOT_THE_LAST_EXECUTION: Final = "{execution_id} is not the last query you ran; describe the result of {last_id}"
UNVERIFIED_NUMBERS: Final = (
    "these numbers are not in the result: {numbers}. Quote a number as it appears in the result, with its sign (a "
    'decrease keeps its minus sign); round only with a hedge such as "about"; compute any derived figure (share, '
    "difference, growth) in SQL and run it again"
)
NOT_VALIDATED: Final = "validate the SQL you return with validate_sql"
NOT_THE_VALIDATED_SQL: Final = "return exactly the SQL that last passed validate_sql"

# Optional sign (only when it does not follow a word character, digit, "." or ","), a digit run with any "." and ","
# (validated afterwards: only "," in groups of three and one "." decimal point are numbers), an exponent that makes the
# figure unverifiable, then a magnitude suffix and a percent sign. A suffix letter must not continue into a word
# ("5 months" is a plain 5); the words may follow a space.
FIGURE: Final = re.compile(
    r"""
    (?:(?<![\w.,])(?P<sign>[+\-\u2212])(?=\d))?
    (?P<body>\d[\d.,]*\d|\d)
    (?P<exponent>[eE][+\-]?\d+)?
    (?P<magnitude>\s?(?:thousand|million|billion)\b|(?:bn|mn|[kKmMbB])(?![A-Za-z]))?
    (?P<percent>\s?%|\s?percent\b)?
    """,
    re.VERBOSE | re.IGNORECASE,
)
PLAIN_NUMBER: Final = re.compile(r"\d+(?:\.\d+)?")
GROUPED_NUMBER: Final = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")
ISO_DATE: Final = re.compile(r"(\d{4})-(\d{2})(?:-(\d{2}))?")
MONTHS: Final = range(1, 13)
DAYS: Final = range(1, 32)
SCALE_OR_PERCENT: Final = r"\s?(?:%|(?:percent|thousand|million|billion|bn|mn|[kKmMbB])\b)"
ABBREVIATION_TOKEN: Final = re.compile(r"[A-Za-z0-9]+")
# A single-quoted literal or a double-quoted identifier (a doubled quote escapes itself), or a run of whitespace.
SQL_QUOTED_OR_SPACE: Final = re.compile(r"""'(?:[^']|'')*'|"(?:[^"]|"")*"|\s+""")


class Figure(BaseModel):
    """A number as the answer wrote it. `value` is `None` when the form is not an accepted one."""

    model_config = ConfigDict(frozen=True)

    raw: str
    value: Decimal | None
    shown_decimals: int
    scale: Decimal
    percent: bool
    hedged: bool


def check_execution_reference(execution_id: str, executions: Sequence[Execution]) -> str | None:
    if not any(execution.execution_id == execution_id for execution in executions):
        return NO_SUCH_EXECUTION.format(execution_id=execution_id)
    last_id = executions[-1].execution_id
    if execution_id != last_id:
        return NOT_THE_LAST_EXECUTION.format(execution_id=execution_id, last_id=last_id)
    return None


def check_draft_sql(sql: str, last_validated_sql: str | None) -> str | None:
    if last_validated_sql is None:
        return NOT_VALIDATED
    if _normalise_sql(sql) != _normalise_sql(last_validated_sql):
        return NOT_THE_VALIDATED_SQL
    return None


def numbers_message(numbers: Sequence[str]) -> str:
    return UNVERIFIED_NUMBERS.format(numbers=", ".join(numbers))


def unverified_numbers(
    answer_text: str, execution: Execution, *, question: str, time_range: Sequence[str], as_of_date: date
) -> list[str]:
    """The numbers of `answer_text` that are none of the §5.2 forms, as written, once each and in order.

    Choices where the spec leaves room, all on the strict side: a sign must match the result value's sign; "," in
    groups of three is the only thousands separator ("1.180.000", "1 180 000", "1,18" are not numbers we accept);
    scientific notation is refused; the 5% tolerance applies to suffix forms ("1.2M") and to plain integers right
    after a hedge ("about 3,400"), so an unhedged "949,999" must match within rounding; percentages never match
    counts or dates.
    """
    reference = _Reference(execution, question=question, time_range=time_range, as_of_date=as_of_date)
    unverified: list[str] = []
    for figure in _figures(_without_text_cells(answer_text, execution)):
        if not reference.accepts(figure) and figure.raw not in unverified:
            unverified.append(figure.raw)
    return unverified


class _Reference:
    """What the answer's numbers may be: result values, counts, date parts, and the question's numbers."""

    def __init__(self, execution: Execution, *, question: str, time_range: Sequence[str], as_of_date: date) -> None:
        self._values = _numeric_cells(execution)
        # A count over a truncated result is not a total (§5.3), so it is not a number the answer may state.
        self._counts = (
            set() if execution.truncated else {Decimal(execution.returned_rows), *_distinct_counts(execution)}
        )
        self._date_parts = _date_parts(_date_cells(execution), question, *time_range, as_of_date.isoformat())
        self._question = {
            (figure.value * figure.scale, figure.percent) for figure in _figures(question) if figure.value is not None
        }
        self._time_range_numbers = {
            figure.value * figure.scale
            for text in time_range
            for figure in _figures(text)
            if figure.value is not None and not figure.percent
        }

    def accepts(self, figure: Figure) -> bool:
        if figure.value is None:
            return False
        scaled = figure.value * figure.scale
        if (scaled, figure.percent) in self._question:
            return True
        if figure.percent:
            return self._near_a_result_value(figure)
        return (
            scaled in self._counts
            or scaled in self._date_parts
            or scaled in self._time_range_numbers
            or self._near_a_result_value(figure)
        )

    def _near_a_result_value(self, figure: Figure) -> bool:
        assert figure.value is not None
        shown = figure.value * figure.scale
        rounding = HALF_UNIT * Decimal(10) ** -figure.shown_decimals * figure.scale
        relative = figure.scale != 1 or figure.hedged
        for value in self._values:
            for candidate in (value, value * PERCENT_FACTOR) if figure.percent else (value,):
                gap = abs(shown - candidate)
                if gap <= rounding or (relative and gap <= MAGNITUDE_TOLERANCE * abs(candidate)):
                    return True
        return False


def _figures(text: str) -> list[Figure]:
    return [_figure(match) for match in FIGURE.finditer(text)]


def _figure(match: re.Match[str]) -> Figure:
    body = match["body"]
    raw = match.group(0).strip()
    magnitude = (match["magnitude"] or "").strip().lower()
    percent = match["percent"] is not None
    if match["exponent"] or not (PLAIN_NUMBER.fullmatch(body) or GROUPED_NUMBER.fullmatch(body)):
        return Figure(raw=raw, value=None, shown_decimals=0, scale=Decimal(1), percent=percent, hedged=False)
    digits = body.replace(",", "")
    value = Decimal(digits)
    if match["sign"] in MINUS_SIGNS:
        value = -value
    decimals = len(digits.partition(".")[2])
    return Figure(
        raw=raw,
        value=value,
        shown_decimals=decimals,
        scale=MAGNITUDES.get(magnitude, Decimal(1)),
        percent=percent,
        hedged=not magnitude and not percent and decimals == 0 and bool(HEDGE.search(match.string[: match.start()])),
    )


def _without_text_cells(answer_text: str, execution: Execution) -> str:
    """Digit runs inside a text cell are no numbers (§5.2). A cell is removed only when it stands alone: not touching
    a word, a decimal point or a sign/currency, and not followed by a scale or percent, so a cell "42" cannot launder
    "42%" or "42 thousand". A digits-only cell shorter than 5 digits is not removed at all (it may be a year or a
    count the answer must verify). Abbreviated hashes ("9be4a3f9…") count as inside their cell."""
    cells = {
        cell
        for row in execution.rows
        for cell, column_type in zip(row, execution.column_types, strict=False)
        if isinstance(cell, str) and not _is_numeric(column_type) and _quotable(cell)
    }
    for cell in sorted(cells, key=len, reverse=True):
        left = r"(?<![\w.,+\-\u2212\u20ac$\u00a3])" if re.match(r"\w", cell) else ""
        right = rf"(?!\w)(?![.,]\d)(?!{SCALE_OR_PERCENT})" if re.search(r"\w$", cell) else ""
        answer_text = re.sub(f"{left}{re.escape(cell)}{right}", " ", answer_text)
    lowered = [cell.lower() for cell in cells]
    return ABBREVIATION_TOKEN.sub(
        lambda match: " " if _is_abbreviated_hash(match[0], lowered) else match[0], answer_text
    )


def _quotable(cell: str) -> bool:
    if not any(char.isdigit() for char in cell):
        return False
    return not cell.isdigit() or len(cell) >= MIN_UNQUOTED_DIGITS


def _is_abbreviated_hash(token: str, lowered_cells: list[str]) -> bool:
    mixed = any(char.isdigit() for char in token) and any(char.isalpha() for char in token)
    return mixed and len(token) >= MIN_ABBREVIATED_HASH and any(token.lower() in cell for cell in lowered_cells)


def _numeric_cells(execution: Execution) -> list[Decimal]:
    values: list[Decimal] = []
    for row in execution.rows:
        for cell, column_type in zip(row, execution.column_types, strict=False):
            if _is_numeric(column_type) and (value := _decimal(cell)) is not None:
                values.append(value)
    return values


def _decimal(cell: JsonValue) -> Decimal | None:
    if isinstance(cell, bool) or not isinstance(cell, int | float | str):
        return None
    try:
        value = Decimal(str(cell).strip())
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _distinct_counts(execution: Execution) -> list[Decimal]:
    counts: list[Decimal] = []
    for index in range(len(execution.columns)):
        distinct = {repr(row[index]) for row in execution.rows if index < len(row) and row[index] is not None}
        counts.append(Decimal(len(distinct)))
    return counts


def _date_cells(execution: Execution) -> list[str]:
    return [
        cell
        for row in execution.rows
        for cell, column_type in zip(row, execution.column_types, strict=False)
        if isinstance(cell, str) and column_type.startswith(DATE_TYPE_PREFIXES)
    ]


def _date_parts(cells: Iterable[str], *texts: str) -> set[Decimal]:
    """Year, month, day and quarter number of every ISO date (or year-month) in the cells and texts."""
    parts: set[Decimal] = set()
    for text in (*cells, *texts):
        for year, month, day in ISO_DATE.findall(text):
            if int(month) not in MONTHS or (day and int(day) not in DAYS):
                continue
            parts |= {Decimal(int(year)), Decimal(int(month)), Decimal((int(month) - 1) // QUARTER_MONTHS + 1)}
            if day:
                parts.add(Decimal(int(day)))
    return parts


def _is_numeric(column_type: str) -> bool:
    return column_type.lower().startswith(NUMERIC_TYPE_PREFIXES)


def _normalise_sql(sql: str) -> str:
    """Equal modulo one trailing `;` and whitespace outside quotes: inside a literal a space is data."""
    stripped = sql.strip()
    stripped = stripped.removesuffix(";").rstrip()
    return SQL_QUOTED_OR_SPACE.sub(lambda match: " " if match.group(0).isspace() else match.group(0), stripped)
