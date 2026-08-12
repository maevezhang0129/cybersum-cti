"""The unified query builder must emit the SQL that produced the published numbers.

``tests/golden/sql/`` holds the ten queries as they were rendered by the two
separate aggregation implementations before they were merged behind ``Scope``.
If these tests pass, the merge changed layout and nothing else. If one fails,
either the merge altered what the thesis measured, or a divergence was
introduced without being declared as a ``Scope`` field -- both are things a
reader of the results deserves to have caught.
"""

from __future__ import annotations

import pathlib

import pytest

from cybersum.aggregation import SIGNAL_BUILDERS, Scope, normalise_sql

GOLDEN = pathlib.Path(__file__).resolve().parents[1] / "golden" / "sql"

SIGNALS = ["firewall", "uptime", "azure", "ddos", "trend"]
SCOPES = {
    "production": Scope.production(),
    "window": Scope.window(4),
}


def load_golden(scope_name: str, signal: str) -> str:
    return (GOLDEN / scope_name / f"{signal}.sql").read_text()


@pytest.mark.parametrize("scope_name", sorted(SCOPES))
@pytest.mark.parametrize("signal", SIGNALS)
def test_rendered_sql_matches_published_artifact(scope_name: str, signal: str) -> None:
    built = SIGNAL_BUILDERS[signal](SCOPES[scope_name])
    assert normalise_sql(built) == normalise_sql(load_golden(scope_name, signal))


def test_production_and_window_actually_differ() -> None:
    """Guards the guard: if normalise_sql over-normalised, every test above
    would pass vacuously. The two scopes must still produce different SQL."""
    differing = [
        s for s in SIGNALS
        if normalise_sql(SIGNAL_BUILDERS[s](Scope.production()))
        != normalise_sql(SIGNAL_BUILDERS[s](Scope.window(4)))
    ]
    # ddos carries no time bound in either scope, so only the window predicate
    # distinguishes it -- but that is still a difference. All five differ.
    assert sorted(differing) == sorted(SIGNALS)


def test_normalise_preserves_json_key_case() -> None:
    """JSON keys are case-sensitive in Postgres, so normalisation must not fold
    them -- otherwise a real typo in a key would compare equal to the original."""
    a = normalise_sql("SELECT raw_data ->> 'clientCountryName' FROM logs")
    b = normalise_sql("SELECT raw_data ->> 'clientcountryname' FROM logs")
    assert a != b


@pytest.mark.parametrize("scope_name,expected", [("production", ()), ("window", ("4",))])
def test_bind_params_follow_the_scope(scope_name: str, expected: tuple[str, ...]) -> None:
    assert SCOPES[scope_name].params == expected


@pytest.mark.parametrize("signal", SIGNALS)
def test_window_scope_binds_exactly_one_placeholder_per_query(signal: str) -> None:
    """A mismatch between %s count and params is a runtime error, not a wrong
    answer, so it is worth catching without a database."""
    sql = SIGNAL_BUILDERS[signal](Scope.window(4))
    assert sql.count("%s") == len(Scope.window(4).params)


@pytest.mark.parametrize("signal", SIGNALS)
def test_production_scope_binds_no_placeholders(signal: str) -> None:
    assert SIGNAL_BUILDERS[signal](Scope.production()).count("%s") == 0
