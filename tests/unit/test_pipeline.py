"""Pipeline stages and the orchestrator, with every dependency stubbed."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from cybersum.config import Settings
from cybersum.pipeline import (
    Deps,
    PayloadTooLarge,
    StageError,
    load_context,
    run_daily_report,
    serialize_context,
    validate,
)
from cybersum.report import ReportResult

CONTEXT = {
    "security_summary": {"total_blocked_events": 2275, "top_attacks": [{"n": 1}]},
    "infrastructure_health": {"abnormal_services": [], "azure_resource_usage": []},
    "historical_trends": {"data": []},
}

REPORT = ReportResult(
    success=True,
    report="1: EXECUTIVE SUMMARY\nfine\n",
    status_code="STATUS C",
    top_5_origins={"US": 9},
    metadata={"total_tokens": 3683, "model_version": "gpt-4o"},
)


def settings_from(**env: str) -> Settings:
    return Settings.from_env(env)


def fixed_date() -> date:
    return date(2026, 3, 10)


class Recorder:
    """Stubs that record what the orchestrator asked them to do."""

    def __init__(self, *, context: Any = CONTEXT, result: Any = REPORT) -> None:
        self.context, self.result = context, result
        self.persist_calls: list[tuple] = []
        self.notify_calls: list[tuple] = []

    def load_context(self, settings: Settings, scope: Any = None) -> dict:
        if isinstance(self.context, Exception):
            raise self.context
        return self.context

    def generate(self, context: dict, settings: Settings, *, client: Any = None) -> ReportResult:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def persist(
        self, result: ReportResult, execution_id: str, report_date: str, s: Settings
    ) -> bool:
        self.persist_calls.append((result, execution_id, report_date))
        return True

    def notify(self, result: ReportResult, report_date: str, s: Settings) -> bool:
        self.notify_calls.append((result, report_date))
        return True

    def as_deps(self) -> Deps:
        return Deps(self.load_context, self.generate, self.persist, self.notify)


# ── validate ─────────────────────────────────────────────────────────────────

def test_mock_mode_does_not_require_database_settings() -> None:
    validate(settings_from(USE_MOCK_DATA="true"))


def test_live_mode_names_every_missing_variable() -> None:
    with pytest.raises(StageError) as excinfo:
        validate(settings_from(OPENAI_API_KEY="k", DB_HOST="", DB_NAME="", DB_USER="", DB_PASS=""))
    reason = excinfo.value.reason
    assert excinfo.value.stage == "validate"
    for key in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASS"):
        assert key in reason


def test_email_recipients_required_only_when_email_is_enabled() -> None:
    validate(settings_from(USE_MOCK_DATA="true", EMAIL_ENABLED="false"))
    with pytest.raises(StageError, match="EMAIL_RECIPIENTS"):
        validate(settings_from(USE_MOCK_DATA="true", EMAIL_ENABLED="true"))


# ── serialize ────────────────────────────────────────────────────────────────

def test_database_types_survive_serialisation() -> None:
    raw = {
        "security_summary": {"risk": Decimal("20.5")},
        "when": datetime(2026, 3, 10, 8, 0, tzinfo=UTC),
        "id": UUID("12345678-1234-5678-1234-567812345678"),
        "blob": b"bytes",
    }
    out, _ = serialize_context(raw)
    assert out["security_summary"]["risk"] == 20.5
    assert out["when"].startswith("2026-03-10")
    assert out["id"].startswith("12345678")
    assert out["blob"] == "bytes"


def test_oversized_context_aborts_before_the_model_call() -> None:
    with pytest.raises(PayloadTooLarge):
        serialize_context({"blob": "x" * 2_000_000}, warn_mb=1.0, abort_mb=1.0)


def test_large_but_acceptable_context_warns_and_proceeds(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING"):
        out, size = serialize_context({"blob": "x" * 200_000}, warn_mb=0.1, abort_mb=10.0)
    assert out and size > 0.1
    assert any("larger than expected" in r.message for r in caplog.records)


# ── fetch ────────────────────────────────────────────────────────────────────

def test_empty_aggregation_is_a_failure_not_an_all_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Five empty signals look exactly like a quiet day. If ingestion has
    stopped, saying so beats publishing a reassuring briefing."""
    empty = {"security_summary": {}, "infrastructure_health": {}, "historical_trends": {}}
    monkeypatch.setattr("cybersum.pipeline.aggregate", lambda conn, scope: empty)
    monkeypatch.setattr("cybersum.pipeline.connect", _fake_connect)
    with pytest.raises(StageError, match="every aggregation signal was empty"):
        load_context(settings_from(DB_PASS="x"))


class _FakeConn:
    def __enter__(self) -> _FakeConn:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


def _fake_connect(settings: Any) -> _FakeConn:
    return _FakeConn()


def test_mock_mode_reads_the_committed_fixture() -> None:
    context = load_context(settings_from(USE_MOCK_DATA="true"))
    assert "security_summary" in context
    assert context["security_summary"]["top_attacks"]


# ── orchestrator ─────────────────────────────────────────────────────────────

def test_happy_path_stores_and_sends_once_each() -> None:
    rec = Recorder()
    result = run_daily_report(
        settings_from(OPENAI_API_KEY="k", DB_PASS="p"), deps=rec.as_deps(), today=fixed_date
    )
    assert result.ok is True
    assert result.report_date == "2026-03-10"
    assert result.status_code == "STATUS C"
    assert len(rec.persist_calls) == 1
    assert len(rec.notify_calls) == 1
    assert rec.persist_calls[0][2] == "2026-03-10"


def test_mock_mode_never_writes_or_sends() -> None:
    """A demo run must be incapable of touching a database or a mail relay."""
    rec = Recorder()
    result = run_daily_report(
        settings_from(USE_MOCK_DATA="true"), deps=rec.as_deps(), today=fixed_date
    )
    assert result.ok is True
    assert result.report
    assert rec.persist_calls == []
    assert rec.notify_calls == []
    assert (result.saved, result.emailed) == (False, False)


def test_a_stage_failure_is_reported_not_raised() -> None:
    """The timer trigger retries on exception, so an unrecoverable run has to
    return rather than raise -- otherwise a bad day becomes a retry storm
    against a metered API."""
    rec = Recorder(result=StageError("generate", "model refused"))
    result = run_daily_report(
        settings_from(OPENAI_API_KEY="k", DB_PASS="p"), deps=rec.as_deps(), today=fixed_date
    )
    assert result.ok is False
    assert result.stage == "generate"
    assert "model refused" in (result.reason or "")
    assert rec.persist_calls == []


def test_an_unexpected_exception_is_also_contained() -> None:
    rec = Recorder(context=RuntimeError("psycopg2 exploded"))
    result = run_daily_report(
        settings_from(OPENAI_API_KEY="k", DB_PASS="p"), deps=rec.as_deps(), today=fixed_date
    )
    assert result.ok is False
    assert result.stage == "unexpected"
    assert "psycopg2 exploded" in (result.reason or "")


def test_summary_flags_a_defaulted_status() -> None:
    rec = Recorder(result=ReportResult(success=True, report="x", extraction_failed=True))
    result = run_daily_report(
        settings_from(USE_MOCK_DATA="true"), deps=rec.as_deps(), today=fixed_date
    )
    assert "DEFAULTED" in result.summary()


def test_summary_of_a_failed_run_names_the_stage() -> None:
    rec = Recorder(context=StageError("fetch", "database unreachable"))
    result = run_daily_report(
        settings_from(USE_MOCK_DATA="true"), deps=rec.as_deps(), today=fixed_date
    )
    assert "failed at fetch" in result.summary()
    assert "database unreachable" in result.summary()
