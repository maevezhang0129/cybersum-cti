"""Deterministic aggregation: five SQL signals over the raw ``logs`` table.

This is the grounding layer. Raw logs never reach the model; these five results
do. The set is fixed, so the context handed to the LLM stays roughly the same
size whether the table holds ten thousand rows or ten million.

The project previously carried two copies of these queries -- one for the
production pipeline, one inlined in the evaluation harness -- kept apart on
purpose so that "the experiment reproduces production" stayed an honest claim.
It did not work: the copies drifted in five ways that nothing recorded, and a
third copy fell out of use entirely without anyone noticing.

So the two paths are unified here, and every way they are allowed to differ is a
named field on :class:`Scope`. Adding a divergence means adding a field; there is
no other way to make the two disagree. ``tests/unit/test_query_parity.py`` checks
what this builder emits against the SQL frozen from the pre-refactor code, so the
queries behind the published numbers cannot drift silently again.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Scope:
    """Which rows a run of the aggregation is allowed to see.

    ``Scope.production()`` is the daily pipeline: a rolling 24-hour window over
    live data. ``Scope.window(n)`` is one synthetic scenario window from the
    evaluation harness, which is time-independent because the fixture generator
    writes all of a window's rows at once.
    """

    window_id: int | None = None
    lookback: str | None = "24 hours"
    trend_lookback: str | None = "90 days"
    require_labelled_rows: bool = True
    label: str = "Last 24 Hours"
    timestamps_as_iso: bool = False

    @classmethod
    def production(cls) -> Scope:
        return cls()

    @classmethod
    def window(cls, n: int) -> Scope:
        # Synthetic windows carry no meaningful event_timestamp spread, so the
        # time bounds are lifted and the window tag selects rows instead. The
        # NOT NULL guards go too: the generator always writes both fields, and
        # keeping the guards would have made the two paths differ in a way that
        # the published results do not reflect.
        return cls(
            window_id=n,
            lookback=None,
            trend_lookback=None,
            require_labelled_rows=False,
            label=f"Scenario Window {n}",
            timestamps_as_iso=True,
        )

    @property
    def params(self) -> tuple[str, ...]:
        """Bind parameters for any query carrying the window predicate."""
        return () if self.window_id is None else (str(self.window_id),)

    def _time_predicate(self, interval: str | None) -> str:
        if interval is None:
            return ""
        return f"\n          AND event_timestamp >= NOW() - INTERVAL '{interval}'"

    def _window_predicate(self, indent: str = "          ") -> str:
        if self.window_id is None:
            return ""
        return f"\n{indent}AND raw_data ->> 'window_id' = %s"


# ── Query builders ───────────────────────────────────────────────────────────
# Each returns the SQL text for one signal under a given scope. Kept as literal
# templates rather than assembled from fragments so that the SQL a reader sees
# here is the SQL the database runs.

def firewall_sql(scope: Scope) -> str:
    where = scope._time_predicate(scope.lookback) + scope._window_predicate()
    if scope.require_labelled_rows:
        where += (
            "\n          AND raw_data ->> 'clientRequestHTTPHost' IS NOT NULL"
            "\n          AND raw_data ->> 'clientCountryName' IS NOT NULL"
        )
    return f"""SELECT
            COALESCE(raw_data ->> 'clientRequestHTTPHost', 'Unknown') AS target_host,
            COALESCE(raw_data ->> 'clientCountryName', 'Unknown') AS attacker_country,
            COUNT(*) AS block_count
        FROM logs
        WHERE provider = 'cloudflare'
          AND service = 'firewall'
          AND raw_data ->> 'action' = 'block'{where}
        GROUP BY target_host, attacker_country
        ORDER BY block_count DESC
        LIMIT 5;"""


def total_blocked_sql(scope: Scope) -> str:
    """Total blocked events over the same rows the top-five breakdown covers.

    Not part of the original aggregation. It is here because the evaluation
    showed the model inventing this number by summing the top-five rows, which
    undercounts by roughly half. See docs/findings.md.
    """
    where = scope._time_predicate(scope.lookback) + scope._window_predicate()
    return f"""SELECT COUNT(*) AS total_blocked_events
        FROM logs
        WHERE provider = 'cloudflare'
          AND service = 'firewall'
          AND raw_data ->> 'action' = 'block'{where};"""


def uptime_sql(scope: Scope) -> str:
    return f"""WITH latest_scan AS (
            SELECT raw_data
            FROM logs
            WHERE provider = 'uptimerobot'{scope._window_predicate(indent="              ")}
            ORDER BY ingested_at DESC
            LIMIT 1
        )
        SELECT
            monitor ->> 'friendly_name' AS service_name,
            monitor ->> 'url' AS service_url,
            CASE
                WHEN (monitor ->> 'status')::int = 0 THEN 'Paused'
                WHEN (monitor ->> 'status')::int = 1 THEN 'Not Checked Yet'
                WHEN (monitor ->> 'status')::int = 2 THEN 'Up'
                WHEN (monitor ->> 'status')::int = 8 THEN 'Seems Down'
                WHEN (monitor ->> 'status')::int = 9 THEN 'Down'
                ELSE 'Unknown'
            END AS status_text
        FROM latest_scan,
        jsonb_array_elements(raw_data -> 'monitors') AS monitor
        WHERE (monitor ->> 'status')::int != 2;"""


def azure_sql(scope: Scope) -> str:
    where = scope._time_predicate(scope.lookback) + scope._window_predicate()
    return rf"""SELECT
            to_char(event_timestamp, 'YYYY-MM-DD HH24:00') AS time_bucket,
            ROUND(AVG(
                CASE
                    WHEN raw_data ->> 'memory_mib' ~ '^\d+\.?\d*$'
                    THEN (raw_data ->> 'memory_mib')::numeric
                    ELSE NULL
                END
            ), 2) AS avg_memory_mb,
            ROUND(MAX(
                CASE
                    WHEN raw_data ->> 'cpu_total_sec' ~ '^\d+\.?\d*$'
                    THEN (raw_data ->> 'cpu_total_sec')::numeric
                    ELSE NULL
                END
            ), 2) AS max_cpu_load
        FROM logs
        WHERE provider = 'azure'
          AND service = 'backend_monitor'{where}
        GROUP BY time_bucket
        ORDER BY time_bucket DESC
        LIMIT 24;"""


def ddos_sql(scope: Scope) -> str:
    return rf"""SELECT
            event_timestamp,
            raw_data ->> 'health' AS health_status,
            CASE
                WHEN raw_data ->> 'risk_score' ~ '^\d+\.?\d*$'
                THEN (raw_data ->> 'risk_score')::numeric
                ELSE NULL
            END AS risk_score,
            ROUND(
                CASE
                    WHEN raw_data ->> 'malicious_ratio' ~ '^\d+\.?\d*$'
                    THEN (raw_data ->> 'malicious_ratio')::numeric * 100
                    ELSE NULL
                END, 1) AS malicious_percent
        FROM logs
        WHERE provider = 'cloudflare'
          AND service = 'ddos_analyzer'{scope._window_predicate()}
        ORDER BY event_timestamp DESC
        LIMIT 1;"""


def trend_sql(scope: Scope) -> str:
    # Column names are abbreviated to d/c/t on purpose: this result is ~90 rows
    # of the model's context budget, and the accompanying note explains the keys.
    indent = "              "
    where = scope._time_predicate(scope.trend_lookback).replace("          AND", indent + "AND")
    where += scope._window_predicate(indent=indent)
    return f"""WITH daily_stats AS (
            SELECT
                TO_CHAR(event_timestamp, 'MM-DD') AS day_label,
                raw_data ->> 'action' AS action,
                COUNT(*) AS action_count
            FROM logs
            WHERE provider = 'cloudflare'
              AND service = 'firewall'{where}
              AND raw_data ->> 'action' IS NOT NULL
            GROUP BY day_label, action
        ),
        ranked_actions AS (
            SELECT
                day_label, action, action_count,
                ROW_NUMBER() OVER (
                    PARTITION BY day_label ORDER BY action_count DESC
                ) AS rn
            FROM daily_stats
        )
        SELECT
            ds.day_label AS d,
            SUM(ds.action_count) AS c,
            MAX(ra.action) AS t
        FROM daily_stats ds
        JOIN ranked_actions ra
          ON ds.day_label = ra.day_label AND ra.rn = 1
        GROUP BY ds.day_label
        ORDER BY ds.day_label ASC;"""


#: The default scope. A module-level singleton rather than ``Scope()`` in each
#: signature: the value is frozen, so one shared instance is correct, and it
#: gives the production path a name to refer to.
PRODUCTION = Scope.production()


SIGNAL_BUILDERS = {
    "firewall": firewall_sql,
    "uptime": uptime_sql,
    "azure": azure_sql,
    "ddos": ddos_sql,
    "trend": trend_sql,
}


def normalise_sql(sql: str) -> str:
    """Collapse formatting so two queries compare on meaning, not layout.

    Comments are dropped, whitespace runs collapse, padding next to brackets and
    commas is removed, and the ``AS`` keyword is case-folded -- all differences
    a SQL parser ignores.

    Everything else is left alone. In particular identifiers and string literals
    keep their case, because ``raw_data ->> 'clientCountryName'`` and
    ``->> 'clientcountryname'`` select different things in Postgres, and a test
    that folded them would call a real typo a match.

    The bracket and comma rules operate on the whole string, so a literal
    containing ``( `` would be normalised too. None of the queries here have
    one, and ``test_normalise_preserves_json_key_case`` covers the case that
    actually matters.
    """
    sql = re.sub(r"--[^\n]*", "", sql)
    sql = re.sub(r"\s+", " ", sql).strip()
    sql = re.sub(r"\s*([(),])\s*", r"\1", sql)
    return re.sub(r"\bas\b", "AS", sql, flags=re.IGNORECASE)


# ── Execution ────────────────────────────────────────────────────────────────

def _rows(cur: Any) -> list[dict[str, Any]]:
    columns = [d[0] for d in cur.description]
    return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]


def _run(cur: Any, name: str, sql: str, params: Sequence[Any]) -> list[dict[str, Any]]:
    """Execute one signal. A failure yields no rows and a log line.

    Errors are swallowed per signal so that one unavailable data source degrades
    the briefing instead of cancelling it -- but note what that means: a total
    database failure produces five empty results, which read as a quiet day
    rather than as an outage. The pipeline guards against that by treating an
    entirely empty context as a stage failure.
    """
    try:
        cur.execute(sql, params)
        rows = _rows(cur)
        if not rows:
            logger.warning("Aggregation signal %r returned no rows.", name)
        return rows
    except Exception as exc:  # noqa: BLE001 - one bad signal must not kill the run
        logger.error("Aggregation signal %r failed: %s", name, exc)
        return []


def get_firewall_stats(cur: Any, scope: Scope = PRODUCTION) -> list[dict[str, Any]]:
    return _run(cur, "firewall", firewall_sql(scope), scope.params)


def get_total_blocked(cur: Any, scope: Scope = PRODUCTION) -> int:
    rows = _run(cur, "total_blocked", total_blocked_sql(scope), scope.params)
    return int(rows[0]["total_blocked_events"]) if rows else 0


def get_uptime_stats(cur: Any, scope: Scope = PRODUCTION) -> list[dict[str, Any]]:
    return _run(cur, "uptime", uptime_sql(scope), scope.params)


def get_azure_stats(cur: Any, scope: Scope = PRODUCTION) -> list[dict[str, Any]]:
    return _run(cur, "azure", azure_sql(scope), scope.params)


def get_ddos_status(cur: Any, scope: Scope = PRODUCTION) -> dict[str, Any]:
    rows = _run(cur, "ddos", ddos_sql(scope), scope.params)
    return rows[0] if rows else {}


def get_90day_trend(cur: Any, scope: Scope = PRODUCTION) -> list[dict[str, Any]]:
    return _run(cur, "trend", trend_sql(scope), scope.params)


def aggregate(
    conn: Any, scope: Scope = PRODUCTION, *, now: datetime | None = None
) -> dict[str, Any]:
    """Run all signals and assemble the context handed to the model.

    The shape of this dict is a prompt input, not just a return value: the model
    is told to read ``total_blocked_events`` for the headline figure, and the
    abbreviated trend keys are explained by the note beside them.
    """
    generated_at = now or datetime.now(UTC)
    with conn.cursor() as cur:
        return {
            "report_generated_at": generated_at.isoformat() if scope.timestamps_as_iso
            else generated_at,
            "period": scope.label,
            "security_summary": {
                # Deliberately first, and named as a total: the top_attacks list
                # below is a five-row sample, and models will happily sum it and
                # present the result as the total unless given the real one.
                "total_blocked_events": get_total_blocked(cur, scope),
                "top_attacks_note": "top_attacks is the 5 busiest host/country "
                                    "pairs only, not the full breakdown; it does "
                                    "not sum to total_blocked_events",
                "top_attacks": get_firewall_stats(cur, scope),
                "ddos_status": get_ddos_status(cur, scope),
            },
            "infrastructure_health": {
                "abnormal_services": get_uptime_stats(cur, scope),
                "azure_resource_usage": get_azure_stats(cur, scope),
            },
            "historical_trends": {
                "note": "d=date(MM-DD), c=attack_count, t=top_action",
                "data": get_90day_trend(cur, scope),
            },
        }
