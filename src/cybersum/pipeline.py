"""The daily run, as six stages.

This was one 245-line ``try`` block inside the Azure Functions entry point,
which meant none of it could be exercised without Azure, a database and a
network. Each stage is now a function taking what it needs and returning what it
produced, and the orchestrator is small enough to read in one go.

One property is preserved exactly: **the pipeline never raises**. The timer
trigger that calls it treats an exception as a failed invocation and retries, so
a malformed row or an expired key would become a retry storm against a paid API.
A bad run is a no-op with a log line instead.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from .aggregation import PRODUCTION, Scope, aggregate
from .config import Settings
from .grounding import GroundingReport, check_grounding
from .llm_client import ChatClient
from .notify import send_security_report
from .report import ReportResult, generate_daily_report
from .storage import PostgresSafeEncoder, connect, save_report

logger = logging.getLogger(__name__)

Stage = str


class StageError(Exception):
    """A stage could not complete. Carries which one, and why."""

    def __init__(self, stage: Stage, reason: str) -> None:
        super().__init__(f"{stage}: {reason}")
        self.stage = stage
        self.reason = reason


class PayloadTooLarge(StageError):
    pass


@dataclass(frozen=True)
class PipelineResult:
    ok: bool
    stage: Stage
    execution_id: str
    report_date: str
    reason: str | None = None
    report: str | None = None
    status_code: str = "STABLE"
    top_5_origins: dict[str, Any] = field(default_factory=dict)
    extraction_failed: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    grounding: GroundingReport | None = None
    saved: bool = False
    emailed: bool = False

    @classmethod
    def failed(
        cls, stage: Stage, reason: str, execution_id: str, report_date: str
    ) -> PipelineResult:
        return cls(ok=False, stage=stage, reason=reason,
                   execution_id=execution_id, report_date=report_date)

    def summary(self) -> str:
        if not self.ok:
            return f"Run {self.execution_id} failed at {self.stage}: {self.reason}"
        parts = [
            f"Run {self.execution_id} produced {self.report_date} [{self.status_code}]",
            f"saved={self.saved}",
            f"emailed={self.emailed}",
        ]
        if self.extraction_failed:
            parts.append("status was DEFAULTED (data block unusable)")
        if self.grounding is not None and not self.grounding.ok:
            parts.append(f"{len(self.grounding.ungrounded)} UNGROUNDED figure(s)")
        if tokens := self.metadata.get("total_tokens"):
            parts.append(f"{tokens} tokens")
        return ", ".join(parts)


# ── stages ───────────────────────────────────────────────────────────────────

def validate(settings: Settings) -> None:
    """Fail before spending anything if configuration is incomplete."""
    missing = settings.missing_required(need_db=not settings.use_mock_data)
    if missing:
        raise StageError("validate", f"missing configuration: {', '.join(missing)}")


def load_context(settings: Settings, scope: Scope = PRODUCTION) -> dict[str, Any]:
    """Aggregate the signals, or read the fixture in mock mode."""
    if settings.use_mock_data:
        if not settings.mock_data_path.is_file():
            raise StageError("fetch", f"no fixture at {settings.mock_data_path}")
        logger.info("Reading aggregated context from %s.", settings.mock_data_path)
        return json.loads(settings.mock_data_path.read_text())

    try:
        with connect(settings.db) as conn:
            context = aggregate(conn, scope)
    except Exception as exc:
        raise StageError("fetch", f"aggregation failed: {exc}") from exc

    # Every signal empty means the database answered but had nothing, which is
    # indistinguishable from a total ingestion outage. Reporting "all quiet" in
    # that case is the failure mode worth guarding against.
    if not _has_any_signal(context):
        raise StageError("fetch", "every aggregation signal was empty")
    return context


def _has_any_signal(context: dict[str, Any]) -> bool:
    security = context.get("security_summary", {})
    infra = context.get("infrastructure_health", {})
    trends = context.get("historical_trends", {})
    return any([
        security.get("total_blocked_events"),
        security.get("top_attacks"),
        security.get("ddos_status"),
        infra.get("abnormal_services"),
        infra.get("azure_resource_usage"),
        trends.get("data"),
    ])


def serialize_context(
    context: dict[str, Any], warn_mb: float = 1.0, abort_mb: float = 10.0
) -> tuple[dict[str, Any], float]:
    """Round-trip through the encoder so Decimals and datetimes are JSON-safe.

    Pure, and the only stage with no I/O -- which is why the size limits are
    checked here rather than at the call site.
    """
    payload = json.dumps(context, cls=PostgresSafeEncoder)
    size_mb = len(payload.encode("utf-8")) / 1024 / 1024
    if size_mb > abort_mb:
        raise PayloadTooLarge("serialize", f"context is {size_mb:.1f} MB, over {abort_mb} MB")
    if size_mb > warn_mb:
        logger.warning("Context is %.2f MB, larger than expected.", size_mb)
    return json.loads(payload), size_mb


def generate(
    context: dict[str, Any], settings: Settings, *, client: ChatClient | None = None
) -> ReportResult:
    result = generate_daily_report(context, settings.llm, client=client)
    if not result.success:
        raise StageError("generate", f"{result.error_type}: {result.error}")
    return result


def persist(result: ReportResult, execution_id: str, report_date: str, settings: Settings) -> bool:
    """Non-fatal: a report that cannot be stored is still worth emailing."""
    try:
        with connect(settings.db) as conn:
            return save_report(
                conn,
                report=result.report,
                status_code=result.status_code,
                top_5_origins=result.top_5_origins,
                metadata=result.metadata,
                execution_id=execution_id,
                report_date=report_date,
            )
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not reach the database to store the briefing: %s", exc)
        return False


def notify(result: ReportResult, report_date: str, settings: Settings) -> bool:
    """Non-fatal: a stored report that failed to send is still stored."""
    try:
        return send_security_report(
            result.report, result.status_code, report_date, settings.email
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not send the briefing: %s", exc)
        return False


# ── orchestrator ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Deps:
    """Injection points, so a full run can be exercised with no I/O at all."""

    load_context: Callable[..., dict[str, Any]] = load_context
    generate: Callable[..., ReportResult] = generate
    persist: Callable[..., bool] = persist
    notify: Callable[..., bool] = notify


DEFAULT_DEPS = Deps()


def run_daily_report(
    settings: Settings,
    *,
    scope: Scope = PRODUCTION,
    deps: Deps = DEFAULT_DEPS,
    today: Callable[[], date] = date.today,
    client: ChatClient | None = None,
) -> PipelineResult:
    execution_id = datetime.now(UTC).isoformat()
    report_date = today().strftime("%Y-%m-%d")

    try:
        validate(settings)
        context = deps.load_context(settings, scope)
        serialized, _ = serialize_context(
            context, settings.payload_warn_mb, settings.payload_abort_mb
        )
        result = deps.generate(serialized, settings, client=client)
    except StageError as exc:
        logger.error("Run %s failed at %s: %s", execution_id, exc.stage, exc.reason)
        return PipelineResult.failed(exc.stage, exc.reason, execution_id, report_date)
    except Exception as exc:
        logger.exception("Run %s failed unexpectedly.", execution_id)
        return PipelineResult.failed("unexpected", repr(exc), execution_id, report_date)

    # Every figure in the prose is traced back to the context it came from.
    # This is a deterministic check, not a second opinion from a model: it
    # catches the failure the evaluation surfaced, where a plausible,
    # well-formed number was computed rather than read.
    grounding = check_grounding(result.report, serialized)
    if not grounding.ok:
        logger.warning("Run %s: %s", execution_id, grounding.summary())

    # Mock runs produce a report to look at, and touch nothing else.
    saved = emailed = False
    if not settings.use_mock_data:
        saved = deps.persist(result, execution_id, report_date, settings)
        emailed = deps.notify(result, report_date, settings)

    return PipelineResult(
        ok=True,
        stage="complete",
        execution_id=execution_id,
        report_date=report_date,
        report=result.report,
        status_code=result.status_code,
        top_5_origins=result.top_5_origins,
        extraction_failed=result.extraction_failed,
        metadata=result.metadata,
        grounding=grounding,
        saved=saved,
        emailed=emailed,
    )
