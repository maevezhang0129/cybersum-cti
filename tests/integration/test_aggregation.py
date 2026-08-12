"""Aggregation semantics against real data.

test_query_parity proves the SQL text is unchanged; these prove the text means
what the Scope fields claim it means.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from cybersum.aggregation import (
    Scope,
    aggregate,
    get_firewall_stats,
    get_origin_countries,
    get_total_blocked,
)

from .conftest import insert_firewall_row

pytestmark = pytest.mark.postgres


def test_production_scope_excludes_rows_older_than_a_day(db: Any) -> None:
    insert_firewall_row(db, age=timedelta(hours=1))
    insert_firewall_row(db, age=timedelta(hours=2))
    insert_firewall_row(db, age=timedelta(hours=30))  # outside the 24h window

    with db.cursor() as cur:
        assert get_total_blocked(cur, Scope.production()) == 2


def test_production_scope_drops_rows_missing_host_or_country(db: Any) -> None:
    """The NOT NULL guards are a declared Scope field, so their effect is
    asserted rather than assumed from reading the SQL."""
    insert_firewall_row(db, host="www.site1.org", country="US")
    insert_firewall_row(db, host=None, country="US")  # type: ignore[arg-type]
    insert_firewall_row(db, host="api.site2.org", country=None)  # type: ignore[arg-type]

    with db.cursor() as cur:
        rows = get_firewall_stats(cur, Scope.production())
    hosts = {r["target_host"] for r in rows}
    assert hosts == {"www.site1.org"}


def test_non_block_actions_are_not_counted(db: Any) -> None:
    insert_firewall_row(db, action="block")
    insert_firewall_row(db, action="managed_challenge")
    insert_firewall_row(db, action="allow")

    with db.cursor() as cur:
        assert get_total_blocked(cur, Scope.production()) == 1


def test_window_scope_isolates_one_window(db: Any) -> None:
    for _ in range(3):
        insert_firewall_row(db, window_id="4")
    for _ in range(5):
        insert_firewall_row(db, window_id="3")

    with db.cursor() as cur:
        assert get_total_blocked(cur, Scope.window(4)) == 3
        assert get_total_blocked(cur, Scope.window(3)) == 5


def test_window_scope_ignores_age(db: Any) -> None:
    """Synthetic windows are written all at once, so lifting the time bound is
    what makes them queryable at all. A 200-day-old row must still count."""
    insert_firewall_row(db, window_id="4", age=timedelta(days=200))

    with db.cursor() as cur:
        assert get_total_blocked(cur, Scope.window(4)) == 1
        assert get_total_blocked(cur, Scope.production()) == 0


def test_window_scope_keeps_rows_with_missing_labels(db: Any) -> None:
    insert_firewall_row(db, window_id="4", host=None)  # type: ignore[arg-type]
    with db.cursor() as cur:
        rows = get_firewall_stats(cur, Scope.window(4))
    assert [r["target_host"] for r in rows] == ["Unknown"]


def test_total_is_larger_than_the_sum_of_the_top_five(db: Any) -> None:
    """The finding this whole aggregate exists for. With more than five
    host/country pairs, the top-five list is a strict subset -- and a model
    summing it under-reports, which is exactly what happened across all five
    published scenario windows."""
    for i in range(8):
        for _ in range(i + 1):
            insert_firewall_row(db, host=f"host{i}.example.org", country=f"C{i}")

    with db.cursor() as cur:
        total = get_total_blocked(cur, Scope.production())
        top5 = get_firewall_stats(cur, Scope.production())

    assert len(top5) == 5
    assert sum(r["block_count"] for r in top5) < total


def test_aggregate_reports_the_total_before_the_sample(db: Any) -> None:
    """Field order is part of the prompt input. The total is stated first, and
    the sample is labelled as a sample."""
    insert_firewall_row(db)
    with db.cursor():
        context = aggregate(db, Scope.production())

    summary = context["security_summary"]
    keys = list(summary)
    assert keys.index("total_blocked_events") < keys.index("top_attacks")
    assert "does not sum to total_blocked_events" in summary["top_attacks_note"]


def test_empty_database_yields_empty_collections_never_none(db: Any) -> None:
    context = aggregate(db, Scope.production())
    assert context["security_summary"]["total_blocked_events"] == 0
    assert context["security_summary"]["top_attacks"] == []
    assert context["security_summary"]["ddos_status"] == {}
    assert context["infrastructure_health"]["abnormal_services"] == []
    assert context["historical_trends"]["data"] == []


def test_production_context_timestamps_are_datetimes_and_window_are_strings(db: Any) -> None:
    """Both forms feed the prompt, and the two scopes emitted different types in
    the original code. Scope.timestamps_as_iso preserves that difference on
    purpose rather than harmonising it."""
    assert not isinstance(aggregate(db, Scope.production())["report_generated_at"], str)
    assert isinstance(aggregate(db, Scope.window(4))["report_generated_at"], str)


def test_country_totals_cover_every_row_not_just_the_top_five(db: Any) -> None:
    """The second instance of the same failure. The model was summing the
    country column of top_attacks and presenting it as a country total; with
    eight host/country pairs, that sum is strictly smaller than the real one.
    """
    # Two countries, spread across enough hosts that neither fits in the top five.
    for i in range(6):
        for _ in range(i + 1):
            insert_firewall_row(db, host=f"host{i}.example.org", country="United States")
    for _ in range(3):
        insert_firewall_row(db, host="host9.example.org", country="China")

    with db.cursor() as cur:
        countries = get_origin_countries(cur, Scope.production())
        top5 = get_firewall_stats(cur, Scope.production())

    from_top5 = sum(r["block_count"] for r in top5 if r["attacker_country"] == "United States")
    assert countries["United States"] == 21
    assert from_top5 < countries["United States"]


def test_country_totals_sum_to_the_overall_total(db: Any) -> None:
    """Unlike top_attacks, this breakdown is complete, so it must reconcile."""
    for i in range(4):
        insert_firewall_row(db, country=f"Country{i}")
    insert_firewall_row(db, country="Country0", action="allow")  # not a block

    with db.cursor() as cur:
        countries = get_origin_countries(cur, Scope.production())
        total = get_total_blocked(cur, Scope.production())

    assert sum(countries.values()) == total
