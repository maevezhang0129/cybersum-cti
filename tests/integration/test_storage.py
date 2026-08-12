"""Schema and persistence against a real PostgreSQL."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from cybersum.storage import fetch_latest_report, save_report

pytestmark = pytest.mark.postgres


def report_kwargs(**overrides: Any) -> dict[str, Any]:
    base = {
        "report": "1: EXECUTIVE SUMMARY\nfirst\n",
        "status_code": "STABLE",
        "top_5_origins": {"US": 10},
        "metadata": {
            "total_tokens": 3683,
            "prompt_tokens": 3361,
            "completion_tokens": 322,
            "model_version": "gpt-4o-2024-11-20",
        },
        "execution_id": "2026-03-10T08:00:00",
        "report_date": date(2026, 3, 10),
    }
    return {**base, **overrides}


def test_schema_applies_cleanly_to_an_empty_database(fresh_schema: Any) -> None:
    with fresh_schema.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables"
            " WHERE table_schema = 'public' ORDER BY table_name;"
        )
        names = [row[0] for row in cur.fetchall()]
    assert "logs" in names
    assert "daily_security_reports" in names


def test_report_date_carries_a_unique_constraint(fresh_schema: Any) -> None:
    """The whole idempotency story rests on this. Without the constraint,
    ON CONFLICT (report_date) raises instead of upserting, and the daily job
    breaks the first time it runs twice in one day."""
    with fresh_schema.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
            WHERE tc.table_name = 'daily_security_reports'
              AND tc.constraint_type = 'UNIQUE'
              AND kcu.column_name = 'report_date';
            """
        )
        assert cur.fetchone()[0] == 1


def test_rerunning_a_day_overwrites_rather_than_duplicating(db: Any) -> None:
    assert save_report(db, **report_kwargs()) is True
    assert save_report(
        db,
        **report_kwargs(
            report="1: EXECUTIVE SUMMARY\nsecond\n",
            status_code="STATUS C",
            top_5_origins={"CN": 99},
            execution_id="2026-03-10T09:00:00",
        ),
    ) is True

    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM daily_security_reports WHERE report_date = %s;",
                    (date(2026, 3, 10),))
        assert cur.fetchone()[0] == 1
        cur.execute(
            "SELECT report_content, status_code, execution_id, top_5_origins"
            " FROM daily_security_reports WHERE report_date = %s;", (date(2026, 3, 10),)
        )
        content, status, execution_id, origins = cur.fetchone()

    assert "second" in content
    assert status == "STATUS C"
    assert execution_id == "2026-03-10T09:00:00"
    assert origins == {"CN": 99}


def test_two_different_days_coexist(db: Any) -> None:
    save_report(db, **report_kwargs(report_date=date(2026, 3, 10)))
    save_report(db, **report_kwargs(report_date=date(2026, 3, 11)))
    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM daily_security_reports;")
        assert cur.fetchone()[0] == 2


def test_token_metadata_is_stored_for_cost_accounting(db: Any) -> None:
    save_report(db, **report_kwargs())
    with db.cursor() as cur:
        cur.execute(
            "SELECT total_tokens, prompt_tokens, completion_tokens, model_version"
            " FROM daily_security_reports;"
        )
        assert cur.fetchone() == (3683, 3361, 322, "gpt-4o-2024-11-20")


def test_non_dict_origins_are_coerced_rather_than_crashing(db: Any) -> None:
    assert save_report(db, **report_kwargs(top_5_origins=["US", "CN"])) is True  # type: ignore[arg-type]
    with db.cursor() as cur:
        cur.execute("SELECT top_5_origins FROM daily_security_reports;")
        assert cur.fetchone()[0] == {}


def test_fetch_latest_returns_the_newest_report(db: Any) -> None:
    save_report(db, **report_kwargs(report_date=date(2026, 3, 9), status_code="STABLE"))
    save_report(db, **report_kwargs(report_date=date(2026, 3, 11), status_code="STATUS C"))
    save_report(db, **report_kwargs(report_date=date(2026, 3, 10), status_code="STATUS A"))

    rows = fetch_latest_report(db)
    assert len(rows) == 1
    assert rows[0]["report_date"] == date(2026, 3, 11)
    assert rows[0]["status_code"] == "STATUS C"


def test_fetch_latest_on_an_empty_table_returns_no_rows(db: Any) -> None:
    assert fetch_latest_report(db) == []
