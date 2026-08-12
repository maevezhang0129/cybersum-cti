"""Generating the daily briefing.

One model call produces both audiences' text. That is a research constraint, not
an implementation detail: the claim under test is that a single grounded call
can serve an executive summary and a technical brief at once, so splitting this
into two calls would answer a different question. ``report.py`` must contain
exactly one ``chat.completions.create`` call site, and a test asserts it.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .config import LLMSettings
from .llm_client import ChatClient, make_client, model_name
from .parsing import ParsedReport, extract_json_data, format_report_for_dashboard
from .prompts import load_prompt
from .retry import backoff_seconds, should_retry

logger = logging.getLogger(__name__)

MAX_CONTEXT_BYTES = 500_000
WARN_CONTEXT_BYTES = 100_000


@dataclass(frozen=True)
class ReportResult:
    success: bool
    report: str = ""
    status_code: str = "STABLE"
    top_5_origins: dict[str, Any] = field(default_factory=dict)
    extraction_failed: bool = False
    error: str | None = None
    error_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def failed(cls, error: str, error_type: str) -> ReportResult:
        return cls(success=False, error=error, error_type=error_type)


def _serialise_context(context: dict[str, Any]) -> str:
    payload = json.dumps(context, default=str)
    size = len(payload.encode("utf-8"))
    if size > MAX_CONTEXT_BYTES:
        raise ValueError(
            f"Aggregated context is {size / 1024:.0f} KB, over the "
            f"{MAX_CONTEXT_BYTES / 1024:.0f} KB limit."
        )
    if size > WARN_CONTEXT_BYTES:
        logger.warning("Aggregated context is %.0f KB; unusually large.", size / 1024)
    return payload


def generate_daily_report(
    context: dict[str, Any],
    settings: LLMSettings,
    *,
    client: ChatClient | None = None,
    prompt_name: str = "production_report_v1",
) -> ReportResult:
    """Turn an aggregated context into a briefing.

    Returns a result object on every path, including failure, so the caller can
    record what happened without catching exceptions from here.
    """
    if not isinstance(context, dict) or not context:
        return ReportResult.failed("Context must be a non-empty dict.", "ValidationError")

    try:
        payload = _serialise_context(context)
    except (ValueError, TypeError) as exc:
        return ReportResult.failed(str(exc), type(exc).__name__)

    prompt = load_prompt(prompt_name)

    try:
        chat_client = client if client is not None else make_client(settings)
    except (ValueError, ImportError) as exc:
        return ReportResult.failed(str(exc), type(exc).__name__)

    last_error: Exception | None = None
    for attempt in range(1, settings.max_retries + 1):
        try:
            response = chat_client.chat.completions.create(
                model=model_name(settings),
                messages=[
                    {"role": "system", "content": prompt.text},
                    {"role": "user", "content": payload},
                ],
                temperature=settings.temperature,
            )
            return _build_result(response, prompt, settings, attempt)
        except Exception as exc:  # noqa: BLE001 - classified by should_retry
            last_error = exc
            if not should_retry(exc, attempt, settings.max_retries):
                logger.error("Model call failed permanently: %s", exc)
                break
            delay = backoff_seconds(attempt)
            logger.warning(
                "Model call attempt %d/%d failed (%s); retrying in %.1fs.",
                attempt, settings.max_retries, type(exc).__name__, delay,
            )
            time.sleep(delay)

    assert last_error is not None
    return ReportResult.failed(str(last_error), type(last_error).__name__)


def _build_result(
    response: Any, prompt: Any, settings: LLMSettings, attempts: int
) -> ReportResult:
    raw = response.choices[0].message.content or ""
    parsed: ParsedReport = extract_json_data(raw)
    usage = getattr(response, "usage", None)

    if parsed.extraction_failed:
        # The briefing still ships -- but the status it carries is a default,
        # not a finding, and downstream should be able to tell the difference.
        logger.warning(
            "Structured data block unusable (%s); status defaults to %s.",
            parsed.failure_reason, parsed.status_code,
        )

    return ReportResult(
        success=True,
        report=format_report_for_dashboard(parsed.text),
        status_code=parsed.status_code,
        top_5_origins=parsed.top_5_origins,
        extraction_failed=parsed.extraction_failed,
        metadata={
            "model_version": getattr(response, "model", settings.model),
            "prompt_name": prompt.name,
            "prompt_sha256": prompt.sha256,
            "temperature": settings.temperature,
            "attempts": attempts,
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        },
    )
