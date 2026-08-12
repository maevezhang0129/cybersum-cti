"""Database access.

The project previously had two ways into the same database: ``db_service.py``
built a connection from discrete ``DB_*`` variables, while ``src/db_utils.py``
parsed a ``DATABASE_URL``. They disagreed on the default port. Both paths are
merged here, behind :class:`DatabaseSettings`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg2

from .config import DatabaseSettings

logger = logging.getLogger(__name__)


class PostgresSafeEncoder(json.JSONEncoder):
    """Serialise what psycopg2 hands back.

    ``NUMERIC`` arrives as ``Decimal`` and timestamps as ``datetime``, neither of
    which the stdlib encoder handles. Since the result of this encoding is the
    model's input, an unencodable value would fail the run rather than produce a
    wrong briefing -- but failing the run is still worse than converting it.
    """

    def default(self, o: Any) -> Any:
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        if isinstance(o, UUID):
            return str(o)
        if isinstance(o, bytes):
            return o.decode("utf-8", errors="replace")
        return super().default(o)


@contextmanager
def connect(settings: DatabaseSettings) -> Iterator[Any]:
    """Open a connection, always close it."""
    conn = psycopg2.connect(**settings.connect_kwargs())
    try:
        yield conn
    finally:
        conn.close()


def save_report(
    conn: Any,
    *,
    report: str,
    status_code: str,
    top_5_origins: dict[str, Any],
    metadata: dict[str, Any],
    execution_id: str,
    report_date: date | str,
) -> bool:
    """Write one day's briefing, replacing any existing row for that date.

    Idempotency rests on ``report_date`` being UNIQUE: re-running a day
    overwrites rather than accumulating. Without the constraint this statement
    raises instead of upserting, which is why deploy/sql/001_schema.sql declares
    it and an integration test asserts it.
    """
    if not isinstance(top_5_origins, dict):
        logger.warning(
            "top_5_origins was %s, not an object; storing {}.", type(top_5_origins).__name__
        )
        top_5_origins = {}

    statement = """
        INSERT INTO daily_security_reports
            (report_date, report_content, status_code, top_5_origins,
             total_tokens, prompt_tokens, completion_tokens, model_version, execution_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (report_date) DO UPDATE SET
            report_content    = EXCLUDED.report_content,
            status_code       = EXCLUDED.status_code,
            top_5_origins     = EXCLUDED.top_5_origins,
            total_tokens      = EXCLUDED.total_tokens,
            prompt_tokens     = EXCLUDED.prompt_tokens,
            completion_tokens = EXCLUDED.completion_tokens,
            model_version     = EXCLUDED.model_version,
            execution_id      = EXCLUDED.execution_id,
            created_at        = CURRENT_TIMESTAMP;
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                statement,
                (
                    report_date,
                    report,
                    status_code,
                    json.dumps(top_5_origins),
                    metadata.get("total_tokens"),
                    metadata.get("prompt_tokens"),
                    metadata.get("completion_tokens"),
                    metadata.get("model_version"),
                    execution_id,
                ),
            )
        conn.commit()
        logger.info("Stored briefing for %s [%s].", report_date, status_code)
        return True
    except Exception as exc:  # noqa: BLE001 - a failed write must not lose the report
        conn.rollback()
        logger.error("Could not store briefing for %s: %s", report_date, exc)
        return False


def fetch_latest_report(conn: Any) -> list[dict[str, Any]]:
    """The most recent briefing, shaped for the dashboard that polls for it."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT report_date, report_content, status_code, top_5_origins,
                   total_tokens, model_version, created_at
            FROM daily_security_reports
            ORDER BY report_date DESC, id DESC
            LIMIT 1;
            """
        )
        columns = [d[0] for d in cur.description]
        rows = cur.fetchall()
    return [dict(zip(columns, row, strict=True)) for row in rows]


def insert_log(
    conn: Any,
    *,
    provider: str,
    service: str,
    log_type: str,
    raw_data: dict[str, Any],
    event_timestamp: datetime | None = None,
) -> None:
    """Append one raw telemetry record to the Bronze layer.

    ``raw_data`` is stored whole and read back later with ``->> 'key'``. There is
    no schema shared between the collector writing a key and the aggregation
    reading it, so adding a metric means changing both sides.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO logs (provider, service, log_type, event_timestamp, raw_data)
            VALUES (%s, %s, %s, %s, %s);
            """,
            (
                provider,
                service,
                log_type,
                event_timestamp or datetime.now(),
                json.dumps(raw_data, cls=PostgresSafeEncoder),
            ),
        )
    conn.commit()
