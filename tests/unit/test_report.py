"""Report generation, with a fake client standing in for the model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from openai import APIConnectionError, APIError, AuthenticationError, RateLimitError

from cybersum.config import LLMSettings
from cybersum.report import generate_daily_report

# The SDK's exception classes reach into a real httpx response, so the fakes
# have to be real httpx objects rather than None.
_REQUEST = httpx.Request("POST", "https://api.example.test/v1/chat/completions")


def http_error(cls: type, status: int, message: str = "boom") -> Exception:
    response = httpx.Response(status_code=status, request=_REQUEST)
    return cls(message, response=response, body=None)

CONTEXT = {"security_summary": {"total_blocked_events": 2275}}

GOOD_RESPONSE = (
    "1: EXECUTIVE SUMMARY\nQuiet day.\n\n2: TECHNICAL BRIEF\n- nothing\n"
    '###DATA_START###\n{"status_code": "STATUS C", "top_5_origins": {"US": 9}}\n###DATA_END###'
)


@dataclass
class _Usage:
    prompt_tokens: int = 3361
    completion_tokens: int = 322
    total_tokens: int = 3683


class FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]
        self.usage = _Usage()
        self.model = "gpt-4o-2024-11-20"


class FakeCompletions:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0) if self.outcomes else FakeResponse(GOOD_RESPONSE)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeClient:
    def __init__(self, *outcomes: Any) -> None:
        self.completions = FakeCompletions(list(outcomes))
        self.chat = self

    @property
    def calls(self) -> list[dict[str, Any]]:
        return self.completions.calls


def settings(**overrides: Any) -> LLMSettings:
    base = {"provider": "openai", "api_key": "test-key", "model": "gpt-4o", "max_retries": 3}
    return LLMSettings(**{**base, "temperature": 0.2, **overrides})


def api_error(status: int) -> APIError:
    err = APIError("boom", request=_REQUEST, body=None)
    err.status_code = status  # type: ignore[attr-defined]
    return err


# ── the research contract ────────────────────────────────────────────────────

def test_both_audiences_come_from_exactly_one_model_call() -> None:
    """The executive summary and the technical brief are produced together by a
    single completion. Splitting them into two calls would be a different
    system answering a different question, so the count is asserted, not
    assumed."""
    client = FakeClient(FakeResponse(GOOD_RESPONSE))
    result = generate_daily_report(CONTEXT, settings(), client=client)

    assert result.success is True
    assert len(client.calls) == 1
    assert "1: EXECUTIVE SUMMARY" in result.report
    assert "2: TECHNICAL BRIEF" in result.report


def test_successful_result_carries_status_origins_and_usage() -> None:
    result = generate_daily_report(CONTEXT, settings(), client=FakeClient())
    assert result.status_code == "STATUS C"
    assert result.top_5_origins == {"US": 9}
    assert result.extraction_failed is False
    assert result.metadata["total_tokens"] == 3683
    assert result.metadata["model_version"] == "gpt-4o-2024-11-20"


def test_metadata_records_which_prompt_produced_the_report() -> None:
    """Scores move when prompts move. Recording the name and hash on every
    result is what makes a change in output attributable later."""
    result = generate_daily_report(CONTEXT, settings(), client=FakeClient())
    assert result.metadata["prompt_name"] == "production_report_v1"
    assert len(result.metadata["prompt_sha256"]) == 64


def test_prompt_is_the_system_message_and_context_is_the_user_message() -> None:
    client = FakeClient()
    generate_daily_report(CONTEXT, settings(), client=client)
    messages = client.calls[0]["messages"]
    assert [m["role"] for m in messages] == ["system", "user"]
    assert "cybersecurity analyst" in messages[0]["content"].lower()
    assert "total_blocked_events" in messages[1]["content"]


# ── validation ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [{}, None, "not a dict", []])
def test_rejects_unusable_context_without_calling_the_model(bad: Any) -> None:
    client = FakeClient()
    result = generate_daily_report(bad, settings(), client=client)
    assert result.success is False
    assert result.error_type == "ValidationError"
    assert result.status_code == "STABLE"
    assert result.top_5_origins == {}
    assert client.calls == [], "a rejected context must not reach the model"


# ── retry classification ─────────────────────────────────────────────────────

def test_transient_failures_are_retried_then_succeed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cybersum.report.time.sleep", lambda _: None)
    client = FakeClient(
        http_error(RateLimitError, 429, "slow down"),
        APIConnectionError(request=_REQUEST),
        FakeResponse(GOOD_RESPONSE),
    )
    result = generate_daily_report(CONTEXT, settings(), client=client)
    assert result.success is True
    assert len(client.calls) == 3
    assert result.metadata["attempts"] == 3


def test_auth_failure_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retrying a rejected key spends the backoff budget to reach the same
    error, and delays the log line that says what is actually wrong."""
    monkeypatch.setattr("cybersum.report.time.sleep", lambda _: None)
    client = FakeClient(http_error(AuthenticationError, 401, "bad key"))
    result = generate_daily_report(CONTEXT, settings(), client=client)
    assert result.success is False
    assert result.error_type == "AuthenticationError"
    assert len(client.calls) == 1


def test_server_errors_retry_but_client_errors_do_not(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cybersum.report.time.sleep", lambda _: None)

    server = FakeClient(api_error(503), FakeResponse(GOOD_RESPONSE))
    assert generate_daily_report(CONTEXT, settings(), client=server).success is True
    assert len(server.calls) == 2

    client_side = FakeClient(api_error(400))
    assert generate_daily_report(CONTEXT, settings(), client=client_side).success is False
    assert len(client_side.calls) == 1


def test_retries_stop_at_the_configured_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cybersum.report.time.sleep", lambda _: None)
    failures = [http_error(RateLimitError, 429) for _ in range(9)]
    client = FakeClient(*failures)
    result = generate_daily_report(CONTEXT, settings(max_retries=3), client=client)
    assert result.success is False
    assert len(client.calls) == 3


# ── degradation ──────────────────────────────────────────────────────────────

def test_report_ships_when_the_data_block_is_missing_but_says_so() -> None:
    """A briefing without its data block is still worth delivering. What must
    not happen is delivering it as though STABLE were a finding."""
    client = FakeClient(FakeResponse("1: EXECUTIVE SUMMARY\nAll quiet.\n"))
    result = generate_daily_report(CONTEXT, settings(), client=client)
    assert result.success is True
    assert result.report.strip() != ""
    assert result.extraction_failed is True
    assert result.status_code == "STABLE"


def test_oversized_context_is_refused_before_the_call() -> None:
    client = FakeClient()
    huge = {"blob": "x" * 600_000}
    result = generate_daily_report(huge, settings(), client=client)
    assert result.success is False
    assert result.error_type == "ValueError"
    assert client.calls == []
