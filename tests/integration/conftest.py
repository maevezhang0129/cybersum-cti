"""Fixtures for tests that need a real PostgreSQL.

Run `make db-up` first. These are excluded from the default pytest run, so a
contributor without Docker still gets a green `pytest`.
"""

from __future__ import annotations

import json
import os
import pathlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg2
import pytest

from cybersum.config import DatabaseSettings

SCHEMA = pathlib.Path(__file__).resolve().parents[2] / "deploy" / "sql" / "001_schema.sql"


def db_settings() -> DatabaseSettings:
    return DatabaseSettings.from_env(
        {
            "DB_HOST": os.environ.get("DB_HOST", "localhost"),
            "DB_PORT": os.environ.get("DB_PORT", "5432"),
            "DB_NAME": os.environ.get("DB_NAME", "cybersum"),
            "DB_USER": os.environ.get("DB_USER", "cybersum"),
            "DB_PASS": os.environ.get("DB_PASS", "cybersum"),
        }
    )


@pytest.fixture(scope="session")
def raw_connection() -> Iterator[Any]:
    try:
        conn = psycopg2.connect(**db_settings().connect_kwargs())
    except psycopg2.OperationalError as exc:
        pytest.skip(f"No PostgreSQL available: {exc}")
    conn.autocommit = True
    yield conn
    conn.close()


@pytest.fixture
def db(raw_connection: Any) -> Iterator[Any]:
    """A connection over empty tables.

    Truncating per test rather than per session keeps each test's arrangement
    visible in the test itself.
    """
    with raw_connection.cursor() as cur:
        cur.execute("TRUNCATE logs, daily_security_reports RESTART IDENTITY;")
    raw_connection.commit()
    yield raw_connection


@pytest.fixture
def fresh_schema(raw_connection: Any) -> Iterator[Any]:
    """A database with the schema dropped and reapplied from the .sql file."""
    with raw_connection.cursor() as cur:
        cur.execute("DROP VIEW IF EXISTS vw_latest_security_briefings;")
        cur.execute("DROP TABLE IF EXISTS logs, daily_security_reports;")
        cur.execute(SCHEMA.read_text())
    raw_connection.commit()
    yield raw_connection


def insert_firewall_row(
    conn: Any,
    *,
    host: str = "www.site1.org",
    country: str = "US",
    action: str = "block",
    age: timedelta = timedelta(hours=1),
    window_id: str | None = None,
) -> None:
    payload: dict[str, Any] = {"action": action}
    if host is not None:
        payload["clientRequestHTTPHost"] = host
    if country is not None:
        payload["clientCountryName"] = country
    if window_id is not None:
        payload["window_id"] = window_id
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO logs (provider, service, log_type, event_timestamp, raw_data)"
            " VALUES ('cloudflare', 'firewall', 'event', %s, %s);",
            (datetime.now(UTC) - age, json.dumps(payload)),
        )
    conn.commit()
