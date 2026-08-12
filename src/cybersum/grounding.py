"""Check that every figure in a briefing came from the data.

The evaluation found the model reporting a total it had computed rather than
read -- a number that is well-formed, plausible, roughly half the truth, and
invisible to every downstream check. Adding the aggregate to the context fixed
that instance. Nothing prevents the next one.

This is the deterministic version of that check: pull every figure out of the
generated prose, and confirm each one appears in the context the model was
given. No model involved, so it cannot hallucinate its own verdict.

Deliberately conservative. It reports what it could not account for; it does not
decide what to do about it. A briefing with an unexplained number is still worth
delivering with a warning attached, and blocking on a false positive would be a
worse failure than the one being prevented.
"""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

#: Relative tolerance for a match. The model rounds -- 10,029.99 becomes
#: "10,030" or "about 10,000" -- and rejecting that would flag correct prose.
RELATIVE_TOLERANCE = 0.005

#: Figures at or below this are ignored. Small integers are overwhelmingly
#: ordinals, list markers and counts of things the reader can see ("the top 5
#: origins", "3 services"), and checking them produces noise rather than signal.
SMALL_NUMBER_CEILING = 10


class Kind(StrEnum):
    QUANTITY = "quantity"
    PERCENTAGE = "percentage"
    CLOCK = "clock"
    DATE = "date"


@dataclass(frozen=True)
class Figure:
    """A number found in the prose, with enough context to explain a verdict."""

    raw: str
    value: float
    kind: Kind
    snippet: str

    def __str__(self) -> str:
        return f"{self.raw} ({self.kind.value})"


@dataclass
class GroundingReport:
    grounded: list[Figure] = field(default_factory=list)
    ungrounded: list[Figure] = field(default_factory=list)
    skipped: list[Figure] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.ungrounded

    @property
    def checked(self) -> int:
        return len(self.grounded) + len(self.ungrounded)

    def summary(self) -> str:
        if not self.checked:
            return "No figures to check."
        if self.ok:
            return f"All {self.checked} figures trace to the aggregated context."
        return (
            f"{len(self.ungrounded)} of {self.checked} figures could not be traced "
            f"to the context: " + ", ".join(str(f) for f in self.ungrounded)
        )


# ── extracting facts from the context ────────────────────────────────────────

# Times and dates live inside strings ("2026-03-10 18:00"), so they are pulled
# out separately rather than treated as numbers.
CLOCK_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
DATE_RE = re.compile(r"\b(\d{1,2})-(\d{1,2})\b")
YEAR_RE = re.compile(r"\b(20\d{2})\b")


def _walk(value: Any) -> Iterator[Any]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk(item)
    else:
        yield value


@dataclass(frozen=True)
class Facts:
    """Every value the model could legitimately have read."""

    numbers: frozenset[float]
    clocks: frozenset[str]
    dates: frozenset[str]

    @classmethod
    def from_context(cls, context: dict[str, Any]) -> Facts:
        numbers: set[float] = set()
        clocks: set[str] = set()
        dates: set[str] = set()

        for leaf in _walk(context):
            if isinstance(leaf, bool) or leaf is None:
                continue
            if isinstance(leaf, (int, float)):
                numbers.add(float(leaf))
                continue
            if not isinstance(leaf, str):
                continue

            # Numeric values often arrive as strings from psycopg2's NUMERIC.
            with contextlib.suppress(ValueError):
                numbers.add(float(leaf))
            for hour, minute in CLOCK_RE.findall(leaf):
                clocks.add(f"{int(hour):02d}:{minute}")
            for month, day in DATE_RE.findall(leaf):
                dates.add(f"{int(month):02d}-{int(day):02d}")
            # Timestamps carry a year the prose will repeat as a plain number
            # ("March 10, 2026"), so it has to be a known quantity too.
            for year in YEAR_RE.findall(leaf):
                numbers.add(float(year))

        # A percentage may be stated either way round: a ratio of 0.45 in the
        # data is "45%" in the prose.
        numbers |= {n * 100 for n in numbers if 0 < n < 1}
        return cls(frozenset(numbers), frozenset(clocks), frozenset(dates))

    def has_number(self, value: float, tolerance: float = RELATIVE_TOLERANCE) -> bool:
        if value in self.numbers:
            return True
        margin = abs(value) * tolerance
        return any(abs(value - known) <= margin for known in self.numbers)


# ── extracting figures from the prose ────────────────────────────────────────

# Ordered: the first pattern to match a position wins, so clock times are taken
# before their components can be read as two separate quantities.
FIGURE_RE = re.compile(
    r"""
    (?P<clock>\b(?:[01]?\d|2[0-3]):[0-5]\d\b)
  | (?P<date>\b\d{1,2}-\d{1,2}\b(?!\d))
  | (?P<percentage>\b\d[\d,]*(?:\.\d+)?\s?%)
  | (?P<quantity>\b\d[\d,]*(?:\.\d+)?\b)
    """,
    re.VERBOSE,
)

# Contexts in which digits are not claims about the data.
IGNORE_AROUND = re.compile(
    r"""
    https?://\S*                      # URLs
  | \b[\w-]+\.(?:org|net|com|io|gov)\b  # hostnames
  | ^\s*\d+[.)]\s                     # list markers at line start
  | \bSTATUS\s+[A-C]\b                # the status vocabulary
    # The reporting window is a property of the request, not a finding about
    # the data. "the last 24 hours" is boilerplate every briefing repeats.
  | \b(?:last|past|previous|over\ the|within\ the)\ \d+[\s-]?(?:hour|day|week|month)s?\b
  | \b\d+[\s-]?(?:hour|day)\ (?:period|window|span)\b
    """,
    re.VERBOSE | re.MULTILINE | re.IGNORECASE,
)


def _masked(text: str) -> str:
    """Blank out spans whose digits are not figures, preserving offsets."""
    out = list(text)
    for match in IGNORE_AROUND.finditer(text):
        for i in range(match.start(), match.end()):
            if out[i].isdigit():
                out[i] = " "
    return "".join(out)


def extract_figures(text: str) -> list[Figure]:
    masked = _masked(text)
    figures = []
    for match in FIGURE_RE.finditer(masked):
        kind_name = match.lastgroup or "quantity"
        raw = match.group()
        start, end = match.span()
        snippet = " ".join(text[max(0, start - 40):end + 25].split())

        if kind_name == "clock":
            hour, minute = raw.split(":")
            figures.append(
                Figure(raw, float(hour) * 60 + float(minute), Kind.CLOCK, snippet)
            )
        elif kind_name == "date":
            month, day = raw.split("-")
            figures.append(Figure(raw, float(f"{month}.{day}"), Kind.DATE, snippet))
        else:
            value = float(raw.rstrip("% ").replace(",", ""))
            kind = Kind.PERCENTAGE if kind_name == "percentage" else Kind.QUANTITY
            figures.append(Figure(raw, value, kind, snippet))
    return figures


# ── the check ────────────────────────────────────────────────────────────────

def check_grounding(report: str, context: dict[str, Any]) -> GroundingReport:
    """Trace every figure in the prose back to the context.

    A figure is grounded when the same value appears in the data the model was
    given, within a rounding tolerance. Figures the check declines to judge --
    small integers, clock times and dates -- are counted separately so the
    denominator stays honest.
    """
    facts = Facts.from_context(context)
    result = GroundingReport()

    for figure in extract_figures(report):
        if figure.kind is Kind.CLOCK:
            hour = int(figure.raw.split(":")[0])
            normalised = f"{hour:02d}:{figure.raw.split(':')[1]}"
            (result.grounded if normalised in facts.clocks else result.ungrounded).append(figure)
            continue

        if figure.kind is Kind.DATE:
            month, day = figure.raw.split("-")
            normalised = f"{int(month):02d}-{int(day):02d}"
            (result.grounded if normalised in facts.dates else result.ungrounded).append(figure)
            continue

        if figure.value <= SMALL_NUMBER_CEILING and figure.kind is Kind.QUANTITY:
            result.skipped.append(figure)
            continue

        (result.grounded if facts.has_number(figure.value) else result.ungrounded).append(figure)

    if not result.ok:
        for figure in result.ungrounded:
            logger.warning("Ungrounded figure %s in: %s", figure, figure.snippet)
    return result
